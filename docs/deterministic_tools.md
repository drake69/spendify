# Spendify — Strumenti deterministici

> Inventario completo di tutti gli algoritmi, regole e trasformazioni che non usano LLM.
> Ogni voce indica: dove si trova nel codice, a quale stadio della pipeline è applicata, se è automatica o richiede azione utente.

---

## Mappa di posizionamento nella pipeline

```
FILE (CSV / XLSX)
     │
     ▼  ── STADIO 1: CARICAMENTO ──────────────────────────────────────────
     │   detect_encoding          (normalizer.py)
     │   detect_delimiter         (normalizer.py)  [solo CSV]
     │   detect_header_row        (normalizer.py)  [solo CSV]
     │   detect_best_sheet        (normalizer.py)  [solo Excel]
     │
     ▼  ── STADIO 1b: PRE-PROCESSING (Phase 0) ────────────────────────────
     │   detect_and_strip_preheader_rows (normalizer.py)  [CSV + Excel]
     │   drop_low_variability_columns    (normalizer.py)  [CSV + Excel]
     │
     ▼  ── STADIO 2: SCHEMA / CLASSIFICAZIONE ─────────────────────────────
     │   compute_columns_key      (normalizer.py)  [cache schema]
     │   compute_file_hash        (normalizer.py)  [idempotenza import]
     │   [FASE 0 classifier.py: sinonimi colonne deterministici]
     │
     ▼  ── STADIO 3: NORMALIZZAZIONE ──────────────────────────────────────
     │   parse_date_safe          (normalizer.py)
     │   parse_amount             (normalizer.py)
     │   apply_sign_convention    (normalizer.py)
     │   normalize_description    (normalizer.py)
     │   compute_transaction_id   (normalizer.py)  [SHA-256 dedup]
     │   [dedup intra-file: aggregazione righe identiche]
     │   remove_card_balance_row  (normalizer.py)
     │
     ▼  ── STADIO 4: DEDUP CHECK ────────────────────────────────────────────
     │   get_existing_tx_ids      (repository.py)
     │   [ID già calcolati al passo 3 → nessuna LLM call su tx già note]
     │
     ▼  ── STADIO 5: PULIZIA DESCRIZIONI ──────────────────────────────────
     │   redact_pii               (sanitizer.py)   [prima dell'LLM]
     │   restore_owner_aliases    (sanitizer.py)   [dopo l'LLM]
     │   [filtro output LLM: scarta "null","none","nan",…]
     │
     ▼  ── STADIO 6: RILEVAMENTO GIROCONTI ────────────────────────────────
     │   detect_internal_transfers (normalizer.py)
     │     ├─ Fase 1: accoppiamento importo + data
     │     └─ Fase 2: match nome proprietario
     │
     ▼  ── STADIO 7: RICONCILIAZIONE CARTE ───────────────────────────────
     │   find_card_settlement_matches (normalizer.py)
     │     ├─ Fase 1: finestra temporale
     │     ├─ Fase 2: sliding window (contigui)
     │     └─ Fase 3: subset sum ai bordi
     │
     ▼  ── STADIO 8: CATEGORIZZAZIONE (cascata) ──────────────────────────
     │   _try_deterministic       (categorizer.py)
     │     ├─ Livello 0: regole utente (CategoryRule.matches)
     │     └─ Livello 1: regole statiche (_STATIC_RULES)
     │   [se nessun match → LLM]
     │   [dopo LLM: validazione tassonomia deterministica]
     │
     ▼  ── STADIO 9: PERSISTENZA DB ──────────────────────────────────────
     │   [upsert idempotente per ogni tx, link, schema]
     │
     ▼  ── STADIO 10: REVISIONE MANUALE ──────────────────────────────────
         apply_rules_to_review_transactions  (repository.py)  [auto al caricamento pagina Review]
         apply_all_rules_to_all_transactions (repository.py)  [pulsante "Esegui tutte le regole"]
         _apply_description_rule_bulk        (review_page.py) [su richiesta utente]
         _rerun_transfer_detection           (review_page.py) [su richiesta utente]
```

---

