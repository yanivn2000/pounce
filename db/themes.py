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


# ── Editable themes (DB-backed) ─────────────────────────────────────────────────
def _product_names(conn) -> dict:
    """ASIN(upper) → display name, from catalog first then order titles."""
    names = {r[0].upper(): (r[1] or "")
             for r in conn.execute(
                 "SELECT asin, name FROM products_catalog WHERE asin IS NOT NULL AND asin!=''"
             ).fetchall()}
    for asin, title in conn.execute(
        "SELECT UPPER(asin), MAX(title) FROM orders "
        "WHERE asin IS NOT NULL AND asin!='' GROUP BY UPPER(asin)"
    ).fetchall():
        names.setdefault(asin, title or "")
    return names


def init_theme_tables(conn) -> None:
    """Create themes/theme_skus and seed once from the keyword auto-grouping."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS themes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            label      TEXT,
            sort_order INTEGER DEFAULT 99,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS theme_skus (
            theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
            asin     TEXT NOT NULL,
            PRIMARY KEY (theme_id, asin)
        );
    """)
    conn.commit()
    if conn.execute("SELECT 1 FROM themes LIMIT 1").fetchone():
        return  # already seeded
    names = _product_names(conn)
    for i, (key, (label, words)) in enumerate(THEME_GROUPS.items()):
        cur = conn.execute(
            "INSERT INTO themes(name, label, sort_order) VALUES(?,?,?)", (key, label, i)
        )
        tid = cur.lastrowid
        for asin, nm in names.items():
            if any(w in (nm or "").lower() for w in words):
                conn.execute(
                    "INSERT OR IGNORE INTO theme_skus(theme_id, asin) VALUES(?,?)",
                    (tid, asin.upper()),
                )
    conn.commit()


def get_theme_membership(conn) -> dict:
    """{theme_id: {'name', 'label', 'asins': set()}} in sort order."""
    rows = conn.execute("""
        SELECT t.id, t.name, t.label, t.sort_order, ts.asin
        FROM themes t
        LEFT JOIN theme_skus ts ON ts.theme_id = t.id
        ORDER BY t.sort_order, t.name
    """).fetchall()
    out = {}
    for tid, name, label, _so, asin in rows:
        d = out.setdefault(tid, {"name": name, "label": label or name, "asins": set()})
        if asin:
            d["asins"].add(asin.upper())
    return out


# CRUD (each manages its own connection)
def get_themes() -> list[dict]:
    conn = get_conn(); init_theme_tables(conn)
    rows = conn.execute("""
        SELECT t.id, t.name, t.label, t.sort_order, COUNT(ts.asin) AS n
        FROM themes t LEFT JOIN theme_skus ts ON ts.theme_id = t.id
        GROUP BY t.id ORDER BY t.sort_order, t.name
    """).fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "label": r[2] or r[1],
             "sort_order": r[3], "n_skus": r[4]} for r in rows]


def get_all_products() -> list[tuple]:
    """[(ASIN, name)] for every product (catalog ∪ sold), sorted by name."""
    conn = get_conn()
    names = _product_names(conn)
    conn.close()
    return sorted(names.items(), key=lambda kv: (kv[1] or kv[0]).lower())


def get_theme_skus(theme_id: int) -> list[str]:
    conn = get_conn(); init_theme_tables(conn)
    rows = conn.execute(
        "SELECT asin FROM theme_skus WHERE theme_id=? ORDER BY asin", (theme_id,)
    ).fetchall()
    conn.close()
    return [r[0].upper() for r in rows]


def create_theme(name: str, label: str = None) -> str:
    name = (name or "").strip()
    if not name:
        return "error: name required"
    conn = get_conn(); init_theme_tables(conn)
    try:
        nxt = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM themes").fetchone()[0]
        conn.execute("INSERT INTO themes(name, label, sort_order) VALUES(?,?,?)",
                     (name, (label or "").strip() or name, nxt))
        conn.commit(); conn.close(); return "saved"
    except Exception as e:
        conn.close()
        return "duplicate" if "UNIQUE" in str(e) else f"error: {e}"


def rename_theme(theme_id: int, name: str, label: str = None) -> str:
    name = (name or "").strip()
    if not name:
        return "error: name required"
    conn = get_conn()
    try:
        conn.execute("UPDATE themes SET name=?, label=? WHERE id=?",
                     (name, (label or "").strip() or name, theme_id))
        conn.commit(); conn.close(); return "saved"
    except Exception as e:
        conn.close()
        return "duplicate" if "UNIQUE" in str(e) else f"error: {e}"


def delete_theme(theme_id: int) -> None:
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM theme_skus WHERE theme_id=?", (theme_id,))
        conn.execute("DELETE FROM themes WHERE id=?", (theme_id,))
    conn.close()


def set_theme_skus(theme_id: int, asins: list) -> None:
    """Replace a theme's SKU membership with the given ASIN list."""
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM theme_skus WHERE theme_id=?", (theme_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO theme_skus(theme_id, asin) VALUES(?,?)",
            [(theme_id, a.upper()) for a in asins],
        )
    conn.close()


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

    init_theme_tables(conn)
    membership = get_theme_membership(conn)   # {theme_id: {name,label,asins}}

    cur   = _orders_by_asin(conn, p_start, p_end)
    prior = _orders_by_asin(conn, yoy_start, yoy_end)

    cost = {r[0].upper(): float(r[1] or 0)
            for r in conn.execute("SELECT asin, cost_usd FROM sellerboard_cogs").fetchall()}
    conn.close()

    # Aggregate per theme using its curated SKU membership
    out = []
    for tid, m in membership.items():
        a = {"label": m["label"], "skus": set(), "units": 0, "revenue": 0.0,
             "cogs": 0.0, "prior_revenue": 0.0}
        for asin in m["asins"]:
            d = cur.get(asin)
            if not d:
                continue   # member made no sales this period
            a["skus"].add(asin)
            a["units"] += d["units"]
            a["revenue"] += d["revenue_usd"]
            a["cogs"] += cost.get(asin, 0.0) * d["units"]
            a["prior_revenue"] += prior.get(asin, {}).get("revenue_usd", 0.0)

        n = len(a["skus"])
        if n == 0:
            continue
        k = m["name"]
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
