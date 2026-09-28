"""Conectores: e-mail (SMTP/IMAP e Outlook), APIs REST de ERPs/CRMs e bancos SQL.
Credenciais SEMPRE em variáveis de ambiente (nunca no prompt nem no YAML)."""
import email
import imaplib
import os
import re
import smtplib
import sqlite3
import sys
from email.message import EmailMessage

import httpx

from .. import config
from .registry import tool


def _env(name: str) -> str:
    if name not in os.environ:
        raise KeyError(f"Defina a variável de ambiente {name}")
    return os.environ[name]


@tool("email_send", "Envia e-mail via SMTP (EMAIL_SMTP_HOST/PORT/USER/PASS). Anexos = arquivos do workspace.",
      {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
       "attachments": {"type": "array"}}, ("to", "subject", "body"), risky=True)
def email_send(to: str, subject: str, body: str, attachments: list | None = None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = _env("EMAIL_SMTP_USER"), to, subject
    m.set_content(body)
    for a in attachments or []:
        p = config.safe_path(a)
        m.add_attachment(p.read_bytes(), maintype="application", subtype="octet-stream", filename=p.name)
    with smtplib.SMTP_SSL(_env("EMAIL_SMTP_HOST"), int(os.environ.get("EMAIL_SMTP_PORT", "465"))) as s:
        s.login(_env("EMAIL_SMTP_USER"), _env("EMAIL_SMTP_PASS"))
        s.send_message(m)
    return {"sent_to": to}


@tool("email_read", "Lê e-mails por IMAP (EMAIL_IMAP_HOST/USER/PASS). Retorna assunto, remetente e trecho.",
      {"folder": {"type": "string"}, "search": {"type": "string", "description": "critério IMAP, ex.: UNSEEN"},
       "limit": {"type": "integer"}, "save_attachments": {"type": "boolean"}})
def email_read(folder: str = "INBOX", search: str = "UNSEEN", limit: int = 10,
               save_attachments: bool = False):
    out = []
    with imaplib.IMAP4_SSL(_env("EMAIL_IMAP_HOST")) as im:
        im.login(_env("EMAIL_IMAP_USER"), _env("EMAIL_IMAP_PASS"))
        im.select(folder, readonly=True)
        _, ids = im.search(None, search)
        for i in ids[0].split()[-limit:]:
            msg = email.message_from_bytes(im.fetch(i, "(RFC822)")[1][0][1])
            text, saved = "", []
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    text += part.get_payload(decode=True).decode(errors="replace")
                elif save_attachments and part.get_filename():
                    name = re.sub(r"[^\w.\-]", "_", part.get_filename())
                    config.safe_path(f"emails/{name}").write_bytes(part.get_payload(decode=True))
                    saved.append(f"emails/{name}")
            out.append({"from": msg["From"], "subject": msg["Subject"], "date": msg["Date"],
                        "text": text[:1000], "attachments": saved})
    return out


@tool("outlook_send", "Envia e-mail pelo Outlook instalado (Windows).",
      {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
       "attachments": {"type": "array"}}, ("to", "subject", "body"), risky=True)
def outlook_send(to: str, subject: str, body: str, attachments: list | None = None):
    if sys.platform != "win32":
        raise RuntimeError("Outlook só no Windows.")
    import win32com.client
    mail = win32com.client.Dispatch("Outlook.Application").CreateItem(0)
    mail.To, mail.Subject, mail.Body = to, subject, body
    for a in attachments or []:
        mail.Attachments.Add(str(config.safe_path(a)))
    mail.Send()
    return {"sent_to": to}


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