## Catalogo completo per stadio

---

### Stadio 1 — Caricamento file

#### `detect_encoding` · `core/normalizer.py`
**Funzione:** rileva la codifica del file tramite `chardet`
**Input:** bytes grezzi del file
**Output:** stringa codifica (es. `"utf-8"`, `"latin-1"`)
**Note:** `ascii` è normalizzato a `utf-8`; default `utf-8` se chardet fallisce

#### `detect_delimiter` · `core/normalizer.py`
**Funzione:** conta la frequenza dei caratteri candidati e restituisce il più frequente
**Input:** contenuto testuale del CSV
**Output:** uno tra `,` `;` `\t` `|`
**Candidati (hardcoded):** `[",", ";", "\t", "|"]`

#### `detect_header_row` · `core/normalizer.py`
**Funzione:** trova la prima riga con ≥ 2 campi non-numerici e non-vuoti
**Input:** lista di righe testo
**Output:** indice riga (0 se non trovata)
**Pattern numerico (hardcoded):** `^[\d\.\,\-\+\s€$£%]+$`

#### `detect_best_sheet` · `core/normalizer.py`
**Funzione:** sceglie il foglio Excel con più dati utili
**Input:** oggetto workbook Excel
**Output:** nome del foglio
**Logica:**
- Esclude fogli con nome corrispondente a `summary|totale|riepilogo` (case-insensitive)
- Punteggio = `n_righe + n_colonne_numeriche × 10`
- Soglia: > 50% delle righe deve avere valori numerici per colonna considerata numerica

---

### Stadio 1b — Pre-processing Phase 0

#### `detect_and_strip_preheader_rows` · `core/normalizer.py`
**Funzione:** rimuove righe di metadati sparse presenti *prima* dell'intestazione reale della tabella transazioni
**Input:** DataFrame grezzo + nome file (per log)
**Output:** `(DataFrame ripulito, n_righe_rimosse)`
**Algoritmo:**
1. Ricostruisce la riga header consumata da pandas come riga 0 (i nomi colonna `Unnamed: N` vengono trattati come celle vuote)
2. Calcola la densità non-null per riga (numero celle non-null / numero colonne)
3. Calcola la mediana delle densità
4. Conta le righe contigue dall'inizio con densità < `mediana × 0.5`
5. Limiti di sicurezza: max **20 righe** assolute **E** max **10%** del totale → altrimenti `ValueError`
6. Riassegna i nomi colonna dalla prima riga non-sparsa

**Costanti (hardcoded):**

| Costante | Valore | Descrizione |
|---|---|---|
| `_PREHEADER_MAX_ROWS` | 20 | Max righe sparse assolute prima dell'errore |
| `_PREHEADER_MAX_RATIO` | 0.10 | Max % righe sparse rispetto al totale |
| `_PREHEADER_DENSITY_THRESHOLD` | 0.5 | Moltiplicatore della mediana per soglia sparsa |

**Casi speciali:**
- DataFrame con < 4 righe → restituisce invariato
- 0 righe sparse → restituisce invariato (fast path)

**Rationale:** approccio statistico e language-agnostic — non usa dizionari di termini bancari

---

#### `drop_low_variability_columns` · `core/normalizer.py`
**Funzione:** rimuove colonne con valori quasi costanti (es. "Nome titolare", "Numero carta" nei file AmEx)
**Input:** DataFrame + nome file (per log)
**Output:** `(DataFrame ripulito, lista_colonne_rimosse)`
**Algoritmo:** per ogni colonna calcola `nunique(col) / n_righe`; se < soglia → candidata alla rimozione
**Protezione:** mai scende sotto 2 colonne (preserva sempre un minimo lavorabile)

**Costanti (hardcoded):**

| Costante | Valore | Descrizione |
|---|---|---|
| `_LOW_VARIABILITY_RATIO` | 0.015 | Soglia: < 1.5 % unique/nrows → colonna metadata |

**Casi speciali:**
- DataFrame con < 2 righe → restituisce invariato
- DataFrame con ≤ 2 colonne → restituisce invariato

---

