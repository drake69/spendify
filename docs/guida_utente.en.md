# Spendif.ai — User Guide

> Everything you need to use Spendif.ai in less than 10 minutes.

---

## The idea in one sentence

You download your transaction files from the bank, drag them into Spendif.ai, it unifies them, classifies them, and tells you where your money goes. That's it.

---

## 1. First launch — onboarding wizard

**Situation:** You've just installed Spendif.ai and are launching it for the first time.

The app automatically shows the **onboarding wizard** (4 steps). No need to look for any menu.

1. **Step 1 — Language:** choose the taxonomy language. The wizard suggests your browser language. This also sets date format and number separators (e.g. `31/12/2025` with `,` separator for Italian).
2. **Step 2 — Holders:** enter your name (and the variants used by the bank — e.g. `Mario Rossi, ROSSI MARIO`). These names are used to protect your privacy in LLM prompts and to detect internal transfers.
3. **Step 3 — Accounts:** add your bank accounts (name + bank + account type). The account type is mandatory and indicates the financial instrument: *Bank account*, *Credit card*, *Debit card*, *Prepaid card*, *Savings account* or *Cash*. You can skip this step and add them later from Settings.
4. **Step 4 — Confirm:** review the summary and click **Start!** — data is saved only at this point.

> **Updating from a previous version?** The wizard does not appear if the database already has data — the app opens normally.

---

## 2. First import

**Situation:** You've completed the wizard and want to load your transaction files.

1. Go to **Import** (the first button at the top left).
2. Drag one or more files into the dashed area — CSV, XLSX, XLS all work together.
3. For each file, select the associated bank account from the dropdown menu.
4. Click **Avvia elaborazione**.
5. Wait for the green bar. You can close the browser and reopen it: the work continues in the background.

> **Example:** You have three files — `estratto_unicredit_gen.csv`, `carta_visa_gen.xlsx`, `conto_deposito.csv`. You select all three at once. Spendif.ai automatically figures out what type they are.

**What happens behind the scenes:** Spendif.ai assigns each transaction a unique code based on its content. If you import the same file twice nothing bad happens — duplicates are silently discarded.

> **Import History:** the **Import History** page in the sidebar shows the full history of all imports (date, file, account, number of transactions). You can undo an import by deleting all transactions from that batch at once using the "Cancel import" button. See the dedicated section below.

### Automatic import and schema review

Spendif.ai analyses the structure of each file and computes a **confidence score** (from 0 to 100%) on how well it understood the format.

- **Confidence >= 80%** — import proceeds automatically, with no user interaction needed.
- **Confidence < 80%** — a review form appears where you can verify the detected columns (date, amount, description, document type) and correct them if needed. After manual confirmation, the confidence is set to 100%.

Once a file's schema is confirmed, all subsequent imports of the same format will be automatic — Spendif.ai remembers the structure.

> **Auto-selected account:** if the file format matches a schema previously imported for a specific account, Spendif.ai automatically pre-selects that account in the dropdown. No need to choose it every time.

> **First upload warning:** when you upload a file with a format never seen before (first upload) and the file contains fewer than 50 rows, a yellow warning appears: *"For optimal recognition, upload the transactions file as downloaded from the bank, without modifications, ideally with 250-300 transactions."* This is because automatic schema detection works better with more data.

### Rows to skip — when does this field appear?

Some bank files have header rows before the data table (bank name, period, account number…) and summary/total rows at the bottom. Spendif.ai uses density-based analysis to automatically detect where the actual data table starts and ends, removing both preamble rows at the top and totals at the bottom. In most cases no manual configuration is needed.

If automatic detection **fails** (unusual format, fully numeric, no text headers), a **"Rows to skip"** field will appear next to the filename. Enter how many rows to skip before the table header.

> **Example:** Open the CSV file in a text editor. If the first 3 rows are `Bank XYZ`, `Account 123`, `From 01/01 to 31/01` and row 4 is `Date,Amount,Description`, enter `3`.

Once the file schema is confirmed, you won't see this field on subsequent imports — Spendif.ai remembers it automatically.

### Import summary

After processing, a detailed summary is shown for each imported file:

| Metric | Meaning |
|--------|---------|
| **Righe E/C** (Movements file rows) | Total number of data rows in the transactions file (excluding headers) |
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

