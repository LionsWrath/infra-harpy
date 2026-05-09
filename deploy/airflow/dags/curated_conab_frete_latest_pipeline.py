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
    dag_id='curated_conab_frete_latest_pipeline',
    start_date=datetime(2026, 4, 24),
    schedule=None,
    catchup=False,
    tags=['curated', 'routes', 'conab'],
)
def curated_conab_frete_latest_pipeline():
    @task
    def rebuild(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.curated_conab_frete_latest;
        CREATE TABLE iceberg.oidw.curated_conab_frete_latest
        WITH (format='PARQUET') AS
        WITH typed AS (
          SELECT
            cast(r.datestr as date) as datestr,
            trim(r.municipio_origem) as municipio_origem,
            upper(trim(r.uf_origem)) as uf_origem,
            trim(r.municipio_destino) as municipio_destino,
            upper(trim(r.uf_destino)) as uf_destino,
            nullif(trim(r.cod_ibge_origem), '') as cod_ibge_origem,
            nullif(trim(r.cod_ibge_destino), '') as cod_ibge_destino,
            try_cast(replace(r.distancia_km_raw, ',', '.') as double) as conab_km,
            try_cast(replace(r.valor_frete_tonelada_raw, ',', '.') as double) as conab_r_t,
            try_cast(replace(r.valor_tonelada_km_raw, ',', '.') as double) as conab_r_tkm,
            r.source_url,
            r.source_run_date,
            r.ingested_at
          FROM iceberg.oidw.raw_conab_frete r
          WHERE try_cast(replace(r.distancia_km_raw, ',', '.') as double) IS NOT NULL
            AND try_cast(replace(r.valor_frete_tonelada_raw, ',', '.') as double) IS NOT NULL
        ), ranked AS (
          SELECT t.*, row_number() OVER (
            PARTITION BY municipio_origem, uf_origem, municipio_destino, uf_destino
            ORDER BY datestr DESC, ingested_at DESC
          ) rn
          FROM typed t
        )
        SELECT
          datestr,
          municipio_origem,
          uf_origem,
          municipio_destino,
          uf_destino,
          cod_ibge_origem,
          cod_ibge_destino,
          conab_km,
          conab_r_t,
          conab_r_tkm,
          source_url,
          source_run_date,
          ingested_at
        FROM ranked
        WHERE rn = 1;
        """
        run_sql(conn_id, sql)

    rebuild()


curated_conab_frete_latest_pipeline()
