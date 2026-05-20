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
    Compare the two most recent snapshots for every campaign in this marketplace.
    Alert if ROAS dropped >30% AND total profit dropped >20% between them.
    No change log dependency — alerts fire for any significant regression.
    """
    conn = get_conn()

    # Get all distinct campaign+placement combos that have at least 2 snapshots
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

        roas_drop   = (before_roas - after_roas) / before_roas
        profit_drop = (before_profit - after_profit) / abs(before_profit) if before_profit != 0 else 0

        # Alert if ROAS dropped >30% AND profit dropped >20%
        if roas_drop > 0.30 and profit_drop > 0.20:
            alerts.append({
                "campaign":        camp,
                "placement":       placement,
                "before_roas":     round(before_roas, 2),
                "after_roas":      round(after_roas, 2),
                "roas_drop_pct":   round(roas_drop * 100),
                "before_profit":   round(before_profit, 2),
                "after_profit":    round(after_profit, 2),
                "profit_drop_pct": round(profit_drop * 100),
                "before_date":     before["snapshot_date"],
                "after_date":      after["snapshot_date"],
                "spend":           round(after["spend"] or 0, 2),
                "purchases":       after["purchases"] or 0,
            })

    conn.close()
    return alerts


def reset_snapshots():
    """Delete all rows from campaign_performance. Called from Admin tab."""
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM campaign_performance")
    conn.close()


def get_snapshot_count() -> int:
    """Return total number of snapshot rows stored."""
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM campaign_performance").fetchone()[0]
    conn.close()
    return n


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
