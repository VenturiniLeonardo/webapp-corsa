# Piano tecnico — Piattaforma personale di analisi della corsa

> Versione 1.0 — 2026-09-30
> Documento di riferimento per lo sviluppo con Claude Code. Legenda usata nel testo:
> **[FATTO]** verificato su fonte (vedi §Fonti) · **[ASSUNZIONE]** · **[DECISIONE]** · **[RACCOMANDAZIONE]** · **[NON VERIFICATO]**

---

## 1. Executive summary

Una web app personale, single-user, che importa automaticamente le corse registrate con **Apple Watch (app Allenamento nativa) → Apple Salute → Strava**, le salva in un modello dati indipendente dal provider, calcola metriche proprie dagli stream (split, best effort, zone cardiache, efficienza) e le presenta in una dashboard dark-only orientata all'analisi.

Scelte chiave:

- **Sorgente primaria: Strava API**, con polling ogni 30 minuti e backfill iniziale via API. Il volume stimato (circa 150–250 corse) sta nei limiti di rate in una sola sessione di circa 1–2 ore, quindi non serve l'export in bulk.
- **Accesso privato via Tailscale** (rete privata WireGuard, piano gratuito). L'app è raggiungibile solo dai tuoi dispositivi, a casa e da smartphone ovunque. HTTPS valido sul dominio `*.ts.net` senza comprare un dominio, e nessuna porta pubblica aperta.
- **Stack**: Python (FastAPI) + SQLite + un processo worker; React + TypeScript + Vite + ECharts + MapLibre/OpenFreeMap. Due container Docker Compose su una sola VPS ARM.
- **Nessun servizio a pagamento.** L'unica dipendenza esterna critica è Strava.

Tre scoperte della ricerca cambiano il quadro rispetto alle attese:

1. **Strava non riceve la cadenza dagli allenamenti dell'app Allenamento Apple** [FATTO, dichiarazione staff Strava 2023, da riconfermare con dati reali]. Grafici e trend di cadenza richiedono una seconda sorgente: file FIT esportati con un'app come HealthFit, oppure l'export di Apple Salute. Per questo l'import di file è la **prima milestone dopo l'MVP** e il modello dati supporta più sorgenti per attività fin dall'inizio.
2. **Oracle Always Free per le A1 oggi è 2 OCPU / 12 GB** (1.500 OCPU-ore e 9.000 GB-ore al mese) [FATTO]. La tua VM è già al massimo. Oracle può inoltre **recuperare le istanze Always Free inattive** (CPU, rete e memoria sotto il 20% per 7 giorni) [FATTO], e questa app sarà quasi sempre inattiva. Per questo backup fuori dalla VPS e ripristino rapido sono obbligatori, non opzionali (§17, §23).
3. **"Accesso solo dalla rete di casa" non è realizzabile così com'è**: la VPS non sta nella tua rete di casa. Un filtro sull'IP pubblico di casa si romperebbe col cambio di IP dinamico e bloccherebbe lo smartphone in mobilità. Tailscale realizza l'intento (niente esposto su internet) in modo più robusto. Conseguenza: niente webhook Strava, che richiede un endpoint pubblico; si usa il polling.

---

## 2. Requisiti funzionali

| ID | Requisito | Priorità |
|----|-----------|----------|
| RF-01 | Collegare l'account Strava via OAuth (sola lettura, incluse attività private) | Must |
| RF-02 | Import storico completo delle corse da Strava | Must |
| RF-03 | Sincronizzazione automatica periodica + pulsante "Sync now" | Must |
| RF-04 | Recepire modifiche e cancellazioni fatte su Strava (riconciliazione) | Must |
| RF-05 | Solo corsa: Run, Trail Run, corsa su tapis roulant/virtuale. Le altre attività vengono ignorate | Must |
| RF-06 | Deduplicazione: nessuna attività doppia, rilevamento sovrapposizioni | Must |
| RF-07 | Lista attività con ricerca, filtri, ordinamento | Must |
| RF-08 | Dettaglio attività: summary, mappa, grafici sincronizzati, split, lap, zone, best effort, confronto con corse simili, metadata e sorgente | Must |
| RF-09 | Dashboard dinamica con selettore di periodo e grafici di volume, frequenza, ritmo, FC, efficienza, distribuzione, record | Must |
| RF-10 | Record personali e progressione (1K, 5K, 10K, mezza) | Must |
| RF-11 | Note, tag, tipo allenamento (easy/long/workout/race) modificabili; non vengono sovrascritti dalle sincronizzazioni | Must |
| RF-12 | Impostazioni FC (max, riposo, zone) con ricalcolo delle metriche | Must |
| RF-13 | Pagina Sync: stato connessione, job, errori, retry | Must |
| RF-14 | Upload FIT/GPX/TCX con arricchimento di attività esistenti (es. cadenza) | Should |
| RF-15 | Risoluzione manuale dei duplicati (merge/escludi) | Should |
| RF-16 | Analisi avanzate: FC a ritmo di riferimento, decoupling aerobico, trend cadenza | Should |
| RF-17 | Export completo dei dati e cancellazione totale | Should |
| RF-18 | Import di Apple Salute `export.zip` | Could |
| RF-19 | Modelli di carico (fitness/fatigue), predittore gara, heatmap percorsi, meteo | Could |

## 3. Requisiti non funzionali

| ID | Requisito | Target verificabile |
|----|-----------|---------------------|
| RNF-01 | Costo | 0 €/mese (Oracle Always Free, Tailscale Personal, OpenFreeMap, healthchecks.io free) |
| RNF-02 | Risorse | RAM totale app < 1 GB a regime; CPU < 5% medio |
| RNF-03 | Performance | API della dashboard < 300 ms p95 con 1.000 attività; pagina di dettaglio < 1 s |
| RNF-04 | Affidabilità dei dati | RPO 24 h (backup notturno); RTO < 2 h su una VM nuova seguendo il RUNBOOK |
| RNF-05 | Idempotenza | Qualsiasi import rieseguito N volte produce lo stesso stato |
| RNF-06 | Privacy | Nessun dato di attività inviato a terzi, eccetto i backup cifrati, le richieste di tile mappa (§15) e le analisi AI richieste esplicitamente (opt-in, solo statistiche aggregate, §26) |
| RNF-07 | Sicurezza | Nessuna porta applicativa esposta su internet; segreti fuori da git |
| RNF-08 | Manutenibilità | Un solo linguaggio backend, 2 container, deploy con un comando, test automatici sulle parti rischiose |
| RNF-09 | Mobile | Usabile a 375 px di larghezza senza scroll orizzontale |
| RNF-10 | Portabilità | Nessun lock-in Oracle: la VM è sostituibile da qualsiasi host Linux ARM o x86 con Docker |

## 4. Assunzioni

| # | Assunzione | Impatto se falsa |
|---|-----------|------------------|
| A1 | Tutte le corse passano da Strava in automatico (Watch → Salute → Strava). L'app Strava sincronizza da Salute solo attività **degli ultimi 30 giorni** [FATTO] | Corse mancanti su Strava: servirebbe l'import Apple (Could → Should) |
| A2 | Volume: circa 1.000 km nel 2026, quindi circa 130–200 corse/anno; storico totale < 500 attività | Con più attività il backfill richiede più giorni (resta gestito) |
| A3 | Sensore FC: quello ottico del Watch, senza fascia | L'FC in ripetute è meno affidabile: segnalato nell'interpretazione |
| A4 | Unico utente: tu. UI in inglese, unità km e min/km, solo tema scuro | — |
| A5 | Fuso orario delle corse per lo più Europe/Rome; si usa comunque il fuso di ogni attività | — |
| A6 | Sviluppo su Windows in locale, repository git privato (GitHub), deploy via `git pull` sulla VPS | — |
| A7 | L'opzione Strava "nascondi orario di inizio" è disattivata sul tuo account (se attiva, l'API restituisce mezzanotte [FATTO]) | Rompe deduplicazione e analisi per ora del giorno → da verificare nello spike M0-08 |
| A8 | Il tipo di allenamento lo imposti tu; le attività sincronizzate da Apple arrivano con `workout_type` di default | Le analisi non dipendono dai tag (vedi "steady run", §13) |

---

## 5. Architettura proposta

```
 iPhone / Mac / PC (Tailscale client)
        │  HTTPS  https://corsa.<tailnet>.ts.net   (cert Let's Encrypt gestito da Tailscale)
        ▼
┌──────────────────────── Oracle VM A1 (Ubuntu 24.04 arm64, 2 OCPU / 12 GB) ───────────────────────┐
│  tailscaled  ── "tailscale serve" ──► 127.0.0.1:8000                                              │
│                                                                                                   │
│  docker compose                                                                                   │
│  ┌──────────────────────────────┐        ┌───────────────────────────────┐                        │
│  │ api  (FastAPI + SPA statica) │        │ worker (stesso image)         │── HTTPS ──► Strava API │
│  │  - REST /api/*               │        │  - scheduler (sync 30', recon)│                        │
│  │  - auth: header identità TS  │        │  - esegue job da tabella jobs │                        │
│  └──────────────┬───────────────┘        └──────────────┬────────────────┘                        │
│                 └────────────── /data (bind mount) ─────┘                                         │
│                                 ├─ corsa.db (SQLite, WAL)                                         │
│                                 └─ files/ (FIT/GPX/TCX originali, per sha256)                     │
│                                                                                                   │
│  cron host 03:30: sqlite .backup → restic → OCI Object Storage (cifrato) → ping healthchecks.io    │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
 Browser → tile mappa direttamente da OpenFreeMap (nessuna chiave)
```

**Principi**

- **Monolite modulare**: un codebase Python con moduli `ingest/` (provider → formato canonico), `domain/` (modelli, dedup), `metrics/` (calcoli puri), `api/`, `worker/`. Un solo image Docker con due comandi.
- **Pipeline di import separata dall'API**: l'API accoda job, il worker li esegue. L'API resta veloce e le chiamate verso Strava non bloccano mai l'interfaccia.
- **Calcoli per attività eseguiti al momento dell'import** e salvati con un numero di versione dell'algoritmo. **Aggregati della dashboard calcolati a runtime** con SQL: su meno di 10k righe costa millisecondi e non servono rollup precalcolati.
- **Dati sorgente conservati** (JSON Strava e file originali): ogni metrica si può ricalcolare senza scaricare di nuovo i dati.

**Decisioni difficili da cambiare dopo l'avvio** (da fissare in M1):

1. Separazione tra `activities` (canonica) e `source_records` (per provider), la base della multi-sorgente.
2. Unità interne SI (metri, secondi, m/s) e tempi in UTC più fuso IANA per attività.
3. Formato di salvataggio degli stream (blob compresso per record sorgente).
4. Chiavi interne proprie: gli ID Strava non sono mai chiavi primarie.
5. Modello di autenticazione basato sulla rete (Tailscale). Passare a un'esposizione pubblica richiederebbe login e rate limiting (§14).

Più facili da cambiare: SQLite → Postgres (SQLAlchemy + Alembic), libreria di grafici, polling → webhook.

---

## 6. Stack tecnologico

| Area | Scelta | Motivazione breve |
|------|--------|-------------------|
| OS | Ubuntu Server 24.04 LTS aarch64 | Immagine ufficiale su OCI, supporto Docker e Tailscale per arm64 |
| Rete/accesso | **Tailscale** (piano Personal gratuito: fino a 6 utenti, dispositivi illimitati [FATTO]) + `tailscale serve` | HTTPS senza dominio, zero porte pubbliche, identità dell'utente negli header |
| Runtime | Docker Engine + Compose | Build riproducibile su ARM, rollback semplice |
| Backend | **Python 3.13 + FastAPI + Uvicorn** | Lo conosci; ecosistema migliore per FIT (SDK Garmin ufficiale); Pydantic per validazione e OpenAPI |
| ORM/migrazioni | SQLAlchemy 2.x + Alembic | Migrazioni versionate; porta aperta verso Postgres |
| HTTP client | httpx | Timeout espliciti; testabile con `respx` |
| DB | **SQLite** (WAL, `foreign_keys=ON`, `busy_timeout`) | Single-user, < 1 GB di dati per anni, backup di un solo file, nessun container DB |
| Job | Tabella `jobs` in SQLite + processo worker | Nessun Redis/Celery; job visibili nell'UI |
| Parsing file (Should) | `garmin-fit-sdk` (SDK ufficiale Garmin, v21.217.0 del 2026-09-22 [FATTO]); GPX/TCX con `defusedxml` + ElementTree | GPX e TCX sono XML semplici; `defusedxml` protegge da XXE e billion-laughs |
| Calcolo numerico | Libreria standard Python (`statistics`, `math`) | Circa 3.600 punti per corsa: nessun bisogno di numpy/pandas. Da aggiungere solo se la profilazione lo richiede |
| Frontend | **React + TypeScript + Vite** (SPA) | Lo conosci; build statica servita dall'API |
| Routing/stato | React Router + TanStack Query; filtri nello stato dell'URL | Niente Redux: lo stato è quasi tutto "server state" |
| Stile | Tailwind CSS con design token dark; nessuna libreria di componenti | Controllo totale su densità ed estetica; niente look "template" |
| Grafici | **Apache ECharts** | Rende su canvas migliaia di punti, dataZoom, assi multipli, tooltip sincronizzati, tema scuro nativo |
| Mappe | **MapLibre GL JS + OpenFreeMap (stile "Dark")** | Gratuito, senza chiave, senza limiti dichiarati; attribuzione automatica con MapLibre [FATTO] |
| Tipi API | `openapi-typescript` dal file OpenAPI di FastAPI | Evita disallineamenti tra backend e frontend senza scrivere codice a mano |
| Test | pytest, respx, Vitest (solo formatter), Playwright (3 flussi E2E) | Proporzionato |
| Backup | restic → OCI Object Storage (API S3-compatibile) | Cifrato, deduplicato, retention integrata, un solo binario |
| Monitoring | Endpoint `/healthz` + healthchecks.io (dead-man switch, solo ping in uscita) | Funziona anche se l'app è privata |

**Perché non Go** (che conosci): un binario Go sarebbe più leggero, ma il vantaggio è irrilevante con 12 GB di RAM. Python ha un ecosistema migliore per file sportivi e analisi, e FastAPI genera OpenAPI e validazione gratis.

---

## 7. Modello dati

### 7.1 ERD testuale

```
provider_accounts (1 riga per provider collegato)

jobs 1 ──< source_records >── 1 activities 1 ──< laps
                   │                  │ 1 ──1 activity_metrics
                   │                  │ 1 ──< best_efforts
                   └─1 ── 0..1 streams│ >──< tags (via activity_tags)
                                      └── duplicate_of → activities (auto-riferimento, opzionale)
settings (key/value)
```

- Un'**activity** (canonica) ha 1..N **source_records**: per esempio la stessa corsa da Strava e da un FIT HealthFit. Uno è la `primary_source` (fonte dei valori di summary), uno è la `stream_source` (lo stream più ricco).
- Un source_record può esistere senza activity: attività non-corsa (`skipped`), import falliti (`failed`).

### 7.2 Tabelle

**`activities`** (canonica, una riga per ogni corsa reale)

