@echo off
REM Enciende Mi Jarvis: arranca el servidor y abre el navegador.
cd /d "%~dp0"
echo Encendiendo tu Jarvis en http://127.0.0.1:4700 ...
start "" python server.py
timeout /t 2 >nul
start "" http://127.0.0.1:4700
echo.
echo Si el navegador no abrio solo, entra a: http://127.0.0.1:4700
echo Para apagarlo, cierra la ventana de Python (o pulsa Ctrl+C en ella).
