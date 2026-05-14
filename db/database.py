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
        """)
    _migrate_product_costs(conn)
    _migrate_recommendations_score(conn)
    conn.close()


def _migrate_recommendations_score(conn: sqlite3.Connection):
    """Add score column to recommendations if missing."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(recommendations)").fetchall()]
    if "score" not in cols:
        conn.execute("ALTER TABLE recommendations ADD COLUMN score INTEGER")
        conn.commit()


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
