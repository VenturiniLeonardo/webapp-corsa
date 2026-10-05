# Piano Tecnico: PMC, Shoe Tracker, Calendario e Route Planner

> **Progetto:** Corsa — Piattaforma personale di analisi della corsa  
> **Data:** Ottobre 2026  
> **Riferimento architetturale:** [`docs/PLAN.md`](PLAN.md) · [`CLAUDE.md`](../CLAUDE.md)  
> **Stato:** Piano tecnico aggiornato con la tab Pianificazione Percorsi  

---

## 1. Visione d'insieme e Moduli

Il piano copre 4 funzionalità organiche progettate per espandere le capacità analitiche e operative della piattaforma:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           PIATTAFORMA ANALISI CORSA                             │
├────────────────────┬────────────────────┬────────────────────┬──────────────────┤
│ 1. Storico PMC     │ 2. Shoe Tracker    │ 3. Diario Cal.     │ 4. Route Planner │
│ - TRIMP giornaliero│ - Tabella `shoes`  │ - Grid 7+1 colonne │ - Nuova Tab Nav  │
│ - Banister EWMA    │ - Usura vs target  │ - Lun-Dom + Totale │ - Mappa interat. │
│ - CTL/ATL/TSB      │ - Default & switch │ - Badge workout    │ - Curve, D+, GAP │
│ - MarkLine gare    │ - Statistiche paio │ - Switcher vista   │ - Export GPX     │
└────────────────────┴────────────────────┴────────────────────┴──────────────────┘
```

1. **Storico PMC (Performance Management Chart — CTL / ATL / TSB)**: Estensione temporale del modello Banister per visualizzare le curve continue di Fitness (42 gg), Fatica (7 gg) e Forma/Freschezza giorno per giorno, con tracciamento visivo delle finestre di tapering per le gare.
2. **Tracciamento Scarpe & Materiali (Shoe Tracker)**: Gestione del ciclo di vita delle calzature, monitoraggio dei chilometri accumulati rispetto al target di usura (600–800 km) e associazione automatica o manuale alle attività.
3. **Diario a Calendario (Calendar Grid View)**: Vista mensile strutturata a griglia settimanale (Lunedì → Domenica + colonna riepilogo settimana ISO) per sfogliare gli allenamenti come un vero diario di corsa.
4. **Pianificazione Percorsi & Gare (Route Planner & Analyzer)**: Nuova tab nella barra di navigazione con mappa interattiva MapLibre per disegnare nuovi percorsi (snap stradale/pedonale o mano libera), importare tracce GPX/TCX/FIT, calcolare altimetria continua, segmentazione pendenze, analisi delle curve/sinuosità, simulazione del tempo finale basata sul Minetti GAP ed esportazione GPX per smartwatch.

---

## 2. Invarianti Architetturali da Preservare

Tutti gli sviluppi si conformano alle regole del repository:
- **Unità SI interne**: Metri, secondi, m/s, bpm, gradi, coordinate decimali WGS84. Mai memorizzare il passo su database: il passo (min/km o s/km) è sempre derivato.
- **Single-User & No Auth esterna**: Nessuna tabella `users` o `user_id` (ADR-15).
- **SQLite WAL**: Transazioni atomiche, nessun container DB esterno.
- **Stack Mappe & Grafici**: MapLibre GL JS + OpenFreeMap Dark (zero API keys, 0 €/mese), Apache ECharts per i profili altimetrici e le curve.
- **Design & UI dark-only**: Tipografia (`Barlow Condensed`, `IBM Plex Mono`, `Barlow`), colori semantici coerenti, numeri tabulari (`font-mono tabular-nums`).

---

## 3. Specifiche Tecniche per Funzionalità

---

### Funzionalità 1: Storico PMC (Performance Management Chart)

#### 1.1 Modello Matematico e Algoritmo
Il backend già calcola il carico giornaliero TRIMP in [`app/api/stats.py`](../app/api/stats.py#L742):
- **Carico giornaliero (Edwards TRIMP)**:
  $$\text{Load} = \sum_{k=1}^5 k \cdot \frac{\text{minuti in } Z_k}{60} \quad (\text{se non c'è FC: } 2 \cdot \frac{\text{minuti in movimento}}{60})$$
- **Aggiornamento continuo Banister (EWMA giornaliera)**:
  Per ogni giorno $t$ dal primo giorno registrato a oggi:
  $$CTL_t = CTL_{t-1} + (Load_t - CTL_{t-1}) \cdot (1 - e^{-1/42})$$
  $$ATL_t = ATL_{t-1} + (Load_t - ATL_{t-1}) \cdot (1 - e^{-1/7})$$
  $$TSB_t = CTL_t - ATL_t$$

#### 1.2 Backend API ([`app/api/stats.py`](../app/api/stats.py))
- Schema Pydantic:
  ```python
  class LoadPoint(BaseModel):
      date: date
      load: float
      ctl: float
      atl: float
      tsb: float


  class LoadHistory(BaseModel):
      points: list[LoadPoint]
  ```
- Endpoint:
  ```http
  GET /api/stats/load-history?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
  ```
  - Esegue la sequenza Banister dall'origine per azzerare il transitorio di warm-up.
  - Filtra la serie restituita nell'intervallo richiesto dal selettore di periodo della dashboard.

#### 1.3 Frontend UI ([`web/src/pages/DashboardPage.tsx`](../web/src/pages/DashboardPage.tsx))
- Componente `PmcChart`:
  - ECharts integrato con il tema Dark esistente.
  - **Asse Y1 (sinistra)**: Carico CTL (linea solida `#4c8dff`) e ATL (linea tratteggiata `#ef4444`).
  - **Asse Y2 (destra)**: TSB (Freschezza) con visualizzazione ad area sfumata:
    - Verde tenue (`#3fbf6a22`) quando $TSB > 0$ (freschezza favorevole).
    - Arancio/rosso quando $TSB < -25$ (sovraccarico).
  - **MarkLine verticali**: Inserimento delle date di gara configurate in `settings.races` con badge descrittivo.
  - Sincronizzazione con il selettore di periodo (`Week`, `4W`, `12W`, `6M`, `YTD`, `1Y`, `All`, `Custom`).

