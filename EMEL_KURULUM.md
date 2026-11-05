# Diva CANLI — Emel Neural Türkçe kadın sesi

Bu değişiklik, mevcut **DivaShow** uygulamasına isteğe bağlı `tr-TR-EmelNeural` sesini ekler. Orijinal `index.html` değiştirilmez: altı mimik, TikTok yorumları ve hediye tepkileri korunur. `app.py` sayfayı servis ederken ses seçim panelini ekler.

## Render üzerinde açma

1. GitHub değişikliklerini `main` dalına birleştir. Render otomatik dağıtım açıksa yeni sürümü kendi yükler. Değilse Render > **Manual Deploy > Deploy latest commit** seç.
2. Render `requirements.txt` üzerinden `edge-tts` paketini yükler. **Yeni bir API anahtarı eklenmez.** Var olan `DIVA_PIN` aynen kalır.
3. Render durumu **Live** olduğunda kendi `https://...onrender.com` adresini aç. Eski görünüm çıkarsa sayfayı yenile.
4. **⚙️ Ayarlar → TikTok Canlı Bağlantısı** bölümünde mevcut `DIVA_PIN` kodunu gir.
5. Aşağıdaki **Diva Ses Stüdyosu → Ses kaynağı → ☁️ Emel Neural · Türkçe kadın (sunucu)** seç.
6. **▶ Seçili sesi dinle** veya **🔊 Sesi aç / Test et** düğmesine bas. Telefonun medya sesini yükselt.
7. Ses çalışıyorsa TikTok canlıya bağlanıp yorum/hediye testine devam et.

## Önemli sınırlar

- `edge-tts`, Microsoft Edge'in seslendirme servisine erişen **resmî olmayan** açık kaynaklı bir istemcidir. Her zaman ücretsiz/erişilebilir olacağı garanti değildir. Servis `403`, `503` veya bağlantı zaman aşımı verebilir.
- Emel çalışmazsa site telefonun seslendirmesini dener. Telefonda Türkçe paket yoksa yedek ses yine İngilizce gelebilir.
- Telefonun tarayıcısı ses oynatmayı engellerse sitede bir kez **Sesi aç / Test et** düğmesine basmak gerekir.
- `DIVA_PIN` kodunu GitHub dosyalarına koyma ve kimseyle paylaşma.
- Render Free hizmeti uykuya geçebilir; canlı yayınlarda sesin kesintisiz çalışması garanti edilmez.
- Bu değişiklik yapay zekâ cevap üretimini eklemez; mevcut hazır cevap sistemi aynen devam eder.

## Neler değişti?

- `app.py`: `/api/tts` uç noktası ve `index.html` için ses stüdyosu ekleme.
- `diva_voice.py`: Emel Neural MP3 üretimi ve tarayıcı oynatıcı kodu; hatada telefon sesine geçiş.
- `requirements.txt`: `edge-tts` bağımlılığı.

## Güvenli test bilgisi

Yerel kod testlerinde PIN kontrolü, MP3 yanıtı ve mobil tarayıcıda ses istekleri **sahte MP3** ile denendi. Gerçek Microsoft ses sunucusuna bu test ortamında erişim sağlanamadığı için o bağlantı Render'da doğrulanmalıdır.
