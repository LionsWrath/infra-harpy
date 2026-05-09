"""IBGE UFs + Regiões raw ingestion"""
from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime, now
import requests, json
from pathlib import Path

TRINO_CONN_ID='trino_default'
LANDING_BASE=Path('/data/lake/landing/ibge_ufs_regioes')
URL_UF='https://servicodados.ibge.gov.br/api/v1/localidades/estados'
URL_REG='https://servicodados.ibge.gov.br/api/v1/localidades/regioes'

@dag(dag_id='ibge_ufs_regioes_raw_pipeline', start_date=datetime(2026,4,1), schedule='0 2 * * 1', catchup=False, tags=['ibge','raw'])
def ibge_ufs_regioes_raw_pipeline():
    @task
    def fetch_to_landing():
        rd=now('UTC').to_date_string()
        d=LANDING_BASE / f'run_date={rd}' / 'raw'
        d.mkdir(parents=True, exist_ok=True)
        u=requests.get(URL_UF, timeout=60, headers={'User-Agent':'Mozilla/5.0'}); u.raise_for_status()
        r=requests.get(URL_REG, timeout=60, headers={'User-Agent':'Mozilla/5.0'}); r.raise_for_status()
        (d/'ufs.json').write_text(u.text, encoding='utf-8')
        (d/'regioes.json').write_text(r.text, encoding='utf-8')
        return {'run_date':rd,'dir':str(d)}

    @task
    def ensure_tables(conn_id:str=TRINO_CONN_ID):
        h=TrinoHook(trino_conn_id=conn_id)
        with h.get_conn() as c:
            cur=c.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_ibge_ufs(
              uf_id VARCHAR, uf_sigla VARCHAR, uf_nome VARCHAR,
              regiao_id VARCHAR, regiao_sigla VARCHAR, regiao_nome VARCHAR,
              source_url VARCHAR, source_run_date DATE, datestr VARCHAR, ingested_at TIMESTAMP(6)
            ) WITH (format='PARQUET', partitioning=ARRAY['datestr'])
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS iceberg.oidw.raw_ibge_regioes(
              regiao_id VARCHAR, regiao_sigla VARCHAR, regiao_nome VARCHAR,
              source_url VARCHAR, source_run_date DATE, datestr VARCHAR, ingested_at TIMESTAMP(6)
            ) WITH (format='PARQUET', partitioning=ARRAY['datestr'])
            """)

    @task
    def load_promote(meta:dict, conn_id:str=TRINO_CONN_ID):
        rd=meta['run_date']
        p=Path(meta['dir'])
        ufs=json.loads((p/'ufs.json').read_text(encoding='utf-8'))
        regs=json.loads((p/'regioes.json').read_text(encoding='utf-8'))
        h=TrinoHook(trino_conn_id=conn_id)
        def q(v):
            if v is None: return 'NULL'
            return "'"+str(v).replace("'","''")+"'"
        with h.get_conn() as c:
            cur=c.cursor()
            cur.execute(f"DELETE FROM iceberg.oidw.raw_ibge_ufs WHERE datestr='{rd}'")
            cur.execute(f"DELETE FROM iceberg.oidw.raw_ibge_regioes WHERE datestr='{rd}'")
            vals=[]
            for x in ufs:
                rg=x.get('regiao') or {}
                vals.append("("+", ".join([
                    q(x.get('id')), q(x.get('sigla')), q(x.get('nome')),
                    q(rg.get('id')), q(rg.get('sigla')), q(rg.get('nome')),
                    q(URL_UF), f"DATE '{rd}'", q(rd), 'CURRENT_TIMESTAMP'])+")")
            for i in range(0,len(vals),300):
                cur.execute("INSERT INTO iceberg.oidw.raw_ibge_ufs (uf_id,uf_sigla,uf_nome,regiao_id,regiao_sigla,regiao_nome,source_url,source_run_date,datestr,ingested_at) VALUES\n"+",\n".join(vals[i:i+300]))
            vals=[]
            for x in regs:
                vals.append("("+", ".join([
                    q(x.get('id')), q(x.get('sigla')), q(x.get('nome')),
                    q(URL_REG), f"DATE '{rd}'", q(rd), 'CURRENT_TIMESTAMP'])+")")
            cur.execute("INSERT INTO iceberg.oidw.raw_ibge_regioes (regiao_id,regiao_sigla,regiao_nome,source_url,source_run_date,datestr,ingested_at) VALUES\n"+",\n".join(vals))

    ensure_tables(); m=fetch_to_landing(); load_promote(m)

ibge_ufs_regioes_raw_pipeline()
