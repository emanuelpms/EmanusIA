"""Linguagem comum: transforma passos, erros e pedidos de autorização em frases que qualquer pessoa
entende. Nada aqui executa nada — é só tradução para a tela."""
import re

# ---------------------------------------------------------------- nomes amigáveis
FIELD = {"url": "endereço do site", "selector": "onde clicar/escrever", "value": "o que escrever",
         "path": "nome do arquivo", "to": "para quem", "subject": "assunto", "body": "mensagem",
         "cc": "com cópia para", "attachments": "anexos", "credential": "acesso", "text": "texto",
         "key": "tecla", "tcode": "transação", "field_id": "campo do SAP", "source": "planilha de origem",
         "sheet": "aba", "title": "título", "output": "salvar resultado em", "name": "nome",
         "window": "janela", "control": "botão/campo", "command": "programa", "database": "banco de dados",
         "sql": "consulta", "pattern": "arquivos", "timeout_s": "esperar até (segundos)", "env_var": "variável da senha",
         "index": "número", "question": "pergunta", "description": "descrição", "item_selector": "itens da lista",
         "fields": "informações de cada item", "new_tab": "abre em outra aba", "session": "sessão salva",
         "paths": "arquivos", "macro": "macro", "rows": "linhas", "sheets": "abas", "kpis": "indicadores",
         "charts": "gráficos", "filters": "filtros", "where": "filtros", "group_by": "agrupar por", "agg": "totais"}


def field_label(name: str) -> str:
    return FIELD.get(name, name.replace("_", " ").capitalize())


def param_label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def target(args: dict, step: dict | None = None) -> str:
    """Nome humano de um elemento da tela a partir do rótulo gravado ou do seletor."""
    if step and step.get("label"):
        return str(step["label"])
    sel = str(args.get("selector") or args.get("control") or "")
    m = re.match(r"(?:text|role)=(.+)", sel)
    if m:
        return m.group(1).strip("'\" ")
    m = re.search(r"\[(?:name|aria-label|placeholder|data-testid)=[\"']?([^\"'\]]+)", sel)
    if m:
        return m.group(1).replace("_", " ").replace("-", " ")
    m = re.match(r"#([\w\-]+)", sel)
    if m:
        return m.group(1).replace("_", " ").replace("-", " ")
    return sel or "elemento"


def _short(v, n=60) -> str:
    s = str(v)
    return s if len(s) <= n else s[:n - 1] + "…"


def _file(p) -> str:
    return str(p).split("/")[-1]


