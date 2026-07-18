# TASK-00A4 — Standalone MCP SSE Auth Boundary

## Karar

```text
TASK-00A4_STANDALONE_SSE_AUTH_BOUNDARY:
COMPLETE

P0_4_CONFIRMED:
STANDALONE_MCP_SSE_IGNORES_CONFIGURED_LEVH_TOKEN

REMEDIATION:
NOT_STARTED
```

Standalone `server.mcp_sse:app`, `LEVH_TOKEN` ortamda set edilmiş olsa bile
client'tan token istememektedir. Token göndermeyen client initialize olabilmiş,
full profilin 59 tool'unu görmüş, kontrollü tek memory store etmiş ve yalnız o
çağrıda dönen exact ID'yi `forget_memory` ile silebilmiştir.

Main FastAPI altına `/api/mcp` olarak mount edilen aynı MCP SSE uygulaması ise
FastAPI token middleware'i tarafından korunmaktadır: tokensız SSE GET ve MCP
messages POST istekleri 401 dönmüş; geçerli `X-LEVH-Token` header'ı ile MCP
initialize başarılı olmuştur.

Profil seçimi yalnız capability discovery filtresidir; auth boundary değildir.

## Kilitli ortam

- Worktree: `<AUDIT_WORKTREE>`
- Branch: `audit/groundtruth-v2`
- HEAD: `3a97ae7177c128e5484434d76828751330149fc3`
- Python: izole `.venv`, Python 3.11.9
- LEVH: `2.27.2`, bu checkout'tan
- Bind: yalnız `127.0.0.1`, ephemeral portlar
- DB: her scenario için ayrı geçici SQLite
- Token: yalnız sentetik `task00a4-*` değeri
- Product code change: yok
- Commit / push / PR / merge: yok

`sandbox:/mnt/data/CODEX_TASK_00A4_STANDALONE_SSE_AUTH_BOUNDARY.md` yerel
Windows dosya sisteminde bulunamadı. Kullanıcının mesajındaki zorunlu matris ve
güvenlik sınırları eksiksiz uygulandı.

## Güvenli test sınırı

- Bütün uvicorn process'leri `--host 127.0.0.1` ile açıldı.
- Yalnız `store_memory` ve aynı çağrının döndürdüğü exact ID üzerinde
  `forget_memory` çağrıldı.
- `purge_memory`, restore, backup, connector, import/export, global delete veya
  network aracı çağrılmadı.
- Her kontrollü lifecycle sonunda SQLite memory count `0` ve
  `PRAGMA integrity_check=ok` olarak doğrulandı.

## Sonuç matrisi

| Server | `LEVH_TOKEN` | Client token | Profil | Initialize | Tool | Kontrollü store/delete |
|---|---|---|---|---:|---:|---:|
| standalone SSE | yok | yok | unset → full | PASS | 59 | PASS |
| standalone SSE | yok | yok | minimal | PASS | 5 | çağrılmadı |
| standalone SSE | yok | yok | work | PASS | 15 | çağrılmadı |
| standalone SSE | yok | yok | full | PASS | 59 | çağrılmadı |
| standalone SSE | set | **yok** | full | **PASS** | **59** | **PASS** |
| FastAPI-mounted MCP | set | yok | minimal | GET/POST `401` | erişilemedi | çağrılmadı |
| FastAPI-mounted MCP | set | geçerli header | minimal | PASS | 5 | çağrılmadı |

Toplam:

- 19 scenario kaydı, 19 PASS.
- 6 ayrı loopback server process'i.
- Tool surface kayıtları: minimal `5`, work `15`, full `59`.
- Standalone controlled mutation lifecycle: 2/2 PASS.
- Mounted MCP unauthorized HTTP checks: 2/2 `401`.
- Mounted MCP valid-token initialize: PASS.
- 6/6 SQLite integrity check: `ok`.
- Final pytest: `4 passed`.

Testlerin yeşil olması auth invariant'ının geçtiği anlamına gelmez. Audit
harness mevcut davranışı karakterize etmektedir. Standalone token-bypass
senaryosunun PASS olması, `LEVH_TOKEN=set + client token=absent` durumunda
erişimin runtime'da başarıyla yeniden üretildiğini ifade eder.

## Standalone, token yok

`LEVH_MCP_PROFILE` unset ve `LEVH_TOKEN` absent durumda standalone SSE:

