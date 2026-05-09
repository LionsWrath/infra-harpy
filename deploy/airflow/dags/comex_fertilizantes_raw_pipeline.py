"""
COMEX fertilizers raw pipeline
Source: MDIC open data CSVs (IMP_YYYY.csv)
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime, now

import csv
import io
import requests
import urllib3
urllib3.disable_warnings()
from pathlib import Path

TRINO_CONN_ID = "trino_default"
LANDING_BASE = Path("/data/lake/landing/comex_fertilizantes")
YEARS = [2024, 2025, 2026]
BASE = "https://balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm"


@dag(
    dag_id="comex_fertilizantes_raw_pipeline",
    start_date=datetime(2026, 4, 1),
    schedule="45 4 * * *",
    catchup=False,
    tags=["comex", "fertilizantes", "raw"],
)
def comex_fertilizantes_raw_pipeline():

    @task
    def fetch_to_landing():
        run_date = now("UTC").to_date_string()
        d = LANDING_BASE / f"run_date={run_date}" / "raw"
        d.mkdir(parents=True, exist_ok=True)
        out = []
        for y in YEARS:
            url = f"{BASE}/IMP_{y}.csv"
            r = requests.get(url, timeout=180, headers={"User-Agent": "Mozilla/5.0"}, verify=False)
            r.raise_for_status()
            p = d / f"IMP_{y}.csv"
            p.write_text(r.text, encoding="utf-8", errors="ignore")
            out.append(str(p))
        return {"run_date": run_date, "files": out}

    @task
    def load_raw(meta: dict, conn_id: str = TRINO_CONN_ID):
        run_date = meta["run_date"]

        def q(v):
            if v is None:
                return "NULL"
            return "'" + str(v).replace("'", "''") + "'"

        rows = []
        for fp in meta["files"]:
            txt = Path(fp).read_text(encoding="utf-8", errors="ignore")
            rd = csv.DictReader(io.StringIO(txt), delimiter=';')
            for r in rd:
                ncm = (r.get("CO_NCM") or "").strip()
                n6 = ncm[:6]
                if n6 not in {"310420", "310540", "310210"}:
                    continue
                ano = (r.get("CO_ANO") or "").strip()
                mes = (r.get("CO_MES") or "").strip().zfill(2)
                datestr = f"{ano}-{mes}-01" if ano and mes else run_date
                rows.append([
                    datestr,
                    "import",
                    ncm,
                    "",  # product_uuid resolved in SQL join
                    (r.get("CO_PAIS") or "").strip(),
                    "",  # partner name resolved later
                    (r.get("SG_UF_NCM") or "").strip(),
                    "",
                    "",
                    (r.get("QT_ESTAT") or "").strip(),
                    (r.get("CO_UNID") or "").strip(),
                    (r.get("VL_FOB") or "").strip(),
                    "mdic_open_data",
                    f"{BASE}/{Path(fp).name}",
                    Path(fp).name,
                    run_date,
                    run_date,
                ])

        if not rows:
            raise RuntimeError("No fertilizer rows found in COMEX files")

        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()

            # stage table
            stg = f"iceberg.oidw.stg_comex_fertilizantes_raw_{run_date.replace('-', '')}"
            cur.execute(f"DROP TABLE IF EXISTS {stg}")
            cur.execute(f"CREATE TABLE {stg} AS SELECT * FROM iceberg.oidw.raw_comex_fertilizantes WHERE 1=0")

            for i in range(0, len(rows), 300):
                part = rows[i:i+300]
                vals = []
                for r in part:
                    vals.append("(" + ", ".join([
                        q(r[0]), q(r[1]), q(r[2]), q(r[3]), q(r[4]), q(r[5]), q(r[6]), q(r[7]), q(r[8]),
                        q(r[9]), q(r[10]), q(r[11]), q(r[12]), q(r[13]), q(r[14]), f"DATE {q(r[15])}", q(r[16]), "CURRENT_TIMESTAMP"
                    ]) + ")")
                cur.execute(
                    f"INSERT INTO {stg} (datestr, flow_type, ncm_code, product_uuid, partner_country_code, partner_country_name, uf_code, uf_name, product_name_raw, quantity_raw, quantity_unit_raw, value_usd_raw, source_system, source_url, source_file, source_run_date, ingest_datestr, ingested_at) VALUES\n"
                    + ",\n".join(vals)
                )

            # resolve product_uuid via NCM 6-digit map
            cur.execute(
                f"""
                CREATE TABLE iceberg.oidw.stg_comex_fertilizantes_resolved_{run_date.replace('-', '')} AS
                SELECT s.datestr, s.flow_type, s.ncm_code,
                       m.product_uuid,
                       s.partner_country_code, s.partner_country_name, s.uf_code, s.uf_name,
                       s.product_name_raw, s.quantity_raw, s.quantity_unit_raw, s.value_usd_raw,
                       s.source_system, s.source_url, s.source_file, s.source_run_date, s.ingest_datestr, s.ingested_at
                FROM {stg} s
                LEFT JOIN iceberg.oidw.dim_comex_fertilizer_product_ncm m
                  ON substr(s.ncm_code,1,6)=m.ncm_code
                """
            )
            stg2 = f"iceberg.oidw.stg_comex_fertilizantes_resolved_{run_date.replace('-', '')}"

            cur.execute(f"DELETE FROM iceberg.oidw.raw_comex_fertilizantes WHERE datestr IN (SELECT DISTINCT datestr FROM {stg2})")
            cur.execute(f"INSERT INTO iceberg.oidw.raw_comex_fertilizantes SELECT * FROM {stg2}")
            cur.execute(f"DROP TABLE IF EXISTS {stg}")
            cur.execute(f"DROP TABLE IF EXISTS {stg2}")

    m = fetch_to_landing()
    load_raw(m)


comex_fertilizantes_raw_pipeline()
