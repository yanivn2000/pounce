"""
db/returns.py — Amazon FBA Returns report ingestion and analytics.

Return reason buckets
---------------------
  AMAZON    — damage/loss caused by Amazon/carrier → contact Amazon
  DEFECTIVE — product defect or quality issue      → contact manufacturer
  CUSTOMER  — buyer changed mind / normal return   → no action needed

FC auto-detection
-----------------
  Marketplace and country are derived from the Fulfillment Center ID in each
  row, so the user only needs to specify NA or EU as a region hint for the
  rare case where the FC prefix is unknown.
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
    "DAMAGED_BY_CARRIER":       "AMAZON",
    "UNDELIVERABLE_UNKNOWN":    "AMAZON",   # carrier couldn't deliver
    "UNDELIVERABLE_REFUSED":    "AMAZON",
    # Contact Manufacturer
    "DEFECTIVE":                "DEFECTIVE",
    "PRODUCT_DAMAGED":          "DEFECTIVE",
    "DAMAGED":                  "DEFECTIVE",
    "EXPIRED":                  "DEFECTIVE",
    "WRONG_ITEM":               "DEFECTIVE",
    "QUALITY_UNACCEPTABLE":     "DEFECTIVE",
    # Customer / Normal
    "CUSTOMER_RETURN":          "CUSTOMER",
    "NOT_AS_DESCRIBED":         "CUSTOMER",
    "UNDELIVERABLE":            "CUSTOMER",
    "FOUND_BETTER_PRICE":       "CUSTOMER",
    "NO_REASON_GIVEN":          "CUSTOMER",
    "ORDERED_WRONG_ITEM":       "CUSTOMER",
    "UNWANTED_ITEM":            "CUSTOMER",
    "MISSED_ESTIMATED_DELIVERY":"CUSTOMER",
    "NOT_COMPATIBLE":           "CUSTOMER",
    "CUSTOMER_DAMAGED":         "CUSTOMER",
}

BUCKET_LABEL = {
    "AMAZON":    "🔴 Contact Amazon",
    "DEFECTIVE": "🟠 Contact Manufacturer",
    "CUSTOMER":  "⚪ Normal Return",
}

# ── Marketplace / country lookup ─────────────────────────────────────────────

MARKETPLACE_TO_COUNTRY: dict[str, str] = {
    "amazon.com":    "US",
    "amazon.ca":     "CA",
    "amazon.com.mx": "MX",
    "amazon.co.uk":  "UK",
    "amazon.de":     "DE",
    "amazon.fr":     "FR",
    "amazon.it":     "IT",
    "amazon.es":     "ES",
    "amazon.nl":     "NL",
    "amazon.pl":     "PL",
    "amazon.se":     "SE",
}

COUNTRY_FLAG: dict[str, str] = {
    "US": "🇺🇸", "CA": "🇨🇦", "MX": "🇲🇽",
    "UK": "🇬🇧", "DE": "🇩🇪", "FR": "🇫🇷",
    "IT": "🇮🇹", "ES": "🇪🇸", "NL": "🇳🇱",
    "PL": "🇵🇱", "SE": "🇸🇪",
}

NA_MARKETPLACES = {"amazon.com", "amazon.ca", "amazon.com.mx"}
EU_MARKETPLACES = {"amazon.co.uk", "amazon.de", "amazon.fr",
                   "amazon.it", "amazon.es", "amazon.nl", "amazon.se", "amazon.pl"}

# ── FC → marketplace (EU prefix mapping) ─────────────────────────────────────
# Keys are the first 3 characters of the FC code (uppercase).

_EU_FC_PREFIX: dict[str, str] = {
    # United Kingdom
    "EDI": "amazon.co.uk",   # Edinburgh, Scotland
    "CWL": "amazon.co.uk",   # Swansea / Cardiff, Wales
    "LBA": "amazon.co.uk",   # Leeds Bradford, England
    "LTN": "amazon.co.uk",   # Luton, England
    "MAN": "amazon.co.uk",   # Manchester, England
    "BHX": "amazon.co.uk",   # Birmingham, England
    "STN": "amazon.co.uk",   # Stansted / Essex, England
    "EMA": "amazon.co.uk",   # East Midlands, England
    "BRS": "amazon.co.uk",   # Bristol, England
    # France
    "ORY": "amazon.fr",      # Paris Orly
    "LYS": "amazon.fr",      # Lyon
    "CDG": "amazon.fr",      # Paris CDG
    "MRS": "amazon.fr",      # Marseille
    "TLS": "amazon.fr",      # Toulouse
    "BOD": "amazon.fr",      # Bordeaux
    # Italy
    "MXP": "amazon.it",      # Milan Malpensa
    "FCO": "amazon.it",      # Rome Fiumicino
    "NAP": "amazon.it",      # Naples
    "BLQ": "amazon.it",      # Bologna
    "VRN": "amazon.it",      # Verona
    # Spain
    "MAD": "amazon.es",      # Madrid
    "BCN": "amazon.es",      # Barcelona
    "VLC": "amazon.es",      # Valencia
    "SVQ": "amazon.es",      # Seville
    "ZAZ": "amazon.es",      # Zaragoza
    # Poland
    "KTW": "amazon.pl",      # Katowice
    "LCJ": "amazon.pl",      # Łódź (Konstantynów)
    "WAW": "amazon.pl",      # Warsaw
    "WRO": "amazon.pl",      # Wrocław
    "POZ": "amazon.pl",      # Poznań
    "GDN": "amazon.pl",      # Gdańsk
    # Germany / Central EU (BTS2 is Bratislava, Slovakia — serves DE/AT/CH)
    "BTS": "amazon.de",      # Bratislava, Slovakia → Central EU
    "PRG": "amazon.de",      # Prague, Czech Republic → Central EU
    "FRA": "amazon.de",      # Frankfurt, Germany
    "MUC": "amazon.de",      # Munich, Germany
    "DUS": "amazon.de",      # Düsseldorf, Germany
    "HAJ": "amazon.de",      # Hanover, Germany
    "LEJ": "amazon.de",      # Leipzig, Germany
    "CGN": "amazon.de",      # Cologne, Germany
    "HAM": "amazon.de",      # Hamburg, Germany
    "BER": "amazon.de",      # Berlin, Germany
    "STR": "amazon.de",      # Stuttgart, Germany
    "NUE": "amazon.de",      # Nuremberg, Germany
    # Netherlands
    "AMS": "amazon.nl",      # Amsterdam
    "EIN": "amazon.nl",      # Eindhoven
    # Sweden
    "ARN": "amazon.se",      # Stockholm Arlanda
    "GOT": "amazon.se",      # Gothenburg
    "MMX": "amazon.se",      # Malmö
}


def _fc_to_marketplace(fc: str, region_hint: str = "NA") -> tuple[str, bool]:
    """
    Derive Amazon marketplace URL from Fulfillment Center ID.

    NA rule: FCs starting with 'Y' are Canadian (YVR, YYC, YYZ, YEG, …);
             all other NA FCs are US.  This is rule-based, so new NA FCs
             are always classified correctly automatically.
    EU rule: 3-letter FC prefix → country lookup.  Returns (marketplace, known)
             where known=False signals an unrecognised FC prefix.

    Returns (marketplace_url, is_known).
    """
    fc = (fc or "").strip().upper()
    if not fc:
        # No FC in the CSV row at all — use region_hint default
        default = "amazon.com" if region_hint == "NA" else "amazon.co.uk"
        return default, False

    if region_hint == "EU":
        prefix = fc[:3]
        mkt = _EU_FC_PREFIX.get(prefix)
        if mkt:
            return mkt, True
        # Unknown EU prefix — do NOT silently guess DE; store as a neutral EU marker
        return "amazon.de", False          # marketplace stored but flagged unknown
    else:  # NA — pure rule, never needs a lookup table update
        if fc.startswith("Y"):             # YVR3, YYC1, YYZ1/7/9, YEG1/2 …
            return "amazon.ca", True
        return "amazon.com", True


def _region(marketplace: str) -> str:
    m = (marketplace or "").lower().strip()
    if m in EU_MARKETPLACES:
        return "EU"
    return "NA"


# ── CSV column name normalisation ────────────────────────────────────────────

_COL_MAP = {
    "return-date":            "return_date",
    "return date":            "return_date",
    "order-id":               "order_id",
    "order id":               "order_id",
    "sku":                    "sku",
    "asin":                   "asin",
    "title":                  "title",
    "product-name":           "title",
    "product name":           "title",
    "quantity":               "quantity",
    "reason":                 "reason",
    "disposition":            "disposition",
    "detailed-disposition":   "disposition",
    "status":                 "status",
    "fulfillment-center-id":  "fc_id",
    "fulfillment center id":  "fc_id",
    "customer-comments":      "customer_comments",
    "customer comments":      "customer_comments",
}


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower() for c in df.columns]
    return df.rename(columns={k: v for k, v in _COL_MAP.items() if k in df.columns})


# ── Upload ───────────────────────────────────────────────────────────────────

def import_returns_csv(
    df: pd.DataFrame,
    region_hint: str = "NA",
    report_from: str | None = None,
    report_to:   str | None = None,
) -> tuple[int, list[str]]:
    """
    Parse and store an Amazon FBA Returns CSV.

    Marketplace and country are auto-detected from the fulfillment-center-id
    column in each row.  region_hint ("NA" or "EU") is used as a fallback for
    rows where the FC code is missing or unrecognised.

    report_from / report_to — the date range declared by the user at upload
    time; stored in returns_meta so it can be shown as a coverage hint.

    Returns (rows_imported, warnings).
    """
    df    = _norm_cols(df)
    conn  = get_conn()
    count = 0
    warns: list[str] = []

    required = {"return_date", "asin"}
    missing  = required - set(df.columns)
    if missing:
        return 0, [f"Missing columns: {', '.join(missing)}"]

    unknown_fcs: set[str] = set()   # EU prefixes not in our map

    with conn:
        for _, row in df.iterrows():
            asin = str(row.get("asin") or "").strip().upper()
            if not asin or asin == "NAN":
                continue
            ret_date = str(row.get("return_date") or "").strip()[:10]
            if not ret_date:
                continue

            fc_id             = str(row.get("fc_id") or "").strip().upper() or None
            mkt, fc_known     = _fc_to_marketplace(fc_id or "", region_hint)
            if not fc_known and fc_id and region_hint == "EU":
                unknown_fcs.add(fc_id)
            region  = _region(mkt)
            country = MARKETPLACE_TO_COUNTRY.get(mkt, "")

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
                         reason, disposition, status, marketplace, region,
                         country, fc_id, customer_comments)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(order_id, asin, return_date, marketplace) DO UPDATE SET
                        quantity=excluded.quantity,
                        reason=excluded.reason,
                        disposition=excluded.disposition,
                        status=excluded.status,
                        title=COALESCE(excluded.title, amazon_returns.title),
                        country=excluded.country,
                        fc_id=excluded.fc_id,
                        customer_comments=excluded.customer_comments
                """, (ret_date, order_id, sku, asin, title, qty,
                      reason, disp, status, mkt, region,
                      country, fc_id, comments))
                count += 1
            except Exception as e:
                warns.append(str(e)[:120])

    conn.close()

    if unknown_fcs:
        warns.insert(0,
            f"⚠️ Unrecognised EU fulfillment center(s): {', '.join(sorted(unknown_fcs))}. "
            f"These rows were stored under amazon.de as a fallback — "
            f"please report these FC codes so the mapping can be updated."
        )

    # Persist the declared report date range
    if report_from and report_to:
        save_upload_meta(region_hint, report_from, report_to, count)

    return count, warns


