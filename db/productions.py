"""
db/productions.py — CRUD and summary helpers for the Productions feature.
"""

from .database import get_conn
from .products_catalog import calc_product_cost


# ══════════════════════════════════════════════════════════════════════════════
# Productions (header)
# ══════════════════════════════════════════════════════════════════════════════

def get_productions() -> list[dict]:
    """Return all productions ordered by est_start_date desc."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, name, est_start_date, est_delivery_date, notes, created_at, updated_at
        FROM productions
        ORDER BY COALESCE(est_start_date, created_at) DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_production(prod_id: int) -> dict | None:
    """Return a single production by id, or None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name, est_start_date, est_delivery_date, notes FROM productions WHERE id=?",
        (prod_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_production(data: dict) -> int:
    """
    Insert or update a production header.
    data keys: name, est_start_date, est_delivery_date, notes, id (optional).
    Returns the production id.
    """
    conn = get_conn()
    prod_id = data.get("id")
    with conn:
        if prod_id:
            conn.execute("""
                UPDATE productions
                SET name=?, est_start_date=?, est_delivery_date=?, notes=?,
                    updated_at=datetime('now')
                WHERE id=?
            """, (
                data["name"].strip(),
                data.get("est_start_date") or None,
                data.get("est_delivery_date") or None,
                data.get("notes") or None,
                prod_id,
            ))
        else:
            cur = conn.execute("""
                INSERT INTO productions (name, est_start_date, est_delivery_date, notes)
                VALUES (?, ?, ?, ?)
            """, (
                data["name"].strip(),
                data.get("est_start_date") or None,
                data.get("est_delivery_date") or None,
                data.get("notes") or None,
            ))
            prod_id = cur.lastrowid
    conn.close()
    return prod_id


def delete_production(prod_id: int):
    """Delete a production and all its lines (CASCADE)."""
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM productions WHERE id=?", (prod_id,))
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Production lines
# ══════════════════════════════════════════════════════════════════════════════

def get_production_lines(prod_id: int) -> list[dict]:
    """Return raw production_lines rows for a production."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, sku, num_cartons, service_cost FROM production_lines WHERE production_id=? ORDER BY id",
        (prod_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_production_lines(prod_id: int, lines: list[dict]):
    """
    Replace all production_lines for prod_id.
    Each line dict: {sku, num_cartons, service_cost}
    """
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM production_lines WHERE production_id=?", (prod_id,))
        for line in lines:
            sku = str(line.get("SKU") or line.get("sku") or "").strip()
            if not sku:
                continue
            try:
                num_cartons = int(float(line.get("# Cartons") or line.get("num_cartons") or 0))
            except (ValueError, TypeError):
                num_cartons = 0
            conn.execute(
                "INSERT INTO production_lines (production_id, sku, num_cartons) VALUES (?,?,?)",
                (prod_id, sku, num_cartons),
            )
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Summary — computed view of a production with all enriched fields
# ══════════════════════════════════════════════════════════════════════════════

def get_production_summary(prod_id: int) -> list[dict]:
    """
    Return enriched line items with all computed fields from products_catalog.
    Product Cost and Service Cost are pulled from calc_product_cost() (items table),
    not entered by the user.

    Each dict:
        SKU, Product, # Cartons, # Units,
        Product Cost ($), Service Cost ($),
        Net Weight (kg), Gross Weight (kg), CBM
    """
    conn = get_conn()
    lines = conn.execute(
        "SELECT sku, num_cartons FROM production_lines WHERE production_id=? ORDER BY id",
        (prod_id,)
    ).fetchall()

    result = []
    for line in lines:
        sku         = line["sku"]
        num_cartons = line["num_cartons"] or 0

        cat = conn.execute(
            "SELECT id, name, carton_units, carton_nw_kg, carton_gw_kg, carton_cbm FROM products_catalog WHERE sku=?",
            (sku,)
        ).fetchone()

        if cat:
            carton_units = cat["carton_units"] or 0
            num_units    = carton_units * num_cartons
            net_wt       = (cat["carton_nw_kg"] or 0.0) * num_cartons
            gross_wt     = (cat["carton_gw_kg"] or 0.0) * num_cartons
            cbm          = (cat["carton_cbm"]   or 0.0) * num_cartons

            breakdown          = calc_product_cost(cat["id"])
            unit_mfg_cost      = breakdown.get("total_manufacturer", 0.0)
            unit_svc_cost      = breakdown.get("total_service", 0.0)
            total_product_cost = unit_mfg_cost * num_units
            total_service_cost = unit_svc_cost * num_units

            result.append({
                "SKU":                sku,
                "Product":            cat["name"],
                "# Cartons":          num_cartons,
                "# Units":            num_units,
                "Product Cost ($)":   round(total_product_cost, 2),
                "Service Cost ($)":   round(total_service_cost, 2),
                "Net Weight (kg)":    round(net_wt, 2),
                "Gross Weight (kg)":  round(gross_wt, 2),
                "CBM":                round(cbm, 3),
            })
        else:
            result.append({
                "SKU":                sku,
                "Product":            "— not found —",
                "# Cartons":          num_cartons,
                "# Units":            0,
                "Product Cost ($)":   0.0,
                "Service Cost ($)":   0.0,
                "Net Weight (kg)":    0.0,
                "Gross Weight (kg)":  0.0,
                "CBM":                0.0,
            })

    conn.close()
    return result


def get_catalog_skus() -> list[str]:
    """Return all non-empty SKUs from products_catalog, sorted."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT sku FROM products_catalog WHERE sku IS NOT NULL AND sku != '' ORDER BY sku"
    ).fetchall()
    conn.close()
    return [r["sku"] for r in rows]
