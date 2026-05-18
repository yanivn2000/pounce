"""
db/fba_fees.py — CRUD helpers for fba_fees and fx_rates tables.
"""
from db.database import get_conn
import pandas as pd
import io

# ── Column name variants ───────────────────────────────────────────────
_PICK_PACK_COLS = [
    "expected-fulfillment-fee-per-unit",           # NA (US/CA)
    "expected-domestic-fulfilment-fee-per-unit",   # UK/EU
]
_REFERRAL_COL  = "estimated-referral-fee-per-unit"
_ASIN_COL      = "asin"
_CURRENCY_COL  = "currency"
_STORE_COL     = "amazon-store"   # e.g. "US", "CA", "UK"

# Map amazon-store codes → internal marketplace strings
_STORE_MAP = {
    "US": "amazon.com",
    "CA": "amazon.ca",
    "UK": "amazon.co.uk",
    "GB": "amazon.co.uk",
    "DE": "amazon.de",
    "FR": "amazon.fr",
    "ES": "amazon.es",
    "IT": "amazon.it",
    "AU": "amazon.com.au",
    "MX": "amazon.com.mx",
    "JP": "amazon.co.jp",
    "IN": "amazon.in",
}


def import_fee_preview_csv(file_obj) -> tuple[int, list[str]]:
    """
    Parse an Amazon Fee Preview CSV and upsert into fba_fees.
    Marketplace is read from the 'amazon-store' column in the file.
    Returns (rows_saved, warnings_list).
    """
    raw = file_obj.read()
    if isinstance(raw, bytes):
        for enc in ("utf-8-sig", "windows-1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
    else:
        text = raw

    df = pd.read_csv(io.StringIO(text), dtype=str)
    # Strip BOM from first column name (e.g. "?sku" → "sku") and normalise all
    df.columns = [c.lstrip("﻿?").strip().lower() for c in df.columns]

    warnings = []

    if _ASIN_COL not in df.columns:
        return 0, ["No 'asin' column found in file."]

    if _STORE_COL not in df.columns:
        return 0, [f"No '{_STORE_COL}' column found. Available: {list(df.columns)}"]

    # Find pick & pack column
    pick_col = next((c for c in _PICK_PACK_COLS if c in df.columns), None)
    if not pick_col:
        return 0, [f"Could not find pick & pack column. Available: {list(df.columns)}"]

    referral_col_present = _REFERRAL_COL in df.columns
    currency_col_present = _CURRENCY_COL in df.columns

    conn = get_conn()
    saved = 0
    skipped_stores = set()

    with conn:
        for _, row in df.iterrows():
            asin = str(row.get(_ASIN_COL, "")).strip().upper()
            if not asin or asin == "NAN":
                continue

            store_code  = str(row.get(_STORE_COL, "")).strip().upper()
            marketplace = _STORE_MAP.get(store_code)
            if not marketplace:
                skipped_stores.add(store_code)
                continue

            try:
                pick_pack = float(str(row.get(pick_col, "0")).replace(",", "") or 0)
            except ValueError:
                pick_pack = 0.0

            referral = 0.0
            if referral_col_present:
                try:
                    referral = float(str(row.get(_REFERRAL_COL, "0")).replace(",", "") or 0)
                except ValueError:
                    referral = 0.0

            currency = (
                str(row.get(_CURRENCY_COL, "")).strip().upper()
                if currency_col_present else ""
            )

            conn.execute("""
                INSERT INTO fba_fees (asin, marketplace, pick_pack_fee, referral_fee, currency, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(asin, marketplace) DO UPDATE SET
                    pick_pack_fee = excluded.pick_pack_fee,
                    referral_fee  = excluded.referral_fee,
                    currency      = excluded.currency,
                    updated_at    = excluded.updated_at
            """, (asin, marketplace, pick_pack, referral, currency))
            saved += 1

    conn.close()
    if skipped_stores:
        warnings.append(f"Skipped unknown store codes: {', '.join(sorted(skipped_stores))}")
    return saved, warnings


def get_fba_fees_map(marketplace: str) -> dict:
    """Returns {asin_upper: {pick_pack_fee, referral_fee, currency}}"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT asin, pick_pack_fee, referral_fee, currency FROM fba_fees WHERE marketplace = ?",
        (marketplace,)
    ).fetchall()
    conn.close()
    return {
        r["asin"].upper(): {
            "pick_pack_fee": r["pick_pack_fee"] or 0.0,
            "referral_fee":  r["referral_fee"]  or 0.0,
            "currency":      r["currency"] or "",
        }
        for r in rows
    }


def get_all_fba_fees_df() -> pd.DataFrame:
    """Return all fba_fees rows as a DataFrame for display."""
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT asin, marketplace, pick_pack_fee, referral_fee, currency, updated_at "
        "FROM fba_fees ORDER BY marketplace, asin",
        conn
    )
    conn.close()
    return df


def get_fx_rates() -> dict:
    """Returns {marketplace: rate} (local units per 1 USD)."""
    conn = get_conn()
    rows = conn.execute("SELECT marketplace, rate FROM fx_rates").fetchall()
    conn.close()
    return {r["marketplace"]: r["rate"] for r in rows}


def get_fx_rates_df() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT marketplace, rate, note, updated_at FROM fx_rates ORDER BY marketplace",
        conn
    )
    conn.close()
    return df


def save_fx_rate(marketplace: str, rate: float, note: str = ""):
    conn = get_conn()
    with conn:
        conn.execute("""
            INSERT INTO fx_rates (marketplace, rate, note, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(marketplace) DO UPDATE SET
                rate       = excluded.rate,
                note       = excluded.note,
                updated_at = excluded.updated_at
        """, (marketplace, rate, note))
    conn.close()
