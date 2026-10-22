# Bant QR Okuyucu

Hızlı akan banttaki poşetlerin QR kodlarını kameradan sürekli okuyan, her kodu doğrulayan
(geçersizse ekranda büyük **YANLIŞ** uyarısı ve ses) ve bant durduğunda son okunan barkodu
gösterip etiketini yazdıran tarayıcı uygulaması.

## Çalıştırma

1. Depoyu indirin, `index.html` dosyasını Chrome veya Edge ile açın (internet gerekmez, kütüphaneler `vendor/` içinde).
2. **Kamerayı başlat**'a basın, kamera seçin.
3. **Ayarlar** bölümünden doğrulama yöntemini seçin:
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