# ── Analytics queries ────────────────────────────────────────────────────────

def get_return_rate_report(
    region: str | None = None,
    country: str | None = None,
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
    if country and country != "All":
        ret_where.append("country = ?")
        ret_params.append(country)
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
        mkt_list = list(NA_MARKETPLACES if region == "NA" else EU_MARKETPLACES)
        placeholders = ",".join("?" * len(mkt_list))
        ord_where.append(f"marketplace IN ({placeholders})")
        ord_params.extend(mkt_list)
    if country and country != "All":
        # Derive marketplace from country
        mkt = next((m for m, c in MARKETPLACE_TO_COUNTRY.items() if c == country), None)
        if mkt:
            ord_where.append("marketplace = ?")
            ord_params.append(mkt)
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


def get_return_country_breakdown(
    start_date: str | None = None,
    end_date:   str | None = None,
) -> pd.DataFrame:
    """
    Return a DataFrame with one row per country showing total returns
    and breakdown by bucket (AMAZON / DEFECTIVE / CUSTOMER / OTHER).
    """
    conn = get_conn()
    where = []
    params: list = []
    if start_date:
        where.append("return_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("return_date <= ?")
        params.append(end_date)

    sql = """
        SELECT COALESCE(country, region) AS country,
               reason,
               SUM(quantity) AS qty
        FROM amazon_returns
        {where}
        GROUP BY country, reason
    """.format(where=("WHERE " + " AND ".join(where)) if where else "")

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    from collections import defaultdict
    data: dict[str, dict] = defaultdict(lambda: {
        "Total": 0, "AMAZON": 0, "DEFECTIVE": 0, "CUSTOMER": 0, "OTHER": 0
    })
    for r in rows:
        c  = str(r["country"] or "?")
        q  = int(r["qty"] or 0)
        bk = REASON_BUCKET.get(str(r["reason"] or "").upper(), "OTHER")
        data[c]["Total"]  += q
        data[c][bk]       += q

    result = []
    for c, d in sorted(data.items(), key=lambda x: -x[1]["Total"]):
        flag = COUNTRY_FLAG.get(c, "")
        result.append({
            "Country":      f"{flag} {c}",
            "Total":        d["Total"],
            "🔴 Amazon":    d["AMAZON"],
            "🟠 Mfg Defect":d["DEFECTIVE"],
            "⚪ Customer":  d["CUSTOMER"],
            "Other":        d["OTHER"],
        })
    return pd.DataFrame(result) if result else pd.DataFrame()


def get_available_countries() -> list[str]:
    """Return distinct country values stored in amazon_returns."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT country FROM amazon_returns WHERE country IS NOT NULL AND country != '' ORDER BY country"
    ).fetchall()
    conn.close()
    return [r["country"] for r in rows]


def save_upload_meta(region: str, report_from: str, report_to: str, rows: int):
    """Upsert one row per region recording the declared report date range."""
    conn = get_conn()
    with conn:
        conn.execute("""
            INSERT INTO returns_meta (region, report_from, report_to, last_imported, rows_imported)
            VALUES (?, ?, ?, datetime('now'), ?)
            ON CONFLICT(region) DO UPDATE SET
                report_from   = excluded.report_from,
                report_to     = excluded.report_to,
                last_imported = excluded.last_imported,
                rows_imported = excluded.rows_imported
        """, (region, report_from, report_to, rows))
    conn.close()


def get_upload_meta() -> dict[str, dict]:
    """
    Return {region: {report_from, report_to, last_imported, rows_imported}}
    for every region that has been uploaded.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT region, report_from, report_to, last_imported, rows_imported FROM returns_meta"
    ).fetchall()
    conn.close()
    return {r["region"]: dict(r) for r in rows}


def clear_all_returns() -> int:
    """Delete every row from amazon_returns. Returns the number of rows deleted."""
    conn = get_conn()
    with conn:
        cur = conn.execute("DELETE FROM amazon_returns")
        deleted = cur.rowcount
    conn.close()
    return deleted


def get_returns_date_range() -> tuple[str | None, str | None]:
    """Return (min_date, max_date) of all stored returns."""
    conn = get_conn()
    row  = conn.execute(
        "SELECT MIN(return_date) AS mn, MAX(return_date) AS mx FROM amazon_returns"
    ).fetchone()
    conn.close()
    return (row["mn"], row["mx"]) if row else (None, None)
