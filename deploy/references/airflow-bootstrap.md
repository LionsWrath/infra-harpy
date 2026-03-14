# Airflow DB Bootstrap (existing Postgres)

Use these commands before starting Airflow services.

## 1) Set target values (example placeholders)

```bash
export AIRFLOW_DB_NAME="airflow"
export AIRFLOW_DB_USER="airflow_user"
export AIRFLOW_DB_PASSWORD="CHANGE_ME_STRONG"
```

## 2) Create DB + user inside Postgres container

```bash
docker exec -it postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE USER ${AIRFLOW_DB_USER} WITH PASSWORD '${AIRFLOW_DB_PASSWORD}';"
docker exec -it postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE ${AIRFLOW_DB_NAME} OWNER ${AIRFLOW_DB_USER};"
docker exec -it postgres psql -U "$POSTGRES_USER" -d postgres -c "GRANT ALL PRIVILEGES ON DATABASE ${AIRFLOW_DB_NAME} TO ${AIRFLOW_DB_USER};"
```

If user/database already exist, use idempotent SQL block:

```bash
docker exec -i postgres psql -U "$POSTGRES_USER" -d postgres <<SQL
DO
\$do\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${AIRFLOW_DB_USER}') THEN
      CREATE ROLE ${AIRFLOW_DB_USER} LOGIN PASSWORD '${AIRFLOW_DB_PASSWORD}';
   END IF;
END
\$do\$;
SQL

docker exec -i postgres psql -U "$POSTGRES_USER" -d postgres <<SQL
DO
\$do\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = '${AIRFLOW_DB_NAME}') THEN
      CREATE DATABASE ${AIRFLOW_DB_NAME} OWNER ${AIRFLOW_DB_USER};
   END IF;
END
\$do\$;
SQL
```

## 3) Set env values in `deploy/envs/airflow.env`

```ini
AIRFLOW_DB_HOST=postgres
AIRFLOW_DB_PORT=5432
AIRFLOW_DB_NAME=airflow
AIRFLOW_DB_USER=airflow_user
AIRFLOW_DB_PASSWORD=CHANGE_ME_STRONG
AIRFLOW_ADMIN_USER=admin
AIRFLOW_ADMIN_EMAIL=admin@local
AIRFLOW_ADMIN_PASSWORD=CHANGE_ME_ADMIN
AIRFLOW_WEBSERVER_SECRET_KEY=CHANGE_ME_SECRET

# Optional for raw_news_summary DAGs
DUCKDB_API_BASE=http://home.orochi.chst.dev:8090
DUCKDB_API_KEY=CHANGE_ME
RELAY_URL=https://relay.orochi.chst.dev/relay
RELAY_KEY=CHANGE_ME
```

## 4) Run Airflow initialization once

From `deploy/` context (where includes resolve):

```bash
docker compose run --rm airflow-init
```

## 5) Start runtime services

```bash
docker compose up -d airflow-webserver airflow-scheduler airflow-triggerer
```

## 6) Validate

```bash
docker compose ps | grep airflow
```

Open UI: `https://airflow.orochi.chst.dev`
