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
            "reimbursed_net": round(rb["amt"], 2),
            "status": status, "title": lr[11],
        })

    if close:
        conn.close()
    return {"lines": lines, "settings": settings, "today": today.isoformat()}


# ── Customers / Badge / Claims (built from the tracker lines + a few queries) ──
from datetime import datetime as _datetime


def _parse_full(s):
    """Parse 'YYYY-MM-DD HH:MM:SS' to datetime, but only when a real time is
    present (date-only strings return None, so split-minute logic stays dormant
    until orders are re-imported with timestamps)."""
    s = str(s or "")
    if len(s) <= 10:
        return None
    try:
        dt = _datetime.fromisoformat(s.replace("T", " ")[:19])
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            return None   # midnight = almost certainly a date-only import
        return dt
    except Exception:
        return None


def build_customers(lines, settings) -> list[dict]:
    from collections import defaultdict
    g = defaultdict(list)
    for l in lines:
        g[l["customer_key"]].append(l)
    out = []
    for key, ls in g.items():
        mp = ls[0]["marketplace"]
        maxcap = int(_mp_val(settings, "max_order_qty", mp, 15))
        order_ids = sorted(set(l["order_id"] for l in ls))
        dates = sorted(d for d in (_parse_dt(l["order_date"]) for l in ls) if d)
        times = sorted(t for t in (_parse_full(l["order_datetime"]) for l in ls) if t)
        # repeat large buyer: 2+ large lines whose order dates fall within 60 days
        repeat = len(ls) >= 2 and bool(dates) and (dates[-1] - dates[0]).days <= 60
        # split: 2+ distinct orders from this key within 60 minutes (needs real time)
        split = False
        order_times = sorted({(l["order_id"]): t for l in ls
                              for t in [_parse_full(l["order_datetime"])] if t}.values())
        for i in range(len(order_times) - 1):
            if (order_times[i + 1] - order_times[i]).total_seconds() <= 3600:
                split = True
                break
        out.append({
            "customer_key": key, "marketplace": mp,
            "city": ls[0]["city"], "state": ls[0]["state"], "zip": ls[0]["zip"],
            "orders": len(order_ids), "large_lines": len(ls),
            "units": sum(l["qty"] for l in ls),
            "skus": len(set(l["sku"] for l in ls)),
            "first_order": min(l["order_date"] for l in ls),
            "last_order": max(l["order_date"] for l in ls),
            "units_returned": sum(l["received"] for l in ls),
            "refunds_seen": sum(1 for l in ls if l["refund_seen"]),
            "repeat_large": repeat, "split_orders": split,
            "at_cap": any(l["qty"] == maxcap for l in ls),
            "above_cap": any(l["qty"] > maxcap for l in ls),
        })
    out.sort(key=lambda c: (-c["units"], -c["large_lines"]))
    return out


