# 🤖 EmanusIA — sua empresa de funcionários-robô

Plataforma local (roda no seu PC, sem nuvem) onde a equipe cria **bots** que operam sistemas como
funcionários: navegam em portais, fazem login, analisam planilhas, montam dashboards, enviam e-mails
pelo Outlook e operam o SAP e outros programas. Tudo aparece num **escritório em pixel art** que
cresce conforme a empresa contrata mais bots.

![fluxo](https://img.shields.io/badge/LLM-Ollama%20local-blue) ![testes](https://img.shields.io/badge/testes-pytest-green)

## Instalação (Windows — Acer Nitro V15, 16 GB, RTX 4050)
1. Instale **Python 3.10+** e o **[Ollama](https://ollama.com)**.
2. Dê dois cliques em `instalar.bat` (uma vez). Ele instala tudo e baixa os modelos
   `qwen2.5:7b` (raciocínio, cabe nos 6 GB da RTX 4050) e `qwen2.5vl:3b` (visão).
3. `abrir.bat` abre o sistema. Para ver uma empresa de exemplo: `emanusia demo`.

Manual: `pip install -e ".[all,dev]"`, `playwright install chromium`, `ollama pull qwen2.5:7b`.

## O que ele faz
| Área | Como |
|---|---|
| **🏢 Escritório** | Painel ao vivo: bots sentam em mesas por time, chefes em salas, caminham para entregar trabalho ao chefe ou passar dados a outro bot, vão ao café, à reunião e à sala de TI. Reunião a partir de 3 bots, servidores a partir de 6 (LEDs piscam com a carga). Dia/noite pelo relógio. Painel lateral com o status de cada bot; clique para ver detalhes e executar. Abra em `/office` numa TV. |
| **🤖 Bots** | Descreva em português → a IA escreve o código → o **validador** aponta erros → a IA corrige sozinha → você revisa, valida e ativa. Qualquer edição volta o bot a rascunho; ele é revalidado a cada execução. Chefes delegam com `call_bot`. |
| **🔑 Acessos** | Cada usuário cadastra o **próprio** login de cada sistema (site, SAP, programa). O bot usa só o nome (`web_login: portal-x`) e, ao rodar, pega o acesso de quem executa. Senhas criptografadas (chave protegida pelo DPAPI do Windows), nunca vão para a IA, logs ou tela. Admin pode criar acessos compartilhados (conta de serviço). |
| **📊 Excel e dados** | `data_profile` (entender a planilha), `data_query` (filtros, agrupar, somar), `data_pivot` (tabela dinâmica com totais), `data_clean` (R$ 1.234,56 → número, datas, duplicados), `data_merge` (PROCV / conciliação), `data_combine` (consolidar várias planilhas), `data_extract` (tabelas de PDF/HTML), `data_compute` (colunas calculadas). |
| **📈 Dashboards** | `excel_dashboard`: dashboard **nativo do Excel** com cartões de KPI e gráficos por fórmulas (SOMASE/CONT.SE) que se atualizam quando os dados mudam. `dashboard_data`: HTML interativo com filtros, período, busca, tabela ordenável e exportação CSV. `excel_write`: relatórios com moeda/%, totais, formatação condicional e vários gráficos. `excel_update` edita planilhas existentes; `excel_refresh` atualiza Power Query/tabelas dinâmicas e roda macros (Windows). |
| **🌐 Sites complexos** | Login com sessão salva (MFA uma vez), iframes, várias abas/pop-ups, esperas, extração de listas, paginação com `while`, upload/download, alertas. Gravador: você usa o site e ele vira automação. |
| **✉️ E-mail** | Outlook instalado por padrão (conta já logada). SMTP/IMAP configurável em Ajustes (senha só em variável de ambiente). |
| **💡 Aprendizado** | Toda tarefa do chat que dá certo vira experiência: tarefas parecidas ganham dicas e, quando a mesma rotina se repete 3×, o sistema sugere um bot pronto (valores que mudam viram parâmetros). Opcional: criar esses bots automaticamente como rascunho (nunca ativa sozinho). |
| **Outros** | SAP GUI Scripting, RPA de desktop (pywinauto), visão por screenshot, APIs REST, SQL, Power BI, agendador, multiusuário com papéis e auditoria. |

> 📐 Arquitetura completa, tecnologias, o que foi testado e o passo a passo de instalação: **[docs/ARQUITETURA.md](docs/ARQUITETURA.md)**

## Modo simples: qualquer pessoa ensina um robô
1. **Ensinar mostrando** (aba Bots): dá um nome, diz o site e clica em *Começar a ensinar*. Uma janela
   abre, a pessoa faz a tarefa normalmente e clica em *Terminei*. Cada clique vira um passo com o nome
   que aparece na tela (“Clicar em *Buscar notas*”). Senhas não são gravadas: se escolher um acesso,
   o robô usa o login cadastrado de quem rodar.
2. **Passos em português**: a página do robô mostra o que ele faz como uma lista legível, com
   editar/subir/descer/apagar por formulário. O código fica escondido em *Modo avançado*.
3. **Ensaio**: o robô faz a tarefa de verdade e tira um print de cada passo. Se o site mudou, o passo
   fica vermelho com a explicação (“Não encontrei *Buscar notas* na tela…”) e o botão
   **👉 Mostrar onde fica**: o robô refaz os passos anteriores e o primeiro clique da pessoa ensina o
   lugar novo. Robôs que mexem em telas (site, SAP, programas) só trabalham depois de um ensaio que deu certo.
4. **Revisão do padrinho**: robô criado por quem não é padrinho/admin só começa a trabalhar depois que
   um padrinho aprova (ou pede ajustes com um recado). Qualquer mudança depois exige nova revisão.
   Marque o usuário como padrinho em Admin (ou `emanusia user add maria --reviewer`).
6. **Adicionar passos** a um robô pronto: *mostrando* (o robô refaz até o ponto escolhido e grava o que
   a pessoa fizer dali em diante) ou por **ações prontas** (enviar e-mail, esperar, abrir site, salvar
   tabela da página em planilha, montar painel). Funciona também dentro de quadros internos (iframes).
7. **Ensinar no SAP**: grave a tarefa com o gravador do próprio SAP (Alt+F12) e envie o `.vbs`; o
   sistema converte em passos em português (senhas nunca são importadas).
8. **Linguagem comum** em tudo: erros (“Você ainda não cadastrou o seu acesso *erp*”), pedidos de
   autorização (“O robô vai ENVIAR um e-mail para gerente@… com 1 anexo. Pode enviar?”) e
   formulário com as informações que mudam a cada execução.

## Linguagem dos bots (YAML validado)
```yaml
params: {url: "https://portal.fornecedor.com", destino: "financeiro@empresa.com"}
steps:
  - tool: web_login            # usa o acesso "portal" de quem está rodando
    args: {credential: portal}
  - set: {pedidos: [], pagina: 1, tem_proxima: true}
  - while: "tem_proxima and pagina <= 20"
    steps:
      - tool: web_extract
        args: {item_selector: .pedido, fields: {numero: .num, valor: .valor}}
        save_as: lote
      - set: {pedidos: "=pedidos + lote", pagina: "=pagina + 1"}
      - tool: web_exists
        args: {selector: "button.proxima:not([disabled])"}
        save_as: tem_proxima
      - if: "tem_proxima"
        then: [{tool: web_click, args: {selector: button.proxima}}]
  - tool: excel_dashboard
    args: {path: pedidos.xlsx, source: "{{pedidos}}", title: Pedidos,
           kpis: [{label: Total, column: valor, agg: sum, format: money}]}
    retry: 2
  - call_bot: analista-financeiro   # passa o trabalho para outro bot (aparece no escritório)
  - tool: email_send                # Outlook; pede autorização ao rodar
    args: {to: "{{destino}}", subject: Pedidos, body: Segue., attachments: [pedidos.xlsx]}
```
Passos: `tool`, `if/then/else`, `for_each`, `while`, `try/catch`, `set` (`=` avalia expressão),
`call` (workflow), `call_bot`, `log`, `sleep`, `fail`; em ferramentas: `retry`, `on_error: continue`,
`if`. Expressões são seguras (sem código Python). O validador recusa ferramenta ou argumento
inexistente, senha escrita no código, expressões inválidas e chamadas para bots/workflows que não
existem. Exemplo completo: `workflows/exemplo-portal-completo.yaml`.

## Segurança
- Ações que alteram sistemas (enviar e-mail, clicar em "excluir/pagar/aprovar", gravar no SAP, SQL de
  escrita, macros) pedem **Autorizar/Negar**; agendamentos sem supervisão só por admin.
- Papéis admin/operator/viewer, permissões por ferramenta, auditoria com segredos mascarados.
- Só `127.0.0.1` por padrão. Para a equipe acessar pela rede: `emanusia serve --lan` (exige login;
  sem HTTPS, use apenas em rede interna).
- Arquivos presos à pasta `workspace/`.

## Comandos
```
emanusia gui | serve [--lan] | demo | chat | run "tarefa"
emanusia bot list|validate|run|activate|pause <id>
emanusia record <url> <nome> | workflow <nome> | user add <nome> --role admin | tools
```
Uma tarefa por vez é executada (navegador e SAP não suportam paralelismo); as demais ficam na fila.

Testes: `pytest`
