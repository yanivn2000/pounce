"""
db/campaign_manager.py — Import and analyse Amazon Ads Campaign Manager CSV.

The "All campaigns" export from Amazon Ads Campaign Manager contains one row
per (campaign × target/keyword × date-range period).  Amazon splits a target's
rows when its bid changes, so the same Target ID appearing with two different
Target bid values across consecutive date ranges reveals a bid change.

Tables written:
  campaign_targets      — raw rows from the CSV (deduplicated by import)
  keyword_bid_changes   — auto-detected keyword-level bid changes
"""
from __future__ import annotations
import io
import re
from datetime import datetime

import pandas as pd

from db.database import get_conn


# ── Marketplace mapping ───────────────────────────────────────────────────────
_COUNTRY_TO_MP = {
    "US": "amazon.com",
    "CA": "amazon.ca",
    "UK": "amazon.co.uk",
    "GB": "amazon.co.uk",
    "DE": "amazon.de",
    "FR": "amazon.fr",
    "IT": "amazon.it",
    "ES": "amazon.es",
    "NL": "amazon.nl",
    "SE": "amazon.se",
    "PL": "amazon.pl",
    "BE": "amazon.com.be",
    "AU": "amazon.com.au",
    "JP": "amazon.co.jp",
    "MX": "amazon.com.mx",
    "IN": "amazon.in",
    "IE": "amazon.ie",
    "TR": "amazon.com.tr",
    "SA": "amazon.sa",
    "AE": "amazon.ae",
    "SG": "amazon.sg",
}

# Columns we actually care about (lower-cased CSV header → our field name)
_COL_MAP = {
    "date range":                   "date_range",
    "campaign id":                  "campaign_id",
    "campaign name":                "campaign_name",
    "campaign delivery status":     "campaign_status",
    "campaign budget amount":       "campaign_budget",
    "campaign bid strategy":        "bid_strategy",
    "campaign currency code":       "currency",
    "campaign country":             "campaign_country",
    "ad product":                   "ad_product",
    "target id":                    "target_id",
    "target bid":                   "target_bid",
    "advertised product id":        "asin",
    "impressions":                  "impressions",
    "clicks":                       "clicks",
    "total cost":                   "spend",
    "purchases":                    "purchases",
    "sales":                        "sales",
    "roas":                         "roas",
}

_DATE_FMT = "%b %d, %Y"   # e.g. "Jun 16, 2026"


def _parse_date_range(s: str) -> tuple[str | None, str | None]:
    """'Jun 16, 2026 - Jun 22, 2026' → ('2026-06-16', '2026-06-22')"""
    if not s or not isinstance(s, str):
        return None, None
    parts = [p.strip() for p in s.split(" - ")]
    if len(parts) != 2:
        return None, None
    try:
        start = datetime.strptime(parts[0], _DATE_FMT).strftime("%Y-%m-%d")
        end   = datetime.strptime(parts[1], _DATE_FMT).strftime("%Y-%m-%d")
        return start, end
    except ValueError:
        return None, None


def _clean_id(val: str) -> str:
    """Strip Excel formula wrapper: '=""185809160699307""' → '185809160699307'"""
    if not val:
        return ""
    return re.sub(r'^=""|""$', "", str(val).strip()).strip('"')


def _float(val) -> float | None:
    try:
        v = str(val).replace(",", "").replace("%", "").strip()
        return float(v) if v else None
    except (ValueError, TypeError):
        return None


def _int(val) -> int:
    try:
        v = str(val).replace(",", "").strip()
        return int(float(v)) if v else 0
    except (ValueError, TypeError):
        return 0


# ── Main importer ─────────────────────────────────────────────────────────────

