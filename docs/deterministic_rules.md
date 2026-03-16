# Spendify — Strumenti deterministici

> Inventario completo di tutte le regole, algoritmi e trasformazioni **non-LLM** implementate nel sistema.
> Per ogni strumento: dove si trova, cosa fa, le regole hardcoded e il punto di applicazione nella pipeline.

---

## Mappa nella pipeline

```
FILE (CSV / XLSX)
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│  1. RILEVAMENTO FORMATO                                      │ ◄─ DETERMINISTICO
│     detect_encoding · detect_delimiter                       │
│     detect_header_row · detect_best_sheet                    │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  1b. PRE-PROCESSING Phase 0                                  │ ◄─ DETERMINISTICO
│     detect_and_strip_preheader_rows                          │
│     drop_low_variability_columns                             │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  2. CLASSIFICAZIONE DOCUMENTO — Fase 0                       │ ◄─ DETERMINISTICO
│     sinonimi colonne · ispezione segni                       │
└────────────────────────────┬────────────────────────────────┘
                             │ LLM per campi ambigui (Fase 1)
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  3. NORMALIZZAZIONE                                          │ ◄─ DETERMINISTICO
│     parse_date_safe · parse_amount · apply_sign_convention   │
│     normalize_description · compute_transaction_id (SHA-256)│
│     _infer_tx_type · remove_card_balance_row                 │
└────────────────────────────┬────────────────────────────────┘
                             │  ID calcolato qui da valori grezzi
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  4. DEDUP CHECK                                              │ ◄─ DETERMINISTICO
│     get_existing_tx_ids (repository.py)                      │
│     → abort se tutte già in DB, zero LLM calls sprecate      │
└────────────────────────────┬────────────────────────────────┘
                             │  solo tx nuove proseguono
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  5. PULIZIA DESCRIZIONI                                      │
│     PRIVACY / PII REDACTION  ◄─ DETERMINISTICO              │
│     redact_pii · restore_owner_placeholders                  │
│     (applicato PRIMA e DOPO ogni chiamata LLM)               │
│                              ◄─ LLM (estrazione controparte) │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  6. RILEVAMENTO GIROCONTI [RF-04]                            │ ◄─ DETERMINISTICO
│     detect_internal_transfers                                │
│     Fase 1: accoppiamento importo+data                       │
│     Fase 2: matching nome proprietario                       │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  7. RICONCILIAZIONE CARTE [RF-03]                            │ ◄─ DETERMINISTICO
│     find_card_settlement_matches                             │
│     sliding window · subset sum                              │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  8. CATEGORIZZAZIONE — Livello 0 e 1                         │ ◄─ DETERMINISTICO
│     Liv. 0: regole utente (CategoryRule.matches)             │
│     Liv. 1: regole statiche per parola chiave                │
└────────────────────────────┬────────────────────────────────┘
                             │ LLM solo se nessuna regola fa match
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  9. PERSISTENZA DB                                           │ ◄─ DETERMINISTICO
│     upsert idempotente · SHA-256 per file e transazione      │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  10. REVISIONE — auto-applicazione regole                    │ ◄─ DETERMINISTICO
│      apply_rules_to_review_transactions  (to_review=True)    │
│      apply_all_rules_to_all_transactions (tutte le tx)       │
│      bulk description rules · DescriptionRule                │
└─────────────────────────────────────────────────────────────┘
```

---

## 1 — Rilevamento formato file

**Modulo:** `core/normalizer.py`
**Quando:** stadio 1, prima di qualsiasi parsing

| Funzione | Regola hardcoded |
|----------|-----------------|
| `detect_encoding(raw_bytes)` | chardet → normalizza alias (`ascii` → `utf-8`) |
| `detect_delimiter(content)` | conta frequenza di `,` `;` `\t` `\|` → vince il più frequente |
| `detect_header_row(lines)` | prima riga con ≥ 2 campi non-numerici; pattern numerico: `^[\d\.\,\-\+\s€$£%]+$` |
| `detect_best_sheet(workbook)` | esclude fogli con nome `summary\|totale\|riepilogo`; punteggio = righe + (colonne numeriche × 10) |

---

## 2 — Classificazione documento — Fase 0

**Modulo:** `core/classifier.py`
**Quando:** stadio 2 (Flow 2), solo se sorgente senza schema in DB

Risolve i campi colonna **senza LLM** tramite sinonimi:

