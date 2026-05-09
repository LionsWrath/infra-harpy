from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime
import os
import re
import requests
from pathlib import Path

TRINO_CONN_ID = "trino_default"
DAG_ID = "cepea_precos_praca_raw_pipeline"
RAW_TABLE = "iceberg.oidw.raw_cepea_precos_praca"
LANDING_BASE = Path("/data/lake/landing/cepea_precos_praca")

CEPEA_BASE = "https://www.cepea.org.br/br/indicador"
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://jackett:8191/v1")
FALLBACK_BASE = "https://www.noticiasagricolas.com.br/cotacoes"
FALLBACK_SOURCE_NAME = "NOTICIAS_AGRICOLAS_CEPEA_FALLBACK"


def _q(v):
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def _br_decimal(s):
    s = str(s or "").strip().replace("+", "")
    if not s or "s/" in s.lower() or s == "-":
        return None
    s = re.sub(r"[^0-9,.-]", "", s)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _br_date(s: str) -> str | None:
    m = re.search(r"\b(\d{2})/(\d{2})/(20\d{2})\b", str(s or ""))
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def _extract_date(text: str) -> str:
    dates = []
    for d, m, y in re.findall(r"Atualizado em:\s*(\d{2})/(\d{2})/(20\d{2})", text):
        dates.append(f"{y}-{m}-{d}")
    if not dates:
        for d, m, y in re.findall(r"\b(\d{2})/(\d{2})/(20\d{2})\b", text):
            dates.append(f"{y}-{m}-{d}")
    if not dates:
        raise RuntimeError("Could not extract source date from page")
    return max(dates)


def _split_praca_uf(label: str):
    label = " ".join(str(label or "").split())
    m = re.search(r"/([A-Z]{2})(?:\b|\s|\))", label)
    uf = m.group(1) if m else None
    praca = re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()
    praca = re.sub(r"/[A-Z]{2}\b", "", praca).strip()
    return praca, uf


def _cepea_praca_for_table(produto: str, idx: int) -> tuple[str, str | None]:
    if produto == "soja" and idx == 0:
        return "Indicador CEPEA/ESALQ - Paranaguá", "PR"
    if produto == "soja" and idx == 1:
        return "Indicador CEPEA/ESALQ - Paraná", "PR"
    if produto == "milho" and idx == 0:
        return "Indicador ESALQ/BM&FBOVESPA", None
    return f"Indicador CEPEA - {produto} #{idx+1}", None


def _fetch_via_flaresolverr(url: str) -> str:
    payload = {"cmd": "request.get", "url": url, "maxTimeout": 180000}
    r = requests.post(FLARESOLVERR_URL, json=payload, timeout=210)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "ok" or not data.get("solution", {}).get("response"):
        raise RuntimeError(f"FlareSolverr failed for {url}: {data.get('message') or data.get('status')}")
    return data["solution"]["response"]


