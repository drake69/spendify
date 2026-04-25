#!/usr/bin/env bash
# bench_help.sh — Guida rapida benchmark: copia-incolla i comandi per ogni fase.
#
# Uso:  bash benchmark/bench_help.sh

cat <<'EOF'
════════════════════════════════════════════════════════════════════════════════
  SPENDIFY BENCHMARK — GUIDA RAPIDA
════════════════════════════════════════════════════════════════════════════════

──────────────────────────────────────────────────────────────────────────────
  FASE 0 — SETUP (una volta per macchina)
──────────────────────────────────────────────────────────────────────────────

  # 0a. Copia il progetto dalla chiavetta (Linux/macOS)
  cp -r /media/$USER/KIAVETTA/spendif-ai ~/spendifai
  cd ~/spendifai

  # 0a. Copia il progetto dalla chiavetta (Windows PowerShell)
  xcopy E:\spendif-ai $env:USERPROFILE\Desktop\spendif-ai /E /I /Y
  cd $env:USERPROFILE\Desktop\spendif-ai

  # 0b. Crea venv e installa dipendenze (Linux/macOS)
  uv sync
  # Se hai compilato llama-cpp-python con GPU custom (Vulkan/ROCm):
  #   usa --skip-sync per non sovrascrivere

  # 0b. Crea venv e installa dipendenze (Windows)
  uv sync

  # 0c. Verifica ambiente
  bash benchmark/bench_report.sh                    # Linux/macOS
  # powershell benchmark\bench_report.ps1           # Windows

──────────────────────────────────────────────────────────────────────────────
  FASE 1 — QUICK SCAN (8 file, 1 per tipo × formato)
──────────────────────────────────────────────────────────────────────────────

  # Linux/macOS — benchmark completo (tutti i modelli abilitati × 8 file × 2 fasi)
  bash benchmark/run_benchmark_full.sh --max-files 8

  # Linux/macOS — con skip sync (venv già OK, librerie custom)
  bash benchmark/run_benchmark_full.sh --max-files 8 --skip-sync

  # Windows PowerShell
  powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1 -MaxFiles 8

  # --max-files 8 seleziona 1 file per ogni tipo (doc_type × format = 8 tipi)
  # I modelli vengono scaricati automaticamente se mancano (~35 GB totale)
  # Se lo spazio su disco scende sotto 16 GB, i GGUF vengono cancellati dopo l'uso
  # Il resume è automatico: se si interrompe, rilancia lo stesso comando
  # Stima: ~3 ore su GPU, ~6-8 ore su CPU pura (11 modelli)

──────────────────────────────────────────────────────────────────────────────
  FASE 1b — STATISTICHE (dopo ogni run)
──────────────────────────────────────────────────────────────────────────────

  # Statistiche per gruppo di accuratezza
  python benchmark/benchmark_stats.py --by-group

  # Statistiche per macchina (con nomi amichevoli da machine_names.csv)
  python benchmark/benchmark_stats.py --by-group --by-host

  # Usa .venv/bin/python se python non è nel PATH
  .venv/bin/python benchmark/benchmark_stats.py --by-group --by-host

──────────────────────────────────────────────────────────────────────────────
  FASE 1c — COLLECTION RISULTATI (sulla macchina dev)
──────────────────────────────────────────────────────────────────────────────

  # 1. Copia i CSV dalla macchina bench
  #    (chiavetta, scp, rsync — qualsiasi metodo)
  cp /media/$USER/KIAVETTA/spendif-ai/benchmark/results/20*.csv \
     benchmark/results/

  # 2. Aggiungi hostname → nome amichevole (se nuova macchina)
  #    Edita benchmark/machine_names.csv:
  #    hostname_tecnico,Nome Amichevole

  # 3. Verifica le stats aggregate
  .venv/bin/python benchmark/benchmark_stats.py --by-group --by-host

  # 4. Aggiorna il piano (benchmark/benchmark_plan.csv)
  #    status: todo → done, compila exact_pct, fuzzy_pct, etc.

──────────────────────────────────────────────────────────────────────────────
  FASE 2 — FULL BENCHMARK (50 file, solo modelli selezionati)
