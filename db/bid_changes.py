"""
db/bid_changes.py — Auto-detected bid adjustment changes per placement.

Two tables:
  placement_snapshots — written on EVERY upload (ROAS / spend / purchases / bid_pct)
  bid_changes         — written only when bid % actually changed vs previous stored value

Together they power:
  • Bid history timeline per campaign/placement
  • "Did the change work?" — ROAS before the change vs ROAS at the next upload
  • Untreated filter — losing placements with no bid action in the last 30 days

NOTE: Amazon's placement performance reports do NOT include bid adjustment settings.
`record_bid_changes()` therefore never writes rows (bid_pct is always 0).
Use `log_manual_bid_change()` to record changes that were applied in Amazon's UI.
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


# ── Manual entry ───────────────────────────────────────────────────────────────

def log_manual_bid_change(
    campaign_name: str,
    placement_type: str,
    marketplace: str,
    change_date: str,
    bid_before: int,
    bid_after: int,
    notes: str = None,
) -> str:
    """
    Manually record a bid adjustment change that was applied in Amazon's UI.

    Automatically looks up the most recent placement_snapshot on or before
    change_date to populate roas / spend / purchases in the bid_changes row.
    That data is used by the Impact Report as Period 1 baseline.

    Returns a status string: "saved", "duplicate", or "error: <msg>".
    """
    if bid_before == bid_after:
        return "error: bid_before and bid_after are the same — no change to record."

    conn = get_conn()

    # Look up nearest snapshot ≤ change_date for P1 baseline
    snap = conn.execute("""
        SELECT roas, spend, purchases
        FROM placement_snapshots
        WHERE campaign_name = ? AND placement_type = ? AND marketplace = ?
          AND report_date <= ?
        ORDER BY report_date DESC
        LIMIT 1
    """, (campaign_name, placement_type, marketplace, change_date)).fetchone()

    roas      = round(float(snap["roas"]      or 0), 3) if snap else 0.0
    spend     = round(float(snap["spend"]     or 0), 2) if snap else 0.0
    purchases = int(snap["purchases"] or 0)            if snap else 0
    profit    = 0.0

    note_val = notes.strip() if notes and notes.strip() else None
    try:
        with conn:
            conn.execute("""
                INSERT OR IGNORE INTO bid_changes
                    (campaign_name, placement_type, marketplace, report_date,
                     bid_before, bid_after, roas, spend, purchases, profit, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                campaign_name, placement_type, marketplace, change_date,
                bid_before, bid_after, roas, spend, purchases, profit, note_val,
            ))
        affected = conn.execute("""
            SELECT COUNT(*) FROM bid_changes
            WHERE campaign_name=? AND placement_type=? AND marketplace=?
              AND report_date=? AND bid_before=? AND bid_after=?
        """, (campaign_name, placement_type, marketplace, change_date,
              bid_before, bid_after)).fetchone()[0]
        conn.close()
        return "saved" if affected else "duplicate"
    except Exception as e:
        conn.close()
        return f"error: {e}"


def delete_bid_change(campaign_name: str, placement_type: str,
                      marketplace: str, report_date: str) -> int:
    """
    Delete a specific bid change row.
    Returns number of rows deleted (0 = not found, 1 = deleted).
    """
    conn = get_conn()
    with conn:
        cur = conn.execute("""
            DELETE FROM bid_changes
            WHERE campaign_name=? AND placement_type=? AND marketplace=? AND report_date=?
        """, (campaign_name, placement_type, marketplace, report_date))
        deleted = cur.rowcount
    conn.close()
    return deleted


def get_campaigns_with_snapshots(marketplace: str = None) -> list[str]:
    """Return distinct campaign names that have placement_snapshots (for autocomplete)."""
    conn = get_conn()
    mp_filter = "WHERE marketplace=?" if marketplace else ""
    params = [marketplace] if marketplace else []
    rows = conn.execute(f"""
        SELECT DISTINCT campaign_name
        FROM placement_snapshots
        {mp_filter}
        ORDER BY campaign_name
    """, params).fetchall()
    conn.close()
    return [r[0] for r in rows]


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
                   bid_before, bid_after, roas, spend, purchases, profit, notes
            FROM bid_changes
            WHERE marketplace=?
            ORDER BY report_date DESC, campaign_name
        """, conn, params=[marketplace])
    else:
        df = pd.read_sql_query("""
            SELECT report_date, campaign_name, placement_type, marketplace,
                   bid_before, bid_after, roas, spend, purchases, profit, notes
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


