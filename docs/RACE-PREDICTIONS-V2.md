# Previsione del passo gara — specifica v2 proposta

**Stato:** proposta tecnica del 19 ottobre 2026. Non modifica il modello v1 attualmente pubblicato. I calcoli degli esempi sono riproducibili con `verify_examples.py`; i risultati v1 sono stati confrontati con l'implementazione esistente.

Il riferimento originale è il metodo descritto nel messaggio precedente e implementato in `app/metrics/predictions.py`. Il segnaposto del testo allegato è risolto con quel metodo.

**Distinzione delle evidenze.** La proiezione distanza–tempo, l'effetto delle condizioni ambientali, l'influenza della pendenza sul costo energetico e la diversa accuratezza dei sensori hanno riscontri in letteratura. Le equazioni che li combinano qui, i coefficienti individuali, le regole di selezione e le fasce iniziali sono scelte progettuali da validare. Nessuna costante di seguito è una soglia fisiologica universale.

I valori indicati come **[PARAMETRO_DA_CALIBRARE]** hanno un valore iniziale e un intervallo di ricerca plausibile. Questi intervalli sono domini di ricerca progettuali, non intervalli di confidenza né valori raccomandati dalla letteratura. Le distanze ufficiali, le conversioni di unità, i 56 giorni richiesti dal prodotto e le tolleranze numeriche di macchina sono costanti di definizione.

## A. Specifica v2 completa

### 1. Dati e periodo di riferimento

**Obiettivo:** prestazione potenziale attuale su strada pianeggiante, per 5.000, 10.000, 21.097,5 e 42.195 m. Non viene prevista una crescita di forma fra oggi e una futura gara: una data gara serve al solo scenario meteorologico. L'eventuale effetto di un futuro blocco di allenamento richiederebbe un modello ulteriore.

**Unità interne:** metri, secondi, m/s, bpm, °C, giorni di calendario locale. Nelle formule di preparazione, `V` è in km/settimana e `L` in km. Logaritmi naturali. Ogni elaborazione usa un timestamp di cutoff `c`, una versione dei parametri e snapshot degli input; a parità di questi tre elementi il risultato deve essere identico.

Usare solo informazioni disponibili entro `c`; escludere attività future, duplicati, attività escluse dalle statistiche o cancellate alla fonte, indoor, trail e dati invalidi/non finiti. Il GPS sospetto esclude tratti e metriche dipendenti da quel GPS; una gara con distanza e cronometraggio ufficiali verificati può restare come riferimento indipendente, seguendo la sezione 2.1. Per le gare usare il tempo trascorso, soste incluse; per l'efficienza usare i segmenti effettivamente corsi.

La finestra attuale comprende le 56 date locali da `oggi−55` a oggi, estremi inclusi. Calcolare:

- `V = somma delle distanze valide / 8`, senza eliminare le settimane a zero;
- `L = massima distanza di una corsa valida nella finestra`;
- completezza delle importazioni, corse con FC e corse regolari utilizzabili.

**Zero attività e dato mancante sono stati diversi.** Una fonte dichiarata completa rende i giorni senza corse veri zeri. Se non è nota la copertura delle importazioni, `V` e `L` restano statistiche dei dati osservati: non possono essere usati per affermare un calo certo di allenamento. Le quantità fornite esplicitamente dall'atleta come complete sono ammesse come aggregati verificati.

Per ciascun riferimento con data `r`, calcolare `V_ref`, `L_ref` sulle 56 date precedenti, da `r−56` a `r−1`. Mai includere il riferimento stesso o informazioni successive per ricostruire la sua preparazione. Memorizzare la copertura di entrambe le finestre.

| Parametro | Valore iniziale | Intervallo di ricerca |
|---|---:|---:|
| [PARAMETRO_DA_CALIBRARE] Età massima del riferimento | 365 giorni | 180–730 |
| [PARAMETRO_DA_CALIBRARE] Gara considerata recente per la priorità delle fonti | 180 giorni | 90–270 |
| [PARAMETRO_DA_CALIBRARE] Numero minimo di corse attuali per produrre un punto centrale | 3 | 2–6 |
| [PARAMETRO_DA_CALIBRARE] Copertura minima di ciascuna finestra per usare confronti di preparazione | 80% dei giorni | 70–100% |

Sotto il minimo di corse o senza riferimenti eleggibili mostrare «Dati insufficienti», conservando eventuali prestazioni osservate. Non inventare un punto centrale.

### 2. Prestazioni di riferimento

#### 2.1 Gare e distanza ufficiale

Per una gara con distanza ufficiale e tempo ufficiale/trascorso verificati, usare quella distanza. Applicare la distanza minima dei riferimenti riportata nella tabella anche alle gare. Una mezza resta 21.097,5 m anche se il GPS indica 21,4 km. Non cambiare il tempo per compensare la differenza GPS.

La distanza ufficiale deve provenire da metadati dell'organizzatore, associazione verificata all'evento o conferma esplicita dell'utente. Non arrotondare automaticamente una normale corsa di 9,85 km a una gara di 10 km.

Definire `delta_d = abs(d_GPS/d_ufficiale − 1)`. Se supera la tolleranza GPS, mantenere la gara ufficiale verificata ma escludere le metriche derivate da quel GPS finché non sono controllate, incluso il GAP. Se mancano anche conferma dell'evento o tempo attendibile, il riferimento non è eleggibile.

#### 2.2 Normalizzazione della pendenza

Prima di confrontare le prestazioni, ricondurle al piano. Con un profilo attendibile usare lo stesso modello GAP versionato del progetto. In forma esplicita, per segmenti `j` di lunghezza `ell_j` e pendenza `g_j = dislivello/distanza orizzontale`:

```
C(g) = 155,4 g^5 − 30,4 g^4 − 43,3 g^3 + 46,3 g^2 + 19,5 g + 3,6
G = sum(ell_j × C(g_j)/C(0)) / sum(ell_j)
t_piano = t_corso / G + t_soste
```

`t_corso + t_soste = t_trascorso`. Se non sono separabili, usare `t_piano = t_trascorso/G` e registrare la limitazione. Nessuna sosta viene eliminata dal tempo ufficiale.

Se esiste una velocità GAP affidabile riferita allo stesso tratto, è equivalente usare `G = v_GAP / v_grezza`, senza correggere una seconda volta. Non mescolare GAP del tratto e velocità media dell'intera attività.

Normalizzare le lunghezze del profilo alla distanza ufficiale se il GPS è entro la tolleranza; il fattore globale si cancella nel rapporto che definisce `G`. Richiedere copertura sufficiente del profilo e pendenze all'interno del dominio del modello Minetti, ±45%; segmenti fuori dominio sono invalidi, non semplicemente troncati per rendere valida una gara.

Per l'uso su strada, un `G` fuori dall'intervallo operativo genera una richiesta di verifica del profilo e rende indisponibile la correzione. Non saturare artificialmente il beneficio del dislivello.

**I soli D+ non determinano `G`:** mancano discese, distribuzione delle pendenze e profilo netto. Se manca un GAP/profilo attendibile, usare `G=1`, etichettare «dislivello non corretto» quando il percorso non è verificato pianeggiante e ampliare la fascia. Non dichiarare che i D+ sono stati compensati.

Il polinomio descrive un costo energetico: tradurlo in un tempo equivalente resta un'approssimazione del modello, soprattutto in discesa. Il lavoro di Minetti et al. (2002) ne sostiene la base energetica, non garantisce una conversione individuale esatta del tempo. [Fonte](https://pubmed.ncbi.nlm.nih.gov/12183501/).

#### 2.3 Best effort da allenamento: eleggibilità per intensità

Valutare ogni tratto candidato sul proprio tempo, FC e profilo, non sulle medie dell'attività che lo contiene. Mantenere soltanto tratti con GPS valido, distanza e durata minime e rapporto trascorso/movimento entro il limite della sezione 4. Se manca il tempo in movimento, la continuità deve essere attestata dalla classificazione esplicita come test continuo; altrimenti non qualificare il tratto.

Quando la FC del tratto supera i controlli di qualità della sezione 4:

- con FC soglia attendibile, usare prioritariamente il rapporto `FC_tratto/FC_soglia`;
- se manca la FC soglia, usare una FC massima misurata/validata, non una formula basata sull'età;
- per durata fino alla soglia breve usare le soglie HR brevi; oltre, quelle lunghe;
- non cercare il criterio più permissivo fra FC soglia e FC max se entrambi sono disponibili: prevale la FC soglia.

Questi criteri riconoscono un tratto impegnativo, non provano da soli la massimalità. Per la qualità ottica sconosciuta o FC invalida, non usare il rapporto FC per qualificare il tratto.

**Fallback senza FC affidabile:** il tratto è eleggibile soltanto se è dichiarato esplicitamente test continuo a sforzo massimale/quasi massimale e ha RPE sufficiente, riferita al tratto o al test. Un generico «allenamento duro» riferito a un'intera sessione con riscaldamento non basta. Senza tale informazione conservarlo come prestazione osservata, esclusa dal punto centrale.