TOOL_TEXT = {
    "web_open": lambda a, s: f"Abrir o site {_short(a.get('url', ''))}",
    "web_click": lambda a, s: f"Clicar em “{target(a, s)}”",
    "web_fill": lambda a, s: f"Escrever “{_short(a.get('value', ''), 40)}” em “{target(a, s)}”",
    "web_type": lambda a, s: f"Digitar “{_short(a.get('text', ''), 40)}” em “{target(a, s)}”",
    "web_fill_secret": lambda a, s: f"Preencher a senha em “{target(a, s)}”",
    "web_fill_credential": lambda a, s: f"Preencher {'a senha' if a.get('field', 'password') == 'password' else 'o usuário'} do acesso “{a.get('credential')}”",
    "web_login": lambda a, s: f"Entrar no sistema com o acesso “{a.get('credential')}”",
    "web_select": lambda a, s: f"Escolher “{a.get('value')}” em “{target(a, s)}”",
    "web_check": lambda a, s: f"{'Marcar' if a.get('checked', True) else 'Desmarcar'} “{target(a, s)}”",
    "web_press": lambda a, s: f"Apertar a tecla {a.get('key')}",
    "web_wait": lambda a, s: "Esperar a página carregar" if not a.get("selector") else f"Esperar aparecer “{target(a, s)}”",
    "web_table": lambda a, s: "Copiar a tabela da página",
    "web_extract": lambda a, s: "Copiar a lista de itens da página",
    "web_text": lambda a, s: "Ler o texto da página",
    "web_download": lambda a, s: f"Baixar o arquivo {_file(a.get('path', ''))}",
    "web_upload": lambda a, s: "Enviar arquivo(s) para o site",
    "web_screenshot": lambda a, s: "Tirar um print da página",
    "web_exists": lambda a, s: f"Verificar se aparece “{target(a, s)}”",
    "web_switch_tab": lambda a, s: "Mudar de aba do navegador",
    "web_frame": lambda a, s: "Entrar no quadro interno da página" if a.get("selector") else "Voltar para a página principal",
    "web_close": lambda a, s: "Fechar o navegador",
    "excel_write": lambda a, s: f"Criar a planilha {_file(a.get('path', ''))}",
    "excel_dashboard": lambda a, s: f"Montar o dashboard no Excel {_file(a.get('path', ''))}",
    "excel_update": lambda a, s: f"Atualizar a planilha {_file(a.get('path', ''))}",
    "excel_read": lambda a, s: f"Ler a planilha {_file(a.get('path', ''))}",
    "excel_refresh": lambda a, s: f"Atualizar os dados da planilha {_file(a.get('path', ''))}" + (f" e rodar a macro {a['macro']}" if a.get("macro") else ""),
    "dashboard_build": lambda a, s: f"Montar o painel {_file(a.get('path', ''))}",
    "dashboard_data": lambda a, s: f"Montar o painel interativo {_file(a.get('path', ''))}",
    "data_profile": lambda a, s: "Entender a planilha",
    "data_query": lambda a, s: "Filtrar e somar os dados",
    "data_pivot": lambda a, s: "Fazer a tabela dinâmica",
    "data_clean": lambda a, s: "Arrumar os dados (tirar duplicados e espaços)",
    "data_merge": lambda a, s: "Cruzar duas tabelas (PROCV)",
    "data_combine": lambda a, s: "Juntar várias planilhas em uma",
    "data_extract": lambda a, s: f"Tirar as tabelas do arquivo {_file(a.get('path', ''))}",
    "data_compute": lambda a, s: "Calcular novas colunas",
    "powerbi_export": lambda a, s: "Preparar os dados para o Power BI",
    "email_send": lambda a, s: f"Enviar e-mail para {a.get('to')} — “{_short(a.get('subject', ''), 40)}”",
    "email_read": lambda a, s: "Ler os e-mails da caixa de entrada",
    "sap_login": lambda a, s: f"Entrar no SAP com o acesso “{a.get('credential')}”",
    "sap_run_transaction": lambda a, s: f"Abrir a transação {a.get('tcode')} no SAP",
    "sap_set_field": lambda a, s: (f"Preencher “{s['label']}” no SAP com “{_short(a.get('value', ''), 30)}”"
                                   if s and s.get("label") else f"Preencher um campo do SAP com “{_short(a.get('value', ''), 30)}”"),
    "sap_press": lambda a, s: (f"Clicar em “{s['label']}” no SAP" if a.get("button_id") and s and s.get("label")
                               else "Confirmar (Enter) no SAP" if a.get("vkey") in (None, 0)
                               else {8: "Executar (F8) no SAP", 11: "Gravar (Ctrl+S) no SAP", 3: "Voltar (F3) no SAP"}.get(a.get("vkey"), f"Apertar a tecla {a.get('vkey')} no SAP")),
    "sap_select": lambda a, s: (f"{'Marcar' if a.get('value') else 'Desmarcar'} “{(s or {}).get('label', 'opção')}” no SAP" if isinstance(a.get("value"), bool)
                                else f"Escolher “{a.get('value')}” em “{(s or {}).get('label', 'lista')}” no SAP" if a.get("value") not in (None, "")
                                else f"Abrir “{(s or {}).get('label', 'aba')}” no SAP"),
    "sap_call": lambda a, s: {"pressToolbarButton": f"Clicar no botão “{(a.get('args') or [''])[0]}” do relatório SAP",
                              "setCurrentCell": "Selecionar uma célula do relatório SAP",
                              "doubleClickCurrentCell": "Abrir (duplo clique) a linha do relatório SAP",
                              "selectContextMenuItem": f"Escolher “{(a.get('args') or [''])[0]}” no menu do SAP",
                              "contextMenu": "Abrir o menu do relatório SAP"}.get(a.get("method"), f"Ação “{a.get('method')}” no SAP"),
    "sap_read_grid": lambda a, s: "Copiar o relatório do SAP",
    "desktop_start_app": lambda a, s: f"Abrir o programa {_file(a.get('command', ''))}",
    "desktop_click": lambda a, s: f"Clicar em “{a.get('control')}” no programa",
    "desktop_type": lambda a, s: f"Digitar no programa “{_short(a.get('text', ''), 30)}”",
    "file_write": lambda a, s: f"Salvar o arquivo {_file(a.get('path', ''))}",
    "sql_query": lambda a, s: "Consultar o banco de dados",
    "sql_execute": lambda a, s: "Alterar o banco de dados",
    "http_get": lambda a, s: "Buscar dados de um sistema (API)",
    "http_send": lambda a, s: "Enviar dados para um sistema (API)",
    "vision_ask": lambda a, s: "Olhar a tela e responder",
}

