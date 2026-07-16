# StackMemory → Kişisel Dijital Hafıza — Geliştirme Planı & Nirvana Yol Haritası

> **Belge amacı:** StackMemory 2.3.1'i "AI kodlama araçları için yerel hafıza katmanı"ndan
> **"bir insanın iş hayatının dijital hafızası / ikinci beyni"** ürününe taşıyacak
> detaylı, fazlara bölünmüş, ölçülebilir bir geliştirme planı.
>
> **Sürüm:** v1 · **Temel alınan kod:** stackmemory 2.3.1 (release-blockers-fixed)

---

## 0. Varsayımlar (bunları ben seçtim — istediğini değiştir)

Planın yönünü belirleyen 4 çatalı, en tutarlı "nirvana" yönüne göre kilitledim.
Katılmadığın maddeyi söyle, o dalı yeniden yazayım.

| # | Karar | Seçtiğim yön | Neden |
|---|-------|--------------|-------|
| 1 | **Hedef kitle** | Önce ben, sonra ürün | Kendi hafızanla dogfood edersin; olgunlaşınca ürünleşir. Risk düşük, öğrenme hızlı. |
| 2 | **Gizlilik/veri** | Local-first + opsiyonel E2E şifreli senkron | Local-first zaten en güçlü ayrıştırıcın. Şifreli senkron çoklu cihaz açar ama gizliliği bozmaz. |
| 3 | **Yakalama** | İzinli connector'lar (orta agresiflik) | "İş hayatının dijitali" için manuel yetmez; tam ekran-kaydı ise gizlilik/mühendislik kabusu. Ortası en yüksek değer/risk oranı. |
| 4 | **Platform** | Masaüstü beyin + mobil yakalama companion | İş hayatı masaüstünde yaşar ama fikirler/sesler/fotoğraflar telefonda doğar. |

---

## 1. Vizyon: Ne inşa ediyoruz?

### Bugün (2.3.1)
> AI araçları unutmasın diye **AI'ın** yerel hafıza katmanı.

### Hedef (Nirvana)
> **Senin** iş hayatını hatırlayan, insan hafızası gibi çalışan (unutan, pekiştiren,
> çelişkileri çözen) kişisel ikinci beyin. AI araçların da bu beyni okuyup yazabilir.

**Tek cümle ürün:** *"Hayatının çalışan hafızası — insan gibi unutur, ama önemliyi asla kaçırmaz."*

### Neyi cevaplayabilmeli (jobs-to-be-done)
- "Geçen çeyrekte X müşterisiyle neler konuştuk, ne sözü verdim?"
- "Bu mimari karara neden vardık, alternatif neydi?"
- "Ahmet'le en son ne zaman görüştüm, ne konuştuk, ne bekliyor?"
- "Bu hafta ne yaptım? Yarınki toplantıya ne hazırlamalıyım?"
- "6 ay önce çözdüğüm o auth hatasının çözümü neydi?"

Bu sorular bugünkü StackMemory'de **kısmen** cevaplanabiliyor (recall var) ama
**kişiler, olaylar, zaman ekseni ve otomatik yakalama** olmadan "iş hayatının dijitali"
olamaz. Plan tam olarak bu boşlukları kapatıyor.

---

## 2. Farklılaştırıcı: Neden bu, Rewind/Limitless/Mem0'dan iyi?

| Rakip | Ne yapıyor | Zayıf noktası | StackMemory'nin cevabı |
|-------|-----------|---------------|------------------------|
| **Rewind / Limitless** | Her şeyi sonsuza kaydet (ekran/ses) | Sonsuz gürültü, gizlilik korkusu, "hatırlama" değil "arama" | **İnsan gibi unutur** — decay/reinforce ile sinyal yüzeye çıkar |
| **Mem0 / Zep** | AI agent hafızası (developer) | Kişisel yaşam ürünü değil, kara kutu | Kişisel + **açıklanabilir** (H-score, forgetting curve) |
| **Notion / Obsidian** | Manuel ikinci beyin | Elle doldurursun, unutmaz ama curate etmez | **Otomatik yakalar + kendini curate eder** |
| **Google/Microsoft Copilot** | Bulut, kurumsal | Verin onların | **Local-first, private by default** |

