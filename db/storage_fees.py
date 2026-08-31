"""
storage_fees.py — Amazon FBA Monthly Storage Fees report import + overcharge audit.

Amazon bills monthly storage on the *packaged* volume it measures for each unit:
    fee ≈ average_quantity_on_hand × item_volume × base_rate  (+ utilization surcharge)

Because we know the true dimensions of every product (products_catalog), we can
re-derive the volume Amazon *should* have used and flag SKUs where Amazon's
measured volume — and therefore the fee — is inflated beyond a packaging
tolerance. Those are the disputable rows.

The report mixes countries and units within a single file (US = inches / cubic
feet, CA/EU/UK = centimetres / cubic metres), so every value is normalised on
import: sides → cm, volumes → m³, while the native currency + volume unit are
kept for display and for re-computing the expected fee in Amazon's own terms.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone

from .database import get_conn

# ── Constants ────────────────────────────────────────────────────────────────
FT3_PER_M3 = 35.3146667
M3_PER_FT3 = 0.0283168466
CM_PER_INCH = 2.54

# Amazon country_code → our marketplace key
_CC_TO_MP = {
    "US": "amazon.com", "CA": "amazon.ca", "GB": "amazon.co.uk", "UK": "amazon.co.uk",
    "DE": "amazon.de", "FR": "amazon.fr", "IT": "amazon.it", "ES": "amazon.es",
    "NL": "amazon.nl", "SE": "amazon.se", "PL": "amazon.pl", "BE": "amazon.com.be",
    "MX": "amazon.com.mx", "JP": "amazon.co.jp", "AU": "amazon.com.au",
}

MP_LABELS = {
    "amazon.com": "🇺🇸 US", "amazon.ca": "🇨🇦 CA", "amazon.co.uk": "🇬🇧 UK",
    "amazon.de": "🇩🇪 DE", "amazon.fr": "🇫🇷 FR", "amazon.it": "🇮🇹 IT",
    "amazon.es": "🇪🇸 ES", "amazon.nl": "🇳🇱 NL", "amazon.se": "🇸🇪 SE",
    "amazon.pl": "🇵🇱 PL",
}


def _f(x) -> float:
    """Tolerant float — empty/None/'None'/blank → 0.0, handles '3.0E-4'."""
    if x is None:
        return 0.0
    s = str(x).strip()
    if s in ("", "None", "null", "NaN"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _vol_to_m3(v: float, units: str) -> float:
    return v * M3_PER_FT3 if "feet" in (units or "").lower() else v


def _side_to_cm(v: float, units: str) -> float:
    return v * CM_PER_INCH if (units or "").lower().startswith("inch") else v


# ── Schema ───────────────────────────────────────────────────────────────────
def init_storage_fees_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS storage_fees (
            marketplace           TEXT NOT NULL,
            month                 TEXT NOT NULL,       -- 'YYYY-MM'
            asin                  TEXT NOT NULL,
            fnsku                 TEXT,
            product_name          TEXT,
            size_tier             TEXT,
            currency              TEXT,
            avg_qty               REAL,
            amz_item_vol_native   REAL,               -- per unit, report's native volume unit
            amz_item_vol_m3       REAL,               -- per unit, normalised
            volume_units          TEXT,               -- 'cubic feet' | 'cubic meters'
            amz_longest_cm        REAL,
            amz_median_cm         REAL,
            amz_shortest_cm       REAL,
            total_item_vol_native REAL,
            base_rate             REAL,               -- native currency per native volume unit
            base_msf              REAL,
            sus                   REAL,
            fee                   REAL,               -- native currency
            imported_at           TEXT,
            PRIMARY KEY (marketplace, month, asin)
        )
    """)
    conn.commit()