**Bulk validation:** select one or more transactions and click **Valida selezionate** to confirm them all at once.

> **What does "Validated" mean?** Validating a transaction tells Spendif.ai: "I have seen this expense and confirm it is correct (not anomalous)". Validation concerns the **expense itself**, not the category. If a rule or the AI reclassifies the transaction later, the validation stays active: the source badge (AI, Rule, Manual, History) changes, but the "Validated" flag is not touched. Only an explicit click on the "Validato" checkbox (unchecking it) can remove the validation.

### Behavioural fan-out (category propagation)

When you validate a transaction in the Ledger (or in Review), Spendif.ai automatically looks for other transactions with the same description that have not yet been categorised. If it finds any, a suggestion appears: **"Apply to N similar transactions?"**

- Click **Apply to all** to copy the same category/subcategory to all matching transactions (with source "History").
- Click **No thanks** to dismiss the suggestion.

The suggestion is non-intrusive: it only appears when there are actually similar uncategorised transactions. The more you use the system and validate transactions, the less manual work you will need to do in the future.

### Creating rules from the Ledger

Select a row using the selection column (📏) and click **Create rule and apply**: a pre-filled form appears with the pattern extracted from the counterpart, plus the category and context from the transaction. A preview shows how many transactions will be matched. If the rule already exists, a yellow warning appears and the button changes to **Edit rule and apply**. After confirmation, the rule is applied retroactively to all matching transactions. A toast confirms "Rule created" or "Rule updated".

> **Note:** you can only select **one row at a time** to create a rule. If you select more than one, the app shows an error.

For the full workflow, see the [Classification Guide](guida_classificazione.en.md).

---

## 4. Review: transactions to check

**Situation:** Spendif.ai was not confident about some classifications and has put them on hold.

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

**Bulk validation:** select the transactions you are sure about and click **Valida selezionate** to confirm them all at once. When you validate a transaction you are telling Spendif.ai: "I have seen this expense and confirm it is correct". Validation does not change the category, and it is not removed if the category changes later (by rule or AI). Only an explicit click on the checkbox removes it.

**Behavioural fan-out:** in Review as well, after validating a transaction, Spendif.ai offers to apply the same category to similar uncategorised transactions. See the "Behavioural fan-out" section in the Ledger chapter for details.

> **Example:** "PAGAMENTO POS 00112 FARMACIA CENTRALE" was classified as *Casa* but you know it's *Salute*. You correct it once, and if you saved a rule that correction will be applied automatically to future imports.

### Reprocess with LLM
At the bottom of the Review page there is the **🔄 Rielabora con LLM** button. When you see `(N descrizioni non pulite)` it means some transactions still have the raw bank description without having been processed. Click it to reprocess them.

> **When this happens:** If the AI model was offline during import, transactions are imported anyway but with the original description. This button recovers them as soon as the model becomes available again.

---

## 5. Rules: don't correct the same thing twice

**Situation:** Every month "ADDEBITO SDD ENEL ENERGIA" shows up and every month you have to correct it manually.

Go to **Rules**, create a new rule:
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

## 6. Bulk Edit: category, context, and bulk deletion

**Situation:** You've imported years of transaction files and want to clean up incorrect data or remove an entire account you no longer want to track.

Go to **✏️ Bulk Edit**. The page is divided into two main areas.

### 6a — Operations on a reference transaction

1. Search and select a transaction from the dropdown menu (you can filter by text or show only those ⚠️ pending review)
2. Spendif.ai shows how many other transactions have the same or a similar description (Jaccard ≥ 35%)
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

> **Example:** You accidentally imported the transactions file of an account that isn't yours. You filter by account, see 200 transactions in the preview, type ELIMINA, and remove them all at once.

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

### Taxonomy tree filter

In the Analytics page (and the Taxonomy page) a **tree filter** is available that shows the full context / category / subcategory hierarchy with collapsible checkboxes. You can:
- Expand and collapse tree nodes
- Select a parent node to automatically select all children
- Use the **Select all** / **Deselect all** buttons for quick operations
- Tri-state indicators show whether a node is fully selected, partially selected, or deselected

> **Note:** The tree filter is only available in analysis pages (Analytics, Taxonomy). It is not present in Ledger and Review, where flat filters are faster for daily transaction work.

### Counterpart associations (auto-learning)

