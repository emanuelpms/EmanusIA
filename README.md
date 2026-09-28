# 🤖 EmanusIA — funcionário-robô local

Agente de IA que roda **100% no seu PC** (LLM via [Ollama](https://ollama.com)) e opera
sistemas como um funcionário: navega em sites, gera Excel, datasets Power BI, dashboards e opera o SAP.

## Instalação
**Windows (recomendado — SAP e RPA de desktop só funcionam nele):** instale Python 3.10+ e o [Ollama](https://ollama.com),
depois dê dois cliques em `instalar.bat` (uma vez) e `abrir.bat` (sempre). Manual:
```bash
python -m venv .venv && .venv\Scripts\activate      # Linux/Mac: source .venv/bin/activate
pip install -e ".[all,dev]"        # extras: sap, desktop, gui (janela nativa), sql
playwright install chromium
ollama pull qwen2.5:7b             # cérebro + ferramentas (cabe nos 6 GB da RTX 4050)
ollama pull qwen2.5vl:3b           # visão (lê a tela)
```
> Hardware de referência: Acer Nitro V15, 16 GB RAM, RTX 4050 (6 GB VRAM). Modelos de 7B em 4-bit rodam na GPU;
> 14B+ ficariam lentos (transbordam para a RAM). Contexto limitado a 8k (`EMANUSIA_NUM_CTX`). O Ollama troca
> entre o modelo de texto e o de visão sozinho.

## Uso
```bash
emanusia gui                        # interface gráfica (janela nativa; senão abre no navegador)
emanusia chat | run "tarefa"        # terminal
emanusia record https://site nome   # grava uma automação web (vira workflows/nome.yaml)
emanusia workflow nome -p k=v       # roda automação salva, SEM LLM
emanusia user add maria --role operator   # cria usuário (ativa login na GUI)
emanusia tools                      # lista ferramentas (⚠️ = pede autorização)
```

## GUI
Chat com o robô · Automações (gravar, editar YAML, executar com parâmetros) · Agendamentos · Tarefas com
passo a passo · Arquivos gerados (dashboards abrem em sandbox) · Usuários/Auditoria · Ajustes (troca de modelos).
Ações de risco abrem um pop-up **Autorizar / Negar** e a tarefa espera sua resposta.

## Como funciona
```
Usuário ─► Agent (loop LLM) ─► Ferramentas ─► web · excel · dashboard · powerbi · sap · arquivos
                  ▲                              │
                  └──────── observa resultado ◄──┘        workflows YAML = mesmas ferramentas, sem LLM
```
- **Automações de usuários**: o robô resolve a tarefa com o LLM e, se pedirem, salva com `save_workflow`
  em `workflows/*.yaml` (editável à mão, com `{{parâmetros}}`). Depois roda sem IA, agendável por cron/Agendador do Windows.
- **Segurança**: arquivos ficam presos ao `workspace/`; ações que alteram sistemas (clicar, gravar no SAP,
  push Power BI) exigem confirmação; senhas nunca passam pelo LLM (`web_fill_secret` lê variável de ambiente);
  API escuta só em 127.0.0.1.
- **SAP**: via SAP GUI Scripting (Windows; habilite `sapgui/user_scripting` e use o gravador Alt+F12 p/ achar IDs).
- **Power BI**: `.pbix` não é gerável por código; o robô exporta CSV + script Power Query (M) em `workspace/powerbi/`
  e/ou faz push a datasets do Service (`POWERBI_TOKEN`).

## Variáveis
`EMANUSIA_MODEL`, `EMANUSIA_OLLAMA_URL`, `EMANUSIA_WORKSPACE`, `EMANUSIA_WORKFLOWS`, `EMANUSIA_HEADLESS=1`, `EMANUSIA_MAX_STEPS`.

## Recursos
| Recurso | Como |
|---|---|
| Gravador web | injeta JS no navegador; cliques/campos viram passos; senha → `web_fill_secret` (nunca gravada) |
| Visão | `vision_ask` / `vision_locate` (print → modelo multimodal). Experimental: modelos pequenos erram coordenadas |
| RPA desktop | `desktop_*` via pywinauto (UI Automation, por nome de controle) + pyautogui (coordenadas). Windows |
| Agendador | intervalo ou horário/dias; persiste em `data/schedules.json`; execução "sem supervisão" só por admin |
| Multiusuário | papéis admin/operator/viewer, whitelist de ferramentas/workflows por usuário, login por cookie, auditoria em `data/audit.jsonl` (segredos mascarados) |
| Conectores | e-mail SMTP/IMAP, Outlook, REST (`http_get`/`http_send`), SQL (`sql_query` só-leitura; SQLite ou ODBC via variável de ambiente) |

## Roadmap
Mais conectores (ERPs específicos, Teams/WhatsApp) · condicionais/loops nos workflows · empacotar em `.exe` (PyInstaller) ·
recuperação automática de falhas em workflows (o LLM conserta seletores quebrados).

Testes: `pytest`
