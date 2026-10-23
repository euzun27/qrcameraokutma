#!/usr/bin/env python3
"""Sahte Zebra yazıcı: gerçek yazıcı yokken etiket baskısını test etmek için.

Zebra gibi 9100 portunda bekler, gelen ZPL'i ekrana yazar ve araclar/basilan_etiketler/ altına kaydeder.
Kullanım:  python araclar/sahte_yazici.py [--port 9100]
Uygulamada: Kurulum -> Etiket yazıcı -> Ağ, IP: 127.0.0.1, Port: 9100
ZPL'in nasıl görüneceğini görmek için kaydedilen dosya bir ZPL görüntüleyicide açılabilir.
"""
import argparse
import os
import re
import socket
import time

KLASOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "basilan_etiketler")


def main():
    ap = argparse.ArgumentParser(description="Sahte Zebra yazıcı (ZPL, TCP 9100)")
    ap.add_argument("--port", type=int, default=9100)
    a = ap.parse_args()
    os.makedirs(KLASOR, exist_ok=True)
    s = socket.create_server(("127.0.0.1", a.port))
    print(f"Sahte Zebra 127.0.0.1:{a.port} adresinde bekliyor (durdurmak için Ctrl+C)")
    try:
        while True:
            b, _ = s.accept()
            with b:
                b.settimeout(2)
                veri = b""
                try:
                    while True:
                        parca = b.recv(65536)
                        if not parca:
                            break
                        veri += parca
                except socket.timeout:
                    pass
            zpl = veri.decode("utf-8", errors="replace")
            adet = re.search(r"\^PQ(\d+)", zpl)
            ad = os.path.join(KLASOR, time.strftime("%Y%m%d_%H%M%S") + ".zpl")
            open(ad, "w", encoding="utf-8").write(zpl)
            print(f"--- etiket alındı ({adet.group(1) if adet else 1} adet) -> {ad}\n{zpl}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
