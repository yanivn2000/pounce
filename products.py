"""
products.py
Manages product cost data — load, save, calculate break-even ROAS.
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
    "Price",
    "Product Cost",
    "Shipping Cost",
    "Customs Cost",
    "FBA Fee",
]


def ensure_data_dir():
    os.makedirs(os.path.dirname(PRODUCTS_PATH), exist_ok=True)


def load_products() -> pd.DataFrame:
    """Load products CSV. Returns empty DataFrame if file doesn't exist."""
    ensure_data_dir()
    if not os.path.exists(PRODUCTS_PATH):
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(PRODUCTS_PATH, dtype={"ASIN": str})
        # Ensure all columns exist
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[COLUMNS].copy()
    except Exception:
        return pd.DataFrame(columns=COLUMNS)


def save_products(df: pd.DataFrame):
    """Save products DataFrame to CSV."""
    ensure_data_dir()
    df[COLUMNS].to_csv(PRODUCTS_PATH, index=False)


def calc_landed_cost(row) -> float:
    return (
        float(row.get("Product Cost") or 0) +
        float(row.get("Shipping Cost") or 0) +
        float(row.get("Customs Cost") or 0)
    )


def calc_breakeven_roas(row) -> float | None:
    """
    Break-even ROAS = Price / (Price - Landed Cost - FBA Fee - Price * 15%)
    Returns None if data is missing or margin is <= 0.
    """
    try:
        price        = float(row.get("Price") or 0)
        landed_cost  = calc_landed_cost(row)
        fba_fee      = float(row.get("FBA Fee") or 0)
        amazon_fee   = price * AMAZON_FEE_PCT

        if price <= 0:
            return None

        margin = price - landed_cost - fba_fee - amazon_fee
        if margin <= 0:
            return None

        return round(price / margin, 2)
    except Exception:
        return None


def get_breakeven_map() -> dict[str, float]:
    """
    Returns {ASIN: break_even_roas} for all products with valid data.
    Used by analyzer to replace the global target ROAS with product-specific values.
    """
    df = load_products()
    result = {}
    for _, row in df.iterrows():
        asin = str(row.get("ASIN", "")).strip()
        be = calc_breakeven_roas(row)
        if asin and be:
            result[asin] = be
    return result


def import_csv(uploaded_file) -> tuple[pd.DataFrame, str]:
    """
    Import a user-uploaded CSV. Validates columns and returns (df, error_message).
    error_message is empty string if OK.
    """
    try:
        df = pd.read_csv(uploaded_file, dtype={"ASIN": str})
        df.columns = df.columns.str.strip()
        missing = [c for c in COLUMNS if c not in df.columns]
        if missing:
            return pd.DataFrame(), f"Missing columns: {', '.join(missing)}"
        return df[COLUMNS].copy(), ""
    except Exception as e:
        return pd.DataFrame(), str(e)


def products_exist() -> bool:
    return os.path.exists(PRODUCTS_PATH) and os.path.getsize(PRODUCTS_PATH) > 0
