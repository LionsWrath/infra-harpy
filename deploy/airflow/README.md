# Airflow DAG source mirror

This directory mirrors the live DAGs mounted at:
- `/home/lionswrath/data/services/airflow/dags`

Purpose:
- keep DAG changes under git source control
- allow review/diff/history outside the live mount

Operational note:
- live runtime still reads from the mounted DAG directory
- after live DAG edits, sync this mirror before considering work complete
