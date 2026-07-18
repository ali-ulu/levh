# TASK-00A1 — Cross-Process Context Coherence

## Karar

```text
P0-1_CROSS_PROCESS_CONTEXT_COHERENCE:
CONFIRMED
```

Aynı SQLite veritabanına bağlı iki canlı LEVH process'i arasında SQLite-backed `get` okumaları güncel kalırken, `recall` process-local vector cache üzerinden stale sonuç üretmektedir. Sorun create, update ve delete işlemlerinde iki yönde de yeniden üretildi. Observer process yeniden başlatılınca SQLite'tan vector cache yeniden yüklenmekte ve doğru durum geri gelmektedir.

Bu görev yalnız reproduction ve characterization yapmıştır. Ürün koduna düzeltme uygulanmamıştır.

## Kilitli ortam

- Worktree: `<AUDIT_WORKTREE>`
- Branch: `audit/groundtruth-v2`
- HEAD: `3a97ae7177c128e5484434d76828751330149fc3`
- Python: izole `.venv`, Python 3.11.9
- LEVH: editable `2.27.2`, bu checkout'tan
- Embedder: `hash`
- Veritabanı: her test için tek ortak geçici SQLite dosyası
- Product code change: yok
- Commit / push / PR: yok

Node 24/npm 11 ve kapalı Docker daemon bu Python tabanlı gate'in kapsamına girmedi.

## Test yöntemi

### Engine process testi

Önce dört baseline memory SQLite'a yazıldı. Ardından aynı DB'ye bağlı A ve B process'leri eşzamanlı açık tutuldu. Her process kendi `MemoryEngine` örneğini initialize etti. A→B ve B→A yönlerinde:

1. create sonrası observer `get` ve exact-query `recall`,
2. baseline update sonrası observer `get` ve yeni içerikle exact-query `recall`,
3. baseline delete sonrası observer `get` ve eski içerikle exact-query `recall`,
4. observer restart sonrası aynı create/update/delete durumu

kontrol edildi.

### Gerçek transport testi

Aynı DB üzerinde gerçek process'ler birlikte çalıştırıldı:

- REST: `python -m uvicorn server.api:app`
- MCP: `python -m server.mcp_stdio`, MCP client stdio protokolü

REST→MCP stdio ve MCP stdio→REST yönlerinde create, update ve delete gerçekleştirildi. Observer process kapatılmadan recall yapıldı; sonra yalnız observer yeniden başlatılarak recovery doğrulandı.

## Sonuç matrisi

| Katman | Kontrol | A→B / REST→MCP | B→A / MCP→REST |
|---|---|---:|---:|
| Engine | create sonrası DB-backed `get` | PASS | PASS |
| Engine | create sonrası live `recall` | FAIL | FAIL |
| Engine | update sonrası DB-backed `get` | PASS | PASS |
| Engine | update sonrası live `recall` | FAIL — eski cache | FAIL — eski cache |
| Engine | delete sonrası DB-backed `get` | PASS — row yok | PASS — row yok |
| Engine | delete sonrası live `recall` | FAIL — ghost memory | FAIL — ghost memory |
| Engine | observer restart recovery | 3/3 PASS | 3/3 PASS |
| Transport | create sonrası live `recall` | FAIL | FAIL |
| Transport | update sonrası live `recall` | FAIL — eski cache | FAIL — eski cache |
| Transport | delete sonrası live `recall` | FAIL — ghost memory | FAIL — ghost memory |
| Transport | DB-backed REST GET (MCP→REST) | n/a | 3/3 PASS |
| Transport | observer restart recovery | 3/3 PASS | 3/3 PASS |

Toplam kayıt:

- Engine: 18 scenario kaydı; 12 PASS, 6 FAIL.
- Gerçek transport: 15 scenario kaydı; 9 PASS, 6 FAIL.
- Live recall zorunlu invariant'ları: engine 6/6 FAIL; gerçek transport 6/6 FAIL.
- Restart recovery: engine 6/6 PASS; gerçek transport 6/6 PASS.
- İki final SQLite veritabanında `PRAGMA integrity_check=ok`.

