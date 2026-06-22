"""
db/occasions.py — Occasion Runway for a gift business.

Gift mugs live and die by occasions. This maps every catalog ASIN to the
occasions it sells for (auto-inferred from the product name), computes the next
date of each occasion **per market** (Mother's Day is March in the UK, May in
the US/EU, May 10 in Mexico…), and tells you — for each upcoming occasion —
which products will run short and the latest date to reorder from the factory.

Read-only; derives everything from products_catalog + inventory_snapshots +
orders + sellerboard_cogs. No schema changes.
"""
import datetime as _dt
from .database import get_conn

# ── Date helpers ───────────────────────────────────────────────────────────────
def _easter(year: int) -> _dt.date:
    a = year % 19; b = year // 100; c = year % 100
    d = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return _dt.date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """weekday: Mon=0 … Sun=6. n=1 → first such weekday of the month."""
    first = _dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + _dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> _dt.date:
    nxt = _dt.date(year + 1, 1, 1) if month == 12 else _dt.date(year, month + 1, 1)
    last = nxt - _dt.timedelta(days=1)
    return last - _dt.timedelta(days=(last.weekday() - weekday) % 7)


# ── Marketplace → region ────────────────────────────────────────────────────────
_MP_REGION = {
    "amazon.com": "US", "amazon.ca": "CA", "amazon.co.uk": "UK", "amazon.com.au": "AU",
    "amazon.de": "DE", "amazon.fr": "FR", "amazon.es": "ES", "amazon.it": "IT",
    "amazon.nl": "NL", "amazon.com.mx": "MX", "amazon.se": "SE", "amazon.pl": "PL",
    "amazon.com.br": "BR", "amazon.ie": "IE", "amazon.be": "BE",
    # short codes (orders/inventory may store either)
    "US": "US", "CA": "CA", "UK": "UK", "AU": "AU", "DE": "DE", "FR": "FR",
    "ES": "ES", "IT": "IT", "NL": "NL", "MX": "MX", "SE": "SE", "PL": "PL",
}
_REGION_FLAG = {
    "US": "🇺🇸", "CA": "🇨🇦", "UK": "🇬🇧", "AU": "🇦🇺", "DE": "🇩🇪", "FR": "🇫🇷",
    "ES": "🇪🇸", "IT": "🇮🇹", "NL": "🇳🇱", "MX": "🇲🇽", "SE": "🇸🇪", "PL": "🇵🇱",
    "BR": "🇧🇷", "IE": "🇮🇪", "BE": "🇧🇪",
}


# Market groups for occasions that aren't worldwide.
_EN_MARKETS = {"US", "CA", "UK", "AU"}          # US-style "national days" resonate here
_DIASPORA   = {"US", "CA", "UK", "AU"}          # Indian-diaspora gifting markets

# Lunar / movable cultural dates have NO reliable formula — keep a small table and
# EXTEND IT YEARLY. Unknown years return None (no false date is ever shown).
# ⚠️ Verify these against a current Hindu calendar before relying on them.
_LUNAR = {
    "raksha_bandhan": {2025: "2025-08-09", 2026: "2026-08-28", 2027: "2027-08-26"},
    "bhai_dooj":      {2025: "2025-10-23", 2026: "2026-11-11", 2027: "2027-10-31"},
    "diwali":         {2025: "2025-10-20", 2026: "2026-11-08", 2027: "2027-10-29"},
}
def _lunar(name: str, year: int):
    s = _LUNAR.get(name, {}).get(year)
    return _dt.date.fromisoformat(s) if s else None


# ── Per-region occasion dates for a given year (return None = not in this market) ─
def _mothers_day(year, region):
    if region in ("UK", "IE"):  return _easter(year) - _dt.timedelta(days=21)  # Mothering Sun
    if region == "MX":          return _dt.date(year, 5, 10)
    if region == "ES":          return _nth_weekday(year, 5, 6, 1)             # 1st Sun May
    if region in ("FR", "SE"):  return _last_weekday(year, 5, 6)              # last Sun May
    return _nth_weekday(year, 5, 6, 2)                                         # 2nd Sun May