### Stadio 2 — Schema / classificazione documento

#### `compute_columns_key` · `core/normalizer.py`
**Funzione:** genera una chiave di cache per il DocumentSchema basata sui nomi delle colonne
**Input:** DataFrame pandas
**Output:** `"cols:" + SHA-256[:16]`
**Uso:** stesso layout bancario riconosciuto tra file diversi (es. `CARTA_2025.xlsx` → `CARTA_2026.xlsx`)

#### `compute_file_hash` · `core/normalizer.py`
**Funzione:** SHA-256 del file grezzo
**Input:** bytes del file
**Output:** stringa hex
**Uso:** idempotenza a livello di import (stesso file non reimportato)

#### Classifier Fase 0 — sinonimi colonne · `core/classifier.py`
**Funzione:** mapping deterministico dei nomi colonna a campi canonici
**Sinonimi riconosciuti (esempi):**

| Campo canonico | Sinonimi (estratto) |
|---|---|
| `date_col` | data, date, data operazione, buchungsdatum, fecha, date valeur |
| `amount_col` | importo, amount, betrag, montant, importe |
| `debit_col` | dare, addebiti, uscite, debit, ausgaben, débits |
| `credit_col` | avere, accrediti, entrate, credit, eingaben, crédits |
| `description_col` | descrizione, causale, memo, payee, verwendungszweck, libellé |

**Fase 0 prevale sempre sulla Fase 1 (LLM)** — i risultati certi del matching deterministico non vengono sovrascritti

---

### Stadio 3 — Normalizzazione

#### `parse_date_safe` · `core/normalizer.py`
**Funzione:** parsing data con fallback a formati comuni
**Input:** stringa grezza, formato primario dallo schema
**Output:** oggetto `date` oppure `None` (riga scartata se `None`)
**Formati di fallback (in ordine):**
```
%d/%m/%Y  %d-%m-%Y  %d/%m/%y  %d-%m-%y
%Y-%m-%d  %Y/%m/%d  %m/%d/%Y  %m/%d/%y
```

#### `parse_amount` · `core/normalizer.py`
**Funzione:** converte qualsiasi rappresentazione di importo in `Decimal`
**Input:** `str | float | int | Decimal`
**Output:** `Decimal` oppure `None`
**Simboli rimossi (hardcoded):** `€ $ £` e spazi
**Rilevamento separatori:**

| Formato | Esempio | Regola |
|---|---|---|
| Europeo | `1.234,56` | `.` = migliaia, `,` = decimale |
| Americano | `1,234.56` | `,` = migliaia, `.` = decimale |
| Solo virgola | `1234,56` | `,` = decimale se ≤ 2 cifre frazionali |
| Solo punto | `1234.56` | `.` = decimale |

#### `apply_sign_convention` · `core/normalizer.py`
**Funzione:** produce l'importo con segno corretto secondo la convenzione del file
**Input:** riga, colonne, `SignConvention` enum
**Convenzioni:**

| Convenzione | Logica |
|---|---|
| `signed_single` | colonna importo già con segno |
| `debit_positive` | credito − debito (entrambi positivi nel CSV) |
| `credit_negative` | credito as-is, `−|debito|` |

#### `normalize_description` · `core/normalizer.py`
**Funzione:** normalizzazione testo per confronto case-insensitive stabile
**Input:** stringa
**Output:** `NFC_unicode(testo).casefold().strip()`

#### `compute_transaction_id` · `core/normalizer.py`
**Funzione:** chiave di deduplicazione stabile per ogni transazione
**Input:** `account_label | source_file`, data grezza, importo grezzo, descrizione grezza
**Output:** `SHA-256[:24]` della stringa `"chiave|data_raw|importo_raw|desc_raw"`
**Perché valori grezzi:** stabile tra aggiornamenti degli algoritmi di normalizzazione

#### Dedup intra-file
**Funzione:** aggrega righe identiche (stesso account_label + data + importo + descrizione)
**Output:** somma importi, ricalcola hash
**Uso:** evita doppio conteggio se la stessa tx appare più volte nell'export bancario

