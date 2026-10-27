@echo off
chcp 65001 >nul
title Dunyatek Kamera Okutma - Kurulum
cd /d "%~dp0"
echo ================================================
echo   Dunyatek Kamera Okutma - Kurulum
echo ================================================
echo.

rem 1) Python 3 kontrolü
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo [!] Python 3 bulunamadi.
  echo     python.org adresinden Python 3 indirip kurun.
  echo     Kurulumda "Add python.exe to PATH" kutusunu isaretleyin.
  echo     Sonra bu dosyayi tekrar calistirin.
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)
for /f "delims=" %%v in ('%PY% --version') do echo [ok] %%v bulundu.

rem 2) Masaüstü kısayolu (küçültülmüş pencerede başlar)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Dunyatek Kamera Okutma.lnk');" ^
  "$s.TargetPath='%~dp0baslat.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7;" ^
  "$s.IconLocation='%SystemRoot%\System32\shell32.dll,117'; $s.Description='Dunyatek Kamera Okutma'; $s.Save()"
if errorlevel 1 (echo [!] Masaustu kisayolu olusturulamadi.) else (echo [ok] Masaustune "Dunyatek Kamera Okutma" kisayolu eklendi.)

rem 3) İsteğe bağlı: Windows açılınca otomatik başlat
echo.
choice /c EH /m "Bilgisayar acilinca uygulama otomatik baslasin mi? (E=Evet, H=Hayir)"
if errorlevel 2 goto atla
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Dunyatek Kamera Okutma.lnk');" ^
  "$s.TargetPath='%~dp0baslat.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo [ok] Otomatik baslatma eklendi.
:atla

echo.
echo Kurulum tamam. Ilk giris:  kullanici adi: admin   sifre: admin
echo (Ilk giriste sifreyi degistirmeniz istenecek.)
echo.
choice /c EH /m "Uygulama simdi baslatilsin mi?"
if errorlevel 2 exit /b 0
start "Dunyatek Kamera Okutma" /min "%~dp0baslat.bat"
