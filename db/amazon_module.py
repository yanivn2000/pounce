"""
amazon_module.py
────────────────
Self-contained Amazon Transaction module.
Drop this file into any Python project (Flask, Streamlit, CLI).

Exports:
  parse_amazon_transactions(filepath) -> list[dict]
  insert_transactions(conn, transactions) -> dict
  init_amazon_tables(conn)
  get_amazon_report(conn, year, marketplaces=None) -> dict
  render_amazon_upload_ui(conn)   [Streamlit only]
"""

import hashlib
import os
import tempfile

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

# ── Marketplace domain → (code, currency) ─────────────────────────────────────
_DOMAIN_MP_MAP = [
    ("amazon.com.br",  ("BR", "BRL")),
    ("amazon.com.mx",  ("MX", "MXN")),
    ("amazon.com.au",  ("AU", "AUD")),
    ("amazon.com.be",  ("BE", "EUR")),
    ("amazon.co.uk",   ("UK", "GBP")),
    ("amazon.co.jp",   ("JP", "JPY")),
    ("amazon.com",     ("US", "USD")),
    ("amazon.ca",      ("CA", "CAD")),
    ("amazon.de",      ("DE", "EUR")),
    ("amazon.fr",      ("FR", "EUR")),
    ("amazon.es",      ("ES", "EUR")),
    ("amazon.it",      ("IT", "EUR")),
    ("amazon.nl",      ("NL", "EUR")),
    ("amazon.pl",      ("PL", "PLN")),
    ("amazon.se",      ("SE", "SEK")),
    ("amazon.ie",      ("IE", "EUR")),
    ("amazon.in",      ("IN", "INR")),
    ("amazon.sg",      ("SG", "SGD")),
    ("amazon.ae",      ("AE", "AED")),
    ("amazon.sa",      ("SA", "SAR")),
    ("amazon.tr",      ("TR", "TRY")),
    ("amazon.cn",      ("CN", "CNY")),
]

MARKETPLACE_MAP = {
    "A2EUQ1WTGCTBG2": ("CA", "CAD"),
    "ATVPDKIKX0DER":  ("US", "USD"),
    "A1F83G8C2ARO7P": ("UK", "GBP"),
    "A1PA6795UKMFR9": ("DE", "EUR"),
    "A13V1IB3VIYZZH": ("FR", "EUR"),
    "A1RKKUPIHCS9HS": ("ES", "EUR"),
    "APJ6JRA9NG5V4":  ("IT", "EUR"),
    "A1805IZSGTT6HS": ("NL", "EUR"),
    "A2NODRKZP88ZB9": ("SE", "SEK"),
    "A1C3SOZRARQ6R3": ("PL", "PLN"),
    "A1F83G8C2ARO7P": ("UK", "GBP"),
    "AMEN7PMS3EDWL":  ("BE", "EUR"),
    "A2VIGQ35RCS4UG": ("AE", "AED"),
    "A17E79C6D8DWNP": ("SA", "SAR"),
    "A21TJRUUN4KGV":  ("IN", "INR"),
    "A39IBJ37TRP1C6": ("AU", "AUD"),
    "A1VC38T7YXB528": ("JP", "JPY"),
}

def _normalize_marketplace(raw):
    raw = (raw or "").strip()
    if raw in MARKETPLACE_MAP:
        return MARKETPLACE_MAP[raw]
    lower = raw.lower()
    for domain, result in _DOMAIN_MP_MAP:
        if domain in lower:
            return result
    return (raw.upper()[:8] or "US", "USD")


# ── Month name maps ────────────────────────────────────────────────────────────
MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

EU_MONTH_MAP = {
    # French
    "janv": "Jan", "janv.": "Jan", "févr": "Feb", "févr.": "Feb",
    "mars": "Mar", "avr":   "Apr", "avr.": "Apr", "mai":   "May",
    "juin": "Jun", "juil":  "Jul", "juil.":"Jul", "août":  "Aug",
    "sept": "Sep", "sept.": "Sep", "oct":  "Oct", "oct.":  "Oct",
    "nov":  "Nov", "nov.":  "Nov", "déc":  "Dec", "déc.":  "Dec",
    # Polish
    "sty": "Jan", "lut": "Feb", "mar": "Mar", "kwi": "Apr",
    "maj": "May", "cze": "Jun", "lip": "Jul", "sie": "Aug",
    "wrz": "Sep", "paź": "Oct", "lis": "Nov", "gru": "Dec",
    # German
    "jan": "Jan", "feb": "Feb", "mär": "Mar", "apr": "Apr",
    "jun": "Jun", "jul": "Jul", "aug": "Aug", "sep": "Sep",
    "okt": "Oct", "dez": "Dec",
    # Spanish
    "ene": "Jan", "abr": "Apr", "ago": "Aug", "dic": "Dec",
    # Italian
    "gen": "Jan", "mag": "May", "giu": "Jun", "lug": "Jul",
    "set": "Sep", "ott": "Oct",
    # Dutch
    "mrt": "Mar", "mei": "May",
    # Swedish
    "juni": "Jun", "juli": "Jul",
}

