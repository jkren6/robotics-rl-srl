"""
Random Search: randomly sample actions from the action space
"""
import time
import torch as th
import numpy as np
from torch.autograd import Variable
from baselines.common.vec_env.subproc_vec_env import SubprocVecEnv

import environments.kuka_button_gym_env as kuka_env
from pytorch_agents.model import CNNPolicy, MLPPolicy
from pytorch_agents.visualize import load_csv
from pytorch_agents.envs import make_env


def customArguments(parser):
    """
    :param parser: (ArgumentParser Object)
    :return: (ArgumentParser Object)
    """
    parser.add_argument('--num_cpu', help='Number of processes', type=int, default=1)
    parser.add_argument('--num_eval', help='Number of episode to evaluate policy', type=int, default=20)
    parser.add_argument('--no-cuda', action='store_true', default=False,
                        help='disables CUDA training')
    parser.add_argument('--no-deterministic', action='store_true', default=False,
                        help='Enable Stochastic Policy')
    return parser

# TODO: check Uber paper to init network like them
def initNetwork(args, env, obs_shape):
    """
    :param args: (argparse.Namespace Object)
    :param env: (gym env)
    :param ob_space: (numpy tensor)
    :return: (Pytorch Model)
    """
    if len(env.observation_space.shape) == 3:
        actor_critic = CNNPolicy(obs_shape[0], env.action_space, False, input_dim=obs_shape[1])
    else:
        actor_critic = MLPPolicy(obs_shape[0], env.action_space)

    if args.cuda:
        actor_critic.cuda()
    return actor_critic


def update_current_obs(current_obs, obs, num_stack, env):
    """
    Update the current observation:
    Convert numpy array to torch tensor and stack observations if needed
    :param current_obs: (Torch Tensor)
    :param obs: (Numpy tensor)
    :param num_stack: (int)
    :param env: (gym env object)
    :return: (Torch Tensor)
    """
    shape_dim0 = env.observation_space.shape[0]
    obs = th.from_numpy(obs).float()
    if num_stack > 1:
        current_obs[:, :-shape_dim0] = current_obs[:, shape_dim0:]
    current_obs[:, -shape_dim0:] = obs
    return current_obs


def computeMeanReward(log_dir, n_done):
    """
    :param log_dir: (str)
    :param n_done: (int)
    """
    result, _ = load_csv(log_dir)
    y = np.array(result)[:, 1]
    return y[-n_done:].mean()


def main(args, callback=None):
    """
    :param args: (argparse.Namespace Object)
    :param callback: (function)
    """
    args.cuda = not args.no_cuda and th.cuda.is_available()
    args.deterministic = not args.no_deterministic

    # Create Environments and wraps them for monitoring/multiprocessing
    envs = [make_env(args.env, 0, i, args.log_dir, pytorch=True)
            for i in range(args.num_cpu)]
    envs = SubprocVecEnv(envs)

    obs_shape = envs.observation_space.shape
    if len(obs_shape) > 0:
        obs_shape = (obs_shape[0] * args.num_stack, *obs_shape[1:])
    else:
        obs_shape = (args.num_stack, *obs_shape[0])

    actor_critic = initNetwork(args, envs, obs_shape)

    current_obs = th.zeros(args.num_cpu, *obs_shape)
    if args.cuda:
        current_obs = current_obs.cuda()

    obs = envs.reset()
    current_obs = update_current_obs(current_obs, obs, args.num_stack, envs)
    start_time = time.time()
    best_return = - np.inf
    n_done, mean_reward = 0, 0

    # TODO: reset env for multi-cpu
    for step in range(args.num_timesteps):

        # Sample actions
        _, action, _, _ = actor_critic.act(Variable(current_obs, volatile=True), None, None,
                                           deterministic=args.deterministic)
        cpu_actions = action.data.squeeze(1).cpu().numpy()
        obs, reward, done, info = envs.step(cpu_actions)

        n_done += sum(done)
        if n_done > args.num_eval:
            # Evaluate network performance
            mean_reward = computeMeanReward(args.log_dir, n_done)
            # Save Best model
            if mean_reward > best_return:
                print("Saving best model")
                best_return = mean_reward
                if args.cuda:
                    actor_critic.cpu()
                th.save(actor_critic.state_dict(), "{}/random_search.pth".format(args.log_dir))
                if args.cuda:
                    actor_critic.cuda()
            # Initialize a new network
            actor_critic = initNetwork(args, envs, obs_shape)
            n_done = 0

        current_obs = update_current_obs(current_obs, obs, args.num_stack, envs)

        if callback is not None:
            callback(locals(), globals())
        if (step + 1) % 500 == 0:
            total_steps = step * args.num_cpu
            print("{} steps - {:.2f} FPS".format(total_steps, total_steps / (time.time() - start_time)))
            print("Best mean reward: {} - Last mean reward per episode: {}".format(best_return, mean_reward))
