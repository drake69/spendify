# Spendify — Pipeline di elaborazione

> Documento tecnico di riferimento. Ogni riga di codice che trasforma una transazione passa per questi stadi, in questo ordine.

---

## Mappa di alto livello

```
FILE (CSV / XLSX)
        │
        ▼
┌───────────────────┐
│  1. CARICAMENTO   │  parse bytes, encoding, delimiter, header
└────────┬──────────┘
         │
         ▼
┌────────────────────────┐       schema in DB?
│  2. SCHEMA DECISION    │──────────────────────────────┐
└────────┬───────────────┘                              │
         │ no (Flow 2)                                  │ sì (Flow 1)
         ▼                                              │
┌────────────────────────┐                              │
│  2b. CLASSIFICAZIONE   │  LLM → DocumentSchema        │
│      DOCUMENTO [RF-01] │                              │
└────────┬───────────────┘                              │
         └──────────────────────────┬───────────────────┘
                                    │
                                    ▼
                     ┌──────────────────────────┐
                     │  3. NORMALIZZAZIONE       │  date, importi, ID SHA-256
                     │     [RF-02]               │  tipo transazione
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  4. PULIZIA DESCRIZIONI   │  LLM estrae controparte
                     │     [RF-02 pre-cat.]      │  (pagante o ricevente)
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  5. DEDUP CHECK           │  salta tx già importate
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  6. RILEVAMENTO           │  accoppiamento importo+data
                     │     GIROCONTI [RF-04]     │  o nome proprietario
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  7. RICONCILIAZIONE       │  carta di credito ↔ addebito
                     │     CARTE [RF-03]         │  su conto corrente
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  8. CATEGORIZZAZIONE      │  regole → LLM → fallback
                     │     [RF-05]               │
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  9. PERSISTENZA DB        │  upsert idempotente
                     │     [RF-06, RF-07]        │
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  10. REVISIONE MANUALE    │  to_review=True → utente
                     │      + REGOLE [RF-08]     │  → riapplica regole
                     └──────────────────────────┘
```

---

## Stadio 1 — Caricamento file

**Modulo:** `core/normalizer.py` → `load_raw_dataframe()`

```
detect_encoding(raw_bytes)
  └─ chardet → alias normalizzato (ascii → utf-8)

Per XLSX / XLS:
  detect_best_sheet(workbook)
    └─ esclude fogli con nome summary/totale/riepilogo
    └─ punteggio = n_righe + (n_colonne_numeriche × 10)
  pd.read_excel(sheet)

Per CSV / testo:
  detect_delimiter(content)
    └─ frequenza dei caratteri [, ; | TAB] → vince il più frequente
  detect_header_row(lines)
    └─ prima riga con ≥ 2 campi non-numerici e non-vuoti
  pd.read_csv(sep=delimiter, skiprows=skip_rows)
```

**Output:** `DataFrame` grezzo con colonne originali

---

## Stadio 2 — Schema decision / Classificazione documento [RF-01]

**Modulo:** `core/orchestrator.py`, `core/classifier.py`

### Flow 1 — schema già in DB

```
_schema_is_usable(known_schema)
  └─ richiede: date_col AND (amount_col OR (debit_col AND credit_col))
  └─ se valido → salta classificazione
```

### Flow 2 — sorgente nuova, LLM richiesto

