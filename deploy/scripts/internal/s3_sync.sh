#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/../vars.sh
source ${SCRIPT_DIR}/ntfy.sh

BACKUP_PATH=${DATA_PATH}/backups/postgresql
S3_BACKUP_PATH=${S3_PATH}/backups/pgsql

aws s3 sync ${BACKUP_PATH} ${S3_BACKUP_PATH}