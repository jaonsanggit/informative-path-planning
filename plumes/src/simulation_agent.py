# !/usr/bin/python

'''
This library allows access to the simulated robot class, which can be designed using a number of parameters.
'''
from matplotlib import pyplot as plt
from matplotlib import cm
import numpy as np
import scipy as sp
import os
import logging
import random
logger = logging.getLogger('robot')

import heuristic_rewards as aqlib
import mcts_search as mctslib
import gpmodel_library as gplib 
import mission_logger as evalib 
import generate_actions as pathlib 


class Robot(object):
    ''' The Robot class, which includes the vehicles current model of the world and IPP algorithms.'''

    def __init__(self, **kwargs):
        ''' Initialize the robot class with a GP model, initial location, path sets, and prior dataset
        Inputs:
            sample_world (method) a function handle that takes a set of locations as input and returns a set of observations
            start_loc (tuple of floats) the location of the robot initially in 2-D space e.g. (0.0, 0.0, 0.0)
            extent (tuple of floats): a tuple representing the max/min of 2D rectangular domain i.e. (-10, 10, -50, 50)
            kernel_file (string) a filename specifying the location of the stored kernel values
            kernel_dataset (tuple of nparrays) a tuple (xvals, zvals), where xvals is a Npoint x 2 nparray of type float and zvals is a Npoint x 1 nparray of type float 
            prior_dataset (tuple of nparrays) a tuple (xvals, zvals), where xvals is a Npoint x 2 nparray of type float and zvals is a Npoint x 1 nparray of type float
            init_lengthscale (float) lengthscale param of kernel
            init_variance (float) variance param of kernel
            noise (float) the sensor noise parameter of kernel 
            path_generator (string): one of default, dubins, or equal_dubins. Robot path parameterization. 
            frontier_size (int): the number of paths in the generated path set
            horizon_length (float): the length of the paths generated by the robot 
            turning_radius (float): the turning radius (in units of distance) of the robot
            sample_set (float): the step size (in units of distance) between sequential samples on a trajectory
            evaluation (Evaluation object): an evaluation object for performance metric compuation
            f_rew (string): the reward function. One of {hotspot_info, mean, info_gain, exp_info, mes}
                    create_animation (boolean): save the generate world model and trajectory to file at each timestep 
        '''

        # Parameterization for the robot
        # ENVIROMENT
        self.ranges = kwargs['extent']
        self.dim = kwargs['dimension']
        self.loc = kwargs['start_loc']
        self.time = kwargs['start_time']
        self.measure_environment = kwargs['sample_world']
        self.noise = kwargs['noise']
        self.obstacle_world = kwargs['obstacle_world']

        #BELIEF SPACE
        self.kernel = kwargs['kernel']
        self.kparams = kwargs['kparams']

        # Initialize the robot's GP model with the initial kernel parameters
        self.GP = gplib.OnlineGPModel(ranges=self.ranges,
                                      lengthscale=kwargs['init_lengthscale'],
                                      variance=kwargs['init_variance'],
                                      noise=self.noise,
                                      dim=self.dim,
                                      kernel=self.kernel,
                                      kparams=self.kparams)
        # self.GP = gplib.GPModel(ranges = self.ranges, lengthscale = kwargs['init_lengthscale'], variance = kwargs['init_variance'], noise = self.noise, dimension = self.dimension)
                
        # If both a kernel training dataset and a prior dataset are provided, train the kernel using both
        # Can only support this for RBF kernels presently
        if  kwargs['kernel_dataset'] is not None and kwargs['prior_dataset'] is not None and self.kernel == 'rbf':
            data = np.vstack([kwargs['prior_dataset'][0], kwargs['kernel_dataset'][0]])
            observations = np.vstack([kwargs['prior_dataset'][1], kwargs['kernel_dataset'][1]])
            self.GP.train_kernel(data, observations, kwargs['kernel_file']) 
        # Train the kernel using the provided kernel dataset
        elif kwargs['kernel_dataset'] is not None and self.kernel == 'rbf':
            self.GP.train_kernel(kwargs['kernel_dataset'][0], kwargs['kernel_dataset'][1], kwargs['kernel_file'])
        # If a kernel file is provided, load the kernel parameters
        elif kwargs['kernel_file'] is not None:
            self.GP.load_kernel()
        # No kernel information was provided, so the kernel will be initialized with provided values
        else:
            pass
        
        # Incorporate the prior dataset into the model
        if kwargs['prior_dataset'] is not None:
            self.GP.add_data(kwargs['prior_dataset'][0], kwargs['prior_dataset'][1]) 

        #REWARD
        self.f_rew = kwargs['f_rew']
        if self.f_rew == 'mean':
            self.aquisition_function = aqlib.mean_UCB  
        elif self.f_rew == 'info_gain':
            self.aquisition_function = aqlib.info_gain
        elif self.f_rew == 'mes':
            self.aquisition_function = aqlib.mves
        elif self.f_rew == 'exp_improve':
            self.aquisition_function = aqlib.exp_improvement
        elif self.f_rew == 'gumbel':
            self.aquisition_function = aqlib.gumbel_mves
        else:
            raise ValueError('Only \'mean\', \'info-gain\', \'exp-improve\', and \'mes\'reward fucntions supported.')

        #ACTIONS
        self.path_generator = kwargs['path_generator']

        #PLANNER
        self.tree_type = kwargs['tree_type']
        self.nonmyopic = kwargs['nonmyopic']
        print self.nonmyopic
        self.comp_budget = kwargs['computation_budget']
        self.roll_length = kwargs['rollout_length']
        
        #LOGISTICS
        self.create_animation = kwargs['create_animation'] #flagging for removal
        self.eval = kwargs['evaluation'] #flagging for removal
        self.MIN_COLOR = kwargs['MIN_COLOR']
        self.MAX_COLOR = kwargs['MAX_COLOR']
        self.running_simulation = kwargs['running_simulation'] #indicates whether running in sim or on car

        #INITIALIZATION
        self.maxes = []
        self.current_max = -1000
        self.current_max_loc = [0,0]
        self.max_locs = None
        self.max_val = None
        self.target = None

    def choose_trajectory(self, t):
        ''' Select the best trajectory avaliable to the robot at the current pose, according to the reward heuristic.
        Input: 
            t (int > 0): the current planning iteration (value of a point can change with algortihm progress)
        Output:
            either None or the (best path, best path value, all paths, all values, the max_locs for some functions)
        '''
        #initialize heusitic information
        value = {}
        param = None    
        max_locs = max_vals = None

        if self.f_rew == 'mes':
            self.max_val, self.max_locs, self.target = aqlib.sample_max_vals(self.GP, t=t, obstacles=self.obstacle_world)
            param = (self.max_val, self.max_locs, self.target)
        elif self.f_rew == 'exp_improve':
            if len(self.maxes) == 0:
                param = [self.current_max]
            else:
                param = self.maxes
        elif self.f_rew == 'gumbel':
            max_vals = aqlib.sample_max_vals_gumbel(self.GP, t=t, obstacles=self.obstacle_world)
            param = max_vals

        actions = self.path_generator.generate_trajectories(robot_pose=self.loc,
                                                            time=t,
                                                            world=self.obstacle_world,
                                                            using_sim_world=self.running_simulation)
        print 'num of available actions', len(actions)
        for path, action in enumerate(actions):
            value[path] = self.aquisition_function(time=t,
                                                   xvals=np.array(action),
                                                   robot_model=self.GP,
                                                   param=param)

        try:
            # print 'here'
            # print value
            best_key = random.choice([key for key in value.keys() if value[key] == np.nanmax(value.values())])
            return np.array(actions[best_key]), value[best_key], actions, value
        except:
            # print 'failure'
            # return None
            best_key = random.choice([key for key in value.keys()])
            return np.array(actions[best_key]), value[best_key], actions, value
    
    def collect_observations(self, xobs):
        ''' Gather noisy samples of the environment and updates the robot's GP model.
        Input: 
            xobs (float array): an nparray of floats representing observation locations, with dimension NUM_PTS x 2 '''
        zobs = self.measure_environment(xobs[:-1,:], self.time)
        self.GP.add_data(xobs[:-1,:], zobs)

        for z, x in zip (zobs, xobs[1:,:]):
            if z[0] > self.current_max:
                self.current_max = z[0]
                self.current_max_loc = [x[0],x[1]]

    def predict_max(self):
        # If no observations have been collected, return default value
        if self.GP.xvals is None:
            return np.array([0., 0.]), 0.

        # Generate a set of observations from robot model with which to predict mean
        x1vals = np.linspace(self.ranges[0], self.ranges[1], 30)
        x2vals = np.linspace(self.ranges[2], self.ranges[3], 30)
        x1, x2 = np.meshgrid(x1vals, x2vals, sparse = False, indexing = 'xy') 

        if self.dim == 2:
            data = np.vstack([x1.ravel(), x2.ravel()]).T
        elif self.dim == 3:
            data = np.vstack([x1.ravel(), x2.ravel(), self.time * np.ones(len(x1.ravel()))]).T
        observations, var = self.GP.predict_value(data)        

        return data[np.argmax(observations), :], np.max(observations)
        
    def planner(self, T):
        ''' Gather noisy samples of the environment and updates the robot's GP model  
        Input: 
            T (int > 0): the length of the planning horization (number of planning iterations)'''
        
        # initialize
        self.trajectory = []
        self.dist = 0
        
        # step through each planning iteratio in the simulation
        for t in xrange(T):
            # Select the best trajectory according to the robot's aquisition function
            self.time = t
            print "[", t, "] Current Location:  ", self.loc, "Current Time:", self.time
            logger.info("[{}] Current Location: {}".format(t, self.loc))

            # Let's figure out where the best point is in our world
            pred_loc, pred_val = self.predict_max()
            print "Current predicted max and value: \t", pred_loc, "\t", pred_val
            logger.info("Current predicted max and value: {} \t {}".format(pred_loc, pred_val))

            if self.nonmyopic is False:
                print 'Using myopic planner!'
                sampling_path, best_val, all_paths, all_values = self.choose_trajectory(t=t)
            else:
                # set params
                if self.f_rew == "exp_improve":
                    param = self.current_max
                # elif self.f_rew == "gumbel":
                #     max_vals = aqlib.sample_max_vals_gumbel(self.GP, t=t, obstacles=self.obstacle_world)
                #     param = max_vals
                    # _, pred_max = self.predict_max()
                    # param = (pred_max, 10)
                else:
                    param = None
                # create the tree search
                mcts = mctslib.cMCTS(computation_budget=self.comp_budget,
                                     belief=self.GP,
                                     initial_pose=self.loc, 
                                     rollout_length=self.roll_length, 
                                     path_generator=self.path_generator,
                                     aquisition_function=self.aquisition_function, 
                                     f_rew=self.f_rew,
                                     T=t,
                                     aq_param=param, 
                                     tree_type=self.tree_type, 
                                     obs_world=self.obstacle_world, 
                                     use_sim_world=True)
                
                sampling_path, best_val, all_paths, all_values, self.max_locs, self.max_val = mcts.choose_trajectory(t=t)
            
            print 'reward output'
            print all_values
            # Update eval metrics #TODO fix
            self.eval.update_metrics(t, self.GP, self.loc, sampling_path, \
            value=best_val, max_loc=pred_loc, max_val=pred_val, params=[self.current_max, self.current_max_loc, self.max_val, self.max_locs], dist=self.dist) 

            if sampling_path is None:
                break
            data = np.array(sampling_path)
            x1 = data[:,0]
            x2 = data[:,1]
            if self.dim == 2:
                xlocs = np.vstack([x1, x2]).T           
            elif self.dim == 3:
                # Collect observations at the current time
                xlocs = np.vstack([x1, x2, t*np.ones(len(x1))]).T           
            else:
                raise ValueError('Only 2D or 3D worlds supported!')
          
            if t % 10 == 0:
                self.visualize_reward(screen = True, filename = 'REWARD.' + str(t), t = t)

            self.collect_observations(xlocs)
            self.trajectory.append(sampling_path)

            start = self.loc
            for m in sampling_path:
                self.dist += np.sqrt((start[0]-m[0])**2 + (start[1]-m[1])**2)
                start = m
            
            # if self.create_animation:
            if t % 10 == 0:
                self.visualize_trajectory(screen = False, filename = t, best_path = sampling_path, 
                            maxes = self.max_locs, all_paths = all_paths, all_vals = all_values)            

            print 'sampling path', sampling_path
            self.loc = sampling_path[-1,:]
            print 'new_location', self.loc
        np.savetxt('./figures/' + self.f_rew+ '/robot_model.csv', (self.GP.xvals[:, 0], self.GP.xvals[:, 1], self.GP.zvals[:, 0]))

    def visualize_trajectory(self, screen=True, filename='SUMMARY', best_path=None, 
        maxes=None, all_paths=None, all_vals=None):      
        ''' Visualize the set of paths chosen by the robot 
        Inputs:
            screen (boolean): determines whether the figure is plotted to the screen or saved to file
            filename (string): substring for the last part of the filename i.e. '0', '1', ...
            best_path (path object)
            maxes (list of locations)
            all_paths (list of path objects)
            all_vals (list of all path rewards) 
            T (string or int): string append to the figure filename
        '''
        
        # Generate a set of observations from robot model with which to make contour plots
        x1vals = np.linspace(self.ranges[0], self.ranges[1], 100)
        x2vals = np.linspace(self.ranges[2], self.ranges[3], 100)
        x1, x2 = np.meshgrid(x1vals, x2vals, sparse = False, indexing = 'xy') 

        if self.dim == 2:
            data = np.vstack([x1.ravel(), x2.ravel()]).T
        elif self.dim == 3:
            data = np.vstack([x1.ravel(), x2.ravel(), self.time*np.ones(len(x1.ravel()))]).T
        
        observations, var = self.GP.predict_value(data)        
       
        # Plot the current robot model of the world
        fig, ax = plt.subplots(figsize=(8, 8))
        ax.set_xlim(self.ranges[0:2])
        ax.set_ylim(self.ranges[2:])
        # plot = ax.contourf(x1, x2, observations.reshape(x1.shape), cmap = 'viridis', vmin = self.MIN_COLOR, vmax = self.MAX_COLOR, levels=np.linspace(self.MIN_COLOR, self.MAX_COLOR, 15))
        plot = ax.contourf(x1, x2, observations.reshape(x1.shape), 25, cmap = 'viridis', vmin = self.MIN_COLOR)
        if self.GP.xvals is not None:
            scatter = ax.scatter(self.GP.xvals[:, 0], self.GP.xvals[:, 1], c='k', s = 20.0, cmap = 'viridis')                
        color = iter(plt.cm.cool(np.linspace(0,1,len(self.trajectory))))
       
        # Plot the current trajectory
        for i, path in enumerate(self.trajectory):
            c = next(color)
            f = np.array(path)
            plt.plot(f[:,0], f[:,1], c=c)

        # If available, plot the current set of options available to robot, colored
        # by their value (red: low, yellow: high)
        if all_paths is not None:
            all_vals = [x for x in all_vals.values()]   
            path_color = iter(plt.cm.autumn(np.linspace(0, max(all_vals),len(all_vals))/ max(all_vals)))        
            path_order = np.argsort(all_vals)
            
            for index in path_order:
                c = next(path_color)                
                points = all_paths[index]
                f = np.array(points)
                plt.plot(f[:,0], f[:,1], c=c)
               
        # If available, plot the selected path in green
        if best_path is not None:
            f = np.array(best_path)
            plt.plot(f[:,0], f[:,1], c='g')
           
        # If available, plot the current location of the maxes for mes
        if maxes is not None:
            for coord in maxes:
                plt.scatter(coord[0], coord[1], color = 'r', marker = '*', s = 500.0)
            # plt.scatter(maxes[:, 0], maxes[:, 1], color = 'r', marker = '*', s = 500.0)

        # If available, plot the obstacles in the world
        # if len(self.obstacle_world.get_obstacles()) != 0:
        #     for o in self.obstacle_world.get_obstacles():
        #         x,y = o.exterior.xy
        #         plt.plot(x,y,'r',linewidth=3)
           
        # Either plot to screen or save to file
        if screen:
            plt.show()           
        else:
            if not os.path.exists('./figures/' + str(self.f_rew)):
                os.makedirs('./figures/' + str(self.f_rew))
            fig.savefig('./figures/' + str(self.f_rew)+ '/trajectory-N.' + str(filename) + '.png')
            #plt.show()
            plt.close()

    def visualize_reward(self, screen=False, filename='REWARD', t=0):
        # Generate a set of observations from robot model with which to make contour plots
        x1vals = np.linspace(self.ranges[0], self.ranges[1], 100)
        x2vals = np.linspace(self.ranges[2], self.ranges[3], 100)
        x1, x2 = np.meshgrid(x1vals, x2vals, sparse = False, indexing = 'xy') # dimension: NUM_PTS x NUM_PTS       

        if self.dim == 2:
            data = np.vstack([x1.ravel(), x2.ravel()]).T
        elif self.dim == 3:
            data = np.vstack([x1.ravel(), x2.ravel(), self.time * np.ones(len(x1.ravel()))]).T

        print "Entering visualize reward"
        print data.shape

        if self.f_rew == 'mes':
            param = (self.max_val, self.max_locs, self.target)
        elif self.f_rew == 'exp_improve':
            if len(self.maxes) == 0:
                param = (self.current_max)
            else:
                param = self.maxes
        elif self.f_rew == 'gumbel':
            max_vals = aqlib.sample_max_vals_gumbel(self.GP, t=self.time, obstacles=self.obstacle_world)
            param = max_vals
        else:
            param = None
        
        reward = self.aquisition_function(time=self.time, xvals=data, robot_model=self.GP, param=param, FVECTOR=True)
        
        fig2, ax2 = plt.subplots(figsize=(8, 8))
        ax2.set_xlim(self.ranges[0:2])
        ax2.set_ylim(self.ranges[2:])        
        ax2.set_title('Reward Plot ')     

        MAX_COLOR = np.nanpercentile(reward, 100.)
        MIN_COLOR = np.nanpercentile(reward, 2.)

        if MAX_COLOR > MIN_COLOR:
            plot = ax2.contourf(x1, x2, reward.reshape(x1.shape), 25, cmap='plasma', vmin=MIN_COLOR, vmax=MAX_COLOR)
        else:
            plot = ax2.contourf(x1, x2, reward.reshape(x1.shape), 25, cmap='plasma')

        # If available, plot the current location of the maxes for mes
        if self.max_locs is not None:
            for coord in self.max_locs:
                plt.scatter(coord[0], coord[1], color='r', marker='*', s=500.0)

        if not os.path.exists('./figures/' + str(self.f_rew)):
            os.makedirs('./figures/' + str(self.f_rew))
        fig2.savefig('./figures/' + str(self.f_rew)+ '/world_model.' + str(filename) + '.png')
        plt.close()
    
    def visualize_world_model(self, screen = True, filename = 'SUMMARY'):
        ''' Visaulize the robots current world model by sampling points uniformly in space and 
        plotting the predicted function value at those locations.
        Inputs:
            screen (boolean): determines whether the figure is plotted to the screen or saved to file 
            filename (String): name of the file to be made
            maxes (locations of largest points in the world)
        '''
        # Generate a set of observations from robot model with which to make contour plots
        x1vals = np.linspace(self.ranges[0], self.ranges[1], 100)
        x2vals = np.linspace(self.ranges[2], self.ranges[3], 100)
        x1, x2 = np.meshgrid(x1vals, x2vals, sparse = False, indexing = 'xy') # dimension: NUM_PTS x NUM_PTS       

        if self.dim == 2:
            data = np.vstack([x1.ravel(), x2.ravel()]).T
        elif self.dim == 3:
            data = np.vstack([x1.ravel(), x2.ravel(), self.time * np.ones(len(x1.ravel()))]).T
        observations, var = self.GP.predict_value(data)        
        
        fig2, ax2 = plt.subplots(figsize=(8, 6))
        ax2.set_xlim(self.ranges[0:2])
        ax2.set_ylim(self.ranges[2:])        
        ax2.set_title('Countour Plot of the Robot\'s World Model')     
        # plot = ax2.contourf(x1, x2, observations.reshape(x1.shape), cmap = 'viridis', vmin = self.MIN_COLOR, vmax = self.MAX_COLOR, levels=np.linspace(self.MIN_COLOR, self.MAX_COLOR, 15))
        plot = ax2.contourf(x1, x2, observations.reshape(x1.shape), 25, cmap = 'viridis', vmin = self.MIN_COLOR, vmax = self.MAX_COLOR)

        # Plot the samples taken by the robot
        if self.GP.xvals is not None:
            scatter = ax2.scatter(self.GP.xvals[:, 0], self.GP.xvals[:, 1], c=self.GP.zvals.ravel(), s = 10.0, cmap = 'viridis')        
        if screen:
            plt.show()           
        else:
            if not os.path.exists('./figures/' + str(self.f_rew)):
                os.makedirs('./figures/' + str(self.f_rew))
            fig.savefig('./figures/' + str(self.f_rew)+ '/world_model.' + str(filename) + '.png')
            plt.close()
    
    def plot_information(self):
        ''' Visualizes the accumulation of reward and aquisition functions ''' 
        self.eval.plot_metrics()
