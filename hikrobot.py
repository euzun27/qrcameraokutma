"""Hikrobot (MVS SDK) USB3 / GigE endüstriyel kamera desteği.

Hikrobot kameralar (ör. MV-CU020-90UM) Windows'ta normal webcam olarak görünmez;
görüntü Hikrobot MVS yazılımıyla gelen SDK üzerinden alınır. Bu modül SDK'nın Python
örneklerindeki MvImport klasörünü bulur, kamerayı açar, kareleri sürekli alıp JPEG'e çevirir.
Ek Python paketi gerekmez; JPEG dönüşümünü SDK yapar.

Kamera adresi (Kurulum ekranında IP yerine yazılır):
  hikrobot                      -> bulunan ilk Hikrobot kamera
  hikrobot:2                    -> 2. kamera (1'den başlar)
  hikrobot:DA1234567            -> seri numarası verilen kamera
  hikrobot?pozlama=800&kazanc=8 -> pozlama (mikrosaniye), kazanç (dB), fps, kalite (JPEG 1-100)
Ayar verilmezse kameranın MVS'de kaydedilmiş ayarları kullanılır.

Tek başına çalıştırınca kameraları listeler ve hikrobot_test.jpg kaydeder:
  python hikrobot.py [adres]
"""
import ctypes
import glob
import os
import sys
import threading
import time
import urllib.parse

_MV = None  # MvCameraControl_class modülü


def _sdk():
    """MVS Python sarmalayıcısını (MvImport) ve DLL'lerini bulup yükler."""
    global _MV
    if _MV is not None:
        return _MV
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    # Python 3.8+ DLL'leri PATH'ten aramadığı için MVS çalışma ortamını elle ekle
    if hasattr(os, "add_dll_directory"):
        alt = "Win64_x64" if sys.maxsize > 2**32 else "Win32_i86"
        for kok in (os.environ.get("MVCAM_COMMON_RUNENV", ""),
                    os.path.join(pf86, "Common Files", "MVS", "Runtime"),
                    os.path.join(pf, "Common Files", "MVS", "Runtime")):
            yol = os.path.join(kok, alt) if kok else ""
            if yol and os.path.isdir(yol):
                os.add_dll_directory(yol)
                os.environ["PATH"] = yol + os.pathsep + os.environ.get("PATH", "")
    adaylar = [os.environ.get("MVS_PYTHON", ""), os.path.join(os.path.dirname(os.path.abspath(__file__)), "MvImport")]
    for kok in (pf86, pf):
        adaylar += glob.glob(os.path.join(kok, "MVS", "Development", "Samples", "Python", "MvImport"))
        adaylar += glob.glob(os.path.join(kok, "MVS", "Development", "Samples", "Python", "*", "MvImport"))
    for yol in adaylar:
        if yol and os.path.isfile(os.path.join(yol, "MvCameraControl_class.py")):
            if yol not in sys.path:
                sys.path.append(yol)
            break
    try:
        import MvCameraControl_class as mv
    except Exception as e:
        raise RuntimeError(
            "Hikrobot MVS SDK bulunamadı. MVS'yi kurun (Python örnekleri dahil) ya da MVS_PYTHON "
            "ortam değişkenini MvImport klasörüne ayarlayın. Hata: " + str(e))
    if hasattr(mv.MvCamera, "MV_CC_Initialize"):
        mv.MvCamera.MV_CC_Initialize()  # MVS 4.x ve sonrası
    _MV = mv
    return mv


def _metin(dizi):
    return bytes(dizi).split(b"\0", 1)[0].decode("ascii", "replace")


def listele():
    """Bağlı Hikrobot kameraları ([(sıra, model, seri, tür)], ham liste) olarak döndürür."""
    mv = _sdk()
    liste = mv.MV_CC_DEVICE_INFO_LIST()
    ret = mv.MvCamera.MV_CC_EnumDevices(mv.MV_GIGE_DEVICE | mv.MV_USB_DEVICE, liste)
    if ret != 0:
        raise RuntimeError(f"Kamera listesi alınamadı (0x{ret:x})")
    sonuc = []
    for i in range(liste.nDeviceNum):
        bilgi = ctypes.cast(liste.pDeviceInfo[i], ctypes.POINTER(mv.MV_CC_DEVICE_INFO)).contents
        if bilgi.nTLayerType == mv.MV_USB_DEVICE:
            u = bilgi.SpecialInfo.stUsb3VInfo
            sonuc.append((i + 1, _metin(u.chModelName), _metin(u.chSerialNumber), "USB"))
        else:
            g = bilgi.SpecialInfo.stGigEInfo
            sonuc.append((i + 1, _metin(g.chModelName), _metin(g.chSerialNumber), "GigE"))
    return sonuc, liste


