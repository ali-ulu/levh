# Cross-agent, git enrichment and dynamic windowing: state and gaps

Tarih: 2026-10-03 · Kapsam: üç istenen yetenek · Tür: durum kaydı + boşluk envanteri

Bu dosya üç yeteneğin **bugünkü gerçek durumunu** ve **kalan boşlukları** kaydeder.
Bir öneri değil, bir ölçüm: her iddia commit `6f86231` üzerinde koda, canlı veritabanına
veya REST API'ye karşı doğrulandı, ve her iddianın kanıtı yanında yazılı.

`ROADMAP.md`'den farkı: orası "karar verilmiş, ertelenmiş" işleri tutar. Burası
**istenen ama henüz bir satırı olmayan** yetenekleri tutar — yani bir sonraki turda
"bunu yaptık mı?" sorusunun cevabı bir konuşmadan değil bu dosyadan okunur.

---

## 1. Cross-Agent / Team Memory — **çekirdek işbirliği katmanı uygulandı**

**İstenen:** Aynı projede paralel çalışan bağımsız ajanların (backend / frontend /
test) ortak bir hafıza bağlamından beslenmesi; işi açıkça devredebilmesi, ortak
kararları kalıcı tutabilmesi ve çelişkileri sessizce ezmeden yönetebilmesi.

**Uygulanan katmanlar:**

| Parça | Kanıt |
| --- | --- |
| Workspace sınırı + principal context | `memories.workspace_id`, `server/core/tenancy.py` |
| viewer / editor / admin enforcement | Phase 2 foundation, PR #395 |
| Recall access audit | `recall_log` principal/workspace damgası + `/api/v1/memories/{id}/access-audit` |
| Agent presence/checkpoint tenancy | `agent_sessions.workspace_id`, `agent_checkpoints.workspace_id` |
| Agent-to-agent handoff | `server/core/team_memory.py`, `team_handoffs`, `/api/v1/team/handoffs` |
| Shared decision ledger | `team_decisions`, `/api/v1/team/decisions` |
| Çelişki yönetimi | Aynı `project + decision_key` için farklı aktif kararlar `contested`; otomatik kazanan yok |
| Explicit resolution | Admin seçimiyle bir karar `active`, diğerleri `superseded` |
| Collaboration summary | Presence + checkpoints + handoffs + decisions + pending/contested sayaçları |

**Önemli semantik:** Bu katman da mevcut conflict motoru gibi **signal, not
verdict** ilkesini korur. İki ajan aynı karar anahtarına farklı ifadeler
yazdığında sistem son yazanı sessizce kazanan ilan etmez. Her iki öneri de
`contested` kalır; admin açıkça birini resolve eder.

**Kalan gerçek boşluklar:**

- Shared decision conflict şu anda **anahtar-temelli deterministik** koordinasyon:
  `decision_key` aynıysa çatışma görünür. Serbest metin kararlar arasında
  semantik conflict detection henüz `memory_conflict_candidates` ile
  birleştirilmedi.
- Handoff bir durable ledger ve lifecycle sağlar; otomatik görev planlayıcı /
  ajan seçici değildir. Kimin işi alacağı caller veya operatör kararıdır.
- OIDC/account provisioning hâlâ shared-server tasarımının ayrı opt-in fazıdır;
  local ürün `local/default/admin` olarak çalışmaya devam eder.
- Çok-hostlu concurrent writer ihtiyacı doğmadığı sürece Postgres trigger'ı
  hâlâ ateşlenmiş sayılmaz.

