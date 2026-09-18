# LEVH SOLID Karnesi & Teknik Borç Envanteri

> **ARŞİV — güncellenmiyor.** Bu dosya 2026-09-13 tarihli bir anlık görüntüdür (snapshot).
> Satır referansları, sayılar ve "açık" işaretleri o güne aittir; bugünkü `main`'i anlatmaz.
> Kapanan maddeler aşağıda `✅ kapandı` notuyla işaretlidir. Güncel borç durumu için issue'lara bakın.

Tarih: 2026-09-13 · Kapsam: `server/` (26.155 satır Python) · Test: 943 passed / 1 skipped

_2026-09-18 doğrulaması (issue #217): `server/` **27.106 satır**, test **1069 passed / 1 skipped**;
`librarian.py` artık yok (pakete bölündü), ölü fonksiyon listesi temizlendi, `redact_memory` düzeltildi._

Bu döküman iki kaynaktan oluşur: (A) kapsamlı kod incelemesinin kanıtlanmış bulguları ve
(B) SOLID prensiplerine göre modül-modül karnesi. Her bulgu GitHub issue'ya bağlanmıştır.

---

## SOLID KARNESİ (her prensip: A iyi / B orta / C zayıf)

### S — Single Responsibility

| Modül | Not | Detay |
|---|---|---|
| `top etc. engine/*` | B | Her mixin tek iş ama orchestration birleşik |
| `AgentTracker` | **C** | 6 sorumluluk tek sınıfta (presence/heartbeat/checkpoint/billing/collaboration/SQL) |
| ~~`librarian.py` (840 satır)~~ ✅ | — | 9 ayrı görev tek dosyadaydı; `server/core/librarian/` paketine bölündü (#98) |
| `Database` | **C** | 10 query-mixin'den miras, ~79 method god-object |
| `cli_parsers.build_parser` | **C** | 281 satırlık god-function |
| `tools/agent_tracking.py register` | **C** | 325 satır / 10 tool tek fonksiyonda |
| `api.py` | B | CORS+mw+WS+static+engine+widget tek dosya; kabul edilebilir FastAPI deseni |
| route'lar (genel) | A | İnce HTTP adaptörleri, mantık engine'de |

### O — Open/Closed

| Nokta | Not | Detay |
|---|---|---|
| Tool ekleme | **C** | `register.py:45-100` yeni tool için elle import+çağrı listesine ekleme |
| Hook ekleme | **C** | `universal_hooks.py` 5 neredeyse aynı `install_*` fonksiyonu; yeni client = kopyala-yapıştır |
| Route ekleme | B | Router'lar ayrı dosya, `api.py` include — makul |
| Connector ekleme | B | `BaseConnector` ABC var; ama 7 connector'da `connect` doğrulama/kopya tekrarı var |

### L — Liskov

| Nokta | Not | Detay |
|---|---|---|
| `Database` is-a query-mixin | **C** | İlişki gerçekte "sahiptir" (composition); miras olarak IS-A yanlış |
| `BaseConnector` | A | Soyut kontrat düzgün; `connect/fetch/disconnect` tutarlı |
| Mixin'ler | B | Ortak `self.*` sözleşmesine güveniyor (belge dışı interface) |
| MCP tool'ları | B | Dekoratör tabanlı, tutarlı |

### I — Interface Segregation

| Nokta | Not | Detay |
|---|---|---|
| `MemoryEngine` | **C** | 26+ mixin interface'i tek sınıfta birleşir; tüketici hepsine maruz |
| `Database` | **C** | 79 method'luk dev interface |
| `AgentTracker` | C | Tüketicilerin çoğu birkaç metod kullanıyor ama hepsine bağımlı |
| `RecallRequest` gibi Pydantic | A | Ayrık request modelerı iyi ayrışmış |

### D — Dependency Inversion

| Nokta | Not | Detay |
|---|---|---|
| Engine'e erişim | **C** | `engine_provider.get_engine()` global singleton; tam Service Locator anti-pattern |
| Route'lar | **C** | `deps.get_engine()` → `api.get_engine()` → global; DI yok |
| `api.py` module-globals | **C** | `_engine/_ws_clients/_event_loop/_subscribed_engines` global mutable; `set_engine` test-enjeksiyonu |
| `ask`/`consolidate` | **C** | `self._embedder._http` private alana doğrudan erişim (kapsülleme kırık) |
| `Database` alt katmanı | A | SQL soyutlaması makul; WAL/FTS5 mimarisi iyi |

---

## KANITLANMIŞ BULGULAR & ISSUE LİSTESİ

### P0 — Yüksek öncelik

1. **Conflict/Trust O(n²) tam tarama** — `detect_conflict_candidates` (conflict_service.py:57-120),
   `recompute_trust_scores` + `get_trust` (trust_service.py:137-178), `_ensure_derived_state`
   (lifecycle.py:126-131). Her derived-read tetiklemesi full corpus rebuild.
2. **Continuity heuristic kırılgan** — `get_continuity_context` karar/blocker algısı keyword-listeleriyle;
   yüksek yanlış-negatif. `session_memories` ölü değişken (continuity.py:159).

### P1 — Orta / teknik borç

3. **Service Locator anti-pattern** — `engine_provider.get_engine()` + `deps.get_engine()` + `api.set_engine`
   + `api._ws_clients` erişimi; DI/global state.
4. **`Database` god-object** — 10 mixin ~79 method; composition-over-inheritance.
5. **`MemoryEngine` tanrı objesi** — 26+ mixin; `__init__` elle servis kurulumu.
6. **Tool register god-function** — `tools/agent_tracking.py` 325 satır / 10 tool; `register.py` elle liste.
7. **`universal_hooks` DRY** — 5 benzer `install_*` fonksiyonu (kopyala-yapıştır).
8. ~~**`librarian.py` tek dosya 9 sorumluluk** — parçalanmalı.~~ ✅ `server/core/librarian/` paketine bölündü (#98).
9. **`AgentTracker` SRP** — 6 sorumluluk tek sınıf.

### P2 — Düşük / ölü kod

10. ~~**Ölü fonksiyonlar (10 adet)**~~ ✅ Listelenen 12 adın tamamı (`get_registry`, `_levh_url`,
    `auto_heartbeat_enabled`, `with_auto_heartbeat`, `add_batch`, `embed_batch`, `unsubscribe`,
    `clear_all_memories`, `clear_all_sessions`, `get_sync_state`, `conflicts_for_memory`) ve
    `session_memories` artık kodda **0 referans**; liste kapandı (#98 kapsamı).
11. **`cli_parsers.build_parser` god-function** (281 satır) — alt-perserlere bölünmeli.
12. ~~**`redact_memory` çift `vector_store.add`** (privacy.py:105) — gereksiz tekrar.~~ ✅
    `server/core/engine/privacy.py` tek `episodic.update` + `_refresh_memory_caches` yapıyor.

---

## DÜZELTME PLANI (sıralı)

> Arşiv notu: aşağıdaki liste 2026-09-13 durumudur. ✅ işaretli maddeler tamamlandı ve
> plan dışıdır; kalanlar güncelliğini yitirmiş olabilir, güncel durumu issue'lardan doğrulayın.

- [ ] P0-1 → conflict/trust taramalarını async+incremental yap
- [ ] P0-2 → continuity heuristic'i tag/db-bazlı yap, ölü `session_memories` kaldır
- [ ] P1-3 → service locator'ı FastAPI dependency/DI kalıbına taşı
- [ ] P1-4 → Database'i composition'a çevir (repo nesnelerini inject et)
- [ ] P1-5 → MemoryEngine orchestration-only yap
- [ ] P1-6 → tool registration'ı deklaratif yap
- [ ] P1-7 → universal_hooks ortak üretici fonksiyon
- [x] P1-8 → librarian.py'yi parçala ✅ (#98)
- [ ] P1-9 → AgentTracker'ı sorumluluklara böl
- [x] P2-10 → 10+1 ölü fonksiyonu/değişkeni sil ✅ (0 referans kaldı)
- [ ] P2-11 → cli_parsers'ı böl
- [x] P2-12 → çift `vector_store.add` kaldır ✅ (privacy.py tek update yapıyor)