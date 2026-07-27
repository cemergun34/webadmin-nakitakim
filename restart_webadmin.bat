@echo off
echo ============================================
echo  webadmin-nakitAkim Guncelleme ve Restart
echo ============================================

cd /d "%~dp0"

echo.
echo [1/3] Git pull yapiliyor...
git pull origin main
if errorlevel 1 (
    echo HATA: git pull basarisiz!
    pause
    exit /b 1
)

echo.
echo [2/3] Calisan webadmin sureci durduruluyor...
taskkill /F /FI "WINDOWTITLE eq webadmin*" /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| find ":5050" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo.
echo [3/3] webadmin yeniden baslatiliyor...
start "webadmin-nakitakim" /min python app.py

echo.
echo ============================================
echo  TAMAM! webadmin http://localhost:5050
echo  adresinde calisiyor.
echo ============================================
timeout /t 3 /nobreak >nul
