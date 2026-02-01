# Flusso 1 — Pre-analisi del formato + normalizzazione deterministica + ML/LLM mirato

## 1. Scopo del flusso

Gestire l’importazione di estratti conto e documenti finanziari tramite:

- pre-analisi strutturale del formato
- normalizzazione deterministica
- assegnazione di categoria e contesto
- uso opzionale e limitato di LLM
  con pieno rispetto dei requisiti di privacy.

Questo flusso privilegia riproducibilità, controllo e auditabilità.

---

## 2. Input

- Dataframe importato da:
  - CSV / XLS(X)
  - PDF strutturati
- Metadati di importazione:
  - lingua presunta
  - localizzazione
  - sorgente (banca, carta, documento)
- Eventuali documenti associati (fatture, ricevute)

---

## 3. Pre-analisi del formato

### 3.1 Riconoscimento strutturale

- Identificazione automatica o semi-automatica di:
  - colonne data (operazione, contabile)
  - colonna descrizione
  - colonna importo
  - colonna valuta
- Riconoscimento:
  - schema Dare/Avere
  - segno dell’importo
  - separatori decimali e migliaia

### 3.2 Inferenza della localizzazione

- Inferenza o conferma di:
  - formato data
  - convenzioni monetarie
  - lingua della descrizione

Output: **schema canonico di parsing** specifico per la sorgente.

---

## 4. Pre-normalizzazione

Applicazione dello schema canonico per produrre un dataframe intermedio:

- parsing date → ISO 8601
- importi → float normalizzato con segno coerente
- valuta → ISO 4217
- descrizione → stringa normalizzata (casefold, trim, unicode)

Nessuna categorizzazione in questa fase.

---

## 5. Normalizzazione canonica

Produzione del **dataframe transazioni canonico**:

- una riga = una transazione
- campi obbligatori:
  - transaction_id (fuzzy)
  - date_operation
  - date_accounting (opzionale)
  - amount
  - currency
  - description_normalized
  - source_account

Idempotenza garantita a questo livello.

---

## 6. Sanitizzazione dati (privacy)

Prima di qualunque uso di modelli ML/LLM:

- redazione di:
  - nomi dei proprietari (lista configurabile)
  - IBAN, PAN, numeri carta/conto
  - identificativi bancari sensibili
- mascheramento deterministico e reversibile solo localmente

Output: **view sanitized** del dataframe.

---

## 7. Assegnazione categoria e contesto

### 7.1 Regole deterministiche

- matching su:
  - pattern descrizione
  - merchant noti
  - IBAN/MCC (se disponibili)
- assegnazione con confidenza massima

### 7.2 Modelli ML

- classificatori supervisionati
- output:
  - categoria
  - contesto
  - probabilità

### 7.3 Uso opzionale di LLM

- limitato a:
  - descrizioni ambigue
  - clustering merchant
- input solo sanitized
- output sempre validato

---

## 8. Associazione documenti

- matching documento–transazione
- se documento ha contesto assegnato:
  - propagazione automatica alla transazione
- override manuale tracciato

---

## 9. Output del flusso

- Transazioni normalizzate
- Categoria e contesto assegnati
- Stato:
  - automatico
  - da revisionare
- Log completo delle decisioni

---

## 10. Proprietà del flusso

- Alta riproducibilità
- Auditabilità completa
- Dipendenza minima da LLM
- Adatto a volumi elevati e storico lungo


