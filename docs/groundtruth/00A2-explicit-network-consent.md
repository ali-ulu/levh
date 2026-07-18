# TASK-00A2 — Explicit Network Consent

## Karar

```text
TASK-00A2_EXPLICIT_NETWORK_CONSENT:
COMPLETE

P0_2_CONFIRMED:
AMBIENT_OPENAI_KEY_ACTIVATES_OUTBOUND_MEMORY_TRANSMISSION

REMEDIATION:
NOT_STARTED
```

Ambient `OPENAI_API_KEY`, embedder açıkça `hash` olarak kilitli olsa bile
`MemoryEngine.ask`, `MemoryEngine.summarize_session` ve etkin session-end
auto-summary yollarında OpenAI chat-completions POST yolunu aktive etmektedir.
Ask payload'ı recalled memory içeriğini; summary payload'ları session memory
içeriğini taşımaktadır.

Gerçek ağ isteği gönderilmedi. `httpx.AsyncClient.post`, engine çağrılarından
önce test guard'ı ile değiştirildi; URL, header varlığı ve sentetik payload
kaydedildikten sonra yerel `httpx.Response` döndürüldü. HTTP transportuna
delegasyon sayısı ve gerçek ağ isteği sayısı kesin olarak `0` kaldı.

## Kilitli ortam

- Worktree: `<AUDIT_WORKTREE>`
- Branch: `audit/groundtruth-v2`
- HEAD: `3a97ae7177c128e5484434d76828751330149fc3`
- Python: izole `.venv`, Python 3.11.9
- LEVH: `2.27.2`, bu checkout'tan
- Embedder: constructor üzerinden açıkça `hash`
- Credential: yalnız işlevsiz sentetik `sk-task00a2-*` test değeri
- Gerçek network: `0`
- Product code change: yok
- Commit / push / PR / merge: yok

`sandbox:/mnt/data/CODEX_TASK_00A2_EXPLICIT_NETWORK_CONSENT.md` yerel Windows
dosya sisteminde bulunamadı. Kullanıcının mesajında verdiği zorunlu matris ve
engine yolları eksiksiz uygulanmıştır.

## Test yöntemi

Her senaryo gerçek `MemoryEngine` ve geçici SQLite DB kullandı. Hash embedder
ile sentetik memory oluşturuldu. POST guard şu sınırda kuruldu:

```text
httpx.AsyncClient.post
        ↓
URL + sentetik payload capture
        ↓
transport_delegated = false
real_network_requests = 0
```

Session-end yolu için `AUTO_SUMMARIZE_SESSIONS=1` ayarlandı. Ask,
`summarize_session(store=False)` ve `end_session` ayrı ayrı ölçüldü.

## Sonuç matrisi

| Key | Yol | Mode | Embedder | POST girişimi | Memory payload'da | Gerçek ağ | Sonuç |
|---|---|---|---|---:|---:|---:|---|
| yok | `MemoryEngine.ask` | engine `auto` | hash | 0 | hayır | 0 | offline PASS |
| yok | `MemoryEngine.summarize_session` | engine `auto` | hash | 0 | hayır | 0 | offline PASS |
| yok | `MemoryEngine.end_session` auto-summary | engine `auto` | hash | 0 | hayır | 0 | offline PASS |
| ambient sentetik | `MemoryEngine.ask` | engine `auto` | hash | 1 | evet | 0 | P0-2 reproduced |
| ambient sentetik | `MemoryEngine.summarize_session` | engine `auto` | hash | 1 | evet | 0 | P0-2 reproduced |
| ambient sentetik | `MemoryEngine.end_session` auto-summary | engine `auto` | hash | 1 | evet | 0 | P0-2 reproduced |
| ambient sentetik | `answer_question` | explicit `extractive` | n/a | 0 | hayır | 0 | offline PASS |
| ambient sentetik | `summarize_texts` | explicit `extractive` | n/a | 0 | hayır | 0 | offline PASS |

Toplam:

- 9 scenario kaydı, 9 characterization assertion PASS.
- Ambient-key gerçek engine yolları: 3/3 OpenAI POST girişimi.
- Bu üç payload'ın 3/3'ünde sentetik memory içeriği mevcut.
- Explicit extractive üretim helper'ları: 2/2 sıfır outbound.
- Transport delegasyonu: 0.
- Gerçek ağ isteği: 0.
- Final pytest: `4 passed`.