Per tutti i tratti eleggibili della stessa attività calcolare il tempo equivalente standard sui 10 km:

```
score = t_standard_del_tratto × (10000 / d_tratto)^k0
```

Scegliere il minimo score, non il tratto più lungo. In caso di identico score non arrotondato, preferire la distanza maggiore; poi l'inizio del tratto più precoce; infine l'identificativo minore. Se non c'è alcun tratto eleggibile, quell'attività non aggiunge un riferimento.

Per evitare circolarità, nella selezione usare `k0`, senza correzioni di preparazione o di forma. Il tratto selezionato verrà successivamente proiettato su tutte le distanze con il modello completo.

#### 2.4 Priorità esplicita delle fonti

- Se c'è almeno una gara entro la soglia di recenza, mantenere la semantica v1: usare solo le gare eleggibili dell'ultimo anno.
- In assenza di gare recenti, considerare gare storiche eleggibili e best effort qualificati. Applicare la protezione del peso delle gare descritta nella sezione 6.
- Con soli best effort qualificati, produrre stime a bassa affidabilità.
- Con soli tratti non qualificati, mostrare le prestazioni osservate senza convertirle in una previsione centrale di gara.

| Parametro | Valore iniziale | Intervallo di ricerca |
|---|---:|---:|
| [PARAMETRO_DA_CALIBRARE] Tolleranza GPS rispetto alla distanza ufficiale | 2% | 1–3% |
| [PARAMETRO_DA_CALIBRARE] Finestra spaziale di regolarizzazione del profilo, se serve ricostruire GAP | 50 m | 25–100 m |
| [PARAMETRO_DA_CALIBRARE] Copertura minima valida di profilo/FC | 80% | 70–95% |
| [PARAMETRO_DA_CALIBRARE] Intervallo operativo di G su strada | 0,90–1,15 | estremo basso 0,80–1,00; alto 1,10–1,30 |
| [PARAMETRO_DA_CALIBRARE] Distanza minima dei riferimenti; durata minima del best effort | 3.000 m; 600 s | 2.000–5.000 m; 360–1.200 s |
| [PARAMETRO_DA_CALIBRARE] Durata che separa i criteri HR brevi/lunghi | 3.600 s | 1.800–5.400 s |
| [PARAMETRO_DA_CALIBRARE] FC/FC soglia minima: breve; lunga | 0,95; 0,90 | 0,90–1,00; 0,85–0,98 |
| [PARAMETRO_DA_CALIBRARE] FC/FC max minima: breve; lunga | 0,90; 0,85 | 0,85–0,95; 0,80–0,90 |
| [PARAMETRO_DA_CALIBRARE] RPE minima del test dichiarato senza FC affidabile | 9/10 | 8,5–10/10 |

### 3. Correzione per caldo e umidità

#### 3.1 Due prodotti distinti

Restituire sempre **la previsione standard**, con `H_obiettivo=1`: piano, condizioni temperate di riferimento, senza una penalizzazione termica. È una convenzione di confronto, non un singolo valore universale di temperatura ottimale.

Restituire separatamente, quando i dati lo permettono, **lo scenario gara**, usando località, data e orario gara. La forma atletica resta quella al cutoff; non viene proiettata nel futuro.

Ordine deterministico per lo scenario gara:

1. Se esiste una previsione meteo emessa entro `c` che copre l'evento entro l'orizzonte ammesso, usare quella.
2. Altrimenti, con data/periodo e località noti, usare climatologia storica costruita solo con anni precedenti e disponibili entro `c`.
3. Senza data/periodo o senza dati adeguati, non produrre lo scenario meteo: lasciare disponibile lo standard. Non sostituire il meteo del momento.

Con mese ma senza giorno, usare tutti i giorni di quel mese negli anni ammessi. Con giorno noto, usare la finestra stagionale centrata sul giorno dell'anno, con gestione circolare di dicembre/gennaio. Per il 29 febbraio usare il 28 negli anni non bisestili. Se manca l'orario, usare quello convenzionale dichiarandolo.

Valutare il meteo all'istante `partenza + t_standard_coerente/2`, nella timezone della località; fare una sola valutazione, senza iterazioni implicite fra caldo e durata. Scegliere l'osservazione disponibile più vicina entro la tolleranza temporale, con spareggio a favore di quella precedente. Per orari locali ambigui al cambio dell'ora richiedere un offset UTC esplicito; senza, lo scenario resta indisponibile. Per la climatologia, ogni anno ha lo stesso peso totale, indipendentemente dal numero di osservazioni valide. Temperatura e rugiada devono appartenere alla stessa osservazione; non combinarne separatamente i quantili.

#### 3.2 Tabella di base e dipendenza dalla durata

Definire `X = T_F + DP_F = 1,8 × (T_C + DP_C) + 64`. Rifiutare valori non finiti o punto di rugiada superiore alla temperatura, senza una tolleranza implicita non dichiarata.

La tabella empirica mantenuta dal modello originale è:

| X | 100 | 110 | 120 | 130 | 140 | 150 | 160 | 170 | 180 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| h, frazione di rallentamento | 0 | 0,005 | 0,010 | 0,020 | 0,030 | 0,045 | 0,060 | 0,080 | 0,100 |

Interpolare linearmente. Per `X<100`, porre `h=0`, senza attribuire un bonus per freddo. Per `X>180`, proseguire con la pendenza degli ultimi due nodi e applicare il tetto configurato:

```
h(X) = min(h_max, 0,10 + (0,10−0,08)/(180−170) × (X−180))
```

Per una durata `t` in secondi:

```
lambda(t) = clip((t/3600)^gamma, lambda_min, lambda_max)
H(T,DP,t) = 1 + lambda(t) × h(X)
```

Per normalizzare un riferimento usare la sua durata trascorsa osservata, evitando un'equazione implicita. Quindi:

```
t_i_standard = t_i_piano / H(T_i, DP_i, t_i_trascorso)
```

Per il trend FC usare lo stesso H con la durata del segmento regolare, non necessariamente la durata dell'attività completa.

Per lo scenario gara usare la durata standard finale della specifica distanza. A parità di meteo, il rallentamento aumenta con la durata fino al tetto: il moltiplicatore di una maratona sarà normalmente maggiore di quello di un 5 km. Il tetto evita estrapolazioni numeriche incontrollate, ma non valida il modello in condizioni estreme.

