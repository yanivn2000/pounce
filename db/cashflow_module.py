"""
cashflow_module.py — Cash flow forecasting for EOS ONLINE LLC + EOS TRADE LTD
"""
from __future__ import annotations

COMPANY_LABELS = {
    "LLC": "EOS ONLINE LLC",
    "IL":  "EOS TRADE LTD",
}

# ── Default accounts (seeded on first run) ──────────────────────────────────
_DEFAULT_ACCOUNTS = [
    # (name,           company, currency, current_balance, credit_limit, sort_order)
    ("Amazon",        "LLC",   "USD",    20500.0,   0.0,      1),
    ("BOFA",          "LLC",   "USD",     5000.0,   0.0,      2),
    ("Wise",          "LLC",   "USD",      200.0,   0.0,      3),
    ("Payoneer",      "LLC",   "USD",     2000.0,   0.0,      4),
    ("Walmart",       "LLC",   "USD",      500.0,   0.0,      5),
    ("Mizrahi USD",   "IL",    "USD",     1000.0,   0.0,      6),
    ("Mizrahi NIS",   "IL",    "ILS",  -108000.0, 100000.0,   7),
]

_CATEGORIES = [
    "Amazon Payout",
    "Walmart",
    "Salary",
    "Supplier / COGS",
    "Logistics",
    "Tax - US",
    "Tax - IL",
    "Office / Visa",
    "Loan Payment",
    "Other Income",
    "Other Expense",
]

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun",
          "Jul","Aug","Sep","Oct","Nov","Dec"]


# ── Init ─────────────────────────────────────────────────────────────────────
def init_cashflow_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cashflow_accounts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            company         TEXT    NOT NULL,
            currency        TEXT    NOT NULL,
            current_balance REAL    DEFAULT 0,
            credit_limit    REAL    DEFAULT 0,
            sort_order      INTEGER DEFAULT 99,
            updated_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
            is_active       INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS cashflow_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            direction   TEXT NOT NULL,          -- 'in' | 'out'
            category    TEXT NOT NULL,
            amount      REAL NOT NULL,
            currency    TEXT NOT NULL,
            frequency   TEXT NOT NULL,          -- 'monthly'|'quarterly'|'annual'|'once'
            company     TEXT NOT NULL DEFAULT 'LLC',  -- 'LLC' | 'IL'
            start_ym    TEXT NOT NULL,          -- 'YYYY-MM'
            end_ym      TEXT,                   -- NULL = forever
            notes       TEXT,
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS cashflow_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id    INTEGER NOT NULL,
            snapshot_date TEXT    NOT NULL,
            balance       REAL    NOT NULL,
            notes         TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Migration: add company column to cashflow_items if it was created without it
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cashflow_items)").fetchall()]
    if "company" not in cols:
        conn.execute("ALTER TABLE cashflow_items ADD COLUMN company TEXT NOT NULL DEFAULT 'LLC'")
    # Migration: drop old account_id column isn't possible in SQLite, just ignore it
    conn.commit()
    # Seed default accounts only if table is empty
    if not conn.execute("SELECT 1 FROM cashflow_accounts LIMIT 1").fetchone():
        conn.executemany(
            "INSERT INTO cashflow_accounts(name,company,currency,current_balance,credit_limit,sort_order) "
            "VALUES(?,?,?,?,?,?)",
            _DEFAULT_ACCOUNTS
        )
        conn.commit()


# ── CRUD helpers ──────────────────────────────────────────────────────────────
def get_accounts(conn):
    return conn.execute(
        "SELECT id,name,company,currency,current_balance,credit_limit,sort_order,updated_at "
        "FROM cashflow_accounts WHERE is_active=1 ORDER BY sort_order,id"
    ).fetchall()


def update_account_balance(conn, account_id: int, balance: float):
    from datetime import date
    conn.execute(
        "UPDATE cashflow_accounts SET current_balance=?, updated_at=? WHERE id=?",
        [balance, date.today().isoformat(), account_id]
    )
    conn.execute(
        "INSERT INTO cashflow_snapshots(account_id,snapshot_date,balance) VALUES(?,?,?)",
        [account_id, date.today().isoformat(), balance]
    )
    conn.commit()


def get_items(conn):
    return conn.execute(
        "SELECT id,name,direction,category,amount,currency,frequency,company,start_ym,end_ym,notes "
        "FROM cashflow_items WHERE is_active=1 ORDER BY direction DESC, category, name"
    ).fetchall()


def add_item(conn, name, direction, category, amount, currency,
             frequency, company, start_ym, end_ym, notes):
    conn.execute(
        "INSERT INTO cashflow_items(name,direction,category,amount,currency,frequency,"
        "company,start_ym,end_ym,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [name, direction, category, amount, currency, frequency,
         company, start_ym, end_ym or None, notes or None]
    )
    conn.commit()


def delete_item(conn, item_id: int):
    conn.execute("UPDATE cashflow_items SET is_active=0 WHERE id=?", [item_id])
    conn.commit()


def add_account(conn, name, company, currency, balance, credit_limit):
    conn.execute(
        "INSERT INTO cashflow_accounts(name,company,currency,current_balance,credit_limit) VALUES(?,?,?,?,?)",
        [name, company, currency, balance, credit_limit]
    )
    conn.commit()


# ── Amazon payout forecast from DB ───────────────────────────────────────────
_TRANSFER_TYPES = (
    "Transfer","Debt","Übertrag","Verbindlichkeit",
    "Transfert","Solde négatif","Transferir","Saldo descubierto",
    "Overboeking","Schuld","Saldo negativo"
)


