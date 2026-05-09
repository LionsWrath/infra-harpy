from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime
import requests
import csv
import io

TRINO_CONN_ID = "trino_default"

URL_RECEITA_2024 = "https://dados.antt.gov.br/dataset/5213d6c6-f494-4cb6-93a7-822ee1cae157/resource/e44486e4-f359-4851-b8e9-2ad5b3018c99/download/receita_por_praca_2024.csv"
URL_VOL_EQ_2026_MENSAL = 'https://dados.antt.gov.br/dataset/f3ea994f-7435-4867-83b4-1ee5d1debbce/resource/e6744204-58be-40ac-bcb7-ecc23eff88f0/download/volume-trafego-equivalente-praca-pedagio-2024_mensal_consolidado.csv'
URL_VOL_2026_MENSAL = 'https://dados.antt.gov.br/dataset/5bf70ec3-b24e-4f73-99a0-78b200f5e915/resource/b7b2e108-99d8-449f-abbe-63a248b1172b/download/volume-trafego-praca-pedagio-2024_mensal_consolidado.csv'


def _fnum(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.upper() == "NULL":
        return None
    s = s.replace("R$", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _esc(s):
    return (s or "").replace("'", "''")


def _read_csv(url):
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    text = r.text
    first = text.splitlines()[0]
    delim = ";" if first.count(";") > first.count(",") else ","
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delim))
    return rows


