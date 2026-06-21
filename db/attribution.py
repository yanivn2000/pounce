"""
db/attribution.py — Change Attribution ($) + settle-time verdicts.

Scores every documented bid change by its **dollar-profit impact**, not just a
ROAS arrow. Profit comes from the 30-day placement reports already stored in
`campaign_performance` (total_profit per campaign/placement/snapshot).

Settle-time: because the reports are 30-day *trailing* windows, a change's
effect is only "clean" in a snapshot taken at least `settle_days` (default 30)
after the change date — until then the window still overlaps pre-change days, so
the read is preliminary. Verdicts stay locked as "settling" until a clean
snapshot exists.

This module is read-only; it derives everything from existing tables
(bid_changes + campaign_performance). No schema changes.
"""
from datetime import date, datetime, timedelta
from .database import get_conn

# bid_changes uses long placement names; campaign_performance uses short ones.
_PL_LONG_TO_SHORT = {
    "Top of Search":  "Top",
    "Rest of Search": "Rest",
    "Product Pages":  "Product",
}


def _to_date(s: str) -> date:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def get_change_attribution(marketplace: str = None,
                           settle_days: int = 30,
                           win_threshold_usd: float = 20.0) -> list[dict]:
    """Return one attribution dict per documented bid change, newest first.

    Each dict:
      change_date, campaign, placement, marketplace, bid_before, bid_after, notes,
      profit_p1, profit_p2 (30-day profit before / after),
      monthly_delta (per-30-day $ impact), cumulative_usd (accrued since change,
      only when settled), days_since, settled (bool), clean_read_date,
      verdict (win|loss|flat|settling|pending|no_baseline), purchases, p1_date, p2_date
    """
    conn = get_conn()
    today = date.today()

    mp_filter = "WHERE marketplace=?" if marketplace else ""
    params = [marketplace] if marketplace else []
    changes = conn.execute(f"""
        SELECT id, report_date, campaign_name, placement_type, marketplace,
               bid_before, bid_after, notes
        FROM bid_changes
        {mp_filter}
        ORDER BY report_date DESC, campaign_name
    """, params).fetchall()

    def _perf(camp, mkt, pl_short, op, ref_date, order):
        return conn.execute(f"""
            SELECT snapshot_date, total_profit, roas, spend, purchases, breakeven_roas
            FROM campaign_performance
            WHERE campaign_name=? AND marketplace=? AND placement_type=?
              AND snapshot_date {op} ?
            ORDER BY snapshot_date {order} LIMIT 1
        """, (camp, mkt, pl_short, ref_date)).fetchone()

    rows = []
    for ch in changes:
        pl_short  = _PL_LONG_TO_SHORT.get(ch["placement_type"], ch["placement_type"])
        cdate     = str(ch["report_date"])[:10]
        camp, mkt = ch["campaign_name"], ch["marketplace"]
        clean_date = (_to_date(cdate) + timedelta(days=settle_days)).isoformat()
        day_after  = (_to_date(cdate) + timedelta(days=1)).isoformat()

        p1       = _perf(camp, mkt, pl_short, "<=", cdate, "DESC")        # pre-change
        p2_clean = _perf(camp, mkt, pl_short, ">=", clean_date, "ASC")    # clean post
        p2_prelim = _perf(camp, mkt, pl_short, ">=", day_after, "ASC")    # any post
        days_since = (today - _to_date(cdate)).days

        profit_p1 = float(p1["total_profit"]) if p1 else None
        settled   = p2_clean is not None
        p2        = p2_clean or p2_prelim
        profit_p2 = float(p2["total_profit"]) if p2 else None

        monthly_delta = (profit_p2 - profit_p1) \
            if (profit_p1 is not None and profit_p2 is not None) else None
        cumulative = (monthly_delta * days_since / 30.0) \
            if (settled and monthly_delta is not None) else None

        if profit_p1 is None:
            verdict = "no_baseline"
        elif profit_p2 is None:
            verdict = "pending"
        elif not settled:
            verdict = "settling"
        elif monthly_delta > win_threshold_usd:
            verdict = "win"
        elif monthly_delta < -win_threshold_usd:
            verdict = "loss"
        else:
            verdict = "flat"

        purch = (int(p1["purchases"]) if p1 and p1["purchases"] else 0) \
              + (int(p2["purchases"]) if p2 and p2["purchases"] else 0)

        rows.append({
            "id":             ch["id"],
            "change_date":    cdate,
            "campaign":       camp,
            "placement":      ch["placement_type"],
            "marketplace":    mkt,
            "bid_before":     int(ch["bid_before"] or 0),
            "bid_after":      int(ch["bid_after"] or 0),
            "notes":          ch["notes"],
            "profit_p1":      round(profit_p1, 2) if profit_p1 is not None else None,
            "profit_p2":      round(profit_p2, 2) if profit_p2 is not None else None,
            "monthly_delta":  round(monthly_delta, 2) if monthly_delta is not None else None,
            "cumulative_usd": round(cumulative, 2) if cumulative is not None else None,
            "days_since":     days_since,
            "settled":        settled,
            "clean_read_date": clean_date,
            "verdict":        verdict,
            "purchases":      purch,
            "p1_date":        str(p1["snapshot_date"])[:10] if p1 else None,
            "p2_date":        str(p2["snapshot_date"])[:10] if p2 else None,
        })

    conn.close()
    return rows


def summarize_attribution(rows: list[dict]) -> dict:
    """Roll up attribution rows into scoreboard headline numbers."""
    settled = [r for r in rows if r["verdict"] in ("win", "loss", "flat")]
    wins    = [r for r in settled if r["verdict"] == "win"]
    losses  = [r for r in settled if r["verdict"] == "loss"]
    realized = sum(r["cumulative_usd"] or 0 for r in settled)
    monthly_run_rate = sum(r["monthly_delta"] or 0 for r in settled)
    decided = len(wins) + len(losses)
    return {
        "total":            len(rows),
        "settled":          len(settled),
        "wins":             len(wins),
        "losses":           len(losses),
        "flat":             len(settled) - len(wins) - len(losses),
        "settling":         sum(1 for r in rows if r["verdict"] == "settling"),
        "pending":          sum(1 for r in rows if r["verdict"] == "pending"),
        "no_baseline":      sum(1 for r in rows if r["verdict"] == "no_baseline"),
        "realized_usd":     round(realized, 2),
        "monthly_run_rate": round(monthly_run_rate, 2),
        "win_rate":         round(100 * len(wins) / decided, 0) if decided else None,
    }


def undo_hint(row: dict) -> str:
    """Plain-language reverse action for a losing change."""
    return (f"Revert: set **{row['placement']}** bid on "
            f"**{row['campaign']}** back from {row['bid_after']}% "
            f"to {row['bid_before']}%.")
