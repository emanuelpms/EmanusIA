@echo off
REM Instalação no Windows (Acer Nitro V15 / RTX 4050 6GB): rode uma vez.
python -m venv .venv || goto :erro
call .venv\Scripts\activate
pip install -e ".[all,dev]" || goto :erro
playwright install chromium
where ollama >nul 2>nul || (echo Instale o Ollama em https://ollama.com e rode este script de novo. & pause & exit /b 1)
ollama pull qwen2.5:7b
ollama pull qwen2.5vl:3b
echo Pronto! Use abrir.bat
pause
exit /b 0
:erro
echo Falhou. Verifique se o Python 3.10+ esta instalado e no PATH.
pause