| Campo | Sinonimi riconosciuti |
|-------|-----------------------|
| `date_col` | data, date, data operazione, booking date, buchungsdatum, … |
| `amount_col` | importo, amount, betrag, montant, somme, … |
| `debit_col` | dare, addebiti, uscite, debit, ausgaben, … |
| `credit_col` | avere, accrediti, entrate, credit, einnahmen, … |
| `description_col` | descrizione, causale, memo, payee, bezeichnung, libellé, … |

**Ispezione segni (Fase 0.5):**
Se `amount_col` semantica "neutral" → legge i dati reali; se qualsiasi valore < 0 → `invert_sign=False` certo, nessun LLM necessario.

---

## 3 — Normalizzazione

**Modulo:** `core/normalizer.py`, `core/orchestrator.py`
**Quando:** stadio 3, dopo classificazione schema

### 3a — Parsing data

**`parse_date_safe(valore, formato)`**

1. Prova il formato dello schema
2. Fallback su formati comuni (in ordine):
   `%d/%m/%Y` · `%d-%m-%Y` · `%d/%m/%y` · `%d-%m-%y` · `%Y-%m-%d` · `%Y/%m/%d` · `%m/%d/%Y` · `%m/%d/%y`
3. Restituisce `None` se tutto fallisce (riga scartata)

### 3b — Parsing importo

**`parse_amount(valore)`**

```
Strip simboli: €  $  £  (spazi)

Euristica separatori:
  "1.234,56"  → punto = migliaia, virgola = decimale → 1234.56
  "1,234.56"  → virgola = migliaia, punto = decimale → 1234.56
  "1234,56"   → virgola sola con ≤ 2 decimali       → 1234.56
  "1234.56"   → punto sola con ≤ 2 decimali          → 1234.56
```

### 3c — Convenzione di segno

**`apply_sign_convention(riga, convention)`**

| Convenzione | Regola |
|-------------|--------|
| `signed_single` | usa `amount_col` così com'è |
| `debit_positive` | `credito − debito` (entrambi positivi nel CSV) |
| `credit_negative` | credito as-is positivo; debito negato |

Dopo: se `invert_sign=True` (tipico per carte) → moltiplica per −1.

### 3d — Normalizzazione descrizione

**`normalize_description(testo)`**
`unicodedata.normalize("NFC", testo).casefold().strip()`
Garantisce confronti case-insensitive stabili; non modifica mai `raw_description`.

### 3e — Identificatore transazione (idempotency key)

**`compute_transaction_id(account_label, data, importo, descrizione)`**
SHA-256[:24] della stringa: `{account_label}|{data ISO}|{importo}|{descrizione_raw}`
Usato su **valori grezzi** → stabile tra versioni di normalizzazione.

**`compute_file_hash(raw_bytes)`**
SHA-256 completo del file → dedup a livello di importazione.

### 3f — Inferenza tipo transazione

**`_infer_tx_type(importo, doc_type, descrizione, pattern_interni)`**

```
1. descrizione matcha pattern_interni (lista da DB) → internal_out / internal_in
2. doc_type in {credit_card, debit_card, prepaid_card}  → card_tx
3. importo ≥ 0                                          → income
4. importo < 0                                          → expense
```

### 3g — Rimozione riga saldo carta

**`remove_card_balance_row(txs, epsilon, owner_label)`**
Rileva la riga il cui `|importo| ≈ Σ|altri importi|` (entro epsilon 0.01 €).
Con `owner_label` → rinomina la descrizione (il rilevamento giroconti la cattura).
Senza `owner_label` → rimuove la riga (evita doppio conteggio).

---

## 4 — Dedup check

**Modulo:** `db/repository.py` → `get_existing_tx_ids()`
**Quando:** stadio 4, dopo normalizzazione e **prima** della pulizia descrizioni (LLM)
**Perché:** l'ID SHA-256 è calcolato al passo 3 da valori grezzi → possiamo scartare i duplicati senza sprecare token LLM

```
ids_esistenti = SELECT id FROM transaction WHERE id IN (tutti_gli_id_del_batch)
→ filtra le tx già presenti
→ se tutte presenti → abort early (file già importato)
```

---

## 5 — Privacy / PII Redaction

**Modulo:** `core/sanitizer.py`
**Quando:** PRIMA di ogni chiamata LLM (pulizia descrizioni + categorizzazione); DOPO per il ripristino nomi proprietari

