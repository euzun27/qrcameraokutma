#!/usr/bin/env python3
"""Bant QR Okuyucu yerel sunucusu.

index.html'i sunar ve IP kameranın görüntüsünü aynı adresten (/kamera) yayınlar.
Tarayıcı başka adresteki kamera görüntüsünü güvenlik nedeniyle okuyamadığı için bu aracı gerekir.

Kullanım:  python sunucu.py [--port 8080]
Sonra tarayıcıda:  http://localhost:8080/?kamera=192.168.1.152

/kamera?url=... şunları kabul eder:
  192.168.1.152                      -> bilinen MJPEG / anlık görüntü adresleri sırayla denenir
  http://kullanici:sifre@ip/yol      -> MJPEG akışı veya tek JPEG (tek JPEG ise sürekli yenilenir)
  rtsp://kullanici:sifre@ip:554/yol  -> ffmpeg kuruluysa MJPEG'e çevrilir
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BOUNDARY = b"kare"
# IP verilip yol verilmezse denenecek yaygın adresler (önce akışlar, sonra anlık görüntüler)
COMMON_PATHS = [
    "/video", "/video.mjpg", "/mjpg/video.mjpg", "/mjpeg", "/stream", "/videostream.cgi",
    "/cgi-bin/mjpg/video.cgi", "/axis-cgi/mjpg/video.cgi", "/video.cgi",
    "/snapshot.jpg", "/shot.jpg", "/image.jpg", "/jpg/image.jpg", "/snap.jpg",
    "/cgi-bin/snapshot.cgi", "/ISAPI/Streaming/channels/101/picture", "/onvif-http/snapshot",
    "/",
]
_found = {}  # ip -> çalışan tam adres


def opener_for(url):
    p = urllib.parse.urlsplit(url)
    handlers = []
    if p.username:
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        clean = p._replace(netloc=p.hostname + (f":{p.port}" if p.port else "")).geturl()
        mgr.add_password(None, clean, urllib.parse.unquote(p.username), urllib.parse.unquote(p.password or ""))
        handlers += [urllib.request.HTTPBasicAuthHandler(mgr), urllib.request.HTTPDigestAuthHandler(mgr)]
        url = clean
    return urllib.request.build_opener(*handlers), url


def open_image(url, timeout=5):
    """Adresi açar; görüntü/akış dönüyorsa (yanıt, içerik türü) verir, değilse None."""
    opener, clean = opener_for(url)
    try:
        r = opener.open(clean, timeout=timeout)
    except Exception:
        return None
    ctype = r.headers.get("Content-Type", "").lower()
    if ctype.startswith("image/") or "multipart" in ctype:
        return r, ctype
    r.close()
    return None


def resolve(target):
    """Kullanıcının verdiği adresi çalışan bir http(s) ya da rtsp adresine çevirir."""
    if "://" in target:
        return target
    host = target.strip().strip("/")
    if host in _found:
        return _found[host]
    for path in COMMON_PATHS:
        url = f"http://{host}{path}"
        res = open_image(url, timeout=3)
        if res:
            res[0].close()
            _found[host] = url
            print(f"[kamera] {host} için çalışan adres: {url}")
            return url
    if shutil.which("ffmpeg"):
        return f"rtsp://{host}:554/"
    return None


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if not self.path.startswith("/kamera"):
            super().log_message(fmt, *args)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path != "/kamera":
            return super().do_GET()
        target = urllib.parse.parse_qs(u.query).get("url", [""])[0]
        url = resolve(target) if target else None
        if not url:
            return self.send_error(502, "Kamera adresi bulunamadi")
        try:
            if url.startswith("rtsp"):
                self.stream_ffmpeg(url)
            else:
                self.stream_http(url)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def start_multipart(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + BOUNDARY.decode())
        self.end_headers()

    def send_frame(self, jpg):
        self.wfile.write(b"--" + BOUNDARY + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
        self.wfile.flush()

    def stream_http(self, url):
        res = open_image(url)
        if not res:
            return self.send_error(502, "Kameradan goruntu alinamadi")
        r, ctype = res
        if "multipart" in ctype:
            # Akışı olduğu gibi aktar
            self.send_response(200)
            self.send_header("Content-Type", r.headers["Content-Type"])
            self.end_headers()
            while True:
                chunk = r.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            return
        # Tek JPEG veren kamera: sürekli yenileyip akışa çevir
        self.start_multipart()
        self.send_frame(r.read())
        r.close()
        while True:
            t = time.time()
            res = open_image(url)
            if res:
                self.send_frame(res[0].read())
                res[0].close()
            time.sleep(max(0, 0.04 - (time.time() - t)))

    def stream_ffmpeg(self, url):
        if not shutil.which("ffmpeg"):
            return self.send_error(502, "RTSP icin ffmpeg kurulu olmali")
        p = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url,
             "-an", "-f", "mpjpeg", "-boundary_tag", BOUNDARY.decode(), "-q:v", "4", "-"],
            stdout=subprocess.PIPE)
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + BOUNDARY.decode())
        self.end_headers()
        try:
            while True:
                chunk = p.stdout.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            p.kill()


def main():
    ap = argparse.ArgumentParser(description="Bant QR Okuyucu yerel sunucusu")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"Bant QR Okuyucu: http://localhost:{a.port}/   (durdurmak için Ctrl+C)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