```
classify_document(df_raw, llm_backend)

  FASE 0 — Python, deterministico
    └─ Sinonimi colonne (nessuna LLM):
       data_col  → data, date, data operazione, buchungsdatum, …
       amount_col → importo, amount, betrag, montant, …
       debit/credit → dare/avere, addebiti/accrediti, uscite/entrate, …
       description → descrizione, causale, memo, payee, …

  FASE 0.5 — Ispezione segni
    └─ Se amount_col semantica "neutral":
       legge dati reali → se qualsiasi valore < 0 → invert_sign=False certo

  FASE 1 — LLM, campi ambigui
    input:
      - nomi colonne
      - prime 20 righe (dati sensibili redatti)
      - risultati Fase 0 (come fatti certi)
    output JSON:
      {
        doc_type:   bank_account | credit_card | debit_card | prepaid_card | savings | unknown
        date_format: strptime pattern (es. %d/%m/%Y)
        sign_convention: signed_single | debit_positive | credit_negative
        invert_sign: true/false  (carte: spese tipicamente positive nel CSV)
        internal_transfer_patterns: ["bonifico", "giroconto", …]
      }

  POST-LLM — Fase 0 prevale su LLM
    └─ merge: i risultati certi della Fase 0 sovrascrivono il LLM
    └─ safety: se doc_type = carta → invert_sign=True forzato
```

**Output:** `DocumentSchema` con mappatura colonne e convenzioni segno

---

## Stadio 3 — Normalizzazione [RF-02]

**Modulo:** `core/normalizer.py` → `_normalize_df_with_schema()`

Per ogni riga del DataFrame:

```
parse_date_safe(valore, formato)
  └─ prova formato schema → fallback a formati comuni IT/ISO/US
  └─ None se fallisce (riga scartata)

apply_sign_convention(riga, convention)
  ├─ signed_single:    usa amount_col così com'è
  ├─ debit_positive:   credito − debito  (entrambi positivi nel CSV)
  └─ credit_negative:  credito as-is, −|debito|

parse_amount(valore)
  ├─ "1.234,56" (EU)  → 1234.56
  ├─ "1,234.56" (US)  → 1234.56
  └─ "1234,56"        → 1234.56

normalize_description(testo)
  └─ NFC unicode + casefold + strip

compute_transaction_id(account_label, data_raw, importo_raw, descrizione_raw)
  └─ SHA-256[:24] su valori GREZZI
  └─ stabile tra versioni di normalizzazione

_infer_tx_type(importo, doc_type, descrizione, pattern_interni)
  ├─ matcha pattern_interni → internal_out (< 0) / internal_in (≥ 0)
  ├─ carta di credito/debito/prepagata → card_tx
  └─ altrimenti: income (≥ 0) / expense (< 0)
```

**Dedup intra-file:**
```
Righe con stessa (account_label + data + importo + descrizione)
  → somma importi, ricalcola hash
  (evita doppio conteggio se la stessa tx appare più volte nell'export)
```

**Rimozione riga saldo carta:**
```
remove_card_balance_row(txs, epsilon)
  └─ rileva la riga il cui |importo| ≈ Σ|altri importi|
  └─ con owner_label → rinomina descrizione (il rilevamento giroconti la cattura)
  └─ senza owner_label → rimuove la riga
```

**Output:** lista `dict` transazioni con tutti i campi canonici, `raw_description` immutabile

---

## Stadio 4 — Pulizia descrizioni [RF-02, pre-categorizzazione]

**Modulo:** `core/description_cleaner.py` → `clean_descriptions_batch()`

Estrae il nome della **controparte** dalla stringa grezza della banca.

```
Divisione per segno:
  spese (importo < 0) → PASS 1: estrai DESTINATARIO
  entrate (importo ≥ 0) → PASS 2: estrai MITTENTE

Privacy (obbligatoria prima di ogni LLM call):
  redact_pii(descrizione, sanitize_config)
    ├─ Nomi proprietari → nomi fittizi plausibili (pool per lingua)
    │    IT: Carlo Brambilla, Marta Pellegrino, …
    │    EN: James Fletcher, Helen Norris, …
    │    DE: Klaus Hartmann, Monika Braun, …
    │    FR: Pierre Dumont, Claire Lebrun, …
    ├─ IBAN → <ACCOUNT_ID>
    ├─ PAN / carta (13-19 cifre) → <CARD_ID>
    ├─ Carta mascherata (****0178) → <CARD_ID>
    ├─ Codici transazione (CAU, NDS, CRO, RIF, TRN…) → <TX_CODE>
    └─ Codice fiscale → <FISCAL_ID>

  LLM elabora descrizione redatta

  restore_owner_placeholders(risultato_llm)
    └─ riporta i nomi fittizi → nomi reali del proprietario
```