ICON = {"web": "🌐", "excel": "📗", "dashboard": "📊", "data": "🧮", "mail": "✉️", "sap": "🟦",
        "desktop": "🖥", "access": "🔑", "powerbi": "📈", "files": "📄", "connectors": "🔌", "vision": "👁"}


def _friendly(text: str) -> str:
    """{{filial}} -> «Filial» (a informação que muda a cada vez)."""
    rx = r"\{\{\s*([\w.]+)\s*\}\}"
    lab = lambda m: "«" + param_label(m.group(1).split(".")[0]) + "»"
    text = re.sub("“" + rx + "”", lab, text)  # “{{filial}}” -> «Filial» (sem aspas duplicadas)
    return re.sub(rx, lab, text)


def describe_step(s: dict) -> str:
    return _friendly(_describe(s))


def _describe(s: dict) -> str:
    if "tool" in s:
        from .tools import registry
        a = s.get("args") or {}
        fn = TOOL_TEXT.get(s["tool"])
        try:
            text = fn(a, s) if fn else registry.get(s["tool"]).description.split(".")[0]
        except KeyError:
            text = f"(ação desconhecida: {s['tool']})"
        if s.get("retry"):
            text += f" — tenta {int(s['retry']) + 1} vezes se falhar"
        return text
    if "if" in s:
        return f"Se {_cond(s['if'])}:"
    if "for_each" in s:
        return f"Para cada item de {_cond(s['for_each'])}:"
    if "while" in s:
        return f"Repetir enquanto {_cond(s['while'])}:"
    if "try" in s:
        return "Tentar fazer:"
    if "set" in s:
        return "Guardar: " + ", ".join(param_label(k) for k in s["set"])
    if "call_bot" in s:
        return f"Passar o trabalho para o bot “{s['call_bot']}”"
    if "call" in s:
        return f"Executar a automação “{s['call']}”"
    if "log" in s:
        return f"Anotar: {_short(s['log'])}"
    if "sleep" in s:
        return f"Esperar {s['sleep']} segundos"
    if "fail" in s:
        return f"Parar com o aviso: {_short(s['fail'])}"
    return "Passo"


def _cond(e) -> str:
    t = str(e)
    for a, b in ((" and ", " e "), (" or ", " ou "), ("not ", "não "), ("len(", "quantidade de ("),
                 ("==", "="), ("!=", "≠"), (">=", "≥"), ("<=", "≤"), ("True", "sempre"), ("False", "nunca")):
        t = t.replace(a, b)
    return t.replace("_", " ")


def step_icon(s: dict) -> str:
    if "tool" not in s:
        return "↪"
    from .tools import registry
    try:
        return ICON.get(registry.get(s["tool"]).group, "⚙️")
    except KeyError:
        return "❓"


