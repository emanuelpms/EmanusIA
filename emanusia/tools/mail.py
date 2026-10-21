"""E-mail. Padrão: Outlook instalado no Windows (usa a conta já logada, sem senha no robô).
Alternativa configurável em Ajustes: SMTP/IMAP (host/porta/usuário nos Ajustes; senha SEMPRE na
variável de ambiente EMAIL_SMTP_PASS / EMAIL_IMAP_PASS)."""
import email
import imaplib
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

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


class Filter:
    """Filtros de leitura: assunto/remetente contém, últimos N dias, tipo de anexo."""
    def __init__(self, subject_contains="", from_contains="", since_days=0, attachment_types="",
                 attachments_folder="emails", save_attachments=False):
        self.subject = (subject_contains or "").lower()
        self.sender = (from_contains or "").lower()
        self.since = time.time() - float(since_days) * 86400 if since_days else 0
        self.exts = tuple("." + e.strip().lower().lstrip(".") for e in str(attachment_types or "").replace(";", ",").split(",") if e.strip())
        self.folder = attachments_folder or "emails"
        self.save = bool(save_attachments or self.exts)

    def header_ok(self, subject, sender) -> bool:
        return (not self.subject or self.subject in (subject or "").lower()) and \
               (not self.sender or self.sender in (sender or "").lower())

    def wants(self, filename: str) -> bool:
        return self.save and (not self.exts or filename.lower().endswith(self.exts))

    def store(self, filename: str) -> tuple:
        name = re.sub(r"[^\w.\-]", "_", filename)
        p = config.safe_path(f"{self.folder}/{name}")
        n = 1
        while p.exists():  # não sobrescreve anexos com o mesmo nome
            n += 1
            p = p.with_name(f"{Path(name).stem}_{n}{Path(name).suffix}")
        return p, config.display_path(p)


def outlook_read(folder, unread_only, limit, flt: Filter):
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
        try:
            received = it.ReceivedTime.timestamp()
        except Exception:
            received = time.time()
        if flt.since and received < flt.since:
            break  # ordenado do mais novo para o mais antigo
        if not flt.header_ok(it.Subject, f"{it.SenderName} {it.SenderEmailAddress}"):
            continue
        saved = []
        for att in it.Attachments:
            if flt.wants(att.FileName):
                p, rel = flt.store(att.FileName)
                att.SaveAsFile(str(p))
                saved.append(rel)
        if flt.exts and not saved:
            continue  # pediu e-mails com anexo de um tipo e este não tem
        out.append({"from": it.SenderEmailAddress, "from_name": it.SenderName, "subject": it.Subject,
                    "date": str(it.ReceivedTime)[:19], "text": (it.Body or "")[:3000], "attachments": saved})
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


def imap_read(folder, unread_only, limit, flt: Filter):
    out = []
    with imaplib.IMAP4_SSL(_setting("imap_host", "EMAIL_IMAP_HOST")) as im:
        im.login(_setting("imap_user", "EMAIL_IMAP_USER", config.settings().get("smtp_user")),
                 _secret("EMAIL_IMAP_PASS"))
        im.select(folder or "INBOX", readonly=True)
        crit = ["UNSEEN" if unread_only else "ALL"]
        if flt.since:
            crit += ["SINCE", time.strftime("%d-%b-%Y", time.gmtime(flt.since))]
        _, ids = im.search(None, *crit)
        for i in reversed(ids[0].split()):
            if len(out) >= limit:
                break
            msg = email.message_from_bytes(im.fetch(i, "(RFC822)")[1][0][1])
            subject, sender = _decode(msg["Subject"]), _decode(msg["From"])
            if not flt.header_ok(subject, sender):
                continue
            text, saved = "", []
            for part in msg.walk():
                fname = _decode(part.get_filename()) if part.get_filename() else ""
                if part.get_content_type() == "text/plain" and not fname:
                    text += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                elif fname and flt.wants(fname):
                    p, rel = flt.store(fname)
                    p.write_bytes(part.get_payload(decode=True))
                    saved.append(rel)
            if flt.exts and not saved:
                continue
            out.append({"from": sender, "subject": subject, "date": msg["Date"], "text": text[:3000],
                        "attachments": saved})
    return out


def _decode(v) -> str:
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return str(v or "")


def _attachments(v) -> list[str]:
    from .files import expand_files
    items = _list(v) if isinstance(v, str) else list(v or [])
    for it in items:
        if not any(c in str(it) for c in "*?[") and not config.safe_path(str(it), mkdir=False).is_file():
            raise FileNotFoundError(f"anexo não encontrado: {it}")
    return [str(p) for p in expand_files(items)] if items else []


# ---------------- ferramentas ----------------
@tool("email_send", "Envia e-mail (Outlook por padrão; SMTP se configurado). to/cc: e-mails separados "
      "por ';'. attachments = arquivos (lista ou padrão como 'saida/*.pdf').",
      {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
       "cc": {"type": "string"}, "attachments": {}, "html": {"type": "boolean"},
       "provider": {"type": "string", "enum": ["outlook", "smtp"]}},
      ("to", "subject", "body"), risky=True)
def email_send(to, subject: str, body: str, cc=None, attachments: list | None = None,
               html: bool = False, provider: str | None = None):
    if not _list(to):
        raise ValueError("destinatário vazio")
    prov = get_provider(provider)
    attachments = _attachments(attachments)
    (outlook_send if prov == "outlook" else smtp_send)(to, subject, body, attachments, cc, html)
    return {"sent_to": _list(to), "provider": prov}


@tool("email_read", "Lê e-mails (Outlook por padrão; IMAP se configurado). Filtros: assunto contém, remetente "
      "contém, últimos N dias e tipo de anexo (ex.: 'pdf,xml' — salva só esses anexos e só traz e-mails que os "
      "tenham). Os anexos vão para `attachments_folder` (padrão 'emails').",
      {"folder": {"type": "string"}, "unread_only": {"type": "boolean"}, "limit": {"type": "integer"},
       "save_attachments": {"type": "boolean"}, "subject_contains": {"type": "string"},
       "from_contains": {"type": "string"}, "since_days": {"type": "number"},
       "attachment_types": {"type": "string"}, "attachments_folder": {"type": "string"},
       "provider": {"type": "string", "enum": ["outlook", "smtp"]}})
def email_read(folder: str = "INBOX", unread_only: bool = True, limit: int = 10,
               save_attachments: bool = False, subject_contains: str = "", from_contains: str = "",
               since_days: float = 0, attachment_types: str = "", attachments_folder: str = "emails",
               provider: str | None = None):
    prov = get_provider(provider)
    flt = Filter(subject_contains, from_contains, since_days, attachment_types, attachments_folder, save_attachments)
    config.safe_path(f"{flt.folder}/x")  # pasta liberada? (falha antes de abrir a caixa)
    fn = outlook_read if prov == "outlook" else imap_read
    return fn(folder, unread_only, min(int(limit), 500), flt)
