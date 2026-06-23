"""
db/database.py — SQLite connection and schema for Pounce.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pounce.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_conn()
    conn.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                asin        TEXT NOT NULL,
                sku         TEXT,
                title       TEXT,
                marketplace TEXT NOT NULL DEFAULT 'amazon.com',
                category    TEXT,
                UNIQUE(asin, marketplace)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id        TEXT NOT NULL,
                order_date      TEXT NOT NULL,
                asin            TEXT,
                sku             TEXT,
                title           TEXT,
                marketplace     TEXT NOT NULL DEFAULT 'amazon.com',
                quantity        INTEGER DEFAULT 0,
                item_price      REAL DEFAULT 0,
                item_tax        REAL DEFAULT 0,
                shipping_price  REAL DEFAULT 0,
                currency        TEXT DEFAULT 'USD',
                order_status    TEXT,
                UNIQUE(order_id, asin)
            );

            CREATE TABLE IF NOT EXISTS change_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date    TEXT NOT NULL,
                asin        TEXT NOT NULL,
                marketplace TEXT NOT NULL DEFAULT 'amazon.com',
                change_type TEXT NOT NULL,
                notes       TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS recommendations (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                date_given              TEXT NOT NULL,
                asin                    TEXT,
                marketplace             TEXT NOT NULL DEFAULT 'amazon.com',
                campaign_name           TEXT,
                placement_type          TEXT,
                campaign_type           TEXT,
                current_multiplier      REAL,
                recommended_action      TEXT,
                recommended_multiplier  REAL,
                reasoning               TEXT,
                window_days             INTEGER DEFAULT 14,
                review_date             TEXT,
                outcome                 TEXT,
                outcome_measured_at     TEXT,
                created_at              TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_orders_date    ON orders(order_date);
            CREATE INDEX IF NOT EXISTS idx_orders_asin    ON orders(asin);
            CREATE INDEX IF NOT EXISTS idx_orders_market  ON orders(marketplace);
            CREATE INDEX IF NOT EXISTS idx_recs_date      ON recommendations(date_given);
            CREATE INDEX IF NOT EXISTS idx_recs_asin      ON recommendations(asin);
            CREATE INDEX IF NOT EXISTS idx_changelog_asin ON change_log(asin, log_date);

            CREATE TABLE IF NOT EXISTS force_logout (
                username     TEXT PRIMARY KEY,
                requested_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS inventory_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date   TEXT NOT NULL,
                asin            TEXT NOT NULL,
                sku             TEXT,
                title           TEXT,
                location        TEXT NOT NULL,
                units_available INTEGER DEFAULT 0,
                units_inbound   INTEGER DEFAULT 0,
                units_reserved  INTEGER DEFAULT 0,
                source          TEXT DEFAULT 'upload',
                created_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(asin, location, snapshot_date)
            );

            CREATE TABLE IF NOT EXISTS sku_asin_map (
                sku     TEXT PRIMARY KEY,
                asin    TEXT NOT NULL,
                title   TEXT,
                source  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_inv_asin     ON inventory_snapshots(asin);
            CREATE INDEX IF NOT EXISTS idx_inv_location ON inventory_snapshots(location);
            CREATE INDEX IF NOT EXISTS idx_inv_date     ON inventory_snapshots(snapshot_date);

            CREATE TABLE IF NOT EXISTS fba_fees (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                asin          TEXT NOT NULL,
                marketplace   TEXT NOT NULL,
                pick_pack_fee REAL DEFAULT 0,
                referral_fee  REAL DEFAULT 0,
                currency      TEXT DEFAULT 'USD',
                updated_at    TEXT DEFAULT (datetime('now')),
                UNIQUE(asin, marketplace)
            );

            CREATE TABLE IF NOT EXISTS fx_rates (
                marketplace TEXT PRIMARY KEY,
                rate        REAL NOT NULL DEFAULT 1.0,
                note        TEXT,
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS campaign_performance (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date   TEXT NOT NULL,
                campaign_name   TEXT NOT NULL,
                marketplace     TEXT NOT NULL,
                placement_type  TEXT NOT NULL,
                roas            REAL,
                spend           REAL,
                sales           REAL,
                purchases       INTEGER,
                total_profit    REAL,
                margin_per_unit REAL,
                breakeven_roas  REAL,
                UNIQUE(snapshot_date, campaign_name, placement_type, marketplace)
            );
            CREATE INDEX IF NOT EXISTS idx_camp_perf ON campaign_performance(campaign_name, marketplace, snapshot_date);

            INSERT OR IGNORE INTO fx_rates (marketplace, rate, note) VALUES
                ('amazon.com',    1.00,  'USD baseline'),
                ('amazon.co.uk',  0.79,  'GBP — update regularly'),
                ('amazon.ca',     1.36,  'CAD — update regularly'),
                ('amazon.com.au', 1.53,  'AUD — update regularly'),
                ('amazon.de',     0.92,  'EUR — update regularly'),
                ('amazon.fr',     0.92,  'EUR — update regularly'),
                ('amazon.es',     0.92,  'EUR — update regularly'),
                ('amazon.it',     0.92,  'EUR — update regularly'),
                ('amazon.nl',     0.92,  'EUR — update regularly'),
                ('amazon.be',     0.92,  'EUR — update regularly'),
                ('amazon.ie',     0.92,  'EUR — update regularly'),
                ('amazon.se',    10.50,  'SEK — update regularly'),
                ('amazon.pl',     4.00,  'PLN — update regularly'),
                ('amazon.com.mx', 17.50, 'MXN — update regularly'),
                ('amazon.com.br', 5.80,  'BRL — update regularly');

            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            INSERT OR IGNORE INTO app_settings (key, value) VALUES
                ('alert_roas_drop_pct',   '30'),
                ('alert_profit_drop_pct', '20'),
                ('alert_roas_gain_pct',   '30'),
                ('alert_profit_gain_pct', '20');

            CREATE TABLE IF NOT EXISTS suppliers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                category        TEXT,
                is_manufacturer INTEGER DEFAULT 1,
                notes           TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS items (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                item_type         TEXT NOT NULL,
                supplier_id       INTEGER REFERENCES suppliers(id),
                manufacturer_cost REAL NOT NULL DEFAULT 0,
                service_cost      REAL NOT NULL DEFAULT 0,
                net_width_cm      REAL,
                hst_code          TEXT,
                upc               TEXT,
                currency          TEXT DEFAULT 'USD',
                notes             TEXT,
                updated_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS products_catalog (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                asin            TEXT,
                sku             TEXT,
                name            TEXT NOT NULL,
                product_type    TEXT,
                marketplace     TEXT DEFAULT 'amazon.com',
                width_cm        REAL,
                length_cm       REAL,
                height_cm       REAL,
                weight_kg       REAL,
                shipping_cost   REAL DEFAULT 0,
                customs_rate    REAL DEFAULT 0,
                fba_fee         REAL DEFAULT 0,
                is_new_product  INTEGER DEFAULT 0,
                notes           TEXT,
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS product_components (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL REFERENCES products_catalog(id) ON DELETE CASCADE,
                item_id     INTEGER NOT NULL REFERENCES items(id),
                quantity    INTEGER NOT NULL DEFAULT 1,
                UNIQUE(product_id, item_id)
            );

            CREATE TABLE IF NOT EXISTS productions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL UNIQUE,
                est_start_date    TEXT,
                est_delivery_date TEXT,
                notes             TEXT,
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS production_lines (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                production_id INTEGER NOT NULL REFERENCES productions(id) ON DELETE CASCADE,
                sku           TEXT NOT NULL,
                num_cartons   INTEGER DEFAULT 0,
                service_cost  REAL    DEFAULT 0
            );
        """)
    _migrate_product_costs(conn)
    _migrate_recommendations_score(conn)
    _migrate_recommendations_source(conn)
    _migrate_product_costs_new_product(conn)
    _migrate_recommendations_end_date(conn)
    _migrate_fba_fees(conn)
    _migrate_fx_rates(conn)
    _migrate_recommendations_dedup_index(conn)
    _migrate_recommendations_debug_json(conn)
    _migrate_campaign_performance(conn)
    _migrate_app_settings(conn)
    _migrate_product_catalog(conn)
    _migrate_product_costs_to_view(conn)
    _migrate_suppliers_contact_fields(conn)
    _migrate_products_schema_v2(conn)
    _migrate_items_net_weight(conn)
    _migrate_products_carton(conn)
    _migrate_items_supplier_code(conn)
    _migrate_products_upc(conn)
    _migrate_products_weight_gr(conn)
    _migrate_items_part_id(conn)
    _migrate_products_part_ids(conn)
    _migrate_products_image_url(conn)
    _migrate_productions(conn)
    _migrate_shipments(conn)
    _migrate_bid_changes(conn)
    _migrate_recommendations_notes(conn)
    _migrate_placement_snapshots(conn)
    _migrate_returns(conn)
    _migrate_orders_address_fields(conn)
    _migrate_bundle_sales(conn)
    _migrate_bid_changes_unique_constraint(conn)
    # Amazon transactions module
    from db.amazon_module import init_amazon_tables
    init_amazon_tables(conn)
    # Aged inventory surcharge
    from db.aged_inventory import init_aged_inventory_table
    init_aged_inventory_table(conn)
    conn.close()


