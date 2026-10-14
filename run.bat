@echo off
REM Arranque en un solo paso (Windows): entorno, dependencias, modelos, datos y servidor.
cd /d "%~dp0"

if not exist .venv (
  echo Creando entorno virtual...
  py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt

if not exist .env (
  copy .env.example .env >nul
  echo Cree .env desde .env.example: completa ANTHROPIC_API_KEY y FISH_API_KEY.
)

python scripts\download_models.py
if not exist data\wecare.db python scripts\seed_demo.py

echo Totem:  http://127.0.0.1:8000/
echo Panel:  http://127.0.0.1:8000/admin
echo Kiosco: chrome --kiosk --use-fake-ui-for-media-stream http://127.0.0.1:8000/
uvicorn wecare.main:app --host 127.0.0.1 --port 8000
pause
