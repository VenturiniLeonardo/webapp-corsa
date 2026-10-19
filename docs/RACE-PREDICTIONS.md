# Previsioni gara nella pagina Record

`GET /api/records/predictions` è in sola lettura e restituisce quattro stime
(5.000, 10.000, 21.097,5 e 42.195 metri), con passo, tempo, fascia indicativa,
qualità del riferimento e attività di origine. Non richiede migrazioni.

## Modello v1

- Riegel: `t2 = t1 * (d2/d1)^k`, con `k = 1.06` come riferimento.
- Prestazioni dell'ultimo anno: mediana ponderata delle proiezioni, peso con
  emivita di 56 giorni e preferenza per distanze simili. Se esiste una gara
  negli ultimi 180 giorni, si usano solo gare. In alternativa, una prova
  ricavata dagli allenamenti è un riferimento conservativo, segnalato come tale.
  Si considera al massimo un best effort per allenamento (il più lungo).
- Preparazione: volume medio sulle 8 settimane intere (anche quelle senza
  corse) e lungo massimo. Nelle estrapolazioni verso distanze maggiori,
  `k` aumenta fino a 0,06 per preparazione mezza incompleta, e fino a ulteriori
  0,12 per maratona. Sono coefficienti euristici, non una regressione validata.
- FC: confronto fra corse regolari delle ultime 56 date locali, oggi incluso.
  Efficienza = velocità GAP / FC, normalizzata per caldo. Si confrontano corse
  distanti almeno 7 giorni, a FC entro 5 bpm e durata entro un fattore 2,
  con copertura meteo e GAP omogenea. Servono 8 corse, 21 giorni di estensione
  e 12 coppie; la pendenza mediana del logaritmo dell'efficienza aggiorna
  il riferimento fino a oggi, con tetto ±8% su 56 giorni. Per gare anteriori
  alla finestra non si estrapola la tendenza oltre 56 giorni.
- Caldo: riusa `heat_slowdown_pct` (tabella empirica Hadley temperatura +
  punto di rugiada). Prima rimuove il caldo dalla prestazione storica, poi
  applica le condizioni attuali. Non è una misura individuale di acclimatazione;
  vento, sole e percorso della gara non sono modellati.
- Fasce: margini euristici crescenti con età del riferimento, assenza di FC o
  meteo, estrapolazione e preparazione incompleta; non intervalli di confidenza.
  La maratona riceve margini più ampi e una segnalazione specifica se volume
  inferiore a 40 km/settimana o lungo massimo inferiore a 28 km.

Esclusi duplicati, attività escluse o cancellate a monte, indoor, trail,
GPS sospetto e dati palesemente impossibili. Per produrre una stima servono
almeno tre corse nelle ultime otto settimane e un riferimento nell'ultimo anno.
Senza FC confrontabile si conserva la proiezione di base, segnalando il limite.

## Meteo e privacy

Rispetta `weather_enabled`. Open-Meteo riceve solo le coordinate di partenza
dell'ultima corsa GPS recente, arrotondate a 0,01°, con lo stesso sistema del
meteo storico. La UI specifica che si tratta di quella zona, non della posizione
attuale del dispositivo o di una futura gara. Cache in memoria di 15 minuti,
timeout di 4 secondi; errori memorizzati per un minuto. Meteo assente, scaduto
o disabilitato non viene sostituito con quello dell'ultimo allenamento: si
mostra esplicitamente una stima in condizioni fresche di riferimento.

In alternativa è possibile cercare una località con
`GET /api/records/predictions/locations?q=...` (Open-Meteo / GeoNames), scegliere
un risultato distinguendo regione e paese, e passare `latitude`, `longitude`
e `location_name` a `/api/records/predictions`. Le coordinate devono essere
presenti insieme e nei limiti WGS84; vengono arrotondate a 0,01° per il meteo.
La posizione personalizzata non richiede GPS nello storico e non modifica
allenamenti o impostazioni. La scelta è salvata solo nel browser, con pulsante
per ripristinare la zona dell'ultima corsa GPS. Il meteo disabilitato nelle
impostazioni resta disabilitato anche per una località personalizzata.

## Riferimenti

- Riegel, verifica in amatori: https://pmc.ncbi.nlm.nih.gov/articles/PMC5000509/
- FC e storico: https://pubmed.ncbi.nlm.nih.gov/30440994/
- Effetti meteo: https://pubmed.ncbi.nlm.nih.gov/34652333/
- Dati meteo: https://open-meteo.com/en/docs

Il modello combinato v1 è una stima esplicabile: richiede validazione prospettica
su nuove gare prima di rivendicare accuratezza individuale.
