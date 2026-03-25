# Spendify — User Guide

> Everything you need to use Spendify in less than 10 minutes.

---

## The idea in one sentence

You download your bank statements, drag them into Spendify, it unifies them, classifies them, and tells you where your money goes. That's it.

---

## 1. First launch — onboarding wizard

**Situation:** You've just installed Spendify and are launching it for the first time.

The app automatically shows the **onboarding wizard** (4 steps). No need to look for any menu.

1. **Step 1 — Language:** choose the taxonomy language. The wizard suggests your browser language. This also sets date format and number separators (e.g. `31/12/2025` with `,` separator for Italian).
2. **Step 2 — Holders:** enter your name (and the variants used by the bank — e.g. `Mario Rossi, ROSSI MARIO`). These names are used to protect your privacy in LLM prompts and to detect internal transfers.
3. **Step 3 — Accounts:** add your bank accounts (name + bank + account type). The account type is mandatory and indicates the financial instrument: *Bank account*, *Credit card*, *Debit card*, *Prepaid card*, *Savings account* or *Cash*. You can skip this step and add them later from Settings.
4. **Step 4 — Confirm:** review the summary and click **Inizia!** — data is saved only at this point.

> **Updating from a previous version?** The wizard does not appear if the database already has data — the app opens normally.

---

## 2. First import

**Situation:** You've completed the wizard and want to load your bank statements.

1. Go to **Import** (the first button at the top left).
2. Drag one or more files into the dashed area — CSV, XLSX, XLS all work together.
3. For each file, select the associated bank account from the dropdown menu.
4. Click **Avvia elaborazione**.
5. Wait for the green bar. You can close the browser and reopen it: the work continues in the background.

> **Example:** You have three files — `estratto_unicredit_gen.csv`, `carta_visa_gen.xlsx`, `conto_deposito.csv`. You select all three at once. Spendify automatically figures out what type they are.

**What happens behind the scenes:** Spendify assigns each transaction a unique code based on its content. If you import the same file twice nothing bad happens — duplicates are silently discarded.

### Automatic import and schema review

Spendify analyses the structure of each file and computes a **confidence score** (from 0 to 100%) on how well it understood the format.

- **Confidence >= 80%** — import proceeds automatically, with no user interaction needed.
- **Confidence < 80%** — a review form appears where you can verify the detected columns (date, amount, description, document type) and correct them if needed. After manual confirmation, the confidence is set to 100%.

Once a file's schema is confirmed, all subsequent imports of the same format will be automatic — Spendify remembers the structure.

> **Auto-selected account:** if the file format matches a schema previously imported for a specific account, Spendify automatically pre-selects that account in the dropdown. No need to choose it every time.

> **First upload warning:** when you upload a file with a format never seen before (first upload) and the file contains fewer than 50 rows, a yellow warning appears: *"For optimal recognition, upload the bank statement as downloaded from the bank, without modifications, ideally with 250-300 transactions."* This is because automatic schema detection works better with more data.

### Rows to skip — when does this field appear?

Some bank files have header rows before the data table (bank name, period, account number…) and summary/total rows at the bottom. Spendify uses density-based analysis to automatically detect where the actual data table starts and ends, removing both preamble rows at the top and totals at the bottom. In most cases no manual configuration is needed.

If automatic detection **fails** (unusual format, fully numeric, no text headers), a **"Rows to skip"** field will appear next to the filename. Enter how many rows to skip before the table header.

> **Example:** Open the CSV file in a text editor. If the first 3 rows are `Bank XYZ`, `Account 123`, `From 01/01 to 31/01` and row 4 is `Date,Amount,Description`, enter `3`.

Once the file schema is confirmed, you won't see this field on subsequent imports — Spendify remembers it automatically.

### Import summary

After processing, a detailed summary is shown for each imported file:

| Metric | Meaning |
|--------|---------|
| **Righe E/C** (Statement rows) | Total number of data rows in the bank statement (excluding headers) |
| **Imported** | New transactions saved to the database |
| **Already present** | Previously imported transactions (duplicates, skipped) |
| **Giroconti** (Internal transfers) | Internal transfers detected (tooltip with details). Internal transfers are **always saved** to the database, even in "Exclude" mode — the setting only controls visibility in views |
| **Discarded** | Rows that could not be imported |

If there are discarded rows, a warning shows the reason for each:
- **Missing date** — the date cell is empty
- **Unparseable date** — the date format doesn't match the schema
- **Unparseable amount** — the amount value is not recognisable
- **Amount: both Debit/Credit columns empty** — in files with separate debit and credit columns, neither column has a value for that row

