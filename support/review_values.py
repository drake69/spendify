import json
import re
from dateutil import parser
import pandas as pd
from langchain_ollama import OllamaLLM as Ollama
from support.logging import setup_logging

logger = setup_logging()

DATE_SPLIT_RE = re.compile(r'[\/\-.]')




def _normalize_dates(df, locale):
    for col in ["Data_Valuta", "Data_Operazione"]:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: _parse_date_safe(x, locale)
            )

    # fallback: se Data vuota usa Data_Operazione
    if "Data_Valuta" in df.columns and "Data_Operazione" in df.columns:
        df["Data_Valuta"] = df.apply(
            lambda r: r["Data_Operazione"]
            if not r["Data_Valuta"] else r["Data_Valuta"],
            axis=1
        )

    return df

def _parse_date_safe(value, locale):
    if pd.isna(value) or str(value).strip() == "":
        return ""

    try:
        if locale == "EU":
            dt = parser.parse(value, dayfirst=True)
        else:
            dt = parser.parse(value, dayfirst=False)

        return dt.strftime("%d/%m/%Y")

    except Exception:
        return ""

def _clean_descriptions_with_patterns(descriptions_list):
    """
    Pulisce descrizioni bancarie:
    1. Rimuove automaticamente date, importi, numeri di carta e transazioni.
    2. Usa LLM per identificare pattern più complessi e ottenere le controparti pulite.
    """
    if not descriptions_list:
        return {}
    
    descriptions_list = [desc.upper() for desc in descriptions_list if isinstance(desc, str) and desc.strip()]

    # replace all symbols with spaces
    descriptions_list = [re.sub(r'[^\w\s]', ' ', desc) for desc in descriptions_list]

    # replace two spaces with one
    descriptions_list = [re.sub(r'\s+', ' ', desc).strip() for desc in descriptions_list]

    # creiamo un vocabolario con la conta delle parole splittando le descrizioni per spazio
    words = [w for desc in descriptions_list for w in desc.split()]
    word_counts = pd.Series(words).value_counts()
    # remove word with len < 3
    # word_counts = word_counts[word_counts.index.str.len() >= 3]
    number_of_words = len(word_counts)
    common_words = word_counts[word_counts/number_of_words > 0.05].index.tolist()  # parole comuni

    # --- PASSO 1: Rimozione automatica tramite regex generica ---
    regex_generic = r"""
        (\b\d{1,2}[/./-]\d{1,2}[/./-]\d{2,4}\b)   |  # date tipo 01/12/2025 o 01-12-2025 o 01.12.2025 o 2025.12.01
        (\b\d{1,3}(?:[.,]\d{2})?\s?(?:€|EUR)\b)   |  # importi tipo 123,45 € o 123.45 EUR
        (\bCARTA \*{4}\d{4}\b)                     |  # carte tipo ****1234
        (\bCAU \d+\b)                              |  # codici transazione numerici
        (\bID transazione \d+\b)                   |  # ID transazione
        (\b\d+\b)                                    # numeri generici residui (inclusi numeri isolati)
    """

    logger.info(f"Parole comuni identificate per la pulizia: {common_words}")
    # aggiungi alla regex le parole comuni identificate solo se intere
    if common_words:
        common_pattern = r'|'.join([rf'\b{re.escape(word)}\b' for word in common_words])
        regex_generic += f'|({common_pattern})'

    pre_cleaned_descriptions = [
        re.sub(regex_generic, '', desc, flags=re.VERBOSE | re.IGNORECASE).strip()
        for desc in descriptions_list
    ]

    # remove residual symbols and extra spaces befroe and after
    pre_cleaned_descriptions = [
        re.sub(r'[^\w\s]', '', desc).strip()
        for desc in pre_cleaned_descriptions
    ]

    logger.info("Pulizia preliminare completata con regex generica. Alcune descrizioni esempio:")
    # for original, cleaned in list(zip(descriptions_list, pre_cleaned_descriptions))[:10]:
    #     logger.info(f"Originale: {original} -> Pulita: {cleaned}")

    # replace more than one space with one space
    pre_cleaned_descriptions = [
        re.sub(r'\s+', ' ', desc).strip()
        for desc in pre_cleaned_descriptions
    ]


    # --- PASSO 2: Pulizia e estrazione finale con LLM ---
    prompt_final = f"""
    [SYSTEM]
    Sei un esperto di data mining bancario. Ora le descrizioni sono state parzialmente pulite 
    rimuovendo date, importi e numeri variabili. Estrai il nome finale del beneficiario/mittente.
    Non aggiungere altre informazioni e non lasciare campi vuoti.

    [USER]
    Descrizioni pulite parzialmente:
    {pre_cleaned_descriptions}

    COMPITI:
    1. Identifica eventuali pattern ricorrenti residui (es: "Pagam. POS", "Bonifico X").
    2. Estrai per ogni riga il nome del beneficiario/mittente pulito.

    RISPONDI SOLO IN JSON con schema:
    {{
        "patterns_identificati": ["elenco pattern rimossi"],
        "mapping": {{
            "descrizione_originale": "CONTROPARTE PULITA"
        }}
    }}
    """

    try:
        response_final = llm.invoke(prompt_final)
        json_start = response_final.find('{')
        json_end = response_final.rfind('}') + 1
        result_final = json.loads(response_final[json_start:json_end])

        mapping_clean = result_final.get("mapping", {})
        patterns_identificati = result_final.get("patterns_identificati", [])
        logger.info(f"Pattern residui identificati dall'AI: {patterns_identificati}")

        logger.info("Pulizia finale completata con LLM. Alcune mappature esempio:")
        for orig, clean in list(mapping_clean.items())[-10:]:
            logger.info(f"Originale: {orig} -> Pulita: {clean}")

        return mapping_clean

    except Exception as e:
        logger.error(f"Errore nella pulizia finale con LLM: {e}")
        # Fallback: restituisce le descrizioni già pulite tramite regex
        return {orig: clean for orig, clean in zip(descriptions_list, pre_cleaned_descriptions)}


