# !/usr/bin/python

'''
Script for running myopic experiments using the run_sim bash script.
Generally a function of convenience in the event of parallelizing simulation runs.
Note: some of the parameters may need to be set prior to running the bash script.

License: MIT
Maintainers: Genevieve Flaspohler and Victoria Preston
'''
import os
import time
import sys
import logging
import numpy as np
import scipy as sp

import pdb

import aq_library as aqlib
import mcts_library as mctslib
import gpmodel_library as gplib 
import evaluation_library as evalib 
import paths_library as pathlib 
import envmodel_library as envlib 
import robot_library as roblib
import obstacles as obslib
import bag_utils as baglib

from scipy.spatial import distance


print "User specified options: SEED, REWARD_FUNCTION, PATHSET, USE_COST, NONMYOPIC, GOAL_ONLY, TREE_TYPE, RUN_REAL"
# Allow selection of seed world to be consistent, and to run through reward functions
SEED =  int(sys.argv[1])
# SEED = 0 
REWARD_FUNCTION = sys.argv[2]
PATHSET = sys.argv[3]
USE_COST = (sys.argv[4] == "True")
NONMYOPIC = (sys.argv[5] == "True")
GOAL_ONLY = (sys.argv[6] == "True")
TREE_TYPE = sys.argv[7] # one of dpw or belief
RUN_REAL_EXP = (sys.argv[8] == "True") # one of dpw or belief

if RUN_REAL_EXP:
    # MAX_COLOR = 1.50
    # MIN_COLOR = -1.80
    MAX_COLOR = None
    MIN_COLOR = None
    ranges = (0.0, 50.0, 0.0, 50.0)
else:
    MAX_COLOR = 25.0
    MIN_COLOR = -25.0
    ranges = (0.0, 0.0, 10.0, 10.0)
# MAX_COLOR = None
# MIN_COLOR = None

# Parameters for plotting based on the seed world information
# Set up paths for logging the data from the simulation run
if not os.path.exists('./figures/' + str(REWARD_FUNCTION)): 
    os.makedirs('./figures/' + str(REWARD_FUNCTION))
logging.basicConfig(filename = './figures/'+ REWARD_FUNCTION + '/robot.log', level = logging.INFO)
logger = logging.getLogger('robot')

# Create a random enviroment sampled from a GP with an RBF kernel and specified hyperparameters, mean function 0 
# The enviorment will be constrained by a set of uniformly distributed  sample points of size NUM_PTS x NUM_PTS

# Create obstacle world
ow = obslib.FreeWorld()
# ow = obslib.ChannelWorld(ranges, (3.5, 7.), 3., 0.3)
# ow = obslib.BugTrap(ranges, (2.2, 3.0), 4.6, orientation = 'left', width = 5.0)
# ow = obslib.BlockWorld(ranges,12, dim_blocks=(1., 1.), centers=[(2.5, 2.5), (7.,4.), (5., 8.), (8.75, 6.), (3.5,6.), (6.,1.5), (1.75,5.), (6.2,6.), (8.,8.5), (4.2, 3.8), (8.75,2.5), (2.2,8.2)])


if RUN_REAL_EXP:
    ''' Bagging '''
    xfull, zfull = baglib.read_fulldataset()

    # Add subsampled data from a previous bagifle
    seed_bag = '/home/genevieve/mit-whoi/barbados/rosbag_15Jan_slicklizard/slicklizard_2019-01-15-20-22-16.bag'
    xseed, zseed = baglib.read_bagfile(seed_bag, 20)
   
    # PLUMES trials
    seed_bag = '/home/genevieve/mit-whoi/barbados/rosbag_16Jan_slicklizard/slicklizard_2019-01-17-03-01-44.bag'
    xobs, zobs = baglib.read_bagfile(seed_bag, 1)
    trunc_index = baglib.truncate_by_distance(xobs, dist_lim = 1000.0)
    print "PLUMES trunc:", trunc_index
    xobs = xobs[0:trunc_index, :]
    zobs = zobs[0:trunc_index, :]

    # Myopic trials
    # seed_bag = '/home/genevieve/mit-whoi/barbados/rosbag_16Jan_slicklizard/slicklizard_2019-01-17-03-43-09.bag'
    # xobs, zobs = baglib.read_bagfile(seed_bag, 1)
    # trunc_index = baglib.truncate_by_distance(xobs, dist_lim = 1000.0)
    # print "myopic trunc:", trunc_index
    # xobs = xobs[0:trunc_index, :]
    # zobs = zobs[0:trunc_index, :]

    # Lawnmower trials
    # seed_bag = '/home/genevieve/mit-whoi/barbados/rosbag_16Jan_slicklizard/slicklizard_2019-01-16-16-12-40.bag'
    # xobs, zobs = baglib.read_bagfile(seed_bag, 1)
    # trunc_index = baglib.truncate_by_distance(xobs, dist_lim = 1000.0)
    # print "Lawnmower trunc:", trunc_index
    # xobs = xobs[0:trunc_index, :]
    # zobs = zobs[0:trunc_index, :]

    xobs = np.vstack([xseed, xobs])
    zobs = np.vstack([zseed, zobs])

    # Create the GP model
    gp_world = gplib.GPModel(ranges, lengthscale = 4.0543111858072445, variance = 0.3215773006606948, noise = 0.0862445597387173)
    gp_world.add_data(xfull[::5], zfull[::5])

    # VAR = 0.3215773006606948
    # LEN = 4.0543111858072445
    # NOISE = 0.0862445597387173

    LEN = 2.0122 
    VAR = 5.3373 / 10.0
    NOISE = 0.19836 / 10.0
