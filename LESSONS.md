# LESSONS

Hatalardan çıkan kalıcı dersler. Her görev öncesi ilgili anahtar kelimeyle aranır, her RCA sonrası güncellenir.

## KALICI KURALLAR

- Yeni bir CLI alt komutu eklerken mevcut `sub.add_parser(...)` bloklarının üzerine yazma; parser ile `main()` içindeki `elif args.command == ...` dispatch'i daima birlikte kontrol et.
- MCP resource URI template'lerinde query string (`?key={value}`) kullanma; parametreler yol segmenti olmalı.
- Kullanıcıdan gelen ISO tarihleri karşılaştırmadan önce timezone-aware hale getir (naive ise UTC varsay).

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
