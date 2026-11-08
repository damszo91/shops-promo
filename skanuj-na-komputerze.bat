@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Shops Promo: skan sklepow blokujacych GitHuba (CCC, Adidas, eobuwie) ===
echo.
echo [1/4] Pobieram najnowsza wersje z GitHuba...
git pull --rebase || goto blad
echo [2/4] Sprawdzam biblioteki (za pierwszym razem chwile potrwa)...
python -m pip install --quiet --disable-pip-version-check curl_cffi playwright || goto blad
echo [3/4] Skanuje - otworzy sie okno Edge, nie zamykaj go (kilka minut)...
python scraper.py --local || goto blad
echo [4/4] Wysylam wyniki na GitHuba...
git add docs data
git diff --cached --quiet || git commit -q -m "Skan z komputera"
git pull --rebase || goto konflikt
git push || goto blad
echo.
echo GOTOWE - produkty pojawia sie na stronie za 1-2 minuty.
pause
exit /b 0

:konflikt
git rebase --abort
git reset -q --hard @{u}
echo.
echo W tym samym czasie skonczyl sie skan na GitHubie. Uruchom ten plik jeszcze raz.
pause
exit /b 1

:blad
echo.
echo Cos poszlo nie tak - zrob zrzut tego okna i wyslij.
pause
exit /b 1