**Üç kalıcı ayrıştırıcı:**
1. **İnsan-hafıza modeli** (decay + reinforcement + interference + consolidation) — bir
   *bütün hayata* uygulanmış. Rakipler ya sonsuz saklar ya kara kutu. Sen "unutan ama
   pekişen" bir beyin sunuyorsun. Bu **teknik olarak zaten sende var**, kimsede yok.
2. **Açıklanabilirlik** — neden bu anıyı getirdim (score breakdown), ne kadar hatırlıyorum
   (forgetting curve). Kişisel hafızada güven = her şey.
3. **Local-first + MCP-native** — hem gizlilik hem "AI'ın da senin beynini kullanması".

---

## 3. Boşluk analizi: Var olan vs. gereken

| Yetenek | 2.3.1 durumu | "Dijital hafıza" için gereken |
|---------|--------------|-------------------------------|
| Hafıza store/recall | ✅ Sağlam | Korunur |
| İnsan-hafıza modeli | ✅ Güçlü (decay/reinforce/feedback/interference) | Genişletilir (bkz. Faz 5) |
| **Otomatik yakalama** | ⚠️ Sadece git commit | 🔴 **En büyük boşluk** — takvim/mail/toplantı/döküman/mesaj |
| **Kişi & varlık grafiği** | ⚠️ Sadece flat memory + tag | 🔴 People/Org/Project/Event/Decision entity graph |
| **Zaman ekseni (timeline)** | ⚠️ created_at var, görünüm yok | 🔴 Episodik zaman görünümü |
| **Hayatına soru sor (Q&A)** | ⚠️ Recall var, sentez yok | 🔴 Alıntılı doğal dil Q&A |
| **Proaktif hafıza** | ❌ Yok | 🔴 Günlük brief, toplantı öncesi hazırlık, takip |
| **Şifreleme (at-rest)** | ❌ Düz SQLite | 🔴 Kişisel veri için zorunlu |
| **Senkron / mobil** | ❌ Yok | 🟡 Çoklu cihaz için |
| **Onboarding (non-dev)** | ⚠️ Dev-flavored dashboard | 🟡 Ürünleşme için |
| **Güven/provenance** | ⚠️ source etiketi var | 🟡 Confidence + çelişki tespiti |

Renk: 🔴 kritik yeni · 🟡 önemli · ⚠️ kısmi · ✅ hazır

---

## 4. Hedef mimari (katmanlı)

```
┌─────────────────────────────────────────────────────────────┐
│  YAKALAMA (Ingestion)                                        │
│  Calendar · Email · Meetings(Whisper) · Docs · Chat · Browser│
│  Mobile: ses/foto(OCR)/not/konum · Git · Manuel              │
└───────────────┬─────────────────────────────────────────────┘
                ▼  normalize → Memory + provenance
┌─────────────────────────────────────────────────────────────┐
│  ÇIKARIM (Extraction)                                        │
│  Entity NER+LLM · İlişki · Karar/Söz tespiti · Çelişki       │
└───────────────┬─────────────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────────────┐
│  HAFIZA ÇEKİRDEĞİ (mevcut, güçlendirilecek)                  │
│  3-katman · H-score · decay/reinforce · vector · SQLite      │
│  + Knowledge Graph (People/Org/Project/Event/Decision/Task)  │
└───────────────┬─────────────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────────────┐
│  ANLAM (Intelligence)                                        │
│  Ask-your-life Q&A(RAG+alıntı) · Timeline · Brief · Follow-up│
└───────────────┬─────────────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────────────┐
│  ARAYÜZ                                                      │
│  Masaüstü dashboard · MCP(29+ tool) · REST/WS · Mobile app   │
└─────────────────────────────────────────────────────────────┘
        │ hepsi: E2E şifreli · opsiyonel cihazlar-arası senkron │
```

Kritik ilke: **çekirdek değişmiyor, üstüne katman ekleniyor.** Mevcut engine, H-score,
MCP yüzeyi olduğu gibi kalır; yakalama/çıkarım/anlam katmanları onun *üstüne* biner.

---

## 5. Fazlı yol haritası

Her faz **tek başına kullanılabilir** bir değer bırakır (shippable). Süreler tek
geliştirici + AI eşliğinde kaba tahmindir.

