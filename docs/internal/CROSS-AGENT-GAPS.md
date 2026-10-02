# Cross-agent, git enrichment and dynamic windowing: state and gaps

Tarih: 2026-10-03 · Kapsam: üç istenen yetenek · Tür: durum kaydı + boşluk envanteri

Bu dosya üç yeteneğin **bugünkü gerçek durumunu** ve **kalan boşlukları** kaydeder.
Bir öneri değil, bir ölçüm: her iddia commit `6f86231` üzerinde koda, canlı veritabanına
veya REST API'ye karşı doğrulandı, ve her iddianın kanıtı yanında yazılı.

`ROADMAP.md`'den farkı: orası "karar verilmiş, ertelenmiş" işleri tutar. Burası
**istenen ama henüz bir satırı olmayan** yetenekleri tutar — yani bir sonraki turda
"bunu yaptık mı?" sorusunun cevabı bir konuşmadan değil bu dosyadan okunur.

---

## 1. Cross-Agent / Team Memory — **yapılmadı**

**İstenen:** Aynı projede paralel çalışan bağımsız ajanların (backend / frontend /
test) ortak bir hafıza grafiğinden beslenmesi; birbirini manipüle etmeden, çelişki
yönetimiyle ortak karar alması.

**Bugün var olan zemin (dürüstçe: azımsanacak değil):**

| Parça | Kanıt |
| --- | --- |
| Ajan oturum kaydı | `agent_sessions` tablosu, canlı DB'de **62 satır** |
| Canlı presence + heartbeat | `agent_tracker.py`, `AgentPresenceService` |
| Çelişki adayı tespiti | `memory_conflict_candidates`, canlı DB'de **26 satır** (25 open, 1 confirmed) |
| "Signal, not verdict" ayrımı | `server/core/conflict.py` — LLM yok, ağ yok, antonym/negation/attribute tespiti |
| İnsan onaylı çözüm akışı | `dismiss` / `confirm` / `resolve_keep_a`, trust skoruna risk sinyali |
| Workspace sınırı | `memories.workspace_id` canlı DB'de mevcut (şema v4) |

**Eksik olan — yeteneğin kendisi:**

- **Ortak hafıza grafiği yok.** `get_project_collaboration()`
  (`server/core/agent_services.py:470`) tek bir sayı döndürüyor:
  `collaboration_score = len([a for a in agents if a["online"]])` — yani **kaç ajan
  online**. Paylaşılan yazma, ortak karar, ajanlar arası devir yok.
- **Roller enforce edilmiyor.** `viewer` / `editor` / `admin` yalnızca
  `SHARED-MEMORY-DESIGN.md`'de var; `server/core/tenancy.py`'de karşılığı yok
  (`grep viewer|editor|admin|authorize` boş döner).
- **`team` / `shared_memory` adlı bir modül yok** (`git ls-tree -r origin/main server/`
  ile doğrulandı).

**Neden yapılmadı — bu bir ihmal değil, kayıtlı bir karar:**
`SHARED-MEMORY-DESIGN.md` Faz 1'i (workspace_id + Principal) uyguladı ve Faz 2–4'ü
tetikleyiciye bağladı. O belgenin kendi cümlesi: *"No funded surface is implemented
yet; the next step is whichever one the owner funds."* Yani blokaj teknik değil,
**sahibin kararı**.

---

## 2. Git/GitHub Entegrasyonu — **araç yazıldı, hiç çalıştırılmadı**

**İstenen:** Hafızanın commit'ler, pull request'ler ve kodun canlı mimarisiyle
**otomatik** beslenmesi; ajanın *"bu fonksiyon 3 commit önce Ali tarafından şu
sebeple değiştirilmişti, güven skoru yüksek bir kural"* diyebilmesi.

**Yapılanlar:**

| Parça | Kanıt |
| --- | --- |
| Yerel git connector | `server/connectors/git.py`, main'de (PR #369) |
| Registry kaydı | `GitConnector.name: GitConnector` — REST + MCP + CLI otomatik alıyor |
| Testler | `tests/test_git_connector.py`, **21 test** |
| Canlı API'de görünür | `GET /api/v1/connectors` → `{"name":"git",...,"required_config_keys":["repo_path"]}` |
| GitHub connector | `server/connectors/github.py` (README + issues + PRs), uzun süredir var |
| Sync framework | `ingest_items` + `connector_sync` tablosu + `/api/connectors/sync` |

**Eksik olan — istenen cümlenin tam kalbi:**

- **Hiç çalıştırılmadı.** `connector_sync` tablosunda yalnızca **iki** satır var:
  `transcript` (1 fetched / 0 stored, 2026-09-10) ve `local_files`
  (379 fetched / 99 stored, 2026-08-29). **`git` satırı yok** — connector kurulu,
  kayıtlı, canlı sunucuda görünüyor, ama bir kez bile koşmamış.