### Regole di redazione

| Pattern | Regex | Sostituito con |
|---------|-------|---------------|
| IBAN | `[A-Z]{2}\d{2}[A-Z0-9]{4,30}` | `<ACCOUNT_ID>` |
| PAN / carta (13-19 cifre) | `\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,7}` | `<CARD_ID>` |
| Carta mascherata | `[\*X]{4}[\s\-]?\d{4}` | `<CARD_ID>` |
| Codici transazione | `(CAU\|NDS\|TRN\|CRO\|RIF\|ID TRANSAZIONE)\s*[\d\-]+` | `<TX_CODE>` |
| Codice fiscale IT | `[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]` | `<FISCAL_ID>` |
| Pattern aggiuntivi utente | configurabile | `<REDACTED>` |

### Nomi proprietari → nomi fittizi (per LLM)

I nomi reali vengono sostituiti con nomi **plausibili ma falsi** (il LLM può ancora riconoscerli come persone ed estrarli correttamente). Dopo la risposta LLM, `restore_owner_placeholders()` rimette i nomi reali.

| Lingua | Pool nomi fittizi |
|--------|------------------|
| IT | Carlo Brambilla, Marta Pellegrino, Alberto Marini, Giovanna Ferrara, … |
| EN | James Fletcher, Helen Norris, David Lawson, Susan Palmer, … |
| DE | Klaus Hartmann, Monika Braun, Stefan Richter, Ingrid Weber, … |
| FR | Pierre Dumont, Claire Lebrun, Michel Garnier, Sophie Renard, … |
| ES | Carlos Navarro, Elena Vega, Miguel Torres, Isabel Molina, … |

**Guardia finale:** `assert_sanitized(testo)` → lancia `ValueError` se IBAN o PAN ancora presenti.

---

## 6 — Rilevamento giroconti [RF-04]

**Modulo:** `core/normalizer.py` → `detect_internal_transfers()`
**Quando:** stadio 6, dopo dedup

### Fase 1 — Accoppiamento importo + data

```
Per ogni coppia (i, j) con account_label_i ≠ account_label_j:

  amount_match = |importo_i + importo_j| ≤ epsilon
  date_match   = |data_i − data_j| ≤ delta_days

  Se entrambi verificati:
    high_symmetry = amount ≤ epsilon_strict AND date ≤ delta_days_strict

    Confidenza:
      HIGH   → keyword della lista pattern_interni trovata in descrizione
      MEDIUM → high_symmetry senza keyword

    Se require_keyword_confirmation=True AND confidenza=MEDIUM:
      → segna transfer_pair_id, NON aggiorna tx_type (va in revisione)
    Altrimenti:
      → tx_type: internal_out (uscita) / internal_in (entrata)
```

### Fase 2 — Matching nome proprietario

```
Per ogni tx non ancora accoppiata:
  Se descrizione contiene un nome proprietario
  (regex con tutte le permutazioni dei token del nome):
    → tx_type = internal_out / internal_in
    → transfer_confidence = HIGH
```

### Parametri chiave

| Parametro | Default |
|-----------|---------|
| `epsilon` | 0.01 € |
| `epsilon_strict` | 0.005 € |
| `delta_days` | 5 giorni |
| `delta_days_strict` | 1 giorno |

---

## 7 — Riconciliazione carte [RF-03]

**Modulo:** `core/normalizer.py` → `find_card_settlement_matches()`
**Quando:** stadio 7, abbina `card_settlement` (conto corrente) con `card_tx` (carta)

### Fase 1 — Finestra temporale
```
card_tx in [data_addebito − 45 giorni, data_addebito + 7 giorni]
```

### Fase 2 — Sliding window (sottoinsiemi contigui)
```
Per ogni sottoinsieme contiguo [i..j]:
  verifica: gap tra tx consecutive ≤ max_gap_days (5 gg)
  somma = Σ |importo[i..j]|
  Se |somma − importo_addebito| ≤ epsilon → MATCH ✓
```

### Fase 3 — Subset sum ai bordi (fallback)
```
Prende k=10 tx prima + k=10 dopo la data addebito (max 20 tx)
Ricerca esaustiva: tutti i sottoinsiemi → 2^20 ≈ 1M combinazioni (sicuro)
Prima combinazione che somma all'importo → MATCH ✓
```

