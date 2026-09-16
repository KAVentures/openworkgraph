@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py -3
) else (
  where python >nul 2>nul
  if not %errorlevel%==0 (
    echo Python 3 is not installed. Install Python 3.11 or newer from python.org and select "Add Python to PATH", then run this file again.
    pause
    exit /b 1
  )
  set PY=python
)
if not exist .venv (
  echo First-time setup: installing Workflow Observer locally...
  %PY% -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
)
echo Checking OpenWorkGraph dependencies...
.venv\Scripts\python.exe -m pip install -e . --disable-pip-version-check -q
if not %errorlevel%==0 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
.venv\Scripts\python.exe start.py --mode observe
pause
