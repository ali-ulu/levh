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
- Yeni MCP tool eklerken `server/tools/profiles.py` içindeki `TOOL_TIERS`'a da ekle ve sabit tool sayılarını güncelle; `tests/test_mcp_profiles.py` kayıt ile katman haritasının birebir örtüşmesini kilitliyor.
- `server/api.py` içindeki `_engine` modül global'i; testte `SQLITE_DB_PATH` değiştirmek yetmez, `api._engine`/`api._initialized`/`engine_provider.set_engine(None)` sıfırlanmalı.
- aiosqlite bağlantısı onu açan event loop'a bağlıdır; `asyncio.run(initialize())` ile açıp sonra ayrı bir loop çalıştırma — FastMCP `lifespan` kullan.
- CLI/package sürümünü ikinci bir hard-coded semver literal'iyle çoğaltma; kullanıcıya gösterilen sürümü package metadata'sından türet ve release testinde entrypoint sözleşmesini kilitle.

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

## 2026-08-13 — levh / MCP tool profilleri

- HATA: Guard tool'ları eklendiğinde `test_mcp_profiles.py` iki testte düştü; tool kodunda hata yoktu.
- KÖK NEDEN: `profiles.py` her tool'u bir katmana atıyor ve testler hem katman haritasının kayıtla birebir örtüşmesini hem de sabit tool sayılarını (59) doğruluyor. Yeni tool bu haritaya eklenmeden kaydedilince kayıt ile harita ayrıştı.
- KURAL: Yeni MCP tool eklerken `TOOL_TIERS`'a katmanıyla birlikte ekle; `profiles.py`, `configs.py`, `test_mcp_profiles.py`, `test_onboarding_*.py` içindeki sabit sayıları güncelle. Katman seçimi bir tasarım kararıdır: varsayılan profil `work`, yani `work`'te olmayan tool pratikte görünmez.
- KAPSAM: `server/tools/register.py` ile birlikte her yeni tool.

## 2026-08-13 — levh / üretilen MCP sunucusunun event loop'u

- HATA: `levh mcp init --with-memory` ile üretilen sunucu ilk tool çağrısında takılıyordu; dosyalar doğru yazılmıştı.
- KÖK NEDEN: Üretilen `main()` önce `asyncio.run(engine.initialize())` çağırıyor, sonra `mcp.run()` kendi event loop'unu açıyordu. aiosqlite bağlantısı ilk (kapanmış) loop'a bağlı kaldığı için her sorgu asılı kalıyor.
- KURAL: Async bir kaynağı sunucudan önce ayrı bir `asyncio.run()` içinde açma; FastMCP `lifespan` kullan (`server/mcp_stdio.py` deseni). Kod üreten bir özellikte, üretilen kodu import edip gerçekten bir tool çağıran test yaz — dosya varlığını doğrulamak yetmez.
- KAPSAM: `server/scaffold.py` ve async kaynak açan tüm giriş noktaları.

## 2026-08-13 — levh / public demo modunda arama

- HATA: Public demo'da hafıza araması tamamen çalışmıyordu; testi olmadığı için fark edilmemişti.
- KÖK NEDEN: Demo koruması tüm mutating HTTP metodlarını engelliyor, `recall` ise sorguyu taşımak için POST kullanıyor. Aynı dosyadaki WebSocket yolu `recall`'a açıkça izin veriyordu — iki yarı çelişiyordu.
- KURAL: "Yazma" kararını HTTP metoduna göre verme; POST kullanan okuma uçları için açık bir izin listesi tut ve yan etkiyi (reinforcement) sunucu tarafında zorla kapat, istek gövdesine güvenme. Güvenlik sınırlarını manuel checklist ile değil testle doğrula.
- KAPSAM: `server/api.py` demo/yetki middleware'leri.

## 2026-08-13 — levh / CLI sürüm drift'i (#46)

- HATA: Paket metadata'sı `2.28.0` iken kullanıcı-facing `levh --version` yolu `server.cli` içindeki ikinci bir sabit nedeniyle `2.27.2` bildirebiliyordu.
- KÖK NEDEN: Release pipeline canonical package sürümünü güncellese de CLI aynı semver'i ayrı bir literal olarak tutuyordu; yeni release'te bu ikinci kaynak güncellenmedi ve mevcut consistency testi console entrypoint'i doğrulamıyordu.
- KURAL: Sürüm bilgisini çoğaltma; installed CLI sürümünü `importlib.metadata.version("levh")` üzerinden package metadata'sından türet ve entrypoint hedefini regression testiyle kilitle.
- KAPSAM: `pyproject.toml`, `server/entrypoint.py`, CLI sürüm raporlama ve release testleri.

## 2026-08-13 — levh / bölme sonrası sürüm literali

- HATA: 2.29.0 bump'ından sonra `/api/config` hâlâ 2.28.0 bildiriyordu.
- KÖK NEDEN: `server/api.py` bölünürken sürüm sabiti `routes/deps.py`'ye ikinci bir literal olarak kopyalanmıştı. `scripts/release.py` yalnızca kendi `VERSION_SITES` listesindeki dört yeri yeniden yazıyor, yeni kopyayı bilmiyordu — #46'daki drift'in aynısı, bu kez refaktörün ürettiği.
- KURAL: Refaktörde sabitleri çoğaltma; sürümü `levh_version()` üzerinden package metadata'sından türet. Bölme sonrası `python scripts/release.py --check` çalıştır.
- KAPSAM: `server/routes/deps.py`, sürüm bildiren tüm yollar.

## 2026-08-14 — levh / header health polling

- HATA: Sunucu access log'u `GET /api/health 200` satırlarıyla doluyordu; her satır farklı bir efemeral porttan geliyordu ve başka hiçbir isteği okumak mümkün değildi.
- KÖK NEDEN: `frontend/src/components/layout/header.tsx` online rozeti için 15 saniyede bir koşulsuz `setInterval` çalıştırıyordu — sekme arka planda unutulsa bile. Farklı port ise ayrı bir hata değil, aynı olgunun sonucu: uvicorn'un `timeout_keep_alive` varsayılanı 5 sn, polling aralığı 15 sn olduğu için bağlantı her seferinde boştayken kapanıyor ve keep-alive hiç devreye girmiyor.
- KURAL: Süreli polling ekliyorsan `document.visibilityState` ile kapıla ve `visibilitychange`'e de bağla, yoksa görünmeyen sekme sonsuza kadar istek üretir. Access log gürültüsünü filtrelerken yalnızca başarılı (`<400`) yanıtları düşür — başarısız health çağrısı görülmesi gereken tek satırdır.
- KAPSAM: `frontend/src/components/layout/header.tsx`, `server/core/log_filters.py`, süreli poll eden tüm bileşenler.