**Cosa il LLM deve eliminare:**
```
- Etichette tipo pagamento: POS, Bonifico, Virement, Lastschrift, SCT, wire transfer
- Marcatori beneficiario: Fv., F.V., Beg., Begünstigter, Pour, For the benefit of
- VOSTRA DISPOSIZIONE, Disposizione
- Importi e valute: "352,00 EUR", "9.798,76 EUR"
- Date: "23.12.2025", "2025-12-29", "29/10.41"
- Numeri carta, codici auth (CAU/NDS), riferimenti (RIF:/CRO:/INV/)
- Token ORD., codici paese (ITA)(FRA)
- Città dopo il nome dell'azienda
- Frasi duplicate: "Rimborso spese rimborso spese" → "Rimborso spese"
```

**Spese origine bancaria** (nessuna controparte esterna):
```
→ etichetta nella lingua configurata:
   IT: "Interessi bancari", "Commissioni bancarie"
   EN: "Bank fees", "Bank interest"
   FR: "Frais bancaires", "Intérêts bancaires"
   DE: "Bankgebühren", "Bankzinsen"
```

**Fallback:** se LLM fallisce → mantieni `raw_description` originale

**Output:** `transaction["description"]` aggiornato; `raw_description` mai modificato

---

## Stadio 5 — Dedup check

**Modulo:** `db/repository.py` → `get_existing_tx_ids()`

```
existing_ids = query DB WHERE id IN (tutti_gli_id_del_batch)
→ filtra le tx già presenti
→ se tutte presenti → abort early (file già importato)
```

---

## Stadio 6 — Rilevamento giroconti [RF-04]

**Modulo:** `core/normalizer.py` → `detect_internal_transfers()`

```
FASE 1 — Accoppiamento tra conti diversi
  Per ogni coppia (i, j) con i.account_label ≠ j.account_label:

    amount_match = |importo_i + importo_j| ≤ epsilon          (0.01 €)
    date_match   = |data_i − data_j| ≤ delta_days             (5 gg)

    Se entrambi:
      high_symmetry = |importo_i + importo_j| ≤ epsilon_strict (0.005 €)
                    AND |data_i − data_j| ≤ delta_days_strict  (1 gg)

      Confidenza:
        HIGH   → keyword "bonifico/giroconto/transfer/…" in descrizione
        MEDIUM → high_symmetry senza keyword

      Se require_keyword_confirmation=True AND confidenza=MEDIUM:
        → segna transfer_pair_id ma NON aggiorna tx_type (to_review)
      Altrimenti:
        → aggiorna tx_type: internal_out (uscita) / internal_in (entrata)

FASE 2 — Match per nome proprietario (tx non ancora accoppiate)
  Per ogni tx senza coppia:
    Se la descrizione contiene un nome proprietario
    (regex con tutte le permutazioni dei token del nome):
      → tx_type = internal_out / internal_in
      → transfer_confidence = HIGH
      (il proprietario è la controparte: nessun accoppiamento necessario)
```

**Parametri chiave:**

| Parametro | Default | Significato |
|-----------|---------|-------------|
| `tolerance` | 0.01 € | epsilon importo |
| `tolerance_strict` | 0.005 € | epsilon strict |
| `settlement_days` | 5 gg | finestra date |
| `settlement_days_strict` | 1 gg | finestra strict |

---

## Stadio 7 — Riconciliazione carte [RF-03]

**Modulo:** `core/normalizer.py` → `find_card_settlement_matches()`

Abbina gli addebiti `card_settlement` (dal conto corrente) alle singole `card_tx` (dalla carta).

