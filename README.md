# Dunyatek Kamera Okutma

Hızlı akan banttaki poşetlerin QR kodlarını kameradan sürekli okuyan, her kodu doğrulayan
(geçersizse ekranda büyük **YANLIŞ** uyarısı ve ses) ve bant durduğunda son okunan barkodu
gösterip etiketini yazdıran tarayıcı uygulaması. Kullanıcı girişi ve kamera kurulum ekranı vardır.

## Yeni bilgisayara kurulum

1. Bilgisayarda Python 3 yoksa [python.org](https://www.python.org/downloads/windows/) adresinden kurun
   (kurulumda **Add python.exe to PATH** kutusunu işaretleyin). Ek paket gerekmez.
2. Bu klasörü bilgisayara kopyalayın (ör. `C:\DunyatekKamera`).
3. `kur.bat` dosyasına çift tıklayın. Masaüstüne **Dunyatek Kamera Okutma** kısayolu eklenir;
   isterseniz Windows açılınca otomatik başlatma da eklenir.
4. Kısayola tıklayınca sunucu küçültülmüş pencerede başlar ve tarayıcıda uygulama açılır.
   Pencere kapatılırsa uygulama durur.

Elle başlatmak için: `python sunucu.py --ac` (`--port 8080`, `--host 127.0.0.1` = sadece bu bilgisayar).
Varsayılan olarak ağdaki diğer bilgisayarlar da `http://<bu-bilgisayarın-IP'si>:8080/` ile girebilir.

## Giriş ve kurulum

- İlk giriş: kullanıcı adı **admin**, şifre **admin**. İlk girişte şifreyi değiştirmeniz istenir.
- Üstteki **Kurulum** düğmesi:
  - **Kameralar:** kamera adı ve IP adresi ile kamera ekleyin, düzenleyin, silin; isteğe bağlı kamera
    kullanıcı adı/şifresi girilebilir. **Görüntüyü test et** ile kaydetmeden önce görüntü denenir.
    - Sadece IP verilirse sunucu yaygın MJPEG / anlık görüntü adreslerini sırayla dener.
    - Tam adres de verilebilir: `http://192.168.1.152/cgi-bin/mjpg/video.cgi`
    - RTSP (`rtsp://...`) için bilgisayarda `ffmpeg` kurulu olmalıdır.
  - **Kullanıcı / Şifre:** mevcut şifreyle kullanıcı adı ve şifre değiştirilir. Şifre değişince diğer oturumlar kapanır.
- Ayarlar `veri/ayarlar.json` dosyasında tutulur (şifre PBKDF2 ile özetlenmiş halde). Şifre unutulursa
  bu dosya silinip uygulama yeniden başlatılınca giriş tekrar admin/admin olur, ama kayıtlı kameralar da silinir.

## Kullanım

1. Üstteki listeden kamerayı seçip **Kamerayı başlat**'a basın.
   Kamera adresle de açılabilir: `http://localhost:8080/?kamera=Hat 1` (kamera adı ya da IP).
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
