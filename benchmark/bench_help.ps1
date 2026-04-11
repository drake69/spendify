#!/usr/bin/env pwsh
# bench_help.ps1 — Guida rapida benchmark: copia-incolla i comandi per ogni fase.
#
# Uso:  powershell -ExecutionPolicy Bypass -File benchmark\bench_help.ps1

Write-Host @"
════════════════════════════════════════════════════════════════════════════════
  SPENDIFY BENCHMARK — GUIDA RAPIDA (Windows)
════════════════════════════════════════════════════════════════════════════════

──────────────────────────────────────────────────────────────────────────────
  FASE 0 — SETUP (una volta per macchina)
──────────────────────────────────────────────────────────────────────────────

  # 0a. Copia dalla chiavetta
  xcopy E:\spendif-ai $env:USERPROFILE\Desktop\spendif-ai /E /I /Y
  cd $env:USERPROFILE\Desktop\spendif-ai

  # 0b. Installa uv (se non c'è)
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

  # 0c. Crea venv
  uv sync

  # 0d. Verifica ambiente
  powershell -ExecutionPolicy Bypass -File benchmark\bench_report.ps1

──────────────────────────────────────────────────────────────────────────────
  FASE 1 — QUICK SCAN (8 file, 1 per tipo)
──────────────────────────────────────────────────────────────────────────────

  powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1 -MaxFiles 8

  # -MaxFiles 8 seleziona 1 file per ogni tipo (doc_type x format = 8 tipi)
  # I modelli vengono scaricati automaticamente (~35 GB totale, 11 modelli)
  # Se lo spazio disco scende sotto 16 GB, i GGUF vengono cancellati dopo l'uso
  # Stima: ~6-8 ore su CPU pura

──────────────────────────────────────────────────────────────────────────────
  FASE 1b — STATISTICHE
──────────────────────────────────────────────────────────────────────────────

  .venv\Scripts\python benchmark\benchmark_stats.py --by-group
  .venv\Scripts\python benchmark\benchmark_stats.py --by-group --by-host

──────────────────────────────────────────────────────────────────────────────
  FASE 1c — SALVA RISULTATI SU CHIAVETTA
──────────────────────────────────────────────────────────────────────────────

  copy benchmark\results\20*.csv E:\spendif-ai\benchmark\results\

──────────────────────────────────────────────────────────────────────────────
  FASE 2 — FULL BENCHMARK (50 file, solo modelli selezionati)
──────────────────────────────────────────────────────────────────────────────

  powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1

──────────────────────────────────────────────────────────────────────────────
  COMANDI UTILI
──────────────────────────────────────────────────────────────────────────────

  # Benchmark singolo modello
  .venv\Scripts\python benchmark\benchmark_classifier.py `
      --runs 1 --backend local_llama_cpp `
      --model-path $env:USERPROFILE\.spendifai\models\Qwen_Qwen3.5-9B-Q3_K_M.gguf `
      --max-files 8

  # Monitor
  powershell -ExecutionPolicy Bypass -File benchmark\monitor_benchmark.ps1

  # Report ambiente
  powershell -ExecutionPolicy Bypass -File benchmark\bench_report.ps1

══════════════════════════════════════════════════════════════════════════════
"@
