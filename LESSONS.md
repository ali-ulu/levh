# LESSONS

Hatalardan çıkan kalıcı dersler. Her görev öncesi ilgili anahtar kelimeyle aranır, her RCA sonrası güncellenir.

## KALICI KURALLAR

- Yeni bir CLI alt komutu eklerken mevcut `sub.add_parser(...)` bloklarının üzerine yazma; parser ile `main()` içindeki `elif args.command == ...` dispatch'i daima birlikte kontrol et.
- MCP resource URI template'lerinde query string (`?key={value}`) kullanma; parametreler yol segmenti olmalı.
- Kullanıcıdan gelen ISO tarihleri karşılaştırmadan önce timezone-aware hale getir (naive ise UTC varsay).
- Bir hata mesajını UI'da göstermeden önce string'e indir; API hata gövdesinden `detail` alanını çıkar, ham objeyi interpolasyona sokma.
- Repoda birden fazla Tailwind/PostCSS config dosyası bırakma; ikinci config sessizce ilkini gölgeleyip tema token'larını düşürür.
- Harici bir aracın config formatını varsayma; her istemci için gerçek dokümantasyonundan doğrula (JSON şemaları bile aynı değil, Codex TOML / Hermes YAML kullanır).
- Tarayıcı bir dosyanın mutlak yolunu asla vermez; sunucunun okuyacağı dosya için yol input'u değil upload uçtan uca akışı tasarla.
- FastAPI `TestClient` kendini loopback dışı bir istemci olarak sunar; loopback sınırı olan endpoint'leri test ederken `TestClient(app, client=("127.0.0.1", <port>))` kullan.

## 2026-08-12 — levh / server.cli

- HATA: `levh export-full` komutu tamamen kayboldu; argparse `invalid choice: 'export-full'` veriyordu.
- KÖK NEDEN: `continue` parser'ı eklenirken `export-full` parser bloğunun üzerine yazıldı, dispatch ve `cmd_export_full` yerinde kaldığı için hata sessiz kaldı.
- KURAL: CLI alt komutu eklerken/silerken `python -m server.cli <komut> --help` ile tüm mevcut komutları doğrula; parser bloğunu asla üzerine yazma.
- KAPSAM: `server/cli.py`, tüm alt komutlar.

## 2026-08-12 — levh / memory_engine.get_continuity_context

- HATA: `levh continue` hiçbir zaman session göstermiyordu; `--since` ise bare date verildiğinde `TypeError` ile çöküyordu.
- KÖK NEDEN: Session filtresi `metadata["project"]` bekliyordu ama hiçbir kod yolu bu alanı yazmıyor; `--since` filtresi `sessions[:limit]` slice'ından sonra uygulanıyordu ve naive/aware datetime karşılaştırması yalnızca `ValueError` ile korunuyordu.
- KURAL: Bir alan üzerinden filtre yazmadan önce o alanın gerçekten doldurulduğunu grep ile doğrula; filtreleri daima slice/limit işleminden ÖNCE uygula; kullanıcı tarihlerini UTC-aware'e normalize et.
- KAPSAM: `server/core/memory_engine.py`, session/memory filtreleme yapan tüm metodlar.

## 2026-08-12 — levh / server.tools.continuity

- HATA: `levh://session/{project}/continuity?task={task}` resource'u kaydoluyor ama okunduğunda `ValueError: Unknown resource` veriyordu.
- KÖK NEDEN: FastMCP template'i regex'e çevirirken `?` karakterini escape etmiyor; `continuity?` deseninde `y` opsiyonel quantifier'a dönüşüp URI hiçbir zaman eşleşmiyor.
- KURAL: MCP resource template parametrelerini yol segmenti olarak tanımla (`.../continuity/{task}`), query string olarak değil; her yeni resource'u `read_resource()` ile fiilen okuyarak doğrula.
- KAPSAM: `server/tools/*.py` içindeki tüm `@mcp.resource` tanımları.

## 2026-08-12 — levh / dashboard hata gösterimi (#32)

- HATA: Başarısız API çağrıları dashboard'da `[object Object]` olarak görünüyordu; gerçek red sebebi kullanıcıya hiç ulaşmıyordu.
- KÖK NEDEN: Hata nesnesi doğrudan template string'e interpole ediliyordu; FastAPI'nin `HTTPException` gövdesindeki `detail` alanı hiç okunmuyordu.
- KURAL: Hata mesajını göstermeden önce string'e indir ve API gövdesinden `detail` alanını çıkar.
- KAPSAM: `frontend/src/lib/api.ts` ve hata mesajı gösteren tüm sayfalar.

## 2026-08-12 — levh / Tailwind config gölgelemesi (#34)

- HATA: Popover/dropdown menüler şeffaf render ediliyor, arkasındaki içerik okunuyordu.
- KÖK NEDEN: Repoda iki Tailwind config dosyası vardı; ikincisi ilkini gölgeleyerek tema token'larını (popover arka planı dahil) düşürüyordu. Build hata vermediği için sessiz kaldı.
- KURAL: Tek bir Tailwind/PostCSS config tut; tema bozulmalarında önce config dosyalarının tekilliğini doğrula.
- KAPSAM: `frontend/` build konfigürasyonu.

## 2026-08-12 — levh / istemci config üreticisi (#34)

- HATA: opencode, Codex ve Hermes için üretilen MCP config'i bu araçlar tarafından sessizce yok sayılıyordu.
- KÖK NEDEN: Üretici tüm istemcilerin Claude Desktop'ın JSON şemasını okuduğunu varsayıyordu; opencode farklı bir JSON şeması (`mcp` anahtarı, argv dizisi), Codex TOML, Hermes YAML kullanıyor.
- KURAL: Yeni istemci eklerken config formatını dokümantasyonundan doğrula ve üretilen çıktıyı hedef formatın parser'ıyla (`tomllib`, PyYAML, `json`) parse ederek test et.
- KAPSAM: `server/tools/` içindeki config üretimi ve istemci listesi.

## 2026-08-12 — levh / connector dosya yükleme (#33)

- HATA: Connector import'u tarayıcıda mutlak dosya yolu yazılmasını istiyordu; kullanıcı dosyanın nasıl yükleneceğini anlayamıyordu.
- KÖK NEDEN: Tarayıcı bir dosyanın içeriğini verir ama mutlak yolunu asla vermez; yol tabanlı bir input tarayıcıdan doldurulamaz. Aynı sayfadaki JSON import'u gerçek dosya seçici kullandığı için iki farklı zihinsel model yan yana duruyordu.
- KURAL: Sunucunun okuyacağı bir dosya için yol input'u değil upload akışı tasarla; sunucu dosyayı yazıp yolu geri döndürsün. Yüklenen dosya adını basename'e indir ve karakter kümesini kısıtla.
- KAPSAM: `server/api.py` upload uçları, `frontend/src/app/settings/page.tsx` connector formu.

## 2026-08-12 — levh / TestClient loopback sınırı

- HATA: Yeni upload endpoint'inin tüm testleri `401 remote access requires LEVH_TOKEN` ile düştü; endpoint'te sorun yoktu.
- KÖK NEDEN: `RemoteAccessBoundaryMiddleware` token yokken yalnızca loopback'e izin veriyor; FastAPI `TestClient` varsayılan olarak kendini `testclient` host'u ile sunuyor ve bu loopback sayılmıyor.
- KURAL: Loopback sınırı olan endpoint'leri test ederken `TestClient(app, client=("127.0.0.1", <port>))` ver ya da httpx `ASGITransport` kullan.
- KAPSAM: `tests/` içindeki tüm API testleri.
