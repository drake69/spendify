from support.globals import DEFAULT_CATEGORIES, OWN_NAMES, DEFAULT_CONTEXTS, MODEL_PRIMARY, MODEL_SECONDARY
from services.llm_services import build_llm_batch_prompt, initialize_model, unload_model, get_optimal_context, get_ollama_token_count, call_llm, make_cache_key
from support.logging import setup_logger
import pandas as pd
import time
import re

logger = setup_logger()

def apply_fallback_ai(df, ai_mode, api_key, categorize_fn):
    if ai_mode == "Nessuno":
        return df

    for idx, row in df.iterrows():
        if row["Categoria"] == "Varie da Identificare":
            df.at[idx, "Categoria"] = categorize_fn(
                row["Descrizione"],
                ai_mode,
                api_key
            )
    return df

# --- CORE PROCESSING ---
def process_full_pass(df, model_name):
    results = {}
    print(f"\n>>> AVVIO PASSATA CON: {model_name}")

    # Inizializziamo il modello una sola volta per passata
    llm = initialize_model(model_name)
    
    # Otteniamo il limite di contesto calcolato per il Mac
    ctx_limit = get_optimal_context(model_name, reserve_gb=2)
    max_tokens_threshold = int(ctx_limit * 0.85)  # Margine di sicurezza più prudente
    
    current_idx = 0
    start_time = time.time()
    total_rows = len(df)

    while current_idx < len(df):
        batch_size = 70
        exit = False
        # 1. Trova il batch size ottimale che non superi la finestra di contesto
        while True:
            # Non andare oltre la fine del dataframe
            temp_batch = df.iloc[current_idx : current_idx + batch_size]
            records = [
                {
                    "index": i,
                    "data": str(row["Data_Operazione"]),
                    DESCRIPTION: str(row["Descrizione"]),
                    "entrata": row.get("Entrata", 0),
                    "uscita": row.get("Uscita", 0)
                }
                for i, row in temp_batch.iterrows()
            ]
            
            prompt = build_llm_batch_prompt(records)
            if exit == True: break
            estimated_tokens = get_ollama_token_count(model_name, prompt)
            
            # Se siamo sotto soglia e c'è ancora margine per crescere
            if estimated_tokens < max_tokens_threshold and (current_idx + batch_size) < len(df):
                batch_size += 1# Incremento graduale
                continue 
            # Se abbiamo sforato, riduciamo leggermente e procediamo
            elif estimated_tokens > max_tokens_threshold and batch_size > 5:
                batch_size = int(batch_size * 0.9) 
                exit = True
            
            if current_idx + batch_size >= len(df):
                exit = True

        # 2. Esecuzione della chiamata con il batch trovato
        print(f"[{model_name}] Batch size: {len(temp_batch)} | Tokens stimati: {int(estimated_tokens)}")
        
        key = make_cache_key(records, model_name)
        out = call_llm(llm, prompt, key)
        
        if out:
            for item in out:
                try:
                    real_idx = item["index"]
                    results[real_idx] = item
                except Exception as e:
                    continue
        
        # 3. Avanzamento dell'indice globale e stima tempo
        current_idx += len(temp_batch)
        elapsed = time.time() - start_time
        avg_time_per_row = elapsed / current_idx
        remaining_rows = total_rows - current_idx
        eta_seconds = remaining_rows * avg_time_per_row
        eta_time = time.strftime("%H:%M:%S", time.gmtime(eta_seconds))
        finish_time = time.time() + eta_seconds
        print(f"Progress: {current_idx}/{total_rows} | Tempo trascorso: {elapsed/60:.1f}m | ETA: {eta_time} | Ora: {time.strftime('%H:%M:%S', time.localtime(finish_time))}")

    unload_model(model_name)
    total_time = time.time() - start_time
    print(f"\n✅ Passata {model_name} completata in {total_time/60:.1f} minuti")
    return results