---

### Funzionalità 2: Tracciamento Scarpe & Materiali (Shoe Tracker)

#### 2.1 Modello Dati e Migrazione Database
In [`app/domain/models.py`](../app/domain/models.py):

```python
class Shoe(Base):
    __tablename__ = "shoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)  # es. "Saucony Triumph 21"
    brand: Mapped[str | None] = mapped_column(Text)  # es. "Saucony"
    model: Mapped[str | None] = mapped_column(Text)
    target_distance_m: Mapped[float] = mapped_column(REAL, default=700000.0)  # 700 km
    initial_distance_m: Mapped[float] = mapped_column(REAL, default=0.0)  # km pregressi
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    retired_at: Mapped[str | None] = mapped_column(Text)  # data ISO ritiro o NULL
    created_at: Mapped[str] = mapped_column(Text, default=utcnow_iso)
```

In `Activity`:
```python
shoe_id: Mapped[int | None] = mapped_column(
    ForeignKey("shoes.id", ondelete="SET NULL", name="fk_activities_shoe"), nullable=True
)
```

- Migrazione Alembic: `alembic revision --autogenerate -m "add_shoes_table"`

#### 2.2 Pipeline di Ingestion
In [`app/ingest/files.py`](../app/ingest/files.py) e [`app/ingest/intervals.py`](../app/ingest/intervals.py):
- Quando una nuova attività viene creata senza una scarpa indicata:
  - Query sulla scarpa attiva (`retired_at IS NULL`) con `is_default == True`.
  - Assegnazione automatica del `shoe_id`.

#### 2.3 Endpoints API
Nuovo modulo `app/api/shoes.py` registrato in [`app/main.py`](../app/main.py):
- `GET /api/shoes`: Elenco di tutte le scarpe con aggregazione calcolata su `activities`:
  - `total_distance_m` $= \text{initial\_distance\_m} + \sum \text{activities.distance\_m}$
  - `run_count`: Numero di uscite collegate.
  - `weighted_pace_s_per_km`: Passo medio ponderato con quel modello.
  - `percent_worn`: $\frac{\text{total\_distance\_m}}{\text{target\_distance\_m}} \times 100$.
