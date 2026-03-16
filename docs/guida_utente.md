# Spendify — Guida Utente

> Tutto quello che ti serve per usare Spendify in meno di 10 minuti.

---

## L'idea in una frase

Scarichi gli estratti conto dalla banca, li trascini in Spendify, lui li unifica, li classifica e ti dice dove vanno i tuoi soldi. Fine.

---

## 1. Prima importazione

**Situazione:** Hai appena installato Spendify e vuoi caricare i tuoi estratti conto.

1. Vai su **Import** (il primo pulsante in alto a sinistra).
2. Trascina uno o più file nell'area tratteggiata — vanno bene CSV, XLSX, XLS tutti insieme.
3. Clicca **Avvia elaborazione**.
4. Aspetta la barra verde. Puoi chiudere il browser e riaprirlo: il lavoro continua in background.

> **Esempio:** Hai tre file — `estratto_unicredit_gen.csv`, `carta_visa_gen.xlsx`, `conto_deposito.csv`. Li selezioni tutti e tre in una volta sola. Spendify capisce da solo che tipo sono.

**Cosa succede dietro le quinte:** Spendify assegna a ogni transazione un codice univoco basato sul contenuto. Se importi lo stesso file due volte non succede nulla di male — i duplicati vengono scartati silenziosamente.

---

## 2. Il Ledger: vedere tutte le transazioni

**Situazione:** Vuoi controllare cosa è stato importato.

Vai su **Ledger**. Trovi la lista completa in ordine cronologico, con filtri per data, conto, categoria e tipo (entrata/uscita).

> **Esempio:** Vuoi vedere solo le spese di gennaio 2025 sul conto corrente. Imposti il filtro data e selezioni il conto. La tabella si aggiorna subito.

**Filtri rapidi di periodo:** usa i pulsanti in cima — *Mese corrente*, *Mese precedente* (si sposta mese per mese ogni volta che lo premi), *Ultimi 3 mesi*, *Anno corrente*, *♾ Tutto* (azzera tutti i filtri contemporaneamente).