def merge_results(idx, res_g, res_l):
    """Logica di decisione tra i due modelli."""
    # Se un modello manca, usa l'altro
    if not res_g: return res_l
    if not res_l: return res_g

    same_cat = res_g[CATEGORY] == res_l[CATEGORY]
    # Se concordano, media confidence. Se discordano, penalizza e scegli il migliore.
    conf = (res_g["confidence"] + res_l["confidence"]) / 2 if same_cat else max(res_g["confidence"], res_l["confidence"]) * 0.5
    
    best = res_g if res_g["confidence"] >= res_l["confidence"] else res_l

    return {
        "Controparte": res_g["controparte"] or res_l["controparte"],
        "Categoria": res_g[CATEGORY] if same_cat else best[CATEGORY],
        "Giroconto": res_g["giroconto"] or res_l["giroconto"],
        "Entrata_Reale": res_g["entrata_reale"] and res_l["entrata_reale"],
        "Confidence": round(conf, 2),
        "Motivazione": "Consenso" if same_cat else f"Scelto {best[CATEGORY]} (Divergenza)"
    }

# --- MAIN FUNCTION ---
def enrich_transactions(df, OWN_NAMES=OWN_NAMES):
    df_work = df.copy()

    descriptions_list = [desc.upper() for desc in df["Descrizione"] if isinstance(desc, str) and desc.strip()]

    logger.info(f"Original descriptions count: {len(descriptions_list)}")
    
    # replace all symbols with spaces eccetto apostrofo
    descriptions_list = [re.sub(r"[^\w\s']", ' ', desc) for desc in descriptions_list]

    # replace two spaces with one
    descriptions_list = [re.sub(r'\s+', ' ', desc).strip() for desc in descriptions_list]

    # --- PASSO 1: Rimozione automatica tramite regex generica ---
    regex_generic = r"""
        (\b\d{1,2}[/./-]\d{1,2}[/./-]\d{2,4}\b)   |  # date tipo 01/12/2025 o 01-12-2025 o 01.12.2025 o 2025.12.01
        (\b\d{1,3}(?:[.,]\d{2})?\s?(?:€|EUR)\b)   |  # importi tipo 123,45 € o 123.45 EUR
        (\bCARTA \*{4}\d{4}\b)                     |  # carte tipo ****1234
        (\bCAU \d+\b)                              |  # codici transazione numerici
        (\bID transazione \d+\b)                   |  # ID transazione
        (\b\d+\b)                                    # numeri generici residui (inclusi numeri isolati)
    """

    pre_cleaned_descriptions = [
        re.sub(regex_generic, '', desc, flags=re.VERBOSE | re.IGNORECASE).strip()
        for desc in descriptions_list
    ]

    # remove residual symbols and extra spaces before and after
    pre_cleaned_descriptions = [
        re.sub(r'[^\w\s]', '', desc.upper()).strip()
        for desc in pre_cleaned_descriptions
    ]

    # replace multiple spaces with one
    pre_cleaned_descriptions = [
        re.sub(r'\s+', ' ', desc).strip()
        for desc in pre_cleaned_descriptions
    ]

    # creiamo un vocabolario con la conta delle parole splittando le descrizioni per spazio
    words = [w for desc in pre_cleaned_descriptions for w in desc.split()]
    # exclude from words own names
    for name in OWN_NAMES:
        name_parts = name.upper().split()
        words = [w for w in words if w not in name_parts]

    word_counts = pd.Series(words).value_counts()
    # save as dataframe
    df_word_counts = word_counts.reset_index()
    df_word_counts.columns = ['word', 'count']
    # sort by count descending
    df_word_counts = df_word_counts.sort_values(by='count', ascending=False)
    df_word_counts.to_excel("/Users/lcorsaro/Desktop/word_counts.xlsx", index=False)
    number_of_words = len(word_counts)

    common_words = word_counts[word_counts/number_of_words > 0.05].index.tolist()  # parole comuni


    logger.info(f"Parole comuni identificate per la pulizia: {common_words}")

    logger.info("Pulizia preliminare completata con regex generica. Alcune descrizioni esempio:")

    # replace more than one space with one space
    pre_cleaned_descriptions = [
        re.sub(r'\s+', ' ', desc).strip()
        for desc in pre_cleaned_descriptions
    ]

    df['Descrizione'] = pre_cleaned_descriptions
    df['Controparte'] = None
    df['Categoria'] = None
    df['Giroconto'] = None
    df['Entrata_Reale'] = None
    df['Confidence'] = None
    df['Motivazione'] = None

    # sort by Data_Operazione ascending
    df_work = df_work.sort_values(by="Data_Operazione", ascending=True).reset_index(drop=True)
    
    # Passata 1: Gemma
    primary_model_dict = process_full_pass(df_work, MODEL_PRIMARY)

    pd.DataFrame.from_dict(primary_model_dict, orient="index").to_excel("/Users/lcorsaro/Desktop/gemma_output.xlsx")

    # Passata 2: Llama
    secondary_model_dict = process_full_pass(df_work, MODEL_SECONDARY)
    pd.DataFrame.from_dict(secondary_model_dict, orient="index").to_excel("/Users/lcorsaro/Desktop/llama_output.xlsx")
    
    # Merge finale
    print("\n>>> Esecuzione Merge dei risultati...")
    final_data = []
    for idx in df_work.index:
        res = merge_results(idx, primary_model_dict.get(idx), secondary_model_dict.get(idx))
        if res:
            final_data.append(res)
        else:
            final_data.append({})

    df_res = pd.DataFrame(final_data, index=df_work.index)
    df_final = pd.concat([df_work, df_res], axis=1)
    
    # Pulizia e coerenza (tue funzioni originali)
    # df_final = deterministic_giroconto_override(df_final)
    # df_final = enforce_category_consistency(df_final)
    
    return df_final