### FAZ 0 — Kişisel-güvenlik temeli (~1 hafta)
*Amaç: kişisel veri koymadan önce güvenli zemin.*
- ✅ **Şifreli backup/restore (YAPILDI — v2.12.0):** tam snapshot (tüm memory'ler decay
  durumuyla + session'lar); opsiyonel passphrase ile at-rest şifreleme (PBKDF2 + Fernet/
  AES-128, authenticated → yanlış parola/bozuk dosya sessizce değil, açıkça hata verir).
  `/api/backup` + `/api/restore`, `create_backup`/`restore_backup` MCP araçları, dashboard
  Settings **Backup & Restore** paneli (merge/replace). `cryptography` core bağımlılığa eklendi.
- **At-rest DB şifreleme (kalan):** tüm SQLite dosyasının SQLCipher ile şifrelenmesi
  (opsiyonel `STACKMEMORY_ENCRYPTION_KEY`). Backup şifrelemesi geldi; canlı DB şifrelemesi
  sıradaki adım.
- ✅ **Gerçek hard-delete + redaction (YAPILDI — v2.18.0):** `audit_deletion` bir memory'nin
  3 katmanın herhangi birinde kalıp kalmadığını denetler; `purge_memory` hard-delete + post-
  condition audit ("gerçekten silindi" kanıtlanır). `audit_secrets`/`redact_memory`/
  `redact_all_secrets` gate öncesi sızmış secret'ları bulup redakte eder (redaction_history'ye
  loglar; assignment-tipi için idempotent). `/api/memories/audit-secrets|redact-all|{id}/redact|
  {id}/purge` + `audit_secrets`/`redact_secrets`/`purge_memory` MCP + `stackmemory audit-secrets|
  redact-secrets|purge` CLI + Settings Privacy kartı.
- ✅ **Cihaz kilidi (single-user auth) — TAM:** `STACKMEMORY_TOKEN` + CORS localhost kilidi
  api.py'de aktif; 2.23A'da UX'e bağlandı — `X-StackMemory-Token` header'ı her `/api/*`
  çağrısında, `?token=` WS handshake'inde, `AuthGate` + Settings token yönetimi + `/api/health`
  `auth_required` bayrağı.
- **Reviewer'ın bulduğu `test_cli.py` subprocess timeout'unu düzelt** (test izolasyonu).
- **Çıktı:** "kişisel verimi koyabilirim" güveni + yeşil CI.

### FAZ 1 — Yakalama katmanı (~3-4 hafta) 🔴 en büyük değer
*Amaç: hafıza kendi kendine dolmaya başlasın.*
- ✅ **Memory Admission Gate (YAPILDI — v2.16.0):** kaynak sayısı artmadan ÖNCE gelen
  kalite kapısı — "hangi bilgi hafızaya alınır?". admit / review / reject (duplicate) /
  redact (secret temizleme; e-posta korunur). Deterministik/offline. `admission.evaluate`
  + `engine.evaluate_admission`/`admit_memory` + `/api/memories/admit|evaluate-admission`
  + `admit_memory`/`evaluate_admission` MCP + `stackmemory admit` CLI + Settings önizleme.
  *Connector framework v2 bunun üstüne kurulacak (ingest → admission gate).*
- ✅ **Connector framework v2 (YAPILDI — v2.17.0):** gate-entegre artımlı ingest —
  her item admission gate'ten geçer (dedup + secret redaction), item-bazlı hata
  izolasyonu, `connector_sync` tablosunda sync defteri (re-sync artımlı ve raporlanabilir).
  `engine.ingest_items`/`list_sync_state` + `/api/connectors/sync|sync-state` +
  `sync_connector`/`connector_sync_status` MCP + `stackmemory sync` CLI + Settings toggle.
  *Kalan: zamanlama (scheduler/cron) + rate-limit — otomatik periyodik sync.*
- ✅ **Calendar connector (YAPILDI — v2.5.0):** iCalendar `.ics` (dosya veya yayınlanmış
  URL) ayrıştırıcısı; toplantılar → Event memory'leri (katılımcı, zaman, başlık, konum,
  not). Sıfır bağımlılık, tamamen offline, `past_days`/`future_days` pencere filtresi.
  Registry + REST + MCP + dashboard'a bağlı. Sıradaki: Google/Outlook canlı API (OAuth).
- ✅ **Email connector (YAPILDI — v2.6.0):** `.mbox` / `.eml` ayrıştırıcısı (Gmail
  Takeout, Thunderbird, Apple Mail, Outlook export) — stdlib `email`/`mailbox`, sıfır
  bağımlılık, offline, IMAP/OAuth yok. Her mesaj → memory (gönderen/alıcı/konu/tarih/
  gövde özeti), `exclude_senders` ile gürültü filtresi. Sıradaki: canlı Gmail/IMAP API.
