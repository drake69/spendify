# Benchmark Accuracy Equivalence Table

I risultati di benchmark sono comparabili **solo tra commit con la stessa pipeline di accuratezza**.
Commit che cambiano solo infra, docs, CI, performance (senza toccare la logica di classificazione/categorizzazione)
producono risultati equivalenti in termini di accuratezza.

## Come usare questa tabella

- Prima di confrontare risultati di run diversi, verificare che i commit appartengano allo **stesso gruppo**.
- Run di gruppi diversi **non sono confrontabili** — le metriche di accuratezza riflettono pipeline diverse.
- Il campo `git_commit` nei CSV di risultato identifica il commit. Usa `benchmark_stats.py --by-commit` per separare.

## Gruppi di equivalenza

| Gruppo | Commit accuracy-boundary        | Data       | Cosa cambia                                                          |
|--------|---------------------------------|------------|----------------------------------------------------------------------|
| **G6** | `b3947a7` → `78ec3e8` (HEAD)   | 06-08 apr  | real taxonomy_map, fix SyntheticHistoryCache (ultimo boundary: `b3947a7`) |
| **G5** | `c4a6768` → `a8f9fb7`          | 05-06 apr  | static rules da JSON per paese + NSI lookup                          |
| **G4** | `4e912f8` → `78af418`          | 02-05 apr  | cleaner dedup + indexed batch (I-16)                                 |
| **G3** | `5ffe1aa` → `398c56e`          | 02 apr     | enforce user account_type over LLM doc_type (BUG-08)                 |
| **G2** | `03fdeec` → `4ac9c52`          | 01 apr     | 9 fix pipeline: Phase 0, sign convention, rarity sampling, CSV parser|
| **G1** | `44b973f` → `560cae7`          | 31 mar-01 apr | multi-step classifier, debit_credit_signed convention              |
| **G0** | `d231f57` e precedenti         | ≤ 28 mar   | encoding fallback, pipeline pre-refactor                             |

### Commit nel gruppo G6 (corrente)

Tutti questi commit producono **risultati di accuratezza identici** — le differenze sono solo infra/perf/docs:

| Commit    | Data     | Descrizione                              | Tipo      |
|-----------|----------|------------------------------------------|-----------|
| `78ec3e8` | 08 apr   | GPU backend detection .dylib (macOS)     | infra     |
| `164bcac` | 08 apr   | cap n_ctx a 16K (perf, non accuratezza)  | perf      |
| `6eaba1f` | 08 apr   | security hardening CI                    | ci        |
| `4bca4b4` | 08 apr   | security scanning job                    | ci        |
| `c3630c5` | 06 apr   | Patreon badge README                     | docs      |
| `0095d1a` | 06 apr   | release pipeline DMG/ZIP                 | infra     |
| `351110d` | 06 apr   | deploy scripts macOS/Windows             | infra     |
| `b4e5163` | 06 apr   | OSM-aware synthetic dataset (T-10)       | benchmark |
| `a5a007b` | 06 apr   | download progress bar                    | infra     |
| `8a23584` | 06 apr   | macOS deploy script                      | infra     |
| `27a0093` | 06 apr   | docs scenario interpretation             | docs      |
| `38f28f5` | 06 apr   | docs counterpart extraction              | docs      |
| `86d7d51` | 06 apr   | default Claude model ID update           | config    |
| `73bfb86` | 06 apr   | ClaudeBackend default update             | config    |
| `bd0875f` | 06 apr   | default Claude model update              | config    |
| `61e6024` | 06 apr   | model catalogue + delete_after           | benchmark |
| `8e7b6ff` | 06 apr   | DescriptionProfile fix + catalogue       | benchmark |
| **`b3947a7`** | 06 apr | **real taxonomy_map + fix HistoryCache** | **accuracy boundary** |

### Nota su `164bcac` (cap n_ctx)

Questo commit limita `n_ctx` a 16K. Non cambia la logica di classificazione/categorizzazione,
ma un `n_ctx` eccessivo (es. 262K su Qwen 3.5) causa KV cache sovradimensionata che può
produrre **output troncati o timeout** → fallback rate più alto.

In pratica: risultati pre-`164bcac` e post-`164bcac` hanno la stessa pipeline,
ma i risultati pre-cap possono mostrare **fallback artificialmente più alti** su modelli
con context window grande (Qwen 3.5 in particolare).
