# Amazon Module — Handoff Package
## For integration into a Streamlit + SQLite app

---

## 1. מה המודול הזה עושה

טאב Amazon בתזרים המזומנים מאפשר:

1. **העלאת קבצי CSV/TSV** של "Transaction View" מ-Amazon Seller Central (כל מרקטפלייס בנפרד)
2. **פרסור אוטומטי** של הפורמט האמריקאי (25 עמודות) והאירופאי (29 עמודות, בשפות: FR/DE/ES/IT/NL/PL/SE)
3. **שמירה ב-SQLite** עם deduplication לפי hash
4. **המרת מטבע** לדולר (שערים קבועים + override חודשי)
5. **דוח חודשי** — מכירות, החזרות, עמלות, Estimated Payment לפי מרקטפלייס
6. **סנכרון אוטומטי** לשורות בתזרים (מכירות בפועל, הכנסה בפועל)

---

## 2. DB Schema — טבלאות הדרושות

```sql
-- עסקאות אמזון (source of truth)
CREATE TABLE IF NOT EXISTS amazon_transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_hash          TEXT NOT NULL UNIQUE,      -- MD5 dedup key
    tx_date          TEXT NOT NULL,             -- YYYY-MM-DD
    tx_status        TEXT,
    tx_type          TEXT,                      -- Order / Refund / Transfer / Debt / Service Fee / etc.
    order_id         TEXT,
    product_details  TEXT,
    gross_sales      REAL DEFAULT 0,            -- EU: כולל VAT; US: ללא מס
    promo_rebates    REAL DEFAULT 0,
    amazon_fees      REAL DEFAULT 0,            -- selling_fees + fba_fees
    other            REAL DEFAULT 0,
    withheld_tax     REAL DEFAULT 0,            -- marketplace withheld tax
    net_total        REAL DEFAULT 0,            -- ה-"Total" בדוח
    month            TEXT,                      -- Jan/Feb/.../Dec
    year             INTEGER,
    marketplace      TEXT DEFAULT 'US',         -- US/CA/UK/DE/FR/ES/IT/NL/PL/SE/BE/IE
    currency         TEXT DEFAULT 'USD'         -- USD/CAD/GBP/EUR/PLN/SEK
);

-- שערי חליפין בסיסיים
CREATE TABLE IF NOT EXISTS fx_rates (
    currency    TEXT PRIMARY KEY,
    rate_to_usd REAL NOT NULL,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- שערי חליפין override חודשיים (לדיוק גבוה יותר)
CREATE TABLE IF NOT EXISTS monthly_fx_rates (
    currency   TEXT    NOT NULL,
    year       INTEGER NOT NULL,
    month      TEXT    NOT NULL,       -- Jan/Feb/.../Dec
    rate       REAL    NOT NULL,
    updated_at TEXT    DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (currency, year, month)
);
```

---

## 3. Logic עיקרי — parse_amazon_transactions()

הפונקציה `parse_amazon_transactions(filepath)` ב-`database.py` עושה:

### א. זיהוי פורמט
```python
# מזהה EU file לפי שורות הפתיחה של הקובץ:
is_eu_file = any of:
    "tous les montants sont en"   # FR
    "alle beträge in"             # DE  
    "alle bedragen in"            # NL
    "todos los importes"          # ES
    "tutti gli importi"           # IT
    "wszystkie kwoty w"           # PL
    "alla belopp i"               # SE

# מזהה currency מהשורה הזו:
eu_currency = PLN / SEK / EUR (ברירת מחדל EUR)
```

### ב. מציאת header row
- English: שורה שמתחילה ב-`"date/time"`
- EU: שורה עם ≥20 עמודות
- fallback: שורה עם ≥10 שדות

### ג. נרמול עמודות
```python
# נרמול שם עמודה:
col = col.strip().lower().replace(" ","_").replace("(","").replace(")","")

# EU: מיפוי שמות עמודות מהשפה המקורית לאנגלית
# דוגמאות:
"umsätze"           → "product_sales"        # DE
"ventes_de_produits"→ "product_sales"        # FR
"produktumsatzsteuer" → "product_sales_tax"  # DE 29-col  ← חשוב!
"taxes_sur_la_vente_des_produits" → "product_sales_tax"  # FR 29-col  ← חשוב!
```