def _migrate_bid_changes(conn: sqlite3.Connection):
    """Create bid_changes table — auto-populated on report upload when bid shifts."""
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bid_changes (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_name  TEXT NOT NULL,
                placement_type TEXT NOT NULL,
                marketplace    TEXT NOT NULL,
                report_date    TEXT NOT NULL,
                bid_before     REAL NOT NULL,
                bid_after      REAL NOT NULL,
                roas           REAL,
                spend          REAL,
                purchases      INTEGER,
                profit         REAL,
                notes          TEXT,
                created_at     TEXT DEFAULT (datetime('now')),
                UNIQUE(campaign_name, placement_type, marketplace, report_date)
            );
            CREATE INDEX IF NOT EXISTS idx_bid_changes_camp
                ON bid_changes(campaign_name, marketplace, report_date);
        """)
    except Exception:
        pass
    # Add notes column to existing installations
    try:
        conn.execute("ALTER TABLE bid_changes ADD COLUMN notes TEXT")
        conn.commit()
    except Exception:
        pass


def _migrate_bid_changes_unique_constraint(conn: sqlite3.Connection):
    """
    Fix bid_changes UNIQUE constraint to include bid_before + bid_after.
    Old: UNIQUE(campaign_name, placement_type, marketplace, report_date)
    New: UNIQUE(campaign_name, placement_type, marketplace, report_date, bid_before, bid_after)
    This allows multiple bid changes per campaign on the same day (e.g. 15→35 and 35→50).
    SQLite doesn't support DROP CONSTRAINT so we recreate the table.
    """
    try:
        # Check if old constraint exists (no bid_before/after in unique index)
        idx = conn.execute("""
            SELECT sql FROM sqlite_master
            WHERE type='table' AND name='bid_changes'
        """).fetchone()
        if idx and "bid_before" not in (idx[0] or "").split("UNIQUE")[1] if "UNIQUE" in (idx[0] or "") else False:
            return  # Already migrated
        # Check if already has the new constraint
        tbl_sql = (idx[0] or "") if idx else ""
        if "bid_before" in tbl_sql and "bid_after" in tbl_sql:
            return  # Already correct
        conn.executescript("""
            PRAGMA foreign_keys=OFF;
            CREATE TABLE IF NOT EXISTS bid_changes_new (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_name  TEXT NOT NULL,
                placement_type TEXT NOT NULL,
                marketplace    TEXT NOT NULL,
                report_date    TEXT NOT NULL,
                bid_before     REAL NOT NULL,
                bid_after      REAL NOT NULL,
                roas           REAL,
                spend          REAL,
                purchases      INTEGER,
                profit         REAL,
                notes          TEXT,
                created_at     TEXT DEFAULT (datetime('now')),
                UNIQUE(campaign_name, placement_type, marketplace, report_date, bid_before, bid_after)
            );
            INSERT OR IGNORE INTO bid_changes_new
                SELECT id, campaign_name, placement_type, marketplace, report_date,
                       bid_before, bid_after, roas, spend, purchases, profit, notes, created_at
                FROM bid_changes;
            DROP TABLE bid_changes;
            ALTER TABLE bid_changes_new RENAME TO bid_changes;
            CREATE INDEX IF NOT EXISTS idx_bid_changes_camp
                ON bid_changes(campaign_name, marketplace, report_date);
            PRAGMA foreign_keys=ON;
        """)
        conn.commit()
    except Exception:
        pass


def _migrate_placement_snapshots(conn: sqlite3.Connection):
    """
    Create placement_snapshots table — records ROAS/spend/purchases/bid_pct
    on EVERY report upload (not only on bid changes).
    Used to answer "did the bid change work?" by comparing ROAS before/after.
    """
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS placement_snapshots (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_name  TEXT NOT NULL,
                placement_type TEXT NOT NULL,
                marketplace    TEXT NOT NULL,
                report_date    TEXT NOT NULL,
                roas           REAL,
                spend          REAL,
                purchases      INTEGER,
                bid_pct        REAL,
                created_at     TEXT DEFAULT (datetime('now')),
                UNIQUE(campaign_name, placement_type, marketplace, report_date)
            );
            CREATE INDEX IF NOT EXISTS idx_pl_snap_camp
                ON placement_snapshots(campaign_name, marketplace, report_date);
        """)
    except Exception:
        pass