In the Analytics section you will also find the **Counterpart associations** view, which shows how Spendif.ai has learned to associate each counterpart (e.g. ESSELUNGA, AMAZON) with categories over time.

For each counterpart it shows:
- **Category/Subcategory** most frequent among validated transactions
- **Count** of validated occurrences
- **Homogeneity** — a 0-to-1 indicator measuring how "stable" the counterpart's classification is:
  - 🟢 **>= 0.90** — auto-categorisable (e.g. ESSELUNGA: always Food)
  - 🟡 **0.50–0.89** — mixed (e.g. ROSSOPOMODORO: almost always Dining, sometimes Holidays)
  - 🔴 **< 0.50** — heterogeneous (e.g. AMAZON: Technology, Food, Clothing...)

> **How it works:** Spendif.ai only counts transactions you have **validated** (the checkbox in Ledger or Review). The more transactions you validate, the more the system learns. When you import new files, counterparts with high homogeneity are classified automatically from history — without calling the AI.

---

## 9. Report: where does your money go

**Situation:** You want a structured summary of your spending by context and category, with percentages and trends over time.

Go to **Report** in the sidebar. The page offers three views.

### View 1 — Pivot table

A **context x category x subcategory** pivot table showing:
- Total amount for each combination
- Percentage of total spending
- Subtotals per context

Internal transfers between your own accounts are automatically excluded from totals.

### View 2 — Time trend

Interactive charts (Plotly) showing:
- **Line chart** with monthly trends for the top 10 spending categories
- **Stacked bar** to visualise monthly composition
- Separate tabs for **expenses** and **income**

### View 3 — Excel export

A button to download an `.xlsx` file with three types of sheets:
- **Riepilogo** (Summary) — the complete pivot table
- **Trend** — monthly data by category
- **One sheet per context** — transaction details

### Available filters

- **Period:** date range picker with quick buttons — *Month*, *3 months*, *Year*, *All*
- **Accounts:** multiselect to filter one or more accounts

> **Practical example:** You want to know how much you spent on "Alimentari" (Food) in the last 3 months. Open Report, click "3 months", look at the Alimentari row in the pivot table. Then switch to View 2 to see if spending is trending up or down.

---

## 10. Import History: undoing an import

**Situation:** You uploaded the wrong file and want to remove all transactions from that import.

Go to **Import History** in the sidebar. You will find the complete history of all imports, showing:

- **Date** of the import
- **File** uploaded
- **Account** associated
- **Number of transactions** imported

To undo an import, click the **Cancel import** button on the corresponding row. The operation deletes all transactions from that batch in one go (hard delete). The deletion is irreversible.

> **Practical example:** You accidentally imported the December transactions file instead of January. Go to Import History, find the wrong import, click "Cancel import" and all transactions from that batch are removed. Then import the correct file.

> **How it works:** Each import is recorded as a batch with a unique identifier (`batch_id`). All transactions imported in that session are linked to the batch, making it possible to surgically undo just that import without affecting other imports.

---

## 11. Budget: setting spending targets

**Situation:** You want to set a spending target for each category (e.g. max 30% on Housing, max 15% on Food).

Go to **Budget** in the sidebar. You will find a table with all spending categories and a percentage field for each.

- Enter the **target percentage** for each category (e.g. 30% for Housing).
- At the bottom of the table, a **summary** shows the total allocated and the remaining liquidity.
- If the total exceeds 100%, a **yellow warning** appears.

Categories without a percentage have no target and are not monitored in the Budget vs Actual page.

> **Practical example:** You want a maximum of 30% on Housing, 15% on Food, 10% on Transport. You set the three percentages and immediately see you have allocated 55%, with 45% remaining liquidity.

---

## 12. Budget vs Actual: are you meeting your targets?

**Situation:** You want to compare your actual spending against the targets you defined in the Budget page.

Go to **Budget vs Actual** in the sidebar. The page shows:

### Period selector

At the top you will find the period selector with four modes: **Month**, **Quarter**, **Year**, **Custom**. Use the **left arrow** and **right arrow** to navigate in time (e.g. previous month, next month).

### Metrics row

A row of aggregate indicators shows:
- **Income** in the period
- **Expenses** in the period
- **Liquidity** (income - expenses)
- **Liquidity %** relative to income

### Comparison table with traffic lights