def _fathers_day(year, region):
    if region == "DE":          return _easter(year) + _dt.timedelta(days=39)  # Ascension
    if region in ("ES", "IT"):  return _dt.date(year, 3, 19)
    if region == "AU":          return _nth_weekday(year, 9, 6, 1)            # 1st Sun Sep
    if region == "SE":          return _nth_weekday(year, 11, 6, 2)           # 2nd Sun Nov
    return _nth_weekday(year, 6, 6, 3)                                         # 3rd Sun Jun

def _grandparents_day(year, region):
    if region == "US":          return _nth_weekday(year, 9, 0, 1) + _dt.timedelta(days=6)
    if region == "UK":          return _nth_weekday(year, 10, 6, 1)           # 1st Sun Oct
    if region == "ES":          return _dt.date(year, 7, 26)                  # Día de los Abuelos
    if region == "MX":          return _dt.date(year, 8, 28)                  # Día del Abuelo
    if region == "IT":          return _dt.date(year, 10, 2)                  # Festa dei nonni
    return None

def _valentines(year, region):  return _dt.date(year, 2, 14)
def _christmas(year, region):   return _dt.date(year, 12, 25)
def _galentines(year, region):  return _dt.date(year, 2, 13) if region in _EN_MARKETS else None
def _sweetest_day(year, region): return _nth_weekday(year, 10, 5, 3) if region in ("US", "CA") else None  # 3rd Sat Oct

# US "national days" — fire for English-language markets only
def _siblings_day(year, region): return _dt.date(year, 4, 10)  if region in _EN_MARKETS else None
def _sisters_day(year, region):  return _nth_weekday(year, 8, 6, 1) if region in _EN_MARKETS else None  # 1st Sun Aug
def _brothers_day(year, region): return _dt.date(year, 5, 24) if region in _EN_MARKETS else None
def _friends_day(year, region):  return _dt.date(year, 6, 8)  if region in _EN_MARKETS else None  # Best Friends
def _bosss_day(year, region):    return _dt.date(year, 10, 16) if region in _EN_MARKETS else None
def _nurses_day(year, region):   return _dt.date(year, 5, 6)  if region in _EN_MARKETS else None
def _teacher_day(year, region):
    # US Teacher Appreciation Day = Tuesday of the first full week of May
    return (_nth_weekday(year, 5, 6, 1) + _dt.timedelta(days=2)) if region in _EN_MARKETS else None

# Spanish / Latino
def _three_kings(year, region):  return _dt.date(year, 1, 6) if region in ("ES", "MX") else None
def _compadres(year, region):
    # Mexican Jueves de Compadres ≈ 2nd Thursday before Ash Wednesday (Easter−59).
    # ⚠️ Tradition/date varies by region — confirm locally.
    return (_easter(year) - _dt.timedelta(days=59)) if region == "MX" else None

# Indian diaspora (lunar — from table)
def _rakhi(year, region):     return _lunar("raksha_bandhan", year) if region in _DIASPORA else None
def _bhai_dooj(year, region): return _lunar("bhai_dooj", year)      if region in _DIASPORA else None
def _diwali(year, region):    return _lunar("diwali", year)         if region in _DIASPORA else None


# occasion key → (display label, date function(year, region))
OCCASIONS = {
    "three_kings":      ("👑 Three Kings (Reyes)",     _three_kings),
    "galentines":       ("💞 Galentine's Day",          _galentines),
    "valentines":       ("💘 Valentine's Day",          _valentines),
    "sweetest_day":     ("🍫 Sweetest Day",             _sweetest_day),
    "siblings_day":     ("👫 Siblings Day",             _siblings_day),
    "compadres":        ("🤝 Compadres & Comadres",     _compadres),
    "mothers_day":      ("💐 Mother's Day",             _mothers_day),
    "teacher_day":      ("🍎 Teacher Appreciation",     _teacher_day),
    "nurses_day":       ("🩺 Nurses Day",               _nurses_day),
    "brothers_day":     ("👬 Brothers Day",             _brothers_day),
    "fathers_day":      ("👔 Father's Day",             _fathers_day),
    "friends_day":      ("🫶 Best Friends Day",         _friends_day),
    "grandparents_day": ("👵 Grandparents Day",         _grandparents_day),
    "sisters_day":      ("👭 Sisters Day",              _sisters_day),
    "raksha_bandhan":   ("🪢 Raksha Bandhan (Rakhi)",   _rakhi),
    "bhai_dooj":        ("🪔 Bhai Dooj",                _bhai_dooj),
    "bosss_day":        ("💼 Boss's Day",               _bosss_day),
    "diwali":           ("🪔 Diwali",                   _diwali),
    "christmas":        ("🎄 Christmas",                _christmas),
}