def _migrate_recommendations_notes(conn: sqlite3.Connection):
    """Add notes column to recommendations if missing."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(recommendations)").fetchall()]
        if "notes" not in cols:
            conn.execute("ALTER TABLE recommendations ADD COLUMN notes TEXT")
            conn.commit()
    except Exception:
        pass


def _migrate_product_costs_new_product(conn: sqlite3.Connection):
    """Add is_new_product column to the product_costs TABLE if it still exists as a table."""
    try:
        obj = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='product_costs'"
        ).fetchone()
        if not obj or obj[0] != 'table':
            return  # already a VIEW, or doesn't exist yet — nothing to do
        cols = [r[1] for r in conn.execute("PRAGMA table_info(product_costs)").fetchall()]
        if "is_new_product" not in cols:
            conn.execute("ALTER TABLE product_costs ADD COLUMN is_new_product INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass


def _migrate_recommendations_source(conn: sqlite3.Connection):
    """Add source column to recommendations if missing."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(recommendations)").fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE recommendations ADD COLUMN source TEXT DEFAULT 'auto'")
        conn.commit()


def _migrate_recommendations_end_date(conn: sqlite3.Connection):
    """Add end_date column to recommendations if missing."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(recommendations)").fetchall()]
    if "end_date" not in cols:
        try:
            conn.execute("ALTER TABLE recommendations ADD COLUMN end_date TEXT")
            conn.commit()
        except Exception:
            pass  # column already exists (race condition or prior partial migration)


def _migrate_recommendations_dedup_index(conn: sqlite3.Connection):
    """
    Unique index on (campaign_name, placement_type, marketplace) so each
    campaign/placement/marketplace combo keeps only the latest recommendation.
    Re-uploading any window overwrites the previous row for that campaign.
    Drops the old date_given-keyed index if present, then deduplicates keeping
    the most recent row (MAX id) per group.
    """
    try:
        # Drop old index (included date_given, causing duplicates across uploads)
        conn.execute("DROP INDEX IF EXISTS idx_recs_dedup")
        # Keep only the latest row per campaign/placement/marketplace
        conn.execute("""
            DELETE FROM recommendations
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM recommendations
                GROUP BY campaign_name, placement_type, marketplace
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_recs_dedup
            ON recommendations(campaign_name, placement_type, marketplace)
        """)
        conn.commit()
    except Exception:
        pass


def flag_force_logout(username: str):
    """Mark a user to be logged out on their next page load."""
    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO force_logout (username) VALUES (?)", (username,)
        )
    conn.close()


def check_and_clear_force_logout(username: str) -> bool:
    """Return True (and clear the flag) if this user has been force-logged out."""
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM force_logout WHERE username = ?", (username,)
    ).fetchone()
    if row:
        with conn:
            conn.execute("DELETE FROM force_logout WHERE username = ?", (username,))
        conn.close()
        return True
    conn.close()
    return False


def list_force_logout_users() -> list[str]:
    """Return usernames currently flagged for force logout."""
    conn = get_conn()
    rows = conn.execute("SELECT username FROM force_logout").fetchall()
    conn.close()
    return [r[0] for r in rows]


def _migrate_recommendations_score(conn: sqlite3.Connection):
    """Add score column to recommendations if missing."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(recommendations)").fetchall()]
    if "score" not in cols:
        conn.execute("ALTER TABLE recommendations ADD COLUMN score INTEGER")
        conn.commit()


