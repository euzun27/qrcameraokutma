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
  192.168.1.152                      -> bilinen MJPEG / anlık görüntü adresleri sırayla denenir
  http://kullanici:sifre@ip/yol      -> MJPEG akışı veya tek JPEG (tek JPEG ise sürekli yenilenir)
  rtsp://kullanici:sifre@ip:554/yol  -> ffmpeg kuruluysa MJPEG'e çevrilir
/okuyucu?id=<no>        -> kayıtlı barkod okuyucunun (TCP) kodları, Server-Sent Events olarak
"""
import argparse
import hashlib
import hmac
import json
import os
import queue
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

UYGULAMA = "Dünyatek Hızlı Barkod Okuma Sistemi"
PAKETLI = getattr(sys, "frozen", False)  # PyInstaller ile tek exe olarak mı çalışıyor
# KOK: sayfa/kütüphane dosyaları (paketliyken exe'nin içinden açılan geçici klasör)
KOK = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
# KURULUM: exe'nin bulunduğu klasör (ffmpeg buraya konur)
KURULUM = os.path.dirname(sys.executable) if PAKETLI else KOK
# Ayarlar: kurulu sürümde ProgramData altında (Program Files yazılabilir değil, güncellemede korunur)
VERI_DIZINI = (os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Dunyatek", "HizliBarkod")
               if PAKETLI else os.path.join(KOK, "veri"))
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
ACIK_DOSYALAR = {"/giris.html", "/gorsel/logo.png"}
KORUNAN_DOSYALAR = {"/index.html", "/vendor/jsQR.js", "/vendor/qrcode.js", "/vendor/zxing.min.js"}


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
    return {"id": k["id"], "ad": k["ad"], "adres": k["adres"], "tur": k.get("tur", "kamera"),
            "port": k.get("port"), "okunamadi": k.get("okunamadi", ""),
            "kullanici": k.get("kullanici", ""), "sifre_var": bool(k.get("sifre"))}


# ---------------- barkod okuyucu (TCP) ----------------
# Endüstriyel okuyucular (Hikrobot, Cognex, Datalogic…) okudukları kodu TCP üzerinden metin
# olarak gönderir. Sunucu okuyucuya istemci olarak bağlanır; kodlar CR/LF/ETX/NUL ile ayrılır,
# ayraç gelmezse kısa bir sessizlikten sonra gelen metin tek kod sayılır.
AYRACLAR = b"\r\n\x03\x00"


class Okuyucu:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.aboneler = set()
        self.durum = "baglaniyor"
        self.kilit = threading.Lock()
        threading.Thread(target=self.calis, daemon=True).start()

    def yay(self, olay, veri):
        with self.kilit:
            for q in list(self.aboneler):
                try:
                    q.put_nowait((olay, veri))
                except queue.Full:
                    pass

    def durum_ayarla(self, durum):
        if durum != self.durum:
            self.durum = durum
            self.yay("durum", durum)

    def calis(self):
        while True:
            try:
                with socket.create_connection((self.host, self.port), timeout=5) as s:
                    s.settimeout(0.15)
                    self.durum_ayarla("bagli")
                    print(f"[okuyucu] {self.host}:{self.port} bağlandı")
                    tampon = b""
                    while True:
                        try:
                            parca = s.recv(4096)
                            if not parca:
                                break
                            tampon += parca
                        except socket.timeout:
                            # Ayraçsız gönderen okuyucular: sessizlik = kod bitti
                            if tampon:
                                self.kod(tampon)
                                tampon = b""
                            continue
                        while True:
                            i = next((i for i, b in enumerate(tampon) if b in AYRACLAR), -1)
                            if i < 0:
                                break
                            self.kod(tampon[:i])
                            tampon = tampon[i + 1:]
                        if len(tampon) > 4096:
                            tampon = b""
            except OSError:
                pass
            self.durum_ayarla("kopuk")
            time.sleep(2)

    def kod(self, ham):
        ham = ham.strip(b"\x02 \t")  # STX ve boşluklar
        if not ham:
            return
        try:
            metin = ham.decode("utf-8")
        except UnicodeDecodeError:
            metin = ham.decode("latin-1")
        self.yay("kod", metin)


_okuyucular = {}  # (host, port) -> Okuyucu
_okuyucu_kilit = threading.Lock()


def okuyucu_al(host, port):
    with _okuyucu_kilit:
        o = _okuyucular.get((host, port))
        if not o:
            o = _okuyucular[(host, port)] = Okuyucu(host, port)
        return o


# ---------------- okuma kayıtları (kalıcı tablo) ----------------
DURUMLAR = ("DOĞRU", "YANLIŞ", "OKUNAMADI", "BAĞLANTI YOK")
_db_kilit = threading.Lock()


def db():
    os.makedirs(VERI_DIZINI, exist_ok=True)
    c = sqlite3.connect(os.path.join(VERI_DIZINI, "kayitlar.db"), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS okumalar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, zaman TEXT NOT NULL, kod TEXT NOT NULL, tip TEXT,
        durum TEXT NOT NULL, aciklama TEXT, kaynak TEXT, kullanici TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_okumalar_zaman ON okumalar(zaman)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_okumalar_kod ON okumalar(kod)")
    c.execute("""CREATE TABLE IF NOT EXISTS baskilar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, zaman TEXT NOT NULL, kod TEXT NOT NULL, adet INTEGER NOT NULL,
        yazici TEXT, kullanici TEXT)""")
    return c


@contextmanager
def baglanti():
    """Kilitli, işlem sonunda commit edilip kapanan veritabanı bağlantısı."""
    with _db_kilit:
        c = db()
        try:
            with c:
                yield c
        finally:
            c.close()


def okuma_ekle(kod, tip, durum, aciklama, kaynak, kullanici):
    zaman = time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    with baglanti() as c:
        cur = c.execute("INSERT INTO okumalar (zaman, kod, tip, durum, aciklama, kaynak, kullanici)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)", (zaman, kod, tip, durum, aciklama, kaynak, kullanici))
        return cur.lastrowid, zaman


def okuma_sorgu(qs, limit=1000):
    """?bas=YYYY-MM-DD&bit=YYYY-MM-DD&durum=...&ara=... süzgeçleriyle kayıtlar (yeniden eskiye)."""
    al = lambda k: (qs.get(k, [""])[0] or "").strip()
    kosul, deger = [], []
    if al("bas"):
        kosul.append("zaman >= ?"); deger.append(al("bas")[:10])
    if al("bit"):
        kosul.append("zaman < ?"); deger.append(al("bit")[:10] + " 99")  # günün sonuna kadar
    if al("durum") in DURUMLAR:
        kosul.append("durum = ?"); deger.append(al("durum"))
    if al("ara"):
        kosul.append("kod LIKE ?"); deger.append("%" + al("ara")[:100] + "%")
    where = (" WHERE " + " AND ".join(kosul)) if kosul else ""
    with baglanti() as c:
        ozet = c.execute(f"SELECT COUNT(*) toplam, SUM(durum='DOĞRU') dogru FROM okumalar{where}", deger).fetchone()
        sinir = f" LIMIT {int(limit)}" if limit else ""
        satirlar = c.execute(f"SELECT * FROM okumalar{where} ORDER BY id DESC{sinir}", deger).fetchall()
    toplam, dogru = ozet["toplam"] or 0, ozet["dogru"] or 0
    return [dict(r) for r in satirlar], {"toplam": toplam, "dogru": dogru, "yanlis": toplam - dogru}


# ---------------- Zebra etiket yazıcı (ZPL) ----------------
# Zebra ZT410/ZT411: ağdan ham TCP (port 9100) ya da Windows yazıcısına RAW olarak ZPL gönderilir.
YAZICI_VARSAYILAN = {"baglanti": "ag", "ip": "", "port": 9100, "windows_ad": "", "dpi": 203,
                     "genislik_mm": 100, "yukseklik_mm": 50, "adet": 1, "sablon": ""}


def yazici_ayar():
    with _kilit:
        y = dict(YAZICI_VARSAYILAN)
        y.update(ayar_oku().get("yazici", {}))
    return y


def zpl_kacis(metin):
    """^FH_ ile kullanılacak alan verisi: ZPL özel karakterlerini onaltılık yaz."""
    return "".join("_%02X" % ord(c) if c in "_^~" else c for c in metin)


def zpl_uret(y, kod, durum, adet):
    simdi = time.localtime()
    degerler = {"KOD": zpl_kacis(kod), "DURUM": zpl_kacis(durum), "ADET": str(adet),
                "TARIH": time.strftime("%d.%m.%Y", simdi), "SAAT": time.strftime("%H:%M:%S", simdi)}
    if y.get("sablon", "").strip():
        # Kullanıcının şablonu (ör. ZebraDesigner çıktısı): {KOD} {DURUM} {TARIH} {SAAT} {ADET}
        zpl = y["sablon"]
        for k, v in degerler.items():
            zpl = zpl.replace("{" + k + "}", v)
        if "^PQ" not in zpl:
            i = zpl.rfind("^XZ")
            zpl = (zpl[:i] + f"^PQ{adet}\n" + zpl[i:]) if i >= 0 else zpl + f"\n^PQ{adet}\n^XZ"
        return zpl
    nokta = lambda mm: int(round(float(mm) * int(y["dpi"]) / 25.4))
    w, h, k = nokta(y["genislik_mm"]), nokta(y["yukseklik_mm"]), nokta(3)  # 3 mm kenar boşluğu
    qr = h - 2 * k  # QR kare alanı
    buyutme = max(2, min(10, qr // 33))  # 33 modül civarı QR için modül boyutu (nokta)
    yazi_x, yazi_w = k + qr + k, w - (k + qr + k) - k
    f1, f2 = max(20, h // 7), max(18, h // 10)
    return (f"^XA\n^CI28\n^PW{w}\n^LL{h}\n^LH0,0\n"
            f"^FO{k},{k}^BQN,2,{buyutme}^FH_^FDMA,{degerler['KOD']}^FS\n"
            f"^FO{yazi_x},{k}^A0N,{f1},{f1}^FB{yazi_w},4,4,L^FH_^FD{degerler['KOD']}^FS\n"
            f"^FO{yazi_x},{k + f1 * 4 + 10}^A0N,{f1},{f1}^FB{yazi_w},1,0,L^FH_^FD{degerler['DURUM']}^FS\n"
            f"^FO{yazi_x},{h - k - f2}^A0N,{f2},{f2}^FD{degerler['TARIH']} {degerler['SAAT']}^FS\n"
            f"^PQ{adet},0,1,Y\n^XZ\n")


def windows_yazicilari():
    """Kurulu Windows yazıcılarının adları (EnumPrintersW, seviye 4)."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class PRINTER_INFO_4(ctypes.Structure):
        _fields_ = [("pPrinterName", wintypes.LPWSTR), ("pServerName", wintypes.LPWSTR),
                    ("Attributes", wintypes.DWORD)]

    ws = ctypes.WinDLL("winspool.drv")
    bayrak = 0x2 | 0x4  # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    gerekli, adet = wintypes.DWORD(), wintypes.DWORD()
    ws.EnumPrintersW(bayrak, None, 4, None, 0, ctypes.byref(gerekli), ctypes.byref(adet))
    if not gerekli.value:
        return []
    tampon = ctypes.create_string_buffer(gerekli.value)
    if not ws.EnumPrintersW(bayrak, None, 4, tampon, gerekli, ctypes.byref(gerekli), ctypes.byref(adet)):
        return []
    dizi = ctypes.cast(tampon, ctypes.POINTER(PRINTER_INFO_4))
    return [dizi[i].pPrinterName for i in range(adet.value)]