### ד. חישוב gross_sales (חשוב!)
```python
# EU marketplaces — המחיר ב-Umsätze הוא net-of-VAT, ה-VAT הוא עמודה נפרדת:
if marketplace in {UK, DE, FR, ES, IT, NL, BE, PL, SE, IE}:
    gross_sales = product_sales + product_sales_tax  # מוסיפים VAT חזרה

# US/CA — gross_sales = product_sales (מס לא נכלל בדוח)
```

### ה. Marketplace detection
```python
# ממפה URL/domain → קוד + מטבע:
"amazon.com"  → ("US", "USD")
"amazon.ca"   → ("CA", "CAD")
"amazon.co.uk"→ ("UK", "GBP")
"amazon.de"   → ("DE", "EUR")
"amazon.fr"   → ("FR", "EUR")
"amazon.es"   → ("ES", "EUR")
"amazon.it"   → ("IT", "EUR")
"amazon.nl"   → ("NL", "EUR")
"amazon.pl"   → ("PL", "PLN")
"amazon.se"   → ("SE", "SEK")
"amazon.be"   → ("BE", "EUR")
"amazon.ie"   → ("IE", "EUR")
# + עוד 10 מרקטפלייסים (BR/MX/AU/JP/IN/SG/AE/SA/TR/CN)
```

### ו. TX Type canonicalization
```python
# כל variant → canonical:
"order" / "commande" / "bestellung" / "pedido" / "ordine" → "Order"
"refund" / "remboursement" / "erstattung" / "reembolso"   → "Refund"
"transfer" / "transférer" / "überweisung"                  → "Transfer"
"service fee" / "servicegebühr" / "frais de service"       → "Service Fee"
"debt" / "dette"                                           → "Debt"
```

### ז. Dedup hash
```python
hash = MD5(f"{date_str}|{tx_type}|{order_id}|{net_total}|{marketplace}")
```

---

## 4. Logic סנכרון לתזרים — sync_amazon_to_cashflow()

```python
def sync_amazon_to_cashflow(year, conn, monthly_data_row_names):
    """
    מחשב מכירות והכנסה חודשית ומעדכן בטבלת monthly_data.
    
    gross_sales  = SUM(gross_sales) WHERE tx_type='Order'   → "מכירות אמזון בפועל"
    estimated_payment = SUM(net_total) WHERE tx_type NOT IN (Transfer, Debt, ...)  → "הכנסה בפועל אמזון"
    
    ⚠️ Transfer/Debt = הפקדות בנקאיות — לא הכנסה! אסור לכלול.
    """
    
    # FX rates: monthly override → fallback לשיעור קבוע
    fx = dict(fx_rates)
    monthly_fx = dict(monthly_fx_rates WHERE year=year)
    
    def get_rate(currency, month):
        return monthly_fx.get((currency, month), fx.get(currency, 1.0))
    
    # Aggregate
    rows = SQL:
        SELECT month, currency,
               SUM(CASE WHEN tx_type='Order' THEN gross_sales ELSE 0 END) AS orders_gross,
               SUM(CASE WHEN tx_type NOT IN ('Transfer','Debt','Transferin','Transferout')
                        THEN net_total ELSE 0 END) AS estimated_payment
        FROM amazon_transactions
        WHERE year=?
        GROUP BY month, currency
    
    # Convert to USD and sum
    for each (month, currency, orders_gross, estimated_payment):
        rate = get_rate(currency, month)
        sales_by_month[month]  += orders_gross * rate
        income_by_month[month] += estimated_payment * rate
```

---

## 5. FX Rates — ערכי ברירת מחדל

```python
DEFAULT_FX = {
    "USD": 1.0,
    "CAD": 0.73,
    "GBP": 1.27,
    "EUR": 1.08,
    "AUD": 0.65,
    "JPY": 0.0067,
    "MXN": 0.058,
    "BRL": 0.20,
    "SEK": 0.096,
    "PLN": 0.25,
    "INR": 0.012,
    "CZK": 0.044,
    "DKK": 0.145,
}
```

---

## 6. מה ה-Report מציג

דוח חודשי מחולק לשורות:

