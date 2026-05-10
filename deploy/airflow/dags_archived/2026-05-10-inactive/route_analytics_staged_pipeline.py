from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime

TRINO_CONN_ID = "trino_default"
DAG_ID = "route_analytics_staged_pipeline"


def run_sql(conn_id: str, sql: str) -> None:
    hook = TrinoHook(trino_conn_id=conn_id)
    with hook.get_conn() as conn:
        cur = conn.cursor()
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            cur.execute(stmt)


@dag(
    dag_id=DAG_ID,
    start_date=datetime(2026, 4, 22),
    schedule=None,
    catchup=False,
    tags=["routes", "staged", "iceberg", "on-demand"],
)
def route_analytics_staged_pipeline():
    @task
    def stage_0_universe(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.tmp_route_stage_0_universe;
        CREATE TABLE IF NOT EXISTS iceberg.oidw.tmp_route_stage_0_universe
        WITH (format='PARQUET', partitioning=ARRAY['datestr']) AS
        WITH base AS (
          SELECT
            cast(r.datestr as date) as datestr,
            r.municipio_origem, r.uf_origem,
            r.municipio_destino, r.uf_destino,
            r.conab_km as conab_km,
            r.conab_r_t as conab_r_t,
            r.conab_r_tkm as conab_r_tkm,
            mo.municipio_id as origin_municipio_ibge,
            md.municipio_id as dest_municipio_ibge
          FROM iceberg.oidw.curated_conab_frete_latest r
          LEFT JOIN iceberg.oidw.curated_ibge_municipios mo
            ON upper(trim(mo.municipio_nome)) = upper(trim(regexp_replace(r.municipio_origem,'-..$','')))
           AND upper(trim(mo.uf_sigla))=upper(trim(r.uf_origem))
          LEFT JOIN iceberg.oidw.curated_ibge_municipios md
            ON upper(trim(md.municipio_nome)) = upper(trim(regexp_replace(r.municipio_destino,'-..$','')))
           AND upper(trim(md.uf_sigla))=upper(trim(r.uf_destino))
        ), with_nodes AS (
          SELECT b.*, dn.node_code as dest_node_code, dn.node_type as dest_node_type
          FROM base b
          LEFT JOIN iceberg.oidw.dim_logistic_node dn
            ON b.dest_municipio_ibge = dn.municipio_id_ibge
          WHERE dn.node_code IS NOT NULL
        )
        SELECT *,
          CASE WHEN origin_municipio_ibge IS NOT NULL AND dest_municipio_ibge IS NOT NULL
               THEN 'matched' ELSE 'partial_or_unmatched' END as quality_status
        FROM with_nodes;
        """
        run_sql(conn_id, sql)

    @task
    def stage_1_osrm_tmp(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.tmp_route_stage_1_osrm;
        CREATE TABLE IF NOT EXISTS iceberg.oidw.tmp_route_stage_1_osrm
        WITH (format='PARQUET', partitioning=ARRAY['datestr']) AS
        WITH monthly AS (
          SELECT datestr, municipio_origem, uf_origem, municipio_destino, uf_destino,
                 dest_node_code, dest_node_type, origin_municipio_ibge, dest_municipio_ibge,
                 avg(conab_km) conab_km, avg(conab_r_t) conab_r_t, avg(conab_r_tkm) conab_r_tkm
          FROM iceberg.oidw.tmp_route_stage_0_universe
          WHERE uf_origem IN ('MT','MS','GO','DF')
            AND quality_status='matched'
            AND conab_km IS NOT NULL AND conab_r_t IS NOT NULL
          GROUP BY 1,2,3,4,5,6,7,8,9
        ), latest AS (
          SELECT *, row_number() OVER (
            PARTITION BY municipio_origem, uf_origem, municipio_destino, uf_destino, dest_node_code
            ORDER BY datestr DESC
          ) rn
          FROM monthly
        ), baseline AS (
          SELECT datestr, municipio_origem, uf_origem, municipio_destino, uf_destino,
                 dest_node_code, dest_node_type, origin_municipio_ibge, dest_municipio_ibge,
                 conab_km, conab_r_t, conab_r_tkm
          FROM latest WHERE rn=1
        )
        SELECT b.*,
               o.osrm_km,
               o.osrm_duration_h,
               coalesce(o.osrm_status, 'pending') as osrm_status,
               o.osrm_error,
               o.osrm_route_url,
               o.osrm_updated_at
        FROM baseline b
        LEFT JOIN iceberg.oidw.stg_route_osrm o
          ON b.municipio_origem=o.municipio_origem
         AND b.uf_origem=o.uf_origem
         AND b.municipio_destino=o.municipio_destino
         AND b.uf_destino=o.uf_destino
         AND b.dest_node_code=o.dest_node_code;
        """
        run_sql(conn_id, sql)

    @task
    def stage_2_tolls_tmp(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.tmp_route_stage_2_tolls;
        CREATE TABLE IF NOT EXISTS iceberg.oidw.tmp_route_stage_2_tolls AS
        WITH axle AS (
          SELECT eixos, multiplier
          FROM iceberg.oidw.ref_axle_multiplier
          WHERE is_active = true
        )
        SELECT s1.*,
               t.toll_base_trip_brl,
               coalesce(a.multiplier, 4.5) as axle_multiplier,
               CAST(9 AS integer) as axle_count_assumption,
               t.toll_base_trip_brl * coalesce(a.multiplier, 4.5) as toll_axle_trip_brl,
               t.toll_count,
               coalesce(t.tolls_status, 'pending') as tolls_status,
               t.tolls_error,
               t.tolls_updated_at
        FROM iceberg.oidw.tmp_route_stage_1_osrm s1
        LEFT JOIN iceberg.oidw.stg_route_tolls t
          ON s1.municipio_origem=t.municipio_origem
         AND s1.uf_origem=t.uf_origem
         AND s1.municipio_destino=t.municipio_destino
         AND s1.uf_destino=t.uf_destino
         AND s1.dest_node_code=t.dest_node_code
        LEFT JOIN axle a
          ON a.eixos = 9;
        """
        run_sql(conn_id, sql)

    @task
    def stage_3_fuel_tmp(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.tmp_route_stage_3_fuel;
        CREATE TABLE IF NOT EXISTS iceberg.oidw.tmp_route_stage_3_fuel AS
        WITH diesel_latest AS (
          SELECT uf as estado_sigla,
                 diesel_brl_l
          FROM iceberg.oidw.curated_diesel_price_latest_by_uf
        ), b AS (
          SELECT t.*,
                 do.diesel_brl_l as diesel_origin_brl_l,
                 dd.diesel_brl_l as diesel_dest_brl_l,
                 (coalesce(do.diesel_brl_l, dd.diesel_brl_l) + coalesce(dd.diesel_brl_l, do.diesel_brl_l))/2.0 as diesel_corridor_brl_l,
                 CAST(2.2 AS double) as km_per_l_assumption
          FROM iceberg.oidw.tmp_route_stage_2_tolls t
          LEFT JOIN diesel_latest do ON t.uf_origem = do.estado_sigla
          LEFT JOIN diesel_latest dd ON t.uf_destino = dd.estado_sigla
        )
        SELECT b.*,
               CASE WHEN osrm_km IS NOT NULL AND diesel_corridor_brl_l IS NOT NULL
                    THEN (osrm_km / km_per_l_assumption) * diesel_corridor_brl_l ELSE NULL END as fuel_trip_brl,
               CASE WHEN osrm_km IS NOT NULL AND diesel_corridor_brl_l IS NOT NULL
                    THEN ((osrm_km / km_per_l_assumption) * diesel_corridor_brl_l) / osrm_km ELSE NULL END as fuel_r_km,
               CURRENT_TIMESTAMP as fuel_updated_at
        FROM b;
        """
        run_sql(conn_id, sql)

    @task
    def stage_4_antt_tmp(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.tmp_route_stage_4_antt;
        CREATE TABLE IF NOT EXISTS iceberg.oidw.tmp_route_stage_4_antt AS
        SELECT s3.*,
               a.antt_floor_r_t,
               a.antt_floor_trip_brl_32t,
               coalesce(a.antt_status, 'pending') as antt_status,
               a.antt_error,
               a.antt_updated_at
        FROM iceberg.oidw.tmp_route_stage_3_fuel s3
        LEFT JOIN iceberg.oidw.stg_route_antt_floor a
          ON s3.municipio_origem=a.municipio_origem
         AND s3.uf_origem=a.uf_origem
         AND s3.municipio_destino=a.municipio_destino
         AND s3.uf_destino=a.uf_destino
         AND s3.dest_node_code=a.dest_node_code;
        """
        run_sql(conn_id, sql)

    @task
    def stage_5_metrics_base_tmp(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.tmp_route_stage_5_metrics_base;
        CREATE TABLE IF NOT EXISTS iceberg.oidw.tmp_route_stage_5_metrics_base AS
        SELECT s4.*, CAST(32.0 AS double) as payload_t_assumption,
               CASE WHEN fuel_trip_brl IS NOT NULL AND toll_axle_trip_brl IS NOT NULL
                    THEN (fuel_trip_brl + toll_axle_trip_brl)/32.0 ELSE NULL END as osrm_base_r_t,
               CASE WHEN osrm_km IS NOT NULL AND fuel_trip_brl IS NOT NULL AND toll_axle_trip_brl IS NOT NULL
                    THEN ((fuel_trip_brl + toll_axle_trip_brl)/32.0)/osrm_km ELSE NULL END as osrm_base_r_tkm,
               CASE WHEN conab_r_t IS NOT NULL AND antt_floor_r_t IS NOT NULL
                    THEN conab_r_t - antt_floor_r_t ELSE NULL END as gap_conab_vs_antt_r_t
        FROM iceberg.oidw.tmp_route_stage_4_antt s4;
        """
        run_sql(conn_id, sql)

    @task
    def stage_6_calibration_tmp(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.tmp_route_stage_6_calibration;
        CREATE TABLE IF NOT EXISTS iceberg.oidw.tmp_route_stage_6_calibration AS
        WITH b AS (
          SELECT *,
                 CASE WHEN osrm_km < 800 THEN 'short'
                      WHEN osrm_km < 1600 THEN 'medium'
                      ELSE 'long' END as distance_band,
                 CASE WHEN osrm_base_r_t IS NOT NULL AND osrm_base_r_t>0
                      THEN conab_r_t/osrm_base_r_t ELSE NULL END as factor_candidate
          FROM iceberg.oidw.tmp_route_stage_5_metrics_base
        ), f AS (
          SELECT dest_node_code, distance_band,
                 approx_percentile(factor_candidate, 0.5) as factor_applied,
                 count_if(factor_candidate IS NOT NULL) as n_factor
          FROM b
          GROUP BY 1,2
        )
        SELECT b.*, f.factor_applied, f.n_factor
        FROM b
        LEFT JOIN f ON b.dest_node_code=f.dest_node_code AND b.distance_band=f.distance_band;
        """
        run_sql(conn_id, sql)

    @task
    def stage_7_final_incremental(conn_id: str = TRINO_CONN_ID):
        sql = """
        DROP TABLE IF EXISTS iceberg.oidw.analytics_route_cost_daily;

        CREATE TABLE iceberg.oidw.analytics_route_cost_daily
        WITH (format='PARQUET', partitioning=ARRAY['datestr']) AS
        SELECT c.datestr, c.municipio_origem, c.uf_origem, c.municipio_destino, c.uf_destino,
               c.dest_node_code, c.dest_node_type, c.origin_municipio_ibge, c.dest_municipio_ibge,
               c.conab_km, c.conab_r_t, c.conab_r_tkm, c.osrm_km, c.osrm_duration_h,
               c.osrm_status, c.osrm_error, c.osrm_route_url,
               c.toll_base_trip_brl, c.toll_axle_trip_brl, c.toll_count, c.tolls_status, c.tolls_error,
               c.diesel_origin_brl_l, c.diesel_dest_brl_l, c.diesel_corridor_brl_l,
               c.km_per_l_assumption, c.fuel_trip_brl, c.fuel_r_km,
               c.antt_floor_r_t, c.antt_floor_trip_brl_32t, c.antt_status, c.antt_error,
               c.payload_t_assumption, c.osrm_base_r_t, c.osrm_base_r_tkm, c.gap_conab_vs_antt_r_t,
               c.distance_band, c.factor_candidate, c.factor_applied, c.n_factor,
               CASE WHEN c.osrm_base_r_t IS NOT NULL AND c.factor_applied IS NOT NULL
                    THEN c.osrm_base_r_t*c.factor_applied ELSE NULL END as osrm_cal_r_t,
               CASE WHEN c.osrm_base_r_tkm IS NOT NULL AND c.factor_applied IS NOT NULL
                    THEN c.osrm_base_r_tkm*c.factor_applied ELSE NULL END as osrm_cal_r_tkm,
               CASE WHEN c.conab_r_t IS NOT NULL AND c.osrm_base_r_t IS NOT NULL AND c.factor_applied IS NOT NULL
                    THEN (c.osrm_base_r_t*c.factor_applied)-c.conab_r_t ELSE NULL END as gap_vs_conab_r_t,
               CASE WHEN c.conab_r_tkm IS NOT NULL AND c.osrm_base_r_tkm IS NOT NULL AND c.factor_applied IS NOT NULL
                    THEN (c.osrm_base_r_tkm*c.factor_applied)-c.conab_r_tkm ELSE NULL END as gap_vs_conab_r_tkm,
               CASE WHEN c.antt_floor_r_t IS NOT NULL AND c.osrm_base_r_t IS NOT NULL AND c.factor_applied IS NOT NULL
                    THEN (c.osrm_base_r_t*c.factor_applied)-c.antt_floor_r_t ELSE NULL END as gap_vs_antt_floor_r_t,
               'route_model' as model_version,
               'payload32_kmpl2.2_axmult_from_ref_axle_multiplier' as assumption_set,
               CURRENT_TIMESTAMP as analytics_updated_at
        FROM iceberg.oidw.tmp_route_stage_6_calibration c;
        """
        run_sql(conn_id, sql)

    s0 = stage_0_universe()
    s1 = stage_1_osrm_tmp()
    s2 = stage_2_tolls_tmp()
    s3 = stage_3_fuel_tmp()
    s4 = stage_4_antt_tmp()
    s5 = stage_5_metrics_base_tmp()
    s6 = stage_6_calibration_tmp()
    s7 = stage_7_final_incremental()

    s0 >> s1 >> s2 >> s3 >> s4 >> s5 >> s6 >> s7


route_analytics_staged_pipeline()
