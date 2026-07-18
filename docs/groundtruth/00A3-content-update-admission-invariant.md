# TASK-00A3 — Content Update Admission Invariant

## Karar

```text
TASK-00A3_UPDATE_ADMISSION_INVARIANT:
COMPLETE

P0_3_CONFIRMED:
CONTENT_UPDATE_BYPASSES_ADMISSION_AND_SECRET_REDACTION

REMEDIATION:
NOT_STARTED
```

Create ve update politikaları runtime'da farklı davranmaktadır. REST create,
sentetik secret içeriğini admission gate'e gönderip redakte edilmiş içeriği
embed/persist eder. Buna karşılık doğrudan engine, REST PUT ve gerçek MCP stdio
content update yolları raw sentetik secret'ı kabul eder; raw içeriği embed eder,
SQLite ve FTS'e yazar ve recall sonucunda döndürür.

Update ayrıca exact-duplicate `reject` ve near-duplicate `review` kararlarını da
bypass etmektedir.

Bu görev yalnız reproduction ve characterization yapmıştır. Ürün koduna
düzeltme uygulanmamıştır.

## Kilitli ortam

- Worktree: `<AUDIT_WORKTREE>`
- Branch: `audit/groundtruth-v2`
- HEAD: `3a97ae7177c128e5484434d76828751330149fc3`
- Python: izole `.venv`, Python 3.11.9
- LEVH: `2.27.2`, bu checkout'tan
- Embedder: explicit `hash`
- Credential verisi: yalnız işlevsiz `GT00A3*` sentetik assignment değerleri
- Network: kullanılmadı
- Product code change: yok
- Commit / push / PR / merge: yok

`sandbox:/mnt/data/CODEX_TASK_00A3_UPDATE_ADMISSION_INVARIANT.md` yerel Windows
dosya sisteminde bulunamadı. Kullanıcının mesajındaki zorunlu matris ve yüzeyler
eksiksiz uygulanmıştır.

## Test edilen yüzeyler

| Yüzey | Content update var | Runtime testi |
|---|---:|---:|
| `MemoryEngine.update_memory` | evet | evet |
| REST `PUT /api/memories/{id}` | evet | evet |
| MCP stdio `update_memory` | evet | gerçek stdio process ile evet |
| CLI | hayır | update komutu bulunmadı |
| WebSocket `/ws/memory` | hayır | yalnız store/recall/forget/stats/ping var |

WebSocket `store` yolu admission-gatedir; ayrı bir WebSocket update action'ı
yoktur. CLI'da capture/admit create yolları vardır fakat content update komutu
yoktur.

## Sonuç matrisi

| Kontrol | Create | Direct engine update | REST PUT update | MCP stdio update |
|---|---:|---:|---:|---:|
| Secret admission kararı | `redact` | kontrol kararı `redact`, uygulanmadı | uygulanmadı | kontrol kararı `redact`, uygulanmadı |
| Raw secret persist | hayır | evet | evet | evet |
| Raw secret embed | hayır | evet, input capture | evet, input capture | evet, persisted hash eşleşmesi |
| SQLite raw content | hayır | evet | evet | evet |
| FTS raw content | hayır | evet | evet | evet |
| Recall raw content | hayır | evet | evet | evet |
| Secret audit sonrası flag | 0 | evet | evet | evet |

Duplicate matrisi:

| İçerik | REST create | REST update |
|---|---|---|
| exact duplicate, similarity `1.0` | `409 reject` | `200`, kabul edildi |
| near duplicate, similarity `0.9461` | `409 review` | `200`, kabul edildi |

Toplam:

- 25 scenario kaydı, 25 PASS.
- Final pytest: `4 passed`.
- Direct engine secret zinciri: admission control, raw embedding, SQLite, FTS,
  recall ve audit doğrulandı.
- REST create redaction control: raw sentetik token embedder'a ulaşmadı;
  `[REDACTED]` içerik iki embedding çağrısında kullanıldı.
- REST PUT raw secret zinciri: response, embed input, SQLite, FTS, recall ve
  audit doğrulandı.
- Gerçek MCP stdio update: raw secret tool output'unda döndü, SQLite/FTS/recall'a
  girdi ve persisted embedding raw secret hash'iyle eşleşti.

Testlerin yeşil olması ürün invariant'ının geçtiği anlamına gelmez. Audit
harness mevcut bypass davranışını karakterizasyon beklentisi olarak assert
etmektedir; 25 PASS, P0-3 davranışının 25 ayrı gözlemde yeniden üretildiğini
ifade eder.

## Create kontrolü

REST create'e şu tür sentetik içerik verildi:

```text
password=GT00A3CREATECONTROL38BD10 synthetic create control
```

Sonuç:

- admission action: `redact`
- persisted/returned content: `password=[REDACTED] ...`
- raw token response veya SQLite içeriğinde yok
- embedding input'larının tamamı redakte edilmiş içerik
- admission metadata redaction kararını doğru kaydediyor

