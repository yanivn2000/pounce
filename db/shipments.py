"""
db/shipments.py — CRUD helpers for the Shipments feature.

Stock model
-----------
  available per SKU = total production cartons
                    − cartons in shipped shipments
                    − cartons in draft shipments

Drafts reserve stock immediately so you can never over-commit.
Once a shipment is marked "shipped" it is locked permanently.
"""

from .database import get_conn
from .productions import get_sku_catalog_info


# ── Name auto-generation ──────────────────────────────────────────────────────

def get_next_shipment_name() -> str:
    """Return the next available SHP-NNN name (e.g. SHP-001)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT name FROM shipments WHERE name LIKE 'SHP-%' ORDER BY name"
    ).fetchall()
    conn.close()
    used = set()
    for r in rows:
        try:
            used.add(int(r["name"].split("-")[1]))
        except (IndexError, ValueError):
            pass
    n = 1
    while n in used:
        n += 1
    return f"SHP-{n:03d}"


# ── Shipment header ───────────────────────────────────────────────────────────

def get_shipments() -> list[dict]:
    """Return all shipments, newest first."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, name, destination, status, notes, created_at, updated_at
        FROM shipments
        ORDER BY id DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_shipment(shipment_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name, destination, status, notes FROM shipments WHERE id=?",
        (shipment_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_shipment(data: dict) -> int:
    """
    Insert or update a shipment header.
    data keys: name, destination, notes, id (optional — omit for insert).
    Returns shipment id.
    """
    conn = get_conn()
    shipment_id = data.get("id")
    with conn:
        if shipment_id:
            conn.execute("""
                UPDATE shipments
                SET name=?, destination=?, notes=?, updated_at=datetime('now')
                WHERE id=? AND status='draft'
            """, (
                data["name"].strip(),
                data.get("destination") or None,
                data.get("notes") or None,
                shipment_id,
            ))
        else:
            cur = conn.execute("""
                INSERT INTO shipments (name, destination, notes)
                VALUES (?, ?, ?)
            """, (
                data["name"].strip(),
                data.get("destination") or None,
                data.get("notes") or None,
            ))
            shipment_id = cur.lastrowid
    conn.close()
    return shipment_id


def mark_shipped(shipment_id: int):
    """Lock a shipment permanently — cannot be undone."""
    conn = get_conn()
    with conn:
        conn.execute("""
            UPDATE shipments
            SET status='shipped', updated_at=datetime('now')
            WHERE id=?
        """, (shipment_id,))
    conn.close()


def delete_shipment(shipment_id: int):
    """Delete a draft shipment and all its lines (CASCADE)."""
    conn = get_conn()
    with conn:
        conn.execute(
            "DELETE FROM shipments WHERE id=? AND status='draft'",
            (shipment_id,)
        )
    conn.close()


# ── Shipment lines ────────────────────────────────────────────────────────────

def get_shipment_lines(shipment_id: int) -> list[dict]:
    """Return raw shipment_lines rows."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, sku, num_cartons FROM shipment_lines WHERE shipment_id=? ORDER BY id",
        (shipment_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_shipment_lines(shipment_id: int, lines: list[dict]):
    """
    Replace all lines for a draft shipment.
    Each line dict: {sku, num_cartons}
    """
    conn = get_conn()
    with conn:
        # Only allow editing drafts
        row = conn.execute(
            "SELECT status FROM shipments WHERE id=?", (shipment_id,)
        ).fetchone()
        if not row or row["status"] != "draft":
            return
        conn.execute("DELETE FROM shipment_lines WHERE shipment_id=?", (shipment_id,))
        for line in lines:
            sku = str(line.get("SKU") or line.get("sku") or "").strip()
            if not sku:
                continue
            try:
                num_cartons = int(float(line.get("# Cartons") or line.get("num_cartons") or 0))
            except (ValueError, TypeError):
                num_cartons = 0
            if num_cartons <= 0:
                continue
            conn.execute(
                "INSERT INTO shipment_lines (shipment_id, sku, num_cartons) VALUES (?,?,?)",
                (shipment_id, sku, num_cartons),
            )
    conn.close()


# ── Stock calculations ────────────────────────────────────────────────────────

def get_production_totals() -> dict[str, int]:
    """Return {sku: total_cartons} across all productions."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT sku, SUM(num_cartons) AS total
        FROM production_lines
        GROUP BY sku
    """).fetchall()
    conn.close()
    return {r["sku"]: int(r["total"] or 0) for r in rows}


def get_allocated_cartons() -> dict[str, int]:
    """
    Return {sku: total_cartons} reserved by ALL shipments
    (both draft and shipped).
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT sl.sku, SUM(sl.num_cartons) AS total
        FROM shipment_lines sl
        JOIN shipments s ON s.id = sl.shipment_id
        GROUP BY sl.sku
    """).fetchall()
    conn.close()
    return {r["sku"]: int(r["total"] or 0) for r in rows}


def get_stock_to_be_shipped() -> list[dict]:
    """
    Return per-SKU available (unallocated) stock, enriched with catalog info.
    available = production_total - allocated (draft + shipped)

    Each dict: SKU, Product, available_cartons, units,
               product_cost, service_cost, nw_kg, gw_kg, cbm
    Sorted by SKU.
    """
    prod_totals = get_production_totals()
    allocated   = get_allocated_cartons()
    sku_info    = get_sku_catalog_info()

    result = []
    all_skus = set(prod_totals.keys()) | set(sku_info.keys())
    for sku in sorted(all_skus):
        prod_total  = prod_totals.get(sku, 0)
        used        = allocated.get(sku, 0)
        available   = max(0, prod_total - used)
        if prod_total == 0:
            continue   # not in any production — skip
        info     = sku_info.get(sku, {})
        cu       = info.get("carton_units", 0) or 0
        units    = cu * available
        result.append({
            "SKU":               sku,
            "Product":           info.get("name", ""),
            "Available Cartons": available,
            "# Units":           units,
            "Product Cost ($)":  round(info.get("unit_mfg", 0.0) * units, 2),
            "Service Cost ($)":  round(info.get("unit_svc", 0.0) * units, 2),
            "Net Weight (kg)":   round(info.get("nw_kg", 0.0) * available, 2),
            "Gross Weight (kg)": round(info.get("gw_kg", 0.0) * available, 2),
            "CBM":               round(info.get("cbm",  0.0) * available, 3),
            # raw values used for validation in shipment editor
            "_prod_total":       prod_total,
            "_allocated":        used,
        })
    return result


def get_available_per_sku() -> dict[str, int]:
    """Return {sku: available_cartons} — used for validation when editing shipment lines."""
    prod_totals = get_production_totals()
    allocated   = get_allocated_cartons()
    return {
        sku: max(0, total - allocated.get(sku, 0))
        for sku, total in prod_totals.items()
    }


def get_available_per_sku_excluding(shipment_id: int) -> dict[str, int]:
    """
    Like get_available_per_sku() but excludes the given shipment's own lines
    from the allocated total — so the shipment can be edited without blocking
    itself.
    """
    prod_totals = get_production_totals()
    allocated   = get_allocated_cartons()

    # cartons in THIS shipment — these are not "used by others"
    own_lines = get_shipment_lines(shipment_id)
    own       = {ln["sku"]: int(ln["num_cartons"] or 0) for ln in own_lines}

    result = {}
    for sku, total in prod_totals.items():
        used_by_others = allocated.get(sku, 0) - own.get(sku, 0)
        result[sku] = max(0, total - used_by_others)
    return result