def _migrate_fba_fees(conn: sqlite3.Connection):
    """Ensure fba_fees table exists and has size_tier column."""
    try:
        conn.execute("SELECT 1 FROM fba_fees LIMIT 1")
    except Exception:
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fba_fees (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    asin          TEXT NOT NULL,
                    marketplace   TEXT NOT NULL,
                    pick_pack_fee REAL DEFAULT 0,
                    referral_fee  REAL DEFAULT 0,
                    currency      TEXT DEFAULT 'USD',
                    size_tier     TEXT DEFAULT '',
                    updated_at    TEXT DEFAULT (datetime('now')),
                    UNIQUE(asin, marketplace)
                )
            """)
            conn.commit()
        except Exception:
            pass
    # Add columns to existing tables that predate them
    cols = [r[1] for r in conn.execute("PRAGMA table_info(fba_fees)").fetchall()]
    if "size_tier" not in cols:
        conn.execute("ALTER TABLE fba_fees ADD COLUMN size_tier TEXT DEFAULT ''")
        conn.commit()
    if "has_local_inventory" not in cols:
        conn.execute("ALTER TABLE fba_fees ADD COLUMN has_local_inventory TEXT DEFAULT ''")
        conn.commit()
    if "remote_fulfillment_fee" not in cols:
        conn.execute("ALTER TABLE fba_fees ADD COLUMN remote_fulfillment_fee REAL DEFAULT NULL")
        conn.commit()


def _migrate_fx_rates(conn: sqlite3.Connection):
    """Ensure fx_rates table and seed rows exist (safe no-op if already present)."""
    try:
        conn.execute("SELECT 1 FROM fx_rates LIMIT 1")
    except Exception:
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fx_rates (
                    marketplace TEXT PRIMARY KEY,
                    rate        REAL NOT NULL DEFAULT 1.0,
                    note        TEXT,
                    updated_at  TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
        except Exception:
            pass
    # Always attempt seed — INSERT OR IGNORE is safe
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO fx_rates (marketplace, rate, note) VALUES (?,?,?)",
            [
                ('amazon.com',    1.00,  'USD baseline'),
                ('amazon.co.uk',  0.79,  'GBP — update regularly'),
                ('amazon.ca',     1.36,  'CAD — update regularly'),
                ('amazon.com.au', 1.53,  'AUD — update regularly'),
                ('amazon.de',     0.92,  'EUR — update regularly'),
                ('amazon.fr',     0.92,  'EUR — update regularly'),
                ('amazon.es',     0.92,  'EUR — update regularly'),
                ('amazon.it',     0.92,  'EUR — update regularly'),
                ('amazon.nl',     0.92,  'EUR — update regularly'),
                ('amazon.be',     0.92,  'EUR — update regularly'),
                ('amazon.ie',     0.92,  'EUR — update regularly'),
                ('amazon.se',    10.50,  'SEK — update regularly'),
                ('amazon.pl',     4.00,  'PLN — update regularly'),
                ('amazon.com.mx', 17.50, 'MXN — update regularly'),
                ('amazon.com.br', 5.80,  'BRL — update regularly'),
            ]
        )
        conn.commit()
    except Exception:
        pass


def _migrate_campaign_performance(conn: sqlite3.Connection):
    """Ensure campaign_performance table and its index exist."""
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS campaign_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL, campaign_name TEXT NOT NULL,
            marketplace TEXT NOT NULL, placement_type TEXT NOT NULL,
            roas REAL, spend REAL, sales REAL, purchases INTEGER,
            total_profit REAL, margin_per_unit REAL, breakeven_roas REAL,
            UNIQUE(snapshot_date, campaign_name, placement_type, marketplace))""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camp_perf ON campaign_performance(campaign_name, marketplace, snapshot_date)")
        conn.commit()
    except Exception:
        pass


def _migrate_recommendations_debug_json(conn: sqlite3.Connection):
    """Add debug_json column to recommendations if missing."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(recommendations)").fetchall()]
        if "debug_json" not in cols:
            conn.execute("ALTER TABLE recommendations ADD COLUMN debug_json TEXT")
            conn.commit()
    except Exception:
        pass


