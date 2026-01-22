import pytest
import pandas as pd
from unittest.mock import patch

from streamlit import json
import support.core_logic as core

@pytest.fixture
def mock_df():
    return pd.DataFrame({'Data_Valuta': ['01/01/2024'], 'Descrizione': ['AMAZON RETAIL'], 'Uscita': [-50.0], 'IBAN': ['IT123']})

def test_enhanced_categorization():
    df = pd.DataFrame({'Descrizione': ['AMAZON.IT'], 'Uscita': [-10.0]})
    res = core.apply_enhanced_categorization(df, {})
    assert res.iloc[0]['Categoria'] == 'Amazon/E-commerce'
    assert res.iloc[0]['Richiede_Documento'] == True

def test_fuzzy_match():
    kb = {"CONAD MILANO": "Alimentari"}
    assert core.get_fuzzy_category("SPESA POS CONAD ROMA", kb) == "Alimentari"


def test_duplicate_prevention():
    h = pd.DataFrame({'IBAN':['A'], 'Data_Valuta':['1/1'], 'Descrizione':['X'], 'Uscita':[10], 'Categoria_Approvata':[True]})
    n = pd.DataFrame({'IBAN':['A'], 'Data_Valuta':['1/1'], 'Descrizione':['X'], 'Uscita':[10], 'Categoria_Approvata':[False]})
    res = core.remove_duplicates(n, h)
    assert len(res) == 1
    assert res.iloc[0]['Categoria_Approvata'] == True

def test_budget_alerts():
    sum_df = pd.DataFrame({'Anno':[2024], 'Mese':[1], 'Categoria':['Alimentari'], 'Uscita':[600.0]})
    limits = {'Alimentari': 500}
    alerts = core.check_budget_alerts(sum_df, limits)
    assert alerts.iloc[0]['Stato'] == "🔴 Superato"

@patch('langchain_community.llms.Ollama.invoke')
def test_extract_pdf_with_iban_and_mapping(mock_ollama):
    # Simuliamo risposta con IBAN trovato nel testo della pagina
    mock_ollama.return_value = json.dumps({
        "iban_o_carta": "IT99L0123456789012345678901",
        "mapping": {
            "Data_Valuta": "Data_Operazione",
            "Causale": DESCRIPTION,
            "Uscita": "Uscita"
        },
        "account_type": "conto_corrente",
        "double_column": "Sì"
    })
    
    # Eseguiamo la funzione (il path è dummy grazie ai mock precedenti)
    df = core.load_pdf("mock.pdf")
    
    # Verifiche
    assert 'IBAN' in df.columns
    assert df.iloc[0]['IBAN'] == "IT99L0123456789012345678901"
    assert list(df.columns[:6]) == ['Data_Operazione', 'Data_Valuta', 'Descrizione', 'Valuta', 'Entrata', 'Uscita']