def integrate_historical_mappings(df, historical_df=None):
    """
    Integra mappature storiche (controparte -> categoria) da un dataframe storico.
    Se una controparte esiste nello storico, usa quella categoria con alta confidence.
    
    Args:
        df: DataFrame con colonne 'Controparte' e 'Categoria' da arricchire
        historical_df: DataFrame storico con colonne 'Controparte' e 'Categoria'
        
    Returns:
        DataFrame arricchito con mappature storiche
    """
    if historical_df is None or historical_df.empty:
        logger.warning("Nessun dataframe storico fornito")
        return df
    
    df_work = df.copy()
    
    # Crea dizionario storico: controparte -> categoria più frequente
    historical_mappings = {}
    for _, row in historical_df.iterrows():
        controparte = str(row.get("Controparte", "")).strip()
        categoria = str(row.get("Categoria", "")).strip()
        
        if controparte and categoria and controparte.upper() != "NONE":
            key = controparte.upper()
            if key not in historical_mappings:
                historical_mappings[key] = {}
            historical_mappings[key][categoria] = historical_mappings[key].get(categoria, 0) + 1
    
    # Prendi la categoria più frequente per ogni controparte
    historical_mappings = {
        k: max(v.items(), key=lambda x: x[1])[0] 
        for k, v in historical_mappings.items()
    }
    
    logger.info(f"Mappature storiche caricate: {len(historical_mappings)}")
    
    # Applica le mappature storiche
    for idx, row in df_work.iterrows():
        controparte = str(row.get("Controparte", "")).strip().upper()
        
        if controparte in historical_mappings:
            # Sovrascrivi categoria se manca o con bassa confidence
            if pd.isna(row.get("Categoria")) or row.get("Confidence", 0) < 0.7:
                df_work.at[idx, "Categoria"] = historical_mappings[controparte]
                df_work.at[idx, "Confidence"] = 0.95
                df_work.at[idx, "Motivazione"] = "Da storico"
    
    return df_work