You can expand the detail to see the original data of each discarded row, useful for understanding whether there's a problem with the file or the schema.

> **Tip:** if the numbers don't add up (e.g. rows in file ≠ sum of imported + already present + discarded + header), it may indicate a schema problem. Try clearing the saved schema from ⚙️ Settings and re-importing the file.

---

## 3. The Ledger: viewing all transactions

**Situation:** You want to check what has been imported.

Go to **Ledger**. You'll find the complete list in chronological order, with filters for date, account, category, and type (income/expense).

> **Example:** You want to see only January 2025 expenses on your current account. You set the date filter and select the account. The table updates immediately.

**Quick period filters:** use the buttons at the top — *Mese corrente*, *Mese precedente* (moves one month back each time you press it), *Ultimi 3 mesi*, *Anno corrente*, *♾ Tutto* (resets all filters at once).

**Additional options in the second filter row:**
- ☑ **Nascondi giroconti** — excludes transfers between your own accounts from the results (default: follows the global setting)
- ☑ **Mostra raw** — adds the "Raw description" column to the table so you can compare the bank's original text with the processed version

**Indicator columns (emoji, read-only):**
- ⚠️ = needs review (automatic classification was not confident) — shows "·" when inactive
- ✅ = transaction validated by the user — shows "·" when inactive
- 🔄 = internal transfer (e.g. bank transfer between your own accounts, excluded from totals) — shows "·" when inactive

**Checkbox columns (after the emoji columns):**
- **Validato ☐** (Validated) — clickable checkbox to validate/unvalidate a transaction. Saves immediately on click (no need to press Save). When you validate, the ⚠️ flag is automatically cleared. When you unvalidate, `human_validated` is set back to False.
- **🔄 Giroconto ☐** — clickable checkbox to mark/unmark an internal transfer (saves with the Save button)

**Column order (right side of the grid):**
`... | Fonte | ⚠️ | ✅ | 🔄 | Validato ☐ | 🔄 Giroconto ☐`

**Fonte column (classification tracking):**
- Shows who assigned the current category: 🧠 AI, 📏 Rule, 👤 Manual, 📚 History

**Bulk validation:** select one or more transactions and click **Valida selezionate** to confirm that the assigned categories are correct, without having to modify them. Validating a transaction tells Spendify "this category is correct" — the information is used to improve future classifications.

### Creating rules from the Ledger

Select a row using the selection column (📏) and click **Create rule and apply**: a pre-filled form appears with the pattern extracted from the counterpart, plus the category and context from the transaction. A preview shows how many transactions will be matched. If the rule already exists, a yellow warning appears and the button changes to **Edit rule and apply**. After confirmation, the rule is applied retroactively to all matching transactions. A toast confirms "Rule created" or "Rule updated".

> **Note:** you can only select **one row at a time** to create a rule. If you select more than one, the app shows an error.

For the full workflow, see the [Classification Guide](guida_classificazione.en.md).

---

## 4. Review: transactions to check

**Situation:** Spendify was not confident about some classifications and has put them on hold.

Go to **Review**. You'll find the transactions marked with ⚠️. For each one you can:
- Change the category/subcategory from the dropdown menu
- Confirm by clicking **Salva**

**Indicator columns (emoji, read-only):**
- ⚠️ = needs review — shows "·" when inactive
- ✅ = transaction validated by the user — shows "·" when inactive

**Checkbox columns (after the emoji columns):**
- **Validato ☐** (Validated) — clickable checkbox to validate/unvalidate a transaction. Saves immediately on click. When you validate, the ⚠️ flag is automatically cleared.

**Fonte column (classification tracking):**
- Badge showing who assigned the category: 🧠 AI, 📏 Rule, 👤 Manual, 📚 History

**Bulk validation:** select the transactions you are sure about and click **Valida selezionate** to confirm them all at once. When you validate a transaction you are telling Spendify: "this classification is correct". Validation does not change the category — it only confirms it is right.

> **Example:** "PAGAMENTO POS 00112 FARMACIA CENTRALE" was classified as *Casa* but you know it's *Salute*. You correct it once, and if you saved a rule that correction will be applied automatically to future imports.

### Reprocess with LLM
At the bottom of the Review page there is the **🔄 Rielabora con LLM** button. When you see `(N descrizioni non pulite)` it means some transactions still have the raw bank description without having been processed. Click it to reprocess them.

