from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime
import re
import requests
import subprocess
from pathlib import Path

TRINO_CONN_ID = "trino_default"
DAG_ID = "estadual_precos_interior_raw_pipeline"
RAW_TABLE = "iceberg.oidw.raw_estadual_precos_interior"
LANDING_BASE = Path("/data/lake/landing/estadual_precos_interior")
SOURCE_URL = "https://www.agricultura.pr.gov.br/system/files/publico/Precos/prp.xls"
SOURCE_NAME = "DERAL_PR"


def q(v):
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def extract_source_date_from_period(s: str):
    m = re.search(r"PER[IÍ]ODO:\s*(\d{2})/(\d{2})/(20\d{2})\s*a\s*(\d{2})/(\d{2})/(20\d{2})", s, re.I)
    if m:
        return f"{m.group(6)}-{m.group(5)}-{m.group(4)}"
    m = re.search(r"(\d{2})/(\d{2})/(20\d{2})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def fetch_binary_with_fallback(url: str, out_file: Path):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Connection": "keep-alive"}
    session = requests.Session()
    for _ in range(3):
        try:
            r = session.get(url, timeout=120, headers=headers, allow_redirects=True)
            r.raise_for_status()
            out_file.write_bytes(r.content)
            return dict(r.headers)
        except Exception:
            pass
    p = subprocess.run(["curl", "-fsSL", "-A", "Mozilla/5.0", "-D", "-", "-o", str(out_file), url], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"fetch failed via requests and curl: {p.stderr[:300]}")
    hdr = {}
    for line in p.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            hdr[k.strip()] = v.strip()
    return hdr


@dag(dag_id=DAG_ID, start_date=datetime(2026, 4, 1), schedule=None, catchup=False, tags=["raw", "agro", "deral", "parana"])
def pipeline():
    @task
    def fetch_to_landing() -> dict:
        landing = LANDING_BASE / "latest"
        landing.mkdir(parents=True, exist_ok=True)
        out_file = landing / "prp.xls"
        fetch_binary_with_fallback(SOURCE_URL, out_file)
        if out_file.stat().st_size < 1000:
            raise RuntimeError("Downloaded DERAL XLS is unexpectedly small")
        return {"path": str(out_file), "source_url": SOURCE_URL}

    @task
    def create_stg(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        # Date is discovered from the workbook during load; use a stable temp table per DAG.
        stg = f"iceberg.oidw.stg_{DAG_ID}"
        h = TrinoHook(trino_conn_id=conn_id)
        with h.get_conn() as c:
            cur = c.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {stg}")
            cur.execute(f"CREATE TABLE {stg} AS SELECT * FROM {RAW_TABLE} WHERE 1=0")
        meta["stg_table"] = stg
        return meta

    @task
    def load_stg(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        import xlrd

        book = xlrd.open_workbook(meta["path"])
        sh = book.sheet_by_index(0)
        header0 = " ".join(str(sh.cell_value(0, j)) for j in range(sh.ncols))
        source_date = extract_source_date_from_period(header0)
        if not source_date:
            raise RuntimeError("Cannot extract DERAL source date from XLS period header")

        headers = [str(sh.cell_value(1, j)).strip() for j in range(sh.ncols)]
        city_cols = [(j, h) for j, h in enumerate(headers) if j >= 3 and h and h not in {"MÉDIA", "MSA", "%MSA"}]
        rows = []
        for i in range(2, sh.nrows):
            produto_raw = str(sh.cell_value(i, 1)).strip()
            produto_norm = produto_raw.lower()
            if produto_norm not in {"soja", "milho"}:
                continue
            unidade = str(sh.cell_value(i, 2)).strip() or "60 kg"
            produto = "soja" if produto_norm == "soja" else "milho"
            for j, cidade in city_cols:
                val = sh.cell_value(i, j)
                if val in (None, ""):
                    continue
                try:
                    preco = float(val)
                except Exception:
                    continue
                if preco <= 1:
                    raise RuntimeError(f"Suspicious DERAL price for {produto}/{cidade}: {preco}")
                rows.append([
                    SOURCE_NAME, produto, cidade, "PR", source_date, preco,
                    f"R$/{unidade}", "BRL", meta["source_url"], f"{source_date} 00:00:00", source_date,
                ])

        if len(rows) < 20:
            raise RuntimeError(f"Too few DERAL soja/milho rows parsed: {len(rows)}")

        stg = meta["stg_table"]
        h = TrinoHook(trino_conn_id=conn_id)
        with h.get_conn() as c:
            cur = c.cursor()
            for i in range(0, len(rows), 200):
                values = []
                for x in rows[i:i+200]:
                    values.append("(" + ", ".join([
                        q(x[0]), q(x[1]), q(x[2]), q(x[3]), f"DATE {q(x[4])}", str(x[5]),
                        q(x[6]), q(x[7]), q(x[8]), f"TIMESTAMP {q(x[9])}", q(x[10]), "CURRENT_TIMESTAMP"
                    ]) + ")")
                cur.execute(
                    f"INSERT INTO {stg} (fonte, produto, praca, uf, data_referencia, preco_valor, preco_unidade, moeda, source_url, dt_coleta, ingest_datestr, ingested_at) VALUES "
                    + ", ".join(values)
                )
        meta["source_date"] = source_date
        meta["row_count"] = len(rows)
        return meta

    @task
    def promote(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        if int(meta.get("row_count", 0)) <= 0:
            raise RuntimeError("Refusing to promote empty DERAL stage")
        stg = meta["stg_table"]
        h = TrinoHook(trino_conn_id=conn_id)
        with h.get_conn() as c:
            cur = c.cursor()
            cur.execute(f"DELETE FROM {RAW_TABLE} WHERE ingest_datestr IN (SELECT DISTINCT ingest_datestr FROM {stg})")
            cur.execute(f"INSERT INTO {RAW_TABLE} SELECT * FROM {stg}")
            cur.execute(f"DROP TABLE IF EXISTS {stg}")
        return meta

    @task
    def validate_target(meta: dict, conn_id: str = TRINO_CONN_ID):
        h = TrinoHook(trino_conn_id=conn_id)
        with h.get_conn() as c:
            cur = c.cursor()
            cur.execute(f"SELECT count(*), count(DISTINCT produto), min(preco_valor), max(preco_valor) FROM {RAW_TABLE} WHERE ingest_datestr = {q(meta['source_date'])}")
            cnt, produtos, pmin, pmax = cur.fetchone()
        if cnt < 20:
            raise RuntimeError(f"DERAL DQ failed: expected >=20 rows for {meta['source_date']}, got {cnt}")
        if produtos < 2:
            raise RuntimeError(f"DERAL DQ failed: expected soja+milho for {meta['source_date']}, got {produtos} produtos")
        if pmin is None or pmin <= 1 or pmax is None:
            raise RuntimeError(f"DERAL DQ failed: invalid price range for {meta['source_date']}: {pmin}..{pmax}")

    validate_target(promote(load_stg(create_stg(fetch_to_landing()))))

pipeline()