else:
    gp_world = None
    # VAR = 50.0
    # LEN = 5.0
    # NOISE = 0.1
    VAR = 100.0
    LEN = 1.0
    NOISE = 1.0

world = envlib.Environment(ranges = ranges,
                           NUM_PTS = 20, 
                           variance = VAR,
                           lengthscale = LEN,
                           noise = NOISE,
                           visualize = True,
                           seed = SEED,
                           MAX_COLOR = MAX_COLOR,
                           MIN_COLOR = MIN_COLOR,
			   model = gp_world,
                           obstacle_world = ow)

# Create the evaluation class used to quantify the simulation metrics
evaluation = evalib.Evaluation(world = world, reward_function = REWARD_FUNCTION)

# Generate a prior dataset
'''
x1observe = np.linspace(ranges[0], ranges[1], 5)
x2observe = np.linspace(ranges[2], ranges[3], 5)
x1observe, x2observe = np.meshgrid(x1observe, x2observe, sparse = False, indexing = 'xy')  
data = np.vstack([x1observe.ravel(), x2observe.ravel()]).T
observations = world.sample_value(data)
'''

print "Creating robot!"
# Create the point robot
robot = roblib.Robot(sample_world = world.sample_value, #function handle for collecting observations
                     start_loc = (5.0, 5.0, 0.0), #where robot is instantiated
                     extent = ranges, #extent of the explorable environment
                     MAX_COLOR = MAX_COLOR,
                     MIN_COLOR = MIN_COLOR,
                     kernel_file = None,
                     kernel_dataset = None,
                     # prior_dataset =  (data, observations), 
                     prior_dataset =  (xobs, zobs), 
                     # prior_dataset = None,
                     init_lengthscale = LEN,
                     init_variance = VAR,
                     noise = NOISE,
                     # init_lengthscale = 2.0122,
                     # init_variance = 5.3373,
                     # noise = 0.19836,
                     # noise = float(sys.argv[1]),
                     path_generator = PATHSET, #options: default, dubins, equal_dubins, fully_reachable_goal, fully_reachable_step
                     goal_only = GOAL_ONLY, #select only if using fully reachable step and you want the reward of the step to only be the goal
                     frontier_size = 10,
                     horizon_length = 1.50, 
                     turning_radius = 0.11,
                     sample_step = 0.1,
                     evaluation = evaluation, 
                     f_rew = REWARD_FUNCTION, 
                     create_animation = True, #logs images to the file folder
                     learn_params = False, #if kernel params should be trained online
                     nonmyopic = NONMYOPIC,
                     discretization = (20, 20), #parameterizes the fully reachable sets
                     use_cost = USE_COST, #select if you want to use a cost heuristic
                     computation_budget = 250,
                     rollout_length = 1,
                     obstacle_world = ow, 
                     tree_type = TREE_TYPE) 

print "Done creating robot!"


if RUN_REAL_EXP:
    print "Evaluting!"

    true_val = np.array(world.max_val).reshape((-1, 1))
    true_loc = np.array(world.max_loc).reshape((-1, 2))
    
    print [x for x in robot.GP.xvals]
    # distance = [np.sqrt((x[0, 0] - true_loc[0])**2 + (x[0, 1] - true_loc[1])**2) for x in robot.GP.xvals]
    # print distance
    # distance_close = (distance < 10.0)
    # print "Proportion within epsilon:", float(len(distance_close)) / float(len(distance))

    # distance_z = [np.sqrt((float(z[0]) - true_val)**2) for z in robot.GP.zvals]
    # distance_close_z = (distance_z < 0.5)
    # print "Proportion within delta:", float(len(distance_close_z)) / float(len(distance_z))


    max_vals, max_locs, func = aqlib.sample_max_vals(robot.GP, t = 0, nK = 100, obstacles = ow)

    max_vals=  np.array(max_vals).reshape((-1, 1))
    max_locs = np.array(max_locs).reshape((-1, 2))

    dist_loc = distance.cdist(max_locs, true_loc, 'euclidean')
    dist_val = distance.cdist(max_vals, true_val, 'euclidean')

    print "Distance mean location:", np.mean(dist_loc), "\t Value:", np.mean(dist_val)

    loc_kernel = sp.stats.gaussian_kde(max_locs.T)
    loc_kernel.set_bandwidth(bw_method=LEN)

    density_loc = loc_kernel(max_locs.T)
    density_loc = density_loc / np.sum(density_loc)
    entropy_loc = -np.mean(np.log(density_loc))
    print density_loc
    print "Entropy of star location:", entropy_loc

    val_kernel = sp.stats.gaussian_kde(max_vals.T)
    val_kernel.set_bandwidth(bw_method='silverman')

    density_val = val_kernel(max_vals.T)
    density_val = density_val / np.sum(density_val)
    entropy_val = -np.mean(np.log(density_val))
    print "Entropy of star value:", entropy_val

    robot.planner(T = 1)
    pdb.set_trace()
else:
    robot.planner(T = 150)

    robot.visualize_world_model(screen = True)
    robot.visualize_trajectory(screen = False) #creates a summary trajectory image
    robot.plot_information() #plots all of the metrics of interest

