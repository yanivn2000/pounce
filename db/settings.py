"""
db/settings.py — Persistent key-value app settings stored in SQLite.
"""
from db.database import get_conn

# Keys and their defaults
ALERT_KEYS = {
    "alert_roas_drop_pct":   30.0,   # % ROAS must DROP   to trigger negative alert
    "alert_profit_drop_pct": 20.0,   # % profit must DROP to trigger negative alert
    "alert_roas_gain_pct":   30.0,   # % ROAS must RISE   to trigger positive alert
    "alert_profit_gain_pct": 20.0,   # % profit must RISE to trigger positive alert
}


def get_alert_thresholds() -> dict:
    """
    Return the four alert threshold values from DB, falling back to defaults
    for any key not yet stored.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE 'alert_%'"
    ).fetchall()
    conn.close()
    result = dict(ALERT_KEYS)
    for r in rows:
        if r["key"] in result:
            try:
                result[r["key"]] = float(r["value"])
            except (ValueError, TypeError):
                pass
    return result


def save_setting(key: str, value) -> None:
    """Upsert a single setting value."""
    conn = get_conn()
    with conn:
        conn.execute("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value      = excluded.value,
                updated_at = excluded.updated_at
        """, (key, str(value)))
    conn.close()
