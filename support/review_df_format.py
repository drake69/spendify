def infer_year_position(series: pd.Series, min_year=1900):
    """
    Infers whether the year is at start, end, or ambiguous.
    Returns: 'start' | 'end' | 'ambiguous'
    """
    year_start = 0
    year_end = 0
    ends = []
    starts = []
    for val in series.dropna().astype(str):
        parts = DATE_SPLIT_RE.split(val.strip())
        if len(parts) != 3:
            continue

        try:
            nums = list(map(int, parts))
        except ValueError:
            continue

        if nums[0] >= min_year:
            year_start += 1
        starts.append(nums[0])
        if nums[2] >= min_year:
            year_end += 1
        ends.append(nums[2])

    count_start = len(set(starts))
    count_end = len(set(ends))

    if year_start > year_end * 1.5 or count_start < count_end:
        return "start"
    
    if year_end > year_start * 1.5 or count_end < count_start:
        return "end"

    return "ambiguous"

def expand_two_digit_years(series: pd.Series, year_position: str, pivot=30):
    """
    Expands 2-digit years to 4-digit years based on known year position.
    
    pivot=30:
      00–29 → 2000–2029
      30–99 → 1930–1999
    """

    def expand(val):
        if pd.isna(val):
            return val

        s = str(val).strip()
        parts = DATE_SPLIT_RE.split(s)

        if len(parts) != 3:
            return val

        if year_position == "start":
            year_idx = 0
        elif year_position == "end":
            year_idx = 2
        else:
            # ambiguous → do nothing
            return val

        year = parts[year_idx]

        if len(year) == 2 and year.isdigit():
            y = int(year)
            parts[year_idx] = str(2000 + y if y < pivot else 1900 + y)

        # recompose preserving original separators
        sep = re.findall(r'[\/\-.]', s)
        return parts[0] + sep[0] + parts[1] + sep[1] + parts[2]

    return series.apply(expand)

def _unify_column_descriptions(df):
    df = df.copy()

    # -----------------------------------
    # 1 Pre-filtro colonne testuali
    # -----------------------------------
    candidate_cols = [
        c for c in df.columns
        if df[c].dtype == object
        and df[c].astype(str).str.len().mean() > 5
        and not df[c].astype(str).str.match(r"^[\d\W]+$").all()
    ]

    logger.info(f"Candidate text columns: {candidate_cols}")

    if not candidate_cols:
        df["Descrizione"] = ""
        return df

    # -----------------------------------
    # 2 Merge vettoriale + deduplica
    # -----------------------------------
    if candidate_cols:
        merged = (
            df[candidate_cols]
            .astype(str)
            .replace("nan", "", regex=False)
            .agg(" ".join, axis=1)
        )

        df["Descrizione"] = merged.str.strip()

        df.drop(columns=candidate_cols, inplace=True, errors="ignore")
    else:
        df["Descrizione"] = ""

    # -----------------------------------
    # Debug
    # -----------------------------------
    df.to_excel("./backup/map_columns_with_llm_0.xlsx", index=False)

    return df

def _get_amount_info(df):
    df = df.copy()

    # -----------------------------------
    # 1️⃣ CANDIDATE NUMERICHE (PYTHON)
    # -----------------------------------
    numeric_candidates = []

    for col in df.columns:
        series = (
            df[col]
            .astype(str)
            .str.replace(r"[€$£\s]", "", regex=True)
            .str.replace(",", "", regex=False)
            .str.replace(".", "", regex=False)
        )
        parsed = pd.to_numeric(series, errors="coerce")
        if parsed.notna().mean() > 0.6:  # almeno 60% numeri
            numeric_candidates.append(col)

    logger.info(f"Colonne numeriche candidate: {numeric_candidates}")

    if not numeric_candidates:
        return {
            "case": "CASE_5",
            "columns": {
                AMOUNT: None,
                "entrata": None,
                "uscita": None,
                "saldo": None
            }
        }

    if len(numeric_candidates) == 1:
        logger.info("Solo una colonna numerica trovata, assumendo importo unico")
        return {
            "case": "CASE_5",
            "columns": {
                AMOUNT: numeric_candidates[0],
                "entrata": None,
                "uscita": None,
                "saldo": None
            }
        }
    else:
        logger.info("Multiple colonne numeriche trovate, procedendo con LLM")
        # -----------------------------------
        # 2️⃣ CAMPIONE RIDOTTO
        # -----------------------------------
        sample = df[numeric_candidates].head(20).to_json(orient="records")

        # -----------------------------------
        # 3️⃣ PROMPT LLM (RIDOTTO)
        # -----------------------------------
        amount_prompt = f"""
        Sei un esperto di estratti conto bancari.

        Colonne numeriche candidate:
        {numeric_candidates}

        Campione:
        {sample}

        Classifica la STRUTTURA degli importi scegliendo UN SOLO caso:

        CASE_1 = importo (+/-) e saldo
        CASE_2 = entrata (+), uscita (+), saldo
        CASE_3 = entrata (+), uscita (+)
        CASE_4 = uscita sola
        CASE_5 = importo (+/-)

        Rispondi SOLO in JSON:

        {{
        "case": "CASE_1 | CASE_2 | CASE_3 | CASE_4 | CASE_5",
        "columns": {{
            AMOUNT: "colonna | null",
            "entrata": "colonna | null",
            "uscita": "colonna | null",
            "saldo": "colonna | null"
        }}
        }}
        """

        # -----------------------------------
        # 4️⃣ LLM CALL + PARSING SICURO
        # -----------------------------------
        try:
            res = llm.invoke(amount_prompt)
            parsed = res[res.find("{"):res.rfind("}") + 1]
            amount_info = json.loads(parsed)
        except Exception as e:
            logger.warning(f"LLM amount detection fallito: {e}")
            amount_info = None

        # -----------------------------------
        # 5️⃣ VALIDAZIONE + FALLBACK
        # -----------------------------------
        valid_cols = set(df.columns)

    if not amount_info or "case" not in amount_info:
        logger.warning("Fallback automatico importo_unico")
        return {
            "case": "CASE_5",
            "columns": {
                AMOUNT: numeric_candidates[0],
                "entrata": None,
                "uscita": None,
                "saldo": None
            }
        }

    # elimina colonne inesistenti
    for k, v in amount_info["columns"].items():
        if v not in valid_cols:
            amount_info["columns"][k] = None

    return amount_info