Bu, secret detector ve admission policy'nin çalıştığını; ihlalin detector
eksikliğinden değil update'in gate'i çağırmamasından kaynaklandığını gösterir.

## Direct engine update

Safe memory oluşturulduktan sonra aynı sentetik secret adayı
`evaluate_admission` ile kontrol edildi ve `redact` kararı alındı. Ardından aynı
içerik doğrudan `MemoryEngine.update_memory` ile uygulandı.

Gözlem:

- update raw içeriği kabul etti,
- `Embedder.embed` ilk input'u raw secret'ın birebir kendisiydi,
- SQLite embedding'i `hash_embed(raw_secret)` ile birebir eşleşti,
- FTS sentetik token'ı buldu,
- exact-query recall raw içeriği döndürdü,
- `audit_secrets` memory'yi flagledi.

## REST PUT update

REST üzerinden admission-gated safe memory oluşturuldu. Ardından content, raw
sentetik token assignment'ına PUT ile değiştirildi.

Gözlem:

- response `200` ve raw secret content içeriyor,
- raw secret embedder'a gönderiliyor,
- SQLite ve FTS raw secret içeriyor,
- recall raw secret döndürüyor,
- secret audit memory'yi flagliyor,
- metadata içindeki `admission` receipt eski safe create kararında kalıyor.

Son madde ayrıca provenance sorunudur: içerik artık secret-bearing olduğu halde
metadata hâlâ `action=admit`, `redacted=false`, `secrets=[]` göstermektedir.

## MCP stdio update

Safe memory SQLite'a admission-gated olarak yazıldı, engine kapatıldı ve aynı DB
ile gerçek `python -m server.mcp_stdio` process'i `LEVH_MCP_PROFILE=full`
profilinde başlatıldı. MCP client gerçek stdio protokolü üzerinden
`update_memory` çağırdı.

Gözlem:

- tool başarılı sonuç ve raw secret preview döndürdü,
- SQLite raw content içerdi,
- persisted embedding raw secret hash'iyle eşleşti,
- FTS raw token'ı buldu,
- MCP process kapandıktan sonra yeni engine initialization + recall raw content
  döndürdü,
- audit secret'ı flagledi.

## Duplicate/review ayrımı

Hash embedder ile deterministik iki control kullanıldı:

- exact içerik: similarity `1.0`, create `reject`
- `revised` varyantı: similarity `0.9461`, create `review`

Her iki create 409 ile durduruldu. İki ayrı safe memory aynı içeriklere REST PUT
ile güncellendiğinde ikisi de 200 döndü ve içerikleri kabul etti. Dolayısıyla
update yalnız secret redaction'ı değil duplicate/review admission kararlarını da
atlamaktadır.

## Kök neden karakterizasyonu

- `MemoryEngine.update_memory` content'i doğrudan memory nesnesine yazar
  (`server/core/memory_engine.py:472`, `:487`).
- Aynı raw content doğrudan embed edilir (`:488`) ve episodic storage'a yazılır
  (`:501`).
- Update yolu `evaluate_admission` (`:1864`) veya `admit_memory` (`:1892`)
  çağırmaz.
- REST create `engine.admit_memory` kullanır (`server/api.py:311`, `:320`).
- REST PUT doğrudan `engine.update_memory` kullanır (`:548`, `:551`).
- MCP update aracı da doğrudan aynı engine metodunu çağırır
  (`server/tools/update.py:12`, `:33`).
- SQLite FTS update trigger'ı yeni raw content'i otomatik indeksler
  (`server/core/database.py:153-154`).

Bu bölüm remediation tasarımı değildir; yalnız runtime bulgusunun kod yolunu
sınırlar.

## Kanıt dizini

- `tests/groundtruth/test_update_admission_invariant.py`: audit-only harness
- `evidence/groundtruth/task-00A3/scenarios.jsonl`: 25 scenario kaydı
- `evidence/groundtruth/task-00A3/embedding-inputs.jsonl`: engine ve REST
  embedding input capture'ları
- `evidence/groundtruth/task-00A3/persistence-state.jsonl`: SQLite content,
  embedding ve admission metadata kanıtı
- `evidence/groundtruth/task-00A3/surface-inventory.json`: update yüzeyleri
- `evidence/groundtruth/task-00A3/stdout/mcp-tool-results.jsonl`: gerçek MCP
  tool sonucu
- `evidence/groundtruth/task-00A3/stderr/mcp.log`: MCP stderr
- `evidence/groundtruth/task-00A3/stdout/pytest.txt`: final pytest çıktısı
- `evidence/groundtruth/task-00A3/stderr/pytest.txt`: final pytest stderr
- `evidence/groundtruth/task-00A3/commands.txt`: komut kronolojisi

## Sınırlar

- Gerçek credential veya credential biçimli gerçek token kullanılmadı.
- Yalnız mevcut content update yüzeyleri test edildi; CLI ve WebSocket'te update
  bulunmadığı kaydedildi.
- Başka P0 görevi başlatılmadı.
- Remediation uygulanmadı.
