from asyncio.log import logger
import io
import json
import pdfplumber
import pandas as pd
import openai
from thefuzz import fuzz
from langchain_ollama import OllamaLLM as Ollama
from support.review_values import _clean_descriptions_with_patterns, _map_columns_with_llm
from support.globals import DEFAULT_CATEGORIES
from support.logging import setup_logging

logger = setup_logging()

def load_csv(file_path, decimal=",", thousands="."):
    """
    Carica un file CSV in un DataFrame pandas con gestione di decimali e migliaia.
    """
    try:
        df = pd.read_csv(file_path, sep=None, engine='python', decimal=decimal, thousands=thousands)
        logger.info(f"CSV caricato con successo: {file_path} con {len(df)} righe")
        return df
    except Exception as e:
        logger.error(f"Errore nel caricamento del CSV: {str(e)}", exc_info=True)
        return pd.DataFrame()

def load_excel(file_path):
    """
    Carica un file Excel in un DataFrame pandas.
    """
    try:
        df = pd.read_excel(file_path)
        logger.info(f"Excel caricato con successo: {file_path} con {len(df)} righe")
        return df
    except Exception as e:
        logger.error(f"Errore nel caricamento dell'Excel: {str(e)}", exc_info=True)
        return pd.DataFrame()

def load_pdf(pdf_path):
    """
    Estrae transazioni da PDF (Conto/Carta) con pulizia dei codici tecnici bancari (CAU, NDS, POS, ecc.).
    """
    logger.info(f"Avvio estrazione da: {pdf_path}")
    all_transactions = []
    iban_finale = "NON_RILEVATO"

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text(layout=True)
                if not text or not text.strip():
                    continue

                prompt = f"""

                Sei un esperto di movimenti bancari.

                Ti viene fornito il TESTO COMPLETO di una pagina di un file movimenti PDF.
                Il testo può contenere intestazioni, saldi, note e TABELLE.

                OBIETTIVO:
                - Individua SOLO la tabella delle TRANSAZIONI
                - Estrai UNA riga per ogni transazione
                - NON includere saldi, riepiloghi
                - NON aggiungere nulla, se non esiste restituisci una lista vuota

                TESTO PAGINA {i+1}:
                {text}

                REGOLE:
                - Le date possono essere in qualsiasi formato (DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, DD.MM.YY ecc.)
                - L'importo va restituito ESATTAMENTE come appare (stringa, con segni e separatori ed eventuale testo CR/DB)
                - Mantieni la descrizione completa cosi come appare
                - NON normalizzare numeri o date
                - NON interpretare Entrata/Uscita
                - NON prendere le transazioni senza data o importo o descrizione, le righe riepilogative, di saldo, riferite a movimenti di periodi precendenti
                - NON aggiungere righe inesistenti

                RISPONDI SOLO IN JSON VALIDO:

                {{
                "iban_o_carta": "stringa | null",
                "account_type": "conto_corrente | carta_credito | null",
                "transazioni": [
                    {{
                    DATE_OPERATION: "stringa | null",
                    DATE_VALUE: "stringa | null",
                    DESCRIPTION: "stringa",
                    AMOUNT: "stringa",
                    "valuta": "stringa | null"
                    }}
                ]
                }}
                """                
                
                response = llm.invoke(prompt)
                
                # Parsing JSON sicuro
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                page_data = json.loads(response[json_start:json_end])
                
                if page_data.get('iban_o_carta') and iban_finale == "NON_RILEVATO":
                    iban_finale = page_data['iban_o_carta']
                
                if page_data.get('formato_importi_rilevato'):
                    logger.info(f"Formato importi rilevato: {page_data['formato_importi_rilevato']}")
                
                if page_data.get('transazioni'):
                    logger.info(f"Pagina {i+1}: Estratte {len(page_data['transazioni'])} transazioni")
                    all_transactions.extend(page_data['transazioni'])
                    logger.info(f"Totale transazioni accumulate: {len(all_transactions)}")

        if not all_transactions:
            return iban_finale, pd.DataFrame()

        df = pd.DataFrame(all_transactions)

        # rimuovi transazioni senza data o descrizione o importo
        df = df[
            df[DATE_OPERATION].notna() &
            df[DESCRIPTION].notna() &
            df[AMOUNT].notna() &
            (df[DATE_OPERATION].astype(str).str.strip() != "") &
            (df[DESCRIPTION].astype(str).str.strip() != "") &
            (df[AMOUNT].astype(str).str.strip() != "")
        ]

        if page_data.get('account_type') == "carta_credito":
            # metti un segno meno nell'importo togliendo "CR" o "DB"
            if AMOUNT in df.columns:
                df[AMOUNT] = df[AMOUNT].astype(str).apply(
                    lambda x: '-' + x.replace('CR', '').replace('DB', '').strip() 
                    if 'CR' in x or 'DB' in x else x
                )

            # mettere davanti ai numeri con segno meno un segno positivo altimenti mettere davanti ai numero senza segno un segno negativo
            # Normalizzazione segni importi: se negativo aggiungi +, se positivo aggiungi -
            if AMOUNT in df.columns:
                df[AMOUNT] = df[AMOUNT].astype(str).apply(
                    lambda x: '+' + x.lstrip('-') if str(x).startswith('-') else '-' + x
                )
        

        df.to_excel("./backup/pdf_debug_extracted_transactions.xlsx", index=False)  # Salvataggio per debug
                
        return iban_finale, df

    except Exception as e:
        logger.error(f"Errore critico: {str(e)}", exc_info=True)
        return None, pd.DataFrame()

