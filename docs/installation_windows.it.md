# Spendif.ai — Guida all'installazione su Windows

*English version: [installation_windows.md](installation_windows.md).*

> **Ambito:** questa guida copre l'installazione nativa di Spendif.ai su
> Windows tramite lo script PowerShell di installazione. Per macOS vedi
> [installation_macos.md](installation_macos.md). Per Docker vedi
> [deployment.md](deployment.md).

---

## Requisiti di sistema

| Requisito | Minimo | Consigliato |
|---|---|---|
| Windows | 10 21H2 (build 19044) | 11 22H2 o successivo |
| PowerShell | 5.1 (incluso) | 7.4+ |
| winget | 1.4+ (opzionale, vedi sotto) | 1.6+ |
| RAM | 8 GB | 16 GB (per LLM da 12B parametri) |
| Disco | 5 GB liberi | 12 GB (Python + venv + modelli + dati) |
| Python | 3.13 (installato automaticamente) | 3.13 |
| Git | qualsiasi versione recente | installato automaticamente |
| GPU | opzionale | NVIDIA (per inferenza LLM accelerata via CUDA) |
| VRAM | — (solo CPU) | >= dimensione del modello (es. 8 GB per 7B Q4) |

**GPU NVIDIA (opzionale):** se viene rilevata una GPU NVIDIA, l'installer installa automaticamente una wheel CUDA 12.x di `llama-cpp-python`. Tutto funziona anche senza GPU — l'inferenza è semplicemente più lenta.

**Selezione del modello in base alla VRAM:** al primo avvio, Spendif.ai rileva la VRAM disponibile tramite `nvidia-smi`. Il modello scaricato automaticamente è dimensionato per stare nella VRAM, non nella RAM di sistema — ad esempio un PC con 32 GB di RAM ma 8 GB di VRAM scaricherà Qwen2.5-3B (2.1 GB), non Gemma-3-12B (6.8 GB).

**AMD / Intel Arc:** viene utilizzata automaticamente la modalità solo CPU. Il supporto Vulkan in `llama-cpp-python` è sperimentale e non viene abilitato da questo installer.

---

## Quick Start — One-Liner

Apri **PowerShell** (non CMD) e incolla:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; irm https://raw.githubusercontent.com/drake69/spendify/main/packaging/windows/install.ps1 | iex
```

Il prefisso `Set-ExecutionPolicy` è necessario perché PowerShell blocca per default gli script non firmati. Si applica solo alla sessione corrente — la policy di sistema non viene modificata in modo permanente.

Per ispezionare lo script prima di eseguirlo:

```powershell
# Scarica prima lo script
Invoke-WebRequest https://raw.githubusercontent.com/drake69/spendify/main/packaging/windows/install.ps1 -OutFile install.ps1
# Leggilo
notepad install.ps1
# Eseguilo (dalla stessa cartella)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\install.ps1
```

---

## Due modalità di installazione: winget vs Manuale

L'installer rileva se `winget` (Windows Package Manager) è disponibile e lo utilizza per installare Python e Git se necessari. Se winget è assente o fallisce, ricade su download diretti.

### Modalità winget (preferita)

`winget` è incluso in **Windows 10 21H2** e in tutte le release di Windows 11 come parte del pacchetto *App Installer*. L'installer esegue:

```
winget install Python.Python.3.13
winget install Git.Git
```

**Pro:**
- I pacchetti sono firmati e verificati da Microsoft
- Il PATH viene registrato automaticamente per l'utente corrente
- Gestisce correttamente conflitti di versione e aggiornamenti
- Non richiede interazione tramite browser

**Contro:**
- Richiede winget 1.4+ (i sistemi più vecchi potrebbero dover aggiornare App Installer)
- Gli ambienti aziendali a volte bloccano l'accesso di rete di winget
- Possono comparire dialog di consenso al primo utilizzo se eseguito fuori da un terminale

### Modalità manuale (fallback)

Se winget non viene trovato o fallisce, l'installer scarica direttamente gli installer:

| Pacchetto | URL sorgente |
|---|---|
| Python 3.13 | `https://www.python.org/ftp/python/3.13.3/python-3.13.3-amd64.exe` |
| Git | `https://github.com/git-for-windows/git/releases/...` |

Entrambi vengono eseguiti in modalità silenziosa (`/quiet` / `/VERYSILENT`) con installazione per-utente (nessuna elevazione UAC richiesta) e `PrependPath=1` per registrarli su `%PATH%`.

