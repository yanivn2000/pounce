"""
products.py
Manages product cost data — load, save, calculate break-even ROAS.
Price is calculated dynamically from the ads report (Sales / Orders).
Data is stored in data/products.csv on the server.
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