| Campo | Tipo | Note |
|-------|------|------|
| id | INTEGER PK | Interno |
| sport_type | TEXT NOT NULL CHECK IN ('run','trail_run','treadmill') | Mappato da `sport_type` Strava (Run, TrailRun, VirtualRun) + flag `trainer` |
| name | TEXT | Dalla sorgente |
| start_time_utc | TEXT (ISO-8601 UTC) NOT NULL | |
| timezone | TEXT | IANA, es. `Europe/Rome` (estratto dal campo `timezone` di Strava) |
| local_date | TEXT (YYYY-MM-DD) NOT NULL | Derivato; base per settimane e mesi |
| elapsed_s, moving_s | INTEGER | |
| distance_m | REAL CHECK ≥ 0 | |
| elev_gain_m, elev_loss_m | REAL NULL | Come riportato dalla sorgente (dipende dall'algoritmo) |
| avg_hr, max_hr | REAL NULL | |
| avg_cadence_spm | REAL NULL | Sempre in **passi/min** (normalizzato, §8.3) |
| avg_power_w | REAL NULL | Stima Apple (§13.1) |
| calories_kcal | REAL NULL | Stima del dispositivo |
| has_gps, has_hr, has_cadence, is_indoor | INTEGER (bool) | Per filtri e grafici condizionali |
| workout_type | TEXT NULL CHECK IN ('easy','long','workout','race','other') | **Locale**, modificabile; iniziale da Strava (1 = race, 2 = long, 3 = workout) |
| notes | TEXT NULL | **Locale** |
| excluded_from_stats | INTEGER DEFAULT 0 | **Locale** (es. corsa registrata male) |
| primary_source_id | INTEGER FK → source_records | |
| stream_source_id | INTEGER FK → source_records NULL | |
| summary_polyline | TEXT NULL | Encoded polyline, per usi futuri |
| duplicate_of_id | INTEGER FK → activities NULL | Impostato dalla deduplicazione (§8.4) |
| upstream_deleted_at | TEXT NULL | Attività non più presente su Strava |
| created_at, updated_at | TEXT | |

Indici: `(local_date)`, `(start_time_utc)`, `(sport_type, local_date)`, `(distance_m)`, `(workout_type)`.
Regola: i campi **Locali** non vengono mai sovrascritti dalla sincronizzazione; gli altri sì.

**`source_records`** (uno per ogni oggetto sorgente)

| Campo | Tipo | Note |
|-------|------|------|
| id | INTEGER PK | |
| source | TEXT NOT NULL CHECK IN ('strava','file_fit','file_gpx','file_tcx','apple_health') | Enum, non una tabella |
| external_id | TEXT NOT NULL | ID Strava, oppure sha256 del file |
| activity_id | INTEGER FK NULL | NULL se skipped/failed |
| status | TEXT CHECK IN ('imported','skipped','failed') | |
| error | TEXT NULL | Ultimo errore |
| raw_summary | TEXT (JSON) NULL | SummaryActivity da Strava |
| raw_detail | TEXT (JSON) NULL | DetailedActivity da Strava |
| file_path | TEXT NULL | `files/<sha256[:2]>/<sha256>.<ext>` |
| source_start_time_utc | TEXT NULL | Per la deduplicazione anche senza activity |
| fetched_at | TEXT | |
| mapper_version | INTEGER | Versione del mapper che ha prodotto l'activity |
| job_id | INTEGER FK → jobs NULL | |

Vincolo: **UNIQUE(source, external_id)**, il cuore della deduplicazione intra-sorgente. Indici: `(activity_id)`, `(status)`.

**`streams`** (serie temporali, una riga per source_record)

| Campo | Tipo | Note |
|-------|------|------|
| source_record_id | INTEGER PK FK | |
| activity_id | INTEGER FK | Denormalizzato per le query |
| n_points | INTEGER | |
| channels | TEXT | es. `time,distance,lat,lng,altitude,hr,cadence,power,moving,speed` |
| data | BLOB | JSON `{canale: [valori]}` compresso gzip; array allineati per indice |
| codec_version | INTEGER | |

Dimensione stimata: circa 30–80 KB per corsa compressa, cioè meno di 20 MB all'anno.

**`laps`**: id, activity_id FK, kind CHECK IN ('device_lap','split_km'), idx, start_offset_s, elapsed_s, moving_s, distance_m, avg_speed_ms, avg_hr, max_hr, avg_cadence_spm, elev_gain_m. UNIQUE(activity_id, kind, idx).
`device_lap` arriva dalla sorgente (lap Strava o FIT); `split_km` è **calcolato da noi** dallo stream, così gli split sono coerenti tra sorgenti.

**`best_efforts`**: id, activity_id FK, distance_m (target: 400, 1000, 1609, 5000, 10000, 21097.5, 42195), elapsed_s, start_offset_s, algo_version. UNIQUE(activity_id, distance_m). Indice `(distance_m, elapsed_s)`.

**`activity_metrics`** (metriche calcolate, separate dai dati misurati): activity_id PK FK, algo_version, computed_at, zones_hash, time_in_zones_s (JSON [z1..z5]), efficiency_factor, pace_cv (variabilità del passo), is_steady (bool), decoupling_pct NULL (Should), trimp NULL (Could), gps_suspect (bool: velocità non plausibili).

**`tags`**: id, name UNIQUE COLLATE NOCASE. **`activity_tags`**: (activity_id, tag_id) PK composta, FK con ON DELETE CASCADE.

**`provider_accounts`**: provider TEXT PK, athlete_id, access_token, refresh_token, expires_at, scopes, status CHECK IN ('active','reauth_required','revoked'), sync_cursor (epoch di start_date dell'ultima attività importata), last_sync_at, last_reconcile_at, updated_at.

**`jobs`**: id, kind CHECK IN ('strava_backfill','strava_sync','strava_reconcile','file_import','recompute'), status CHECK IN ('queued','running','succeeded','partial','failed'), params JSON, progress_done, progress_total, attempts, error, not_before (per attese di rate limit), created_at, started_at, heartbeat_at, finished_at. Indice `(status, not_before)`.

**`settings`**: key TEXT PK, value JSON. Chiavi: `hr_max`, `hr_rest`, `hr_zones` (limiti in bpm), `steady_cv_threshold`.

### 7.3 Scelte e non-scelte

- **Nessuna tabella `users` né colonna `user_id`** [DECISIONE, cambia quanto detto nel messaggio precedente]. Con un utente solo è complessità morta. Un eventuale multi-utente richiederebbe comunque di rifare autenticazione e OAuth, e la migrazione (`user_id DEFAULT 1`) sarebbe banale.
- **Stream come blob per record invece di una riga per punto.** Non si interrogano mai i singoli punti via SQL: le metriche si calcolano in Python al momento dell'import. Una riga per punto significherebbe milioni di righe, backup più lenti e nessun beneficio reale.
- **Token OAuth in chiaro nel DB** con permessi file `600` e backup cifrati [DECISIONE]. Cifrarli con una chiave salvata sulla stessa VM sarebbe sicurezza di facciata. Lo scope è di sola lettura e revocabile.
- **Zone FC: una sola configurazione corrente**; cambiandola, le metriche vengono ricalcolate (secondi di lavoro per centinaia di attività). Nessuno storico delle zone (YAGNI).

---

## 8. Data ingestion e import pipeline

### 8.1 Flusso generico (uguale per ogni sorgente)

```
fetch/ricezione ─► source_record (raw salvato, UNIQUE source+external_id)
   ─► filtro sport (non-corsa → status=skipped, stop)
   ─► mapper provider→canonico (funzione pura, versionata)
   ─► dedup cross-sorgente (§8.4): nuova activity | allega a esistente | flag duplicato
   ─► upsert activity + laps(device) + streams        ┐ in UNA transazione
   ─► calcolo metriche (split_km, best_efforts, zone, EF) ┘ per singola attività
   ─► avanzamento cursore/progresso del job
```

**Regola di atomicità**: ogni attività è una transazione. Un job interrotto a metà lascia attività complete oppure nessuna traccia: mai mezze attività.

### 8.2 Formati supportati

| Formato | Quando | Perché |
|---------|--------|--------|
| JSON Strava API | MVP | Sorgente primaria |
| **FIT** | Should | Formato più ricco (cadenza, potenza, lap, eventi); è quello prodotto da HealthFit; SDK ufficiale Garmin |
| GPX (+ estensioni `gpxtpx` per FC e cadenza) | Should | Universale; è quello contenuto in `workout-routes/` dell'export Apple |
| TCX | Should | Economico da aggiungere con lo stesso parser XML; comune negli export storici |
| Apple `export.zip` (export.xml + GPX) | Could | Storico completo con cadenza e metriche di corsa; file enorme e formato non documentato |
| CSV | No | Nessuna sorgente reale che ti serva |

### 8.3 Normalizzazione: cosa normalizzare e cosa tenere grezzo

**Normalizzato** (colonne tipizzate, unità SI):

- tempi (UTC + fuso), durate, distanza, velocità (il **passo è sempre derivato**, mai salvato);
- FC, cadenza in **passi/min**, potenza, dislivello, calorie;
- stream sui canali canonici, laps.

Casi specifici:

- **Cadenza**: Strava riporta storicamente la cadenza di corsa come "passi di un piede" (metà dei passi/min) [NON VERIFICATO sui tuoi dati]. Il mapper la moltiplica per 2 se la sorgente è Strava, verificando nello spike M0-08.
- **Tapis roulant**: `is_indoor=1`; distanza stimata, quindi esclusa di default da passo, best effort ed EF.
- **Fuso**: `start_date_local` di Strava serve solo per validare; la verità è `start_date` (UTC) più IANA.

**Conservato grezzo**: JSON completo di summary e detail Strava (`raw_summary`, `raw_detail`) e i file originali. Servono a ricalcolare le metriche, a usare in futuro campi oggi ignorati (es. `device_name`, `description`, `suffer_score`) e a fare debug dei mapper. Gli stream Strava non si duplicano grezzi: la mappatura sui canali canonici è senza perdita (solo rinomina delle chiavi).

**Metriche specifiche del provider** (es. Relative Effort di Strava): restano nel JSON grezzo e non si promuovono a colonne finché non servono.

### 8.4 Deduplicazione

1. **Stessa sorgente**: `UNIQUE(source, external_id)` più upsert. Rieseguire un import non crea mai duplicati. Per i file, `external_id = sha256`: lo stesso file caricato due volte viene riconosciuto.
2. **Cross-sorgente e sovrapposizioni** (es. FIT HealthFit della stessa corsa già arrivata da Strava, oppure due registrazioni della stessa uscita): prima di creare una nuova activity si cercano candidate con
   - `|Δ start_time| ≤ 120 s` **e** sovrapposizione degli intervalli [start, start+elapsed] ≥ 80% **e**
   - distanza entro ±10% (se entrambe hanno distanza).
3. Esito:
   - **1 candidata, sorgente diversa** → il record viene **allegato** all'activity esistente. Se il suo stream ha più canali (es. la cadenza), diventa `stream_source` e le metriche vengono ricalcolate.
   - **1 candidata, stessa sorgente** (due attività Strava sovrapposte) → nuova activity con `duplicate_of_id` e `excluded_from_stats=1`, segnalata nell'UI.
   - **più candidate** → come sopra, con risoluzione manuale.
4. **Precedenza dei summary**: la prima sorgente importata resta primaria, quindi Strava. Si cambia solo manualmente (Should).

Il mapper e l'algoritmo di deduplicazione sono funzioni pure: sono la parte più testata del progetto (§18).

### 8.5 Job e fallimenti

- Il worker prende un job in modo atomico (`UPDATE … SET status='running' WHERE id=? AND status='queued'`), aggiorna `heartbeat_at` ogni 30 s ed elabora attività per attività.
- **Crash del worker a metà**: all'avvio i job `running` con heartbeat più vecchio di 5 minuti tornano `queued`. Il backfill riparte dai source_record non ancora `imported`, e l'upsert idempotente garantisce l'assenza di duplicati.
- **Errore su una singola attività** (parse, dati corrotti): il source_record va in `failed` con l'errore, il job continua e termina `partial`. L'UI mostra gli elementi falliti con "Retry".
- **429 / rate limit**: il job imposta `not_before` al prossimo quarto d'ora (o a mezzanotte UTC se è esaurito il limite giornaliero), torna `queued` e riprende da solo.
- **Token revocato** (401 dopo il refresh): `provider_accounts.status='reauth_required'`, job `failed`, banner "Reconnect Strava".
- **5xx o errori di rete**: 3 tentativi con backoff (10 s, 60 s, 300 s), poi `failed` e retry al ciclo successivo.

---

## 9. Integrazione Strava

### 9.1 Fatti verificati (developers.strava.com, consultato 2026-09-30)

| Tema | Fatto |
|------|-------|
| Capacità app | Le nuove app partono in "Single Player Mode" (capacità di 1 atleta: solo i propri dati). Sufficiente |
| Rate limit | Complessivo 200 richieste/15 min e 2.000/giorno; **lettura (non-upload) 100/15 min e 1.000/giorno**. Finestre allineate a :00/:15/:30/:45; reset giornaliero a mezzanotte UTC; header `X-RateLimit-*` e `X-ReadRateLimit-*`; oltre il limite → 429 |
| OAuth | `GET https://www.strava.com/oauth/authorize`, `POST https://www.strava.com/oauth/token`. Access token valido 6 ore; **ogni refresh restituisce un nuovo refresh token e il vecchio smette di funzionare** (va persistito subito, in modo atomico). Dal 2026-04-23 la risposta include `scope` |
| Revoca | Dal 2026-06-01 l'endpoint raccomandato è `POST https://www.strava.com/oauth/revoke` (il vecchio `/oauth/deauthorize` è legacy) |
| Callback | Deve stare nell'"Authorization Callback Domain" dell'app; `localhost` e `127.0.0.1` sono in whitelist |
| Scope necessari | `activity:read_all` (incluse attività "Only You" e dati delle zone privacy) |
| Endpoint | `GET /athlete/activities` (`before`, `after`, `page`, `per_page` max 200); `GET /activities/{id}` (DetailedActivity: `splits_metric`, `laps`, `device_name`, `workout_type`, `sport_type`, `average_cadence`, `average_heartrate`, `calories`, `timezone`, `map.polyline`); `GET /activities/{id}/streams` (chiavi: time, distance, latlng, altitude, velocity_smooth, heartrate, cadence, watts, temp, moving, grade_smooth); `GET /activities/{id}/laps`, `/zones` |
| File originali | **Nessun endpoint API** per scaricare il file originale di un'attività (solo per le route) |
| Webhook | Callback che risponda entro 2 s all'handshake `hub.challenge`; eventi create/update (titolo, tipo, privacy)/delete e deautorizzazione; 3 tentativi. Richiede un endpoint raggiungibile da Strava |
| **Cambio base URL** | Da `https://www.strava.com/api/v3` a **`https://api-v3.strava.com`**, in vigore **dal 2027-01-04** → base URL configurabile e task dedicato (M2-08) |
| Privacy orario | Dal 2024-07 le attività con orario nascosto restituiscono mezzanotte in `start_date_local` |
| Agreement | API Agreement in vigore dal 2026-06-01: i dati di un utente possono essere mostrati **solo a quell'utente** (ok: sei tu); alla cessazione dell'accordo vanno cancellati tutti i dati Strava (§15). Una lettura automatica non ha trovato clausole su AI o su cancellazione per singola attività → [NON VERIFICATO manualmente, rileggere] |
| Dati da Apple | Solo allenamenti dell'app Allenamento Apple **degli ultimi 30 giorni** vengono sincronizzati (support Strava). Staff Strava (2023): **la cadenza di corsa da Apple non è supportata**, la potenza di corsa sì → da riconfermare nello spike M0-08 |

### 9.2 Registrazione app e OAuth (con Tailscale, senza dominio)

1. Su strava.com/settings/api crea l'app. **Authorization Callback Domain** = `corsa.<tailnet>.ts.net`. Il redirect OAuth avviene nel tuo browser, che è nella tailnet: Strava non deve raggiungere il server.
   - Fallback se Strava rifiuta il dominio `ts.net` [NON VERIFICATO]: callback `localhost`, eseguendo il flusso una sola volta via tunnel SSH. Con un solo utente il refresh token dura indefinitamente finché non revochi.
2. `GET /api/strava/connect` genera un `state` casuale (salvato in `settings` con scadenza di 10 min) e fa redirect all'authorize con `scope=activity:read_all` e `approval_prompt=auto`.
3. `GET /api/strava/callback` verifica lo `state`, scambia il `code`, verifica che lo scope concesso includa `activity:read_all`, salva i token e accoda `strava_backfill`.

### 9.3 Strategia di sincronizzazione

| Job | Quando | Chiamate API |
|-----|--------|--------------|
| `strava_backfill` | Dopo il collegamento (o manuale) | Liste paginate (200/pagina) → per ogni corsa: detail + streams = **2 chiamate/corsa**. 250 corse ≈ 502 chiamate |
| `strava_sync` | Ogni **30 min** + pulsante "Sync now" | 1 lista `after=cursor-1h` + 2 per ogni corsa nuova. In più, 1 lista delle attività degli **ultimi 30 giorni** per rilevare modifiche (nome, tipo, distanza): se un summary cambia, si rifà il fetch del dettaglio |
| `strava_reconcile` | Settimanale | Lista completa dei summary (circa 1–3 chiamate): ID assenti upstream → `upstream_deleted_at` + `excluded_from_stats`. **Nessuna cancellazione fisica automatica** |

- **Budget**: il client si autolimita a 90 letture/15 min e 900/giorno, lasciando margine per l'uso manuale. Il backfill di circa 500 chiamate richiede circa 1,5 ore, anche in un solo giorno. Il sync regolare consuma circa 100 chiamate al giorno.
- **Latenza attesa di una nuova corsa**: Watch → Salute → Strava (dipende da iOS, di solito minuti) + fino a 30 min di polling. Accettabile per un diario personale; il pulsante "Sync now" copre l'impazienza.
- **Webhook**: esclusi dall'MVP (richiedono un endpoint pubblico). Opzione futura: **Tailscale Funnel** esposto solo sul path `/webhooks/strava` (Could).

### 9.4 Mapping Strava → canonico (punti delicati)

- `sport_type` ∈ {Run, TrailRun, VirtualRun} → import; altrimenti `skipped`. `trainer=true` → `treadmill`.
- `workout_type` 1 → race, 2 → long, 3 → workout; 0 o assente → NULL.
- Stream `latlng` → canali `lat` e `lng`; `velocity_smooth` → `speed`; `heartrate` → `hr`; `watts` → `power`; `cadence` × 2 (se confermato).
- Laps Strava → `laps.kind='device_lap'`. `splits_metric` di Strava viene ignorato: gli split si calcolano dallo stream.

---

## 10. Integrazione Apple / iPhone

**Fatto chiave**: Apple Salute non ha un'API web. I dati escono dall'iPhone solo tramite un'app sul telefono che usa HealthKit, oppure tramite l'export manuale dall'app Salute [FATTO, fonti di terze parti concordi; Apple non documenta il formato dell'export].

| Opzione | Cosa porta | Automatica? | Costo | Collocazione |
|---------|-----------|-------------|-------|--------------|
| **A. Via Strava** (flusso attuale) | GPS, FC, tempo, distanza, altitudine, calorie, potenza; **niente cadenza**; solo allenamenti ≤ 30 giorni | Sì | 0 | **MVP** |
| **B. HealthFit → file FIT → upload nell'app** | Cadenza, potenza, segmenti/lap, FC, GPS dall'Apple Watch [FATTO: descrizione dell'app, da confermare su un file reale] | Semi: export automatico su iCloud Drive/File, poi upload dal telefono tramite la pagina Import (selezione multipla) | App a pagamento una tantum (circa 2–3 $) [NON VERIFICATO sul prezzo attuale] | **Should (M7)** |
| C. Export `export.zip` di Salute | Tutto lo storico: workout, record di FC, passi, potenza, lunghezza del passo, ecc. in un XML da centinaia di MB + GPX per percorso | No, manuale e tutto-o-niente | 0 | Could |
| D. Health Auto Export → POST REST al server via Tailscale | Workout + route in JSON | Sì (automazioni iOS; affidabilità in background [NON VERIFICATO]) | Abbonamento [NON VERIFICATO] | Could |
| E. App iOS nativa propria | Tutto | Sì | Apple Developer 99 $/anno + Swift | Escluso |

**Raccomandazione**: A subito. B come prima estensione se vuoi la cadenza, perché riusa la pipeline FIT e la deduplicazione cross-sorgente arricchisce automaticamente le attività Strava esistenti. **Spike M0-09**: esporta una corsa con HealthFit e ispeziona il FIT prima di investire in M7.
C serve solo per recuperare la cadenza dello storico o corse che non sono mai arrivate su Strava. Parsing in streaming (`iterparse`) per non caricare centinaia di MB in RAM; i campioni di FC e cadenza si associano al workout per finestra temporale.

---

## 11. Backend / API

Convenzioni: JSON, prefisso `/api`, errori `{"error": {"code", "message"}}`, date ISO-8601. Tutte le rotte richiedono l'identità Tailscale (§14), incluse quelle del callback OAuth. I metodi mutanti richiedono inoltre l'header `X-Corsa: 1` (anti-CSRF).

### Autenticazione e sistema

| Metodo | Endpoint | Scopo | Output |
|--------|----------|-------|--------|
| GET | `/healthz` | DB raggiungibile, età dell'ultimo sync riuscito, job falliti recenti | `{status, db, last_sync_age_s, failed_jobs_24h}` |
| GET | `/api/me` | Identità vista dal server | `{login}` |

### Strava e sincronizzazione

| Metodo | Endpoint | Scopo | Input → Output |
|--------|----------|-------|----------------|
| GET | `/api/strava/status` | Stato del collegamento | → `{connected, athlete_id, scopes, status, last_sync_at, rate_usage}` |
| GET | `/api/strava/connect` | Avvio OAuth | → 302 verso Strava |
| GET | `/api/strava/callback` | Fine OAuth | `code, state, scope` → 302 `/sync` |
| POST | `/api/strava/disconnect` | Revoca su Strava + cancellazione token | `{purge_data: bool}` → `{ok}` |
| POST | `/api/sync` | Accoda `strava_sync` (idempotente se ce n'è già uno in coda) | → `{job_id}` |
| GET | `/api/jobs` | Storico job | `?status&limit` → `[job]` |
| GET | `/api/jobs/{id}` | Dettaglio con elementi falliti | → `{job, failed_records[]}` |
| POST | `/api/source-records/{id}/retry` | Nuovo tentativo su un elemento fallito | → `{job_id}` |
| POST | `/api/imports/files` (Should) | Upload multiplo FIT/GPX/TCX | multipart → `{job_id, accepted[], rejected[]}` |

### Attività

| Metodo | Endpoint | Scopo | Input → Output |
|--------|----------|-------|----------------|
| GET | `/api/activities` | Lista filtrata | `from,to,dist_min,dist_max,dur_min,dur_max,pace_min,pace_max,hr_min,hr_max,type[],workout_type[],tag[],source[],q,include_excluded,sort,order,page,page_size` → `{items[], total, aggregate:{count, distance_m, moving_s, weighted_pace_s_per_km}}` |
| GET | `/api/activities/{id}` | Dettaglio completo | → `{activity, metrics, laps[], splits[], best_efforts[] (con flag PR), sources[], tags[], duplicate_candidates[]}` |
| GET | `/api/activities/{id}/streams` | Serie per i grafici | `?channels=` → `{channels:{time[],distance[],…}}` (gzip) |
| GET | `/api/activities/{id}/similar` | Corse confrontabili | → ultime 5 dello stesso tipo con distanza ±15% e delta di passo/FC/EF |
| PATCH | `/api/activities/{id}` | Modifiche locali | `{notes?, workout_type?, tags?, excluded_from_stats?}` → activity |
| GET | `/api/activities/{id}/sources/{sid}/raw` | JSON grezzo (debug) | → JSON |
| POST | `/api/activities/{id}/merge` (Should) | Unisce un duplicato | `{other_id}` → activity |

### Statistiche

Parametri comuni: `from, to` (date locali), `type[]`, `include_indoor`.

| Metodo | Endpoint | Output |
|--------|----------|--------|
| GET | `/api/stats/summary` | Totali del periodo e del **periodo precedente di pari durata**: km, tempo, corse, km/settimana medi, corsa più lunga, passo ponderato |
| GET | `/api/stats/volume?bucket=week\|month` | Serie per bucket: km, tempo, corse, dislivello, corsa più lunga, flag `partial` |
| GET | `/api/stats/calendar?year=` | km per giorno |
| GET | `/api/stats/distribution?field=distance\|duration\|pace` | Istogramma con conteggi |
| GET | `/api/stats/zones?bucket=week` | Secondi per zona per bucket |
| GET | `/api/stats/trends?metric=pace\|ef\|hr\|cadence` | Punti per corsa (steady/tutte) + mediana mobile 28 gg + pendenza Theil-Sen con `n` |
| GET | `/api/stats/pace-hr` | Punti (passo, FC, data) delle corse steady |
| GET | `/api/stats/top-weeks?limit=10` | Classifica settimane per km |
| GET | `/api/records` | PR correnti per distanza + storico dei miglioramenti |

### Impostazioni e dati

| Metodo | Endpoint | Scopo |
|--------|----------|-------|
| GET/PUT | `/api/settings` | FC max e riposo, zone, soglia "steady"; il PUT accoda `recompute` se cambiano le zone |
| GET | `/api/tags` | Elenco tag con conteggi |
| GET | `/api/export` (Should) | ZIP: CSV attività, JSON completi, file originali |
| DELETE | `/api/data` (Should) | Cancella tutto; richiede `{confirm: "DELETE ALL"}` |

---

## 12. Frontend / UI

### 12.1 Struttura

| Rotta | Pagina | Contenuto |
|-------|--------|-----------|
| `/` | **Dashboard** | Selettore periodo, strip riassuntiva, sezioni di grafici (§13) |
| `/activities` | **Activities** | Filtri + tabella; aggregati del set filtrato in testa |
| `/activities/:id` | **Activity** | Dettaglio (§12.3) |
| `/records` | **Records** | PR per distanza, progressione, best effort recenti |
| `/sync` | **Sync** | Connessione Strava, ultimo sync, job, elementi falliti, upload file (Should) |
| `/settings` | **Settings** | Zone FC, soglie, dati (export/cancellazione) |

Navigazione: barra superiore compatta su desktop; **barra inferiore a 4 voci** su mobile (Dashboard, Activities, Records, Sync), con Settings raggiungibile da Sync. I filtri e il periodo stanno nell'URL: pagine condivisibili tra i tuoi dispositivi e tasto indietro funzionante.

### 12.2 Linee guida visive (dark-only, strumento di analisi)

- **Superfici**: sfondo quasi nero neutro, una sola superficie più chiara per i pannelli, bordi 1 px a basso contrasto. Nessuna ombra, nessun gradiente, nessun glassmorphism.
- **Gerarchia con tipografia e spazio, non con box**: sezioni separate da titolo e spaziatura; niente griglie di card. Font di sistema/Inter, **`tabular-nums` su tutti i numeri**, 3 livelli di grigio per il testo.
- **Colori semantici fissi in tutta l'app**: passo = blu, FC = rosso, dislivello = grigio (area), cadenza = viola, potenza = ambra. Zone FC = una scala sequenziale a 5 step (non arcobaleno). Un solo colore d'accento per gli elementi interattivi. I colori di serie non vengono mai riusati per la decorazione.
- **Numeri sempre con contesto**: unità, confronto con il periodo precedente (Δ con segno, in colore neutro; nessun verde o rosso "morale", perché più km non è sempre meglio), `n` delle osservazioni sui trend.
- **Etichettatura epistemica**: i valori stimati o modellati portano un marcatore discreto `est.` o `model` con tooltip esplicativo (§13.1).
- **Grafici**: griglie tenui, niente animazioni d'ingresso, tooltip su hover e tap. Asse del passo **invertito** (più veloce = più in alto), con clamp oltre 10:00/km per evitare che soste e camminate schiaccino la scala.
- **Densità**: 2 colonne di grafici da ≥ 1280 px, 1 colonna sotto. Tabelle dense su desktop, righe su due linee su mobile. Target touch ≥ 40 px.
- **Accessibilità**: contrasto AA, informazione mai affidata solo al colore (linee tratteggiate e marcatori), navigazione da tastiera.
- **Vietato**: hero, card-KPI giganti, pill decorative, icone ornamentali, gradienti, empty state illustrati.

### 12.3 Pagina di dettaglio attività

Desktop, dall'alto:

1. **Header su una riga**: nome · data e ora locale · tipo (modificabile) · icone sorgente (Strava, FIT) · link "View on Strava".
2. **Tabella statistiche a due colonne raggruppate**: *Tempo/distanza* (distanza, tempo in movimento, tempo totale, passo medio ponderato, passo migliore di 1 km) · *Cuore* (FC media e massima, EF) · *Terreno* (D+/D−, altitudine min e max) · *Altro* (cadenza, potenza `est.`, calorie `est.`). Accanto a ogni valore principale, un Δ rispetto alla mediana delle corse simili.
3. **Mappa** (60% della larghezza) con traccia colorata per passo o FC (selettore) e marker di inizio e fine | a destra la **tabella degli split per km**: km, passo, FC, D+, con barra orizzontale del passo relativa alla media.
4. **Grafici sincronizzati** (stesso asse X, distanza o tempo): passo, FC con fasce delle zone in background, altimetria ad area, cadenza e potenza (solo se presenti). L'hover muove un marker sulla mappa e il cursore su tutti i grafici; zoom a selezione condiviso.
5. **Tempo nelle zone** (barra orizzontale segmentata + secondi e percentuale).
6. **Best effort della corsa** (1K/5K/10K…) con badge "PR" o "2nd best" rispetto alla storia a quella data.
7. **Confronto**: tabella delle ultime 5 corse simili con delta di passo, FC ed EF.
8. **Lap del dispositivo** (se presenti e diversi dagli split).
9. **Note e tag** (editor inline).
10. **Sources & data** (comprimibile): record sorgente, `fetched_at`, versione del mapper, visualizzatore del JSON grezzo, eventuali candidati duplicati.

Mobile: una colonna; mappa alta 240 px; statistiche in griglia 2×N compatta; grafici a larghezza piena impilati; split sotto la mappa.

### 12.4 Lista attività e filtri

- **Filtri**: preset di periodo (4W, 12W, 6M, YTD, 1Y, All) + intervallo custom; range numerici con `<input type="number">` per distanza, durata, passo e FC media; multi-select per tipo e tag; sorgente; testo libero (nome e note, `LIKE`: con meno di 10k righe non serve FTS); toggle "include excluded".
- Tabella: data, nome, tipo, km, tempo, passo, FC media, D+, tag; ordinamento su ogni colonna; paginazione server-side (50 per pagina).
- **Aggregato del set filtrato** in una riga di testo in testa, per esempio: "23 runs · 187.4 km · 16h 42m · 5:21 /km". Trasforma la lista in uno strumento di analisi ad hoc ("tutte le corse > 15 km di quest'estate").
- Filtri salvati: Could.

---

## 13. Dashboard e analytics

### 13.1 Classificazione epistemica delle metriche

| Classe | Esempi | Trattamento nell'UI |
|--------|--------|---------------------|
| **Misurato** (dal sensore) | Tempo, posizione GPS, FC ottica, altitudine (barometrica/GPS), passi | Mostrato senza marcatori. Nota: anche il misurato ha errore (GPS ±1–3% sulla distanza, FC ottica instabile in ripetute) |
| **Calcolato** (deterministico dal misurato) | Distanza, passo, tempo in movimento, split, best effort, tempo nelle zone, volume, EF, variabilità del passo | Nessun marcatore; la metodologia è documentata nel tooltip "How is this computed" |
| **Stimato** (dal dispositivo o dalla sorgente con modelli proprietari) | Calorie, potenza di corsa Apple, VO2max Apple, dislivello Strava (algoritmo proprio), Relative Effort | Marcatore `est.` |
| **Modello interpretativo** | Fitness/fatigue/form (ATL/CTL/TSB), TRIMP, predittore gara (Riegel), passo corretto per la pendenza | Marcatore `model`, pagina dedicata (Could), mai mescolati ai dati nella dashboard principale |

### 13.2 Metriche calcolate da noi (MVP)

- **Split per km**: interpolazione lineare sullo stream distanza/tempo a ogni confine di 1000 m; ultimo split parziale.
- **Best effort**: finestra scorrevole a due puntatori sullo stream della distanza, con il tempo minimo per coprire D metri (interpolato). O(n). Esclusi `is_indoor` e `gps_suspect`.
- **Tempo nelle zone**: somma dei Δt tra campioni di FC per zona (Δt limitato a 10 s per non contare le pause).
- **Efficiency Factor (EF)** = velocità in movimento (m/min) / FC media. Più alto = più veloce a parità di battito.
- **Corsa "steady"** (deterministica, non dipende dai tag): outdoor, ≥ 20 min, con FC, coefficiente di variazione del passo su finestre di 1 min < soglia (default 0,08, configurabile), `workout_type` diverso da workout/race. Serve a confrontare mele con mele.
- **gps_suspect**: velocità > 7 m/s per più di 10 s oppure salti di posizione anomali.
- **GAP** (`model`, 2026-10-01): passo corretto per la pendenza; pendenza su finestra centrata di 50 m, ogni tratto pesato con il costo energetico di Minetti (2002). Per attività e per split km. Non per treadmill.
- **FC a passo di riferimento** (`model`, M7-04): per corsa, retta ai minimi quadrati FC ~ velocità sui campioni piani (|pendenza| ≤ 2%, passo su finestra di 60 s, primi 5 min esclusi), letta al passo impostato (default 7:00/km). Nessuna estrapolazione: il passo deve stare tra il 10° e il 90° percentile della corsa.
- **EF corretto** (`model`): EF sulla velocità GAP, aumentato del rallentamento atteso per il caldo (tabella di Hadley su temperatura + punto di rugiada in °F). Meteo da Open-Meteo, opt-in (M8-05): invia coordinate di partenza arrotondate a ~1 km e data.
- **Alert di rischio**: rampa km 7 gg vs media delle 3 settimane precedenti (> 15% warn, > 30% high), ACWR > 1,3 / > 1,5 / < 0,8, monotonia > 2.
- **Cadenza per fascia di passo** (D12): fasce fisse di 30 s/km da 5:00 a 8:00, solo corse steady; mediana e Theil-Sen per fascia.

### 13.3 Visualizzazioni

Selettore globale: **periodo** (4W · 12W · 6M · YTD · 1Y · All · custom) e **granularità** (week/month, scelta automatica in base al periodo e modificabile). Le settimane sono ISO (lunedì), calcolate su `local_date`.

| # | Visualizzazione | Cosa mostra | Perché è utile | Dati | Periodo | Problemi di interpretazione |
|---|-----------------|-------------|----------------|------|---------|-----------------------------|
| D1 | **KPI card** (6 card con Δ colorato: verde = miglioramento, rosso = peggioramento; passo: più basso = meglio) | km, tempo, n° corse, km/settimana medi, corsa più lunga, passo ponderato + Δ sul periodo precedente | Risposta in 2 secondi a "quanto e quanto spesso corro" | activities | Qualsiasi | Periodo corrente parziale contro periodo precedente completo → il Δ usa lo **stesso numero di giorni trascorsi** |
| D2 | **Volume per settimana/mese** (barre) + **media mobile 4 settimane** (linea); toggle km/tempo/corse/D+ | Andamento del carico grezzo | Mostra costanza, pause, aumenti bruschi (> 10–15%/settimana = rischio infortunio) | activities | ≥ 4W | Il bucket corrente è parziale (barra tratteggiata); i mesi hanno lunghezze diverse |
| D3 | **Calendario di costanza** (heatmap giornaliera, km) | Giorni corsi e buchi | Frequenza e regolarità a colpo d'occhio | activities | 1Y fisso (scorre) | Nessuno rilevante |
| D4 | **Corsa lunga settimanale** (linea) + quota della lunga sul volume settimanale | Progressione della resistenza | Metrica chiave per mezza o maratona | activities | ≥ 12W | Settimane senza lunga → valori bassi, non "regressione" |
| D5 | **Trend del passo** (scatter per corsa, colore = tipo) + mediana mobile 28 gg delle sole steady | Come cambia il ritmo | Risponde a "sono più veloce?" | activities, metrics | ≥ 12W | **Media del passo di tutte le corse = fuorviante**: mescolare lenti e ripetute crea trend fittizi (paradosso di Simpson). Il ritmo dipende anche da caldo, dislivello e fatica → mediana su steady + n visibile |
| D6 | **Trend dell'efficienza (EF)** delle corse steady + mediana mobile + pendenza Theil-Sen ("+2.1% in 12 weeks, n=31") | Più velocità per battito = adattamento aerobico | È la risposta più onesta a "a parità di ritmo la FC scende?" | metrics | ≥ 12W | Caldo e umidità alzano la FC (stagionalità); FC ottica rumorosa; con n < 8 il trend non viene mostrato |
| D7 | **Passo vs FC** (scatter; ogni punto è una corsa steady; colore = data, da vecchio a recente) | Spostamento della relazione nel tempo | Se i punti recenti stanno in basso a destra (più veloce, meno FC) il miglioramento è visibile | metrics | ≥ 12W | Confronta corse di durata diversa (la deriva cardiaca cresce con la durata) → filtro per durata |
| D8 | **Distribuzione nelle zone** (barre impilate per settimana) + % complessiva del periodo | Intensità dell'allenamento | Verifica il rapporto facile/duro (es. circa 80/20) | metrics | ≥ 4W | Dipende totalmente dalla correttezza della FC max impostata |
| D9 | **Distribuzione delle distanze** (istogramma per classi 0–5, 5–8, 8–12, 12–16, 16–21, 21+ km) | Quali distanze corri più spesso | Varietà degli stimoli; eccesso di "tutte uguali" | activities | Qualsiasi | Con n piccolo meglio mostrare i conteggi che le percentuali |
| D10 | **Progressione dei best effort** (1K, 5K, 10K, 21.1K): scatter dei best effort per corsa + linea a gradini "PR alla data" | Come cambiano i tempi sulle distanze | Risponde a "come sono cambiati i miei 5K" | best_efforts | All | Un best effort dentro un allenamento **non è una gara**; il GPS gonfia o sgonfia la distanza; sono distinti i PR da gara (`workout_type=race`) |
| D11 | **Top settimane** (tabella: settimana, km, corse, tempo, passo, D+) | Settimane più intense | Contesto per infortuni e picchi di forma | activities | Qualsiasi | — |
| D12 | **Trend della cadenza** (Should) | Evoluzione della meccanica | Utile con dati da FIT | streams | ≥ 12W | Nascosto se mancano i dati; la cadenza dipende dal passo → mostrata per fasce di passo |

**Metriche escluse dalla dashboard perché fuorvianti**: passo medio aritmetico di tutte le corse, FC media di tutte le corse, calorie totali come "risultato", VO2max come verità, confronti tra un mese in corso e uno completo.

### 13.4 Statistiche usate

- **Mediana e mediana mobile per finestra temporale** (28 gg, non "ultime N corse") al posto della media: robuste agli outlier (GPS impazzito, corsa con soste).
- **Passo aggregato ponderato** = tempo totale / distanza totale (mai la media dei passi).
- **Pendenza Theil-Sen** (mediana delle pendenze tra coppie, O(n²) accettabile con n < 500) con **n sempre mostrato**; nessun trend se n < 8.
- **Stratificazione per tipo** (steady / workout / long) prima di ogni trend.

### 13.5 Risposte alle domande chiave

| Domanda | Dove si risponde |
|---------|------------------|
| Sto migliorando? | D6 (EF) + D10 (best effort) + D7; mai un singolo numero "fitness" |
| Come è cambiato il ritmo? | D5 (steady, mediana mobile) |
| A parità di ritmo la FC scende? | D6 e D7; poi "HR at reference pace" (Should): FC mediana nei minuti con passo in una fascia scelta (es. 5:30–5:45) e pendenza entro ±2% |
| Volume settimanale? | D2, D4 |
| Settimane più intense? | D11 + D8 |
| Distanze più frequenti? | D9 |
| Tempi su 5K/10K? | D10, pagina Records |
| Progressione nel tempo? | Selettore "All" su D2, D5, D6, D10 |

---

## 14. Autenticazione e sicurezza

### 14.1 Modello di minaccia (realistico per un'app personale)

| Rischio reale | Mitigazione |
|---------------|-------------|
| Esposizione pubblica accidentale dell'app | L'API ascolta **solo su 127.0.0.1**; nessuna regola di ingresso in OCI oltre SSH (poi chiusa); l'unico accesso è `tailscale serve` |
| Qualcuno nella tailnet (dispositivo condiviso o compromesso) | ACL Tailscale: solo il tuo utente può raggiungere il nodo; l'app verifica l'header `Tailscale-User-Login` = login autorizzato (Serve **rimuove gli header omonimi in ingresso**, quindi non sono falsificabili dalla rete [FATTO]) |
| CSRF (il browser nella tailnet ha "autorità ambientale" di rete) | Metodi mutanti accettati solo con `Content-Type: application/json` e header custom `X-Corsa: 1` (forzano un preflight CORS, e CORS non è abilitato); callback OAuth protetta dallo `state` |
| Furto dei token Strava | Scope in sola lettura, revocabili; file DB `600`; backup cifrati |
| Perdita di dati (VPS recuperata, errore umano) | §17 |
| File malevoli in upload (Should) | Limite 25 MB per file e 100 MB per richiesta; whitelist estensione **e** magic bytes (FIT: `.FIT` all'offset 8; XML: parse con `defusedxml`); `.gz` decompresso con limite di dimensione (anti zip-bomb); nome salvato = sha256, mai il nome utente nel path; parsing nel worker, mai nell'API |
| Vulnerabilità delle dipendenze | `unattended-upgrades` per l'OS; Dependabot + `pip-audit`/`npm audit` in CI (Should); rebuild mensile delle immagini |
| SSH esposto | Dopo il setup di Tailscale: SSH solo via tailnet, porta 22 chiusa nella Security List OCI; accesso di emergenza dalla console seriale OCI |

### 14.2 Scelte

- **Nessun login applicativo nell'MVP** [DECISIONE]: l'autenticazione è l'identità Tailscale (WireGuard + SSO dell'account Tailscale, con 2FA sull'account di identità) verificata tramite header. Una password in più proteggerebbe solo da un dispositivo della tailnet compromesso, già coperto dall'ACL sul tuo utente.
  - **Se in futuro l'app diventa pubblica** (es. Funnel), serviranno login applicativo, sessioni e rate limiting. È una decisione difficile da cambiare: annotata nell'ADR-06.
- **Rate limiting in ingresso: non implementato** (rete privata, un solo utente). Rate limiting in uscita verso Strava: obbligatorio (§9.3).
- **Segreti**: file `/opt/corsa/.env` (`chmod 600`, fuori da git) con `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `ALLOWED_LOGINS`, `STRAVA_API_BASE`, `HEALTHCHECK_URL_*`; `restic` usa file separati. **Copia di tutti i segreti in un password manager**: senza la password restic i backup sono inutili.
- **Sviluppo locale**: `AUTH_DEV_LOGIN=me@example` accettato solo se `ENV=dev` (bind su localhost).
- **Header di sicurezza** di base: `Content-Security-Policy` (script `self`; tile e stili da `tiles.openfreemap.org`), `X-Content-Type-Options`, `Referrer-Policy: same-origin`.

---

## 15. Privacy e gestione dati

| Dato | Dove sta | Esce dalla VPS? |
|------|----------|-----------------|
| Attività, stream GPS (rivelano casa e abitudini), FC (dato sanitario) | SQLite sulla VPS | Solo nei backup **cifrati** restic (OCI Object Storage, copia sul PC di casa) |
| Token OAuth Strava | SQLite (`provider_accounts`) | Idem |
| Note e tag | SQLite | Idem |
| File caricati | `/data/files` | Idem |
| Richieste di tile mappa | Il browser le fa a OpenFreeMap | **Sì**: OpenFreeMap vede l'IP del tuo dispositivo e le aree geografiche visualizzate (non la traccia). Mitigazione futura: self-hosting delle tile (non giustificato ora) |
| Ping di monitoraggio | healthchecks.io | Solo "ok/fail" e timestamp; nessun dato di attività |
| Metadati di rete | Coordinamento Tailscale | Nomi dei dispositivi, IP; il traffico è cifrato end-to-end (WireGuard). Il nome host `*.ts.net` finisce nei log pubblici di Certificate Transparency [NON VERIFICATO in questa sessione, noto dalla doc Tailscale]: **non usare nomi personali** per macchina e tailnet |
| Chiamate a Strava | Strava | Solo token e ID; nessun dato inviato (sola lettura) |

**Revoca dell'accesso Strava**: pulsante "Disconnect" → `POST /oauth/revoke` + cancellazione dei token (con opzione per eliminare anche i dati Strava). In alternativa, da strava.com → Settings → My Apps → Revoke; al primo sync l'app rileva il 401 e passa a `revoked`.

**Cancellazione completa**: `DELETE /api/data` elimina DB e file. **I backup mantengono i dati fino alla scadenza della retention** (massimo 12 mesi con la policy §17): per una cancellazione immediata, `restic forget --prune` di tutti gli snapshot o eliminazione del bucket (documentato nel RUNBOOK).

**API Agreement Strava (2026-06-01)**: alla cessazione dell'accordo (es. se smetti di usare l'app API) vanno cancellati tutti i dati ottenuti via API. Gli stessi dati restano tuoi e recuperabili con l'export in bulk di Strava, che è un canale diverso. Questa non è una consulenza legale; rileggere il testo integrale.

**Cifratura**: a riposo solo per i backup (fuori dalla VPS). Il disco della VM usa la cifratura di default dei volumi OCI [NON VERIFICATO per i volumi Always Free]; la cifratura applicativa del DB non è giustificata (la chiave starebbe sulla stessa macchina).

---

## 16. Deployment su Oracle Cloud Free Tier

### 16.1 Risorse (doc ufficiale Oracle, consultata 2026-09-30)

| Risorsa | Limite Always Free | Uso previsto |
|---------|--------------------|--------------|
| Compute A1 | 1.500 OCPU-h + 9.000 GB-h/mese = **2 OCPU / 12 GB** [FATTO] | 1 VM 2 OCPU / 12 GB (la tua) |
| Block storage | **200 GB totali** (boot + block), boot minimo 47 GB, **5 backup di volume** [FATTO] | Boot da circa 50 GB; dati < 1 GB |
| Object Storage | 20 GB (account solo Always Free) o 10+10+10 GB (con trial o pagamento), 50.000 richieste API/mese [FATTO] | Backup restic: < 1 GB, ~30 richieste/notte ≈ 1.000/mese |
| Traffico in uscita | 10 TB/mese [FATTO] | Trascurabile |
| **Recupero delle istanze inattive** | CPU p95 < 20% **e** rete < 20% **e** memoria < 20% (solo A1) su 7 giorni → "may be reclaimed" [FATTO] | **L'app soddisferà tutte e tre le condizioni** |
| Capacità | Possibile errore "out of host capacity" in creazione [FATTO] | Già superato (VM creata); rilevante solo se devi ricrearla |

**Discordanza tra fonti**: molte guide online (e la mia conoscenza pregressa) indicano per le A1 **4 OCPU / 24 GB**. La doc ufficiale attuale dice **2 OCPU / 12 GB per le tenancy Always Free**. Considero affidabile la doc ufficiale; la tua VM è coerente con essa.

### 16.2 Rischio di recupero per inattività: decisione da prendere in M0

Opzioni:

1. **Upgrade a Pay As You Go** mantenendo solo risorse Always Free, con budget alert a 1 €. È diffusamente riportato che le istanze PAYG non vengono recuperate, ma **la doc ufficiale non lo dice esplicitamente** [NON VERIFICATO]. Richiede la carta; rischio di addebiti solo creando risorse non gratuite.
2. **Restare Always Free** e accettare il rischio, rendendo **il ripristino una procedura di routine**: backup notturno fuori dalla VPS + script di setup + restore testato (RTO < 2 h).
3. Generare carico artificiale per superare le soglie. **Sconsigliato**: aggira lo spirito della policy e spreca risorse.

**Raccomandazione**: la 2 si fa in ogni caso (serve comunque come disaster recovery); la 1 è consigliata se accetti di associare una carta. Nel frattempo i backup devono essere attivi **prima** di importare i dati reali.

### 16.3 Configurazione della VM

- Ubuntu 24.04 aarch64; utente non-root `corsa`; `unattended-upgrades`; fuso UTC; swap da 2 GB (opzionale, protezione contro OOM durante le build).
- **Tailscale** sull'host (non in un container: meno parti mobili): `tailscale up --ssh`, HTTPS abilitato nella tailnet, `tailscale serve` in background verso `http://127.0.0.1:8000` (sintassi esatta del flag background da verificare con la CLI installata [NON VERIFICATO: `--bg`]).
- Nome macchina neutro (es. `corsa`) → `https://corsa.<tailnet>.ts.net`.
- **Nessun reverse proxy** (Caddy/Nginx): `tailscale serve` fa già terminazione TLS e proxy.
- Security List OCI: nessuna porta in ingresso dopo aver verificato SSH via Tailscale (tenere la console seriale OCI come accesso di emergenza).

### 16.4 Container

| Container | Comando | RAM stimata | Limite | Note |
|-----------|---------|-------------|--------|------|
| `api` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` (porta pubblicata solo come `127.0.0.1:8000`) | 100–200 MB | 512 MB | All'avvio esegue `alembic upgrade head` (lock: solo l'API migra) |
| `worker` | `python -m app.worker` | 100–250 MB | 512 MB | Attende che la migrazione sia completata (verifica della revision) |

- Image unico multi-stage: stage `node` (build della SPA) → stage `python:3.13-slim` (arm64) con la SPA copiata in `/app/static`.
- Volume bind: `/opt/corsa/data` → `/data` (DB + file).
- `restart: unless-stopped`; `healthcheck` su `/healthz`; logging `json-file` con `max-size: 10m`, `max-file: 3`.
- Build **sulla VPS** (ARM nativo, 12 GB bastano): nessun registry e nessuna build cross-arch. Tempo di build stimato 2–5 minuti.

**Carico complessivo**: meno di 1 GB di RAM, CPU vicina allo 0% salvo durante il backfill. I colli di bottiglia sono il rate limit di Strava e l'I/O del boot volume (irrilevante a questi volumi), non la VPS.

### 16.5 Aggiornamenti e rilascio

`scripts/deploy.sh` sulla VPS:

1. `git pull`
2. `docker compose build`
3. **backup pre-migrazione** (`sqlite3 corsa.db ".backup data/pre-deploy-<ts>.db"`, ultimi 5 conservati)
4. `docker compose up -d`
5. attesa di `/healthz` OK, altrimenti messaggio con le istruzioni di rollback

Rollback: `git checkout <tag precedente>` + `deploy.sh`; se la migrazione non era retrocompatibile, ripristino del backup pre-deploy.
Versionamento: tag git `vX.Y.Z` per ogni deploy "stabile".

### 16.6 Dominio e HTTPS

Nessun dominio da acquistare: il certificato Let's Encrypt per `*.ts.net` è emesso e rinnovato automaticamente da Tailscale [FATTO]. Se un giorno comprerai un dominio, basterà un CNAME o un'esposizione con Funnel/Caddy (non necessario).

---

## 17. Backup e disaster recovery

| Cosa | Come | Frequenza | Retention |
|------|------|-----------|-----------|
| DB SQLite | `sqlite3 .backup` (copia consistente a caldo) → restic | Notturno 03:30 UTC | 7 giornalieri, 4 settimanali, 12 mensili |
| File caricati | restic (deduplica: solo i nuovi) | Notturno | Idem |
| `.env` e configurazione | Password manager (manuale) | A ogni modifica | — |
| Copia secondaria | `restic copy` o rsync via Tailscale verso il PC di casa | Settimanale (Should) | Retention locale |
| Snapshot del volume OCI | Manuale prima di upgrade dell'OS | Ad hoc | Max 5 (limite free) |

- Repository restic su **OCI Object Storage via endpoint S3-compatibile** (Customer Secret Keys). Cifratura lato client di restic, con password nel password manager.
- Lo script di backup verifica anche il disco (> 80% → fallimento) ed esegue `restic check` settimanale; al termine fa ping a healthchecks.io (successo o fallimento).
- **Dati ricostruibili vs insostituibili**: tutto ciò che viene da Strava si può riscaricare (con il limite dei rate); **note, tag, tipo allenamento, esclusioni, file caricati e impostazioni no**. I backup proteggono soprattutto questi.
- **Restore** (RUNBOOK): VM nuova → script di setup (Docker, Tailscale) → `git clone` → `.env` dal password manager → `restic restore latest` → `docker compose up -d` → verifica del conteggio attività. **Drill di restore trimestrale** su una VM o in Docker locale.
- **Singolo punto di fallimento**: account Oracle chiuso = VM + Object Storage persi insieme. Per questo la **copia settimanale fuori da Oracle** (PC di casa) è consigliata.

---

## 18. Testing

**Parti più rischiose, da testare in profondità**:

1. **Sync Strava come macchina a stati**: cursore, paginazione, refresh con rotazione del refresh token, 429 con ripresa, 401, crash a metà.
2. **Mapper e normalizzazione**: unità, cadenza, fusi, `sport_type`.
3. **Metriche**: split, best effort, zone, EF, steady; errori qui falsano tutte le analisi senza dare segni evidenti.
4. **Deduplicazione**.
5. **Aggregati temporali**: confini di settimana ISO, fusi, periodi parziali.

| Livello | Cosa | Come |
|---------|------|------|
| Unit | Metriche su **stream sintetici** con risultato noto (es. 3,00 m/s costanti per 10 km → 5K esattamente in 1666,7 s; split da 5:33,3; soste con `moving=false`); formatter del passo; Theil-Sen | pytest, funzioni pure |
| Unit | Mapper Strava su **fixture JSON reali** (5–10 attività scaricate nello spike: corsa GPS con FC, tapis roulant, trail, attività non-corsa, attività con soste) | pytest; **fixture con coordinate troncate o traslate** se il repo diventa pubblico (la traccia rivela casa) |
| Integration | Sync end-to-end contro un finto Strava (respx): backfill di 3 pagine, 429 a metà, token scaduto, attività cancellata upstream, rinomina | pytest + SQLite temporaneo |
| Integration | Dedup: stessa attività 2 volte; FIT sovrapposto a Strava (Should); due Strava sovrapposte; attività adiacenti ma distinte (doppia corsa nello stesso giorno) → **nessun falso merge** | pytest |
| Integration | Crash: interrompere il worker dopo N attività → riavvio → stato finale identico a un'esecuzione pulita | pytest (simulazione con eccezione) |
| API | Filtri (matrice di combinazioni), statistiche contro calcoli di riferimento in Python, auth (401 senza header), CSRF (403 senza `X-Corsa`) | FastAPI TestClient |
| File (Should) | Parser su file reali (FIT HealthFit, GPX Apple, TCX) + file corrotti, troncati, zip-bomb, XML con entità esterne | pytest |
| Frontend | Solo formatter e conversioni di unità | Vitest |
| E2E | 3 flussi su DB seed: (1) dashboard carica e il cambio periodo aggiorna i grafici; (2) lista → filtro → apertura dettaglio con mappa e grafici; (3) modifica note e tag, persistite dopo il reload | Playwright |
| Performance | Seed di 1.000 attività sintetiche → p95 degli endpoint stats < 300 ms | Script una tantum |

Niente test di snapshot della UI né test per ogni componente: costo alto, valore basso.

---

## 19. Monitoring e operations

| Area | Soluzione |
|------|-----------|
| Logging | JSON su stdout (`logging` standard con formatter JSON), campo `job_id` nei log del worker; rotazione di Docker (30 MB massimo per container) |
| Error tracking | La tabella `jobs` + `source_records.status=failed` **sono** il tracker degli errori, visibili in `/sync`. Niente Sentry (invierebbe dati a terzi, sproporzionato) |
| Health check | `/healthz` (usato da Docker e dallo script di deploy) |
| Monitoring esterno | **healthchecks.io** (free) [NON VERIFICATO: limiti del piano free], due check: `backup` (atteso ogni 24 h, grace 2 h) e `sync` (il worker fa ping dopo ogni sync riuscito, atteso ogni 30 min, grace 3 h). Email se mancano. Funziona anche con app privata perché i ping sono in uscita |
| Metriche di sistema | Non installate (niente Prometheus/Grafana). Il disco è controllato dallo script di backup; `docker stats` all'occorrenza |
| Job falliti | Retry automatico al ciclo successivo per errori transitori; `failed` definitivo dopo 3 tentativi → badge rosso in `/sync` + pulsante "Retry"; il check `sync` scatta se falliscono per > 3 h |
| Import falliti a metà | §8.5: transazione per attività, stato ripristinato all'avvio, ripresa idempotente |
| Aggiornamenti | OS: `unattended-upgrades` (con riavvio automatico notturno solo se richiesto). App: `deploy.sh`. Dipendenze: Dependabot mensile (Should) |
| RUNBOOK | `docs/RUNBOOK.md`: setup VM, deploy, rollback, restore, rotazione dei segreti, ricollegamento Strava, cancellazione dati, cosa fare se Oracle recupera la VM |

---

## 20. MVP

### Must have (MVP)

- Infrastruttura: VM + Tailscale + Docker Compose + deploy script + **backup testato** + healthchecks.
- Autenticazione tramite identità Tailscale + protezione CSRF.
- Strava: OAuth, backfill, sync ogni 30 min + manuale, rilevamento delle modifiche su 30 giorni, riconciliazione settimanale delle cancellazioni, rate limiting, ripresa dopo crash.
- Modello dati multi-sorgente con raw, stream, dedup (intra-sorgente + rilevamento sovrapposizioni).
- Metriche: split per km, best effort, zone FC, EF, steady, gps_suspect.
- Lista attività con tutti i filtri di §12.4 e aggregato.
- Dettaglio attività completo (§12.3), con confronto con corse simili; editor di note, tag, tipo ed esclusione.
- Dashboard D1–D11.
- Records.
- Sync page e Settings (zone FC con ricalcolo).

### Should have (subito dopo)

Upload FIT/GPX/TCX + arricchimento cross-sorgente (cadenza via HealthFit) · UI di merge dei duplicati · FC a ritmo di riferimento + decoupling · trend della cadenza (D12) · export ZIP + cancellazione totale · CI con test e audit delle dipendenze · copia settimanale dei backup sul PC di casa · passaggio al nuovo base URL Strava (**entro il 2027-01-04**, anticipato in M2).

### Could have

Import Apple `export.zip` · Health Auto Export push · pagina "Training load" (TRIMP/ATL/CTL, etichettata `model`) · predittore gara · heatmap di tutti i percorsi · rilevamento "stesso percorso" · meteo storico (Open-Meteo: invierebbe coordinate e orari a terzi → opt-in) · webhook via Tailscale Funnel · PWA installabile · filtri salvati.

### Future (non deve influenzare l'architettura)

Metriche giornaliere di salute (VO2max, FC a riposo, HRV, sonno) · obiettivi e piani di allenamento · altri sport · multi-utente · app iOS nativa.

---

## 21. Roadmap

L'ordine è pensato per eliminare subito i rischi maggiori: infrastruttura su ARM, Tailscale, callback Strava e contenuto reale dei dati Apple→Strava vengono verificati prima di scrivere il dominio. I backup sono attivi **prima** dei dati reali.

| Fase | Obiettivo | Attività principali | Dipendenze | Output verificabile | Criterio di completamento |
|------|-----------|---------------------|------------|---------------------|---------------------------|
| **M0 Fondamenta** | Scheletro deployato e sicuro; rischi esterni chiariti | Repo, tooling, VM hardening, Tailscale, compose hello-world, auth tramite header, backup e restore con DB fittizio, healthchecks, **spike Strava** (OAuth + fixture), decisione PAYG | — | `https://corsa.<tailnet>.ts.net/healthz` OK dall'iPhone su rete mobile; snapshot restic presente; fixture JSON salvate | Tutti i task M0 superati; risposte scritte alle incognite dello spike (cadenza, callback, orario) |
| **M1 Dominio** | Modello dati e metriche corretti, senza rete | Modelli + migrazione iniziale, codec degli stream, mapper Strava, motore metriche, dedup, settings | M0 (fixture) | Suite di test verde su fixture e stream sintetici | Coverage delle funzioni in `metrics/`, `ingest/`, `domain/dedup` ≥ 90% |
| **M2 Sync Strava** | Dati reali importati in modo affidabile | Jobs + worker + scheduler, client Strava, OAuth, backfill, sync, riconciliazione, base URL configurabile | M1 | Il tuo storico reale nel DB | AC-02…AC-08 |
| **M3 API** | Tutti gli endpoint MVP | Attività, streams, similar, patch, stats, records, settings, tipi OpenAPI → TS | M2 (dati reali per validare) | OpenAPI completo; test API verdi | Tutti gli endpoint di §11 marcati MVP con test |
| **M4 UI base** | App navigabile | Shell, token dark, nav responsive, formatter, lista + filtri, dettaglio (mappa + grafici + split), Sync, Settings | M3 | Uso reale dal telefono | AC-09, AC-11, AC-13 |
| **M5 Dashboard** | Analisi | D1–D11, Records | M3, M4 | Dashboard completa | AC-10, AC-12 |
| **M6 Hardening → MVP** | Pronto per l'uso quotidiano | E2E, test di performance, drill di restore su dati reali, RUNBOOK, checklist di sicurezza | M5 | Tag `v1.0.0` | **Tutti gli AC di §22 superati** |
| **M7 Should** | Cadenza e qualità dei dati | Upload file, merge cross-sorgente, UI duplicati, analisi avanzate, export/cancellazione, CI | M6 (+ spike M0-09) | FIT HealthFit arricchisce una corsa Strava | AC-19…AC-21 |
| **M8 Could** | Estensioni | A scelta | M7 | — | Per singolo task |

---

## 22. Criteri di accettazione (Definition of Done dell'MVP)

L'MVP è completo quando **tutti** i seguenti criteri sono verificati sulla VPS con i tuoi dati reali:

| ID | Criterio |
|----|----------|
| AC-01 | Dall'iPhone su rete mobile con Tailscale attivo, `https://corsa.<tailnet>.ts.net` apre la dashboard con certificato valido. Da un dispositivo fuori dalla tailnet l'host non risponde. Una richiesta all'API senza header di identità (es. `curl` locale sulla VPS verso `127.0.0.1:8000/api/activities`) riceve **401**; una POST senza `X-Corsa` riceve **403** |
| AC-02 | "Connect Strava" → autorizzazione → ritorno su `/sync` con "Connected", scope contenente `activity:read_all`. Un access token scaduto (simulato impostando `expires_at` nel passato) viene rinnovato in modo trasparente e **il nuovo refresh token è persistito** |
| AC-03 | Al termine del backfill il numero di corse nel DB coincide con quello visibile su Strava (conteggio manuale per anno), le attività non-corsa sono `skipped` e i km totali per anno coincidono con Strava entro lo 0,5%. **Rieseguire il backfill crea 0 attività nuove** |
| AC-04 | Una nuova corsa registrata col Watch compare nell'app **entro 45 minuti** dalla sua comparsa su Strava senza azioni manuali, ed entro 2 minuti con "Sync now" |
| AC-05 | Con un 429 simulato (test) il job si sospende fino alla finestra successiva e completa senza perdite né duplicati |
| AC-06 | Uccidendo il container worker durante il backfill e riavviandolo, il job riprende e lo stato finale (conteggi, somme) è identico a un'esecuzione senza interruzioni |
| AC-07 | Due attività Strava sovrapposte: la seconda è segnalata come possibile duplicato ed esclusa dalle statistiche. Due corse distinte nello stesso giorno **non** sono segnalate |
| AC-08 | Rinominare una corsa su Strava → nome aggiornato dopo il sync successivo; note e tag locali invariati. Cancellarla su Strava → entro 7 giorni appare come "Deleted on Strava" ed è esclusa dalle statistiche, ma non cancellata |
| AC-09 | Nel dettaglio di una corsa GPS con FC: mappa col percorso; grafici di passo, FC e altimetria sincronizzati tra loro e con il marker sulla mappa; somma degli split = distanza e tempo totali (±1%); somma dei tempi nelle zone = tempo con FC (±1%) |
| AC-10 | La pagina Records mostra PR e progressione per 1K, 5K, 10K e mezza (se esiste), coerenti con i best effort (test di regressione su 3 corse verificate a mano); i PR ottenuti su tapis roulant o con GPS sospetto sono esclusi |
| AC-11 | Filtri combinati (es. 2026, 8–12 km, passo 5:00–5:40, tag "trail") restituiscono esattamente le attività attese (test); l'URL ricrea lo stato; a 375 px nessuno scroll orizzontale |
| AC-12 | Cambiando periodo, tutti i grafici della dashboard si aggiornano; endpoint stats < 300 ms p95 con 1.000 attività; i totali settimanali coincidono con una query di controllo; bucket parziali marcati |
| AC-13 | Note, tag, tipo ed esclusione modificati dall'UI persistono dopo il reload e dopo un sync forzato |
| AC-14 | Esiste uno snapshot notturno su Object Storage; un **restore su un ambiente pulito** seguendo il RUNBOOK riproduce lo stesso numero di attività e le stesse note in meno di 2 ore |
| AC-15 | Fermando il worker, healthchecks.io invia un'email entro la grace configurata; fermando il cron di backup, idem |
| AC-16 | Nessuna porta in ascolto su interfacce pubbliche oltre a quelle di Tailscale (`ss -tlnp`); Security List OCI senza ingresso; nessun segreto nel repo (`git grep` sui nomi delle variabili) |
| AC-17 | "Disconnect Strava" revoca l'accesso (l'app scompare da Strava → My Apps) e cancella i token |
| AC-18 | Cambiando la FC max nelle impostazioni, zone e metriche di tutte le attività vengono ricalcolate (job `recompute` completato) |

Should (M7): **AC-19** un FIT HealthFit di una corsa già importata da Strava viene allegato alla stessa attività (nessun duplicato) e la cadenza compare nel dettaglio; **AC-20** file non validi (estensione falsa, XML con entità esterne, gz > limite) vengono rifiutati con un messaggio chiaro; **AC-21** l'export ZIP contiene tutte le attività e i file e si reimporta in un'istanza pulita.

---

## 23. Rischi e mitigazioni

| # | Rischio | Prob./Grav. | Conseguenza | Mitigazione | Decisione da prendere prima |
|---|---------|-------------|-------------|-------------|------------------------------|
| R1 | **Oracle recupera la VM inattiva** | Media / Alta | App giù; possibile perdita della VM | Backup notturni fuori dalla VPS, RUNBOOK di restore testato, valutazione del PAYG | **PAYG sì/no in M0** |
| R2 | Account Oracle chiuso o sospeso | Bassa / Alta | Persi VM **e** Object Storage | Copia settimanale dei backup sul PC di casa | — |
| R3 | Strava cambia API o termini (base URL 2027-01-04, endpoint rimossi: club a settembre 2026) | Alta (cambi continui) / Media | Sync rotto | Base URL configurabile; client isolato in un modulo; JSON grezzo salvato; lettura del changelog Strava a ogni rilascio | — |
| R4 | Strava revoca o limita l'app, o chiudi l'account | Bassa / Media | Nessun dato nuovo | I dati storici restano locali; FIT via HealthFit come sorgente alternativa completa | — |
| R5 | **Cadenza assente da Strava** (e forse altre metriche Apple) | Alta / Bassa | Grafici di cadenza vuoti | Grafici condizionali; M7 file FIT; spike M0-08/M0-09 | Accettare il costo di HealthFit? (in M7) |
| R6 | Callback OAuth su `ts.net` rifiutata da Strava | Bassa / Bassa | OAuth dall'UI non possibile | Fallback: flusso `localhost` via tunnel SSH una tantum | Verifica in M0-08 |
| R7 | Orario nascosto su Strava → mezzanotte | Bassa / Media | Dedup e ora del giorno errati | Verifica in M0-08; rilevamento automatico (orario 00:00:01 → warning) | — |
| R8 | Errori nel calcolo delle metriche (unità, split, zone) | Media / Alta | Analisi sbagliate e credute vere | Funzioni pure testate su stream sintetici; `algo_version` con ricalcolo; confronto con i valori Strava (tolleranza) come test di sanità | — |
| R9 | Qualità del GPS e della FC ottica | Alta / Media | Best effort falsi, EF rumoroso | `gps_suspect`, mediane e trend robusti, n visibile, esclusione manuale | — |
| R10 | Falsi positivi e negativi nella deduplicazione | Media / Media | Corse perse o doppie | Nessun merge automatico tra attività della stessa sorgente; flag + risoluzione manuale; test su casi limite | Soglie (120 s / 80% / 10%) configurabili nel codice |
| R11 | Corruzione di SQLite o lock tra API e worker | Bassa / Media | Errori `database is locked` | WAL + `busy_timeout=5000`; transazioni brevi; backup con `.backup`, mai copia a caldo del file | — |
| R12 | Perdita della password restic o dei segreti | Bassa / Alta | Backup irrecuperabili | Password manager; verifica durante il drill di restore | — |
| R13 | Privacy: traccia GPS (casa) esposta in repo pubblici o fixture | Media / Media | Esposizione della posizione | Repo privato; fixture con coordinate traslate; `.gitignore` su `/data` | Repo privato |
| R14 | Dipendenza da OpenFreeMap (servizio gratuito senza SLA) | Media / Bassa | Mappe non visibili | Stile e URL delle tile configurabili; fallback alle tile raster OSM (policy d'uso OSM da rispettare) | — |
| R15 | Scope creep (dashboard "con tutto") | Alta / Media | MVP mai finito | MoSCoW rigido; D12+ fuori dall'MVP | — |
| R16 | Formato non documentato dell'export Apple | Media / Bassa | Parser fragile | Solo Could; parsing difensivo; test su un export reale | — |

---

## 24. Architecture Decision Records

**ADR-01 — Monolite modulare (API + worker) invece di microservizi**
- *Alternative*: microservizi; un unico processo con thread in background.
- *Perché*: una persona, un dominio piccolo. Due processi dallo stesso codice isolano i crash del worker dall'API senza costi di coordinamento.
- *Trade-off*: scalabilità orizzontale inesistente (irrilevante con un utente).
- *Futuro*: i moduli `ingest/` e `metrics/` sono estraibili se mai servisse.

**ADR-02 — SQLite invece di PostgreSQL/TimescaleDB**
- *Alternative*: Postgres (+ PostGIS), TimescaleDB, DuckDB.
- *Perché*: zero container e zero tuning; backup = un file; volume < 1 GB in anni; SQLite ha window function, JSON e CHECK constraint. Postgres porterebbe PostGIS e concorrenza, che qui non servono.
- *Trade-off*: un solo writer (API + worker, gestito con WAL); tipi più deboli (compensati dai CHECK); niente query geospaziali in SQL.
- *Futuro*: SQLAlchemy + Alembic rendono la migrazione a Postgres un lavoro di giorni, non di settimane. Da rivalutare se arrivano multi-utente o query geospaziali pesanti. **Rischio**: evitare SQL specifico di SQLite fuori da un modulo `stats/queries`.

**ADR-03 — Stream come blob compresso per source_record**
- *Alternative*: una riga per punto; file Parquet su disco; colonne array in Postgres.
- *Perché*: gli stream si leggono sempre interi per una sola attività; le metriche si calcolano in Python. Un blob = una lettura.
- *Trade-off*: niente SQL sui singoli punti (es. "tutti i km percorsi sopra 170 bpm" richiede una scansione in Python: accettabile per centinaia di attività).
- *Futuro*: `codec_version` permette di cambiare formato (es. array binari) con una migrazione dei dati. **Difficile da cambiare una volta accumulati i dati** → deciso in M1.

**ADR-04 — Activity canonica + source_records (modello multi-sorgente)**
- *Alternative*: tabelle per provider (`strava_activities`); un'unica tabella con colonne del provider.
- *Perché*: deduplicazione e arricchimento tra sorgenti (Strava + FIT) sono requisiti espliciti; il raw per provider resta tracciabile.
- *Trade-off*: più join e la logica di precedenza tra sorgenti.
- *Futuro*: aggiungere una sorgente = un mapper + un valore dell'enum. **Decisione più difficile da cambiare di tutto il progetto.**

**ADR-05 — Strava via polling come sorgente primaria** — **SUPERATO (2026-10-01)** [DECISIONE]: integrazione API/OAuth Strava rimossa per complessità di gestione; unica sorgente = import da file (export Strava .zip, FIT/GPX/TCX, Health Auto Export). La tabella `provider_accounts` resta nel DB, inutilizzata. Sorgente automatica sostitutiva: pull da intervals.icu (API key, polling 30 min, file originali importati come file; le attività che intervals.icu riceve da Strava non sono esposte dalla sua API).
- *Alternative*: webhook; export in bulk periodico; Apple come primaria.
- *Perché*: i webhook richiedono un endpoint pubblico (in contrasto con l'accesso privato); il polling ogni 30 minuti usa circa il 10% del budget giornaliero; Strava ha già i dati Apple in forma pulita e completa (salvo la cadenza).
- *Trade-off*: latenza fino a 30 minuti; le cancellazioni sono viste solo con la riconciliazione settimanale.
- *Futuro*: webhook con Tailscale Funnel limitato a un path (Could), senza cambiare il modello.

**ADR-06 — Accesso privato con Tailscale Serve + identità da header, senza login applicativo**
- *Alternative*: app pubblica con dominio, Caddy e login; allowlist dell'IP di casa nella Security List; VPN WireGuard gestita a mano.
- *Perché*: zero porte esposte, HTTPS valido senza dominio, funziona dal telefono ovunque; niente gestione di password o sessioni.
- *Trade-off*: serve il client Tailscale su ogni dispositivo; dipendenza dal servizio di coordinamento Tailscale (se è giù, le connessioni già stabilite di solito continuano, ma non è garantito). Nessuna condivisione con terzi senza invitarli nella tailnet.
- *Futuro*: per l'esposizione pubblica servono login, sessioni, rate limiting e protezione CSRF basata sui cookie. **Decisione difficile da invertire** (tocca tutta la superficie dell'API).

**ADR-07 — Docker Compose con build sulla VPS**
- *Alternative*: systemd + virtualenv; registry con build in CI multi-arch.
- *Perché*: ambiente identico tra dev e prod; rollback semplice; la VPS ARM builda nativamente in pochi minuti.
- *Trade-off*: overhead di Docker (circa 100 MB); build sul server di produzione.
- *Futuro*: passaggio a CI + GHCR banale se le build diventano lente.

**ADR-08 — SPA React (Vite) servita da FastAPI**
- *Alternative*: Next.js (SSR); HTMX + template Jinja.
- *Perché*: una dashboard molto interattiva (grafici sincronizzati, mappa) è naturale in una SPA; nessun server Node in produzione.
- *Trade-off*: SEO nullo (irrilevante); bundle JS più pesante (ECharts + MapLibre ≈ 1–1,5 MB, con code-splitting per rotta).
- *Futuro*: PWA semplice da aggiungere.

**ADR-09 — Apache ECharts per i grafici**
- *Alternative*: Recharts (SVG, lento con migliaia di punti, poca interattività avanzata); uPlot (velocissimo ma basso livello); Plotly (pesante).
- *Perché*: canvas, dataZoom, assi multipli, `connect` per sincronizzare i grafici, heatmap calendario inclusa, tema scuro.
- *Trade-off*: API a oggetti di configurazione verbosa; bundle grande (si importano solo i moduli usati).
- *Futuro*: sostituibile per singolo grafico.

**ADR-10 — MapLibre + OpenFreeMap**
- *Alternative*: Leaflet + tile raster OSM (la policy OSM scoraggia l'uso intensivo e non ha uno stile scuro); Mapbox o MapTiler (chiave API, limiti, account).
- *Perché*: gratuito, senza chiave, stile Dark disponibile, vettoriale.
- *Trade-off*: servizio comunitario senza SLA; le richieste di tile rivelano l'area visualizzata.
- *Futuro*: self-hosting delle tile (OpenFreeMap è open source) se diventasse necessario.

**ADR-11 — Coda di job su tabella SQLite**
- *Alternative*: Celery/RQ + Redis; APScheduler in-process; cron che invoca script.
- *Perché*: persistente, visibile nell'UI, zero servizi aggiuntivi; il volume è di pochi job all'ora.
- *Trade-off*: si implementano claim, heartbeat e retry (circa 100 righe da testare bene).
- *Futuro*: la sostituzione con una libreria di coda non tocca il dominio.

**ADR-12 — Metriche calcolate da noi, versionate**
- *Alternative*: usare split, best effort e zone di Strava.
- *Perché*: coerenza tra sorgenti (FIT e Strava calcolati allo stesso modo), controllo delle soglie e delle esclusioni, ricalcolo al cambio delle zone.
- *Trade-off*: i numeri potrebbero differire leggermente da quelli di Strava (da spiegare nel tooltip).
- *Futuro*: `algo_version` permette di migliorare gli algoritmi e ricalcolare tutto.

**ADR-13 — Unità SI interne, passo derivato, UTC + IANA**
- *Perché*: evita errori di conversione e medie di passi; i fusi corretti per ogni attività (viaggi) sono gestiti.
- *Trade-off*: conversioni nel frontend (formatter centralizzati e testati).
- **Difficile da cambiare** → fissato in M1.

**ADR-14 — Backup con restic su OCI Object Storage + copia a casa**
- *Alternative*: Litestream (replica continua); snapshot dei volumi OCI; rclone di un dump cifrato con age.
- *Perché*: cifratura, deduplicazione, retention e verifica in un solo strumento; RPO di 24 h sufficiente (i dati Strava sono riscaricabili).
- *Trade-off*: fino a 24 h di note e tag a rischio.
- *Futuro*: Litestream se l'RPO dovesse scendere a minuti.

**ADR-15 — Nessun user_id / single-user esplicito**
- *Perché e trade-off*: vedi §7.3. Un multi-utente futuro richiederebbe comunque una riscrittura di auth e OAuth.

---
## 25. Backlog iniziale (per milestone, ordinato per priorità)

Formato: **ID — Titolo** · Priorità (P0 = bloccante per l'MVP, P1 = Should, P2 = Could) · Dipendenze
Descrizione · ✅ Criterio di accettazione · 📝 Note tecniche

### M0 — Fondamenta e spike

**M0-01 — Struttura del repository** · P0 · —
Monorepo: `backend/` (pacchetto `app` con moduli `api/`, `domain/`, `ingest/`, `metrics/`, `stats/`, `worker/`), `frontend/`, `deploy/` (compose, script), `docs/`. Tooling: `uv` + ruff + pytest; npm + eslint + prettier; `Makefile` o script con `test`, `lint`, `dev`. Repo **privato** su GitHub; `.gitignore` su `data/` e `.env`.
✅ `make test` e `make lint` girano in locale su un progetto vuoto. 📝 Python 3.13; verificare la disponibilità di wheel arm64 per tutte le dipendenze.

**M0-02 — Decisione PAYG e budget alert** · P0 · —
Decidere l'upgrade a Pay As You Go (§16.2). Se sì: upgrade + budget alert a 1 €.
✅ Decisione annotata nel RUNBOOK. 📝 Decisione tua, non tecnica.

**M0-03 — Hardening base della VM** · P0 · —
Utente `corsa` non-root con sudo, chiavi SSH, `unattended-upgrades`, fuso UTC, swap 2 GB, `sqlite3` CLI, Docker Engine + compose plugin dal repo ufficiale Docker (arm64).
✅ `docker run --rm hello-world` ok; `unattended-upgrades --dry-run` ok. 📝 Script `deploy/setup-vm.sh` idempotente: servirà per il restore.

**M0-04 — Tailscale e accesso privato** · P0 · M0-03
Installazione di Tailscale, `tailscale up --ssh`, HTTPS e MagicDNS attivi nella tailnet, nome macchina neutro, `tailscale serve` persistente verso `127.0.0.1:8000`. ACL: solo il tuo utente. Client Tailscale su iPhone e PC. Poi chiusura della porta 22 nella Security List OCI.
✅ Da iPhone su rete mobile `https://corsa.<tailnet>.ts.net` risponde (anche un server di test); `ssh` funziona via Tailscale; `nmap` dall'esterno sull'IP pubblico non mostra porte aperte. 📝 Verificare la sintassi corrente di `tailscale serve` (flag background). Tenere la console seriale OCI come accesso di emergenza.

**M0-05 — Skeleton FastAPI + container + auth** · P0 · M0-01, M0-04
Image multi-stage (placeholder SPA), servizi `api` e `worker` (worker che per ora fa solo log), `/healthz`, dependency di auth su `Tailscale-User-Login` ∈ `ALLOWED_LOGINS`, middleware anti-CSRF (`X-Corsa`) sui metodi mutanti, header di sicurezza, bypass solo in `ENV=dev`.
✅ Test: 401 senza header, 403 su POST senza `X-Corsa`, 200 con entrambi; deploy sulla VM raggiungibile via ts.net. 📝 Porta pubblicata come `127.0.0.1:8000:8000`.

**M0-06 — Backup e monitoraggio dei backup** · P0 · M0-03
Bucket OCI Object Storage + Customer Secret Key; repository restic; script `deploy/backup.sh` (sqlite `.backup` → restic backup → forget/prune → check settimanale → controllo disco → ping healthchecks.io); cron alle 03:30.
✅ Con un DB fittizio: snapshot visibile (`restic snapshots`); restore in una cartella temporanea identico (checksum); disattivando il cron arriva l'email di healthchecks.io. 📝 Password restic e chiavi nel password manager **prima** di proseguire.

**M0-07 — Script di deploy e RUNBOOK iniziale** · P0 · M0-05, M0-06
`deploy/deploy.sh` (§16.5) e `docs/RUNBOOK.md` con setup, deploy, rollback e restore.
✅ Un deploy da commit nuovo a servizio aggiornato con un comando; rollback provato una volta.

**M0-08 — Spike Strava** · P0 · M0-04
Creazione dell'app Strava (callback domain = host ts.net), flusso OAuth manuale (anche con curl), download di detail + streams per 5–10 attività rappresentative (GPS+FC, tapis roulant, trail, non-corsa, corsa con soste) → `backend/tests/fixtures/strava/` (coordinate traslate).
✅ Risposte scritte nel RUNBOOK: (a) callback `ts.net` accettata? (b) quali stream arrivano dalle corse del Watch (cadence? watts? altitude?) (c) unità della `average_cadence` (d) l'orario è reale o mezzanotte? (e) `workout_type` delle attività Apple. 📝 Riduce i rischi R5, R6, R7.

**M0-09 — Spike HealthFit (opzionale, anticipabile)** · P1 · —
Esportare 1–2 corse come FIT con HealthFit; ispezionare con `garmin-fit-sdk` i messaggi presenti (record: FC, cadenza, potenza, velocità; lap; session).
✅ Elenco dei campi disponibili annotato; decisione di andare avanti con M7. 📝 Verificare prezzo e modalità di export attuali dell'app.

### M1 — Dominio, database, metriche

**M1-01 — Modelli SQLAlchemy + migrazione iniziale** · P0 · M0-01
Tutte le tabelle di §7.2 con vincoli, indici e CHECK; engine con pragma `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`.
✅ `alembic upgrade head` su un DB vuoto; test che violano UNIQUE e CHECK falliscono come previsto. 📝 Timestamp come testo ISO UTC; helper unico per l'ora corrente.

**M1-02 — Codec degli stream e canali canonici** · P0 · M1-01
Encode/decode gzip JSON; validazione (array della stessa lunghezza, `time` crescente); elenco dei canali canonici.
✅ Round-trip senza perdita su fixture; errore esplicito su array disallineati.

**M1-03 — Mapper Strava → canonico** · P0 · M1-02, M0-08
Funzione pura `(summary, detail, streams) → CanonicalActivity(+laps, streams)`: `sport_type`, tapis roulant, `workout_type`, IANA timezone, `local_date`, cadenza normalizzata, `has_*`, `mapper_version`.
✅ Test su tutte le fixture M0-08: valori attesi scritti a mano per almeno 3 attività; attività non-corsa → `skipped`.

**M1-04 — Motore delle metriche** · P0 · M1-02
Funzioni pure: tempo in movimento, split per km, best effort (7 distanze), tempo nelle zone, EF, `pace_cv` / `is_steady`, `gps_suspect`; `ALGO_VERSION`.
✅ Test su stream sintetici con risultati analitici (§18), incluse soste, stream senza FC, stream più corto della distanza target, salti GPS. Coverage ≥ 90%. 📝 Solo libreria standard.

**M1-05 — Servizio di dedup e upsert** · P0 · M1-01
Upsert per `(source, external_id)`; ricerca dei candidati per sovrapposizione (§8.4); allegato cross-sorgente vs flag di duplicato; aggiornamento dei soli campi non locali.
✅ Matrice di test §18 (4 casi + doppia corsa nello stesso giorno) verde; i campi locali non vengono mai sovrascritti.

**M1-06 — Settings e ricalcolo** · P0 · M1-04
Default delle zone (% di FC max, con FC max iniziale = massimo osservato negli ultimi 12 mesi, marcato `est.`); job `recompute` per le attività con `algo_version` o `zones_hash` diversi.
✅ Cambiando le zone, le metriche cambiano per tutte le attività; ricalcolo idempotente.

### M2 — Sincronizzazione Strava

**M2-01 — Jobs, worker, scheduler** · P0 · M1-01
Claim atomico, heartbeat, recupero dei job stantii all'avvio, `not_before`, retry con backoff, scheduler interno (sync ogni 30', riconciliazione settimanale, `recompute` all'avvio se necessario), attesa della migrazione.
✅ Test: due worker concorrenti non prendono lo stesso job; un job con heartbeat vecchio torna `queued`; un job in errore viene ritentato 3 volte poi `failed`.

**M2-02 — Client Strava** · P0 · M2-01
httpx con timeout; refresh del token con **persistenza atomica** del nuovo refresh token prima di usare il nuovo access token; lettura degli header di rate limit; autolimitazione (90/15', 900/giorno); 429 → `RateLimited(resume_at)`; 401 → `ReauthRequired`; base URL da `STRAVA_API_BASE`.
✅ Test respx: refresh con rotazione, 429 alla richiesta N, 401 dopo il refresh, 5xx con retry.

**M2-03 — Endpoint OAuth** · P0 · M2-02
`/api/strava/connect`, `/callback` (verifica di `state` e scope), `/status`, `/disconnect` (`/oauth/revoke`).
✅ AC-02 e AC-17.

**M2-04 — Job di backfill** · P0 · M2-02, M1-03, M1-05, M1-04, **M0-06**
Paginazione completa → source_record per ogni attività (le non-corsa `skipped`) → per ogni corsa ancora da importare: detail + streams → pipeline §8.1; progresso aggiornato; ripresa.
✅ AC-03, AC-05, AC-06. 📝 Esecuzione sui dati reali solo con i backup attivi.

**M2-05 — Sync incrementale + finestra di modifica di 30 giorni** · P0 · M2-04
Lista `after=cursor-1h`, nuove corse → pipeline; lista degli ultimi 30 giorni → confronto dei summary → refetch del dettaglio se cambiati; ping a healthchecks.io `sync` a fine job riuscito.
✅ AC-04, rinomina di AC-08.

**M2-06 — Riconciliazione settimanale** · P0 · M2-04
Lista completa degli ID → `upstream_deleted_at` + esclusione; ricomparsa → ripristino.
✅ Cancellazione di AC-08 (test respx + verifica manuale).

**M2-07 — API di sync e dei job** · P0 · M2-01
`POST /api/sync`, `GET /api/jobs`, `GET /api/jobs/{id}`, retry del source_record.
✅ Test API; un secondo `POST /api/sync` con un sync già in coda non crea un nuovo job.

**M2-08 — Migrazione del base URL Strava** · P0 (scadenza **2027-01-04**) · M2-02
Verifica del nuovo host `https://api-v3.strava.com` (compatibilità di path e auth secondo la doc a quella data) e cambio del valore di default.
✅ Sync reale riuscito con il nuovo base URL prima di dicembre 2026. 📝 Controllare il changelog Strava.

### M3 — API di lettura

**M3-01 — Lista attività con filtri e aggregato** · P0 · M2-04
Tutti i parametri di §11; validazione Pydantic; ordinamento con whitelist di colonne; aggregato con passo ponderato; esclusi di default `excluded_from_stats` e `duplicate_of_id`.
✅ Test a matrice dei filtri; p95 < 100 ms con 1.000 attività.

**M3-02 — Dettaglio, stream, simili, raw** · P0 · M3-01
Dettaglio completo con flag PR sui best effort (PR alla data dell'attività); stream con selezione dei canali e risposta gzip; simili (stesso tipo, ±15%, ultime 5).
✅ Test API sulle fixture; somma degli split ≈ totale (AC-09 lato backend).

**M3-03 — Modifiche locali e tag** · P0 · M3-02
PATCH (note, tipo, tag creati al volo, esclusione); `GET /api/tags`.
✅ AC-13 lato API; un sync successivo non altera i campi locali (test).

**M3-04 — Endpoint statistici** · P0 · M3-01
summary (con periodo precedente a parità di giorni), volume (week/month con `partial`), calendar, distribution, zones, trends (mediana mobile 28 gg + Theil-Sen + n, nessun trend se n < 8), pace-hr, top-weeks. Query in `stats/queries.py`.
✅ Ogni endpoint confrontato con un calcolo di riferimento in Python su dati seed; test sul confine di settimana ISO con fuso Europe/Rome (corsa di domenica alle 23:30 locali).

**M3-05 — Records** · P0 · M3-04
PR correnti per distanza (esclusi indoor e `gps_suspect`; PR da gara distinti) + storico dei miglioramenti.
✅ Test su una progressione sintetica.

**M3-06 — Tipi TypeScript da OpenAPI** · P1 · M3-01
Script `npm run gen:api` con `openapi-typescript`.
✅ Il frontend compila contro i tipi generati.

### M4 — Frontend base

**M4-01 — Shell dell'app e design token** · P0 · M0-05
Vite + React + TS, router, TanStack Query, Tailwind con token dark (§12.2), top bar e bottom nav, client API (header `X-Corsa`), formatter (passo, durata, distanza, data locale) con test Vitest, stati di errore e caricamento sobri.
✅ Navigazione tra le 6 rotte da desktop e da 375 px; test dei formatter verdi.

**M4-02 — Pagina Activities** · P0 · M3-01, M4-01
Filtri nell'URL, tabella e righe mobile, ordinamento, paginazione, riga dell'aggregato.
✅ AC-11.

**M4-03 — Pagina Activity (dettaglio)** · P0 · M3-02, M3-03, M4-01
Statistiche, mappa MapLibre + OpenFreeMap Dark con traccia colorata, grafici ECharts sincronizzati (`echarts.connect`) + marker sulla mappa, split con barre, zone, best effort con badge PR, simili, lap, editor di note/tag/tipo/esclusione, sezione sources/raw.
✅ AC-09 (UI), AC-13. 📝 Code-splitting: MapLibre ed ECharts caricati solo dove servono. Grafici di cadenza e potenza solo se presenti.

**M4-04 — Pagina Sync** · P0 · M2-07, M4-01
Stato della connessione (connect/disconnect), ultimo sync, uso del rate limit, "Sync now", storico dei job, elementi falliti con retry, banner di reauth.
✅ Flusso di connessione completo dall'UI; un errore simulato è visibile e ritentabile.

**M4-05 — Pagina Settings** · P0 · M1-06, M4-01
FC max e riposo, limiti delle zone (validati come crescenti), soglia steady; conferma prima del ricalcolo.
✅ AC-18.

### M5 — Dashboard e Records

**M5-01 — Selettore del periodo + strip riassuntiva (D1)** · P0 · M3-04, M4-01
Preset + intervallo custom nell'URL; granularità automatica e modificabile; Δ a parità di giorni.
✅ Il cambio di periodo aggiorna la strip; Δ corretto a metà mese (test).

**M5-02 — Volume, lunga, calendario (D2, D3, D4)** · P0 · M5-01
✅ Bucket parziale tratteggiato; toggle km/tempo/corse/D+; calendario a 12 mesi.

**M5-03 — Passo, EF, passo vs FC (D5, D6, D7)** · P0 · M5-01
Marcatori `n`, trend nascosto con n < 8, tooltip "How is this computed".
✅ Valori e trend coerenti con `/api/stats/trends`; con dati insufficienti un messaggio sobrio, non un grafico vuoto.

**M5-04 — Zone, distribuzione, top settimane (D8, D9, D11)** · P0 · M5-01
✅ Somma delle percentuali delle zone = 100%; top settimane cliccabili (→ lista filtrata su quella settimana).

**M5-05 — Pagina Records (D10)** · P0 · M3-05, M4-01
Tabella dei PR, grafico a gradini + scatter per distanza, link all'attività.
✅ AC-10.

### M6 — Hardening e rilascio dell'MVP

**M6-01 — E2E Playwright** · P0 · M5-*
3 flussi (§18) su DB seed in Docker locale.
✅ Suite verde in locale (e in CI quando c'è).

**M6-02 — Performance** · P0 · M5-*
Seed di 1.000 attività sintetiche; misura del p95 degli endpoint; eventuali indici aggiuntivi.
✅ AC-12 (parte performance).

**M6-03 — Drill di restore sui dati reali + RUNBOOK completo** · P0 · M2-04, M0-06
✅ AC-14.

**M6-04 — Monitoring completo** · P0 · M2-05, M0-06
Check `sync` e `backup` attivi con grace corrette; test dei due allarmi.
✅ AC-15.

**M6-05 — Checklist di sicurezza** · P0 · tutto
Porte, Security List, segreti, permessi dei file (`600` su DB ed `.env`), header, CSRF, `pip-audit`/`npm audit` puliti o accettati motivatamente.
✅ AC-01, AC-16.

**M6-06 — Verifica di accettazione dell'MVP e tag v1.0.0** · P0 · M6-01…05
✅ Tutti gli AC-01…AC-18 spuntati in `docs/ACCEPTANCE.md` con data ed evidenza.

### M7 — Should

**M7-01 — Upload file FIT/GPX/TCX** · P1 · M6-06, M0-09
Endpoint multipart, validazione (§14.1), salvataggio per sha256, job `file_import`, parser FIT (`garmin-fit-sdk`), GPX/TCX (`defusedxml` + estensioni `gpxtpx`), distanza haversine se manca, UI di upload in `/sync` (multi-file, anche dall'app File dell'iPhone).
✅ AC-20; import di FIT HealthFit, GPX Apple e TCX di prova.

**M7-02 — Arricchimento cross-sorgente** · P1 · M7-01
Allegato alla corsa Strava esistente, scelta dello `stream_source` più ricco, ricalcolo delle metriche, `has_cadence`.
✅ AC-19.

**M7-03 — UI dei duplicati** · P1 · M1-05, M4-03
Elenco dei possibili duplicati; azioni merge, "not a duplicate" ed escludi.
✅ Merge reversibile (i source_record restano tracciati).

**M7-04 — Analisi avanzate** · P1 · M5-03
FC a passo di riferimento (fascia configurabile, pendenza entro ±2%), decoupling aerobico (Pa:HR prima e seconda metà, solo corse steady > 45 min), trend della cadenza per fascia di passo (D12).
✅ Test su stream sintetici con deriva nota.

**M7-05 — Export e cancellazione totale** · P1 · M3-*
ZIP (CSV + JSON + file originali); `DELETE /api/data` con conferma testuale; documentazione della cancellazione dai backup.
✅ AC-21.

**M7-06 — CI e dipendenze** · P1 · M6-01
GitHub Actions: lint, test backend, test frontend, E2E; Dependabot mensile; `pip-audit` e `npm audit`.
✅ Pipeline verde su una PR di prova.

**M7-07 — Copia dei backup fuori da Oracle** · P1 · M0-06
Copia settimanale via Tailscale sul PC di casa (`restic copy` o rsync, schedulata sul PC).
✅ Snapshot presente anche in locale; restore da quella copia provato una volta.

### M8 — Could (da ordinare al momento)

- **M8-01** Import Apple `export.zip` (streaming `iterparse`, workout Running + GPX + campioni di FC e cadenza per finestra temporale) · P2
- **M8-02** Pagina Training load (TRIMP → ATL 7d / CTL 42d / TSB), tutta marcata `model` con spiegazione · P2
- **M8-03** Predittore gara (Riegel) marcato `model` · P2
- **M8-04** Heatmap di tutti i percorsi + rilevamento "stesso percorso" · P2
- **M8-05** Meteo storico opt-in (Open-Meteo), con avviso privacy · P2
- **M8-06** Webhook Strava via Tailscale Funnel su un solo path, con verifica del `verify_token` · P2
- **M8-07** PWA (manifest + icona) · P2
- **M8-08** Filtri salvati · P2

---

---

## 26. Analisi AI (OpenRouter)

Aggiunta su richiesta esplicita dopo il piano originale. **[DECISIONE]** L'AI **interpreta** numeri già calcolati dall'app; non calcola, non predice, non viene mai chiamata in automatico.

### 26.1 Cosa fa e cosa no

| Funzione | Scelta | Motivo |
|----------|--------|--------|
| **Analisi di una corsa** (dettaglio attività) | LLM | Lettura combinata di split, FC, zone, EF, corse simili: è sintesi, non calcolo |
| **Analisi di un periodo** (dashboard, periodo selezionato vs precedente) | LLM | Mette insieme volume, picchi di carico, trend steady, PR, distribuzione intensità |
| Medie, trend, Δ periodi, PR, carico, distribuzione | **Deterministico** (già in `/api/stats/*`) | Il codice è esatto e gratuito; l'LLM riceve i risultati |
| Picchi di carico (> 15 % sulla media 4 settimane), settimane vuote | **Deterministico** (`flags` nel contesto) | Soglia nota (D2); il modello li commenta soltanto |
| Previsioni (tempo gara, forma) | **Escluse** | Senza modello statistico validato sarebbe un numero inventato; il predittore Riegel resta Could (M8), etichettato `model` |
| Chat libera "chiedi all'AI" | **Esclusa** | Domande aperte = contesto grande, risposte non verificabili, quota 50/giorno |
| Riepiloghi periodici automatici | **Esclusi** | Consumerebbero quota senza un'azione dell'utente |

### 26.2 Flusso

```
GET  /api/ai/{activity/{id} | period?from_date&to_date}  → solo cache + stato (mai il modello)
POST /api/ai/{activity/{id} | period {from_date,to_date}} →
  contesto deterministico (riusa get_activity, get_similar, stats.summary/volume/trends/zones/records)
  → gate "dati sufficienti" (corsa senza distanza/tempo; periodo < 3 corse → 422, nessuna richiesta)
  → cache per hash(prompt version, catena modelli, soggetto, contesto) → hit = nessuna richiesta
  → lock di processo (doppio click = 409, non due richieste)
  → OpenRouter: Ultra → Super → Qwen → errore controllato
  → estrazione + validazione Pydantic → salvataggio in `ai_analyses` (un risultato per soggetto)
```

GET restituisce `stale=true` quando dati o prompt sono cambiati dopo l'analisi salvata: l'UI la mostra attenuata con "Re-analyze".

### 26.3 Modelli e fallback

Ordine fisso in `app/core/config.py` (`AI_PRIMARY_MODEL`, `AI_FALLBACK_MODEL_1`, `AI_FALLBACK_MODEL_2`; sovrascrivibili da `.env`): `nvidia/nemotron-3-ultra-550b-a55b:free` → `nvidia/nemotron-3-super-120b-a12b:free` → `qwen/qwen3.8-27b:free`.

- **Fallback al modello successivo**: timeout, errore di rete, 429/5xx dopo i retry, altri 4xx, errore nel body (anche con HTTP 200), body non JSON, risposta vuota, output non estraibile o non conforme allo schema.
- **Stop immediato senza fallback**: 401 (chiave errata: fallirebbero tutti) e quota locale esaurita.
- **Retry**: solo su 429/5xx, `AI_MAX_RETRIES=1` per modello, backoff `AI_RETRY_BACKOFF_S·2^n`; se `Retry-After` > `AI_MAX_WAIT_S` si passa subito al modello successivo. Caso peggiore: 6 richieste.
- Log (`app.ai.openrouter`): modello, status, latenza, motivo del fallback. Mai la chiave, mai il prompt, mai il body del provider.

### 26.4 Quota locale

Ogni richiesta HTTP in uscita (retry inclusi) consuma quota: `AI_DAILY_LIMIT=40` (giorno UTC, persistito in `settings.ai_usage`, sopravvive ai riavvii) e `AI_MINUTE_LIMIT=10` (in memoria). Limiti OpenRouter Free: 50/giorno, 20/minuto. Oltre → 429 `rate_limited` senza chiamare il provider.

### 26.5 Output strutturato e prompt

- Nessuna fiducia in `response_format`: il prompt (`app/ai/prompts.py`) descrive lo schema; il parser rimuove `<think>…</think>`, code fence e prosa, prende l'oggetto `{…}` e lo valida con Pydantic (`Analysis`: `summary`, 1–6 `insights` con `kind` ∈ observation/interpretation/hypothesis + `evidence`, ≤ 4 `caveats`, `data_sufficiency`). Non valido → fallback; mai salvato.
- Regole del prompt di sistema: solo i dati forniti, niente ricalcoli, ogni insight etichettato e con evidenza, campi mancanti = sconosciuti, nessuna previsione, nessuna diagnosi medica (in caso di valori anomali: suggerire un professionista).
- Il contesto contiene solo numeri già formattati (passo `m:ss/km`, km, %): **niente nome, note, tag, GPS/polyline, ID, ora del giorno**. Periodi lunghi: serie mensile invece che settimanale (max 26 bucket). Dimensione tipica 1,5–5 KB.
- `PROMPT_VERSION` fa parte della chiave di cache: incrementarlo invalida tutte le analisi.

### 26.6 Privacy e termini

Dati inviati a OpenRouter e al provider del modello, solo su click. **Rischi residui da valutare**: (1) i modelli `:free` di OpenRouter possono richiedere di consentire nelle impostazioni privacy dell'account il logging/training dei prompt da parte del provider; (2) l'API Agreement Strava contiene clausole sull'uso dei dati in applicazioni AI [NON VERIFICATO: rileggere il testo prima dell'uso]. Senza `OPENROUTER_API_KEY` la funzione è disattivata e nessun dato esce.

### 26.7 Estendere

- **Nuovo modello**: cambiare `AI_*_MODEL*` in `.env` (la catena entra nell'hash: le cache vecchie diventano `stale`). Un quarto modello = una riga in `Settings.ai_models`.
- **Nuova analisi**: (1) valore in `Kind` e nel CHECK di `ai_analyses` (migrazione); (2) builder di contesto deterministico in `app/api/ai.py` che restituisce `(ctx, motivo_insufficienza)`; (3) testo in `prompts.TASKS`; (4) coppia GET/POST che chiama `_state`/`_generate`; (5) `<AiPanel path=…>` nella pagina.


## Cose che non abbiamo ancora potuto verificare

| # | Elemento | Come verificarlo | Quando |
|---|----------|------------------|--------|
| U1 | Strava accetta un dominio `*.ts.net` come Authorization Callback Domain | Prova nella configurazione dell'app | M0-08 |
| U2 | Dati che Strava riceve oggi dalle corse dell'app Allenamento Apple (assenza della cadenza e presenza della potenza sono dichiarazioni dello staff Strava del 2023; la pagina di supporto attuale non elenca i campi) | Stream reali delle tue corse | M0-08 |
| U3 | Unità di `average_cadence` e dello stream `cadence` per la corsa (mezza cadenza?) | Confronto con un valore noto | M0-08 / M0-09 |
| U4 | L'orario nascosto (mezzanotte) si applica anche ai token del proprietario? | Impostazioni privacy + fixture | M0-08 |
| U5 | Esenzione PAYG dal recupero delle istanze inattive; se "reclaim" significa stop o terminate; come si misura la memoria | Doc Oracle / supporto | M0-02 |
| U6 | API Agreement Strava 2026: assenza di clausole su AI e sulla cancellazione per singola attività (letto con un fetch automatico, non integralmente) | Lettura manuale | Prima di M2 |
| U7 | Sintassi attuale di `tailscale serve` in background e pubblicazione del nome host nei log di Certificate Transparency | Doc Tailscale/CLI installata | M0-04 |
| U8 | Prezzo attuale di HealthFit, contenuto dei suoi FIT, destinazioni dell'export automatico | Spike | M0-09 |
| U9 | Health Auto Export: prezzo o abbonamento, affidabilità delle automazioni in background con Tailscale su iOS | Prova | Solo se M8 |
| U10 | Struttura esatta di `export.xml` di Apple (formato non documentato ufficialmente) | Export reale | Solo se M8-01 |
| U11 | Limiti del piano free di healthchecks.io | Sito | M0-06 |
| U12 | Compatibilità di restic con l'endpoint S3 di OCI Object Storage (configurazione di regione e namespace) | Prova | M0-06 |
| U13 | Versioni correnti di ECharts, MapLibre, React, Tailwind e wheel arm64 di tutte le dipendenze | Al momento dello scaffold | M0-01 / M4-01 |
| U14 | Cifratura di default dei volumi OCI Always Free | Doc Oracle | Informativo |
| U15 | Nuovo host `api-v3.strava.com`: pieno equivalente dei path attuali? | Changelog/doc Strava | M2-08 |

---

## Review interna del piano: correzioni apportate

1. **Rimosso `user_id`** (annunciato nel messaggio con le domande): complessità inutile con un solo utente; motivazione in ADR-15.
2. **Abbandonato l'export in bulk di Strava per l'import iniziale** (proposto nelle domande): con circa 250 corse bastano circa 500 chiamate, entro il budget di lettura di un giorno. L'export in bulk resta un'opzione di recupero dati.
3. **"Accesso solo da casa" → Tailscale**: la VPS non è nella LAN di casa; un'allowlist sull'IP dinamico è fragile ed esclude lo smartphone. Tailscale mantiene l'intento (privato) e migliora l'uso da mobile.
4. **Webhook rimossi dall'MVP**: incompatibili con l'accesso privato; il polling e la riconciliazione coprono creazioni, modifiche e cancellazioni.
5. **Limiti Oracle corretti** (2 OCPU / 12 GB, non 4/24) e **aggiunto il rischio di recupero per inattività** come rischio R1, con backup attivi prima dei dati reali (dipendenza esplicita M2-04 → M0-06).
6. **Cadenza non garantita**: i grafici di cadenza sono condizionali; import FIT e arricchimento spostati come prima milestone Should, con uno spike anticipato per non investire alla cieca.
7. **Analisi indipendenti dai tag**: EF e trend del passo avrebbero richiesto di taggare ogni corsa (le attività Apple arrivano senza `workout_type`). Introdotta la classificazione deterministica "steady".
8. **Eliminati componenti superflui**: reverse proxy Caddy/Nginx (sostituito da `tailscale serve`), Redis/Celery, numpy/pandas, gpxpy (GPX/TCX con parser XML sicuro), tabella `files` separata (fusa in `source_records`), copia grezza duplicata degli stream.
9. **Scadenza esterna trovata**: base URL Strava che cambia il 2027-01-04 → task P0 M2-08 e base URL configurabile.
10. **CSRF**: l'autenticazione di rete dà al browser "autorità ambientale"; aggiunto l'header custom obbligatorio sui metodi mutanti (mancava nella prima stesura).
11. **Rotazione del refresh token Strava**: ogni refresh invalida il precedente → persistenza atomica esplicitata (M2-02); altrimenti un crash tra refresh e salvataggio richiederebbe una nuova autorizzazione.
12. **Confronto dei periodi**: il Δ del periodo corrente parziale contro un periodo completo sarebbe stato fuorviante → confronto a parità di giorni trascorsi.
13. **Privacy delle fixture**: le tracce reali rivelano l'indirizzo di casa → coordinate traslate e repo privato.
14. **Controllo over-engineering**: tenuti solo `openapi-typescript` (costo minimo, evita bug di disallineamento) e la tabella `jobs` (serve per visibilità e ripresa). Il login applicativo è stato valutato e scartato (ADR-06).
15. **Coerenza delle dipendenze**: verificato che nessun task MVP dipenda da M7 (l'import file è opzionale per l'MVP) e che gli AC coprano tutti i Must.

---

## Fonti (consultate il 2026-09-30)

- Strava — Rate limits: https://developers.strava.com/docs/rate-limits/
- Strava — Authentication: https://developers.strava.com/docs/authentication/
- Strava — API reference: https://developers.strava.com/docs/reference/
- Strava — Webhooks: https://developers.strava.com/docs/webhooks/
- Strava — Changelog (voci 2024–2026, incluso il base URL dal 2027-01-04): https://developers.strava.com/docs/changelog/
- Strava — API Agreement (in vigore dal 2026-06-01): https://www.strava.com/legal/api
- Strava Support — Apple Health and Strava: https://support.strava.com/en-us/articles/15402024-apple-health-and-strava
- Strava Community Hub — FAQ integrazione Apple Watch (staff Strava, 2023–2024): https://communityhub.strava.com/insider-journal-9/commonly-asked-questions-about-apple-watch-integration-1545
- Strava Support — Export e bulk export: https://support.strava.com/en-us/articles/15401919-exporting-your-data-and-bulk-export
- Oracle — Always Free Resources: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- Tailscale — Serve: https://tailscale.com/kb/1312/serve · Pricing: https://tailscale.com/pricing
- OpenFreeMap: https://openfreemap.org/
- garmin-fit-sdk (v21.217.0, 2026-09-22): https://pypi.org/project/garmin-fit-sdk/
- HealthFit (fonte di terze parti): https://thesweetsetup.com/generate-fit-files-apple-watch-workouts/
- Health Auto Export — REST API: https://help.healthyapps.dev/en/health-auto-export/automations/rest-api/
- Struttura dell'export di Apple Salute (fonte di terze parti): https://www.healthexport.dev/blog/apple-health-export-xml-too-big
