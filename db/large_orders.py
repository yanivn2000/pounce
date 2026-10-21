"""
db/large_orders.py — Large-order return-risk tracking.

Covers the Reimbursements importer plus (added incrementally) the large-line
tracker, customer view, badge-impact estimator and claims queue.

Buyer PII (ship_name / address / buyer_name / buyer_email) is NEVER surfaced by
anything in this module — only marketplace + city/state/postal are used.
"""
from __future__ import annotations

import io
import pandas as pd

from .database import get_conn

# currency-unit → our marketplace key (per-row; NA file mixes USD+CAD)
_CURRENCY_MP = {
    "USD": "amazon.com", "CAD": "amazon.ca", "GBP": "amazon.co.uk",
    "EUR": "amazon.de", "AUD": "amazon.com.au", "SEK": "amazon.se",
    "PLN": "amazon.pl", "MXN": "amazon.com.mx",
}


def _read_csv_any_encoding(file_obj, sep: str = ",") -> pd.DataFrame:
    """Read a CSV that may be UTF-8 (with/without BOM) or Windows-1252.

    Amazon CA/UK reports frequently arrive as cp1252 (0x92 curly apostrophe),
    which pandas' default UTF-8 reader mangles or rejects."""
    if isinstance(file_obj, (str, bytes)) and not hasattr(file_obj, "read"):
        with open(file_obj, "rb") as fh:
            raw = fh.read()
    else:
        raw = file_obj.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        try:
            file_obj.seek(0)
        except Exception:
            pass
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return pd.read_csv(io.StringIO(raw.decode(enc)), dtype=str, sep=sep)
        except (UnicodeDecodeError, Exception):
            continue
    # last resort: replace undecodable bytes
    return pd.read_csv(io.StringIO(raw.decode("utf-8", errors="replace")), dtype=str, sep=sep)


def _f(x) -> float:
    try:
        v = float(str(x).strip().replace(",", ""))
        return 0.0 if v != v else v  # NaN guard
    except (TypeError, ValueError):
        return 0.0


def _i(x) -> int:
    return int(_f(x))


# ── Reimbursements importer ───────────────────────────────────────────────────
_REIMB_COLS = [
    "approval-date", "reimbursement-id", "case-id", "amazon-order-id", "reason",
    "sku", "fnsku", "asin", "product-name", "condition", "currency-unit",
    "amount-per-unit", "amount-total", "quantity-reimbursed-cash",
    "quantity-reimbursed-inventory", "quantity-reimbursed-total",
    "original-reimbursement-id", "original-reimbursement-type",
]


def import_reimbursements_csv(file_obj, conn=None) -> tuple[int, list[str]]:
    """Import an Amazon Reimbursements report (NA or UK/EU — same format).

    Idempotent upsert on (reimbursement_id, sku). Marketplace is derived per row
    from currency-unit. Returns (rows_imported, warnings)."""
    close = conn is None
    if conn is None:
        conn = get_conn()
    warnings: list[str] = []
    try:
        df = _read_csv_any_encoding(file_obj)
    except Exception as e:
        if close:
            conn.close()
        return 0, [f"Failed to read CSV: {e}"]

    df.columns = [c.strip().lower() for c in df.columns]
    if "reimbursement-id" not in df.columns:
        if close:
            conn.close()
        return 0, [f"Not a Reimbursements report. Columns: {list(df.columns)[:8]}…"]

    def g(row, col):
        v = row.get(col)
        return None if v is None or (isinstance(v, float) and v != v) else str(v).strip()

    imported = 0
    with conn:
        for _, row in df.iterrows():
            rid = g(row, "reimbursement-id")
            if not rid:
                continue
            cur = (g(row, "currency-unit") or "").upper()
            mp = _CURRENCY_MP.get(cur, "")
            conn.execute("""
                INSERT INTO reimbursements
                    (approval_date, reimbursement_id, case_id, order_id, reason, sku,
                     fnsku, asin, product_name, condition, currency, amount_per_unit,
                     amount_total, qty_cash, qty_inventory, qty_total,
                     original_reimbursement_id, original_reimbursement_type, marketplace)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(reimbursement_id, sku) DO UPDATE SET
                    approval_date=excluded.approval_date, case_id=excluded.case_id,
                    order_id=excluded.order_id, reason=excluded.reason,
                    fnsku=excluded.fnsku, asin=excluded.asin,
                    product_name=excluded.product_name, condition=excluded.condition,
                    currency=excluded.currency, amount_per_unit=excluded.amount_per_unit,
                    amount_total=excluded.amount_total, qty_cash=excluded.qty_cash,
                    qty_inventory=excluded.qty_inventory, qty_total=excluded.qty_total,
                    original_reimbursement_id=excluded.original_reimbursement_id,
                    original_reimbursement_type=excluded.original_reimbursement_type,
                    marketplace=excluded.marketplace
            """, (
                g(row, "approval-date"), rid, g(row, "case-id"),
                g(row, "amazon-order-id"), g(row, "reason"),
                (g(row, "sku") or ""), g(row, "fnsku"),
                (g(row, "asin") or "").upper() or None, g(row, "product-name"),
                g(row, "condition"), cur or None,
                _f(row.get("amount-per-unit")), _f(row.get("amount-total")),
                _i(row.get("quantity-reimbursed-cash")),
                _i(row.get("quantity-reimbursed-inventory")),
                _i(row.get("quantity-reimbursed-total")),
                g(row, "original-reimbursement-id"),
                g(row, "original-reimbursement-type"), mp,
            ))
            imported += 1
    if close:
        conn.close()
    return imported, warnings


def get_reimbursements_summary(conn=None) -> dict:
    """Small coverage summary for the UI / verification."""
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*), MIN(approval_date), MAX(approval_date), "
            "COALESCE(SUM(amount_total),0) FROM reimbursements"
        ).fetchone()
        by_mp = {r[0]: r[1] for r in conn.execute(
            "SELECT marketplace, COUNT(*) FROM reimbursements GROUP BY marketplace")}
        return {"rows": int(row[0] or 0), "earliest": row[1], "latest": row[2],
                "total_amount": float(row[3] or 0), "by_marketplace": by_mp}
    except Exception:
        return {"rows": 0, "earliest": None, "latest": None,
                "total_amount": 0.0, "by_marketplace": {}}
    finally:
        if close:
            conn.close()
