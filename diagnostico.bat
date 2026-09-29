@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo O EmanusIA ainda nao foi instalado. Rode instalar.bat primeiro.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
emanusia doctor
echo Tire um print desta janela se precisar de ajuda.
pause