def _migrate_app_settings(conn: sqlite3.Connection):
    """Ensure app_settings table exists and default alert thresholds are seeded."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.executemany(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
            [
                ('alert_roas_drop_pct',   '30'),
                ('alert_profit_drop_pct', '20'),
                ('alert_roas_gain_pct',   '30'),
                ('alert_profit_gain_pct', '20'),
            ]
        )
        conn.commit()
    except Exception:
        pass


def _migrate_product_costs(conn: sqlite3.Connection):
    """If product_costs still has a marketplace column, collapse to one row per ASIN."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(product_costs)").fetchall()]
    if "marketplace" not in cols:
        return
    # Recreate without marketplace column, keeping one row per ASIN (latest updated_at)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS product_costs_new (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            asin          TEXT NOT NULL UNIQUE,
            product_name  TEXT,
            product_cost  REAL DEFAULT 0,
            shipping_cost REAL DEFAULT 0,
            customs_cost  REAL DEFAULT 0,
            fba_fee       REAL DEFAULT 0,
            updated_at    TEXT DEFAULT (datetime('now'))
        );
        INSERT OR IGNORE INTO product_costs_new
            (asin, product_name, product_cost, shipping_cost, customs_cost, fba_fee, updated_at)
        SELECT asin, product_name, product_cost, shipping_cost, customs_cost, fba_fee, MAX(updated_at)
        FROM product_costs
        GROUP BY asin;
        DROP TABLE product_costs;
        ALTER TABLE product_costs_new RENAME TO product_costs;
    """)
    conn.close()


def _migrate_suppliers_contact_fields(conn: sqlite3.Connection):
    """Add address, contact_person, email, tel columns to suppliers if missing."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(suppliers)").fetchall()]
        for col, typedef in [
            ("address",        "TEXT"),
            ("contact_person", "TEXT"),
            ("email",          "TEXT"),
            ("tel",            "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE suppliers ADD COLUMN {col} {typedef}")
        conn.commit()
    except Exception:
        pass


def _migrate_products_schema_v2(conn: sqlite3.Connection):
    """
    1. Add hst_code_na and hst_code_uk columns to items (replacing hst_code).
    2. Remove marketplace and customs_rate columns from products_catalog.
    3. Rebuild the product_costs VIEW without customs_rate (customs_cost = 0).
    """
    try:
        # ── items: add hst_code_na / hst_code_uk ──────────────────────────────
        item_cols = [r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
        if "hst_code_na" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN hst_code_na TEXT")
        if "hst_code_uk" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN hst_code_uk TEXT")
        # Migrate existing hst_code value → hst_code_na
        if "hst_code" in item_cols:
            conn.execute("""
                UPDATE items SET hst_code_na = hst_code
                WHERE hst_code IS NOT NULL AND hst_code != '' AND hst_code_na IS NULL
            """)
            try:
                conn.execute("ALTER TABLE items DROP COLUMN hst_code")
            except Exception:
                pass  # SQLite < 3.35 — leave column, just stop using it

        # ── products_catalog: drop marketplace and customs_rate ─────────────
        cat_cols = [r[1] for r in conn.execute("PRAGMA table_info(products_catalog)").fetchall()]
        for col in ("marketplace", "customs_rate"):
            if col in cat_cols:
                try:
                    conn.execute(f"ALTER TABLE products_catalog DROP COLUMN {col}")
                except Exception:
                    pass  # SQLite < 3.35 — leave column, just stop using it

        # ── Rebuild product_costs VIEW (customs_cost = 0) ───────────────────
        obj = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='product_costs'"
        ).fetchone()
        if obj and obj[0] == 'view':
            conn.execute("DROP VIEW product_costs")

        conn.execute("""
            CREATE VIEW IF NOT EXISTS product_costs AS
            SELECT
                pc.id,
                pc.asin,
                pc.name                          AS product_name,
                COALESCE((
                    SELECT SUM((i.manufacturer_cost + i.service_cost) * pcomp.quantity)
                    FROM product_components pcomp
                    JOIN items i ON i.id = pcomp.item_id
                    WHERE pcomp.product_id = pc.id
                ), 0)                            AS product_cost,
                pc.shipping_cost,
                0.0                              AS customs_cost,
                COALESCE(pc.fba_fee, 0)          AS fba_fee,
                COALESCE(pc.is_new_product, 0)   AS is_new_product,
                pc.updated_at
            FROM products_catalog pc
            WHERE pc.asin IS NOT NULL AND pc.asin != ''
        """)
        conn.commit()
    except Exception:
        pass


def _migrate_product_costs_to_view(conn: sqlite3.Connection):
    """
    Replace the product_costs TABLE with a VIEW computed from products_catalog.
    Steps:
      1. If product_costs is already a view, return immediately.
      2. If product_costs is a table, migrate any orphan rows (ASIN not already in
         products_catalog) as stub catalog entries, then DROP the table.
      3. CREATE VIEW product_costs computing costs from products_catalog + components + items.
    """
    try:
        obj = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='product_costs'"
        ).fetchone()
        if obj and obj[0] == 'view':
            return  # already migrated

        if obj and obj[0] == 'table':
            # Migrate orphan rows into products_catalog as stubs
            conn.execute("""
                INSERT OR IGNORE INTO products_catalog
                    (asin, name, fba_fee, is_new_product)
                SELECT asin,
                       COALESCE(product_name, asin),
                       COALESCE(fba_fee, 0),
                       COALESCE(is_new_product, 0)
                FROM product_costs
                WHERE asin IS NOT NULL AND asin != ''
                  AND NOT EXISTS (
                      SELECT 1 FROM products_catalog cat WHERE cat.asin = product_costs.asin
                  )
            """)
            conn.execute("DROP TABLE product_costs")

        conn.execute("""
            CREATE VIEW IF NOT EXISTS product_costs AS
            SELECT
                pc.id,
                pc.asin,
                pc.name                          AS product_name,
                COALESCE((
                    SELECT SUM((i.manufacturer_cost + i.service_cost) * pcomp.quantity)
                    FROM product_components pcomp
                    JOIN items i ON i.id = pcomp.item_id
                    WHERE pcomp.product_id = pc.id
                ), 0)                            AS product_cost,
                pc.shipping_cost,
                COALESCE((
                    SELECT SUM(i.manufacturer_cost * pcomp.quantity)
                    FROM product_components pcomp
                    JOIN items i ON i.id = pcomp.item_id
                    WHERE pcomp.product_id = pc.id
                ), 0) * pc.customs_rate / 100.0  AS customs_cost,
                COALESCE(pc.fba_fee, 0)          AS fba_fee,
                COALESCE(pc.is_new_product, 0)   AS is_new_product,
                pc.updated_at
            FROM products_catalog pc
            WHERE pc.asin IS NOT NULL AND pc.asin != ''
        """)
        conn.commit()
    except Exception:
        pass


