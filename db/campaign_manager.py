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
    "budget currency":              "budget_currency",
    "campaign country":             "campaign_country",
    "ad product":                   "ad_product",
    "target id":                    "target_id",
    "target bid":                   "target_bid",
    "targeting":                    "keyword_text",
    "targeting match type":         "match_type",
    "placement classification":     "placement",
    "advertised product name":      "product_name",
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

def import_campaign_manager_csv(file_obj, export_date: str = None) -> tuple[int, int, list[str]]:
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

    # import_date = the day the CSV was exported (= when the bids were current).
    # The date range inside the CSV only affects performance columns (spend/ROAS),
    # NOT the Target Bid column — bids always reflect the moment of export.
    import_date = export_date or datetime.utcnow().strftime("%Y-%m-%d")

    conn = get_conn()
    rows_saved = 0
    rows_for_detection: list[dict] = []

    with conn:
        # Re-upload same date → replace existing data cleanly
        conn.execute("DELETE FROM campaign_targets WHERE import_date = ?", (import_date,))
        conn.execute("DELETE FROM keyword_bid_changes WHERE change_date = ?", (import_date,))

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
            def _str(v):
                s = str(v).strip() if v is not None else ""
                return "" if s.lower() in ("nan", "none") else s

            bid_strategy  = _str(row.get("bid_strategy"))
            _cur = _str(row.get("currency")) or _str(row.get("budget_currency"))
            currency      = _cur
            ad_product    = _str(row.get("ad_product"))
            asin          = _str(row.get("asin"))
            keyword_text  = _str(row.get("keyword_text"))
            match_type    = _str(row.get("match_type"))
            placement     = _str(row.get("placement"))
            product_name  = _str(row.get("product_name"))[:120] if row.get("product_name") else ""
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
                         target_id, target_bid, keyword_text, match_type, placement,
                         product_name, date_range_start, date_range_end,
                         impressions, clicks, spend, purchases, sales, roas,
                         asin, currency)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    import_date, campaign_id, campaign_name, marketplace,
                    ad_product, campaign_status, campaign_budget, bid_strategy,
                    target_id, target_bid, keyword_text, match_type, placement,
                    product_name, dr_start, dr_end,
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

    # ── Bid change detection across ALL consecutive import pairs ─────────────
    # Run for every consecutive (older, newer) import pair so that adding a
    # baseline retroactively triggers detection for already-stored newer imports.
    all_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT import_date FROM campaign_targets ORDER BY import_date"
    ).fetchall()]

    bid_changes_detected = 0
    with conn:
        for i in range(1, len(all_dates)):
            older, newer = all_dates[i - 1], all_dates[i]
            bid_changes_detected += _detect_bid_changes_pair(older, newer, conn)

    conn.close()
    return rows_saved, bid_changes_detected, warnings


