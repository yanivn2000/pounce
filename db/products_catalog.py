"""
db/products_catalog.py — Suppliers, Items, Products Catalog, and cost calculations.
"""

from .database import get_conn


# ══════════════════════════════════════════════════════════════════════════════
# SUPPLIERS
# ══════════════════════════════════════════════════════════════════════════════

def get_suppliers() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, category, is_manufacturer, notes FROM suppliers ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_supplier(name: str, category: str, is_manufacturer: int, notes: str,
                    supplier_id: int | None = None) -> int:
    conn = get_conn()
    with conn:
        if supplier_id:
            conn.execute(
                """UPDATE suppliers
                   SET name=?, category=?, is_manufacturer=?, notes=?
                   WHERE id=?""",
                (name, category, is_manufacturer, notes, supplier_id),
            )
            result_id = supplier_id
        else:
            cur = conn.execute(
                """INSERT INTO suppliers (name, category, is_manufacturer, notes)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       category=excluded.category,
                       is_manufacturer=excluded.is_manufacturer,
                       notes=excluded.notes""",
                (name, category, is_manufacturer, notes),
            )
            # If INSERT triggered the conflict branch, fetch the existing id
            if cur.lastrowid and cur.lastrowid != 0:
                result_id = cur.lastrowid
            else:
                result_id = conn.execute(
                    "SELECT id FROM suppliers WHERE name=?", (name,)
                ).fetchone()[0]
    conn.close()
    return result_id


def delete_supplier(supplier_id: int):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# ITEMS
# ══════════════════════════════════════════════════════════════════════════════