# Product-name keyword → occasions it serves.
# Occasions with NO keywords here = universal gift days (apply to every product):
#   christmas, three_kings, diwali.
_THEME_KEYWORDS = {
    "mothers_day":  ["mom", "mommy", "mother", "mum", "grandma", "grandmother",
                     "nana", "abuela", "abuelita", "wife", "wifey", "mrs",
                     "madrina", "mamá", "mama"],
    "fathers_day":  ["dad", "daddy", "father", "grandpa", "grandfather", "papa",
                     "abuelo", "abuelito", "husband", "hubby", "uncle",
                     "padrino", "grandad", "grandparents"],
    "valentines":   ["husband", "wife", "mr & mrs", "mr&mrs", "mr right",
                     "mrs always", "couple", "hubby", "wifey", "anniversary",
                     "love", "boyfriend", "girlfriend", "mr mrs"],
    "sweetest_day": ["husband", "wife", "mr & mrs", "mr&mrs", "mr right",
                     "mrs always", "couple", "hubby", "wifey", "anniversary",
                     "love", "boyfriend", "girlfriend", "mr mrs"],
    "galentines":   ["sister", "bestie", "best friend", "bff", "friend",
                     "galentine", "wife", "wifey", "soul sister", "bonus sister"],
    "grandparents_day": ["grandma", "grandpa", "grandparents", "grandmother",
                         "grandfather", "abuela", "abuelo", "nana", "grandad"],
    "siblings_day": ["sister", "brother", "sibling", "big sister", "little sister",
                     "big brother", "crazy sister", "best sister", "best brother"],
    "sisters_day":  ["sister", "big sister", "little sister", "crazy sister",
                     "best sister", "bonus sister"],
    "brothers_day": ["brother", "big brother", "best brother", "bonus brother"],
    "friends_day":  ["friend", "bestie", "best friend", "bff", "bonus"],
    "bosss_day":    ["boss"],
    "teacher_day":  ["teacher", "mentor", "professor", "tutor"],
    "nurses_day":   ["nurse"],
    "compadres":    ["padrino", "madrina", "compadre", "comadre", "abuela", "abuelo"],
    "raksha_bandhan": ["sister", "brother", "sibling", "rakhi", "bhai", "behen"],
    "bhai_dooj":    ["brother", "sister", "bhai", "sibling"],
}

# Universal gift days = occasions with no keyword filter.
_UNIVERSAL = {k for k in OCCASIONS if k not in _THEME_KEYWORDS}


def tag_occasions(name: str) -> set:
    """Return the set of occasion keys a product name serves.

    Universal gift days (Christmas, Three Kings, Diwali) apply to every product;
    the rest are matched on name keywords.
    """
    low = (name or "").lower()
    keys = set(_UNIVERSAL)
    for occ, words in _THEME_KEYWORDS.items():
        if any(w in low for w in words):
            keys.add(occ)
    return keys


def get_upcoming_occasions(regions: list, horizon_days: int = 210,
                           from_date: _dt.date = None) -> list[dict]:
    """Upcoming occasions across the given regions, grouped by (occasion, date)."""
    today = from_date or _dt.date.today()
    horizon = today + _dt.timedelta(days=horizon_days)
    grouped = {}   # (occ_key, date) -> set(regions)
    for region in regions:
        for occ_key, (label, fn) in OCCASIONS.items():
            for yr in (today.year, today.year + 1):
                try:
                    d = fn(yr, region)
                except Exception:
                    d = None
                if d and today <= d <= horizon:
                    grouped.setdefault((occ_key, d), set()).add(region)
                    break   # earliest upcoming instance per region
    out = []
    for (occ_key, d), regs in grouped.items():
        label, _ = OCCASIONS[occ_key]
        out.append({
            "occasion": occ_key, "label": label, "date": d,
            "days_away": (d - today).days,
            "regions": sorted(regs),
        })
    return sorted(out, key=lambda r: r["date"])


