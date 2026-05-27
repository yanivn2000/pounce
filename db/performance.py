"""
db/performance.py — Campaign performance snapshots and backfire detection.
"""
from datetime import date
from db.database import get_conn


def save_performance_snapshot(results: list, snapshot_date: str, marketplace: str):
    """
    Save per-placement performance for each campaign result.
    Called after every analysis run.
    results: list of CampaignResult dataclass instances.
    """
    rows = []
    for r in results:
        be = getattr(r, 'breakeven_roas', 0.0) or 0.0

        for pl_name, pl_data in [('Top', r.top), ('Rest', r.rest), ('Product', r.product)]:
            if pl_data is None:
                continue
            spend     = pl_data.spend  or 0
            sales     = pl_data.sales  or 0
            purchases = pl_data.orders or 0
            roas      = pl_data.roas   or 0

            if be > 0 and sales > 0 and purchases > 0:
                avg_p        = sales / purchases
                m_per_unit   = avg_p * (1 - 1 / be)
                total_profit = round((m_per_unit * purchases) - spend, 2)
                margin       = round(m_per_unit, 4)
            else:
                total_profit = 0.0
                margin       = 0.0

            rows.append((snapshot_date, r.campaign, marketplace, pl_name,
                         roas, spend, sales, purchases, total_profit, margin, be))

    if rows:
        conn = get_conn()
        with conn:
            conn.executemany("""
                INSERT OR REPLACE INTO campaign_performance
                    (snapshot_date, campaign_name, marketplace, placement_type,
                     roas, spend, sales, purchases, total_profit, margin_per_unit, breakeven_roas)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, rows)
        conn.close()


def get_performance_alerts(marketplace: str, thresholds: dict = None) -> list:
    """
    Compare the two most recent snapshots for every campaign in this marketplace.
    Returns alerts with type='regression' (negative) or type='improvement' (positive).
    Thresholds are read from DB if not provided.
    """
    if thresholds is None:
        # Lazy import to avoid circular dependency
        from db.settings import get_alert_thresholds
        thresholds = get_alert_thresholds()

    roas_drop_thresh   = thresholds.get("alert_roas_drop_pct",   30) / 100
    profit_drop_thresh = thresholds.get("alert_profit_drop_pct", 20) / 100
    roas_gain_thresh   = thresholds.get("alert_roas_gain_pct",   30) / 100
    profit_gain_thresh = thresholds.get("alert_profit_gain_pct", 20) / 100

    conn = get_conn()

    # All campaign+placement combos with at least 2 snapshots
    combos = conn.execute("""
        SELECT campaign_name, placement_type
        FROM campaign_performance
        WHERE marketplace = ?
        GROUP BY campaign_name, placement_type
        HAVING COUNT(DISTINCT snapshot_date) >= 2
    """, (marketplace,)).fetchall()

    alerts = []

    for row in combos:
        camp      = row["campaign_name"]
        placement = row["placement_type"]

        # Two most recent distinct snapshots
        snaps = conn.execute("""
            SELECT snapshot_date, roas, spend, sales, purchases, total_profit
            FROM campaign_performance
            WHERE campaign_name = ? AND marketplace = ? AND placement_type = ?
            ORDER BY snapshot_date DESC
            LIMIT 2
        """, (camp, marketplace, placement)).fetchall()

        if len(snaps) < 2:
            continue

        after  = snaps[0]   # most recent
        before = snaps[1]   # previous

        before_roas   = before["roas"]        or 0
        after_roas    = after["roas"]          or 0
        before_profit = before["total_profit"] or 0
        after_profit  = after["total_profit"]  or 0

        if before_roas <= 0 or after_roas <= 0:
            continue

        roas_change   = (after_roas - before_roas) / before_roas
        profit_change = (after_profit - before_profit) / abs(before_profit) if before_profit != 0 else 0

        # If profit data is missing for both snapshots (legacy data stored as 0),
        # fall back to ROAS-only comparison so existing snapshots still produce alerts.
        profit_data_available = not (before_profit == 0 and after_profit == 0)

        base = {
            "campaign":           camp,
            "placement":          placement,
            "before_roas":        round(before_roas, 2),
            "after_roas":         round(after_roas, 2),
            "roas_chg_pct":       round(abs(roas_change) * 100),
            "before_profit":      round(before_profit, 2),
            "after_profit":       round(after_profit, 2),
            "profit_chg_pct":     round(abs(profit_change) * 100),
            "profit_data":        profit_data_available,
            "before_date":        before["snapshot_date"],
            "after_date":         after["snapshot_date"],
            "spend":              round(after["spend"] or 0, 2),
            "purchases":          after["purchases"] or 0,
        }

        if profit_data_available:
            # Full check: ROAS AND profit must both move beyond threshold
            if roas_change < -roas_drop_thresh and profit_change < -profit_drop_thresh:
                alerts.append({**base, "type": "regression"})
            elif roas_change > roas_gain_thresh and profit_change > profit_gain_thresh:
                alerts.append({**base, "type": "improvement"})
        else:
            # Fallback: ROAS-only (no profit data in these snapshots)
            if roas_change < -roas_drop_thresh:
                alerts.append({**base, "type": "regression"})
            elif roas_change > roas_gain_thresh:
                alerts.append({**base, "type": "improvement"})

    conn.close()
    return alerts


# Backwards-compatible alias used by existing callers
def get_backfire_alerts(marketplace: str, **kwargs) -> list:
    return [a for a in get_performance_alerts(marketplace, **kwargs) if a["type"] == "regression"]


def get_bad_roas_campaigns(marketplace: str, min_spend: float = 5.0) -> list[dict]:
    """
    Return placements that were below their breakeven ROAS in BOTH of the two
    most recent snapshots — chronic underperformers with no dramatic change.

    min_spend: ignore placements with negligible spend (avoids noise from
    paused or zero-traffic placements).

    Each dict:  campaign, placement, before_roas, after_roas, breakeven_roas,
                before_date, after_date, spend, purchases, total_profit
    Sorted by worst (lowest) after_roas first.
    """
    conn = get_conn()
    combos = conn.execute("""
        SELECT campaign_name, placement_type
        FROM   campaign_performance
        WHERE  marketplace = ?
        GROUP  BY campaign_name, placement_type
        HAVING COUNT(DISTINCT snapshot_date) >= 2
    """, (marketplace,)).fetchall()

    result = []
    for row in combos:
        camp      = row["campaign_name"]
        placement = row["placement_type"]

        snaps = conn.execute("""
            SELECT snapshot_date, roas, spend, purchases, breakeven_roas, total_profit
            FROM   campaign_performance
            WHERE  campaign_name = ? AND marketplace = ? AND placement_type = ?
            ORDER  BY snapshot_date DESC
            LIMIT  2
        """, (camp, marketplace, placement)).fetchall()

        if len(snaps) < 2:
            continue

        after  = snaps[0]   # most recent
        before = snaps[1]   # previous

        after_roas  = after["roas"]  or 0.0
        before_roas = before["roas"] or 0.0
        spend       = after["spend"] or 0.0

        # Skip low-activity placements or placements with no ROAS
        if spend < min_spend or after_roas <= 0 or before_roas <= 0:
            continue

        # Use stored breakeven_roas; fall back to 1.0 (at least break even on spend)
        after_be  = (after["breakeven_roas"]  or 0.0) or 1.0
        before_be = (before["breakeven_roas"] or 0.0) or 1.0

        if after_roas < after_be and before_roas < before_be:
            result.append({
                "campaign":      camp,
                "placement":     placement,
                "before_roas":   round(before_roas, 2),
                "after_roas":    round(after_roas,  2),
                "breakeven_roas": round(after_be,   2),
                "before_date":   before["snapshot_date"],
                "after_date":    after["snapshot_date"],
                "spend":         round(spend, 2),
                "purchases":     after["purchases"]    or 0,
                "total_profit":  round(after["total_profit"] or 0, 2),
            })

    conn.close()
    return sorted(result, key=lambda x: x["after_roas"])


def reset_snapshots(marketplace: str = None):
    """
    Delete snapshot rows. If marketplace is given, deletes only that marketplace.
    If None, deletes all marketplaces.
    """
    conn = get_conn()
    with conn:
        if marketplace:
            conn.execute("DELETE FROM campaign_performance WHERE marketplace = ?", (marketplace,))
        else:
            conn.execute("DELETE FROM campaign_performance")
    conn.close()


def get_snapshot_count(marketplace: str = None) -> int:
    """Return total snapshot rows, optionally filtered by marketplace."""
    conn = get_conn()
    if marketplace:
        n = conn.execute(
            "SELECT COUNT(*) FROM campaign_performance WHERE marketplace = ?", (marketplace,)
        ).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM campaign_performance").fetchone()[0]
    conn.close()
    return n


def get_snapshot_summary() -> list[dict]:
    """
    Return per-marketplace snapshot summary:
    marketplace, distinct_dates, campaign_count, latest_date
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            marketplace,
            COUNT(DISTINCT snapshot_date)  AS distinct_dates,
            COUNT(DISTINCT campaign_name)  AS campaign_count,
            MAX(snapshot_date)             AS latest_date
        FROM campaign_performance
        GROUP BY marketplace
        ORDER BY marketplace
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_snapshot_dates(marketplace: str) -> list[str]:
    """Return distinct snapshot dates for a marketplace, newest first."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT snapshot_date FROM campaign_performance
        WHERE marketplace = ?
        ORDER BY snapshot_date DESC
    """, (marketplace,)).fetchall()
    conn.close()
    return [r[0] for r in rows]