- `POST /api/shoes`: Creazione nuova scarpa.
- `PATCH /api/shoes/{id}`: Modifica metadati, cambio default o pensionamento (`retired_at`).
- `DELETE /api/shoes/{id}`: Eliminazione permessa solo se nessuna attività è collegata (altrimenti 409 con suggerimento di pensionamento).
- Aggiornamento di [`app/api/activities.py`](../app/api/activities.py):
  - Aggiunta di `shoe_id: int | None = None` nello schema `ActivityPatch`.
  - Inclusione di `shoe: ShoeSummary | None` nella risposta di dettaglio dell'attività.

#### 2.4 Frontend UI
1. **Sezione Scarpe in Impostazioni** ([`SettingsPage.tsx`](../web/src/pages/SettingsPage.tsx)):
   - Card per ogni scarpa con indicatore di usura a colori:
     - Verde (< 70%).
     - Arancione (70% - 90%).
     - Rosso (> 90%, con avviso *"Consigliata sostituzione"*).
   - Statistiche: km totali, uscite, passo medio registrato.
   - Azioni: Imposta come predefinita, Modifica target, Ritira scarpa.
   - Form per aggiungere un nuovo paio (con supporto ai km già percorsi prima di usare l'app).
2. **Selettore nel Dettaglio Allenamento** ([`ActivityDetailPage.tsx`](../web/src/pages/ActivityDetailPage.tsx)):
   - Dropdown rapido nell'header dell'attività accanto a `Workout type` e `Difficoltà`.
3. **Filtro nell'elenco corse** ([`ActivitiesPage.tsx`](../web/src/pages/ActivitiesPage.tsx)):
   - Aggiunta del filtro `shoe_id` nei parametri di query per filtrare tutte le sessioni corse con una specifica scarpa.

---

### Funzionalità 3: Diario a Calendario (Calendar Grid View)

#### 3.1 Backend API ([`app/api/stats.py`](../app/api/stats.py))
- Endpoint dedicato:
  ```http
  GET /api/stats/calendar-month?year=YYYY&month=MM
  ```
- **Struttura risposta**:
  ```python
  class CalendarActivityItem(BaseModel):
      id: int
      name: str | None
      sport_type: str
      workout_type: str | None
      distance_m: float
      moving_s: int
      avg_hr: float | None
      has_pr: bool


  class CalendarWeekSummary(BaseModel):
      iso_week: int
      total_distance_m: float
      total_moving_s: int
      run_count: int
      elev_gain_m: float


  class CalendarMonthOut(BaseModel):
      year: int
      month: int
      days: dict[str, list[CalendarActivityItem]]  # Chiave: "YYYY-MM-DD"
      weeks: dict[int, CalendarWeekSummary]  # Chiave: numero settimana ISO
  ```

#### 3.2 Frontend UI
In [`web/src/pages/ActivitiesPage.tsx`](../web/src/pages/ActivitiesPage.tsx):
- Switcher visivo nell'header: **[Tabella | Calendario]** (persistito come parametro URL `view=calendar` o `view=table`).
- Componente `ActivityCalendarView`:
  - **Barra di navigazione mese**: Pulsanti `←` e `→`, etichetta del mese in italiano (es. *"Ottobre 2026"*), totale km del mese e media km/settimana.
  - **Griglia a 8 colonne**:
    - Colonne 1–7: **Lun, Mar, Mer, Gio, Ven, Sab, Dom**.
    - Colonna 8: **Totale Settimana**.
  - **Contenuto cella giornaliera**:
    - Numero del giorno discreto in alto a sinistra.
    - Se presente una o più corse:
      - Bordo colorato semantico in base al tipo di seduta:
        - Blu (`#4c8dff`): Corsa facile / Fondo lento (`easy`).
        - Oro / Ambra (`#f59e0b`): Corsa lunga (`long`).
        - Viola (`#a855f7`): Lavoro di qualità / Ripetute (`workout`).
        - Rosso (`#ef4444`): Gara (`race`).
      - Distanza formattata in grassetto mono (es. **`10.5 km`**).
      - Passo medio e durata (es. `4:58/km · 52m`).
      - Badge compatto `PR` dorato se la corsa contiene un best effort da record.
      - Clic sulla cella/card: navigazione diretta a [`/activities/:id`](../web/src/pages/ActivityDetailPage.tsx).
  - **Cella "Totale Settimana"**:
    - Sfondo scuro differenziato (`#191d23`).
    - Totale km settimanali in evidenza (es. **`48.2 km`**).
    - Tempo totale cumulato e dislivello positivo D+.
    - Delta percentuale rispetto alla settimana precedente.
  - **Design Mobile (< 768px)**:
    - Adattamento fluido: layout a vista settimanale scorrevole con swipe orizzontale o elenco compatto a gruppi settimanali per garantire la perfetta leggibilità anche a 375 px (RNF-09).

---

### Funzionalità 4: Nuova Tab Pianificazione Percorsi (Route Planner & GPX Studio)

#### 4.1 Visione e Flusso Utente
Viene aggiunta una nuova voce nella barra di navigazione [`web/src/components/Shell.tsx`](../web/src/components/Shell.tsx):
`Percorsi` (icona `Route` / `Compass`), collegata alla rotta `/routes`.

L'interfaccia si compone di due modalità integrate:
1. **Editor & Disegno**: Mappa interattiva a pieno schermo con pannello laterale a scomparsa per posizionare waypoint. Due modalità di tracciamento:
   - **Snap su strade/sentieri**: Utilizzo dell'API pubblica OSRM Foot/Running gratuita per calcolare la rotta lungo strade e percorsi pedonali tra i punti cliccati.
   - **Linea d'aria / Manuale**: Disegno libero punto per punto per tratti fuori sentiero o piste di atletica.
   - Azioni rapide: Annulla ultimo punto, Inverti traccia (ritorno), Chiudi anello (loop al punto di partenza).
2. **Importazione Traccia**:
   - Trascina o seleziona un file `.gpx`, `.tcx` o `.fit` per analizzarlo all'istante.
   - Pulsante *"Copia da corsa esistente"*: seleziona una sessione passata per trasformarla in traccia pianificata.

```
┌────────────────────────────────────────────────────────────────────────┐
│  MAPPA INTERATTIVA MAPLIBRE (Dark OpenFreeMap)                         │
│  [ ● Partenza ] ───► [ Waypoint 1 ] ───► [ Waypoint 2 ] ───► [ Arrivo ]│
├────────────────────────────────────────────────────────────────────────┤
│  PROFILO ALTIMETRICO ECHARTS (Sincronizzato al passaggio del cursore)  │
│  Quota (m)  /\    /\_                                                  │
│            /  \__/   \______ D+ 145m · D- 140m · Quota max 182m        │
├────────────────────────────────────────────────────────────────────────┤
│  CARD METRICHE E ANALISI TRACCIATO                                     │
│  • Distanza: 12.42 km       • Pendenza media: 2.8% (max 8.5%)          │
│  • Curve: 24 (4 a gomito)   • Sinuosità: 1.34 (Percorso scorrevole)    │
│  • Passo GAP simulato:      5:12/km (equivalente a 5:00/km in piano)   │
│  • Tempo stimato:           1h 04m 32s                                 │
│  [ Salva Percorso ]  [ Esporta file GPX per Apple Watch / Garmin ]     │
└────────────────────────────────────────────────────────────────────────┘
```

#### 4.2 Analisi Automatica del Tracciato (Backend & Algoritmi)
Modulo di calcolo geometrico e morfologico in [`app/metrics/routes.py`](../app/metrics/routes.py):

1. **Distanza e Geometria**:
   - Calcolo cumulativo Haversine per ogni coppia di coordinate adiacenti.
2. **Altimetria e Pendenze**:
   - *Se da file GPX*: Quota estratta dai tag `<ele>`.
   - *Se disegnato da zero*: Arricchimento quote tramite **Open-Meteo Elevation API** (gratuita, pubblica, senza token):
     `https://api.open-meteo.com/v1/elevation?latitude=45.46,45.47&longitude=9.19,9.20`
   - Calcolo pendenze con filtro a finestra mobile centrata di 50m (`GRADE_WINDOW_M` già validato nel motore metriche di Corsa).
   - Ripartizione del tracciato in fasce:
     - Pianeggiante ($-2\% \le \text{pendenza} \le +2\%$)
     - Salita dolce ($+2\% < \text{pendenza} \le +5\%$)
     - Salita ripida ($\text{pendenza} > +5\%$)
     - Discesa ($\text{pendenza} < -2\%$)
3. **Analisi delle Curve e Sinuosità**:
   - Per ogni terzetto di punti $(P_{i-1}, P_i, P_{i+1})$, calcolo dell'angolo di deviazione:
     $$\theta = \arccos\left(\frac{\vec{u} \cdot \vec{v}}{\|\vec{u}\| \|\vec{v}\|}\right)$$
   - Classificazione:
     - Curve a gomito: deviazione $\theta \ge 75^\circ$
     - Tornanti: deviazione $\theta \ge 120^\circ$
   - **Indice di Sinuosità**: $\frac{\text{Lunghezza reale tracciato}}{\text{Distanza geodetica tra inizio e fine}}$. (Se il percorso è ad anello, calcolato come rapporto tra lunghezza e perimetro del convex hull).
4. **Simulatore Tempo & Passo Equivalente (Minetti GAP)**:
   - Applicando il fattore energetico Minetti già presente nel motore metriche [`minetti_factor`](../app/metrics/engine.py#L270):
     $$\text{Distanza equivalente GAP} = \sum \Delta d_i \cdot \text{Minetti}(g_i)$$
   - Dato un passo target in piano scelto dall'utente (oppure ricavato automaticamente dalla soglia/VDOT recente calcolata in [`app/api/stats.py`](../app/api/stats.py#L603)), l'app calcola il tempo totale stimato e il passo medio risultante su quel tracciato.

#### 4.3 Modello Dati Database ([`app/domain/models.py`](../app/domain/models.py))
```python
class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)  # es. "Lungo Colline 21K"
    sport_type: Mapped[str] = mapped_column(Text, default="run")
    distance_m: Mapped[float] = mapped_column(REAL)
    elev_gain_m: Mapped[float] = mapped_column(REAL)
    elev_loss_m: Mapped[float] = mapped_column(REAL)
    points_geojson: Mapped[str] = mapped_column(Text)  # LineString GeoJSON
    elevation_profile: Mapped[Any] = mapped_column(JSON)  # [{d_m, ele_m, grade}]
    turns_count: Mapped[int] = mapped_column(Integer, default=0)
    turns_sharp: Mapped[int] = mapped_column(Integer, default=0)
    sinuosity: Mapped[float | None] = mapped_column(REAL)
    target_flat_pace_s_per_km: Mapped[float | None] = mapped_column(REAL)
    est_duration_s: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(Text, default=utcnow_iso, onupdate=utcnow_iso)
```

#### 4.4 Endpoints API (`app/api/routes.py`)
- `GET /api/routes`: Elenco percorsi salvati con metadati e statistiche sintetiche.
- `GET /api/routes/{id}`: Dettaglio completo tracciato, coordinate, profilo altimetrico e analisi curve.
- `POST /api/routes`: Salvataggio nuovo percorso.
- `POST /api/routes/analyze`: Calcolo istantaneo da GeoJSON/coordinate senza salvataggio obbligatorio (per preview istantanea durante il disegno).
- `POST /api/routes/import-file`: Upload multipart GPX/TCX/FIT con parsing e restituzione analisi geometrica.
- `GET /api/routes/{id}/export-gpx`: Download del file `.gpx` con traccia standard WGS84 e altimetrie, pronto da importare su Apple Watch (app *WorkOutDoors*, app nativa *Allenamento* watchOS 11 o orologi Garmin/Coros).
- `PATCH /api/routes/{id}` & `DELETE /api/routes/{id}`.

#### 4.5 Frontend UI
1. **Navigazione** in [`web/src/components/Shell.tsx`](../web/src/components/Shell.tsx):
   - Aggiunta link `Pianificazione` (icona `Compass` da `lucide-react`) tra *Allenamenti* e *Record*.
2. **Nuova Pagina** `web/src/pages/RoutePlannerPage.tsx`:
   - Mappa interattiva MapLibre con stile Dark OpenFreeMap (già configurato in `ActivityDetailPage`).
   - Modalità inserimento waypoint con click su mappa: snap a strada via OSRM pedonale o click diretto.
   - Sotto la mappa: Grafico altimetrico ECharts con marker interattivo sincronizzato sulla mappa (spostando il mouse sul profilo altimetrico, un cerchietto si muove lungo la traccia sulla mappa).
   - Sidebar/Pannello analisi:
     - Distanza totale, D+, D-, quota massima e minima.
     - Griglia pendenze: % tempo/distanza in salita, discesa e falsopiano.
     - Indicatore di curve: conteggio totale e alert curve secche.
     - Box simulazione passo: cursore o input numerico del passo in piano desiderato $\rightarrow$ calcolo immediato tempo stimato totale e passo medio atteso con dislivello.
     - Pulsanti *"Salva"* ed *"Esporta GPX"*.
3. **Elenco Percorsi Salvati**:
   - Card dei percorsi memorizzati con anteprima mappa, statistiche e opzione "Modifica" o "Esporta".

---

## 5. Piano di Rilascio e Sequenza di Lavoro

Lo sviluppo è strutturato in 4 fasi incrementali per mantenere la test-suite sempre verde:

```
FASE 1: PMC (Fitness/Fatigue)
  ├── 1.1 Backend: Calcolo serie Banister e nuovo endpoint /api/stats/load-history
  ├── 1.2 Test backend: Unit test su curva EWMA e test di integrazione API
  ├── 1.3 Frontend: Componente PmcChart (ECharts) in DashboardPage con markLine gare
  └── 1.4 Verifica build, lint e test Vitest

FASE 2: Shoe Tracker
  ├── 2.1 Backend: Modello Shoe, FK in Activity, migrazione Alembic
  ├── 2.2 Backend: Logica di assegnazione default nell'ingest e modulo app/api/shoes.py
  ├── 2.3 Test backend: Test CRUD scarpe e vincoli di integrità referenziale
  ├── 2.4 Frontend: Gestione scarpe in SettingsPage e selettore in ActivityDetailPage
  └── 2.5 Frontend: Filtro per scarpa in ActivitiesPage

FASE 3: Diario a Calendario
  ├── 3.1 Backend: Endpoint /api/stats/calendar-month con aggregati giornalieri e settimanali
  ├── 3.2 Test backend: Test di correttezza confini mese/settimana ISO
  ├── 3.3 Frontend: Componente ActivityCalendarView con griglia 7+1 colonne
  ├── 3.4 Frontend: Toggle Tabella/Calendario in ActivitiesPage
  └── 3.5 Test E2E / Verifica responsiveness mobile a 375px

FASE 4: Pianificazione Percorsi (Route Planner & Analyzer)
  ├── 4.1 Backend: Modello Route, migrazione Alembic, modulo app/metrics/routes.py
  │     (distanza, quota Open-Meteo, curve, pendenze, GAP Minetti)
  ├── 4.2 Backend: Modulo app/api/routes.py (CRUD, import GPX/FIT, export GPX)
  ├── 4.3 Test backend: Test calcolo curve, dislivello e generazione XML GPX valido
  ├── 4.4 Frontend: Nuova rotta /routes in App.tsx e Shell.tsx
  ├── 4.5 Frontend: RoutePlannerPage con MapLibre editor, OSRM routing e grafico ECharts D+
  └── 4.6 Frontend: Pannello metriche percorso, simulatore passo gara ed export GPX
```

---

## 6. Criteri di Accettazione e Verifica (Definition of Done)

- [ ] **Test automatici**: Tutti i 199 test attuali più i nuovi test su tutte e 4 le aree passano (`pytest -q` e `npm test`).
- [ ] **Typecheck & Linter**: Zero errori da `mypy app`, `tsc -b --noEmit` e `oxlint`.
- [ ] **Performance**: Tutti i nuovi endpoint rispondono sotto i 150 ms su SQLite WAL.
- [ ] **Validità GPX**: Il file GPX generato dal Route Planner è valido XML conforme a GPX 1.1 e importabile con successo su Apple Watch o dispositivi di terze parti.
- [ ] **Zero Costi & Privacy**: Nessun token a pagamento introdotto (OpenFreeMap + Open-Meteo elevation + OSRM sono tutti aperti e gratuiti).
