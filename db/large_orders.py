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


# ══════════════════════════════════════════════════════════════════════════════
# Analytics — large-line tracker, customers, badge impact, claims
# ══════════════════════════════════════════════════════════════════════════════
import json as _json
from datetime import date as _date, timedelta as _td

_ALL_MPS = ["amazon.com", "amazon.ca", "amazon.co.uk", "amazon.de",
            "amazon.fr", "amazon.it", "amazon.es", "amazon.com.au"]

DEFAULT_SETTINGS = {
    "large_threshold": 4,
    "refund_no_return_days": 45,
    "max_order_qty":      {m: 15 for m in _ALL_MPS},
    "transit_days":       {m: 3 for m in _ALL_MPS},
    "return_window_days": {m: 30 for m in _ALL_MPS},
    "holiday_window":     {m: (m == "amazon.com") for m in _ALL_MPS},
}


def get_lo_settings(conn=None) -> dict:
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key='lo_settings'").fetchone()
        saved = _json.loads(row[0]) if row and row[0] else {}
    except Exception:
        saved = {}
    out = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}
    for k, v in (saved or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    if close:
        conn.close()
    return out


def save_lo_settings(conn, settings: dict) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('lo_settings', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_json.dumps(settings),))
    conn.commit()


def get_badge_inputs(conn=None) -> dict:
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key='lo_badge_inputs'").fetchone()
        out = _json.loads(row[0]) if row and row[0] else {}
    except Exception:
        out = {}
    if close:
        conn.close()
    return out


def save_badge_inputs(conn, data: dict) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('lo_badge_inputs', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_json.dumps(data),))
    conn.commit()


def _norm_zip(z) -> str:
    return "".join(ch for ch in str(z or "").upper() if ch.isalnum())


def customer_key(mp: str, city, postal) -> str:
    return f"{mp}|{_norm_zip(postal)}|{str(city or '').strip().upper()}"


def _parse_dt(s):
    """Parse an ISO-ish date/datetime string to a date; None on failure."""
    if not s:
        return None
    try:
        return _date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _mp_val(settings: dict, key: str, mp: str, default):
    d = settings.get(key, {})
    return d.get(mp, default) if isinstance(d, dict) else default


def _window_end(order_d: "_date", mp: str, settings: dict):
    transit = int(_mp_val(settings, "transit_days", mp, 3))
    rwin = int(_mp_val(settings, "return_window_days", mp, 30))
    end = order_d + _td(days=transit + rwin)
    if _mp_val(settings, "holiday_window", mp, False) and order_d.month in (11, 12):
        jan31 = _date(order_d.year + 1, 1, 31)
        if jan31 > end:
            end = jan31
    return end


