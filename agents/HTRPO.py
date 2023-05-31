import abc
import time
import pickle
import os
import random
import copy
import numpy as np
from collections import deque

import torch
from torch import nn
from torch.nn.utils.convert_parameters import vector_to_parameters, parameters_to_vector

from .TRPO import TRPO, TRPO_Gaussian, TRPO_Softmax
from .HPG import HPG, HPG_Gaussian, HPG_Softmax
from .config import HTRPO_CONFIG

from utils.vec_envs import space_dim
from utils.rms import RunningMeanStd
from utils.mathutils import explained_variance
from utils.viewer import VideoWriter
from utils.density_curiosity import KernalDensityEstimator, CuriosityAlphaMixture

class HTRPO(HPG, TRPO):
    __metaclass__ = abc.ABCMeta
    def __init__(self, hyperparams):
        config = copy.deepcopy(HTRPO_CONFIG)
        config.update(hyperparams)
        super(HTRPO, self).__init__(config)
        self.using_kl2 = config['using_kl2']
        self.kl_for_trpo = config['KL_esti_method_for_TRPO']
        self.using_htrpo = self.sampled_goal_num is None or self.sampled_goal_num > 0
        self.using_trpo = self.sampled_goal_num == 0 and self.using_original_data
        assert (self.using_htrpo or self.using_trpo)
        self.using_hgf_goals = config['using_hgf_goals']
        self.using_curiosity = config['using_curiosity']
        if self.using_curiosity:
            self._init_density_estimators(config["logger"])

    def _init_density_estimators(self, logger):
        self.ag_kde = KernalDensityEstimator(name="achieved_goal", logger=logger)
        self.dg_kde = KernalDensityEstimator(name="achieved_goal", logger=logger)
        self.curiosity_alpha = CuriosityAlphaMixture(ag_kde=self.ag_kde, dg_kde=self.dg_kde, logger=logger)

    def generate_subgoals(self):
        # generate subgoals from sampled data
        ags = self.achieved_goal.cpu().numpy()
        self.subgoals = np.unique(ags.round(decimals=2), axis=0)
        # - np.inf means the invalid achieved goals
        self.subgoals = self.subgoals[self.subgoals.mean(-1) > -np.inf]
        if self.sampled_goal_num is not None:
            if not self.using_hgf_goals:
                self.generate_subgoals_random()
            else:
                if not self.using_curiosity:
                    self.generate_subgoals_hgf_heuristic()
                else:
                    self.generate_subgoals_hgf_curiosity()

    def generate_subgoals_hgf_curiosity(self):
        pass

    def generate_subgoals_hgf_heuristic(self):
        dg = np.unique(self.desired_goal.cpu().numpy().round(decimals=2), axis=0)
        dg_max = np.max(dg, axis=0)
        dg_min = np.min(dg, axis=0)
        g_ind = (dg_min != dg_max)
        subgoals = self.subgoals[np.sum((self.subgoals[:, g_ind] > dg_max[g_ind]) |
                                        (self.subgoals[:, g_ind] < dg_min[g_ind]), axis = -1) == 0]
        if subgoals.shape[0] == 0:
            dist_to_dg_center = np.linalg.norm(self.subgoals - np.mean(dg, axis = 0), axis=1)
            ind_subgoals = np.argsort(dist_to_dg_center)
            self.subgoals = np.unique(np.concatenate([
                self.subgoals[ind_subgoals[:self.sampled_goal_num]], subgoals
            ], axis=0), axis=0)
        else:
            self.subgoals = subgoals

        size = min(self.sampled_goal_num, self.subgoals.shape[0])

        # initialization
        init_ind = np.random.randint(self.subgoals.shape[0])
        selected_subgoals = self.subgoals[init_ind:init_ind + 1]
        self.subgoals = np.delete(self.subgoals, init_ind, axis=0)

        # (Ng - 1) x 1
        dists = np.linalg.norm(
            np.expand_dims(selected_subgoals, axis=0) - np.expand_dims(self.subgoals, axis=1),
            axis=-1)

        for g in range(size - 1):
            selected_ind = np.argmax(np.min(dists, axis=1))
            selected_subgoal = self.subgoals[selected_ind:selected_ind + 1]
            selected_subgoals = np.concatenate((selected_subgoals, selected_subgoal), axis=0)

            self.subgoals = np.delete(self.subgoals, selected_ind, axis=0)
            dists = np.delete(dists, selected_ind, axis=0)

            new_dist = np.linalg.norm(
                np.expand_dims(selected_subgoal, axis=0) - np.expand_dims(self.subgoals, axis=1),
                axis=-1)

            dists = np.concatenate((dists, new_dist), axis=1)

        self.subgoals = selected_subgoals

    def generate_subgoals_random(self):
        # generate subgoals randomly
        ind = list(range(self.subgoals.shape[0]))
        random.shuffle(ind)
        size = min(self.sampled_goal_num, self.subgoals.shape[0])
        ind = ind[:size]
        self.subgoals = self.subgoals[ind]

    def update_curiosity(self):
        self.ag_kde.extend(self.achieved_goals.cpu().numpy())
        self.ag_kde.fit()
        self.dg_kde.extend(self.desired_goals.cpu().numpy())
        self.dg_kde.fit()
        self.curiosity_alpha.update()

    def learn(self):
        if self.using_htrpo:
            return self.learn_htrpo()
        elif self.using_trpo:
            return self.learn_trpo()
        else:
            raise RuntimeError

    def learn_trpo(self):
        self.sample_batch()
        self.split_episode()
        # No valid episode is collected
        if self.n_valid_ep == 0:
            return
        self.data_preprocess()
        self.other_data = self.goal

        # imp_fac: should be a 1-D Variable or Tensor, size is the same with a.size(0)
        imp_fac = self.compute_imp_fac()
        self.estimate_value()
        self.A = (self.A - self.A.mean()) / (self.A.std() + 1e-8)
        self.loss = - (imp_fac * self.A).mean() - self.entropy_weight * self.compute_entropy()
        if self.value_type is not None:
            # update value
            for i in range(self.iters_v):
                self.update_value()
        self.policy.zero_grad()
        loss_grad = torch.autograd.grad(
            self.loss, self.policy.parameters(), create_graph=True)
        # loss_grad_vector is a 1-D Variable including all parameters in self.policy
        loss_grad_vector = parameters_to_vector([grad for grad in loss_grad])
        # solve Ax = -g, A is Hessian Matrix of KL divergence
        trpo_grad_direc = self.conjunction_gradient( - loss_grad_vector)
        shs = .5 * torch.sum(trpo_grad_direc * self.hessian_vector_product(trpo_grad_direc))
        beta = torch.sqrt(self.max_kl / shs)
        fullstep = trpo_grad_direc * beta
        gdotstepdir = -torch.sum(loss_grad_vector * trpo_grad_direc)
        theta = self.linear_search(parameters_to_vector(
            self.policy.parameters()), fullstep, gdotstepdir * beta)
        # update policy
        vector_to_parameters(theta, self.policy.parameters())
        self.learn_step_counter += 1
        self.cur_kl = self.mean_kl_divergence().item()
        self.policy_ent = self.compute_entropy().item()
        self.update_normalizer()

    def learn_htrpo(self):
        b_t = time.time()
        self.sample_batch()
        self.split_episode()
        if self.using_curiosity:
            self.update_curiosity()
        # No valid episode is collected
        if self.n_valid_ep == 0:
            return
        self.generate_subgoals()
        if not self.using_original_data:
            self.reset_training_data()
        if self.sampled_goal_num is None or self.sampled_goal_num > 0:
            self.generate_fake_data()
        self.data_preprocess()
        self.other_data = self.goal

        # Optimize Value Estimator
        self.estimate_value()
        if self.value_type is not None:
            # update value
            for i in range(self.iters_v):
                self.update_value()

        # Optimize Policy
        # imp_fac: should be a 1-D Variable or Tensor, size is the same with a.size(0)
        # Likelihood Ratio
        # self.estimate_value()
        imp_fac = self.compute_imp_fac()

        if self.value_type:
            # old value estimator
            self.A = self.gamma_discount * self.hratio * self.A
        else:
            self.A = self.gamma_discount * self.A

        # Here mean() and sum() / self.n_traj is equivalent, because there
        # is only a coefficient between two expressions. This coefficient
        # will be compensated by the stepsize computation in TRPO. However,
        # in vanilla PG, there is no compensation, therefore, it needs to
        # be in the exact form of the euqation in the paper.
        self.loss = - (imp_fac * self.A).mean() - self.entropy_weight * self.compute_entropy()

        self.policy.zero_grad()
        loss_grad = torch.autograd.grad(
            self.loss, self.policy.parameters(), create_graph=True)
        # loss_grad_vector is a 1-D Variable including all parameters in self.policy
        loss_grad_vector = parameters_to_vector([grad for grad in loss_grad])
        # solve Ax = -g, A is Hessian Matrix of KL divergence
        trpo_grad_direc = self.conjunction_gradient(- loss_grad_vector)
        shs = .5 * torch.sum(trpo_grad_direc * self.hessian_vector_product(trpo_grad_direc))
        beta = torch.sqrt(self.max_kl / shs)
        fullstep = trpo_grad_direc * beta
        gdotstepdir = -torch.sum(loss_grad_vector * trpo_grad_direc)
        theta = self.linear_search(parameters_to_vector(
            self.policy.parameters()), fullstep, gdotstepdir * beta)
        vector_to_parameters(theta, self.policy.parameters())
        self.learn_step_counter += 1
        self.cur_kl = self.mean_kl_divergence().item()
        self.policy_ent = self.compute_entropy().item()
        self.update_normalizer()
        print("iteration time:   {:.4f}".format(time.time()-b_t))

