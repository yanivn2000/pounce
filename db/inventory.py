"""
db/inventory.py — Inventory import, query and manual-entry helpers.
"""
import io
import math
import pandas as pd
import streamlit as st
from datetime import date, timedelta
from db.database import get_conn


def _orders_fingerprint() -> tuple:
    """Cheap change token for the (append-only) orders table — bumps on any
    insert/delete so cached derivations refresh immediately."""
    conn = get_conn()
    try:
        r = conn.execute("SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM orders").fetchone()
        return (int(r[0] or 0), int(r[1] or 0))
    except Exception:
        return (0, 0)
    finally:
        conn.close()

# ── Encoding-safe CSV reader ─────────────────────────────────────────────────
def _read_csv_bytes(file_obj) -> str:
    """
    Read a file-like object and decode it robustly.
    Amazon reports are often Windows-1252, not UTF-8.
    Returns the decoded text string.
    """
    raw = file_obj.read()
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8-sig", "windows-1252", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _csv_to_df(file_obj, sep: str = None) -> pd.DataFrame:
    """Decode file, auto-detect separator, return DataFrame."""
    text = _read_csv_bytes(file_obj)
    first_line = text.split("\n")[0]
    if sep is None:
        sep = "\t" if first_line.count("\t") >= first_line.count(",") else ","
    return pd.read_csv(io.StringIO(text), dtype=str, sep=sep)


# ── Location metadata ─────────────────────────────────────────────────────────
LOCATIONS = {
    "FBA_US":     {"label": "FBA US",       "type": "FBA",        "marketplace": "amazon.com"},
    "FBA_CA":     {"label": "FBA CA",       "type": "FBA",        "marketplace": "amazon.ca"},
    "FBA_UK":     {"label": "FBA UK",       "type": "FBA",        "marketplace": "amazon.co.uk"},
    "AWD_US":     {"label": "AWD US",       "type": "AWD",        "marketplace": "amazon.com"},
    "AWD_CN":     {"label": "AWD CN ⚠️",    "type": "AWD",        "marketplace": None},
    "3PL_UK":     {"label": "3PL UK",       "type": "3PL",        "marketplace": "amazon.co.uk"},
    "WH_CN":      {"label": "WH China",     "type": "Warehouse",  "marketplace": None},
    "PRODUCTION": {"label": "Production",   "type": "Production", "marketplace": None},
}

FBA_LOCATIONS   = ["FBA_US", "FBA_CA", "FBA_UK"]
AWD_LOCATIONS   = ["AWD_US", "AWD_CN"]
MANUAL_LOCATIONS = ["WH_CN", "PRODUCTION"]

ALERT_CRITICAL  = 45   # days
ALERT_URGENT    = 90   # days
ALERT_PLAN      = 135  # days
ALERT_AGING     = 90   # days of stock = aging if velocity is low


# ── Importers ─────────────────────────────────────────────────────────────────

def import_fba_csv(file_obj, location: str, snapshot_date: str = None) -> tuple[int, list[str]]:
    """
    Parse an Amazon FBA Manage Inventory Health CSV and upsert into inventory_snapshots.
    location must be one of FBA_US, FBA_CA, FBA_UK.
    """
    if snapshot_date is None:
        snapshot_date = str(date.today())
    warnings = []
    try:
        df = _csv_to_df(file_obj)
    except Exception as e:
        return 0, [f"Failed to read CSV: {e}"]

    df.columns = [c.strip().lower() for c in df.columns]

    if "asin" not in df.columns:
        return 0, [f"No 'asin' column found. Columns: {list(df.columns)}"]

    def _int(val):
        try:
            v = float(val)
            return 0 if math.isnan(v) else int(v)
        except (TypeError, ValueError):
            return 0

    conn = get_conn()
    imported = 0
    with conn:
        for _, row in df.iterrows():
            asin = str(row.get("asin", "")).strip().upper()
            if not asin or asin == "NAN":
                continue
            sku   = str(row.get("sku", "") or "").strip()
            title = str(row.get("product-name", "") or "").strip()
            available = _int(row.get("afn-fulfillable-quantity", 0))
            inbound   = (_int(row.get("afn-inbound-shipped-quantity", 0))
                         + _int(row.get("afn-inbound-receiving-quantity", 0)))
            reserved  = _int(row.get("afn-reserved-quantity", 0))
            try:
                conn.execute("""
                    INSERT INTO inventory_snapshots
                        (snapshot_date, asin, sku, title, location, units_available, units_inbound, units_reserved, source)
                    VALUES (?,?,?,?,?,?,?,?,'upload')
                    ON CONFLICT(asin, location, snapshot_date) DO UPDATE SET
                        sku=excluded.sku, title=excluded.title,
                        units_available=excluded.units_available,
                        units_inbound=excluded.units_inbound,
                        units_reserved=excluded.units_reserved
                """, (snapshot_date, asin, sku, title, location, available, inbound, reserved))
                imported += 1
                # auto-populate sku_asin_map
                if sku:
                    conn.execute("""
                        INSERT OR IGNORE INTO sku_asin_map (sku, asin, title, source)
                        VALUES (?, ?, ?, ?)
                    """, (sku, asin, title, location))
            except Exception as e:
                warnings.append(f"Row skipped ({asin}): {e}")
    conn.close()
    return imported, warnings


