# Requisiti Applicazione di Analisi Estratti Conto

## 1. Scopo e ambito

L’applicazione ha l’obiettivo di analizzare, normalizzare e classificare transazioni finanziarie provenienti da estratti conto bancari, carte di credito e carte di debito, supportando più formati, lingue e localizzazioni, con capacità di apprendimento incrementale, associazione documentale e reportistica avanzata.

L’uso primario è il personal finance management, con possibilità di estensione a contesti semi-professionali e professionali.

---

## 2. Input e ingestione dati

### 2.1 Tipologie di sorgenti supportate

- Conti correnti bancari
- Carte di credito
- Carte di debito / prepagate
- Documenti giustificativi associabili alle transazioni

### 2.2 Formati di input

- CSV, TSV
- XLS / XLSX
- PDF strutturati
- Immagini (JPEG, PNG, HEIC)
- Formati documentali ignoti a priori
- API bancarie (opzionale, non obbligatorio nella prima versione)

OCR e parsing semantico dei documenti non strutturati sono opzionali e configurabili.

### 2.3 Idempotenza e normalizzazione

Il sistema deve essere idempotente rispetto al formato e riconoscere la stessa transazione anche se:

- le date sono in formati diversi (es. `YYYY-MM-DD`, `DD/MM/YYYY`, formati localizzati)
- sono presenti due date distinte:
  - data operazione
  - data contabile / valuta
- l’importo è indicato:
  - con segno positivo o negativo
  - tramite colonne Dare/Avere
- la valuta è esplicita o implicita
- la descrizione presenta variazioni minori (case, whitespace, encoding)

### 2.4 Identificazione univoca delle transazioni

Una transazione è identificata tramite una chiave composita fuzzy, ad esempio:

- data operazione con tolleranza configurabile
- importo normalizzato
- valuta
- hash normalizzato della descrizione
- conto di origine/destinazione

---

## 3. Riconoscimento dei giroconti

### 3.1 Definizione

Un giroconto è un insieme di transazioni che rappresentano lo stesso trasferimento di denaro tra conti controllati dall’utente.

### 3.2 Requisiti funzionali

- Riconoscimento automatico di:
  - bonifici interni
  - trasferimenti conto ↔ carta
- Matching basato su:
  - importi uguali o entro tolleranza
  - date compatibili
  - conti noti all’utente
- Possibilità di:
  - conferma manuale
  - forzatura o esclusione
- I giroconti non devono influenzare il totale delle spese

---

## 4. Categorizzazione delle transazioni

### 4.1 Categorie di spesa

- Categorie configurabili dall’utente
- Supporto a:
  - gerarchie (es. `Casa > Affitto`)
  - categorie speciali (es. `Giroconto`, `Entrate`)

### 4.2 Assegnazione automatica

- Basata su:
  - regole deterministiche (pattern testuali, merchant, IBAN, MCC)
  - modelli di machine learning
- Ogni assegnazione deve includere:
  - categoria proposta
  - probabilità/confidenza

### 4.3 Assegnazione manuale

- Override manuale sempre possibile
- Le correzioni devono essere persistite come esempi supervisionati

---

## 5. Contesto della transazione

### 5.1 Definizione

Il contesto rappresenta l’ambito funzionale della spesa (es. `Casa`, `Lavoro`, `Personale`, `Famiglia`).

### 5.2 Requisiti

- Contesti configurabili dall’utente
- Ogni transazione può avere zero o un contesto
- Il contesto è indipendente dalla categoria di spesa

---

## 6. Associazione di documenti alle transazioni

### 6.1 Caricamento documenti

- Possibilità di caricare documenti di formato non noto a priori
- Ogni documento deve essere associato a:
  - un contesto predefinito (configurabile)
  - metadati disponibili (data, importo, fornitore, valuta, se estraibili)

### 6.2 Associazione automatica documento–transazione

Il sistema deve:

- tentare l’associazione automatica tra documenti caricati e transazioni finanziarie
- utilizzare criteri di matching fuzzy basati su:
  - importo
  - data (con tolleranza)
  - valuta
  - descrizione / merchant / fornitore
- supportare associazioni uno-a-uno e uno-a-molti

### 6.3 Propagazione del contesto

Se un documento è caricato con un contesto assegnato, allora:

- quando viene individuata la transazione corrispondente
- il contesto del documento deve essere automaticamente assegnato alla transazione

Esempi:

- fatture elettroniche caricate con contesto `Lavoro` → la transazione associata eredita il contesto `Lavoro`
- ricevute fotografiche caricate con contesto `Casa` → la transazione associata eredita il contesto `Casa`

### 6.4 Override e validazione

- L’utente può:
  - confermare
  - modificare
  - annullare l’associazione documento–transazione
- Le correzioni manuali sono tracciate e utilizzabili come feedback supervisionato

---

## 7. Apprendimento incrementale

### 7.1 Comportamento

Se:

- la categoria o il contesto non vengono riconosciuti automaticamente, oppure
- la probabilità è inferiore a una soglia configurabile

allora:

- la transazione viene marcata come “da revisionare”
- la scelta manuale dell’utente viene usata come feedback supervisionato

### 7.2 Vincoli

- Apprendimento incrementale
- Nessun retraining completo obbligatorio
- Versionamento dei modelli consigliato

---

## 8. Multilingua e localizzazione

### 8.1 Requisiti

- Supporto a più lingue (UI e descrizioni)
- Supporto a più localizzazioni
- Gestione corretta di:
  - formati data
  - separatori decimali
  - valute
  - simboli monetari

---

## 9. Reportistica

### 9.1 Tipologie di report

- Mensile
- Annuale
- Progressivo da inizio periodo (YTD o custom)

### 9.2 Dimensioni di aggregazione

- Categoria di spesa
- Contesto
- Conto / carta
- Periodo temporale

### 9.3 Output

- Visualizzazione web interattiva
- Esportazione:
  - CSV
  - XLSX
  - PDF (opzionale)

---

## 10. Budgeting

### 10.1 Definizione budget

- Budget configurabile:
  - per categoria
  - per periodo (mensile, annuale)
- Supporto a:
  - budget nulli (solo tracking)
  - rollover opzionale

### 10.2 Confronto e alerting

- Confronto automatico spesa vs budget
- Evidenziazione:
  - superamento soglia
  - trend anomali

---

## 11. Architettura, LLM e protezione dei dati

### 11.1 Autonomia e privacy

- Funzionamento offline-first
- Nessuna dipendenza obbligatoria da servizi cloud

### 11.2 Uso di LLM

- LLM locale preferito
- Uso di LLM via API consentito solo se esplicitamente configurato

### 11.3 Sanitizzazione dei dati per LLM via API

Nel caso di utilizzo di LLM tramite API esterne, il sistema deve obbligatoriamente:

- sanitizzare le descrizioni delle transazioni e dei documenti prima dell’invio
- rimuovere o mascherare:
  - nomi e cognomi dei proprietari dei conti (lista configurabile)
  - riferimenti diretti o indiretti a numeri di conto, IBAN, carte di credito/debito
  - codici identificativi sensibili (es. PAN, CVV, ID transazione bancari)
- applicare regole di redazione configurabili e versionate
- garantire che nessun dato riconducibile a una persona fisica o a uno strumento di pagamento venga trasmesso all’esterno

---

## 12. Interfaccia utente

### 12.1 Web UI

- Applicazione web
- Dashboard principale con:
  - overview delle spese
  - alert
  - transazioni e documenti da revisionare

### 12.2 Requisiti non funzionali UI

- Reattiva
- Utilizzabile su desktop e tablet
- Nessuna dipendenza da browser proprietari

---

## 13. Requisiti non funzionali generali

- Riproducibilità dei risultati
- Logging completo delle decisioni automatiche
- Tracciabilità delle modifiche manuali
- Performance adeguate a dataset di almeno 5–10 anni di transazioni e documenti associati