def get_amazon_monthly_net(conn, year: int, usd_nis: float) -> dict[int, float]:
    """Return {month_index: net_payout_usd} for a given year."""
    tt = "','".join(_TRANSFER_TYPES)
    rows = conn.execute(
        f"SELECT month, currency, SUM(net_total) FROM amazon_transactions "
        f"WHERE year=? AND tx_type NOT IN ('{tt}') GROUP BY month, currency",
        [year]
    ).fetchall()
    result: dict[int, float] = {}
    for month_name, currency, val in rows:
        if month_name not in MONTHS:
            continue
        idx = MONTHS.index(month_name) + 1
        usd = _to_usd(float(val or 0), currency or "USD", usd_nis)
        result[idx] = result.get(idx, 0.0) + usd
    return result


def _to_usd(amount: float, currency: str, usd_nis: float) -> float:
    if currency in ("USD", ""):
        return amount
    if currency == "ILS":
        return amount / usd_nis if usd_nis else 0.0
    _ROUGH = {"EUR": 1.08, "GBP": 1.27, "CAD": 0.74, "AUD": 0.65,
              "SEK": 0.095, "PLN": 0.25, "MXN": 0.058, "BRL": 0.20}
    return amount * _ROUGH.get(currency, 1.0)


# ── Forecast engine ───────────────────────────────────────────────────────────
def build_forecast(conn, months_ahead: int, usd_nis: float,
                   amazon_growth: float) -> tuple:
    """
    Returns:
        accounts : list of account rows
        forecast : list of dicts, one per month:
                   { ym, year, month, balances: {account_id: float},
                     total_in, total_out, flows: [...] }

    Items are now linked to a *company* (LLC or IL), not a specific account.
    The forecast distributes each item to the primary account for that
    company+currency combination.
    """
    from datetime import date
    today = date.today()

    accounts = get_accounts(conn)   # list of tuples
    items    = get_items(conn)

    # Amazon payout history (previous year, fallback two years)
    prev_year_net = get_amazon_monthly_net(conn, today.year - 1, usd_nis)
    two_years_net = get_amazon_monthly_net(conn, today.year - 2, usd_nis)

    # Amazon account id (first account named "Amazon")
    amazon_acc_id = next(
        (a[0] for a in accounts if a[1].lower() == "amazon"), None
    )

    # Primary account per (company, currency) — used to route item flows
    # Priority: first account in sort_order that matches
    _primary: dict[tuple, int] = {}
    for a in accounts:
        key = (a[2], a[3])   # (company, currency)
        if key not in _primary:
            _primary[key] = a[0]

    # Build month list
    fy, fm = today.year, today.month
    forecast_months = []
    for _ in range(months_ahead):
        forecast_months.append((fy, fm))
        fm += 1
        if fm > 12:
            fm = 1
            fy += 1

    # Running balances per account (start from current balance)
    running = {a[0]: a[4] for a in accounts}

    result = []
    for (fy, fm) in forecast_months:
        ym = f"{fy}-{fm:02d}"
        monthly_flows = []

        # ── Amazon payout ──────────────────────────────────────────────────
        base = prev_year_net.get(fm) or two_years_net.get(fm, 0.0)
        amz_payout = max(base * (1 + amazon_growth), 0.0)
        if amazon_acc_id and amz_payout:
            running[amazon_acc_id] = running.get(amazon_acc_id, 0.0) + amz_payout
            monthly_flows.append({
                "name": "Amazon Payout (auto)", "direction": "in",
                "amount": amz_payout, "currency": "USD",
                "company": "LLC", "auto": True,
            })

        # ── Scheduled items ────────────────────────────────────────────────
        for item in items:
            iid, name, direction, category, amount, currency, frequency, \
                company, start_ym, end_ym, notes = item

            if category == "Amazon Payout":
                continue
            if start_ym and ym < start_ym:
                continue
            if end_ym and ym > end_ym:
                continue

            # Does this item fire this month?
            applies = False
            if frequency == "monthly":
                applies = True
            elif frequency == "quarterly":
                if start_ym:
                    sy, sm = int(start_ym[:4]), int(start_ym[5:7])
                    months_elapsed = (fy - sy) * 12 + (fm - sm)
                    applies = months_elapsed >= 0 and months_elapsed % 3 == 0
            elif frequency == "annual":
                if start_ym:
                    applies = int(start_ym[5:7]) == fm
            elif frequency == "once":
                applies = ym == start_ym

            if not applies:
                continue

            sign = 1 if direction == "in" else -1

            # Route to primary account for this company+currency
            acc_id = _primary.get((company, currency))
            if acc_id is None:
                # Fallback: any account for this company
                acc_id = next((a[0] for a in accounts if a[2] == company), None)

            if acc_id is not None:
                running[acc_id] = running.get(acc_id, 0.0) + sign * amount

            monthly_flows.append({
                "name": name, "direction": direction, "category": category,
                "amount": amount, "currency": currency,
                "company": company, "auto": False,
            })

        result.append({
            "ym": ym, "year": fy, "month": fm,
            "balances": dict(running),
            "flows": monthly_flows,
            "total_in":  sum(f["amount"] for f in monthly_flows if f["direction"] == "in"),
            "total_out": sum(f["amount"] for f in monthly_flows if f["direction"] == "out"),
        })

    return accounts, result