- ✅ **Toplantı transkripti (YAPILDI — v2.7.0):** `.vtt`/`.srt`/`.txt` transkript
  ayrıştırıcısı (Zoom/Meet/Teams/Otter/Fireflies/Whisper). Her toplantı → tek özet
  memory (konuşmacılar + özet, summarizer ile — LLM/offline), sıfır bağımlılık.
  Sıradaki: yerel ses → Whisper otomatik transkripsiyon; karar/söz çıkarımı.
- **Döküman senkronu:** local klasör izleme (watchdog) + Drive/Dropbox opsiyonel;
  mevcut local_files connector'u artımlı hale getir.
- **Chat export** (Slack/Telegram/WhatsApp export dosyaları) → memory.
- **Çıktı:** Bir haftalık kullanımda hafıza elle girmeden dolar. "İş hayatının dijitali"
  hikayesi burada gerçek olur.

### FAZ 2 — Bilgi grafiği & varlıklar (~3 hafta)
*Amaç: flat memory → bağlı bilgi ağı.*
- ✅ **Kişi grafiği (YAPILDI — v2.8.0):** yakalanan metadata'dan (takvim katılımcıları,
  e-posta from/to, transcript konuşmacıları) kişiler otomatik çıkarılıyor; email ile
  kimlik birleştirme. `/api/people`, `list_people`/`about_person` MCP araçları, dashboard
  **People** sayfası ("X hakkında ne biliyorum" + tek tık Ask özeti). Sıradaki: serbest
  metinden LLM/NER ile kişi çıkarımı.
- ✅ **Timeline (YAPILDI — v2.9.0):** günlük gruplu aktivite görünümü (/api/timeline,
  timeline MCP tool, dashboard Timeline sayfası).