**Pro:**
- Funziona su sistemi dove winget non è disponibile o è bloccato
- Nessuna dipendenza dal Microsoft Store

**Contro:**
- I download possono essere bloccati da firewall aziendali restrittivi
- La versione è hardcoded nello script — aggiorna l'URL per release più recenti

### Entrambe le modalità: `uv` per la gestione delle dipendenze

Indipendentemente da come è stato ottenuto Python, entrambe le modalità usano `uv` per creare il virtual environment e installare tutte le dipendenze da `pyproject.toml` / `uv.lock`. `uv` viene installato automaticamente tramite `irm https://astral.sh/uv/install.ps1 | iex`, con fallback a `pip install uv` se lo script di bootstrap fallisce.

---

## Supporto GPU — Auto-rilevamento CUDA

Durante l'installazione lo script verifica la presenza di una GPU NVIDIA:

```powershell
Get-WmiObject Win32_VideoController | Where-Object { $_.Name -like "*NVIDIA*" }
```

### GPU NVIDIA trovata

L'installer tenta di installare la wheel pre-compilata CUDA 12.x di `llama-cpp-python` dall'indice non ufficiale di wheel mantenuto dall'autore della libreria:

```
https://abetlen.github.io/llama-cpp-python/whl/cu124/
```

Questa wheel è compatibile con i driver CUDA 12.x (versione driver ≥ 525). Se l'installazione della wheel fallisce per qualsiasi motivo (driver vecchio, errore di rete, mismatch ABI), l'installer riprova automaticamente con la wheel standard solo CPU da PyPI — l'app si avvia e funziona correttamente, semplicemente senza accelerazione GPU.

**Verifica dell'inferenza GPU dopo l'installazione:**

Nella pagina Impostazioni di Spendif.ai, sotto *LLM Backend → Local*, vedrai se `llama_cpp` è stato caricato con supporto CUDA. In alternativa:

```powershell
cd $env:LOCALAPPDATA\Spendif.ai
.venv\Scripts\python.exe -c "import llama_cpp; print(llama_cpp.llama_supports_gpu_offload())"
```

Restituisce `True` quando CUDA è attivo.

### Nessuna GPU NVIDIA

Viene installata la wheel solo CPU. L'inferenza funziona su qualsiasi hardware ma è significativamente più lenta per modelli grandi (12B parametri). Considera l'uso di una API key OpenAI o Anthropic per l'uso in produzione su macchine solo CPU.

---

## Struttura dei file dopo l'installazione

| Percorso | Contenuto |
|---|---|
| `%LOCALAPPDATA%\Spendif.ai\` | Codice (clone del repository git) |
| `%LOCALAPPDATA%\Spendif.ai\.venv\` | Virtual environment Python (uv) |
| `%LOCALAPPDATA%\Spendif.ai\launch.bat` | Launcher dell'app |
| `%APPDATA%\Spendif.ai\` | Directory dei dati utente |
| `%APPDATA%\Spendif.ai\spendifai.db` | Database SQLite (creato al primo avvio) |
| `%APPDATA%\Spendif.ai\models\` | File dei modelli LLM locali (.gguf) |
| `%APPDATA%\Spendif.ai\.update_available` | Flag di notifica aggiornamento (scritto dal launcher) |
| `%APPDATA%\Spendif.ai\install_path.txt` | Percorso della directory codice (usato dal verificatore di aggiornamenti) |

---

## Primo avvio

Cosa succede al primo avvio dipende dall'installer usato:

### Installazione MSIX (consigliata)

Se installato da `Spendif.ai.msix` (Start Menu → Spendif.ai), l'app apre una
**finestra nativa**. Niente finestra dei comandi, niente browser.

1. **Splash screen** (finestra pywebview con testo di avanzamento).
2. **Download modello AI parte in un thread di background** non appena il
   launcher si avvia. `core.model_manager.ensure_model_available()` sceglie
   il GGUF più grande che entra in VRAM (o RAM se non c'è GPU): Qwen2.5-1.5B
   (2-4 GB), Qwen2.5-3B (4-8 GB), Qwen2.5-7B (8-12 GB), Gemma-3-12B (12 GB+).
   Scaricato in `%USERPROFILE%\.spendifai\models\`. Progresso scritto ad ogni
   chunk su `%USERPROFILE%\.spendifai\model_download.status`.
3. **`.env` viene scritto** in `%USERPROFILE%\.spendifai\.env`
   (`LLM_BACKEND=local_llama_cpp`, `SPENDIFAI_DB=sqlite:///...ledger.db`).