def windows_ham_yazdir(ad, veri):
    """Windows yazıcı kuyruğuna RAW veri gönderir (sürücü ZPL'i olduğu gibi iletir)."""
    import ctypes
    from ctypes import wintypes

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR),
                    ("pDatatype", wintypes.LPWSTR)]

    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
    h = wintypes.HANDLE()
    if not ws.OpenPrinterW(ad, ctypes.byref(h), None):
        raise OSError(f"Yazıcı açılamadı: {ad}")
    try:
        if not ws.StartDocPrinterW(h, 1, ctypes.byref(DOC_INFO_1("Dunyatek etiket", None, "RAW"))):
            raise OSError("Yazdırma işi başlatılamadı")
        try:
            ws.StartPagePrinter(h)
            yazilan = wintypes.DWORD()
            if not ws.WritePrinter(h, veri, len(veri), ctypes.byref(yazilan)) or yazilan.value != len(veri):
                raise OSError("Yazıcıya veri gönderilemedi")
            ws.EndPagePrinter(h)
        finally:
            ws.EndDocPrinter(h)
    finally:
        ws.ClosePrinter(h)


def yazdir(y, zpl):
    veri = zpl.encode("utf-8")
    if y["baglanti"] == "windows":
        if not y.get("windows_ad"):
            raise OSError("Windows yazıcısı seçilmemiş")
        windows_ham_yazdir(y["windows_ad"], veri)
    else:
        if not y.get("ip"):
            raise OSError("Yazıcı IP adresi girilmemiş")
        with socket.create_connection((y["ip"], int(y.get("port") or 9100)), timeout=5) as s:
            s.sendall(veri)


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


