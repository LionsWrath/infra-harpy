#!/bin/bash
 
docker exec postgres pg_dumpall -U harpydb | gzip > ${DATA_PATH}/backups/postgresql/$(date +"%Y_%m_%d")_pg_bck.gz