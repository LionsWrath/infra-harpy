"""
SIDRA lavouras raw ingestion pipeline
Source table: SIDRA 1612
Products: soja (2713), milho (2711), cana-de-açúcar (2696)
Flow: fetch -> stg -> load -> promote -> cleanup
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime, now

import json
import requests
from pathlib import Path

TRINO_CONN_ID = "trino_default"
LANDING_BASE = Path("/data/lake/landing/sidra_lavouras")
PRODUCT_CODES = ["2713", "2711", "2696"]
VAR_CODES = "214,215,216,109,112"


def sidra_url_for(year: str, product_code: str) -> str:
    return f"https://apisidra.ibge.gov.br/values/t/1612/n6/all/v/{VAR_CODES}/p/{year}/c81/{product_code}"


def discover_latest_years(max_year: int, count: int = 1):
    # light probe at Brazil level to discover available years for soja
    u = "https://apisidra.ibge.gov.br/values/t/1612/n1/all/v/214/p/all/c81/2713"
    r = requests.get(u, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    payload = r.json()
    rows = payload[1:] if isinstance(payload, list) and len(payload) > 1 else []
    years = sorted({int(str(x.get("D3C", "0"))) for x in rows if str(x.get("D3C", "")).isdigit() and str(x.get("V", "")) not in ("..", "-")})
    years = [y for y in years if y <= max_year]
    return [str(y) for y in years[-count:]]


@dag(
    dag_id="sidra_lavouras_raw_pipeline",
    start_date=datetime(2026, 4, 1),
    schedule="30 2 * * 1",
    catchup=False,
    tags=["sidra", "lavouras", "raw"],
)
def sidra_lavouras_raw_pipeline():

    @task
    def fetch_to_landing() -> dict:
        run_date = now("UTC").to_date_string()
        out_dir = LANDING_BASE / f"run_date={run_date}" / "raw"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "sidra_1612_chunked.json"

        current_year = int(run_date[:4])
        years = discover_latest_years(current_year, 1)

        merged = []
        for y in years:
            for pc in PRODUCT_CODES:
                url = sidra_url_for(y, pc)
                r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                payload = r.json()
                rows = payload[1:] if isinstance(payload, list) and len(payload) > 1 else []
                if rows:
                    merged.extend(rows)

        if not merged:
            raise RuntimeError("No rows returned from SIDRA chunked requests")

        out_file.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        return {"path": str(out_file), "run_date": run_date, "source_url": "sidra_1612_chunked"}

    @task
    def ensure_raw_table(conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_sidra_lavouras (
                    nc VARCHAR,
                    nn VARCHAR,
                    mc VARCHAR,
                    mn VARCHAR,
                    v_raw VARCHAR,
                    d1c VARCHAR,
                    d1n VARCHAR,
                    d2c VARCHAR,
                    d2n VARCHAR,
                    d3c VARCHAR,
                    d3n VARCHAR,
                    d4c VARCHAR,
                    d4n VARCHAR,
                    source_url VARCHAR,
                    source_run_date DATE,
                    datestr VARCHAR,
                    ingested_at TIMESTAMP(6)
                )
                WITH (
                    format='PARQUET',
                    partitioning=ARRAY['datestr']
                )
                """
            )

    @task
    def create_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = f"iceberg.oidw.stg_sidra_lavouras_raw_{meta['run_date'].replace('-', '')}"
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {stg}")
            cur.execute(f"CREATE TABLE {stg} AS SELECT * FROM iceberg.oidw.raw_sidra_lavouras WHERE 1=0")
        meta["stg_table"] = stg
        return meta

    @task
    def load_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = meta["stg_table"]
        rows = json.loads(Path(meta["path"]).read_text(encoding="utf-8"))
        if not rows:
            raise RuntimeError("No SIDRA rows parsed")

        def q(v):
            if v is None:
                return "NULL"
            return "'" + str(v).replace("'", "''") + "'"

        vals = []
        for r in rows:
            ano = str(r.get("D3C", "")).strip()
            datestr = f"{ano}-01-01" if ano.isdigit() and len(ano) == 4 else meta["run_date"]
            vals.append([
                r.get("NC"), r.get("NN"), r.get("MC"), r.get("MN"), r.get("V"),
                r.get("D1C"), r.get("D1N"), r.get("D2C"), r.get("D2N"),
                r.get("D3C"), r.get("D3N"), r.get("D4C"), r.get("D4N"),
                meta["source_url"], meta["run_date"], datestr,
            ])

        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            for i in range(0, len(vals), 300):
                part = vals[i:i+300]
                tuples = []
                for r in part:
                    tuples.append(
                        "(" + ", ".join([
                            q(r[0]), q(r[1]), q(r[2]), q(r[3]), q(r[4]), q(r[5]), q(r[6]),
                            q(r[7]), q(r[8]), q(r[9]), q(r[10]), q(r[11]), q(r[12]),
                            q(r[13]), f"DATE {q(r[14])}", q(r[15]), "CURRENT_TIMESTAMP"
                        ]) + ")"
                    )
                sql = (
                    f"INSERT INTO {stg} ("
                    "nc, nn, mc, mn, v_raw, d1c, d1n, d2c, d2n, d3c, d3n, d4c, d4n, source_url, source_run_date, datestr, ingested_at"
                    ") VALUES\n" + ",\n".join(tuples)
                )
                cur.execute(sql)
        return meta

    @task
    def promote_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM iceberg.oidw.raw_sidra_lavouras WHERE datestr IN (SELECT DISTINCT datestr FROM {stg})")
            cur.execute(f"INSERT INTO iceberg.oidw.raw_sidra_lavouras SELECT * FROM {stg}")
        return meta

    @task
    def cleanup_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID):
        stg = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            conn.cursor().execute(f"DROP TABLE IF EXISTS {stg}")

    ensure_raw_table()
    m1 = fetch_to_landing()
    m2 = create_stg_table(m1)
    m3 = load_stg_table(m2)
    m4 = promote_stg_table(m3)
    cleanup_stg_table(m4)


sidra_lavouras_raw_pipeline()
