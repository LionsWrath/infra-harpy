"""
BCB SELIC/PTAX raw ingestion pipeline
Flow: landing zone -> stg -> promote to raw (partition replace by source_api + datestr)
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from airflow.operators.python import get_current_context
from pendulum import datetime, now

import calendar
import csv
import re
import requests
from datetime import date as dt_date, datetime as dt_datetime, timedelta
from pathlib import Path

TRINO_CONN_ID = "trino_default"
LANDING_BASE = Path("/data/lake/landing/bcb_rates_fx")


def _month_bounds(y: int, m: int):
    last = calendar.monthrange(y, m)[1]
    return dt_date(y, m, 1), dt_date(y, m, last)


@dag(
    dag_id="bcb_rates_fx_raw_pipeline",
    start_date=datetime(2026, 3, 29),
    schedule="30 3 * * *",
    catchup=False,
    tags=["bcb", "selic", "ptax", "raw"],
)
def bcb_rates_fx_raw_pipeline():

    @task
    def fetch_to_landing() -> dict:
        ctx = get_current_context()
        ds = ctx.get("ds")  # YYYY-MM-DD
        m = re.match(r"^(\d{4})-(\d{2})-\d{2}$", str(ds))
        if not m:
            raise RuntimeError(f"Invalid ds format: {ds}")
        year = int(m.group(1))
        month = int(m.group(2))

        d0, d1 = _month_bounds(year, month)
        run_date = now("UTC").to_date_string()

        raw_dir = LANDING_BASE / f"run_date={run_date}" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        out_csv = raw_dir / f"bcb_rates_fx_{year:04d}{month:02d}.csv"

        rows = []

        # 1) SELIC (SGS 432)
        selic_url = (
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados"
            f"?formato=json&dataInicial={d0.strftime('%d/%m/%Y')}&dataFinal={d1.strftime('%d/%m/%Y')}"
        )
        r = requests.get(selic_url, timeout=30)
        r.raise_for_status()
        for item in r.json():
            d_raw = item.get("data", "")
            try:
                d_obj = dt_datetime.strptime(d_raw, "%d/%m/%Y").date()
                d_iso = d_obj.isoformat()
            except Exception:
                d_iso = d0.isoformat()
            rows.append({
                "source_api": "sgs",
                "series_code": "432",
                "metric_name": "selic_meta",
                "date_raw": d_raw,
                "value_raw": str(item.get("valor", "")),
                "currency_pair": "",
                "source_url": selic_url,
                "source_run_date": run_date,
                "datestr": d_iso,
            })

        # 2) PTAX USD/BRL daily in month
        cur = d0
        while cur <= d1:
            q = cur.strftime("%m-%d-%Y")
            ptax_url = (
                "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
                "CotacaoDolarDia(dataCotacao=@dataCotacao)"
                f"?@dataCotacao='{q}'&$top=100&$format=json"
            )
            try:
                pr = requests.get(ptax_url, timeout=25)
                pr.raise_for_status()
                values = pr.json().get("value", [])
                for v in values:
                    dhr = str(v.get("dataHoraCotacao", ""))
                    d_iso = dhr[:10] if len(dhr) >= 10 else ""
                    if d_iso:
                        d_fmt = dt_datetime.fromisoformat(d_iso).strftime('%d/%m/%Y')
                    else:
                        d_fmt = cur.strftime('%d/%m/%Y')

                    compra = v.get("cotacaoCompra")
                    venda = v.get("cotacaoVenda")
                    if compra is not None:
                        rows.append({
                            "source_api": "ptax",
                            "series_code": "usd_brl_compra",
                            "metric_name": "ptax_compra",
                            "date_raw": d_fmt,
                            "value_raw": str(compra),
                            "currency_pair": "USD/BRL",
                            "source_url": ptax_url,
                            "source_run_date": run_date,
                            "datestr": dt_datetime.strptime(d_fmt, "%d/%m/%Y").date().isoformat(),
                        })
                    if venda is not None:
                        rows.append({
                            "source_api": "ptax",
                            "series_code": "usd_brl_venda",
                            "metric_name": "ptax_venda",
                            "date_raw": d_fmt,
                            "value_raw": str(venda),
                            "currency_pair": "USD/BRL",
                            "source_url": ptax_url,
                            "source_run_date": run_date,
                            "datestr": dt_datetime.strptime(d_fmt, "%d/%m/%Y").date().isoformat(),
                        })
            except Exception:
                # keep resilient on specific day failures
                pass
            cur += timedelta(days=1)

        if not rows:
            raise RuntimeError("No BCB rows collected for selected month")

        cols = [
            "source_api", "series_code", "metric_name", "date_raw", "value_raw",
            "currency_pair", "source_url", "source_run_date", "datestr"
        ]
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in rows:
                w.writerow(row)

        return {
            "path": str(out_csv),
            "run_date": run_date,
            "row_count": len(rows),
        }

    @task
    def create_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        run_date = meta["run_date"]
        stg_table = f"iceberg.oidw.stg_bcb_rates_fx_raw_{run_date.replace('-', '')}"
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {stg_table}")
            cur.execute(
                f"""
                CREATE TABLE {stg_table} AS
                SELECT * FROM iceberg.oidw.raw_bcb_rates_fx
                WHERE 1 = 0
                """
            )
        return {**meta, "stg_table": stg_table}

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
                    row.get("source_api", ""),
                    row.get("series_code", ""),
                    row.get("metric_name", ""),
                    row.get("date_raw", ""),
                    row.get("value_raw", ""),
                    row.get("currency_pair", ""),
                    row.get("source_url", ""),
                    row.get("source_run_date", ""),
                    row.get("datestr", ""),
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
                            q(r[0]), q(r[1]), q(r[2]), q(r[3]), q(r[4]), q(r[5]), q(r[6]),
                            f"DATE {q(r[7])}", f"DATE {q(r[8])}", "CURRENT_TIMESTAMP"
                        ]) + ")"
                    )
                sql = (
                    f"INSERT INTO {stg_table} ("
                    "source_api, series_code, metric_name, date_raw, value_raw, currency_pair, source_url, source_run_date, datestr, ingested_at"
                    ") VALUES\n" + ",\n".join(tuples)
                )
                cur.execute(sql)

        return meta

    @task
    def promote_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg_table = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                DELETE FROM iceberg.oidw.raw_bcb_rates_fx
                WHERE (source_api, datestr) IN (
                    SELECT DISTINCT source_api, datestr
                    FROM {stg_table}
                )
                """
            )
            cur.execute(
                f"""
                INSERT INTO iceberg.oidw.raw_bcb_rates_fx
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
    m4 = promote_stg_table(meta=m3)
    cleanup_stg_table(meta=m4)


bcb_rates_fx_raw_pipeline()
