@echo off
setlocal
chcp 65001 >nul
if not defined APP_PORT set "APP_PORT=8769"
if not defined APP_PYTHON set "APP_PYTHON=python"
echo Starting Video Translator...
echo Open http://127.0.0.1:%APP_PORT% in your browser.
echo.
"%APP_PYTHON%" -c "import importlib.util,sys; missing=[name for name in ('uvicorn','faster_whisper') if importlib.util.find_spec(name) is None]; print('Missing Python packages: ' + ', '.join(missing)) if missing else None; sys.exit(1 if missing else 0)"
if errorlevel 1 (
    echo.
    echo Video Translator could not start with: %APP_PYTHON%
    echo Activate the Conda environment where requirements.txt was installed,
    echo or set APP_PYTHON to that environment's python.exe.
    echo Example: set "APP_PYTHON=C:\path\to\env\python.exe"
    echo.
    pause
    exit /b 1
)
"%APP_PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port %APP_PORT% --no-proxy-headers
set "APP_EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %APP_EXIT_CODE%
