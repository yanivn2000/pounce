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
        """SELECT id, name, category, is_manufacturer, notes,
                  address, contact_person, email, tel
           FROM suppliers ORDER BY name"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_supplier(name: str, category: str, is_manufacturer: int, notes: str,
                    address: str = "", contact_person: str = "",
                    email: str = "", tel: str = "",
                    supplier_id: int | None = None) -> int:
    conn = get_conn()
    with conn:
        if supplier_id:
            conn.execute(
                """UPDATE suppliers
                   SET name=?, category=?, is_manufacturer=?, notes=?,
                       address=?, contact_person=?, email=?, tel=?
                   WHERE id=?""",
                (name, category, is_manufacturer, notes,
                 address, contact_person, email, tel, supplier_id),
            )
            result_id = supplier_id
        else:
            cur = conn.execute(
                """INSERT INTO suppliers (name, category, is_manufacturer, notes,
                                          address, contact_person, email, tel)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       category=excluded.category,
                       is_manufacturer=excluded.is_manufacturer,
                       notes=excluded.notes,
                       address=excluded.address,
                       contact_person=excluded.contact_person,
                       email=excluded.email,
                       tel=excluded.tel""",
                (name, category, is_manufacturer, notes,
                 address, contact_person, email, tel),
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
        SELECT i.id, i.part_id, i.name, i.item_type, i.supplier_id,
               s.name AS supplier_name,
               i.manufacturer_cost, i.service_cost,
               (i.manufacturer_cost + i.service_cost) AS total_cost,
               i.net_weight_grams, i.hst_code_na, i.hst_code_uk, i.currency, i.notes
        FROM items i
        LEFT JOIN suppliers s ON s.id = i.supplier_id
        ORDER BY i.part_id, i.name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_item(data: dict, item_id: int | None = None) -> int:
    conn = get_conn()
    fields = (
        data.get("part_id"), data.get("name"), data.get("item_type"), data.get("supplier_id"),
        data.get("manufacturer_cost", 0), data.get("service_cost", 0),
        data.get("net_weight_grams"), data.get("hst_code_na"), data.get("hst_code_uk"),
        data.get("currency", "USD"), data.get("notes"),
    )
    with conn:
        if item_id:
            conn.execute("""
                UPDATE items
                SET part_id=?, name=?, item_type=?, supplier_id=?, manufacturer_cost=?,
                    service_cost=?, net_weight_grams=?, hst_code_na=?, hst_code_uk=?,
                    currency=?, notes=?, updated_at=datetime('now')
                WHERE id=?
            """, (*fields, item_id))
            result_id = item_id
        else:
            cur = conn.execute("""
                INSERT INTO items
                    (part_id, name, item_type, supplier_id, manufacturer_cost, service_cost,
                     net_weight_grams, hst_code_na, hst_code_uk, currency, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        data.get("asin"), data.get("sku"), data.get("upc"), data.get("name"),
        data.get("product_type"),
        data.get("width_cm"), data.get("length_cm"), data.get("height_cm"),
        data.get("weight_gr"),
        int(data.get("is_new_product", 0)), data.get("notes"),
        data.get("carton_units"), data.get("carton_length_cm"),
        data.get("carton_width_cm"), data.get("carton_height_cm"),
        data.get("carton_nw_kg"), data.get("carton_gw_kg"), data.get("carton_cbm"),
        data.get("part_id_1"), data.get("part_id_2"),
    )
    with conn:
        if product_id:
            conn.execute("""
                UPDATE products_catalog
                SET asin=?, sku=?, upc=?, name=?, product_type=?,
                    width_cm=?, length_cm=?, height_cm=?, weight_gr=?,
                    is_new_product=?, notes=?,
                    carton_units=?, carton_length_cm=?, carton_width_cm=?,
                    carton_height_cm=?, carton_nw_kg=?, carton_gw_kg=?, carton_cbm=?,
                    part_id_1=?, part_id_2=?,
                    updated_at=datetime('now')
                WHERE id=?
            """, (*fields, product_id))
            result_id = product_id
        else:
            cur = conn.execute("""
                INSERT INTO products_catalog
                    (asin, sku, upc, name, product_type,
                     width_cm, length_cm, height_cm, weight_gr,
                     is_new_product, notes,
                     carton_units, carton_length_cm, carton_width_cm,
                     carton_height_cm, carton_nw_kg, carton_gw_kg, carton_cbm,
                     part_id_1, part_id_2)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, fields)
            result_id = cur.lastrowid
    conn.close()
    return result_id


def delete_product_catalog(product_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM product_components WHERE product_id=?", (product_id,))
    conn.execute("DELETE FROM products_catalog WHERE id=?", (product_id,))
    conn.commit()
    conn.close()


def delete_all_products_catalog():
    """Delete every product and its components."""
    conn = get_conn()
    # executescript issues an implicit COMMIT before running, guaranteeing the
    # deletes are persisted even if a transaction was already open on this conn.
    conn.executescript("""
        DELETE FROM product_components;
        DELETE FROM products_catalog;
    """)
    conn.close()


def get_product_components(product_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT pc.id, pc.item_id, pc.quantity,
               i.part_id, i.name AS item_name, i.item_type,
               i.manufacturer_cost, i.service_cost, i.net_weight_grams,
               s.name AS supplier_name
        FROM product_components pc
        JOIN items i ON i.id = pc.item_id
        LEFT JOIN suppliers s ON s.id = i.supplier_id
        WHERE pc.product_id = ?
        ORDER BY i.part_id, i.name
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
    Returns a detailed cost breakdown for a product using part_id_1 / part_id_2.
    Each referenced item contributes qty=1.
    {
      items: [{part_id, name, mfg_cost, service_cost, subtotal, weight_gr}],
      total_manufacturer, total_service,
      shipping_cost, landed_cost, total_weight_gr,
    }
    """
    conn = get_conn()
    product = conn.execute(
        "SELECT part_id_1, part_id_2 FROM products_catalog WHERE id=?", (product_id,)
    ).fetchone()
    if not product:
        conn.close()
        return {}

    # TODO: compute dynamically from product dimensions once size tiers are set up.
    # For now use a flat $0.50 USD per product.
    shipping_cost = 0.50

    part_ids = [p for p in (product["part_id_1"], product["part_id_2"]) if p]
    item_rows = []
    total_manufacturer = 0.0
    total_service = 0.0
    total_weight_gr = 0.0

    for pid in part_ids:
        item = conn.execute(
            "SELECT part_id, name, manufacturer_cost, service_cost, net_weight_grams FROM items WHERE part_id=?",
            (pid,)
        ).fetchone()
        if not item:
            continue
        mfg = item["manufacturer_cost"] or 0.0
        svc = item["service_cost"] or 0.0
        wt  = item["net_weight_grams"] or 0.0
        item_rows.append({
            "part_id":      item["part_id"] or "",
            "name":         item["name"],
            "mfg_cost":     mfg,
            "service_cost": svc,
            "subtotal":     mfg + svc,
            "weight_gr":    wt,
        })
        total_manufacturer += mfg
        total_service += svc
        total_weight_gr += wt

    conn.close()
    landed_cost = total_manufacturer + total_service + shipping_cost

    return {
        "items": item_rows,
        "total_manufacturer": total_manufacturer,
        "total_service": total_service,
        "shipping_cost": shipping_cost,
        "landed_cost": landed_cost,
        "total_weight_gr": total_weight_gr,
    }


def sync_product_to_costs(product_id: int):
    """No-op: product_costs is now a VIEW computed from products_catalog."""
    pass


# ══════════════════════════════════════════════════════════════════════════════
# CSV IMPORT
# ══════════════════════════════════════════════════════════════════════════════

def import_items_csv(file_obj) -> tuple[int, list[str]]:
    """
    Import items from CSV. Matches existing items by name (case-insensitive) and
    updates them; inserts new rows otherwise.

    Expected columns (case-insensitive, spaces→underscores):
      name*, item_type*, supplier_name, manufacturer_cost, service_cost,
      net_width_cm, hst_code_na, hst_code_uk, upc, currency, notes
    (* required)
    Returns (rows_imported, warnings).
    """
    import pandas as pd

    try:
        sample = file_obj.read(4096)
        if isinstance(sample, bytes):
            sample = sample.decode("utf-8", errors="replace")
        file_obj.seek(0)
        first_line = sample.split("\n")[0]
        sep = "\t" if first_line.count("\t") >= first_line.count(",") else ","
        df = pd.read_csv(file_obj, dtype=str, sep=sep)
    except Exception as e:
        return 0, [f"Failed to read CSV: {e}"]

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    missing = [c for c in ("name",) if c not in df.columns]
    if missing:
        return 0, [f"Missing required columns: {missing}"]

    # Build supplier name → id map (case-insensitive, whitespace-stripped)
    suppliers = get_suppliers()
    sup_map = {s["name"].strip().lower(): s["id"] for s in suppliers}
    valid_supplier_names = [s["name"] for s in suppliers]

    warnings: list[str] = []
    imported = 0
    conn = get_conn()

    for _, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        item_type = str(row.get("item_type", "other")).strip() or "other"

        # Resolve supplier — must exist in the closed list
        sup_id = None
        sup_name_raw = str(row.get("supplier_name", "")).strip()
        if sup_name_raw and sup_name_raw.lower() != "nan":
            key = sup_name_raw.strip().lower()
            if key in sup_map:
                sup_id = sup_map[key]
            else:
                warnings.append(
                    f"Row '{name}': supplier '{sup_name_raw}' not found. "
                    f"Valid names: {', '.join(valid_supplier_names)}. Item saved without supplier."
                )

        def _f(col, default=0.0):
            try:
                v = row.get(col)
                return float(v) if v and str(v).strip() not in ("", "nan") else default
            except Exception:
                return default

        def _s(col):
            v = str(row.get(col, "")).strip()
            return v if v and v != "nan" else None

        data = {
            "part_id":           _s("part_id"),
            "name":              name,
            "item_type":         item_type,
            "supplier_id":       sup_id,
            "manufacturer_cost": _f("manufacturer_cost"),
            "service_cost":      _f("service_cost"),
            "net_weight_grams":  _f("net_weight_grams") or _f("net_width_cm") or None,
            "hst_code_na":       _s("hst_code_na") or _s("hst_code"),
            "hst_code_uk":       _s("hst_code_uk"),
            "currency":          _s("currency") or "USD",
            "notes":             _s("notes"),
        }

        try:
            # Lookup strategy:
            # • If part_id is provided → match ONLY by part_id. If not found, INSERT
            #   a new row (do NOT fall back to name — different part_ids are different
            #   items even if they share a name).
            # • If part_id is absent → fall back to name for backward compatibility.
            part_id_val = data.get("part_id")
            existing = None
            if part_id_val:
                existing = conn.execute(
                    "SELECT id FROM items WHERE part_id=?", (part_id_val,)
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT id FROM items WHERE LOWER(name)=LOWER(?)", (name,)
                ).fetchone()
            item_id = existing[0] if existing else None
            upsert_item(data, item_id=item_id)
            imported += 1
        except Exception as e:
            warnings.append(f"Row '{name}' skipped: {e}")

    conn.close()
    return imported, warnings


def import_products_catalog_csv(file_obj) -> tuple[int, list[str]]:
    """
    Import products from CSV. One row per product.

    Product columns (case-insensitive, spaces→underscores):
      name*, asin, sku, upc, product_type,
      width_cm, length_cm, height_cm,
      is_new_product, notes,
      carton_units, carton_length_cm, carton_width_cm, carton_height_cm,
      carton_nw_kg, carton_gw_kg, carton_cbm,
      part_id_1, part_id_2   ← items.part_id references (each qty=1)

    (* required)
    Returns (rows_saved, warnings).
    """
    import pandas as pd

    try:
        sample = file_obj.read(4096)
        if isinstance(sample, bytes):
            sample = sample.decode("utf-8", errors="replace")
        file_obj.seek(0)
        first_line = sample.split("\n")[0]
        sep = "\t" if first_line.count("\t") >= first_line.count(",") else ","
        df = pd.read_csv(file_obj, dtype=str, sep=sep)
    except Exception as e:
        return 0, [f"Failed to read CSV: {e}"]

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    if "name" not in df.columns:
        return 0, ["Missing required column: 'name'"]

    # Build item lookup: part_id → id (primary), name → id (fallback)
    items_list = get_items()
    item_map_by_part_id = {i["part_id"]: i["id"] for i in items_list if i.get("part_id")}
    item_map = {i["name"].lower(): i["id"] for i in items_list}

    warnings: list[str] = []
    saved = 0

    def _f(row, col, default=0.0):
        try:
            v = row.get(col)
            return float(v) if v and str(v).strip() not in ("", "nan") else default
        except Exception:
            return default

    def _s(row, col):
        v = str(row.get(col, "")).strip()
        return v if v and v != "nan" else None

    # Group rows by (asin, name) key to accumulate components
    conn = get_conn()
    processed: dict[str, int] = {}  # key → product_id

    # Build item lookup: part_id → part_id (validate existence)
    valid_part_ids = {i["part_id"] for i in get_items() if i.get("part_id")}

    for _, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        asin = (_s(row, "asin") or "").strip().upper() or None

        # Find existing product by ASIN then name
        existing_id = None
        if asin:
            r = conn.execute("SELECT id FROM products_catalog WHERE asin=?", (asin,)).fetchone()
            if r:
                existing_id = r[0]
        if existing_id is None:
            r = conn.execute("SELECT id FROM products_catalog WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
            if r:
                existing_id = r[0]

        # Validate part_id references
        p1_raw = _s(row, "part_id_1")
        p2_raw = _s(row, "part_id_2")
        p1 = p1_raw if p1_raw and p1_raw in valid_part_ids else None
        p2 = p2_raw if p2_raw and p2_raw in valid_part_ids else None
        if p1_raw and not p1:
            warnings.append(f"Product '{name}': part_id_1 '{p1_raw}' not found in items — ignored.")
        if p2_raw and not p2:
            warnings.append(f"Product '{name}': part_id_2 '{p2_raw}' not found in items — ignored.")

        data = {
            "asin":           asin,
            "sku":            _s(row, "sku"),
            "upc":            _s(row, "upc"),
            "name":           name,
            "product_type":   _s(row, "product_type"),
            "width_cm":       _f(row, "width_cm") or None,
            "length_cm":      _f(row, "length_cm") or None,
            "height_cm":      _f(row, "height_cm") or None,
            "weight_gr":      _f(row, "weight_gr") or None,
            "is_new_product": 1 if str(row.get("is_new_product", "")).strip().lower() in ("1", "true", "yes") else 0,
            "notes":          _s(row, "notes"),
            "carton_units":       int(_f(row, "carton_units") or 0) or None,
            "carton_length_cm":   _f(row, "carton_length_cm") or None,
            "carton_width_cm":    _f(row, "carton_width_cm") or None,
            "carton_height_cm":   _f(row, "carton_height_cm") or None,
            "carton_nw_kg":       _f(row, "carton_nw_kg") or None,
            "carton_gw_kg":       _f(row, "carton_gw_kg") or None,
            "carton_cbm":         _f(row, "carton_cbm") or None,
            "part_id_1":      p1,
            "part_id_2":      p2,
        }
        try:
            upsert_product_catalog(data, product_id=existing_id)
            saved += 1
        except Exception as e:
            warnings.append(f"Product '{name}' skipped: {e}")

    conn.close()
    return saved, warnings
