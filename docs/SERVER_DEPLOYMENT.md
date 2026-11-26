# Sunucu kurulumu

Kira Agent 2.0, Docker ile sürekli çalışan tek servis olarak dağıtılabilir.

## Gereksinimler
- Docker Engine ve Docker Compose plugin'i bulunan bir Linux sunucu
- Telegram bot token'ı ve chat ID (bildirim isteniyorsa)
- Sunucuda 8000/TCP portu veya ters proxy üzerinden HTTPS

## Kurulum
1. Depoyu sunucuya klonlayın.
2. `.env.example` dosyasını `.env` olarak kopyalayın.
3. `.env` içine Telegram bilgilerini girin.
4. `docker compose up -d --build` komutunu çalıştırın.
5. `http://SUNUCU_ADRESI:8000/api/health` adresinin `{"status":"ok"}` döndürdüğünü kontrol edin.
6. Web panelini `http://SUNUCU_ADRESI:8000` adresinden açın.

## Kalıcılık ve çalışma
- `kira_data` Docker volume'u ayarları, sonuçları, görülen ilanları, son tarama zamanını ve tarama geçmişi SQLite veritabanını saklar.
- Servis yeniden başladığında veriler korunur ve tarama zamanlayıcısı yeniden kurulur.
- İlk tarama servis başladıktan yaklaşık 10 saniye sonra başlar.
- Tarama sıklığı web panelindeki ayarlardan değiştirilebilir (15-1440 dakika).
- `restart: unless-stopped` sunucu yeniden başlatıldığında servisi tekrar ayağa kaldırır.

## Kontrol komutları
```bash
docker compose ps
docker compose logs -f kira-agent
docker compose restart kira-agent
docker compose down
```

Verileri silmeden durdurmak için `docker compose down` kullanın. `docker compose down -v` kalıcı veri volume'unu da siler; dikkatli kullanın.

## API
- `GET /api/health`: servis sağlık kontrolü
- `GET /api/status`: zamanlayıcı ve son tarama durumu
- `GET /api/results`: ilan sonuçları
- `GET /api/settings`: aktif arama ayarları
- `PUT /api/settings`: ayarları kaydetme
- `POST /api/scan`: manuel tarama kuyruğa alma

Bu aşama sunucu çalışma katmanını ve temel API'yi ekler. Yeni emlak sitesi bağlayıcıları, daha kapsamlı ilan veritabanı ve harita/karşılaştırma ekranları sonraki aşamalardır.