- ✅ **Organizations (YAPILDI — v2.11.0):** kişi grafiği e-posta domain'ine göre
  kurumlara toplanıyor (`mail.acme.co.uk` → "Acme"); kişisel e-posta sağlayıcıları
  hariç. `/api/organizations[/{key}]`, `list_organizations`/`about_organization` MCP
  araçları, dashboard **Organizations** sayfası (kurum profili + kişiler + memory'ler).
- ✅ **Decisions (YAPILDI — v2.11.0):** episodik içerikten karar cümleleri deterministik
  yakalanıyor ("we decided / agreed to / karar verdik / üzerinde anlaştık"); ne, ne zaman,
  nerede. `/api/decisions`, `list_decisions` MCP aracı, dashboard **Decisions** sayfası.
- ✅ **Entity modeli + ilişki grafiği (YAPILDI — v2.19.0):** kalıcı `entities` +
  `memory_entities` tabloları; tipler person / organization / event / document / task.
  `extract_entities` (deterministik) + `reindex_entities`/`list_entities_graph`/`get_entity`
  engine + `/api/entities/*` + `reindex_entities`/`list_entities`/`about_entity` MCP +
  `stackmemory entities` CLI + dashboard **Graph** sayfası. Co-occurrence sorguları join ile:
  "bu toplantıda kimler vardı", "X kişisi hangi kurumla/dokümanla bağlı".
- **Çıkarım pipeline'ı:** her yeni memory'de entity extraction (LLM varsa yapılandırılmış,
  yoksa deterministik NER/regex fallback — mevcut "her zaman çalışır" felsefesine sadık).
- **Graph sorguları:** "X kişisiyle ilgili her şey", "Y projesinin zaman çizelgesi",
  "bu toplantıda kimler vardı". `related_memories` zaten tohum — grafiğe bağla.
- **UI:** entity sayfaları (kişi/proje profili), memory detayında "bağlı varlıklar".
- **Çıktı:** "Ahmet hakkında ne biliyorum?" tek tıkla cevaplanır.

### FAZ 3 — Hayatına soru sor + proaktif (~3-4 hafta) 🔴 killer feature
*Amaç: hafıza pasif depo değil, aktif asistan olsun.*
- **Ask-your-life Q&A:** doğal dil sorusu → ilgili memory'ler (recall) → LLM sentezi
  → **alıntılı** cevap (her iddia hangi memory'ye/tarihe dayanıyor). OpenAI/Ollama/yerel.
- **Timeline görünümü:** "bu hafta/ay ne oldu" episodik zaman şeridi.
- ✅ **Günlük/haftalık brief:** "Bugün şu etkinliklerin var; şu sözler açık; şunları
  unutmak üzeresin." (v2.10.0 — `briefing()` engine + `/api/briefing` + `briefing`
  MCP tool + dashboard **Briefing** sayfası; deterministik/offline, LLM opsiyonel.)
- ✅ **Söz/takip tespiti:** "…yapacağım / göndereceğim / I'll / follow-up" ifadelerini
  yakala → açık taahhütler listesi (v2.10.0 briefing içinde). *Kalan: cron + e-posta.*
- ✅ **Toplantı öncesi hazırlık (YAPILDI — v2.13.0):** sıradaki toplantı (veya sorguyla
  seçilen) için otomatik brief — katılımcılar, her biriyle en son ne konuştuğun, ilgili
  açık taahhütler ve kararlar. `meeting_prep()` engine + `/api/meeting-prep` + `meeting_prep`
  MCP aracı + dashboard **Meeting Prep** sayfası. Deterministik/offline.
- **Söz/takip tespiti:** "…yapacağım / göndereceğim" ifadelerini yakala → açık taahhütler
  listesi. Yerine getirilmeyeni hatırlat.
- **Çıktı:** ürün "hatırlatıcı + danışman" hissi verir; günlük dokunuş noktası oluşur.

### FAZ 4 — Mobil companion & senkron (~4 hafta)
*Amaç: hafıza her yerde yakalanır, her cihazda erişilir.*
- **E2E şifreli senkron protokolü:** cihazlar arası; sunucu sadece şifreli blob taşır
  (zero-knowledge). Çakışma çözümü (CRDT veya last-write-wins + audit).
- **Mobil uygulama** (React Native/Expo veya Flutter): ses memo → transkript, foto → OCR,
  hızlı not, konum etiketi. Offline yakala, senkronda gönder.
- **Çıktı:** telefonda aklına geleni yakala, masaüstü beyninde bul. Daha önce konuştuğun
  "telefon/appliance" fikrinin yazılım temeli.

### FAZ 5 — Hafıza modelini derinleştir (paralel, sürekli)
*Amaç: ayrıştırıcıyı bilimsel olarak keskinleştir.*
- ✅ **Çelişki adayı işaretleme (deterministik yarısı YAPILDI — v2.21.0):** aynı entity'yi
  paylaşan + zıt yüzey pattern'i (antonym / negation / attribute_value) taşıyan hafıza
  çiftleri **çelişki ADAYı** olarak işaretlenir (verdict değil, insan review'a düşer;
  auto-delete yok). Open aday trust'a küçük risk ekler. `memory_conflict_candidates`
  tablosu + `conflict.py` + `detect/list/review_conflict_candidate` engine +
  `/api/conflicts/*` + 3 MCP + `stackmemory conflicts` CLI + dashboard **Conflicts** sayfası.
  *Kalan (opsiyonel, gated & default-off): LLM ile "bu iki memory kesin çelişiyor mu"
  adapter'ı — offline deterministik çizgiyi bozmamak için varsayılan kapalı.*
- ✅ **Consolidation/özetleme (YAPILDI — v2.14.0):** eski, birbirine yakın memory
  kümelerini tek "consolidated memory"ye sıkıştır (insan uykuda hafıza konsolidasyonu
  gibi) — LLM varsa LLM, yoksa extractive. Ham olanlar `metadata.consolidated_from`
  içinde arşivleniyor (kaybolmuyor), özet aktif kalıyor. Pinned ve yeni (< min_age_days)
  hafızalar korunuyor. `consolidate_memories()` engine + `/api/memories/consolidate-similar`
  + `consolidate_similar` MCP aracı + Settings butonları.
- ✅ **Spaced-repetition review (YAPILDI — v2.15.0):** fading kuyruğu artık proaktif
  review akışı — keep / reinforce / weaken / pin / snooze / forget; her karar
  `metadata.review_history`'de kayıtlı. Yaşam döngüsü kapandı: store → recall → decay →
  review → reinforce/weaken/forget. `review_queue()`/`apply_review()` engine +
  `/api/memories/review[/{id}]` + `list_review_memories`/`review_memory` MCP araçları +
  `stackmemory review` CLI + dashboard **Review** sayfası.
- ✅ **Güven skoru (provenance) (YAPILDI — v2.20.0):** deterministik, açıklanabilir,
  offline confidence — `0.30·source + 0.25·corroboration + 0.20·review + 0.15·recency
  − 0.10·risk`; corroboration entity graph üzerinden (aynı entity kaç DISTINCT kaynak
  türünde geçiyor). H-score'dan ayrı, recall sıralamasını değiştirmiyor, "truth" iddiası
  değil. `memory_trust_scores` tablosu + `recompute_trust_scores`/`get_trust`/`list_low_trust`
  engine + `/api/memories/{id}/trust|trust/recompute|low-trust` + 3 MCP + `stackmemory trust`
  CLI + Settings kartı. Senin "HUQAN trust" fikrinin oturduğu deterministik sinyal katmanı:
  StackMemory hatırlar → trust score açıklar → HUQAN ileride yargılar.

### FAZ 6 — Ürünleşme (opsiyonel, product moduna geçersen)
- Onboarding sihirbazı (non-dev): hesapları bağla → hafıza dolsun → ilk soruyu sor.
- Multi-user / tenant izolasyonu, auth (OAuth), faturalama.
- Bulut opsiyonu (yine E2E şifreli varsayılan).
- Landing + demo + fiyatlandırma.

---

## 6. Veri modeli evrimi

### Memory (mevcut — korunur, birkaç alan eklenir)
```
+ entity_ids: list        # bağlı varlıklar
+ confidence: float       # provenance güven skoru (0-1)
+ captured_at: str        # olayın gerçek zamanı (created_at = kayıt zamanı)
+ location: str | null    # mobil/konum
+ redacted: bool          # PII redaction işareti
```

### Yeni: Entity
```
id · type(Person/Org/Project/Event/Document/Decision/Task)
name · aliases[] · attributes{} · first_seen · last_seen · memory_count
```

### Yeni: EntityLink (memory ↔ entity, entity ↔ entity)
```
source_id · target_id · relation(mentions/attended/decided/owns/relates)
weight · created_at
```

### Yeni: CaptureSource (connector durumu)
```
name · type · last_synced_at · cursor · status · config(şifreli)
```

Hepsi mevcut auto-migration desenine eklenir (ALTER TABLE on connect) — geriye uyumlu.

---

## 7. Gizlilik & güven modeli (kişisel hafızanın kalbi)

Kişisel iş hafızası = en hassas veri. Bu bir özellik değil, **temel ilke**:

1. **Private by default:** hiçbir şey varsayılan olarak cihazdan çıkmaz.
2. **At-rest şifreleme:** SQLCipher; anahtar kullanıcının (parola/OS keychain).
3. **E2E senkron:** sunucu asla düz metin görmez (zero-knowledge).
4. **Granular capture control:** her connector ayrı ayrı aç/kapa; "bu klasörü/etiketi
   yakalama" dışlama kuralları.
5. **Gerçek unutma:** "forget" gerçekten siler (export dahil). "Redact" PII'yi maskeler.
6. **Şeffaflık:** ne yakalandı, nereden geldi, ne zaman — her memory'de görünür (source
   zaten var, genişlet).