**Sonuç:** “Kaç ajan online?” seviyesinden gerçek team coordination primitive'lerine
geçildi: devir, ortak karar, contest ve açık çözüm artık kalıcı ve workspace
sınırında enforce ediliyor.

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
| 1 | **`recall_log` beslenmiyor** | Canlı DB'de **0 satır** | "Kim neyi okudu" denetim zemini var, veri yok. `SHARED-MEMORY-DESIGN.md` Faz 2'yi buna dayandırıyor. → issue [#376](https://github.com/ali-ulu/levh/issues/376), **çözüldü**: varsayılan artık açık (#382). |
| 2 | **Suite duvar-saati çözünürlüğüne bağımlı** (ilk kayıtta "sıra-bağımlı" yazıyordu — **yanlıştı**, aşağıya bak) | Global CPython 3.12'de `datetime.now()` çözünürlüğü **15.6 ms**; 4000 ardışık çağrıda yalnızca 2 farklı değer. venv 3.13'te **1e-07 s**. | Kırmızı sinyal güvenilmez; ayrıca **gerçek bir veri-doğruluk hatası** ortaya çıkardı (emeklilik boş geçerlilik penceresi üretebiliyor). → issue [#379](https://github.com/ali-ulu/levh/issues/379). |
| 3 | **Kilitli olmayan yorumlayıcı** | Ortam kurulumuyla ilgili üç tuzak; en sinsi olanı, `uv.lock` dışı bir `python`'ın import anında hata vermesi ve bunun **kod hatası gibi okunması** | Doğru yol: `.venv\Scripts\python.exe` (`uv sync --frozen --extra dev` sonrası), çünkü `uv run --frozen` **tek başına** `dev` extra'sını kurmaz. → issue [#380](https://github.com/ali-ulu/levh/issues/380), **çözüldü**: `docs/testing.md` (#384). |

### Düzeltme: "sıra-bağımlı suite" yanlış teşhisti

Bu dosyanın ilk sürümü 2. maddeyi **"test suite sıra-bağımlı"** olarak kaydetmişti.
Teşhis bunu çürüttü: **paylaşılan durum sızıntısı yok.** Tek bir test dosyası,
izole koşulda, başka hiçbir test çalışmamışken de kırılıyor.

Gerçek kök neden **duvar-saati çözünürlüğü**: `created_at` / `valid_from` /
`superseded_at` alanları `datetime.now(timezone.utc).isoformat()` ile üretiliyor
ve bu string'ler **katı (`>`) sıralama ve sınır anahtarı** olarak kullanılıyor.
Global CPython 3.12'de saat 15.6 ms'lik tick'lerle ilerliyor, dolayısıyla ardışık
iki yazma **aynı** zaman damgasını alabiliyor ve sıralamayı zaman değil tick
rastlantısı belirliyor. CI Linux'ta saat mikrosaniye çözünürlükte olduğu için
hata orada hiç görünmüyor.

Tek kök nedenin üç yüzeyi: `auto_checkpoint` delta'sı boş kalıyor, `ORDER BY
created_at DESC LIMIT 1` tie-break'siz olduğu için eski satırı döndürüyor, ve
emeklilikte `valid_to == valid_from` olup yarım-açık aralık **boş** kalıyor — bu
sonuncusu test meselesi değil, gerçek bir veri hatası.

**Ders:** "flaky" bir gözlem, kök nedeni hakkında bir iddia değildir. İlk kayıt
"hangi durum sızıyor?" diye sordu; doğru soru "bu sonucu ne belirliyor?" idi.

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

| # | İş | Neden bu sırada | Boyut | Issue |
| --- | --- | --- | --- | --- |
| 1 | **git connector'ı çalıştır** (`/api/connectors/sync`, `repo_path` ile) ve commit geçmişini bir projeye bas | Araç hazır; tek eksik çalıştırmak. Bu, **2. maddenin "hafıza besleniyor" kısmını gerçekten kapatır** ve trust/conflict katmanını devreye sokar | Küçük | [#374](https://github.com/ali-ulu/levh/issues/374) |
| 2 | **`github` connector'ını çalıştır** (token + repo) | Kurulu ama hiç koşmamış; PR/issue beslemesi 2. maddenin diğer yarısı | Küçük | [#374](https://github.com/ali-ulu/levh/issues/374) |
| 3 | **Dynamic windowing'e grafik-farkındalık ekle** — `recall`'ın kullandığı entity-graph aday kaynağını `_rank_context_candidates`'a bağla | 3. maddenin kalan asıl eksiği; mevcut desen kopyalanabilir | Orta | [#375](https://github.com/ali-ulu/levh/issues/375) |
| 4 | **Adaptif bütçe** — `max_tokens` çağırana bağlı olmaktan çıksın | 3. maddenin ikinci eksiği | Orta | [#375](https://github.com/ali-ulu/levh/issues/375) |
| 5 | **`recall_log`'u besle** ve Faz 2 denetim yüzeyini buna dayandır | Alttaki önkoşul; zemin zaten var | Orta | [#376](https://github.com/ali-ulu/levh/issues/376) |
| 6 | **Continuity ölçümü** (ROADMAP Faz A) | Tenancy'e bağlı değil, "bugün başlanabilir" | Orta | [#378](https://github.com/ali-ulu/levh/issues/378) |
| 7 | **Roller + workspace paylaşımı** (Faz 2) ve ardından Team Memory | **Sahibin kararına bağlı** — blokaj teknik değil | Büyük | [#377](https://github.com/ali-ulu/levh/issues/377) |

Ayrıca bu dosyanın kaydettiği iki kusur kendi issue'larına sahip:
[#379](https://github.com/ali-ulu/levh/issues/379) (sıra-bağımlı test suite) ve
[#380](https://github.com/ali-ulu/levh/issues/380) (kilitli `uv` ortamı ve üç tuzağı).

**1 ve 2 bugün yapılabilir ve ölçülebilir sonuç üretir.** 3 ve 4, 3. maddeyi
istenen cümleye tamamlar. 7, `SHARED-MEMORY-DESIGN.md`'nin dört sorusu
yanıtlanmadan başlamamalı — kimlik, depolamadan önce tasarlanmalı.

> **Bu listenin nerede yaşadığı.** Bu dosya ve `ROADMAP.md` işi *kaydeder*, ama
> kimseye *atamaz*: ikisi de bir sıra numarası olan bir kuyruk değil. Bir okuyucu
> "sıradaki iş ne?" sorusunu buradan öğrenebilir, ancak üstlenilecek bir birim
> (assignee, durum, yorum) yoktur.
>
> Bu yüzden yukarıdaki maddeler **issue olarak açıldı** — keşfedilebilir ve
> devredilebilir olsunlar diye. Kuyruk artık
> [açık issue'lar](https://github.com/ali-ulu/levh/issues). Bu dosya *neden*
> olduğunu ve kanıtını tutar; issue'lar *ne yapılacağını* ve durumunu tutar.
> Biri diğerinin yerine geçmez.
