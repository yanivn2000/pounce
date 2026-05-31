"""
db/bid_changes.py — Auto-detected bid adjustment changes per placement.

Populated on every report upload: if a placement's bid adjustment differs
from the most recent stored value we write one row.  No manual entry.
"""
from .database import get_conn
import pandas as pd


# ── Write ─────────────────────────────────────────────────────────────────────

def record_bid_changes(results: list, report_date: str, marketplace: str):
    """
    Called after every analysis run.
    results: list of CampaignResult dataclass instances.

    For each placement, fetch the most recent bid_after stored for that
    campaign/placement/marketplace.  If it differs from the current bid
    adjustment, insert a new row.
    """
    conn = get_conn()

    _PL_MAP = {"Top": "Top of Search", "Rest": "Rest of Search", "Product": "Product Pages"}

    for r in results:
        be = getattr(r, "breakeven_roas", 0.0) or 0.0

        for pl_short, pl_data in [("Top", r.top), ("Rest", r.rest), ("Product", r.product)]:
            if pl_data is None or (pl_data.spend or 0) == 0:
                continue

            placement_type = _PL_MAP.get(pl_short, pl_short)

            # Current bid from report (BidAdj is stored in placement_algorithm)
            algo = getattr(r, "placement_algorithm", {}) or {}
            current_bid = None
            for p in algo.get("placements", []):
                if p.get("pl") == pl_short:
                    current_bid = round(float(p.get("current_adj", 0) or 0) * 100)
                    break
            if current_bid is None:
                continue  # no bid data for this placement

            # Most recent stored bid for this campaign/placement
            prev = conn.execute("""
                SELECT bid_after FROM bid_changes
                WHERE campaign_name=? AND placement_type=? AND marketplace=?
                ORDER BY report_date DESC LIMIT 1
            """, (r.campaign, placement_type, marketplace)).fetchone()

            bid_before = int(prev["bid_after"]) if prev else 0

            if current_bid == bid_before:
                continue  # no change — skip

            # Compute profit for this placement
            spend     = pl_data.spend  or 0
            sales     = pl_data.sales  or 0
            purchases = pl_data.orders or 0
            roas      = pl_data.roas   or 0
            if be > 0 and sales > 0 and purchases > 0:
                avg_p        = sales / purchases
                m_per_unit   = avg_p * (1 - 1 / be)
                profit       = round((m_per_unit * purchases) - spend, 2)
            else:
                profit = 0.0

            try:
                with conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO bid_changes
                            (campaign_name, placement_type, marketplace, report_date,
                             bid_before, bid_after, roas, spend, purchases, profit)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        r.campaign, placement_type, marketplace, report_date,
                        bid_before, current_bid,
                        round(roas, 3), round(spend, 2), int(purchases), profit,
                    ))
            except Exception:
                pass

    conn.close()


# ── Read ──────────────────────────────────────────────────────────────────────

def get_bid_history(campaign_name: str, placement_type: str,
                    marketplace: str) -> list[dict]:
    """Return bid change history for one campaign/placement, newest first."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT report_date, bid_before, bid_after, roas, spend, purchases, profit
        FROM bid_changes
        WHERE campaign_name=? AND placement_type=? AND marketplace=?
        ORDER BY report_date DESC
    """, (campaign_name, placement_type, marketplace)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_bid_changes(marketplace: str = None) -> pd.DataFrame:
    """Return all bid changes as a DataFrame, newest first."""
    conn = get_conn()
    if marketplace:
        df = pd.read_sql_query("""
            SELECT report_date, campaign_name, placement_type, marketplace,
                   bid_before, bid_after, roas, spend, purchases, profit
            FROM bid_changes
            WHERE marketplace=?
            ORDER BY report_date DESC, campaign_name
        """, conn, params=[marketplace])
    else:
        df = pd.read_sql_query("""
            SELECT report_date, campaign_name, placement_type, marketplace,
                   bid_before, bid_after, roas, spend, purchases, profit
            FROM bid_changes
            ORDER BY report_date DESC, campaign_name
        """, conn)
    conn.close()
    return df


def save_recommendation_note(rec_id: int, note: str):
    """Save a short note on a recommendation row."""
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE recommendations SET notes=? WHERE id=?",
            (note.strip() or None, rec_id)
        )
    conn.close()
