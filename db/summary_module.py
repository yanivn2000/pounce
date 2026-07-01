"""
summary_module.py
Deterministic monthly business summary for the Amazon P&L.

Design rule (same as claude_client.py):
  ALL numbers, comparisons, %-changes and "dramatic change" flags are
  computed here in pure Python/SQL — never by the LLM. Claude only turns
  the finished numbers into a plain-English narrative (see
  claude_client.generate_monthly_summary).

The SQL predicates below intentionally mirror the P&L tab in app.py
(advertising / storage / coupon / transfer definitions) so the summary
totals match what the user already sees on screen.

Summaries are cached per (ym, marketplace-scope) in the monthly_summaries
table: the deterministic stats + the Claude narrative are stored once and
reloaded instantly on revisit. Regeneration is explicit (user button).
"""

import json
import hashlib
from datetime import datetime, timezone

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ── Multilingual tx_type / product_details predicates (mirror app.py P&L) ──────

# Advertising = Service Fee rows whose description is "Cost of Advertising"
_ADV_PRED = (
    "(LOWER(product_details) LIKE '%advertis%' "
    "OR product_details='Werbekosten' "
    "OR product_details LIKE '%publicité%' "
    "OR product_details LIKE '%pubblicità%' "
    "OR product_details='Koszt reklamy')"
)

# Storage fee tx_types across marketplaces
_STORAGE_TYPES = [
    "FBA Inventory Fee",
    "Versand durch Amazon Lagergebühr",
    "Tarifas de inventario de Logística de Amazon",
    "Frais de stock Expédié par Amazon",
    "Costo di stoccaggio Logistica di Amazon",
    "FBA Inventory Fee - Correction",
    "FBA Inventory Fee - Reversal",
]
_STORAGE_IN = "','".join(_STORAGE_TYPES)

# Transfer / debt equivalents excluded from net payout
_TRANSFER_TYPES = [
    "Transfer", "Debt",
    "Übertrag", "Verbindlichkeit",       # DE
    "Transfert", "Solde négatif",         # FR
    "Transferir", "Saldo descubierto",    # ES
    "Overboeking", "Schuld",              # NL
    "Saldo negativo",                     # IT
]
_TRANSFER_IN = "','".join(_TRANSFER_TYPES)

# Currency → marketplace key used in fx_rates (rate = local units per 1 USD)
_CURRENCY_TO_MP = {
    "USD": "amazon.com",   "CAD": "amazon.ca",
    "GBP": "amazon.co.uk", "EUR": "amazon.de",
    "AUD": "amazon.com.au", "SEK": "amazon.se",
    "PLN": "amazon.pl",    "MXN": "amazon.com.mx",
    "BRL": "amazon.com.br",
}


# ── Cache table ───────────────────────────────────────────────────────────────

def init_summary_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monthly_summaries (
            ym           TEXT NOT NULL,
            scope_key    TEXT NOT NULL,
            stats_json   TEXT,
            summary_text TEXT,
            model        TEXT,
            generated_at TEXT,
            PRIMARY KEY (ym, scope_key)
        )
    """)
    conn.commit()


def _scope_key(mps) -> str:
    """Stable key for a marketplace selection (order-independent)."""
    return hashlib.md5(",".join(sorted(mps)).encode()).hexdigest()[:12]


def get_cached_summary(conn, ym, mps) -> dict | None:
    init_summary_table(conn)
    row = conn.execute(
        "SELECT stats_json, summary_text, model, generated_at "
        "FROM monthly_summaries WHERE ym=? AND scope_key=?",
        (ym, _scope_key(mps)),
    ).fetchone()
    if not row:
        return None
    return {
        "stats": json.loads(row[0]) if row[0] else None,
        "summary_text": row[1],
        "model": row[2],
        "generated_at": row[3],
    }


def save_summary(conn, ym, mps, stats: dict, summary_text: str, model: str):
    init_summary_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO monthly_summaries "
        "(ym, scope_key, stats_json, summary_text, model, generated_at) "
        "VALUES (?,?,?,?,?,?)",
        (ym, _scope_key(mps), json.dumps(stats), summary_text, model,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()


# ── FX ────────────────────────────────────────────────────────────────────────

def _load_fx(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT marketplace, rate FROM fx_rates").fetchall()}


def _to_usd(amount, currency, fx) -> float:
    mp = _CURRENCY_TO_MP.get(currency)
    rate = fx.get(mp) if mp else None
    if rate:
        return amount / rate
    return amount  # unknown currency — pass through


# ── Period totals ─────────────────────────────────────────────────────────────

def available_months(conn) -> list[tuple[int, str]]:
    """(year, month_name) pairs that have Order rows, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT year, month FROM amazon_transactions "
        "WHERE tx_type='Order'"
    ).fetchall()
    pairs = [(int(y), m) for (y, m) in rows if m in MONTHS]
    pairs.sort(key=lambda p: (p[0], MONTHS.index(p[1])), reverse=True)
    return pairs


