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

            CREATE TABLE IF NOT EXISTS product_costs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                asin            TEXT NOT NULL UNIQUE,
                product_name    TEXT,
                product_cost    REAL DEFAULT 0,
                shipping_cost   REAL DEFAULT 0,
                customs_cost    REAL DEFAULT 0,
                fba_fee         REAL DEFAULT 0,
                updated_at      TEXT DEFAULT (datetime('now'))
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
                ('amazon.com',    1.00, 'USD baseline'),
                ('amazon.co.uk',  0.79, 'GBP — update regularly'),
                ('amazon.ca',     1.36, 'CAD — update regularly'),
                ('amazon.com.au', 1.53, 'AUD — update regularly'),
                ('amazon.de',     0.92, 'EUR — update regularly'),
                ('amazon.fr',     0.92, 'EUR — update regularly'),
                ('amazon.es',     0.92, 'EUR — update regularly'),
                ('amazon.it',     0.92, 'EUR — update regularly');

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
    conn.close()


def _migrate_product_costs_new_product(conn: sqlite3.Connection):
    """Add is_new_product column to product_costs if missing."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(product_costs)").fetchall()]
    if "is_new_product" not in cols:
        conn.execute("ALTER TABLE product_costs ADD COLUMN is_new_product INTEGER DEFAULT 0")
        conn.commit()


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
    Add a unique index on (date_given, campaign_name, placement_type, marketplace)
    so re-uploading the same placement report overwrites rather than duplicates.
    Uses CREATE UNIQUE INDEX IF NOT EXISTS — safe to call repeatedly.
    Existing duplicate rows are removed first (keep lowest id per group).
    """
    try:
        # Remove duplicates before creating the index (keep earliest row per group)
        conn.execute("""
            DELETE FROM recommendations
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM recommendations
                GROUP BY date_given, campaign_name, placement_type, marketplace
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_recs_dedup
            ON recommendations(date_given, campaign_name, placement_type, marketplace)
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
    """Ensure fba_fees table exists (created in executescript; safe no-op)."""
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
                    updated_at    TEXT DEFAULT (datetime('now')),
                    UNIQUE(asin, marketplace)
                )
            """)
            conn.commit()
        except Exception:
            pass


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
                ('amazon.com',    1.00, 'USD baseline'),
                ('amazon.co.uk',  0.79, 'GBP — update regularly'),
                ('amazon.ca',     1.36, 'CAD — update regularly'),
                ('amazon.com.au', 1.53, 'AUD — update regularly'),
                ('amazon.de',     0.92, 'EUR — update regularly'),
                ('amazon.fr',     0.92, 'EUR — update regularly'),
                ('amazon.es',     0.92, 'EUR — update regularly'),
                ('amazon.it',     0.92, 'EUR — update regularly'),
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