Testlerin yeşil olması privacy invariant'ının geçtiği anlamına gelmez. Bunlar
mevcut davranışı karakterize eder: ambient key'in outbound yolu aktive etmesi
beklenen mevcut davranış olarak assert edilmekte ve bu davranış P0-2 ihlalini
doğrulamaktadır.

## Payload kanıtı

Ask capture kaydı şunları doğruladı:

- hedef URL: `https://api.openai.com/v1/chat/completions`
- authorization header mevcut ve yalnız sentetik anahtarı kullanıyor
- user message içinde `GT00A2_AMBIENT_SECRET_MEMORY_8A4D20 payload sentinel`
- aynı message içinde sentetik soru mevcut
- transport delegasyonu yok

Summary ve auto-summary capture kayıtlarında aynı sentetik session memory
içeriği `Session memories:` user message'ına girdi.

Tam sentetik payload'lar
`evidence/groundtruth/task-00A2/captured-httpx-posts.jsonl` içindedir. Anahtar
değeri evidence'a yazılmamış, yalnız synthetic-key eşleşme boolean'ı
kaydedilmiştir.

## Explicit extractive sınırı

`answer_question(..., mode="extractive")` ve
`summarize_texts(..., mode="extractive")`, ambient key mevcutken sıfır POST
üretti. Ancak gerçek engine yüzeyi bu seçimi expose etmemektedir:

- `MemoryEngine.ask` signature'ında `mode` yok.
- `MemoryEngine.summarize_session` signature'ında `mode` yok.
- Her ikisi de helper'ı `mode="auto"` ile çağırır.
- `end_session` auto-summary de aynı `summarize_session` yoluna gider.

Dolayısıyla explicit extractive davranışı alt seviye üretim helper'ında güvenli
olsa da, zorunlu engine yollarında kullanıcı tarafından seçilebilir bir consent
kontrolü değildir.

## Kök neden karakterizasyonu

- `answer_question` auto modunda yalnız `OPENAI_API_KEY` varlığıyla LLM yolunu
  seçer (`server/core/answerer.py:71`).
- Ask memory ve question içeriğini chat payload'ına ekler (`:85`) ve OpenAI
  endpoint'ine POST eder (`:97`).
- `summarize_texts` aynı ambient-key seçimini yapar
  (`server/core/summarizer.py:64`), session memory'lerini payload'a ekler (`:73`)
  ve POST eder (`:84`).
- `MemoryEngine.ask` helper'ı `mode="auto"` ile çağırır
  (`server/core/memory_engine.py:414`, `:454`).
- `MemoryEngine.summarize_session` aynı auto helper yolunu kullanır (`:1015`).
- `end_session`, auto-summary açıkken `summarize_session` çağırır (`:1059`,
  `:1068`, `:1070`).

Hash embedder yalnız embedding provider'ını yerel tutmaktadır; ask/summary chat
provider seçimini sınırlamamaktadır.

## Kanıt dizini

- `tests/groundtruth/test_explicit_network_consent.py`: audit-only harness
- `evidence/groundtruth/task-00A2/scenarios.jsonl`: scenario kararları
- `evidence/groundtruth/task-00A2/captured-httpx-posts.jsonl`: sanitize edilmiş
  sentetik payload capture'ları
- `evidence/groundtruth/task-00A2/network-guard.txt`: sıfır gerçek-ağ hükmü
- `evidence/groundtruth/task-00A2/stdout/pytest.txt`: final pytest çıktısı
- `evidence/groundtruth/task-00A2/stderr/pytest.txt`: final stderr
- `evidence/groundtruth/task-00A2/commands.txt`: komut kronolojisi

## Sınırlar

- Gerçek OpenAI anahtarı kullanılmadı.
- DNS, socket veya gerçek HTTP transportuna gidilmedi.
- Yalnız ask, session summary ve session-end auto-summary P0-2 matrisi test
  edildi; başka outbound özellikler taranmadı.
- Ürün kodu düzeltilmedi ve başka P0 görevi başlatılmadı.
