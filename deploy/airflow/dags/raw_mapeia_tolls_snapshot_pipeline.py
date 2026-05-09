"""
Mapeia tolls daily snapshot raw ingestion pipeline
Flow: fetch json -> landing csv -> stg -> promote to raw (partition replace by source_system + datestr)
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from airflow.operators.python import get_current_context
from pendulum import datetime, now

import csv
import requests
from pathlib import Path

TRINO_CONN_ID = "trino_default"
LANDING_BASE = Path("/opt/airflow/landing/mapeia_tolls")
SOURCE_URL = "https://www.mapeia.com.br/tolls.json"


@dag(
    dag_id="raw_mapeia_tolls_snapshot_pipeline",
    start_date=datetime(2026, 4, 20),
    schedule="15 2 * * *",
    catchup=False,
    tags=["mapeia", "tolls", "raw", "routes"],
)
def raw_mapeia_tolls_snapshot_pipeline():

    @task
    def fetch_to_landing() -> dict:
        ctx = get_current_context()
        ds = str(ctx.get("ds"))  # YYYY-MM-DD
        run_date = now("UTC").to_date_string()

        raw_dir = LANDING_BASE / f"run_date={run_date}" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        out_csv = raw_dir / f"mapeia_tolls_{ds}.csv"

        resp = requests.get(SOURCE_URL, timeout=45)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise RuntimeError("Unexpected payload for tolls.json (expected list)")

        rows = []
        for item in data:
            rows.append({
                "datestr": ds,
                "source_system": "mapeia",
                "toll_id": str(item.get("i", "")),
                "heading_code": str(item.get("h", "")),
                "toll_name_raw": str(item.get("s", "")),
                "radius_m_raw": str(item.get("r", "")),
                "price_brl_raw": str(item.get("p", "")),
                "source_url": SOURCE_URL,
                "source_run_date": run_date,
            })

        if not rows:
            raise RuntimeError("No rows returned from mapeia tolls.json")

        cols = [
            "datestr",
            "source_system",
            "toll_id",
            "heading_code",
            "toll_name_raw",
            "radius_m_raw",
            "price_brl_raw",
            "source_url",
            "source_run_date",
        ]

        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in rows:
                w.writerow(row)

        return {
            "path": str(out_csv),
            "run_date": run_date,
            "datestr": ds,
            "row_count": len(rows),
        }

    @task
    def create_raw_and_stg(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        run_date = meta["run_date"]
        stg_table = f"iceberg.oidw.stg_mapeia_tolls_raw_{run_date.replace('-', '')}"
        raw_table = "iceberg.oidw.raw_mapeia_tolls_snapshot"

        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {raw_table} (
                    datestr date,
                    source_system varchar,
                    toll_id varchar,
                    heading_code varchar,
                    toll_name_raw varchar,
                    radius_m_raw varchar,
                    price_brl_raw varchar,
                    source_url varchar,
                    source_run_date date,
                    ingested_at timestamp(6)
                )
                WITH (
                    format = 'PARQUET',
                    partitioning = ARRAY['source_system', 'datestr']
                )
                """
            )

            cur.execute(f"DROP TABLE IF EXISTS {stg_table}")
            cur.execute(
                f"""
                CREATE TABLE {stg_table} AS
                SELECT *
                FROM {raw_table}
                WHERE 1 = 0
                """
            )

        return {**meta, "stg_table": stg_table, "raw_table": raw_table}

    @task
    def load_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg_table = meta["stg_table"]

        def q(v):
            if v is None:
                return "NULL"
            return "'" + str(v).replace("'", "''") + "'"

        rows = []
        with open(meta["path"], newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                rows.append([
                    row.get("datestr", ""),
                    row.get("source_system", ""),
                    row.get("toll_id", ""),
                    row.get("heading_code", ""),
                    row.get("toll_name_raw", ""),
                    row.get("radius_m_raw", ""),
                    row.get("price_brl_raw", ""),
                    row.get("source_url", ""),
                    row.get("source_run_date", ""),
                ])

        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            chunk_size = 200
            for i in range(0, len(rows), chunk_size):
                part = rows[i:i + chunk_size]
                tuples = []
                for r in part:
                    tuples.append(
                        "(" + ", ".join([
                            f"DATE {q(r[0])}",
                            q(r[1]), q(r[2]), q(r[3]), q(r[4]), q(r[5]), q(r[6]), q(r[7]),
                            f"DATE {q(r[8])}",
                            "CURRENT_TIMESTAMP",
                        ]) + ")"
                    )

                sql = (
                    f"INSERT INTO {stg_table} ("
                    "datestr, source_system, toll_id, heading_code, toll_name_raw, radius_m_raw, price_brl_raw, source_url, source_run_date, ingested_at"
                    ") VALUES\n" + ",\n".join(tuples)
                )
                cur.execute(sql)

        return meta

    @task
    def promote_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg_table = meta["stg_table"]
        raw_table = meta["raw_table"]
        hook = TrinoHook(trino_conn_id=conn_id)

        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                DELETE FROM {raw_table}
                WHERE (source_system, datestr) IN (
                    SELECT DISTINCT source_system, datestr
                    FROM {stg_table}
                )
                """
            )
            cur.execute(
                f"""
                INSERT INTO {raw_table}
                SELECT *
                FROM {stg_table}
                """
            )

        return meta

    @task
    def cleanup_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID):
        stg_table = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {stg_table}")

    m1 = fetch_to_landing()
    m2 = create_raw_and_stg(meta=m1)
    m3 = load_stg_table(meta=m2)
    m4 = promote_stg_table(meta=m3)
    cleanup_stg_table(meta=m4)


raw_mapeia_tolls_snapshot_pipeline()