def all_marketplaces(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT marketplace FROM amazon_transactions "
        "WHERE marketplace IS NOT NULL ORDER BY marketplace").fetchall()]


def _period_totals(conn, year, month_name, mps, fx) -> dict:
    """USD totals for one month across the selected marketplaces."""
    ph = ",".join("?" * len(mps))

    def usd_sum(sql, extra_params=()):
        rows = conn.execute(
            sql, [year, month_name, *extra_params, *mps]).fetchall()
        return sum(_to_usd(float(r[1] or 0), r[0], fx) for r in rows)

    base = (f"FROM amazon_transactions "
            f"WHERE year=? AND month=? {{extra}} AND marketplace IN ({ph}) "
            f"GROUP BY currency")

    sales = usd_sum("SELECT currency,SUM(gross_sales) " +
                    base.format(extra="AND tx_type='Order'"))
    net_orders = usd_sum("SELECT currency,SUM(net_total) " +
                         base.format(extra="AND tx_type='Order'"))
    fees = usd_sum("SELECT currency,SUM(ABS(amazon_fees)) " +
                   base.format(extra="AND tx_type='Order'"))
    refunds = usd_sum("SELECT currency,SUM(ABS(net_total)) " +
                      base.format(extra="AND tx_type='Refund'"))
    advertising = usd_sum("SELECT currency,SUM(ABS(net_total)) " +
                          base.format(extra=f"AND tx_type='Service Fee' AND {_ADV_PRED}"))
    storage = usd_sum("SELECT currency,SUM(ABS(net_total)) " +
                      base.format(extra=(f"AND tx_type IN ('{_STORAGE_IN}') "
                                         f"AND LOWER(COALESCE(product_details,'')) NOT LIKE '%long%'")))
    lt_storage = usd_sum("SELECT currency,SUM(ABS(net_total)) " +
                         base.format(extra=(f"AND tx_type IN ('{_STORAGE_IN}') "
                                            f"AND LOWER(COALESCE(product_details,'')) LIKE '%long%'")))
    coupons = usd_sum("SELECT currency,SUM(ABS(net_total)) " +
                      base.format(extra=("AND tx_type='Service Fee' "
                                         "AND LOWER(product_details) LIKE '%coupon%'")))
    # Net payout = everything Amazon actually pays out (excl. bank transfers)
    net_payout = usd_sum("SELECT currency,SUM(net_total) " +
                         base.format(extra=f"AND tx_type NOT IN ('{_TRANSFER_IN}')"))
    orders_cnt = conn.execute(
        f"SELECT COUNT(*) FROM amazon_transactions "
        f"WHERE year=? AND month=? AND tx_type='Order' AND marketplace IN ({ph})",
        [year, month_name, *mps]).fetchone()[0]

    cogs = _cogs_for_month(conn, year, month_name, mps, fx)

    return {
        "sales": sales,
        "net_orders": net_orders,
        "fees": fees,
        "refunds": refunds,
        "advertising": advertising,
        "storage": storage,
        "lt_storage": lt_storage,
        "coupons": coupons,
        "net_payout": net_payout,
        "cogs": cogs,
        "profit": net_payout - cogs,
        "orders": int(orders_cnt),
    }