# ── TX type canonicalization ───────────────────────────────────────────────────
EU_TYPE_MAP = {
    # French
    "commande":                             "Order",
    "remboursement":                        "Refund",
    "transférer":                           "Transfer",
    "frais de service":                     "Service Fee",
    "dette":                                "Debt",
    "frais d'inventaire fba":               "FBA Inventory Fee",
    "frais d'inventaire de l'expédition par amazon": "FBA Inventory Fee",
    "frais fba":                            "FBA Inventory Fee",
    # Polish
    "zamówienie":                           "Order",
    "zwrot":                                "Refund",
    "przelew":                              "Transfer",
    "opłata za usługę":                     "Service Fee",
    "dług":                                 "Debt",
    "fba — opłata za zapasy":               "FBA Inventory Fee",
    "fba opłata za zapasy":                 "FBA Inventory Fee",
    # German
    "bestellung":                           "Order",
    "erstattung":                           "Refund",
    "überweisung":                          "Transfer",
    "servicegebühr":                        "Service Fee",
    "fba-lagergebühren":                    "FBA Inventory Fee",
    # Spanish
    "pedido":                               "Order",
    "reembolso":                            "Refund",
    "transferencia":                        "Transfer",
    "tarifa de servicio":                   "Service Fee",
    # Italian
    "ordine":                               "Order",
    "rimborso":                             "Refund",
    "trasferimento":                        "Transfer",
    "commissione di servizio":              "Service Fee",
    # Dutch
    "bestelling":                           "Order",
    "terugbetaling":                        "Refund",
    "overdracht":                           "Transfer",
    "servicekosten":                        "Service Fee",
    # Swedish
    "beställning":                          "Order",
    "återbetalning":                        "Refund",
    "överföring":                           "Transfer",
    "serviceavgift":                        "Service Fee",
}

_TYPE_CANON = {
    "order":                   "Order",
    "order payment":           "Order Payment",
    "order retrocharge":       "Order Retrocharge",
    "order_retrocharge":       "Order Retrocharge",
    "refund":                  "Refund",
    "adjustment":              "Adjustment",
    "transfer":                "Transfer",
    "debt":                    "Debt",
    "liquidations":            "Liquidations",
    "service fee":             "Service Fee",
    "service fees":            "Service Fee",
    "service charges":         "Service Fee",
    "amazon fees":             "Amazon Fees",
    "amazon charges":          "Amazon Fees",
    "fba inventory fee":       "FBA Inventory Fee",
    "fba transaction fees":    "FBA Transaction Fees",
    "fba transaction fee":     "FBA Transaction Fees",
    "inventory reimbursement": "Inventory Reimbursement",
    "a-to-z guarantee claim":  "A-to-z Guarantee Claim",
}

def _canon_type(t):
    key = (t or "").strip().lower()
    return _TYPE_CANON.get(key) or EU_TYPE_MAP.get(key) or (t or "").strip()