---

## 8 — Categorizzazione — livelli deterministici

**Modulo:** `core/categorizer.py`
**Quando:** stadio 8, prima del LLM (livelli 0 e 1)

### Livello 0 — Regole utente (CategoryRule)

Salvate in DB, ordinate per priorità decrescente. **Prima che fa match vince.**

**`CategoryRule.matches(descrizione, doc_type)`:**

| Tipo | Logica |
|------|--------|
| `exact` | `descrizione.casefold() == pattern.casefold()` |
| `contains` | `pattern.casefold() IN descrizione.casefold()` |
| `regex` | `re.search(pattern, descrizione, IGNORECASE)` |

Se `doc_type` specificato nella regola → deve coincidere con quello della transazione.

### Livello 1 — Regole statiche per parola chiave

Hardcoded nel codice, direction-aware (spese/entrate separate):

**SPESE:**

| Pattern (regex, case-insensitive) | Categoria | Sottocategoria |
|-----------------------------------|-----------|----------------|
| `conad\|coop\|esselunga\|lidl\|carrefour\|eurospin\|aldi\|penny\|pam` | Alimentari | Spesa supermercato |
| `farmacia\|pharma` | Salute | Farmaci |
| `eni\|shell\|q8\|tamoil\|ip\|api\|agip` | Trasporti | Carburante |
| `telepass\|autostrad` | Trasporti | Parcheggio / ZTL |
| `trenitalia\|italo\|frecciarossa\|frecciargento` | Trasporti | Trasporto pubblico |
| `enel\|iren\|a2a\|hera\|eni gas` | Casa | Energia elettrica |
| `netflix\|spotify\|amazon prime\|disney+\|apple tv` | Svago | Streaming / abbonamenti digitali |
| `commissione\|canone conto\|spese tenuta` | Finanza | Commissioni bancarie |

**ENTRATE:**

| Pattern | Categoria | Sottocategoria |
|---------|-----------|----------------|
| `stipendio\|salary\|busta paga` | Lavoro dipendente | Stipendio |
| `pensione\|inps rendita` | Prestazioni sociali | Pensione / rendita |

---

## 9 — Persistenza DB

**Modulo:** `db/repository.py`
**Quando:** stadio 9, tutto idempotente

| Funzione | Regola idempotenza |
|----------|--------------------|
| `upsert_transaction(tx)` | se `tx.id` esiste → skip |
| `create_import_batch(sha256)` | se sha256 esiste → return existing |
| `upsert_document_schema(schema)` | se `source_identifier` esiste → aggiorna |
| `create_reconciliation_link(sid, did)` | se coppia `(sid, did)` esiste → skip |
| `create_transfer_link(out_id, in_id)` | se coppia esiste → skip |
| `update_transaction_category()` | imposta sempre: `confidence=high`, `source=manual`, `to_review=False` |

---

## 10 — Revisione manuale — strumenti deterministici

**Modulo:** `db/repository.py`, `ui/review_page.py`

### Auto-applicazione regole (pagina Review)

**`apply_rules_to_review_transactions(session, user_rules)`**
Ad ogni caricamento della pagina Review:
```
Per ogni tx con to_review=True:
  Per ogni regola (ordinate per priorità DESC):
    Se regola.matches(tx.description, tx.doc_type):
      → aggiorna categoria, source=rule, to_review=False
      → passa alla tx successiva
```

### Esegui tutte le regole (pagina Regole)

**`apply_all_rules_to_all_transactions(session, user_rules)`**
Pulsante "▶️ Esegui tutte le regole" nella pagina Regole:
```
Applica tutte le regole a TUTTE le transazioni (non solo to_review=True):
  Regole ordinate per priorità DESC
  Per ogni tx:
    Per ogni regola:
      Se regola.matches(tx.description, tx.doc_type):
        → aggiorna categoria, subcategory, source=rule, confidence=high
        → se tx.to_review=True → imposta to_review=False (n_cleared++)
        → passa alla tx successiva (primo match vince)
  Restituisce (n_matched, n_cleared_review)
```
Richiede conferma tramite checkbox prima dell'esecuzione.

### DescriptionRule — regole di correzione descrizione in blocco

Salvate in DB (`description_rule`). Pattern su `raw_description`:

