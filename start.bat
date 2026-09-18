@echo off
chcp 65001 >nul
if not defined APP_PORT set "APP_PORT=8769"
echo Starting Video Translator...
echo Open http://127.0.0.1:%APP_PORT% in your browser.
echo.
REM Adjust the path below if your Python/uvicorn is in a different location
uvicorn app.main:app --host 127.0.0.1 --port %APP_PORT% --no-proxy-headers
pause