──────────────────────────────────────────────────────────────────────────────

  # Prerequisito: selezionare max 2 modelli dalla Fase 1
  # Disabilitare gli altri in benchmark/benchmark_models.csv (enabled=false)

  # Linux/macOS
  bash benchmark/run_benchmark_full.sh

  # Linux/macOS con skip sync
  bash benchmark/run_benchmark_full.sh --skip-sync

  # Windows PowerShell
  powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1

  # Stima: ~5-7 ore su GPU, ~20-35 ore su CPU pura

──────────────────────────────────────────────────────────────────────────────
  COMANDI UTILI
──────────────────────────────────────────────────────────────────────────────

  # Benchmark singolo modello (bypass shell script)
  .venv/bin/python benchmark/benchmark_classifier.py \
      --runs 1 --backend local_llama_cpp \
      --model-path ~/.spendifai/models/Qwen_Qwen3.5-9B-Q3_K_M.gguf \
      --max-files 8

  .venv/bin/python benchmark/benchmark_categorizer.py \
      --runs 1 --backend local_llama_cpp \
      --model-path ~/.spendifai/models/Qwen_Qwen3.5-9B-Q3_K_M.gguf \
      --max-files 8

  # Monitor in tempo reale (altra finestra)
  bash benchmark/monitor_benchmark.sh

  # Report ambiente (verifica GPU, backend, modelli)
  bash benchmark/bench_report.sh

  # Push su chiavetta (dalla macchina dev)
  bash benchmark/bench_push_usb.sh --dest /Volumes/KIAVETTA/spendif-ai

  # Salva risultati su chiavetta (dalla macchina bench)
  cp benchmark/results/20*.csv /media/$USER/KIAVETTA/spendif-ai/benchmark/results/

──────────────────────────────────────────────────────────────────────────────
  FILE DI CONFIGURAZIONE
──────────────────────────────────────────────────────────────────────────────

  benchmark/benchmark_models.csv   — modelli abilitati (enabled=true/false)
  benchmark/benchmark_plan.csv     — tracking completamento per macchina
  benchmark/machine_names.csv      — hostname → nome amichevole
  benchmark/.custom_packages       — librerie GPU da proteggere da uv sync
  benchmark/accuracy_groups.py     — gruppi di equivalenza commit (aggiornare ad ogni commit!)
  benchmark/ACCURACY_EQUIVALENCE.md — documentazione gruppi di equivalenza

──────────────────────────────────────────────────────────────────────────────
  MODELLI ABILITATI (Fase 1)
──────────────────────────────────────────────────────────────────────────────

  Qwen 2.5 1.5B    (1.0 GB)   — Qwen vecchia generazione, piccolo
  Qwen 3.5 2B      (1.3 GB)   — Qwen nuova generazione, piccolo
  Qwen 2.5 3B      (1.9 GB)   — Qwen vecchia generazione, medio
  Phi4-mini         (2.5 GB)   — Microsoft, ~3.8B
  Nemotron-Mini 4B  (2.7 GB)   — NVIDIA
  Qwen 3.5 4B      (2.9 GB)   — Qwen nuova generazione, medio
  Gemma 4 E2B       (2.9 GB)   — Google
  Mistral-7B        (4.4 GB)   — Mistral
  Qwen 3.5 9B Q3   (4.6 GB)   — Qwen nuova, best accuracy attuale
  DeepSeek-R1 7B    (4.7 GB)   — DeepSeek
  Qwen 3.5 9B Q4   (5.6 GB)   — Qwen nuova, premium

──────────────────────────────────────────────────────────────────────────────
  GESTIONE SPAZIO DISCO
──────────────────────────────────────────────────────────────────────────────

  I modelli GGUF NON vengono scaricati tutti in anticipo.
  Per ogni modello: check disco → download → run → cleanup → next.
  - Pre-check: serve 16 GB + size modello liberi
  - Dopo il run: se disco < 16 GB, il GGUF viene cancellato
  - Al prossimo lancio verrà riscaricato se necessario
  - Funziona anche con soli 20-25 GB liberi su disco

══════════════════════════════════════════════════════════════════════════════
EOF
