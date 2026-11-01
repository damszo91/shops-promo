@echo off
cd /d "%~dp0"
set CEL=wscript.exe \"%~dp0skan-w-tle.vbs\"
schtasks /create /tn "Shops Promo 07" /tr "%CEL%" /sc daily /st 07:00 /f >nul || goto blad
schtasks /create /tn "Shops Promo 13" /tr "%CEL%" /sc daily /st 13:00 /f >nul || goto blad
schtasks /create /tn "Shops Promo 19" /tr "%CEL%" /sc daily /st 19:00 /f >nul || goto blad
echo ok> .harmonogram-ok
echo Automatyczne skanowanie wlaczone: codziennie o 7:00, 13:00 i 19:00 (gdy komputer jest wlaczony^).
exit /b
:blad
echo Nie udalo sie ustawic harmonogramu.
