#!/usr/bin/env python
import argparse
import numpy as np

from ptan.common import env_params, runfile
from ptan.actions.epsilon_greedy import ActionSelectorEpsilonGreedy
from ptan import experience

import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable

import gym

GAMMA = 0.99


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--runfile", required=True, help="Name of the runfile to use")
    parser.add_argument("-m", "--monitor", help="Use monitor and save it's data into given dir")
    args = parser.parse_args()

    run = runfile.RunFile(args.runfile)

    cuda_enabled = run.getboolean("defaults", "cuda")
    env = gym.make(run.get("defaults", "env")).env
    if args.monitor:
        env = gym.wrappers.Monitor(env, args.monitor)

    params = env_params.EnvParams.from_env(env)
    env_params.register(params)

    model = nn.Sequential(
        nn.Linear(params.state_shape[0], 50),
        nn.ReLU(),
        # nn.Linear(100, 100),
        # nn.ReLU(),
        nn.Linear(50, params.n_actions)
    )
    if cuda_enabled:
        model.cuda()

    loss_fn = nn.MSELoss(size_average=False)
    optimizer = optim.Adam(model.parameters(), lr=run.getfloat("learning", "lr"))

    action_selector = ActionSelectorEpsilonGreedy(epsilon=run.getfloat("defaults", "epsilon"), params=params)

    def agent(states):
        """
        Return actions to take by a batch of states
        :param states: numpy array with states 
        :return: 
        """
        # TODO: move this into separate class
        v = Variable(torch.from_numpy(np.array(states, dtype=np.float32)))
        if cuda_enabled:
            v = v.cuda()
        q = model(v)
        actions = action_selector(q)
        return actions.data.cpu().numpy()

    exp_source = experience.ExperienceSource(env=env, agent=agent, steps_count=run.getint("defaults", "n_steps"))
    exp_replay = experience.ExperienceReplayBuffer(exp_source, buffer_size=run.getint("exp_buffer", "size"))

    def batch_to_train(batch):
        """
        Convert batch into training data using bellman's equation
        :param batch: list of tuples with Experience instances 
        :return: 
        """
        states = []
        q_vals = []
        for exps in batch:
            # calculate q_values for first and last state in experience sequence
            # first is needed for reference, last is used to approximate rest value
            v = Variable(torch.from_numpy(np.array([exps[0].state, exps[-1].state], dtype=np.float32)))
            if cuda_enabled:
                v = v.cuda()
            q = model(v)
            # accumulate total reward for the chain
            total_reward = 0.0 if exps[-1].done else q[1].data.max()
            for exp in reversed(exps[:-1]):
                total_reward = exp.reward + GAMMA * total_reward
            train_state = exps[0].state
            train_q = q[0].data
            train_q[exps[0].action] = total_reward
            states.append(train_state)
            q_vals.append(train_q)
        return torch.from_numpy(np.array(states, dtype=np.float32)), torch.stack(q_vals)

    losses = []
    mean_q = []

    for idx in range(10000):
        exp_replay.populate(run.getint("exp_buffer", "populate"))

        for batch in exp_replay.batches(run.getint("learning", "batch_size")):
            optimizer.zero_grad()

            # populate buffer
            states, q_vals = batch_to_train(batch)
            # ready to train
            states, q_vals = Variable(states), Variable(q_vals)
            if cuda_enabled:
                states = states.cuda()
                q_vals = q_vals.cuda()
            l = loss_fn(model(states), q_vals)
            losses.append(l.data[0])
            mean_q.append(q_vals.mean().data[0])
            l.backward()
            optimizer.step()

        action_selector.epsilon *= run.getfloat("defaults", "epsilon_decay")

        if idx % 10 == 0:
            total_rewards = exp_source.pop_total_rewards()
            if total_rewards:
                mean_reward = np.mean(total_rewards)
                print("%d: Mean reward: %.2f, done: %d, epsilon: %.4f" % (
                    idx, mean_reward, len(total_rewards), action_selector.epsilon
                ))
                if mean_reward > run.getfloat("defaults", "stop_mean_reward", fallback=2*mean_reward):
                    print("We've reached mean reward bound, exit")
                    break
            else:
                print("%d: no reward info, epsilon: %.4f" % (idx, action_selector.epsilon))
    env.close()
    pass