def import_campaign_manager_csv(file_obj) -> tuple[int, int, list[str]]:
    """
    Parse an Amazon Ads Campaign Manager CSV and:
      1. Upsert rows into campaign_targets
      2. Auto-detect keyword bid changes and insert into keyword_bid_changes

    Returns (rows_saved, bid_changes_detected, warnings_list).
    """
    raw = file_obj.read()
    if isinstance(raw, bytes):
        for enc in ("utf-8-sig", "windows-1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
    else:
        text = raw

    df_raw = pd.read_csv(io.StringIO(text), dtype=str)
    df_raw.columns = [c.lstrip("﻿?").strip().lower() for c in df_raw.columns]

    warnings = []

    # Rename columns we care about
    rename = {k: v for k, v in _COL_MAP.items() if k in df_raw.columns}
    df = df_raw.rename(columns=rename)

    required = ["campaign_id", "campaign_name", "date_range"]
    missing = [r for r in required if r not in df.columns]
    if missing:
        return 0, 0, [f"Missing required columns: {missing}. Available: {list(df_raw.columns[:10])}"]

    import_date = datetime.utcnow().strftime("%Y-%m-%d")
    conn = get_conn()
    rows_saved = 0

    rows_for_detection: list[dict] = []

    with conn:
        for _, row in df.iterrows():
            campaign_id   = _clean_id(row.get("campaign_id", ""))
            campaign_name = str(row.get("campaign_name", "")).strip()
            if not campaign_id or not campaign_name:
                continue

            date_range    = str(row.get("date_range", "")).strip()
            dr_start, dr_end = _parse_date_range(date_range)
            if not dr_start:
                continue

            country    = str(row.get("campaign_country", "")).strip().upper()
            marketplace = _COUNTRY_TO_MP.get(country, "")
            if not marketplace:
                warnings.append(f"Unknown country code: {country}")
                continue

            target_id     = _clean_id(row.get("target_id", ""))
            target_bid    = _float(row.get("target_bid"))
            campaign_status = str(row.get("campaign_status", "")).strip()
            campaign_budget = _float(row.get("campaign_budget"))
            bid_strategy  = str(row.get("bid_strategy", "")).strip()
            currency      = str(row.get("currency", "")).strip()
            ad_product    = str(row.get("ad_product", "")).strip()
            asin          = str(row.get("asin", "")).strip()
            impressions   = _int(row.get("impressions"))
            clicks        = _int(row.get("clicks"))
            spend         = _float(row.get("spend")) or 0.0
            purchases     = _int(row.get("purchases"))
            sales         = _float(row.get("sales")) or 0.0
            roas          = _float(row.get("roas"))

            try:
                conn.execute("""
                    INSERT OR IGNORE INTO campaign_targets
                        (import_date, campaign_id, campaign_name, marketplace,
                         ad_product, campaign_status, campaign_budget, bid_strategy,
                         target_id, target_bid, date_range_start, date_range_end,
                         impressions, clicks, spend, purchases, sales, roas,
                         asin, currency)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    import_date, campaign_id, campaign_name, marketplace,
                    ad_product, campaign_status, campaign_budget, bid_strategy,
                    target_id, target_bid, dr_start, dr_end,
                    impressions, clicks, spend, purchases, sales, roas,
                    asin, currency,
                ))
                rows_saved += 1
            except Exception:
                pass

            if target_id and target_bid is not None:
                rows_for_detection.append({
                    "campaign_id":   campaign_id,
                    "campaign_name": campaign_name,
                    "marketplace":   marketplace,
                    "ad_product":    ad_product,
                    "target_id":     target_id,
                    "target_bid":    target_bid,
                    "date_start":    dr_start,
                    "currency":      currency,
                })

    # ── Bid change detection ──────────────────────────────────────────────────
    bid_changes_detected = _detect_bid_changes(rows_for_detection, conn)
    conn.close()
    return rows_saved, bid_changes_detected, warnings


def _detect_bid_changes(rows: list[dict], conn) -> int:
    """
    For each (campaign_id, target_id) group, sort by date and find consecutive
    rows where the bid differs.  Insert into keyword_bid_changes if not already
    stored.

    Returns number of new bid changes inserted.
    """
    if not rows:
        return 0

    df = pd.DataFrame(rows)
    inserted = 0

    for (campaign_id, target_id), grp in df.groupby(["campaign_id", "target_id"]):
        grp_sorted = grp.sort_values("date_start").reset_index(drop=True)
        if len(grp_sorted) < 2:
            continue

        for i in range(1, len(grp_sorted)):
            prev = grp_sorted.iloc[i - 1]
            curr = grp_sorted.iloc[i]

            bid_before = float(prev["target_bid"])
            bid_after  = float(curr["target_bid"])

            if abs(bid_before - bid_after) < 0.001:
                continue  # same bid — no change

            change_date = curr["date_start"]
            campaign_name = curr["campaign_name"]
            marketplace   = curr["marketplace"]
            currency      = curr["currency"]
            ad_product    = curr["ad_product"]

            try:
                with conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO keyword_bid_changes
                            (change_date, campaign_id, campaign_name, target_id,
                             marketplace, bid_before, bid_after, currency, ad_product)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (
                        change_date, campaign_id, campaign_name, target_id,
                        marketplace, round(bid_before, 3), round(bid_after, 3),
                        currency, ad_product,
                    ))
                inserted += 1
            except Exception:
                pass

    return inserted


# ── Campaign overview ─────────────────────────────────────────────────────────

def get_campaign_overview(marketplace: str = None) -> pd.DataFrame:
    """
    Returns one row per campaign from the latest import, aggregated across
    all targets and date ranges in that import.

    Columns: campaign_name, marketplace, ad_product, campaign_status,
             bid_strategy, campaign_budget, currency,
             total_spend, total_sales, total_purchases, roas, import_date
    """
    conn = get_conn()
    mp_filter = "AND marketplace = ?" if marketplace else ""
    params = [marketplace] if marketplace else []

    df = pd.read_sql_query(f"""
        SELECT
            campaign_name,
            marketplace,
            ad_product,
            MAX(campaign_status)  AS campaign_status,
            MAX(bid_strategy)     AS bid_strategy,
            MAX(campaign_budget)  AS campaign_budget,
            MAX(currency)         AS currency,
            MAX(import_date)      AS import_date,
            SUM(spend)            AS total_spend,
            SUM(sales)            AS total_sales,
            SUM(purchases)        AS total_purchases
        FROM campaign_targets
        WHERE import_date = (SELECT MAX(import_date) FROM campaign_targets)
          {mp_filter}
        GROUP BY campaign_name, marketplace, ad_product
        ORDER BY total_spend DESC
    """, conn, params=params)
    conn.close()

    if df.empty:
        return df

    df["roas"] = df.apply(
        lambda r: round(r["total_sales"] / r["total_spend"], 2)
        if r["total_spend"] and r["total_spend"] > 0 else None,
        axis=1,
    )
    return df


def get_keyword_bid_changes(marketplace: str = None) -> pd.DataFrame:
    """Return all auto-detected keyword bid changes, newest first."""
    conn = get_conn()
    mp_filter = "WHERE marketplace = ?" if marketplace else ""
    params = [marketplace] if marketplace else []
    df = pd.read_sql_query(f"""
        SELECT change_date, campaign_name, target_id, marketplace,
               ad_product, bid_before, bid_after, currency, notes
        FROM keyword_bid_changes
        {mp_filter}
        ORDER BY change_date DESC, campaign_name
    """, conn, params=params)
    conn.close()
    return df


def get_campaign_manager_last_import() -> str | None:
    """Return the date of the last Campaign Manager import, or None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(import_date) FROM campaign_targets"
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else None
