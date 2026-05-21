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

DB_COLUMNS = ["ASIN", "Product Name", "Product Cost", "Shipping Cost", "Customs Cost", "New Product"]


def load_products_db() -> pd.DataFrame:
    from db.database import get_conn
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT asin AS ASIN, product_name AS 'Product Name', "
        "product_cost AS 'Product Cost', shipping_cost AS 'Shipping Cost', "
        "customs_cost AS 'Customs Cost', "
        "COALESCE(is_new_product, 0) AS 'New Product' "
        "FROM product_costs ORDER BY asin",
        conn
    )
    conn.close()
    df["New Product"] = df["New Product"].astype(bool)
    return df


def save_products_db(df: pd.DataFrame):
    from db.database import get_conn
    conn = get_conn()
    with conn:
        for _, row in df.iterrows():
            asin = str(row.get("ASIN", "")).strip().upper()
            if not asin:
                continue
            is_new = 1 if row.get("New Product") else 0
            existing = conn.execute(
                "SELECT id FROM products_catalog WHERE asin=?", (asin,)
            ).fetchone()
            if existing:
                conn.execute("""
                    UPDATE products_catalog
                    SET name=COALESCE(?, name),
                        fba_fee=?,
                        is_new_product=?,
                        updated_at=datetime('now')
                    WHERE asin=?
                """, (
                    str(row.get("Product Name") or "").strip() or None,
                    float(row.get("FBA Fee") or 0),
                    is_new,
                    asin,
                ))
            else:
                conn.execute("""
                    INSERT INTO products_catalog
                        (asin, name, fba_fee, is_new_product, updated_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                """, (
                    asin,
                    str(row.get("Product Name") or asin),
                    float(row.get("FBA Fee") or 0),
                    is_new,
                ))
    conn.close()


def delete_product_db(asin: str):
    from db.database import get_conn
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM products_catalog WHERE asin = ?", (asin.strip().upper(),))
    conn.close()


def get_cost_map_db() -> dict:
    """Returns {ASIN: {product_cost, shipping_cost, customs_cost, fba_fee, landed_cost, is_new_product}}
    fba_fee is kept as a DB fallback even though it's no longer shown in the UI.
    """
    from db.database import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT asin, product_name, product_cost, shipping_cost, customs_cost, "
        "COALESCE(fba_fee, 0) AS fba_fee, COALESCE(is_new_product, 0) AS is_new_product "
        "FROM product_costs ORDER BY asin"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        asin = str(r["asin"] or "").strip()
        if not asin:
            continue
        pc  = float(r["product_cost"]  or 0)
        sc  = float(r["shipping_cost"] or 0)
        cc  = float(r["customs_cost"]  or 0)
        fba = float(r["fba_fee"]       or 0)
        result[asin] = {
            "product_cost":   pc,
            "shipping_cost":  sc,
            "customs_cost":   cc,
            "fba_fee":        fba,
            "landed_cost":    pc + sc + cc,
            "is_new_product": bool(r["is_new_product"]),
        }
    return result


def products_exist_db() -> bool:
    from db.database import get_conn
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM products_catalog WHERE asin IS NOT NULL AND asin != ''"
    ).fetchone()[0]
    conn.close()
    return n > 0


def migrate_csv_to_db():
    """One-time migration: import existing products.csv into DB."""
    if not products_exist():
        return
    df = load_products()
    save_products_db(df)
