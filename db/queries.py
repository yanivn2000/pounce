"""
db/queries.py — Aggregation queries for the Sales Dashboard.
"""

import pandas as pd
from db.database import get_conn


def get_daily_sales(marketplace: str = None, days: int = 30,
                    include_pending: bool = False) -> pd.DataFrame:
    """
    Returns daily units + revenue per ASIN for the last N days.
    When marketplace is specified: one row per (date, asin, marketplace).
    When marketplace is None (all): aggregated by (date, asin) across all marketplaces,
    title preferred from amazon.com.
    include_pending: when True, Pending orders are counted alongside Shipped ones.
    """
    conn = get_conn()
    _status_filter = "AND order_status NOT IN ('Cancelled')" if include_pending else "AND order_status NOT IN ('Cancelled', 'Pending')"
    if marketplace:
        sql = f"""
            SELECT order_date, asin,
                   MAX(title) AS title,
                   marketplace,
                   SUM(quantity)   AS units,
                   SUM(item_price) AS revenue
            FROM orders
            WHERE marketplace = ?
              AND order_date >= date('now', '-' || ? || ' days')
              {_status_filter}
            GROUP BY order_date, asin, marketplace
            ORDER BY order_date DESC, units DESC
        """
        params = [marketplace, days]
    else:
        sql = f"""
            SELECT order_date, asin,
                   COALESCE(
                       MAX(CASE WHEN marketplace = 'amazon.com' THEN title END),
                       MAX(title)
                   ) AS title,
                   SUM(quantity)   AS units,
                   SUM(item_price) AS revenue
            FROM orders
            WHERE order_date >= date('now', '-' || ? || ' days')
              {_status_filter}
            GROUP BY order_date, asin
            ORDER BY order_date DESC, units DESC
        """
        params = [days]

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()

    # Normalise titles: same ASIN must get one consistent title across all dates,
    # otherwise pivot_table(index=["asin","title"]) creates duplicate ASIN rows
    # (e.g. English title on amazon.com days, German title on amazon.de-only days).
    # Prefer amazon.com title; fall back to the most common title for that ASIN.
    if not df.empty and "title" in df.columns:
        _com_titles = (
            df[df["marketplace"] == "amazon.com"].groupby("asin")["title"].first()
            if "marketplace" in df.columns else pd.Series(dtype=str)
        )
        _any_title = df.groupby("asin")["title"].agg(
            lambda x: x.dropna().mode().iloc[0] if not x.dropna().empty else ""
        )
        _title_map = _any_title.to_dict()
        _title_map.update(_com_titles.dropna().to_dict())   # amazon.com wins
        df["title"] = df["asin"].map(_title_map).fillna("")

    return df


def get_sales_matrix(marketplace: str = None, days: int = 30,
                     include_pending: bool = False) -> pd.DataFrame:
    """Daily pivot: ASIN × Date, values = revenue (kept for legacy use)."""
    df = get_daily_sales(marketplace=marketplace, days=days, include_pending=include_pending)
    if df.empty:
        return df
    pivot = df.pivot_table(
        index=["asin", "title"], columns="order_date",
        values="revenue", aggfunc="sum", fill_value=0,
    )
    pivot.columns.name = None
    pivot = pivot.reset_index()
    date_cols = sorted([c for c in pivot.columns if c not in ("asin", "title")], reverse=True)
    return pivot[["asin", "title"] + date_cols]


def get_units_matrix(marketplace: str = None, days: int = 30,
                     include_pending: bool = False) -> pd.DataFrame:
    """Daily pivot: ASIN × Date, values = units sold."""
    df = get_daily_sales(marketplace=marketplace, days=days, include_pending=include_pending)
    if df.empty:
        return df
    pivot = df.pivot_table(
        index=["asin", "title"], columns="order_date",
        values="units", aggfunc="sum", fill_value=0,
    )
    pivot.columns.name = None
    pivot = pivot.reset_index()
    date_cols = sorted([c for c in pivot.columns if c not in ("asin", "title")], reverse=True)
    return pivot[["asin", "title"] + date_cols]


def get_weekly_units_matrix(marketplace: str = None, weeks: int = 8,
                            include_pending: bool = False) -> pd.DataFrame:
    """Weekly pivot: ASIN × week_start, values = units sold."""
    df = get_weekly_summary(marketplace=marketplace, weeks=weeks, include_pending=include_pending)
    if df.empty:
        return df
    pivot = df.pivot_table(
        index=["asin", "title"], columns="week_start",
        values="units", aggfunc="sum", fill_value=0,
    )
    pivot.columns.name = None
    pivot = pivot.reset_index()
    date_cols = sorted([c for c in pivot.columns if c not in ("asin", "title")], reverse=True)
    return pivot[["asin", "title"] + date_cols]


