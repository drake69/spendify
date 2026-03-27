# Spendify — Guida Utente

> Tutto quello che ti serve per usare Spendify in meno di 10 minuti.

---

## L'idea in una frase

Scarichi i file movimenti dalla banca, li trascini in Spendify, lui li unifica, li classifica e ti dice dove vanno i tuoi soldi. Fine.

---

## 1. Primo avvio — wizard di onboarding

**Situazione:** Hai appena installato Spendify e lo avvii per la prima volta.

L'app ti mostra automaticamente il **wizard di configurazione iniziale** (4 step). Non devi cercare nessun menu.

1. **Step 1 — Lingua:** scegli la lingua della tassonomia. Il wizard suggerisce la lingua del tuo browser. Questa scelta imposta anche il formato delle date e i separatori numerici (es. `31/12/2025` con separatore `,` per l'italiano).
2. **Step 2 — Titolari:** inserisci il tuo nome (e le varianti usate dalla banca — es. `Mario Rossi, ROSSI MARIO`). Questi nomi vengono usati per proteggere la tua privacy nei prompt LLM e per riconoscere i tuoi bonifici interni.
3. **Step 3 — Conti:** aggiungi i tuoi conti bancari (nome + banca + tipo conto). Il tipo conto è obbligatorio e indica lo strumento finanziario: *Conto corrente*, *Carta di credito*, *Carta di debito*, *Carta prepagata*, *Conto risparmio* o *Contanti*. Puoi saltare questo step e aggiungerli dopo dalle Impostazioni.
4. **Step 4 — Conferma:** controlla il riepilogo e clicca **Inizia!** — solo a questo punto i dati vengono salvati.

> **Aggiornamento da versione precedente?** Il wizard non compare se il database ha già dati — l'app si apre direttamente come sempre.

---

## 2. Prima importazione

**Situazione:** Hai completato il wizard e vuoi caricare i tuoi file movimenti.

1. Vai su **Import** (il primo pulsante in alto a sinistra).
2. Trascina uno o più file nell'area tratteggiata — vanno bene CSV, XLSX, XLS tutti insieme.
3. Per ogni file scegli il conto bancario associato dal menu a tendina.
4. Clicca **Avvia elaborazione**.
5. Aspetta la barra verde. Puoi chiudere il browser e riaprirlo: il lavoro continua in background.

> **Esempio:** Hai tre file — `estratto_unicredit_gen.csv`, `carta_visa_gen.xlsx`, `conto_deposito.csv`. Li selezioni tutti e tre in una volta sola. Spendify capisce da solo che tipo sono.

**Cosa succede dietro le quinte:** Spendify assegna a ogni transazione un codice univoco basato sul contenuto. Se importi lo stesso file due volte non succede nulla di male — i duplicati vengono scartati silenziosamente.

> **In arrivo:** e prevista una pagina **Storico import** che mostrera la cronologia di tutte le importazioni eseguite (data, file, conto, numero transazioni). Sara possibile annullare un'importazione eliminando in blocco tutte le transazioni di quel batch.

### Importazione automatica e revisione schema

Spendify analizza la struttura di ogni file e calcola un **punteggio di confidenza** (da 0 a 100%) su quanto ha capito del formato.

- **Confidenza >= 80%** — l'importazione procede in automatico, senza chiederti nulla.
- **Confidenza < 80%** — compare un form di revisione dove puoi verificare le colonne rilevate (data, importo, descrizione, tipo documento) e correggerle se necessario. Dopo la conferma manuale, la confidenza sale a 100%.

Una volta confermato lo schema di un file, tutte le importazioni successive dello stesso formato saranno automatiche — Spendify ricorda la struttura.

> **Conto pre-selezionato:** se il formato del file corrisponde a uno schema gia importato in passato per un certo conto, Spendify pre-seleziona automaticamente quel conto nel menu a tendina. Non serve sceglierlo ogni volta.

> **Avviso primo caricamento:** quando carichi un file con un formato mai visto prima (primo upload) e il file contiene meno di 50 righe, compare un avviso giallo: *"Per un riconoscimento ottimale, carica il file movimenti cosi come scaricato dalla banca, senza modifiche, idealmente con 250-300 transazioni."* Questo perche il rilevamento automatico dello schema funziona meglio con piu dati.

### Righe da saltare — quando compare questo campo?

Alcuni file di banca hanno righe di intestazione prima della tabella dati (nome della banca, periodo, numero conto…) e righe di riepilogo/totali in fondo. Spendify usa un'analisi basata sulla densità dei dati per individuare automaticamente dove inizia e dove finisce la tabella reale, rimuovendo sia le righe di contorno in alto che i totali in basso. Nella maggior parte dei casi non serve impostare nulla manualmente.

Se però il rilevamento automatico **non è riuscito** (file con formato insolito, tutto numerico, senza intestazioni testuali), comparirà il campo **"Righe da saltare"** accanto al nome del file. Inserisci quante righe vuoi saltare prima dell'intestazione della tabella.

> **Esempio:** Apri il file CSV con un editor di testo. Se le prime 3 righe sono `Banca XYZ`, `Conto 123`, `Dal 01/01 al 31/01` e la riga 4 è `Data,Importo,Descrizione`, inserisci `3`.

Una volta confermato lo schema del file, alle importazioni successive non vedrai più questo campo — Spendify lo ricorda automaticamente.

### Riepilogo importazione

Al termine dell'elaborazione, per ogni file importato viene mostrato un riepilogo dettagliato:

| Metrica | Significato |
|---------|-------------|
| **Righe E/C** | Numero totale di righe dati nel file movimenti (escluse intestazioni) |
| **Importate** | Nuove transazioni salvate nel database |
| **Già presenti** | Transazioni già importate in precedenza (duplicate, saltate) |
| **Giroconti** | Trasferimenti interni rilevati (tooltip con dettaglio). I giroconti vengono **sempre salvati** nel database, anche con modalità "Escludi" — l'impostazione controlla solo la visibilità nelle viste |
| **Scartate** | Righe che non è stato possibile importare |

Se ci sono righe scartate, un avviso mostra il motivo per ciascuna:
- **Data mancante** — la cella della data è vuota
- **Data non parsabile** — il formato della data non corrisponde allo schema
- **Importo non parsabile** — il valore dell'importo non è riconoscibile
- **Importo: entrambe le colonne Dare/Avere vuote** — nei file con colonne separate per dare e avere, nessuna delle due contiene un valore per quella riga

Puoi espandere il dettaglio per vedere i dati originali di ogni riga scartata, utile per capire se c'è un problema nel file o nello schema.

> **Consiglio:** se i numeri non tornano (es. righe nel file ≠ somma di importate + già presenti + scartate + intestazione), potrebbe indicare un problema nello schema. Prova a cancellare lo schema salvato da ⚙️ Impostazioni e reimportare il file.

---

## 3. Il Ledger: vedere tutte le transazioni

**Situazione:** Vuoi controllare cosa è stato importato.

Vai su **Ledger**. Trovi la lista completa in ordine cronologico, con filtri per data, conto, categoria e tipo (entrata/uscita).

> **Esempio:** Vuoi vedere solo le spese di gennaio 2025 sul conto corrente. Imposti il filtro data e selezioni il conto. La tabella si aggiorna subito.

**Filtri rapidi di periodo:** usa i pulsanti in cima — *Mese corrente*, *Mese precedente* (si sposta mese per mese ogni volta che lo premi), *Ultimi 3 mesi*, *Anno corrente*, *♾ Tutto* (azzera tutti i filtri contemporaneamente).

**Opzioni aggiuntive nella seconda riga filtri:**
- ☑ **Nascondi giroconti** — esclude bonifici tra tuoi stessi conti dai risultati (default: segue l'impostazione globale)
- ☑ **Mostra raw** — aggiunge la colonna "Raw description" nella tabella per confrontare il testo originale della banca con quello rielaborato

**Colonne indicatore (emoji, sola lettura):**
- ⚠️ = da rivedere (la classificazione automatica non era sicura) — mostra "·" quando inattivo
- ✅ = transazione validata dall'utente — mostra "·" quando inattivo
- 🔄 = giroconto interno (es. bonifico tra tuoi conti, escluso dai totali) — mostra "·" quando inattivo

**Colonne checkbox (dopo le emoji):**
- **Validato ☐** — checkbox cliccabile per validare/invalidare una transazione. Il salvataggio è immediato (non serve premere Salva). Quando validi, il flag ⚠️ viene automaticamente rimosso. Quando rimuovi la validazione, `human_validated` torna a False.
- **🔄 Giroconto ☐** — checkbox cliccabile per segnare/rimuovere un giroconto (si salva con il pulsante Salva)

**Ordine colonne (parte destra della griglia):**
`... | Fonte | ⚠️ | ✅ | 🔄 | Validato ☐ | 🔄 Giroconto ☐`

**Colonna Fonte (tracking classificazione):**
- Mostra chi ha assegnato la categoria corrente: 🧠 AI, 📏 Regola, 👤 Manuale, 📚 Storico

**Validazione in blocco:** seleziona una o più transazioni e clicca **Valida selezionate** per confermarle tutte in una volta.

> **Cosa significa "Validato"?** Validare una transazione significa dire a Spendify: "ho visto questa spesa e confermo che è corretta (non è anomala)". La validazione riguarda la **spesa**, non la categoria. Se una regola o l'AI riclassificano la transazione in un secondo momento, la validazione rimane attiva: la fonte (AI, Regola, Manuale, Storico) cambia, ma il flag "Validato" non viene toccato. Solo un click esplicito sulla checkbox "Validato" (deselezionandola) può rimuovere la validazione.

### Creare regole dal Ledger

Seleziona una riga nella colonna di selezione (📏) e clicca **Crea regola e applica**: compare un form pre-compilato con il pattern estratto dalla controparte, la categoria e il contesto della transazione. Un'anteprima mostra quante transazioni verranno matchate. Se la regola esiste gia, viene mostrato un avviso giallo e il pulsante diventa **Modifica regola e applica**. Dopo la conferma, la regola si applica retroattivamente a tutte le transazioni corrispondenti. Un toast conferma "Regola creata" o "Regola aggiornata".

> **Attenzione:** puoi selezionare solo **una riga alla volta** per creare una regola. Se ne selezioni piu di una, l'app mostra un errore.

Per il flusso completo, vedi la [Guida alla Classificazione](guida_classificazione.md).

---

## 4. Review: le transazioni da controllare

**Situazione:** Spendify non era sicuro di alcune classificazioni e le ha messe in attesa.

Vai su **Review**. Trovi le transazioni con il ⚠️. Per ognuna puoi:
- Cambiare categoria/sottocategoria dal menu a tendina
- Confermare cliccando **Salva**

**Colonne indicatore (emoji, sola lettura):**
- ⚠️ = da rivedere — mostra "·" quando inattivo
- ✅ = transazione validata dall'utente — mostra "·" quando inattivo

**Colonne checkbox (dopo le emoji):**
- **Validato ☐** — checkbox cliccabile per validare/invalidare una transazione. Il salvataggio è immediato. Quando validi, il flag ⚠️ viene automaticamente rimosso.

**Colonna Fonte (tracking classificazione):**
- Badge che indica chi ha assegnato la categoria: 🧠 AI, 📏 Regola, 👤 Manuale, 📚 Storico

**Validazione in blocco:** seleziona le transazioni di cui sei sicuro e clicca **Valida selezionate** per confermarle tutte in una volta. Quando validi una transazione stai dicendo a Spendify: "ho visto questa spesa e confermo che va bene". La validazione non cambia la categoria e non viene rimossa se la categoria cambia in seguito (per regola o AI). Solo un click esplicito sulla checkbox la rimuove.

> **Esempio:** "PAGAMENTO POS 00112 FARMACIA CENTRALE" è stato classificato come *Casa* ma tu sai che è *Salute*. Lo correggi una volta, e se hai salvato una regola quella correzione si applicherà automaticamente alle prossime importazioni.

### Rielabora con LLM
In fondo alla pagina Review c'è il pulsante **🔄 Rielabora con LLM**. Quando vedi `(N descrizioni non pulite)` significa che alcune transazioni hanno ancora la descrizione grezza della banca senza essere state elaborate. Cliccalo per riprocessarle.

> **Quando capita:** Se durante l'importazione il modello AI era offline, le transazioni vengono importate comunque ma con la descrizione originale. Questo pulsante le recupera appena il modello torna disponibile.

---

## 5. Regole: non correggere la stessa cosa due volte

**Situazione:** Ogni mese arriva "ADDEBITO SDD ENEL ENERGIA" e ogni mese lo devi correggere a mano.

Vai su **Regole**, crea una nuova regola:
- **Pattern:** `ENEL ENERGIA`
- **Categoria:** Utenze → Elettricità

Da quel momento in poi, ogni transazione che contiene quelle parole viene classificata automaticamente — sia nelle importazioni future che in quelle già presenti nel database.

**Riesegui tutte le regole in blocco**

Se hai creato molte regole in sessioni diverse e vuoi applicarle tutte in una volta a tutto il tuo storico, usa il pulsante **▶️ Esegui tutte le regole** in fondo alla sezione. Spunta la casella di conferma e clicca il pulsante: tutte le regole vengono applicate a ogni transazione del ledger, non solo a quelle in attesa di revisione. Al termine ti dirà quante transazioni sono state aggiornate.

> **Esempio pratico con tre tipi di regola:**
> - *Uguale esatto:* `NETFLIX.COM` → Abbonamenti → Streaming (corrisponde solo se la descrizione è esattamente quella)
> - *Contiene il testo:* `ESSELUNGA` → Alimentari → Supermercato (corrisponde se la parola appare ovunque nella descrizione)
> - *Espressione avanzata:* `RATA \d+/\d+` → Casa → Mutuo (corrisponde a "RATA 3/12", "RATA 10/12" ecc.)

---

## 6. Modifiche massive: categoria, contesto e eliminazione in blocco

**Situazione:** Hai importato anni di movimenti e vuoi pulire dati sbagliati o rimuovere un intero conto che non vuoi più tracciare.

Vai su **✏️ Modifiche massive**. La pagina è divisa in due aree principali.

### 6a — Operazioni su transazione di riferimento

1. Cerca e seleziona una transazione dal menu a tendina (puoi filtrare per testo o mostrare solo quelle ⚠️ da rivedere)
2. Spendify mostra quante altre transazioni hanno la stessa descrizione o una simile (Jaccard ≥ 35%)
3. Poi scegli cosa fare:
   - **2a Giroconto** — segna/rimuovi come bonifico interno, con un click propaga a tutte le tx con la stessa descrizione
   - **2b Contesto** — assegna un contesto (es. "Vacanza") alla singola tx o a tutte le simili
   - **2c Categoria** — corregge categoria e sottocategoria, salva una regola deterministica per i file futuri, applica subito alle simili

### 6b — Eliminazione massiva da filtro

**Situazione:** Vuoi cancellare tutte le transazioni di un conto chiuso, o tutte quelle di un periodo di test.

1. Imposta i filtri (date, conto, tipo, descrizione, categoria) — almeno uno è obbligatorio
2. Il contatore mostra subito quante transazioni verranno cancellate
3. Clicca **👁 Anteprima** per vedere le prime 10 righe prima di procedere
4. Digita **`ELIMINA`** nel campo di conferma — solo allora il pulsante si abilita
5. Clicca il pulsante rosso

> ⚠️ **L'eliminazione è irreversibile.** Fai sempre un backup del file `ledger.db` prima di eliminare grandi quantità di dati (vedi la guida Deployment).

> **Esempio:** Hai importato per errore il file movimenti di un conto corrente che non è tuo. Filtri per conto, vedi 200 transazioni nell'anteprima, digiti ELIMINA e le rimuovi in un colpo solo.

---

## 7. Correggere le descrizioni in blocco (pagina Review)

**Situazione:** La banca scrive "SOTTOSCRIZIONI FONDI E SICAV SOTTOSCRIZIONE ETICA AZIONARIO R DEP.TITOLI 081/663905/000" — un disastro leggibile. Vuoi sostituirla con "Fondo Etico Azionario" per tutte le occorrenze.

Vai su **Review**, scorri in fondo, apri il pannello **✏️ Correggi descrizione in blocco**:
1. Incolla la descrizione grezza nel campo *Pattern*
2. Scrivi la descrizione leggibile nel campo *Descrizione pulita*
3. Clicca **Applica e salva regola**

Tutte le transazioni che corrispondono vengono aggiornate immediatamente e la regola viene salvata per i file futuri.

---

## 8. Analytics: i grafici

**Situazione:** Vuoi capire dove spendi di più.

Vai su **Analytics**. Trovi:
- Grafico a torta spese per categoria
- Andamento mensile entrate/uscite
- Saldo netto nel tempo

Usa i filtri in alto per restringere a un periodo o a un conto specifico.

### Associazioni controparte (auto-apprendimento)

Nella sezione Analytics trovi anche la vista **Associazioni controparte → categoria**, che mostra come Spendify ha imparato ad associare ogni controparte (es. ESSELUNGA, AMAZON) alle categorie nel tempo.

Per ogni controparte vengono mostrati:
- **Categoria/Sottocategoria** più frequente tra le transazioni validate
- **Conteggio** delle ricorrenze validate
- **Omogenità** — un indicatore da 0 a 1 che misura quanto la controparte è "stabile" nella classificazione:
  - 🟢 **≥ 0.90** — auto-categorizzabile (es. ESSELUNGA: sempre Alimentari)
  - 🟡 **0.50–0.89** — mista (es. ROSSOPOMODORO: quasi sempre Ristorazione, a volte Vacanze)
  - 🔴 **< 0.50** — eterogenea (es. AMAZON: Tecnologia, Alimentari, Abbigliamento...)

> **Come funziona:** Spendify conta solo le transazioni che hai **validato** (checkbox ✅ nel Ledger o nella Review). Più transazioni validi, più il sistema impara. Quando importi nuovi file, le controparti con alta omogenità vengono classificate automaticamente dallo storico — senza chiamare l'AI.

---

## 9. Check List: tutto in ordine?

**Situazione:** Vuoi controllare a colpo d'occhio se stai importando regolarmente tutti i tuoi file movimenti, senza buchi di mesi.

Vai su **✅ Check List**. Trovi una tabella con:
- **Una riga per ogni mese**, dal mese corrente in poi verso il passato
- **Una colonna per ogni conto** che hai configurato in Impostazioni

Ogni cella mostra il numero di transazioni importate per quel mese e quel conto. Se il numero è **—** (grigio), non hai transazioni per quella combinazione.

> **Esempio pratico:** Hai tre conti — Conto Corrente, Carta Visa, Conto Deposito. Guardi la check list e vedi che Luglio 2024 ha "—" per il Conto Deposito. Significa che non hai mai importato i movimenti del conto deposito di quel mese. Vai a scaricarli dalla banca e importali.

**Come leggere i colori:**
- **—** grigio = nessuna transazione
- 🔵 azzurro chiaro = 1–4 transazioni (poche — forse manca qualcosa?)
- 🔵 azzurro medio = 5–19 transazioni (normale)
- 🔵 azzurro scuro = ≥ 20 transazioni (mese pieno)

**Filtri utili:**
- *Mostra solo conti* — confronta solo i conti che ti interessano
- *Ultimi N mesi* — focalizza su un periodo recente
- *Nascondi mesi senza transazioni* — rimuove i mesi in cui non hai ancora dati per nessun conto

Puoi scaricare la tabella come CSV con il pulsante **⬇️ Scarica CSV**.

---

## 10. Impostazioni: cambiare il modello AI

**Situazione:** Vuoi usare un modello diverso per la classificazione (es. Claude invece di Ollama locale).

Vai su **Impostazioni**:
- **Backend LLM:** scegli tra llama.cpp (locale, gratuito, default per nuove installazioni), Ollama (locale, gratuito), OpenAI, Claude, o qualsiasi provider OpenAI-compatible (Groq, Google AI Studio, ecc.)
- **Modello:** specifica il nome del modello (es. `gpt-4o-mini`, `claude-3-5-haiku-20241022`, `gemma2-9b-it`) oppure seleziona un file `.gguf` per llama.cpp
- **API Key:** inserisci la chiave se usi un servizio remoto

> **Nota sulla privacy:** Se usi un backend remoto (OpenAI o Claude), Spendify rimuove automaticamente IBAN, numeri carta, codice fiscale e nome del titolare prima di inviare qualsiasi dato.

Per istruzioni dettagliate su dove registrarsi e come ottenere le API key di ogni provider, consulta il **[Manuale di Configurazione](configurazione.md)**.

### Tipo conto

Ogni conto ha un **tipo** obbligatorio che indica lo strumento finanziario:

| Tipo | Etichetta |
|------|-----------|
| `bank_account` | Conto corrente |
| `credit_card` | Carta di credito |
| `debit_card` | Carta di debito |
| `prepaid_card` | Carta prepagata |
| `savings_account` | Conto risparmio |
| `cash` | Contanti |

> Solo la **carta di credito** richiede un trattamento speciale (inversione del segno: le spese nel CSV sono positive ma vanno registrate come uscite). Carta di debito e prepagata hanno comportamento del segno identico, ma sono valori separati perché l'etichetta è chiara per l'utente. Il formato del file (colonna unica, dare/avere, ecc.) viene rilevato automaticamente da Spendify — tu devi solo indicare *che tipo di strumento è*.

### Rinominare un conto

Puoi rinominare un conto bancario in qualsiasi momento da **⚙️ Impostazioni → 🏦 Conti bancari**. Quando rinomini un conto, Spendify ricalcola automaticamente l'identificativo univoco di ogni transazione associata, perche il nome del conto fa parte della chiave di calcolo.

L'operazione e **atomica**: se qualcosa va storto durante il ricalcolo, nessun dato viene modificato. I tuoi dati restano sempre integri.

> **In pratica:** rinomina il conto senza preoccupazioni. Le transazioni, le categorie, le regole e i giroconti associati rimangono intatti. L'unica cosa che cambia e l'identificativo tecnico interno (invisibile a te).

### Scaricare un modello (llama.cpp)

Se usi llama.cpp come backend (default per nuove installazioni), puoi scaricare un modello GGUF direttamente dall'app:

1. Vai su **⚙️ Impostazioni → 🤖 Configurazione LLM**
2. Seleziona il backend **llama.cpp (locale)**
3. Scegli un modello suggerito (es. `gemma-2-2b-it-Q4_K_M`, ~1.6 GB) oppure incolla un URL diretto
4. Clicca **⬇️ Scarica**

I modelli vengono salvati in `~/.spendify/models/`. La sezione **Modelli locali** mostra i file `.gguf` disponibili, con percorso e dimensione.

### Scaricare un modello (Ollama)

Se usi Ollama come backend, puoi scaricare o aggiornare il modello direttamente dall'app senza aprire il terminale:

1. Vai su **⚙️ Impostazioni → 🤖 Configurazione LLM**
2. Inserisci il nome del modello (es. `gemma3:12b`)
3. Clicca **⬇️ Pull modello**

Una barra di progresso mostra i MB scaricati. Il download può richiedere qualche minuto (il modello `gemma3:12b` pesa circa 8 GB).

### Verificare che il modello funzioni

Per qualsiasi backend (llama.cpp, Ollama, OpenAI, Claude, Compatible):

1. Configura backend, URL/API key e modello
2. Clicca **🧪 Test LLM**

Spendify invia un prompt di prova ("PAGAMENTO POS FARMACIA") e mostra la risposta del modello (categoria + livello di confidenza). Se qualcosa non va, il messaggio di errore indica se il problema è la connessione, l'API key o il modello.

> **Consiglio:** fai sempre il test dopo aver cambiato modello o backend, prima di lanciare un import.

### Cancellare gli schemi dei file

Spendify memorizza la struttura di ogni file importato (colonne, formato date, convenzione di segno) per velocizzare le importazioni successive. Se un file viene importato con lo schema sbagliato — ad esempio mancano le entrate o le colonne sono invertite — puoi resettare la cache:

1. Vai su **⚙️ Impostazioni → 📐 Schema file importati**
2. Clicca **🗑️ Cancella tutti gli schemi salvati**
3. Reimporta il file — Spendify lo rianalizza da zero e ti chiede di confermare lo schema

> **Quando serve:** se il riepilogo import mostra righe scartate con motivo "Importo non parsabile" per le entrate (o le uscite), lo schema salvato probabilmente usa una convenzione di segno sbagliata. Cancella e reimporta.

**Reset tassonomia:** se vuoi cambiare la lingua della tassonomia dopo l'onboarding, vai in **⚙️ Impostazioni → 🔄 Reset tassonomia**, scegli la lingua e conferma. Le categorie esistenti vengono sostituite con quelle del template scelto.

---

## Strumenti di benchmark (per utenti avanzati)

Spendify include due script di benchmark nella cartella `tests/`:

- **`benchmark_pipeline.py`** — misura la qualita del riconoscimento schema (header, colonne, formato date, convenzione segno).
- **`benchmark_categorizer.py`** — misura la qualita della categorizzazione LLM in isolamento (senza database, regole o storico). Metriche: accuratezza esatta, accuratezza fuzzy (primo livello), tasso di fallback.

Entrambi supportano gli stessi argomenti CLI: `--runs`, `--files`, `--backend`, `--model`, `--model-path`. Utili per confrontare modelli diversi prima di scegliere quale usare in produzione.

---

## Domande frequenti

**Posso importare file di banche diverse insieme?**
Sì. Spendify riconosce il formato automaticamente. Non devi dirgli che tipo di file è.

**Ho importato un file due volte per sbaglio. Problema?**
No. Le transazioni duplicate vengono ignorate.

**Una transazione appare due volte — una sul conto e una sulla carta.**
Spendify gestisce questa cosa automaticamente (si chiama "riconciliazione carta-conto"). Se vedi ancora un duplicato, controlla in Review se una delle due ha l'icona 🔄.

**Voglio esportare i dati.**
In Analytics trovi il pulsante **Esporta** che genera HTML, CSV o XLSX.

**Ho cambiato la tassonomia ma le transazioni vecchie non si sono aggiornate.**
Le categorie vecchie rimangono come sono. Puoi rielaborarle manualmente dalla pagina Review o ri-applicando le regole.