# ── EU column name → standard English name ────────────────────────────────────
EU_COL_NAME_MAP = {
    # date/time
    "date/heure":            "date/time",
    "datum/uhrzeit":         "date/time",
    "fecha/hora":            "date/time",
    "fecha_y_hora":          "date/time",
    "data/ora":              "date/time",
    "data/ora:":             "date/time",
    "datum/tijd":            "date/time",
    "data/godzina":          "date/time",
    "datum/tid":             "date/time",
    # type
    "typ":                   "type",
    "tipo":                  "type",
    # order id
    "numéro_de_la_commande": "order_id",
    "bestellnummer":         "order_id",
    "número_del_pedido":     "order_id",
    "numero_d'ordine":       "order_id",
    "bestelnummer":          "order_id",
    "identyfikator_zamówienia": "order_id",
    "ordernummer":           "order_id",
    # description
    "beschreibung":          "description",
    "descripción":           "description",
    "descrizione":           "description",
    "omschrijving":          "description",
    "opis":                  "description",
    "produktbeskrivning":    "description",
    # marketplace
    "site_de_vente":         "marketplace",
    "marktplatz":            "marketplace",
    "sitio_web":             "marketplace",
    "web_de_amazon":         "marketplace",
    "sito_web":              "marketplace",
    "rynek":                 "marketplace",
    "marknadsplats":         "marketplace",
    # product sales
    "ventes_de_produits":    "product_sales",
    "umsätze":               "product_sales",
    "produktumsätze":        "product_sales",
    "ventas_de_productos":   "product_sales",
    "vendite":               "product_sales",
    "verkoop_van_producten": "product_sales",
    "sprzedaż_produktów":    "product_sales",
    "försäljning_av_produkter": "product_sales",
    "product_sales":         "product_sales",
    # promotional rebates
    "total_des_réductions":  "promotional_rebates",
    "sonderangebotsrabatte": "promotional_rebates",
    "descuentos_promocionales": "promotional_rebates",
    "devoluciones_promocionales": "promotional_rebates",
    "sconti_promozionali":   "promotional_rebates",
    "promotiekortingen":     "promotional_rebates",
    "rabaty_promocyjne":     "promotional_rebates",
    "kampanjrabatter":       "promotional_rebates",
    # product_sales_tax (VAT collected) — ⚠️ CRITICAL for DE/FR gross_sales
    "taxe_de_ventes_prélevée":          "product_sales_tax",  # FR 25-col
    "taxes_sur_la_vente_des_produits":  "product_sales_tax",  # FR 29-col ← NEW
    "erhobene_umsatzsteuer":            "product_sales_tax",  # DE 25-col
    "produktumsatzsteuer":              "product_sales_tax",  # DE 29-col ← NEW
    "impuesto_de_ventas_cobrado":       "product_sales_tax",
    "impuesto_de_ventas_de_productos":  "product_sales_tax",  # ES 29-col
    "iva_riscosso":                     "product_sales_tax",
    "imposta_sulle_vendite_dei_prodotti": "product_sales_tax",  # IT 29-col
    "geïnd_btw":                        "product_sales_tax",
    "pobrany_podatek_od_sprzedaży":     "product_sales_tax",
    "insamlad_moms":                    "product_sales_tax",
    "product_sales_tax":                "product_sales_tax",
    # marketplace_withheld_tax
    "taxe_marketplace_facilitator":     "marketplace_withheld_tax",  # FR 25-col
    "taxes_retenues_sur_le_site_de_vente": "marketplace_withheld_tax",  # FR 29-col ← NEW
    "marktplatz-quellensteuer":         "marketplace_withheld_tax",  # DE 25-col
    "einbehaltene_steuer_auf_marketplace": "marketplace_withheld_tax",  # DE 29-col ← NEW
    "impuesto_retenido_en_el_sitio_web":"marketplace_withheld_tax",
    "impuesto_retenido_de_marketplace": "marketplace_withheld_tax",
    "ritenuta_marketplace":             "marketplace_withheld_tax",
    "trattenuta_iva_del_marketplace":   "marketplace_withheld_tax",  # IT 29-col
    "marketplace-bronbelasting":        "marketplace_withheld_tax",
    "podatek_od_transakcji_marketplace_facilitator": "marketplace_withheld_tax",
    "marknadsplatsskatt":               "marketplace_withheld_tax",
    "marketplace_withheld_tax":         "marketplace_withheld_tax",
    # selling fees
    "frais_de_vente":            "selling_fees",
    "verkaufsgebühren":          "selling_fees",
    "comisiones_de_venta":       "selling_fees",
    "tarifas_de_venta":          "selling_fees",
    "commissioni_di_vendita":    "selling_fees",
    "verkoopkosten":             "selling_fees",
    "opłaty_za_sprzedaż":        "selling_fees",
    "försäljningsavgifter":      "selling_fees",
    # fba fees
    "frais_pour_le_service_expédié_par_amazon": "fba_fees",
    "fba-gebühren":              "fba_fees",
    "tarifas_de_fba":            "fba_fees",
    "tarifas_de_logística_de_amazon": "fba_fees",
    "costi_fba":                 "fba_fees",
    "costi_del_servizio_logistica_di_amazon": "fba_fees",
    "fba-kosten":                "fba_fees",
    "opłaty_za_fba":             "fba_fees",
    "fba-avgifter":              "fba_fees",
    # other
    "autres":   "other",  "sonstige": "other",  "otros": "other",
    "otro":     "other",  "altro":    "other",   "overige":"other",
    "inne":     "other",  "övrigt":   "other",
    # total/net
    "total":   "total",  "gesamt": "total",  "suma":   "total",
    "totale":  "total",  "totaal": "total",  "totalt": "total",
    # status
    "statut_de_la_transaction":     "transaction_status",
    "transaktionsstatus":           "transaction_status",
    "estado_de_la_transacción":     "transaction_status",
    "stato_della_transazione":      "transaction_status",
    "stato_transazione":            "transaction_status",
    "transactiestatus":             "transaction_status",
    "status_transakcji":            "transaction_status",
}


# ── Core parser ────────────────────────────────────────────────────────────────