@dag(
    dag_id="raw_antt_toll_economics_pipeline",
    start_date=datetime(2026, 4, 24),
    schedule="10 3 * * *",
    catchup=False,
    tags=["antt", "tolls", "raw", "economics"],
)
def raw_antt_toll_economics_pipeline():
    @task
    def ingest(conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()

            def ex(sql):
                cur.execute(sql)

            ex("DROP TABLE IF EXISTS iceberg.oidw.raw_antt_receita_praca")
            ex(
                """
            CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_antt_receita_praca (
              source_url varchar,
              ingested_at timestamp(6),
              concessionaria varchar,
              praca_de_pedagio varchar,
              ano_pnv_snv varchar,
              uf varchar,
              rodovia varchar,
              km_m double,
              tipo_de_pista varchar,
              sentido varchar,
              municipio varchar,
              direcao varchar,
              latitude double,
              longitude double,
              receita_pedagio double,
              mes_ano varchar,
              date_yyyy integer,
              date_mm integer
            ) WITH (format='PARQUET', partitioning=ARRAY['date_yyyy'])
            """
            )

            ex("DROP TABLE IF EXISTS iceberg.oidw.raw_antt_volume_trafego_praca")
            ex(
                """
            CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_antt_volume_trafego_praca (
              source_url varchar,
              ingested_at timestamp(6),
              concessionaria varchar,
              mes_ano varchar,
              sentido varchar,
              praca varchar,
              tipo_cobranca varchar,
              categoria_eixo varchar,
              tipo_de_veiculo varchar,
              volume_total double
            ) WITH (format='PARQUET')
            """
            )

            ex("DROP TABLE IF EXISTS iceberg.oidw.raw_antt_volume_trafego_equiv_praca")
            ex(
                """
            CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_antt_volume_trafego_equiv_praca (
              source_url varchar,
              ingested_at timestamp(6),
              concessionaria varchar,
              mes_ano varchar,
              sentido varchar,
              praca varchar,
              tipo_de_cobranca varchar,
              categoria_eixo varchar,
              tipo_de_veiculo varchar,
              volume_total double,
              multiplicador_de_tarifa double,
              volume_veiculo_equivalente double
            ) WITH (format='PARQUET')
            """
            )

            ex("DELETE FROM iceberg.oidw.raw_antt_receita_praca WHERE date_yyyy = 2024")
            ex("DELETE FROM iceberg.oidw.raw_antt_volume_trafego_praca")
            ex("DELETE FROM iceberg.oidw.raw_antt_volume_trafego_equiv_praca")

            receita = _read_csv(URL_RECEITA_2024)
            vol = _read_csv(URL_VOL_2026_MENSAL)
            vol_eq = _read_csv(URL_VOL_EQ_2026_MENSAL)

            chunk = 100
            vals = []
            for r in receita:
                mes_ano = r.get('Mes_ano') or ''
                yy = int(mes_ano.split('/')[1]) if '/' in mes_ano else None
                mm = int(mes_ano.split('/')[0]) if '/' in mes_ano else None
                vals.append(
                    "(" +
                    f"'{_esc(URL_RECEITA_2024)}', CAST(CURRENT_TIMESTAMP AS timestamp(6))," +
                    f"'{_esc(r.get('Concessionaria'))}'," +
                    f"'{_esc(r.get('Praca_de_pedagio'))}'," +
                    f"'{_esc(r.get('Ano_PNV_SNV'))}'," +
                    f"'{_esc(r.get('UF'))}'," +
                    f"'{_esc(r.get('Rodovia'))}'," +
                    ("NULL" if _fnum(r.get('Km_m')) is None else str(_fnum(r.get('Km_m')))) + "," +
                    f"'{_esc(r.get('Tipo_de_Pista'))}'," +
                    f"'{_esc(r.get('Sentido'))}'," +
                    f"'{_esc(r.get('Municipio'))}'," +
                    f"'{_esc(r.get('Direcao'))}'," +
                    ("NULL" if _fnum(r.get('Latitude')) is None else str(_fnum(r.get('Latitude')))) + "," +
                    ("NULL" if _fnum(r.get('Longitude')) is None else str(_fnum(r.get('Longitude')))) + "," +
                    ("NULL" if _fnum(r.get('Receita_Praca_de_Pedagio')) is None else str(_fnum(r.get('Receita_Praca_de_Pedagio')))) + "," +
                    f"'{_esc(mes_ano)}'," +
                    ("NULL" if yy is None else str(yy)) + "," +
                    ("NULL" if mm is None else str(mm)) +
                    ")"
                )
                if len(vals) >= chunk:
                    ex("INSERT INTO iceberg.oidw.raw_antt_receita_praca VALUES " + ",".join(vals))
                    vals = []
            if vals:
                ex("INSERT INTO iceberg.oidw.raw_antt_receita_praca VALUES " + ",".join(vals))

            vals = []
            for r in vol:
                vals.append(
                    "(" +
                    f"'{_esc(URL_VOL_2026_MENSAL)}', CAST(CURRENT_TIMESTAMP AS timestamp(6))," +
                    f"'{_esc(r.get('concessionaria'))}'," +
                    f"'{_esc(r.get('mes_ano'))}'," +
                    f"'{_esc(r.get('sentido'))}'," +
                    f"'{_esc(r.get('praca'))}'," +
                    f"'{_esc(r.get('tipo_cobranca'))}'," +
                    f"'{_esc(r.get('categoria_eixo'))}'," +
                    f"'{_esc(r.get('tipo_de_veiculo'))}'," +
                    ("NULL" if _fnum(r.get('volume_total')) is None else str(_fnum(r.get('volume_total')))) +
                    ")"
                )
                if len(vals) >= chunk:
                    ex("INSERT INTO iceberg.oidw.raw_antt_volume_trafego_praca VALUES " + ",".join(vals))
                    vals = []
            if vals:
                ex("INSERT INTO iceberg.oidw.raw_antt_volume_trafego_praca VALUES " + ",".join(vals))

            vals = []
            for r in vol_eq:
                vals.append(
                    "(" +
                    f"'{_esc(URL_VOL_EQ_2026_MENSAL)}', CAST(CURRENT_TIMESTAMP AS timestamp(6))," +
                    f"'{_esc(r.get('concessionaria'))}'," +
                    f"'{_esc(r.get('mes_ano'))}'," +
                    f"'{_esc(r.get('sentido'))}'," +
                    f"'{_esc(r.get('praca'))}'," +
                    f"'{_esc(r.get('tipo_de_cobranca'))}'," +
                    f"'{_esc(r.get('categoria_eixo'))}'," +
                    f"'{_esc(r.get('tipo_de_veiculo'))}'," +
                    ("NULL" if _fnum(r.get('volume_total')) is None else str(_fnum(r.get('volume_total')))) + "," +
                    ("NULL" if _fnum(r.get('multiplicador_de_tarifa')) is None else str(_fnum(r.get('multiplicador_de_tarifa')))) + "," +
                    ("NULL" if _fnum(r.get('volume_veiculo_equivalente')) is None else str(_fnum(r.get('volume_veiculo_equivalente')))) +
                    ")"
                )
                if len(vals) >= chunk:
                    ex("INSERT INTO iceberg.oidw.raw_antt_volume_trafego_equiv_praca VALUES " + ",".join(vals))
                    vals = []
            if vals:
                ex("INSERT INTO iceberg.oidw.raw_antt_volume_trafego_equiv_praca VALUES " + ",".join(vals))

            return {
                "receita_rows": len(receita),
                "volume_rows": len(vol),
                "volume_equiv_rows": len(vol_eq),
            }

    ingest()


raw_antt_toll_economics_pipeline()
