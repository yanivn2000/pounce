"""
db/aged_inventory.py — Import and query Amazon Aged Inventory Surcharge report.
"""
from __future__ import annotations
import io
import pandas as pd
from db.database import get_conn

_COUNTRY_TO_MP = {
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
    "NL": "amazon.nl",
    "BE": "amazon.be",
    "PL": "amazon.pl",
    "SE": "amazon.se",
    "IE": "amazon.ie",
}


def _age_lower(tier: str) -> int:
    """Return the lower bound of a tier string like '271-300' or '365+'."""
    tier = tier.strip()
    if "+" in tier:
        return int(tier.replace("+", ""))
    return int(tier.split("-")[0])


def init_aged_inventory_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS aged_inventory (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date  TEXT NOT NULL,
            sku            TEXT,
            asin           TEXT NOT NULL,
            product_name   TEXT,
            currency       TEXT,
            country        TEXT NOT NULL,
            marketplace    TEXT,
            qty_charged    REAL,
            amount_charged REAL,
            age_tier       TEXT,
            age_lower      INTEGER,
            rate_surcharge REAL,
            updated_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_aged_inv_asin ON aged_inventory(asin);
        CREATE INDEX IF NOT EXISTS idx_aged_inv_country ON aged_inventory(country);
    """)
    conn.commit()


def clear_aged_inventory(conn):
    conn.execute("DELETE FROM aged_inventory")
    conn.commit()


def import_aged_inventory_csv(file_obj) -> tuple[int, list[str]]:
    """Parse an Aged Inventory Surcharge CSV and replace all stored rows."""
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
    df.columns = [c.lstrip("﻿?").strip().lower() for c in df.columns]

    required = {"asin", "country", "surcharge-age-tier"}
    missing = required - set(df.columns)
    if missing:
        return 0, [f"Missing columns: {missing}. Found: {list(df.columns)}"]

    conn = get_conn()
    clear_aged_inventory(conn)
    saved = 0

    with conn:
        for _, row in df.iterrows():
            asin = str(row.get("asin", "")).strip().upper()
            if not asin or asin == "NAN":
                continue

            tier = str(row.get("surcharge-age-tier", "")).strip()
            if not tier or tier == "nan":
                continue

            country     = str(row.get("country", "")).strip().upper()
            marketplace = _COUNTRY_TO_MP.get(country, country)

            try:
                qty = float(str(row.get("qty-charged", "0")).replace(",", "") or 0)
            except ValueError:
                qty = 0.0
            try:
                amount = float(str(row.get("amount-charged", "0")).replace(",", "") or 0)
            except ValueError:
                amount = 0.0
            try:
                rate = float(str(row.get("rate-surcharge", "0")).replace(",", "") or 0)
            except ValueError:
                rate = 0.0

            conn.execute("""
                INSERT INTO aged_inventory
                    (snapshot_date, sku, asin, product_name, currency,
                     country, marketplace, qty_charged, amount_charged,
                     age_tier, age_lower, rate_surcharge)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(row.get("snapshot-date", "")).strip(),
                str(row.get("sku", "")).strip(),
                asin,
                str(row.get("product-name", "")).strip(),
                str(row.get("currency", "")).strip().upper(),
                country,
                marketplace,
                qty,
                amount,
                tier,
                _age_lower(tier),
                rate,
            ))
            saved += 1

    conn.close()
    return saved, []


def get_aged_inventory_alerts(min_lower: int, max_lower: int | None = None) -> pd.DataFrame:
    """
    Return aggregated rows where age_lower is in [min_lower, max_lower).
    If max_lower is None, no upper bound (captures 365+ etc.).
    Groups by (asin, marketplace) and sums qty/amount across age tiers.
    """
    conn = get_conn()
    if max_lower is None:
        query = """
            SELECT asin, marketplace, currency,
                   MIN(product_name) AS product_name,
                   SUM(qty_charged)    AS total_units,
                   SUM(amount_charged) AS total_charged,
                   MAX(age_lower)      AS max_age_lower,
                   GROUP_CONCAT(DISTINCT age_tier) AS age_tiers
            FROM aged_inventory
            WHERE age_lower >= ?
            GROUP BY asin, marketplace, currency
            ORDER BY total_charged DESC
        """
        df = pd.read_sql_query(query, conn, params=[min_lower])
    else:
        query = """
            SELECT asin, marketplace, currency,
                   MIN(product_name) AS product_name,
                   SUM(qty_charged)    AS total_units,
                   SUM(amount_charged) AS total_charged,
                   MAX(age_lower)      AS max_age_lower,
                   GROUP_CONCAT(DISTINCT age_tier) AS age_tiers
            FROM aged_inventory
            WHERE age_lower >= ? AND age_lower < ?
            GROUP BY asin, marketplace, currency
            ORDER BY total_charged DESC
        """
        df = pd.read_sql_query(query, conn, params=[min_lower, max_lower])
    conn.close()
    return df


def get_aged_inventory_snapshot_date() -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT snapshot_date FROM aged_inventory ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0][:10] if row else ""
