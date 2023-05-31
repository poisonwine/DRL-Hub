from torch import optim
import torch.nn.functional as F

PPOconfig = {
        'n_states': 3,
        'n_action_dims': 1,
        'action_bounds': 2,
        'memory_size': 2000,
        'reward_decay': 0.95,
        'steps_per_update': 256,
        'batch_size': 32,
        'max_grad_norm': 2,
        'GAE_lambda': 0.95,
        'clip_epsilon': 0.2,
        'lr': 1e-3,
        'lr_v': 1e-3,
        'hidden_layers' : [50],
        'use_batch_norm' : False,
        'optimizer': optim.Adam,
        'v_optimizer': optim.Adam,
        'value_type': 'FC',
        'dicrete_action': False,
        'act_func': F.relu,
        'out_act_func': F.tanh,
    }