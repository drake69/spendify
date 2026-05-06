# Spendif.ai — Guida all'installazione su macOS

*English version: [installation_macos.md](installation_macos.md).*

> **Ambito:** questa guida copre l'installazione nativa su macOS di Spendif.ai
> tramite l'installer del bundle `.app`. Per Linux, Docker o Windows vedi
> [installazione.md](installazione.md).

---

## Requisiti di sistema

| Requisito | Minimo | Consigliato |
|---|---|---|
| macOS | 12 Monterey | 13 Ventura o successivo |
| RAM | 8 GB | 16 GB (per LLM da 12B parametri) |
| Disco | 5 GB liberi | 10 GB (modelli + dati) |
| Python | 3.11 | 3.13 (installato dalla modalità `--brew`) |
| Xcode CLT | richiesto | richiesto |
| Homebrew | opzionale | necessario solo per la modalità `--brew` |

**Apple Silicon (M1/M2/M3/M4):** l'inferenza GPU tramite Metal è abilitata
automaticamente durante l'installazione — nessuna configurazione manuale richiesta.

**Mac Intel:** Metal non è disponibile per l'inferenza LLM su Intel; l'app gira
in modalità solo CPU. Tutto funziona correttamente, ma l'inferenza è più lenta.

### Xcode Command Line Tools

L'installer richiede `git`, incluso negli Xcode CLT. Se i CLT non sono
installati, lo script attiverà automaticamente il dialog di installazione.
Puoi anche installarli manualmente prima di lanciare l'installer:

```bash
xcode-select --install
```

---

## Quick Start — One-liner

```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/packaging/macos/install.sh | bash
```

Questo esegue l'installer in **modalità default** (Python di sistema + uv).
Vedi le sezioni sotto per le opzioni.

Per ispezionare lo script prima di eseguirlo:

```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/packaging/macos/install.sh -o install.sh
less install.sh
bash install.sh
```

---

## Due modalità di installazione

### Modalità default (Python di sistema + uv)

```bash
bash install.sh
```

Usa qualsiasi `python3` già presente nel `PATH` (macOS include Python 3.9+;
molti sviluppatori hanno una versione più recente installata). `uv` (il package
manager basato su Rust) viene scaricato automaticamente se assente.

**Pro:**
- Nessun Homebrew richiesto — zero dipendenze oltre a `git` e `curl`
- Setup più veloce (nessun bootstrap del package manager)
- Funziona allo stesso modo su qualsiasi Python >= 3.11

