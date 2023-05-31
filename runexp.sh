#!/bin/bash
CUDA_VISIBLE_DEVICES=0  nohup python -u main.py --alg HTRPO --env SweepPile-v0 --seed 1 --eval_interval 96000 --num_steps 5000000 --num_eval 100    >SweepPile1.log 2>&1 &
CUDA_VISIBLE_DEVICES=1  nohup python -u main.py --alg HTRPO --env SweepPile-v0 --seed 2 --eval_interval 96000 --num_steps 5000000 --num_eval 100   >SweepPile2.log 2>&1 &
CUDA_VISIBLE_DEVICES=2  nohup python -u main.py --alg HTRPO --env SweepPile-v0 --seed 3  --eval_interval 96000 --num_steps 5000000 --num_eval 100   >SweepPile3.log 2>&1 &
CUDA_VISIBLE_DEVICES=4  nohup python -u main.py --alg HTRPO --env SweepPile-v0 --seed 4 --eval_interval 96000 --num_steps 5000000 --num_eval 100   >SweepPile4.log 2>&1 &
#CUDA_VISIBLE_DEVICES=3  nohup python -u main.py --alg HTRPO --env FetchThrowRubberBall-v0 --seed 1 --eval_interval 96000 --num_steps 5000000 --num_eval 100    >fetchthrowball1.log  2>&1 &
#CUDA_VISIBLE_DEVICES=3  nohup python -u main.py --alg HTRPO --env FetchThrowRubberBall-v0 --seed 2 --eval_interval 96000 --num_steps 5000000 --num_eval 100   >fetchthrowball2.log  2>&1 &
#CUDA_VISIBLE_DEVICES=4  nohup python -u main.py --alg HTRPO --env FetchThrowRubberBall-v0 --seed 3  --eval_interval 96000 --num_steps 5000000 --num_eval 100   >fetchthrowball3.log 2>&1 &
#CUDA_VISIBLE_DEVICES=4  nohup python -u main.py --alg HTRPO --env FetchThrowRubberBall-v0 --seed 4 --eval_interval 96000 --num_steps 5000000 --num_eval 100   >fetchthrowball4.log  2>&1 &
#CUDA_VISIBLE_DEVICES=3  nohup python -u main.py --alg HTRPO --env FetchPickAndThrow-v0 --seed 1 --eval_interval 96000 --num_steps 5000000 --num_eval 100    >fetchpickthrow1.log 2>&1 &
#CUDA_VISIBLE_DEVICES=3  nohup python -u main.py --alg HTRPO --env FetchPickAndThrow-v0 --seed 2 --eval_interval 96000 --num_steps 5000000 --num_eval 100   >fetchpickthrow2.log 2>&1 &
#CUDA_VISIBLE_DEVICES=3  nohup python -u main.py --alg HTRPO --env FetchPickAndThrow-v0 --seed 3  --eval_interval 96000 --num_steps 5000000 --num_eval 100   >fetchpickthrow3.log 2>&1 &
#CUDA_VISIBLE_DEVICES=3  nohup python -u main.py --alg HTRPO --env FetchPickAndThrow-v0 --seed 4 --eval_interval 96000 --num_steps 5000000 --num_eval 100   >fetchpickthrow4.log 2>&1 &