For each category with a defined target:
- The **Target** column shows the target percentage
- The **Actual** column shows the actual percentage
- A traffic light indicates the deviation:
  - 🟢 **Green** — within 5% of target (all good)
  - 🟡 **Yellow** — between 5% and 10% over target (attention)
  - 🔴 **Red** — more than 10% over target (over budget)

### Charts

- **Bar chart**: side-by-side bars for target vs actual per category — you can immediately see where you are on track and where you are overspending.
- **Donut chart**: distribution of actual expenses across categories.

> **Note:** Internal transfers between your own accounts are excluded from calculations to avoid double-counting.

> **Practical example:** You set a target of max 30% on Housing, but last month you reached 38%. The traffic light is red, the bar chart shows the actual bar taller than the target. You know you need to cut back.

---

## 13. Checklist: is everything in order?

**Situation:** You want to check at a glance whether you are regularly importing all your transaction files, without gaps of months.

Go to **✅ Checklist**. You'll find a table with:
- **One row for each month**, from the current month going back into the past
- **One column for each account** you have configured in Settings

Each cell shows the number of transactions imported for that month and account. If the number is **—** (grey), you have no transactions for that combination.

> **Practical example:** You have three accounts — Conto Corrente, Carta Visa, Conto Deposito. You look at the check list and see that July 2024 shows "—" for Conto Deposito. This means you never imported the transactions file for that deposit account for that month. Go download it from the bank and import it.

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

## 14. Assistant: ask Spendif.ai for help

**Situation:** You have a question about how a feature works, how to import a file, or how to configure an option.

Click **💬 Assistant** in the sidebar. A chat opens where you can type your question in natural language.

### How it works

The chatbot adapts automatically to the LLM backend you configured in Settings:

- **If you use a cloud service** (OpenAI, Claude, etc.) — the chatbot searches FAQ and documentation, then generates an answer with AI. Answers are more natural and flexible.
- **If you use Ollama or vLLM** — same behaviour, but the model runs on your computer.
- **If you use llama.cpp or haven't configured any LLM** — the chatbot uses a deterministic system that finds the most similar question in pre-built FAQ. No LLM involved, works on any hardware.

### Suggested questions

On first open, the chatbot shows clickable suggested questions to get started. Examples:
- "How do I import a file?"
- "What formats are supported?"
- "How do I change a category?"

### Sources

When the chatbot answers, you can expand the **Sources** section to see which document or FAQ the answer was drawn from. If the chatbot doesn't find enough information, it says so explicitly.

> **Practical example:** You can't remember how to change the LLM backend. Open the Assistant and type "how do I change the AI model?". The chatbot searches the documentation and replies with step-by-step instructions.

---

## 15. Settings: changing the AI model

**Situation:** You want to use a different model for classification (e.g. Claude instead of local Ollama).

Go to **⚙️ Settings**:
- **Backend LLM:** choose between llama.cpp (local, free, default for new installations), Ollama (local, free), OpenAI, Claude, or any OpenAI-compatible provider (Groq, Google AI Studio, etc.)
- **Model:** specify the model name (e.g. `gpt-4o-mini`, `claude-3-5-haiku-20241022`, `gemma2-9b-it`) or select a `.gguf` file for llama.cpp
- **API Key:** enter the key if you use a remote service

> **Privacy note:** If you use a remote backend (OpenAI or Claude), Spendif.ai automatically removes IBANs, card numbers, tax identification numbers, and the account holder's name before sending any data.

For detailed instructions on where to register and how to obtain API keys for each provider, see the **[Configuration Manual](configurazione.en.md)**.

### Account type

Every account has a mandatory **type** indicating the financial instrument:

| Type | Label (IT) | Label (EN) |
|------|------------|------------|
| `bank_account` | Conto corrente | Bank account |
| `credit_card` | Carta di credito | Credit card |
| `debit_card` | Carta di debito | Debit card |
| `prepaid_card` | Carta prepagata | Prepaid card |
| `savings_account` | Conto risparmio | Savings account |
| `cash` | Contanti | Cash |

> Only **credit cards** require special treatment (sign inversion: expenses in the CSV are positive but must be recorded as outflows). Debit and prepaid cards have identical sign behaviour but are separate values because the labels are clear and unambiguous for the user. The file format (single column, debit/credit split, etc.) is detected automatically by Spendif.ai — you only need to indicate *what type of instrument it is*.

