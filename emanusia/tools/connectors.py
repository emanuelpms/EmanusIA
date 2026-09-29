"""Conectores: APIs REST de ERPs/CRMs e bancos SQL (e-mail fica em mail.py).
Credenciais SEMPRE em variáveis de ambiente (nunca no prompt nem no YAML)."""
import os
import re
import sqlite3

import httpx

from .. import config
from .registry import tool


def _env(name: str) -> str:
    if name not in os.environ:
        raise KeyError(f"Defina a variável de ambiente {name}")
    return os.environ[name]


@tool("http_get", "GET em API REST (JSON). auth_env = nome da variável com o Bearer token.",
      {"url": {"type": "string"}, "params": {"type": "object"}, "auth_env": {"type": "string"}},
      ("url",))
def http_get(url: str, params: dict | None = None, auth_env: str = ""):
    h = {"Authorization": f"Bearer {_env(auth_env)}"} if auth_env else {}
    r = httpx.get(url, params=params, headers=h, timeout=60)
    r.raise_for_status()
    return r.json() if "json" in r.headers.get("content-type", "") else r.text[:8000]


@tool("http_send", "POST/PUT/PATCH/DELETE em API REST (altera dados no sistema remoto).",
      {"method": {"type": "string"}, "url": {"type": "string"}, "json": {"type": "object"},
       "auth_env": {"type": "string"}}, ("method", "url"), risky=True)
def http_send(method: str, url: str, json: dict | None = None, auth_env: str = ""):
    if method.upper() not in ("POST", "PUT", "PATCH", "DELETE"):
        raise ValueError("método inválido")
    h = {"Authorization": f"Bearer {_env(auth_env)}"} if auth_env else {}
    r = httpx.request(method.upper(), url, json=json, headers=h, timeout=60)
    r.raise_for_status()
    return {"status": r.status_code, "body": r.text[:2000]}


def _connect(database: str):
    """`database` = arquivo .db do workspace, ou nome de variável de ambiente com string ODBC."""
    if database in os.environ:
        import pyodbc  # pip install pyodbc (SQL Server, Oracle, Postgres... via ODBC)
        return pyodbc.connect(os.environ[database])
    return sqlite3.connect(config.safe_path(database))


@tool("sql_query", "Consulta SQL somente-leitura (SELECT/WITH). Retorna [cabeçalho, linhas...].",
      {"database": {"type": "string"}, "sql": {"type": "string"}, "max_rows": {"type": "integer"}},
      ("database", "sql"))
def sql_query(database: str, sql: str, max_rows: int = 1000):
    if not re.match(r"\s*(select|with)\b", sql, re.I) or ";" in sql.strip().rstrip(";"):
        raise PermissionError("sql_query aceita apenas uma instrução SELECT/WITH")
    con = _connect(database)
    try:
        cur = con.cursor()
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        return [cols] + [list(r) for r in cur.fetchmany(max_rows)]
    finally:
        con.close()


@tool("sql_execute", "Executa INSERT/UPDATE/DELETE/DDL (altera o banco).",
      {"database": {"type": "string"}, "sql": {"type": "string"}}, ("database", "sql"), risky=True)
def sql_execute(database: str, sql: str):
    con = _connect(database)
    try:
        cur = con.cursor()
        cur.execute(sql)
        con.commit()
        return {"rows_affected": cur.rowcount}
    finally:
        con.close()