def ffmpeg_yolu():
    """ffmpeg'i bulur; winget ile yeni kurulduysa PATH yenilenmemiş olabilir."""
    paketli = os.path.join(KURULUM, "ffmpeg", "ffmpeg.exe")  # kurulumla gelen
    if os.path.isfile(paketli):
        return paketli
    yol = shutil.which("ffmpeg")
    if yol:
        return yol
    winget = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe")
    return winget if os.path.isfile(winget) else None


def resolve(target, kullanici="", sifre=""):
    """Verilen adresi çalışan bir http(s) ya da rtsp adresine çevirir."""
    target = target.strip()
    if "://" in target:
        p = urllib.parse.urlsplit(target)
        # "http(s)://IP" gibi yolsuz yazılan adres sadece IP verilmiş gibi aranır
        if p.scheme in ("http", "https") and p.path in ("", "/") and not p.query and p.hostname:
            if p.username and not kullanici:
                kullanici, sifre = urllib.parse.unquote(p.username), urllib.parse.unquote(p.password or "")
            target = p.hostname + (f":{p.port}" if p.port else "")
        else:
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
    if ffmpeg_yolu():
        return kimlik_ekle(f"rtsp://{host}:554/", kullanici, sifre)
    return None


# ---------------- HTTP ----------------
class Handler(SimpleHTTPRequestHandler):
    server_version = "DunyatekKamera/1.0"

    def log_message(self, fmt, *args):
        if not self.path.startswith(("/kamera", "/okuyucu", "/api/ben", "/api/okuma")):
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
            if yol.startswith(("/api/", "/kamera", "/okuyucu")):
                return self.hata(401, "Giriş gerekli")
            return self.yonlendir("/giris.html")
        if yol in KORUNAN_DOSYALAR:
            self.path = yol
            return super().do_GET()
        if yol == "/kamera":
            return self.kamera(urllib.parse.parse_qs(u.query))
        if yol == "/okuyucu":
            return self.okuyucu(urllib.parse.parse_qs(u.query).get("id", [""])[0])
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
        qs = urllib.parse.parse_qs(u.query)
        if yol == "/api/okuma/var":
            # Mükerrer kontrolü: bu kod daha önce DOĞRU olarak okundu mu
            kod = qs.get("kod", [""])[0][:500]
            with baglanti() as c:
                n = c.execute("SELECT COUNT(*) FROM okumalar WHERE kod = ? AND durum = 'DOĞRU'", (kod,)).fetchone()[0]
            return self.json_yanit(200, {"dogru": n})
        if yol == "/api/okumalar":
            kayitlar, ozet = okuma_sorgu(qs)
            return self.json_yanit(200, {"kayitlar": kayitlar, "ozet": ozet})
        if yol == "/api/okumalar.csv":
            return self.okuma_csv(qs)
        if yol == "/api/yazici":
            return self.json_yanit(200, yazici_ayar())
        if yol == "/api/yazicilar":
            try:
                return self.json_yanit(200, windows_yazicilari())
            except OSError:
                return self.json_yanit(200, [])
        return self.hata(404, "Bulunamadı")

    def yazici_kaydet(self, v):
        y = dict(YAZICI_VARSAYILAN)
        try:
            y.update({"baglanti": "windows" if v.get("baglanti") == "windows" else "ag",
                      "ip": str(v.get("ip", "")).strip()[:100], "port": int(v.get("port") or 9100),
                      "windows_ad": str(v.get("windows_ad", ""))[:200], "dpi": int(v.get("dpi") or 203),
                      "genislik_mm": float(v.get("genislik_mm") or 100), "yukseklik_mm": float(v.get("yukseklik_mm") or 50),
                      "adet": int(v.get("adet") or 1), "sablon": str(v.get("sablon", ""))[:20000]})
        except (TypeError, ValueError):
            return self.hata(400, "Yazıcı ayarlarında sayı alanları geçersiz")
        if not 1 <= y["port"] <= 65535 or y["dpi"] not in (203, 300, 600) or not 1 <= y["adet"] <= 9999 \
                or not (10 <= y["genislik_mm"] <= 300 and 10 <= y["yukseklik_mm"] <= 300):
            return self.hata(400, "Port, DPI (203/300/600), adet (1-9999) veya etiket ölçüsü (10-300 mm) geçersiz")
        if y["sablon"].strip() and "^XA" not in y["sablon"]:
            return self.hata(400, "Şablon ^XA ile başlayan bir ZPL olmalı")
        with _kilit:
            a = ayar_oku()
            a["yazici"] = y
            ayar_yaz(a)
        return self.json_yanit(200, y)

    def yazdir_istek(self, v):
        kod = str(v.get("kod", ""))[:500]
        durum = str(v.get("durum", ""))[:40]
        try:
            adet = int(v.get("adet") or 1)
        except (TypeError, ValueError):
            adet = 0
        if not kod or not 1 <= adet <= 9999:
            return self.hata(400, "Kod boş ya da adet 1-9999 dışında")
        y = yazici_ayar()
        try:
            yazdir(y, zpl_uret(y, kod, durum, adet))
        except OSError as e:
            print(f"[yazıcı] hata: {e}")
            return self.hata(502, "Yazıcıya gönderilemedi. Bağlantıyı ve yazıcı ayarlarını kontrol edin.")
        hedef = y["windows_ad"] if y["baglanti"] == "windows" else f"{y['ip']}:{y['port']}"
        with _kilit:
            kullanici = ayar_oku()["kullanici"]["ad"]
        with baglanti() as c:
            c.execute("INSERT INTO baskilar (zaman, kod, adet, yazici, kullanici) VALUES (?, ?, ?, ?, ?)",
                      (time.strftime("%Y-%m-%d %H:%M:%S"), kod, adet, hedef, kullanici))
        print(f"[yazıcı] {hedef}: {kod} x{adet}")
        return self.json_yanit(200, {"ok": True, "adet": adet, "yazici": hedef})

    def okuma_csv(self, qs):
        """Excel'in doğrudan açtığı CSV: UTF-8 BOM, noktalı virgül ayraç."""
        kayitlar, _ = okuma_sorgu(qs, limit=0)
        satirlar = [["No", "Zaman", "Kod", "Tip", "Durum", "Açıklama", "Kamera / okuyucu", "Kullanıcı"]]
        satirlar += [[r["id"], r["zaman"], r["kod"], r["tip"] or "", r["durum"], r["aciklama"] or "",
                      r["kaynak"] or "", r["kullanici"] or ""] for r in reversed(kayitlar)]

        def hucre(v):
            v = str(v)
            if v[:1] in ("=", "+", "-", "@"):  # Excel formül enjeksiyonunu önle
                v = "'" + v
            return '"' + v.replace('"', '""') + '"'

        govde = ("﻿" + "\r\n".join(";".join(hucre(v) for v in s) for s in satirlar)).encode("utf-8")
        ad = "okumalar_" + time.strftime("%Y%m%d_%H%M") + ".csv"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{ad}"')
        self.send_header("Content-Length", str(len(govde)))
        self.end_headers()
        self.wfile.write(govde)

    def okuma_kaydet(self, v):
        kod = str(v.get("kod", ""))[:500]
        durum = str(v.get("durum", ""))
        if not kod or durum not in DURUMLAR:
            return self.hata(400, "Geçersiz okuma")
        with _kilit:
            kullanici = ayar_oku()["kullanici"]["ad"]
        kid, zaman = okuma_ekle(kod, str(v.get("tip", ""))[:40], durum, str(v.get("aciklama", ""))[:300],
                                str(v.get("kaynak", ""))[:100], kullanici)
        return self.json_yanit(200, {"id": kid, "zaman": zaman})

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
        if yol == "/api/okuma":
            return self.okuma_kaydet(v)
        if yol == "/api/yazici":
            return self.yazici_kaydet(v)
        if yol == "/api/yazdir":
            return self.yazdir_istek(v)
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
        tur = "okuyucu" if v.get("tur") == "okuyucu" else "kamera"
        port = None
        if tur == "okuyucu":
            if "://" in adres or "/" in adres:
                return self.hata(400, "Okuyucu için sadece IP adresi yazın (ör. 192.168.1.50)")
            try:
                port = int(v.get("port"))
            except (TypeError, ValueError):
                port = 0
            if not 1 <= port <= 65535:
                return self.hata(400, "Port 1-65535 arasında olmalı")
        elif "://" in adres and not adres.lower().startswith(("http://", "https://", "rtsp://")):
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
            k["ad"], k["adres"], k["tur"], k["port"] = ad, adres, tur, port
            k["okunamadi"] = str(v.get("okunamadi", "")).strip()[:40] if tur == "okuyucu" else ""
            k["kullanici"] = str(v.get("kullanici", "")).strip() if tur == "kamera" else ""
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

    # --- barkod okuyucu akışı (Server-Sent Events) ---
    def okuyucu(self, kid):
        with _kilit:
            k = next((x for x in ayar_oku()["kameralar"]
                      if x["id"] == kid and x.get("tur") == "okuyucu"), None)
        if not k:
            return self.hata(404, "Okuyucu bulunamadı")
        o = okuyucu_al(k["adres"], int(k["port"]))
        q = queue.Queue(maxsize=1000)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        with o.kilit:
            o.aboneler.add(q)
        try:
            self.olay_yaz("durum", o.durum)
            while True:
                try:
                    olay, veri = q.get(timeout=15)
                    self.olay_yaz(olay, veri)
                except queue.Empty:
                    self.wfile.write(b": canli\n\n")  # bağlantıyı açık tut
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            with o.kilit:
                o.aboneler.discard(q)

    def olay_yaz(self, olay, veri):
        self.wfile.write(f"event: {olay}\ndata: {json.dumps(veri, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    # --- kamera akışı ---
    def kamera(self, qs):
        kid = qs.get("id", [""])[0]
        if kid:
            with _kilit:
                k = next((x for x in ayar_oku()["kameralar"] if x["id"] == kid), None)
            if not k or k.get("tur") == "okuyucu":
                return self.send_error(404, "Kamera bulunamadi")
            url = resolve(k["adres"], k.get("kullanici", ""), k.get("sifre", ""))
        else:
            target = qs.get("url", [""])[0]
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

    def stream_ffmpeg(self, url):
        ff = ffmpeg_yolu()
        if not ff:
            return self.send_error(502, "RTSP icin ffmpeg kurulu olmali")
        p = subprocess.Popen(
            [ff, "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url,
             "-an", "-f", "mpjpeg", "-boundary_tag", BOUNDARY.decode(), "-q:v", "4", "-"],
            stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # Pencereli exe'den çalışırken siyah konsol penceresi açılmasın
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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


def yerel_ip():
    """Ağdaki diğer bilgisayarların kullanacağı IP (bilinmiyorsa None)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # paket gönderilmez, sadece çıkış arayüzü seçilir
            return s.getsockname()[0]
    except OSError:
        return None


def tepsi(srv, adres, port):
    """Saatin yanında simge: uygulamayı aç / çıkış. Pencere açık kalmaz."""
    import pystray
    from PIL import Image

    def ac(icon=None, item=None):
        webbrowser.open(adres)

    def cikis(icon, item):
        threading.Thread(target=srv.shutdown, daemon=True).start()
        icon.stop()

    ip = yerel_ip()
    menu = [pystray.MenuItem("Uygulamayı aç", ac, default=True)]
    if ip:
        menu.append(pystray.MenuItem(f"Ağdan erişim: http://{ip}:{port}/", None, enabled=False))
    menu += [pystray.Menu.SEPARATOR, pystray.MenuItem("Çıkış", cikis)]
    simge = pystray.Icon("DunyatekBarkod", Image.open(os.path.join(KOK, "gorsel", "ikon.png")),
                         UYGULAMA, pystray.Menu(*menu))
    simge.run()


def main():
    ap = argparse.ArgumentParser(description=UYGULAMA + " yerel sunucusu")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0", help="127.0.0.1 = sadece bu bilgisayar")
    ap.add_argument("--ac", action="store_true", help="Tarayıcıda uygulamayı aç")
    ap.add_argument("--tepsi", action="store_true", default=PAKETLI,
                    help="Saatin yanında simgeyle, pencere açmadan çalış (kurulu sürümde varsayılan)")
    a = ap.parse_args()
    if sys.stdout is None or PAKETLI:
        # Pencereli exe'de konsol yok: çıktıları günlük dosyasına yaz
        os.makedirs(VERI_DIZINI, exist_ok=True)
        gunluk = open(os.path.join(VERI_DIZINI, "sunucu.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = gunluk
    os.chdir(KOK)
    adres = f"http://localhost:{a.port}/"
    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError:
        # Port dolu: büyük olasılıkla uygulama zaten çalışıyor, sadece tarayıcıyı aç
        print(f"{a.port} portu kullanımda; uygulama zaten çalışıyor olabilir: {adres}")
        if a.ac:
            webbrowser.open(adres)
        sys.exit(0 if a.tepsi else 1)
    srv.daemon_threads = True
    with _kilit:
        ayar_oku()  # ilk çalıştırmada varsayılan ayarları oluştur
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {UYGULAMA}: {adres}   (durdurmak için Ctrl+C)")
    if a.ac:
        threading.Timer(0.5, webbrowser.open, args=(adres,)).start()
    if a.tepsi:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tepsi(srv, adres, a.port)
        return
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