class HTRPO_Gaussian(HTRPO, HPG_Gaussian, TRPO_Gaussian):
    def __init__(self, hyperparams):
        super(HTRPO_Gaussian, self).__init__(hyperparams)

    def mean_kl_divergence(self, inds = None, model = None):
        if inds is None:
            inds = np.arange(self.s.size(0))
        if model is None:
            model = self.policy
        mu1, logsigma1, sigma1 = model(self.s[inds], other_data=self.other_data[inds])
        if not self.using_trpo or (self.using_trpo and self.kl_for_trpo != 'origin'):
            logp = self.compute_logp(mu1, logsigma1, sigma1, self.a[inds])
            logp_old = self.logpac_old[inds].squeeze()
            # print(float((torch.abs(logp - logp_old) > 0.5).sum().item()) / float(logp.shape[0]))
            if self.using_kl2:
                mean_kl = (1 - self.gamma) * torch.sum(
                    self.hratio[inds] * self.gamma_discount * 0.5 * torch.pow((logp_old - logp), 2)) / self.n_traj
            else:
                mean_kl = (1 - self.gamma) * torch.sum(
                    self.hratio[inds] * self.gamma_discount * (logp_old - logp)) / self.n_traj
        else:
            mu2, logsigma2, sigma2 = self.mu[inds], torch.log(self.sigma[inds]), self.sigma[inds]
            sigma1 = torch.pow(sigma1, 2)
            sigma2 = torch.pow(sigma2, 2)
            mean_kl = 0.5 * (torch.sum(torch.log(sigma1) - torch.log(sigma2), dim=1) - self.n_action_dims +
                        torch.sum(sigma2 / sigma1, dim=1) + torch.sum(torch.pow((mu1 - mu2), 2) / sigma1, 1)).mean()
        return mean_kl

