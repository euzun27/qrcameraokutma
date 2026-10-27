@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Kurulum dosyasını üretir: paketleme\cikti\DunyatekBarkod_Kurulum_<sürüm>.exe
python paketleme\paketle.py
pause