def parse_amazon_transactions(filepath: str) -> list:
    """
    Parse Amazon Transaction View CSV/TSV.
    Handles US (25-col English) and EU (25/29-col multilingual: DE/FR/ES/IT/NL/PL/SE/BE/IE).
    Returns list of dicts ready to INSERT into amazon_transactions table.
    """
    import pandas as pd
    import re as _re

    with open(filepath, encoding="utf-8-sig") as f:
        lines = f.readlines()

    # Detect EU locale from preamble
    is_eu_file = False
    eu_currency = "EUR"
    for line in lines[:5]:
        low = line.lower()
        if any(p in low for p in [
            "tous les montants sont en", "alle beträge in", "alle bedragen in",
            "todos los importes", "tutti gli importi", "wszystkie kwoty w",
            "alla belopp i", "všechny částky"
        ]):
            is_eu_file = True
            eu_currency = "PLN" if "pln" in low else "SEK" if "sek" in low else "EUR"
            break

    # Find the real header row
    header_row = None
    for i, line in enumerate(lines):
        low = line.lower()
        tab_fields   = line.split("\t")
        comma_fields = line.split(",")
        n_fields = max(len(tab_fields), len(comma_fields))
        if n_fields < 5:
            continue
        if '"date/time"' in low or low.strip().startswith('"date/time') \
                or low.strip().startswith('date/time'):
            header_row = i
            break
        if is_eu_file and n_fields >= 20:
            header_row = i
            break
    if header_row is None:
        for i, line in enumerate(lines):
            if len(line.split(",")) >= 10 or len(line.split("\t")) >= 10:
                header_row = i
                break
    if header_row is None:
        return []

    header_line = lines[header_row] if lines else ""
    delimiter = "\t" if "\t" in header_line else ","

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp:
            tmp.writelines(lines[header_row:])
        del lines
        df = pd.read_csv(tmp_path, delimiter=delimiter, dtype=str, keep_default_na=False)
    finally:
        os.unlink(tmp_path)

    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_").replace("(", "").replace(")", "")
                  for c in df.columns]

    # EU: rename local-language columns to English
    if is_eu_file:
        rename_map = {col: EU_COL_NAME_MAP[col] for col in df.columns if col in EU_COL_NAME_MAP}
        if rename_map:
            df = df.rename(columns=rename_map)

    def find_col(df, *candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    col_date     = find_col(df, "date/time", "date")
    col_type     = find_col(df, "type", "transaction_type")
    col_status   = find_col(df, "transaction_status", "status")
    col_order    = find_col(df, "order_id", "order id", "order-id")
    col_product  = find_col(df, "description", "product_details", "product details")
    col_gross    = find_col(df, "product_sales", "total_product_charges")
    col_sales_tax    = find_col(df, "product_sales_tax", "product sales tax")
    col_withheld_tax = find_col(df, "marketplace_withheld_tax", "marketplace withheld tax")
    col_promos   = find_col(df, "promotional_rebates", "promotional rebates", "total_promotional_rebates")
    col_selling_fees = find_col(df, "selling_fees", "selling fees")
    col_fba_fees     = find_col(df, "fba_fees", "fba fees")
    col_fees         = find_col(df, "amazon_fees")
    col_other    = find_col(df, "other")
    col_net      = find_col(df, "total", "total_usd")
    col_marketplace = find_col(df, "marketplace")

    if not col_date or not col_net:
        return []

    def to_num(series):
        if is_eu_file:
            return pd.to_numeric(
                series.str.replace(".", "", regex=False)
                      .str.replace(",", ".", regex=False).str.strip(),
                errors="coerce"
            ).fillna(0.0)
        return pd.to_numeric(
            series.str.replace(",", "", regex=False)
                  .str.replace("$", "", regex=False).str.strip(),
            errors="coerce"
        ).fillna(0.0)

    # Parse dates
    date_col_raw = df[col_date].str.strip()
    if is_eu_file:
        def _eu_date_normalize(s):
            s = _re.sub(r'\s+(UTC|CET|CEST|GMT)$', '', s.strip())
            parts = s.split()
            if len(parts) >= 3:
                m_raw = parts[1].lower().rstrip('.')
                eng = EU_MONTH_MAP.get(m_raw) or EU_MONTH_MAP.get(parts[1].lower())
                if eng:
                    parts[1] = eng
            return ' '.join(parts)
        date_series = date_col_raw.apply(_eu_date_normalize)
    else:
        date_series = (date_col_raw
                       .str.replace(r'p\.m\.', 'PM', regex=True)
                       .str.replace(r'a\.m\.', 'AM', regex=True)
                       .str.replace(r'\bSept\b', 'Sep', regex=True)
                       .str.replace(r'\s+(PST|PDT|EST|EDT|UTC|BST|GMT|CET|CEST)$', '', regex=True)
                       .str.strip())
    import pandas as pd
    dates = pd.to_datetime(date_series, errors="coerce", dayfirst=True, format="mixed")
    mask  = dates.notna()
    df    = df[mask].copy()
    dates = dates[mask]

    df["_date_str"] = df[col_date].str.strip()
    df["_tx_date"]  = dates.dt.strftime("%Y-%m-%d")
    df["_month"]    = dates.dt.month.apply(lambda m: MONTH_NAMES[m - 1])
    df["_year"]     = dates.dt.year.astype(int)
    df["_net"]      = to_num(df[col_net])
    df["_gross"]    = to_num(df[col_gross])      if col_gross      else 0.0
    df["_sales_tax"]     = to_num(df[col_sales_tax])     if col_sales_tax     else 0.0
    df["_withheld_tax"]  = to_num(df[col_withheld_tax])  if col_withheld_tax  else 0.0
    df["_promos"]   = to_num(df[col_promos])     if col_promos     else 0.0
    if col_selling_fees and col_fba_fees:
        df["_fees"] = to_num(df[col_selling_fees]) + to_num(df[col_fba_fees])
    elif col_selling_fees:
        df["_fees"] = to_num(df[col_selling_fees])
    elif col_fba_fees:
        df["_fees"] = to_num(df[col_fba_fees])
    elif col_fees:
        df["_fees"] = to_num(df[col_fees])
    else:
        df["_fees"] = 0.0
    df["_other"]    = to_num(df[col_other])      if col_other      else 0.0

    df["_type"]    = df[col_type].apply(_canon_type) if col_type else ""
    df["_status"]  = df[col_status].str.strip()  if col_status  else ""
    df["_order"]   = df[col_order].str.strip().fillna("---") if col_order else "---"
    df["_product"] = df[col_product].str[:120]   if col_product else ""

    # Marketplace → code + currency
    if col_marketplace:
        mp_pairs = df[col_marketplace].apply(_normalize_marketplace)
        df["_marketplace"] = mp_pairs.apply(lambda x: x[0])
        df["_currency"]    = mp_pairs.apply(lambda x: x[1])
        if is_eu_file:
            unknown_mask = df["_marketplace"].isin(["", "US"]) & (df[col_marketplace].str.strip() == "")
            mp_mode = df.loc[df[col_marketplace].str.strip() != "", "_marketplace"].mode()
            if len(mp_mode):
                df.loc[unknown_mask, "_marketplace"] = mp_mode[0]
            df.loc[df["_currency"] == "USD", "_currency"] = eu_currency
    else:
        df["_marketplace"] = "US"
        df["_currency"]    = "USD"

    # ⚠️ EU VAT: add product_sales_tax to gross_sales (EU prices are net-of-VAT in CSV)
    EU_VAT_MARKETPLACES = {"UK","DE","FR","ES","IT","NL","BE","PL","SE","IE"}
    vat_mask = df["_marketplace"].isin(EU_VAT_MARKETPLACES)
    df.loc[vat_mask, "_gross"] = df.loc[vat_mask, "_gross"] + df.loc[vat_mask, "_sales_tax"]

    # Dedup hash
    df["_hash_str"] = (df["_date_str"] + "|" + df["_type"] + "|" + df["_order"]
                       + "|" + df["_net"].astype(str) + "|" + df["_marketplace"])
    df["_hash"] = df["_hash_str"].apply(lambda s: hashlib.md5(s.encode()).hexdigest())

    return df[[
        "_hash","_tx_date","_status","_type","_order","_product",
        "_gross","_promos","_fees","_other","_withheld_tax","_net","_month","_year",
        "_marketplace","_currency"
    ]].rename(columns={
        "_hash":         "tx_hash",     "_tx_date":     "tx_date",
        "_status":       "tx_status",   "_type":        "tx_type",
        "_order":        "order_id",    "_product":     "product_details",
        "_gross":        "gross_sales", "_promos":      "promo_rebates",
        "_fees":         "amazon_fees", "_other":       "other",
        "_withheld_tax": "withheld_tax","_net":         "net_total",
        "_month":        "month",       "_year":        "year",
        "_marketplace":  "marketplace", "_currency":    "currency",
    }).to_dict("records")


# ── DB init ────────────────────────────────────────────────────────────────────

DEFAULT_FX = {
    "USD": 1.0,   "CAD": 0.73,  "GBP": 1.27,  "EUR": 1.08,
    "AUD": 0.65,  "JPY": 0.0067,"MXN": 0.058, "BRL": 0.20,
    "SEK": 0.096, "PLN": 0.25,  "INR": 0.012, "CZK": 0.044,
    "DKK": 0.145,
}

def init_amazon_tables(conn):
    """Create amazon_transactions, monthly_fx_rates, and sellerboard_cogs tables if needed.
    Note: fx_rates is managed by pounce's database.py (schema: marketplace/rate/note)
    so we do NOT create or seed it here.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS amazon_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT NOT NULL UNIQUE,
            tx_date TEXT NOT NULL,
            tx_status TEXT,
            tx_type TEXT,
            order_id TEXT,
            product_details TEXT,
            gross_sales REAL DEFAULT 0,
            promo_rebates REAL DEFAULT 0,
            amazon_fees REAL DEFAULT 0,
            other REAL DEFAULT 0,
            withheld_tax REAL DEFAULT 0,
            net_total REAL DEFAULT 0,
            month TEXT,
            year INTEGER,
            marketplace TEXT DEFAULT 'US',
            currency TEXT DEFAULT 'USD'
        );
        CREATE TABLE IF NOT EXISTS monthly_fx_rates (
            currency TEXT NOT NULL,
            year INTEGER NOT NULL,
            month TEXT NOT NULL,
            rate REAL NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (currency, year, month)
        );
        CREATE TABLE IF NOT EXISTS sellerboard_cogs (
            asin        TEXT PRIMARY KEY,
            cost_usd    REAL NOT NULL,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    _seed_sellerboard_cogs(conn)


# ── SellerBoard COGS per ASIN (imported 2026-06-15) ───────────────────────────
# Source: SellerBoard Products export. cost_usd = full landed COGS per unit
# (manufacturer + inbound shipping + FBA fees as tracked by SellerBoard).
# When an ASIN had multiple SKU rows we take the maximum (most conservative COGS).
_SELLERBOARD_COGS_DATA: dict[str, float] = {
    "B0CS7VGBT3": 9.10,   # Crepe Delicious gift basket
    "B082921XST": 4.92,   # GIFFTED_075 triple gift set
    "B07DFGY39W": 4.51,   # GIFFTED_22 Mr&Mrs mugs w/ lids
    "B07BDQSG42": 4.38,   # GIFFTED_16 travel mug Not A Day Over
    "B07FQN3G8L": 4.38,   # GIFFTED_038 travel mug Not A Day Over (alt SKU)
    "B09P8TK727": 6.00,   # WPLAY_001 ABC preschool kit (FBM cost)
    "B07BDQSB96": 3.71,   # GIFFTED_04 Mr&Mrs coffee mugs 380ml
    "B0DP5G2XS4": 2.64,   # GIFFTED_2504 I Love You Berry Much mug set
    "B01JZFUD5C": 2.20,   # 8H-HQY1-D65N silicone car coasters
    "B0CSJT5N42": 1.89,   # GIFFTED_064 Let's Have Coffee mug set
    "B0DP5GHTZ3": 1.89,   # GIFFTED_2505 Promoted to Grandparents 13oz
    "B0DP5GFK31": 1.89,   # GIFFTED_2506 Let's Have Coffee 13oz
    "B0DP5G5YPD": 1.89,   # GIFFTED_2507 Mr&Mrs 13oz
    "B0GF3868KN": 1.89,   # GIFFTED_2607 Promoted to Grandparents 13oz v2
    "B0GDWZ3W37": 1.89,   # GIFFTED_2601 Housewarming mug set
    "B0GF3FPWLD": 1.89,   # GIFFTED_2602 Hubby&Wifey mug set
    "B0GF3GWDJW": 1.89,   # GIFFTED_2604 Grandparents Spanish 13oz
    "B0GF39XJ8J": 1.89,   # GIFFTED_2603 Padrino&Madrina mug set
    "B07BDQSB99": 1.89,   # GIFFTED_11 funny couples mugs 380ml
    "B0GF3BVRNV": 1.89,   # GIFFTED_2608 Promoted to Mommy&Daddy 13oz
    "B0CXPXQ8YP": 1.89,   # GIFFTED_068 funny mugs + coupons
    "B0DP5GD1ZY": 1.89,   # GIFFTED_2508 Good Morning mug set 13oz
    "B0GF3BX3WT": 1.68,   # GIFFTED_2609 Let's Have Coffee 13oz v2
    "B0CSJQJDMX": 1.68,   # GIFFTED_063 Mr Right Mrs Always Right
    "B07FQJV9RS": 1.65,   # GIFFTED_030 I'm a Grandma Superpower mug
    "B07BDQD5HR": 1.64,   # GIFFTED_15 Boss Lady mug
    "B07WRDV3QL": 1.63,   # GIFFTED_051 Worlds Best Husband mug v4
    "B07WQYMHY7": 1.62,   # GIFFTED_052 Bonus Sister mug
    "B07BDNQ8QQ": 1.45,   # GIFFTED_17 Mr&Mrs 380ml
    "B0CQYY64QV": 1.45,   # GIFFTED_060 Spanish grandparents mugs
    "B07FQR7TF5": 1.45,   # GIFFTED_032 Mr Right Mrs Always Right 380ml
    "B073VN9ZZ7": 1.45,   # JZ-EOS2-0003 Worlds Best Grandparents 380ml
    "B07FQQ3NPQ": 1.45,   # GIFFTED_031 Worlds Best Grandparents 380ml
    "B078R7FNFT": 1.45,   # JZ-EOS2-0017 Mr Right Mrs Always Right 380ml
    "B07FQQT4ZJ": 1.45,   # GIFFTED_043 Best Cat Mom mug
    "B0CXPYFSGQ": 1.45,   # GIFFTED_069 Pop Art Fart gag mugs
    "B073VNXW3B": 1.44,   # JZ-EOS1-0009 Best Big Sister mug
    "B07Q4R6NK4": 1.44,   # GIFFTED_047 You're Going to Be Babysitter
    "B07BDNYJPX": 1.35,   # GIFFTED_09 Worlds Best Husband 13oz
    "B07BDPZW1W": 1.34,   # GIFFTED_13 Best Sister mug + socks
    "B0CSJRPSZM": 1.34,   # GIFFTED_062/065 Worlds Best Wife mug + socks
    "B073VM86Z9": 1.34,   # JZ-EOS1-0001 Best Grandma mug + socks
    "B07FQH7YW9": 1.34,   # GIFFTED_039 Best Sister Pink mug + socks
    "B07FQPH913": 1.31,   # GIFFTED_040 Best Uncle mug + socks
    "B07BDPLVKF": 1.18,   # GIFFTED_10 Worlds Best Wife 13oz
    "B073VNSQRT": 1.18,   # JZ-EOS1-0013 Worlds Best Mom mug + socks
    "B0DP5HWDGM": 1.10,   # GIFFTED_2502 Abuelito Spanish mug 13oz
    "B0DP5LF3B9": 1.10,   # GIFFTED_2501 Abuelita Spanish mug 13oz
    "B0DP5HQLS4": 0.98,   # GIFFTED_2503 La Mejor Abuela 13oz
    "B07BDPTBJW": 0.98,   # GIFFTED_12 Worlds Best Boss mug
    "B073VMV7ZQ": 0.95,   # JZ-EOS1-0015 Simply The Best Wife mug
    "B0GDJLVTHZ": 0.94,   # GIFFTED_2606 Promoted to Grandpa mug
    "B0GDKB651N": 0.94,   # GIFFTED_2605 Promoted to Grandma mug
    "B073VNC7BQ": 0.94,   # JZ-EOS1-0008 Best Crazy Sister mug
    "B089GQ41QZ": 0.78,   # GIFFTED_054 El Mejor Abuelo Spanish mug
    "B089GQ4MXZ": 0.78,   # GIFFTED_053 La Mejor Abuela Spanish mug
    "B073VMY39W": 0.76,   # JZ-EOS1-0002 Worlds Best Grandpa mug
}


def _seed_sellerboard_cogs(conn) -> None:
    """Insert SellerBoard COGS rows — skips ASINs already present."""
    existing = {r[0] for r in conn.execute("SELECT asin FROM sellerboard_cogs").fetchall()}
    rows = [(asin, cost) for asin, cost in _SELLERBOARD_COGS_DATA.items()
            if asin not in existing]
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO sellerboard_cogs (asin, cost_usd) VALUES (?, ?)",
            rows,
        )
        conn.commit()


def get_sellerboard_cost_map(conn) -> dict[str, float]:
    """Return {ASIN_UPPER: cost_usd} from sellerboard_cogs table."""
    return {r[0].upper(): r[1]
            for r in conn.execute("SELECT asin, cost_usd FROM sellerboard_cogs").fetchall()}


def insert_transactions(conn, transactions: list) -> dict:
    """
    Insert parsed transactions, skip duplicates.
    Returns {"inserted": int, "skipped": int, "total": int}
    """
    existing = {r[0] for r in conn.execute("SELECT tx_hash FROM amazon_transactions").fetchall()}
    new_rows = [tx for tx in transactions if tx["tx_hash"] not in existing]
    skipped  = len(transactions) - len(new_rows)
    if new_rows:
        conn.executemany("""
            INSERT OR IGNORE INTO amazon_transactions
            (tx_hash, tx_date, tx_status, tx_type, order_id, product_details,
             gross_sales, promo_rebates, amazon_fees, other, withheld_tax, net_total,
             month, year, marketplace, currency)
            VALUES (:tx_hash,:tx_date,:tx_status,:tx_type,:order_id,:product_details,
                    :gross_sales,:promo_rebates,:amazon_fees,:other,:withheld_tax,:net_total,
                    :month,:year,:marketplace,:currency)
        """, new_rows)
        conn.commit()
    return {"inserted": len(new_rows), "skipped": skipped, "total": len(transactions)}


# ── Report aggregation ────────────────────────────────────────────────────────

def get_amazon_report(conn, year: int, marketplaces: list = None) -> dict:
    """
    Build monthly Amazon P&L report.
    Returns dict with keys:
      years, all_marketplaces, active_months,
      by_type (list of {tx_type, month, currency, net, gross, fees, promos, cnt}),
      fees_by_month, withheld_by_month, estimated_payment_by_month (all in USD)
    """
    fx = {r["currency"]: r["rate_to_usd"]
          for r in conn.execute("SELECT currency, rate_to_usd FROM fx_rates").fetchall()}
    mfx = {(r["currency"], r["month"]): r["rate"]
           for r in conn.execute(
               "SELECT currency, month, rate FROM monthly_fx_rates WHERE year=?", (year,)
           ).fetchall()}

    def get_rate(currency, month):
        return mfx.get((currency, month), fx.get(currency, 1.0))

    years   = [r[0] for r in conn.execute(
        "SELECT DISTINCT year FROM amazon_transactions ORDER BY year DESC").fetchall()]
    all_mps = [r[0] for r in conn.execute(
        "SELECT DISTINCT marketplace FROM amazon_transactions WHERE marketplace IS NOT NULL ORDER BY marketplace"
    ).fetchall()]

    mp_filter = ""
    mp_params = [year]
    if marketplaces:
        ph = ",".join("?" * len(marketplaces))
        mp_filter = f" AND marketplace IN ({ph})"
        mp_params = [year, *marketplaces]

    type_rows = conn.execute(
        f"SELECT tx_type, month, currency, "
        f"SUM(net_total) as net, SUM(gross_sales) as gross, "
        f"SUM(amazon_fees) as fees, SUM(promo_rebates) as promos, COUNT(*) as cnt "
        f"FROM amazon_transactions "
        f"WHERE year=? AND tx_type NOT IN ('Transfer','Debt'){mp_filter} "
        f"GROUP BY tx_type, month, currency", mp_params
    ).fetchall()

    # Build estimated payment per month (USD)
    est_pay = {m: 0.0 for m in MONTHS}
    orders_gross = {m: 0.0 for m in MONTHS}
    refunds = {m: 0.0 for m in MONTHS}
    fees_total = {m: 0.0 for m in MONTHS}

    for r in type_rows:
        m, cur = r["month"], r["currency"]
        if m not in MONTHS:
            continue
        rate = get_rate(cur, m)
        net_usd = (r["net"] or 0.0) * rate
        if r["tx_type"] == "Order":
            orders_gross[m] += (r["gross"] or 0.0) * rate
        if r["tx_type"] == "Refund":
            refunds[m] += net_usd
        est_pay[m] += net_usd
        fees_total[m] += (r["fees"] or 0.0) * rate

    active_months = sorted(
        {r["month"] for r in type_rows if r["month"] in MONTHS},
        key=lambda m: MONTHS.index(m)
    )

    return {
        "years":            years,
        "all_marketplaces": all_mps,
        "active_months":    active_months,
        "by_type":          [dict(r) for r in type_rows],
        "orders_gross_usd":       orders_gross,
        "refunds_usd":            refunds,
        "fees_usd":               fees_total,
        "estimated_payment_usd":  est_pay,
    }


# ── Streamlit UI helpers ───────────────────────────────────────────────────────

def render_amazon_upload_ui(conn):
    """
    Streamlit UI for uploading Amazon Transaction View files.
    Call this inside a Streamlit tab or page.
    """
    try:
        import streamlit as st
    except ImportError:
        raise ImportError("streamlit is required for render_amazon_upload_ui()")

    st.subheader("📤 העלאת Transaction View")
    st.caption(
        "Seller Central → Payments → Transaction View → Download → CSV/TSV. "
        "תומך: US, CA, UK, DE, FR, ES, IT, NL, PL, SE, BE, IE"
    )

    uploaded = st.file_uploader(
        "בחר קובץ CSV/TSV",
        type=["csv", "tsv"],
        key="amazon_tx_uploader"
    )

    if uploaded:
        if st.button("📥 ייבא עסקאות", key="btn_amazon_import"):
            suffix = ".tsv" if uploaded.name.endswith(".tsv") else ".csv"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded.read())
                tmp_path = tmp.name
            try:
                with st.spinner("מנתח קובץ..."):
                    transactions = parse_amazon_transactions(tmp_path)
            finally:
                os.unlink(tmp_path)

            if not transactions:
                st.error("❌ לא נמצאו עסקאות — בדוק שהדוח הוא Transaction View")
                return

            result = insert_transactions(conn, transactions)
            st.success(
                f"✅ יובאו **{result['inserted']:,}** עסקאות חדשות "
                f"(דולגו {result['skipped']:,} כפולות מתוך {result['total']:,})"
            )

            # Expose affected years so the caller can run their own sync logic
            st.session_state["amazon_uploaded_years"] = {tx["year"] for tx in transactions}
