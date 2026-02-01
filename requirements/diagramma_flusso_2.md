```markdown
# Diagramma di flusso — Flusso 2 (End-to-end via LLM, schema-on-read)

```mermaid
flowchart TD
  A[Start: Import file (formato ignoto)] --> B[Load dataframe grezzo (as-is)]
  B --> C[Sanitizzazione preventiva (obbligatoria)<br/>- redazione nomi proprietari (config)<br/>- mascheramento IBAN/PAN/conti/carte<br/>- rimozione ID sensibili]
  C --> D[Prompting strutturato al LLM<br/>Richieste:<br/>- inferenza colonne/date/importi/valuta<br/>- normalizzazione schema canonico<br/>- categoria+contesto+confidenza]
  D --> E[LLM output: JSON/schema canonico + confidenze]
  E --> F{Parsing output valido?}
  F -->|No| G[Fallback: errore + richiesta intervento manuale<br/>o fallback Flusso 1]
  F -->|Sì| H[Post-validazione deterministica<br/>- vincoli schema<br/>- coerenza importi/date<br/>- cardinalità righe]
  H --> I[Deduplicazione / idempotenza<br/>- chiave fuzzy]
  I --> J{Riconoscimento giroconti? (rule-based)}
  J -->|Sì| K[Detect & link giroconti]
  J -->|No| L[Continua]
  K --> L[Vista transazioni canoniche]
  L --> M{Documenti caricati?}
  M -->|Sì| N[Matching documento–transazione<br/>- preferibilmente rule-based<br/>- opzionalmente LLM (sanitized)]
  N --> O[Propagazione contesto da documento<br/>se presente]
  M -->|No| P[Skip documenti]
  O --> Q{Confidenza >= soglia?}
  P --> Q
  Q -->|Sì| R[Persist + audit log]
  Q -->|No| S[Flag: da revisionare]
  R --> T[Output<br/>- transazioni canoniche<br/>- categorie/contesti<br/>- giroconti marcati<br/>- stato (ok/review)]
  S --> T
  T --> U[Reportistica/Budgeting (a valle)]
  U --> V[End]