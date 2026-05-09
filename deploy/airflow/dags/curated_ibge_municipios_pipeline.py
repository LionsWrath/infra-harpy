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
    dag_id='curated_ibge_municipios_pipeline',
    start_date=datetime(2026, 4, 24),
    schedule=None,
    catchup=False,
    tags=['curated', 'routes', 'ibge'],
)
def curated_ibge_municipios_pipeline():
    @task
    def rebuild(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.curated_ibge_municipios;
        CREATE TABLE iceberg.oidw.curated_ibge_municipios
        WITH (format='PARQUET') AS
        WITH latest AS (
          SELECT *
          FROM iceberg.oidw.raw_ibge_municipios
          WHERE cast(datestr as date) = (
            SELECT max(cast(datestr as date)) FROM iceberg.oidw.raw_ibge_municipios
          )
        )
        SELECT
          cast(municipio_id as varchar) as municipio_id,
          trim(municipio_nome) as municipio_nome,
          upper(trim(uf_sigla)) as uf_sigla,
          trim(regiao_nome) as regiao_nome,
          trim(uf_nome) as uf_nome,
          upper(trim(regexp_replace(municipio_nome, '[^A-Za-zÀ-ÿ0-9 ]', ''))) as municipio_nome_norm,
          cast(datestr as date) as snapshot_date
        FROM latest;
        """
        run_sql(conn_id, sql)

    rebuild()


curated_ibge_municipios_pipeline()
