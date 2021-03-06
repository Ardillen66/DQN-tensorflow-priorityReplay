#!/bin/bash -l
# number of nodes and cores 
#PBS -l nodes=1:ppn=1
# memory requirements change this when using more/less experience replay samples
#PBS -l mem=16gb
# max run time
#PBS -l walltime=500:00:00
# output and error files
#PBS -o dqn-si.out
#PBS -e dqn-si.err
#PBS -N dqn-si
#PBS -V

module add OpenBLAS
cd $HOME
source .bashrc
source activate dqn
cd DQN-tensorflow-priorityReplay
python main.py --use_gpu 0 --env_name=SpaceInvaders-v0