def adres_mi(adres):
    return str(adres).strip().lower().startswith("hikrobot")


def _adres_coz(adres):
    govde, _, sorgu = adres.strip().partition("?")
    secim = govde.partition(":")[2].strip()
    ayar = {k: v[0] for k, v in urllib.parse.parse_qs(sorgu).items()}
    return secim, ayar


class HikKamera:
    """Kamerayı açık tutar, son kareyi JPEG olarak paylaşır (birden çok izleyici için)."""

    def __init__(self, adres):
        self.secim, self.ayar = _adres_coz(adres)
        self.jpeg = None
        self.sayac = 0
        self.hata = None
        self.kosul = threading.Condition()
        self.calisiyor = True
        threading.Thread(target=self._dongu, daemon=True).start()

    @staticmethod
    def _kontrol(ret, ne):
        if ret != 0:
            raise RuntimeError(f"Hikrobot {ne} hatası (0x{ret:x})")

    def _ac(self):
        mv = _sdk()
        kameralar, liste = listele()
        if not kameralar:
            raise RuntimeError("Bağlı Hikrobot kamera bulunamadı (kablo takılı mı, MVS açıksa kapatın)")
        sira = 0
        if self.secim.isdigit():
            sira = int(self.secim) - 1
        elif self.secim:
            sira = next((s - 1 for s, _, seri, _ in kameralar if seri == self.secim), -1)
        if not 0 <= sira < len(kameralar):
            raise RuntimeError(f"İstenen Hikrobot kamera yok: {self.secim}. Bulunanlar: {kameralar}")
        bilgi = ctypes.cast(liste.pDeviceInfo[sira], ctypes.POINTER(mv.MV_CC_DEVICE_INFO)).contents
        cam = mv.MvCamera()
        self._kontrol(cam.MV_CC_CreateHandle(bilgi), "CreateHandle")
        ret = cam.MV_CC_OpenDevice(mv.MV_ACCESS_Exclusive, 0)
        if ret != 0:
            cam.MV_CC_DestroyHandle()
            raise RuntimeError(f"Kamera açılamadı (0x{ret:x}). MVS programı açıksa kapatın.")
        if bilgi.nTLayerType == mv.MV_GIGE_DEVICE:
            boy = cam.MV_CC_GetOptimalPacketSize()
            if int(boy) > 0:
                cam.MV_CC_SetIntValue("GevSCPSPacketSize", boy)
        cam.MV_CC_SetEnumValue("TriggerMode", 0)  # sürekli çekim
        if "pozlama" in self.ayar:
            cam.MV_CC_SetEnumValue("ExposureAuto", 0)
            cam.MV_CC_SetFloatValue("ExposureTime", float(self.ayar["pozlama"]))
        if "kazanc" in self.ayar:
            cam.MV_CC_SetEnumValue("GainAuto", 0)
            cam.MV_CC_SetFloatValue("Gain", float(self.ayar["kazanc"]))
        if "fps" in self.ayar:
            cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
            cam.MV_CC_SetFloatValue("AcquisitionFrameRate", float(self.ayar["fps"]))
        self._kontrol(cam.MV_CC_StartGrabbing(), "StartGrabbing")
        print(f"[hikrobot] {kameralar[sira][1]} ({kameralar[sira][2]}) açıldı")
        return cam

    def _jpeg(self, cam, kare, tampon):
        mv = _MV
        fi = kare.stFrameInfo
        if hasattr(mv, "MV_SAVE_IMAGE_PARAM_EX3") and hasattr(cam, "MV_CC_SaveImageEx3"):
            p, kaydet = mv.MV_SAVE_IMAGE_PARAM_EX3(), cam.MV_CC_SaveImageEx3
        else:
            p, kaydet = mv.MV_SAVE_IMAGE_PARAM_EX(), cam.MV_CC_SaveImageEx2
        ctypes.memset(ctypes.byref(p), 0, ctypes.sizeof(p))
        p.enImageType = mv.MV_Image_Jpeg
        p.enPixelType = fi.enPixelType
        p.nWidth, p.nHeight = fi.nWidth, fi.nHeight
        p.nDataLen = fi.nFrameLen
        p.pData = ctypes.cast(kare.pBufAddr, ctypes.POINTER(ctypes.c_ubyte))
        p.pImageBuffer = ctypes.cast(ctypes.byref(tampon), ctypes.POINTER(ctypes.c_ubyte))
        p.nBufferSize = ctypes.sizeof(tampon)
        p.nJpgQuality = int(self.ayar.get("kalite", 80))
        self._kontrol(kaydet(p), "JPEG")
        return ctypes.string_at(tampon, p.nImageLen)

    def _dongu(self):
        while self.calisiyor:
            cam = None
            try:
                mv = _sdk()
                cam = self._ac()
                self.hata = None
                kare = mv.MV_FRAME_OUT()
                tampon = None
                while self.calisiyor:
                    ctypes.memset(ctypes.byref(kare), 0, ctypes.sizeof(kare))
                    ret = cam.MV_CC_GetImageBuffer(kare, 1000)
                    if ret != 0:
                        if ret == getattr(mv, "MV_E_NODATA", 0x80000007):
                            continue  # bu saniye kare gelmedi
                        raise RuntimeError(f"Kare alınamadı (0x{ret:x})")
                    try:
                        fi = kare.stFrameInfo
                        gerek = fi.nWidth * fi.nHeight * 3 + 4096
                        if tampon is None or ctypes.sizeof(tampon) < gerek:
                            tampon = (ctypes.c_ubyte * gerek)()
                        jpg = self._jpeg(cam, kare, tampon)
                    finally:
                        cam.MV_CC_FreeImageBuffer(kare)
                    with self.kosul:
                        self.jpeg = jpg
                        self.sayac += 1
                        self.kosul.notify_all()
            except Exception as e:
                self.hata = str(e)
                print("[hikrobot]", e)
                with self.kosul:
                    self.kosul.notify_all()
            finally:
                if cam is not None:
                    for f in ("MV_CC_StopGrabbing", "MV_CC_CloseDevice", "MV_CC_DestroyHandle"):
                        try:
                            getattr(cam, f)()
                        except Exception:
                            pass
            if self.calisiyor:
                time.sleep(2)  # kablo çıktı vb.: yeniden dene

    def kare_bekle(self, onceki, zaman_asimi=5):
        """Sayaç onceki'den farklı olana kadar bekler: (sayac, jpeg); kare yoksa (onceki, None)."""
        with self.kosul:
            self.kosul.wait_for(lambda: self.sayac != onceki or self.hata, zaman_asimi)
            if self.sayac == onceki:
                return onceki, None
            return self.sayac, self.jpeg

    def durdur(self):
        self.calisiyor = False


