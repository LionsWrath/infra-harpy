"""
CONAB Frete raw ingestion pipeline
Source: https://portaldeinformacoes.conab.gov.br/downloads/arquivos/Frete.txt
Flow: fetch -> stg -> load -> promote -> cleanup
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime, now

import csv
import io
import requests
from pathlib import Path

TRINO_CONN_ID = "trino_default"
LANDING_BASE = Path("/data/lake/landing/conab_frete")
SOURCE_URL = "https://portaldeinformacoes.conab.gov.br/downloads/arquivos/Frete.txt"


@dag(
    dag_id="conab_frete_raw_pipeline",
    start_date=datetime(2026, 4, 1),
    schedule="15 4 * * *",
    catchup=False,
    tags=["conab", "frete", "raw"],
)
def conab_frete_raw_pipeline():

    @task
    def fetch_to_landing() -> dict:
        run_date = now("UTC").to_date_string()
        out_dir = LANDING_BASE / f"run_date={run_date}" / "raw"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "Frete.txt"

        r = requests.get(SOURCE_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
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
                CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_conab_frete (
                    dsc_fonte VARCHAR,
                    municipio_origem VARCHAR,
                    cod_ibge_origem VARCHAR,
                    uf_origem VARCHAR,
                    municipio_destino VARCHAR,
                    cod_ibge_destino VARCHAR,
                    uf_destino VARCHAR,
                    ano_raw VARCHAR,
                    mes_raw VARCHAR,
                    distancia_km_raw VARCHAR,
                    valor_frete_tonelada_raw VARCHAR,
                    valor_tonelada_km_raw VARCHAR,
                    source_url VARCHAR,
                    source_run_date DATE,
                    datestr VARCHAR,
                    ingest_datestr VARCHAR,
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
        stg = f"iceberg.oidw.stg_conab_frete_raw_{meta['run_date'].replace('-', '')}"
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {stg}")
            cur.execute(f"CREATE TABLE {stg} AS SELECT * FROM iceberg.oidw.raw_conab_frete WHERE 1=0")
        meta["stg_table"] = stg
        return meta

    @task
    def load_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = meta["stg_table"]

        def q(v):
            if v is None:
                return "NULL"
            return "'" + str(v).replace("'", "''") + "'"

        rows = []
        txt = Path(meta["path"]).read_text(encoding="utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(txt), delimiter=';')
        for r in reader:
            ano = (r.get("ano") or "").strip()
            mes = (r.get("mes") or "").strip()
            datestr = f"{ano}-{mes.zfill(2)}-01" if ano and mes else ""

            rows.append([
                (r.get("dsc_fonte") or "").strip(),
                (r.get("municipio_origem") or "").strip(),
                (r.get("cod_ibge_origem") or "").strip(),
                (r.get("uf_origem") or "").strip(),
                (r.get("municipio_destino") or "").strip(),
                (r.get("cod_ibge_destino") or "").strip(),
                (r.get("uf_destino") or "").strip(),
                ano,
                mes,
                (r.get("distancia_km") or "").strip(),
                (r.get("valor_frete_tonelada") or "").strip(),
                (r.get("valor_tonelada_km") or "").strip(),
                meta["source_url"],
                meta["run_date"],
                datestr,
                meta["run_date"],
            ])

        if not rows:
            raise RuntimeError("No rows parsed from Conab Frete source")

        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            for i in range(0, len(rows), 200):
                part = rows[i:i+200]
                values = []
                for r in part:
                    values.append(
                        "(" + ", ".join([
                            q(r[0]), q(r[1]), q(r[2]), q(r[3]), q(r[4]), q(r[5]), q(r[6]), q(r[7]), q(r[8]),
                            q(r[9]), q(r[10]), q(r[11]), q(r[12]), f"DATE {q(r[13])}", q(r[14]), q(r[15]), "CURRENT_TIMESTAMP"
                        ]) + ")"
                    )
                sql = (
                    f"INSERT INTO {stg} ("
                    "dsc_fonte, municipio_origem, cod_ibge_origem, uf_origem, municipio_destino, cod_ibge_destino, uf_destino, "
                    "ano_raw, mes_raw, distancia_km_raw, valor_frete_tonelada_raw, valor_tonelada_km_raw, source_url, source_run_date, datestr, ingest_datestr, ingested_at"
                    ") VALUES\n" + ",\n".join(values)
                )
                cur.execute(sql)
        return meta

    @task
    def promote_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM iceberg.oidw.raw_conab_frete WHERE datestr IN (SELECT DISTINCT datestr FROM {stg})")
            cur.execute(f"INSERT INTO iceberg.oidw.raw_conab_frete SELECT * FROM {stg}")
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


conab_frete_raw_pipeline()