#### `remove_card_balance_row` · `core/normalizer.py`
**Funzione:** rileva ed elimina la riga "totale saldo" presente in alcuni file carta
**Condizione di rilevamento:** `||importo_i| − Σ|altri importi|| ≤ epsilon`
**Richiede:** ≥ 3 transazioni nel file
**Comportamento:**
- Con `owner_name_label` → rinomina la descrizione con il nome del proprietario (il rilevamento giroconti la cattura poi)
- Senza `owner_name_label` → rimuove la riga (legacy: evita doppio conteggio)

---

### Stadio 4 — Dedup check

#### `get_existing_tx_ids` · `db/repository.py`
**Funzione:** query DB per trovare quali ID della batch esistono già
**Input:** lista di ID transazione (calcolati al passo 3 da valori grezzi)
**Output:** set di ID già presenti
**Uso:** filtra le tx già importate; se tutte presenti → abort immediato, zero chiamate LLM

> Questo passo è stato spostato **prima** della pulizia descrizioni per evitare di sprecare
> token LLM su transazioni che verrebbero comunque scartate perché già in DB.

---

### Stadio 5 — Pulizia descrizioni (wrapper deterministico attorno all'LLM)

#### `redact_pii` · `core/sanitizer.py`
**Funzione:** sostituisce dati sensibili con segnaposto o nomi fittizi **prima** di ogni chiamata LLM
**Input:** testo, `SanitizationConfig`
**Output:** testo redatto

**Pattern hardcoded (regex precompilate):**

| Dato | Pattern | Segnaposto |
|---|---|---|
| IBAN | `\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b` | `<ACCOUNT_ID>` |
| PAN / carta (13-19 cifre) | `\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,7}\b` | `<CARD_ID>` |
| Carta mascherata | `[\*X]{4}[\s\-]?\d{4}` | `<CARD_ID>` |
| Codici bancari (CAU, NDS, CRO, RIF…) | `\b(CAU\|NDS\|CRO\|RIF\|TRN\|ID\s*TRANSAZIONE)\s*[\d\-]+` | `<TX_CODE>` |
| Codice fiscale IT | `\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b` | `<FISCAL_ID>` |
| Pattern extra utente | da `SanitizationConfig.extra_patterns` | `<REDACTED>` |

**Nomi proprietari → nomi fittizi (pool per lingua):**

| Lingua | Pool (6 nomi) |
|---|---|
| IT | Carlo Brambilla, Marta Pellegrino, Alberto Marini, Giovanna Ferrara, Luca Montanari, Silvia Cattaneo |
| EN | James Fletcher, Helen Norris, Robert Ashworth, Patricia Holt, Edward Tilman, Susan Delaney |
| FR | Pierre Dumont, Claire Lebrun, François Martel, Isabelle Renaud, Gilles Fontaine, Nathalie Girard |
| DE | Klaus Hartmann, Monika Braun, Werner Schulze, Ingrid Bauer, Dieter Hoffmann, Renate Fischer |
| ES | Carlos Navarro, Elena Vega, Javier Romero, Isabel Fuentes, Andrés Molina, Lucía Castillo |

Tutte le permutazioni dei token vengono catturate ("Luigi Corsaro" **e** "Corsaro Luigi")

#### `restore_owner_aliases` · `core/sanitizer.py`
**Funzione:** operazione inversa — rimpiazza i nomi fittizi con i nomi reali del proprietario
**Quando:** dopo ogni risposta LLM
**Perché:** il rilevamento giroconti lavora sui nomi reali

#### Filtro output LLM · `core/description_cleaner.py`
**Funzione:** scarta risposte LLM inutilizzabili
**Valori scartati (hardcoded):** `"null"`, `"none"`, `"n/a"`, `"na"`, `"nan"`, `"-"`, `"—"`
**Ulteriore controllo:** scarta se risultato == descrizione originale (nessun cambiamento)

---

### Stadio 6 — Rilevamento giroconti [RF-04]

#### `detect_internal_transfers` · `core/normalizer.py`
**Funzione:** individua coppie di transazioni che si annullano (trasferimento interno tra conti)
**Eseguito:** automaticamente all'import + su richiesta utente dalla pagina Review

