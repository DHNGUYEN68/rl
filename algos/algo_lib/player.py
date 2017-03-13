import logging
import numpy as np


class Player:
    """
    Simple syncronous pool of players
    """
    def __init__(self, env, reward_steps, gamma, max_steps, player_index, state_filter=lambda x: x):
        self.env = env
        self.reward_steps = reward_steps
        self.gamma = gamma
        self.state_filter = state_filter

        self.state = self.state_filter(env.reset())

        self.memory = []
        self.episode_reward = 0.0
        self.step_index = 0
        self.max_steps = max_steps
        self.player_index = player_index

        self.done_rewards = []

    @classmethod
    def step_players(cls, model, players):
        """
        Do one step for list of players
        :param model: model to use for predictions
        :param players: player instances
        :return: list of samples
        """
        probs, values = model.predict_on_batch(np.array([
            p.state for p in players
        ]))
        result = []
        for idx, player in enumerate(players):
            action = np.random.choice(len(probs[idx]), p=probs[idx])
            result.extend(player.step(action, values[idx][0]))
        return result

    def step(self, action, value):
        result = []
        new_state, reward, done, _ = self.env.step(action)
        self.episode_reward += reward
        self.memory.append((self.state, action, reward, value))
        self.state = self.state_filter(new_state)
        self.step_index += 1

        if done or self.step_index > self.max_steps:
            self.state = self.state_filter(self.env.reset())
            logging.info("%3d: Episode done @ step %5d, sum reward %d",
                         self.player_index, self.step_index, int(self.episode_reward))
            self.done_rewards.append(self.episode_reward)
            self.episode_reward = 0.0
            self.step_index = 0
            result.extend(self._memory_to_samples(is_done=done))
        elif len(self.memory) == self.reward_steps + 1:
            result.extend(self._memory_to_samples(is_done=False))
        return result

    def _memory_to_samples(self, is_done):
        """
        From existing memory, generate samples
        :param is_done: is episode done
        :return: list of training samples
        """
        result = []
        sum_r, last_item = 0.0, None

        if not is_done:
            last_item = self.memory.pop()
            sum_r = last_item[-1]

        for state, action, reward, value in reversed(self.memory):
            sum_r = reward + sum_r * self.gamma
            result.append((state, action, sum_r))

        self.memory = [] if is_done else [last_item]
        return result

    @classmethod
    def gather_done_rewards(cls, *players):
        """
        Collect rewards from list of players
        :param players: list of players
        :return: list of steps, list of rewards of done episodes
        """
        res = []
        for p in players:
            res.extend(p.done_rewards)
            p.done_rewards = []
        return res


def generate_batches(model, players, batch_size):
    samples = []

    while True:
        samples.extend(Player.step_players(model, players))
        while len(samples) >= batch_size:
            states, actions, rewards, advantages = list(map(np.array, zip(*samples[:batch_size])))
            yield [states, actions, advantages], [rewards, rewards]
            samples = samples[batch_size:]