"""
products.py
Manages product cost data — load, save, calculate break-even ROAS.
Price is calculated dynamically from the ads report (Sales / Orders).
Costs are stored per marketplace in SQLite (product_costs table).
Legacy CSV path kept for migration only.
"""

import pandas as pd
import numpy as np
import os

PRODUCTS_PATH = os.path.join(os.path.dirname(__file__), "data", "products.csv")
AMAZON_FEE_PCT = 0.15  # 15% Amazon referral fee

COLUMNS = [
    "ASIN",
    "Product Name",
    "Product Cost",
    "Shipping Cost",
    "Customs Cost",
    "FBA Fee",
]


def ensure_data_dir():
    os.makedirs(os.path.dirname(PRODUCTS_PATH), exist_ok=True)


def load_products() -> pd.DataFrame:
    ensure_data_dir()
    if not os.path.exists(PRODUCTS_PATH):
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(PRODUCTS_PATH, dtype={"ASIN": str})
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[COLUMNS].copy()
    except Exception:
        return pd.DataFrame(columns=COLUMNS)


def save_products(df: pd.DataFrame):
    ensure_data_dir()
    df[COLUMNS].to_csv(PRODUCTS_PATH, index=False)


def calc_landed_cost(row) -> float:
    return (
        float(row.get("Product Cost") or 0) +
        float(row.get("Shipping Cost") or 0) +
        float(row.get("Customs Cost") or 0)
    )


def calc_breakeven_roas(row, avg_price: float) -> float | None:
    """
    Break-even ROAS = Price / (Price - Landed Cost - FBA Fee - Price * 15%)
    Price is calculated dynamically from the ads report.
    Returns None if data is missing or margin is <= 0.
    """
    try:
        if avg_price <= 0:
            return None

        landed_cost = calc_landed_cost(row)
        fba_fee     = float(row.get("FBA Fee") or 0)
        amazon_fee  = avg_price * AMAZON_FEE_PCT
        margin      = avg_price - landed_cost - fba_fee - amazon_fee

        if margin <= 0:
            return None

        return round(avg_price / margin, 2)
    except Exception:
        return None


def get_cost_map() -> dict[str, dict]:
    """
    Returns {ASIN: {product_cost, shipping_cost, customs_cost, fba_fee}}
    Used by analyzer to look up costs per campaign.
    """
    df = load_products()
    result = {}
    for _, row in df.iterrows():
        asin = str(row.get("ASIN", "")).strip()
        if asin:
            result[asin] = {
                "product_cost":  float(row.get("Product Cost") or 0),
                "shipping_cost": float(row.get("Shipping Cost") or 0),
                "customs_cost":  float(row.get("Customs Cost") or 0),
                "fba_fee":       float(row.get("FBA Fee") or 0),
                "landed_cost":   calc_landed_cost(row),
            }
    return result


def import_csv(uploaded_file) -> tuple[pd.DataFrame, str]:
    try:
        df = pd.read_csv(uploaded_file, dtype={"ASIN": str})
        df.columns = df.columns.str.strip()
        # Accept old format with Price column — just drop it
        if "Price" in df.columns:
            df = df.drop(columns=["Price"])
        missing = [c for c in COLUMNS if c not in df.columns]
        if missing:
            return pd.DataFrame(), f"Missing columns: {', '.join(missing)}"
        return df[COLUMNS].copy(), ""
    except Exception as e:
        return pd.DataFrame(), str(e)


def products_exist() -> bool:
    return os.path.exists(PRODUCTS_PATH) and os.path.getsize(PRODUCTS_PATH) > 0


# ── DB-backed product costs (per marketplace) ─────────────────────────────────

DB_COLUMNS = ["ASIN", "Product Name", "Product Cost", "Shipping Cost", "Customs Cost", "FBA Fee"]


def load_products_db() -> pd.DataFrame:
    from db.database import get_conn
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT asin AS ASIN, product_name AS 'Product Name', "
        "product_cost AS 'Product Cost', shipping_cost AS 'Shipping Cost', "
        "customs_cost AS 'Customs Cost', fba_fee AS 'FBA Fee' "
        "FROM product_costs ORDER BY asin",
        conn
    )
    conn.close()
    return df


def save_products_db(df: pd.DataFrame):
    from db.database import get_conn
    conn = get_conn()
    with conn:
        for _, row in df.iterrows():
            asin = str(row.get("ASIN", "")).strip().upper()
            if not asin:
                continue
            conn.execute("""
                INSERT INTO product_costs
                    (asin, product_name, product_cost,
                     shipping_cost, customs_cost, fba_fee, updated_at)
                VALUES (?,?,?,?,?,?,datetime('now'))
                ON CONFLICT(asin) DO UPDATE SET
                    product_name  = excluded.product_name,
                    product_cost  = excluded.product_cost,
                    shipping_cost = excluded.shipping_cost,
                    customs_cost  = excluded.customs_cost,
                    fba_fee       = excluded.fba_fee,
                    updated_at    = datetime('now')
            """, (
                asin,
                str(row.get("Product Name") or ""),
                float(row.get("Product Cost") or 0),
                float(row.get("Shipping Cost") or 0),
                float(row.get("Customs Cost") or 0),
                float(row.get("FBA Fee") or 0),
            ))
    conn.close()


def delete_product_db(asin: str):
    from db.database import get_conn
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM product_costs WHERE asin = ?", (asin.strip().upper(),))
    conn.close()


def get_cost_map_db() -> dict:
    """Returns {ASIN: {product_cost, shipping_cost, customs_cost, fba_fee, landed_cost}}"""
    df = load_products_db()
    result = {}
    for _, row in df.iterrows():
        asin = str(row.get("ASIN", "")).strip()
        if asin:
            lc = calc_landed_cost(row)
            result[asin] = {
                "product_cost":  float(row.get("Product Cost") or 0),
                "shipping_cost": float(row.get("Shipping Cost") or 0),
                "customs_cost":  float(row.get("Customs Cost") or 0),
                "fba_fee":       float(row.get("FBA Fee") or 0),
                "landed_cost":   lc,
            }
    return result


def products_exist_db() -> bool:
    from db.database import get_conn
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM product_costs").fetchone()[0]
    conn.close()
    return n > 0


def migrate_csv_to_db():
    """One-time migration: import existing products.csv into DB."""
    if not products_exist():
        return
    df = load_products()
    save_products_db(df)