# ── Import ───────────────────────────────────────────────────────────────────
def import_storage_fees_csv(path: str, conn=None) -> dict:
    """Import one Monthly Storage Fees report (may span several countries).

    Rows are aggregated per (marketplace, month, ASIN) — summing across
    fulfilment centres — then upserted. Returns a summary keyed by marketplace.
    """
    close = conn is None
    if conn is None:
        conn = get_conn()
    init_storage_fees_table(conn)

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    agg: dict[tuple, dict] = {}
    for r in rows:
        cc = (r.get("country_code") or "").strip().upper()
        mp = _CC_TO_MP.get(cc, cc.lower())
        asin = (r.get("asin") or "").strip().upper()
        month = (r.get("month_of_charge") or "").strip()
        if not asin or not month:
            continue
        vu = (r.get("volume_units") or "").strip()
        mu = (r.get("measurement_units") or "").strip()

        k = (mp, month, asin)
        d = agg.get(k)
        if d is None:
            d = agg[k] = {
                "marketplace": mp, "month": month, "asin": asin,
                "fnsku": (r.get("fnsku") or "").strip(),
                "product_name": (r.get("product_name") or "").strip(),
                "size_tier": (r.get("product_size_tier") or r.get("category") or "").strip(),
                "currency": (r.get("currency") or "").strip(),
                "volume_units": vu,
                "avg_qty": 0.0, "amz_item_vol_native": 0.0, "amz_item_vol_m3": 0.0,
                "amz_longest_cm": 0.0, "amz_median_cm": 0.0, "amz_shortest_cm": 0.0,
                "total_item_vol_native": 0.0, "base_rate": 0.0,
                "base_msf": 0.0, "sus": 0.0, "fee": 0.0,
            }
        d["avg_qty"] += _f(r.get("average_quantity_on_hand"))
        d["total_item_vol_native"] += _f(r.get("estimated_total_item_volume"))
        d["base_msf"] += _f(r.get("est_base_msf"))
        d["sus"] += _f(r.get("est_sus"))
        d["fee"] += _f(r.get("estimated_monthly_storage_fee"))
        # per-unit measures are constant across FCs; take the largest seen (worst case)
        iv = _f(r.get("item_volume"))
        if iv > d["amz_item_vol_native"]:
            d["amz_item_vol_native"] = iv
            d["amz_item_vol_m3"] = _vol_to_m3(iv, vu)
        d["base_rate"] = max(d["base_rate"], _f(r.get("base_rate")))
        d["amz_longest_cm"] = max(d["amz_longest_cm"], _side_to_cm(_f(r.get("longest_side")), mu))
        d["amz_median_cm"] = max(d["amz_median_cm"], _side_to_cm(_f(r.get("median_side")), mu))
        d["amz_shortest_cm"] = max(d["amz_shortest_cm"], _side_to_cm(_f(r.get("shortest_side")), mu))
        if not d["size_tier"]:
            d["size_tier"] = (r.get("product_size_tier") or r.get("category") or "").strip()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for d in agg.values():
        conn.execute("""
            INSERT INTO storage_fees
              (marketplace, month, asin, fnsku, product_name, size_tier, currency,
               avg_qty, amz_item_vol_native, amz_item_vol_m3, volume_units,
               amz_longest_cm, amz_median_cm, amz_shortest_cm,
               total_item_vol_native, base_rate, base_msf, sus, fee, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(marketplace, month, asin) DO UPDATE SET
               fnsku=excluded.fnsku, product_name=excluded.product_name,
               size_tier=excluded.size_tier, currency=excluded.currency,
               avg_qty=excluded.avg_qty, amz_item_vol_native=excluded.amz_item_vol_native,
               amz_item_vol_m3=excluded.amz_item_vol_m3, volume_units=excluded.volume_units,
               amz_longest_cm=excluded.amz_longest_cm, amz_median_cm=excluded.amz_median_cm,
               amz_shortest_cm=excluded.amz_shortest_cm,
               total_item_vol_native=excluded.total_item_vol_native,
               base_rate=excluded.base_rate, base_msf=excluded.base_msf,
               sus=excluded.sus, fee=excluded.fee, imported_at=excluded.imported_at
        """, (
            d["marketplace"], d["month"], d["asin"], d["fnsku"], d["product_name"],
            d["size_tier"], d["currency"], d["avg_qty"], d["amz_item_vol_native"],
            d["amz_item_vol_m3"], d["volume_units"], d["amz_longest_cm"],
            d["amz_median_cm"], d["amz_shortest_cm"], d["total_item_vol_native"],
            d["base_rate"], d["base_msf"], d["sus"], d["fee"], now,
        ))
    conn.commit()

    summary: dict[str, dict] = {}
    for d in agg.values():
        s = summary.setdefault(d["marketplace"], {"asins": 0, "fee": 0.0, "currency": d["currency"], "month": d["month"]})
        s["asins"] += 1
        s["fee"] += d["fee"]
    if close:
        conn.close()
    return summary


# ── Audit ────────────────────────────────────────────────────────────────────
def get_storage_fee_months(conn=None) -> list[str]:
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        rows = conn.execute("SELECT DISTINCT month FROM storage_fees ORDER BY month DESC").fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        if close:
            conn.close()