**Contro:**
- La versione di Python dipende da quella già installata
- Il Python di sistema di macOS (3.9 sulle release più vecchie) è troppo
  datato — vedi [Troubleshooting](#troubleshooting) se il check fallisce

### Modalità Homebrew (`--brew`)

```bash
bash install.sh --brew
```

Installa Homebrew se assente, poi installa Python 3.13 tramite
`brew install python@3.13`, e usa quel Python per il virtual environment.

**Pro:**
- Fornisce sempre un Python aggiornato e supportato (3.13)
- Gestito da Homebrew → facile da aggiornare in seguito con
  `brew upgrade python@3.13`
- Ambiente coerente tra macchine diverse

**Contro:**
- Richiede ~500 MB extra di disco per Homebrew (se non già presente)
- L'installazione di Homebrew stessa può richiedere qualche minuto su una
  macchina pulita

### Entrambe le modalità: `uv` per la gestione delle dipendenze

Entrambe le modalità usano `uv sync` per creare il virtual environment e
installare tutte le dipendenze da `pyproject.toml` / `uv.lock`. Il `.venv`
risultante è deterministico e riproducibile. `llama-cpp-python` viene compilato
dai sorgenti con Metal abilitato (`CMAKE_ARGS="-DGGML_METAL=on"`) durante
`uv sync`.

---

## Tutte le opzioni dell'installer

```
bash install.sh [OPTIONS]

--brew               Installa Python 3.13 via Homebrew se non presente
--install-dir DIR    Directory del codice (default: ~/Applications/Spendif.ai)
--branch BRANCH      Branch git       (default: main)
--copy-db PATH       Copia un DB SQLite esistente in ~/.spendifai/spendifai.db
--copy-models PATH   Copia una directory di modelli in ~/.spendifai/models/
--launch             Apre l'app immediatamente dopo l'installazione
--update             Solo aggiornamento (git pull + alembic), non reinstallazione completa
-h, --help           Mostra l'aiuto
```

---

## Primo avvio e inizializzazione del database

Al primo avvio:
1. Streamlit parte su `http://localhost:8501` (si apre una finestra di Terminale)
2. Il browser viene aperto automaticamente dopo 3 secondi
3. SQLAlchemy crea `~/.spendifai/spendifai.db` al primo accesso al database
4. Il wizard di onboarding ti guida nella configurazione del backend LLM

Il database **non viene mai** creato o modificato dall'installer — solo dall'app
stessa. Questo significa che puoi rieseguire l'installer o lanciare `--update`
in tutta sicurezza senza toccare i tuoi dati.

### Migrazione da un'installazione esistente

Se hai già un database Spendif.ai altrove, passalo in fase di installazione:

```bash
bash install.sh --copy-db ~/old_spendifai/ledger.db
```

L'installer lo copia in `~/.spendifai/spendifai.db` e lancia immediatamente
`alembic upgrade head` per applicare eventuali migrazioni di schema pendenti.

---

## Come funziona Spotlight con il bundle .app

L'installer crea `/Applications/Spendif.ai.app` — un application bundle
standard di macOS con la seguente struttura:

```
/Applications/Spendif.ai.app/
├── Contents/
│   ├── Info.plist           (CFBundleIdentifier: ai.spendif.app)
│   ├── MacOS/
│   │   └── Spendif.ai       (script launcher eseguibile)
│   └── Resources/
│       └── spendifai.icns   (icona dell'app)
```

Dopo la creazione, l'installer chiama `mdimport` per registrare il bundle con
Spotlight immediatamente. In pochi secondi puoi:

- Premere **Cmd+Space**, digitare `Spendif` e premere **Invio** per avviare
- Trovare l'app nel **Launchpad**
- Trascinarla nel **Dock** da `/Applications`
- Aggiungerla agli **Elementi di login** tramite Impostazioni di sistema →
  Generali → Elementi di login

Il launcher del bundle `.app` apre una **finestra di Terminale** che mostra
l'output del server Streamlit. Chiudendo quella finestra di Terminale si ferma
il server.

---

## Come funziona la notifica di aggiornamento

Ogni volta che lanci Spendif.ai tramite il bundle `.app`, il launcher esegue in
background un `git fetch` e confronta il branch locale con `origin/main`.

Se la tua installazione è indietro:

1. Il launcher scrive `~/.spendifai/.update_available` con un messaggio del tipo
   `"3 commits behind origin/main"`
2. La **sidebar di Spendif.ai** legge questo file (con cache di 5 minuti) e
   mostra un badge giallo di avviso in alto:

   > 🔔 **Aggiornamento disponibile** (3 commits behind origin/main)
   > Per aggiornare, esegui da Terminale: ...

3. Il badge sparisce automaticamente al lancio successivo dopo l'aggiornamento

Questo controllo è completamente **non bloccante** — se `git fetch` fallisce
(no internet, firewall), l'app parte normalmente senza ritardi e senza messaggi
di errore.

---

## Aggiornamento manuale

Per aggiornare in qualsiasi momento senza passare dall'installer completo:

```bash
bash ~/Applications/Spendif.ai/packaging/macos/install.sh --update
```

`--update` fa esattamente tre cose:

1. `git fetch` + `git pull --ff-only` sul branch corrente
2. `uv sync` per installare le dipendenze nuove o aggiornate
3. `alembic upgrade head` per migrare lo schema del database (se il DB esiste)

**Non** ricrea il bundle `.app`, non modifica `.env`, e non tocca i tuoi modelli.
Dopo `--update`, chiudi e riapri l'app.

---

## Troubleshooting

### La compilazione Metal fallisce durante `uv sync`

**Sintomo:**
```
error: command '/usr/bin/clang' failed with exit code 1
```
oppure
```
GGML_METAL build failed
```

**Causa:** gli Xcode CLT non sono aggiornati o gli header del Metal SDK sono
mancanti.

**Fix:**
```bash
sudo rm -rf /Library/Developer/CommandLineTools
xcode-select --install
# Aspetta che l'installazione finisca, poi riprova:
bash ~/Applications/Spendif.ai/packaging/macos/install.sh --update
```

Se hai un'installazione completa di Xcode (non solo i CLT), esegui anche:
```bash
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
```

L'installer include un fallback solo CPU: se la compilazione Metal fallisce,
riprova senza i flag Metal. L'app funzionerà, ma l'inferenza LLM sarà più lenta
su Apple Silicon.

---

### Errore di permessi su `/Applications/`

**Sintomo:**
```
mkdir: /Applications/Spendif.ai.app: Permission denied
```

**Causa:** `/Applications` è gestita dal sistema e il tuo account potrebbe non
avere accesso in scrittura (raro su setup macOS standard, comune su Mac
gestiti/aziendali).

**Fix — opzione A (preferita):** installa in una directory custom:
```bash
bash install.sh --install-dir ~/Applications/Spendif.ai
```
Il bundle `.app` verrà creato nella tua home directory:
`~/Applications/Spendif.ai.app`

**Fix — opzione B:** concedi l'accesso con sudo (sconsigliato su Mac gestiti):
```bash
sudo bash install.sh
```

---

### Porta 8501 già in uso

**Sintomo:** il browser mostra "This site can't be reached" oppure Streamlit
stampa:
```
Address already in use: port 8501
```

**Causa:** una sessione precedente di Spendif.ai (o un'altra app Streamlit) è
ancora in esecuzione.

**Fix:**
```bash
# Trova e termina il processo che occupa la porta 8501
lsof -ti tcp:8501 | xargs kill -9
# Poi rilancia l'app normalmente
open -a Spendif.ai
```

Lo script launcher tenta già di terminare un processo Python esistente sulla
8501 prima di avviarne uno nuovo. Se persiste dopo un avvio normale, usa il
comando qui sopra.

---

### Versione di `python3` troppo vecchia (modalità default)

**Sintomo:**
```
✖  Python 3.9 found, but >= 3.11 required.
```

**Fix — opzione A:** usa la modalità Homebrew per ottenere Python 3.13:
```bash
bash install.sh --brew
```

**Fix — opzione B:** installa Python 3.13 manualmente da
[python.org](https://www.python.org/downloads/) e rilancia l'installer.

---

### `uv sync` si blocca compilando `llama-cpp-python`

Compilare `llama-cpp-python` con il supporto Metal può richiedere
**5–15 minuti** su macchine più lente. È un costo una tantum. Le installazioni
successive (es. dopo `--update`) saltano la ricompilazione se il pacchetto non
è cambiato.

Puoi monitorare il progresso eseguendo l'install in un Terminale visibile
invece di farlo via pipe da `curl`:

```bash
bash ~/Applications/Spendif.ai/packaging/macos/install.sh
```

---

### Icona dell'app non mostrata (icona generica nel Dock)

**Causa:** Pillow non era disponibile quando è stato eseguito `create_icon.py`,
quindi l'installer ha fatto fallback a un'icona di sistema generica.

**Fix:**
```bash
cd ~/Applications/Spendif.ai
uv pip install Pillow
uv run python packaging/macos/create_icon.py
# Copia l'icona generata nel bundle dell'app:
cp packaging/macos/spendifai.icns /Applications/Spendif.ai.app/Contents/Resources/spendifai.icns
# Forza il Finder a rinfrescare la cache delle icone:
touch /Applications/Spendif.ai.app
killall Dock
```

---

## Disinstallazione

Esegui il disinstaller interattivo:

```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.sh | bash
```

Lo script chiede separatamente se rimuovere ciascun componente:

| Componente | Posizione |
|---|---|
| Bundle dell'app | `/Applications/Spendif.ai.app` |
| Directory del codice | `~/Applications/Spendif.ai` (o `--install-dir` custom) |
| Database | `~/.spendifai/spendifai.db` |
| Modelli LLM | `~/.spendifai/models/` |
| Config e flag | `~/.spendifai/` |

Per disinstallare manualmente senza lo script:

```bash
rm -rf /Applications/Spendif.ai.app
rm -rf ~/Applications/Spendif.ai
# Solo se vuoi anche cancellare i tuoi dati finanziari e i modelli:
rm -rf ~/.spendifai
```
