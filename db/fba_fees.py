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
_REFERRAL_COL         = "estimated-referral-fee-per-unit"
_ASIN_COL             = "asin"
_CURRENCY_COL         = "currency"
_STORE_COL            = "amazon-store"   # e.g. "US", "CA", "UK"
_SIZE_TIER_COL        = "product-size-tier"
_HAS_LOCAL_INV_COL    = "has-local-inventory"          # "Yes" / "No"
_REMOTE_FEE_COLS      = [
    "expected-remote-fulfillment-fee-per-unit",    # NARF (US→CA/MX)
    "expected-efn-fulfillment-fee-per-unit",       # EFN (EU cross-border)
]

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


def clear_all_fba_fees():
    """Delete all rows from fba_fees (used before a full re-import)."""
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM fba_fees")
    conn.close()


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

    referral_col_present   = _REFERRAL_COL in df.columns
    currency_col_present   = _CURRENCY_COL in df.columns
    has_local_inv_present  = _HAS_LOCAL_INV_COL in df.columns
    remote_fee_col         = next((c for c in _REMOTE_FEE_COLS if c in df.columns), None)

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

            size_tier = (
                str(row.get(_SIZE_TIER_COL, "")).strip()
                if _SIZE_TIER_COL in df.columns else ""
            )

            has_local = (
                str(row.get(_HAS_LOCAL_INV_COL, "")).strip()
                if has_local_inv_present else ""
            )

            remote_fee = None
            if remote_fee_col:
                try:
                    v = str(row.get(remote_fee_col, "")).replace(",", "").strip()
                    remote_fee = float(v) if v else None
                except ValueError:
                    remote_fee = None

            conn.execute("""
                INSERT INTO fba_fees (asin, marketplace, pick_pack_fee, referral_fee, currency,
                                      size_tier, has_local_inventory, remote_fulfillment_fee, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(asin, marketplace) DO UPDATE SET
                    pick_pack_fee        = excluded.pick_pack_fee,
                    referral_fee         = excluded.referral_fee,
                    currency             = excluded.currency,
                    size_tier            = excluded.size_tier,
                    has_local_inventory  = excluded.has_local_inventory,
                    remote_fulfillment_fee = excluded.remote_fulfillment_fee,
                    updated_at           = excluded.updated_at
            """, (asin, marketplace, pick_pack, referral, currency, size_tier,
                  has_local, remote_fee))
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
        "SELECT asin, marketplace, size_tier, pick_pack_fee, referral_fee, currency, updated_at "
        "FROM fba_fees ORDER BY marketplace, asin",
        conn
    )
    conn.close()
    return df


def get_pick_pack_anomalies() -> pd.DataFrame:
    """
    Flag ASINs overcharged on Pick & Pack vs other products with identical
    physical dimensions on the same marketplace.

    Grouping is by (marketplace, rounded W×L×H from products_catalog) —
    NOT by Amazon's size-tier string, which is too coarse (e.g. a single mug
    and a set-of-two both land in "UsLargeStandardSize" but have different
    dimensions and should not be compared).

    Only flags fee > expected (being charged less is fine).
    """
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT
            f.asin,
            f.marketplace,
            f.size_tier,
            f.pick_pack_fee,
            f.currency,
            f.has_local_inventory,
            ROUND(p.width_cm,  1) AS w,
            ROUND(p.length_cm, 1) AS l,
            ROUND(p.height_cm, 1) AS h
        FROM fba_fees f
        JOIN products_catalog p ON UPPER(f.asin) = UPPER(p.asin)
        WHERE p.width_cm  IS NOT NULL
          AND p.length_cm IS NOT NULL
          AND p.height_cm IS NOT NULL
    """, conn)
    conn.close()

    if df.empty:
        return pd.DataFrame()

    anomalies = []
    for (mp, w, l, h), grp in df.groupby(["marketplace", "w", "l", "h"]):
        fee_counts = grp["pick_pack_fee"].value_counts()
        if len(fee_counts) < 2:
            continue                          # all same fee — no anomaly
        expected_fee = fee_counts.index[0]    # most common fee = correct fee
        # Only flag ASINs charged MORE than expected — lower is fine
        outliers = grp[grp["pick_pack_fee"] > expected_fee].copy()
        if outliers.empty:
            continue
        outliers["expected_fee"] = expected_fee
        outliers["same_size_asins"] = len(grp)
        # Reason: cross-border if has_local_inventory explicitly "No", else measurement error
        def _reason(row):
            if str(row.get("has_local_inventory", "")).strip().lower() == "no":
                return "Cross-border (no local inventory)"
            return "Possible measurement error → open case"
        outliers["reason"] = outliers.apply(_reason, axis=1)
        anomalies.append(outliers)

    if not anomalies:
        return pd.DataFrame()
    return pd.concat(anomalies, ignore_index=True)


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
