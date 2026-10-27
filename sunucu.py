#!/usr/bin/env python3
"""Dunyatek Kamera Okutma yerel sunucusu.

Uygulamayı sunar, kullanıcı girişini yönetir, kayıtlı IP kameraların görüntüsünü
aynı adresten (/kamera) yayınlar. Tarayıcı başka adresteki kamera görüntüsünü
güvenlik nedeniyle okuyamadığı için bu aracı gerekir.

Kullanım:  python sunucu.py [--port 8080] [--host 0.0.0.0] [--ac]
Sonra tarayıcıda:  http://localhost:8080/   (ilk giriş: admin / admin)

Ayarlar (kullanıcı, kameralar) veri/ayarlar.json dosyasında tutulur.

/kamera?id=<kamera no>  -> kurulumda kayıtlı kamera
/kamera?url=...         -> doğrudan adres:
  hikrobot[:n|:seri][?pozlama=us&kazanc=dB&fps=n] -> Hikrobot USB/GigE kamera (MVS SDK, hikrobot.py)
  192.168.1.152                      -> bilinen MJPEG / anlık görüntü adresleri sırayla denenir
  http://kullanici:sifre@ip/yol      -> MJPEG akışı veya tek JPEG (tek JPEG ise sürekli yenilenir)
  rtsp://kullanici:sifre@ip:554/yol  -> ffmpeg kuruluysa MJPEG'e çevrilir
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import hikrobot

UYGULAMA = "Dunyatek Kamera Okutma"
KOK = os.path.dirname(os.path.abspath(__file__))
VERI_DIZINI = os.path.join(KOK, "veri")
AYAR_DOSYASI = os.path.join(VERI_DIZINI, "ayarlar.json")
OTURUM_SURESI = 12 * 3600  # saniye
CEREZ = "dt_oturum"

BOUNDARY = b"kare"
# IP verilip yol verilmezse denenecek yaygın adresler (önce akışlar, sonra anlık görüntüler)
COMMON_PATHS = [
    "/video", "/video.mjpg", "/mjpg/video.mjpg", "/mjpeg", "/stream", "/videostream.cgi",
    "/cgi-bin/mjpg/video.cgi", "/axis-cgi/mjpg/video.cgi", "/video.cgi",
    "/snapshot.jpg", "/shot.jpg", "/image.jpg", "/jpg/image.jpg", "/snap.jpg",
    "/cgi-bin/snapshot.cgi", "/ISAPI/Streaming/channels/101/picture", "/onvif-http/snapshot",
    "/",
]
_found = {}  # (ip, kullanıcı) -> çalışan tam adres

# Giriş yapmadan açılabilen dosyalar; giriş sonrası da yalnızca bu listedekiler sunulur
ACIK_DOSYALAR = {"/giris.html"}
KORUNAN_DOSYALAR = {"/index.html", "/vendor/jsQR.js", "/vendor/qrcode.js"}


# ---------------- ayarlar ----------------
_kilit = threading.Lock()


def sifre_ozeti(sifre, tuz=None):
    tuz = tuz or secrets.token_hex(16)
    ozet = hashlib.pbkdf2_hmac("sha256", sifre.encode("utf-8"), bytes.fromhex(tuz), 200_000).hex()
    return tuz, ozet


def varsayilan_ayarlar():
    tuz, ozet = sifre_ozeti("admin")
    return {
        "kullanici": {"ad": "admin", "tuz": tuz, "ozet": ozet, "sifre_degismeli": True},
        "kameralar": [],
        "sonraki_id": 1,
    }


def ayar_oku():
    try:
        with open(AYAR_DOSYASI, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        a = varsayilan_ayarlar()
        ayar_yaz(a)
        return a


def ayar_yaz(a):
    os.makedirs(VERI_DIZINI, exist_ok=True)
    gecici = AYAR_DOSYASI + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=2)
    os.replace(gecici, AYAR_DOSYASI)


def kamera_disari(k):
    """Kamera kaydını tarayıcıya gönderilecek hale getirir (şifre gönderilmez)."""
    return {"id": k["id"], "ad": k["ad"], "adres": k["adres"],
            "kullanici": k.get("kullanici", ""), "sifre_var": bool(k.get("sifre"))}


# ---------------- oturumlar ----------------
_oturumlar = {}  # jeton -> bitiş zamanı
_hatalar = {}  # ip -> [hatalı deneme sayısı, kilit bitişi]


def oturum_ac():
    jeton = secrets.token_urlsafe(32)
    _oturumlar[jeton] = time.time() + OTURUM_SURESI
    return jeton


def oturumlari_kapat():
    _oturumlar.clear()


# ---------------- kamera ----------------
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


def kimlik_ekle(url, kullanici, sifre):
    """Kamera kullanıcı adı/şifresini adrese ekler (adreste zaten yoksa)."""
    if not kullanici:
        return url
    p = urllib.parse.urlsplit(url)
    if p.username:
        return url
    kimlik = urllib.parse.quote(kullanici, safe="") + ":" + urllib.parse.quote(sifre or "", safe="")
    return p._replace(netloc=kimlik + "@" + p.netloc).geturl()


def resolve(target, kullanici="", sifre=""):
    """Verilen adresi çalışan bir http(s) ya da rtsp adresine çevirir."""
    target = target.strip()
    if "://" in target:
        return kimlik_ekle(target, kullanici, sifre)
    host = target.strip("/")
    anahtar = (host, kullanici)
    if anahtar in _found:
        return _found[anahtar]
    for path in COMMON_PATHS:
        url = kimlik_ekle(f"http://{host}{path}", kullanici, sifre)
        res = open_image(url, timeout=3)
        if res:
            res[0].close()
            _found[anahtar] = url
            print(f"[kamera] {host} için çalışan adres: http://{host}{path}")
            return url
    if shutil.which("ffmpeg"):
        return kimlik_ekle(f"rtsp://{host}:554/", kullanici, sifre)
    return None


# ---------------- HTTP ----------------
class Handler(SimpleHTTPRequestHandler):
    server_version = "DunyatekKamera/1.0"

    def log_message(self, fmt, *args):
        if not self.path.startswith(("/kamera", "/api/ben")):
            super().log_message(fmt, *args)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    # --- yardımcılar ---
    def jeton(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[CEREZ].value if CEREZ in c else None

    def girisli(self):
        j = self.jeton()
        bitis = _oturumlar.get(j) if j else None
        if not bitis or bitis < time.time():
            _oturumlar.pop(j, None)
            return False
        _oturumlar[j] = time.time() + OTURUM_SURESI
        return True

    def json_yanit(self, durum, veri, cerez=None):
        govde = json.dumps(veri, ensure_ascii=False).encode("utf-8")
        self.send_response(durum)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(govde)))
        if cerez is not None:
            self.send_header("Set-Cookie", cerez)
        self.end_headers()
        self.wfile.write(govde)

    def hata(self, durum, mesaj):
        self.json_yanit(durum, {"hata": mesaj})

    def govde_oku(self):
        if "application/json" not in self.headers.get("Content-Type", ""):
            return None
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if n <= 0 or n > 65536:
            return None
        try:
            v = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return v if isinstance(v, dict) else None

    def yonlendir(self, yer):
        self.send_response(302)
        self.send_header("Location", yer)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --- GET / HEAD ---
    def do_HEAD(self):
        # Dosya sunucusunun HEAD yolu giriş kontrolünü atlamasın
        self.send_response(405)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        yol = u.path
        if yol == "/":
            yol = "/index.html"
        if yol in ACIK_DOSYALAR:
            if yol == "/giris.html" and self.girisli():
                return self.yonlendir("/")
            self.path = yol
            return super().do_GET()
        if not self.girisli():
            if yol.startswith(("/api/", "/kamera")):
                return self.hata(401, "Giriş gerekli")
            return self.yonlendir("/giris.html")
        if yol in KORUNAN_DOSYALAR:
            self.path = yol
            return super().do_GET()
        if yol == "/kamera":
            return self.kamera(urllib.parse.parse_qs(u.query))
        if yol == "/api/ben":
            with _kilit:
                a = ayar_oku()
            return self.json_yanit(200, {"kullanici": a["kullanici"]["ad"],
                                         "sifre_degismeli": a["kullanici"].get("sifre_degismeli", False),
                                         "uygulama": UYGULAMA})
        if yol == "/api/kameralar":
            with _kilit:
                a = ayar_oku()
            return self.json_yanit(200, [kamera_disari(k) for k in a["kameralar"]])
        return self.hata(404, "Bulunamadı")

    # --- POST ---
    def do_POST(self):
        yol = urllib.parse.urlsplit(self.path).path
        if yol == "/api/giris":
            return self.giris()
        if not self.girisli():
            return self.hata(401, "Giriş gerekli")
        if yol == "/api/cikis":
            _oturumlar.pop(self.jeton(), None)
            return self.json_yanit(200, {"ok": True}, f"{CEREZ}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        v = self.govde_oku()
        if v is None:
            return self.hata(400, "Geçersiz istek")
        if yol == "/api/hesap":
            return self.hesap(v)
        if yol == "/api/kameralar":
            return self.kamera_kaydet(v, None)
        if yol.startswith("/api/kameralar/"):
            parca = yol.rsplit("/", 2)
            if yol.endswith("/sil"):
                return self.kamera_sil(parca[-2])
            return self.kamera_kaydet(v, parca[-1])
        return self.hata(404, "Bulunamadı")

    def giris(self):
        ip = self.client_address[0]
        sayac = _hatalar.get(ip, [0, 0])
        if sayac[1] > time.time():
            return self.hata(429, f"Çok fazla hatalı deneme. {int(sayac[1] - time.time()) + 1} sn sonra tekrar deneyin.")
        v = self.govde_oku()
        if v is None:
            return self.hata(400, "Geçersiz istek")
        with _kilit:
            a = ayar_oku()
        k = a["kullanici"]
        _, ozet = sifre_ozeti(str(v.get("sifre", "")), k["tuz"])
        dogru = hmac.compare_digest(ozet, k["ozet"]) and hmac.compare_digest(
            str(v.get("kullanici", "")).strip().lower(), k["ad"].lower())
        if not dogru:
            sayac[0] += 1
            if sayac[0] >= 5:
                sayac = [0, time.time() + 60]
            _hatalar[ip] = sayac
            time.sleep(0.5)
            return self.hata(401, "Kullanıcı adı veya şifre hatalı")
        _hatalar.pop(ip, None)
        jeton = oturum_ac()
        return self.json_yanit(200, {"ok": True, "sifre_degismeli": k.get("sifre_degismeli", False)},
                               f"{CEREZ}={jeton}; Path=/; HttpOnly; SameSite=Strict")

    def hesap(self, v):
        yeni_ad = str(v.get("yeni_kullanici", "")).strip()
        yeni_sifre = str(v.get("yeni_sifre", ""))
        with _kilit:
            a = ayar_oku()
            k = a["kullanici"]
            _, ozet = sifre_ozeti(str(v.get("eski_sifre", "")), k["tuz"])
            if not hmac.compare_digest(ozet, k["ozet"]):
                return self.hata(403, "Mevcut şifre hatalı")
            if yeni_ad:
                if len(yeni_ad) < 3 or len(yeni_ad) > 40:
                    return self.hata(400, "Kullanıcı adı 3-40 karakter olmalı")
                k["ad"] = yeni_ad
            if yeni_sifre:
                if len(yeni_sifre) < 6:
                    return self.hata(400, "Yeni şifre en az 6 karakter olmalı")
                if yeni_sifre == "admin":
                    return self.hata(400, "Varsayılan şifre kullanılamaz")
                k["tuz"], k["ozet"] = sifre_ozeti(yeni_sifre)
                k["sifre_degismeli"] = False
            elif k.get("sifre_degismeli"):
                return self.hata(400, "İlk girişte yeni şifre belirlemelisiniz")
            ayar_yaz(a)
        # Şifre değiştiyse diğer oturumları kapat, bu oturumu yenile
        oturumlari_kapat()
        jeton = oturum_ac()
        return self.json_yanit(200, {"ok": True, "kullanici": k["ad"]},
                               f"{CEREZ}={jeton}; Path=/; HttpOnly; SameSite=Strict")

    def kamera_kaydet(self, v, kid):
        ad = str(v.get("ad", "")).strip()
        adres = str(v.get("adres", "")).strip()
        if not ad or len(ad) > 60:
            return self.hata(400, "Kamera adı 1-60 karakter olmalı")
        if not adres or len(adres) > 300 or any(c.isspace() for c in adres):
            return self.hata(400, "Kamera adresi geçersiz")
        if "://" in adres and not adres.lower().startswith(("http://", "https://", "rtsp://")):
            return self.hata(400, "Adres http://, https:// veya rtsp:// ile başlamalı")
        with _kilit:
            a = ayar_oku()
            if kid is None:
                k = {"id": str(a.get("sonraki_id", 1))}
                a["sonraki_id"] = int(k["id"]) + 1
                a["kameralar"].append(k)
            else:
                k = next((x for x in a["kameralar"] if x["id"] == kid), None)
                if not k:
                    return self.hata(404, "Kamera bulunamadı")
            k["ad"], k["adres"] = ad, adres
            k["kullanici"] = str(v.get("kullanici", "")).strip()
            if v.get("sifre") or not k["kullanici"]:
                k["sifre"] = str(v.get("sifre", "")) if k["kullanici"] else ""
            ayar_yaz(a)
        _found.clear()
        return self.json_yanit(200, kamera_disari(k))

    def kamera_sil(self, kid):
        with _kilit:
            a = ayar_oku()
            once = len(a["kameralar"])
            a["kameralar"] = [x for x in a["kameralar"] if x["id"] != kid]
            if len(a["kameralar"]) == once:
                return self.hata(404, "Kamera bulunamadı")
            ayar_yaz(a)
        return self.json_yanit(200, {"ok": True})

    # --- kamera akışı ---
    def kamera(self, qs):
        kid = qs.get("id", [""])[0]
        if kid:
            with _kilit:
                k = next((x for x in ayar_oku()["kameralar"] if x["id"] == kid), None)
            if not k:
                return self.send_error(404, "Kamera bulunamadi")
            if hikrobot.adres_mi(k["adres"]):
                return self.stream_hikrobot(k["adres"])
            url = resolve(k["adres"], k.get("kullanici", ""), k.get("sifre", ""))
        else:
            target = qs.get("url", [""])[0]
            if hikrobot.adres_mi(target):
                return self.stream_hikrobot(target)
            url = resolve(target) if target else None
        if not url:
            return self.send_error(502, "Kamera adresi bulunamadi")
        try:
            if url.startswith("rtsp"):
                self.stream_ffmpeg(url)
            else:
                self.stream_http(url)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
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

    def stream_hikrobot(self, adres):
        """Hikrobot (MVS SDK) kameranın karelerini MJPEG olarak yayınlar."""
        try:
            kam = hikrobot.kamera_al(adres)
        except Exception as e:
            return self.send_error(502, "Hikrobot: " + str(e))
        sayac, jpg = kam.kare_bekle(kam.sayac - 1 if kam.jpeg else kam.sayac, 8)
        if jpg is None:
            return self.send_error(502, "Hikrobot kameradan goruntu alinamadi: " + (kam.hata or "zaman asimi"))
        self.start_multipart()
        try:
            while True:
                self.send_frame(jpg)
                sayac, jpg = kam.kare_bekle(sayac, 5)
                if jpg is None:
                    if kam.hata:
                        break
                    jpg = kam.jpeg  # bant durdu / kare gelmiyor: son kareyi tekrar gönder
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

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
    ap = argparse.ArgumentParser(description=UYGULAMA + " yerel sunucusu")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0", help="127.0.0.1 = sadece bu bilgisayar")
    ap.add_argument("--ac", action="store_true", help="Tarayıcıda uygulamayı aç")
    a = ap.parse_args()
    os.chdir(KOK)
    adres = f"http://localhost:{a.port}/"
    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError:
        # Port dolu: büyük olasılıkla uygulama zaten çalışıyor
        print(f"{a.port} portu kullanımda; uygulama zaten çalışıyor olabilir: {adres}")
        if a.ac:
            webbrowser.open(adres)
        sys.exit(1)
    srv.daemon_threads = True
    with _kilit:
        ayar_oku()  # ilk çalıştırmada varsayılan ayarları oluştur
    print(f"{UYGULAMA}: {adres}   (durdurmak için Ctrl+C)")
    if a.ac:
        threading.Timer(0.5, webbrowser.open, args=(adres,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
