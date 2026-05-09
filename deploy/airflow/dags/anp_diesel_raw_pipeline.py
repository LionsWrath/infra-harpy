"""
ANP combustiveis raw ingestion pipeline
Flow: landing zone -> stg -> promote to raw (partition overwrite by source_dataset + datestr)
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from airflow.operators.python import get_current_context
from pendulum import datetime, now

import os
import re
import requests
import pandas as pd
from pathlib import Path
from urllib.parse import urlparse

TRINO_CONN_ID = "trino_default"
LANDING_BASE = Path("/opt/airflow/landing/anp_combustiveis")
SOURCE_PAGE = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/serie-historica-de-precos-de-combustiveis"

DATASET_PATTERNS = {
    # supports legacy monthly naming (e.g. ...-01.csv) and newer open-data naming
    "diesel_gnv_monthly": r"precos-diesel-gnv.*\.csv$",
    "gasolina_etanol_monthly": r"precos-gasolina-etanol.*\.csv$",
    "glp_monthly": r"precos-glp.*\.csv$",
}


def _extract_year_month(url: str):
    m = re.search(r"/dsan/(\d{4})/(\d{2})", url)
    if m:
        return int(m.group(1)), int(m.group(2))
    m2 = re.search(r"/dsan/(\d{4})/.*-(\d{2})\.csv$", url)
    if m2:
        return int(m2.group(1)), int(m2.group(2))
    return (0, 0)


@dag(
    dag_id="anp_diesel_raw_pipeline",
    start_date=datetime(2023, 1, 1),
    schedule="0 9 5 * *",
    catchup=False,
    tags=["anp", "combustiveis", "raw"],
)
def anp_diesel_raw_pipeline():

    @task
    def fetch_to_landing() -> dict:
        html = requests.get(SOURCE_PAGE, timeout=30).text
        links = sorted(set(re.findall(r'href="([^"]+)"', html)))
        dsan_links = [l for l in links if "/arquivos/shpc/dsan/" in l and l.lower().endswith(".csv")]

        ctx = get_current_context()
        ds = ctx.get("ds")  # YYYY-MM-DD logical date
        mref = re.match(r"^(\d{4})-(\d{2})-\d{2}$", str(ds))
        if not mref:
            raise RuntimeError(f"Invalid Airflow ds format: {ds}")
        ref_year = int(mref.group(1))
        ref_month = int(mref.group(2))

        selected = {}
        for source_dataset, patt in DATASET_PATTERNS.items():
            matches = [l for l in dsan_links if re.search(patt, l.lower())]
            if not matches:
                raise RuntimeError(f"No ANP files found for dataset {source_dataset}")

            filtered = [l for l in matches if _extract_year_month(l) == (ref_year, ref_month)]
            if filtered:
                url = sorted(filtered)[-1]
            else:
                # partial-load mode: fallback to latest available for this dataset
                url = sorted(matches, key=_extract_year_month)[-1]

            y, m = _extract_year_month(url)
            selected[source_dataset] = {
                "url": url,
                "year": y,
                "month": m,
                "datestr": f"{y:04d}-{m:02d}-01",
            }

        run_date = now("UTC").to_date_string()
        raw_dir = LANDING_BASE / f"run_date={run_date}" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        files = []
        for source_dataset, info in selected.items():
            url = info["url"]
            filename = os.path.basename(urlparse(url).path) or f"{source_dataset}.csv"
            out_path = raw_dir / filename
            with requests.get(url, timeout=120, stream=True) as r:
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)

            files.append(
                {
                    "source_dataset": source_dataset,
                    "url": url,
                    "source_file": filename,
                    "path": str(out_path),
                    "run_date": run_date,
                    "datestr": info["datestr"],
                }
            )

        return {"run_date": run_date, "files": files}

    @task
    def create_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        run_date = meta["run_date"]
        stg_table = f"iceberg.oidw.stg_anp_combustiveis_raw_{run_date.replace('-', '')}"
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {stg_table}")
            cur.execute(
                f"""
                CREATE TABLE {stg_table} AS
                SELECT * FROM iceberg.oidw.raw_anp_combustiveis
                WHERE 1 = 0
                """
            )
        return {**meta, "stg_table": stg_table}

    @task
    def load_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg_table = meta["stg_table"]

        col = {
            "Regiao - Sigla": "regiao_sigla",
            "Estado - Sigla": "estado_sigla",
            "Municipio": "municipio",
            "Revenda": "revenda",
            "CNPJ da Revenda": "cnpj_revenda",
            "Nome da Rua": "nome_rua",
            "Numero Rua": "numero_rua",
            "Complemento": "complemento",
            "Bairro": "bairro",
            "Cep": "cep",
            "Produto": "produto",
            "Data da Coleta": "data_coleta_raw",
            "Valor de Venda": "valor_venda_raw",
            "Valor de Compra": "valor_compra_raw",
            "Unidade de Medida": "unidade_medida_raw",
            "Bandeira": "bandeira",
        }

        all_rows = []
        for f in meta["files"]:
            df = pd.read_csv(f["path"], sep=";", dtype=str, encoding="utf-8", keep_default_na=False)
            missing = [k for k in col if k not in df.columns]
            if missing:
                raise RuntimeError(f"Missing expected ANP columns in {f['source_file']}: {missing}")

            df = df[list(col.keys())].rename(columns=col)
            for r in df.to_dict("records"):
                all_rows.append([
                    r.get("regiao_sigla", ""),
                    r.get("estado_sigla", ""),
                    r.get("municipio", ""),
                    r.get("revenda", ""),
                    r.get("cnpj_revenda", ""),
                    r.get("nome_rua", ""),
                    r.get("numero_rua", ""),
                    r.get("complemento", ""),
                    r.get("bairro", ""),
                    r.get("cep", ""),
                    r.get("produto", ""),
                    r.get("data_coleta_raw", ""),
                    r.get("valor_venda_raw", ""),
                    r.get("valor_compra_raw", ""),
                    r.get("unidade_medida_raw", ""),
                    r.get("bandeira", ""),
                    f["source_dataset"],
                    f["source_file"],
                    f["run_date"],
                    f["datestr"],
                ])

        def q(v):
            if v is None:
                return "NULL"
            return "'" + str(v).replace("'", "''") + "'"

        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            chunk_size = 200
            for i in range(0, len(all_rows), chunk_size):
                part = all_rows[i:i + chunk_size]
                tuples = []
                for r in part:
                    tuples.append(
                        "(" + ", ".join([
                            q(r[0]), q(r[1]), q(r[2]), q(r[3]), q(r[4]),
                            q(r[5]), q(r[6]), q(r[7]), q(r[8]), q(r[9]),
                            q(r[10]), q(r[11]), q(r[12]), q(r[13]), q(r[14]),
                            q(r[15]), q(r[16]), q(r[17]), f"DATE {q(r[18])}", f"DATE {q(r[19])}", "CURRENT_TIMESTAMP"
                        ]) + ")"
                    )

                sql = (
                    f"INSERT INTO {stg_table} ("
                    "regiao_sigla, estado_sigla, municipio, revenda, cnpj_revenda,"
                    "nome_rua, numero_rua, complemento, bairro, cep,"
                    "produto, data_coleta_raw, valor_venda_raw, valor_compra_raw,"
                    "unidade_medida_raw, bandeira, source_dataset, source_file, source_run_date, datestr, ingested_at"
                    ") VALUES\n" + ",\n".join(tuples)
                )
                cur.execute(sql)

        return {**meta, "inserted_rows": len(all_rows)}

    @task
    def validate_stg_not_empty(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg_table = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT count(*) FROM {stg_table}")
            n = int(cur.fetchone()[0])
        if n <= 0:
            raise RuntimeError(f"STG table {stg_table} is empty; aborting promote to protect raw table")
        return {**meta, "stg_row_count": n}

    @task
    def promote_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg_table = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                DELETE FROM iceberg.oidw.raw_anp_combustiveis
                WHERE (source_dataset, datestr) IN (
                    SELECT DISTINCT source_dataset, datestr
                    FROM {stg_table}
                )
                """
            )
            cur.execute(
                f"""
                INSERT INTO iceberg.oidw.raw_anp_combustiveis
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
    m2 = create_stg_table(meta=m1)
    m3 = load_stg_table(meta=m2)
    m4 = validate_stg_not_empty(meta=m3)
    m5 = promote_stg_table(meta=m4)
    cleanup_stg_table(meta=m5)


anp_diesel_raw_pipeline()
