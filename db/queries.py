"""
db/queries.py — Aggregation queries for the Sales Dashboard.
"""

import pandas as pd
from db.database import get_conn


def get_daily_sales(marketplace: str = None, days: int = 30) -> pd.DataFrame:
    """
    Returns daily revenue per ASIN for the last N days.
    Columns: order_date, asin, title, marketplace, units, revenue
    """
    conn = get_conn()
    market_filter = "AND marketplace = ?" if marketplace and marketplace != "all" else ""
    params = [days]
    if marketplace and marketplace != "all":
        params = [marketplace, days]
        market_filter = "AND marketplace = ?"
        sql = f"""
            SELECT
                order_date,
                asin,
                MAX(title)     AS title,
                marketplace,
                SUM(quantity)  AS units,
                SUM(item_price) AS revenue
            FROM orders
            WHERE marketplace = ?
              AND order_date >= date('now', '-' || ? || ' days')
              AND order_status NOT IN ('Cancelled', 'Pending')
            GROUP BY order_date, asin, marketplace
            ORDER BY order_date DESC, revenue DESC
        """
    else:
        sql = f"""
            SELECT
                order_date,
                asin,
                MAX(title)      AS title,
                marketplace,
                SUM(quantity)   AS units,
                SUM(item_price) AS revenue
            FROM orders
            WHERE order_date >= date('now', '-' || ? || ' days')
              AND order_status NOT IN ('Cancelled', 'Pending')
            GROUP BY order_date, asin, marketplace
            ORDER BY order_date DESC, revenue DESC
        """
        params = [days]

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def get_sales_matrix(marketplace: str = None, days: int = 30) -> pd.DataFrame:
    """
    Returns a pivot: ASIN (rows) × Date (columns), values = revenue.
    """
    df = get_daily_sales(marketplace=marketplace, days=days)
    if df.empty:
        return df

    pivot = df.pivot_table(
        index=["asin", "title"],
        columns="order_date",
        values="revenue",
        aggfunc="sum",
        fill_value=0,
    )
    pivot.columns.name = None
    pivot = pivot.reset_index()
    # Sort columns: asin, title, then dates newest first
    date_cols = sorted([c for c in pivot.columns if c not in ("asin", "title")], reverse=True)
    return pivot[["asin", "title"] + date_cols]


def get_weekly_summary(marketplace: str = None, weeks: int = 8) -> pd.DataFrame:
    """
    Weekly revenue + units per ASIN for last N weeks.
    Columns: week_start, asin, title, marketplace, units, revenue
    """
    conn = get_conn()
    market_clause = "AND marketplace = ?" if marketplace and marketplace != "all" else ""
    days = weeks * 7

    if marketplace and marketplace != "all":
        params = [marketplace, days]
        sql = f"""
            SELECT
                date(order_date, 'weekday 1', '-7 days') AS week_start,
                asin,
                MAX(title)      AS title,
                marketplace,
                SUM(quantity)   AS units,
                SUM(item_price) AS revenue
            FROM orders
            WHERE marketplace = ?
              AND order_date >= date('now', '-' || ? || ' days')
              AND order_status NOT IN ('Cancelled', 'Pending')
            GROUP BY week_start, asin, marketplace
            ORDER BY week_start DESC, revenue DESC
        """
    else:
        params = [days]
        sql = """
            SELECT
                date(order_date, 'weekday 1', '-7 days') AS week_start,
                asin,
                MAX(title)      AS title,
                marketplace,
                SUM(quantity)   AS units,
                SUM(item_price) AS revenue
            FROM orders
            WHERE order_date >= date('now', '-' || ? || ' days')
              AND order_status NOT IN ('Cancelled', 'Pending')
            GROUP BY week_start, asin, marketplace
            ORDER BY week_start DESC, revenue DESC
        """

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def get_recommendations_history(marketplace: str = None, limit: int = 200) -> pd.DataFrame:
    """All saved placement recommendations, newest first."""
    conn = get_conn()
    if marketplace and marketplace != "all":
        sql = """
            SELECT * FROM recommendations
            WHERE marketplace = ?
            ORDER BY date_given DESC
            LIMIT ?
        """
        params = [marketplace, limit]
    else:
        sql = "SELECT * FROM recommendations ORDER BY date_given DESC LIMIT ?"
        params = [limit]

    df = pd.read_sql_query(sql, conn, params=params)
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