def get_items() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT i.id, i.name, i.item_type, i.supplier_id,
               s.name AS supplier_name,
               i.manufacturer_cost, i.service_cost,
               (i.manufacturer_cost + i.service_cost) AS total_cost,
               i.net_width_cm, i.hst_code, i.upc, i.currency, i.notes
        FROM items i
        LEFT JOIN suppliers s ON s.id = i.supplier_id
        ORDER BY i.name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_item(data: dict, item_id: int | None = None) -> int:
    conn = get_conn()
    fields = (
        data.get("name"), data.get("item_type"), data.get("supplier_id"),
        data.get("manufacturer_cost", 0), data.get("service_cost", 0),
        data.get("net_width_cm"), data.get("hst_code"), data.get("upc"),
        data.get("currency", "USD"), data.get("notes"),
    )
    with conn:
        if item_id:
            conn.execute("""
                UPDATE items
                SET name=?, item_type=?, supplier_id=?, manufacturer_cost=?,
                    service_cost=?, net_width_cm=?, hst_code=?, upc=?,
                    currency=?, notes=?, updated_at=datetime('now')
                WHERE id=?
            """, (*fields, item_id))
            result_id = item_id
        else:
            cur = conn.execute("""
                INSERT INTO items
                    (name, item_type, supplier_id, manufacturer_cost, service_cost,
                     net_width_cm, hst_code, upc, currency, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, fields)
            result_id = cur.lastrowid
    conn.close()
    return result_id


def delete_item(item_id: int):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCTS CATALOG
# ══════════════════════════════════════════════════════════════════════════════

def get_products_catalog() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM products_catalog ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_product_catalog(data: dict, product_id: int | None = None) -> int:
    conn = get_conn()
    fields = (
        data.get("asin"), data.get("sku"), data.get("name"),
        data.get("product_type"), data.get("marketplace", "amazon.com"),
        data.get("width_cm"), data.get("length_cm"), data.get("height_cm"),
        data.get("weight_kg"), data.get("shipping_cost", 0),
        data.get("customs_rate", 0), data.get("fba_fee", 0),
        int(data.get("is_new_product", 0)), data.get("notes"),
    )
    with conn:
        if product_id:
            conn.execute("""
                UPDATE products_catalog
                SET asin=?, sku=?, name=?, product_type=?, marketplace=?,
                    width_cm=?, length_cm=?, height_cm=?, weight_kg=?,
                    shipping_cost=?, customs_rate=?, fba_fee=?,
                    is_new_product=?, notes=?, updated_at=datetime('now')
                WHERE id=?
            """, (*fields, product_id))
            result_id = product_id
        else:
            cur = conn.execute("""
                INSERT INTO products_catalog
                    (asin, sku, name, product_type, marketplace,
                     width_cm, length_cm, height_cm, weight_kg,
                     shipping_cost, customs_rate, fba_fee, is_new_product, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, fields)
            result_id = cur.lastrowid
    conn.close()
    return result_id


def delete_product_catalog(product_id: int):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM product_components WHERE product_id=?", (product_id,))
        conn.execute("DELETE FROM products_catalog WHERE id=?", (product_id,))
    conn.close()


def get_product_components(product_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT pc.id, pc.item_id, pc.quantity,
               i.name AS item_name, i.item_type,
               i.manufacturer_cost, i.service_cost,
               s.name AS supplier_name
        FROM product_components pc
        JOIN items i ON i.id = pc.item_id
        LEFT JOIN suppliers s ON s.id = i.supplier_id
        WHERE pc.product_id = ?
        ORDER BY i.name
    """, (product_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_product_components(product_id: int, components: list[dict]):
    """Replace all components for a product. components = [{"item_id": X, "quantity": Y}]"""
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM product_components WHERE product_id=?", (product_id,))
        for comp in components:
            conn.execute("""
                INSERT INTO product_components (product_id, item_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(product_id, item_id) DO UPDATE SET quantity=excluded.quantity
            """, (product_id, comp["item_id"], comp.get("quantity", 1)))
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# COST CALCULATION
# ══════════════════════════════════════════════════════════════════════════════

def calc_product_cost(product_id: int) -> dict:
    """
    Returns a detailed cost breakdown for a product.
    {
      items: [{name, qty, mfg_cost, service_cost, subtotal}],
      total_manufacturer,
      total_service,
      shipping_cost,
      customs_rate_pct,
      customs_cost,
      landed_cost,
    }
    """
    conn = get_conn()
    product = conn.execute(
        "SELECT shipping_cost, customs_rate FROM products_catalog WHERE id=?", (product_id,)
    ).fetchone()
    if not product:
        conn.close()
        return {}

    shipping_cost = product["shipping_cost"] or 0.0
    customs_rate_pct = product["customs_rate"] or 0.0

    components = conn.execute("""
        SELECT pc.quantity, i.name, i.manufacturer_cost, i.service_cost
        FROM product_components pc
        JOIN items i ON i.id = pc.item_id
        WHERE pc.product_id = ?
    """, (product_id,)).fetchall()
    conn.close()

    item_rows = []
    total_manufacturer = 0.0
    total_service = 0.0
    for comp in components:
        qty = comp["quantity"]
        mfg = (comp["manufacturer_cost"] or 0.0) * qty
        svc = (comp["service_cost"] or 0.0) * qty
        item_rows.append({
            "name": comp["name"],
            "qty": qty,
            "mfg_cost": mfg,
            "service_cost": svc,
            "subtotal": mfg + svc,
        })
        total_manufacturer += mfg
        total_service += svc

    customs_cost = total_manufacturer * customs_rate_pct / 100.0
    landed_cost = total_manufacturer + total_service + shipping_cost + customs_cost

    return {
        "items": item_rows,
        "total_manufacturer": total_manufacturer,
        "total_service": total_service,
        "shipping_cost": shipping_cost,
        "customs_rate_pct": customs_rate_pct,
        "customs_cost": customs_cost,
        "landed_cost": landed_cost,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SYNC TO product_costs
# ══════════════════════════════════════════════════════════════════════════════

def sync_product_to_costs(product_id: int):
    """
    Write computed costs into product_costs so the analyzer keeps working.
    product_cost = total_manufacturer + total_service
    """
    conn = get_conn()
    product = conn.execute(
        "SELECT asin, name, fba_fee, is_new_product FROM products_catalog WHERE id=?",
        (product_id,),
    ).fetchone()
    conn.close()

    if not product or not product["asin"]:
        return

    breakdown = calc_product_cost(product_id)
    if not breakdown:
        return

    asin = product["asin"].strip().upper()
    product_name = product["name"]
    product_cost = breakdown["total_manufacturer"] + breakdown["total_service"]
    shipping_cost = breakdown["shipping_cost"]
    customs_cost = breakdown["customs_cost"]
    fba_fee = product["fba_fee"] or 0.0
    is_new = int(product["is_new_product"] or 0)

    conn = get_conn()
    with conn:
        conn.execute("""
            INSERT INTO product_costs
                (asin, product_name, product_cost, shipping_cost, customs_cost,
                 fba_fee, is_new_product, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(asin) DO UPDATE SET
                product_name   = excluded.product_name,
                product_cost   = excluded.product_cost,
                shipping_cost  = excluded.shipping_cost,
                customs_cost   = excluded.customs_cost,
                fba_fee        = excluded.fba_fee,
                is_new_product = excluded.is_new_product,
                updated_at     = excluded.updated_at
        """, (asin, product_name, product_cost, shipping_cost, customs_cost,
              fba_fee, is_new))
    conn.close()