**Fase 1 — Accoppiamento per importo + data**

Per ogni coppia `(i, j)` con `i.account_label ≠ j.account_label`:

```
amount_match = |importo_i + importo_j| ≤ epsilon         (default 0.01 €)
date_match   = |data_i − data_j| ≤ delta_days             (default 5 gg)

high_symmetry = |importo_i + importo_j| ≤ epsilon_strict   (default 0.005 €)
              AND |data_i − data_j| ≤ delta_days_strict     (default 1 gg)

Confidenza:
  HIGH   → keyword presente in descrizione (lista da DB utente)
  MEDIUM → high_symmetry senza keyword

Con require_keyword_confirmation=True AND confidenza=MEDIUM:
  → imposta transfer_pair_id ma NON aggiorna tx_type (rimane in coda revisione)
Altrimenti:
  → tx_type = internal_out (uscita) / internal_in (entrata)
```

**Fase 2 — Match nome proprietario** (per tx non ancora accoppiate)

```
Se la descrizione contiene un nome proprietario
(regex con tutte le permutazioni dei token):
  → tx_type = internal_out / internal_in
  → transfer_confidence = HIGH
```

**Parametri configurabili:**

| Parametro | Default | Configurabile |
|---|---|---|
| `epsilon` | 0.01 € | Sì |
| `epsilon_strict` | 0.005 € | Sì |
| `delta_days` | 5 gg | Sì |
| `delta_days_strict` | 1 gg | Sì |
| `keyword_patterns` | da DB | Sì (pagina Impostazioni) |
| `owner_names` | da DB | Sì (pagina Impostazioni) |
| `require_keyword_confirmation` | `True` | Sì |

---

### Stadio 7 — Riconciliazione carte [RF-03]

#### `find_card_settlement_matches` · `core/normalizer.py`
**Funzione:** abbina le righe `card_settlement` (dal conto corrente) alle singole `card_tx` (dalla carta)
**Eseguito:** automaticamente all'import quando entrambi i file sono presenti

**Fase 1 — Filtro finestra temporale**
```
Considera solo card_tx in [data_addebito − 45 gg, data_addebito + 7 gg]
```

**Fase 2 — Sliding window (sottoinsiemi contigui)**
```
Per ogni sottoinsieme contiguo [i..j]:
  verifica gap tra tx consecutive ≤ max_gap_days (default 5 gg)
  somma = Σ |importo[i..j]|
  Se |somma − importo_addebito| ≤ epsilon → MATCH ✓ (method="sliding_window")
```

**Fase 3 — Subset sum ai bordi (fallback)**
```
Prende k=10 tx prima + k=10 tx dopo la data addebito (tot. ≤ 20)
Ricerca esaustiva su tutti i sottoinsiemi: O(2^20) ≈ 1M → sicuro
Se un sottoinsieme somma all'importo → MATCH ✓ (method="subset_sum")
```

**Parametri configurabili:**

| Parametro | Default | Configurabile |
|---|---|---|
| `epsilon` | 0.01 € | Sì |
| `window_days` | 45 gg | Sì |
| `max_gap_days` | 5 gg | Sì |
| `boundary_k` | 10 | Sì |

---

### Stadio 8 — Categorizzazione (parte deterministica)