**Opzioni aggiuntive nella seconda riga filtri:**
- ☑ **Nascondi giroconti** — esclude bonifici tra tuoi stessi conti dai risultati (default: segue l'impostazione globale)
- ☑ **Mostra raw** — aggiunge la colonna "Raw description" nella tabella per confrontare il testo originale della banca con quello rielaborato

**Icone nelle colonne:**
- 🔄 = giroconto interno (es. bonifico tra tuoi conti) — escluso dai totali
- ⚠️ = da rivedere (la classificazione automatica non era sicura)

---

## 3. Review: le transazioni da controllare

**Situazione:** Spendify non era sicuro di alcune classificazioni e le ha messe in attesa.

Vai su **Review**. Trovi le transazioni con il ⚠️. Per ognuna puoi:
- Cambiare categoria/sottocategoria dal menu a tendina
- Confermare cliccando **Salva**

> **Esempio:** "PAGAMENTO POS 00112 FARMACIA CENTRALE" è stato classificato come *Casa* ma tu sai che è *Salute*. Lo correggi una volta, e se hai salvato una regola quella correzione si applicherà automaticamente alle prossime importazioni.

### Rielabora con LLM
In fondo alla pagina Review c'è il pulsante **🔄 Rielabora con LLM**. Quando vedi `(N descrizioni non pulite)` significa che alcune transazioni hanno ancora la descrizione grezza della banca senza essere state elaborate. Cliccalo per riprocessarle.

> **Quando capita:** Se durante l'importazione il modello AI era offline, le transazioni vengono importate comunque ma con la descrizione originale. Questo pulsante le recupera appena il modello torna disponibile.

---

## 4. Regole: non correggere la stessa cosa due volte

**Situazione:** Ogni mese arriva "ADDEBITO SDD ENEL ENERGIA" e ogni mese lo devi correggere a mano.

Vai su **Regole**, crea una nuova regola:
- **Pattern:** `ENEL ENERGIA`
- **Categoria:** Utenze → Elettricità

Da quel momento in poi, ogni transazione che contiene quelle parole viene classificata automaticamente — sia nelle importazioni future che in quelle già presenti nel database.

**Riesegui tutte le regole in blocco**

Se hai creato molte regole in sessioni diverse e vuoi applicarle tutte in una volta a tutto il tuo storico, usa il pulsante **▶️ Esegui tutte le regole** in fondo alla sezione. Spunta la casella di conferma e clicca il pulsante: tutte le regole vengono applicate a ogni transazione del ledger, non solo a quelle in attesa di revisione. Al termine ti dirà quante transazioni sono state aggiornate.

> **Esempio pratico con tre tipi di regola:**
> - *Esatta:* `NETFLIX.COM` → Abbonamenti → Streaming (corrisponde solo se la descrizione è esattamente quella)
> - *Contiene:* `ESSELUNGA` → Alimentari → Supermercato (corrisponde se la parola appare ovunque nella descrizione)
> - *Regex:* `RATA \d+/\d+` → Casa → Mutuo (corrisponde a "RATA 3/12", "RATA 10/12" ecc.)

---

## 5. Modifiche massive: categoria, contesto e eliminazione in blocco

**Situazione:** Hai importato anni di estratti conto e vuoi pulire dati sbagliati o rimuovere un intero conto che non vuoi più tracciare.

Vai su **✏️ Modifiche massive**. La pagina è divisa in due aree principali.

### 5a — Operazioni su transazione di riferimento

1. Cerca e seleziona una transazione dal menu a tendina (puoi filtrare per testo o mostrare solo quelle ⚠️ da rivedere)
2. Spendify mostra quante altre transazioni hanno la stessa descrizione o una simile (Jaccard ≥ 35%)
3. Poi scegli cosa fare:
   - **2a Giroconto** — segna/rimuovi come bonifico interno, con un click propaga a tutte le tx con la stessa descrizione
   - **2b Contesto** — assegna un contesto (es. "Vacanza") alla singola tx o a tutte le simili
   - **2c Categoria** — corregge categoria e sottocategoria, salva una regola deterministica per i file futuri, applica subito alle simili

### 5b — Eliminazione massiva da filtro

**Situazione:** Vuoi cancellare tutte le transazioni di un conto chiuso, o tutte quelle di un periodo di test.

1. Imposta i filtri (date, conto, tipo, descrizione, categoria) — almeno uno è obbligatorio
2. Il contatore mostra subito quante transazioni verranno cancellate
3. Clicca **👁 Anteprima** per vedere le prime 10 righe prima di procedere
4. Digita **`ELIMINA`** nel campo di conferma — solo allora il pulsante si abilita
5. Clicca il pulsante rosso

> ⚠️ **L'eliminazione è irreversibile.** Fai sempre un backup del file `ledger.db` prima di eliminare grandi quantità di dati (vedi la guida Deployment).

> **Esempio:** Hai importato per errore l'estratto conto di un conto corrente che non è tuo. Filtri per conto, vedi 200 transazioni nell'anteprima, digiti ELIMINA e le rimuovi in un colpo solo.

---

## 6. Correggere le descrizioni in blocco (pagina Review)

**Situazione:** La banca scrive "SOTTOSCRIZIONI FONDI E SICAV SOTTOSCRIZIONE ETICA AZIONARIO R DEP.TITOLI 081/663905/000" — un disastro leggibile. Vuoi sostituirla con "Fondo Etico Azionario" per tutte le occorrenze.

Vai su **Review**, scorri in fondo, apri il pannello **✏️ Correggi descrizione in blocco**:
1. Incolla la descrizione grezza nel campo *Pattern*
2. Scrivi la descrizione leggibile nel campo *Descrizione pulita*
3. Clicca **Applica e salva regola**

Tutte le transazioni che corrispondono vengono aggiornate immediatamente e la regola viene salvata per i file futuri.

---

## 7. Analytics: i grafici

**Situazione:** Vuoi capire dove spendi di più.

Vai su **Analytics**. Trovi:
- Grafico a torta spese per categoria
- Andamento mensile entrate/uscite
- Saldo netto nel tempo

Usa i filtri in alto per restringere a un periodo o a un conto specifico.

---

## 8. Check List: tutto in ordine?

**Situazione:** Vuoi controllare a colpo d'occhio se stai importando regolarmente tutti i tuoi estratti conto, senza buchi di mesi.

Vai su **✅ Check List**. Trovi una tabella con:
- **Una riga per ogni mese**, dal mese corrente in poi verso il passato
- **Una colonna per ogni conto** che hai configurato in Impostazioni

Ogni cella mostra il numero di transazioni importate per quel mese e quel conto. Se il numero è **—** (grigio), non hai transazioni per quella combinazione.

> **Esempio pratico:** Hai tre conti — Conto Corrente, Carta Visa, Conto Deposito. Guardi la check list e vedi che Luglio 2024 ha "—" per il Conto Deposito. Significa che non hai mai importato l'estratto conto del conto deposito di quel mese. Vai a scaricarlo dalla banca e importalo.

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

## 9. Impostazioni: cambiare il modello AI

**Situazione:** Vuoi usare un modello diverso per la classificazione (es. Claude invece di Ollama locale).

Vai su **Impostazioni**:
- **Backend LLM:** scegli tra Ollama (locale, gratuito, privato), OpenAI, Claude
- **Modello:** specifica il nome del modello (es. `gpt-4o-mini`, `claude-haiku-4-5`)
- **API Key:** inserisci la chiave se usi un servizio remoto

> **Nota sulla privacy:** Se usi un backend remoto (OpenAI o Claude), Spendify rimuove automaticamente IBAN, numeri carta, codice fiscale e nome del titolare prima di inviare qualsiasi dato.

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
