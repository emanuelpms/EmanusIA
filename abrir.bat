@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo O EmanusIA ainda nao foi instalado. Rode instalar.bat primeiro.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
where ollama >nul 2>nul || echo AVISO: Ollama nao encontrado - o chat e a IA nao vao funcionar ate instala-lo.
emanusia gui
if errorlevel 1 (
  echo.
  echo O EmanusIA fechou com erro. Tire um print desta janela para o suporte.
  pause
)
