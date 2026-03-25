# Transaction Classification Guide

> How Spendify turns your bank statements into categorised data with minimal manual effort.

---

## 1. Import

Upload your bank statement (CSV, XLSX, XLS) by dragging it into the upload area.

- Spendify recognises the file schema automatically and computes a **confidence score** (0-100%).
- If confidence is >= 80%, import proceeds without user intervention.
- Transactions are categorised by AI (LLM) and any existing deterministic rules.

---

## 2. Review

Transactions where the AI was not confident are flagged for review.

- Uncertain transactions are marked with a **warning** flag (needs review)
- For each one you can correct **category**, **subcategory** and **context** from dropdown menus
- Confirm with **Validated** to tell Spendify "this classification is correct"
- You can also create a rule directly from the Review page, just like in the Ledger

---

## 3. Ledger — The command centre

Full view of all imported transactions, with filters for date, account, category and type.

**Direct editing:** change category, subcategory and context directly in the grid. Each change updates `classification_source` to "manual".

**Validation:** tick the Validated checkbox to confirm the category is correct. Saving is immediate and the warning flag is automatically cleared.

**Create a rule from a transaction:**

1. Select **1 row** in the ledger grid
2. A **"Create rule"** form appears, pre-filled with:
   - **Pattern**: extracted from the transaction's counterpart (e.g. `ESSELUNGA`)
   - **Match type**: `contains` (default)
   - **Category/Subcategory/Context**: copied from the selected transaction
3. A **preview** shows how many transactions will be matched by the rule
4. Click **"Create rule and apply"**:
   - The rule is saved to the database
   - It is applied **retroactively** to all matching transactions that have not been validated yet

---

## 4. Automatic rules

Rules created (from the Ledger, Review or Rules page) are automatically applied to future imports.

- **Priority**: more specific rules have higher priority. First match wins.
- **Match types**: exact, contains, regex.
- **Rules page**: manage, edit, delete existing rules. Use the "Run all rules" button to apply them in bulk to your entire history.

---

## 5. The virtuous cycle

```
Import --> AI categorises --> User reviews --> Create rule --> Next import: 0 interventions
```

The more you use Spendify, the less manual work. Every rule you create reduces the number of transactions to review on the next import. Goal: **zero pain**.

---

## Indicator columns

| Indicator | Meaning |
|-----------|---------|
| Warning | Needs review (uncertain classification) |
| Validated | Validated by the user |
| Internal transfer | Internal transfer (transfer between own accounts) |

---

## Classification source badges

| Badge | Meaning |
|-------|---------|
| AI | Categorised by artificial intelligence |
| Rule | Categorised by deterministic rule |
| Manual | Manually modified by the user |
| History | Categorised from history (future) |

---

For operational details on each page, see the [User Guide](guida_utente.en.md).