> **When this happens:** If the AI model was offline during import, transactions are imported anyway but with the original description. This button recovers them as soon as the model becomes available again.

---

## 5. Regole: don't correct the same thing twice

**Situation:** Every month "ADDEBITO SDD ENEL ENERGIA" shows up and every month you have to correct it manually.

Go to **Regole**, create a new rule:
- **Pattern:** `ENEL ENERGIA`
- **Category:** Utenze → Elettricità

From that point on, every transaction containing those words is classified automatically — both in future imports and in those already present in the database.

**Re-run all rules at once**

If you have created many rules across different sessions and want to apply them all at once to your entire history, use the **▶️ Esegui tutte le regole** button at the bottom of the section. Check the confirmation checkbox and click the button: all rules are applied to every transaction in the ledger, not just those pending review. When finished, it will tell you how many transactions were updated.

> **Practical example with three rule types:**
> - *Exact match:* `NETFLIX.COM` → Abbonamenti → Streaming (matches only if the description is exactly that)
> - *Contains text:* `ESSELUNGA` → Alimentari → Supermercato (matches if the word appears anywhere in the description)
> - *Advanced expression:* `RATA \d+/\d+` → Casa → Mutuo (matches "RATA 3/12", "RATA 10/12", etc.)

---

## 6. Modifiche massive: category, context, and bulk deletion

**Situation:** You've imported years of bank statements and want to clean up incorrect data or remove an entire account you no longer want to track.

Go to **✏️ Modifiche massive**. The page is divided into two main areas.

### 6a — Operations on a reference transaction

1. Search and select a transaction from the dropdown menu (you can filter by text or show only those ⚠️ pending review)
2. Spendify shows how many other transactions have the same or a similar description (Jaccard ≥ 35%)
3. Then choose what to do:
   - **2a Giroconto** — mark/unmark as an internal transfer; with one click it propagates to all transactions with the same description
   - **2b Contesto** — assign a context (e.g. "Vacanza") to the single transaction or to all similar ones
   - **2c Categoria** — corrects category and subcategory, saves a deterministic rule for future files, applies immediately to similar transactions

### 6b — Bulk deletion by filter

**Situation:** You want to delete all transactions from a closed account, or all those from a test period.

1. Set the filters (dates, account, type, description, category) — at least one is required
2. The counter immediately shows how many transactions will be deleted
3. Click **👁 Anteprima** to see the first 10 rows before proceeding
4. Type **`ELIMINA`** in the confirmation field — only then does the button become enabled
5. Click the red button

> ⚠️ **Deletion is irreversible.** Always make a backup of the `ledger.db` file before deleting large amounts of data (see the Deployment guide).

> **Example:** You accidentally imported the bank statement of an account that isn't yours. You filter by account, see 200 transactions in the preview, type ELIMINA, and remove them all at once.

---

## 7. Correcting descriptions in bulk (Review page)

**Situation:** The bank writes "SOTTOSCRIZIONI FONDI E SICAV SOTTOSCRIZIONE ETICA AZIONARIO R DEP.TITOLI 081/663905/000" — an unreadable mess. You want to replace it with "Fondo Etico Azionario" for all occurrences.

Go to **Review**, scroll to the bottom, open the **✏️ Correggi descrizione in blocco** panel:
1. Paste the raw description into the *Pattern* field
2. Write the readable description in the *Descrizione pulita* field
3. Click **Applica e salva regola**

All matching transactions are updated immediately and the rule is saved for future files.

---

## 8. Analytics: the charts

**Situation:** You want to understand where you spend the most.

Go to **Analytics**. You'll find:
- Pie chart of expenses by category
- Monthly income/expense trend
- Net balance over time

Use the filters at the top to narrow down to a specific period or account.

---

## 9. Check List: is everything in order?

**Situation:** You want to check at a glance whether you are regularly importing all your bank statements, without gaps of months.

Go to **✅ Check List**. You'll find a table with:
- **One row for each month**, from the current month going back into the past
- **One column for each account** you have configured in Impostazioni

Each cell shows the number of transactions imported for that month and account. If the number is **—** (grey), you have no transactions for that combination.

> **Practical example:** You have three accounts — Conto Corrente, Carta Visa, Conto Deposito. You look at the check list and see that July 2024 shows "—" for Conto Deposito. This means you never imported the bank statement for that deposit account for that month. Go download it from the bank and import it.

