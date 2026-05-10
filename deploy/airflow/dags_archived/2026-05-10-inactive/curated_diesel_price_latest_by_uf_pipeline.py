from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime

TRINO_CONN_ID = 'trino_default'


def run_sql(conn_id: str, sql: str) -> None:
    hook = TrinoHook(trino_conn_id=conn_id)
    with hook.get_conn() as conn:
        cur = conn.cursor()
        for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
            cur.execute(stmt)


@dag(
    dag_id='curated_diesel_price_latest_by_uf_pipeline',
    start_date=datetime(2026, 4, 24),
    schedule=None,
    catchup=False,
    tags=['curated', 'routes', 'anp', 'diesel'],
)
def curated_diesel_price_latest_by_uf_pipeline():
    @task
    def rebuild(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.curated_diesel_price_latest_by_uf;
        CREATE TABLE iceberg.oidw.curated_diesel_price_latest_by_uf
        WITH (format='PARQUET') AS
        WITH base AS (
          SELECT
            upper(trim(estado_sigla)) as uf,
            try_cast(replace(valor_venda_raw, ',', '.') as double) as valor_venda,
            cast(ingested_at as timestamp(6)) as ingested_at
          FROM iceberg.oidw.raw_anp_combustiveis
          WHERE lower(produto) LIKE '%diesel%'
            AND try_cast(replace(valor_venda_raw, ',', '.') as double) > 0
        ), agg AS (
          SELECT
            uf,
            avg(valor_venda) as diesel_brl_l,
            max(ingested_at) as updated_at
          FROM base
          GROUP BY 1
        )
        SELECT * FROM agg;
        """
        run_sql(conn_id, sql)

    rebuild()


curated_diesel_price_latest_by_uf_pipeline()