def get_all_bid_effectiveness(marketplace: str = None) -> pd.DataFrame:
    """
    Impact Report — every documented change, with Period 1 and Period 2 ROAS.

    Includes:
      • bid_changes rows  — full P1/P2 data, bid % shown
      • note-only rows    — recommendation with note but no bid_change;
                            bid % shown as None, ROAS columns None

    Columns returned:
        change_date, campaign, placement, marketplace,
        notes, bid_before, bid_after,
        roas_p1, spend_p1, purchases_p1,
        roas_p2, spend_p2, purchases_p2,
        delta_roas, result
    Sorted newest first.
    """
    conn = get_conn()
    mp_filter = "WHERE bc.marketplace=?" if marketplace else ""
    mp_filter_r = "AND r.marketplace=?" if marketplace else ""
    params    = [marketplace] if marketplace else []

    # ── Part 1: bid_changes rows ──────────────────────────────────────────────
    changes = conn.execute(f"""
        SELECT bc.report_date AS change_date,
               bc.campaign_name, bc.placement_type, bc.marketplace,
               COALESCE(bc.notes, r.notes) AS notes,
               bc.bid_before, bc.bid_after,
               bc.roas    AS roas_p1,
               bc.spend   AS spend_p1,
               bc.purchases AS purchases_p1
        FROM bid_changes bc
        LEFT JOIN recommendations r
            ON r.campaign_name  = bc.campaign_name
           AND r.placement_type = bc.placement_type
           AND r.marketplace    = bc.marketplace
        {mp_filter}
        ORDER BY bc.report_date DESC, bc.campaign_name
    """, params).fetchall()

    rows = []
    for ch in changes:
        nxt = conn.execute("""
            SELECT report_date, roas, spend, purchases
            FROM placement_snapshots
            WHERE campaign_name=? AND placement_type=? AND marketplace=?
              AND report_date > ?
            ORDER BY report_date ASC LIMIT 1
        """, (ch["campaign_name"], ch["placement_type"],
              ch["marketplace"], ch["change_date"])).fetchone()

        roas_p1 = float(ch["roas_p1"] or 0)
        if nxt and nxt["roas"] is not None:
            roas_p2    = round(float(nxt["roas"]), 2)
            delta_roas = round(roas_p2 - roas_p1, 2)
            result     = "✅" if delta_roas > 0.05 else ("❌" if delta_roas < -0.05 else "➡️")
            p2_date    = nxt["report_date"]
            spend_p2   = nxt["spend"]
            purch_p2   = nxt["purchases"]
        else:
            roas_p2 = delta_roas = p2_date = spend_p2 = purch_p2 = None
            result  = "⏳"

        rows.append({
            "change_date":   ch["change_date"],
            "campaign":      ch["campaign_name"],
            "placement":     ch["placement_type"],
            "marketplace":   ch["marketplace"],
            "notes":         ch["notes"],
            "bid_before":    int(ch["bid_before"] or 0),
            "bid_after":     int(ch["bid_after"]  or 0),
            "roas_p1":       round(roas_p1, 2),
            "spend_p1":      round(float(ch["spend_p1"] or 0), 2),
            "purchases_p1":  int(ch["purchases_p1"] or 0),
            "roas_p2":       roas_p2,
            "spend_p2":      round(float(spend_p2 or 0), 2) if spend_p2 else None,
            "purchases_p2":  int(purch_p2 or 0) if purch_p2 is not None else None,
            "p2_date":       p2_date,
            "delta_roas":    delta_roas,
            "result":        result,
        })

    # ── Part 2: note-only (no bid_change row) ─────────────────────────────────
    noted = conn.execute(f"""
        SELECT r.date_given AS change_date,
               r.campaign_name, r.placement_type, r.marketplace,
               r.notes
        FROM recommendations r
        WHERE r.notes IS NOT NULL AND TRIM(r.notes) != ''
          {mp_filter_r}
          AND NOT EXISTS (
              SELECT 1 FROM bid_changes bc2
              WHERE bc2.campaign_name  = r.campaign_name
                AND bc2.placement_type = r.placement_type
                AND bc2.marketplace    = r.marketplace
          )
    """, params).fetchall()

    for n in noted:
        ch_date = n["change_date"]
        camp    = n["campaign_name"]
        pl      = n["placement_type"]
        mkt     = n["marketplace"]

        # P1 = most recent snapshot ON OR BEFORE the note date
        p1 = conn.execute("""
            SELECT report_date, roas, spend, purchases
            FROM placement_snapshots
            WHERE campaign_name=? AND placement_type=? AND marketplace=?
              AND report_date <= ?
            ORDER BY report_date DESC LIMIT 1
        """, (camp, pl, mkt, ch_date)).fetchone()

        # P2 = first snapshot AFTER the note date
        p2 = conn.execute("""
            SELECT report_date, roas, spend, purchases
            FROM placement_snapshots
            WHERE campaign_name=? AND placement_type=? AND marketplace=?
              AND report_date > ?
            ORDER BY report_date ASC LIMIT 1
        """, (camp, pl, mkt, ch_date)).fetchone()

        roas_p1 = round(float(p1["roas"] or 0), 2) if p1 else None
        roas_p2 = round(float(p2["roas"] or 0), 2) if p2 else None

        if roas_p1 is not None and roas_p2 is not None:
            delta_roas = round(roas_p2 - roas_p1, 2)
            result     = "✅" if delta_roas > 0.05 else ("❌" if delta_roas < -0.05 else "➡️")
        else:
            delta_roas = None
            result     = "📝"  # no snapshots available yet

        rows.append({
            "change_date":   ch_date,
            "campaign":      camp,
            "placement":     pl,
            "marketplace":   mkt,
            "notes":         n["notes"],
            "bid_before":    None,
            "bid_after":     None,
            "roas_p1":       roas_p1,
            "spend_p1":      round(float(p1["spend"] or 0), 2) if p1 else None,
            "purchases_p1":  int(p1["purchases"] or 0) if p1 else None,
            "roas_p2":       roas_p2,
            "spend_p2":      round(float(p2["spend"] or 0), 2) if p2 else None,
            "purchases_p2":  int(p2["purchases"] or 0) if p2 else None,
            "p2_date":       p2["report_date"] if p2 else None,
            "delta_roas":    delta_roas,
            "result":        result,
        })

    conn.close()
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("change_date", ascending=False).reset_index(drop=True)
    return df


