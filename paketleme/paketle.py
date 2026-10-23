#!/usr/bin/env python3
"""Tek kurulum dosyası üretir: paketleme\\cikti\\DunyatekBarkod_Kurulum_<sürüm>.exe

Gerekenler (sadece paketleyen bilgisayarda):
  python -m pip install pyinstaller pystray pillow
  winget install JRSoftware.InnoSetup
  winget install Gyan.FFmpeg            (kuruluma ffmpeg koymak için)
Kullanım:  python paketleme\\paketle.py   (ya da paketle.bat)
"""
import glob
import os
import shutil
import subprocess
import sys

BURA = os.path.dirname(os.path.abspath(__file__))
KOK = os.path.dirname(BURA)
CIKTI = os.path.join(BURA, "cikti")


def adim(metin):
    print(f"\n=== {metin} ===", flush=True)


def ffmpeg_bul():
    yol = shutil.which("ffmpeg") or os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe")
    if not os.path.exists(yol):
        return None, None
    gercek = os.path.realpath(yol)  # winget kısayolu -> paket klasöründeki asıl dosya
    lisans = os.path.join(os.path.dirname(os.path.dirname(gercek)), "LICENSE")
    return gercek, (lisans if os.path.isfile(lisans) else None)


def iscc_bul():
    adaylar = [shutil.which("ISCC")] + glob.glob(r"C:\Program Files*\Inno Setup *\ISCC.exe") \
        + glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup *\ISCC.exe"))
    return next((a for a in adaylar if a and os.path.isfile(a)), None)


def main():
    os.makedirs(CIKTI, exist_ok=True)

    adim("1/3 Uygulama tek exe olarak paketleniyor (PyInstaller)")
    veri = [("index.html", "."), ("giris.html", "."), ("gorsel/logo.png", "gorsel"),
            ("gorsel/ikon.png", "gorsel")] + [(f"vendor/{d}", "vendor") for d in
                                              ("jsQR.js", "qrcode.js", "zxing.min.js")]
    komut = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
             "--name", "DunyatekBarkod", "--icon", os.path.join(KOK, "gorsel", "ikon.ico"),
             "--version-file", os.path.join(BURA, "surum.txt"),
             "--distpath", os.path.join(CIKTI, "dist"), "--workpath", os.path.join(CIKTI, "build"),
             "--specpath", CIKTI]
    for kaynak, hedef in veri:
        komut += ["--add-data", os.path.join(KOK, kaynak) + os.pathsep + hedef]
    komut.append(os.path.join(KOK, "sunucu.py"))
    subprocess.run(komut, check=True)

    adim("2/3 ffmpeg kopyalanıyor")
    ff, lisans = ffmpeg_bul()
    if not ff:
        sys.exit("ffmpeg bulunamadı: winget install Gyan.FFmpeg")
    hedef = os.path.join(CIKTI, "ffmpeg")
    os.makedirs(hedef, exist_ok=True)
    shutil.copy2(ff, os.path.join(hedef, "ffmpeg.exe"))
    if lisans:
        shutil.copy2(lisans, os.path.join(hedef, "LICENSE"))
    else:
        open(os.path.join(hedef, "LICENSE"), "w").write("FFmpeg - GPLv3 - https://ffmpeg.org/legal.html\n")
    print(ff)

    adim("3/3 Kurulum dosyası derleniyor (Inno Setup)")
    iscc = iscc_bul()
    if not iscc:
        sys.exit("Inno Setup bulunamadı: winget install JRSoftware.InnoSetup")
    subprocess.run([iscc, "/Q", os.path.join(BURA, "kurulum.iss")], check=True)
    for f in glob.glob(os.path.join(CIKTI, "DunyatekBarkod_Kurulum_*.exe")):
        print(f"HAZIR: {f}  ({os.path.getsize(f) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