# ── Stock + sales for the runway ────────────────────────────────────────────────
def _onhand_by_asin(conn) -> dict:
    rows = conn.execute("""
        SELECT UPPER(asin) AS asin,
               SUM(COALESCE(units_available,0)+COALESCE(units_inbound,0)
                 +COALESCE(units_reserved,0)) AS qty
        FROM inventory_snapshots
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM inventory_snapshots)
        GROUP BY UPPER(asin)
    """).fetchall()
    return {r[0]: int(r[1] or 0) for r in rows}


def _daily_sales_by_asin(conn, lookback_days: int = 30) -> dict:
    since = (_dt.date.today() - _dt.timedelta(days=lookback_days)).isoformat()
    try:
        rows = conn.execute("""
            SELECT UPPER(asin) AS asin, SUM(quantity) AS units
            FROM orders
            WHERE substr(order_date,1,10) >= ?
              AND COALESCE(order_status,'') NOT IN ('Cancelled','Pending')
            GROUP BY UPPER(asin)
        """, (since,)).fetchall()
    except Exception:
        return {}
    return {r[0]: (float(r[1] or 0) / lookback_days) for r in rows}


def build_occasion_runway(lead_days: int = 90, horizon_days: int = 210,
                          sales_lookback: int = 30) -> list[dict]:
    """For each upcoming occasion, list themed products with stock/reorder status.

    Each occasion dict: occasion, label, date, days_away, regions, order_by,
    order_status (overdue|soon|ok), items: [ {asin, name, onhand, daily, cover_days,
    stockout_date, short_for_occasion} ] sorted by risk.
    """
    conn = get_conn()
    today = _dt.date.today()

    # Markets we actually sell in (from orders)
    try:
        mkts = [r[0] for r in conn.execute(
            "SELECT DISTINCT marketplace FROM orders WHERE marketplace IS NOT NULL"
        ).fetchall()]
    except Exception:
        mkts = []
    regions = sorted({_MP_REGION.get(m, None) for m in mkts} - {None}) or ["US"]

    products = conn.execute(
        "SELECT UPPER(asin), name FROM products_catalog WHERE asin IS NOT NULL AND asin!=''"
    ).fetchall()
    onhand = _onhand_by_asin(conn)
    daily  = _daily_sales_by_asin(conn, sales_lookback)
    conn.close()

    # Pre-tag products
    tagged = []   # (asin, name, occasion_keys)
    for asin, name in products:
        tagged.append((asin, name, tag_occasions(name)))

    occasions = get_upcoming_occasions(regions, horizon_days, today)

    out = []
    for occ in occasions:
        order_by = occ["date"] - _dt.timedelta(days=lead_days)
        days_to_order = (order_by - today).days
        if days_to_order <= 0:
            order_status = "overdue"
        elif days_to_order <= 30:
            order_status = "soon"
        else:
            order_status = "ok"

        items = []
        for asin, name, keys in tagged:
            if occ["occasion"] not in keys:
                continue
            oh = onhand.get(asin, 0)
            ds = daily.get(asin, 0.0)
            # only surface products that are live (have stock or velocity)
            if oh == 0 and ds == 0:
                continue
            cover = (oh / ds) if ds > 0 else None
            stockout = (today + _dt.timedelta(days=int(cover))) if cover is not None else None
            short = bool(stockout and stockout < occ["date"])
            items.append({
                "asin": asin, "name": name, "onhand": oh,
                "daily": round(ds, 2),
                "cover_days": int(cover) if cover is not None else None,
                "stockout_date": stockout.isoformat() if stockout else None,
                "short_for_occasion": short,
            })
        # risk sort: short first, then lowest cover, then highest velocity
        items.sort(key=lambda it: (
            not it["short_for_occasion"],
            it["cover_days"] if it["cover_days"] is not None else 10**9,
            -it["daily"],
        ))
        out.append({
            **occ,
            "date": occ["date"].isoformat(),
            "order_by": order_by.isoformat(),
            "days_to_order": days_to_order,
            "order_status": order_status,
            "n_short": sum(1 for it in items if it["short_for_occasion"]),
            "items": items,
        })
    return out
