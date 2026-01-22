# 🏦 AI Bank Statement Categorizer & Multi-Bank Manager

Sistema intelligente per la gestione degli estratti conto bancari (PDF/CSV). Il software apprende dalle tue approvazioni, riconcilia le ricevute mancanti e fornisce un'analisi dettagliata dei budget.

## 🌟 Caratteristiche Principali

- **Multi-Banca**: Riconoscimento automatico tramite IBAN per evitare duplicati.
- **Supporto Duale**: Gestione nativa di PDF (estrazione tabelle) e CSV (export bancari).
- **Fuzzy Learning**: Se correggi una categoria, il sistema la imparerà per tutte le transazioni simili future.
- **AI Fallback**: Integrazione con Ollama (Locale) o OpenAI per categorizzare voci sconosciute.
- **Riconciliazione Documentale**: Flag automatico su voci "scatola chiusa" (Amazon, Prelievi) che richiedono una fattura/ricevuta.
- **Budgeting**: Alert visivi se superi le soglie di spesa mensili.

## 🛠️ Requisiti e Installazione

1. Assicurati di avere **Python 3.9+** installato.
2. Scarica i file della codebase in una cartella.
3. Esegui il file `setup.bat` (Windows) o `setup.sh` (Linux/Mac).

### Configurazione AI Locale (Opzionale ma consigliata)

Per usare l'AI senza inviare dati all'esterno:

1. Scarica **Ollama** da [ollama.com](https://ollama.com).
2. Esegui nel terminale: `ollama pull llama3`.
3. Mantieni Ollama attivo durante l'uso dell'app.

## 📂 Struttura della Cartella

- `app.py`: Interfaccia utente Streamlit.
- `core_logic.py`: Motore di elaborazione e intelligenza.
- `test_finance.py`: Suite di test automatici.
- `history_database.csv`: (Generato automaticamente) Il tuo database storico protetto localmente.

## 📖 Come usare l'app

1. **Carica**: Trascina un PDF o CSV.
2. **Controlla**: Verifica le categorie suggerite.
3. **Riconcilia**: Per le voci Amazon/E-commerce, inserisci l'ID della ricevuta nella colonna dedicata.
4. **Approva**: Spunta la casella 'Categoria_Approvata' e salva.
5. **Esporta**: Scarica il report Excel con i grafici pronti per la tua contabilità.

---

*Nota: Tutti i dati sono salvati esclusivamente sul tuo computer nel file `history_database.csv`.*