4. **Streamlit parte dentro la stessa finestra** *in parallelo* al download
   del modello — il wizard appare in pochi secondi e il modello continua a
   scaricarsi in background.
5. **Wizard di onboarding** (4 step): lingua, titolari, conti, riepilogo.
   Un banner live in cima ad ogni step mostra il progresso del download
   così puoi seguirlo mentre compili il wizard.
6. **Step riepilogo — bottone "Avvia" GATED sul completamento del download.**
   Puoi compilare il wizard mentre il modello si scarica. Premendo "Avvia"
   si attende il 100% (il bottone resta disabilitato con indicatore
   "⏳ Attendi modello AI — 78% · ~3 min") prima di applicare le
   impostazioni, scrivere `llama_cpp_model_path` nel DB e marcare
   `onboarding_done`. Garanzia: la pagina Import funziona dal primo click
   subito dopo la fine del wizard.
7. **App pronta.** Dai successivi avvii gli step 2-3 sono saltati (modello
   già su disco) e 5-6 (wizard già completato).

> Spazio libero richiesto al primo avvio: ~5 GB (modello + stato Python).
> L'utente è produttivo nel wizard in pochi secondi — l'unico "blocco" è
> sul bottone finale Avvia se finisce il wizard prima del download.

### Installazione via script (legacy `install.ps1`)

Se installato via `irm ... | iex` (script PowerShell):

1. Doppio click sul collegamento **Spendif.ai** sul Desktop (oppure trovalo nello Start Menu)
2. Si apre una finestra dei comandi che mostra l'avvio del server Streamlit
3. Il browser predefinito si apre automaticamente all'indirizzo `http://localhost:8501` dopo circa 4 secondi
4. SQLAlchemy crea `%APPDATA%\Spendif.ai\spendifai.db` al primo accesso al database
5. La procedura di onboarding ti guida nella configurazione del backend LLM

Il database **non viene mai** creato o modificato dall'installer — solo dall'app stessa. Puoi rieseguire l'installer o eseguire `-Update` in sicurezza senza toccare i tuoi dati finanziari.

### Migrazione da un'installazione esistente

Se hai un database Spendif.ai esistente, passalo in fase di installazione:

```powershell
.\install.ps1 -CopyDb C:\path\to\old_ledger.db
```

L'installer lo copia in `%APPDATA%\Spendif.ai\spendifai.db` ed esegue immediatamente `alembic upgrade head` per applicare eventuali migrazioni di schema pendenti.

---

## Collegamenti Start Menu e Desktop

L'installer crea due collegamenti `.lnk`:

| Collegamento | Percorso |
|---|---|
| Start Menu | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Spendif.ai.lnk` |
| Desktop | `%USERPROFILE%\Desktop\Spendif.ai.lnk` |

Entrambi i collegamenti invocano:

```
cmd.exe /c "%LOCALAPPDATA%\Spendif.ai\launch.bat"
```

**Icona:** i collegamenti usano `%SystemRoot%\System32\shell32.dll,13`
(icona moneta/denaro dalla libreria di icone integrata di Windows), che
non richiede file aggiuntivi. Se `packaging\macos\spendifai_256.png` è
presente (generata da `create_icon.py`), viene segnalato nell'output
dell'installer ma per affidabilità del `.lnk` viene comunque usata
l'icona shell32 — sostituisci la variabile `$IconPath` nello script se
converti il PNG in `.ico`.

---

## Come funziona la notifica degli aggiornamenti

Ogni volta che avvii Spendif.ai tramite un collegamento, `launch.bat`
esegue in background un `git fetch` e confronta il branch locale con
`origin/main`.

Se la tua installazione è indietro:

1. Il launcher scrive `%APPDATA%\Spendif.ai\.update_available` con un
   messaggio tipo `"3 commits behind origin/main"`
2. La **sidebar di Spendif.ai** legge questo file tramite
   `ui/components/update_checker.py` (cache di 5 minuti) e mostra un
   badge giallo di avviso:

   > Update available (3 commits behind origin/main)
   > To update, run: .\install.ps1 -Update

3. Il badge scompare automaticamente al successivo avvio dopo
   l'aggiornamento

Il controllo è completamente **non bloccante** — `launch.bat` avvia il
git fetch in un processo di background sganciato (`start /b`). Se il
fetch fallisce (offline, firewall), l'app si avvia normalmente senza
ritardi e senza messaggi di errore.

Il meccanismo è identico al launcher macOS; `update_checker.py` legge
lo stesso percorso `~/.spendifai/.update_available` (che su Windows
viene risolto in `%APPDATA%\Spendif.ai\.update_available` perché
`Path.home()` di Python restituisce la directory del profilo utente
su Windows).

> **Nota:** su Windows, `Path.home()` in Python restituisce
> `C:\Users\<user>`, e ci si aspetta il file di flag in
> `C:\Users\<user>\AppData\Roaming\Spendif.ai\.update_available`
> (cioè `%APPDATA%\Spendif.ai\.update_available`). Il file
> `update_checker.py` attualmente ha hardcoded
> `Path.home() / ".spendifai" / ".update_available"` — se sei su
> Windows dovrai allineare il percorso. Vedi l'issue tracker GitHub
> per il task di tracking del percorso cross-platform.

---

## Aggiornamento manuale

Per aggiornare in qualsiasi momento senza ripassare per l'installer
completo:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& "$env:LOCALAPPDATA\Spendif.ai\packaging\windows\install.ps1" -Update
```

