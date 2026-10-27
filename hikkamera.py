"""Hikrobot endüstriyel kamera (USB3 Vision / GigE) desteği - Hikrobot MVS SDK ile.

Kamera kod çözmez, sadece görüntü verir: kareler JPEG'e çevrilip uygulamanın /kamera akışına
verilir, barkodlar tarayıcıda diğer kameralarla aynı şekilde okunur.

Gerekli: hedef bilgisayarda Hikrobot MVS kurulu olmalı (USB sürücüsü + MvCameraControl.dll +
Python örnek modülleri "MvImport"). Kamera aynı anda tek bir programa açılır; MVS açıkken
kameraya bağlıysa bu program kamerayı açamaz.
"""
import ctypes
import io
import os
import sys
import threading
import time
from types import SimpleNamespace

MONO8 = 0x01080001
_sdk = None
_sdk_hata = None
_sdk_kilit = threading.Lock()


def sdk():
    """MVS SDK modüllerini bir kez yükler; yüklenemezse None (neden: sdk_hatasi())."""
    global _sdk, _sdk_hata
    with _sdk_kilit:
        if _sdk or _sdk_hata:
            return _sdk
        try:
            if os.name != "nt":
                raise OSError("Hikrobot desteği şimdilik sadece Windows'ta")
            gelistirme = os.environ.get("MVCAM_COMMON_RUNENV") or r"C:\Program Files (x86)\MVS\Development"
            calisma = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                                   "Common Files", "MVS", "Runtime", "Win64_x64")
            if os.path.isdir(calisma):
                os.add_dll_directory(calisma)
                os.environ["PATH"] = calisma + os.pathsep + os.environ.get("PATH", "")
            ornekler = os.path.join(gelistirme, "Samples", "Python")
            if not os.path.isdir(os.path.join(ornekler, "MvImport")):
                raise OSError("Hikrobot MVS kurulu değil (MvImport bulunamadı)")
            if ornekler not in sys.path:
                sys.path.append(ornekler)
            from MvImport import CameraParams_const, CameraParams_header, MvCameraControl_class, PixelType_header
            ns = SimpleNamespace()
            for m in (CameraParams_const, CameraParams_header, PixelType_header, MvCameraControl_class):
                ns.__dict__.update({k: v for k, v in vars(m).items() if not k.startswith("__")})
            ns.MvCamera.MV_CC_Initialize()
            _sdk = ns
        except Exception as e:  # SDK yoksa uygulama diğer kameralarla çalışmaya devam eder
            _sdk_hata = str(e) or e.__class__.__name__
        return _sdk


def sdk_hatasi():
    return _sdk_hata


def _metin(dizi):
    b = bytes(memoryview(dizi)).split(b"\x00", 1)[0]
    for kod in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(kod)
        except UnicodeDecodeError:
            pass
    return ""


def _liste(s):
    liste = s.MV_CC_DEVICE_INFO_LIST()
    r = s.MvCamera.MV_CC_EnumDevices(s.MV_USB_DEVICE | s.MV_GIGE_DEVICE, liste)
    if r != 0:
        raise OSError(f"Kameralar listelenemedi (0x{r:x})")
    sonuc = []
    for i in range(liste.nDeviceNum):
        b = ctypes.cast(liste.pDeviceInfo[i], ctypes.POINTER(s.MV_CC_DEVICE_INFO)).contents
        if b.nTLayerType == s.MV_USB_DEVICE:
            u = b.SpecialInfo.stUsb3VInfo
            sonuc.append(({"model": _metin(u.chModelName), "seri": _metin(u.chSerialNumber), "baglanti": "USB"}, b))
        else:
            g = b.SpecialInfo.stGigEInfo
            ip = ".".join(str((g.nCurrentIp >> k) & 0xFF) for k in (24, 16, 8, 0))
            sonuc.append(({"model": _metin(g.chModelName), "seri": _metin(g.chSerialNumber),
                           "baglanti": f"GigE {ip}"}, b))
    return sonuc


def cihazlar():
    """Bağlı Hikrobot kameraları: [{model, seri, baglanti}]"""
    s = sdk()
    if not s:
        raise OSError(sdk_hatasi())
    return [d for d, _ in _liste(s)]


