#!/usr/bin/env python
""" online_modeling.py: 

Online modeling using deep neural networks.

# VERSION UPDATES
0.0.2 (Apr/02/2018) : added validation set inside memory buffer class so model
                      can be  evaluated using unseen data.
0.0.3 (Apr/12/2018) : enabled model comparison method (mostly between initial
                      and final model generated)
0.0.4 (Apr/17/2018) : added argparse

"""
__author__ = "Vinicius G. Goecks"
__version__ = "0.0.4"
__date__ = "April 17, 2018"

# import
import numpy as np
import matplotlib.pyplot as plt

import argparse
import sys, os
import time
import threading
import gym

from keras.models import Sequential, load_model
from keras.layers import Dense, Dropout, LSTM
from keras.optimizers import SGD


class TestController(object):
    """ Controller that takes random actions
    """

    def __init__(self, env):
        self.env = env
        self.action_space = env.action_space

    def act(self, state):
        """ Compute controls
        """
        control = self.action_space.sample()
        return control


class MemoryBuffer(object):
    """ Stores experienced states and controls for modeling.

    Experiences are defined as:
    [current_states, control_applied] -> [(next_states - current_states)]

    This class also handles the creation of a validation set. This is filled 
    before the memory buffer so the learned model can be evaluated on unseen
    data (not the same data from the memory buffer that is used to improve 
    the model).

    Arguments
    ==================
    env: plant to model
    buffer_size: number of experiences to store
    """

    def __init__(self, env, buffer_size=100, val_data_size=100):
        self.env = env
        self.buffer_size = buffer_size
        self.val_data_size = val_data_size

        # create buffer
        self.n_states = env.observation_space.shape[0]
        self.n_controls = env.action_space.shape[0]
        self.n_inputs = self.n_states + self.n_controls
        self.n_outputs = self.n_states

        self.buffer = np.zeros(self.buffer_size,
                               dtype=[('data_in', np.float32,
                                       (self.n_inputs,)),
                                      ('data_out', np.float32,
                                       (self.n_outputs,))])

        self.buffer_counter = 0  # counts idx of where
        # current experience should be placed

        self.buffer_filled = False  # flag becomes true when reset counter for
        # the first time

        # create validation set
        self.val_data = np.zeros(self.val_data_size,
                                 dtype=[('data_in', np.float32,
                                         (self.n_inputs,)),
                                        ('data_out', np.float32,
                                         (self.n_outputs,))])
        self.val_data_counter = 0
        self.val_data_filled = False

    def add_to_buffer(self, current_state, control, next_state):
        """ Organize data to fit buffer and manage number of experiences added.

        Initially fills the validation data set, then fills the memory buffer.
        """
        # check first if validation set is filled
        if not self.val_data_filled:
            # add current experience to validation set
            # simplify notation
            idx = self.val_data_counter

            # add inputs (current_state, control)
            self.val_data[idx][0] = np.hstack([current_state, control])

            # add output (next_state - current_state)
            self.val_data[idx][1] = np.array([next_state - current_state])

            # increase buffer_counter
            self.val_data_counter += 1

            # if validation data is full, raise flag to stop
            if self.val_data_counter == self.val_data_size:
                print('[*] Filled validation set.')
                self.val_data_filled = True

        else:  # validation set is full, fills memory buffer
            # if buffer full, overwrite older experiences
            if self.buffer_counter >= self.buffer_size:
                # reset buffer_counter
                self.buffer_counter = 0
                self.buffer_filled = True

            # simplify notation
            idx = self.buffer_counter

            # add inputs (current_state, control)
            self.buffer[idx][0] = np.hstack([current_state, control])

            # add output (next_state - current_state)
            self.buffer[idx][1] = np.array([next_state - current_state])

            # increase buffer_counter
            self.buffer_counter += 1

    def generate_batch(self, batch_size=1, shuffle=False):
        """ Sample and return batch of experiences
        """
        # check if buffer is filled
        if self.buffer_filled:
            # start batch from anywhere in the buffer
            initial_idx = np.random.randint(
                low=0,
                high=(self.buffer_size - batch_size))
            final_idx = initial_idx + batch_size

        # if not, check if at least have enough experiences to return batch
        elif batch_size < self.buffer_counter:
            # start batch from anywhere in the filled part of buffer
            initial_idx = np.random.randint(
                low=0,
                high=(self.buffer_counter - batch_size))
            final_idx = initial_idx + batch_size
        else:
            # buffer doest have enough data to generate batch
            return None, None

        # sample buffer
        input_data = self.buffer['data_in'][initial_idx:final_idx]
        output_data = self.buffer['data_out'][initial_idx:final_idx]

        return input_data, output_data