def import_awd_csv(file_obj, snapshot_date: str = None) -> tuple[int, list[str]]:
    """
    Parse an Amazon AWD inventory CSV (has 4-row metadata header before column names).
    Creates AWD_US and AWD_CN rows per ASIN.
    """
    if snapshot_date is None:
        snapshot_date = str(date.today())
    warnings = []
    try:
        text  = _read_csv_bytes(file_obj)
        lines = text.splitlines()
        # Find the header row (contains "ASIN")
        header_idx = next((i for i, l in enumerate(lines) if "ASIN" in l), None)
        if header_idx is None:
            return 0, ["Could not find header row containing 'ASIN'."]
        first_line = lines[header_idx]
        sep = "\t" if first_line.count("\t") >= first_line.count(",") else ","
        df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])), dtype=str, sep=sep)
    except Exception as e:
        return 0, [f"Failed to read AWD CSV: {e}"]

    df.columns = [c.strip() for c in df.columns]

    if "ASIN" not in df.columns:
        return 0, [f"No ASIN column found. Columns: {list(df.columns)}"]

    def _int(col):
        if col not in df.columns:
            return pd.Series([0] * len(df))
        return pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["_awd_us"]      = _int("Available Units in AWD (US)")
    df["_awd_cn"]      = _int("Available Units in AWD (CN)")
    df["_inbound_awd"] = _int("Inbound to AWD (units)")

    conn = get_conn()
    imported = 0
    with conn:
        for _, row in df.iterrows():
            asin = str(row.get("ASIN", "")).strip().upper()
            if not asin or asin == "NAN":
                continue
            sku   = str(row.get("SKU", "") or "").strip()
            title = str(row.get("Product Name", "") or "").strip()
            for loc, avail in [("AWD_US", int(row["_awd_us"])), ("AWD_CN", int(row["_awd_cn"]))]:
                try:
                    conn.execute("""
                        INSERT INTO inventory_snapshots
                            (snapshot_date, asin, sku, title, location, units_available, units_inbound, units_reserved, source)
                        VALUES (?,?,?,?,?,?,?,?,'upload')
                        ON CONFLICT(asin, location, snapshot_date) DO UPDATE SET
                            units_available=excluded.units_available,
                            units_inbound=excluded.units_inbound
                    """, (snapshot_date, asin, sku, title, loc, avail, int(row["_inbound_awd"]) if loc == "AWD_US" else 0, 0))
                    imported += 1
                except Exception as e:
                    warnings.append(f"AWD row skipped ({asin}/{loc}): {e}")
    conn.close()
    return imported, warnings


