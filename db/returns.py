"""
db/returns.py — Amazon FBA Returns report ingestion and analytics.

Return reason buckets
---------------------
  AMAZON    — damage/loss caused by Amazon/carrier → contact Amazon
  DEFECTIVE — product defect or quality issue      → contact manufacturer
  CUSTOMER  — buyer changed mind / normal return   → no action needed
"""
from __future__ import annotations

import pandas as pd
from .database import get_conn

# ── Reason classification ────────────────────────────────────────────────────

REASON_BUCKET: dict[str, str] = {
    # Contact Amazon
    "FC_DAMAGED":               "AMAZON",
    "CARRIER_DAMAGED":          "AMAZON",
    "SWITCHEROO":               "AMAZON",
    "WAREHOUSE_DAMAGED":        "AMAZON",
    "LOST_IN_TRANSIT":          "AMAZON",
    "DAMAGED_BY_FC":            "AMAZON",
    # Contact Manufacturer
    "DEFECTIVE":                "DEFECTIVE",
    "PRODUCT_DAMAGED":          "DEFECTIVE",
    "DAMAGED":                  "DEFECTIVE",
    "EXPIRED":                  "DEFECTIVE",
    "WRONG_ITEM":               "DEFECTIVE",
    # Customer / Normal
    "CUSTOMER_RETURN":          "CUSTOMER",
    "NOT_AS_DESCRIBED":         "CUSTOMER",
    "UNDELIVERABLE":            "CUSTOMER",
    "FOUND_BETTER_PRICE":       "CUSTOMER",
    "NO_REASON_GIVEN":          "CUSTOMER",
    "ORDERED_WRONG_ITEM":       "CUSTOMER",
    "UNWANTED_ITEM":            "CUSTOMER",
    "MISSED_ESTIMATED_DELIVERY":"CUSTOMER",
}

BUCKET_LABEL = {
    "AMAZON":    "🔴 Contact Amazon",
    "DEFECTIVE": "🟠 Contact Manufacturer",
    "CUSTOMER":  "⚪ Normal Return",
}

NA_MARKETPLACES = {"amazon.com", "amazon.ca", "amazon.com.mx"}
EU_MARKETPLACES = {"amazon.co.uk", "amazon.de", "amazon.fr",
                   "amazon.it", "amazon.es", "amazon.nl", "amazon.se", "amazon.pl"}


def _region(marketplace: str) -> str:
    m = (marketplace or "").lower().strip()
    if m in EU_MARKETPLACES:
        return "EU"
    return "NA"


# ── CSV column name normalisation ────────────────────────────────────────────

_COL_MAP = {
    "return-date":          "return_date",
    "return date":          "return_date",
    "order-id":             "order_id",
    "order id":             "order_id",
    "sku":                  "sku",
    "asin":                 "asin",
    "title":                "title",
    "product name":         "title",
    "quantity":             "quantity",
    "reason":               "reason",
    "disposition":          "disposition",
    "detailed-disposition": "disposition",
    "status":               "status",
    "fulfillment-center-id":"fc_id",
    "customer-comments":    "customer_comments",
    "customer comments":    "customer_comments",
}


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower() for c in df.columns]
    return df.rename(columns={k: v for k, v in _COL_MAP.items() if k in df.columns})


# ── Upload ───────────────────────────────────────────────────────────────────

def import_returns_csv(
    df: pd.DataFrame,
    marketplace: str,
) -> tuple[int, list[str]]:
    """
    Parse and store an Amazon FBA Returns CSV for one marketplace.
    Returns (rows_imported, warnings).
    """
    df    = _norm_cols(df)
    conn  = get_conn()
    count = 0
    warns: list[str] = []
    region = _region(marketplace)

    required = {"return_date", "asin"}
    missing  = required - set(df.columns)
    if missing:
        return 0, [f"Missing columns: {', '.join(missing)}"]

    with conn:
        for _, row in df.iterrows():
            asin = str(row.get("asin") or "").strip().upper()
            if not asin or asin == "NAN":
                continue
            ret_date = str(row.get("return_date") or "").strip()[:10]
            if not ret_date:
                continue
            order_id = str(row.get("order_id") or "").strip() or None
            qty      = int(row.get("quantity") or 1)
            reason   = str(row.get("reason")      or "").strip().upper() or None
            disp     = str(row.get("disposition")  or "").strip().upper() or None
            status   = str(row.get("status")       or "").strip()         or None
            title    = str(row.get("title")        or "").strip()         or None
            sku      = str(row.get("sku")          or "").strip()         or None
            comments = str(row.get("customer_comments") or "").strip()    or None
            try:
                conn.execute("""
                    INSERT INTO amazon_returns
                        (return_date, order_id, sku, asin, title, quantity,
                         reason, disposition, status, marketplace, region, customer_comments)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(order_id, asin, return_date, marketplace) DO UPDATE SET
                        quantity=excluded.quantity,
                        reason=excluded.reason,
                        disposition=excluded.disposition,
                        status=excluded.status,
                        title=COALESCE(excluded.title, amazon_returns.title),
                        customer_comments=excluded.customer_comments
                """, (ret_date, order_id, sku, asin, title, qty,
                      reason, disp, status, marketplace, region, comments))
                count += 1
            except Exception as e:
                warns.append(str(e)[:120])

    conn.close()
    return count, warns