class ThreadingModeling(object):
    """ Running the modeling functions on the background:

    The run() method will be started and it will run in the background
    until the application exits.

    The updated model is queried whenever it is needed.
    """

    def __init__(self, memory_buffer, batch_size=1, update_model_dt=0,
                 run_id='test'):
        # keep track of current epi and time step to know model is updated
        self.run_id = run_id
        self.epi_n = 0
        self.step_n = 0
        self.track_model = []
        self.hist_train = []

        # initialize memory buffer
        self.memory = memory_buffer

        # create initial model
        self.batch_size = batch_size
        self.update_model_dt = update_model_dt  # how often should be model
        # be updated (sec) (assuming we
        #  have CPU power for that)
        self.__init_model()

        # run model updates in the background forever
        self.thread = threading.Thread(target=self.__update_model, args=())
        self.thread.daemon = True    # kills background thread when main
        self.keep_computing_model = True  # flag to stop daemon
        # function is over
        self.thread.start()

    def __init_model(self):
        """ Initialize pre-defined model.
        """
        print('[*] Initializing model...')
        model = Sequential()

        # model.add(Dropout(0.2, input_shape=(input_dim,)))
        model.add(Dense(220,
                        input_shape=(self.memory.n_inputs,),
                        kernel_initializer='normal',
                        activation='relu'))

        # model.add(Dropout(0.2))
        model.add(Dense(160, kernel_initializer='normal', activation='relu'))
        model.add(Dropout(0.2))
        model.add(Dense(130, kernel_initializer='normal', activation='relu'))
        model.add(Dropout(0.2))
        model.add(Dense(self.memory.n_outputs,
                        kernel_initializer='normal', activation='linear'))

        # compile model
        model.compile(loss='mse', optimizer='adam')

        # save model internally and dump on file
        self.model = model
        self.model.save('./experiments/' + self.run_id + '/initial_model.h5')

    def __update_model(self):
        """ Receive new batch of data and update model.
        """
        while self.keep_computing_model:
            start_time = time.time()

            # receive new data
            input_data, output_data = self.memory.generate_batch(
                batch_size=self.batch_size)

            # only update model when validation set is ready to use
            if self.memory.val_data_filled:
                if input_data is not None:
                    # update model if data is not None

                    # prepare validation data
                    val_input = self.memory.val_data['data_in']
                    val_output = self.memory.val_data['data_out']

                    # # TODO: both options below seem to work fine, but need to do
                    # # some research (or testing) to see if they are equivalent
                    # option 1
                    hist = self.model.fit( input_data, output_data, epochs=1,
                                   steps_per_epoch=1, verbose=0,
                                   validation_data=(val_input, val_output),
                                   validation_steps=1)
                    # # option 2
                    # self.model.train_on_batch(input_data, output_data)

                    # update list that tracks when model was updated
                    # print('[*] Model updated.')
                    self.track_model.append((self.epi_n+1, self.step_n))

                    # save fit history
                    self.hist_train.append( (hist.history['loss'][0],
                                             hist.history['val_loss'][0]) )

                # follow specified time delay
                time_compute = time.time() - start_time
                if time_compute < self.update_model_dt:
                    # computed too fast, way a bit to follow dt
                    time.sleep(self.update_model_dt - time_compute)

    def compare_models(self):
        """After training, load different models and plot time history
        of their predictions so one can visually compare them.
        """
        # list models
        model_names = ['/initial_model.h5', '/final_model.h5']

        # prepare validation data
        val_input = self.memory.val_data['data_in']
        val_states = val_input[:,0:self.memory.n_states]
        val_controls = val_input[:,-self.memory.n_controls]
        n_steps = self.memory.val_data_size

        # make sure arrays have same structure
        val_states = val_states.reshape(n_steps, self.memory.n_states)
        val_controls = val_controls.reshape(n_steps, self.memory.n_controls)

        # MAIN LOOP
        for i in range(len(model_names)):
            # load model
            model = load_model('./experiments/' + self.run_id + model_names[i])
            pred_states = np.zeros((n_steps, self.memory.n_states))

            # step-by-step prediction (using validation data)
            current_state = val_states[0,:]
            control = val_controls[0,:]

            # store data
            pred_states[0,:] = current_state

            for j in range(1,n_steps):

                # predict next states
                # format input data and predict different in next states
                input_data = np.hstack((current_state, control))
                delta_next_state = model.predict(input_data.reshape(
                    1, self.memory.n_inputs))

                # return next states
                next_state = current_state + delta_next_state[0]

                # update states and controls
                current_state = next_state
                control = val_controls[j,:]

                # store data
                pred_states[j,:] = current_state

            # plot predicted data            
            plt.figure()
            
            # plot states
            for l in range(self.memory.n_states):
                plt.subplot(self.memory.n_states+1, 1, l+1)
                if l == 0:
                    plt.title('Model comparison: {}'.format(model_names[i]))

                plt.plot(val_states[:, l], '-', label='x{}'.format(l))
                plt.plot(pred_states[:, l], '--', label='pred_x{}'.format(l))
                plt.grid()
                plt.legend(loc='best')

            # plot controls
            plt.subplot(self.memory.n_states+1, 1, self.memory.n_states+1)
            for m in range(n_controls):
                plt.plot(val_controls[:, m], label='u{}'.format(m))

            plt.grid()
            plt.legend(loc='best')



    def predict_next_states(self, current_state, control):
        """ Predict next states using current model based on current states and
        control performed.
        """
        # format input data and predict different in next states
        input_data = np.hstack((current_state, control))
        delta_next_state = self.model.predict(input_data.reshape(
            1, self.memory.n_inputs))

        # return next states
        next_state = current_state + delta_next_state[0]
        return next_state

    def close(self):
        """ Raise flag to stop daemon thread.
        """
        self.keep_computing_model = False


