"""
db/bid_changes.py — Auto-detected bid adjustment changes per placement.

Two tables:
  placement_snapshots — written on EVERY upload (ROAS / spend / purchases / bid_pct)
  bid_changes         — written only when bid % actually changed vs previous stored value

Together they power:
  • Bid history timeline per campaign/placement
  • "Did the change work?" — ROAS before the change vs ROAS at the next upload
  • Untreated filter — losing placements with no bid action in the last 30 days
"""
from .database import get_conn
import pandas as pd


# ── Snapshots (every upload) ───────────────────────────────────────────────────

def record_placement_snapshots(results: list, report_date: str, marketplace: str):
    """
    Called after every analysis run regardless of bid changes.
    Saves one row per campaign+placement with spend > 0.
    Idempotent — INSERT OR IGNORE so re-running the same date is safe.
    """
    conn = get_conn()
    _PL_MAP = {"Top": "Top of Search", "Rest": "Rest of Search", "Product": "Product Pages"}

    for r in results:
        algo = getattr(r, "placement_algorithm", {}) or {}
        placements_data = algo.get("placements", [])

        for pl_short, pl_data in [("Top", r.top), ("Rest", r.rest), ("Product", r.product)]:
            if pl_data is None or (pl_data.spend or 0) == 0:
                continue

            placement_type = _PL_MAP.get(pl_short, pl_short)

            # Current bid %
            bid_pct = None
            for p in placements_data:
                if p.get("pl") == pl_short:
                    bid_pct = round(float(p.get("current_adj", 0) or 0) * 100)
                    break

            try:
                with conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO placement_snapshots
                            (campaign_name, placement_type, marketplace, report_date,
                             roas, spend, purchases, bid_pct)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        r.campaign, placement_type, marketplace, report_date,
                        round(float(pl_data.roas or 0), 3),
                        round(float(pl_data.spend or 0), 2),
                        int(pl_data.orders or 0),
                        bid_pct,
                    ))
            except Exception:
                pass

    conn.close()


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


def get_bid_effectiveness(campaign_name: str, placement_type: str,
                          marketplace: str) -> list[dict]:
    """
    For each bid change on this campaign/placement, look up:
      roas_at_change  — ROAS in the report that triggered the change
                        (already stored in bid_changes.roas)
      roas_after      — ROAS in the first placement_snapshot AFTER that date
      delta           — roas_after − roas_at_change
      result          — ✅ improved  /  ❌ worsened  /  ➡️ flat  /  ⏳ pending

    Returns newest-first list of dicts.
    """
    conn = get_conn()

    changes = conn.execute("""
        SELECT report_date, bid_before, bid_after, roas, spend, purchases, profit
        FROM bid_changes
        WHERE campaign_name=? AND placement_type=? AND marketplace=?
        ORDER BY report_date DESC
    """, (campaign_name, placement_type, marketplace)).fetchall()

    rows = []
    for ch in changes:
        ch_date  = ch["report_date"]
        roas_at  = float(ch["roas"] or 0)

        nxt = conn.execute("""
            SELECT roas FROM placement_snapshots
            WHERE campaign_name=? AND placement_type=? AND marketplace=?
              AND report_date > ?
            ORDER BY report_date ASC LIMIT 1
        """, (campaign_name, placement_type, marketplace, ch_date)).fetchone()

        if nxt and nxt["roas"] is not None:
            roas_after = round(float(nxt["roas"]), 2)
            delta      = round(roas_after - roas_at, 2)
            if delta > 0.05:
                result = "✅"
            elif delta < -0.05:
                result = "❌"
            else:
                result = "➡️"
        else:
            roas_after = None
            delta      = None
            result     = "⏳"

        rows.append({
            "report_date":    ch_date,
            "bid_before":     int(ch["bid_before"] or 0),
            "bid_after":      int(ch["bid_after"]  or 0),
            "roas_at_change": round(roas_at, 2),
            "roas_after":     roas_after,
            "delta":          delta,
            "result":         result,
            "spend":          ch["spend"],
            "purchases":      ch["purchases"],
            "profit":         ch["profit"],
        })

    conn.close()
    return rows


