# Flusso 2 — Elaborazione end-to-end tramite LLM (schema-on-read)

## 1. Scopo del flusso

Gestire l’importazione di estratti conto e documenti finanziari delegando:

- parsing
- normalizzazione
- categorizzazione
- assegnazione del contesto

a un LLM, mantenendo vincoli stringenti di privacy.

Questo flusso privilegia flessibilità e rapidità di onboarding.

---

## 2. Input

- Dataframe grezzo così come importato:
  - colonne non note
  - formati misti
  - localizzazione ignota
- Eventuali documenti associati
- Metadati minimi:
  - contesto di caricamento documenti (se presente)

---

## 3. Sanitizzazione preventiva (obbligatoria)

Prima dell’invio a LLM:

- rimozione o mascheramento di:
  - nomi dei proprietari (configurabile)
  - numeri di conto, IBAN, PAN
  - riferimenti diretti a carte o conti
- sostituzione con placeholder semantici:
  - `<OWNER>`
  - `<ACCOUNT_ID>`
  - `<CARD_ID>`

Nessun dato sensibile deve uscire dal perimetro locale.

---

## 4. Prompting strutturato al LLM

Il prompt deve richiedere esplicitamente:

- identificazione delle colonne rilevanti
- inferenza di:
  - date (operazione / contabile)
  - importi e segno
  - valuta
- normalizzazione in schema canonico
- assegnazione:
  - categoria
  - contesto
- restituzione di:
  - probabilità/confidenza
  - spiegazione sintetica (opzionale)

---

## 5. Parsing dell’output LLM

- Validazione sintattica dello schema restituito
- Controlli di coerenza:
  - importi
  - date
  - cardinalità righe
- Rifiuto automatico di output non conformi

---

## 6. Post-validazione deterministica

- Verifica idempotenza:
  - deduplicazione transazioni
- Verifica vincoli:
  - segni importi
  - valute ammesse
- Eventuale correzione rule-based

---

## 7. Associazione documenti

- Matching basato su output LLM
- Propagazione del contesto dai documenti alle transazioni
- Override manuale consentito

---

## 8. Apprendimento e feedback

- Le correzioni manuali:
  - non aggiornano il modello LLM
  - possono essere salvate come regole locali
- Possibile caching dei risultati per sorgente simile

---

## 9. Output del flusso

- Transazioni completamente interpretate
- Categoria e contesto assegnati
- Flag di affidabilità per singola transazione

---

## 10. Proprietà del flusso

- Massima flessibilità su formati ignoti
- Bassa riproducibilità rispetto al Flusso 1
- Forte dipendenza dal comportamento del LLM
- Adatto a:
  - volumi ridotti
  - onboarding rapido
  - sorgenti non standard
