@echo off
chcp 65001 >nul
title Shops Promo
cd /d "%~dp0"
set PYTHONUTF8=1

echo ==========================================
echo   Shops Promo - skanowanie okazji
echo ==========================================
echo.

python --version >nul 2>nul
if errorlevel 1 (
  echo Nie znaleziono Pythona. Instaluje go teraz (moze pojawic sie pytanie o zgode^)...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python zainstalowany. Zamknij to okno i kliknij URUCHOM.bat jeszcze raz.
  pause
  exit /b
)

if not exist ".instalacja-ok" (
  echo Pierwsze uruchomienie - instaluje potrzebne skladniki (kilka minut^)...
  python -m pip install --upgrade pip >nul
  python -m pip install -r requirements.txt || goto blad
  python -m playwright install chromium || goto blad
  echo ok> .instalacja-ok
  echo.
)

echo Skanuje Sinsay, to potrwa kilka minut...
python scraper.py || goto blad
start "" "%~dp0docs\index.html"

if not exist ".harmonogram-ok" (
  echo.
  echo Ustawiam automatyczne skanowanie 3 razy dziennie (7:00, 13:00, 19:00^)...
  call "%~dp0harmonogram.bat"
)
echo.
echo Gotowe! Strona z okazjami otworzyla sie w przegladarce.
pause
exit /b

:blad
echo.
echo Cos poszlo nie tak. Zrzuty ekranu sa w folderze "debug" - wyslij je Claude.
pause