def _migrate_product_catalog(conn: sqlite3.Connection):
    """Ensure suppliers, items, products_catalog, product_components tables exist."""
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS suppliers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                category        TEXT,
                is_manufacturer INTEGER DEFAULT 1,
                notes           TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS items (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                item_type         TEXT NOT NULL,
                supplier_id       INTEGER REFERENCES suppliers(id),
                manufacturer_cost REAL NOT NULL DEFAULT 0,
                service_cost      REAL NOT NULL DEFAULT 0,
                net_width_cm      REAL,
                hst_code          TEXT,
                upc               TEXT,
                currency          TEXT DEFAULT 'USD',
                notes             TEXT,
                updated_at        TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS products_catalog (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                asin            TEXT,
                sku             TEXT,
                name            TEXT NOT NULL,
                product_type    TEXT,
                marketplace     TEXT DEFAULT 'amazon.com',
                width_cm        REAL,
                length_cm       REAL,
                height_cm       REAL,
                weight_kg       REAL,
                shipping_cost   REAL DEFAULT 0,
                customs_rate    REAL DEFAULT 0,
                fba_fee         REAL DEFAULT 0,
                is_new_product  INTEGER DEFAULT 0,
                notes           TEXT,
                updated_at      TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS product_components (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL REFERENCES products_catalog(id) ON DELETE CASCADE,
                item_id     INTEGER NOT NULL REFERENCES items(id),
                quantity    INTEGER NOT NULL DEFAULT 1,
                UNIQUE(product_id, item_id)
            );
        """)
        conn.commit()
    except Exception:
        pass


def _migrate_items_net_weight(conn: sqlite3.Connection):
    """Rename net_width_cm → net_weight_grams in items table."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
        if "net_weight_grams" not in cols:
            if "net_width_cm" in cols:
                # SQLite 3.25+ supports RENAME COLUMN
                conn.execute("ALTER TABLE items RENAME COLUMN net_width_cm TO net_weight_grams")
            else:
                conn.execute("ALTER TABLE items ADD COLUMN net_weight_grams REAL")
            conn.commit()
    except Exception:
        pass


def _migrate_products_carton(conn: sqlite3.Connection):
    """Add master carton fields to products_catalog."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(products_catalog)").fetchall()]
        for col, typedef in [
            ("carton_units",     "INTEGER"),
            ("carton_length_cm", "REAL"),
            ("carton_width_cm",  "REAL"),
            ("carton_height_cm", "REAL"),
            ("carton_nw_kg",     "REAL"),
            ("carton_gw_kg",     "REAL"),
            ("carton_cbm",       "REAL"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE products_catalog ADD COLUMN {col} {typedef}")
        conn.commit()
    except Exception:
        pass


def _migrate_items_part_id(conn: sqlite3.Connection):
    """Add part_id column to items (user-defined unique identifier)."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
        if "part_id" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN part_id TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_part_id ON items(part_id) WHERE part_id IS NOT NULL")
            conn.commit()
    except Exception:
        pass


def _migrate_products_part_ids(conn: sqlite3.Connection):
    """Add part_id_1 / part_id_2 to products_catalog and migrate existing product_components."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(products_catalog)").fetchall()]
        if "part_id_1" not in cols:
            conn.execute("ALTER TABLE products_catalog ADD COLUMN part_id_1 TEXT")
        if "part_id_2" not in cols:
            conn.execute("ALTER TABLE products_catalog ADD COLUMN part_id_2 TEXT")

        # Migrate existing product_components rows → part_id_1 / part_id_2
        products = conn.execute("SELECT id FROM products_catalog").fetchall()
        for prod in products:
            comps = conn.execute("""
                SELECT i.part_id
                FROM product_components pc
                JOIN items i ON i.id = pc.item_id
                WHERE pc.product_id = ?
                ORDER BY pc.id
                LIMIT 2
            """, (prod[0],)).fetchall()
            p1 = comps[0][0] if len(comps) > 0 else None
            p2 = comps[1][0] if len(comps) > 1 else None
            if p1 or p2:
                conn.execute(
                    "UPDATE products_catalog SET part_id_1=?, part_id_2=? WHERE id=? AND part_id_1 IS NULL",
                    (p1, p2, prod[0]),
                )
        conn.commit()
    except Exception:
        pass


def _migrate_products_weight_gr(conn: sqlite3.Connection):
    """Rename products_catalog.weight_kg → weight_gr."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(products_catalog)").fetchall()]
        if "weight_gr" not in cols:
            if "weight_kg" in cols:
                conn.execute("ALTER TABLE products_catalog RENAME COLUMN weight_kg TO weight_gr")
            else:
                conn.execute("ALTER TABLE products_catalog ADD COLUMN weight_gr REAL")
            conn.commit()
    except Exception:
        pass