```
Per ogni addebito:

  FASE 1 — Finestra temporale
    └─ card_tx in [data_addebito − 45 gg, data_addebito + 7 gg]

  FASE 2 — Sliding window (sottoinsiemi contigui)
    Per ogni sottoinsieme contiguo [i..j]:
      ├─ verifica gap tra tx consecutive ≤ max_gap_days (5 gg)
      ├─ somma = Σ |importo[i..j]|
      └─ Se |somma − importo_addebito| ≤ epsilon → MATCH ✓

  FASE 3 — Subset sum ai bordi (fallback)
    ├─ prende le k=10 tx prima + k=10 tx dopo la data addebito
    ├─ ricerca esaustiva su tutti i sottoinsiemi (n ≤ 20 → 2^20 ≈ 1M, sicuro)
    └─ Se qualsiasi sottoinsieme somma all'importo → MATCH ✓

  Se MATCH trovato:
    → ReconciliationLink {settlement_id, matched_ids, delta, method}
    → tx matched: reconciled=True
```

---

## Stadio 8 — Categorizzazione [RF-05]

**Modulo:** `core/categorizer.py` → `categorize_batch()`

Elabora solo `expense`, `income`, `card_tx`, `unknown`. Salta giroconti e card_settlement.

```
Per ogni transazione — cascata a 4 livelli:

  LIVELLO 0 — Regole utente (CategoryRule, ordinate per priorità)
  ──────────────────────────────────────────────────────────────
  Per ogni regola (in ordine di priorità decrescente):
    CategoryRule.matches(descrizione, doc_type):
      ├─ exact:    descrizione.casefold() == pattern.casefold()
      ├─ contains: pattern.casefold() IN descrizione.casefold()
      └─ regex:    re.search(pattern, descrizione.casefold())

    Se doc_type specificato nella regola → deve coincidere

    VINCE la prima regola che fa match →
      categoria, sottocategoria, confidenza=HIGH, sorgente=rule, to_review=False

  LIVELLO 1 — Regole statiche per parola chiave (direction-aware)
  ───────────────────────────────────────────────────────────────
  Pattern hardcoded, separati per spese/entrate:

  SPESE:
    conad|coop|esselunga|lidl|carrefour|…  → Alimentari / Spesa supermercato
    farmacia|pharma|…                      → Salute / Farmaci
    eni|shell|q8|tamoil|…                  → Trasporti / Carburante
    telepass|autostrad|…                   → Trasporti / Parcheggio e ZTL
    trenitalia|italo|frecciarossa|…        → Trasporti / Trasporto pubblico
    enel|iren|a2a|hera|…                   → Casa / Energia elettrica
    netflix|spotify|amazon prime|…         → Svago / Streaming

  ENTRATE:
    stipendio|salary|busta paga|…          → Lavoro dipendente / Stipendio
    pensione|inps rendita|…                → Prestazioni sociali / Pensione

    → confidenza=HIGH, sorgente=rule, to_review=False

  LIVELLO 2 — Modello ML (stub)
  ──────────────────────────────
  → restituisce None (riservato a sviluppi futuri)

  LIVELLO 3 — LLM (due batch direzionali)
  ────────────────────────────────────────
  Batch separati per spese ed entrate.

  Privacy:
    redact_pii(descrizione) prima di inviare al LLM

  Payload per ogni tx:
    {"amount": "−352.00", "description": "Notorious Cinemas"}

  Risposta attesa:
    {
      "results": [
        {
          "category": "Svago e tempo libero",
          "subcategory": "Cinema e teatro",
          "confidence": "high",
          "rationale": "Cinema"
        },
        …
      ]
    }

  Validazione risposta LLM:
    ├─ categoria + sottocategoria valida nella tassonomia?
    ├─ direzione corretta (spesa per spese, entrata per entrate)?
    ├─ Se sottocategoria non trovata → cerca categoria padre
    ├─ Se categoria non trovata → primo sub valido per quella categoria
    └─ Se correzione necessaria → confidenza=low, to_review=True

  Livelli confidenza:
    HIGH   → to_review=False
    MEDIUM → to_review=False (sopra threshold 0.80)
    LOW    → to_review=True

  LIVELLO 4 — Fallback (tutto fallisce)
  ──────────────────────────────────────
  spese:   categoria=Altro,         sub=Spese non classificate
  entrate: categoria=Altro entrate, sub=Entrate non classificate
  confidenza=LOW, sorgente=llm, to_review=True
```

