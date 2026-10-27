#!/usr/bin/env python3
"""Sahte barkod okuyucu: gerçek cihaz gelmeden uygulamayı test etmek için.

Endüstriyel okuyucu gibi TCP sunucu açar ve bağlanan istemciye sıralı kodlar gönderir.
Kullanım:  python araclar/sahte_okuyucu.py [--port 2001] [--aralik 1.0] [--hata 0.1]
Uygulamada: Kurulum -> Tür: Barkod okuyucu, IP: 127.0.0.1, Port: 2001, Okunamadı metni: NoRead
"""
import argparse
import random
import socket
import threading
import time


def istemci(baglanti, adres, a):
    print(f"bağlandı: {adres}")
    no = a.baslangic
    try:
        while True:
            r = random.random()
            if r < a.hata:
                metin = "NoRead"
            elif r < a.hata * 2:
                metin = f"PSK-{no + 5:06d}"  # sıra dışı kod (YANLIŞ olmalı)
            else:
                metin = f"PSK-{no:06d}"
                no += 1
            baglanti.sendall(metin.encode() + b"\r\n")
            print("gönderildi:", metin)
            time.sleep(a.aralik)
    except OSError:
        print(f"koptu: {adres}")
    finally:
        baglanti.close()


def main():
    ap = argparse.ArgumentParser(description="Sahte barkod okuyucu (TCP)")
    ap.add_argument("--port", type=int, default=2001)
    ap.add_argument("--aralik", type=float, default=1.0, help="kodlar arası saniye")
    ap.add_argument("--hata", type=float, default=0.1, help="okunamadı / sıra dışı oranı")
    ap.add_argument("--baslangic", type=int, default=1)
    a = ap.parse_args()
    s = socket.create_server(("127.0.0.1", a.port))
    print(f"Sahte okuyucu 127.0.0.1:{a.port} adresinde bekliyor (durdurmak için Ctrl+C)")
    try:
        while True:
            b, adres = s.accept()
            threading.Thread(target=istemci, args=(b, adres, a), daemon=True).start()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