def build_claims(conn, lines) -> dict:
    # Net reimbursements per (order_id, ASIN). Reimbursement_Reversal rows carry
    # negative units/amount, so SUM() nets clawbacks out automatically.
    reimb_net = {}
    for r in conn.execute(
        "SELECT order_id, UPPER(asin), COALESCE(SUM(qty_total),0), COALESCE(SUM(amount_total),0) "
        "FROM reimbursements WHERE order_id IS NOT NULL AND order_id!='' "
        "GROUP BY order_id, UPPER(asin)").fetchall():
        reimb_net[(str(r[0]), str(r[1] or ""))] = (int(r[2] or 0), round(float(r[3] or 0), 2))

    # 1) contradiction: CUSTOMER_DAMAGED but carrier/FC fault — aggregated per
    #    order-line, cross-referenced against reimbursements so you see which
    #    units are still OPEN (not yet net-reimbursed) vs already covered.
    contradiction = []
    for r in conn.execute("""
        SELECT order_id, UPPER(asin) AS asin, MAX(sku) AS sku, MAX(marketplace) AS mp,
               COUNT(*) AS units, MAX(return_date) AS last_return,
               GROUP_CONCAT(DISTINCT reason) AS reasons
        FROM amazon_returns
        WHERE UPPER(COALESCE(disposition,''))='CUSTOMER_DAMAGED'
          AND UPPER(COALESCE(reason,'')) IN ('DAMAGED_BY_CARRIER','DAMAGED_BY_FC')
        GROUP BY order_id, UPPER(asin)
        ORDER BY units DESC""").fetchall():
        oid, asin, units = str(r[0]), str(r[1] or ""), int(r[4] or 0)
        rb_u, rb_amt = reimb_net.get((oid, asin), (0, 0.0))
        rb_u = max(0, rb_u)
        open_units = max(0, units - rb_u)
        contradiction.append({
            "order_id": oid, "asin": asin, "sku": r[2], "marketplace": r[3],
            "reason": r[6], "last_return": (r[5] or "")[:10],
            "units": units, "reimbursed_units": rb_u, "reimbursed_net": rb_amt,
            "open_units": open_units, "status": "open" if open_units > 0 else "covered",
        })
    # 2) refund-no-return overdue
    overdue = [l for l in lines if l["status"] == "REFUND_NO_RETURN_OVERDUE"]
    # 3) shortfall on refunded lines: ordered - received - reimbursed > 0
    shortfall = [dict(l, shortfall=l["qty"] - l["received"] - l["reimbursed_units"])
                 for l in lines
                 if l["refund_seen"] and (l["qty"] - l["received"] - l["reimbursed_units"]) > 0]
    # 4) sellable returns from flagged large lines → inspect for tampering
    inspect = [l for l in lines if l["sellable_back"] > 0]
    return {"contradiction": contradiction, "refund_no_return_overdue": overdue,
            "shortfall": shortfall, "inspect_sellable": inspect}


def build_badge(conn, lines, settings, badge_inputs=None) -> list[dict]:
    badge_inputs = badge_inputs or {}
    today = _date.today()
    since = (today - _td(days=90)).isoformat()
    units90 = {}
    for r in conn.execute("""
        SELECT UPPER(asin) a, marketplace, SUM(quantity)
        FROM orders WHERE order_date >= ? AND COALESCE(order_status,'') NOT IN ('Cancelled')
        GROUP BY UPPER(asin), marketplace""", (since,)).fetchall():
        units90[(r[0], r[1])] = int(r[2] or 0)
    ret90 = {}
    for r in conn.execute("""
        SELECT UPPER(asin) a, marketplace, COUNT(*)
        FROM amazon_returns WHERE return_date >= ?
        GROUP BY UPPER(asin), marketplace""", (since,)).fetchall():
        ret90[(r[0], r[1])] = int(r[2] or 0)

    # open large lines per (asin, mp) → potential pp impact
    open_lines = {}
    for l in lines:
        if l["status"] in ("AT_RISK", "RETURN_IN_PROGRESS"):
            open_lines.setdefault((l["asin"], l["marketplace"]), []).append(l["qty"])

    keys = set(units90) | set(open_lines) | {tuple(k.split("|")) for k in badge_inputs if "|" in k}
    out = []
    for (asin, mp) in keys:
        u = units90.get((asin, mp), 0)
        rr = ret90.get((asin, mp), 0)
        mi = badge_inputs.get(f"{asin}|{mp}", {})
        thr = mi.get("threshold")
        opens = open_lines.get((asin, mp), [])
        max_pp = round(max(opens) / u * 100, 2) if (opens and u) else 0.0
        rate = round(rr / u * 100, 2) if u else None
        would_cross = bool(thr and rate is not None and (rate + max_pp) >= float(thr))
        out.append({
            "asin": asin, "marketplace": mp, "units_90d": u,
            "returns_90d_lowerbound": rr, "return_rate_pct": rate,
            "short_term_rate": mi.get("short_term"), "long_term_rate": mi.get("long_term"),
            "suggested_threshold": thr, "badge": mi.get("badge"),
            "open_large_lines": len(opens),
            "max_pp_if_returned": max_pp, "would_cross_threshold": would_cross,
        })
    out.sort(key=lambda b: (-(b["max_pp_if_returned"] or 0), -(b["units_90d"] or 0)))
    return out