---

## Stadio 9 — Persistenza DB [RF-06, RF-07]

**Modulo:** `db/repository.py` → `persist_import_result()`

Tutto in una transazione atomica, ogni operazione è idempotente:

```
create_import_batch(sha256, filename, flow_used, n_transactions)
  └─ se sha256 esiste già → return existing (file già importato)

upsert_document_schema(schema)
  └─ se source_identifier esiste → aggiorna; altrimenti crea

Per ogni transazione:
  upsert_transaction(tx)
    └─ se tx.id esiste → skip (dedup finale)
    └─ altrimenti: INSERT con tutti i campi

Per ogni riconciliazione:
  create_reconciliation_link(settlement_id, detail_id, delta, method)
  update tx: reconciled=True

Per ogni giroconto:
  create_transfer_link(out_id, in_id, confidence, keyword_matched)

session.commit()
```

---

## Stadio 10 — Revisione manuale e regole [RF-08]

**Pagina:** `ui/review_page.py`

```
Auto-applicazione regole (ad ogni caricamento pagina):
  apply_rules_to_review_transactions(session, user_rules)
    └─ per ogni tx con to_review=True:
       └─ prima regola che fa match →
          categoria, sorgente=rule, to_review=False

Pulsante "Rielabora con LLM":
  _rerun_llm_on_review(engine)
    └─ carica tutte le tx con to_review=True
       (esclusi giroconti e card_settlement)
    └─ ri-esegue clean_descriptions_batch()
    └─ ri-esegue categorize_batch()
       (salta le tx con category_source=manual o rule)

Correzione manuale:
  update_transaction_category(tx_id, categoria, sotto)
    └─ category_source=manual, to_review=False

Creazione regola:
  create_category_rule(pattern, match_type, categoria, sotto, priorità)
    └─ si propaga immediatamente a tutte le tx simili

Bulk edit descrizione:
  _apply_description_rule_bulk(engine, pattern, match_type, nuova_desc)
    └─ aggiorna description per tutte le tx con raw_description matching
    └─ ri-categorizza con LLM
```

---

## Tabella riepilogativa sorgenti di categoria

| Sorgente (`category_source`) | Significato | `to_review` |
|------------------------------|-------------|-------------|
| `rule` | Regola utente o keyword statica | `False` |
| `llm` confidenza HIGH/MEDIUM | LLM sopra threshold | `False` |
| `llm` confidenza LOW | LLM sotto threshold | `True` |
| `manual` | Correzione manuale utente | `False` |
| `llm` fallback (Altro) | Tutto fallito | `True` |

---

## Parametri globali di configurazione

| Parametro | Default | Dove si imposta |
|-----------|---------|-----------------|
| `llm_backend` | `local_ollama` | Impostazioni |
| `description_language` | `it` | Impostazioni |
| `confidence_threshold` | 0.80 | `ProcessingConfig` |
| `tolerance` (importo trasferimento) | 0.01 € | `ProcessingConfig` |
| `settlement_days` | 5 gg | `ProcessingConfig` |
| `window_days` (riconciliazione carte) | 45 gg | `ProcessingConfig` |
| `require_keyword_confirmation` | `True` | `ProcessingConfig` |
| `owner_names` | — | Impostazioni |
| `batch_size` (LLM) | 20 tx/chiamata | `categorize_batch()` |