| שורה | SQL | הערות |
|------|-----|--------|
| **Orders — Gross Sales** | SUM(gross_sales) WHERE tx_type='Order' | כולל VAT ב-EU |
| **Orders — Promo Rebates** | SUM(promo_rebates) WHERE tx_type='Order' | שלילי |
| **Orders — Net** | SUM(net_total) WHERE tx_type='Order' | |
| **Refunds** | SUM(net_total) WHERE tx_type='Refund' | שלילי |
| **Amazon Fees** | SUM(amazon_fees) | selling + FBA |
| **Service Fees** | SUM(net_total) WHERE tx_type='Service Fee' | |
| **Withheld Tax** | SUM(withheld_tax) | |
| **Estimated Payment** | SUM(net_total) WHERE tx_type NOT IN (Transfer,Debt) | = מה שמופקד בפועל |
| **Transfers** | SUM(net_total) WHERE tx_type='Transfer' | הפקדות בנק |

---

## 7. קוד Python מוכן להעתקה — DB + Parser

### 7.1 יצירת טבלאות
```python
def init_amazon_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS amazon_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT NOT NULL UNIQUE,
            tx_date TEXT NOT NULL,
            tx_status TEXT,
            tx_type TEXT,
            order_id TEXT,
            product_details TEXT,
            gross_sales REAL DEFAULT 0,
            promo_rebates REAL DEFAULT 0,
            amazon_fees REAL DEFAULT 0,
            other REAL DEFAULT 0,
            withheld_tax REAL DEFAULT 0,
            net_total REAL DEFAULT 0,
            month TEXT,
            year INTEGER,
            marketplace TEXT DEFAULT 'US',
            currency TEXT DEFAULT 'USD'
        );
        CREATE TABLE IF NOT EXISTS fx_rates (
            currency TEXT PRIMARY KEY,
            rate_to_usd REAL NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS monthly_fx_rates (
            currency TEXT NOT NULL,
            year INTEGER NOT NULL,
            month TEXT NOT NULL,
            rate REAL NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (currency, year, month)
        );
    """)
    # Seed FX defaults
    defaults = {"USD":1.0,"CAD":0.73,"GBP":1.27,"EUR":1.08,"AUD":0.65,
                "JPY":0.0067,"MXN":0.058,"BRL":0.20,"SEK":0.096,"PLN":0.25}
    for cur, rate in defaults.items():
        conn.execute("INSERT OR IGNORE INTO fx_rates (currency, rate_to_usd) VALUES (?,?)", (cur, rate))
    conn.commit()
```

### 7.2 העלאת קובץ ב-Streamlit
```python
import streamlit as st
import tempfile, os

def render_amazon_upload_tab(conn):
    st.subheader("📤 העלאת Transaction View")
    
    uploaded = st.file_uploader(
        "בחר קובץ CSV/TSV מ-Amazon Seller Central → Payments → Transaction View",
        type=["csv", "tsv"],
        help="תומך ב-US, CA, UK וכל מרקטפלייס EU (DE/FR/ES/IT/NL/PL/SE/BE/IE)"
    )
    
    if uploaded and st.button("📥 ייבא"):
        suffix = ".tsv" if uploaded.name.endswith(".tsv") else ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded.read())
            tmp_path = tmp.name
        
        try:
            transactions = parse_amazon_transactions(tmp_path)
        finally:
            os.unlink(tmp_path)
        
        if not transactions:
            st.error("לא נמצאו עסקאות — בדוק שהדוח הוא Transaction View")
            return
        
        existing = {r[0] for r in conn.execute("SELECT tx_hash FROM amazon_transactions").fetchall()}
        new_rows = [tx for tx in transactions if tx["tx_hash"] not in existing]
        skipped  = len(transactions) - len(new_rows)
        
        if new_rows:
            conn.executemany("""
                INSERT OR IGNORE INTO amazon_transactions
                (tx_hash, tx_date, tx_status, tx_type, order_id, product_details,
                 gross_sales, promo_rebates, amazon_fees, other, withheld_tax, net_total,
                 month, year, marketplace, currency)
                VALUES (:tx_hash,:tx_date,:tx_status,:tx_type,:order_id,:product_details,
                        :gross_sales,:promo_rebates,:amazon_fees,:other,:withheld_tax,:net_total,
                        :month,:year,:marketplace,:currency)
            """, new_rows)
            conn.commit()
            st.success(f"✅ יובאו {len(new_rows):,} עסקאות חדשות (דולגו {skipped:,} כפולות)")
        else:
            st.info(f"כל {skipped:,} העסקאות כבר קיימות במסד הנתונים")
        
        # Auto-sync
        affected_years = {tx["year"] for tx in new_rows}
        for yr in affected_years:
            sync_amazon_to_cashflow(yr, conn)
            st.success(f"🔄 סונכרן לתזרים — שנת {yr}")
```