# ── Analytics queries ────────────────────────────────────────────────────────

def get_return_rate_report(
    region: str | None = None,
    start_date: str | None = None,
    end_date:   str | None = None,
) -> pd.DataFrame:
    """
    Return a DataFrame with one row per ASIN:
      asin, title, units_sold, units_returned, return_rate_pct,
      top_reason, amazon_cnt, defective_cnt, customer_cnt, other_cnt
    """
    conn = get_conn()

    # ── Returns aggregated by ASIN ────────────────────────────────────────
    ret_where = []
    ret_params: list = []
    if region and region != "All":
        ret_where.append("region = ?")
        ret_params.append(region)
    if start_date:
        ret_where.append("return_date >= ?")
        ret_params.append(start_date)
    if end_date:
        ret_where.append("return_date <= ?")
        ret_params.append(end_date)

    ret_sql = """
        SELECT asin,
               MAX(title) AS title,
               SUM(quantity) AS units_returned,
               reason
        FROM amazon_returns
        {where}
        GROUP BY asin, reason
    """.format(where=("WHERE " + " AND ".join(ret_where)) if ret_where else "")

    ret_rows = conn.execute(ret_sql, ret_params).fetchall()

    # ── Orders (sales) in same period/region ──────────────────────────────
    ord_where = []
    ord_params: list = []
    if region and region != "All":
        mkt_list = NA_MARKETPLACES if region == "NA" else EU_MARKETPLACES
        placeholders = ",".join("?" * len(mkt_list))
        ord_where.append(f"marketplace IN ({placeholders})")
        ord_params.extend(mkt_list)
    if start_date:
        ord_where.append("order_date >= ?")
        ord_params.append(start_date)
    if end_date:
        ord_where.append("order_date <= ?")
        ord_params.append(end_date)

    ord_sql = """
        SELECT asin, SUM(quantity) AS units_sold
        FROM orders
        {where}
        GROUP BY asin
    """.format(where=("WHERE " + " AND ".join(ord_where)) if ord_where else "")

    sold_map: dict[str, int] = {
        r["asin"]: int(r["units_sold"] or 0)
        for r in conn.execute(ord_sql, ord_params).fetchall()
    }
    conn.close()

    # ── Build per-ASIN summary ────────────────────────────────────────────
    from collections import defaultdict
    asin_data: dict[str, dict] = defaultdict(lambda: {
        "title": "", "units_returned": 0,
        "AMAZON": 0, "DEFECTIVE": 0, "CUSTOMER": 0, "OTHER": 0,
        "reason_counts": defaultdict(int),
    })

    for r in ret_rows:
        asin   = str(r["asin"] or "").upper()
        reason = str(r["reason"] or "").strip().upper()
        qty    = int(r["units_returned"] or 0)
        bucket = REASON_BUCKET.get(reason, "OTHER")

        d = asin_data[asin]
        d["title"]          = d["title"] or (r["title"] or "")
        d["units_returned"] += qty
        d[bucket]           += qty
        d["reason_counts"][reason] += qty

    rows = []
    for asin, d in asin_data.items():
        sold     = sold_map.get(asin, 0)
        returned = d["units_returned"]
        rate     = round(returned / sold * 100, 1) if sold > 0 else None
        top_reason = (
            max(d["reason_counts"], key=d["reason_counts"].get)
            if d["reason_counts"] else "—"
        )
        top_bucket = REASON_BUCKET.get(top_reason, "OTHER")
        rows.append({
            "ASIN":           asin,
            "Product":        d["title"],
            "Units Sold":     sold,
            "Returns":        returned,
            "Return Rate %":  rate,
            "Top Reason":     top_reason,
            "Action":         BUCKET_LABEL.get(top_bucket, "⚪ Normal Return"),
            "🔴 Amazon":      d["AMAZON"],
            "🟠 Mfg Defect":  d["DEFECTIVE"],
            "⚪ Customer":    d["CUSTOMER"],
            "Other":          d["OTHER"],
        })

    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    if not df.empty:
        df = df.sort_values("Return Rate %", ascending=False, na_position="last")
    return df


def get_returns_date_range() -> tuple[str | None, str | None]:
    """Return (min_date, max_date) of all stored returns."""
    conn = get_conn()
    row  = conn.execute(
        "SELECT MIN(return_date) AS mn, MAX(return_date) AS mx FROM amazon_returns"
    ).fetchone()
    conn.close()
    return (row["mn"], row["mx"]) if row else (None, None)