7. **Yerel LLM opsiyonu:** Ollama ile Q&A/çıkarım tamamen offline yapılabilsin — hiçbir
   veri OpenAI'a gitmesin isteyen için.

---

## 8. "Nirvana" — kuzey yıldızı özellikleri

Bunlar ürünü *sıçratır* (her biri bir faza dağılmış ama vizyon olarak):

- **"Hayatını sorgula":** "2024'te en çok kiminle çalıştım?" → grafik + alıntı.
- **Zamanda geri sarma (ama akıllı):** Rewind gibi ama sonsuz gürültü değil — decay ile
  sadece önemli kalır, unutulanı "arşivden" isteyerek çağır.
- **Proaktif beyin:** sabah brief'i + toplantı öncesi hazırlık + açık sözler.
- **Çelişki çözücü:** "Geçen ay 'X' dedin ama bugün 'Y' — hangisi?" → hafızan tutarlı kalır.
- **AI'ının da beyni:** Claude/Cursor MCP ile senin hayat-hafızanı okur; "bana benim
  gibi yaz", "geçen sefer ne karar vermiştik" — araç seni tanır.
- **Duygusal/bağlamsal ağırlık:** importance = insan hafızasındaki "salience"; önemli
  anlar (kilit kararlar, ilk tanışmalar) daha dayanıklı.