def import_spm_csv(file_obj, snapshot_date: str = None) -> tuple[int, list[str], list[str]]:
    """
    Parse an SPM (UK 3PL) CSV. Returns (imported, warnings, unmapped_skus).
    Unmapped SKUs need to be resolved via the sku_asin_map table.
    """
    if snapshot_date is None:
        snapshot_date = str(date.today())
    warnings = []
    try:
        df = _csv_to_df(file_obj)
    except Exception as e:
        return 0, [f"Failed to read CSV: {e}"], []

    df.columns = [c.strip() for c in df.columns]
    if "SKU" not in df.columns:
        return 0, [f"No SKU column. Columns: {list(df.columns)}"], []

    # Load existing SKU→ASIN map
    conn = get_conn()
    map_rows = conn.execute("SELECT sku, asin FROM sku_asin_map").fetchall()
    sku_map = {r["sku"]: r["asin"] for r in map_rows}

    def _int(row, col):
        try:
            v = float(row.get(col, 0) or 0)
            return 0 if math.isnan(v) else int(v)
        except (TypeError, ValueError):
            return 0

    imported = 0
    unmapped = []
    with conn:
        for _, row in df.iterrows():
            sku = str(row.get("SKU", "")).strip()
            if not sku:
                continue
            asin = sku_map.get(sku)
            if not asin:
                unmapped.append(sku)
                continue
            available = _int(row, "OnHand")
            inbound   = _int(row, "InTransit") + _int(row, "OnOrder")
            title     = str(row.get("Name", "") or "").strip()
            try:
                conn.execute("""
                    INSERT INTO inventory_snapshots
                        (snapshot_date, asin, sku, title, location, units_available, units_inbound, units_reserved, source)
                    VALUES (?,?,?,?,?,?,?,?,'upload')
                    ON CONFLICT(asin, location, snapshot_date) DO UPDATE SET
                        units_available=excluded.units_available,
                        units_inbound=excluded.units_inbound
                """, (snapshot_date, asin, sku, title, "3PL_UK", available, inbound, 0))
                imported += 1
            except Exception as e:
                warnings.append(f"SPM row skipped ({sku}): {e}")
    conn.close()
    return imported, warnings, list(set(unmapped))


def import_whcn_csv(file_obj, snapshot_date: str = None) -> tuple[int, list[str]]:
    """
    Parse a simple WH_CN CSV with columns: asin, units (and optionally title).
    """
    if snapshot_date is None:
        snapshot_date = str(date.today())
    warnings = []
    try:
        df = _csv_to_df(file_obj, sep=",")
    except Exception as e:
        return 0, [f"Failed to read CSV: {e}"]
    df.columns = [c.strip().lower() for c in df.columns]
    if "asin" not in df.columns or "units" not in df.columns:
        return 0, [f"Need 'asin' and 'units' columns. Found: {list(df.columns)}"]
    conn = get_conn()
    imported = 0
    with conn:
        for _, row in df.iterrows():
            asin = str(row.get("asin", "")).strip().upper()
            if not asin:
                continue
            try:
                units = int(float(row.get("units", 0) or 0))
            except (ValueError, TypeError):
                units = 0
            title = str(row.get("title", "") or "").strip()
            try:
                conn.execute("""
                    INSERT INTO inventory_snapshots
                        (snapshot_date, asin, title, location, units_available, units_inbound, units_reserved, source)
                    VALUES (?,?,?,'WH_CN',?,0,0,'upload')
                    ON CONFLICT(asin, location, snapshot_date) DO UPDATE SET
                        units_available=excluded.units_available, title=excluded.title
                """, (snapshot_date, asin, title, units))
                imported += 1
            except Exception as e:
                warnings.append(f"WH_CN row skipped ({asin}): {e}")
    conn.close()
    return imported, warnings


def upsert_manual_inventory(asin: str, location: str, units: int, snapshot_date: str = None):
    """Save a single manual inventory entry (Production or WH_CN)."""
    if snapshot_date is None:
        snapshot_date = str(date.today())
    conn = get_conn()
    with conn:
        conn.execute("""
            INSERT INTO inventory_snapshots
                (snapshot_date, asin, location, units_available, units_inbound, units_reserved, source)
            VALUES (?,?,?,?,0,0,'manual')
            ON CONFLICT(asin, location, snapshot_date) DO UPDATE SET
                units_available=excluded.units_available
        """, (snapshot_date, asin, location, units))
    conn.close()


def save_sku_mapping(sku: str, asin: str, title: str = ""):
    conn = get_conn()
    with conn:
        conn.execute("""
            INSERT OR REPLACE INTO sku_asin_map (sku, asin, title, source)
            VALUES (?, ?, ?, 'manual')
        """, (sku, asin, title))
    conn.close()


