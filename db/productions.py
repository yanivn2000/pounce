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


def get_sku_supplier_cost_map() -> dict:
    """
    Return {sku: {supplier_name: {unit_mfg, unit_svc, unit_nw_kg}}}

    Costs are per-unit contributions from items belonging to that supplier
    (via part_id_1 / part_id_2 → items → suppliers).
    unit_nw_kg = item.net_weight_grams / 1000  (kg per finished unit).

    Used by the Production table's supplier-filtered view so each supplier
    row shows only their share of Product Cost, Service Cost, and Net Weight,
    while CBM and Gross Weight are set to zero (whole-carton attributes that
    cannot be split per supplier).
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT pc.sku,
               sup1.name  AS sup1_name,
               i1.manufacturer_cost  AS i1_mfg,
               i1.service_cost       AS i1_svc,
               i1.net_weight_grams   AS i1_nw,
               sup2.name  AS sup2_name,
               i2.manufacturer_cost  AS i2_mfg,
               i2.service_cost       AS i2_svc,
               i2.net_weight_grams   AS i2_nw
        FROM   products_catalog pc
        LEFT JOIN items     i1   ON i1.part_id  = pc.part_id_1
        LEFT JOIN suppliers sup1 ON sup1.id      = i1.supplier_id
        LEFT JOIN items     i2   ON i2.part_id  = pc.part_id_2
        LEFT JOIN suppliers sup2 ON sup2.id      = i2.supplier_id
        WHERE  pc.sku IS NOT NULL AND pc.sku != ''
    """).fetchall()
    conn.close()

    result: dict = {}
    for row in rows:
        sku = row["sku"]
        if sku not in result:
            result[sku] = {}
        for sup_name, mfg, svc, nw in (
            (row["sup1_name"], row["i1_mfg"], row["i1_svc"], row["i1_nw"]),
            (row["sup2_name"], row["i2_mfg"], row["i2_svc"], row["i2_nw"]),
        ):
            if not sup_name:
                continue
            if sup_name not in result[sku]:
                result[sku][sup_name] = {"unit_mfg": 0.0, "unit_svc": 0.0, "unit_nw_kg": 0.0}
            result[sku][sup_name]["unit_mfg"]   += float(mfg or 0)
            result[sku][sup_name]["unit_svc"]   += float(svc or 0)
            result[sku][sup_name]["unit_nw_kg"] += float(nw  or 0) / 1000.0
    return result


def get_sku_supplier_map() -> dict:
    """
    Return {sku: [supplier_name, ...]} for all SKU-bearing products.
    Each entry is a deduplicated, ordered list of supplier names derived from
    the product's part_id_1 / part_id_2 → items → suppliers chain.
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT pc.sku,
               s1.name AS sup1,
               s2.name AS sup2
        FROM   products_catalog pc
        LEFT JOIN items    i1 ON i1.part_id = pc.part_id_1
        LEFT JOIN suppliers s1 ON s1.id = i1.supplier_id
        LEFT JOIN items    i2 ON i2.part_id = pc.part_id_2
        LEFT JOIN suppliers s2 ON s2.id = i2.supplier_id
        WHERE  pc.sku IS NOT NULL AND pc.sku != ''
    """).fetchall()
    conn.close()

    result: dict = {}
    for row in rows:
        sups: list[str] = []
        for s in (row["sup1"], row["sup2"]):
            if s and s not in sups:
                sups.append(s)
        result[row["sku"]] = sups
    return result


