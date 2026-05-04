import json
import hashlib
import time
import psutil
import requests

from diskcache import Cache
from langchain_ollama import OllamaLLM
from litellm import token_counter
from support.globals import MODEL_PRIMARY, MODEL_SECONDARY, LLM_CACHE_PATH
from support.logging import setup_logging

logger = setup_logging()

llm_primary = OllamaLLM(model=MODEL_PRIMARY)
llm_secondary = OllamaLLM(model=MODEL_SECONDARY)

cache = Cache(LLM_CACHE_PATH)

def get_optimal_context(model_name, reserve_gb=4):
    """
    Calcola il num_ctx massimo basato sulla RAM libera del Mac
    ed evita di mandare il sistema in Swap.
    """
    try:
        # 1. Ottieni dimensione modello da Ollama
        resp = requests.post("http://localhost:11434/api/show", json={"name": model_name})
        model_info = resp.json()
        model_size_gb = int(model_info.get('size', 0)) / (1024**3)
        
        ctx_length = next(
            (v for k, v in model_info.get('model_info', {}).items() 
            if 'context_length' in k.lower()),
            None
        ) or 4096  # Default se non trovato

        # 2. RAM disponibile (Safe limit su Mac = 75% della totale)
        total_ram = psutil.virtual_memory().total / (1024**3)
        safe_limit = total_ram * 0.75
        
        # 3. Spazio rimanente per il contesto (KV Cache)
        # Sottraiamo la dimensione del modello e una riserva per il sistema
        available_for_kv = safe_limit - model_size_gb - reserve_gb
        
        if available_for_kv <= 0:
            print(f"⚠️ Memoria limitata per {model_name}. Context minimo impostato.")
            return 2048 # Fallback minimo di sicurezza

        # 4. Calcolo Token (Stima conservativa per modelli GGUF)
        # ~1GB ogni 8k-10k token per modelli medi (12b-30b)
        # ~1GB ogni 2k-3k token per modelli giganti (70b)
        is_giant = "70b" in model_name.lower()
        multiplier = 3000 if is_giant else 8000
        
        estimated_ctx = int(available_for_kv * multiplier)
        
        # Cap di sicurezza (Ollama default o necessità del batch)
        # Per le transazioni bancarie, raramente serve più di 16k se il batch è da 20
        return min(max(estimated_ctx, 2048), ctx_length)

    except Exception as e:
        print(f"Errore calcolo contesto: {e}")
        return 4096 # Default prudenziale

def initialize_model(model_name):
    """Inizializza il modello con il contesto calcolato dinamicamente."""
    ctx_size = get_optimal_context(model_name)
    print(f"🚀 Inizializzazione {model_name} con num_ctx: {ctx_size}")
    
    return OllamaLLM(
        model=model_name,
        num_ctx=ctx_size,
        num_gpu=999,      # Forza Metal su Mac
        temperature=0.1   # Precisione analitica
    )

# --- LOGICA DI MEMORIA ---
def unload_model(model_name):
    """Forza Ollama a rimuovere il modello dalla VRAM/RAM."""
    try:
        requests.post("http://localhost:11434/api/generate", 
                      json={"model": model_name, "keep_alive": 0})
        print(f"--- Modello {model_name} scaricato con successo ---")
        time.sleep(2) # Breve pausa per stabilizzare la RAM
    except Exception as e:
        print(f"Errore unload: {e}")

# --- HELPERS ---
def make_cache_key(records, model_prefix):
    raw = json.dumps(records, sort_keys=True)
    return hashlib.md5(f"{raw}_{model_prefix}".encode()).hexdigest()

# pip install litellm
    
def get_ollama_token_count(model_name, prompt):
    # Traduciamo il nome per litellm se necessario
    # litellm capisce "ollama/gemma3:12b"
    full_model_name = f"ollama/{model_name}"
    
    messages = [{"role": "user", "content": prompt}]
    try:
        return token_counter(model=full_model_name, messages=messages)
    except:
        # Fallback se il modello non è mappato
        return token_counter(model="gpt-4", messages=messages)


def call_llm(llm, prompt, cache_key):

    if cache_key in cache:
        return cache[cache_key]

    res = llm.invoke(prompt)
    try:
        # Estrazione flessibile del JSON
        start = res.find("[")
        end = res.rfind("]") + 1
        parsed = json.loads(res[start:end])
        cache[cache_key] = parsed
        return parsed
    except:
        return None


