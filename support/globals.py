import json
import my_secrets

OWN_NAMES = my_secrets.OWN_NAMES
API_KEY = my_secrets.API_KEY

DB_PATH = "history_database.csv"
LLM_CACHE_PATH = "./llm_cache"

DATE_OPERATION = "data_operazione"
DATE_VALUE = "data_valuta"
DESCRIPTION = "descrizione"
AMOUNT = "importo"
CATEGORY = "categoria"
CONTEXT = "contesto"
NOTES = "note"

COLUMN_NAMES = [
    DATE_OPERATION,
    DATE_VALUE,
    DESCRIPTION,
    AMOUNT,
    CATEGORY,
    CONTEXT,
    NOTES
]



# Configurazione Categorie e Regole di Riconciliazione
DEFAULT_CATEGORIES = {
    'Alimentari': {'keywords': ['conad', 'coop', 'esselunga', 'lidl', 'carrefour','eurospin','il gigante'], 'richiede_doc': False},
    'Trasporti': {'keywords': ['eni', 'shell', 'q8', 'tamoil', 'telepass', 'trenitalia'], 'richiede_doc': False},
    'E-commerce': {'keywords': ['alibaba','amazon', 'ebay', 'marketplace', 'paypal'], 'richiede_doc': True},
    'Prelievo Contante': {'keywords': ['prelievo', 'atm', 'bancomat'], 'richiede_doc': True},
    'Salute': {'keywords': ['farmacia', 'studio medico', 'ospedale','medico','dentista'], 'richiede_doc': False},
    'Casa/Affitto': {'keywords': ['bonifico immob', 'rata mutuo', 'iren', 'enel'], 'richiede_doc': False},
    'Varie da Identificare': {'keywords': [], 'richiede_doc': True}
}

DEFAULT_CONTEXTS = ['Lavoro', 'Casa']

# Esempio di mappature verificate (Controparte -> Categoria)
CERTIFIED_MAPPINGS = {
    "Esselunga": "Alimentari",
    "Amazon Marketplace": "Altro",
    "Enel Energia": "Utenze",
    "Netflix": "Svago",
    "Trenitalia": "Trasporti",
    "Airchina": "Viaggi",
    "Unipol": "Assicurazione",
    "Ikea": "Casa",
    "Zara": "Abbigliamento"
}


# --- CONFIGURAZIONE ---
MODEL_PRIMARY = "gemma3:12b"
MODEL_SECONDARY = "mannix/llama3-12b:latest"
LOGGER_NAME = "ai_finance_manager"


def categories_llm_prompt(records, categories=DEFAULT_CATEGORIES, certified_mappings=CERTIFIED_MAPPINGS, OWN_NAMES=OWN_NAMES):
    """
    Builds a prompt to enrich bank transactions with structured LLM output.
    Includes certified mappings, priority logic, and self-transfer recognition.
    """
    names_list = ", ".join(OWN_NAMES)
    
    return f"""
You are a senior financial analyst. Your task is to enrich a list of bank transactions.

### 1. CERTIFIED REFERENCES (Maximum Priority):
If you identify one of the following counterparties, use STRICTLY the associated category:
{json.dumps(certified_mappings, indent=2, ensure_ascii=False)}

### 2. ALLOWED CATEGORIES:
{", ".join(categories)}

### 3. ANALYSIS LOGIC:
- **Counterparty**: Extract the clean name (e.g., "POS 12345 AMZN" -> "Amazon").
- **Self-Transfer**: Set to `true` if the transaction is a transfer between accounts/cards of the same user.
  **IMPORTANT**: If the payer or recipient matches one of these names: [{names_list}], classify as a SELF-TRANSFER.
- **Real Income**: `true` only if it increases net worth (e.g., Salary). Refunds, reversals, and especially SELF-TRANSFERS are `false`.
- **Priority**: If the transaction is not in "Certified References", use your knowledge to assign the most probable category.

### 4. OUTPUT CONSTRAINTS:
- Respond ONLY with a JSON array, no markdown formatting (no ```json).
- Use the exact schema below for each transaction.
- Maintain the original index order strictly.

### TRANSACTIONS TO PROCESS:
{json.dumps(records, ensure_ascii=False, indent=2)}

### EXPECTED JSON SCHEMA:
Respond with a JSON array of objects. Each object must follow this schema exactly:
{{
  "index": <integer>,
  "counterparty": "string or null",
  "self_transfer": <boolean>,
  "real_income": <boolean>,
  "confidence": <float between 0.0 and 1.0>,
  "category": "string",
  "reasoning": "brief explanation"
}}
""".strip()