Oppure, se hai scaricato lo script localmente:

```powershell
.\install.ps1 -Update
```

`-Update` fa esattamente tre cose:

1. `git fetch` + `git pull --ff-only` sul branch corrente
2. `uv sync` per installare le dipendenze nuove o aggiornate
3. `alembic upgrade head` per migrare lo schema del database (se il
   DB esiste)

**Non** ricrea i collegamenti, non modifica `.env` e non tocca i tuoi
modelli o il contenuto del database. Dopo `-Update`, chiudi e riapri
l'app.

---

## Tutti i parametri dell'installer

```
.\install.ps1 [OPTIONS]

-Brew               No-op (accettato per parità CLI con l'installer macOS)
-InstallDir <path>  Directory del codice (default: %LOCALAPPDATA%\Spendif.ai)
-Branch <branch>    Branch git (default: main)
-CopyDb <path>      Copia un DB SQLite esistente in %APPDATA%\Spendif.ai\spendifai.db
-CopyModels <path>  Copia la directory dei modelli in %APPDATA%\Spendif.ai\models\
-Launch             Avvia l'app immediatamente dopo l'installazione
-Update             Solo aggiornamento (git pull + uv sync + alembic)
-Help               Mostra l'help
```

---

## Troubleshooting

### Errore ExecutionPolicy

**Sintomo:**
```
File install.ps1 cannot be loaded because running scripts is disabled on this system.
```

**Soluzione:** esegui questo nella stessa sessione PowerShell prima di
lanciare lo script:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

Modifica la policy solo per il processo corrente — non incide sulla
configurazione di sistema e viene resettata alla chiusura di
PowerShell. Se la tua policy IT impedisce anche gli override
per-processo, chiedi all'amministratore di permettere l'esecuzione di
script firmati, oppure usa il deployment basato su Docker.

---

### winget non trovato

**Sintomo:**
```
!  winget not found -- will use direct download fallback for Python and Git
```

**Causa:** winget è incluso in Windows 10 21H2+ come parte del pacchetto
*App Installer*. Su sistemi più vecchi o edizioni LTSC potrebbe essere
assente.

**Soluzione — opzione A (consigliata):** aggiorna App Installer dal
Microsoft Store o scaricalo da https://aka.ms/getwinget

**Soluzione — opzione B:** non fare nulla — l'installer ricadrà
automaticamente sui download diretti da python.org e git-scm.com.

---

### Python non trovato sul PATH dopo l'installazione

**Sintomo:**
```
x  Python installed but 'python' command not found on PATH.
    Open a new PowerShell and re-run the installer.
```

**Causa:** l'installer di Python registra `%PATH%` nel registry di
Windows, ma la sessione PowerShell corrente è iniziata prima
dell'installazione e non vede la modifica.

**Soluzione:** chiudi la finestra PowerShell, aprine una nuova e
rilancia:
```powershell
.\install.ps1
```

In alternativa, l'installer tenta di ricaricare automaticamente
`%PATH%` dal registry — questo funziona nella maggior parte dei casi
ma non quando la shell di Windows necessita di un riavvio completo
per propagare la modifica.

---

### Porta 8501 già in uso

**Sintomo:** il browser mostra "This site can't be reached" oppure
`launch.bat` stampa:
```
Error: [Errno 10048] error while attempting to bind on address ('0.0.0.0', 8501): only one usage of each socket address...
```

**Causa:** una sessione precedente di Spendif.ai (o un'altra app
Streamlit) è ancora in esecuzione.

