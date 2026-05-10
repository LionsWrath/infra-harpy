from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime

import csv
import io
import requests

TRINO_CONN_ID = "trino_default"
ANTT_CSV_URL = (
    "https://dados.antt.gov.br/dataset/a7e1e12d-f8e8-40cd-bc1f-57973a4a4a6d/"
    "resource/de9b0e18-7caa-4849-9f64-451302b4c274/download/"
    "dados-dos-pracas-de-pedagio2_2026.csv"
)


def _q(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).strip()
    if s == "":
        return "NULL"
    return "'" + s.replace("'", "''") + "'"


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    if s == "":
        return None
    try:
        return float(s)
    except Exception:
        return None


def _date_dmy(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        d, m, y = s.split("/")
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except Exception:
        return None


@dag(
    dag_id="raw_antt_pracas_pedagio_pipeline",
    start_date=datetime(2026, 4, 24),
    schedule=None,
    catchup=False,
    tags=["antt", "tolls", "raw", "on-demand"],
)
def raw_antt_pracas_pedagio_pipeline():
    @task
    def ingest(conn_id: str = TRINO_CONN_ID):
        resp = requests.get(ANTT_CSV_URL, timeout=120)
        resp.raise_for_status()
        text = resp.content.decode("latin-1", errors="replace")
        rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))

        if len(rows) == 0:
            raise ValueError("ANTT CSV returned zero rows")

        hook = TrinoHook(trino_conn_id=conn_id)
        conn = hook.get_conn()
        cur = conn.cursor()

        cur.execute("DROP TABLE IF EXISTS iceberg.oidw.raw_antt_pracas_pedagio")
        cur.execute(
            """
            CREATE TABLE iceberg.oidw.raw_antt_pracas_pedagio (
              source_url varchar,
              ingested_at timestamp(6) with time zone,
              concessionaria varchar,
              praca_de_pedagio varchar,
              ano_do_pnv_snv varchar,
              rodovia varchar,
              uf varchar,
              km_m double,
              municipal varchar,
              tipo_de_pista varchar,
              sentido varchar,
              situacao varchar,
              data_da_inativacao date,
              latitude double,
              longitude double
            ) WITH (format='PARQUET')
            """
        )

        chunk = 200
        for i in range(0, len(rows), chunk):
            part = rows[i : i + chunk]
            vals = []
            for r in part:
                d_inat = _date_dmy(r.get("data_da_inativacao"))
                vals.append(
                    "("
                    + ",".join(
                        [
                            _q(ANTT_CSV_URL),
                            "CURRENT_TIMESTAMP",
                            _q(r.get("concessionaria")),
                            _q(r.get("praca_de_pedagio")),
                            _q(r.get("ano_do_pnv_snv")),
                            _q(r.get("rodovia")),
                            _q((r.get("uf") or "").upper()),
                            _q(_num(r.get("km_m"))),
                            _q(r.get("municipal")),
                            _q(r.get("tipo_de_pista")),
                            _q(r.get("sentido")),
                            _q(r.get("situacao")),
                            f"DATE '{d_inat}'" if d_inat else "NULL",
                            _q(_num(r.get("latitude"))),
                            _q(_num(r.get("longitude"))),
                        ]
                    )
                    + ")"
                )
            cur.execute("INSERT INTO iceberg.oidw.raw_antt_pracas_pedagio VALUES " + ",".join(vals))

        # quality checks
        cur.execute(
            """
            SELECT
              count(*) as n,
              count_if(situacao='Ativo') as n_ativo,
              count_if(latitude IS NULL OR longitude IS NULL) as n_sem_coord,
              count_if(praca_de_pedagio IS NULL OR praca_de_pedagio='') as n_sem_nome
            FROM iceberg.oidw.raw_antt_pracas_pedagio
            """
        )
        n, n_ativo, n_sem_coord, n_sem_nome = cur.fetchone()

        if n is None or n < 100:
            raise ValueError(f"Quality check failed: row_count too low ({n})")
        if n_sem_coord is not None and n_sem_coord > n * 0.5:
            raise ValueError(f"Quality check failed: too many missing coordinates ({n_sem_coord}/{n})")

        return {
            "rows": int(n),
            "active_rows": int(n_ativo or 0),
            "missing_coords": int(n_sem_coord or 0),
            "missing_name": int(n_sem_nome or 0),
        }

    ingest()


raw_antt_pracas_pedagio_pipeline()