def get_catalog_skus() -> list[str]:
    """Return all non-empty SKUs from products_catalog, sorted."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT sku FROM products_catalog WHERE sku IS NOT NULL AND sku != '' ORDER BY sku"
    ).fetchall()
    conn.close()
    return [r["sku"] for r in rows]


def get_sku_catalog_info() -> dict:
    """
    Return {sku: {name, carton_units, nw_kg, gw_kg, cbm, unit_mfg, unit_svc,
                  length_cm, width_cm, height_cm}}
    for all SKU-bearing products. One DB round-trip.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, sku, upc, name, carton_units, carton_nw_kg, carton_gw_kg, carton_cbm, "
        "carton_length_cm, carton_width_cm, carton_height_cm "
        "FROM products_catalog WHERE sku IS NOT NULL AND sku != '' ORDER BY sku"
    ).fetchall()
    conn.close()

    result = {}
    for row in rows:
        breakdown = calc_product_cost(row["id"])
        result[row["sku"]] = {
            "name":         row["name"] or "",
            "upc":          row["upc"]  or "",
            "carton_units": row["carton_units"] or 0,
            "nw_kg":        row["carton_nw_kg"]  or 0.0,
            "gw_kg":        row["carton_gw_kg"]  or 0.0,
            "cbm":          row["carton_cbm"]    or 0.0,
            "length_cm":    row["carton_length_cm"] or 0.0,
            "width_cm":     row["carton_width_cm"]  or 0.0,
            "height_cm":    row["carton_height_cm"] or 0.0,
            "unit_mfg":     breakdown.get("total_manufacturer", 0.0),
            "unit_svc":     breakdown.get("total_service",      0.0),
        }
    return result


def get_asin_image_map() -> dict[str, str]:
    """
    Return {ASIN (uppercase): image_url} for products that have a stored image_url.
    Only includes ASINs where a URL has been explicitly saved (either fetched
    automatically or pasted manually). No broken CDN guesses.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT asin, image_url FROM products_catalog "
        "WHERE asin IS NOT NULL AND asin != '' "
        "  AND image_url IS NOT NULL AND image_url != ''"
    ).fetchall()
    conn.close()

    return {
        str(r["asin"]).strip().upper(): r["image_url"].strip()
        for r in rows
        if r["asin"] and r["image_url"]
    }


def fetch_asin_image_url(asin: str) -> str | None:
    """
    Fetch the main product image URL for an ASIN by reading the Amazon listing
    page and extracting the og:image meta tag.

    Tries amazon.com first, then amazon.co.uk as a fallback.
    Returns None if the image URL cannot be determined.
    """
    import re
    try:
        import requests as _req
    except ImportError:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    for base in ("https://www.amazon.com/dp/", "https://www.amazon.co.uk/dp/"):
        try:
            resp = _req.get(f"{base}{asin}", headers=headers, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                continue
            html = resp.text
            # 1. og:image meta tag  (most reliable)
            m = re.search(
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                html
            )
            if not m:
                m = re.search(
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                    html
                )
            if m:
                url = m.group(1).strip()
                if url.startswith("http"):
                    return url
            # 2. landingAsinColor JSON blob — hiRes key
            m = re.search(r'"hiRes"\s*:\s*"(https://[^"]+)"', html)
            if m:
                return m.group(1).strip()
        except Exception:
            continue
    return None


def fetch_and_store_all_images() -> dict[str, str]:
    """
    For every ASIN in products_catalog that has no image_url, fetch and store it.
    Returns {asin: result} where result is the URL on success or an error string.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, asin FROM products_catalog "
        "WHERE asin IS NOT NULL AND asin != '' "
        "  AND (image_url IS NULL OR image_url = '')"
    ).fetchall()
    conn.close()

    report: dict[str, str] = {}
    for row in rows:
        asin = str(row["asin"]).strip().upper()
        url  = fetch_asin_image_url(asin)
        if url:
            conn = get_conn()
            with conn:
                conn.execute(
                    "UPDATE products_catalog SET image_url=? WHERE id=?",
                    (url, row["id"])
                )
            conn.close()
            report[asin] = url
        else:
            report[asin] = "NOT_FOUND"
    return report


def get_asin_cost_map() -> dict[str, float]:
    """
    Return {ASIN (uppercase): unit_cost} derived from products_catalog.
    unit_cost = total_manufacturer + total_service per unit.
    Used as fallback when product_costs table is not populated.
    If multiple SKUs share the same ASIN, the highest cost wins.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, asin FROM products_catalog "
        "WHERE asin IS NOT NULL AND asin != ''"
    ).fetchall()
    conn.close()

    result: dict[str, float] = {}
    for row in rows:
        asin = str(row["asin"]).strip().upper()
        if not asin:
            continue
        breakdown = calc_product_cost(row["id"])
        cost = breakdown.get("total_manufacturer", 0.0) + breakdown.get("total_service", 0.0)
        if cost > 0:
            result[asin] = max(result.get(asin, 0.0), cost)
    return result
