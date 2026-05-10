from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime

TRINO_CONN_ID = "trino_default"


@dag(
    dag_id="curated_antt_tarifa_base_pipeline",
    start_date=datetime(2026, 4, 24),
    schedule=None,
    catchup=False,
    tags=["antt", "tolls", "curated", "on-demand"],
)
def curated_antt_tarifa_base_pipeline():
    @task
    def build_curated(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.curated_antt_tarifa_base_praca;

        CREATE TABLE iceberg.oidw.curated_antt_tarifa_base_praca
        WITH (format='PARQUET') AS
        WITH rec AS (
          SELECT
            upper(trim(concessionaria)) AS concessionaria,
            upper(trim(praca_de_pedagio)) AS praca,
            mes_ano,
            sum(receita_pedagio) AS receita_brl
          FROM iceberg.oidw.raw_antt_receita_praca
          WHERE receita_pedagio IS NOT NULL
            AND receita_pedagio > 0
          GROUP BY 1,2,3
        ),
        vol AS (
          SELECT
            upper(trim(concessionaria)) AS concessionaria,
            upper(trim(praca)) AS praca,
            mes_ano,
            sum(volume_veiculo_equivalente) AS volume_equivalente
          FROM iceberg.oidw.raw_antt_volume_trafego_equiv_praca
          WHERE volume_veiculo_equivalente IS NOT NULL
            AND volume_veiculo_equivalente > 0
          GROUP BY 1,2,3
        ),
        monthly AS (
          SELECT
            rec.concessionaria,
            rec.praca,
            rec.mes_ano,
            rec.receita_brl,
            vol.volume_equivalente,
            rec.receita_brl / vol.volume_equivalente AS tarifa_base_brl
          FROM rec
          JOIN vol
            ON rec.concessionaria = vol.concessionaria
           AND rec.praca = vol.praca
           AND rec.mes_ano = vol.mes_ano
          WHERE rec.receita_brl / vol.volume_equivalente > 0
        ),
        stats AS (
          SELECT
            concessionaria,
            praca,
            approx_percentile(tarifa_base_brl, 0.5) AS tarifa_base_median_brl,
            avg(tarifa_base_brl) AS tarifa_base_mean_brl,
            min(tarifa_base_brl) AS tarifa_base_min_brl,
            max(tarifa_base_brl) AS tarifa_base_max_brl,
            count(*) AS n_months
          FROM monthly
          GROUP BY 1,2
        )
        SELECT
          m.concessionaria,
          m.praca,
          m.mes_ano,
          m.receita_brl,
          m.volume_equivalente,
          m.tarifa_base_brl,
          s.tarifa_base_median_brl,
          s.tarifa_base_mean_brl,
          s.tarifa_base_min_brl,
          s.tarifa_base_max_brl,
          s.n_months,
          current_timestamp AS updated_at
        FROM monthly m
        JOIN stats s
          ON m.concessionaria = s.concessionaria
         AND m.praca = s.praca
        ;
        """

        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                cur.execute(stmt)

    build_curated()


curated_antt_tarifa_base_pipeline()