def get_unified_changes(marketplace: str = None) -> pd.DataFrame:
    """
    Single table combining bid_changes and noted recommendations.

    Rules:
    - Every bid_changes row appears (Was%/Now% filled).
      Its note comes from bid_changes.notes, falling back to the
      current recommendation note for that campaign/placement.
    - Recommendations with a note that have NO bid_change row
      appear once with Was%/Now% as None (shown as N/A).

    Columns: change_date, campaign_name, placement_type, marketplace,
             notes, bid_before, bid_after
    Sorted newest first.
    """
    conn = get_conn()
    mp_filter = "WHERE bc.marketplace = ?" if marketplace else ""
    params_bc  = [marketplace] if marketplace else []
    mp_filter_r = "AND r2.marketplace = ?" if marketplace else ""
    params_r   = [marketplace] if marketplace else []

    # Part 1: bid_changes rows (always shown), note from bid_change or recommendation
    df_bc = pd.read_sql_query(f"""
        SELECT
            bc.report_date   AS change_date,
            bc.campaign_name,
            bc.placement_type,
            bc.marketplace,
            COALESCE(bc.notes, r.notes) AS notes,
            bc.bid_before,
            bc.bid_after
        FROM bid_changes bc
        LEFT JOIN recommendations r
            ON r.campaign_name  = bc.campaign_name
           AND r.placement_type = bc.placement_type
           AND r.marketplace    = bc.marketplace
        {mp_filter}
        ORDER BY bc.report_date DESC
    """, conn, params=params_bc)

    # Part 2: recommendations with note, no bid_change row exists
    df_noted = pd.read_sql_query(f"""
        SELECT
            r2.date_given    AS change_date,
            r2.campaign_name,
            r2.placement_type,
            r2.marketplace,
            r2.notes,
            NULL             AS bid_before,
            NULL             AS bid_after
        FROM recommendations r2
        WHERE r2.notes IS NOT NULL AND TRIM(r2.notes) != ''
          {mp_filter_r}
          AND NOT EXISTS (
              SELECT 1 FROM bid_changes bc2
              WHERE bc2.campaign_name  = r2.campaign_name
                AND bc2.placement_type = r2.placement_type
                AND bc2.marketplace    = r2.marketplace
          )
    """, conn, params=params_r)

    conn.close()

    df = pd.concat([df_bc, df_noted], ignore_index=True)
    if df.empty:
        return df
    df = df.sort_values("change_date", ascending=False).reset_index(drop=True)
    return df


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