_kameralar = {}
_kameralar_kilit = threading.Lock()


def kamera_al(adres):
    """Aynı adres için tek kamera nesnesi (kamera aynı anda tek programa açılabilir)."""
    anahtar = adres.strip().lower()
    with _kameralar_kilit:
        k = _kameralar.get(anahtar)
        if k is None:
            k = _kameralar[anahtar] = HikKamera(adres)
        return k


if __name__ == "__main__":
    try:
        kameralar, _ = listele()
    except RuntimeError as e:
        print(e)
        sys.exit(1)
    if not kameralar:
        print("Bağlı Hikrobot kamera bulunamadı. MVS açıksa kapatın.")
        sys.exit(1)
    for sira, model, seri, tur in kameralar:
        print(f"{sira}. {model}  seri: {seri}  ({tur})  -> adres: hikrobot:{sira}")
    k = HikKamera(sys.argv[1] if len(sys.argv) > 1 else "hikrobot")
    n, jpg, t = 0, None, None
    for _ in range(61):
        n, yeni = k.kare_bekle(n, 8)
        if yeni is None:
            break
        jpg = yeni
        t = t or time.time()
    sure = time.time() - (t or time.time())
    k.durdur()
    if jpg is None:
        print("Görüntü alınamadı:", k.hata)
        sys.exit(1)
    with open("hikrobot_test.jpg", "wb") as f:
        f.write(jpg)
    print(f"{n} kare alındı (~{(n - 1) / sure if sure else 0:.1f} kare/sn). Son kare: hikrobot_test.jpg ({len(jpg) // 1024} KB)")
