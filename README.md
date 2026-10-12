<img width="100%" src="https://capsule-render.vercel.app/api?type=waving&height=200&section=header&color=0:009688,100:3776AB&text=AniFrame&fontSize=56&fontColor=FFFFFF&fontAlignY=34&animation=fadeIn&desc=AniFrame%3A%20%C4%B0nternet%20videosunu%20kolayca%20alg%C4%B1la%2C%20filtrele%2C%20indir%20ve%20izle&descSize=18&descAlignY=55" alt="AniFrame" />

<div align="center">

</div>

AniFrame, kullanıcılar için internet videosunu kolayca algılamak, filtrelemek ve indirmek amacıyla tasarlanmış bir projedir. Video izleme ve indirme sürecini basitleştirmek için 7 katmanlı video algılama, yumusatılmış ad/takipçi filtresi, otomatik yeniden bağlanma, chrome.storage wrapper ve daha fazlası sunuyor.

<p align="center">
  <img src="https://skillicons.dev/icons?i=py,fastapi&perline=2" height="48" alt="python, fastapi" />
</p>

<details>
<summary><b>📋 İçindekiler</b></summary>

- [✨ Özellikler](#-özellikler)
- [⚙️ Nasıl Çalışır](#-nasıl-çalışır)
- [📦 Kurulum](#-kurulum)
- [🚀 Kullanım](#-kullanım)
- [🔌 API Referansı](#-api-referansı)
- [📁 Proje Yapısı](#-proje-yapısı)
- [🤝 Katkıda Bulunma](#-katkıda-bulunma)
- [👤 Geliştirici](#-geliştirici)

</details>

## ✨ Özellikler

### 🔍 Video Algılama ve İşleme

- 7 katmanlı video algılama sistemi (DOM, Shadow DOM, Network, Player API, MutationObserver, Meta, Blob/MSE)
- Yumusatılmış ad/takipçi filtresi
- Streaming CDN'leri haksiz yere filtrelenmesin
- REST API ve WebSocket istemcisi
- Otomatik yeniden bağlanma
- Chrome.storage wrapper ve service worker restart sonrası recovery
- Videoların indirilmesini sağlar ve dosya yollarını ayarlayabilir
- Videoların analizini sağlar ve analiz sonuçlarını görüntüler
- Tarayıcı uzantılarını yükler ve kaldırır ve tarayıcıları otomatik olarak tarar
- Uygulamanın dilini Türkçe veya İngilizce olarak değiştirebilir

### 📹 Video Çözümleyici

- YouTube, Vimeo, Dailymotion, Twitter/X, Instagram, TikTok ve daha fazlası gibi videoları çözümler
- Videoları çözümlerken videonun formatını ve kalitesini de göz önüne alır
- Multi-page crawling yöntemiyle oynatma listelerini çözümler
- Oynatma listelerini çözümlerken videoların tüm linklerini toplar
- Jina Reader entegrasyonu kullanılarak web sayfalarını temiz metin olarak çözümler
- Video URL'lerini de tespit eder
- Agent-Reach Bilibili, Social ve Web API'lerini çözümler
- Gerçek API'lerini kullanır
- Cloudflare Turnstile bypass, fingerprint spoofing ve session management tekniklerini kullanarak videoları çözümler
- Sayfalardan video URL'lerini çıkarır

### 🚀 Sunucu İşlemleri

- Sunucuyu başlatır
- Sunucuyu durdurur
- Sunucuyu yeniler
- Sunucu günlüklerini temizler
- Sunucu ayarlarını çalıştırır
- Sunucu ayarlarını yapılandırır
- Sunucuyu tarayıcıda açar


## ⚙️ Nasıl Çalışır

```mermaid
flowchart LR
  S0["Video Algılama"]
  S1["Video Filtreleme"]
  S2["Video Çözümleme"]
  S3["Video İndirme"]
  S4["Video İzleme"]
  S0 --> S1
  S1 --> S2
  S2 --> S3
  S3 --> S4
```

1. **Video Algılama** — Kullanıcı internet videosunu algılamak için AniFrame'i başlatır
2. **Video Filtreleme** — AniFrame, video URL'sini ve ad/takipçi bilgilerini filtreler
3. **Video Çözümleme** — AniFrame, videonun formatını ve kalitesini göz önüne alarak videoyu çözümler
4. **Video İndirme** — AniFrame, videoyu indirir ve dosya yollarını ayarlar
5. **Video İzleme** — Kullanıcı videoyu izler ve AniFrame'i kapatır

| Bileşen | Görev |
| --- | --- |
| `extension` | Kullanıcı video algılama işlemini başlatabilir, indirmeyi başlatabilir ve ayarları değiştirebilir |
| `backend/templates` | AniFrame Video Downloader uygulamasının ana sayfasıdır ve kullanıcılara videoların indirilmesini sağlar |
| `backend/extractors` | Farklı videoları ve oynatma listelerini çözümleyerek videoları ve oynatma listeleri elde etmek için farklı videoları ve oynatma listelerini çözümler |
| `root` | AniFrame projesi için sunucu paneli işlemlerini yönetir ve sunucuyu başlatır, durdurur veya yeniler |

## 📦 Kurulum

**Gereksinimler:** Python 3.10+

```bash
# Repoyu klonlayın
git clone https://github.com/Burakcnz/AniFrame.git
cd AniFrame

# Sanal ortam oluşturun
python -m venv .venv

# Sanal ortamı etkinleştirin (macOS/Linux: source .venv/bin/activate)
.venv\Scripts\activate

# Bağımlılıkları yükleyin
pip install -r requirements.txt
```

## 🚀 Kullanım

```bash
python server_panel.py
```

Uygulama varsayılan olarak <http://localhost:8000> adresinde açılır. Windows'ta `panel.bat` dosyasına çift tıklayarak da başlatabilirsiniz.

#### Video Algılama ve İndirme

1. AniFrame'i başlatmak için 'extension' bileşenini kullanın
2. Video URL'sini girin veya videoyu izlemek için açılan sayfadan algılayın
3. Videoyu filtreleyin ve videonun formatını ve kalitesini seçin
4. Videoyu indirmeyi başlatın
5. Indirme işlemi tamamlandığında videoyu izleyin

#### Video Çözümleme

1. Videonun URL'sini veya oynatma listesi sayfasını girin
2. extract() fonksiyonunu kullanarak videoyu çözümleyin
3. Oynatma listelerini çözümlemek için Scrapling Spider Extractor'ı kullanın
4. Web sayfalarını çözümlemek için Agent-Reach Web Extractor'ı kullanın

#### Sunucu İşlemleri

1. Sunucuyu başlatmak için 'root' bileşenini kullanın
2. Sunucuyu durdurmak veya yenilemek için sunucu işlemleri menüsünü kullanın
3. Sunucu ayarlarını çalıştırmak veya yapılandırmak için sunucu ayarları menüsünü kullanın
4. Sunucuyu tarayıcıda açmak için sunucu erişimi menüsünü kullanın

## 🔌 API Referansı

Toplam **44** uç nokta.

<details>
<summary><b>backend</b> (44)</summary>

| Yöntem | Yol | Açıklama |
| --- | --- | --- |
| `GET` | `/` | Ana sayfayı görüntüler |
| `GET` | `/api/videos` |  |
| `GET` | `/api/play-local/{video_id}` |  |
| `GET` | `/api/video/{video_id}` |  |
| `HEAD` | `/api/video/{video_id}` |  |
| `GET` | `/api/thumbnail/{video_id}` |  |
| `POST` | `/api/analyze` |  |
| `POST` | `/api/download` |  |
| `POST` | `/api/download/batch` |  |
| `GET` | `/api/queue` |  |
| `DELETE` | `/api/queue/{task_id}` |  |
| `GET` | `/api/stats` |  |
| `GET` | `/api/history` |  |
| `DELETE` | `/api/history` |  |
| `GET` | `/api/folders` |  |
| `POST` | `/api/folders` |  |
| `DELETE` | `/api/folders/{folder_id}` |  |
| `POST` | `/api/folders/scan` |  |
| `GET` | `/api/settings` |  |
| `POST` | `/api/settings` |  |
| `POST` | `/api/extension/toggle` |  |
| `GET` | `/api/extension/status` |  |
| `GET` | `/api/browsers` |  |
| `POST` | `/api/extension/install` |  |
| `POST` | `/api/extension/uninstall` |  |
| `POST` | `/api/extension/detect` |  |
| `GET` | `/api/integration/ping` | Health check for browser extension connector. |
| `GET` | `/api/integration/sync` | Sync endpoint - extension periodically polls this. |
| `POST` | `/api/integration/video` | Receive video detection from integration connector. |
| `GET` | `/api/integration/videos` | Get detected videos for a tab. |
| `GET` | `/api/host-settings` |  |
| `GET` | `/api/host-settings/{hostname}` |  |
| `POST` | `/api/host-settings/{hostname}` |  |
| `GET` | `/api/proxy` |  |
| `POST` | `/api/proxy` |  |
| `DELETE` | `/api/proxy/{pattern}` |  |
| `POST` | `/api/download/priority` |  |
| `GET` | `/api/speed-limit` | Universal extractor using priority chain (yt-dlp -> Agent-Reach -> Scrapling -> browser). |
| `POST` | `/api/speed-limit` | Universal extractor using priority chain (yt-dlp -> Agent-Reach -> Scrapling -> browser). |
| `POST` | `/api/extract/universal` | Universal extractor using priority chain (yt-dlp -> Agent-Reach -> Scrapling -> browser). |
| `POST` | `/api/extract/stealth` | Scrapling Stealth extractor (Cloudflare bypass, anti-bot). |
| `POST` | `/api/agent-reach/status` | Agent-Reach kanal durumu (bilibili, twitter, web, youtube, ...). |
| `GET` | `/api/extract/list` | List all available extractors. |
| `WS` | `/ws` |  |

</details>

## 📁 Proje Yapısı

```text
├── backend/
│   ├── extractors/
│   ├── static/
│   ├── templates/
│   ├── __init__.py
│   ├── browser_extractor.py
│   ├── browser_installer.py
│   ├── database.py
│   ├── downloader.py
│   ├── host_settings.py
│   ├── integration.py
│   ├── main.py
│   ├── manifest_parser.py
│   ├── metadata.py
│   ├── native_downloader.py
│   └── …
├── downloads/
│   └── video/
├── extension/  # AniFrame'in 'extension' bileşeninin 'GROUP' alt bileşeninin sorumlu…
│   ├── icons/
│   ├── lib/
│   ├── options/
│   ├── panels/
│   ├── popup/
│   ├── background.js
│   ├── content.js
│   └── manifest.json
├── aniframe.db
├── native_host.bat
├── panel_settings.json
├── panel.bat
├── panel.sh
├── project_info.txt
├── requirements.txt
└── server_panel.py
```

## 🤝 Katkıda Bulunma

Katkılarınızı bekliyoruz! Şu adımları izleyin:

1. Repoyu fork'layın
2. Yeni bir dal oluşturun (`git checkout -b ozellik/yeni-ozellik`)
3. Değişikliklerinizi commit'leyin (`git commit -m 'Yeni özellik ekle'`)
4. Dalınızı push'layın (`git push origin ozellik/yeni-ozellik`)
5. Bir Pull Request açın

## 👤 Geliştirici

<p align="center">
  <a href="https://github.com/Burakcnz"><img src="https://github.com/Burakcnz.png?size=120" width="110" alt="Burak Can Zengin" /></a>
  <br />
  <b>Burak Can Zengin</b> · <a href="https://github.com/Burakcnz">@Burakcnz</a>
</p>

<p align="center"><a href="https://github.com/Burakcnz/AniFrame">⭐ Projeyi beğendiyseniz yıldız vermeyi unutmayın!</a></p>

<img width="100%" src="https://capsule-render.vercel.app/api?type=waving&height=110&section=footer&color=0:009688,100:3776AB" alt="" />
