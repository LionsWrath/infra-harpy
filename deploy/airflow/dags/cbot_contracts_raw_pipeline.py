"""
CBOT contract-specific futures raw ingestion (Yahoo chart API)
Flow: landing -> stg -> promote by (root_symbol, contract_code, datestr)
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
LANDING_BASE = Path("/data/lake/landing/cbot_contracts")
ROOT_CONFIG = [
    ("ZS", ".CBT"), ("ZM", ".CBT"), ("ZL", ".CBT"), ("ZC", ".CBT"),
    ("BZ", ".NYM"), ("CL", ".NYM"), ("RB", ".NYM"), ("HO", ".NYM"), ("NG", ".NYM"),
]
MONTH_CODES = ["F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"]


def _month_bounds(y: int, m: int):
    last = calendar.monthrange(y, m)[1]
    return dt_date(y, m, 1), dt_date(y, m, last)


def _contracts_for_window(year: int):
    # current + next year coverage
    years = [str(year % 100).zfill(2), str((year + 1) % 100).zfill(2)]
    out = []
    for r, suffix in ROOT_CONFIG:
        for c in MONTH_CODES:
            for yy in years:
                code = f"{r}{c}{yy}"
                out.append((r, code, f"{code}{suffix}"))
    return out


@dag(
    dag_id="cbot_contracts_raw_pipeline",
    start_date=datetime(2026, 4, 1),
    schedule="45 3 * * *",
    catchup=False,
    tags=["cbot", "futures", "contracts", "raw"],
)
def cbot_contracts_raw_pipeline():

    @task
    def fetch_to_landing() -> dict:
        ds = str(get_current_context().get("ds"))
        year = int(ds[0:4])
        month = int(ds[5:7])
        d0, d1 = _month_bounds(year, month)

        run_date = now("UTC").to_date_string()
        out_dir = LANDING_BASE / f"run_date={run_date}" / "raw"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / f"cbot_contracts_{year:04d}{month:02d}.csv"

        rows, per_contract = [], {}
        for root, contract_code, yahoo_symbol in _contracts_for_window(year):
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
            params = {"interval": "1d", "range": "1mo", "events": "history"}
            try:
                r = requests.get(url, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code != 200:
                    continue
                payload = r.json()
                err = ((payload.get("chart") or {}).get("error"))
                res = ((payload.get("chart") or {}).get("result") or [None])[0]
                if err or not res:
                    continue

                meta = res.get("meta", {})
                currency = meta.get("currency", "")
                exchange = meta.get("exchangeName", "")
                ts = res.get("timestamp", []) or []
                q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
                adj = (((res.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose", [])

                n = 0
                for i, t in enumerate(ts):
                    d_iso = dt_datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
                    if d_iso < d0.isoformat() or d_iso > d1.isoformat():
                        continue

                    def pick(arr):
                        return arr[i] if isinstance(arr, list) and i < len(arr) else None

                    rows.append({
                        "root_symbol": root,
                        "contract_code": contract_code,
                        "yahoo_symbol": yahoo_symbol,
                        "datestr": d_iso,
                        "open_raw": "" if pick(q.get("open", [])) is None else str(pick(q.get("open", []))),
                        "high_raw": "" if pick(q.get("high", [])) is None else str(pick(q.get("high", []))),
                        "low_raw": "" if pick(q.get("low", [])) is None else str(pick(q.get("low", []))),
                        "close_raw": "" if pick(q.get("close", [])) is None else str(pick(q.get("close", []))),
                        "adj_close_raw": "" if not isinstance(adj, list) or i >= len(adj) or adj[i] is None else str(adj[i]),
                        "volume_raw": "" if pick(q.get("volume", [])) is None else str(pick(q.get("volume", []))),
                        "currency_raw": currency,
                        "exchange_raw": exchange,
                        "source_api": "yahoo_chart",
                        "source_url": url,
                        "source_run_date": run_date,
                    })
                    n += 1
                if n:
                    per_contract[contract_code] = n
            except Exception:
                continue

        if not rows:
            raise RuntimeError("No contract rows collected")

        cols = [
            "root_symbol", "contract_code", "yahoo_symbol", "datestr",
            "open_raw", "high_raw", "low_raw", "close_raw", "adj_close_raw", "volume_raw",
            "currency_raw", "exchange_raw", "source_api", "source_url", "source_run_date"
        ]
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader(); w.writerows(rows)

        return {"path": str(out_csv), "run_date": run_date, "rows": len(rows), "contracts": len(per_contract)}

    @task
    def ensure_raw_table(conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_cbot_contracts (
                    root_symbol VARCHAR,
                    contract_code VARCHAR,
                    yahoo_symbol VARCHAR,
                    datestr VARCHAR,
                    open_raw VARCHAR,
                    high_raw VARCHAR,
                    low_raw VARCHAR,
                    close_raw VARCHAR,
                    adj_close_raw VARCHAR,
                    volume_raw VARCHAR,
                    currency_raw VARCHAR,
                    exchange_raw VARCHAR,
                    source_api VARCHAR,
                    source_url VARCHAR,
                    source_run_date DATE,
                    ingested_at TIMESTAMP(6)
                )
                WITH (
                    format = 'PARQUET',
                    partitioning = ARRAY['root_symbol', 'datestr']
                )
            """)

    @task
    def create_stg(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = f"iceberg.oidw.stg_cbot_contracts_raw_{meta['run_date'].replace('-', '')}"
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            c = conn.cursor(); c.execute(f"DROP TABLE IF EXISTS {stg}")
            c.execute(f"CREATE TABLE {stg} AS SELECT * FROM iceberg.oidw.raw_cbot_contracts WHERE 1=0")
        meta["stg"] = stg
        return meta

    @task
    def load_stg(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = meta["stg"]
        def q(v):
            if v is None: return "NULL"
            return "'" + str(v).replace("'", "''") + "'"
        rows = list(csv.DictReader(open(meta["path"], encoding="utf-8")))
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            c = conn.cursor()
            for i in range(0, len(rows), 200):
                part = rows[i:i+200]
                vals = []
                for r in part:
                    vals.append("(" + ", ".join([
                        q(r["root_symbol"]), q(r["contract_code"]), q(r["yahoo_symbol"]), q(r["datestr"]),
                        q(r["open_raw"]), q(r["high_raw"]), q(r["low_raw"]), q(r["close_raw"]), q(r["adj_close_raw"]), q(r["volume_raw"]),
                        q(r["currency_raw"]), q(r["exchange_raw"]), q(r["source_api"]), q(r["source_url"]), f"DATE {q(r['source_run_date'])}", "CURRENT_TIMESTAMP"
                    ]) + ")")
                c.execute(
                    f"INSERT INTO {stg} (root_symbol, contract_code, yahoo_symbol, datestr, open_raw, high_raw, low_raw, close_raw, adj_close_raw, volume_raw, currency_raw, exchange_raw, source_api, source_url, source_run_date, ingested_at) VALUES\n" + ",\n".join(vals)
                )
        return meta

    @task
    def promote(meta: dict, conn_id: str = TRINO_CONN_ID):
        stg = meta["stg"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            c = conn.cursor()
            c.execute(f"DELETE FROM iceberg.oidw.raw_cbot_contracts WHERE (root_symbol, contract_code, datestr) IN (SELECT DISTINCT root_symbol, contract_code, datestr FROM {stg})")
            c.execute(f"INSERT INTO iceberg.oidw.raw_cbot_contracts SELECT * FROM {stg}")
            c.execute(f"DROP TABLE IF EXISTS {stg}")

    ensure_raw_table()
    m1 = fetch_to_landing()
    m2 = create_stg(m1)
    m3 = load_stg(m2)
    promote(m3)


cbot_contracts_raw_pipeline()
