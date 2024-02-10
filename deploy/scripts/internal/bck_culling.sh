#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/../vars.sh

RETENTION_TIME=30

arr1=$(ls -t | head -n $RETENTION_TIME)
arr2=$(ls -t)

res=$(${arr1[@]} ${arr2[@]} | tr ' ' '\n' | sort | uniq -u)

for i in "${res[@]}"
    do
        rm ${DATA_PATH}/backups/postgresql/$i
    done