def _cogs_for_month(conn, year, month_name, mps, fx) -> float:
    """COGS via SellerBoard per-unit costs joined on the orders table.
    Returns 0.0 when the orders table is empty / ASINs are unmapped."""
    try:
        from db.amazon_module import get_sellerboard_cost_map
        sb = get_sellerboard_cost_map(conn)  # {ASIN_UPPER: cost_usd}
    except Exception:
        return 0.0
    if not sb:
        return 0.0
    ph = ",".join("?" * len(mps))
    try:
        rows = conn.execute(f"""
            SELECT o.asin, SUM(o.quantity) AS units
            FROM amazon_transactions at
            JOIN orders o ON o.order_id = at.order_id
            WHERE at.year=? AND at.month=? AND at.tx_type='Order'
              AND at.marketplace IN ({ph})
              AND o.order_status NOT IN ('Cancelled','Pending')
            GROUP BY o.asin
        """, [year, month_name, *mps]).fetchall()
    except Exception:
        return 0.0
    total = 0.0
    for asin, units in rows:
        total += float(units or 0) * sb.get(str(asin or "").upper(), 0.0)
    return total


# ── Product movers ────────────────────────────────────────────────────────────
# Sourced from the orders table and keyed by ASIN (not title) so near-identical
# product titles are disambiguated. All-marketplaces (orders marketplace codes
# differ from the P&L codes), consistent with theme movers.

def _short(name: str, n: int = 55) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


def _orders_by_asin_detailed(conn, start, end, fx) -> dict:
    """{ASIN: {sales_usd, units, orders, title, sku}} for [start, end)."""
    rows = conn.execute("""
        SELECT UPPER(asin) AS asin, currency,
               SUM(quantity) AS units, SUM(item_price) AS revenue,
               COUNT(*) AS cnt, MAX(sku) AS sku, MAX(title) AS title
        FROM orders
        WHERE substr(order_date,1,10) >= ? AND substr(order_date,1,10) < ?
          AND COALESCE(order_status,'') NOT IN ('Cancelled','Pending')
          AND asin IS NOT NULL AND asin != ''
        GROUP BY UPPER(asin), currency
    """, (start, end)).fetchall()
    out: dict = {}
    for asin, ccy, units, rev, cnt, sku, title in rows:
        d = out.setdefault(asin, {"sales": 0.0, "units": 0, "orders": 0,
                                  "title": "", "sku": ""})
        d["sales"] += _to_usd(float(rev or 0), ccy, fx)
        d["units"] += int(units or 0)
        d["orders"] += int(cnt or 0)
        if not d["title"] and title:
            d["title"] = title
        if not d["sku"] and sku:
            d["sku"] = sku
    return out


