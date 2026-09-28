# 🤖 EmanusIA — funcionário-robô local

Agente de IA que roda **100% no seu PC** (LLM via [Ollama](https://ollama.com)) e opera
sistemas como um funcionário: navega em sites, gera Excel, datasets Power BI, dashboards e opera o SAP.

## Instalação (Windows/Linux/Mac)
```bash
python -m venv .venv && .venv\Scripts\activate        # Linux/Mac: source .venv/bin/activate
pip install -e ".[dev]"            # no Windows p/ SAP: pip install -e ".[sap,dev]"
playwright install chromium
ollama pull qwen2.5:14b            # bom em tool-calling; PC fraco: qwen2.5:7b (EMANUSIA_MODEL)
```

## Uso
```bash
emanusia chat                       # conversa no terminal
emanusia run "Abra https://... extraia a tabela e gere um dashboard"
emanusia serve                      # UI web em http://127.0.0.1:8765
emanusia workflow exemplo-relatorio # roda automação salva, SEM LLM (rápido e determinístico)
emanusia tools                      # lista ferramentas (⚠️ = pede autorização)
```

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

## Roadmap
Gravador de ações web → workflow · visão (screenshot → LLM multimodal) para apps sem API · RPA desktop
(pywinauto) para outros softwares · agendador embutido · multiusuário com permissões · mais conectores (Outlook, ERPs).

Testes: `pytest`
