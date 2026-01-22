
def review_csv(df):

    df = df.copy()

    if df.empty:
        logger.info("DataFrame vuoto")
        return df

    logger.info(f"Revisione CSV - colonne originali: {df.columns.tolist()}")

    # ---------- NORMALIZZAZIONE NOMI COLONNE ----------
    df = df.copy()
    df.columns = [c.lower().strip() for c in df.columns]

    # ---------- FASE 1: MAPPING COLONNE (LLM + fallback) ----------
    df = _map_columns_with_llm(df)

    df.to_excel("./backup/review_csv_0_debug_mapped_columns.xlsx", index=False)  # Salvataggio per debug

    # ---------- FASE 5: SCHEMA STANDARD ----------
    standard_cols = [
        "Data_Operazione",
        "Data_Valuta",
        "Descrizione",
        "Valuta",
        "Entrata",
        "Uscita"
    ]

    for col in standard_cols:
        if col not in df.columns:
            df[col] = ""

    df = df[standard_cols].reset_index(drop=True)

    logger.info(f"DataFrame rivisto - colonne finali: {df.columns.tolist()}, righe iniziali: {len(df)}")
    df.to_excel("./backup/review_csv_3_debug_numeric_conversion.xlsx", index=False)  # Salvataggio per debug

    # ---------- PULIZIA RIGHE VUOTE ----------
    # df = df[
    #     ~(
    #         (df["Entrata"] == 0) &
    #         (df["Uscita"] == 0) &
    #         (df["Descrizione"].astype(str).str.strip() == "")
    #     )
    # ]
    # logger.info(f"DataFrame rivisto - colonne finali: {df.columns.tolist()}, righe finali: {len(df)}")
    # df.to_excel("./backup/review_csv_4_debug_after_row_cleanup.xlsx", index=False)  # Salvataggio per debug

    return df.reset_index(drop=True)
    