def _product_movers(conn, cur_s, cur_e, base_s, base_e, fx,
                    img_map=None, top_n=5) -> dict:
    """Per-ASIN sales change: current window [cur_s,cur_e) vs baseline
    [base_s,base_e). Used for both MoM (prev month) and YoY (same month
    last year)."""
    img_map = img_map or {}
    now = _orders_by_asin_detailed(conn, cur_s, cur_e, fx)
    base = _orders_by_asin_detailed(conn, base_s, base_e, fx)

    movers = []
    for asin in set(now) | set(base):
        a = now.get(asin)
        b = base.get(asin)
        sales_now = a["sales"] if a else 0.0
        sales_prev = b["sales"] if b else 0.0
        delta = sales_now - sales_prev
        pct = (delta / sales_prev) if sales_prev > 0 else None
        title = (a or b).get("title") or ""
        sku = (a or b).get("sku") or ""
        movers.append({
            "asin": asin,
            "sku": sku,
            "product": _short(title),
            "image": img_map.get(asin, ""),
            "sales_now": round(sales_now, 2),
            "sales_prev": round(sales_prev, 2),
            "units_now": a["units"] if a else 0,
            "units_prev": b["units"] if b else 0,
            "delta_abs": round(delta, 2),
            "delta_pct": round(pct, 4) if pct is not None else None,
        })

    # Gainers/losers = EXISTING SKUs only (sold in both windows), so genuine
    # improvements/declines aren't drowned out by brand-new SKUs (0 → X), which
    # get their own list.
    gainers = sorted([m for m in movers if m["sales_prev"] > 0 and m["delta_abs"] > 0],
                     key=lambda m: m["delta_abs"], reverse=True)[:top_n]
    losers = sorted([m for m in movers if m["sales_now"] > 0 and m["delta_abs"] < 0],
                    key=lambda m: m["delta_abs"])[:top_n]
    new_products = sorted(
        [m for m in movers if m["sales_prev"] == 0 and m["sales_now"] > 0],
        key=lambda m: m["sales_now"], reverse=True)[:top_n]
    dropped = sorted(
        [m for m in movers if m["sales_now"] == 0 and m["sales_prev"] > 0],
        key=lambda m: m["sales_prev"], reverse=True)[:top_n]

    return {
        "gainers": gainers,
        "losers": losers,
        "new_products": new_products,
        "dropped_products": dropped,
    }


# ── Theme movers (curated gift-niche themes, month vs previous month) ──────────
# NOTE: theme performance is sourced from the orders table via db.themes and is
# always across ALL marketplaces (like the Themes tab), independent of the
# summary's marketplace scope.

def _month_window(year, mnum):
    import datetime as _d
    start = _d.date(year, mnum, 1)
    end = _d.date(year + 1, 1, 1) if mnum == 12 else _d.date(year, mnum + 1, 1)
    return start.isoformat(), end.isoformat()


def compute_theme_movers(ym: str, top_n=6) -> dict:
    """Per-theme revenue this month vs previous month, with top movers.
    All-marketplaces (orders table). Returns {} if themes/orders unavailable."""
    try:
        from db.themes import get_theme_performance
    except Exception:
        return {"all": [], "gainers": [], "losers": []}

    year, mnum = int(ym[:4]), int(ym[5:7])
    prev_y, prev_num = (year - 1, 12) if mnum == 1 else (year, mnum - 1)
    cur_s, cur_e = _month_window(year, mnum)
    prev_s, prev_e = _month_window(prev_y, prev_num)

    try:
        cur = {t["theme"]: t for t in get_theme_performance(start=cur_s, end=cur_e)}
        prev = {t["theme"]: t for t in get_theme_performance(start=prev_s, end=prev_e)}
    except Exception:
        return {"all": [], "gainers": [], "losers": []}

    rows = []
    for key in set(cur) | set(prev):
        c = cur.get(key)
        p = prev.get(key)
        rev_now = float(c["revenue"]) if c else 0.0
        rev_prev = float(p["revenue"]) if p else 0.0
        delta = rev_now - rev_prev
        pct = (delta / rev_prev) if rev_prev > 0 else None
        rows.append({
            "theme": (c or p)["label"],
            "revenue_now": round(rev_now, 0),
            "revenue_prev": round(rev_prev, 0),
            "rev_per_sku": (c["rev_per_sku"] if c else 0),
            "n_skus": (c["n_skus"] if c else 0),
            "margin_pct": (c["margin_pct"] if c else (p["margin_pct"] if p else 0)),
            "yoy_pct": (c["growth_pct"] if c else None),
            "delta_abs": round(delta, 0),
            "delta_pct": round(pct, 4) if pct is not None else None,
        })

    rows.sort(key=lambda r: r["revenue_now"], reverse=True)
    gainers = sorted([r for r in rows if r["delta_abs"] > 0],
                     key=lambda r: r["delta_abs"], reverse=True)[:top_n]
    losers = sorted([r for r in rows if r["delta_abs"] < 0],
                    key=lambda r: r["delta_abs"])[:top_n]
    return {"all": rows, "gainers": gainers, "losers": losers}