# ── Queries ───────────────────────────────────────────────────────────────────

def get_latest_inventory() -> pd.DataFrame:
    """
    Return one row per (asin, location) using the most recent snapshot_date.
    Columns: asin, location, units_available, units_inbound, units_reserved, snapshot_date, title, sku
    """
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT s.asin, s.location, s.units_available, s.units_inbound,
               s.units_reserved, s.snapshot_date, s.title, s.sku
        FROM inventory_snapshots s
        INNER JOIN (
            SELECT asin, location, MAX(snapshot_date) AS max_date
            FROM inventory_snapshots
            GROUP BY asin, location
        ) latest ON s.asin = latest.asin
                       AND s.location = latest.location
                       AND s.snapshot_date = latest.max_date
    """, conn)
    conn.close()
    return df


def get_inventory_overview(cost_map: dict, avg_daily_sales: dict) -> pd.DataFrame:
    """
    Build the wide-format inventory overview table.
    cost_map: {asin: landed_cost}
    avg_daily_sales: {(asin, marketplace): avg_units_per_day}
    Returns one row per ASIN with columns for each location + computed metrics.
    """
    inv = get_latest_inventory()
    if inv.empty:
        return pd.DataFrame()

    # Pivot: one row per ASIN, one column per location
    pivot = inv.pivot_table(
        index="asin",
        columns="location",
        values="units_available",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()

    pivot_ib = inv.pivot_table(
        index="asin",
        columns="location",
        values="units_inbound",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()

    # Ensure all location columns exist
    for loc in LOCATIONS:
        if loc not in pivot.columns:
            pivot[loc] = 0
        if loc not in pivot_ib.columns:
            pivot_ib[loc] = 0

    # AWD "Inbound to AWD (units)" is owned stock in transit to the AWD
    # warehouse. Fold it into the AWD columns so it's counted in the column,
    # Total and Value — otherwise these units are invisible once the matching
    # manual shipment is (intentionally) excluded, and inventory is undercounted.
    _ib_by_asin = pivot_ib.set_index("asin")
    for _loc in AWD_LOCATIONS:
        if _loc in pivot.columns and _loc in _ib_by_asin.columns:
            pivot[_loc] = pivot.apply(
                lambda r, L=_loc: int(r[L]) + int(_ib_by_asin[L].get(r["asin"], 0) or 0),
                axis=1,
            )

    # Attach title — prefer product_name from product_costs (user-defined),
    # fall back to title from inventory snapshots (Amazon CSV placeholder text)
    conn_t = get_conn()
    pc_titles = {
        r["asin"].strip().upper(): r["product_name"]
        for r in conn_t.execute(
            "SELECT asin, product_name FROM product_costs WHERE product_name IS NOT NULL AND product_name != ''"
        ).fetchall()
    }
    conn_t.close()

    snap_titles = (inv[inv["title"].notna() & (inv["title"] != "")]
                   .groupby("asin")["title"].first())
    pivot["title"] = pivot["asin"].apply(
        lambda a: pc_titles.get(str(a).strip().upper())
                  or snap_titles.get(a, "")
    )

    # Totals
    fba_cols = [c for c in FBA_LOCATIONS if c in pivot.columns]
    awd_cols = [c for c in AWD_LOCATIONS if c in pivot.columns]
    all_avail = fba_cols + awd_cols + ["3PL_UK", "WH_CN"]
    pivot["total_available"] = pivot[[c for c in all_avail if c in pivot.columns]].sum(axis=1)

    # Value = total units × landed cost
    pivot["value_usd"] = pivot["asin"].map(cost_map).fillna(0) * pivot["total_available"]

    # Days of stock per FBA marketplace
    mkt_map = {"FBA_US": "amazon.com", "FBA_CA": "amazon.ca", "FBA_UK": "amazon.co.uk"}
    for fba_loc, mkt in mkt_map.items():
        col = f"days_{fba_loc.lower()}"
        def _days(row, fl=fba_loc, m=mkt):
            velocity = avg_daily_sales.get((row["asin"], m), 0)
            if not velocity or velocity <= 0:
                return None
            # count FBA live + inbound for that market + relevant AWD
            avail_for_mkt = row.get(fl, 0)
            ib_fba = pivot_ib.set_index("asin").loc[row["asin"], fl] if row["asin"] in pivot_ib.set_index("asin").index else 0
            awd_us = row.get("AWD_US", 0) if m == "amazon.com" else 0
            tpl = row.get("3PL_UK", 0) if m == "amazon.co.uk" else 0
            total = avail_for_mkt + ib_fba + awd_us + tpl
            return round(total / velocity)
        pivot[col] = pivot.apply(_days, axis=1)

    return pivot


@st.cache_data(ttl=300, show_spinner=False)
def _avg_daily_sales_cached(days: int, fp: tuple, today: str) -> dict:
    # fp/today are part of the cache key (no leading underscore, or Streamlit
    # would drop them from the key and never invalidate).
    conn = get_conn()
    cutoff = str(date.fromisoformat(today) - timedelta(days=days))
    rows = conn.execute("""
        SELECT asin, marketplace, SUM(quantity) as total_qty
        FROM orders
        WHERE order_date >= ?
        GROUP BY asin, marketplace
    """, (cutoff,)).fetchall()
    conn.close()
    return {(r["asin"], r["marketplace"]): r["total_qty"] / days for r in rows}


def get_avg_daily_sales(days: int = 30) -> dict:
    """
    Compute avg daily units sold per (asin, marketplace) from orders table.
    Returns {(asin, marketplace): avg_units_per_day}

    Cached (keyed on the orders fingerprint + today) so it isn't recomputed on
    every Streamlit rerun; refreshes the instant new orders are imported.
    """
    return _avg_daily_sales_cached(days, _orders_fingerprint(), date.today().isoformat())


def get_sold_units(marketplace: str, start_date: str, end_date: str) -> dict[str, int]:
    """
    Return {asin: total_units_sold} for the given marketplace and date range (inclusive).
    marketplace: e.g. "amazon.com", "amazon.ca", "amazon.co.uk"
    start_date / end_date: "YYYY-MM-DD" strings
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT asin, SUM(quantity) AS total
        FROM orders
        WHERE marketplace = ?
          AND order_date  >= ?
          AND order_date  <= ?
        GROUP BY asin
    """, (marketplace, start_date, end_date)).fetchall()
    conn.close()
    return {r["asin"]: int(r["total"] or 0) for r in rows}


def get_inventory_alerts(overview: pd.DataFrame) -> list[dict]:
    """Generate alert dicts from the overview DataFrame."""
    alerts = []
    mkt_day_cols = {
        "amazon.com":    "days_fba_us",
        "amazon.ca":     "days_fba_ca",
        "amazon.co.uk":  "days_fba_uk",
    }
    for _, row in overview.iterrows():
        asin  = row["asin"]
        title = str(row.get("title", "") or asin)[:50]

        for mkt, dcol in mkt_day_cols.items():
            days = row.get(dcol)
            if days is None:
                continue
            if days < ALERT_CRITICAL:
                alerts.append({"level": "critical", "asin": asin, "title": title,
                                "market": mkt, "days": days,
                                "msg": f"🔴 {days}d left — ship NOW (too late for new production)"})
            elif days < ALERT_URGENT:
                alerts.append({"level": "urgent", "asin": asin, "title": title,
                                "market": mkt, "days": days,
                                "msg": f"🟠 {days}d left — start production immediately"})
            elif days < ALERT_PLAN:
                alerts.append({"level": "plan", "asin": asin, "title": title,
                                "market": mkt, "days": days,
                                "msg": f"🟡 {days}d left — plan & fund purchase now"})

        # Aging alert removed — days-of-stock calculation produces false positives
        # for new/seasonal products. Will be re-added using Amazon's actual
        # Inventory Age report (warehouse time per unit) when uploaded.

    # Note: imbalance/overstocked alert removed — inventory cannot be moved between marketplaces

    return sorted(alerts, key=lambda a: {"critical": 0, "urgent": 1, "plan": 2, "imbalance": 3, "aging": 4}.get(a["level"], 5))