| Tipo | Logica |
|------|--------|
| `exact` | `raw_description.lower() == pattern.lower()` |
| `contains` | `pattern.lower() IN raw_description.lower()` |
| `regex` | `re.search(pattern, raw_description, IGNORECASE)` |

Applicazione: aggiorna `description` → ri-categorizza con LLM.

---

## 11 — Analytics — soglie e filtri

**Modulo:** `ui/analytics_page.py`

### Tipi esclusi dai grafici

```python
EXCLUDED = {"internal_out", "internal_in", "card_settlement", "aggregate_debit"}
```

### Benchmark di spesa (confronto ISTAT)

Soglie applicate per ogni categoria rispetto al benchmark familiare di riferimento:

| Segnale | Condizione | Icona |
|---------|-----------|-------|
| Spesa anomala alta | spesa > **1.5 ×** benchmark | 🔴 |
| Spesa anomala bassa | spesa < **0.5 ×** benchmark | 🔵 |
| Spesa normale | tra 0.5× e 1.5× | 🟢 |
| Assente | nessuna spesa in categoria | ⚪ |

---

## Riepilogo — Tutti gli strumenti per stadio pipeline

| Stadio | Strumento | Modulo | LLM? |
|--------|-----------|--------|------|
| 1. Formato file | detect_encoding / detect_delimiter / detect_header_row / detect_best_sheet | normalizer.py | ✗ |
| 1b. Pre-processing | detect_and_strip_preheader_rows / drop_low_variability_columns | normalizer.py | ✗ |
| 2. Schema — Fase 0 | sinonimi colonne, ispezione segni | classifier.py | ✗ |
| 2. Schema — Fase 1 | classificazione doc_type, date_format, sign_convention | classifier.py | ✓ LLM |
| 3. Normalizzazione | parse_date_safe / parse_amount / apply_sign_convention / normalize_description / compute_transaction_id / _infer_tx_type / remove_card_balance_row | normalizer.py + orchestrator.py | ✗ |
| 4. Dedup | get_existing_tx_ids | repository.py | ✗ |
| 5. Privacy | redact_pii / restore_owner_placeholders | sanitizer.py | ✗ |
| 5. Pulizia descrizioni | clean_descriptions_batch | description_cleaner.py | ✓ LLM |
| 6. Giroconti | detect_internal_transfers (Fase 1 + Fase 2) | normalizer.py | ✗ |
| 7. Riconciliazione carte | find_card_settlement_matches (3 fasi) | normalizer.py | ✗ |
| 8. Categorizzazione Liv. 0 | CategoryRule.matches (regole utente) | categorizer.py | ✗ |
| 8. Categorizzazione Liv. 1 | _apply_static_rules (keyword hardcoded) | categorizer.py | ✗ |
| 8. Categorizzazione Liv. 3 | categorize_batch (LLM) | categorizer.py | ✓ LLM |
| 9. Persistenza | upsert_transaction / persist_import_result | repository.py | ✗ |
| 10. Auto-regole | apply_rules_to_review_transactions | repository.py | ✗ |
| 10. Esegui tutte le regole | apply_all_rules_to_all_transactions | repository.py | ✗ |
| 10. Bulk descrizioni | DescriptionRule + _apply_description_rule_bulk | repository.py + review_page.py | ✓ LLM (ri-cat.) |
| Analytics | EXCLUDED / benchmark ISTAT 0.5×–1.5× | analytics_page.py | ✗ |

---

## Parametri globali di configurazione

Tutti i default sono in `ProcessingConfig` (`core/orchestrator.py`):

| Parametro | Default | Usato da |
|-----------|---------|---------|
| `tolerance` | 0.01 € | rilevamento giroconti, riconciliazione carte |
| `tolerance_strict` | 0.005 € | giroconti high-symmetry |
| `settlement_days` | 5 gg | finestra accoppiamento giroconti |
| `settlement_days_strict` | 1 gg | finestra strict giroconti |
| `window_days` | 45 gg | finestra temporale riconciliazione carte |
| `max_gap_days` | 5 gg | sliding window carte |
| `boundary_pre_post` | 10 tx | subset sum riconciliazione |
| `confidence_threshold` | 0.80 | soglia LLM → to_review |
| `require_keyword_confirmation` | True | giroconti medium → to_review se no keyword |
| `batch_size` (descrizioni) | 30 tx/call | clean_descriptions_batch |
| `batch_size` (categorie) | 20 tx/call | categorize_batch |