class HikKamera:
    """Bir kamerayı açık tutar, son kareyi JPEG olarak paylaşır. İzleyen kalmayınca kapanır."""
    BOSTA_KAPAT_SN = 10

    def __init__(self, seri):
        self.seri = seri
        self.ayar = {"pozlama_us": 500.0, "kazanc": 6.0, "kare_hizi": 0.0}
        self.jpeg, self.no, self.durum = None, 0, "kapalı"
        self.izleyen, self.son_izleme = 0, time.time()
        self.kosul = threading.Condition()
        self.calisiyor = False
        self.yeniden_ayarla = False

    def ayarla(self, pozlama_us, kazanc, kare_hizi):
        yeni = {"pozlama_us": float(pozlama_us), "kazanc": float(kazanc), "kare_hizi": float(kare_hizi)}
        if yeni != self.ayar:
            self.ayar = yeni
            self.yeniden_ayarla = True

    def abone_ol(self):
        with self.kosul:
            self.izleyen += 1
            if not self.calisiyor:
                self.calisiyor = True
                threading.Thread(target=self.calis, daemon=True).start()

    def abonelikten_cik(self):
        with self.kosul:
            self.izleyen -= 1
            self.son_izleme = time.time()

    def kare_bekle(self, son_no, zaman_asimi=3.0):
        with self.kosul:
            self.kosul.wait_for(lambda: self.no != son_no, timeout=zaman_asimi)
            return self.no, self.jpeg

    # --- kamera döngüsü ---
    def calis(self):
        try:
            while self.izleyen > 0 or time.time() - self.son_izleme < self.BOSTA_KAPAT_SN:
                try:
                    self.oturum()
                except Exception as e:
                    self.durum = f"hata: {e}"
                    print(f"[hikrobot] {self.seri or 'ilk kamera'}: {e}")
                    time.sleep(2)
        finally:
            with self.kosul:
                self.calisiyor = False
            self.durum = "kapalı"

    def _uygula(self, cam, s):
        a = self.ayar
        cam.MV_CC_SetEnumValue("TriggerMode", s.MV_TRIGGER_MODE_OFF)
        cam.MV_CC_SetEnumValueByString("PixelFormat", "Mono8")
        cam.MV_CC_SetEnumValue("ExposureAuto", 0)
        cam.MV_CC_SetFloatValue("ExposureTime", a["pozlama_us"])
        cam.MV_CC_SetEnumValue("GainAuto", 0)
        cam.MV_CC_SetFloatValue("Gain", a["kazanc"])
        if a["kare_hizi"] > 0:
            cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
            cam.MV_CC_SetFloatValue("AcquisitionFrameRate", a["kare_hizi"])
        else:
            cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", False)
        self.yeniden_ayarla = False

    def oturum(self):
        from PIL import Image
        s = sdk()
        if not s:
            raise OSError(sdk_hatasi())
        aday = [b for d, b in _liste(s) if not self.seri or d["seri"] == self.seri]
        if not aday:
            raise OSError("Kamera bulunamadı (USB kablosunu ve MVS sürücüsünü kontrol edin)")
        cam = s.MvCamera()
        if cam.MV_CC_CreateHandle(aday[0]) != 0:
            raise OSError("Kamera tanıtıcısı oluşturulamadı")
        try:
            r = cam.MV_CC_OpenDevice(s.MV_ACCESS_Exclusive, 0)
            if r != 0:
                raise OSError("Kamera açılamadı: başka bir program (ör. MVS) kullanıyor olabilir"
                              if r == 0x80000203 else f"Kamera açılamadı (0x{r:x})")
            try:
                self._uygula(cam, s)
                if cam.MV_CC_StartGrabbing() != 0:
                    raise OSError("Görüntü alımı başlatılamadı")
                self.durum = "açık"
                print(f"[hikrobot] {self.seri or 'ilk kamera'} açıldı")
                kare = s.MV_FRAME_OUT()
                son_kodlama, hatali = 0.0, 0
                while self.izleyen > 0 or time.time() - self.son_izleme < self.BOSTA_KAPAT_SN:
                    if self.yeniden_ayarla:
                        self._uygula(cam, s)
                    ctypes.memset(ctypes.byref(kare), 0, ctypes.sizeof(kare))
                    if cam.MV_CC_GetImageBuffer(kare, 1000) != 0:
                        hatali += 1
                        if hatali >= 5:
                            raise OSError("Kameradan görüntü gelmiyor")
                        continue
                    hatali = 0
                    try:
                        fi = kare.stFrameInfo
                        simdi = time.time()
                        # İzleyen varken en fazla ~30 kare/sn JPEG'e çevir (işlemciyi korur)
                        if self.izleyen > 0 and simdi - son_kodlama >= 1 / 30:
                            if fi.enPixelType != MONO8:
                                raise OSError("Kamera Mono8 dışında bir piksel biçiminde")
                            ham = ctypes.string_at(kare.pBufAddr, fi.nWidth * fi.nHeight)
                            tampon = io.BytesIO()
                            Image.frombuffer("L", (fi.nWidth, fi.nHeight), ham, "raw", "L", 0, 1).save(
                                tampon, "JPEG", quality=85)
                            son_kodlama = simdi
                            with self.kosul:
                                self.jpeg, self.no = tampon.getvalue(), self.no + 1
                                self.kosul.notify_all()
                    finally:
                        cam.MV_CC_FreeImageBuffer(kare)
                cam.MV_CC_StopGrabbing()
            finally:
                cam.MV_CC_CloseDevice()
        finally:
            cam.MV_CC_DestroyHandle()
            print(f"[hikrobot] {self.seri or 'ilk kamera'} kapatıldı")


_kameralar = {}
_kameralar_kilit = threading.Lock()


def durumlar():
    """Açılmaya çalışılmış kameraların son durumu: {seri: "açık" / "hata: ..." / "kapalı"}"""
    with _kameralar_kilit:
        return {s: k.durum for s, k in _kameralar.items()}


def kamera_al(seri):
    with _kameralar_kilit:
        k = _kameralar.get(seri)
        if not k:
            k = _kameralar[seri] = HikKamera(seri)
        return k
