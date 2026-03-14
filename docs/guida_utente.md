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

**Icone nella colonna Note:**
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

> **Esempio pratico con tre tipi di regola:**
> - *Esatta:* `NETFLIX.COM` → Abbonamenti → Streaming (corrisponde solo se la descrizione è esattamente quella)
> - *Contiene:* `ESSELUNGA` → Alimentari → Supermercato (corrisponde se la parola appare ovunque nella descrizione)
> - *Regex:* `RATA \d+/\d+` → Casa → Mutuo (corrisponde a "RATA 3/12", "RATA 10/12" ecc.)

---

## 5. Correggere le descrizioni in blocco

**Situazione:** La banca scrive "SOTTOSCRIZIONI FONDI E SICAV SOTTOSCRIZIONE ETICA AZIONARIO R DEP.TITOLI 081/663905/000" — un disastro leggibile. Vuoi sostituirla con "Fondo Etico Azionario" per tutte le occorrenze.

Vai su **Review**, scorri in fondo, apri il pannello **✏️ Correggi descrizione in blocco**:
1. Incolla la descrizione grezza nel campo *Pattern*
2. Scrivi la descrizione leggibile nel campo *Descrizione pulita*
3. Clicca **Applica e salva regola**

Tutte le transazioni che corrispondono vengono aggiornate immediatamente e la regola viene salvata per i file futuri.

---

## 6. Analytics: i grafici

**Situazione:** Vuoi capire dove spendi di più.

Vai su **Analytics**. Trovi:
- Grafico a torta spese per categoria
- Andamento mensile entrate/uscite
- Saldo netto nel tempo

Usa i filtri in alto per restringere a un periodo o a un conto specifico.

---

## 7. Impostazioni: cambiare il modello AI

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