---

## 9. Riskler & önlemler

| Risk | Etki | Önlem |
|------|------|-------|
| Yakalama = gizlilik korkusu | Kullanıcı güvenmez | Local-first + E2E + granular kontrol + açık "sil" |
| Çıkarım LLM maliyeti/gizliliği | Pahalı veya veri sızar | Ollama/yerel opsiyon + deterministik fallback |
| Kapsam patlaması (her şeyi yakala) | Bitmez proje | Faz faz ship, her faz tek başına değerli |
| Bağlam/limit maliyeti (senin derdin) | Geliştirme yavaşlar | Küçük dilimler, her faz bağımsız oturum |
| Rakip hız (Limitless vs.) | Geç kalma | Ayrıştırıcıya odaklan: unutan + private + açıklanabilir |
| Gürültü (hafıza çöplüğe döner) | Recall bozulur | Decay + consolidation + dedupe zaten var — bel bağla |

---

## 10. Başarı metrikleri

- **Recall kalitesi:** hit@3, MRR (benchmark zaten var) — connector verisiyle ölç.
- **Yakalama kapsamı:** günde otomatik yakalanan memory / elle girilen oranı.
- **Aktif kullanım:** günlük Q&A sorusu / brief açılma oranı (dokunuş noktası).
- **Sinyal/gürültü:** fading kuyruğuna düşen vs. reinforce edilen oranı.
- **Güven:** "forget" sonrası gerçekten silindi doğrulaması; şifreleme aktif oranı.

---

## 11. Hemen başlangıç — ilk 2 hafta (somut)

Vizyonu beklemeden bugün değer üretecek dilim:

**Hafta 1 — Faz 0 + Faz 1 tohumu**
1. `test_cli.py` timeout'unu düzelt, CI'yı tam yeşile al.
2. SQLCipher opsiyonel şifreleme + `backup`/`restore` CLI.
3. Connector framework v2 iskeleti (artımlı senkron + scheduler).
4. **Calendar connector** (ilk gerçek yaşam yakalayıcı) — toplantılar Event memory olur.

**Hafta 2 — İlk "vay be" anı**
5. Toplantı transkripti: local ses → Whisper → özet + katılımcı çıkarımı.
6. Basit **Ask-your-life** ucu: `/api/ask` — soru → recall → LLM sentez + alıntı (Faz 3'ün
   MVP'si, connector verisiyle test).
7. Dashboard'a "Ask" kutusu + "Bu hafta" timeline kartı.

Bu 2 haftanın sonunda: **takvimin ve toplantıların otomatik hafızaya akıyor, onlara doğal
dille soru sorabiliyorsun.** "İş hayatının dijitali"nin ilk çalışan çekirdeği.

---

## 12. Benden öneri (dürüst yön)

- **En yüksek kaldıraç Faz 1 (yakalama).** UI ne kadar güzel olursa olsun, hafıza elle
  doluyorsa ürün ölü. Otomatik yakalama = kullanım alışkanlığı = her şey.
- **Ayrıştırıcın zaten kodda: unutan hafıza.** Bunu bir *bütün hayata* uygula ve
  pazarla — Rewind "her şeyi sakla" korkusu satarken sen "insan gibi unutan, private
  ikinci beyin" satarsın. Bu net bir konumlama.
- **Product'a acele etme.** Önce kendi hayatınla 1 ay dogfood et (Faz 0-3). Gerçek
  kullanımdan gelen sinyal, hayali özellik listesinden iyidir.
- **Donanım (B planı) hâlâ açık kapı:** local-first + Ollama + SQLite mimarisi bir
  appliance'a birebir uyar. Faz 4 mobil + senkron biterse, donanıma geçiş kod değil
  paketleme işi olur. Şimdi kovalamak yok; opsiyonu bedava koruyorsun.

---

## Sonraki adım

Bana "Faz 0'dan başla" ya da "önce şu dilimi göster" de — kod yazmaya geçeyim.
İstersen bu planı sürümleyip proje `docs/` altına da koyarım (kalıcı yol haritası olur).
Varsayımlardan (Bölüm 0) değiştirmek istediğin varsa, önce onu söyle; planı ona göre
yeniden şekillendiririm.
