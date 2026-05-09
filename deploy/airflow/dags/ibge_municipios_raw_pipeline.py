"""
IBGE municípios raw ingestion pipeline
Source: https://servicodados.ibge.gov.br/api/v1/localidades/municipios
Flow: fetch -> stg -> load -> promote -> cleanup
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime, now

import json
import requests
from pathlib import Path

TRINO_CONN_ID = "trino_default"
LANDING_BASE = Path("/data/lake/landing/ibge_municipios")
SOURCE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"


@dag(
    dag_id="ibge_municipios_raw_pipeline",
    start_date=datetime(2026, 4, 1),
    schedule="0 2 * * 1",  # weekly Monday 02:00 UTC
    catchup=False,
    tags=["ibge", "municipios", "raw"],
)
def ibge_municipios_raw_pipeline():

    @task
    def fetch_to_landing() -> dict:
        run_date = now("UTC").to_date_string()
        out_dir = LANDING_BASE / f"run_date={run_date}" / "raw"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "municipios.json"

        r = requests.get(SOURCE_URL, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        out_file.write_text(r.text, encoding="utf-8")
        return {"path": str(out_file), "run_date": run_date, "source_url": SOURCE_URL}

    @task
    def ensure_raw_table(conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_ibge_municipios (
                    municipio_id VARCHAR,
                    municipio_nome VARCHAR,
                    microrregiao_id VARCHAR,
                    microrregiao_nome VARCHAR,
                    mesorregiao_id VARCHAR,
                    mesorregiao_nome VARCHAR,
                    uf_id VARCHAR,
                    uf_sigla VARCHAR,
                    uf_nome VARCHAR,
                    regiao_id VARCHAR,
                    regiao_sigla VARCHAR,
                    regiao_nome VARCHAR,
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
        stg = f"iceberg.oidw.stg_ibge_municipios_raw_{meta['run_date'].replace('-', '')}"
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {stg}")
            cur.execute(f"CREATE TABLE {stg} AS SELECT * FROM iceberg.oidw.raw_ibge_municipios WHERE 1=0")
        meta["stg_table"] = stg
        return meta

    @task
    def load_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = meta["stg_table"]
        data = json.loads(Path(meta["path"]).read_text(encoding="utf-8"))

        def q(v):
            if v is None:
                return "NULL"
            return "'" + str(v).replace("'", "''") + "'"

        rows = []
        for m in data:
            mr = m.get("microrregiao") or {}
            me = mr.get("mesorregiao") or {}
            uf = me.get("UF") or {}
            rg = uf.get("regiao") or {}
            rows.append([
                m.get("id"),
                m.get("nome"),
                mr.get("id"),
                mr.get("nome"),
                me.get("id"),
                me.get("nome"),
                uf.get("id"),
                uf.get("sigla"),
                uf.get("nome"),
                rg.get("id"),
                rg.get("sigla"),
                rg.get("nome"),
                meta["source_url"],
                meta["run_date"],
                meta["run_date"],
            ])

        if not rows:
            raise RuntimeError("No municípios parsed from IBGE source")

        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            for i in range(0, len(rows), 300):
                part = rows[i:i+300]
                vals = []
                for r in part:
                    vals.append(
                        "(" + ", ".join([
                            q(r[0]), q(r[1]), q(r[2]), q(r[3]), q(r[4]), q(r[5]), q(r[6]), q(r[7]), q(r[8]),
                            q(r[9]), q(r[10]), q(r[11]), q(r[12]), f"DATE {q(r[13])}", q(r[14]), "CURRENT_TIMESTAMP"
                        ]) + ")"
                    )
                sql = (
                    f"INSERT INTO {stg} ("
                    "municipio_id, municipio_nome, microrregiao_id, microrregiao_nome, mesorregiao_id, mesorregiao_nome, "
                    "uf_id, uf_sigla, uf_nome, regiao_id, regiao_sigla, regiao_nome, source_url, source_run_date, datestr, ingested_at"
                    ") VALUES\n" + ",\n".join(vals)
                )
                cur.execute(sql)
        return meta

    @task
    def promote_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM iceberg.oidw.raw_ibge_municipios WHERE datestr IN (SELECT DISTINCT datestr FROM {stg})")
            cur.execute(f"INSERT INTO iceberg.oidw.raw_ibge_municipios SELECT * FROM {stg}")
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


ibge_municipios_raw_pipeline()