def _migrate_items_supplier_code(conn: sqlite3.Connection):
    """Rename items.upc → items.supplier_code."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
        if "supplier_code" not in cols:
            if "upc" in cols:
                conn.execute("ALTER TABLE items RENAME COLUMN upc TO supplier_code")
            else:
                conn.execute("ALTER TABLE items ADD COLUMN supplier_code TEXT")
            conn.commit()
    except Exception:
        pass


def _migrate_products_upc(conn: sqlite3.Connection):
    """Add upc column to products_catalog."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(products_catalog)").fetchall()]
        if "upc" not in cols:
            conn.execute("ALTER TABLE products_catalog ADD COLUMN upc TEXT")
            conn.commit()
    except Exception:
        pass


def _migrate_products_image_url(conn: sqlite3.Connection):
    """Add image_url column to products_catalog."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(products_catalog)").fetchall()]
        if "image_url" not in cols:
            conn.execute("ALTER TABLE products_catalog ADD COLUMN image_url TEXT")
            conn.commit()
    except Exception:
        pass


def _migrate_shipments(conn: sqlite3.Connection):
    """Ensure shipments and shipment_lines tables exist."""
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS shipments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                destination TEXT,
                address     TEXT,
                status      TEXT NOT NULL DEFAULT 'draft',
                notes       TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS shipment_lines (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
                sku         TEXT NOT NULL,
                num_cartons INTEGER NOT NULL DEFAULT 0
            );
        """)
    except Exception:
        pass
    # Add address column to existing DBs that pre-date this field
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(shipments)").fetchall()]
        if "address" not in cols:
            conn.execute("ALTER TABLE shipments ADD COLUMN address TEXT")
            conn.commit()
    except Exception:
        pass


def _migrate_productions(conn: sqlite3.Connection):
    """Ensure productions and production_lines tables exist (safe no-op if already created)."""
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS productions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL UNIQUE,
                est_start_date    TEXT,
                est_delivery_date TEXT,
                notes             TEXT,
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS production_lines (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                production_id INTEGER NOT NULL REFERENCES productions(id) ON DELETE CASCADE,
                sku           TEXT NOT NULL,
                num_cartons   INTEGER DEFAULT 0,
                service_cost  REAL    DEFAULT 0
            );
        """)
    except Exception:
        pass


def _migrate_bundle_sales(conn: sqlite3.Connection):
    """
    Create bundle_sales table for Amazon Bundle Performance reports.
    Columns mirror the CSV: DATE, BUNDLE_ASIN, TITLE, IS_VIRTUAL_MULTIPACK,
    BUNDLES_SOLD, TOTAL_SALES.
    """
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bundle_sales (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_date    TEXT NOT NULL,
                bundle_asin  TEXT NOT NULL,
                title        TEXT,
                is_virtual   INTEGER DEFAULT 0,
                bundles_sold INTEGER DEFAULT 0,
                total_sales  REAL    DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(sale_date, bundle_asin)
            );
            CREATE INDEX IF NOT EXISTS idx_bundle_date  ON bundle_sales(sale_date);
            CREATE INDEX IF NOT EXISTS idx_bundle_asin  ON bundle_sales(bundle_asin);
        """)
        conn.commit()
    except Exception:
        pass