Pytest final hükmü bilinçli olarak kırmızı tutuldu:

```text
2 failed
engine: create/update/delete recall coherence iki yönde başarısız
transport: create/update/delete recall coherence iki yönde başarısız
```

Bu bir test altyapısı hatası değildir; testler tüm senaryoları ve restart kontrollerini tamamladıktan sonra bozulan coherence invariant'larını topluca assert etmektedir.

## Gözlenen davranış

### Create

Writer process yeni row'u SQLite'a ekledi. Observer process aynı ID'yi `get` ile gördü; fakat observer'ın `recall` sonucu yeni memory'yi içermedi. Aynı sonuç ters yönde ve REST ↔ MCP stdio üzerinde tekrarlandı.

### Update

Writer içeriği ve embedding'i SQLite'ta güncelledi. Observer `get` ile yeni içeriği gördü. Buna rağmen observer `recall` process başlangıcında yüklediği eski Memory nesnesini/eski içeriği döndürdü.

### Delete

Writer row'u SQLite'tan sildi. Observer `get` 404/None verdi. Observer `recall` ise kendi vector cache'indeki silinmiş memory'yi döndürmeye devam etti; bu doğrudan ghost-memory davranışıdır.

### Restart

Observer process yeniden başlatıldığında:

- karşı process'in create ettiği memory recall'a girdi,
- update edilmiş yeni içerik recall'da görüldü,
- delete edilmiş memory recall'dan kayboldu.

Dolayısıyla persistent SQLite state doğru; tutarsızlık canlı process'lerin in-memory derived state senkronizasyonundadır.

## Kök neden karakterizasyonu

Kaynak kod gözlemi runtime sonucuyla uyumludur:

- `MemoryEngine.initialize()` SQLite'taki tüm memory'leri yalnız initialization sırasında process-local `vector_store` içine yükler (`server/core/memory_engine.py:197`, `:213`, `:216`).
- `recall()` adayları SQLite'tan değil process-local `vector_store.search()` üzerinden alır (`:324`, `:356`).
- `store`, `update_memory` ve `forget` yalnız işlemi yapan engine örneğinin vector cache'ini değiştirir. Update'in local cache yazımı `:472`, `:503-504`; delete'in local cache silmesi `:459`, `:463`.
- Diğer process için invalidation, change feed, version poll veya cache refresh yolu gözlenmedi.

Bu bölüm remediation önerisi değildir; yalnız doğrulanan davranışın mekanizmasını sınırlar.

## Kanıt dizini

- `evidence/groundtruth/task-00A1/engine-scenarios.jsonl`: engine scenario bazlı expected/observed/pass kayıtları
- `evidence/groundtruth/task-00A1/transport-scenarios.jsonl`: gerçek REST ↔ MCP stdio kayıtları
- `evidence/groundtruth/task-00A1/process-map.txt`: process/DB/transport eşlemesi
- `evidence/groundtruth/task-00A1/sqlite-state.txt`: final DB state ve integrity sonucu
- `evidence/groundtruth/task-00A1/stdout/`: worker protokolü, REST logları, MCP tool sonuçları
- `evidence/groundtruth/task-00A1/stderr/`: engine, REST ve MCP stderr kayıtları
- `evidence/groundtruth/task-00A1/commands.txt`: komut kronolojisi ve final hüküm

## Sınırlar

- Yalnız P0-1 yeniden üretildi; başka P0 görevi başlatılmadı.
- Docker/SSE transport bu görevin zorunlu REST ↔ MCP stdio matrisine dahil edilmedi.
- MCP client helper child PID'yi dışarı açmadığından `process-map.txt` MCP process'ini launcher ve stdio transport ile kaydeder; REST ve engine PID'leri doğrudan kaydedilmiştir.
- Test DB'leri pytest geçici dizinindeydi; test kapanmadan final state ve integrity kanıtı `sqlite-state.txt` içine yazıldı.

## Gate sonucu

```text
TASK-00A1_CROSS_PROCESS_COHERENCE:
COMPLETE

P0-1:
CONFIRMED

REMEDIATION:
NOT_STARTED
```
