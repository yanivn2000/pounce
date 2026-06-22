"""
db/themes.py — Product Theme performance ("which niche drives the business").

Groups products into overlapping gift themes (grandparents, grandma, grandpa,
couples, sisters, anniversary, Spanish/Latino, new-parent, …) from their names,
then ranks each theme by units, revenue and gross profit over a period — plus
revenue-per-SKU and growth vs the prior period, which hint at what to develop
next (a theme earning a lot from few SKUs is an under-served vein to expand).

Read-only; derives from orders + sellerboard_cogs + products_catalog.
"""
import datetime as _dt
from .database import get_conn

# Rough local-currency-per-USD (theme ranking only — exact FX not needed).
_CCY_PER_USD = {
    "USD": 1.0, "": 1.0, "CAD": 1.36, "GBP": 0.79, "EUR": 0.92,
    "SEK": 10.5, "PLN": 4.0, "AED": 3.67, "SAR": 3.75, "MXN": 17.5,
    "AUD": 1.53, "BRL": 5.8, "JPY": 150.0, "INR": 83.0,
}
def _to_usd(amount, ccy):
    return float(amount or 0) / _CCY_PER_USD.get((ccy or "USD").upper(), 1.0)


# Theme key → (display label, name keywords). Overlapping by design
# (grandma ⊂ grandparents). Order roughly broad → specific.
THEME_GROUPS = {
    "grandparents": ("👵👴 Grandparents", ["grandparent", "grandma", "grandpa",
                     "grandmother", "grandfather", "nana", "grandad", "abuel"]),
    "grandma":      ("👵 Grandma", ["grandma", "grandmother", "nana", "abuela", "abuelita"]),
    "grandpa":      ("👴 Grandpa", ["grandpa", "grandfather", "grandad", "abuelo", "abuelito"]),
    "couples":      ("💑 Couples", ["mr & mrs", "mr&mrs", "mr mrs", "mr right", "mrs always",
                     "couple", "hubby", "wifey", "husband", "wife", "anniversary"]),
    "husband":      ("🤵 Husband", ["husband", "hubby"]),
    "wife":         ("👰 Wife", ["wife", "wifey"]),
    "anniversary":  ("💍 Anniversary", ["anniversary", "mr & mrs", "mr&mrs", "mr mrs"]),
    "sisters":      ("👭 Sisters", ["sister"]),
    "brothers":     ("👬 Brothers", ["brother"]),
    "mom":          ("👩 Mom", ["mom", "mommy", "mother", "mum", "mamá", "mama"]),
    "dad":          ("👨 Dad", ["dad", "daddy", "father", "papá", "papa"]),
    "uncle":        ("👨 Uncle", ["uncle", "tío", "tio"]),
    "aunt":         ("👩 Aunt", ["aunt", "auntie", "tía", "tia"]),
    "boss":         ("💼 Boss", ["boss"]),
    "new_parent":   ("🍼 New parent / Pregnancy", ["promoted to", "going to be",
                     "mommy & daddy", "mommy&daddy", "baby", "grandparents to be"]),
    "housewarming": ("🏠 Housewarming", ["housewarming", "new home"]),
    "spanish":      ("🇪🇸 Spanish / Latino", ["abuel", "mejor", "mamá", "papá",
                     "tía", "tío", "madrina", "padrino", "navidad", "español"]),
}


def tag_themes(name: str) -> set:
    low = (name or "").lower()
    return {k for k, (_, words) in THEME_GROUPS.items() if any(w in low for w in words)}


def _orders_by_asin(conn, start: str, end: str) -> dict:
    """{ASIN: {'units': int, 'revenue_usd': float}} for [start, end)."""
    rows = conn.execute("""
        SELECT UPPER(asin) AS asin, currency,
               SUM(quantity) AS units, SUM(item_price) AS revenue
        FROM orders
        WHERE substr(order_date,1,10) >= ? AND substr(order_date,1,10) < ?
          AND COALESCE(order_status,'') NOT IN ('Cancelled','Pending')
          AND asin IS NOT NULL AND asin != ''
        GROUP BY UPPER(asin), currency
    """, (start, end)).fetchall()
    out = {}
    for asin, ccy, units, rev in rows:
        d = out.setdefault(asin, {"units": 0, "revenue_usd": 0.0})
        d["units"] += int(units or 0)
        d["revenue_usd"] += _to_usd(rev, ccy)
    return out