- **`github` connector'i de hiç çalışmamış** — `connector_sync`'te satırı yok.
- **`blame` yok.** Yerel connector yalnızca `git log` okuyor; satır-yazar bilgisi yok.
- **Kodun canlı mimarisiyle senkron yok.** Commit'ler dosya-diff düzeyinde memory
  oluyor, mimari çıkarımı yok.
- **"Güven skoru yüksek kural" yok.** Commit'ler memory'ye girmediği için trust
  katmanı onlara hiç dokunmuyor; `governed-memory` akışı devreye girmiyor.
- **Otomatik beslenme yok.** Connector'lar *pull-on-demand*; arka plan worker
  planlanmıyor (ROADMAP'te kayıtlı karar). Yani "otomatik" kısmı tasarım gereği
  yok — çalıştırma bir çağrıya bağlı.

**Sonuç:** Araç var, **sonuç yok**. `git log` → memory boru hattı kurulu ama
musluk açılmamış.

---

## 3. Dynamic Windowing — **büyük ölçüde yapıldı**

**İstenen:** Bilgi grafiğinden yararlanarak, o anki prompt'a göre en optimize context
paketini dinamik hazırlamak; token tasarrufu sağlayan akıllı veri filtresi olmak.

**Yapılanlar:**

| Parça | Kanıt |
| --- | --- |
| Sorgu-farkında pencere | `get_context(query=...)` (PR #370 + #371) |
| H(x,ψ) sıralaması | `recall`'ın puanlayıcısı yeniden kullanılıyor, superseded cezası dahil |
| Gerçek token bütçesi | `server/core/tokens.py` — `estimate_tokens` (`len//4` yerine) + `truncate_to_tokens` |
| Bütçe-farkında paketleme | `get_context_packing()` → `ContextPacking` (giren / elenen / `used_tokens` / mod) |
| Aday havuzu genişliği | kısa-vade + lexical tarama + FTS (synonym'lerle) — `recall`'ın kaynakları |
| Emekli satır filtresi | `valid_to` kontrolü; mutation-check ile doğrulandı |
| Yüzeyler | REST `GET /api/v1/context?query=`, MCP `get_context(query=...)`, `openapi.json` + TS SDK güncel |
| Testler | `tests/test_context_window.py`, **22 test** |

**Eksik olan:**

- **Bilgi grafiği farkında değil.** `recall` entity graph'ı aday kaynağı olarak
  kullanıyor; `_rank_context_candidates` **kullanmıyor**. İstenen cümledeki
  *"sahip olduğu bilgi grafiği sayesinde"* kısmı tam olarak burada eksik.
- **Adaptif bütçe yok.** `max_tokens`'ı çağıran veriyor; pencere basıncına,
  modele veya kalan bağlama göre kendini ayarlamıyor.
- **Sorgu anında sıkıştırma yok.** Sıkıştırma `consolidate_memories` içinde
  *offline*; pencere kurulurken özetleme yapılmıyor.
- **Grafik-farkında paketleme testi yok** — çünkü özellik yok.

---

## Bu turda bulunan, hiçbir listede olmayan kusurlar

Aşağıdakiler yukarıdaki üç maddenin parçası değil; çalışma sırasında ölçülüp
doğrulandı ve ayrı iş olarak duruyor.

| # | Bulgu | Kanıt | Etki |
| --- | --- | --- | --- |
| 1 | **`recall_log` beslenmiyor** | Canlı DB'de **0 satır** | "Kim neyi okudu" denetim zemini var, veri yok. `SHARED-MEMORY-DESIGN.md` Faz 2'yi buna dayandırıyor. |
| 2 | **Test suite flaky** | Temiz `main` checkout'unda `pytest tests/test_auto_checkpoint.py` izole olarak 3 koşudan 1'inde kırıldı | Kırmızı sinyal güvenilmez; "changed lines" kapısı yanlış yere bakar. |
| 3 | **Bu makinede global Python bozuk** | `pydantic 2.13.5` ↔ `pydantic_core 2.41.5` (2.46.5 gerekli) | Global python ile test koşmak rastgele `SystemError` verir. Doğru yol: `.venv\Scripts\python.exe` (yani `uv sync --frozen --extra dev` sonrası), çünkü `uv run --frozen` **tek başına** `dev` extra'sını kurmaz ve `pytest` bulunamaz. |

### Geri alınan bir bulgu: `scripts/export_openapi.py`

Bu dosyanın ilk sürümü buraya dördüncü bir kusur yazmıştı: script'in dosya olarak
çalıştırıldığında kurulu `levh` paketini import ettiği ve bu yüzden `--check`'in
yanlış ağaca karşı "up to date" dediği iddiası.

**Bu iddia yanlıştı ve kayıttan çıkarıldı.** Yeniden üretildi ve script doğru
çalışıyor:

- `.venv` içinde `server` modülü worktree'ye çözülüyor
  (`__editable__.levh-2.32.0.pth` → `MAPPING = {'server': '...\\levh-gitconnector\\server'}`).
- Canlı şemaya gerçek bir parametre eklendiğinde `--check` **doğru şekilde stale
  döndürdü** (`rc=1`) ve `tests/test_openapi_contract.py` aynı anda kırıldı — yani
  ikisi tutarlı.
- `openapi.json` yeniden üretildiğinde `git diff` **boş**: script çıktıyı byte-sadık
  yazıyor, BOM eklemiyor (ilk baytlar `7B 0A 20`).
- Farklı bir çalışma dizininden, mutlak yolla çalıştırıldığında da aynı sonucu veriyor.

İlk turda görülen tutarsızlık, iddia edilen import kusurundan değil, birbirini izleyen
koşuların farklı şema durumlarını ölçmesinden kaynaklandı. `ROADMAP.md`'deki
`openapi-export-tree` satırı bu yüzden kaldırıldı.

**Ders:** "script yanlış ağacı ölçüyor" gibi bir teşhis, ancak yeniden üretilerek
kayda geçmeli. Bu turda önce yazıldı, sonra test edildi; sırası tersti.

### Bir süreç kusuru (kod değil)

PR #370, CodeRabbit'in bulduğu düzeltmeler push edilmeden **squash-merge edildi**.
Sonuç: düzeltmeler main'e girmedi, branch head ilerledi ama GitHub'da koşacak bir PR
kalmadığı için **hiç CI tetiklenmedi**. Düzeltmeler main'de canlıydı ve yalnızca elle
karşılaştırmayla fark edildi; takip PR'ı #371 ile kapandı. Ajan ve insan aynı PR'ı
merge ediyorsa bu tekrar olur.

---

## Öneri sırası

Her satır **çalıştırılabilir** bir iş; "araştır" maddesi yok.

| # | İş | Neden bu sırada | Boyut |
| --- | --- | --- | --- |
| 1 | **git connector'ı çalıştır** (`/api/connectors/sync`, `repo_path` ile) ve commit geçmişini bir projeye bas | Araç hazır; tek eksik çalıştırmak. Bu, **2. maddenin "hafıza besleniyor" kısmını gerçekten kapatır** ve trust/conflict katmanını devreye sokar | Küçük |
| 2 | **`github` connector'ını çalıştır** (token + repo) | Kurulu ama hiç koşmamış; PR/issue beslemesi 2. maddenin diğer yarısı | Küçük |
| 3 | **Dynamic windowing'e grafik-farkındalık ekle** — `recall`'ın kullandığı entity-graph aday kaynağını `_rank_context_candidates`'a bağla | 3. maddenin kalan asıl eksiği; mevcut desen kopyalanabilir | Orta |
| 4 | **Adaptif bütçe** — `max_tokens` çağırana bağlı olmaktan çıksın | 3. maddenin ikinci eksiği | Orta |
| 5 | **`recall_log`'u besle** ve Faz 2 denetim yüzeyini buna dayandır | Alttaki önkoşul; zemin zaten var | Orta |
| 6 | **Continuity ölçümü** (ROADMAP Faz A) | Tenancy'e bağlı değil, "bugün başlanabilir" | Orta |
| 7 | **Roller + workspace paylaşımı** (Faz 2) ve ardından Team Memory | **Sahibin kararına bağlı** — blokaj teknik değil | Büyük |

**1 ve 2 bugün yapılabilir ve ölçülebilir sonuç üretir.** 3 ve 4, 3. maddeyi
istenen cümleye tamamlar. 7, `SHARED-MEMORY-DESIGN.md`'nin dört sorusu
yanıtlanmadan başlamamalı — kimlik, depolamadan önce tasarlanmalı.

> **Bu listenin nerede yaşadığı.** Bu dosya ve `ROADMAP.md` işi *kaydeder*, ama
> kimseye *atamaz*: ikisi de bir sıra numarası olan bir kuyruk değil. Başka bir
> ajan ya da geliştirici "sıradaki iş ne?" sorusunu buradan okuyabilir, ancak
> üstlenilecek bir birim (assignee, durum, yorum) yok. Bu depo `CONTRIBUTING.md`
> ile zaten issue-önce çalışıyor; bu yedi maddeyi **issue** olarak açmak, işi
> hem keşfedilebilir hem de devredilebilir yapar.
