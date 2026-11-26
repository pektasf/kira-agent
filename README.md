# Kira Agent – Kurulum

## 1) Telegram botu
1. Telegram'da @BotFather → /newbot → token'ı al.
2. Yeni botuna bir mesaj yaz, sonra tarayıcıda aç:
   https://api.telegram.org/bot<TOKEN>/getUpdates  → "chat":{"id":...} değeri CHAT_ID'dir.

## 2) Çalıştırma (ücretsiz, bilgisayarın kapalıyken de çalışır)
1. GitHub'da **private** repo aç, bu klasörün içeriğini yükle.
2. Settings → Secrets and variables → Actions: `TELEGRAM_TOKEN` ve `TELEGRAM_CHAT_ID` ekle.
3. Actions sekmesinden "kira-agent" → Run workflow ile ilk testi yap. Sonra saat başı kendi çalışır.

Yerelde test: `pip install -r requirements.txt` ardından
`TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... python agent.py`

## 3) Ayarlar
- `config.yaml`: bütçe, bina yaşı, mahalle kademeleri, puan eşikleri.
- Kaynak URL'leri: sitede filtreni (Kağıthane, 2+1, eşyasız, fiyat) uygula, adres çubuğundaki URL'yi `url:` alanına yapıştır.

## Bilinen sınırlar
- Sahibinden Cloudflare ile bot trafiğini engeller; 403 alırsan o kaynak atlanır.
  Alternatif: Sahibinden'de kendi arama alarmını kur (e-posta bildirimi), agent'a sonra ekleriz.
- Site HTML'i değişirse ayrıştırma bozulabilir; log'da "0 ilan" görürsen link_pattern'i güncelle.
- Puanlama ilan kartındaki metne dayanır; kartta olmayan bilgi (bina yaşı, asansör) "belirtilmemiş" diye işaretlenir, eleme yapılmaz.

## Web arayüzü (GitHub Pages)
1. Repo **Public** olmalı (ücretsiz Pages için). Gizli bilgi yok; token'lar Secrets'ta. Private kalsın istersen Cloudflare Pages kullan (docs/ klasörü).
2. Settings → Pages → Branch: main, Folder: /docs. Adres: https://KULLANICI.github.io/REPO/
3. GitHub → Settings → Developer settings → Fine-grained token: sadece bu repo, izinler: *Contents: Read/Write* ve *Actions: Read/Write*. Token'ı arayüzdeki alana yapıştır.
4. Arayüzden ayarları değiştirip kaydet; sonraki taramada geçerli olur. "Şimdi tara" anında çalıştırır.
Not: Public repoda 60 gün hiç etkinlik olmazsa zamanlanmış çalışma durur; bot her çalışmada commit attığı için normalde sorun olmaz, durursa Actions'tan tekrar aç.