**How to read the colours:**
- **—** grey = no transactions
- 🔵 light blue = 1–4 transactions (few — maybe something is missing?)
- 🔵 medium blue = 5–19 transactions (normal)
- 🔵 dark blue = ≥ 20 transactions (full month)

**Useful filters:**
- *Mostra solo conti* — compare only the accounts you care about
- *Ultimi N mesi* — focus on a recent period
- *Nascondi mesi senza transazioni* — removes months in which you have no data for any account

You can download the table as CSV with the **⬇️ Scarica CSV** button.

---

## 10. Settings: changing the AI model

**Situation:** You want to use a different model for classification (e.g. Claude instead of local Ollama).

Go to **⚙️ Impostazioni**:
- **Backend LLM:** choose between Ollama (local, free, private), OpenAI, Claude, or any OpenAI-compatible provider (Groq, Google AI Studio, etc.)
- **Model:** specify the model name (e.g. `gpt-4o-mini`, `claude-3-5-haiku-20241022`, `gemma2-9b-it`)
- **API Key:** enter the key if you use a remote service

> **Privacy note:** If you use a remote backend (OpenAI or Claude), Spendify automatically removes IBANs, card numbers, tax identification numbers, and the account holder's name before sending any data.

For detailed instructions on where to register and how to obtain API keys for each provider, see the **[Configuration Manual](configurazione.en.md)**.

### Account type

Every account has a mandatory **type** indicating the financial instrument:

| Type | Label |
|------|-------|
| `bank_account` | Conto corrente |
| `credit_card` | Carta di credito |
| `debit_card` | Carta di debito |
| `prepaid_card` | Carta prepagata |
| `savings_account` | Conto risparmio |
| `cash` | Contanti |

The account type is used during import to guide automatic format detection: for example, a **credit card** automatically forces `invert_sign=True` (expenses in the CSV are positive but must be recorded as outflows). The type is shown in the account list with an Italian label.

### Renaming an account

You can rename a bank account at any time from **⚙️ Impostazioni → 🏦 Conti bancari**. When you rename an account, Spendify automatically recalculates the unique identifier of every associated transaction, because the account name is part of the hash key.

The operation is **atomic**: if anything goes wrong during recalculation, no data is changed. Your data always remains intact.

> **In practice:** rename the account without worry. Transactions, categories, rules, and associated internal transfers remain untouched. The only thing that changes is the internal technical identifier (invisible to you).

### Downloading a model (Ollama)

If you use Ollama as your backend, you can download or update the model directly from the app without opening a terminal:

1. Go to **⚙️ Impostazioni → 🤖 Configurazione LLM**
2. Enter the model name (e.g. `gemma3:12b`)
3. Click **⬇️ Pull modello**

A progress bar shows the downloaded MB. The download may take a few minutes (`gemma3:12b` is about 8 GB).

### Checking that the model works

For any backend (Ollama, OpenAI, Claude, Compatible):

1. Configure backend, URL/API key, and model
2. Click **🧪 Test LLM**

Spendify sends a test prompt ("PAGAMENTO POS FARMACIA") and shows the model's response (category + confidence level). If something is wrong, the error message indicates whether the problem is the connection, the API key, or the model.

> **Tip:** always run the test after changing model or backend, before starting an import.

### Clearing saved file schemas

Spendify remembers the structure of each imported file (columns, date format, sign convention) to speed up future imports. If a file was imported with the wrong schema — for example, income rows are missing or columns are swapped — you can reset the cache:

1. Go to **⚙️ Impostazioni → 📐 Schema file importati**
2. Click **🗑️ Cancella tutti gli schemi salvati**
3. Re-import the file — Spendify re-analyses it from scratch and asks you to confirm the schema

> **When to use:** if the import summary shows discarded rows with reason "Importo non parsabile" for income (or expense) rows, the saved schema probably uses the wrong sign convention. Clear and re-import.

---

## Frequently asked questions

**Can I import files from different banks together?**
Yes. Spendify recognises the format automatically. You don't need to tell it what type of file it is.

**I imported a file twice by mistake. Is that a problem?**
No. Duplicate transactions are ignored.

**A transaction appears twice — once on the account and once on the card.**
Spendify handles this automatically (it's called "card-account reconciliation"). If you still see a duplicate, check in Review whether one of them has the 🔄 icon.

**I want to export the data.**
In Analytics you'll find the **Esporta** button which generates HTML, CSV, or XLSX.

**I changed the Tassonomia but the old transactions haven't updated.**
Old categories stay as they are. You can reprocess them manually from the Review page or by re-applying the rules.
