"""
db/performance.py — Campaign performance snapshots and backfire detection.
"""
import json
from datetime import date, timedelta
import pandas as pd
from db.database import get_conn


def save_performance_snapshot(results: list, snapshot_date: str, marketplace: str):
    """
    Save per-placement performance for each campaign result.
    Called after every analysis run.
    results: list of CampaignResult dataclass instances.
    """
    conn = get_conn()
    with conn:
        for r in results:
            # Get avg_price and margin from placement_algorithm debug or approximate
            algo    = r.placement_algorithm or {}
            be_roas = 0.0
            margin  = 0.0

            for pl_name, pl_data in [('Top', r.top), ('Rest', r.rest), ('Product', r.product)]:
                if pl_data is None:
                    continue
                spend     = pl_data.spend     or 0
                sales     = pl_data.sales     or 0
                purchases = pl_data.orders    or 0
                roas      = pl_data.roas      or 0

                # Get breakeven from placements list in algo result
                be = 0.0
                for p in algo.get('placements', []):
                    if p.get('label', '').startswith(pl_name[:3]):
                        be = p.get('breakeven_roas', 0) or 0
                        break

                # margin_per_unit: approximate from sales/purchases and breakeven
                if be > 0 and roas > 0 and sales > 0 and purchases > 0:
                    avg_p = sales / purchases
                    m_per_unit = avg_p * (1 - 1 / be) if be > 0 else 0
                    total_profit = (m_per_unit * purchases) - spend
                    margin = m_per_unit
                else:
                    total_profit = 0.0
                    margin = 0.0

                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO campaign_performance
                            (snapshot_date, campaign_name, marketplace, placement_type,
                             roas, spend, sales, purchases, total_profit, margin_per_unit, breakeven_roas)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (snapshot_date, r.campaign, marketplace, pl_name,
                          roas, spend, sales, purchases,
                          round(total_profit, 2), round(margin, 2), be))
                except Exception:
                    pass
    conn.close()


def get_backfire_alerts(marketplace: str, current_date: str = None) -> list:
    """
    For each ASIN that had a 'bid' change log entry since the previous snapshot,
    compare performance before vs after. Return list of alert dicts.
    """
    if current_date is None:
        current_date = str(date.today())

    conn = get_conn()

    # Get all bid-type change log entries in the last 60 days
    cutoff = str(date.today() - timedelta(days=60))
    bid_changes = conn.execute("""
        SELECT asin, log_date, notes
        FROM change_log
        WHERE change_type = 'bid'
          AND marketplace = ?
          AND log_date >= ?
        ORDER BY log_date DESC
    """, (marketplace, cutoff)).fetchall()

    alerts = []

    for change in bid_changes:
        asin      = change["asin"].upper()
        log_date  = change["log_date"]
        notes     = change["notes"] or ""

        # Find campaigns for this ASIN (campaign name contains ASIN)
        campaigns = conn.execute("""
            SELECT DISTINCT campaign_name FROM campaign_performance
            WHERE marketplace = ?
              AND UPPER(campaign_name) LIKE ?
        """, (marketplace, f"%{asin}%")).fetchall()

        for camp_row in campaigns:
            camp = camp_row["campaign_name"]

            for placement in ['Top', 'Rest', 'Product']:
                # Get snapshot just BEFORE the bid change
                before = conn.execute("""
                    SELECT roas, spend, sales, purchases, total_profit, snapshot_date
                    FROM campaign_performance
                    WHERE campaign_name = ? AND marketplace = ? AND placement_type = ?
                      AND snapshot_date < ?
                    ORDER BY snapshot_date DESC LIMIT 1
                """, (camp, marketplace, placement, log_date)).fetchone()

                # Get snapshot AFTER the bid change (most recent)
                after = conn.execute("""
                    SELECT roas, spend, sales, purchases, total_profit, snapshot_date
                    FROM campaign_performance
                    WHERE campaign_name = ? AND marketplace = ? AND placement_type = ?
                      AND snapshot_date >= ?
                    ORDER BY snapshot_date DESC LIMIT 1
                """, (camp, marketplace, placement, log_date)).fetchone()

                if not before or not after:
                    continue

                before_roas   = before["roas"]         or 0
                after_roas    = after["roas"]           or 0
                before_profit = before["total_profit"]  or 0
                after_profit  = after["total_profit"]   or 0

                if before_roas <= 0 or after_roas <= 0:
                    continue

                roas_drop   = (before_roas - after_roas) / before_roas
                profit_drop = (before_profit - after_profit) / abs(before_profit) if before_profit != 0 else 0

                # Alert if ROAS dropped >30% AND profit dropped >20%
                if roas_drop > 0.30 and profit_drop > 0.20:
                    alerts.append({
                        "campaign":        camp,
                        "placement":       placement,
                        "asin":            asin,
                        "change_date":     log_date,
                        "change_notes":    notes,
                        "before_roas":     round(before_roas, 2),
                        "after_roas":      round(after_roas, 2),
                        "roas_drop_pct":   round(roas_drop * 100),
                        "before_profit":   round(before_profit, 2),
                        "after_profit":    round(after_profit, 2),
                        "profit_drop_pct": round(profit_drop * 100),
                        "before_date":     before["snapshot_date"],
                        "after_date":      after["snapshot_date"],
                    })

    conn.close()
    return alerts
