@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
python scraper.py > ostatni-skan.log 2>&1
