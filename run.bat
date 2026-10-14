@echo off
REM Arranque en un solo paso (Windows): entorno, dependencias, modelos, datos y servidor.
setlocal
cd /d "%~dp0"

REM ---- 1. Buscar Python 3.10+ -------------------------------------------------------------
set "PY="
set "PYARGS="
REM Preferimos 3.12 / 3.11 / 3.10: las librerias (onnxruntime, opencv) publican paquetes para esas versiones.
for %%V in (3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V --version >nul 2>nul
    if not errorlevel 1 (
      set "PY=py"
      set "PYARGS=-%%V"
    )
  )
)
if not defined PY (
  python --version >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  py -3 --version >nul 2>nul
  if not errorlevel 1 (
    set "PY=py"
    set "PYARGS=-3"
  )
)
REM Instalaciones por usuario (winget --scope user / instalador python.org) que aun no estan en el PATH
if not defined PY (
  for %%V in (313 312 311 310) do (
    if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
  )
)
if not defined PY (
  for %%V in (313 312 311 310) do (
    if not defined PY if exist "%ProgramFiles%\Python%%V\python.exe" set "PY=%ProgramFiles%\Python%%V\python.exe"
  )
)
if defined PY echo Usando Python: %PY% %PYARGS%
if not defined PY (
  echo.
  echo [ERROR] No encontre Python en esta computadora.
  echo   1. Instalalo desde https://www.python.org/downloads/windows/  ^(Python 3.12, 64 bits^)
  echo      y marca la casilla "Add python.exe to PATH" en el instalador.
  echo      Alternativa en una consola:  winget install Python.Python.3.12
  echo   2. Cerra esta ventana, abri una nueva y volve a ejecutar run.bat
  echo.
  pause
  exit /b 1
)
"%PY%" %PYARGS% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Se necesita Python 3.10 o superior. Version encontrada:
  "%PY%" %PYARGS% --version
  pause
  exit /b 1
)
"%PY%" %PYARGS% -c "import sys; sys.exit(0 if sys.version_info < (3, 14) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Esta version de Python es demasiado nueva para onnxruntime/opencv:
  "%PY%" %PYARGS% --version
  echo   Instala Python 3.12:  winget install --source winget --id Python.Python.3.12 --scope user
  pause
  exit /b 1
)

REM ---- 2. Entorno virtual -----------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Creando entorno virtual...
  "%PY%" %PYARGS% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] No pude crear el entorno virtual.
    pause
    exit /b 1
  )
)
set "VPY=.venv\Scripts\python.exe"

echo Instalando dependencias ^(la primera vez tarda unos minutos^)...
"%VPY%" -m pip install -q --upgrade pip
"%VPY%" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Fallo la instalacion de dependencias. Revisa la conexion a internet y volve a intentar.
  pause
  exit /b 1
)

REM ---- 3. Configuracion, modelos y datos --------------------------------------------------
if not exist ".env" (
  copy .env.example .env >nul
  echo Cree .env desde .env.example: completa ANTHROPIC_API_KEY y FISH_API_KEY y volve a ejecutar run.bat
)
"%VPY%" scripts\download_models.py
if errorlevel 1 (
  echo [AVISO] No pude descargar los modelos faciales; el totem arranca sin reconocimiento facial ^(PIN y QR siguen funcionando^).
)
if not exist "data\wecare.db" "%VPY%" scripts\seed_demo.py

REM ---- 4. Servidor ------------------------------------------------------------------------
echo.
echo Totem:  http://127.0.0.1:8000/
echo Panel:  http://127.0.0.1:8000/admin
echo Kiosco: chrome --kiosk --use-fake-ui-for-media-stream http://127.0.0.1:8000/
echo ^(Ctrl+C para detener^)
echo.
"%VPY%" -m uvicorn wecare.main:app --host 127.0.0.1 --port 8000
pause