def _migrate_orders_address_fields(conn: sqlite3.Connection):
    """
    Add shipping-address and buyer-name columns to the orders table.
    Amazon order CSVs contain these fields; we start capturing them so
    the Order Search feature can find orders by recipient / address / zip.
    """
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        for col, typedef in [
            ("ship_name",        "TEXT"),
            ("ship_address_1",   "TEXT"),
            ("ship_city",        "TEXT"),
            ("ship_state",       "TEXT"),
            ("ship_postal_code", "TEXT"),
            ("ship_country",     "TEXT"),
            ("buyer_name",       "TEXT"),
            ("buyer_email",      "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {typedef}")
        # Indexes for fast address search
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_orders_ship_country
                ON orders(ship_country);
            CREATE INDEX IF NOT EXISTS idx_orders_ship_postal
                ON orders(ship_postal_code);
        """)
        conn.commit()
    except Exception:
        pass


def _migrate_returns(conn: sqlite3.Connection):
    """Create amazon_returns table and add columns added in later migrations."""
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS amazon_returns (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                return_date       TEXT NOT NULL,
                order_id          TEXT,
                sku               TEXT,
                asin              TEXT,
                title             TEXT,
                quantity          INTEGER DEFAULT 1,
                reason            TEXT,
                disposition       TEXT,
                status            TEXT,
                marketplace       TEXT NOT NULL,
                region            TEXT NOT NULL DEFAULT 'NA',
                country           TEXT,
                fc_id             TEXT,
                customer_comments TEXT,
                created_at        TEXT DEFAULT (datetime('now')),
                UNIQUE(order_id, asin, return_date, marketplace)
            );
            CREATE INDEX IF NOT EXISTS idx_returns_asin    ON amazon_returns(asin);
            CREATE INDEX IF NOT EXISTS idx_returns_date    ON amazon_returns(return_date);
            CREATE INDEX IF NOT EXISTS idx_returns_region  ON amazon_returns(region);
            CREATE INDEX IF NOT EXISTS idx_returns_country ON amazon_returns(country);
        """)
    except Exception:
        pass
    # Add columns to existing tables that were created before these columns existed
    for col, typedef in [("country", "TEXT"), ("fc_id", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE amazon_returns ADD COLUMN {col} {typedef}")
            conn.commit()
        except Exception:
            pass  # column already exists

    # Metadata table — one row per region, records what date range was uploaded
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS returns_meta (
                region          TEXT PRIMARY KEY,
                report_from     TEXT,
                report_to       TEXT,
                last_imported   TEXT,
                rows_imported   INTEGER DEFAULT 0
            );
        """)
    except Exception:
        pass


# ── Report freshness dashboard ────────────────────────────────────────────────
def get_report_freshness() -> list[dict]:
    """
    Return a list of report status dicts for the Data Health dashboard.
    Each dict: { group, label, last_date (YYYY-MM-DD or None), cadence_days }
    cadence_days is the recommended max gap before a yellow alert (red = 2×).
    """
    conn = get_conn()

    def _q(sql, params=()):
        row = conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] else None

    reports = []

    # ── Inventory ─────────────────────────────────────────────────────────
    for loc, label, cadence in [
        ("FBA_US",  "FBA Inventory — US",       14),
        ("FBA_CA",  "FBA Inventory — CA",       14),
        ("FBA_UK",  "FBA Inventory — UK",       14),
        ("AWD_US",  "AWD Inventory — US",       14),
        ("AWD_CN",  "AWD Inventory — CN",       30),
        ("3PL_UK",  "3PL Inventory — UK (SPM)", 14),
        ("WH_CN",   "Warehouse — China",        30),
    ]:
        d = _q(
            "SELECT MAX(snapshot_date) FROM inventory_snapshots WHERE location=?",
            (loc,)
        )
        reports.append({"group": "Inventory", "label": label,
                        "last_date": d[:10] if d else None, "cadence_days": cadence})

    # ── Amazon Transactions ───────────────────────────────────────────────
    mp_rows = conn.execute(
        "SELECT DISTINCT marketplace FROM amazon_transactions "
        "WHERE marketplace IS NOT NULL ORDER BY marketplace"
    ).fetchall()
    for row in mp_rows:
        mp = row[0]
        d = _q("SELECT MAX(tx_date) FROM amazon_transactions WHERE marketplace=?", (mp,))
        reports.append({"group": "Amazon Transactions", "label": f"Transactions — {mp}",
                        "last_date": d[:10] if d else None, "cadence_days": 7})

    # ── FBA Fees ──────────────────────────────────────────────────────────
    d = _q("SELECT MAX(updated_at) FROM fba_fees")
    reports.append({"group": "FBA Fees", "label": "FBA Fee Preview",
                    "last_date": d[:10] if d else None, "cadence_days": 30})

    # ── Aged Inventory Surcharge ──────────────────────────────────────────
    d = _q("SELECT MAX(snapshot_date) FROM aged_inventory")
    reports.append({"group": "Aged Inventory", "label": "Aged Inventory Surcharge",
                    "last_date": d[:10] if d else None, "cadence_days": 30})

    # ── Returns ───────────────────────────────────────────────────────────
    try:
        ret_rows = conn.execute(
            "SELECT region, last_imported FROM returns_meta ORDER BY region"
        ).fetchall()
        for row in ret_rows:
            region, last_imp = row[0], row[1]
            reports.append({"group": "Returns", "label": f"Returns — {region}",
                            "last_date": last_imp[:10] if last_imp else None, "cadence_days": 14})
    except Exception:
        pass

    # ── Advertising / Campaign Performance ───────────────────────────────
    try:
        cp_rows = conn.execute(
            "SELECT DISTINCT marketplace FROM campaign_performance ORDER BY marketplace"
        ).fetchall()
        for row in cp_rows:
            mp = row[0]
            d = _q(
                "SELECT MAX(snapshot_date) FROM campaign_performance WHERE marketplace=?",
                (mp,)
            )
            reports.append({"group": "Advertising", "label": f"Placement Report — {mp}",
                            "last_date": d[:10] if d else None, "cadence_days": 14})
    except Exception:
        pass

    conn.close()
    return reports