def get_weekly_units_matrix_yoy(marketplace: str = None, weeks: int = 8,
                                include_pending: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (current_pivot, ly_pivot) both ASIN × week_start with units.
    LY uses +364 days shift (52 exact weeks) to align same weekday.
    current_pivot columns are this year's week_starts.
    ly_pivot columns are the same week_starts (shifted from last year's data).
    """
    conn = get_conn()
    days = weeks * 7
    mkt_clause = "AND marketplace = ?" if marketplace else ""
    title_expr = ("MAX(title)" if marketplace else
                  "COALESCE(MAX(CASE WHEN marketplace='amazon.com' THEN title END), MAX(title))")
    _sf = "AND order_status NOT IN ('Cancelled')" if include_pending else "AND order_status NOT IN ('Cancelled', 'Pending')"

    # Current period
    sql_cur = f"""
        SELECT date(order_date, 'weekday 1', '-7 days') AS week_start,
               asin, {title_expr} AS title, SUM(quantity) AS units
        FROM orders
        WHERE order_date >= date('now', '-' || ? || ' days')
          {_sf}
          {mkt_clause}
        GROUP BY week_start, asin
    """
    params_cur = [days] + ([marketplace] if marketplace else [])
    df_cur = pd.read_sql_query(sql_cur, conn, params=params_cur)

    # Last year: shift dates forward 364 days so week labels match current year
    sql_ly = f"""
        SELECT date(date(order_date, '+364 days'), 'weekday 1', '-7 days') AS week_start,
               asin, {title_expr} AS title, SUM(quantity) AS units
        FROM orders
        WHERE order_date >= date('now', '-' || ? || ' days', '-364 days')
          AND order_date <  date('now', '-364 days')
          {_sf}
          {mkt_clause}
        GROUP BY week_start, asin
    """
    params_ly = [days] + ([marketplace] if marketplace else [])
    df_ly = pd.read_sql_query(sql_ly, conn, params=params_ly)
    conn.close()

    def _pivot(df):
        if df.empty:
            return df
        p = df.pivot_table(
            index=["asin", "title"], columns="week_start",
            values="units", aggfunc="sum", fill_value=0,
        )
        p.columns.name = None
        p = p.reset_index()
        date_cols = sorted([c for c in p.columns if c not in ("asin", "title")], reverse=True)
        return p[["asin", "title"] + date_cols]

    return _pivot(df_cur), _pivot(df_ly)


def get_weekly_summary(marketplace: str = None, weeks: int = 8,
                       include_pending: bool = False) -> pd.DataFrame:
    """
    Weekly units + revenue per ASIN.
    When marketplace specified: grouped by (week, asin, marketplace).
    When None (all): grouped by (week, asin), title from amazon.com preferred.
    """
    conn = get_conn()
    days = weeks * 7
    _sf = "AND order_status NOT IN ('Cancelled')" if include_pending else "AND order_status NOT IN ('Cancelled', 'Pending')"
    if marketplace:
        sql = f"""
            SELECT date(order_date, 'weekday 1', '-7 days') AS week_start,
                   asin, MAX(title) AS title, marketplace,
                   SUM(quantity) AS units, SUM(item_price) AS revenue
            FROM orders
            WHERE marketplace = ?
              AND order_date >= date('now', '-' || ? || ' days')
              {_sf}
            GROUP BY week_start, asin, marketplace
            ORDER BY week_start DESC, units DESC
        """
        params = [marketplace, days]
    else:
        sql = f"""
            SELECT date(order_date, 'weekday 1', '-7 days') AS week_start,
                   asin,
                   COALESCE(
                       MAX(CASE WHEN marketplace = 'amazon.com' THEN title END),
                       MAX(title)
                   ) AS title,
                   SUM(quantity) AS units, SUM(item_price) AS revenue
            FROM orders
            WHERE order_date >= date('now', '-' || ? || ' days')
              {_sf}
            GROUP BY week_start, asin
            ORDER BY week_start DESC, units DESC
        """
        params = [days]
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def search_orders_by_address(
    name: str = "",
    address: str = "",
    zip_code: str = "",
    country: str = "",
    date_from: str = "",
    date_to: str = "",
    marketplace: str = "",
) -> pd.DataFrame:
    """
    Full-text order lookup by shipping address / recipient.
    All parameters are optional — only non-empty values are applied as filters.
    Returns one row per order line with order details + shipping address.
    """
    conn = get_conn()
    clauses = []
    params = []

    if name.strip():
        clauses.append("(LOWER(ship_name) LIKE ? OR LOWER(buyer_name) LIKE ?)")
        _n = f"%{name.strip().lower()}%"
        params += [_n, _n]

    if address.strip():
        clauses.append("(LOWER(ship_address_1) LIKE ? OR LOWER(ship_city) LIKE ? OR LOWER(ship_state) LIKE ?)")
        _a = f"%{address.strip().lower()}%"
        params += [_a, _a, _a]

    if zip_code.strip():
        clauses.append("ship_postal_code LIKE ?")
        params.append(f"%{zip_code.strip()}%")

    if country.strip():
        # Accept 2-letter code (US, GB, CA…) or full country name fragment
        clauses.append("LOWER(ship_country) LIKE ?")
        params.append(f"%{country.strip().lower()}%")

    if date_from.strip():
        clauses.append("order_date >= ?")
        params.append(date_from.strip())

    if date_to.strip():
        clauses.append("order_date <= ?")
        params.append(date_to.strip())

    if marketplace.strip() and marketplace.strip() != "all":
        clauses.append("marketplace = ?")
        params.append(marketplace.strip())

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = f"""
        SELECT order_id, order_date, asin, title, marketplace,
               quantity, item_price, currency, order_status,
               ship_name, buyer_name, ship_address_1, ship_city,
               ship_state, ship_postal_code, ship_country, buyer_email
        FROM orders
        {where}
        ORDER BY order_date DESC, order_id
        LIMIT 500
    """
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def get_recommendations_history(marketplace: str = None) -> pd.DataFrame:
    """All saved placement recommendations, newest first."""
    conn = get_conn()
    if marketplace and marketplace != "all":
        sql = """
            SELECT * FROM recommendations
            WHERE marketplace = ?
            ORDER BY date_given DESC
        """
        params = [marketplace]
    else:
        sql = "SELECT * FROM recommendations ORDER BY date_given DESC"
        params = []

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()

    # Same title-normalisation as get_daily_sales to prevent duplicate ASIN rows
    if not df.empty and "title" in df.columns:
        _com_titles = (
            df[df["marketplace"] == "amazon.com"].groupby("asin")["title"].first()
            if "marketplace" in df.columns else pd.Series(dtype=str)
        )
        _any_title = df.groupby("asin")["title"].agg(
            lambda x: x.dropna().mode().iloc[0] if not x.dropna().empty else ""
        )
        _title_map = _any_title.to_dict()
        _title_map.update(_com_titles.dropna().to_dict())
        df["title"] = df["asin"].map(_title_map).fillna("")

    return df


def get_noted_recommendations(marketplace: str = None) -> pd.DataFrame:
    """
    Return all recommendation rows that have a non-empty note,
    newest first.  Used in History tab as a lightweight change-log
    (notes = documentation that a change was made, without needing
    a separate bid_changes entry).
    """
    conn = get_conn()
    mp_filter = "AND marketplace = ?" if marketplace else ""
    params = [marketplace] if marketplace else []
    df = pd.read_sql_query(f"""
        SELECT
            date_given, campaign_name, placement_type, marketplace,
            recommended_action, recommended_multiplier, current_multiplier,
            reasoning, notes
        FROM recommendations
        WHERE notes IS NOT NULL AND TRIM(notes) != ''
          {mp_filter}
        ORDER BY date_given DESC, campaign_name
    """, conn, params=params)
    conn.close()
    return df


def get_change_log(asin: str = None, marketplace: str = None, days: int = 90) -> pd.DataFrame:
    conn = get_conn()
    clauses = [f"log_date >= date('now', '-{days} days')"]
    params = []
    if asin:
        clauses.append("asin = ?")
        params.append(asin)
    if marketplace and marketplace != "all":
        clauses.append("marketplace = ?")
        params.append(marketplace)

    where = " AND ".join(clauses)
    df = pd.read_sql_query(
        f"SELECT * FROM change_log WHERE {where} ORDER BY log_date DESC",
        conn, params=params
    )
    conn.close()
    return df


def get_marketplaces() -> list[str]:
    """Return list of distinct marketplaces that have order data."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT marketplace FROM orders ORDER BY marketplace"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_order_date_range() -> tuple[str, str]:
    """Return (min_date, max_date) from orders table."""
    conn = get_conn()
    row = conn.execute(
        "SELECT MIN(order_date), MAX(order_date) FROM orders"
    ).fetchone()
    conn.close()
    return (row[0] or "—", row[1] or "—")


def count_orders() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    return n