def describe_code(code: dict) -> list[dict]:
    """Lista plana para a tela: [{path, depth, text, icon, tool, args, label, editable}]."""
    out = []

    def walk(steps, prefix, depth):
        for i, s in enumerate(steps or [], 1):
            path = f"{prefix}{i}"
            out.append({"path": path, "depth": depth, "text": describe_step(s), "icon": step_icon(s),
                        "tool": s.get("tool"), "args": s.get("args") or {}, "label": s.get("label"),
                        "fixable": s.get("tool") in ("web_click", "web_fill", "web_type", "web_select",
                                                      "web_check", "web_wait", "web_exists", "web_hover")})
            for key, title in (("then", None), ("else", "Senão:"), ("steps", None), ("try", None),
                               ("catch", "Se der erro:")):
                if s.get(key):
                    if title:
                        out.append({"path": f"{path}.{key}", "depth": depth + 1, "text": title, "icon": "↪",
                                    "tool": None, "args": {}, "label": None, "fixable": False, "header": True})
                    walk(s[key], f"{path}.{key}." if key in ("else", "catch") else f"{path}.",
                         depth + 1 + (1 if title else 0))
    walk(code.get("steps"), "", 0)
    return out


# ---------------------------------------------------------------- autorizações
def describe_action(tool: str, args: dict) -> str:
    a = args or {}
    if tool == "email_send":
        n = len(a.get("attachments") or [])
        return (f"O robô vai ENVIAR um e-mail para {a.get('to')} com o assunto “{_short(a.get('subject', ''), 50)}”"
                + (f" e {n} anexo(s)" if n else "") + ". Pode enviar?")
    if tool == "web_click":
        return f"O robô vai clicar em “{target(a)}”. Esse botão pode enviar, apagar ou aprovar algo no sistema. Pode clicar?"
    if tool == "sap_set_field":
        return f"O robô vai alterar um campo no SAP para “{_short(a.get('value', ''), 40)}”. Pode alterar?"
    if tool == "sap_press":
        return "O robô vai confirmar/gravar uma tela no SAP. Pode continuar?"
    if tool == "sap_call":
        return f"O robô vai fazer uma ação no relatório do SAP ({describe_step({'tool': tool, 'args': a}).lower()}). Pode continuar?"
    if tool == "sql_execute":
        return "O robô vai ALTERAR dados no banco de dados. Pode continuar?"
    if tool == "http_send":
        return f"O robô vai enviar dados para outro sistema ({a.get('method', '')} {_short(a.get('url', ''), 50)}). Pode enviar?"
    if tool in ("run_bot", "run_workflow"):
        return f"O robô vai executar “{a.get('bot_id') or a.get('name')}”. Pode executar?"
    if tool == "excel_refresh":
        return f"O robô vai rodar a macro “{a.get('macro')}” na planilha {_file(a.get('path', ''))}. Pode rodar?"
    if tool in ("desktop_start_app", "desktop_click", "desktop_type", "desktop_hotkey", "desktop_click_xy"):
        return f"O robô vai mexer em um programa do computador ({describe_step({'tool': tool, 'args': a}).lower()}). Pode continuar?"
    if tool == "web_eval":
        return "O robô vai rodar um comando avançado dentro do site. Pode continuar?"
    return describe_step({"tool": tool, "args": a}) + ". Pode continuar?"


# ---------------------------------------------------------------- erros
def _step_at(code: dict | None, path: str | None) -> dict | None:
    if not code or not path:
        return None
    from .engine import get_step
    try:
        return get_step(code, path)
    except (KeyError, ValueError, IndexError, TypeError):
        return None