def get_theme_performance(days: int = 365) -> list[dict]:
    """Per-theme performance over the trailing `days`, with prior-period growth.

    Each dict: theme, label, n_skus, units, revenue, cogs, gross_profit,
    margin_pct, rev_per_sku, profit_per_sku, prior_revenue, growth_pct.

    growth_pct is **year-over-year**: this period vs the SAME calendar window
    one year earlier. This removes seasonality (Q4 gift peak), which an adjacent
    period comparison would otherwise mistake for a decline. None = no sales in
    that window a year ago.
    """
    conn = get_conn()
    today = _dt.date.today()
    p_start = (today - _dt.timedelta(days=days)).isoformat()
    p_end   = today.isoformat()
    # Year-over-year baseline: same window, one year earlier
    yoy_start = (today - _dt.timedelta(days=days + 365)).isoformat()
    yoy_end   = (today - _dt.timedelta(days=365)).isoformat()

    cur   = _orders_by_asin(conn, p_start, p_end)
    prior = _orders_by_asin(conn, yoy_start, yoy_end)

    # ASIN → name (catalog first, fall back to most recent order title)
    names = {r[0].upper(): (r[1] or "")
             for r in conn.execute(
                 "SELECT asin, name FROM products_catalog WHERE asin IS NOT NULL AND asin!=''"
             ).fetchall()}
    cost = {r[0].upper(): float(r[1] or 0)
            for r in conn.execute("SELECT asin, cost_usd FROM sellerboard_cogs").fetchall()}
    missing = [a for a in cur if a not in names]
    if missing:
        ph = ",".join("?" * len(missing))
        for asin, title in conn.execute(
            f"SELECT UPPER(asin), title FROM orders WHERE UPPER(asin) IN ({ph}) "
            f"AND title IS NOT NULL GROUP BY UPPER(asin)", missing
        ).fetchall():
            names.setdefault(asin, title or "")
    conn.close()

    # Aggregate per theme (a product contributes to every theme it matches)
    agg = {k: {"label": lbl, "skus": set(), "units": 0, "revenue": 0.0,
               "cogs": 0.0, "prior_revenue": 0.0}
           for k, (lbl, _) in THEME_GROUPS.items()}

    for asin, d in cur.items():
        themes = tag_themes(names.get(asin, ""))
        units, rev = d["units"], d["revenue_usd"]
        cgs = cost.get(asin, 0.0) * units
        prev_rev = prior.get(asin, {}).get("revenue_usd", 0.0)
        for t in themes:
            a = agg[t]
            a["skus"].add(asin); a["units"] += units
            a["revenue"] += rev; a["cogs"] += cgs
            a["prior_revenue"] += prev_rev

    out = []
    for k, a in agg.items():
        n = len(a["skus"])
        if n == 0:
            continue
        gp = a["revenue"] - a["cogs"]
        out.append({
            "theme": k, "label": a["label"], "n_skus": n,
            "units": a["units"], "revenue": round(a["revenue"], 0),
            "cogs": round(a["cogs"], 0), "gross_profit": round(gp, 0),
            "margin_pct": round(100 * gp / a["revenue"], 1) if a["revenue"] else 0.0,
            "rev_per_sku": round(a["revenue"] / n, 0),
            "profit_per_sku": round(gp / n, 0),
            "prior_revenue": round(a["prior_revenue"], 0),
            "growth_pct": (round(100 * (a["revenue"] - a["prior_revenue"]) / a["prior_revenue"], 0)
                           if a["prior_revenue"] > 0 else None),
        })
    out.sort(key=lambda r: r["revenue"], reverse=True)
    return out