---

## 8. Gotchas חשובים

### 8.1 DE/FR — 29 עמודות vs 25 עמודות
Amazon עדכן את הפורמט של DE ו-FR ל-29 עמודות. שמות העמודות שונו:

| שדה | 25-col (ישן) | 29-col (חדש) |
|-----|-------------|-------------|
| product_sales_tax (DE) | `erhobene_umsatzsteuer` | `produktumsatzsteuer` |
| product_sales_tax (FR) | `taxe_de_ventes_prélevée` | `taxes_sur_la_vente_des_produits` |
| marketplace_withheld_tax (DE) | `marktplatz-quellensteuer` | `einbehaltene_steuer_auf_marketplace` |
| marketplace_withheld_tax (FR) | `taxe_marketplace_facilitator` | `taxes_retenues_sur_le_site_de_vente` |

**אם לא ממפים את השמות החדשים → gross_sales של DE/FR יהיה ללא VAT → undercount של ~8%**

### 8.2 Transfer/Debt — לא לכלול בהכנסה
```
Transfer = הפקדה בנקאית (Amazon משלם לחשבון)
Debt     = חוב שנגבה

אם תכלול Transfer בסכום ההכנסה → כפל ספירה!
ינואר לדוגמה: Transfer = -$58,163 → יגרום להכנסה שלילית של -$52K
```

### 8.3 Sellerboard vs App — פער בנובמבר
```
Sellerboard משתמש ב-settlement date (SP-API Finance API)
App משתמש ב-transaction date (עמודת date/time בדוח)

הזמנות 1-2 דצמבר מופיעות ב-Sellerboard תחת נובמבר (settlement period)
→ פער של ~$10K בנובמבר הוא נורמלי ולא באג
```

### 8.4 SE — 20% gap
מרקטפלייס שבדיה עדיין לא נחקר — ייתכן שיש בעיה בשער חליפין SEK או נתונים חסרים.

---

## 9. קובץ database.py המלא רלוונטי

הפונקציות שצריך להעתיק מ-`database.py`:
- `_DOMAIN_MP_MAP` (שורות 242-264)
- `_normalize_marketplace()` (שורות 267-279)
- `MONTH_MAP`, `MONTH_NAMES`, `EU_MONTH_MAP` (שורות 281-315)
- `EU_TYPE_MAP` (שורות 317-362)
- `EU_COL_NAME_MAP` (שורות 366-492)
- `parse_amazon_transactions()` (שורות 495-747)

הפונקציות שצריך להעתיק מ-`app.py`:
- `sync_amazon_to_cashflow()` (שורות 744-793)
- לוגיקת ה-report מ-`amazon_report()` (שורות 920-1060+)

---

## 10. DB הקיים בשרת

- **שרת:** 34.141.110.140 (GCP europe-west3-a)
- **DB path:** `/home/yaniv/cashflow-app/cashflow.db`
- **גודל:** 508 עסקאות × 2 שנים (2025 + 2026)
- **מרקטפלייסים עם נתונים:** US, CA, UK, DE, FR, ES, IT, NL, BE, IE, PL, SE

לייצוא ל-SQLite חדש:
```bash
ssh yaniv@34.141.110.140
sqlite3 /home/yaniv/cashflow-app/cashflow.db ".dump amazon_transactions" > amazon_dump.sql
sqlite3 /home/yaniv/cashflow-app/cashflow.db ".dump fx_rates" >> amazon_dump.sql
sqlite3 /home/yaniv/cashflow-app/cashflow.db ".dump monthly_fx_rates" >> amazon_dump.sql
```
