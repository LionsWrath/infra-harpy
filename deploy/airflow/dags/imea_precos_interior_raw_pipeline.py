from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime
import requests
from pathlib import Path

TRINO_CONN_ID = "trino_default"
DAG_ID = "imea_precos_interior_raw_pipeline"
RAW_TABLE = "iceberg.oidw.raw_imea_precos_interior"
LANDING_BASE = Path("/data/lake/landing/imea_precos_interior")
SOURCE_BASE = "https://api1.imea.com.br/api/v2/mobile/cadeias"
SOURCE_NAME = "IMEA"
PRODUCTS = {
    "soja": {"cadeia_id": 4, "indicador_final_id": "708192508838936580"},
    "milho": {"cadeia_id": 3, "indicador_final_id": "708192508838936581"},
}


def _q(v):
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def _to_date(value: str) -> str:
    if not value:
        raise RuntimeError("Missing source date")
    s = str(value).strip()
    if "T" in s and len(s) >= 10:
        return s[:10]
    if "/" in s:
        d, m, y = s.split("/")[:3]
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    raise RuntimeError(f"Unsupported date format: {value}")


@dag(dag_id=DAG_ID, start_date=datetime(2026, 4, 1), schedule=None, catchup=False, tags=["raw", "agro", "soja", "milho", "imea"])
def _pipeline():

    @task
    def fetch_to_landing() -> dict:
        landing = LANDING_BASE / "latest"
        landing.mkdir(parents=True, exist_ok=True)
        pages = {}
        source_dates = []
        for produto, cfg in PRODUCTS.items():
            url = f"{SOURCE_BASE}/{cfg['cadeia_id']}/cotacoes"
            r = requests.get(
                url,
                timeout=120,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Origin": "https://www.imea.com.br",
                    "Referer": f"https://www.imea.com.br/imea-site/indicador-{produto}",
                },
            )
            r.raise_for_status()
            out_file = landing / f"{produto}.json"
            out_file.write_text(r.text, encoding="utf-8")
            rows = [x for x in r.json() if str(x.get("IndicadorFinalId")) == cfg["indicador_final_id"]]
            if not rows:
                raise RuntimeError(f"No IMEA rows found for {produto} indicador {cfg['indicador_final_id']}")
            source_dates.append(max(_to_date(x.get("DataPublicacao")) for x in rows if x.get("DataPublicacao")))
            pages[produto] = {"path": str(out_file), "url": url, "indicador_final_id": cfg["indicador_final_id"]}
        return {"pages": pages, "source_url": SOURCE_BASE, "source_date": max(source_dates)}

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
        import json

        rows = []
        for produto, info in meta["pages"].items():
            data = json.loads(Path(info["path"]).read_text(encoding="utf-8"))
            product_rows = [x for x in data if str(x.get("IndicadorFinalId")) == info["indicador_final_id"]]
            source_date = max(_to_date(x.get("DataPublicacao")) for x in product_rows if x.get("DataPublicacao"))
            for item in product_rows:
                praca = str(item.get("Localidade") or "").strip()
                unidade = str(item.get("UnidadeSigla") or "R$/sc").strip()
                valor = item.get("Valor")
                if not praca or valor is None:
                    continue
                valor = float(valor)
                if valor <= 1:
                    raise RuntimeError(f"Suspicious IMEA price for {produto}/{praca}: {valor}")
                rows.append([
                    SOURCE_NAME,
                    produto,
                    praca,
                    "MT",
                    source_date,
                    valor,
                    unidade,
                    "BRL",
                    info["url"],
                    f"{source_date} 00:00:00",
                    source_date,
                ])

        dedup = {}
        for r in rows:
            dedup[(r[0], r[1], r[2], r[3], r[4])] = r
        rows = list(dedup.values())
        if len(rows) < 20:
            raise RuntimeError(f"Too few IMEA rows parsed: {len(rows)}")

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
        return meta

    @task
    def promote(meta: dict, conn_id: str = TRINO_CONN_ID) -> dict:
        if int(meta.get("row_count", 0)) <= 0:
            raise RuntimeError("Refusing to promote empty IMEA stage")
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
            cur.execute(f"SELECT count(*), count(DISTINCT produto), min(preco_valor), max(preco_valor) FROM {RAW_TABLE} WHERE ingest_datestr = {_q(meta['source_date'])}")
            cnt, produtos, pmin, pmax = cur.fetchone()
        if cnt < 20:
            raise RuntimeError(f"IMEA DQ failed: expected >=20 rows for {meta['source_date']}, got {cnt}")
        if produtos < 2:
            raise RuntimeError(f"IMEA DQ failed: expected soja+milho for {meta['source_date']}, got {produtos} produtos")
        if pmin is None or pmin <= 1 or pmax is None:
            raise RuntimeError(f"IMEA DQ failed: invalid price range for {meta['source_date']}: {pmin}..{pmax}")

    validate_target(promote(load_stg(create_stg(fetch_to_landing()))))


_pipeline()