if __name__ == '__main__':
    """ Testing controller on a simple OpenAI Gym environment: Pendulum-v1

    More info : https://github.com/openai/gym/wiki/Pendulum-v0

    Summary:

    States [low_bound, high_bound]:
    =================================
    x0 = cos(theta) [-1.0, 1.0]
    x1 = sin(theta) [-1.0, 1.0]
    x2 = theta dot  [-8.0, 8.0]

    Initial States
    =================================
    Random angle from -pi to pi, and random velocity between -1 and 1

    Controls [low_bound, high_bound]:
    =================================
    u0 = torque [-2.0, 2.0]


    TODO:
    =================================
    Create a class to handle different plants (environments) or
    to run this loop (class to manage experiments).

    """

    # ARGPARSE
    ap = argparse.ArgumentParser()
    ap.add_argument('exp_name', help='experiment/log name')
    ap.add_argument('--no_plot', action='store_true',
                                     help='enables plotting')
    ap.add_argument('--n_eps', default=1, required=False,
                                     help='number of episodes')
    ap.add_argument('--n_steps', default=300, required=False,
                                     help='number of steps')
    ap.add_argument('--buffer_size', default=100, required=False,
                                     help='size of the memory buffer')
    ap.add_argument('--val_data_size', default=100, required=False,
                                     help='size of validation dataset')
    ap.add_argument('--batch_size', default=16, required=False,
                                     help='batch size for model updates')
    args = vars(ap.parse_args())
    
    # options    
    run_id = args['exp_name']
    PLOTTING = not args['no_plot']

    # define experiment id and create folder to store results
    os.system('mkdir ./experiments/{}'.format(run_id))

    # create environment (plant)
    ENV_NAME = 'Pendulum-v0'
    env = gym.make(ENV_NAME)
    n_states = env.observation_space.shape[0]
    n_controls = env.action_space.shape[0]

    # create controller
    agent = TestController(env)

    # starts modeling in the background with memory buffer
    memory = MemoryBuffer(env, buffer_size=args['buffer_size'],
                               val_data_size=args['val_data_size'])
    modeling = ThreadingModeling(memory_buffer=memory,
                                 batch_size=args['batch_size'],
                                 update_model_dt=0.5,
                                 run_id=run_id)

    # general simulation parameters
    n_episodes = args['n_eps']
    n_steps = args['n_steps']
    sim_dt = .02

    # store states (current and predicted) and actions
    # TODO:
    # create a data logger class to make it cleaner
    # format: (timestep x data) x episodes
    states = np.zeros((n_steps, n_states, n_episodes))
    pred_states = np.zeros((n_steps, n_states, n_episodes))
    controls = np.zeros((n_steps, n_controls, n_episodes))

    print('[*] Simulating...')
    for i in range(n_episodes):
        print('[*] Episode {} out of {}'.format(i+1, n_episodes))
        # get initial states
        state = env.reset()

        # log initial data
        states[0, :, i] = state
        pred_states[0, :, i] = state
        controls[0, :, i] = np.zeros(n_controls)

        for j in range(1, n_steps):
            if j%100 == 0:
                print('[*] Time step {}+/{}'.format(j,n_steps))
            start_time = time.time()
            # modeling keeps track of current epi and time step
            modeling.epi_n = i
            modeling.step_n = j

            # save current state
            current_state = state

            # compute control based on current state
            control = agent.act(state)

            # execute control and observe next states
            state, _, done, _ = env.step(control)

            # add experience to buffer
            modeling.memory.add_to_buffer(current_state, control, state)

            # use current model to predict next states
            pred_state = modeling.predict_next_states(current_state, control)

            # log data
            states[j, :, i] = state
            pred_states[j, :, i] = pred_state
            controls[j, :, i] = control

            # follow specified time delay
            time_compute = time.time() - start_time
            if time_compute < sim_dt:
                # computed too fast, way a bit to follow dt
                time.sleep(sim_dt - time_compute)

    # close everything and stop modeling
    env.close()
    modeling.close()

    # save last model and data
    modeling.model.save('./experiments/' + modeling.run_id + '/final_model.h5')
    hist_train = np.array(modeling.hist_train)
    np.save('./experiments/' + run_id + '/model_hist.npy', hist_train)

    # plot results (cycle for different episodes)
    if PLOTTING:
        # recover data from when model was updated
        track = np.array(modeling.track_model)

        plt.figure()
        plt.title('Model performance (MSE loss) on validation_data')
        plt.plot(hist_train[:,1])
        plt.xlabel('Model update #')
        plt.ylabel('MSE Loss')
        plt.grid()

        # plot saved data
        for k in range(n_episodes):
            plt.figure()

            # find when model was updated this episode
            updates = np.where(track[:, 0] == k+1)[0]
            idx_updates = track[updates, 1]   # gets the step number when the
            # model was updated this episode

            # plot states
            for l in range(n_states):
                plt.subplot(n_states+1, 1, l+1)
                if l == 0:
                    plt.title('Episode {} out of {}'.format(k+1, n_episodes))

                plt.plot(states[:, l, k], '-',
                         label='x{}'.format(l))
                plt.plot(pred_states[:, l, k], '--',
                         label='pred_x{}'.format(l))
                plt.plot(idx_updates, pred_states[idx_updates, l, k], 'kx',
                         label='new_model')
                plt.grid()
                plt.legend(loc='best')

            # plot controls
            plt.subplot(n_states+1, 1, n_states+1)
            for m in range(n_controls):
                plt.plot(controls[:, m, k], label='u{}'.format(m))

            plt.grid()
            plt.legend(loc='best')

        # compare different models used during simulation
        modeling.compare_models()

        # show or save plot
        plt.show()