**Soluzione:**
```powershell
# Trova e termina il processo che usa la porta 8501
netstat -ano | findstr :8501
# Annota il PID (ultima colonna), poi:
taskkill /PID <PID> /F
```

Quindi rilancia tramite il collegamento.

---

### Installazione della wheel CUDA fallita — fallback su CPU

**Sintomo** (visibile nell'output dell'installer):
```
!  CUDA wheel install failed: ... -- falling back to CPU-only build
v  llama-cpp-python installed (CPU-only fallback)
```

**Causa:** incompatibilità della wheel CUDA (driver troppo vecchio,
versione CUDA errata) o errore di rete nel raggiungere
`abetlen.github.io`.

**Implicazione:** l'app si avvia e funziona correttamente in modalità
solo CPU. L'inferenza LLM è più lenta su CPU.

**Soluzione — aggiorna il driver NVIDIA:**
1. Scarica l'ultimo driver Game Ready o Studio da https://www.nvidia.com/drivers
2. Installa e riavvia
3. Rilancia l'installer: `.\install.ps1 -Update`

**Soluzione — wheel CUDA manuale:**
```powershell
cd $env:LOCALAPPDATA\Spendif.ai
.venv\Scripts\pip install llama-cpp-python `
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 `
    --force-reinstall --no-deps
```

Sostituisci `cu124` con `cu121` o `cu122` se il tuo driver supporta
una versione precedente del toolkit CUDA.

---

### uv non trovato dopo il bootstrap

**Sintomo:**
```
x  Could not install uv.
```

**Soluzione — installazione manuale di uv:**
```powershell
pip install uv
```
oppure scarica l'installer ufficiale da https://docs.astral.sh/uv/getting-started/installation/ e aggiungi `%USERPROFILE%\.local\bin` al tuo `%PATH%`.

---

## Disinstallazione

Non esiste ancora un disinstaller automatico per l'installazione
nativa Windows. Per rimuovere Spendif.ai manualmente:

**1. Rimuovi il codice e il virtual environment:**
```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Spendif.ai"
```

**2. Rimuovi i collegamenti:**
```powershell
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Spendif.ai.lnk" -ErrorAction SilentlyContinue
Remove-Item "$env:USERPROFILE\Desktop\Spendif.ai.lnk" -ErrorAction SilentlyContinue
```

**3. Rimuovi i dati utente (solo se vuoi cancellare il tuo database
finanziario e i modelli):**
```powershell
# ATTENZIONE: questo cancella i tuoi dati finanziari in modo permanente
Remove-Item -Recurse -Force "$env:APPDATA\Spendif.ai"
```

Python e Git **non** vengono disinstallati — sono stati installati a
livello di sistema e potrebbero essere usati da altre applicazioni.
Disinstallali tramite *Impostazioni → App* se non più necessari.

---

## macOS vs Windows — Differenze principali

| Aspetto | macOS | Windows |
|---|---|---|
| Formato installer | Script Bash (`install.sh`) | Script PowerShell (`install.ps1`) |
| Installazione Python | Homebrew (`--brew`) o Python di sistema | winget o download diretto da python.org |
| Package manager | `uv` (uguale) | `uv` (uguale) |
| Accelerazione GPU | Metal (Apple Silicon, automatica) | CUDA 12.x (NVIDIA, auto-rilevata) |
| Directory del codice | `~/Applications/Spendif.ai/` | `%LOCALAPPDATA%\Spendif.ai\` |
| Directory dati utente | `~/.spendifai/` | `%APPDATA%\Spendif.ai\` |
| Percorso database | `~/.spendifai/spendifai.db` | `%APPDATA%\Spendif.ai\spendifai.db` |
| Launcher dell'app | Bundle `.app` (indicizzato da Spotlight) | `launch.bat` + collegamenti `.lnk` |
| Avvio da | Spotlight (`Cmd+Space`) / Launchpad | Start Menu / collegamento Desktop |
| Notifica aggiornamento | `~/.spendifai/.update_available` | `%APPDATA%\Spendif.ai\.update_available` |
| Comando di aggiornamento | `bash .../install.sh --update` | `.\install.ps1 -Update` |
| llama-cpp-python | Compilato da sorgente (flag Metal) | Wheel pre-compilata (CPU o CUDA) |
| ExecutionPolicy | Non applicabile | `Bypass` necessario per script non firmati |
| Installazione one-liner | `curl ... \| bash` | `irm ... \| iex` |