La dipendenza dalla disciplina e dalle condizioni ambientali ha riscontri nella ricerca. La tabella T+DP, la radice della durata e i coefficienti qui scelti restano euristici; non sono le equazioni dello studio di Mantzios et al. [Studio primario](https://pubmed.ncbi.nlm.nih.gov/34652333/).

#### 3.3 Dati mancanti e incertezza meteo

- Meteo storico mancante: usare H=1 come imputazione neutra, registrare la mancanza e aumentare l'incertezza del riferimento. Non confondere «mancante» con «verificato fresco».
- Per lo standard non esiste più il +2% dovuto al meteo attuale mancante: il meteo attuale non è un input necessario.
- Con ensemble/previsioni probabilistiche o campioni climatologici, calcolare H per ogni campione e usare mediana, quantile basso e alto. I quantili meteo non sono da soli un intervallo predittivo del tempo gara.
- Con una sola previsione deterministica, usare margini meteo aggiuntivi dichiarati, in attesa di calibrazione.
- Con `X>180` o temperatura sotto il limite di validità scelto per il freddo, segnalare «scenario termico fuori dal dominio ordinario», affidabilità bassa e un margine extra sul lato lento. H=1 al freddo non significa assenza di effetto del freddo.

| Parametro | Valore iniziale | Intervallo di ricerca |
|---|---:|---:|
| [PARAMETRO_DA_CALIBRARE] Tabella T+DP, incluso il punto di inizio a 100 | tabella riportata | spostamento dei nodi ±10 °F; penalizzazioni ×0,5–1,5, mantenendo monotonicità e zero iniziale |
| [PARAMETRO_DA_CALIBRARE] h_max oltre 180 | 0,20 | 0,10–0,30 |
| [PARAMETRO_DA_CALIBRARE] Durata termica di riferimento; gamma; lambda_min; lambda_max | 3.600 s; 0,5; 0,5; 1,5 | 1.800–5.400 s; 0,25–0,75; 0,25–1,0; 1,0–2,0 |
| [PARAMETRO_DA_CALIBRARE] Orizzonte massimo della previsione meteo | 10 giorni | 7–16, entro copertura della fonte |
| [PARAMETRO_DA_CALIBRARE] Anni climatologici; minimo di anni con dati | 10; 5 | 5–20; 3–10 |
| [PARAMETRO_DA_CALIBRARE] Semifinestra stagionale | 15 giorni | 7–30 |
| [PARAMETRO_DA_CALIBRARE] Ora locale se assente | 09:00 | 06:00–12:00 |
| [PARAMETRO_DA_CALIBRARE] Tolleranza dell'osservazione meteo rispetto all'istante cercato | 60 minuti | 30–90 minuti |
| [PARAMETRO_DA_CALIBRARE] Quantili meteo basso/alto | 10°/90° percentile | 5°–20° / 80°–95° |
| [PARAMETRO_DA_CALIBRARE] Margini log-meteo senza ensemble, veloce/lento | 0,01 / 0,02 | 0–0,03 / 0,01–0,05 |
| [PARAMETRO_DA_CALIBRARE] Limite freddo ordinario; extra log-margine lento per condizioni estreme | 0 °C; 0,03 | −5–5 °C; 0,01–0,08 |

I quantili sono definiti anche per campioni finiti nella sezione 6. Se non è raggiunto il numero minimo di anni, lo scenario climatologico è indisponibile, senza una climatologia sintetica inventata. In caso di dati invalidi o errore della fonte forecast, tentare la climatologia; se fallisce anch'essa mantenere solo lo standard. La durata di riferimento nel denominatore di lambda è il parametro 3.600 s, non una costante fisiologica.

### 4. Andamento della forma attraverso la FC

#### 4.1 Segmenti e qualità del sensore

Usare corse regolari delle ultime otto settimane, evitando il riscaldamento iniziale. Conservare la classificazione di regolarità del sito: coefficiente di variazione del passo su finestre regolari sotto la soglia, attività non classificata gara/ripetute, soste limitate.

Con stream disponibili, eliminare il riscaldamento iniziale, suddividere il tempo restante in finestre consecutive della durata configurata e calcolare in ciascuna le mediane dei campioni validi di velocità e FC. Scartare la finestra se i campioni validi non coprono la quota minima della sua durata; trattare come validi solo intervalli attestati dal campionamento, senza riempire buchi. Per l'ultimo spezzone usare la sua durata reale. Usare poi, per velocità e FC della corsa, le mediane ponderate per durata delle finestre valide. Escludere finestre marcate invalide dal dispositivo e valori FC fuori dall'intervallo fisiologico operativo, senza correggerli artificialmente. Richiedere anche la copertura minima della sezione 2 sulla parte analizzata della corsa.

Attribuire qualità `q=1` a una fascia elettrica attendibile, qualità ridotta al sensore ottico e ulteriormente ridotta a una fonte sconosciuta. Una fascia senza metadati di qualità non rende automaticamente valide letture anomale. Senza stream usare la media dell'attività solo se è già classificata regolare, con qualità «sconosciuta».

Accoppiare corse dallo stesso dispositivo quando entrambi gli identificativi sono noti; se almeno un identificativo manca, richiedere la stessa classe di sensore. Non confrontare direttamente letture di fascia e ottico per stimare un cambiamento di forma. Questa regola evita che un cambio di dispositivo venga interpretato come miglioramento.

Il ricorso a mediana, confronti omogenei e qualità ridotta mitiga il rumore: non è una procedura capace di riconoscere ogni artefatto ottico. La variabilità di accuratezza è documentata da Gillinov et al. (2017). [Fonte](https://pubmed.ncbi.nlm.nih.gov/28709155/).

#### 4.2 Efficienza e confondimento stagionale

Per ogni corsa regolare:

```
E_i = v_GAP_i × H_i / FC_i
```

Usare velocità grezza se il GAP manca, ma confrontare soltanto corse con lo stesso tipo di velocità. E è un indicatore operativo, non una misura di VO2max.

Confrontare coppie distanti almeno il numero minimo di giorni e con rapporto fra le durate analizzate, dopo il riscaldamento, ammesso. Richiedere meteo storico completo per entrambe. Se manca, quelle corse possono contribuire al volume ma non al trend stagionalmente controllato.

Il peso della coppia è:

```
w_ij = q_i q_j
       × max(0, 1 − abs(FC_i−FC_j)/delta_FC)
       × max(0, 1 − abs(T_i−T_j)/delta_T)
       × max(0, 1 − abs(DP_i−DP_j)/delta_DP)
```

Scartare solo le coppie con peso zero. Le coppie con FC poco diversa hanno più peso, senza la precedente soglia rigida a ±5 bpm.

Il confronto con T e rugiada vicine resta necessario anche quando la tabella termica dà h=0: aiuta a non scambiare l'arrivo del fresco per un miglioramento. Se non esistono condizioni confrontabili, il modello non presume che tale confondimento sia stato risolto.

Per ciascuna coppia con `r_j>r_i`:

```
s_ij = ln(E_j/E_i) / (r_j−r_i)
s = mediana_ponderata(s_ij, w_ij)
```

Limitare s a `±ln(1+cap_forma)/56`. Definire `M=sum(w_ij)` come supporto ponderato, non come numero di osservazioni indipendenti. Sia `S` l'estensione in giorni delle corse che partecipano ad almeno una coppia positiva.

```
rho = M/(M+M0) × S/(S+S0)
```

Se non esistono coppie, porre `s=0`, `rho=0`. Il numero di coppie non basta a promettere accuratezza, perché condividono le stesse corse.

#### 4.3 Trasferimento alla gara

Per un riferimento vecchio di `A_i` giorni:

```
F_i,D = exp(beta_D × rho × s × min(A_i,56))
```

Le beta sono più basse sulle distanze brevi: l'efficienza submassimale non viene trasferita automaticamente 1:1 alla prestazione massima. Lo shrinkage sostituisce le soglie secche «8 corse, 21 giorni, 12 coppie»: con poco supporto F rimane vicino a 1. Sono necessari soltanto i dati minimi matematici per formare una coppia valida.

| Parametro | Valore iniziale | Intervallo di ricerca |
|---|---:|---:|
| [PARAMETRO_DA_CALIBRARE] CV massimo del passo per regolarità | 0,08 | 0,05–0,12 |
| [PARAMETRO_DA_CALIBRARE] Durata attività regolare minima/massima | 1.200 / 10.800 s | 900–1.800 / 7.200–14.400 s |
| [PARAMETRO_DA_CALIBRARE] Eccesso massimo del tempo trascorso rispetto al movimento | 5% | 2–10% |
| [PARAMETRO_DA_CALIBRARE] Riscaldamento rimosso; ampiezza finestra FC | 300 s; 30 s | 180–600 s; 15–60 s |
| [PARAMETRO_DA_CALIBRARE] Intervallo operativo FC dei segmenti | 40–220 bpm | limite basso 30–50; alto 200–240 |
| [PARAMETRO_DA_CALIBRARE] Qualità ottico; sconosciuto | 0,50; 0,25 | 0,25–1,00; 0,10–0,50 |
| [PARAMETRO_DA_CALIBRARE] Separazione temporale minima delle coppie | 7 giorni | 3–14 |
| [PARAMETRO_DA_CALIBRARE] Rapporto massimo simmetrico delle durate | 2, cioè 0,5–2 | 1,5–3 |
| [PARAMETRO_DA_CALIBRARE] delta_FC; delta_T; delta_DP | 10 bpm; 3 °C; 3 °C | 6–15 bpm; 2–5 °C; 2–5 °C |
| [PARAMETRO_DA_CALIBRARE] M0; S0 | 12; 21 giorni | 6–30; 14–42 giorni |
| [PARAMETRO_DA_CALIBRARE] cap_forma su 56 giorni | 8% | 4–12% |
| [PARAMETRO_DA_CALIBRARE] beta per 5/10/21,1/42,2 km | 0,25 / 0,50 / 0,75 / 1,00 | 0–0,50 / 0,10–0,80 / 0,25–1,00 / 0,50–1,00; mantenere ordine non decrescente |

### 5. Proiezione sulle distanze

#### 5.1 Riegel con transizioni continue

La forma Riegel rimane il nucleo del modello. Il valore k0 è un riferimento di popolazione da calibrare, soprattutto per la maratona: lo studio di Vickers e Vertosick (2016) ha riscontrato limiti importanti della semplice estrapolazione su quella distanza. [Fonte](https://pubmed.ncbi.nlm.nih.gov/27570626/).

Con `D,d_i` in metri, definire:

```
R = D/d_i
g(R) = clip(ln(R)/ln(R_full), 0, 1)
u_H(D) = clip((D−D_start_H)/(21097,5−D_start_H), 0, 1)
u_M(D) = clip((D−D_start_M)/(42195−D_start_M), 0, 1)

P_H = a_HV max(0,1−V/30) + a_HL max(0,1−L/18)
P_M = a_MV max(0,1−V/55) + a_ML max(0,1−L/30)

k_i,D = k0 + g(R) × [u_H(D)P_H + u_M(D)P_M]
```

`clip(x,0,1)=min(1,max(0,x))`. Le rampe evitano sia il gradino a `D=1,10d`, sia nuove discontinuità artificiali in prossimità di 20 o 40 km. Se D≤d non si applica penalità da estrapolazione verso l'alto.

I denominatori 30,18,55,30 hanno i parametri configurabili riportati sotto. Le formule li mostrano numericamente per facilitarne la lettura.

#### 5.2 Calo documentato della preparazione

Quando entrambe le finestre hanno copertura sufficiente:

```
rV_i = max(0,1−V/V_ref_i), se V_ref_i>0
rL_i = max(0,1−L/L_ref_i), se L_ref_i>0
Q_i,D = 1 + b_D × (a_V rV_i + a_L rL_i)
```

Se un dato di riferimento è mancante, privo di copertura o ha denominatore zero, porre il relativo r a zero e registrare «confronto preparazione non disponibile». Non attribuire un bonus o un calo certo a partire da dati incompleti.

Q agisce anche se D=d: una maratona storica non resta automaticamente riproducibile quando la preparazione è diminuita. Q rappresenta un'ipotesi di penalizzazione da ridotta preparazione, non una diagnosi di detraining fisiologico né una conversione misurata del calo di VO2max.

Per uno scarico pre-gara **esplicitamente registrato**, iniziato da non più della durata massima ammessa, usare per Q la finestra di 56 giorni terminata il giorno prima dello scarico; continuare a mostrare separatamente V e L reali attuali. Senza tale metadato non inferire automaticamente uno scarico da un calo di volume.

Per limitare il doppio conteggio tra calo FC e riduzione del carico:

```
C_i,D = Q_i,D / F_i,D,        se F_i,D ≥ 1
C_i,D = max(Q_i,D,1/F_i,D),   se F_i,D < 1
```

In questo modo due segnali di peggioramento non vengono sommati integralmente. Un miglioramento FC può compensare in parte una riduzione del carico; il backtest deve verificare se questa compensazione è giustificata.

Il fenomeno di detraining ha riscontri sperimentali, ma non determina i coefficienti qui scelti. Per esempio, uno studio di interruzione di due settimane ha rilevato cambiamenti cardiopolmonari in un piccolo campione di atleti: non autorizza a convertire un calo del volume del 70% in una specifica penalizzazione di tempo. [Studio primario](https://pubmed.ncbi.nlm.nih.gov/33517866/).

#### 5.3 Candidato standard per ciascun riferimento

```
t_i,D_standard = t_i_standard × (D/d_i)^k_i,D × C_i,D
```

Non arrotondare i risultati intermedi.

| Parametro | Valore iniziale | Intervallo di ricerca |
|---|---:|---:|
| [PARAMETRO_DA_CALIBRARE] k0 | 1,06 | 1,04–1,10 |
| [PARAMETRO_DA_CALIBRARE] R_full | 2 | 1,5–3 |
| [PARAMETRO_DA_CALIBRARE] D_start_H; D_start_M | 10.000; 21.097,5 m | 5.000–15.000; 18.000–30.000 m |
| [PARAMETRO_DA_CALIBRARE] a_HV; a_HL | 0,03; 0,03 | ciascuno 0–0,06 |
| [PARAMETRO_DA_CALIBRARE] a_MV; a_ML | 0,06; 0,06 | ciascuno 0–0,12 |
| [PARAMETRO_DA_CALIBRARE] V_H; L_H; V_M; L_M | 30 km/settimana; 18 km; 55 km/settimana; 30 km | 20–45; 14–22; 40–80; 24–36, nelle rispettive unità |
| [PARAMETRO_DA_CALIBRARE] a_V; a_L per Q | 0,12; 0,06 | 0,05–0,30; 0–0,15 |
| [PARAMETRO_DA_CALIBRARE] b_D per 5/10/21,1/42,2 km | 0,35 / 0,55 / 0,80 / 1,00 | ciascuno 0–1, mantenendo ordine non decrescente |
| [PARAMETRO_DA_CALIBRARE] Durata massima dello scarico registrato | 21 giorni | 14–28 |

### 6. Combinazione dello storico

#### 6.1 Pesi e protezione delle gare storiche

Per ciascuna distanza obiettivo:

```
w_i,D = 2^(−A_i/tau)
        × exp[−abs(ln(D/d_i))]
        × qualita_i
```

Qualità 1 per gara verificata e qualità ridotta per best effort qualificato. Non ridurre ulteriormente il peso con moltiplicatori impliciti non specificati.

Normalizzare i pesi. Se sono presenti sia gare storiche sia best effort qualificati, ma nessuna gara recente:

1. calcolare la massa normalizzata delle gare `m_R`;
2. se `m_R` è inferiore al minimo configurato `f_R`, assegnare alle gare massa totale `f_R` e ai best effort massa `1−f_R`, conservando i rapporti di peso interni a ciascun gruppo;
3. altrimenti mantenere i pesi normalizzati originali.

Con `f_R=0,5`, un unico best effort recente non può cancellare da solo una gara storica eleggibile. Le gare scadute oltre il limite massimo non ricevono questa protezione. La correzione Q è essenziale: la protezione non significa conservare intatta la forma di un anno prima.

#### 6.2 Mediana ponderata esatta

Raggruppare valori numericamente identici sommando i loro pesi, ordinare i valori distinti `x_1<...<x_n` e normalizzare i pesi a somma 1.

- Se un cumulato supera 0,5 senza essere uguale, restituire il relativo `x_j`.
- Se un cumulato è esattamente 0,5 e c'è un valore distinto successivo, restituire `(x_j+x_(j+1))/2`.
- Con un solo valore, restituirlo.
- Con due valori di uguale peso, restituire la media aritmetica dei due; con pesi disuguali, il valore di peso maggiore.

Per l'uguaglianza a 0,5 ammettere una tolleranza ingegneristica assoluta di 10^-12 sui pesi normalizzati. La mediana dei tempi opera sui secondi, non sui loro logaritmi. Per il trend FC opera sulle pendenze, nelle loro unità.

Per i quantili meteo di ordine p, usare la distribuzione empirica ponderata: primo valore con cumulato ≥p; nessuna media di pareggio, eccetto la mediana p=0,5 per cui vale la regola precedente. Questa distinzione va testata esplicitamente.

#### 6.3 Metadati aggregati delle fonti

Usare gli stessi pesi normalizzati finali `alpha_i,D`, inclusa la protezione dei gruppi:

```
A_bar_D = sum(alpha_i,D × A_i)
f_BE_D = sum(alpha_i,D dei riferimenti di allenamento)
N_eff_D = 1 / sum(alpha_i,D^2)
disp_D = mediana_ponderata(abs(ln(t_i,D/t_raw_D)), alpha_i,D)
```

Gli indicatori di meteo storico mancante, confronto preparazione mancante e pendenza non corretta sono anch'essi frazioni pesate. Per un dato di preparazione mancante si conta una volta il riferimento, anche se mancano sia V_ref sia L_ref.

N_eff descrive la concentrazione dei pesi; non è una numerosità statistica di osservazioni indipendenti. Riportare tutti i riferimenti con contributo e peso. Se si mostra un unico link principale, scegliere il maggiore peso; nei pareggi la data più recente, poi l'ID minore. Quel link non determina da solo età o tipo usati nelle fasce.

#### 6.4 Coerenza fra distanze

Calcolare prima il margine base `u_base_D` della sezione 8, quindi porre:

```
y_D = ln(t_raw_D / (D/1000))
a_D = 1 / u_base_D^2
```

Trovare l'unico minimo di:

```
min sum_D a_D (z_D−y_D)^2
soggetto a z_5k ≤ z_10k ≤ z_mezza ≤ z_maratona
```

Usare PAVA: iniziare con un blocco per distanza; quando due blocchi adiacenti hanno media ponderata decrescente, fonderli con peso somma e media ponderata, ripetendo finché i blocchi sono ordinati. Assegnare a ciascun elemento la media del proprio blocco. Nessuna modifica se i passi sono già ordinati.

Riconvertire `p_D=exp(z_D)`, `t_standard_D=p_D×D/1000`. Registrare l'entità dell'aggiustamento: non è nuova evidenza di forma. Usare gli stessi metadati di origine, senza ricalcolare retroattivamente i pesi.

Esempio matematico, pesi uguali: 10 km a 6:10/km e mezza a 6:05/km vengono entrambi portati alla media geometrica dei due passi, circa 6:07,49/km. Il modello non lascia la mezza più veloce dei 10 km.

Applicare lo stesso vincolo al punto centrale di ogni scenario gara, dopo il moltiplicatore meteorologico. La coerenza è un vincolo di prodotto per le quattro distanze nello stesso scenario standardizzato: gare reali su percorsi o giorni diversi possono non rispettarlo.

| Parametro | Valore iniziale | Intervallo di ricerca |
|---|---:|---:|
| [PARAMETRO_DA_CALIBRARE] tau, emivita del peso temporale | 56 giorni | 28–112 |
| [PARAMETRO_DA_CALIBRARE] Qualità del best effort qualificato | 0,30 | 0,10–0,50 |
| [PARAMETRO_DA_CALIBRARE] f_R, massa minima delle gare storiche | 0,50 | 0,40–0,70 |
| [PARAMETRO_DA_CALIBRARE] Aggiustamento di coerenza che forza affidabilità bassa | 2% in valore assoluto | 1–5% |

### 7. Conversione in passo medio, arrotondamento e formato

Per ogni punto centrale finale, calcolare prima dell'arrotondamento:

```
passo_s_km = tempo_s / (D_m / 1000)
```

Tempo centrale e passo centrale sono arrotondati indipendentemente al secondo più vicino con **half-up** per numeri positivi: `n=floor(x+0,5)`. Non usare il round bancario predefinito di alcuni linguaggi. Non derivare il passo dal tempo già arrotondato.

Per il passo formattare `floor(n/60):(n modulo 60)` con secondi su due cifre. Quindi 299,6 s/km diventa 5:00/km, mai 4:60/km. Per il tempo usare `MM:SS` sotto un'ora e `H:MM:SS` da un'ora in avanti.

Per evitare di restringere le fasce con la visualizzazione, arrotondare l'estremo veloce per difetto e quello lento per eccesso, sia per tempi sia per passi. Conservare nel payload API tutti i valori non arrotondati necessari per audit. Non trasformare null, NaN o infinito in zero: mostrare «— / dati insufficienti».

L'uguaglianza dei passi dopo arrotondamento è ammessa. L'ordine dei passi prima dell'arrotondamento deve già essere corretto.

### 8. Fasce indicative e affidabilità

#### 8.1 Margine di base con più riferimenti

Con `A_bar_D`, `f_BE_D` e `disp_D` della sezione 6, calcolare un margine logaritmico:

```
u_base_D = u0
           + u_age min(A_bar_D/365,1)
           + u_BE f_BE_D
           + u_FC (1−rho)
           + u_tenuta I(D≥21097,5) max(0,1−L/(0,75 D_km))
           + u_mar I(D=42195)
           + u_prep_mar I(D=42195 e (V<40 o L<28))
           + u_meteo_storico f_meteo_mancante_D
           + u_pendenza f_pendenza_non_corretta_D
           + u_baseline f_preparazione_non_confrontabile_D
           + c_disp disp_D
```

`I` vale 1 se la condizione è vera, altrimenti 0. Per `f_pendenza_non_corretta` contare i riferimenti senza profilo/GAP e senza conferma di percorso pianeggiante. Un percorso dichiarato pianeggiante è una dichiarazione di input da conservare, non un fatto inferito da D+ mancanti.

Il giorno 365 nella frazione dell'età segue il limite massimo dei riferimenti se questo parametro viene cambiato. I valori 0,75, 40 e 28 sono parametri, riportati sotto.

Il modello non offre uno sconto automatico di incertezza con `1/sqrt(N_eff)`: riferimenti ripetuti dello stesso atleta possono avere errori comuni. La dispersione aumenta la fascia; una bassa dispersione da sola non prova accuratezza.

Dopo la coerenza del punto standard:

```
u_D = u_base_D + abs(ln(p_standard_D / p_raw_D))
tempo_veloce_D = t_standard_D × exp(−u_D)
tempo_lento_D  = t_standard_D × exp(m_D × u_D)
```

`m_D>1` rende la coda lenta più larga, soprattutto in maratona. Le fasce sono inizialmente indicative, senza un livello di copertura statistica promesso.

#### 8.2 Fasce per lo scenario meteo

Con campioni meteo ottenere `H_low,H_med,H_high` usando la stessa durata standard D. Il punto grezzo dello scenario è `t_standard_D×H_med`, poi reso coerente fra distanze.

```
veloce_scenario_D = veloce_standard_D × H_low
lento_scenario_D  = lento_standard_D  × H_high
```

Con sola previsione puntuale usare `H_low=max(1,H_med×exp(−u_w_fast))` e `H_high=H_med×exp(u_w_slow)`. Per condizioni termiche estreme moltiplicare ulteriormente l'estremo lento per `exp(u_extreme)`.

Per contenere il punto centrale eventualmente spostato da PAVA, estendere le fasce con `lower=min(lower,centrale)` e `upper=max(upper,centrale)`.

Per rendere monotoni anche gli estremi dei passi **senza restringere alcuna fascia**:

```
lower_final_i = min(lower_j per j≥i)   # minimo da destra
upper_final_i = max(upper_j per j≤i)   # massimo da sinistra
```

Operare sui passi in ordine 5 km→maratona, poi riconvertire in tempi. Questa operazione può soltanto ampliare le fasce; una regressione isotonica applicata direttamente agli estremi potrebbe invece restringerle e peggiorarne la copertura.

#### 8.3 Affidabilità

Mostrare «Dati insufficienti» quando non esiste un punto centrale. Negli altri casi usare al massimo «Media» finché il sistema non supera il backtest.

L'affidabilità è **Media** solo se tutte queste condizioni sono vere:

- `u_base_D` non supera la soglia;
- almeno la quota minima dei pesi finali appartiene a gare entro 56 giorni;
- nessun riferimento con peso positivo ha una correzione di pendenza necessaria ma indisponibile;
- copertura attuale dell'allenamento sufficiente;
- aggiustamento di coerenza sotto la soglia;
- per lo scenario gara, meteo disponibile e non classificato estremo.

Altrimenti è **Bassa**. In particolare un riferimento vecchio di 300 giorni resta a bassa affidabilità anche se protetto dai best effort recenti. Non scegliere arbitrariamente l'età del riferimento corrispondente alla mediana: usare A_bar e le quote finali.

Scheda da mostrare per ciascuna distanza: passo e tempo standard, fascia standard, affidabilità, eventuale scenario gara separato con data/località/orario e fonte meteo, pesi e riferimenti principali, copertura del periodo, shrinkage FC, correzione Q, correzione di pendenza, avvisi sui dati mancanti e versione del modello. «Climatologia» e «previsione meteo» devono essere etichette distinte.

| Parametro | Valore iniziale | Intervallo di ricerca |
|---|---:|---:|
| [PARAMETRO_DA_CALIBRARE] u0; u_age; u_BE; u_FC | 0,04; 0,06; 0,06; 0,02 | 0,02–0,08; 0,02–0,10; 0,02–0,12; 0–0,05 |
| [PARAMETRO_DA_CALIBRARE] u_tenuta; quota di distanza del lungo | 0,03; 0,75 | 0–0,08; 0,60–0,90 |
| [PARAMETRO_DA_CALIBRARE] u_mar; u_prep_mar | 0,04; 0,05 | 0,02–0,10; 0,02–0,12 |
| [PARAMETRO_DA_CALIBRARE] V e L per preparazione maratona consolidata | 40 km/settimana; 28 km | 30–60; 24–34 |
| [PARAMETRO_DA_CALIBRARE] u_meteo_storico; u_pendenza; u_baseline | 0,01; 0,03; 0,01 | 0–0,03; 0,01–0,08; 0–0,03 |
| [PARAMETRO_DA_CALIBRARE] c_disp | 1 | 0,5–2 |
| [PARAMETRO_DA_CALIBRARE] m_D per 5/10/21,1/42,2 km | 1,2 / 1,3 / 1,5 / 1,8 | ciascuno 1–2,5, in ordine non decrescente |
| [PARAMETRO_DA_CALIBRARE] Margine massimo per affidabilità Media | 0,10 | 0,06–0,15 |
| [PARAMETRO_DA_CALIBRARE] Quota minima di gare entro 56 giorni per affidabilità Media | 0,50 | 0,40–0,80 |

## B. Modifiche, motivazioni ed effetto atteso

| Criticità | Modifica | Motivazione | Effetto atteso sulla previsione |
|---|---|---|---|
| 1. Tratto più lungo spesso facile | Minimo tempo equivalente sui 10 km fra tratti qualificati | Confronta prestazioni relative, non lunghezze | Può essere più ottimista se emerge un tratto intenso; più prudente se il vecchio riferimento era non qualificato |
| 1. Intensità e dati FC mancanti | Criterio sul tratto, priorità FC soglia; fallback test dichiarato con RPE | Un lungo facile non prova la prestazione massimale | Meno stime centrali non supportate; direzione del tempo non garantita |
| 1. Allenamento recente domina gara vecchia | Massa minima del 50% alle gare storiche, dopo Q | Gestisce esplicitamente il crollo del peso temporale | Variabile: protegge una gara dalla sostituzione con uno sforzo submassimale, ma aumenta la rilevanza di dati vecchi |
| 2. Meteo del momento | Standard sempre disponibile; scenario data-gara o climatologico separato | Stabilità e significato temporale corretto | Standard stabile; scenario più lento se caldo |
| 2. Durata, estremi tabella e margini | H dipendente dalla durata, estensione e tetto dichiarati; nessun +2% per meteo attuale assente | Effetto termico diverso fra gare e nessuna falsa mancanza nello standard | Più prudente per gare lunghe al caldo; meno correzione su quelle brevi |
| 3. GPS e dislivello | Distanza ufficiale, GAP del riferimento, nessuna conversione dal solo D+ | Evita errori geometrici e benefici inventati | Distanza ufficiale più corta può rendere la previsione più lenta; salita corretta può renderla più veloce |
| 4. Calo della preparazione | Q confronta finestre storiche e attuali anche quando D=d | Il record di un periodo molto allenato non resta automaticamente attuale | Più prudente rispetto allo stesso riferimento invariato |
| 4. Scarico e doppio conteggio | Finestra prima dello scarico registrato; max fra i due segnali di peggioramento | Riduce penalità spurie o duplicate | Meno penalizzante di una somma indiscriminata |
| 5. Stagionalità, sensori, trasferimento FC | Coppie con meteo simile, qualità per sensore, shrinkage e beta per distanza | Riduce confondimento e trasferimento 1:1 | Correzione più vicina a zero; miglioramento positivo meno ottimista |
| 6. Passi incoerenti | PAVA sui log-passi | Vincolo esplicito e soluzione unica | Può accelerare o rallentare le distanze coinvolte |
| 7. Mediana e pareggi | Mediana esatta; punto medio del tratto di mediane in caso di metà peso | Elimina dipendenza dall'ordine delle righe | Con due fonti equiponderate media i tempi invece di scegliere il più veloce |
| 8. Margini con più fonti | Età media pesata, quota allenamenti e dispersione | Non attribuisce tutta l'incertezza a una sola attività | Fasce più rappresentative; spesso più ampie quando le fonti divergono |
| 9. Gradino di estrapolazione | Rampe continue in rapporto e distanza | Evita salti a 1,10 e nuove soglie rigide | Più graduale; meno penalizzante vicino alla soglia originale |
| 10. Formato | Half-up dopo conversione, modulo 60, arrotondamento esterno delle fasce | Risultati riproducibili e niente 4:60 | Nessun effetto sostanziale sul modello |
| 11. Fasce simmetriche | Margini logaritmici con coda lenta crescente | Rischio di rallentamento maggiore soprattutto in maratona | Fasce più prudenti sul lato lento |

## C. Pseudocodice dell'intera pipeline

```text
INPUT: cutoff c, attività/stream/metadata disponibili entro c,
       parametri versionati, eventuale località/data/orario gara

valid = filtra_attività_e_fonti(input, c)
V,L,copertura = aggrega_finestra_56_date(valid, oggi)
efficienze = estrai_segmenti_regolari(valid nelle 56 date)
efficienze = controlla_sensori_e_normalizza_GAP_meteo(efficienze)
coppie = confronta_device_durata_FC_T_DP(efficienze)
s = mediana_ponderata_delle_pendenze(coppie), oppure 0
s = limita_pendenza(s)
rho = shrinkage_supporto_e_span(coppie), oppure 0

gare = []
best_effort = []
FOR attività valida entro età massima:
    IF gara verificata:
        riferimento = usa_distanza_ufficiale_e_tempo_trascorso()
        gare.append(normalizza_pendenza_e_meteo(riferimento))
    ELSE:
        tratti = candidati_con_intensità_verificabile(attività)
        tratti = normalizza_pendenza_e_meteo(tratti)
        IF tratti non vuoti:
            best_effort.append(argmin(score_Riegel_10km, spareggi_stabili))

fonti = solo_gare_se_almeno_una_recente_altrimenti_gare_e_BE_qualificati()
IF meno_di_3_corse_attuali OR nessuna_fonte:
    RETURN quattro_schede_dati_insufficienti()

FOR D in [5000,10000,21097.5,42195]:
    FOR riferimento i:
        Vref,Lref = preparazione_prima_del_riferimento(i)
        k = Riegel_con_rampe_continue(D,di,V,L)
        Q = correzione_preparazione_documentata(V,L,Vref,Lref)
        F = exp(beta[D]*rho*s*min(età_i,56))
        C = Q/F se F>=1, altrimenti max(Q,1/F)
        candidato[i] = tempo_standard[i]*(D/di)^k*C
        peso[i] = recenza*similarità*qualità
    alpha = normalizza_e_proteggi_massa_delle_gare(peso)
    raw[D] = mediana_ponderata(candidato,alpha)
    metadati[D] = aggrega_età_tipo_mancanze_dispersione(alpha)
    u_base[D] = margine_base(metadati[D],preparazione,rho)

standard = PAVA(log_passi(raw), pesi=1/u_base^2)
fasce_standard = fasce_asimmetriche(standard,u_base,spostamento_PAVA)
fasce_standard = allarga_con_minimi_da_destra_e_massimi_da_sinistra()

IF località e data/periodo disponibili:
    scenari_meteo = forecast_emesso_entro_c_oppure_climatologia_precedente(c)
    IF scenari_meteo disponibili:
        FOR D:
            Hlow,Hmed,Hhigh = meteo_sulla_durata_standard(D)
            raw_gara[D] = standard[D]*Hmed
            fasce_gara[D] = fasce_standard[D]*(Hlow,Hhigh)
        gara = PAVA(log_passi(raw_gara), stessi_pesi)
        fasce_gara = includi_punto_e_allarga_monotonicamente(fasce_gara,gara)

RETURN schede_con_standard_scenario_distinto_affidabilità_e_provenienza(
    arrotondamento_half_up_dei_centrali,
    arrotondamento_esterno_degli_estremi,
    modello_e_snapshot_versionati
)
```

## D. Esempi numerici prima/dopo

### Condizioni comuni e dati mancanti

Gli esempi originali **non hanno una soluzione numerica univoca**: non specificano meteo futuro, tutti i dati di preparazione storica, qualità/intensità del best effort, magnitudine del trend o profilo altimetrico. Per evitare di inventarli:

- confronto v1/v2 in condizioni obiettivo standard, H=1; nella v1 equivale a usare il suo fallback senza meteo attuale;
- copertura attuale sufficiente e almeno tre corse; i V e L indicati sono aggregati completi;
- profilo 1: percorso verificato piano e preparazione storica uguale a quella attuale, come assunzioni dimostrative;
- profilo 2: meteo storico non disponibile, H=1 con flag; percorso della gara e del tratto assunto piano; il dato 70 km/settimana è assunto rappresentativo e completo delle otto settimane prima della maratona; L_ref della maratona resta ignoto, quindi nessuna penalità inventata su L_ref;
- profilo 2, caso principale: intensità del best effort non verificata, quindi escluso dal punto centrale v2;
- profilo 3: meteo storico assente, H=1 con flag; preparazione storica assunta uguale all'attuale; solo D+ disponibili, quindi G=1 con flag «dislivello non corretto»;
- profilo 3: per poter quantificare «trend positivo e valido», si assume un incremento di efficienza del 4% su 56 giorni, ossia `s=ln(1,04)/56=0,000700369/day`; supporto ponderato M=36 e span S=42 giorni, quindi `rho=(36/48)×(42/63)=0,5`. Questi valori sono aggiunte dichiarate all'esempio, non misure fornite.

Tutti gli arrotondamenti mostrati sono applicati soltanto alla fine. I numeri prima/dopo derivano dalla stessa preparazione dichiarata. In questi esempi i passi sono già coerenti: PAVA non li modifica.

### Profilo 1 — 10 km in 45:00, 21 giorni fa

`V=30`, `L=18`, trend FC assente: F=1. Preparazione invariata nell'assunzione: Q=1.

Meteo storico: T=20 °C, DP=12 °C.

```
X = 1,8×(20+12)+64 = 121,6
h = 0,010 + (1,6/10)×(0,020−0,010) = 0,0116

v1: H = 1,0116; t_base = 2700/1,0116 = 2669,039146 s
v2: lambda = sqrt(2700/3600) = 0,866025404
    H = 1 + 0,866025404×0,0116 = 1,010045895
    t_base = 2700/H = 2673,145858 s
```

Per mezza, P_H=0. Per maratona:

```
P_M = 0,06×(1−30/55) + 0,06×(1−18/30)
    = 0,051272727
k_M = 1,111272727
```

| Distanza | Rapporto D/d | k v1 e v2 | Calcolo v2 in secondi | v1 tempo · passo | v2 tempo · passo |
|---|---:|---:|---|---|---|
| 5 km | 0,5 | 1,06 | 2673,145858×0,5^1,06 | 21:20 · 4:16/km | **21:22 · 4:16/km** |
| 10 km | 1 | 1,06 | 2673,145858 | 44:29 · 4:27/km | **44:33 · 4:27/km** |
| Mezza | 2,10975 | 1,06 | 2673,145858×2,10975^1,06 | 1:38:09 · 4:39/km | **1:38:18 · 4:40/km** |
| Maratona | 4,2195 | 1,111272727 | 2673,145858×4,2195^1,111272727 | 3:40:19 · 5:13/km | **3:40:39 · 5:14/km** |

La piccola differenza deriva dalla correzione termica meno forte per una prestazione di 45 minuti. Per ciascuna riga `passo = secondi_calcolati / D_km`.

### Profilo 2 — maratona in 3:30:00, 300 giorni fa

`V_ref=70`, `V=20`, `L=12`, nessun trend FC disponibile. Il best effort è 5 km in 23:30, 10 giorni fa.

**V1:** non essendoci una gara entro 180 giorni, entrano gara e allenamento. Il peso recente del best effort prevale per tutte e quattro le distanze. Per il best effort:

```
P_H = 0,03×(1−20/30) + 0,03×(1−12/18) = 0,02
P_M = 0,06×(1−20/55) + 0,06×(1−12/30) = 0,074181818
k: 1,06 sui 5/10 km; 1,08 sulla mezza; 1,154181818 sulla maratona
```

**V2, dati effettivamente forniti:** il best effort non è dimostrato intenso/massimale. Resta osservato ma non è eleggibile. Unica fonte centrale: la maratona storica, con affidabilità bassa.

```
rV = 1−20/70 = 0,714285714
rL = 0, perché L_ref non è disponibile; la mancanza amplia la fascia
a_V×rV = 0,12×0,714285714 = 0,085714286

Q_5 = 1+0,35×0,085714286 = 1,030000000
Q_10 = 1+0,55×0,085714286 = 1,047142857
Q_H = 1+0,80×0,085714286 = 1,068571429
Q_M = 1+1,00×0,085714286 = 1,085714286
```

Per tutti gli obiettivi D≤d_maratona, g=0 e k=1,06. F=1, C=Q.

| Distanza | Calcolo v1 dal best effort | Calcolo v2 dalla maratona storica | v1 tempo · passo | v2 tempo · passo |
|---|---|---|---|---|
| 5 km | 1410 | 12600×(5000/42195)^1,06×1,03 | 23:30 · 4:42/km | **22:33 · 4:31/km** |
| 10 km | 1410×2^1,06 | 12600×(10000/42195)^1,06×1,047142857 | 49:00 · 4:54/km | **47:48 · 4:47/km** |
| Mezza | 1410×4,2195^1,08 | 12600×0,5^1,06×1,068571429 | 1:51:16 · 5:16/km | **1:47:38 · 5:06/km** |
| Maratona | 1410×8,439^1,154181818 | 12600×1,085714286 | 4:35:32 · 6:32/km | **3:48:00 · 5:24/km** |

**Interpretazione necessaria:** correggere un difetto logico non garantisce un tempo più prudente rispetto alla v1. Qui la v2 risulta più veloce perché rifiuta di trattare uno sforzo non qualificato come un test e torna a una gara storica corretta. Il coefficiente Q non dimostra che l'atleta possa correre oggi una maratona in 3:48. Il riferimento vecchio, L=12 e i dati mancanti impongono affidabilità bassa e fascia ampia: questo caso è particolarmente importante nel backtest.

#### Variante: il 5 km viene confermato test massimale con RPE 9

Per illustrare la regola sui due gruppi, aggiungere tale conferma e assumere V_ref=20 e L_ref=12 prima del test: Q_test=1. Non è la conclusione automatica dai dati originali.

La massa grezza della gara è sotto il 50% a ogni distanza; la protezione assegna 0,5 alla gara e 0,5 al test. La mediana è il punto medio aritmetico dei due candidati.

| Distanza | Candidato gara corretto, s | Candidato test, s | Mediana v2, s | Tempo · passo v2 |
|---|---:|---:|---:|---|
| 5 km | 1353,129380 | 1410,000000 | 1381,564690 | **23:02 · 4:36/km** |
| 10 km | 2868,136584 | 2939,753446 | 2903,945015 | **48:24 · 4:50/km** |
| Mezza | 6457,766051 | 6675,764122 | 6566,765087 | **1:49:27 · 5:11/km** |
| Maratona | 13680,000000 | 16532,073524 | 15106,036762 | **4:11:46 · 5:58/km** |

Esempio del problema della v1 sulla maratona: peso gara ≈0,0244; peso test ≈0,0314. Il fattore qualità 0,3 non impedisce al test di prevalere. In v2 i due pesi finali sono esplicitamente 0,5/0,5 e A_bar=155 giorni, f_BE=0,5: questi sono i valori da usare nelle fasce, non 10 giorni e «solo allenamento».

### Profilo 3 — mezza in 1:35:00, GPS 21,4 km e D+ 250 m

`V=50`, `L=28`, età=40 giorni. La gara è verificata come mezza ufficiale.

```
delta_d = abs(21400/21097,5−1) = circa 1,434% < 2%
d_v1 = 21400 m
d_v2 = 21097,5 m
```

Il GPS rientra nella tolleranza, ma i soli D+ non permettono comunque di calcolare G. Nel caso principale G=1 e l'assenza di correzione altimetrica viene segnalata.

Con il trend dimostrativo dichiarato:

```
s = ln(1,04)/56
F_v1 = exp(s×40) = 1,028410900
rho_v2 = 0,5
F_v2_5  = exp(0,25×0,5×s×40) = 1,003507988
F_v2_10 = exp(0,50×0,5×s×40) = 1,007028282
F_v2_H  = exp(0,75×0,5×s×40) = 1,010560925
F_v2_M  = exp(1,00×0,5×s×40) = 1,014105961
```

P_H=0 e `P_M=0,06×(1−50/55)+0,06×(1−28/30)=0,009454545`. Q=1 per preparazione invariata nell'assunzione. Sulla maratona entrambe le versioni hanno k=1,069454545; sulle altre distanze k=1,06.

| Distanza | Calcolo v2 in secondi, G non disponibile | v1 tempo · passo | v2 tempo · passo |
|---|---|---|---|
| 5 km | 5700×(5000/21097,5)^1,06 / 1,003507988 | 19:47 · 3:57/km | **20:35 · 4:07/km** |
| 10 km | 5700×(10000/21097,5)^1,06 / 1,007028282 | 41:14 · 4:07/km | **42:45 · 4:17/km** |
| Mezza | 5700 / 1,010560925 | 1:31:00 · 4:19/km | **1:34:00 · 4:27/km** |
| Maratona | 5700×2^1,069454545 / 1,014105961 | 3:10:56 · 4:32/km | **3:16:36 · 4:40/km** |

Qui la distanza ufficiale e il trasferimento FC attenuato rendono il modello più prudente. D+ 250 rimane un limite noto, non una correzione già effettuata.

#### Sensibilità con GAP attendibile aggiunto successivamente

Se, solo a scopo illustrativo, un profilo attendibile fornisse G=1,03, il tempo piano diventerebbe `5700/1,03=5533,980583 s`. G=1,03 **non è ricavato dai 250 m D+**. Gli altri fattori restano uguali e tutti i tempi v2 precedenti vengono divisi per 1,03:

| Distanza | Tempo v2 con G=1,03 · passo |
|---|---|
| 5 km | **19:59 · 4:00/km** |
| 10 km | **41:31 · 4:09/km** |
| Mezza | **1:31:16 · 4:20/km** |
| Maratona | **3:10:52 · 4:31/km** |

### Controlli aritmetici degli esempi

- Il calcolo v1 è verificato contro il modulo attuale del sito, con le stesse ipotesi numeriche.
- Lo script verifica anche pareggi della mediana, continuità a R=1,10, riporto dei secondi in formattazione, PAVA e allargamento monotono degli estremi.
- Nessuno dei tre profili richiede aggiustamenti PAVA con i valori iniziali scelti.
- I valori intermedi completi, i pesi e i candidati sono in `examples.json`.
- Per verificare la continuità: a R=1,10, `g=ln(1,10)/ln(2)≈0,1375035`. La penalità entra gradualmente invece di passare da zero al 100% appena superata la soglia.
- Nessuno scenario meteorologico futuro è inventato per completare le tabelle: è un confronto delle previsioni standard.

## E. Piano di backtest e calibrazione

### Dataset necessario

Per ogni atleta: storia delle importazioni e copertura, corse e stream FC/velocità/pendenza, identificativo e classe di sensore, meteo storico all'orario corretto, gare verificate con distanza ufficiale e tempo, eventuale test dichiarato e RPE, periodi di scarico registrati. Per ricostruire la preparazione di un riferimento vecchio di un anno occorrono anche le sue otto settimane precedenti: non basta esportare gli ultimi dodici mesi esatti.

Per gli scenari gara: archivio delle previsioni meteo con timestamp di emissione e relativa disponibilità, oppure snapshot climatologici ricostruibili. Il meteo osservato dopo una gara non può sostituire il forecast che sarebbe stato disponibile al momento della previsione.

Conservare anche gli esempi esclusi e le ragioni: la copertura del prodotto, cioè quante gare ricevono una previsione, è una metrica distinta dall'accuratezza sulle sole gare facili da prevedere. Non scegliere retroattivamente solo gare riuscite o dati di fascia perfetti.

### Validazione temporale e separazione per atleta

1. Separare gli atleti in training, calibrazione delle fasce e test finale. Nessun atleta compare in più partizioni per la valutazione di generalizzazione a nuovi utenti.
2. Nel training usare validazione interna raggruppata per atleta e cronologica. Calibrare pochi blocchi di parametri per volta: selezione delle fonti, correzione di preparazione/FC, meteo, poi fasce. Penalizzare complessità e variazioni inutili dai valori iniziali.
3. Per ogni gara-obiettivo definire cutoff prima della partenza. Ricostruire tutto il dataset come noto a quel cutoff; rimuovere la gara-obiettivo, i suoi stream e ogni attività successiva.
4. Ricostruire anche i riferimenti e la loro preparazione con i soli dati disponibili al cutoff. Un metadato corretto manualmente dopo la gara non deve comparire retroattivamente senza un test separato.
5. Valutare separatamente almeno due orizzonti di prodotto: immediatamente prima della gara e un periodo più lungo prima della gara, versionando la scelta. L'orizzonte lungo misura anche il limite della forma «congelata» al cutoff.
6. Sul test finale non riottimizzare coefficienti, soglie, fonti o filtri. Le finestre di un atleta possono sovrapporsi nel tempo, ma bootstrap e incertezza devono essere raggruppati per atleta.

Un secondo esperimento, distinto, può misurare la personalizzazione sullo stesso atleta: usare solo sue gare precedenti per l'adattamento e gare successive per il test. Non presentarlo come test su nuovi atleti.

### Baseline e ablation

Confrontare v1, v2 completa, Riegel da ultima gara, Riegel da gara più simile per distanza. Eseguire ablation senza Q, senza trend FC, senza normalizzazione termica e senza protezione delle fonti. Verificare se ogni blocco aggiunge accuratezza, invece di mantenere complessità solo perché plausibile.

Analizzare per distanza, livello di prestazione, età del riferimento, volume, qualità del sensore, meteo, presenza di dislivello e origine gara/test. Il sottogruppo «vecchia maratona + volume attuale molto ridotto» deve essere esplicito.

### Metriche

Per gara g:

```
PE_g = 100 × (t_pred_g − t_reale_g) / t_reale_g
MAPE = media(abs(PE_g))
bias = media(PE_g)
coverage = media(lower_g ≤ t_reale_g ≤ upper_g)
relative_width = media((upper_g−lower_g)/t_reale_g)
```

Con questa convenzione bias negativo significa previsione troppo ottimista. Riportare anche mediana dell'errore assoluto percentuale, quantile alto dell'errore e copertura separata della coda veloce/lenta. La media dei PE firmati è il bias: non chiamarla una seconda misura indipendente di accuratezza.

Per evitare che un atleta con molte gare domini le statistiche, calcolare le metriche per atleta e poi mediare fra atleti; riportare accanto anche le metriche per gara. Aggiungere tasso di astensione, disponibilità meteo e ampiezza degli aggiustamenti di coerenza.

Per lo **standard**, verificare prioritariamente gare piatte e temperate oppure un sottogruppo standardizzato con procedura indipendente. Normalizzare il risultato reale usando esattamente la stessa correzione termica del modello può produrre una validazione circolare. Lo scenario meteo, invece, viene confrontato con il tempo reale usando il forecast/clima che era disponibile al cutoff.

### Da fasce indicative a intervalli calibrati

1. Congelare il modello centrale, inclusa PAVA, e tutti i preprocessing.
2. Su una partizione di calibrazione indipendente stimare gli errori residui logaritmici o calibrare i quantili condizionali. L'architettura con quantile regression più calibrazione conformale è una possibilità documentata da Romano, Patterson e Candès (2019), non una garanzia automatica per serie temporali dello stesso atleta. [Ricerca](https://arxiv.org/abs/1905.03222).
3. Calibrare rispetto a un livello nominale esplicito, con code eventualmente asimmetriche. Non riutilizzare il test per scegliere quel livello o correggere la larghezza.
4. Valutare la copertura finale dopo tutti i passaggi UI/coerenza e l'arrotondamento esterno. L'allargamento monotono proposto non riduce la copertura delle fasce preliminari; può aumentarla.
5. Richiedere un numero sufficiente di atleti e gare per distanza e sottogruppi critici; altrimenti aggregare gerarchicamente e indicare che non c'è calibrazione individuale.
6. Sul test finale verificare copertura, ampiezza e bias con intervalli di incertezza raggruppati per atleta; ripetere su un periodo successivo intatto per controllare il cambiamento di distribuzione.
7. Usare l'etichetta «intervallo calibrato all'80% sul dataset di validazione» solo dopo il superamento del protocollo. Non promettere che ogni singolo atleta avrà copertura dell'80%, né che la garanzia conformale marginale sopravviva senza ipotesi alla dipendenza temporale.

| Criterio progettuale di validazione | Valore iniziale | Intervallo/protocollo da scegliere |
|---|---:|---:|
| [PARAMETRO_DA_CALIBRARE] Livello nominale delle fasce calibrate | 80% | 80–95%, scelta di prodotto prima del test |
| [PARAMETRO_DA_CALIBRARE] Campione minimo per distanza prima di un'etichetta autonoma | 300 gare e 50 atleti | 200–1.000 gare; 30–100 atleti |
| [PARAMETRO_DA_CALIBRARE] Scarto massimo iniziale dalla copertura nominale sul test | 3 punti percentuali | 2–5 punti, con incertezza riportata |
| [PARAMETRO_DA_CALIBRARE] Orizzonti di backtest di prodotto | 1 giorno e 28 giorni prima della gara | 0–3 e 14–56 giorni |

Il criterio di copertura va affiancato all'intervallo di incertezza al 95% e alla larghezza: un campione piccolo compatibile con qualsiasi copertura non costituisce un successo. Il 95% è il livello convenzionale prescelto del protocollo statistico, non una costante fisiologica.

Con poche gare di un singolo utente non è possibile calibrare credibilmente tutte queste costanti. Prima usare dati multiatleta con separazione corretta, poi un adattamento personale fortemente regolarizzato. Finché mancano tali dati mantenere l'etichetta «fascia indicativa».

## F. Decisioni di prodotto aperte

| Decisione | Opzioni | Raccomandazione |
|---|---|---|
| Risultato principale | Solo meteo gara; solo standard; entrambi | **Standard principale, scenario gara separato**, con etichetta forecast/climatologia e forma attuale congelata |
| Data gara assente | Meteo del momento; mese scelto; nessuno scenario | Nessuno scenario automatico; consentire di scegliere data o mese. Eventuale meteo attuale solo come simulazione «se corressi adesso», mai come previsione futura |
| Intensità del best effort ignota | Accettarlo comunque; richiedere conferma; escluderlo definitivamente | Mostrare la prestazione osservata e richiedere conferma di test/RPE per usarla nella stima; non dedurre massimalità dal nome dell'attività |
| Gara storica contro test recente | Pesi liberi; quota protetta; sostituzione automatica | Quota gare iniziale 50%, dopo correzione Q. Esporre i contributi e calibrare la quota prima di presentarla come ottimale |
| Vecchia maratona e preparazione attuale insufficiente | Mostrare il punto a bassa affidabilità; mostrare soltanto fascia/scenario; astenersi | Nel prototipo mostrare il valore come **stima esplorativa a bassa affidabilità**, senza trasformarlo in obiettivo di gara; valutare astensione dopo il backtest di questo sottogruppo |
| D+ senza profilo | Correzione percentuale dal solo D+; G=1 con avviso; richiedere GPX | G=1 con flag e fascia più ampia; consentire caricamento/associazione di un profilo verificato |
| Copertura dello storico sconosciuta | Trattare ogni assenza come riposo; chiedere conferma; disabilitare Q | Richiedere una copertura verificabile o un aggregato dichiarato; senza, niente penalità Q inventata e affidabilità ridotta |
| Scarico pre-gara | Inferirlo dal volume; registrarlo; ignorarlo | Registrazione esplicita del periodo di scarico, per evitare di confonderlo con interruzione o dati mancanti |
| Sensori con metadati incompleti | Considerarli equivalenti; scartarli tutti; ridurre qualità | Qualità ridotta e shrinkage; non confrontare classi diverse per stimare un trend |
| Livello delle fasce | 80%; 90%; scelta utente | 80% come primo livello da calibrare, con ampiezza e copertura effettiva pubblicate; nessun livello percentuale prima del backtest |
| Coerenza su gare reali differenti | Vincolo globale; vincolo per scenario | Vincolo nel medesimo scenario standardizzato. Eventuali simulazioni di percorsi/date diversi devono essere mostrate come scenari separati |
| Migrazione dal sito v1 | Sostituzione immediata; shadow mode; attivazione manuale | **Shadow mode versionato**, confronto con v1 e test retrospettivo; attivazione dopo aver verificato soprattutto maratona e riferimenti vecchi |

Le raccomandazioni costituiscono la configurazione iniziale di questa specifica, non un'approvazione già espressa per modificare il sito. La tabella separa le scelte di prodotto dalla validazione scientifica dei coefficienti.
