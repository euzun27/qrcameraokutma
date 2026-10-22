# Bant QR Okuyucu

Hızlı akan banttaki poşetlerin QR kodlarını kameradan sürekli okuyan, her kodu doğrulayan
(geçersizse ekranda büyük **YANLIŞ** uyarısı ve ses) ve bant durduğunda son okunan barkodu
gösterip etiketini yazdıran tarayıcı uygulaması.

## Çalıştırma

1. Python 3 kurulu bilgisayarda depo klasöründe `python sunucu.py` çalıştırın (ek paket gerekmez).
2. Chrome veya Edge ile `http://localhost:8080/` adresini açın (internet gerekmez, kütüphaneler `vendor/` içinde).
3. Kamera listesinden **IP kamera**'yı seçip adresi yazın (ör. `192.168.1.152`) ya da bilgisayara bağlı kamerayı seçin, **Kamerayı başlat**'a basın.
   Kamera adresi dışarıdan da verilebilir; bu adresle açılınca doğrudan başlar:
   `http://localhost:8080/?kamera=192.168.1.152`
   - Sadece IP verilirse sunucu yaygın MJPEG / anlık görüntü adreslerini sırayla dener.
   - Tam adres de verilebilir: `http://kullanici:sifre@192.168.1.152/video.mjpg`
   - RTSP (`rtsp://...`) için bilgisayarda `ffmpeg` kurulu olmalıdır.
4. **Ayarlar** bölümünden doğrulama yöntemini seçin (varsayılan: sıra kontrolü):
   - **Sıra kontrolü:** koddaki son sayı bir önceki doğru koddan bir fazla olmalı
     (ör. `PSK-000123` → `PSK-000124`). Sıra dışı gelen kod **YANLIŞ** olur ve sıra son doğru koddan devam eder.
     İlk okunan kod sırayı başlatır ya da başlangıç kodu ayarlardan girilir. Bir poşet gerçekten eksikse
     "Sırayı son koddan devam ettir" ile yeni koddan devam edilir.
   - **Kural (desen):** kod bir regex desenine uymalı (ör. `^PSK-\d{4}$`).
   - **Geçerli kod listesi:** her satıra bir kod.
   - **Sistem API:** her kod için `GET` isteği atılır (`{code}` yer tutucusu kullanılabilir).
     HTTP 200 ve JSON'da `valid` / `gecerli` / `ok` alanı `true` ise DOĞRU sayılır.
4. İsteğe bağlı **Kayıt (POST) adresi** verilirse her okuma `{code, valid, time}` olarak sisteme gönderilir.
   Kayıtlar ayrıca tarayıcıda saklanır ve CSV olarak indirilebilir.

## Bant durdu algılama

Ayarlardaki süre (varsayılan 3 sn) boyunca **yeni** bir kod okunmazsa (poşet kameranın önünde
duruyor olsa bile) bant durmuş sayılır: son barkod ve durumu büyük gösterilir, etiketi hazırlanır,
istenirse otomatik yazdırılır. Yeni bir kod okununca okuma moduna dönülür.

## Notlar

- Chrome/Edge'in yerleşik `BarcodeDetector`'ı varsa o kullanılır, yoksa jsQR (saniyede ~60 tarama).
- Hızlı bantta bulanıklığı azaltmak için kamerada kısa pozlama süresi ve iyi aydınlatma önemlidir.
- Sistem API'si farklı bir adresteyse sunucunun CORS izni vermesi gerekir.
- USB barkod okuyucu klavye gibi çalıştığı için "Elle kod" kutusuna da okutulabilir.