def apply_description_smart_cleaning(df):
    """
    Applica la pulizia basata su pattern all'intero DataFrame.
    Ora restituisce direttamente tutte le controparti pulite,
    senza mappature, perché le descrizioni sono quasi tutte uniche.
    """
    
    # dividi per Entrata e Uscita
    df_entrata = df[df['Entrata'] > 0].copy()
    df_uscita = df[df['Uscita'] > 0].copy()

    # Estrai tutte le descrizioni (inclusi duplicati) per entrate
    desc_list_entrata = df_entrata['Descrizione'].tolist()
    clean_list_entrata = list(_clean_descriptions_with_patterns(desc_list_entrata).values()) 
    if not isinstance(clean_list_entrata, list) or len(clean_list_entrata) != len(desc_list_entrata):
        clean_list_entrata = desc_list_entrata

    # Estrai tutte le descrizioni (inclusi duplicati) per uscite
    desc_list_uscita = df_uscita['Descrizione'].tolist()
    clean_list_uscita = list(_clean_descriptions_with_patterns(desc_list_uscita).values()) 
    if not isinstance(clean_list_uscita, list) or len(clean_list_uscita) != len(desc_list_uscita):
        clean_list_uscita = desc_list_uscita

    # Applica le descrizioni pulite ai rispettivi dataframe
    df_entrata['Controparte'] = clean_list_entrata
    df_uscita['Controparte'] = clean_list_uscita

    # Ricombina i dataframe
    df = pd.concat([df_entrata, df_uscita], ignore_index=True)

    # ordine per Data crescente
    df['Data_Valuta'] = pd.to_datetime(df['Data_Valuta'], dayfirst=True, errors='coerce')
    df = df.sort_values(by='Data_Valuta').reset_index(drop=True)

    # add day of week column as first colunn
    if 'Giorno_Settimana' not in df.columns:
        df.insert(0, 'Giorno_Settimana', df['Data_Valuta'].dt.day_name())
    else:
        df['Giorno_Settimana'] = df['Data_Valuta'].dt.day_name()

    # add column Contesto
    df['Contesto'] = ""

    df.to_excel("./backup/description_smart_cleaning_debug.xlsx", index=False)  # Salvataggio per debug

    return df

def apply_enhanced_categorization(df, history_kb):
    logger.info(f"Starting categorization for {len(df)} transactions")
    df['Richiede_Documento'] = False
    df['Link_Ricevuta'] = ""
    df['Stato_Riconciliazione'] = "OK"
    df['Categoria_Approvata'] = False
    
    if 'Categoria' not in df.columns:
        df['Categoria'] = 'Altro'

    for idx, row in df.iterrows():
        desc = str(row['Descrizione']).lower()
        
        match = get_fuzzy_category(desc, history_kb)
        if match:
            df.at[idx, 'Categoria'] = match
            logger.info(f"Row {idx}: Fuzzy matched to {match}")
        else:
            for cat, info in DEFAULT_CATEGORIES.items():
                if any(key in desc for key in info['keywords']):
                    df.at[idx, 'Categoria'] = cat
                    logger.info(f"Row {idx}: Rule matched to {cat}")
                    break
        
        final_cat = df.at[idx, 'Categoria']
        info_cat = DEFAULT_CATEGORIES.get(final_cat, DEFAULT_CATEGORIES['Varie da Identificare'])
        if info_cat['richiede_doc']:
            df.at[idx, 'Richiede_Documento'] = True
            df.at[idx, 'Stato_Riconciliazione'] = "In Attesa Ricevuta"
            
    logger.info("Categorization completed")
    return df

def get_fuzzy_category(description, kb, threshold=80):
    if not kb:
        return None
    description = str(description).lower()
    best_match, highest_score = None, 0
    for hist_desc, category in kb.items():
        score = fuzz.partial_ratio(description, hist_desc.lower())
        if score > highest_score:
            highest_score, best_match = score, category
    
    if highest_score >= threshold:
        logger.info(f"Fuzzy match: '{description}' -> {best_match} (score: {highest_score})")
        return best_match
    return None

def categorize_with_llm(description, provider="ollama", api_key=None):
    logger.info(f"Categorizing with LLM (provider: {provider}): {description}")
    prompt = f"Categorizza questa transazione: '{description}'. Rispondi SOLO con il nome della categoria (es. Alimentari, Trasporti, Casa, Shopping, Intrattenimento, Salute)."
    try:
        if provider == "openai" and api_key:
            logger.info("Using OpenAI provider")
            client = openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content": prompt}])
            result = response.choices[0].message.content.strip()
            logger.info(f"LLM response: {result}")
            return result
        elif provider == "ollama":
            logger.info("Using Ollama provider")
            result = Ollama(model="llama3").invoke(prompt).strip()
            logger.info(f"LLM response: {result}")
            return result
    except Exception as e:
        logger.error(f"LLM categorization failed: {str(e)}", exc_info=True)
    return "Altro"