@dag(dag_id=DAG_ID, start_date=datetime(2026, 4, 1), schedule="20 8 * * 1-5", catchup=False, tags=["raw", "agro", "soja", "milho", "cepea"])
def _pipeline():

    @task
    def fetch_to_landing() -> dict:
        landing = LANDING_BASE / "latest"
        landing.mkdir(parents=True, exist_ok=True)
        fallback_pages = {}
        cepea_pages = {}
        source_dates = []
        errors = []

        for produto in ["soja", "milho"]:
            # Official CEPEA fetch through FlareSolverr. If it fails, the fallback still keeps the DAG useful.
            cepea_url = f"{CEPEA_BASE}/{produto}.aspx"
            try:
                html = _fetch_via_flaresolverr(cepea_url)
                out_file = landing / f"cepea_{produto}.html"
                out_file.write_text(html, encoding="utf-8")
                cepea_pages[produto] = {"path": str(out_file), "url": cepea_url}
                source_dates.append(_extract_date(html))
            except Exception as e:
                errors.append(f"official_cepea_{produto}: {e}")
                (landing / f"cepea_{produto}.error.txt").write_text(str(e), encoding="utf-8")

            # Broad praça fallback from Notícias Agrícolas static pages.
            fallback_url = f"{FALLBACK_BASE}/{produto}"
            r = requests.get(fallback_url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            if "Just a moment" in r.text or "cf-challenge" in r.text:
                raise RuntimeError(f"Anti-bot challenge while fetching fallback {fallback_url}")
            out_file = landing / f"fallback_{produto}.html"
            out_file.write_text(r.text, encoding="utf-8")
            fallback_pages[produto] = {"path": str(out_file), "url": fallback_url}
            source_dates.append(_extract_date(r.text))

        return {
            "cepea_pages": cepea_pages,
            "fallback_pages": fallback_pages,
            "source_date": max(source_dates),
            "source_url": CEPEA_BASE,
            "fetch_errors": errors,
        }

    @task
    def create_stg(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        stg = f"iceberg.oidw.stg_{DAG_ID}_{meta['source_date'].replace('-', '')}"
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {stg}")
            cur.execute(f"CREATE TABLE {stg} AS SELECT * FROM {RAW_TABLE} WHERE 1=0")
        meta["stg_table"] = stg
        return meta

    @task
    def load_stg(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        from bs4 import BeautifulSoup

        rows = []
        official_count = 0
        fallback_count = 0

        # Official CEPEA indicator rows (limited indicators, not broad praça list).
        for produto, info in meta.get("cepea_pages", {}).items():
            text = Path(info["path"]).read_text(encoding="utf-8", errors="ignore")
            soup = BeautifulSoup(text, "html.parser")
            for idx, table in enumerate(soup.find_all("table")):
                data_rows = table.find_all("tr")[1:]
                if not data_rows:
                    continue
                cells = [td.get_text(" ", strip=True) for td in data_rows[0].find_all(["td", "th"])]
                if len(cells) < 2:
                    continue
                ref_date = _br_date(cells[0])
                preco = _br_decimal(cells[1])
                if not ref_date or preco is None:
                    continue
                if preco <= 1:
                    raise RuntimeError(f"Suspicious official CEPEA price for {produto}: {preco}")
                praca, uf = _cepea_praca_for_table(produto, idx)
                rows.append([
                    "CEPEA", produto, praca, uf, ref_date, preco,
                    "R$/sc", "BRL", info["url"], f"{ref_date} 00:00:00", ref_date,
                ])
                official_count += 1

        # Broad praça fallback rows.
        for produto, info in meta["fallback_pages"].items():
            text = Path(info["path"]).read_text(encoding="utf-8", errors="ignore")
            soup = BeautifulSoup(text, "html.parser")
            for table in soup.find_all("table"):
                first = table.find("tr")
                headers = [c.get_text(" ", strip=True) for c in first.find_all(["th", "td"])] if first else []
                header_text = "|".join(headers).lower()
                if "praça" not in header_text and "praca" not in header_text:
                    continue
                if "sc de 60" not in header_text and "saca de 60" not in header_text:
                    continue
                for tr in table.find_all("tr")[1:]:
                    cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                    if len(cells) < 2 or "histórico" in " ".join(cells).lower():
                        continue
                    praca, uf = _split_praca_uf(cells[0])
                    preco = _br_decimal(cells[1])
                    if not praca or not uf or preco is None:
                        continue
                    if preco <= 1:
                        raise RuntimeError(f"Suspicious CEPEA fallback price for {produto}/{praca}: {preco}")
                    rows.append([
                        FALLBACK_SOURCE_NAME, produto, praca, uf, meta["source_date"], preco,
                        "R$/sc", "BRL", info["url"], f"{meta['source_date']} 00:00:00", meta["source_date"],
                    ])
                    fallback_count += 1

        dedup = {}
        for r in rows:
            dedup[(r[0], r[1], r[2], r[3], r[4])] = r
        rows = list(dedup.values())
        if fallback_count < 10:
            raise RuntimeError(f"Too few CEPEA fallback praça rows parsed: {fallback_count}")

        stg = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            for i in range(0, len(rows), 200):
                values = []
                for x in rows[i:i+200]:
                    values.append("(" + ", ".join([
                        _q(x[0]), _q(x[1]), _q(x[2]), _q(x[3]), f"DATE {_q(x[4])}", str(x[5]),
                        _q(x[6]), _q(x[7]), _q(x[8]), f"TIMESTAMP {_q(x[9])}", _q(x[10]), "CURRENT_TIMESTAMP"
                    ]) + ")")
                cur.execute(
                    f"INSERT INTO {stg} (fonte, produto, praca, uf, data_referencia, preco_valor, preco_unidade, moeda, source_url, dt_coleta, ingest_datestr, ingested_at) VALUES "
                    + ", ".join(values)
                )
        meta["row_count"] = len(rows)
        meta["official_count"] = official_count
        meta["fallback_count"] = fallback_count
        return meta

    @task
    def promote(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        if int(meta.get("row_count", 0)) <= 0:
            raise RuntimeError("Refusing to promote empty CEPEA stage")
        stg = meta["stg_table"]
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {RAW_TABLE} WHERE ingest_datestr IN (SELECT DISTINCT ingest_datestr FROM {stg})")
            cur.execute(f"INSERT INTO {RAW_TABLE} SELECT * FROM {stg}")
            cur.execute(f"DROP TABLE IF EXISTS {stg}")
        return meta

    @task
    def validate_target(meta: dict, conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT count(*), count(DISTINCT fonte), count(DISTINCT produto), min(preco_valor), max(preco_valor) FROM {RAW_TABLE} WHERE ingest_datestr = {_q(meta['source_date'])}")
            cnt, fontes, produtos, pmin, pmax = cur.fetchone()
            cur.execute(f"SELECT count(*) FROM {RAW_TABLE} WHERE ingest_datestr = {_q(meta['source_date'])} AND fonte = 'CEPEA'")
            cepea_cnt = cur.fetchone()[0]
        if cnt < 10:
            raise RuntimeError(f"CEPEA DQ failed: expected >=10 rows for {meta['source_date']}, got {cnt}")
        if produtos < 2:
            raise RuntimeError(f"CEPEA DQ failed: expected soja+milho for {meta['source_date']}, got {produtos} produtos")
        if pmin is None or pmin <= 1 or pmax is None:
            raise RuntimeError(f"CEPEA DQ failed: invalid price range for {meta['source_date']}: {pmin}..{pmax}")
        if cepea_cnt < 1:
            raise RuntimeError(f"CEPEA DQ failed: expected at least one official CEPEA row for {meta['source_date']}")

    validate_target(promote(load_stg(create_stg(fetch_to_landing()))))


_pipeline()