- MCP initialize kabul etti,
- backward-compatible default olarak full profil açtı,
- 59 tool advertise etti,
- `store_memory` ile tek sentetik kayıt oluşturdu,
- `forget_memory` ile yalnız dönen ID'yi sildi,
- final DB memory count `0` kaldı.

Bu, zero-config loopback davranışını karakterize eder; tek başına token bypass
iddiası değildir.

## Standalone, `LEVH_TOKEN` set fakat client token yok

Ayrı process'te sentetik `LEVH_TOKEN` set edildi. Client hiçbir header, query
token veya auth nesnesi vermedi.

Gözlem:

- initialize başarılı,
- 59 full-profile tool advertise edildi,
- controlled store başarılı,
- exact-ID forget başarılı,
- final DB boş ve bütünlük `ok`.

Dolayısıyla `LEVH_TOKEN` standalone SSE uygulamasında etkin bir auth sınırı
oluşturmamaktadır.

## Profil matrisi

Standalone process'ler ayrı ayrı `minimal`, `work` ve `full` ile başlatıldı.
Tool setleri profile registry ile birebir eşleşti:

- minimal: 5
- work: 15
- full: 59

Bu filtreleme beklenen capability davranışıdır. Token doğrulaması eklemez.
Minimal/work profillerinde destructive/admin araçların advertise edilmemesi
yararlı risk azaltımıdır fakat kimlik doğrulama değildir. Full profilde admin
araçları görünür olsa da test yalnız tek kayıt store/exact forget kullandı.

## Main FastAPI-mounted MCP

Main app sentetik `LEVH_TOKEN` ve minimal profil ile başlatıldı.

Tokensız:

- `GET /api/mcp/sse` → `401 unauthorized`
- `POST /api/mcp/messages/?session_id=synthetic` → `401 unauthorized`

Geçerli `X-LEVH-Token` header'ı ile:

- MCP initialize başarılı,
- exact minimal tool seti, 5 tool görüldü.

Bu, main FastAPI middleware'inin mount altındaki SSE stream ve messages HTTP
yollarını koruduğunu doğrular.

## Kök neden karakterizasyonu

- Standalone modül doğrudan `FastMCP` oluşturur
  (`server/mcp_sse.py:46`).
- Profil env'i okunur ve tool'lar register edilir (`:51-52`).
- Export edilen app doğrudan `mcp_sse.sse_app()` sonucudur (`:56`).
- Bu modülde `LEVH_TOKEN` okuma veya auth middleware yoktur.
- `LEVH_TOKEN` main FastAPI modülünde okunur (`server/api.py:140`).
- `_require_token` middleware'i `/api/*` yollarını denetler (`:165`, `:168`).
- MCP SSE uygulaması bu korunan prefix altına mount edilir (`:1449`).
- Profil dokümantasyonu filtrenin auth/security boundary olmadığını açıkça
  belirtir (`server/tools/profiles.py:18-19`).

Bu bölüm remediation tasarımı değildir; yalnız runtime bulgusunun uygulama
sınırını belirler.

## Kanıt dizini

- `tests/groundtruth/test_standalone_sse_auth_boundary.py`: audit-only harness
- `evidence/groundtruth/task-00A4/scenarios.jsonl`: 19 scenario kaydı
- `evidence/groundtruth/task-00A4/tool-surfaces.jsonl`: advertise edilen exact
  tool listeleri
- `evidence/groundtruth/task-00A4/process-map.txt`: PID, app, loopback port,
  profil ve token-env haritası
- `evidence/groundtruth/task-00A4/sqlite-state.txt`: final DB state ve integrity
- `evidence/groundtruth/task-00A4/stdout/tool-results.jsonl`: kontrollü
  store/exact-forget sonuçları
- `evidence/groundtruth/task-00A4/stdout/*.log`: uvicorn stdout
- `evidence/groundtruth/task-00A4/stderr/*.log`: uvicorn stderr
- `evidence/groundtruth/task-00A4/stdout/pytest.txt`: final pytest çıktısı
- `evidence/groundtruth/task-00A4/stderr/pytest.txt`: final pytest stderr
- `evidence/groundtruth/task-00A4/commands.txt`: komut kronolojisi

## Sınırlar

- Yalnız loopback üzerinde test edildi; non-loopback erişim iddiası yapılmadı.
- TLS, reverse proxy veya harici deployment test edilmedi.
- Main FastAPI token middleware'i doğrulandı; başka proxy/auth katmanları kapsam
  dışıydı.
- Başka audit görevi başlatılmadı.
- Remediation uygulanmadı.
