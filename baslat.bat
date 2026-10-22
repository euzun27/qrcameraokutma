@echo off
chcp 65001 >nul
title Dunyatek Kamera Okutma
cd /d "%~dp0"

rem Python'u bul (py başlatıcısı ya da python)
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python 3 bulunamadi. Once kur.bat dosyasini calistirin.
  pause
  exit /b 1
)

echo Dunyatek Kamera Okutma calisiyor. Bu pencereyi kapatirsaniz uygulama durur.
%PY% sunucu.py --ac %*
if errorlevel 1 timeout /t 5 >nul
