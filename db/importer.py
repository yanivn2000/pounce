"""
db/importer.py — Import Amazon Orders CSV into SQLite.

Amazon order report columns (varies slightly by marketplace):
  order-id, purchase-date, asin, sku, product-name,
  sales-channel, quantity, item-price, item-tax, shipping-price, currency, order-status
"""

import pandas as pd
import sqlite3
from db.database import get_conn

# Map Amazon CSV column names → our schema
_COL_MAP = {
    "order-id":       "order_id",
    "amazon-order-id": "order_id",
    "purchase-date":  "order_date",
    "asin":           "asin",
    "sku":            "sku",
    "product-name":   "title",
    "sales-channel":  "marketplace",
    "quantity":       "quantity",
    "item-price":     "item_price",
    "item-tax":       "item_tax",
    "shipping-price": "shipping_price",
    "currency":       "currency",
    "order-status":   "order_status",
}

_MARKETPLACE_MAP = {
    "amazon.com":    "amazon.com",
    "amazon.co.uk":  "amazon.co.uk",
    "amazon.ca":     "amazon.ca",
    "amazon.com.au": "amazon.com.au",
    "amazon.de":     "amazon.de",
}


def _normalize_marketplace(raw: str) -> str:
    if not raw:
        return "amazon.com"
    lower = raw.strip().lower()
    for k, v in _MARKETPLACE_MAP.items():
        if k in lower:
            return v
    return raw.strip()


def import_orders_csv(file_obj, marketplace_override: str = None) -> tuple[int, list[str]]:
    """
    Read an Amazon orders CSV and upsert rows into the orders table.
    Returns (rows_imported, warnings).
    """
    try:
        # Auto-detect separator: Amazon fulfillment reports are tab-separated,
        # manually exported Google Sheets files are comma-separated.
        sample = file_obj.read(4096)
        if isinstance(sample, bytes):
            sample = sample.decode("utf-8", errors="replace")
        file_obj.seek(0)
        first_line = sample.split("\n")[0]
        sep = "\t" if first_line.count("\t") >= first_line.count(",") else ","
        df = pd.read_csv(file_obj, dtype=str, sep=sep)
    except Exception as e:
        return 0, [f"Failed to read CSV: {e}"]

    df.columns = [c.strip().lower() for c in df.columns]

    # Rename columns to our schema names
    df = df.rename(columns={k: v for k, v in _COL_MAP.items() if k in df.columns})

    required = {"order_id", "order_date", "asin"}
    missing = required - set(df.columns)
    if missing:
        return 0, [f"Missing required columns: {missing}. Found: {list(df.columns)}"]

    warnings = []

    _CURRENCY_MAP = {"USD": "amazon.com", "CAD": "amazon.ca",
                     "GBP": "amazon.co.uk", "EUR": "amazon.de", "AUD": "amazon.com.au"}

    # Priority: sales-channel column → currency column → override → default
    if "marketplace" in df.columns:
        df["marketplace"] = df["marketplace"].apply(_normalize_marketplace)
    elif "currency" in df.columns:
        df["marketplace"] = df["currency"].str.strip().str.upper().map(
            lambda c: _CURRENCY_MAP.get(c, marketplace_override or "amazon.com")
        )
    elif marketplace_override:
        df["marketplace"] = _normalize_marketplace(marketplace_override)
    else:
        df["marketplace"] = "amazon.com"

    # Coerce numeric columns
    for col in ("quantity", "item_price", "item_tax", "shipping_price"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        else:
            df[col] = 0

    # Normalize date to YYYY-MM-DD
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    invalid_dates = df["order_date"].isna().sum()
    if invalid_dates:
        warnings.append(f"{invalid_dates} rows had unparseable dates and were skipped.")
    df = df.dropna(subset=["order_date"])

    for col in ("sku", "title", "currency", "order_status"):
        if col not in df.columns:
            df[col] = None

    conn = get_conn()
    imported = 0
    with conn:
        for _, row in df.iterrows():
            try:
                conn.execute("""
                    INSERT INTO orders
                        (order_id, order_date, asin, sku, title, marketplace,
                         quantity, item_price, item_tax, shipping_price, currency, order_status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(order_id, asin) DO UPDATE SET
                        order_date      = excluded.order_date,
                        quantity        = excluded.quantity,
                        item_price      = excluded.item_price,
                        item_tax        = excluded.item_tax,
                        shipping_price  = excluded.shipping_price,
                        order_status    = excluded.order_status
                """, (
                    str(row["order_id"]).strip(),
                    row["order_date"],
                    str(row["asin"]).strip().upper(),
                    row.get("sku"),
                    row.get("title"),
                    row["marketplace"],
                    int(row["quantity"]),
                    float(row["item_price"]),
                    float(row["item_tax"]),
                    float(row["shipping_price"]),
                    row.get("currency"),
                    row.get("order_status"),
                ))
                imported += 1
            except Exception as e:
                warnings.append(f"Row skipped ({row.get('order_id')}): {e}")

    conn.close()
    return imported, warnings


def save_recommendation(rec: dict) -> int:
    """Insert a placement recommendation row. Returns new row id."""
    conn = get_conn()
    with conn:
        cur = conn.execute("""
            INSERT OR REPLACE INTO recommendations
                (date_given, asin, marketplace, campaign_name, placement_type,
                 campaign_type, current_multiplier, recommended_action,
                 recommended_multiplier, reasoning, window_days, review_date, score, source,
                 end_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            rec.get("date_given"),
            rec.get("asin"),
            rec.get("marketplace", "amazon.com"),
            rec.get("campaign_name"),
            rec.get("placement_type"),
            rec.get("campaign_type"),
            rec.get("current_multiplier"),
            rec.get("recommended_action"),
            rec.get("recommended_multiplier"),
            rec.get("reasoning"),
            rec.get("window_days", 14),
            rec.get("review_date"),
            rec.get("score"),
            rec.get("source", "auto"),
            rec.get("end_date"),
        ))
        row_id = cur.lastrowid
    conn.close()
    return row_id


def update_recommendation_outcome(rec_id: int, outcome: str):
    """Record what actually happened after a recommendation."""
    conn = get_conn()
    with conn:
        conn.execute("""
            UPDATE recommendations
            SET outcome = ?, outcome_measured_at = datetime('now')
            WHERE id = ?
        """, (outcome, rec_id))
    conn.close()