def get_storage_fee_marketplaces(conn=None, month=None) -> list[str]:
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        if month:
            rows = conn.execute(
                "SELECT DISTINCT marketplace FROM storage_fees WHERE month=? ORDER BY marketplace",
                (month,)).fetchall()
        else:
            rows = conn.execute("SELECT DISTINCT marketplace FROM storage_fees ORDER BY marketplace").fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        if close:
            conn.close()


def get_storage_fee_audit(conn=None, month=None, marketplace=None, tolerance_pct: float = 20.0) -> dict:
    """Return {rows: [...], totals: {mp: {...}}} comparing Amazon's charged storage
    against the fee re-derived from our own product dimensions.

    A row is `disputable` when Amazon's measured per-unit volume exceeds ours by
    more than `tolerance_pct` (packaging accounts for a small, non-disputable gap).
    """
    close = conn is None
    if conn is None:
        conn = get_conn()

    # our known dimensions
    dims: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT UPPER(asin) a, name, length_cm, width_cm, height_cm, carton_cbm, carton_units "
        "FROM products_catalog WHERE asin IS NOT NULL AND asin!=''"
    ).fetchall():
        a = r[0]
        L, W, H = _f(r[2]), _f(r[3]), _f(r[4])
        cc, cu = _f(r[5]), _f(r[6])
        unit_m3 = (L * W * H / 1e6) if (L and W and H) else 0.0
        carton_m3 = (cc / cu) if (cc and cu) else 0.0
        dims[a] = {
            "name": r[1], "unit_m3": unit_m3, "carton_m3": carton_m3,
            "sides": sorted([L, W, H], reverse=True),
        }

    q = "SELECT marketplace, month, asin, product_name, size_tier, currency, avg_qty, " \
        "amz_item_vol_native, amz_item_vol_m3, volume_units, amz_longest_cm, amz_median_cm, " \
        "amz_shortest_cm, total_item_vol_native, base_rate, fee FROM storage_fees WHERE 1=1"
    params: list = []
    if month:
        q += " AND month=?"; params.append(month)
    if marketplace:
        q += " AND marketplace=?"; params.append(marketplace)
    q += " ORDER BY fee DESC"
    src = conn.execute(q, params).fetchall()

    rows: list[dict] = []
    totals: dict[str, dict] = {}
    for (mp, mth, asin, pname, tier, cur, qty, amz_vn, amz_m3, vu, lo, me, sh,
         tvn, rate, fee) in src:
        d = dims.get((asin or "").upper())
        our_m3 = 0.0
        our_src = "—"
        if d:
            if d["unit_m3"] > 0:
                our_m3, our_src = d["unit_m3"], "unit dims"
            elif d["carton_m3"] > 0:
                our_m3, our_src = d["carton_m3"], "carton/units"
        delta_pct = ((amz_m3 / our_m3) - 1.0) * 100.0 if our_m3 else None
        native_factor = FT3_PER_M3 if "feet" in (vu or "").lower() else 1.0
        our_vol_native = our_m3 * native_factor
        expected_fee = our_vol_native * _f(qty) * _f(rate) if our_m3 else None
        over = max(0.0, _f(fee) - expected_fee) if expected_fee is not None else 0.0
        disputable = bool(delta_pct is not None and delta_pct >= tolerance_pct)

        rows.append({
            "marketplace": mp, "mp_label": MP_LABELS.get(mp, mp), "month": mth,
            "asin": asin, "product_name": pname or (d["name"] if d else ""),
            "size_tier": tier, "currency": cur, "avg_qty": _f(qty),
            "amz_vol_l": amz_m3 * 1000.0, "our_vol_l": our_m3 * 1000.0,
            "our_src": our_src, "delta_pct": delta_pct,
            "amz_sides_cm": [lo, me, sh],
            "our_sides_cm": d["sides"] if d else [0, 0, 0],
            "base_rate": _f(rate), "fee": _f(fee),
            "expected_fee": expected_fee, "overcharge": over,
            "disputable": disputable, "matched": bool(d),
        })

        t = totals.setdefault(mp, {
            "mp_label": MP_LABELS.get(mp, mp), "currency": cur,
            "fee": 0.0, "expected": 0.0, "variance": 0.0, "disputable": 0.0,
            "n": 0, "n_disputable": 0, "n_unmatched": 0,
        })
        t["fee"] += _f(fee)
        t["n"] += 1
        if expected_fee is not None:
            t["expected"] += expected_fee
            t["variance"] += over
        if disputable:
            t["disputable"] += over
            t["n_disputable"] += 1
        if not d:
            t["n_unmatched"] += 1

    if close:
        conn.close()
    return {"rows": rows, "totals": totals}
