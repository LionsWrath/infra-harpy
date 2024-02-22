#############################################################
# Sync backup files with S3                                 #
#############################################################
# The .aws file should be locally configured on             # 
# the machine!                                              #
#############################################################

#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/../vars.sh
source ${SCRIPT_DIR}/ntfy.sh

BACKUP_PATH=${DATA_PATH}/backups/postgresql
S3_BACKUP_PATH=${S3_PATH}/backups/pgsql

docker run --rm -it -v {DATA_PATH}/.aws:/root/.aws amazon/aws-cli aws s3 sync ${BACKUP_PATH} ${S3_BACKUP_PATH}

notify "S3 Sync" "default" "computer" "S3 Sync completed." ${MANAGE_TOPIC}