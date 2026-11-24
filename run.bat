@echo off
echo ===================================================
echo Starting ResQNet Disaster Response System
echo ===================================================
echo Backend:  http://127.0.0.1:8001
echo Frontend: http://localhost:8080
echo ===================================================

start "ResQNet Backend (Port 8001)" cmd /k "set DJANGO_DEBUG=1 && cd /d "%~dp0backend" && .venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001"
start "ResQNet Frontend (Port 8080)" cmd /k "set PATH=C:\Users\shreyas\AppData\Roaming\fnm\node-versions\v20.20.2\installation;%PATH% && cd /d "%~dp0" && npm run dev"