### Renaming an account

You can rename a bank account at any time from **⚙️ Settings → 🏦 Bank Accounts**. When you rename an account, Spendif.ai automatically recalculates the unique identifier of every associated transaction, because the account name is part of the hash key.

The operation is **atomic**: if anything goes wrong during recalculation, no data is changed. Your data always remains intact.

> **In practice:** rename the account without worry. Transactions, categories, rules, and associated internal transfers remain untouched. The only thing that changes is the internal technical identifier (invisible to you).

### Downloading a model (llama.cpp)

If you use llama.cpp as your backend (default for new installations), you can download a GGUF model directly from the app:

1. Go to **⚙️ Settings → 🤖 LLM Configuration**
2. Select the **llama.cpp (local)** backend
3. Choose a suggested model (e.g. `gemma-4-E2B-it-Q4_K_M`, ~3.1 GB) or paste a direct URL
4. Click **⬇️ Download**

Models are saved in `~/.spendifai/models/`. The **Local models** section shows the available `.gguf` files, with path and size. Selecting a model from the list automatically fills in the path and **context window**.

> **Gemma 4 E2B** (`gemma-4-E2B-it-Q4_K_M` or `Q3_K_M`) is the recommended model for machines with 4-6 GB of RAM — excellent quality for Italian, latest-generation architecture.

### Downloading a model (Ollama)

If you use Ollama as your backend, you can download or update the model directly from the app without opening a terminal:

1. Go to **⚙️ Settings → 🤖 LLM Configuration**
2. Enter the model name (e.g. `gemma3:12b`)
3. Click **⬇️ Pull modello**

A progress bar shows the downloaded MB. The download may take a few minutes (`gemma3:12b` is about 8 GB).

### Checking that the model works

For any backend (llama.cpp, Ollama, OpenAI, Claude, Compatible):

1. Configure backend, URL/API key, and model
2. Click **🧪 Test LLM**

Spendif.ai sends a test prompt ("PAGAMENTO POS FARMACIA") and shows:
- The model's response (category + confidence level)
- **📐 Context window** — configured tokens and the model's native maximum (e.g. `📐 configured: 8192 tokens · model max: 131072 tokens`)

The context window is detected automatically when you change model for all backends. If something is wrong, the error message indicates whether the problem is the connection, the API key, or the model.

> **Tip:** always run the test after changing model or backend, before starting an import.

### Clearing saved file schemas

Spendif.ai remembers the structure of each imported file (columns, date format, sign convention) to speed up future imports. If a file was imported with the wrong schema — for example, income rows are missing or columns are swapped — you can reset the cache:

1. Go to **⚙️ Settings → 📐 Imported File Schemas**
2. Click **🗑️ Cancella tutti gli schemi salvati**
3. Re-import the file — Spendif.ai re-analyses it from scratch and asks you to confirm the schema

> **When to use:** if the import summary shows discarded rows with reason "Importo non parsabile" for income (or expense) rows, the saved schema probably uses the wrong sign convention. Clear and re-import.

---

## Benchmark tools (for advanced users)

Spendif.ai includes two benchmark scripts in the `tests/` folder:

- **`benchmark_classifier.py`** — measures schema detection quality (header, columns, date format, sign convention).
- **`benchmark_categorizer.py`** — measures LLM categorisation quality in isolation (no database, no user rules, no history). Metrics: exact accuracy, fuzzy accuracy (top-level category), fallback rate.

Both support the same CLI arguments: `--runs`, `--files`, `--backend`, `--model`, `--model-path`. Useful for comparing different models before choosing which one to use in production.

---

## Frequently asked questions

**Can I import files from different banks together?**
Yes. Spendif.ai recognises the format automatically. You don't need to tell it what type of file it is.

**I imported a file twice by mistake. Is that a problem?**
No. Duplicate transactions are ignored.

**A transaction appears twice — once on the account and once on the card.**
Spendif.ai handles this automatically (it's called "card-account reconciliation"). If you still see a duplicate, check in Review whether one of them has the 🔄 icon.

**I want to export the data.**
In Analytics you'll find the **Esporta** button which generates HTML, CSV, or XLSX.

**I changed the Taxonomy but the old transactions haven't updated.**
Old categories stay as they are. You can reprocess them manually from the Review page or by re-applying the rules.