# ── Public API ────────────────────────────────────────────────────────────────

# Metric display order + whether higher = worse (cost)
_METRIC_DEFS = [
    ("sales",       "💵 Total Income (Sales)", False),
    ("net_payout",  "✅ Net Payout",           False),
    ("profit",      "📈 Est. Profit (after COGS)", False),
    ("cogs",        "🏭 COGS",                 True),
    ("advertising", "📢 Advertising",          True),
    ("fees",        "🏦 Amazon Fees",          True),
    ("refunds",     "🔁 Refunds",              True),
    ("storage",     "📦 Storage",              True),
    ("lt_storage",  "📦 Long-term Storage",    True),
    ("coupons",     "🏷️ Coupons",              True),
    ("orders",      "🧾 Orders",               False),
]


def _delta(new, old):
    abs_d = new - old
    pct = (abs_d / old) if old not in (0, None) else None
    return abs_d, pct


def build_summary_stats(conn, ym: str, mps: list[str], threshold_pct=0.40) -> dict:
    """Deterministic month summary. `ym` = 'YYYY-MM'. Never calls the LLM."""
    fx = _load_fx(conn)
    year, mnum = int(ym[:4]), int(ym[5:7])
    month_name = MONTHS[mnum - 1]

    prev_y, prev_num = (year - 1, 12) if mnum == 1 else (year, mnum - 1)
    prev_name = MONTHS[prev_num - 1]
    ly_y, ly_name = year - 1, month_name

    cur = _period_totals(conn, year, month_name, mps, fx)
    prev = _period_totals(conn, prev_y, prev_name, mps, fx)
    ly = _period_totals(conn, ly_y, ly_name, mps, fx)

    metrics = []
    for key, label, is_cost in _METRIC_DEFS:
        mom_abs, mom_pct = _delta(cur[key], prev[key])
        yoy_abs, yoy_pct = _delta(cur[key], ly[key])

        def flag(pct):
            if pct is None or abs(pct) < threshold_pct:
                return None
            worse = (pct > 0) == is_cost
            return "bad" if worse else "good"

        metrics.append({
            "key": key, "label": label, "is_cost": is_cost,
            "current": round(cur[key], 2),
            "previous": round(prev[key], 2),
            "last_year": round(ly[key], 2),
            "mom_abs": round(mom_abs, 2),
            "mom_pct": round(mom_pct, 4) if mom_pct is not None else None,
            "yoy_abs": round(yoy_abs, 2),
            "yoy_pct": round(yoy_pct, 4) if yoy_pct is not None else None,
            "flag_mom": flag(mom_pct),
            "flag_yoy": flag(yoy_pct),
        })

    # Product movers (ASIN-based, from orders, all-marketplaces).
    img_map = {}
    try:
        from db.productions import get_asin_image_map
        img_map = get_asin_image_map()
    except Exception:
        img_map = {}
    cur_s, cur_e = _month_window(year, mnum)
    prev_s, prev_e = _month_window(prev_y, prev_num)
    ly_s, ly_e = _month_window(ly_y, mnum)   # same month, one year earlier
    movers = _product_movers(conn, cur_s, cur_e, prev_s, prev_e, fx, img_map, top_n=5)
    movers_yoy = _product_movers(conn, cur_s, cur_e, ly_s, ly_e, fx, img_map, top_n=5)
    theme_movers = compute_theme_movers(ym, top_n=6)

    return {
        "ym": ym,
        "month_label": f"{month_name} {year}",
        "prev_label": f"{prev_name} {prev_y}",
        "last_year_label": f"{ly_name} {ly_y}",
        "marketplaces": sorted(mps),
        "threshold_pct": threshold_pct,
        "cogs_available": cur["cogs"] > 0,
        "metrics": metrics,
        "product_movers": movers,
        "product_movers_yoy": movers_yoy,
        "theme_movers": theme_movers,
    }
