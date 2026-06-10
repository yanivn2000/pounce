"""
db/bundles.py — Import and query Amazon Bundle Performance reports.

Amazon Bundle CSV columns:
    DATE, BUNDLE_ASIN, TITLE, IS_VIRTUAL_MULTIPACK, BUNDLES_SOLD, TOTAL_SALES
"""

import pandas as pd
from db.database import get_conn


# ── Column name mapping ────────────────────────────────────────────────────────
_COL_MAP = {
    "date":                   "sale_date",
    "bundle_asin":            "bundle_asin",
    "title":                  "title",
    "is_virtual_multipack":   "is_virtual",
    "bundles_sold":           "bundles_sold",
    "total_sales":            "total_sales",
}


def import_bundle_csv(file_obj) -> tuple[int, list[str]]:
    """
    Parse an Amazon Bundle Performance CSV and upsert into bundle_sales.
    Returns (rows_imported, warnings).
    """
    try:
        sample = file_obj.read(4096)
        if isinstance(sample, bytes):
            sample = sample.decode("utf-8", errors="replace")
        file_obj.seek(0)
        first_line = sample.split("\n")[0]
        sep = "\t" if first_line.count("\t") >= first_line.count(",") else ","
        df = pd.read_csv(file_obj, dtype=str, sep=sep)
    except Exception as e:
        return 0, [f"Failed to read CSV: {e}"]

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns={k: v for k, v in _COL_MAP.items() if k in df.columns})

    required = {"sale_date", "bundle_asin", "bundles_sold", "total_sales"}
    missing = required - set(df.columns)
    if missing:
        return 0, [f"Missing columns: {missing}. Found: {list(df.columns)}"]

    warnings = []

    # Normalise types
    df["sale_date"] = pd.to_datetime(df["sale_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    bad_dates = df["sale_date"].isna().sum()
    if bad_dates:
        warnings.append(f"{bad_dates} rows had unparseable dates and were skipped.")
    df = df.dropna(subset=["sale_date"])

    df["bundle_asin"]  = df["bundle_asin"].str.strip().str.upper()
    df["bundles_sold"] = pd.to_numeric(df["bundles_sold"], errors="coerce").fillna(0).astype(int)
    df["total_sales"]  = pd.to_numeric(df["total_sales"],  errors="coerce").fillna(0)
    df["is_virtual"]   = (df.get("is_virtual", "N").str.strip().str.upper() == "Y").astype(int) \
                         if "is_virtual" in df.columns else 0

    if "title" not in df.columns:
        df["title"] = None

    conn = get_conn()
    imported = 0
    with conn:
        for _, row in df.iterrows():
            try:
                conn.execute("""
                    INSERT INTO bundle_sales
                        (sale_date, bundle_asin, title, is_virtual, bundles_sold, total_sales)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(sale_date, bundle_asin) DO UPDATE SET
                        title        = COALESCE(excluded.title, title),
                        is_virtual   = excluded.is_virtual,
                        bundles_sold = excluded.bundles_sold,
                        total_sales  = excluded.total_sales
                """, (
                    row["sale_date"],
                    row["bundle_asin"],
                    row.get("title"),
                    int(row["is_virtual"]) if not isinstance(row["is_virtual"], int) else row["is_virtual"],
                    int(row["bundles_sold"]),
                    float(row["total_sales"]),
                ))
                imported += 1
            except Exception as e:
                warnings.append(f"Row skipped ({row.get('bundle_asin')} {row.get('sale_date')}): {e}")

    conn.close()
    return imported, warnings


def count_bundle_rows() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM bundle_sales").fetchone()[0]
    conn.close()
    return n


def get_bundle_date_range() -> tuple[str, str]:
    conn = get_conn()
    row = conn.execute(
        "SELECT MIN(sale_date), MAX(sale_date) FROM bundle_sales"
    ).fetchone()
    conn.close()
    return (row[0] or "—", row[1] or "—")


def get_bundle_summary() -> pd.DataFrame:
    """
    Per-ASIN totals: bundles_sold, total_sales, avg_price, first_sale, last_sale.
    """
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT bundle_asin,
               MAX(title)          AS title,
               SUM(bundles_sold)   AS total_units,
               SUM(total_sales)    AS total_revenue,
               ROUND(SUM(total_sales)/MAX(SUM(bundles_sold)) OVER (), 2) AS pct_of_total,
               MIN(sale_date)      AS first_sale,
               MAX(sale_date)      AS last_sale
        FROM bundle_sales
        GROUP BY bundle_asin
        ORDER BY total_units DESC
    """, conn)
    conn.close()
    return df


def get_bundle_units_matrix(days: int = 90) -> pd.DataFrame:
    """
    Daily pivot: bundle_asin × sale_date, values = bundles_sold.
    Also includes a Total column.
    """
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT sale_date, bundle_asin, MAX(title) AS title, SUM(bundles_sold) AS bundles_sold
        FROM bundle_sales
        WHERE sale_date >= date('now', '-' || ? || ' days')
        GROUP BY sale_date, bundle_asin
        ORDER BY sale_date DESC
    """, conn, params=[days])
    conn.close()

    if df.empty:
        return df

    pivot = df.pivot_table(
        index=["bundle_asin", "title"], columns="sale_date",
        values="bundles_sold", aggfunc="sum", fill_value=0,
    )
    pivot.columns.name = None
    pivot = pivot.reset_index()

    date_cols = sorted([c for c in pivot.columns if c not in ("bundle_asin", "title")], reverse=True)
    pivot["Total"] = pivot[date_cols].sum(axis=1)
    return pivot[["bundle_asin", "title", "Total"] + date_cols]


def get_bundle_revenue_matrix(days: int = 90) -> pd.DataFrame:
    """
    Daily pivot: bundle_asin × sale_date, values = total_sales (revenue).
    """
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT sale_date, bundle_asin, MAX(title) AS title, SUM(total_sales) AS total_sales
        FROM bundle_sales
        WHERE sale_date >= date('now', '-' || ? || ' days')
        GROUP BY sale_date, bundle_asin
        ORDER BY sale_date DESC
    """, conn, params=[days])
    conn.close()

    if df.empty:
        return df

    pivot = df.pivot_table(
        index=["bundle_asin", "title"], columns="sale_date",
        values="total_sales", aggfunc="sum", fill_value=0,
    )
    pivot.columns.name = None
    pivot = pivot.reset_index()

    date_cols = sorted([c for c in pivot.columns if c not in ("bundle_asin", "title")], reverse=True)
    pivot["Total"] = pivot[date_cols].sum(axis=1).round(2)
    return pivot[["bundle_asin", "title", "Total"] + date_cols]


def get_bundle_daily_trend(days: int = 90) -> pd.DataFrame:
    """
    Daily totals across ALL bundles: sale_date, bundles_sold, total_sales.
    Used for the trend line chart.
    """
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT sale_date,
               SUM(bundles_sold) AS bundles_sold,
               ROUND(SUM(total_sales), 2) AS total_sales
        FROM bundle_sales
        WHERE sale_date >= date('now', '-' || ? || ' days')
        GROUP BY sale_date
        ORDER BY sale_date
    """, conn, params=[days])
    conn.close()
    return df


def get_bundle_per_asin_trend(days: int = 90) -> pd.DataFrame:
    """
    Daily bundles_sold per ASIN — for multi-line chart.
    """
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT sale_date, bundle_asin, SUM(bundles_sold) AS bundles_sold
        FROM bundle_sales
        WHERE sale_date >= date('now', '-' || ? || ' days')
        GROUP BY sale_date, bundle_asin
        ORDER BY sale_date
    """, conn, params=[days])
    conn.close()
    return df


def clear_all_bundles() -> int:
    conn = get_conn()
    with conn:
        n = conn.execute("DELETE FROM bundle_sales").rowcount
    conn.close()
    return n
