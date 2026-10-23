; Dünyatek Hızlı Barkod Okuma Sistemi - Inno Setup kurulum betiği
; paketle.bat tarafından derlenir; çıktı: paketleme\cikti\DunyatekBarkod_Kurulum_<sürüm>.exe

#define SURUM "1.0.0"
#define AD "Dünyatek Hızlı Barkod Okuma Sistemi"
#define EXE "DunyatekBarkod.exe"

[Setup]
AppId={{6E1C2B57-3F4D-4C1E-9A0B-D7A5B1C8E214}
AppName={#AD}
AppVersion={#SURUM}
AppVerName={#AD} {#SURUM}
AppPublisher=Dünyatek Bilişim Teknolojileri
DefaultDirName={autopf}\Dunyatek\HizliBarkod
DefaultGroupName=Dünyatek
DisableProgramGroupPage=yes
OutputDir=cikti
OutputBaseFilename=DunyatekBarkod_Kurulum_{#SURUM}
SetupIconFile=..\gorsel\ikon.ico
UninstallDisplayIcon={app}\{#EXE}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=force

[Languages]
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "masaustu"; Description: "Masaüstüne kısayol oluştur"
Name: "otomatik"; Description: "Windows açılınca otomatik başlat"
Name: "guvenlikduvari"; Description: "Ağdaki diğer bilgisayarlar erişebilsin (güvenlik duvarında 8080 portunu aç)"; Flags: unchecked

[Dirs]
; Ayarlar ve günlük: tüm kullanıcılar yazabilsin, kaldırınca silinmesin
Name: "{commonappdata}\Dunyatek\HizliBarkod"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "cikti\dist\{#EXE}"; DestDir: "{app}"; Flags: ignoreversion
Source: "cikti\ffmpeg\ffmpeg.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
Source: "cikti\ffmpeg\LICENSE"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AD}"; Filename: "{app}\{#EXE}"; Parameters: "--ac"
Name: "{group}\{#AD} kaldır"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AD}"; Filename: "{app}\{#EXE}"; Parameters: "--ac"; Tasks: masaustu
Name: "{commonstartup}\{#AD}"; Filename: "{app}\{#EXE}"; Tasks: otomatik

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Dunyatek Hizli Barkod"" dir=in action=allow protocol=TCP localport=8080"; Flags: runhidden; Tasks: guvenlikduvari
Filename: "{app}\{#EXE}"; Parameters: "--ac"; Description: "Uygulamayı şimdi başlat"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#EXE}"; Flags: runhidden; RunOnceId: "Kapat"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Dunyatek Hizli Barkod"""; Flags: runhidden; RunOnceId: "GuvenlikDuvari"

[Messages]
tr.WelcomeLabel2=Bu sihirbaz bilgisayarınıza [name/ver] uygulamasını kuracak.%n%nİlk giriş: kullanıcı adı admin, şifre admin (ilk girişte değiştirmeniz istenir).