def get_last_effectiveness_bulk(marketplace: str = None) -> dict:
    """
    Returns a dict keyed by (campaign_name, placement_type, marketplace)
    whose value is a short result string: "✅ +0.5x", "❌ -0.3x", "➡️ ~0",
    "⏳ Pending", or "—" (no bid changes recorded).

    Used to populate the 'Last Result' column in the Workbench table.
    """
    conn = get_conn()

    # Latest bid_change per campaign+placement
    mp_filter = "AND marketplace=?" if marketplace else ""
    params = [marketplace] if marketplace else []

    latest_changes = conn.execute(f"""
        SELECT campaign_name, placement_type, marketplace,
               report_date, roas
        FROM bid_changes
        WHERE id IN (
            SELECT MAX(id)
            FROM bid_changes
            {("WHERE marketplace=?" if marketplace else "")}
            GROUP BY campaign_name, placement_type, marketplace
        )
    """, params).fetchall()

    result = {}
    for ch in latest_changes:
        key     = (ch["campaign_name"], ch["placement_type"], ch["marketplace"])
        roas_at = float(ch["roas"] or 0)

        nxt = conn.execute("""
            SELECT roas FROM placement_snapshots
            WHERE campaign_name=? AND placement_type=? AND marketplace=?
              AND report_date > ?
            ORDER BY report_date ASC LIMIT 1
        """, (ch["campaign_name"], ch["placement_type"], ch["marketplace"],
              ch["report_date"])).fetchone()

        if nxt and nxt["roas"] is not None:
            roas_after = float(nxt["roas"])
            delta      = round(roas_after - roas_at, 2)
            sign       = "+" if delta > 0 else ""
            if delta > 0.05:
                result[key] = f"✅ {sign}{delta:.1f}x"
            elif delta < -0.05:
                result[key] = f"❌ {sign}{delta:.1f}x"
            else:
                result[key] = "➡️ ~0"
        else:
            result[key] = "⏳ Pending"

    conn.close()
    return result


def get_untreated_losing(marketplace: str = None) -> set:
    """
    Returns a set of (campaign_name, placement_type, marketplace) tuples
    for recommendations that are:
      1. Currently 'losing' (reasoning starts with LOSING)
      2. Have had no bid change in the last 30 days
    Used to power the 'Untreated' filter in the Workbench.
    """
    conn = get_conn()
    mp_filter = "AND r.marketplace=?" if marketplace else ""
    params = [marketplace] if marketplace else []

    rows = conn.execute(f"""
        SELECT r.campaign_name, r.placement_type, r.marketplace
        FROM recommendations r
        WHERE UPPER(r.reasoning) LIKE 'LOSING%'
          {mp_filter}
          AND NOT EXISTS (
              SELECT 1 FROM bid_changes bc
              WHERE bc.campaign_name = r.campaign_name
                AND bc.placement_type = r.placement_type
                AND bc.marketplace    = r.marketplace
                AND bc.report_date   >= date('now', '-30 days')
          )
    """, params).fetchall()

    conn.close()
    return {(r["campaign_name"], r["placement_type"], r["marketplace"]) for r in rows}


def save_recommendation_note(rec_id: int, note: str):
    """Save a short note on a single recommendation row."""
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE recommendations SET notes=? WHERE id=?",
            (note.strip() or None, rec_id)
        )
    conn.close()


def save_campaign_note(campaign_name: str, marketplace: str, note: str) -> int:
    """
    Save a note on ALL recommendation rows that share the same campaign_name
    and marketplace (i.e. all placements of that campaign).
    Returns the number of rows updated.
    """
    conn = get_conn()
    with conn:
        cur = conn.execute(
            "UPDATE recommendations SET notes=? WHERE campaign_name=? AND marketplace=?",
            (note.strip() or None, campaign_name, marketplace)
        )
        updated = cur.rowcount
    conn.close()
    return updated