def explain_error(err: str, step: dict | None = None) -> str:
    e = str(err or "")
    what = f"“{target(step.get('args') or {}, step)}”" if step and "tool" in step else "o que precisava"
    rules = [
        (r"Timeout.*(locator|waiting for|wait_for)|strict mode|not visible|element is not",
         f"Não encontrei {what} na tela. O site pode ter mudado ou demorado para carregar. "
         f"Use “Mostrar onde fica” para ensinar de novo."),
        (r"Acesso '([^']+)' não mapeado", r"Você ainda não cadastrou o seu acesso “\1”. Vá em 🔑 Acessos e cadastre seu usuário e senha."),
        (r"net::ERR_NAME_NOT_RESOLVED|net::ERR_INTERNET|ERR_CONNECTION|getaddrinfo|Name or service",
         "Não consegui abrir o site: verifique a internet ou o endereço."),
        (r"net::ERR_|Timeout.*goto|Navigation", "O site não abriu a tempo. Tente de novo em instantes."),
        (r"não autorizad|não autorizada", "A ação não foi autorizada, então o robô parou antes de fazê-la."),
        (r"Outlook só funciona no Windows", "O e-mail pelo Outlook só funciona no computador Windows com o Outlook aberto."),
        (r"só (está disponível|funciona) no Windows|só no Windows", "Essa ação só funciona no computador Windows da empresa."),
        (r"No such file|FileNotFound|não existe\b.*arquivo|Nenhuma planilha", "Não achei o arquivo. Confira o nome e se ele está na pasta do robô."),
        (r"Coluna '([^']+)' não existe", r"A planilha não tem a coluna “\1”. Confira o nome da coluna."),
        (r"Variável de ambiente (\w+) não definida|Defina a variável de ambiente (\w+)",
         "Falta configurar uma senha neste computador. Peça ao responsável pelo sistema."),
        (r"Variável '([^']+)' (não definida|não encontrada)", r"O robô tentou usar “\1”, mas essa informação não existe ainda."),
        (r"sem permissão|PermissionError", "Você não tem permissão para essa ação. Fale com o administrador."),
        (r"Nenhum pedido encontrado|^WorkflowError: (.+)$", None),
    ]
    for rx, msg in rules:
        m = re.search(rx, e, re.I | re.M)
        if m:
            if msg is None:
                return re.sub(r"^\w+Error:\s*", "", e).strip()
            return m.expand(msg) if "\\" in msg else msg
    return "Algo deu errado neste passo: " + re.sub(r"^\w+(Error|Exception):\s*", "", e).split("\n")[0][:200]


def explain_validation(rep: dict, code: dict | None = None) -> list[str]:
    """Erros do validador em português comum, citando o passo como a pessoa o vê."""
    out = []
    for e in rep.get("errors", []):
        m = re.match(r"steps\[(\d+)\]((?:\.\w+\[\d+\])*)(?:\.\w+)?:\s*(.*)", e)
        where, msg = "", e
        if m:
            nums = [m.group(1)] + re.findall(r"\[(\d+)\]", m.group(2) or "")
            where = "Passo " + " → ".join(nums) + ": "
            msg = m.group(3)
        rules = [
            (r"ferramenta '([^']+)' não existe", r"o robô não sabe fazer “\1”."),
            (r"'([^']+)' não aceita o argumento '([^']+)'", lambda mm: f"a informação “{field_label(mm.group(2))}” não serve para esta ação."),
            (r"'([^']+)' exige o argumento '([^']+)'", lambda mm: f"falta informar {field_label(mm.group(2))}."),
            (r"segredo escrito no código|campo de senha preenchido", "tem uma senha escrita no robô. Use um acesso cadastrado em 🔑 Acessos."),
            (r"sem permissão para '([^']+)'", "você não tem permissão para esta ação."),
            (r"bot '([^']+)' não existe", r"o bot “\1” não existe."),
            (r"workflow '([^']+)' não existe", r"a automação “\1” não existe."),
            (r"Expressão", "a condição está escrita de um jeito que o robô não entende."),
            (r"o bot não tem passos", "o robô ainda não tem nenhum passo."),
            (r"chama a si mesmo", "o robô está chamando ele mesmo (faria um loop infinito)."),
        ]
        text = None
        for rx, rp in rules:
            mm = re.search(rx, msg)
            if mm:
                text = rp(mm) if callable(rp) else mm.expand(rp) if "\\" in rp else rp
                break
        out.append(where + (text or msg))
    return out
