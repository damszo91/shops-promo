@echo off
schtasks /delete /tn "Shops Promo 07" /f
schtasks /delete /tn "Shops Promo 13" /f
schtasks /delete /tn "Shops Promo 19" /f
del "%~dp0.harmonogram-ok" 2>nul
echo Automatyczne skanowanie wylaczone.
pause