def _normalize_dates_position(df):
    df = df.copy()

    date_pattern = r'^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}$|^\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}$|^\d{1,2}\.\d{1,2}\.\d{2,4}$|^\d{4}\.\d{1,2}\.\d{1,2}$'
    date_columns = [
        col for col in df.columns
        if df[col].astype(str).str.match(date_pattern).any()
    ]

    logger.info(f"Colonne candidate date: {date_columns}")

    sample = df.head(20).to_json(orient="records")

    date_prompt = f"""
        Sei un esperto di normalizzazione di estratti conto bancari.

        Colonne candidate:
        {date_columns}

        Campione CSV:
        {sample}

        TASK:
        - Identifica TUTTE le colonne data
        - Se sono 2, assegna data_operazione e data_valuta
        - Se è 1, assegnala a data_operazione
        - Non inventare colonne
        - estrai il formato data usato (es: %d/%m/%Y)

        Rispondi SOLO in JSON:

        {{
        "date_count": 1 | 2,
        "all_date_columns": ["col1","col2"],
        "format": ["%d/%m/%Y", "%Y-%m-%d"],
        "mapping": {{
            DATE_OPERATION: "nome_colonna | null",
            DATE_VALUE: "nome_colonna | null"
        }}
        }}
        """

    try:
        res = llm.invoke(date_prompt)
        date_info = json.loads(res[res.find("{"):res.rfind("}")+1])
    except Exception as e:
        logger.warning(f"LLM date detection fallito: {e}")
        date_info = None

    # -----------------------------
    # COSTRUZIONE MAPPING SICURO
    # -----------------------------
    rename_map = {}

    if date_info:
        op_col = date_info["mapping"].get(DATE_OPERATION)
        val_col = date_info["mapping"].get(DATE_VALUE)
        date_format = date_info.get("format", ["%d/%m/%Y", "%d/%m/%Y"])
        op_format = date_format[0]
        val_format = date_format[1] if len(date_format) > 1 else date_format[0]

        if op_col and op_col in df.columns:
            rename_map[op_col] = "Data_Operazione"

        if val_col and val_col in df.columns:
            rename_map[val_col] = "Data_Valuta"

    # Fallback deterministico
    if not rename_map and date_columns:
        rename_map[date_columns[0]] = "Data_Operazione"
        if len(date_columns) > 1:
            rename_map[date_columns[1]] = "Data_Valuta"

    # Applica rename UNA SOLA VOLTA
    df = df.rename(columns=rename_map)


    # -----------------------------
    # DUPLICAZIONE LOGICA
    # -----------------------------    
    if "Data_Operazione" in df.columns and "Data_Valuta" not in df.columns:
        pos = infer_year_position(df["Data_Operazione"])
        df["Data_Operazione"] = expand_two_digit_years(df["Data_Operazione"], pos)
        df["Data_Valuta"] = pd.to_datetime(df["Data_Operazione"], format=op_format, errors="coerce")

    if "Data_Valuta" in df.columns and "Data_Operazione" not in df.columns:
        pos = infer_year_position(df["Data_Valuta"])
        df["Data_Valuta"] = expand_two_digit_years(df["Data_Valuta"], pos)
        df["Data_Operazione"] = pd.to_datetime(df["Data_Valuta"], format=val_format, errors="coerce")

    df['Data_Operazione'] = pd.to_datetime(df['Data_Operazione'], errors='coerce',  format=op_format)
    df['Data_Valuta'] = pd.to_datetime(df['Data_Valuta'], errors='coerce',  format=val_format)

    return df

