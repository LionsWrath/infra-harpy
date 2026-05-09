"""
CBOT futures raw ingestion pipeline (Yahoo chart API)
Flow: landing zone -> stg -> promote to raw (partition replace by symbol + datestr)
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from airflow.operators.python import get_current_context
from pendulum import datetime, now

import calendar
import csv
import requests
from datetime import date as dt_date, datetime as dt_datetime, timezone
from pathlib import Path

TRINO_CONN_ID = "trino_default"
LANDING_BASE = Path("/data/lake/landing/cbot_futures")
SYMBOLS = ["ZS=F", "ZM=F", "ZL=F", "ZC=F", "BZ=F", "CL=F", "RB=F", "HO=F", "NG=F"]


def _month_bounds(y: int, m: int):
    last = calendar.monthrange(y, m)[1]
    return dt_date(y, m, 1), dt_date(y, m, last)


@dag(
    dag_id="cbot_futures_raw_pipeline",
    start_date=datetime(2026, 4, 1),
    schedule="30 3 * * *",
    catchup=False,
    tags=["cbot", "futures", "raw"],
)
def cbot_futures_raw_pipeline():

    @task
    def fetch_to_landing() -> dict:
        ctx = get_current_context()
        ds = str(ctx.get("ds"))
        year = int(ds[0:4])
        month = int(ds[5:7])
        d0, d1 = _month_bounds(year, month)

        run_date = now("UTC").to_date_string()
        raw_dir = LANDING_BASE / f"run_date={run_date}" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        out_csv = raw_dir / f"cbot_futures_{year:04d}{month:02d}.csv"

        rows = []
        per_symbol = {}

        for symbol in SYMBOLS:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            params = {"interval": "1d", "range": "1mo", "events": "history"}
            r = requests.get(url, params=params, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            payload = r.json()
            result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                per_symbol[symbol] = 0
                continue

            meta = result.get("meta", {})
            currency = meta.get("currency", "")
            exchange = meta.get("exchangeName", "")

            ts = result.get("timestamp", []) or []
            q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            adj = (((result.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose", [])

            n = 0
            for i, t in enumerate(ts):
                try:
                    d_iso = dt_datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
                except Exception:
                    continue
                if d_iso < d0.isoformat() or d_iso > d1.isoformat():
                    continue

                def pick(arr):
                    return arr[i] if isinstance(arr, list) and i < len(arr) else None

                rows.append({
                    "symbol": symbol,
                    "datestr": d_iso,
                    "contract_code": "",
                    "open_raw": "" if pick(q.get("open", [])) is None else str(pick(q.get("open", []))),
                    "high_raw": "" if pick(q.get("high", [])) is None else str(pick(q.get("high", []))),
                    "low_raw": "" if pick(q.get("low", [])) is None else str(pick(q.get("low", []))),
                    "close_raw": "" if pick(q.get("close", [])) is None else str(pick(q.get("close", []))),
                    "adj_close_raw": "" if not isinstance(adj, list) or i >= len(adj) or adj[i] is None else str(adj[i]),
                    "volume_raw": "" if pick(q.get("volume", [])) is None else str(pick(q.get("volume", []))),
                    "open_interest_raw": "",
                    "currency_raw": currency or "",
                    "exchange_raw": exchange or "",
                    "source_api": "yahoo_chart",
                    "source_url": url,
                    "source_run_date": run_date,
                })
                n += 1
            per_symbol[symbol] = n

        if sum(per_symbol.values()) <= 0:
            raise RuntimeError(f"No CBOT rows collected for {year:04d}-{month:02d}. Per symbol: {per_symbol}")

        cols = [
            "symbol", "datestr", "contract_code", "open_raw", "high_raw", "low_raw", "close_raw", "adj_close_raw",
            "volume_raw", "open_interest_raw", "currency_raw", "exchange_raw", "source_api", "source_url", "source_run_date"
        ]
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in rows:
                w.writerow(row)

        return {"path": str(out_csv), "run_date": run_date, "row_count": len(rows), "per_symbol": per_symbol}

    @task
    def ensure_raw_table(conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_cbot_futures (
                    symbol VARCHAR,
                    datestr VARCHAR,
                    contract_code VARCHAR,
                    open_raw VARCHAR,
                    high_raw VARCHAR,
                    low_raw VARCHAR,
                    close_raw VARCHAR,
                    adj_close_raw VARCHAR,
                    volume_raw VARCHAR,
                    open_interest_raw VARCHAR,
                    currency_raw VARCHAR,
                    exchange_raw VARCHAR,
                    source_api VARCHAR,
                    source_url VARCHAR,
                    source_run_date DATE,
                    ingested_at TIMESTAMP(6)
                )
                WITH (
                    format = 'PARQUET',
                    partitioning = ARRAY['symbol', 'datestr']
                )
                """
            )

    @task
    def create_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg_table = f"iceberg.oidw.stg_cbot_futures_raw_{meta['run_date'].replace('-', '')}"
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {stg_table}")
            cur.execute(f"CREATE TABLE {stg_table} AS SELECT * FROM iceberg.oidw.raw_cbot_futures WHERE 1=0")
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
            for row in csv.DictReader(f):
                rows.append([
                    row.get("symbol", ""), row.get("datestr", ""), row.get("contract_code", ""),
                    row.get("open_raw", ""), row.get("high_raw", ""), row.get("low_raw", ""), row.get("close_raw", ""),
                    row.get("adj_close_raw", ""), row.get("volume_raw", ""), row.get("open_interest_raw", ""),
                    row.get("currency_raw", ""), row.get("exchange_raw", ""), row.get("source_api", ""),
                    row.get("source_url", ""), row.get("source_run_date", ""),
                ])

        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            for i in range(0, len(rows), 200):
                part = rows[i:i+200]
                tuples = []
                for r in part:
                    tuples.append("(" + ", ".join([
                        q(r[0]), q(r[1]), q(r[2]), q(r[3]), q(r[4]), q(r[5]), q(r[6]), q(r[7]),
                        q(r[8]), q(r[9]), q(r[10]), q(r[11]), q(r[12]), q(r[13]), f"DATE {q(r[14])}", "CURRENT_TIMESTAMP"
                    ]) + ")")
                sql = (
                    f"INSERT INTO {stg_table} (symbol, datestr, contract_code, open_raw, high_raw, low_raw, close_raw, adj_close_raw, volume_raw, open_interest_raw, currency_raw, exchange_raw, source_api, source_url, source_run_date, ingested_at) VALUES\n"
                    + ",\n".join(tuples)
                )
                cur.execute(sql)
        return meta

    @task
    def promote_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg_table = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM iceberg.oidw.raw_cbot_futures WHERE (symbol, datestr) IN (SELECT DISTINCT symbol, datestr FROM {stg_table})")
            cur.execute(f"INSERT INTO iceberg.oidw.raw_cbot_futures SELECT * FROM {stg_table}")
        return meta

    @task
    def cleanup_stg_table(meta: dict, conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            conn.cursor().execute(f"DROP TABLE IF EXISTS {meta['stg_table']}")

    ensure_raw_table()
    m1 = fetch_to_landing()
    m2 = create_stg_table(meta=m1)
    m3 = load_stg_table(meta=m2)
    m4 = promote_stg_table(meta=m3)
    cleanup_stg_table(meta=m4)

cbot_futures_raw_pipeline()