class HTRPO_Softmax(HTRPO, HPG_Softmax, TRPO_Softmax):
    def __init__(self, hyperparams):
        super(HTRPO_Softmax, self).__init__(hyperparams)

    def mean_kl_divergence(self, inds = None, model = None):
        if inds is None:
            inds = np.arange(self.s.size(0))
        if model is None:
            model = self.policy
        distr1 = model(self.s[inds], other_data = self.other_data[inds])
        if not self.using_trpo or (self.using_trpo and self.kl_for_trpo != 'origin'):
            logp = self.compute_logp(distr1, self.a[inds])
            logp_old = self.logpac_old[inds].squeeze()
            if self.using_kl2:
                mean_kl = (1 - self.gamma) * torch.sum(
                    self.hratio[inds] * self.gamma_discount * 0.5 * torch.pow((logp_old - logp), 2)) / self.n_traj
            else:
                mean_kl = (1 - self.gamma) * torch.sum(
                    self.hratio[inds] * self.gamma_discount * (logp_old - logp)) / self.n_traj
        else:
            distri2 = self.distri[inds].squeeze()
            logratio = torch.log(distri2 / distr1)
            mean_kl = torch.sum(distri2 * logratio, 1).mean()
        return mean_kl

def run_htrpo_train(env, agent, max_timesteps, logger, eval_interval = None, num_evals = 5, render = False):
    timestep_counter = 0
    total_updates = max_timesteps // agent.nsteps
    epinfobuf = deque(maxlen=100)
    success_history = deque(maxlen=100)
    ep_num = 0

    if render:
        img = env.render("rgb_array")
        out_dir = "./output/{}/".format(env.env_id)
        video_writer = VideoWriter(out_dir=os.path.join(out_dir, "{}_train.avi".format(env.alg)),
                                   resolution=img.shape[:2][::-1])

    if eval_interval:
        eval_ret, eval_success = agent.eval_brain(env, render=render, eval_num=num_evals)
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("eval_ep_rew:".ljust(20) + str(np.mean(eval_ret)))
        print("eval_suc_rate:".ljust(20) + str(np.mean(eval_success)))
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        logger.add_scalar("episode_reward/eval", np.mean(eval_ret), timestep_counter)
        logger.add_scalar("success_rate/eval", np.mean(eval_success), timestep_counter)

    while (True):
        mb_obs, mb_rewards, mb_actions, mb_dones, mb_logpacs, mb_obs_, mb_mus, mb_sigmas \
            , mb_distris = [], [], [], [], [], [], [], [], []

        mb_dg, mb_ag = [], []
        epinfos = []
        successes = []
        obs_dict = env.reset()

        if render:
            video_writer.add_frame(env.render("rgb_array"))

        for i in range(0, agent.nsteps, env.num_envs):
            for key in obs_dict.keys():
                obs_dict[key] = torch.Tensor(obs_dict[key])

            if not agent.dicrete_action:
                actions, mus, logsigmas, sigmas = agent.choose_action(obs_dict["observation"],
                                                                      other_data=obs_dict["desired_goal"])
                logp = agent.compute_logp(mus, logsigmas, sigmas, actions)
                mus = mus.cpu().numpy()
                sigmas = sigmas.cpu().numpy()
                mb_mus.append(mus)
                mb_sigmas.append(sigmas)
            else:
                actions, distris = agent.choose_action(obs_dict["observation"],
                                                       other_data=obs_dict["desired_goal"])
                logp = agent.compute_logp(distris, actions)
                distris = distris.cpu().numpy()
                mb_distris.append(distris)
            observations = obs_dict['observation'].cpu().numpy()
            actions = actions.cpu().numpy()
            logp = logp.cpu().numpy()

            if np.random.rand() < 0.0:
                actions = np.concatenate([np.expand_dims(env.action_space.sample(), axis=0)
                                          for i in range(env.num_envs)], axis = 0)
                obs_dict_, rewards, dones, infos = env.step(actions)
            else:
                obs_dict_, rewards, dones, infos = env.step(actions)

            next_obs_dict = copy.deepcopy(obs_dict_)

            for e, info in enumerate(infos):
                if dones[e]:
                    epinfos.append(info.get('episode'))
                    successes.append(info.get('is_success'))
                    for k in next_obs_dict.keys():
                        next_obs_dict[k][e] = info.get('terminal_observation')[k]
                    ep_num += 1

            if render:
                video_writer.add_frame(env.render("rgb_array"))

            mb_obs.append(observations)
            mb_actions.append(actions)
            mb_logpacs.append(logp)
            mb_dones.append(dones.astype(np.uint8))
            mb_rewards.append(rewards)
            mb_obs_.append(next_obs_dict['observation'].copy())
            mb_dg.append(next_obs_dict['desired_goal'].copy())
            mb_ag.append(next_obs_dict['achieved_goal'].copy())

            obs_dict = obs_dict_

        if render:
            video_writer.save()

        epinfobuf.extend(epinfos)
        success_history.extend(successes)

        # make all final states marked by done, preventing wrong estimating of returns and advantages.
        # done flag:
        #      0: undone and not the final state
        #      1: realdone
        #      2: undone but the final state
        ep_num += (mb_dones[-1] == 0).sum()
        mb_dones[-1][np.where(mb_dones[-1] == 0)] = 2

        def reshape_data(arr):
            s = arr.shape
            return arr.swapaxes(0, 1).reshape(s[0] * s[1], *s[2:])

        mb_obs = reshape_data(np.asarray(mb_obs, dtype=np.float32))
        mb_rewards = reshape_data(np.asarray(mb_rewards, dtype=np.float32))
        mb_actions = reshape_data(np.asarray(mb_actions))
        mb_logpacs = reshape_data(np.asarray(mb_logpacs, dtype=np.float32))
        mb_dones = reshape_data(np.asarray(mb_dones, dtype=np.uint8))
        mb_obs_ = reshape_data(np.asarray(mb_obs_, dtype=np.float32))
        mb_ag = reshape_data(np.asarray(mb_ag, dtype=np.float32))
        mb_dg = reshape_data(np.asarray(mb_dg, dtype=np.float32))

        assert mb_rewards.ndim <= 2 and mb_actions.ndim <= 2 and \
               mb_logpacs.ndim <= 2 and mb_dones.ndim <= 2, \
            "databuffer only supports 1-D data's batch."

        if not agent.dicrete_action:
            mb_mus = reshape_data(np.asarray(mb_mus, dtype=np.float32))
            mb_sigmas = reshape_data(np.asarray(mb_sigmas, dtype=np.float32))
            assert mb_mus.ndim <= 2 and mb_sigmas.ndim <= 2, "databuffer only supports 1-D data's batch."
        else:
            mb_distris = reshape_data(np.asarray(mb_distris, dtype=np.float32))
            assert mb_distris.ndim <= 2, "databuffer only supports 1-D data's batch."

        # store transition
        transition = {
            'state': mb_obs if mb_obs.ndim == 2 or mb_obs.ndim == 4 else np.expand_dims(mb_obs, 1),
            'action': mb_actions if mb_actions.ndim == 2 else np.expand_dims(mb_actions, 1),
            'reward': mb_rewards if mb_rewards.ndim == 2 else np.expand_dims(mb_rewards, 1),
            'next_state': mb_obs_ if mb_obs_.ndim == 2 or mb_obs_.ndim == 4 else np.expand_dims(mb_obs_, 1),
            'done': mb_dones if mb_dones.ndim == 2 else np.expand_dims(mb_dones, 1),
            'logpac': mb_logpacs if mb_logpacs.ndim == 2 else np.expand_dims(mb_logpacs, 1),
            'other_data': {
                'desired_goal': mb_dg if mb_dg.ndim == 2 else np.expand_dims(mb_dg, 1),
                'achieved_goal': mb_ag if mb_ag.ndim == 2 else np.expand_dims(mb_ag, 1),
            }
        }
        if not agent.dicrete_action:
            transition['mu'] = mb_mus if mb_mus.ndim == 2 else np.expand_dims(mb_mus, 1)
            transition['sigma'] = mb_sigmas if mb_sigmas.ndim == 2 else np.expand_dims(mb_sigmas, 1)
        else:
            transition['distri'] = mb_distris if mb_distris.ndim == 2 else np.expand_dims(mb_distris, 1)
        agent.store_transition(transition)

        # agent learning step
        agent.learn()

        # training controller
        timestep_counter += agent.nsteps
        if timestep_counter > max_timesteps:
            break

        print("------------------log information------------------")
        print("total_timesteps:".ljust(20) + str(timestep_counter))
        print("valid_ep_ratio:".ljust(20) + "{:.3f}".format(agent.n_valid_ep / ep_num))
        logger.add_scalar("valid_ep_ratio/train", agent.n_valid_ep / ep_num, timestep_counter)
        if agent.n_valid_ep > 0:
            print("iterations:".ljust(20) + str(agent.learn_step_counter) + " / " + str(int(total_updates)))
            if agent.value_type is not None:
                explained_var = explained_variance(agent.V.cpu().numpy(), agent.esti_R.cpu().numpy())
                print("explained_var:".ljust(20) + str(explained_var))
                logger.add_scalar("explained_var/train", explained_var, timestep_counter)
            print("episode_len:".ljust(20) + "{:.1f}".format(np.mean([epinfo['l'] for epinfo in epinfobuf])))
            rew = np.mean([epinfo['r'] for epinfo in epinfobuf]) + agent.max_steps
            print("episode_rew:".ljust(20) + str(rew))
            logger.add_scalar("episode_reward/train", rew, timestep_counter)
            print("success_rate:".ljust(20) + "{:.3f}".format(100 * np.mean(success_history)) + "%")
            logger.add_scalar("success_rate/train", np.mean(success_history), timestep_counter)
            print("mean_kl:".ljust(20) + str(agent.cur_kl))
            logger.add_scalar("mean_kl/train", agent.cur_kl, timestep_counter)
            print("policy_ent:".ljust(20) + str(agent.policy_ent))
            logger.add_scalar("policy_ent/train", agent.policy_ent, timestep_counter)
            print("value_loss:".ljust(20) + str(agent.value_loss))
            logger.add_scalar("value_loss/train", agent.value_loss, timestep_counter)
            print("actual_imprv:".ljust(20) + "{:.5f}".format(agent.improvement))
            logger.add_scalar("actual_imprv/train", agent.improvement, timestep_counter)
            print("exp_imprv:".ljust(20) + "{:.5f}".format(agent.expected_improvement))
            logger.add_scalar("exp_imprv/train", agent.expected_improvement, timestep_counter)
            ep_num = 0
        else:
            print("No valid episode was collected. Policy has not been updated.")

        if eval_interval and timestep_counter % eval_interval == 0:
            agent.save_model(os.path.join("output/models/HTRPO", env.env_id))
            eval_ret, eval_success = agent.eval_brain(env, render=render, eval_num=num_evals)
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            print("eval_ep_rew:".ljust(20) + str(np.mean(eval_ret)))
            print("eval_suc_rate:".ljust(20) + str(np.mean(eval_success)))
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            logger.add_scalar("episode_reward/eval", np.mean(eval_ret), timestep_counter)
            logger.add_scalar("success_rate/eval", np.mean(eval_success), timestep_counter)

    return agent