def get_monthly_summary(df):
    logger.info(f"Generating monthly summary for {len(df)} rows")
    if df.empty:
        return pd.DataFrame()
    temp = df.copy()
    temp['Data_Valuta'] = pd.to_datetime(temp['Data_Valuta'], dayfirst=True, errors='coerce')
    temp = temp.dropna(subset=['Data_Valuta'])
    temp['Anno'], temp['Mese'] = temp['Data_Valuta'].dt.year, temp['Data_Valuta'].dt.month
    summary = temp.groupby(['Anno', 'Mese', 'Categoria'])['Uscita'].sum().abs().reset_index()
    logger.info(f"Monthly summary generated: {len(summary)} rows")
    return summary.sort_values(['Anno', 'Mese'], ascending=False)

def check_budget_alerts(summary_df, budget_limits):
    logger.info(f"Checking budget alerts against {len(budget_limits)} limits")
    if summary_df.empty:
        return pd.DataFrame()
    latest = summary_df.sort_values(['Anno', 'Mese'], ascending=False).groupby('Categoria').head(1)
    alerts = []
    for _, row in latest.iterrows():
        limite = budget_limits.get(row['Categoria'], 0)
        if limite > 0:
            perc = (row['Uscita'] / limite) * 100
            alert_status = "🔴 Superato" if row['Uscita'] > limite else "🟢 OK"
            alerts.append({'Categoria': row['Categoria'], 'Spesa': row['Uscita'], 'Limite': limite, 
                           'Stato': alert_status, 'Utilizzo': f"{perc:.1f}%"})
            logger.info(f"Alert: {row['Categoria']} - {alert_status} ({perc:.1f}%)")
    return pd.DataFrame(alerts)

def get_upload_log(df):
    logger.info(f"Generating upload log for {len(df)} transactions")
    if df.empty:
        return pd.DataFrame()
    temp = df.copy()
    temp['Data_Valuta'] = pd.to_datetime(temp['Data_Valuta'], dayfirst=True, errors='coerce')
    temp['Anno'], temp['Mese'] = temp['Data_Valuta'].dt.year, temp['Data_Valuta'].dt.month
    result = temp.groupby(['IBAN', 'Anno', 'Mese']).size().reset_index(name='Transazioni').sort_values(['Anno', 'Mese'], ascending=False)
    logger.info(f"Upload log generated: {len(result)} entries")
    return result

def export_to_excel(df):
    logger.info(f"Exporting {len(df)} rows to Excel")
    try:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Report')
            workbook, worksheet = writer.book, writer.sheets['Report']
            chart = workbook.add_chart({'type': 'pie'})
            sum_df = df.groupby('Categoria')['Uscita'].sum().abs().reset_index()
            start_row = len(df) + 3
            sum_df.to_excel(writer, sheet_name='Report', startrow=start_row, index=False)
            chart.add_series({'categories': ['Report', start_row+1, 0, start_row+len(sum_df), 0],
                              'values': ['Report', start_row+1, 1, start_row+len(sum_df), 1]})
            worksheet.insert_chart('G2', chart)
        logger.info("Excel export completed successfully")
        return output.getvalue()
    except Exception as e:
        logger.error(f"Excel export failed: {str(e)}", exc_info=True)
        return None