def _map_columns_with_llm(df):
    """
    Pipeline LLM in 3 fasi:
    1. Merge colonne testuali → Descrizione
    2. Classificazione struttura importi
    3. Normalizzazione finale Entrata / Uscita
    """
    df = df.copy()
    df = _unify_column_descriptions(df)
    df = _normalize_dates_position(df)

    amount_info = _get_amount_info(df)
    df = _normalize_amount_position(df, amount_info)
    
    return df

def _normalize_amount_position(df, info):
    logger.info(
        f"Normalizing amounts with case: {info['case']}, columns: {info['columns']}"
    )

    df = df.copy()
    case = info.get("case")
    cols = info.get("columns", {})

    df = _parse_amount_column(df, cols)

    df["Entrata"] = 0.0
    df["Uscita"] = 0.0

    # -----------------------------------
    # CASE 1 / 5 → importo (+/-)
    # -----------------------------------
    if case in {"CASE_1", "CASE_5"} and cols.get(AMOUNT) in df.columns:
        col = cols[AMOUNT]
        values = df[col]

        df["Entrata"] = values.where(values > 0, 0.0)
        df["Uscita"] = (-values).where(values < 0, 0.0)

        logger.info(
            f"Importo '{col}': +{(values>0).sum()} / -{(values<0).sum()}"
        )

        df.drop(columns=[col], inplace=True)

    # -----------------------------------
    # CASE 2 / 3 → entrata (+) / uscita (+)
    # -----------------------------------
    elif case in {"CASE_2", "CASE_3"}:
        if cols.get("entrata") in df.columns:
            df["Entrata"] = (df[cols["entrata"]]).abs()

        if cols.get("uscita") in df.columns:
            df["Uscita"] = (df[cols["uscita"]]).abs()

    # -----------------------------------
    # CASE 4 → solo uscita
    # -----------------------------------
    elif case == "CASE_4" and cols.get("uscita") in df.columns:
        df["Uscita"] = (df[cols["uscita"]]).abs()
        df["Entrata"] = 0.0

    else:
        logger.warning("Amount normalization fallback → zero amounts")

    # -----------------------------------
    # Debug
    # -----------------------------------
    df.to_excel("./backup/normalize_amounts_debug.xlsx", index=False)

    return df

def _parse_amount_column(df, cols):
    df = df.copy()

    for col in cols.values():
        if col not in df.columns:
            continue

        s = df[col].astype(str)

        # Rimuove valuta e spazi
        cleaned = (
            s.str.replace(r"[€$£\s]", "", regex=True)
             .replace("", pd.NA)
        )

        # --------------------------------------------------
        # 1️⃣ Conta pattern
        # --------------------------------------------------
        has_dot = cleaned.str.contains(r"\.", regex=True, na=False)
        has_comma = cleaned.str.contains(r",", regex=True, na=False)

        dot_comma = cleaned[has_dot & has_comma & (cleaned.str.rfind(".") < cleaned.str.rfind(","))].count()
        comma_dot = cleaned[has_dot & has_comma & (cleaned.str.rfind(",") < cleaned.str.rfind("."))].count()

        only_comma = cleaned[has_comma & ~has_dot].count()
        only_dot = cleaned[has_dot & ~has_comma].count()

        logger.info(
            f"[{col}] patterns → dot,comma:{dot_comma} | comma,dot:{comma_dot} "
            f"| only comma:{only_comma} | only dot:{only_dot}"
        )

        # --------------------------------------------------
        # 2️⃣ Decisione separatori
        # --------------------------------------------------
        decimal_sep = None
        thousand_sep = None

        if dot_comma > 0 or comma_dot > 0:
            # entrambi presenti → scegli il più frequente
            if dot_comma >= comma_dot:
                thousand_sep = "."
                decimal_sep = ","
            else:
                thousand_sep = ","
                decimal_sep = "."
        elif only_comma > 0:
            decimal_sep = ","
            thousand_sep = "."
        elif only_dot > 0:
            decimal_sep = "."
            thousand_sep = ","
        else:
            # fallback sicuro
            decimal_sep = "."
            thousand_sep = ","

        logger.info(
            f"[{col}] decimal='{decimal_sep}' thousand='{thousand_sep}'"
        )

        # --------------------------------------------------
        # 3️⃣ Normalizzazione finale
        # --------------------------------------------------
        normalized = cleaned

        if thousand_sep:
            normalized = normalized.str.replace(thousand_sep, "", regex=False)

        if decimal_sep != ".":
            normalized = normalized.str.replace(decimal_sep, ".", regex=False)

        df[col] = pd.to_numeric(normalized, errors="coerce")

    return df