def get_large_orders_analysis(conn=None, marketplace=None, start=None, end=None,
                              settings=None) -> dict:
    """Return {lines, customers, badge, claims, kpis} for the Large Orders page.

    marketplace: None = all. start/end: ISO dates to bound order_date (inclusive)."""
    close = conn is None
    if conn is None:
        conn = get_conn()
    if settings is None:
        settings = get_lo_settings(conn)
    thr = int(settings.get("large_threshold", 4))
    rnr_days = int(settings.get("refund_no_return_days", 45))
    today = _date.today()

    mp_clause, params = "", []
    if marketplace:
        mp_clause += " AND o.marketplace=?"; params.append(marketplace)
    if start:
        mp_clause += " AND o.order_date>=?"; params.append(str(start))
    if end:
        mp_clause += " AND o.order_date<=?"; params.append(str(end))

    # ── Large lines (one row per order × asin, qty >= threshold, not cancelled) ──
    line_rows = conn.execute(f"""
        SELECT o.order_id, o.order_date,
               COALESCE(o.order_datetime, o.order_date) AS odt,
               o.asin, o.sku, o.marketplace, o.quantity, o.item_price,
               o.ship_city, o.ship_state, o.ship_postal_code, o.title
        FROM orders o
        WHERE o.quantity >= ?
          AND COALESCE(o.order_status,'') NOT IN ('Cancelled')
          {mp_clause}
        ORDER BY o.order_date DESC
    """, [thr, *params]).fetchall()

    # ── Received-back units per (order_id, asin) with disposition/reason ─────────
    recv = {}           # (order_id, asin) -> {"n": int, "by_disp": {...}, "reasons": set, "sellable": int}
    for r in conn.execute(
        "SELECT order_id, asin, disposition, reason, COALESCE(quantity,1) q FROM amazon_returns").fetchall():
        k = (str(r[0] or ""), str(r[1] or "").upper())
        d = recv.setdefault(k, {"n": 0, "by_disp": {}, "reasons": set(), "sellable": 0})
        q = int(r[4] or 1)
        d["n"] += q
        disp = (r[2] or "").strip() or "—"
        d["by_disp"][disp] = d["by_disp"].get(disp, 0) + q
        if r[3]:
            d["reasons"].add(r[3])
        if disp.upper() == "SELLABLE":
            d["sellable"] += q

    # ── Refunds per order_id (any 'Refund' transaction) ─────────────────────────
    refund = {}         # order_id -> {"amt": float, "date": date}
    for r in conn.execute(
        "SELECT order_id, tx_date, gross_sales, net_total FROM amazon_transactions "
        "WHERE order_id IS NOT NULL AND order_id!='' AND LOWER(COALESCE(tx_type,'')) LIKE '%refund%'").fetchall():
        oid = str(r[0])
        amt = abs(float(r[2] or 0) or float(r[3] or 0))
        dt = _parse_dt(r[1])
        d = refund.setdefault(oid, {"amt": 0.0, "date": None})
        d["amt"] += amt
        if dt and (d["date"] is None or dt < d["date"]):
            d["date"] = dt   # earliest refund date

    # ── Reimbursements per (order_id, sku): units + whether CustomerReturn ───────
    reimb = {}          # (order_id, sku) -> {"units": int, "customer_return": bool, "amt": float}
    for r in conn.execute(
        "SELECT order_id, sku, reason, COALESCE(qty_total,0), COALESCE(amount_total,0) FROM reimbursements").fetchall():
        k = (str(r[0] or ""), str(r[1] or ""))
        d = reimb.setdefault(k, {"units": 0, "customer_return": False, "amt": 0.0})
        d["units"] += int(r[3] or 0)
        d["amt"] += float(r[4] or 0)
        if (r[2] or "") == "CustomerReturn":
            d["customer_return"] = True

    # ── Build line records + statuses ────────────────────────────────────────────
    lines = []
    for lr in line_rows:
        oid, odate, odt, asin, sku, mp, qty, price = (
            str(lr[0]), lr[1], lr[2], str(lr[3] or "").upper(), str(lr[4] or ""),
            lr[5], int(lr[6] or 0), float(lr[7] or 0))
        rk = recv.get((oid, asin), {"n": 0, "by_disp": {}, "reasons": set(), "sellable": 0})
        received = rk["n"]
        rf = refund.get(oid, {"amt": 0.0, "date": None})
        rb = reimb.get((oid, sku), {"units": 0, "customer_return": False, "amt": 0.0})
        od = _parse_dt(odate) or today
        wend = _window_end(od, mp, settings)
        days_left = (wend - today).days

        if received >= qty and qty > 0:
            status = "RETURNED_FULL"
        elif received > 0:
            status = "RETURNED_PARTIAL"
        elif (rf["date"] and (today - rf["date"]).days > rnr_days
              and received < qty and not rb["customer_return"]):
            status = "REFUND_NO_RETURN_OVERDUE"
        elif rf["amt"] > 0 and received < qty:
            status = "RETURN_IN_PROGRESS"
        elif today <= wend:
            status = "AT_RISK"
        else:
            status = "CLOSED_SAFE"

        cust = customer_key(mp, lr[8], lr[10])
        lines.append({
            "marketplace": mp, "order_id": oid, "order_date": odate,
            "order_datetime": odt, "sku": sku, "asin": asin, "qty": qty,
            "revenue": round(price, 2), "city": lr[8], "state": lr[9],
            "zip": lr[10], "customer_key": cust, "window_end": wend.isoformat(),
            "days_left": days_left, "received": received,
            "by_disposition": rk["by_disp"], "sellable_back": rk["sellable"],
            "return_reasons": sorted(rk["reasons"]),
            "refund_seen": bool(rf["amt"] > 0), "refund_date": rf["date"].isoformat() if rf["date"] else None,
            "refund_amt": round(rf["amt"], 2), "reimbursed_units": rb["units"],
            "status": status, "title": lr[11],
        })

    if close:
        conn.close()
    return {"lines": lines, "settings": settings, "today": today.isoformat()}
