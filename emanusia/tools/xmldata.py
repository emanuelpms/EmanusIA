"""XML: ler qualquer XML como tabela (escolhendo o item que se repete e os campos) e ler NF-e
(nota fiscal eletrônica) já com os campos que o financeiro/fiscal usa."""
from lxml import etree

from .. import config
from .files import expand_files
from .registry import tool

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=True)


def _root(path) -> etree._Element:
    p = config.safe_path(str(path), mkdir=False)
    root = etree.parse(str(p), _PARSER).getroot()
    if root is None:
        raise ValueError(f"{p.name} não é um XML válido")
    for el in root.iter():  # tira os namespaces: <nfe:emit> vira <emit>
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    etree.cleanup_namespaces(root)
    return root


def _find(el, path: str):
    """'emit/CNPJ' ou '@Id' ou 'emit/enderEmit/xMun' (a partir do elemento; '//' busca em qualquer nível)."""
    path = str(path).strip()
    if not path:
        return None
    if path.startswith("@"):
        return el.get(path[1:])
    attr = None
    if "/@" in path:
        path, attr = path.rsplit("/@", 1)
    q = "." + path if path.startswith("/") else path if path.startswith(".") else "./" + path
    hit = el.find(q)
    if hit is None:
        return None
    if attr:
        return hit.get(attr)
    return (hit.text or "").strip()


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return v


def _to_dict(el):
    kids = list(el)
    if not kids:
        return (el.text or "").strip()
    d = {}
    for k in kids:
        if not isinstance(k.tag, str):
            continue
        v = _to_dict(k)
        if k.tag in d:
            d[k.tag] = d[k.tag] if isinstance(d[k.tag], list) else [d[k.tag]]
            d[k.tag].append(v)
        else:
            d[k.tag] = v
    return d


@tool("xml_read", "Lê arquivo(s) XML. Sem `items`, devolve o conteúdo como objeto. Com `items` (a tag que se "
      "repete, ex.: 'det' ou 'pedido') e `fields` ({coluna: 'caminho/da/tag'}), devolve uma tabela — uma "
      "linha por item. `files` aceita padrão ('xmls/*.xml'); `output` salva em Excel/CSV.",
      {"files": {}, "items": {"type": "string"}, "fields": {"type": "object"}, "output": {"type": "string"}},
      ("files",))
def xml_read(files, items: str = "", fields: dict | None = None, output: str = ""):
    paths = expand_files(files, (".xml",))
    if not paths:
        raise FileNotFoundError(f"nenhum XML em {files}")
    if not items:
        docs = [_to_dict(_root(p)) for p in paths]
        return docs[0] if len(docs) == 1 and not isinstance(files, list) and "*" not in str(files) else docs
    table = []
    for p in paths:
        root = _root(p)
        q = items if items.startswith((".", "/")) else ".//" + items
        q = "." + q if q.startswith("/") else q
        for it in root.iterfind(q):
            if fields:
                row = {c: _find(it, path) for c, path in fields.items()}
            else:
                row = {k.tag: (k.text or "").strip() for k in it if isinstance(k.tag, str) and not len(k)}
            row["arquivo"] = p.name
            table.append(row)
    return _table(table, output)


ID_LIKE = ("cnpj", "cpf", "chave", "ncm", "cfop", "cep", "codigo", "numero", "serie", "item", "id")


def _table(records: list[dict], output: str):
    import pandas as pd
    from .data import result
    df = pd.DataFrame(records)
    for c in df.columns:  # números do XML (1234.56) viram número; códigos (CNPJ, NCM...) ficam texto
        if any(x in str(c).lower() for x in ID_LIKE):
            continue
        col = df[c].replace("", None)
        conv = pd.to_numeric(col, errors="coerce")
        if col.notna().any() and conv.notna().sum() == col.notna().sum():
            df[c] = conv
    return result(df, output or None)


NFE_FIELDS = {
    "chave": "@Id", "numero": "ide/nNF", "serie": "ide/serie", "emissao": "ide/dhEmi", "natureza": "ide/natOp",
    "emitente_cnpj": "emit/CNPJ", "emitente": "emit/xNome", "emitente_uf": "emit/enderEmit/UF",
    "destinatario_cnpj": "dest/CNPJ", "destinatario_cpf": "dest/CPF", "destinatario": "dest/xNome",
    "valor_produtos": "total/ICMSTot/vProd", "valor_frete": "total/ICMSTot/vFrete", "valor_desconto": "total/ICMSTot/vDesc",
    "valor_icms": "total/ICMSTot/vICMS", "valor_ipi": "total/ICMSTot/vIPI", "valor_pis": "total/ICMSTot/vPIS",
    "valor_cofins": "total/ICMSTot/vCOFINS", "valor_total": "total/ICMSTot/vNF",
}
ITEM_FIELDS = {"item": "@nItem", "codigo": "prod/cProd", "descricao": "prod/xProd", "ncm": "prod/NCM",
               "cfop": "prod/CFOP", "unidade": "prod/uCom", "quantidade": "prod/qCom",
               "valor_unitario": "prod/vUnCom", "valor_item": "prod/vProd"}
NUMERIC = {"valor_produtos", "valor_frete", "valor_desconto", "valor_icms", "valor_ipi", "valor_pis",
           "valor_cofins", "valor_total", "quantidade", "valor_unitario", "valor_item"}


@tool("nfe_read", "Lê XML de NF-e (nota fiscal eletrônica): uma linha por nota com chave, número, emissão, "
      "emitente, destinatário e valores (total, ICMS, IPI...). items=true traz uma linha por produto. "
      "`files` aceita padrão ('notas/*.xml'); `output` salva em Excel/CSV.",
      {"files": {}, "items": {"type": "boolean"}, "output": {"type": "string"}}, ("files",))
def nfe_read(files, items: bool = False, output: str = ""):
    paths = expand_files(files, (".xml",))
    if not paths:
        raise FileNotFoundError(f"nenhum XML em {files}")
    rows, skipped = [], []
    for p in paths:
        root = _root(p)
        inf = root if root.tag == "infNFe" else next(root.iter("infNFe"), None)
        if inf is None:
            skipped.append(p.name)
            continue
        head = {k: _find(inf, v) for k, v in NFE_FIELDS.items()}
        head["chave"] = (head["chave"] or "").removeprefix("NFe")
        head["emissao"] = (head["emissao"] or _find(inf, "ide/dEmi") or "")[:10]
        head["arquivo"] = p.name
        if items:
            for det in inf.iter("det"):
                it = {k: _find(det, v) for k, v in ITEM_FIELDS.items()}
                rows.append({**{k: head[k] for k in ("chave", "numero", "emissao", "emitente", "destinatario")}, **it,
                             "arquivo": p.name})
        else:
            rows.append(head)
    for r in rows:
        for k in NUMERIC & set(r):
            r[k] = _num(r[k])
    import pandas as pd
    from .data import result
    res = result(pd.DataFrame(rows), output or None)
    res["notes"] = len(paths) - len(skipped)
    if skipped:
        res["not_nfe"] = skipped
    return res