def _detect_bid_changes_pair(older_date: str, newer_date: str, conn) -> int:
    """
    Compare bids between two consecutive imports (older → newer).
    Insert new rows into keyword_bid_changes where bid differs.
    Skips pairs already fully processed (uses INSERT OR IGNORE).
    Returns number of new changes inserted.
    """
    older_bids = {
        (r["campaign_id"], r["target_id"]): float(r["target_bid"])
        for r in conn.execute("""
            SELECT campaign_id, target_id, MAX(target_bid) AS target_bid
            FROM campaign_targets
            WHERE import_date = ? AND target_bid IS NOT NULL
            GROUP BY campaign_id, target_id
        """, (older_date,)).fetchall()
    }

    newer_rows = conn.execute("""
        SELECT campaign_id, campaign_name, target_id, marketplace,
               currency, ad_product, MAX(target_bid) AS target_bid,
               MAX(keyword_text) AS keyword_text, MAX(match_type) AS match_type
        FROM campaign_targets
        WHERE import_date = ? AND target_bid IS NOT NULL
        GROUP BY campaign_id, target_id
    """, (newer_date,)).fetchall()

    inserted = 0
    for r in newer_rows:
        cid = r["campaign_id"]
        tid = r["target_id"]
        bid_after  = float(r["target_bid"])
        bid_before = older_bids.get((cid, tid))

        if bid_before is None:
            continue  # new keyword since older import
        if abs(bid_after - bid_before) < 0.001:
            continue  # unchanged

        try:
            conn.execute("""
                INSERT OR IGNORE INTO keyword_bid_changes
                    (change_date, campaign_id, campaign_name, target_id,
                     keyword_text, match_type,
                     marketplace, bid_before, bid_after, currency, ad_product)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                newer_date, cid, r["campaign_name"], tid,
                r["keyword_text"] or None, r["match_type"] or None,
                r["marketplace"], round(bid_before, 3), round(bid_after, 3),
                r["currency"], r["ad_product"],
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


def get_keyword_attribution(marketplace: str = None,
                            min_p2_days: int = 7,
                            roas_threshold: float = 0.2) -> list[dict]:
    """
    Score every auto-detected keyword bid change using performance data already
    in campaign_targets (the 90-day window).

    For each keyword_bid_changes row:
      P1 = all campaign_targets rows for same (campaign_id, target_id)
           where date_range_end < change_date  (pre-change performance)
      P2 = all campaign_targets rows for same (campaign_id, target_id)
           where date_range_start >= change_date  (post-change performance)

    Verdict:
      win      — ROAS P2 > P1 + roas_threshold
      loss     — ROAS P2 < P1 − roas_threshold
      flat     — within threshold
      settling — P2 has fewer than min_p2_days of data
      pending  — no P2 data yet
      no_baseline — no P1 data

    Returns list of dicts, newest change first.
    """
    conn = get_conn()
    mp_filter = "WHERE marketplace = ?" if marketplace else ""
    params = [marketplace] if marketplace else []

    changes = conn.execute(f"""
        SELECT id, change_date, campaign_id, campaign_name, target_id,
               marketplace, bid_before, bid_after, currency, ad_product, notes
        FROM keyword_bid_changes
        {mp_filter}
        ORDER BY change_date DESC, campaign_name
    """, params).fetchall()

    rows = []
    for ch in changes:
        cid   = ch["campaign_id"]
        tid   = ch["target_id"]
        mkt   = ch["marketplace"]
        cdate = str(ch["change_date"])[:10]

        # P1: most recent snapshot taken BEFORE the change
        p1 = conn.execute("""
            SELECT SUM(spend) AS spend, SUM(sales) AS sales,
                   SUM(purchases) AS purchases, SUM(impressions) AS impressions,
                   SUM(clicks) AS clicks, MAX(date_range_end) AS last_date
            FROM campaign_targets
            WHERE campaign_id=? AND target_id=? AND marketplace=?
              AND import_date < ?
        """, (cid, tid, mkt, cdate)).fetchone()

        # P2: snapshots taken ON OR AFTER the change (new bids are active)
        p2 = conn.execute("""
            SELECT SUM(spend) AS spend, SUM(sales) AS sales,
                   SUM(purchases) AS purchases, SUM(impressions) AS impressions,
                   SUM(clicks) AS clicks,
                   MIN(date_range_start) AS first_date,
                   MAX(date_range_end)   AS last_date
            FROM campaign_targets
            WHERE campaign_id=? AND target_id=? AND marketplace=?
              AND import_date >= ?
        """, (cid, tid, mkt, cdate)).fetchone()

        # ROAS
        p1_spend = float(p1["spend"] or 0) if p1 else 0
        p1_sales = float(p1["sales"] or 0) if p1 else 0
        p2_spend = float(p2["spend"] or 0) if p2 else 0
        p2_sales = float(p2["sales"] or 0) if p2 else 0

        roas_p1 = round(p1_sales / p1_spend, 2) if p1_spend > 0 else None
        roas_p2 = round(p2_sales / p2_spend, 2) if p2_spend > 0 else None

        # Days of P2 data
        p2_days = None
        if p2 and p2["first_date"] and p2["last_date"]:
            from datetime import datetime as _dt
            try:
                p2_days = (_dt.strptime(p2["last_date"], "%Y-%m-%d") -
                           _dt.strptime(p2["first_date"], "%Y-%m-%d")).days + 1
            except ValueError:
                p2_days = None

        # Verdict
        if roas_p1 is None:
            verdict = "no_baseline"
        elif roas_p2 is None:
            verdict = "pending"
        elif p2_days is not None and p2_days < min_p2_days:
            verdict = "settling"
        elif roas_p2 > roas_p1 + roas_threshold:
            verdict = "win"
        elif roas_p2 < roas_p1 - roas_threshold:
            verdict = "loss"
        else:
            verdict = "flat"

        rows.append({
            "id":           ch["id"],
            "change_date":  cdate,
            "campaign":     ch["campaign_name"],
            "target_id":    tid,
            "marketplace":  mkt,
            "ad_product":   ch["ad_product"],
            "currency":     ch["currency"],
            "bid_before":   ch["bid_before"],
            "bid_after":    ch["bid_after"],
            "notes":        ch["notes"],
            "roas_p1":      roas_p1,
            "roas_p2":      roas_p2,
            "roas_delta":   round(roas_p2 - roas_p1, 2) if (roas_p1 and roas_p2) else None,
            "spend_p1":     round(p1_spend, 2),
            "spend_p2":     round(p2_spend, 2),
            "sales_p1":     round(p1_sales, 2),
            "sales_p2":     round(p2_sales, 2),
            "p2_days":      p2_days,
            "verdict":      verdict,
        })

    conn.close()
    return rows


def summarize_keyword_attribution(rows: list[dict]) -> dict:
    settled  = [r for r in rows if r["verdict"] in ("win", "loss", "flat")]
    wins     = [r for r in settled if r["verdict"] == "win"]
    losses   = [r for r in settled if r["verdict"] == "loss"]
    decided  = len(wins) + len(losses)
    return {
        "total":       len(rows),
        "settled":     len(settled),
        "wins":        len(wins),
        "losses":      len(losses),
        "flat":        len(settled) - len(wins) - len(losses),
        "settling":    sum(1 for r in rows if r["verdict"] == "settling"),
        "pending":     sum(1 for r in rows if r["verdict"] == "pending"),
        "no_baseline": sum(1 for r in rows if r["verdict"] == "no_baseline"),
        "win_rate":    round(100 * len(wins) / decided, 0) if decided else None,
    }


def get_wasted_spend(marketplace: str = None, min_spend: float = 3.0) -> pd.DataFrame:
    """
    Keywords with spend >= min_spend and 0 purchases in the latest import.
    These are prime negative keyword candidates.
    """
    conn = get_conn()
    latest = conn.execute("SELECT MAX(import_date) FROM campaign_targets").fetchone()[0]
    if not latest:
        conn.close()
        return pd.DataFrame()

    mp_filter = "AND marketplace = ?" if marketplace else ""
    params = [latest, min_spend]
    if marketplace:
        params.insert(1, marketplace)

    rows = conn.execute(f"""
        SELECT campaign_name, keyword_text, match_type, marketplace,
               ad_product, asin, product_name,
               SUM(spend)     AS spend,
               SUM(clicks)    AS clicks,
               SUM(impressions) AS impressions,
               SUM(purchases) AS purchases,
               SUM(sales)     AS sales
        FROM campaign_targets
        WHERE import_date = ? {mp_filter}
          AND spend >= ?
        GROUP BY campaign_id, target_id
        HAVING SUM(purchases) = 0
        ORDER BY spend DESC
    """, params).fetchall()
    conn.close()

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["spend"] = df["spend"].round(2)
    return df


def get_campaign_manager_last_import() -> str | None:
    """Return the date of the last Campaign Manager import, or None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(import_date) FROM campaign_targets"
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else None
