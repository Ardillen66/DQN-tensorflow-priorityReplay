"""Code from https://github.com/tambetm/simple_dqn/blob/master/src/replay_memory.py"""

import os
import random
import logging
import numpy as np

from .utils import save_npy, load_npy
from prioritized_experience_replay.rank_based import Experience


class ReplayMemory(object):
  """
  Abstract super class for replay memory.
  Defines required methods and configuration
  All implementations need to implement this class
  """

  def __init__(self, config, model_dir):
    raise NotImplementedError('Subclasses must override initialization!')

  def add(self, previous, reward, action, next, terminal):
    raise NotImplementedError('Subclasses must override add!')

  def sample(self, step):
    raise NotImplementedError('Subclasses must override sample!')

  def save(self):
    raise NotImplementedError('Subclasses must override save!')

  def load(self):
    raise NotImplementedError('Subclasses must override load!')




class ReplayUniform(ReplayMemory):
  def __init__(self, config, model_dir):
    self.model_dir = model_dir

    self.cnn_format = config.cnn_format
    self.memory_size = config.memory_size
    self.actions = np.empty(self.memory_size, dtype = np.uint8)
    self.rewards = np.empty(self.memory_size, dtype = np.integer)
    self.screens = np.empty((self.memory_size, config.screen_height, config.screen_width), dtype = np.float16)
    self.terminals = np.empty(self.memory_size, dtype = np.bool)
    self.history_length = config.history_length
    self.dims = (config.screen_height, config.screen_width)
    self.batch_size = config.batch_size
    self.count = 0
    self.current = 0

    # pre-allocate prestates and poststates for minibatch
    self.prestates = np.empty((self.batch_size, self.history_length) + self.dims, dtype = np.float16)
    self.poststates = np.empty((self.batch_size, self.history_length) + self.dims, dtype = np.float16)

  def add(self, previous, reward, action, next, terminal):
    assert next.shape == self.dims
    # NB! screen is post-state, after action and reward
    self.actions[self.current] = action
    self.rewards[self.current] = reward
    self.screens[self.current, ...] = next
    self.terminals[self.current] = terminal
    self.count = max(self.count, self.current + 1)
    self.current = (self.current + 1) % self.memory_size

  def getState(self, index):
    assert self.count > 0, "replay memory is empy, use at least --random_steps 1"
    # normalize index to expected range, allows negative indexes
    index = index % self.count
    # if is not in the beginning of matrix
    if index >= self.history_length - 1:
      # use faster slicing
      return self.screens[(index - (self.history_length - 1)):(index + 1), ...]
    else:
      # otherwise normalize indexes and use slower list based access
      indexes = [(index - i) % self.count for i in reversed(range(self.history_length))]
      return self.screens[indexes, ...]

  def sample(self, step): #Step not used here, but passed for generic purposes
    # memory must include poststate, prestate and history
    assert self.count > self.history_length
    # sample random indexes
    indexes = []
    while len(indexes) < self.batch_size:
      # find random index 
      while True:
        # sample one index (ignore states wraping over 
        index = random.randint(self.history_length, self.count - 1)
        # if wraps over current pointer, then get new one
        if index >= self.current and index - self.history_length < self.current:
          continue
        # if wraps over episode end, then get new one
        # NB! poststate (last screen) can be terminal state!
        if self.terminals[(index - self.history_length):index].any():
          continue
        # otherwise use this index
        break
      
      # NB! having index first is fastest in C-order matrices
      self.prestates[len(indexes), ...] = self.getState(index - 1)
      self.poststates[len(indexes), ...] = self.getState(index)
      indexes.append(index)

    actions = self.actions[indexes]
    rewards = self.rewards[indexes]
    terminals = self.terminals[indexes]

    if self.cnn_format == 'NHWC':
      return np.transpose(self.prestates, (0, 2, 3, 1)), actions, \
        rewards, np.transpose(self.poststates, (0, 2, 3, 1)), terminals
    else:
      return self.prestates, actions, rewards, self.poststates, terminals

  def save(self):
    for idx, (name, array) in enumerate(
        zip(['actions', 'rewards', 'screens', 'terminals', 'prestates', 'poststates'],
            [self.actions, self.rewards, self.screens, self.terminals, self.prestates, self.poststates])):
      save_npy(array, os.path.join(self.model_dir, name))

  def load(self):
    for idx, (name, array) in enumerate(
        zip(['actions', 'rewards', 'screens', 'terminals', 'prestates', 'poststates'],
            [self.actions, self.rewards, self.screens, self.terminals, self.prestates, self.poststates])):
      array = load_npy(os.path.join(self.model_dir, name))


class ReplayRanked(ReplayMemory):
  """
  Wrapper class for prioirity replay to match base class
  """
  

  def __init__(self, config, model_dir):
    self.model_dir = model_dir
    self.cnn_format = config.cnn_format
    self.dims = (config.screen_height, config.screen_width)
    self.history_length = config.history_length

    #Covert configuration to appropriate one for prioritized replay memory
    expConfig = {
      'size': config.memory_size,
      'batch_size': config.batch_size,
      'learn_start': config.learn_start,
      'total_steps': config.max_step #Is this the same? TODO: check this
    }
    self.exp = Experience(expConfig)

    self.count = 0
    self.current = 0

  def add(self, previous, reward, action, next, terminal):
    assert next.shape == self.dims
    #Convert stored experience to tuple
    experience = (previous, action, reward, next, terminal)
    self.exp.store(experience)

    self.count = max(self.count, self.current + 1)
    self.current = (self.current + 1) % self.memory_size

  def sample(self, step):

    # memory must include poststate, prestate and history
    assert self.count > self.history_length

    actions, rewards, terminals, prestates, poststates, td_errs = []
    
    experience, w, exp_indices = self.exp.sample(step)

    for i in range(self.batch_size):
      sample = experience[i]
      prestates.append(sample[0])
      actions.append(sample[1])
      rewards.append(sample[2])
      poststates.append(sample[3])
      terminals.append(sample[4])
      td_errs.append(self.compute_tde())


    if self.cnn_format == 'NHWC':
      return np.transpose(self.prestates, (0, 2, 3, 1)), actions, \
        rewards, np.transpose(self.poststates, (0, 2, 3, 1)), terminals
    else:
      return prestates, actions, rewards, poststates, terminals, w, exp_indices

  def save(self):
    if not os.path.exists(self.model_dir):
      os.makedirs(self.model_dir)
    save_pkl(experience, os.path.join(self.model_dir, 'experience.pkl'))

  def load(self):
    self.exp = load_pkl(path)
    