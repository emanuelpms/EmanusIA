@echo off
REM EmanusIA - instalacao no Windows (rode uma vez). Recomendado: Python 3.11 ou 3.12.
setlocal
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (where python >nul 2>nul && set PY=python)
if "%PY%"=="" goto :sempython
%PY% -c "import sys; assert sys.version_info >= (3,10)" 2>nul || goto :sempython

echo [1/5] Criando o ambiente...
if not exist .venv %PY% -m venv .venv || goto :erro
call .venv\Scripts\activate.bat || goto :erro
python -m pip install --upgrade pip >nul

echo [2/5] Instalando o EmanusIA...
pip install -e ".[dev]" || goto :erro

echo [3/5] Integracoes do Windows (SAP, Outlook, programas, bancos)...
pip install -e ".[sap,desktop,sql]" || echo   AVISO: alguma integracao do Windows nao instalou. O resto funciona.
pip install pywebview >nul 2>nul || echo   AVISO: janela nativa indisponivel - o sistema abrira no navegador.

echo [4/5] Navegador dos robos...
python -m playwright install chromium || goto :erro

echo [5/5] Modelos de IA...
where ollama >nul 2>nul || goto :semollama
ollama pull qwen2.5:7b || echo   AVISO: nao consegui baixar qwen2.5:7b. Abra o Ollama e rode este script de novo.
ollama pull qwen2.5vl:3b || echo   AVISO: modelo de visao nao baixado (opcional).

echo.
echo Pronto! Use abrir.bat para comecar.
pause
exit /b 0

:sempython
echo Nao encontrei o Python 3.10+. Instale o Python 3.11 em https://www.python.org/downloads/
echo e marque a opcao "Add Python to PATH" durante a instalacao.
pause
exit /b 1
:semollama
echo O sistema foi instalado, mas falta o Ollama (a IA local).
echo Instale em https://ollama.com e rode este script de novo para baixar os modelos.
pause
exit /b 1
:erro
echo.
echo A instalacao falhou na etapa acima. Tire um print desta janela para o suporte.
pause
exit /b 1