#### `_try_deterministic` · `core/categorizer.py`
**Funzione:** applica la cascata deterministica (Livelli 0 e 1) prima di chiamare l'LLM
**Output:** `CategorizationResult` oppure `None` (→ si passa all'LLM)

---

#### Livello 0 — Regole utente (`CategoryRule.matches`)

**Oggetto:** `CategoryRule` in `core/categorizer.py`
**Campi chiave:** `pattern`, `match_type`, `category`, `subcategory`, `doc_type`, `priority`

**Logica di matching (case-insensitive, casefold):**

| `match_type` | Condizione |
|---|---|
| `exact` | `descrizione == pattern` |
| `contains` | `pattern in descrizione` |
| `regex` | `re.search(pattern, descrizione)` |

Filtro opzionale per `doc_type` (es. solo `credit_card`)
**Ordine:** priorità decrescente — la **prima** regola che fa match vince
**Risultato:** `confidenza=HIGH`, `sorgente=rule`, `to_review=False`

---

#### Livello 1 — Regole statiche (hardcoded) · `core/categorizer.py`

10 regole hardcoded, ordinate per tipo di transazione e direzione.
Tutte case-insensitive, applicate solo alla direzione corretta (spesa/entrata).

| # | Pattern | Categoria | Sottocategoria | Direzione |
|---|---|---|---|---|
| 1 | `conad\|coop\|esselunga\|lidl\|carrefour\|eurospin\|aldi\|penny\|pam\b` | Alimentari | Spesa supermercato | Spesa |
| 2 | `farmacia\|pharma` | Salute | Farmaci | Spesa |
| 3 | `eni\b\|shell\|q8\|tamoil\|ip\b\|api\b\|agip` | Trasporti | Carburante | Spesa |
| 4 | `telepass\|autostrad` | Trasporti | Parcheggio e ZTL | Spesa |
| 5 | `trenitalia\|italo\|frecciarossa\|frecciargento` | Trasporti | Trasporto pubblico | Spesa |
| 6 | `enel\b\|iren\b\|a2a\b\|hera\b\|eni gas` | Casa | Energia elettrica | Spesa |
| 7 | `netflix\|spotify\|amazon prime\|disney\+\|apple tv` | Svago e tempo libero | Streaming / abbonamenti digitali | Spesa |
| 8 | `commissione\|canone conto\|spese tenuta` | Finanza e assicurazioni | Commissioni bancarie | Spesa |
| 9 | `stipendio\|salary\|busta paga` | Lavoro dipendente | Stipendio | Entrata |
| 10 | `pensione\|inps rendita` | Prestazioni sociali | Pensione / rendita | Entrata |

**Risultato se match:** `confidenza=HIGH`, `sorgente=rule`, `to_review=False`

---

#### Validazione post-LLM (deterministica) · `core/categorizer.py`

Dopo ogni risposta LLM, prima di accettare la categorizzazione:

```
1. La coppia (categoria, sottocategoria) è valida nella tassonomia?
   NO → cerca la categoria padre della sottocategoria (find_category_for_subcategory)
         SE non trovata → usa la prima sottocategoria valida per quella categoria
         SET confidenza=LOW, to_review=True

2. La categoria è nella direzione corretta (spesa/entrata)?
   NO → fallback (Altro / Spese non classificate), to_review=True

3. Confidenza LLM ≥ threshold (0.80)?
   NO → to_review=True
```

---

### Stadio 10 — Revisione manuale

#### `apply_rules_to_review_transactions` · `db/repository.py`
**Funzione:** applica le regole utente a tutte le tx con `to_review=True`
**Quando:** automaticamente ad ogni caricamento della pagina Review
**Logica:** priorità decrescente, primo match vince
**Effetto:** `to_review=False`, `category_source=rule`

#### `apply_all_rules_to_all_transactions` · `db/repository.py`
**Funzione:** applica tutte le regole utente a **tutte** le transazioni (non solo `to_review=True`)
**Quando:** su richiesta utente tramite pulsante "▶️ Esegui tutte le regole" nella pagina Regole
**Logica:** regole ordinate per priorità decrescente; primo match vince; aggiorna `category`, `subcategory`, `category_source=rule`, `category_confidence=high`; se `to_review=True` → imposta `False`
**Effetto:** restituisce `(n_matched, n_cleared_review)` — transazioni aggiornate e transazioni tolte dalla coda di revisione

#### `_apply_description_rule_bulk` · `ui/review_page.py`
**Funzione:** aggiorna la descrizione di tutte le tx la cui `raw_description` fa match con il pattern
**Quando:** su richiesta utente (pulsante "Applica in blocco")
**Passi deterministici:**
1. `get_transactions_by_raw_pattern(session, pattern, match_type)` → lista tx
2. Aggiorna `tx.description = nuova_descrizione` per ogni tx
3. Avvia re-categorizzazione LLM (non deterministico)

**Salvataggio regola (DescriptionRule):** idempotente su `(raw_pattern, match_type)`

#### `_rerun_transfer_detection` · `ui/review_page.py`
**Funzione:** riesegue `detect_internal_transfers` su tutte le tx non-giroconto nel DB
**Quando:** su richiesta utente (pulsante "Riesegui rilevamento giroconti")
**Uso tipico:** dopo aver importato i file di più conti diversi

---

## Tabella riepilogativa

| # | Strumento | Modulo | Stadio | Auto? | Configurabile? |
|---|---|---|---|---|---|
| 1 | `detect_encoding` | normalizer.py | 1 – Caricamento | ✓ | No |
| 2 | `detect_delimiter` | normalizer.py | 1 – Caricamento | ✓ | No |
| 3 | `detect_header_row` | normalizer.py | 1 – Caricamento | ✓ | No |
| 4 | `detect_best_sheet` | normalizer.py | 1 – Caricamento | ✓ | No |
| 4b | `detect_and_strip_preheader_rows` | normalizer.py | 1b – Pre-processing | ✓ | No |
| 4c | `drop_low_variability_columns` | normalizer.py | 1b – Pre-processing | ✓ | No |
| 5 | `compute_file_hash` | normalizer.py | 2 – Schema | ✓ | No |
| 6 | `compute_columns_key` | normalizer.py | 2 – Schema | ✓ | No |
| 7 | Classifier Fase 0 (sinonimi) | classifier.py | 2 – Classificazione | ✓ | No |
| 8 | `parse_date_safe` | normalizer.py | 3 – Normalizzazione | ✓ | No |
| 9 | `parse_amount` | normalizer.py | 3 – Normalizzazione | ✓ | No |
| 10 | `apply_sign_convention` | normalizer.py | 3 – Normalizzazione | ✓ | Parziale |
| 11 | `normalize_description` | normalizer.py | 3 – Normalizzazione | ✓ | No |
| 12 | `compute_transaction_id` | normalizer.py | 3 – Normalizzazione | ✓ | No |
| 13 | Dedup intra-file | normalizer.py | 3 – Normalizzazione | ✓ | No |
| 14 | `remove_card_balance_row` | normalizer.py | 3 – Normalizzazione | ✓ | `epsilon` |
| 15 | `get_existing_tx_ids` | repository.py | 4 – Dedup check | ✓ | No |
| 16 | `redact_pii` | sanitizer.py | 5 – Pulizia desc. | ✓ | Owner names, extra patterns |
| 17 | `restore_owner_aliases` | sanitizer.py | 5 – Pulizia desc. | ✓ | No |
| 18 | Filtro output LLM | description_cleaner.py | 5 – Pulizia desc. | ✓ | No |
| 19 | `detect_internal_transfers` | normalizer.py | 6 – Giroconti | ✓ + manuale | Sì (tutto) |
| 20 | `find_card_settlement_matches` | normalizer.py | 7 – Riconciliazione | ✓ | Sì |
| 21 | Regole utente (`CategoryRule`) | categorizer.py | 8 – Categorizzazione | ✓ | Sì (utente) |
| 22 | Regole statiche (`_STATIC_RULES`) | categorizer.py | 8 – Categorizzazione | ✓ | No |
| 23 | Validazione post-LLM tassonomia | categorizer.py | 8 – Categorizzazione | ✓ | No |
| 24 | `apply_rules_to_review_transactions` | repository.py | 10 – Revisione | ✓ (caricamento) | No |
| 25 | `apply_all_rules_to_all_transactions` | repository.py | 10 – Revisione | Manuale (pulsante) | No |
| 26 | `_apply_description_rule_bulk` | review_page.py | 10 – Revisione | Manuale | Sì (pattern) |
| 27 | `_rerun_transfer_detection` | review_page.py | 10 – Revisione | Manuale | No |
| 28 | `get_transactions_by_rule_pattern` | repository.py | 10 – Revisione | On demand | No |
| 29 | `get_transactions_by_raw_pattern` | repository.py | 10 – Revisione | On demand | No |
