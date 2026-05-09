"""
Seed/refresh logistics node dimension (ports/terminals)
Trigger-only DAG (on-demand), no cron.
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime

TRINO_CONN_ID = "trino_default"

NODES = [
    ("santos_sp", "port_complex", "Complexo de Santos", "Santos", "SP", "sul_sudeste", "ferrovia_rodovia", "manual_seed", True),
    ("paranagua_pr", "port_complex", "Complexo de Paranaguá", "Paranaguá", "PR", "sul_sudeste", "ferrovia_rodovia", "manual_seed", True),
    ("itaqui_ma", "port_complex", "Complexo do Itaqui", "São Luís", "MA", "arco_norte", "ferrovia_rodovia", "manual_seed", True),
    ("barcarena_pa", "port_complex", "Complexo de Barcarena", "Barcarena", "PA", "arco_norte", "hidrovia_rodovia", "manual_seed", True),
    ("rio_grande_rs", "port_complex", "Complexo de Rio Grande", "Rio Grande", "RS", "sul_sudeste", "rodovia_ferrovia_hidrovia", "manual_seed", True),
    ("santarem_pa", "port_complex", "Complexo de Santarém", "Santarém", "PA", "arco_norte", "hidrovia_rodovia", "manual_seed", True),
    ("sao_francisco_do_sul_sc", "port_complex", "Complexo de São Francisco do Sul", "São Francisco do Sul", "SC", "sul_sudeste", "ferrovia_rodovia", "manual_seed", True),
    ("tubarao_vitoria_es", "port_complex", "Complexo de Tubarão", "Vitória", "ES", "sul_sudeste", "ferrovia", "manual_seed", True),
    ("itacoatiara_am", "port_complex", "Complexo de Itacoatiara", "Itacoatiara", "AM", "arco_norte", "hidrovia", "manual_seed", True),
    ("cotegipe_aratu_ba", "port_complex", "Complexo de Cotegipe / Aratu", "Salvador", "BA", "nordeste", "rodovia_ferrovia", "manual_seed", True),
    ("porto_velho_ro", "terminal_hidro", "Complexo de Porto Velho", "Porto Velho", "RO", "arco_norte", "rodovia_hidrovia", "manual_seed", True),
    ("santana_ap", "port_complex", "Complexo de Santana", "Santana", "AP", "arco_norte", "hidrovia", "manual_seed", True),
]


@dag(
    dag_id="dim_logistic_node_seed_pipeline",
    start_date=datetime(2026, 4, 21),
    schedule=None,
    catchup=False,
    tags=["dim", "logistics", "ports", "on-demand"],
)
def dim_logistic_node_seed_pipeline():

    @task
    def upsert_dim(conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)

        with hook.get_conn() as conn:
            cur = conn.cursor()

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS iceberg.oidw.dim_logistic_node (
                    node_code varchar,
                    node_type varchar,
                    node_name varchar,
                    city_name varchar,
                    uf varchar,
                    logistic_axis varchar,
                    hinterland_connection varchar,
                    source_system varchar,
                    is_active boolean,
                    municipio_id_ibge varchar,
                    uf_id_ibge varchar,
                    regiao_id_ibge varchar,
                    match_status varchar,
                    updated_at timestamp(6)
                )
                WITH (
                    format = 'PARQUET'
                )
                """
            )

            cur.execute("DELETE FROM iceberg.oidw.dim_logistic_node WHERE source_system = 'manual_seed'")

            def q(v: str) -> str:
                return "'" + str(v).replace("'", "''") + "'"

            values = []
            for row in NODES:
                values.append(
                    "(" + ", ".join([
                        q(row[0]), q(row[1]), q(row[2]), q(row[3]), q(row[4]),
                        q(row[5]), q(row[6]), q(row[7]),
                        "true" if row[8] else "false",
                    ]) + ")"
                )

            cur.execute(
                """
                INSERT INTO iceberg.oidw.dim_logistic_node (
                    node_code, node_type, node_name, city_name, uf,
                    logistic_axis, hinterland_connection, source_system, is_active,
                    municipio_id_ibge, uf_id_ibge, regiao_id_ibge, match_status, updated_at
                )
                SELECT
                    v.node_code, v.node_type, v.node_name, v.city_name, v.uf,
                    v.logistic_axis, v.hinterland_connection, v.source_system, v.is_active,
                    m.municipio_id as municipio_id_ibge,
                    m.uf_id as uf_id_ibge,
                    m.regiao_id as regiao_id_ibge,
                    CASE WHEN m.municipio_id IS NOT NULL THEN 'matched_exact' ELSE 'unmatched' END as match_status,
                    CURRENT_TIMESTAMP
                FROM (
                    VALUES
                """ + ",\n".join(values) + """
                ) AS v(
                    node_code, node_type, node_name, city_name, uf,
                    logistic_axis, hinterland_connection, source_system, is_active
                )
                LEFT JOIN iceberg.oidw.raw_ibge_municipios m
                    ON upper(trim(m.municipio_nome)) = upper(trim(v.city_name))
                   AND upper(trim(m.uf_sigla)) = upper(trim(v.uf))
                """
            )

    upsert_dim()


dim_logistic_node_seed_pipeline()
