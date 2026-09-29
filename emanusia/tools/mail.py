"""E-mail. Padrão: Outlook instalado no Windows (usa a conta já logada, sem senha no robô).
Alternativa configurável em Ajustes: SMTP/IMAP (host/porta/usuário nos Ajustes; senha SEMPRE na
variável de ambiente EMAIL_SMTP_PASS / EMAIL_IMAP_PASS)."""
import email
import imaplib
import os
import re
import smtplib
import sys
from email.message import EmailMessage

from .. import config
from .registry import tool


def get_provider(override: str | None = None) -> str:
    p = (override or config.settings().get("email_provider") or "outlook").lower()
    if p not in ("outlook", "smtp"):
        raise ValueError("provedor de e-mail deve ser 'outlook' ou 'smtp'")
    return p


def _setting(key: str, env: str, default=None):
    v = config.settings().get(key) or os.environ.get(env) or default
    if v in (None, ""):
        raise KeyError(f"Configure '{key}' em Ajustes (ou a variável {env})")
    return v


def _secret(env: str) -> str:
    if env not in os.environ:
        raise KeyError(f"Defina a variável de ambiente {env} (senha não fica salva no sistema)")
    return os.environ[env]


def _com():
    if sys.platform != "win32":
        raise RuntimeError("Outlook só funciona no Windows. Em Ajustes, troque o e-mail para SMTP.")
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()  # obrigatório em threads que não são a principal
    return win32com.client.Dispatch("Outlook.Application")


def _list(v) -> list:
    if not v:
        return []
    return [str(x).strip() for x in (v.split(";") if isinstance(v, str) else v) if x and str(x).strip()]


# ---------------- Outlook ----------------
def outlook_send(to, subject, body, attachments=None, cc=None, html=False):
    mail = _com().CreateItem(0)
    mail.To, mail.Subject = "; ".join(_list(to)), subject
    if cc:
        mail.CC = "; ".join(_list(cc))
    if html:
        mail.HTMLBody = body
    else:
        mail.Body = body
    for a in attachments or []:
        mail.Attachments.Add(str(config.safe_path(a)))
    mail.Send()


def outlook_read(folder, unread_only, limit, save_attachments, subject_contains):
    ns = _com().GetNamespace("MAPI")
    box = ns.GetDefaultFolder(6)  # Caixa de Entrada
    if folder and folder.upper() != "INBOX":
        box = box.Folders[folder]
    items = box.Items
    items.Sort("[ReceivedTime]", True)
    if unread_only:
        items = items.Restrict("[UnRead] = True")
    out = []
    for it in items:
        if len(out) >= limit:
            break
        if subject_contains and subject_contains.lower() not in (it.Subject or "").lower():
            continue
        saved = []
        if save_attachments:
            for att in it.Attachments:
                name = re.sub(r"[^\w.\-]", "_", att.FileName)
                att.SaveAsFile(str(config.safe_path(f"emails/{name}")))
                saved.append(f"emails/{name}")
        out.append({"from": it.SenderEmailAddress, "subject": it.Subject,
                    "date": str(it.ReceivedTime), "text": (it.Body or "")[:1000], "attachments": saved})
    return out


# ---------------- SMTP / IMAP ----------------
def smtp_send(to, subject, body, attachments=None, cc=None, html=False):
    host = _setting("smtp_host", "EMAIL_SMTP_HOST")
    port = int(_setting("smtp_port", "EMAIL_SMTP_PORT", 587))
    user = _setting("smtp_user", "EMAIL_SMTP_USER")
    m = EmailMessage()
    m["From"] = config.settings().get("smtp_from") or user
    m["To"], m["Subject"] = ", ".join(_list(to)), subject
    if cc:
        m["Cc"] = ", ".join(_list(cc))
    m.set_content(body, subtype="html" if html else "plain")
    for a in attachments or []:
        p = config.safe_path(a)
        m.add_attachment(p.read_bytes(), maintype="application", subtype="octet-stream", filename=p.name)
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=60)
    else:
        s = smtplib.SMTP(host, port, timeout=60)
        s.starttls()
    with s:
        s.login(user, _secret("EMAIL_SMTP_PASS"))
        s.send_message(m)


def imap_read(folder, unread_only, limit, save_attachments, subject_contains):
    out = []
    with imaplib.IMAP4_SSL(_setting("imap_host", "EMAIL_IMAP_HOST")) as im:
        im.login(_setting("imap_user", "EMAIL_IMAP_USER", config.settings().get("smtp_user")),
                 _secret("EMAIL_IMAP_PASS"))
        im.select(folder or "INBOX", readonly=True)
        _, ids = im.search(None, "UNSEEN" if unread_only else "ALL")
        for i in reversed(ids[0].split()):
            if len(out) >= limit:
                break
            msg = email.message_from_bytes(im.fetch(i, "(RFC822)")[1][0][1])
            if subject_contains and subject_contains.lower() not in (msg["Subject"] or "").lower():
                continue
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


# ---------------- ferramentas ----------------
@tool("email_send", "Envia e-mail (Outlook por padrão; SMTP se configurado). to/cc: e-mails separados "
      "por ';'. attachments = arquivos do workspace.",
      {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
       "cc": {"type": "string"}, "attachments": {"type": "array"}, "html": {"type": "boolean"},
       "provider": {"type": "string", "enum": ["outlook", "smtp"]}},
      ("to", "subject", "body"), risky=True)
def email_send(to, subject: str, body: str, cc=None, attachments: list | None = None,
               html: bool = False, provider: str | None = None):
    if not _list(to):
        raise ValueError("destinatário vazio")
    prov = get_provider(provider)
    (outlook_send if prov == "outlook" else smtp_send)(to, subject, body, attachments, cc, html)
    return {"sent_to": _list(to), "provider": prov}


@tool("email_read", "Lê e-mails da caixa de entrada (Outlook por padrão; IMAP se configurado).",
      {"folder": {"type": "string"}, "unread_only": {"type": "boolean"}, "limit": {"type": "integer"},
       "save_attachments": {"type": "boolean"}, "subject_contains": {"type": "string"},
       "provider": {"type": "string", "enum": ["outlook", "smtp"]}})
def email_read(folder: str = "INBOX", unread_only: bool = True, limit: int = 10,
               save_attachments: bool = False, subject_contains: str = "", provider: str | None = None):
    prov = get_provider(provider)
    fn = outlook_read if prov == "outlook" else imap_read
    return fn(folder, unread_only, min(int(limit), 200), save_attachments, subject_contains)
