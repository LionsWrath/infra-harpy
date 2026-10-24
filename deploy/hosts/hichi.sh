# hichi: compose interpolation variables that differ from scripts/vars.sh (non-secret, sourced after it).
export DATA_PATH=${HOME}/data           # hichi keeps service data directly under ~/data
export LSIO_IP=172.19.0                 # hichi's lsio network is 172.19.0.0/16 (vars.sh has the oichi-era default)
