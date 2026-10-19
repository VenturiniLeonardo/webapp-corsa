# Previsioni gara v2 nella pagina Record

Modello attivo: `riegel-hr-weather-v2`, parametri iniziali euristici, non calibrati.
La specifica matematica completa è in [RACE-PREDICTIONS-V2.md](RACE-PREDICTIONS-V2.md).
La v1 rimane in `app/metrics/predictions.py` per confronti e regressioni.

## Comportamento implementato

La previsione principale descrive la forma attuale in condizioni standard su
percorso pianeggiante. Data o mese, ora locale e località attivano uno scenario
aggiuntivo: forecast entro 10 giorni quando disponibile, altrimenti climatologia
ERA5 del periodo sui 10 anni completi precedenti (almeno 5 anni utilizzabili).
Ogni distanza usa il meteo all'ora di metà della durata standard prevista.
Il meteo del momento non viene usato come previsione di una gara futura.

Il modello combina gare e test qualificati, con normalizzazione del caldo e
della pendenza, Riegel continuo, trend FC attenuato, detraining documentato,
mediana ponderata e coerenza fra distanze. Il lato lento delle fasce è più ampio.
Non attribuisce probabilità alle fasce e non presume miglioramenti futuri.

Il pannello «Dati e conferme per le previsioni» salva separatamente dalle attività
sincronizzate: sensore, FC soglia/massima misurate, periodi di importazione
completa, scarico dichiarato, distanza/tempo ufficiali, percorso piatto, test/RPE
e preparazione precedente verificata. Senza queste conferme non deduce dati
ufficiali o periodi di riposo. D+ da solo non viene usato come GAP.

`weather_enabled` continua a controllare tutte le richieste di meteo gara.
Le coordinate vengono arrotondate a 0,01 gradi. Ricerca esplicita Open-Meteo /
GeoNames. Location e scenario sono ricordati nel browser. Cache di 15 minuti
per forecast, 24 ore per archivio, un minuto per errori. Timeout HTTP 4 secondi.

## API e audit

- `GET /api/records/predictions`: standard, riferimenti, correzioni, affidabilità.
- Query facoltative: `latitude`, `longitude`, `location_name`, `race_date` oppure
  `race_month`, `race_time`, `race_timezone` (IANA). Coordinate sempre in coppia.
- `include_snapshot=true`: esporta input, parametri, campioni meteo, cutoff e hash
  SHA-256. La UI consente di scaricarli. Snapshot generati su richiesta; non viene
  conservato automaticamente un archivio di previsioni sul server.
- `GET /api/records/predictions/locations?q=...`: ricerca città.
- `GET /api/records/prediction-inputs`: profilo, conferme e attività disponibili.
- `PUT /api/records/prediction-inputs/profile` e
  `PUT /api/records/prediction-inputs/activities/{id}`: conferme esplicite.

I metadati sono JSON nella tabella settings: nessuna migrazione DB richiesta.
Attivazione v2 diretta su richiesta dell'utente; non è stato eseguito uno studio
prospettico né sono stati calibrati i coefficienti sullo storico personale.

## Verifica e backtest

I test coprono i cinque esempi numerici della specifica, mediana, continuità,
coerenza, sensori/meteo, copertura, selezione test, persistenza e API. I test browser
coprono quattro card, mobile, gestione errori, location e conferme.

`python -m scripts.backtest_predictions_v2 dataset.json` ricalcola snapshot
esportati senza rete. Ogni record contiene `athlete_id`, `event_date`,
`distance_m`, `actual_elapsed_s`, `standard_eligible`, `product` (`standard` o
`scenario`) e `snapshot` (l'oggetto input_snapshot esportato). La verità standard
richiede verifica indipendente di percorso piano e condizioni temperate.
Lo scenario deve usare il forecast/clima catturato prima della gara.
Il comando rifiuta snapshot alla data della gara o successivi e attività future;
separa gli atleti deterministicamente in development/holdout 80/20, riporta MAPE,
bias, copertura empirica, ampiezza e astensioni per distanza e prodotto.
Non esegue fitting né certifica intervalli calibrati. Per una validazione reale
servono snapshot storici catturati prima degli eventi e un dataset adeguato,
seguendo il protocollo della specifica. I dati DB correnti non ricostruiscono
la disponibilità storica di importazioni, modifiche o vecchi forecast.
