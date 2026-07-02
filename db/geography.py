"""
db/geography.py — Sales by geography (state / province / city).

Source: the orders table (ship_country / ship_state / ship_city / item_price).
Only orders that carry a shipping address are counted, so figures are a
LOWER BOUND — use geo_coverage() to show the caption. The relative
distribution (where we're strong vs weak) is reliable even so.

Country handling:
  US  → ship_country='US',  level 1 = state (2-letter → full name), drill to city
  CA  → ship_country='CA',  level 1 = province (full name),         drill to city
  UK  → ship_country='GB',  level 1 = city (UK is a single country)
"""
from .database import get_conn

_CURRENCY_TO_MP = {
    "USD": "amazon.com",   "CAD": "amazon.ca",
    "GBP": "amazon.co.uk", "EUR": "amazon.de",
    "AUD": "amazon.com.au", "SEK": "amazon.se",
    "PLN": "amazon.pl",    "MXN": "amazon.com.mx",
    "BRL": "amazon.com.br",
}

# Marketplace that represents each country (for coverage denominator)
_COUNTRY_MP = {"US": "amazon.com", "CA": "amazon.ca", "GB": "amazon.co.uk"}

_US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico",
}

_EXCL_STATUS = "('Cancelled','Pending')"


def _load_fx(conn):
    return {r[0]: r[1] for r in conn.execute(
        "SELECT marketplace, rate FROM fx_rates").fetchall()}


def _to_usd(amount, currency, fx):
    mp = _CURRENCY_TO_MP.get(currency)
    rate = fx.get(mp) if mp else None
    return amount / rate if rate else amount


def _date_clause(start, end):
    if start and end:
        return " AND substr(order_date,1,10) >= ? AND substr(order_date,1,10) <= ?", [start, end]
    return "", []


def _state_display(country, raw):
    if country == "US":
        return _US_STATES.get(raw.upper(), raw)
    return raw  # CA provinces already full names


def _city_display(raw):
    return " ".join(w.capitalize() for w in raw.split())


def geo_coverage(country, start, end):
    """Return (orders_with_address, total_orders) for the country in the period."""
    conn = get_conn()
    dc, dp = _date_clause(start, end)
    mp = _COUNTRY_MP.get(country)
    total = conn.execute(
        f"SELECT COUNT(*) FROM orders WHERE marketplace=? {dc} "
        f"AND COALESCE(order_status,'') NOT IN {_EXCL_STATUS}",
        [mp] + dp).fetchone()[0]
    with_addr = conn.execute(
        f"SELECT COUNT(*) FROM orders WHERE ship_country=? {dc} "
        f"AND COALESCE(order_status,'') NOT IN {_EXCL_STATUS}",
        [country] + dp).fetchone()[0]
    conn.close()
    return with_addr, total


def geo_sales_by_region(country, start, end):
    """Level-1 breakdown: state (US) / province (CA) / city (UK)."""
    conn = get_conn()
    fx = _load_fx(conn)
    dc, dp = _date_clause(start, end)
    region_expr = "UPPER(TRIM(ship_city))" if country == "GB" else "TRIM(ship_state)"
    rows = conn.execute(
        f"SELECT {region_expr} AS region, currency, "
        f"SUM(quantity) AS units, SUM(item_price) AS rev, COUNT(*) AS orders "
        f"FROM orders "
        f"WHERE ship_country=? {dc} "
        f"AND COALESCE(order_status,'') NOT IN {_EXCL_STATUS} "
        f"AND {region_expr} IS NOT NULL AND {region_expr}!='' "
        f"GROUP BY region, currency",
        [country] + dp).fetchall()
    conn.close()

    agg = {}
    for region, ccy, units, rev, orders in rows:
        d = agg.setdefault(region, {"units": 0, "revenue": 0.0, "orders": 0})
        d["units"] += int(units or 0)
        d["revenue"] += _to_usd(float(rev or 0), ccy, fx)
        d["orders"] += int(orders or 0)

    disp = _city_display if country == "GB" else (lambda r: _state_display(country, r))
    out = [{"key": k, "region": disp(k), "units": v["units"],
            "revenue": round(v["revenue"], 0), "orders": v["orders"]}
           for k, v in agg.items()]
    out.sort(key=lambda x: x["revenue"], reverse=True)
    _total = sum(x["revenue"] for x in out) or 1
    for x in out:
        x["share_pct"] = round(100 * x["revenue"] / _total, 1)
    return out


def geo_sales_by_city(country, region_key, start, end):
    """Cities within a US state / CA province (region_key = raw ship_state)."""
    conn = get_conn()
    fx = _load_fx(conn)
    dc, dp = _date_clause(start, end)
    rows = conn.execute(
        f"SELECT UPPER(TRIM(ship_city)) AS city, currency, "
        f"SUM(quantity) AS units, SUM(item_price) AS rev, COUNT(*) AS orders "
        f"FROM orders "
        f"WHERE ship_country=? AND TRIM(ship_state)=? {dc} "
        f"AND COALESCE(order_status,'') NOT IN {_EXCL_STATUS} "
        f"AND ship_city IS NOT NULL AND TRIM(ship_city)!='' "
        f"GROUP BY city, currency",
        [country, region_key] + dp).fetchall()
    conn.close()

    agg = {}
    for city, ccy, units, rev, orders in rows:
        d = agg.setdefault(city, {"units": 0, "revenue": 0.0, "orders": 0})
        d["units"] += int(units or 0)
        d["revenue"] += _to_usd(float(rev or 0), ccy, fx)
        d["orders"] += int(orders or 0)
    out = [{"region": _city_display(k), "units": v["units"],
            "revenue": round(v["revenue"], 0), "orders": v["orders"]}
           for k, v in agg.items()]
    out.sort(key=lambda x: x["revenue"], reverse=True)
    return out
