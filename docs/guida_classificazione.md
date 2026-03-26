# Guida alla Classificazione delle Transazioni

> Come Spendify trasforma i tuoi movimenti bancari in dati categorizzati con il minimo intervento manuale.

---

## 1. Import

Carica il file movimenti (CSV, XLSX, XLS) trascinandolo nell'area di upload.

- Spendify riconosce lo schema del file automaticamente e calcola un **confidence score** (0-100%).
- Se la confidenza e >= 80%, l'importazione procede senza intervento.
- Le transazioni vengono categorizzate dall'AI (LLM) e dalle regole deterministiche gia presenti.

---

## 2. Review

Le transazioni su cui l'AI non era sicura vengono marcate con il flag di warning.

- Le transazioni incerte sono marcate con **warning** (da rivedere)
- Per ciascuna puoi correggere **categoria**, **sottocategoria** e **contesto** dai menu a tendina
- Conferma con **Validato** per dire a Spendify "questa classificazione e corretta"
- Puoi anche creare una regola direttamente dalla Review, come nel Ledger

---

## 3. Ledger — Il centro di comando

Vista completa di tutte le transazioni importate, con filtri per data, conto, categoria e tipo.

**Modifica diretta:** cambia categoria, sottocategoria e contesto direttamente nella griglia. Ogni modifica aggiorna il campo `classification_source` a "manual".

**Validazione:** spunta la checkbox Validato per confermare che la categoria e corretta. Il salvataggio e immediato, il flag di warning viene rimosso automaticamente.

**Crea regola da una transazione:**

1. Seleziona **1 riga** nella colonna di selezione (📏) della griglia del ledger. Se ne selezioni piu di una, l'app mostra un errore rosso: *"Si puo creare ed applicare una regola alla volta"*.
2. Compare il form **"Crea regola"**, pre-compilato con:
   - **Pattern**: estratto dalla controparte della transazione (es. `ESSELUNGA`)
   - **Tipo**: *Contiene il testo* (default). Le opzioni disponibili sono: *Contiene il testo*, *Uguale esatto*, *Espressione avanzata*
   - **Categoria/Sottocategoria/Contesto**: copiati dalla transazione selezionata
3. L'**anteprima** mostra quante transazioni verranno matchate dalla regola
4. Se la regola esiste gia, viene mostrato un avviso giallo e il pulsante diventa **"Modifica regola e applica"**
5. Click su **"Crea regola e applica"** (o **"Modifica regola e applica"**):
   - La regola viene salvata nel database
   - Viene applicata **retroattivamente** a tutte le transazioni matching non ancora validate
   - Un toast conferma "Regola creata" o "Regola aggiornata"

---

## 4. Regole automatiche

Le regole create (dal Ledger, dalla Review o dalla pagina Regole) si applicano automaticamente ai prossimi import.

- **Priorita**: le regole piu specifiche hanno priorita piu alta. Il primo match vince.
- **Tipi di match**: *Uguale esatto*, *Contiene il testo*, *Espressione avanzata* (colonna "Tipo" nella griglia regole).
- **Pagina Regole**: gestisci, modifica, elimina le regole esistenti. Usa il pulsante "Esegui tutte le regole" per applicarle in blocco a tutto lo storico.

---

## 5. Il ciclo virtuoso

```
Import --> AI categorizza --> Utente rivede --> Crea regola --> Prossimo import: 0 interventi
```

Piu usi Spendify, meno lavoro manuale. Ogni regola creata riduce il numero di transazioni da rivedere al prossimo import. Obiettivo: **zero pain**.

---

## Colonne indicatore

| Indicatore | Significato |
|------------|------------|
| Warning | Da rivedere (classificazione incerta) |
| Validato | Validata dall'utente |
| Giroconto | Giroconto interno (bonifico tra propri conti) |

---

## Validazione vs. fonte classificazione

Spendify gestisce due informazioni **distinte** per ogni transazione:

| Concetto | Campo | Significato |
|----------|-------|-------------|
| **Validazione** | `human_validated` | "Ho visto questa spesa e confermo che e corretta (non anomala)." E l'approvazione della **spesa**, non della categoria. |
| **Fonte classificazione** | `category_source` | "Chi ha assegnato l'ultima categoria." Valori: AI, Regola, Manuale, Storico. |

**Regola chiave:** i due campi sono indipendenti. Quando una regola o l'AI riclassificano una transazione, la fonte cambia ma la validazione **non viene toccata**. Solo un click esplicito sulla checkbox "Validato" (deselezionandola) puo rimuovere la validazione.

**In pratica:**
- Validi una transazione classificata dall'AI come "Alimentari" -> `validated=True`, `fonte=AI`
- Poi crei una regola che la riclassifica come "Supermercato" -> `validated=True` (invariato), `fonte=Regola`
- La validazione resta perche tu avevi gia confermato che la spesa era corretta

---

## Badge fonte classificazione

| Badge | Significato |
|-------|------------|
| AI | Categorizzata dall'intelligenza artificiale |
| Regola | Categorizzata da regola deterministica |
| Manuale | Modificata manualmente dall'utente |
| Storico | Categorizzata dallo storico (futuro) |

---

Per dettagli operativi su ogni pagina, consulta la [Guida Utente](guida_utente.md).
