# Stima VO₂max aggiornata dalle corse

La scheda fitness usa come numero principale la stima da passo e frequenza
cardiaca degli ultimi 28 giorni (oggi e le 27 date precedenti). Il VDOT del
miglior best effort negli ultimi 180 giorni resta separato, con la propria
data; le proiezioni Riegel restano derivate dal VDOT. Se manca una stima
cardiaca utilizzabile viene mostrato il VDOT, etichettato come indice da
prestazione, senza unità di consumo di ossigeno.

Per ogni corsa su strada con stream canonico si saltano i primi 5 minuti e
si esaminano blocchi consecutivi completi di 10 minuti. Si usano intervalli
abbinati distanza/tempo/FC di durata massima 10 secondi. Ogni blocco richiede
almeno l'80% di copertura valida e almeno 8 minuti coperti all'80%; il CV
del passo sui minuti deve essere inferiore a `steady_cv_threshold` (default
8%). Una corsa intera non regolare può quindi contribuire con i suoi tratti
regolari. Non si modifica la classificazione `is_steady` delle altre metriche.

Il calcolo esistente usa la domanda ACSM per corsa in piano e la relazione
approssimata fra riserva cardiaca e riserva di consumo di ossigeno:

```
f = (FC - FC_riposo) / (FC_max - FC_riposo)
stima = 3.5 + 0.2 * velocità_m_min / f
```

Sono richieste FC massima e a riposo valide. Si mantengono intervalli con
FC 40–220 bpm, velocità 1–7 m/s e `0.3 < f <= 1`. La media delle stime dei
blocchi dà una singola osservazione per corsa. Senza stream è ammesso il
precedente metodo sulle medie dell'attività solo per corse già classificate
regolari e di almeno 20 minuti; uno stream scartato non viene sostituito
con la media dell'intera corsa.

La media delle osservazioni usa pesi `2^(-età_giorni/14)`: ogni corsa
utilizzabile contribuisce, senza richiedere un record e senza dare più
osservazioni a un lungo. Le soglie e il tempo di dimezzamento sono scelte
del modello, da validare, non limiti fisiologici universali. Un identico
risultato o l'arrotondamento a un decimale possono lasciare invariato il
numero visualizzato. Non si attribuisce un incremento automatico ai km.

Sono esclusi GPS sospetto, indoor, trail, gare/allenamenti classificati
intensi, duplicati, attività escluse o cancellate alla fonte e date future.
I filtri di periodo della dashboard non cambiano questa finestra corrente.
L'API restituisce numero di corse e data/ID dell'ultima corsa inclusa.
La dashboard rilegge il dato al ritorno alla pagina, al focus e ogni minuto
mentre è visibile; il ricalcolo avviene sui dati già importati.

È una stima di modello, non una misura clinica né il valore Apple/Garmin.
Caldo, pendenze, deriva cardiaca, FC impostate e qualità del sensore possono
influenzarla; questo metodo non corregge il dislivello o il meteo.

Riferimento della relazione fra riserve: [Swain et al., 1998](https://pubmed.ncbi.nlm.nih.gov/9502363/).

## Riferimento manuale Apple Watch

In Impostazioni si può salvare una lettura Apple Watch con la sua data,
oppure rimuoverla. Il valore accettato è 14–65 ml/kg/min, intervallo
[dichiarato da Apple](https://support.apple.com/en-gb/108790). La data non
può essere futura; servono FC configurate e corse utilizzabili nella finestra
di 28 giorni che termina in quella data.

Al salvataggio si conserva uno snapshot della stima non calibrata `B` in
quella data, della configurazione FC/CV, della versione del modello e del
numero di corse. Il valore mostrato è `stima_corrente + valore_Apple - B`.
È un allineamento additivo su un punto scelto dall'utente, non una fusione
statisticamente validata né una garanzia di accuratezza. Il riferimento
Apple è anch'esso una stima. Nessun parametro fisiologico è modificato.

Le nuove corse mantengono quindi il loro effetto, senza congelare il numero
sul valore Apple. La dashboard mostra valore non calibrato, correzione,
lettura e data. VDOT e proiezioni gara non sono modificati. Il riferimento
non scade automaticamente: la sua data resta visibile e va aggiornata
dall'utente quando desidera un nuovo allineamento.

Lo snapshot non cambia per backfill o per un normale salvataggio delle
impostazioni con lo stesso riferimento. Se cambia FC massima, FC a riposo,
CV o versione del modello, la calibrazione viene sospesa fino a un nuovo
salvataggio del riferimento con la configurazione corrente. Senza corse
attuali non viene mostrata come stima corrente una vecchia lettura Apple.
La cancellazione ripristina il valore non calibrato. Il riferimento è un
JSON nelle impostazioni esistenti; non serve una migrazione del database.