def full_analysis(conn=None, marketplace=None, start=None, end=None) -> dict:
    close = conn is None
    if conn is None:
        conn = get_conn()
    settings = get_lo_settings(conn)
    base = get_large_orders_analysis(conn, marketplace, start, end, settings)
    lines = base["lines"]
    result = {
        "lines": lines,
        "customers": build_customers(lines, settings),
        "claims": build_claims(conn, lines),
        "badge": build_badge(conn, lines, settings, get_badge_inputs(conn)),
        "settings": settings, "today": base["today"],
    }
    # KPIs
    open_lines = [l for l in lines if l["status"] in ("AT_RISK", "RETURN_IN_PROGRESS")]
    result["kpis"] = {
        "large_lines": len(lines),
        "open_risk_units": sum(l["qty"] - l["received"] for l in open_lines),
        "open_risk_revenue": round(sum(l["revenue"] for l in open_lines), 2),
        "units_returned": sum(l["received"] for l in lines),
        "claim_candidates": (sum(1 for c in result["claims"]["contradiction"] if c["open_units"] > 0)
                             + len(result["claims"]["refund_no_return_overdue"])
                             + len(result["claims"]["shortfall"])),
    }
    if close:
        conn.close()
    return result


# ── Amazon case-text builder (auto-filled from a flagged row) ──────────────────
def case_text(kind: str, r: dict) -> str:
    """Return a ready-to-paste Amazon case message for a flagged claim row.
    kind ∈ {'contradiction','overdue','shortfall'}."""
    oid = r.get("order_id", "") or ""
    asin = r.get("asin", "") or ""
    sku = r.get("sku", "") or ""
    if kind == "contradiction":
        units = r.get("open_units") or r.get("units", "")
        return (
            "Subject: Reimbursement request - FBA return mis-dispositioned as customer-damaged\n\n"
            f"Order ID: {oid}\n"
            f"ASIN / SKU: {asin} / {sku}\n"
            f"Units affected: {units}\n\n"
            f"This FBA customer return was dispositioned CUSTOMER_DAMAGED, but the return\n"
            f"reason is {r.get('reason','')}, indicating the damage occurred in transit or at\n"
            "the fulfillment center - not by the customer. As the unit was not customer-\n"
            "damaged, please review the disposition and reimburse the affected unit(s).\n"
            "Return and LPN details available on request.")
    if kind == "overdue":
        open_u = max(0, int(r.get("qty", 0)) - int(r.get("received", 0)) - int(r.get("reimbursed_units", 0)))
        return (
            "Subject: Reimbursement - customer refunded but units never returned\n\n"
            f"Order ID: {oid}\n"
            f"ASIN / SKU: {asin} / {sku}\n"
            f"Refund date: {r.get('refund_date','')}    Refund amount: {r.get('refund_amt','')}\n"
            f"Ordered: {r.get('qty','')}   Received back: {r.get('received','')}   Still missing: {open_u}\n\n"
            f"The customer was refunded on this order, but {open_u} unit(s) were never returned\n"
            "to the fulfillment center and no reimbursement has been issued, although the\n"
            "return window has passed. Please reimburse the un-returned, refunded unit(s).")
    if kind == "shortfall":
        sf = r.get("shortfall", "")
        return (
            "Subject: Reimbursement - missing units on a refunded order\n\n"
            f"Order ID: {oid}\n"
            f"ASIN / SKU: {asin} / {sku}\n"
            f"Ordered: {r.get('qty','')}   Received back: {r.get('received','')}   Reimbursed: {r.get('reimbursed_units','')}\n"
            f"Unaccounted-for units: {sf}\n\n"
            f"This order was refunded, but {sf} unit(s) are unaccounted for - not returned to\n"
            "the fulfillment center and not reimbursed. Please investigate and reimburse the\n"
            "missing unit(s).")
    return ""
