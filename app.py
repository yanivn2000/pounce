"""
app.py — Amazon Ads Placement Analyzer
Streamlit web app for triple gifted advertising team.
"""

import streamlit as st
import streamlit_authenticator as stauth
import tempfile
import os
import uuid
import json
import pandas as pd
import io
from datetime import date, timedelta
from analyzer import analyze_with_products, detect_marketplace_from_xlsx, TARGET_ROAS, LOW_IMPR_THRESHOLD
from claude_client import generate_comments
from excel_builder import build_excel
from products import (
    load_products, save_products, calc_breakeven_roas, calc_landed_cost,
    import_csv, get_cost_map, products_exist, COLUMNS,
    load_products_db, save_products_db, get_cost_map_db, products_exist_db,
    delete_product_db, migrate_csv_to_db, DB_COLUMNS,
)
from db.database import init_db, get_conn, flag_force_logout, check_and_clear_force_logout, list_force_logout_users
from db.performance import save_performance_snapshot, get_performance_alerts, reset_snapshots, get_snapshot_count, get_snapshot_summary
from db.settings import get_alert_thresholds, save_setting
from db.inventory import (
    import_fba_csv, import_awd_csv, import_spm_csv, import_whcn_csv,
    upsert_manual_inventory, save_sku_mapping,
    get_inventory_overview, get_avg_daily_sales, get_inventory_alerts,
    get_latest_inventory, LOCATIONS, FBA_LOCATIONS,
)
from db.importer import import_orders_csv, save_recommendation, save_recommendations_batch, update_recommendation_outcome
from db.fba_fees import (
    import_fee_preview_csv, get_fba_fees_map, get_all_fba_fees_df, clear_all_fba_fees,
    get_fx_rates, get_fx_rates_df, save_fx_rate,
)
from db.queries import (
    get_sales_matrix, get_weekly_summary, get_recommendations_history,
    get_change_log, get_marketplaces, get_order_date_range, count_orders,
    get_units_matrix, get_weekly_units_matrix, get_weekly_units_matrix_yoy,
)
from db.products_catalog import (
    get_suppliers, upsert_supplier, delete_supplier,
    get_items, upsert_item, delete_item,
    get_products_catalog, upsert_product_catalog, delete_product_catalog, delete_all_products_catalog,
    calc_product_cost,
    import_items_csv, import_products_catalog_csv,
)
from db.productions import (
    get_productions, get_production, save_production, delete_production,
    get_production_lines, save_production_lines,
    get_production_summary, get_catalog_skus, get_sku_catalog_info,
)

init_db()
# ── Session isolation ─────────────────────────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

# File-uploader key counters — incrementing resets the widget and breaks
# the import→rerun→re-import infinite loop.
for _k in ("items_import_key", "catalog_import_key", "fba_fees_import_key"):
    if _k not in st.session_state:
        st.session_state[_k] = 0
for _k in ("items_import_result", "catalog_import_result", "fba_fees_import_result", "catalog_delete_msg"):
    if _k not in st.session_state:
        st.session_state[_k] = None

SESSION_DIR = os.path.join(tempfile.gettempdir(), f"amazon_ads_{st.session_state.session_id}")
os.makedirs(SESSION_DIR, exist_ok=True)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Pounce — Amazon Ads Analyzer",
    page_icon="🐾",
    layout="wide",
    initial_sidebar_state="expanded",
)

T = {
    "app_bg":"#ffffff","sidebar_bg":"#f6f8fa","sidebar_border":"#d0d7de",
    "text":"#1f2328","text_secondary":"#57606a","card_bg":"#f6f8fa",
    "card_border":"#d0d7de","metric_val":"#0969da","alert_bg":"#fff8c5",
    "alert_border":"#d4a72c","alert_text":"#7d4e00","score_hi":"#1a7f37",
    "score_mid":"#9a6700","score_lo":"#cf222e","tag_sp_bg":"#ddf4ff",
    "tag_sp_text":"#0550ae","tag_sb_bg":"#fbefff","tag_sb_text":"#8250df",
    "tag_auto_bg":"#dafbe1","tag_auto_text":"#116329","btn_text":"#ffffff",
}

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');
html, body, [class*="css"] {{ font-family: 'IBM Plex Sans', sans-serif; }}
h1, h2, h3 {{ font-family: 'IBM Plex Mono', monospace !important; letter-spacing: -0.03em; }}
/* Tighten Streamlit's default top padding (default is 5rem, min needed to clear fixed header ~2.5rem) */
.block-container {{ padding-top: 2.5rem !important; padding-bottom: 1rem !important; }}
/* Reduce vertical gap between widgets */
div[data-testid="stVerticalBlock"] > div {{ gap: 0.3rem !important; }}
div[data-testid="column"] > div[data-testid="stVerticalBlock"] > div {{ gap: 0.2rem !important; }}
/* Compact dataframe row height */
[data-testid="stDataFrame"] .dvn-scroller [role="gridcell"],
[data-testid="stDataFrame"] .dvn-scroller [role="columnheader"] {{
    padding-top: 2px !important;
    padding-bottom: 2px !important;
    min-height: unset !important;
    line-height: 1.3 !important;
}}
.metric-card {{
    background: {T['card_bg']}; border: 1px solid {T['card_border']};
    border-radius: 6px; padding: 0.45rem 1rem; text-align: center;
}}
.metric-val {{
    font-family: 'IBM Plex Mono', monospace; font-size: 1.15rem;
    font-weight: 600; color: {T['metric_val']}; margin: 0;
}}
.metric-label {{
    font-size: 0.68rem; color: {T['text_secondary']};
    text-transform: uppercase; letter-spacing: 0.08em; margin: 2px 0 0;
}}
.alert-box {{
    background: {T['alert_bg']}; border: 1px solid {T['alert_border']};
    border-radius: 6px; padding: 0.8rem 1rem; margin: 0.4rem 0;
    font-size: 0.85rem; color: {T['alert_text']};
}}
.score-hi  {{ color: {T['score_hi']}  !important; font-weight: 600; }}
.score-mid {{ color: {T['score_mid']} !important; font-weight: 600; }}
.score-lo  {{ color: {T['score_lo']}  !important; font-weight: 600; }}
.tag-sp   {{ background:{T['tag_sp_bg']};   color:{T['tag_sp_text']};   padding:2px 8px; border-radius:4px; font-size:0.75rem; }}
.tag-sb   {{ background:{T['tag_sb_bg']};   color:{T['tag_sb_text']};   padding:2px 8px; border-radius:4px; font-size:0.75rem; }}
.tag-auto {{ background:{T['tag_auto_bg']}; color:{T['tag_auto_text']}; padding:2px 8px; border-radius:4px; font-size:0.75rem; }}
.be-roas  {{ font-family:'IBM Plex Mono',monospace; font-weight:600; color:{T['metric_val']}; }}
.be-warn  {{ font-family:'IBM Plex Mono',monospace; font-weight:600; color:{T['score_lo']}; }}
</style>
""", unsafe_allow_html=True)

# ── Authentication ────────────────────────────────────────────────────────────
_secrets  = st.secrets["auth"]
_creds    = {"usernames": {}}
for _uname, _udata in _secrets["credentials"]["usernames"].items():
    _creds["usernames"][_uname] = {
        "name":            _udata["name"],
        "email":           _udata["email"],
        "password":        _udata["hashed_password"],
    }

authenticator = stauth.Authenticate(
    credentials        = _creds,
    cookie_name        = _secrets["cookie_name"],
    cookie_key         = _secrets["cookie_key"],
    cookie_expiry_days = int(_secrets["cookie_expiry_days"]),
)

# Show custom login page when not yet authenticated
if not st.session_state.get("authentication_status"):
    st.markdown("""
    <style>
    /* Full-page login background */
    .stApp { background: #f0f2f6; }
    .block-container { padding-top: 0 !important; }

    /* Hide default Streamlit form chrome on login page */
    [data-testid="stForm"] {
        background: #ffffff;
        border: 1px solid #d0d7de;
        border-radius: 12px;
        padding: 2rem 2rem 1.5rem !important;
        box-shadow: 0 4px 24px rgba(0,0,0,0.08);
        max-width: 420px;
        margin: 0 auto;
    }
    /* Hide the "Login" subheader — we render our own */
    [data-testid="stForm"] h2 { display: none; }

    /* Input labels */
    [data-testid="stForm"] label { font-size: 0.82rem !important; font-weight: 600 !important; color: #57606a !important; }

    /* Inputs */
    [data-testid="stForm"] input {
        border-radius: 8px !important;
        border: 1px solid #d0d7de !important;
        font-size: 0.95rem !important;
        padding: 0.55rem 0.75rem !important;
    }
    [data-testid="stForm"] input:focus {
        border-color: #0969da !important;
        box-shadow: 0 0 0 3px rgba(9,105,218,0.15) !important;
    }

    /* Login button */
    [data-testid="stFormSubmitButton"] button {
        width: 100% !important;
        background: #0969da !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 0.6rem 1rem !important;
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        margin-top: 0.5rem !important;
        cursor: pointer !important;
        transition: background 0.15s ease !important;
    }
    [data-testid="stFormSubmitButton"] button:hover {
        background: #0860ca !important;
    }

    /* Error message */
    [data-testid="stAlert"] {
        max-width: 420px;
        margin: 0.75rem auto 0;
        border-radius: 8px;
    }

    /* Mobile */
    @media (max-width: 600px) {
        [data-testid="stForm"] {
            border-radius: 0;
            border-left: none;
            border-right: none;
            box-shadow: none;
            padding: 1.5rem 1.25rem !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)

    # Logo + title above the form
    st.markdown("""
    <div style="text-align:center; padding: 3rem 1rem 1.5rem;">
        <div style="font-size:3rem; line-height:1;">🐾</div>
        <div style="font-family:'IBM Plex Mono',monospace; font-size:2rem; font-weight:700;
                    letter-spacing:-0.04em; color:#1f2328; margin-top:0.4rem;">Pounce</div>
        <div style="font-size:0.82rem; color:#57606a; margin-top:0.25rem;
                    letter-spacing:0.06em; text-transform:uppercase;">
            triple gifted · Amazon Ads Intelligence
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Center the form using columns
    _lc, _mc, _rc = st.columns([1, 2, 1])
    with _mc:
        authenticator.login(location="main")

    authentication_status = st.session_state.get("authentication_status")
    if authentication_status is False:
        st.markdown("""
        <div style="max-width:420px;margin:0 auto;">
        </div>""", unsafe_allow_html=True)
        st.error("Incorrect username or password.")
    st.stop()

authentication_status = st.session_state.get("authentication_status")
current_username      = st.session_state.get("username", "")

# Force-logout check — admin can end any user's session from the Admin tab
if current_username and check_and_clear_force_logout(current_username):
    authenticator.logout()
    st.warning("⚠️ Your session was ended by an administrator. Please log in again.")
    st.stop()

# Determine role from secrets
_current_role = st.secrets["auth"]["credentials"]["usernames"].get(
    current_username, {}
).get("role", "team")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🐾 Pounce")
    st.markdown(f"<p style='color:{T['text_secondary']};font-size:0.8rem;margin-top:-10px;'>Hunt down your best placements.</p>", unsafe_allow_html=True)

    _display_name = st.secrets["auth"]["credentials"]["usernames"].get(current_username, {}).get("name", current_username)
    st.markdown(
        f"<p style='font-size:0.8rem;color:{T['text_secondary']};margin:0;'>"
        f"👤 Signed in as <strong>{_display_name}</strong></p>",
        unsafe_allow_html=True,
    )
    authenticator.logout("Sign out", location="sidebar")
    st.divider()

    api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        placeholder="sk-ant-...",
        help="Required to generate AI comments. Leave blank to skip.",
    )

    st.divider()
    products_status = "✅ Loaded" if products_exist_db() else "⚠️ Not set up"
    st.markdown(f"**Product Costs:** {products_status}")
    st.markdown(
        f"<div style='color:{T['text_secondary']};font-size:0.75rem;margin-top:1rem;'>triple gifted · Pounce v1.0</div>",
        unsafe_allow_html=True
    )

# ── Tabs ──────────────────────────────────────────────────────────────────────
if _current_role == "admin":
    tab_ads, tab_sales, tab_inv, tab_profit, tab_production, tab_admin = st.tabs([
        "📣 Ads", "📈 Sales Dashboard", "📦 Inventory", "📦 Products", "🏭 Production", "⚙️ Admin"
    ])
else:
    tab_ads, tab_sales, tab_inv, tab_profit, tab_production = st.tabs([
        "📣 Ads", "📈 Sales Dashboard", "📦 Inventory", "📦 Products", "🏭 Production"
    ])
    tab_admin = None

# Analysis content moved into Ads tab below

# ══════════════════════════════════════════════════════════════════════════════
# TAB — INVENTORY
# ══════════════════════════════════════════════════════════════════════════════
with tab_inv:
    _inv_overview_tab, _inv_upload_tab, _inv_manual_tab = st.tabs([
        "📊 Overview", "📤 Upload Data", "✏️ Manual Entry"
    ])

    # ── OVERVIEW ─────────────────────────────────────────────────────────────
    with _inv_overview_tab:
        st.markdown("# 📦 Inventory Overview")
        _cost_map_raw = get_cost_map_db()
        # get_inventory_overview expects {asin: landed_cost} not the full cost dict
        _cost_map_inv = {asin: v.get("landed_cost", 0) for asin, v in _cost_map_raw.items()}
        _avg_sales    = get_avg_daily_sales(days=30)
        _overview     = get_inventory_overview(_cost_map_inv, _avg_sales)

        if _overview.empty:
            st.info("No inventory data yet. Go to **Upload Data** or **Manual Entry** to add stock.")
        else:
            # ── Alerts ───────────────────────────────────────────────────────
            _alerts = get_inventory_alerts(_overview)
            if _alerts:
                _crit  = [a for a in _alerts if a["level"] == "critical"]
                _urg   = [a for a in _alerts if a["level"] == "urgent"]
                _plan  = [a for a in _alerts if a["level"] == "plan"]
                _other = [a for a in _alerts if a["level"] not in ("critical","urgent","plan")]

                if _crit:
                    with st.expander(f"🔴 CRITICAL — {len(_crit)} stock-out risks", expanded=True):
                        for a in _crit:
                            st.markdown(f"**{a['title']}** (`{a['asin']}`) · {a['market']} · {a['msg']}")
                if _urg:
                    with st.expander(f"🟠 URGENT — {len(_urg)} need production now", expanded=True):
                        for a in _urg:
                            st.markdown(f"**{a['title']}** (`{a['asin']}`) · {a['market']} · {a['msg']}")
                if _plan:
                    with st.expander(f"🟡 PLAN — {len(_plan)} approaching reorder point"):
                        for a in _plan:
                            st.markdown(f"**{a['title']}** (`{a['asin']}`) · {a['market']} · {a['msg']}")
                if _other:
                    with st.expander(f"ℹ️ Other alerts ({len(_other)})"):
                        for a in _other:
                            st.markdown(f"**{a['title']}** (`{a['asin']}`) · {a['msg']}")

            st.divider()

            # ── Summary matrix ────────────────────────────────────────────────
            _loc_labels = {k: v["label"] for k, v in LOCATIONS.items()}
            _display_cols = ["asin", "title"]
            _rename = {"asin": "ASIN", "title": "Title"}

            for loc in LOCATIONS:
                if loc in _overview.columns:
                    col_name = _loc_labels[loc]
                    _overview[col_name] = _overview[loc].fillna(0).astype(int)
                    _display_cols.append(col_name)
                    _rename[col_name] = col_name

            _overview["Total"] = _overview["total_available"].fillna(0).astype(int)
            _overview["Value $"] = _overview["value_usd"].fillna(0).round(0).astype(int)
            _display_cols += ["Total", "Value $"]

            # Days columns — rounded to whole numbers
            _day_col_map = {
                "days_fba_us": "Days US",
                "days_fba_ca": "Days CA",
                "days_fba_uk": "Days UK",
            }
            for raw_col, label in _day_col_map.items():
                if raw_col in _overview.columns:
                    # Int64 (nullable) keeps whole numbers even when NaN present
                    _overview[label] = pd.to_numeric(_overview[raw_col], errors="coerce") \
                                         .round(0).astype("Int64")
                    _display_cols.append(label)

            _show_cols = [c for c in _display_cols if c in _overview.columns]

            # ── Totals row ────────────────────────────────────────────────────
            _skip = {"ASIN", "Title", "asin", "title", "Days US", "Days CA", "Days UK"}
            _num_cols = [c for c in _show_cols
                         if c not in _skip and pd.api.types.is_numeric_dtype(_overview[c])]
            _total_row = {c: "" for c in _show_cols}
            # Mark the TOTAL row — handle both lowercase and display-case column names
            for _id_col in ("asin", "ASIN"):
                if _id_col in _total_row:
                    _total_row[_id_col] = "TOTAL"
            for c in _num_cols:
                _total_row[c] = int(_overview[c].fillna(0).sum())
            _display_df = pd.concat(
                [_overview[_show_cols], pd.DataFrame([_total_row])],
                ignore_index=True
            )

            def _color_days(val):
                try:
                    if pd.isna(val):
                        return ""
                except (TypeError, ValueError):
                    pass
                try:
                    v = float(val)
                except (TypeError, ValueError):
                    return ""
                if v < 45:
                    return "background-color:#cf222e22;color:#cf222e;font-weight:700"
                elif v < 90:
                    return "background-color:#fb8f0022;color:#b45309;font-weight:700"
                elif v < 135:
                    return "background-color:#fff3b0;color:#7d4e00;font-weight:600"
                return "background-color:#1a7f3722;color:#1a7f37"

            def _color_total_row(row):
                """Bold + light grey background on the TOTAL row."""
                if row.get("ASIN") == "TOTAL" or row.get("asin") == "TOTAL":
                    return ["background-color:#f0f0f0;font-weight:700"] * len(row)
                return [""] * len(row)

            _days_subset = [c for c in ["Days US", "Days CA", "Days UK"] if c in _show_cols]
            _styler = _display_df.style.apply(_color_total_row, axis=1)
            if _days_subset:
                _map_fn = getattr(_styler, "map", None) or getattr(_styler, "applymap")
                _styler = _map_fn(_color_days, subset=_days_subset)

            st.dataframe(_styler, use_container_width=True, hide_index=True,
                         column_config={
                             "ASIN":    st.column_config.TextColumn(width=120),
                             "Title":   st.column_config.TextColumn(width=200),
                             "Value $": st.column_config.NumberColumn(format="$%d"),
                             "Days US": st.column_config.NumberColumn(format="%d"),
                             "Days CA": st.column_config.NumberColumn(format="%d"),
                             "Days UK": st.column_config.NumberColumn(format="%d"),
                         })

            st.markdown(
                "<p style='font-size:0.78rem;color:#888;'>"
                "🔴 &lt;45 days (critical) · 🟠 &lt;90 days (start production) · "
                "🟡 &lt;135 days (plan purchase) · 🟢 OK · "
                "Days = FBA live + inbound + AWD/3PL ÷ avg daily sales (last 30 days)</p>",
                unsafe_allow_html=True,
            )

    # ── UPLOAD DATA ───────────────────────────────────────────────────────────
    with _inv_upload_tab:
        st.markdown("# 📤 Upload Inventory Data")
        _snap_date_upload = str(st.date_input("Snapshot date", value=date.today(), key="inv_snap_date"))
        st.divider()

        # FBA uploads
        st.markdown("### 🏭 FBA Reports")
        st.markdown(f"<p style='font-size:0.83rem;color:{T['text_secondary']};'>Download from Seller Central → Reports → Fulfillment → Manage FBA Inventory.</p>", unsafe_allow_html=True)
        _fba_c1, _fba_c2, _fba_c3 = st.columns(3)
        for _col, _loc, _label in [(_fba_c1, "FBA_US", "🇺🇸 FBA United States"),
                                    (_fba_c2, "FBA_CA", "🇨🇦 FBA Canada"),
                                    (_fba_c3, "FBA_UK", "🇬🇧 FBA United Kingdom")]:
            with _col:
                _f = st.file_uploader(_label, type=["csv", "txt"], key=f"fba_{_loc}")
                if _f and st.button(f"Import {_label}", key=f"btn_fba_{_loc}"):
                    _n, _w = import_fba_csv(_f, _loc, _snap_date_upload)
                    st.success(f"✅ {_n} ASINs imported.")
                    for w in _w: st.warning(w)

        st.divider()

        # AWD upload
        st.markdown("### 📦 AWD Report")
        st.markdown(f"<p style='font-size:0.83rem;color:{T['text_secondary']};'>Download from Amazon Warehousing & Distribution console. Auto-splits US vs CN.</p>", unsafe_allow_html=True)
        _awd_f = st.file_uploader("AWD Inventory Report", type=["csv", "txt", "xlsx"], key="awd_upload")
        if _awd_f and st.button("Import AWD", key="btn_awd"):
            _n, _w = import_awd_csv(_awd_f, _snap_date_upload)
            st.success(f"✅ {_n} location-rows imported.")
            for w in _w: st.warning(w)

        st.divider()

        # SPM / 3PL UK upload
        st.markdown("### 🏢 3PL UK — SPM")
        st.markdown(f"<p style='font-size:0.83rem;color:{T['text_secondary']};'>Upload the SPM stock report CSV. SKUs are mapped to ASINs below.</p>", unsafe_allow_html=True)
        _spm_f = st.file_uploader("SPM Stock Report", type=["csv", "txt"], key="spm_upload")
        if _spm_f and st.button("Import SPM", key="btn_spm"):
            _n, _w, _unmapped = import_spm_csv(_spm_f, _snap_date_upload)
            if _n:
                st.success(f"✅ {_n} SKUs imported.")
            for w in _w: st.warning(w)
            if _unmapped:
                st.warning(f"⚠️ {len(_unmapped)} SKUs not mapped to ASINs: {', '.join(_unmapped)}")
                st.info("Map them in the **SKU → ASIN Mapping** section below.")
            st.rerun()

        # SKU → ASIN mapping
        with st.expander("🔗 SKU → ASIN Mapping (for SPM)"):
            _conn_map = get_conn()
            _map_df = pd.read_sql_query("SELECT sku, asin, title FROM sku_asin_map WHERE source != 'FBA_US' OR source IS NULL ORDER BY sku", _conn_map)
            _conn_map.close()
            if not _map_df.empty:
                st.dataframe(_map_df, use_container_width=True, hide_index=True)
            st.markdown("**Add / update mapping:**")
            _mc1, _mc2 = st.columns(2)
            with _mc1:
                _map_sku = st.text_input("SPM SKU", placeholder="GIFFTED_032")
            with _mc2:
                _map_asin = st.text_input("ASIN", placeholder="B0XXXXXXXX")
            if st.button("💾 Save Mapping"):
                if _map_sku and _map_asin:
                    save_sku_mapping(_map_sku.strip(), _map_asin.strip().upper())
                    st.success(f"✅ {_map_sku} → {_map_asin}")
                    st.rerun()

        st.divider()

        # WH CN upload
        st.markdown("### 🇨🇳 China Warehouse (WH_CN)")
        _wh_dl_data = "asin,units,title\nB0XXXXXXXX,100,My Product Name\n"
        st.download_button("⬇️ Download template", data=_wh_dl_data,
                           file_name="wh_cn_template.csv", mime="text/csv")
        _whcn_f = st.file_uploader("WH_CN CSV (asin, units)", type=["csv"], key="whcn_upload")
        if _whcn_f and st.button("Import WH_CN", key="btn_whcn"):
            _n, _w = import_whcn_csv(_whcn_f, _snap_date_upload)
            st.success(f"✅ {_n} rows imported.")
            for w in _w: st.warning(w)

        st.divider()

        # ── FX Rates ──────────────────────────────────────────────────────────
        st.markdown("### 💱 Exchange Rates (USD → local)")
        st.markdown(
            f"<p style='font-size:0.83rem;color:{T['text_secondary']};'>"
            f"Rate = local currency units per 1 USD. Used to convert your USD product costs "
            f"to local currency before the break-even margin calculation.</p>",
            unsafe_allow_html=True,
        )
        _fx_df = get_fx_rates_df()
        if not _fx_df.empty:
            _fx_edited = st.data_editor(
                _fx_df,
                use_container_width=True,
                hide_index=True,
                disabled=["marketplace", "updated_at"],
                column_config={
                    "marketplace": st.column_config.TextColumn("Marketplace"),
                    "rate":        st.column_config.NumberColumn("Rate (local / USD)", format="%.4f", min_value=0.0001),
                    "note":        st.column_config.TextColumn("Note"),
                    "updated_at":  st.column_config.TextColumn("Updated"),
                },
                key="fx_rates_editor",
            )
            if st.button("💾 Save FX Rates", key="btn_save_fx"):
                for _, _fx_row in _fx_edited.iterrows():
                    save_fx_rate(
                        _fx_row["marketplace"],
                        float(_fx_row["rate"] or 1.0),
                        str(_fx_row.get("note") or ""),
                    )
                st.success("✅ FX rates saved.")
                st.rerun()

    # ── MANUAL ENTRY ──────────────────────────────────────────────────────────
    with _inv_manual_tab:
        st.markdown("# ✏️ Manual Inventory Entry")
        st.markdown(f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>For Production (unallocated units) and any ad-hoc corrections.</p>", unsafe_allow_html=True)
        st.divider()

        # Production entry
        st.markdown("### 🏗️ Production (unallocated)")
        _prod_inv = get_latest_inventory()
        _prod_inv = _prod_inv[_prod_inv["location"] == "PRODUCTION"] if not _prod_inv.empty else pd.DataFrame()

        if not _prod_inv.empty:
            st.dataframe(_prod_inv[["asin", "title", "units_available", "snapshot_date"]].rename(
                columns={"units_available": "Units", "snapshot_date": "Last updated"}
            ), use_container_width=True, hide_index=True)

        with st.form("production_form"):
            _pr1, _pr2, _pr3 = st.columns([2, 1, 1])
            with _pr1:
                _prod_asin = st.text_input("ASIN", placeholder="B0XXXXXXXX")
            with _pr2:
                _prod_units = st.number_input("Units in production", min_value=0, step=1)
            with _pr3:
                _prod_date = st.date_input("As of date", value=date.today(), key="prod_date")
            if st.form_submit_button("💾 Save", type="primary"):
                if _prod_asin.strip():
                    upsert_manual_inventory(_prod_asin.strip().upper(), "PRODUCTION",
                                            int(_prod_units), str(_prod_date))
                    st.success("✅ Saved.")
                    st.rerun()
                else:
                    st.warning("Enter an ASIN.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB — PROFIT
# ══════════════════════════════════════════════════════════════════════════════
with tab_profit:
    _suppliers_tab, _items_tab, _catalog_tab, _fba_tab = st.tabs([
        "🏭 Suppliers", "🧩 Items", "📋 Products Catalog", "💰 FBA Fees"
    ])

# ══════════════════════════════════════════════════════════════════════════════
# TAB — SUPPLIERS
# ══════════════════════════════════════════════════════════════════════════════
with _suppliers_tab:
    st.markdown("# 🏭 Suppliers")
    st.markdown(
        f"<p style='color:{T['text_secondary']};'>Manage your supplier and manufacturer contacts.</p>",
        unsafe_allow_html=True
    )
    st.divider()

    _sup_list = get_suppliers()
    if _sup_list:
        _sup_df = pd.DataFrame(_sup_list)
        _sup_df["Type"] = _sup_df["is_manufacturer"].apply(
            lambda x: "Direct Manufacturer" if x else "Agent / Intermediary"
        )
        st.dataframe(
            _sup_df[["name", "category", "Type", "contact_person", "email", "tel", "address", "notes"]].rename(columns={
                "name": "Name", "category": "Category",
                "contact_person": "Contact", "email": "Email",
                "tel": "Tel", "address": "Address", "notes": "Notes"
            }),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("No suppliers yet. Add one below.")

    st.divider()

    with st.expander("➕ Add / Edit Supplier", expanded=not _sup_list):
        _sup_edit_id = None
        if _sup_list:
            _sup_names = ["— New supplier —"] + [s["name"] for s in _sup_list]
            _sup_sel = st.selectbox("Edit existing supplier", _sup_names, key="sup_edit_sel")
            if _sup_sel != "— New supplier —":
                _sup_rec = next(s for s in _sup_list if s["name"] == _sup_sel)
                _sup_edit_id = _sup_rec["id"]
            else:
                _sup_rec = {}
        else:
            _sup_sel = "__new__"
            _sup_rec = {}

        # Use the selection as a key suffix so all widgets reset when the
        # dropdown changes (prevents stale values showing for "— New supplier —")
        _sk = _sup_sel if _sup_list else "__new__"

        with st.form(f"supplier_form_{_sk}"):
            _sup_name = st.text_input("Name *", value=_sup_rec.get("name", ""), key=f"sup_name_{_sk}")
            _sup_cat = st.selectbox(
                "Category",
                ["mugs", "socks", "silicon", "other"],
                index=["mugs", "socks", "silicon", "other"].index(_sup_rec["category"])
                if _sup_rec.get("category") in ["mugs", "socks", "silicon", "other"] else 0,
                key=f"sup_cat_{_sk}",
            )
            _sup_type = st.radio(
                "Supplier type",
                ["Direct Manufacturer", "Agent / Intermediary"],
                index=0 if _sup_rec.get("is_manufacturer", 1) else 1,
                horizontal=True,
                key=f"sup_type_{_sk}",
            )
            _sup_col1, _sup_col2 = st.columns(2)
            with _sup_col1:
                _sup_contact = st.text_input("Contact person", value=_sup_rec.get("contact_person", "") or "", key=f"sup_contact_{_sk}")
                _sup_email   = st.text_input("Email", value=_sup_rec.get("email", "") or "", key=f"sup_email_{_sk}")
            with _sup_col2:
                _sup_tel     = st.text_input("Tel", value=_sup_rec.get("tel", "") or "", key=f"sup_tel_{_sk}")
                _sup_address = st.text_input("Address", value=_sup_rec.get("address", "") or "", key=f"sup_address_{_sk}")
            _sup_notes = st.text_input("Notes", value=_sup_rec.get("notes", "") or "", key=f"sup_notes_{_sk}")
            if st.form_submit_button("💾 Save Supplier", type="primary"):
                if _sup_name.strip():
                    upsert_supplier(
                        name=_sup_name.strip(),
                        category=_sup_cat,
                        is_manufacturer=1 if _sup_type == "Direct Manufacturer" else 0,
                        notes=_sup_notes.strip() or None,
                        address=_sup_address.strip() or None,
                        contact_person=_sup_contact.strip() or None,
                        email=_sup_email.strip() or None,
                        tel=_sup_tel.strip() or None,
                        supplier_id=_sup_edit_id,
                    )
                    st.success("✅ Supplier saved.")
                    st.rerun()
                else:
                    st.warning("Supplier name is required.")

    if _sup_list:
        st.divider()
        st.markdown("#### 🗑️ Delete Supplier")
        _del_sup_name = st.selectbox(
            "Select supplier to delete", [s["name"] for s in _sup_list], key="del_sup_sel"
        )
        _del_sup_confirm = st.checkbox("Confirm deletion", key="del_sup_confirm")
        if st.button("🗑️ Delete Supplier", disabled=not _del_sup_confirm, key="del_sup_btn"):
            _del_sup_id = next(s["id"] for s in _sup_list if s["name"] == _del_sup_name)
            delete_supplier(_del_sup_id)
            st.success(f"✅ Supplier '{_del_sup_name}' deleted.")
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB — ITEMS
# ══════════════════════════════════════════════════════════════════════════════
with _items_tab:
    st.markdown("# 🧩 Items")
    st.markdown(
        f"<p style='color:{T['text_secondary']};'>Raw items and components used to assemble products.</p>",
        unsafe_allow_html=True
    )
    st.divider()

    _item_list = get_items()
    _sup_list_for_items = get_suppliers()

    with st.expander("📥 Import / Export Items CSV"):
        # ── Supplier reference ─────────────────────────────────────────────────
        if _sup_list_for_items:
            st.markdown("**✅ Valid supplier names** — copy exactly into your CSV:")
            for _s in _sup_list_for_items:
                st.code(_s["name"], language=None)
            # Also offer as a downloadable reference CSV
            _sup_ref_df = pd.DataFrame(
                [{"supplier_name": s["name"], "category": s["category"],
                  "type": "Direct Manufacturer" if s["is_manufacturer"] else "Agent / Intermediary"}
                 for s in _sup_list_for_items]
            )
            _sup_ref_buf = io.StringIO()
            _sup_ref_df.to_csv(_sup_ref_buf, index=False)
            st.download_button(
                "⬇️ Download suppliers reference CSV",
                data=_sup_ref_buf.getvalue(),
                file_name="suppliers_reference.csv",
                mime="text/csv",
            )
            st.divider()

        # ── Download current items ─────────────────────────────────────────────
        _ITEMS_COLS = [
            "part_id", "name", "supplier_name", "manufacturer_cost",
            "service_cost", "net_weight_grams", "hst_code_na", "hst_code_uk",
            "currency", "notes",
        ]
        if _item_list:
            _items_export_df = pd.DataFrame(_item_list).reindex(columns=_ITEMS_COLS).fillna("")
        else:
            _items_export_df = pd.DataFrame(columns=_ITEMS_COLS)
        _items_csv_buf = io.StringIO()
        _items_export_df.to_csv(_items_csv_buf, index=False)
        st.download_button(
            "⬇️ Download existing items as CSV",
            data=_items_csv_buf.getvalue(),
            file_name="items_export.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.markdown(
            "**Upload columns:** `part_id`\\*, `name`\\*, `supplier_name`, "
            "`manufacturer_cost`, `service_cost`, `net_weight_grams`, "
            "`hst_code_na`, `hst_code_uk`, `currency`, `notes`"
        )
        # ── Upload ────────────────────────────────────────────────────────────
        # Key is incremented after import so the widget resets and doesn't
        # re-trigger the import on the next rerun (avoids infinite loop).
        _items_csv = st.file_uploader(
            "Upload Items CSV", type=["csv"],
            key=f"items_import_csv_{st.session_state['items_import_key']}",
        )
        if _items_csv:
            _imp_n, _imp_warns = import_items_csv(_items_csv)
            st.session_state["items_import_result"] = (_imp_n, _imp_warns)
            st.session_state["items_import_key"] += 1  # resets the uploader
            st.rerun()
        if st.session_state["items_import_result"] is not None:
            _imp_n, _imp_warns = st.session_state["items_import_result"]
            st.session_state["items_import_result"] = None
            if _imp_warns:
                for w in _imp_warns:
                    st.warning(w)
            st.success(f"✅ {_imp_n} items imported.")

    if _item_list:
        _item_df = pd.DataFrame(_item_list)
        st.dataframe(
            _item_df[[
                "part_id", "name", "supplier_name", "manufacturer_cost",
                "service_cost", "total_cost", "net_weight_grams", "hst_code_na", "hst_code_uk",
                "currency"
            ]].rename(columns={
                "part_id": "Part ID", "name": "Name",
                "supplier_name": "Supplier",
                "manufacturer_cost": "Mfg Cost ($)", "service_cost": "Service Cost ($)",
                "total_cost": "Total Cost ($)", "net_weight_grams": "Net Weight (g)",
                "hst_code_na": "HS Code (NA)", "hst_code_uk": "HS Code (UK)",
                "currency": "Currency",
            }),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("No items yet. Add one below.")

    st.divider()

    with st.expander("➕ Add / Edit Item", expanded=not _item_list):
        _item_edit_id = None
        _item_rec = {}
        if _item_list:
            _item_labels = ["— New item —"] + [
                f"{i['part_id']} — {i['name']}" if i.get("part_id") else i["name"]
                for i in _item_list
            ]
            _item_sel = st.selectbox("Edit existing item", _item_labels, key="item_edit_sel")
            if _item_sel != "— New item —":
                _item_rec = _item_list[_item_labels.index(_item_sel) - 1]
                _item_edit_id = _item_rec["id"]

        _item_currencies = ["USD", "GBP", "EUR", "CAD", "AUD"]
        _sup_options = [None] + [s["name"] for s in _sup_list_for_items]
        _sup_ids_by_name = {s["name"]: s["id"] for s in _sup_list_for_items}

        with st.form("item_form"):
            _item_part_id = st.text_input("Part ID *", value=_item_rec.get("part_id", "") or "")
            _item_name = st.text_input("Name *", value=_item_rec.get("name", ""))
            _item_sup_name = st.selectbox(
                "Supplier",
                _sup_options,
                index=_sup_options.index(_item_rec.get("supplier_name"))
                if _item_rec.get("supplier_name") in _sup_options else 0,
            )
            _c1, _c2 = st.columns(2)
            with _c1:
                _item_mfg_cost = st.number_input(
                    "Manufacturer cost ($)", min_value=0.0, step=0.01, format="%.2f",
                    value=float(_item_rec.get("manufacturer_cost", 0) or 0),
                )
            with _c2:
                _item_svc_cost = st.number_input(
                    "Service cost ($)", min_value=0.0, step=0.01, format="%.2f",
                    value=float(_item_rec.get("service_cost", 0) or 0),
                )
            st.caption("Service cost = agent fee. It is NOT included in customs calculations.")
            _item_net_weight = st.number_input(
                "Net weight (g)", min_value=0.0, step=1.0, format="%.1f",
                value=float(_item_rec.get("net_weight_grams", 0) or 0),
            )
            _c3, _c4 = st.columns(2)
            with _c3:
                _item_hst_na = st.text_input("HS Code (NA — US & CA)", value=_item_rec.get("hst_code_na", "") or "")
            with _c4:
                _item_hst_uk = st.text_input("HS Code (UK)", value=_item_rec.get("hst_code_uk", "") or "")
            _item_currency = st.selectbox(
                "Currency",
                _item_currencies,
                index=_item_currencies.index(_item_rec["currency"])
                if _item_rec.get("currency") in _item_currencies else 0,
            )
            _item_notes = st.text_input("Notes", value=_item_rec.get("notes", "") or "")

            if st.form_submit_button("💾 Save Item", type="primary"):
                if _item_name.strip():
                    upsert_item(
                        data={
                            "part_id": _item_part_id.strip() or None,
                            "name": _item_name.strip(),
                            "item_type": "other",
                            "supplier_id": _sup_ids_by_name.get(_item_sup_name) if _item_sup_name else None,
                            "manufacturer_cost": _item_mfg_cost,
                            "service_cost": _item_svc_cost,
                            "net_weight_grams": _item_net_weight if _item_net_weight else None,
                            "hst_code_na": _item_hst_na.strip() or None,
                            "hst_code_uk": _item_hst_uk.strip() or None,
                            "currency": _item_currency,
                            "notes": _item_notes.strip() or None,
                        },
                        item_id=_item_edit_id,
                    )
                    st.success("✅ Item saved.")
                    st.rerun()
                else:
                    st.warning("Item name is required.")

    if _item_list:
        st.divider()
        st.markdown("#### 🗑️ Delete Item")
        _del_item_name = st.selectbox(
            "Select item to delete", [i["name"] for i in _item_list], key="del_item_sel"
        )
        _del_item_confirm = st.checkbox("Confirm deletion", key="del_item_confirm")
        if st.button("🗑️ Delete Item", disabled=not _del_item_confirm, key="del_item_btn"):
            _del_item_id = next(i["id"] for i in _item_list if i["name"] == _del_item_name)
            delete_item(_del_item_id)
            st.success(f"✅ Item '{_del_item_name}' deleted.")
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB — PRODUCTS CATALOG
# ══════════════════════════════════════════════════════════════════════════════
with _catalog_tab:
    st.markdown("# 📋 Products Catalog")
    st.markdown(
        f"<p style='color:{T['text_secondary']};'>Assembled products with components, dimensions, "
        f"and full landed-cost breakdown.</p>",
        unsafe_allow_html=True
    )
    st.divider()

    _cat_list = get_products_catalog()
    _items_for_cat = get_items()
    _item_ids_by_name = {i["name"]: i["id"] for i in _items_for_cat}
    _prod_types = ["single_mug", "set_two_mugs", "mug_with_socks", "silicon_coaster", "other"]

    with st.expander("📥 Import / Export Products CSV"):
        # ── Download current data — one row per product ───────────────────────
        _PROD_COLS = [
            "asin", "sku", "upc", "name", "product_type",
            "width_cm", "length_cm", "height_cm",
            "is_new_product",
            "carton_units", "carton_length_cm", "carton_width_cm", "carton_height_cm",
            "carton_nw_kg", "carton_gw_kg", "carton_cbm",
            "notes", "part_id_1", "part_id_2",
        ]
        _prod_export_rows = []
        for _pe in _cat_list:
            _pe_base = {c: _pe.get(c, "") for c in _PROD_COLS}
            _pe_base["is_new_product"] = 1 if _pe.get("is_new_product") else 0
            _prod_export_rows.append(_pe_base)
        _prod_export_df = pd.DataFrame(_prod_export_rows if _prod_export_rows else [], columns=_PROD_COLS).fillna("")
        _prod_csv_buf = io.StringIO()
        _prod_export_df.to_csv(_prod_csv_buf, index=False)
        st.download_button(
            "⬇️ Download existing products as CSV",
            data=_prod_csv_buf.getvalue(),
            file_name="products_export.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.markdown(
            "**Columns:** `asin`, `sku`, `upc`, `name`, `product_type`, "
            "`width_cm`, `length_cm`, `height_cm`, `is_new_product`, "
            "`carton_units`, `carton_length_cm`, `carton_width_cm`, `carton_height_cm`, "
            "`carton_nw_kg`, `carton_gw_kg`, `carton_cbm`, `notes`  \n"
            "**Component columns (optional):** `part_id_1`, `part_id_2`  — reference `items.part_id`, each contributes qty 1."
        )
        # ── Upload ────────────────────────────────────────────────────────────
        _cat_csv = st.file_uploader(
            "Upload Products CSV", type=["csv"],
            key=f"catalog_import_csv_{st.session_state['catalog_import_key']}",
        )
        if _cat_csv:
            _cat_n, _cat_warns = import_products_catalog_csv(_cat_csv)
            st.session_state["catalog_import_result"] = (_cat_n, _cat_warns)
            st.session_state["catalog_import_key"] += 1  # resets the uploader
            st.rerun()
        if st.session_state["catalog_import_result"] is not None:
            _cat_n, _cat_warns = st.session_state["catalog_import_result"]
            st.session_state["catalog_import_result"] = None
            if _cat_warns:
                for w in _cat_warns:
                    st.warning(w)
            st.success(f"✅ {_cat_n} products saved.")

    if _cat_list:
        # Build display table with all product fields + computed landed cost
        _cat_rows = []
        for _cp in _cat_list:
            _breakdown = calc_product_cost(_cp["id"])
            _cat_rows.append({
                "ASIN":          _cp.get("asin") or "",
                "SKU":           _cp.get("sku")  or "",
                "UPC":           _cp.get("upc")  or "",
                "Name":          _cp["name"],
                "Type":          _cp.get("product_type") or "",
                "Part ID 1":     _cp.get("part_id_1") or "",
                "Part ID 2":     _cp.get("part_id_2") or "",
                "W (cm)":        _cp.get("width_cm")  or "",
                "L (cm)":        _cp.get("length_cm") or "",
                "H (cm)":        _cp.get("height_cm") or "",
                "Carton Units":  _cp.get("carton_units") or "",
                "Carton L":      _cp.get("carton_length_cm") or "",
                "Carton W":      _cp.get("carton_width_cm")  or "",
                "Carton H":      _cp.get("carton_height_cm") or "",
                "NW (kg)":       _cp.get("carton_nw_kg") or "",
                "GW (kg)":       _cp.get("carton_gw_kg") or "",
                "CBM":           _cp.get("carton_cbm")   or "",
                "New?":          "✓" if _cp.get("is_new_product") else "",
                "Weight (g)":    f"{_breakdown.get('total_weight_gr', 0):.0f}" if _breakdown else "",
                "Landed Cost":   f"${_breakdown.get('landed_cost', 0):.2f}" if _breakdown else "—",
                "Notes":         _cp.get("notes") or "",
            })
        st.dataframe(pd.DataFrame(_cat_rows), use_container_width=True, hide_index=True)

        # Cost breakdown for selected product
        st.markdown("#### 📊 Cost Breakdown")
        _breakdown_sel = st.selectbox(
            "Select product to view breakdown",
            [p["name"] for p in _cat_list],
            key="cat_breakdown_sel",
        )
        _bd_product = next(p for p in _cat_list if p["name"] == _breakdown_sel)
        _bd = calc_product_cost(_bd_product["id"])
        if _bd:
            if _bd["items"]:
                _bd_df = pd.DataFrame([{
                    "Part ID": r.get("part_id") or "",
                    "Item": r["name"],
                    "Mfg Cost ($)": f"${r['mfg_cost']:.2f}",
                    "Service Cost ($)": f"${r['service_cost']:.2f}",
                    "Subtotal ($)": f"${r['subtotal']:.2f}",
                    "Weight (g)": f"{r['weight_gr']:.0f}",
                } for r in _bd["items"]])
                st.dataframe(_bd_df, use_container_width=True, hide_index=True)
            else:
                st.info("No components assigned to this product.")
            st.markdown(
                f"**Net Weight:** {_bd.get('total_weight_gr', 0):.0f} g  \n"
                f"**+ Shipping:** ${_bd['shipping_cost']:.2f}  \n"
                f"───────────────────────  \n"
                f"**Landed Cost: ${_bd['landed_cost']:.2f}**"
            )
    else:
        st.info("No products in catalog yet. Add one below.")

    st.divider()

    with st.expander("➕ Add / Edit Product", expanded=not _cat_list):
        _cat_edit_id = None
        _cat_rec = {}
        if _cat_list:
            _cat_names = ["— New product —"] + [p["name"] for p in _cat_list]
            _cat_sel = st.selectbox("Edit existing product", _cat_names, key="cat_edit_sel")
            if _cat_sel != "— New product —":
                _cat_rec = next(p for p in _cat_list if p["name"] == _cat_sel)
                _cat_edit_id = _cat_rec["id"]

        with st.form("catalog_form"):
            _cfa, _cfb, _cfc = st.columns(3)
            with _cfa:
                _cat_asin = st.text_input("ASIN", value=_cat_rec.get("asin", "") or "")
                _cat_sku  = st.text_input("SKU",  value=_cat_rec.get("sku",  "") or "")
                _cat_upc  = st.text_input("UPC",  value=_cat_rec.get("upc",  "") or "")
            with _cfb:
                _cat_ptype = st.selectbox(
                    "Product type",
                    _prod_types,
                    index=_prod_types.index(_cat_rec["product_type"])
                    if _cat_rec.get("product_type") in _prod_types else 0,
                )
            with _cfc:
                _cat_name = st.text_input("Product name *", value=_cat_rec.get("name", ""))

            st.markdown("**Dimensions** *(Weight is computed from assembled items)*")
            _d1, _d2, _d3 = st.columns(3)
            with _d1:
                _cat_w = st.number_input("Width (cm)", min_value=0.0, step=0.1, format="%.1f",
                                          value=float(_cat_rec.get("width_cm") or 0))
            with _d2:
                _cat_l = st.number_input("Length (cm)", min_value=0.0, step=0.1, format="%.1f",
                                          value=float(_cat_rec.get("length_cm") or 0))
            with _d3:
                _cat_h = st.number_input("Height (cm)", min_value=0.0, step=0.1, format="%.1f",
                                          value=float(_cat_rec.get("height_cm") or 0))

            st.markdown("**Master Carton**")
            _mc1, _mc2, _mc3, _mc4 = st.columns(4)
            with _mc1:
                _cat_carton_units = st.number_input(
                    "Units / carton", min_value=0, step=1,
                    value=int(_cat_rec.get("carton_units") or 0),
                )
            with _mc2:
                _cat_carton_l = st.number_input(
                    "Length (cm)", min_value=0.0, step=0.1, format="%.1f",
                    value=float(_cat_rec.get("carton_length_cm") or 0),
                    key="carton_l",
                )
            with _mc3:
                _cat_carton_w = st.number_input(
                    "Width (cm)", min_value=0.0, step=0.1, format="%.1f",
                    value=float(_cat_rec.get("carton_width_cm") or 0),
                    key="carton_w",
                )
            with _mc4:
                _cat_carton_h = st.number_input(
                    "Height (cm)", min_value=0.0, step=0.1, format="%.1f",
                    value=float(_cat_rec.get("carton_height_cm") or 0),
                    key="carton_h",
                )
            _mc5, _mc6, _mc7 = st.columns(3)
            with _mc5:
                _cat_carton_nw = st.number_input(
                    "NW (kg)", min_value=0.0, step=0.01, format="%.3f",
                    value=float(_cat_rec.get("carton_nw_kg") or 0),
                )
            with _mc6:
                _cat_carton_gw = st.number_input(
                    "GW (kg)", min_value=0.0, step=0.01, format="%.3f",
                    value=float(_cat_rec.get("carton_gw_kg") or 0),
                )
            with _mc7:
                _cat_carton_cbm = st.number_input(
                    "CBM (m³)", min_value=0.0, step=0.0001, format="%.4f",
                    value=float(_cat_rec.get("carton_cbm") or 0),
                )

            _cat_is_new = st.checkbox("🚼 New product (launch phase)", value=bool(_cat_rec.get("is_new_product", 0)))
            _cat_notes = st.text_input("Notes", value=_cat_rec.get("notes", "") or "")

            # Component picker — Part ID 1 (required) and Part ID 2 (optional)
            st.markdown("**Components** *(each contributes qty 1)*")
            if _items_for_cat:
                _none_label = "— none —"
                _part_id_options_with_none = [_none_label] + [i["part_id"] for i in _items_for_cat if i.get("part_id")]

                _existing_p1 = _cat_rec.get("part_id_1") or _none_label
                _existing_p2 = _cat_rec.get("part_id_2") or _none_label

                # Key includes product id so Streamlit resets the widget when switching products
                _p1_key = f"cat_part_id_1_{_cat_edit_id}"
                _p2_key = f"cat_part_id_2_{_cat_edit_id}"

                _cp1, _cp2 = st.columns(2)
                with _cp1:
                    _cat_p1 = st.selectbox(
                        "Part ID 1",
                        _part_id_options_with_none,
                        index=_part_id_options_with_none.index(_existing_p1) if _existing_p1 in _part_id_options_with_none else 0,
                        key=_p1_key,
                    )
                with _cp2:
                    _cat_p2 = st.selectbox(
                        "Part ID 2 (optional)",
                        _part_id_options_with_none,
                        index=_part_id_options_with_none.index(_existing_p2) if _existing_p2 in _part_id_options_with_none else 0,
                        key=_p2_key,
                    )
            else:
                st.info("Add items in the 🧩 Items tab first.")
                _cat_p1 = None
                _cat_p2 = None

            if st.form_submit_button("💾 Save Product", type="primary"):
                if _cat_name.strip():
                    upsert_product_catalog(
                        data={
                            "asin": _cat_asin.strip().upper() or None,
                            "sku":  _cat_sku.strip() or None,
                            "upc":  _cat_upc.strip() or None,
                            "name": _cat_name.strip(),
                            "product_type": _cat_ptype,
                            "width_cm": _cat_w if _cat_w else None,
                            "length_cm": _cat_l if _cat_l else None,
                            "height_cm": _cat_h if _cat_h else None,
                            "is_new_product": _cat_is_new,
                            "notes": _cat_notes.strip() or None,
                            "carton_units":     _cat_carton_units if _cat_carton_units else None,
                            "carton_length_cm": _cat_carton_l if _cat_carton_l else None,
                            "carton_width_cm":  _cat_carton_w if _cat_carton_w else None,
                            "carton_height_cm": _cat_carton_h if _cat_carton_h else None,
                            "carton_nw_kg":     _cat_carton_nw if _cat_carton_nw else None,
                            "carton_gw_kg":     _cat_carton_gw if _cat_carton_gw else None,
                            "carton_cbm":       _cat_carton_cbm if _cat_carton_cbm else None,
                            "part_id_1": _cat_p1 if _cat_p1 and _cat_p1 != "— none —" else None,
                            "part_id_2": _cat_p2 if _cat_p2 and _cat_p2 != "— none —" else None,
                        },
                        product_id=_cat_edit_id,
                    )
                    st.success("✅ Product saved.")
                    st.rerun()
                else:
                    st.warning("Product name is required.")

    # Show post-delete confirmation (stored before rerun)
    if st.session_state["catalog_delete_msg"]:
        st.success(st.session_state["catalog_delete_msg"])
        st.session_state["catalog_delete_msg"] = None

    if _cat_list:
        st.divider()
        st.markdown("#### 🗑️ Delete Product")
        _del_cat_name = st.selectbox(
            "Select product to delete", [p["name"] for p in _cat_list], key="del_cat_sel"
        )
        _del_cat_confirm = st.checkbox("Confirm deletion", key="del_cat_confirm")
        if st.button("🗑️ Delete Product", disabled=not _del_cat_confirm, key="del_cat_btn"):
            _del_cat_id = next(p["id"] for p in _cat_list if p["name"] == _del_cat_name)
            delete_product_catalog(_del_cat_id)
            st.session_state["catalog_delete_msg"] = f"✅ Product '{_del_cat_name}' deleted."
            st.rerun()

        st.divider()
        st.markdown("#### 🗑️ Delete All Products")
        _del_all_confirm = st.checkbox("Confirm — delete ALL products", key="del_all_cat_confirm")
        if st.button("🗑️ Delete All Products", disabled=not _del_all_confirm, type="primary", key="del_all_cat_btn"):
            try:
                delete_all_products_catalog()
                st.session_state["catalog_delete_msg"] = "✅ All products deleted."
                st.rerun()
            except Exception as _e:
                st.error(f"Delete failed: {_e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB — FBA FEES
# ══════════════════════════════════════════════════════════════════════════════
with _fba_tab:
    st.markdown("### 💰 FBA Fees")
    st.markdown(
        f"<p style='font-size:0.85rem;color:{T['text_secondary']};'>"
        "Download from Seller Central → Reports → Fulfillment → Fee Preview. "
        "Uploading a new file <strong>replaces all existing fees</strong>. "
        "Marketplace is detected automatically from the <code>amazon-store</code> column.</p>",
        unsafe_allow_html=True,
    )

    # ── Upload (replace-all) ──────────────────────────────────────────────────
    _fee_csv = st.file_uploader(
        "Upload Fee Preview CSV",
        type=["csv", "txt"],
        key=f"fba_fees_csv_{st.session_state['fba_fees_import_key']}",
    )
    if _fee_csv:
        clear_all_fba_fees()
        _nf, _wf = import_fee_preview_csv(_fee_csv)
        st.session_state["fba_fees_import_result"] = (_nf, _wf)
        st.session_state["fba_fees_import_key"] += 1
        st.rerun()
    if st.session_state["fba_fees_import_result"] is not None:
        _nf, _wf = st.session_state["fba_fees_import_result"]
        st.session_state["fba_fees_import_result"] = None
        if _wf:
            for w in _wf:
                st.warning(w)
        st.success(f"✅ {_nf} ASIN/marketplace fee rows imported.")

    # ── Read-only table ───────────────────────────────────────────────────────
    st.divider()
    _fba_df = get_all_fba_fees_df()
    if _fba_df.empty:
        st.info("No FBA fees imported yet. Upload a Fee Preview CSV above.")
    else:
        st.markdown(f"**{len(_fba_df):,} rows** · last updated from Fee Preview report")
        st.dataframe(
            _fba_df.rename(columns={
                "asin":          "ASIN",
                "marketplace":   "Marketplace",
                "pick_pack_fee": "Pick & Pack ($)",
                "referral_fee":  "Referral ($)",
                "currency":      "Currency",
                "updated_at":    "Updated",
            }),
            use_container_width=True,
            hide_index=True,
        )

# ══════════════════════════════════════════════════════════════════════════════
# TAB — PRODUCTION
# ══════════════════════════════════════════════════════════════════════════════
with tab_production:
    st.markdown(
        f"<p style='font-size:1.1rem;font-weight:700;margin:0 0 4px;'>🏭 Production &nbsp;"
        f"<span style='font-size:0.78rem;font-weight:400;color:{T['text_secondary']};'>"
        f"Plan and track production runs by SKU.</span></p>",
        unsafe_allow_html=True,
    )

    _all_skus    = get_catalog_skus()
    _productions = get_productions()
    _prod_names  = [p["name"] for p in _productions]

    _pcol_list, _pcol_form = st.columns([1, 3], gap="large")

    # ── Left: production list (full rerun when changed) ────────────────────────
    with _pcol_list:
        st.markdown("#### Productions")
        _prod_sel_name = st.radio(
            "Select",
            ["➕ New"] + _prod_names,
            key="prod_radio",
            label_visibility="collapsed",
        )

    _sel_prod = None
    if _prod_sel_name != "➕ New":
        _sel_prod = next((p for p in _productions if p["name"] == _prod_sel_name), None)
    else:
        # Clear any lingering state so New always starts with an empty table
        st.session_state.pop("prod_saved_id", None)
        st.session_state.pop("prod_lines_new", None)

    # ── Fragment: right-side form reruns only itself on editor interactions ─────
    @st.fragment
    def _prod_form(sel_prod, all_skus):
        ctx = str(sel_prod["id"]) if sel_prod else "new"

        # ── Header — one compact row ───────────────────────────────────────────
        _pf1, _pf2, _pf3 = st.columns([3, 2, 2])
        with _pf1:
            prod_name = st.text_input(
                "Production name *",
                value=sel_prod["name"] if sel_prod else "",
                placeholder="e.g. YD26099",
                key=f"prod_name_{ctx}",
            )
        with _pf2:
            prod_start = st.date_input(
                "Est. Start",
                value=(
                    date.fromisoformat(sel_prod["est_start_date"])
                    if sel_prod and sel_prod.get("est_start_date")
                    else date.today()
                ),
                key=f"prod_start_{ctx}",
            )
        with _pf3:
            prod_delivery = st.date_input(
                "Est. Delivery",
                value=(
                    date.fromisoformat(sel_prod["est_delivery_date"])
                    if sel_prod and sel_prod.get("est_delivery_date")
                    else date.today()
                ),
                key=f"prod_delivery_{ctx}",
            )

        prod_notes = st.text_input(
            "Notes (optional)",
            value=(sel_prod.get("notes") or "") if sel_prod else "",
            placeholder="Factory, PO number, etc.",
            key=f"prod_notes_{ctx}",
        )

        # ── Single combined table ──────────────────────────────────────────────
        if not all_skus:
            st.info("No SKUs in the Products catalog yet — add products with SKUs in the 📦 Products tab first.")
        else:
            # Pre-load catalog info once (avoids N+1 queries)
            sku_info = get_sku_catalog_info()

            def _make_full_row(sku, num_cartons):
                info = sku_info.get(sku or "", {})
                cu    = info.get("carton_units", 0) or 0
                units = cu * num_cartons
                return {
                    "SKU":               sku or "",
                    "# Cartons":         num_cartons,
                    "Product":           info.get("name", "") if sku else "",
                    "# Units":           units,
                    "Product Cost ($)":  round(info.get("unit_mfg", 0.0) * units, 2),
                    "Service Cost ($)":  round(info.get("unit_svc", 0.0) * units, 2),
                    "Net Weight (kg)":   round(info.get("nw_kg", 0.0) * num_cartons, 2),
                    "Gross Weight (kg)": round(info.get("gw_kg", 0.0) * num_cartons, 2),
                    "CBM":               round(info.get("cbm",  0.0) * num_cartons, 3),
                }

            _editor_key = f"prod_lines_{ctx}"

            def _safe_int(v):
                """int() that returns 0 for None / NaN / bad values."""
                try:
                    return 0 if v is None or (isinstance(v, float) and pd.isna(v)) else int(v)
                except (TypeError, ValueError):
                    return 0

            # full_df = only the DB-saved rows with pre-computed columns.
            # data_editor tracks all in-progress edits / adds / deletes internally
            # via its own session-state key.  We never bake user-added rows back
            # into full_df — that caused double-display and carton resets.
            if sel_prod:
                _db_lines = get_production_lines(sel_prod["id"])
                _db_rows = [{"SKU": ln["sku"], "# Cartons": int(ln["num_cartons"] or 0)}
                            for ln in _db_lines]
            else:
                _db_rows = []

            _SCHEMA = {
                "SKU": pd.Series(dtype=str), "# Cartons": pd.Series(dtype=int),
                "Product": pd.Series(dtype=str), "# Units": pd.Series(dtype=int),
                "Product Cost ($)": pd.Series(dtype=float),
                "Service Cost ($)": pd.Series(dtype=float),
                "Net Weight (kg)": pd.Series(dtype=float),
                "Gross Weight (kg)": pd.Series(dtype=float),
                "CBM": pd.Series(dtype=float),
            }
            _full_rows = [_make_full_row(r["SKU"], r["# Cartons"]) for r in _db_rows]
            full_df = pd.DataFrame(_full_rows) if _full_rows else pd.DataFrame(_SCHEMA)

            _COMPUTED = ["Product", "# Units", "Product Cost ($)", "Service Cost ($)",
                         "Net Weight (kg)", "Gross Weight (kg)", "CBM"]

            st.caption("Click ＋ (bottom-left) to add a row · select a row and press Delete/Backspace to remove it · computed columns update after Save")
            edited_df = st.data_editor(
                full_df,
                use_container_width=True,
                num_rows="dynamic",
                key=_editor_key,
                disabled=_COMPUTED,
                column_config={
                    "SKU":               st.column_config.SelectboxColumn("SKU", options=all_skus, required=True, width=180),
                    "# Cartons":         st.column_config.NumberColumn("# Cartons", min_value=0, step=1, width=110),
                    "Product":           st.column_config.TextColumn("Product", width=200),
                    "# Units":           st.column_config.NumberColumn("# Units", width=85),
                    "Product Cost ($)":  st.column_config.NumberColumn("Product Cost ($)", format="$%.2f", width=130),
                    "Service Cost ($)":  st.column_config.NumberColumn("Service Cost ($)", format="$%.2f", width=125),
                    "Net Weight (kg)":   st.column_config.NumberColumn("Net Weight (kg)", format="%.2f kg", width=120),
                    "Gross Weight (kg)": st.column_config.NumberColumn("Gross Weight (kg)", format="%.2f kg", width=130),
                    "CBM":               st.column_config.NumberColumn("CBM", format="%.3f", width=75),
                },
            )

            # Totals — computed from edited_df using sku_info so the numbers
            # are always correct (edited_df has the live SKU + # Cartons values).
            _tot_cartons = _tot_units = 0
            _tot_prod = _tot_svc = _tot_nw = _tot_gw = _tot_cbm = 0.0
            for _, _r in edited_df.iterrows():
                _sku = str(_r.get("SKU") or "").strip()
                if not _sku:
                    continue
                _nc = _safe_int(_r.get("# Cartons"))
                _info = sku_info.get(_sku, {})
                _cu = _info.get("carton_units", 0) or 0
                _u  = _cu * _nc
                _tot_cartons += _nc
                _tot_units   += _u
                _tot_prod    += _info.get("unit_mfg", 0.0) * _u
                _tot_svc     += _info.get("unit_svc", 0.0) * _u
                _tot_nw      += _info.get("nw_kg", 0.0) * _nc
                _tot_gw      += _info.get("gw_kg", 0.0) * _nc
                _tot_cbm     += _info.get("cbm",   0.0) * _nc

            if _tot_cartons:
                st.markdown(
                    f"**📊 TOTAL** &nbsp;·&nbsp; "
                    f"{_tot_cartons} cartons &nbsp;·&nbsp; "
                    f"{_tot_units} units &nbsp;·&nbsp; "
                    f"${_tot_prod:.2f} prod cost &nbsp;·&nbsp; "
                    f"${_tot_svc:.2f} svc cost &nbsp;·&nbsp; "
                    f"{_tot_nw:.2f} kg NW &nbsp;·&nbsp; "
                    f"{_tot_gw:.2f} kg GW &nbsp;·&nbsp; "
                    f"{_tot_cbm:.3f} CBM",
                    unsafe_allow_html=True,
                )

            # ── Buttons ────────────────────────────────────────────────────────
            _sb1, _sb2, _sb3 = st.columns([2, 1, 7])
            with _sb1:
                if st.button("💾 Save", type="primary", key=f"prod_save_{ctx}"):
                    if not prod_name.strip():
                        st.error("Production name is required.")
                    else:
                        try:
                            prod_id = save_production({
                                "id":                sel_prod["id"] if sel_prod else None,
                                "name":              prod_name.strip(),
                                "est_start_date":    str(prod_start),
                                "est_delivery_date": str(prod_delivery),
                                "notes":             prod_notes.strip() or None,
                            })
                            save_production_lines(
                                prod_id,
                                edited_df[["SKU", "# Cartons"]].dropna(subset=["SKU"]).to_dict("records"),
                            )
                            # Clear editor state so next load re-reads fresh from DB
                            st.session_state.pop(_editor_key, None)
                            st.session_state["prod_saved_id"] = prod_id
                            st.success(f"✅ '{prod_name.strip()}' saved.")
                            st.rerun()
                        except Exception as _pe:
                            st.error(f"Save failed: {_pe}")

            with _sb2:
                if sel_prod and st.button("🗑️ Delete", key=f"prod_del_{ctx}"):
                    st.session_state[f"prod_del_confirm_{ctx}"] = True

            if sel_prod and st.session_state.get(f"prod_del_confirm_{ctx}"):
                st.warning(f"Delete **{sel_prod['name']}** and all its lines?")
                _dc1, _dc2, _ = st.columns([2, 2, 6])
                with _dc1:
                    if st.button("Yes, delete", type="primary", key=f"prod_del_yes_{ctx}"):
                        delete_production(sel_prod["id"])
                        st.session_state.pop(f"prod_del_confirm_{ctx}", None)
                        st.session_state.pop("prod_saved_id", None)
                        st.session_state.pop(_editor_key, None)
                        st.rerun()
                with _dc2:
                    if st.button("Cancel", key=f"prod_del_no_{ctx}"):
                        st.session_state.pop(f"prod_del_confirm_{ctx}", None)
                        st.rerun()

    with _pcol_form:
        _prod_form(_sel_prod, _all_skus)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SALES DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
with tab_sales:
    st.markdown(
        f"<p style='font-size:1.1rem;font-weight:700;margin:0 0 4px;'>📈 Sales Dashboard &nbsp;"
        f"<span style='font-size:0.78rem;font-weight:400;color:{T['text_secondary']};'>"
        f"Units sold by ASIN × date. Upload Amazon order reports to populate.</span></p>",
        unsafe_allow_html=True
    )

    # ── Import orders ─────────────────────────────────────────────────────────
    with st.expander("📤 Import Amazon Orders CSV", expanded=(count_orders() == 0)):
        st.markdown(
            f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>"
            f"Download from Seller Central → Reports → Business Reports → Orders. "
            f"You can import multiple files — duplicates are handled automatically.</p>",
            unsafe_allow_html=True
        )
        st.markdown(
            f"<p style='color:{T['text_secondary']};font-size:0.82rem;'>"
            f"Marketplace is auto-detected from the <code>sales-channel</code> or <code>currency</code> column in your CSV. "
            f"No manual selection needed.</p>",
            unsafe_allow_html=True
        )
        orders_file = st.file_uploader("Upload Orders CSV", type=["csv", "txt"], key="orders_csv")

        if orders_file and st.button("📥 Import Orders", type="primary"):
            override = None  # always use CSV auto-detection
            with st.spinner("Importing..."):
                n, warns = import_orders_csv(orders_file, marketplace_override=override)
            if warns:
                for w in warns[:5]:
                    st.warning(w)
            if n > 0:
                st.success(f"✅ Imported {n} orders.")
                st.rerun()
            else:
                st.error("No rows imported. Check warnings above.")

    # ── Stats bar ─────────────────────────────────────────────────────────────
    total_orders = count_orders()
    if total_orders == 0:
        st.info("No order data yet. Import an Amazon Orders CSV above to get started.")
    else:
        min_date, max_date = get_order_date_range()
        sc1, sc2, sc3 = st.columns(3)
        sc1.markdown(f'<div class="metric-card"><p class="metric-val">{total_orders:,}</p><p class="metric-label">Total Orders</p></div>', unsafe_allow_html=True)
        sc2.markdown(f'<div class="metric-card"><p class="metric-val">{min_date}</p><p class="metric-label">Earliest Date</p></div>', unsafe_allow_html=True)
        sc3.markdown(f'<div class="metric-card"><p class="metric-val">{max_date}</p><p class="metric-label">Latest Date</p></div>', unsafe_allow_html=True)

        st.markdown("<div style='margin: 6px 0 0;'></div>", unsafe_allow_html=True)

        # ── Filters ───────────────────────────────────────────────────────────
        available_markets = get_marketplaces()
        _PRIORITY = ["amazon.com", "amazon.ca", "amazon.co.uk"]
        _SEP      = "── Other ──────────────"
        _main     = [m for m in _PRIORITY if m in available_markets]
        _others   = sorted(m for m in available_markets if m not in _PRIORITY)
        mkt_options = ["all"] + _main + ([_SEP] + _others if _others else [])

        fcol1, fcol2, fcol3, fcol4 = st.columns([2, 2, 2, 1])
        with fcol1:
            sel_market_raw = st.selectbox("Marketplace", mkt_options, key="dash_market")
            # Treat separator as "all"
            if sel_market_raw == _SEP:
                sel_market_raw = "all"
            sel_market = None if sel_market_raw == "all" else sel_market_raw
        with fcol2:
            view_mode = st.radio("View", ["Daily", "Weekly"], horizontal=True, key="dash_view")
        with fcol3:
            period_opts = [7, 14, 30, 60, 90] if view_mode == "Daily" else [4, 8, 12, 26, 52]
            period_labels = [f"{v} days" for v in period_opts] if view_mode == "Daily" else [f"{v} weeks" for v in period_opts]
            period_idx = 2
            days_back_raw = st.selectbox("Period", period_opts, index=period_idx,
                                         format_func=lambda v: f"{v} {'days' if view_mode == 'Daily' else 'weeks'}",
                                         key="dash_days")
        with fcol4:
            if view_mode == "Weekly":
                yoy_mode = st.checkbox("YoY", value=False, key="dash_yoy",
                                       help="Compare to same week last year")
            else:
                yoy_mode = False

        # ── ASIN / product search ─────────────────────────────────────────────
        _asin_search = st.text_input(
            "🔍 Search ASIN or product name",
            value="",
            placeholder="Filter rows by ASIN or title…",
            key="dash_asin_search",
        )

        # ── Load change log early so matrix can use it ────────────────────────
        cl_df = get_change_log(marketplace=sel_market, days=days_back_raw if view_mode == "Daily" else days_back_raw * 7)

        def _to_week_start(date_str: str) -> str:
            """Replicate SQLite: date(d,'weekday 1','-7 days') → Monday of that week."""
            d = pd.Timestamp(date_str)
            days_to_next_monday = 0 if d.weekday() == 0 else (7 - d.weekday())
            return (d + pd.Timedelta(days=days_to_next_monday) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")

        if not cl_df.empty:
            if view_mode == "Daily":
                change_set = {(str(r.asin), str(r.log_date)) for _, r in cl_df.iterrows()}
            else:
                change_set = {(str(r.asin), _to_week_start(str(r.log_date))) for _, r in cl_df.iterrows()}
        else:
            change_set = set()

        # ── Units matrix ──────────────────────────────────────────────────────
        st.divider()
        compare_label = ("vs same week LY" if yoy_mode else
                         "vs prev week" if view_mode == "Weekly" else "vs prev day")
        st.markdown(f"### Units Sold — color coded {compare_label}")

        ly_matrix = None
        if view_mode == "Daily":
            matrix = get_units_matrix(marketplace=sel_market, days=days_back_raw)
            threshold = 30
        elif yoy_mode:
            matrix, ly_matrix = get_weekly_units_matrix_yoy(
                marketplace=sel_market, weeks=days_back_raw
            )
            threshold = 20
        else:
            matrix = get_weekly_units_matrix(marketplace=sel_market, weeks=days_back_raw)
            threshold = 20

        # Apply ASIN / title search filter
        if _asin_search.strip():
            _q = _asin_search.strip().upper()
            _mask = (
                matrix["asin"].str.upper().str.contains(_q, na=False) |
                matrix["title"].str.upper().str.contains(_q, na=False)
            )
            matrix = matrix[_mask].reset_index(drop=True)
            if yoy_mode and ly_matrix is not None and not ly_matrix.empty:
                _ly_mask = (
                    ly_matrix["asin"].str.upper().str.contains(_q, na=False) |
                    ly_matrix["title"].str.upper().str.contains(_q, na=False)
                )
                ly_matrix = ly_matrix[_ly_mask].reset_index(drop=True)

        if matrix.empty:
            st.info("No data for the selected filters.")
        else:
            date_cols = [c for c in matrix.columns if c not in ("asin", "title")]

            # Build pct_change matrix aligned to matrix index
            pct = pd.DataFrame(index=matrix.index, columns=date_cols, dtype=float)
            for i, col in enumerate(date_cols):
                if yoy_mode and ly_matrix is not None and col in ly_matrix.columns:
                    ly_by_asin = ly_matrix.groupby("asin")[col].sum()
                    ly_vals = ly_by_asin.reindex(matrix["asin"].values).values
                    cur_vals = matrix[col].values.astype(float)
                    pct[col] = pd.Series(
                        [(c - l) / l * 100 if l and l > 0 else None
                         for c, l in zip(cur_vals, ly_vals)],
                        index=matrix.index
                    )
                elif i + 1 < len(date_cols):
                    prev = matrix[date_cols[i + 1]].replace(0, None)
                    pct[col] = (matrix[col] - matrix[date_cols[i + 1]]) / prev * 100
                else:
                    pct[col] = None

            pct_indexed = pct  # shares integer index with matrix

            # ── Latest change log per ASIN ────────────────────────────────────
            from db.database import get_conn as _gcl
            _cl_conn = _gcl()
            _lc_rows = _cl_conn.execute("""
                SELECT asin, log_date, change_type, notes
                FROM change_log
                WHERE id IN (SELECT MAX(id) FROM change_log GROUP BY asin)
            """).fetchall()
            _cl_conn.close()
            _last_change_map = {}
            for _r in _lc_rows:
                _note = (str(_r["notes"] or "")).strip()[:35]
                _note_part = f" · {_note}" if _note else ""
                _last_change_map[str(_r["asin"])] = f"{_r['log_date']} · {_r['change_type']}{_note_part}"

            # Freeze asin + title by setting them as the DataFrame index
            display = matrix.set_index(["asin", "title"])

            # Pre-format cell values: numbers with commas, ⚑ appended for changed cells
            display_marked = display.copy().astype(object)

            # Insert "Last Change" as the first data column (before date columns)
            display_marked.insert(0, "Last Change", [
                _last_change_map.get(str(asin), "—")
                for asin, _ in display_marked.index
            ])

            for col in date_cols:
                col_idx = display_marked.columns.get_loc(col)
                for pos, (asin, title) in enumerate(display_marked.index):
                    val = display_marked.iloc[pos, col_idx]
                    try:
                        num_str = f"{int(float(val)):,}" if pd.notna(val) else "0"
                    except (ValueError, TypeError):
                        num_str = "0"
                    flag = " ⚑" if (str(asin), col) in change_set else ""
                    display_marked.iloc[pos, col_idx] = num_str + flag

            # ── Per-ASIN row totals — inserted right after "Last Change" ──────
            asin_row_totals = matrix[date_cols].sum(axis=1).astype(int)
            _lc_pos = display_marked.columns.get_loc("Last Change")
            display_marked.insert(_lc_pos + 1, "Total", [f"{t:,}" for t in asin_row_totals.values])

            col_cfg = {
                "asin":        st.column_config.TextColumn("ASIN",        width=120),
                "title":       st.column_config.TextColumn("Title",       width=200),
                "Last Change": st.column_config.TextColumn("Last Change", width=220),
                "Total":       st.column_config.TextColumn("Total",       width=90),
            }

            # ── Totals row — prepended as row 0 in the same dataframe ─────────
            _col_sums    = {col: int(matrix[col].sum()) for col in date_cols}
            _grand_total = sum(_col_sums.values())
            _totals_row  = pd.DataFrame(
                [{
                    "Last Change": "📊 TOTAL",
                    "Total":       f"{_grand_total:,}",
                    **{col: f"{_col_sums[col]:,}" for col in date_cols},
                }],
                index=pd.MultiIndex.from_tuples(
                    [("📊 TOTAL", "")], names=display_marked.index.names
                ),
            )
            display_with_totals = pd.concat([_totals_row, display_marked])

            def _color_matrix(df):
                styles = pd.DataFrame("", index=df.index, columns=df.columns)
                # Row 0 is always the totals row — bold gray, no pct coloring
                for col_name in df.columns:
                    styles.iloc[0, styles.columns.get_loc(col_name)] = (
                        "background-color:#e8edf2;color:#24292f;font-weight:700;"
                    )
                # Rows 1+ are data rows — color by day-over-day pct change
                for col in date_cols:
                    if col not in df.columns:
                        continue
                    for data_pos, idx in enumerate(matrix.index):
                        df_pos = data_pos + 1  # +1 because row 0 is totals
                        p = pct_indexed.loc[idx, col]
                        try:
                            p = float(p)
                        except (TypeError, ValueError):
                            continue
                        col_loc = styles.columns.get_loc(col)
                        if p >= threshold:
                            styles.iloc[df_pos, col_loc] = "background-color:#1a7f3733;color:#1a7f37;font-weight:600"
                        elif p <= -threshold:
                            styles.iloc[df_pos, col_loc] = "background-color:#cf222e22;color:#cf222e;font-weight:600"
                # Change-set highlighting (skip totals row at pos 0)
                if change_set:
                    for pos, (asin, _title) in enumerate(df.index):
                        if pos == 0:
                            continue
                        for col in date_cols:
                            if col in df.columns and (str(asin), col) in change_set:
                                col_loc = styles.columns.get_loc(col)
                                if styles.iloc[pos, col_loc] == "":
                                    styles.iloc[pos, col_loc] = "background-color:#fff3b0;color:#7d4e00;font-weight:700"
                return styles

            # ── Single dataframe: totals row + all data rows ───────────────────
            styled = display_with_totals.style.apply(_color_matrix, axis=None)
            st.dataframe(styled, use_container_width=True, column_config=col_cfg)

            # ── Change log grouped by ASIN ────────────────────────────────────
            if change_set and not cl_df.empty:
                visible_asins   = set(matrix["asin"].astype(str))
                visible_changes = cl_df[cl_df["asin"].astype(str).isin(visible_asins)].sort_values(["asin", "log_date"])
                if not visible_changes.empty:
                    blocks = ""
                    for asin_val, grp in visible_changes.groupby("asin"):
                        rows_html = "".join(
                            f"<tr>"
                            f"<td style='padding:2px 12px 2px 0;color:#888;font-size:0.81rem;'>{r.log_date}</td>"
                            f"<td style='padding:2px 12px 2px 0;'><span style='background:#fff3b0;padding:1px 7px;"
                            f"border-radius:4px;font-size:0.79rem;font-weight:600;color:#7d4e00;'>"
                            f"{r.change_type.capitalize()}</span></td>"
                            f"<td style='padding:2px 0;color:#444;font-size:0.81rem;'>{r.get('notes') or ''}</td>"
                            f"</tr>"
                            for _, r in grp.iterrows()
                        )
                        blocks += (
                            f"<div style='margin-bottom:8px;'>"
                            f"<span style='font-weight:700;font-size:0.82rem;'>⚑ {asin_val}</span>"
                            f"<table style='border-collapse:collapse;margin-top:3px;'>{rows_html}</table></div>"
                        )
                    st.markdown(
                        f"<div style='margin-top:8px;padding:10px 14px;border-left:3px solid #d4a72c;"
                        f"background:#fffdf0;border-radius:4px;'>{blocks}</div>",
                        unsafe_allow_html=True
                    )

            # ── Legend ────────────────────────────────────────────────────────
            if yoy_mode:
                st.markdown(
                    "<p style='font-size:0.78rem;color:#888;margin-top:6px;'>"
                    "🟢 Green = sold more than same week last year (&gt;+20%) &nbsp;·&nbsp; "
                    "🔴 Red = sold less than same week last year (&gt;−20%) &nbsp;·&nbsp; "
                    "⬜ White = within ±20% of last year &nbsp;·&nbsp; "
                    "Colors compare vs same week shifted by exactly 364 days (52 weeks)"
                    "</p>",
                    unsafe_allow_html=True
                )
            else:
                period = "previous day" if view_mode == "Daily" else "previous week"
                st.markdown(
                    f"<p style='font-size:0.78rem;color:#888;margin-top:6px;'>"
                    f"🟢 Green = &gt;+{threshold}% vs {period} &nbsp;·&nbsp; "
                    f"🔴 Red = &gt;−{threshold}% vs {period}"
                    f"</p>",
                    unsafe_allow_html=True
                )

            if not cl_df.empty:
                st.markdown("<p style='font-size:0.78rem;color:#888;margin-top:4px;'>🟡 Yellow cell = change logged on that date</p>", unsafe_allow_html=True)

        # ── Change log per product ────────────────────────────────────────────
        st.divider()
        st.markdown("### 📝 Change Log")
        st.markdown(
            f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>"
            f"Record manual changes (price, image, title, deal) to track their sales impact.</p>",
            unsafe_allow_html=True
        )

        with st.form("change_log_form"):
            cl1, cl2, cl3 = st.columns([2, 2, 3])
            with cl1:
                cl_asin = st.text_input("ASIN", placeholder="B0XXXXXXXX")
            with cl2:
                cl_type = st.selectbox("Change Type", ["bid", "price", "image", "title", "deal", "listing", "other"])
            with cl3:
                cl_notes = st.text_input("Notes", placeholder="Reduced price from $29.99 to $24.99")
            cl_date = st.date_input("Date", value=date.today())
            cl_mkt  = st.selectbox("Marketplace", ["amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au", "amazon.de"], key="cl_market")

            if st.form_submit_button("➕ Add Entry"):
                if cl_asin.strip():
                    from db.database import get_conn
                    conn2 = get_conn()
                    with conn2:
                        conn2.execute(
                            "INSERT INTO change_log (log_date, asin, marketplace, change_type, notes) VALUES (?,?,?,?,?)",
                            (str(cl_date), cl_asin.strip().upper(), cl_mkt, cl_type, cl_notes)
                        )
                    conn2.close()
                    st.success("✅ Logged.")
                    st.rerun()
                else:
                    st.warning("ASIN is required.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB — ADS
# ══════════════════════════════════════════════════════════════════════════════
with tab_ads:
    _placement_tab, = st.tabs(["📍 Placement"])

    with _placement_tab:
        _recs_view, _alerts_view, _analysis_view = st.tabs(["📋 Recommendations", "🔔 Alerts", "📊 Analysis"])

        with _recs_view:
            # ── RECOMMENDATIONS content ───────────────────────────────────────
            st.markdown("# 📋 Recommendations History")
            st.markdown(
                f"<p style='color:{T['text_secondary']};'>Track placement bid recommendations over time "
                f"and record outcomes after the review window.</p>",
                unsafe_allow_html=True
            )
            st.divider()

            # ── Log a Recommendation (manual or pre-filled from table) ────────────────
            _pf = st.session_state.get("rec_prefill", {})
            _place_opts  = ["Top of Search", "Rest of Search", "Product Pages"]
            _action_opts = ["Increase", "Decrease", "Disable", "Keep", "Brand awareness only"]
            _type_opts   = ["SP", "SB"]
            _mkt_opts    = ["amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au", "amazon.de"]

            def _safe_int(val, default=0):
                """Convert val to int safely, returning default for None/NaN/invalid."""
                try:
                    v = float(val)
                    import math
                    return default if math.isnan(v) else int(v)
                except (TypeError, ValueError):
                    return default

            _expander_label = "📋 Edit & Log (pre-filled from selection)" if _pf else "➕ Log a Recommendation"
            with st.expander(_expander_label, expanded=bool(_pf)):
                if _pf:
                    st.info(f"Pre-filled from rec #{int(_pf.get('id', 0))} — adjust as needed before saving.")
                    if st.button("✖ Clear pre-fill", key="clear_prefill"):
                        st.session_state.pop("rec_prefill", None)
                        st.rerun()

                with st.form("rec_form", clear_on_submit=True):
                    rc1, rc2 = st.columns(2)
                    with rc1:
                        r_date  = st.date_input("Date Given", value=date.today())
                        r_asin  = st.text_input("ASIN (optional)",
                                                value=str(_pf.get("asin") or ""),
                                                placeholder="B0XXXXXXXX")
                        r_camp  = st.text_input("Campaign Name",
                                                value=str(_pf.get("campaign_name") or ""))
                        _place_idx = _place_opts.index(_pf["placement_type"]) \
                            if _pf.get("placement_type") in _place_opts else 0
                        r_place = st.selectbox("Placement", _place_opts, index=_place_idx)
                    with rc2:
                        _type_idx = _type_opts.index(_pf["campaign_type"]) \
                            if _pf.get("campaign_type") in _type_opts else 0
                        r_type    = st.selectbox("Campaign Type", _type_opts, index=_type_idx)
                        r_cur_mul = st.number_input("Current Multiplier %", min_value=0, max_value=900,
                                                    value=_safe_int(_pf.get("current_multiplier")))
                        _action_idx = next(
                            (i for i, a in enumerate(_action_opts)
                             if a.lower() == str(_pf.get("recommended_action") or "").lower()), 0)
                        r_action  = st.selectbox("Recommended Action", _action_opts, index=_action_idx)
                        r_rec_mul = st.number_input("Recommended Multiplier %", min_value=0, max_value=900,
                                                    value=_safe_int(_pf.get("recommended_multiplier")))

                    _mkt_idx = _mkt_opts.index(_pf["marketplace"]) \
                        if _pf.get("marketplace") in _mkt_opts else 0
                    r_mkt       = st.selectbox("Marketplace", _mkt_opts, index=_mkt_idx, key="rec_mkt")
                    r_reasoning = st.text_area("Reasoning / Notes",
                                               value=str(_pf.get("reasoning") or ""))
                    r_review    = st.date_input("Review Date", value=date.today() + timedelta(days=14))

                    if st.form_submit_button("💾 Save Recommendation", type="primary"):
                        save_recommendation({
                            "date_given":             str(r_date),
                            "asin":                   r_asin.strip().upper() or None,
                            "marketplace":            r_mkt,
                            "campaign_name":          r_camp,
                            "placement_type":         r_place,
                            "campaign_type":          r_type,
                            "current_multiplier":     r_cur_mul,
                            "recommended_action":     r_action,
                            "recommended_multiplier": r_rec_mul,
                            "reasoning":              r_reasoning,
                            "window_days":            14,
                            "review_date":            str(r_review),
                            "source":                 "manual",
                        })
                        st.session_state.pop("rec_prefill", None)
                        st.success("✅ Recommendation saved.")
                        st.rerun()

            st.divider()

            # ── Filter + list ─────────────────────────────────────────────────────────
            rhf1, rhf2, rhf3, rhf4, rhf5 = st.columns(5)
            with rhf1:
                rh_market = st.selectbox("Filter by Marketplace", ["all", "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au", "amazon.de"], key="rh_market")
                rh_market = None if rh_market == "all" else rh_market
            with rhf2:
                rh_source = st.selectbox("Source", ["All", "Manual only", "Auto only"], key="rh_source")
            with rhf3:
                show_pending = st.checkbox("Show only pending review", value=False)
            with rhf4:
                hide_paused = st.checkbox(
                    "⏸ Hide paused",
                    value=True,
                    key="rh_hide_paused",
                    help="Hide recommendations from campaigns whose End Date is 7+ days old (likely paused)"
                )
            with rhf5:
                show_critical_recs = st.checkbox(
                    "🚨 Critical only",
                    value=False,
                    key="rh_critical",
                    help="🔴 Losing money (ROAS < breakeven)  ·  🟢 High-opportunity (score ≥ 70)"
                )

            recs_df = get_recommendations_history(marketplace=rh_market)

            # Apply source filter
            if not recs_df.empty and rh_source != "All":
                _src_val = "manual" if rh_source == "Manual only" else "auto"
                recs_df = recs_df[recs_df["source"].fillna("auto") == _src_val]

            # Sort: highest score first, then newest date
            if not recs_df.empty:
                recs_df["score"] = pd.to_numeric(recs_df["score"], errors="coerce").fillna(0)
                recs_df = recs_df.sort_values(["score", "date_given"], ascending=[False, False]).reset_index(drop=True)

            if recs_df.empty:
                st.info("No recommendations logged yet. Run an analysis to generate them.")
            else:
                if show_pending:
                    today_str_flt = str(date.today())
                    recs_df = recs_df[
                        (recs_df["review_date"].fillna("") <= today_str_flt) &
                        (recs_df["outcome"].isna() | (recs_df["outcome"] == ""))
                    ]

                # Hide paused: end_date present and 7+ days before today
                if hide_paused and "end_date" in recs_df.columns:
                    _cutoff = str(date.today() - timedelta(days=7))
                    _has_end = recs_df["end_date"].notna() & (recs_df["end_date"] != "")
                    _is_paused_row = _has_end & (recs_df["end_date"] < _cutoff)
                    recs_df = recs_df[~_is_paused_row]

                # Critical filter: LOSING placement (risk) or score ≥ 70 (opportunity)
                if show_critical_recs:
                    _rsn_col = recs_df["reasoning"].fillna("").str.upper()
                    _is_losing = _rsn_col.str.startswith("LOSING")
                    _is_oppty  = recs_df["score"] >= 70
                    recs_df = recs_df[_is_losing | _is_oppty]

                def _fmt_change(row):
                    action = str(row.get("recommended_action") or "").strip()
                    mult   = row.get("recommended_multiplier")
                    try:
                        pct = int(round(float(mult)))
                    except (TypeError, ValueError):
                        pct = None
                    if action.lower() == "increase" and pct is not None:
                        return f"+{pct}%"
                    elif action.lower() == "decrease" and pct is not None:
                        return f"-{pct}%"
                    elif action.lower() == "no change":
                        return "0%"
                    return action or "—"

                recs_display = recs_df.copy()
                recs_display["change"] = recs_display.apply(_fmt_change, axis=1)

                display_cols = [
                    "id", "date_given", "end_date", "source", "score", "asin", "marketplace", "campaign_name",
                    "placement_type", "campaign_type", "change",
                    "reasoning", "review_date", "outcome"
                ]
                existing_cols = [c for c in display_cols if c in recs_display.columns]

                st.markdown(
                    f"<p style='font-size:0.8rem;color:{T['text_secondary']};margin-bottom:4px;'>"
                    "💡 Select a row to <strong>Clone &amp; Edit</strong> or <strong>Record Outcome</strong>.</p>",
                    unsafe_allow_html=True,
                )
                _sel = st.dataframe(
                    recs_display[existing_cols],
                    use_container_width=True,
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    column_config={
                        "end_date":  st.column_config.TextColumn("📅 End Date", width=100),
                        "change":    st.column_config.TextColumn("Change",      width=90),
                        "reasoning": st.column_config.TextColumn("Reasoning",   width=400),
                    },
                    key="recs_table_sel",
                )

                # Action bar — appears when a row is selected
                _sel_rows = _sel.selection.rows if _sel and hasattr(_sel, "selection") else []
                if _sel_rows:
                    _sel_data = recs_df.iloc[_sel_rows[0]].to_dict()
                    _camp_preview  = str(_sel_data.get("campaign_name") or "")[:55]
                    _place_preview = str(_sel_data.get("placement_type") or "")
                    _existing_outcome = str(_sel_data.get("outcome") or "")

                    st.markdown(
                        f"<div style='background:{T['card_bg']};border:1px solid {T['card_border']};"
                        f"border-radius:8px;padding:0.6rem 1rem;margin:6px 0;font-size:0.85rem;'>"
                        f"<strong>#{int(_sel_data.get('id', 0))}</strong> &nbsp;·&nbsp; "
                        f"{_camp_preview} &nbsp;·&nbsp; {_place_preview}"
                        + (f"&nbsp;&nbsp;<span style='color:{T['score_hi']};'>✅ Outcome already recorded</span>" if _existing_outcome else "")
                        + "</div>",
                        unsafe_allow_html=True,
                    )

                    _act1, _act2 = st.columns(2)

                    with _act1:
                        st.markdown("**📋 Clone & Edit**")
                        if st.button("Clone & Edit → open form above", key="clone_btn"):
                            st.session_state["rec_prefill"] = _sel_data
                            st.rerun()

                    with _act2:
                        st.markdown("**✅ Record Outcome**")
                        with st.form("outcome_form"):
                            _oc_text = st.text_input(
                                "What happened?",
                                value=_existing_outcome,
                                placeholder="e.g. ROAS improved from 2.1 to 3.4",
                                label_visibility="collapsed",
                            )
                            if st.form_submit_button("💾 Save Outcome", type="primary"):
                                if _oc_text.strip():
                                    update_recommendation_outcome(int(_sel_data["id"]), _oc_text.strip())
                                    st.success("✅ Outcome saved.")
                                    st.rerun()
                                else:
                                    st.warning("Enter an outcome first.")

                    # ── Debug cost breakdown panel ─────────────────────────────────
                    _dbg_raw = _sel_data.get("debug_json") or ""
                    if _dbg_raw and _dbg_raw not in ("{}", "null", ""):
                        try:
                            _dbg = json.loads(_dbg_raw)
                        except Exception:
                            _dbg = {}
                        if _dbg:
                            with st.expander("🔍 Cost Breakdown (Debug)", expanded=False):
                                _mkt = _dbg.get("marketplace", "")
                                _cur = (
                                    "£" if "co.uk" in _mkt
                                    else "€" if any(x in _mkt for x in [".de", ".fr", ".es", ".it"])
                                    else "CA$" if ".ca" in _mkt
                                    else "$"
                                )

                                st.markdown("**How the breakeven ROAS was calculated:**")

                                _rows = []
                                avg_p = _dbg.get("avg_price", 0)
                                if avg_p:
                                    _rows.append({"Item": "Avg Sale Price", "Value": f"{_cur}{avg_p:.2f}", "Notes": "from placement report"})

                                lc_usd = _dbg.get("landed_cost_usd", 0)
                                fx     = _dbg.get("fx_rate", 1.0)
                                ll     = _dbg.get("landed_local", 0)
                                if lc_usd:
                                    pc = _dbg.get("product_cost_usd", 0)
                                    sc = _dbg.get("shipping_cost_usd", 0)
                                    cc = _dbg.get("customs_cost_usd", 0)
                                    _rows.append({"Item": "  Product Cost",  "Value": f"${pc:.2f} USD", "Notes": ""})
                                    _rows.append({"Item": "  Shipping Cost", "Value": f"${sc:.2f} USD", "Notes": ""})
                                    _rows.append({"Item": "  Customs Cost",  "Value": f"${cc:.2f} USD", "Notes": ""})
                                    _rows.append({"Item": "Landed Cost (converted)", "Value": f"{_cur}{ll:.2f}", "Notes": f"${lc_usd:.2f} x {fx:.4f} FX rate"})

                                pp     = _dbg.get("pick_pack_fee", 0)
                                pp_src = _dbg.get("pick_pack_source", "")
                                _rows.append({"Item": "Pick & Pack", "Value": f"{_cur}{pp:.2f}", "Notes": pp_src})

                                rf     = _dbg.get("referral_fee", 0)
                                rf_src = _dbg.get("referral_source", "")
                                _rows.append({"Item": "Referral Fee", "Value": f"{_cur}{rf:.2f}", "Notes": rf_src})

                                tc  = _dbg.get("total_costs_local", 0)
                                mg  = _dbg.get("margin_local", 0)
                                be  = _dbg.get("breakeven_roas", 0)
                                pr  = _dbg.get("placement_roas", 0)
                                sp  = _dbg.get("placement_spend", 0)
                                pur = _dbg.get("placement_purchases", 0)
                                cf  = _dbg.get("confidence", 0)

                                _rows.append({"Item": "----------------", "Value": "", "Notes": ""})
                                _rows.append({"Item": "Total Costs",    "Value": f"{_cur}{tc:.2f}", "Notes": ""})
                                _rows.append({"Item": "Margin",         "Value": f"{_cur}{mg:.2f}", "Notes": f"{(mg / avg_p * 100):.1f}% of price" if avg_p else ""})
                                _rows.append({"Item": "Breakeven ROAS", "Value": f"{be:.2f}x",      "Notes": "min ROAS to cover costs"})
                                _rows.append({"Item": "----------------", "Value": "", "Notes": ""})
                                _rows.append({"Item": "Placement ROAS", "Value": f"{pr:.2f}x",      "Notes": "Profitable" if pr >= be else "Losing"})
                                _rows.append({"Item": "Spend",          "Value": f"{_cur}{sp:.2f}", "Notes": ""})
                                _rows.append({"Item": "Purchases",      "Value": str(int(pur)),     "Notes": f"confidence: {int(cf * 100)}%"})

                                st.dataframe(
                                    pd.DataFrame(_rows),
                                    use_container_width=True,
                                    hide_index=True,
                                    column_config={
                                        "Item":  st.column_config.TextColumn(width=200),
                                        "Value": st.column_config.TextColumn(width=150),
                                        "Notes": st.column_config.TextColumn(width=300),
                                    },
                                )

        with _alerts_view:
            # ── ALERTS content ────────────────────────────────────────────────
            st.markdown("# 🔔 Performance Alerts")
            st.markdown(
                f"<p style='color:{T['text_secondary']};font-size:0.95rem;'>"
                "Compares the two most recent snapshots for every campaign. "
                "Snapshots are saved automatically each time you run an analysis.</p>",
                unsafe_allow_html=True,
            )
            st.divider()

            # Pick marketplace from available snapshots
            from db.database import get_conn as _alerts_get_conn
            _snap_markets = [
                r[0] for r in _alerts_get_conn().execute(
                    "SELECT DISTINCT marketplace FROM campaign_performance ORDER BY marketplace"
                ).fetchall()
            ]

            if not _snap_markets:
                st.info("No snapshots yet. Run an analysis first to start tracking campaign performance over time.")
            else:
                _alerts_market = st.selectbox(
                    "Marketplace", _snap_markets, key="alerts_market_sel"
                )
                _thresh = get_alert_thresholds()
                _all_perf_alerts = get_performance_alerts(_alerts_market, _thresh)
                # Worst regression first (biggest ROAS drop, then biggest profit loss)
                _neg_alerts = sorted(
                    [a for a in _all_perf_alerts if a["type"] == "regression"],
                    key=lambda x: (x["roas_chg_pct"], x["before_profit"] - x["after_profit"]),
                    reverse=True,
                )
                # Best improvement first (biggest ROAS gain, then biggest profit gain)
                _pos_alerts = sorted(
                    [a for a in _all_perf_alerts if a["type"] == "improvement"],
                    key=lambda x: (x["roas_chg_pct"], x["after_profit"] - x["before_profit"]),
                    reverse=True,
                )

                _ac1, _ac2, _ac3 = st.columns(3)
                _ac1.metric("📸 Snapshots stored",
                    _alerts_get_conn().execute(
                        "SELECT COUNT(DISTINCT snapshot_date) FROM campaign_performance WHERE marketplace = ?",
                        (_alerts_market,)
                    ).fetchone()[0]
                )
                _ac2.metric("🔴 Regressions", len(_neg_alerts))
                _ac3.metric("🟢 Improvements", len(_pos_alerts))

                st.divider()

                # ── Threshold editor — wrapped in st.form so +/- clicks don't rerun ──
                with st.expander("⚙️ Alert Thresholds", expanded=False):
                    st.caption(
                        "Saved to the database — changes apply immediately to the alerts above without re-uploading."
                    )
                    with st.form("alert_thresholds_form"):
                        _at1, _at2 = st.columns(2)
                        with _at1:
                            st.markdown("##### 🔴 Negative alert (regression)")
                            _rd = st.number_input(
                                "ROAS drop % to trigger alert",
                                min_value=5, max_value=90, step=5,
                                value=int(_thresh.get("alert_roas_drop_pct", 30)),
                                help="Alert fires when ROAS drops by this % or more vs the previous snapshot."
                            )
                            _pd = st.number_input(
                                "Profit drop % to trigger alert",
                                min_value=5, max_value=90, step=5,
                                value=int(_thresh.get("alert_profit_drop_pct", 20)),
                                help="Both ROAS AND profit must drop to fire a negative alert."
                            )
                        with _at2:
                            st.markdown("##### 🟢 Positive alert (improvement)")
                            _rg = st.number_input(
                                "ROAS gain % to trigger alert",
                                min_value=5, max_value=90, step=5,
                                value=int(_thresh.get("alert_roas_gain_pct", 30)),
                                help="Alert fires when ROAS rises by this % or more vs the previous snapshot."
                            )
                            _pg = st.number_input(
                                "Profit gain % to trigger alert",
                                min_value=5, max_value=90, step=5,
                                value=int(_thresh.get("alert_profit_gain_pct", 20)),
                                help="Both ROAS AND profit must rise to fire a positive alert."
                            )
                        if st.form_submit_button("💾 Save Thresholds"):
                            save_setting("alert_roas_drop_pct",   _rd)
                            save_setting("alert_profit_drop_pct", _pd)
                            save_setting("alert_roas_gain_pct",   _rg)
                            save_setting("alert_profit_gain_pct", _pg)
                            st.rerun()

                st.divider()

                if not _neg_alerts and not _pos_alerts:
                    st.success("✅ No significant changes detected between the last two snapshots.")

                # ── Debug: show raw snapshot comparison ───────────────────────
                with st.expander("🔍 Debug — raw snapshot comparison", expanded=False):
                    _dbg_conn = _alerts_get_conn()
                    _dbg_dates = [r[0] for r in _dbg_conn.execute(
                        "SELECT DISTINCT snapshot_date FROM campaign_performance WHERE marketplace = ? ORDER BY snapshot_date DESC LIMIT 2",
                        (_alerts_market,)
                    ).fetchall()]
                    st.caption(f"Two most recent snapshot dates: **{' → '.join(reversed(_dbg_dates))}**")
                    if len(_dbg_dates) >= 2:
                        _dbg_rows = _dbg_conn.execute("""
                            SELECT campaign_name, placement_type, snapshot_date,
                                   roas, spend, sales, purchases, total_profit, breakeven_roas
                            FROM campaign_performance
                            WHERE marketplace = ? AND snapshot_date IN (?, ?)
                            ORDER BY campaign_name, placement_type, snapshot_date
                        """, (_alerts_market, _dbg_dates[0], _dbg_dates[1])).fetchall()
                        import pandas as _dbg_pd
                        _dbg_df = _dbg_pd.DataFrame([dict(r) for r in _dbg_rows])
                        st.dataframe(_dbg_df, use_container_width=True, hide_index=True)
                    _dbg_conn.close()

                if _neg_alerts:
                    st.error(
                        f"⚠️ **{len(_neg_alerts)} placement(s) regressed** — "
                        f"ROAS dropped >{int(_thresh['alert_roas_drop_pct'])}% AND "
                        f"profit dropped >{int(_thresh['alert_profit_drop_pct'])}% vs previous snapshot."
                    )
                    for _bf in _neg_alerts:
                        with st.expander(
                            f"🔴 {_bf['campaign']} — {_bf['placement']} "
                            f"({_bf['before_date']} → {_bf['after_date']})", expanded=True
                        ):
                            _nc1, _nc2, _nc3, _nc4, _nc5 = st.columns(5)
                            _nc1.metric("ROAS Before", f"{_bf['before_roas']}x")
                            _nc2.metric("ROAS After",  f"{_bf['after_roas']}x",
                                        delta=f"-{_bf['roas_chg_pct']}%", delta_color="inverse")
                            if _bf.get("profit_data"):
                                _nc3.metric("Profit Before", f"${_bf['before_profit']:.0f}")
                                _nc4.metric("Profit After",  f"${_bf['after_profit']:.0f}",
                                            delta=f"{_bf['after_profit'] - _bf['before_profit']:.0f}",
                                            delta_color="inverse")
                            else:
                                _nc3.metric("Profit Before", "—")
                                _nc4.metric("Profit After",  "—", help="Re-run analysis to populate profit data")
                            _nc5.metric("Spend", f"${_bf['spend']:.2f}")
                            st.caption(
                                f"Purchases: {_bf['purchases']} · "
                                f"Snapshots: {_bf['before_date']} → {_bf['after_date']}"
                                + ("" if _bf.get("profit_data") else " · ⚠️ ROAS-only (no profit data — re-run analysis to fix)")
                            )
                            st.caption("💡 Consider reverting the bid multiplier for this placement.")

                if _pos_alerts:
                    st.success(
                        f"🚀 **{len(_pos_alerts)} placement(s) improved significantly** — "
                        f"ROAS rose >{int(_thresh['alert_roas_gain_pct'])}% AND "
                        f"profit rose >{int(_thresh['alert_profit_gain_pct'])}% vs previous snapshot."
                    )
                    for _imp in _pos_alerts:
                        with st.expander(
                            f"🟢 {_imp['campaign']} — {_imp['placement']} "
                            f"({_imp['before_date']} → {_imp['after_date']})", expanded=False
                        ):
                            _pc1, _pc2, _pc3, _pc4, _pc5 = st.columns(5)
                            _pc1.metric("ROAS Before", f"{_imp['before_roas']}x")
                            _pc2.metric("ROAS After",  f"{_imp['after_roas']}x",
                                        delta=f"+{_imp['roas_chg_pct']}%")
                            if _imp.get("profit_data"):
                                _pc3.metric("Profit Before", f"${_imp['before_profit']:.0f}")
                                _pc4.metric("Profit After",  f"${_imp['after_profit']:.0f}",
                                            delta=f"+{_imp['after_profit'] - _imp['before_profit']:.0f}")
                            else:
                                _pc3.metric("Profit Before", "—")
                                _pc4.metric("Profit After",  "—", help="Re-run analysis to populate profit data")
                            _pc5.metric("Spend", f"${_imp['spend']:.2f}")
                            st.caption(
                                f"Purchases: {_imp['purchases']} · "
                                f"Snapshots: {_imp['before_date']} → {_imp['after_date']}"
                            )
                            st.caption("✅ Consider locking in or scaling this placement.")

        with _analysis_view:
            # ── ANALYSIS content ──────────────────────────────────────────────
            st.markdown("# 📊 Amazon Ads Placement Analyzer")
            st.markdown(
                f"<p style='color:{T['text_secondary']};font-size:0.95rem;'>"
                f"Upload your 30-day placement reports. Get scores, bid recommendations, alerts, and AI comments.</p>",
                unsafe_allow_html=True
            )

            if products_exist_db():
                cost_map = get_cost_map_db()
                st.success(f"✅ Product cost data loaded — break-even ROAS calculated dynamically for {len(cost_map)} products.")
            else:
                st.warning("⚠️ No product cost data found. Using default ROAS target. Go to the **📦 Products** tab to set up.")
                cost_map = {}

            st.divider()

            col1, col2 = st.columns(2)
            with col1:
                sp_file = st.file_uploader("📦 Sponsored Products — Placement Report (.xlsx)", type=["xlsx"], key="sp")
            with col2:
                sb_file = st.file_uploader("🏷️ Sponsored Brands — Campaign Placement Report (.xlsx)", type=["xlsx"], key="sb")

            # Optional: let the user back-date the snapshot when uploading old reports
            with st.expander("📅 Snapshot date (optional — set only when uploading old reports)", expanded=False):
                st.caption(
                    "By default the snapshot is saved with **today's date**. "
                    "If you're uploading a report from an earlier date (e.g. May 13) set that date here "
                    "so the regression comparison timeline stays accurate."
                )
                _snap_date_override = st.date_input(
                    "Report date",
                    value=date.today(),
                    max_value=date.today(),
                    key="snap_date_override",
                    label_visibility="collapsed",
                )
                _use_date_override = st.checkbox("Use this date for the snapshot", value=False, key="use_snap_override")

            st.divider()

            # ── Analysis parameters ───────────────────────────────────────────────────
            pc1, pc2, pc3 = st.columns(3)
            with pc1:
                target_roas = st.number_input(
                    "Default Target ROAS",
                    min_value=1.0, max_value=20.0, value=float(TARGET_ROAS), step=0.5,
                    help="Used for campaigns without a matching ASIN in Products & Costs."
                )
            with pc2:
                low_impr = st.number_input(
                    "Low Impressions Alert (Top)",
                    min_value=100, max_value=50000, value=LOW_IMPR_THRESHOLD, step=100,
                )
            with pc3:
                min_margin_pct = st.number_input(
                    "Minimum Profit Margin %",
                    min_value=5, max_value=80, value=25, step=5,
                    help="Bid recommendations will never exceed this margin floor."
                ) / 100

            if sp_file and sb_file:
                if st.button("🚀 Run Analysis", type="primary", use_container_width=True):

                    with st.spinner("Loading and analyzing data..."):
                        sp_path = os.path.join(SESSION_DIR, "sp_report.xlsx")
                        sb_path = os.path.join(SESSION_DIR, "sb_report.xlsx")
                        with open(sp_path, "wb") as f: f.write(sp_file.read())
                        with open(sb_path, "wb") as f: f.write(sb_file.read())
                        detected_marketplace = detect_marketplace_from_xlsx(sp_path)
                        try:
                            _fx_rates = get_fx_rates()
                            _fx_rate  = _fx_rates.get(detected_marketplace, 1.0)
                            _fba_map  = get_fba_fees_map(detected_marketplace)
                            results = analyze_with_products(
                                sp_path, sb_path, target_roas, low_impr, cost_map, min_margin_pct,
                                detected_marketplace,
                                fba_fees_map=_fba_map,
                                fx_rate=_fx_rate,
                            )
                        finally:
                            for p in [sp_path, sb_path]:
                                if os.path.exists(p): os.unlink(p)

                    # ── Save performance snapshot for backfire detection ──────────────
                    _snap_date = (
                        str(_snap_date_override)
                        if _use_date_override and _snap_date_override
                        else str(date.today())
                    )
                    save_performance_snapshot(results, _snap_date, detected_marketplace)

                    # ── Auto-save recommendations to DB (batched) ────────────────────
                    today_str  = str(date.today())
                    review_str = str(date.today() + timedelta(days=14))
                    _rec_batch = []
                    for r in results:
                        if r.is_paused:
                            continue
                        asin = next(
                            (a for a in cost_map if a.upper() in r.campaign.upper()),
                            None
                        )
                        for pl_rec in r.bid_recs_data:
                            _rec_batch.append({
                                "date_given":             today_str,
                                "asin":                   asin,
                                "marketplace":            r.marketplace,
                                "campaign_name":          r.campaign,
                                "placement_type":         pl_rec["placement_type"],
                                "campaign_type":          r.ad_type,
                                "current_multiplier":     None,
                                "recommended_action":     pl_rec["recommended_action"],
                                "recommended_multiplier": pl_rec["recommended_multiplier"],
                                "reasoning":              pl_rec["reasoning"],
                                "window_days":            14,
                                "review_date":            review_str,
                                "score":                  r.score,
                                "end_date":               r.end_date or None,
                                "debug":                  pl_rec.get("debug", {}),
                            })
                    saved_count = save_recommendations_batch(_rec_batch)
                    if saved_count:
                        st.success(f"✅ {saved_count} placement recommendations auto-saved to history.")

                    if api_key:
                        st.markdown("### 🤖 Generating AI Comments...")
                        progress_bar = st.progress(0)
                        status_text  = st.empty()

                        def on_progress(i, total, camp):
                            progress_bar.progress(i / total if total > 0 else 0)
                            status_text.markdown(
                                f"<span style='color:{T['text_secondary']};font-size:0.8rem;'>({i}/{total}) {camp}</span>",
                                unsafe_allow_html=True
                            )

                        results = generate_comments(results, api_key, target_roas, progress_callback=on_progress)
                        progress_bar.progress(1.0)
                        status_text.markdown(f"<span style='color:{T['score_hi']};font-size:0.8rem;'>✓ Comments ready</span>", unsafe_allow_html=True)
                    else:
                        st.info("ℹ️ No API key — skipping AI comments.")

                    st.divider()
                    st.caption(f"🌍 Marketplace auto-detected: **{detected_marketplace}**")
                    st.markdown("### 📈 Summary")

                    sp_count  = sum(1 for r in results if r.ad_type == "SP")
                    sb_count  = sum(1 for r in results if r.ad_type == "SB")
                    hi_count  = sum(1 for r in results if r.score >= 80)
                    alert_cnt = sum(1 for r in results if r.alert)

                    mc1, mc2, mc3, mc4 = st.columns(4)
                    for col, val, label in [
                        (mc1, sp_count, "SP Campaigns"),
                        (mc2, sb_count, "SB Campaigns"),
                        (mc3, hi_count, "Score ≥ 80"),
                        (mc4, alert_cnt, "🚨 Alerts"),
                    ]:
                        col.markdown(
                            f'<div class="metric-card"><p class="metric-val">{val}</p>'
                            f'<p class="metric-label">{label}</p></div>',
                            unsafe_allow_html=True
                        )

                    alerts = [r for r in results if r.alert]
                    if alerts:
                        st.divider()
                        st.markdown("### 🚨 Campaigns Requiring Immediate Attention")
                        for r in alerts[:10]:
                            score_cls = "score-hi" if r.score >= 70 else "score-mid"
                            tag = '<span class="tag-sp">SP</span>' if r.ad_type == "SP" else '<span class="tag-sb">SB</span>'
                            auto_tag = '<span class="tag-auto">AUTO</span>' if r.targeting == "Auto" else ""
                            st.markdown(
                                f'<div class="alert-box">{tag} {auto_tag} '
                                f'<strong>{r.campaign}</strong> — '
                                f'Score: <span class="{score_cls}">{r.score}</span> — {r.alert}<br>'
                                f'<span style="color:{T["text_secondary"]};font-size:0.8rem;">Bid rec: {r.bid_rec}</span>'
                                f'</div>',
                                unsafe_allow_html=True
                            )

                    # ── Snapshot-based alerts live in the 🔔 Alerts tab ──────────────
                    _snap_alert_count = len(get_performance_alerts(detected_marketplace))
                    if _snap_alert_count:
                        st.info(
                            f"🔔 **{_snap_alert_count} performance alert(s)** detected vs the previous snapshot. "
                            f"See the **🔔 Alerts** tab for details."
                        )

                    st.divider()
                    st.markdown("### 🏆 All Campaigns — Ranked by Score")

                    table_data = []
                    for r in results:
                        table_data.append({
                            "Marketplace": r.marketplace,
                            "Campaign": r.campaign, "Type": r.ad_type, "Targeting": r.targeting,
                            "Score": r.score, "Label": r.score_label,
                            "Top ROAS": round(r.top.roas, 2) if r.top.roas else None,
                            "Rest ROAS": round(r.rest.roas, 2) if r.rest.roas else None,
                            "Top Impr.": r.top.impressions, "Bid Rec": r.bid_rec,
                            "Alert": "🚨" if r.alert else "",
                        })

                    st.dataframe(
                        pd.DataFrame(table_data),
                        use_container_width=True, hide_index=True,
                        column_config={
                            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                            "Top Impr.": st.column_config.NumberColumn(format="%d"),
                        }
                    )

                    # ── Placement Algorithm Results ───────────────────────────────────
                    st.divider()
                    st.markdown("### 📍 Placement Algorithm")

                    _algo_counts = {"isolation": 0, "optimization": 0, "learning": 0, "no_data": 0}
                    for _r in results:
                        _m = _r.mode or "no_data"
                        _algo_counts[_m] = _algo_counts.get(_m, 0) + 1

                    _ac1, _ac2, _ac3, _ac4 = st.columns(4)
                    for _col, _mode, _label, _color in [
                        (_ac1, "isolation",    "🔴 Isolation",    "#FFC7CE"),
                        (_ac2, "optimization", "🟢 Optimization", "#C6EFCE"),
                        (_ac3, "learning",     "🚼 Learning",     "#FFEB9C"),
                        (_ac4, "no_data",      "⚫ No Data",      "#D9D9D9"),
                    ]:
                        _cnt = _algo_counts.get(_mode, 0)
                        _col.markdown(
                            f'<div style="background:{_color};border-radius:8px;padding:10px;text-align:center;">'
                            f'<p style="font-size:1.6rem;font-weight:700;margin:0;">{_cnt}</p>'
                            f'<p style="font-size:0.8rem;margin:0;">{_label}</p></div>',
                            unsafe_allow_html=True
                        )

                    st.markdown("")

                    # ── Critical filter ───────────────────────────────────
                    _n_risk  = sum(1 for _r in results if _r.mode == "isolation" and not _r.is_paused)
                    _n_opp   = sum(1 for _r in results if _r.is_critical and _r.mode == "optimization" and not _r.is_paused)
                    _n_paused = sum(1 for _r in results if _r.is_paused)
                    _n_crit  = _n_risk + _n_opp

                    _crit_col, _crit_info = st.columns([3, 7])
                    with _crit_col:
                        _show_critical = st.checkbox(
                            f"🚨 Critical only  ({_n_crit})",
                            value=False,
                            key="show_critical_only",
                            help="Show only campaigns losing money (🔴 Isolation) or high-confidence opportunities (🟢 score ≥ 70)"
                        )
                    with _crit_info:
                        parts = []
                        if _n_risk:  parts.append(f"🔴 **{_n_risk}** losing money")
                        if _n_opp:   parts.append(f"🟢 **{_n_opp}** high-opportunity")
                        if _n_paused: parts.append(f"⏸ **{_n_paused}** paused (excluded)")
                        if parts:
                            st.caption(" &nbsp;·&nbsp; ".join(parts))

                    _display_results = [_r for _r in results if _r.is_critical] if _show_critical else results

                    _MODE_COLORS = {
                        "isolation":    ("#FFC7CE", "🔴 ISOLATION"),
                        "optimization": ("#C6EFCE", "🟢 OPTIMIZATION"),
                        "learning":     ("#FFEB9C", "🚼 LEARNING"),
                        "no_data":      ("#D9D9D9", "⚫ NO DATA"),
                    }
                    _ACTION_COLORS = {
                        "increase":  "#C6EFCE",
                        "reduce":    "#FCE4D6",
                        "keep":      "#F2F2F2",
                    }

                    for _r in _display_results:
                        _algo   = _r.placement_algorithm or {}
                        _mode   = _r.mode or "no_data"
                        _mc, _ml = _MODE_COLORS.get(_mode, ("#D9D9D9", _mode.upper()))
                        _base   = _algo.get("base_bid_change_pct", 0)
                        _rsn    = _algo.get("reasoning", "")
                        _pls    = _algo.get("placements", [])
                        _sc     = _algo.get("score", 0)

                        _base_txt = (
                            f"⬇️ Reduce all keyword bids **{abs(_base)}%**" if _base < 0
                            else "No base bid change" if _base == 0
                            else f"⬆️ Increase base bids {_base}%"
                        )
                        _paused_badge = " &nbsp;⏸ PAUSED" if _r.is_paused else ""
                        _end_txt = f" &nbsp;|&nbsp; 📅 {_r.end_date}" if _r.end_date else ""
                        _exp_label = (
                            f"{_ml}{_paused_badge} &nbsp;|&nbsp; {_r.campaign} &nbsp;|&nbsp; {_base_txt} &nbsp;|&nbsp; Score: {_sc}/10{_end_txt}"
                        )
                        with st.expander(_exp_label, expanded=(_mode == "isolation")):
                            st.markdown(
                                f'<div style="background:{_mc};border-radius:6px;padding:8px 12px;'
                                f'font-size:0.85rem;">{_rsn}</div>',
                                unsafe_allow_html=True
                            )
                            if _pls:
                                st.markdown("")
                                _pl_cols = st.columns(len(_pls))
                                for _ci, _p in enumerate(_pls):
                                    with _pl_cols[_ci]:
                                        _act = (_p.get("recommended_action") or "").lower()
                                        _abg = (
                                            "#C6EFCE" if "increase" in _act
                                            else "#FCE4D6" if "reduce" in _act
                                            else "#F2F2F2"
                                        )
                                        _conf_pct = int(_p.get("confidence", 0) * 100)
                                        _conf_bar = "🟢" if _conf_pct >= 67 else "🟡" if _conf_pct >= 33 else "🔴"
                                        st.markdown(
                                            f'<div style="border:1px solid #ddd;border-radius:8px;padding:10px;">'
                                            f'<p style="font-weight:700;font-size:0.9rem;margin:0 0 6px;">{_p["label"]}</p>'
                                            f'<table style="width:100%;font-size:0.8rem;border-collapse:collapse;">'
                                            f'<tr><td style="color:#666;">ROAS</td><td style="text-align:right;font-weight:600;">{_p.get("roas", 0):.2f}</td></tr>'
                                            f'<tr><td style="color:#666;">Current %</td><td style="text-align:right;">{int(round(_p.get("current_adj",0)*100))}%</td></tr>'
                                            f'<tr><td style="color:#666;">Confidence</td><td style="text-align:right;">{_conf_bar} {_conf_pct}%</td></tr>'
                                            f'<tr><td style="color:#666;">Purchases</td><td style="text-align:right;">{_p.get("purchases", 0)}</td></tr>'
                                            f'</table>'
                                            f'<div style="margin-top:8px;background:{_abg};border-radius:5px;padding:6px 8px;'
                                            f'font-size:0.82rem;font-weight:700;text-align:center;">'
                                            f'{_p.get("recommended_action","—")} → {_p.get("recommended_multiplier","—")}%'
                                            f'</div>'
                                            f'<p style="font-size:0.72rem;color:#555;margin-top:6px;">{_p.get("reasoning","")}</p>'
                                            f'</div>',
                                            unsafe_allow_html=True
                                        )
                            elif _mode == "learning":
                                st.info("🚼 No placement recommendations during launch phase.")
                            else:
                                st.caption("No placement data available.")

                    st.divider()
                    st.markdown("### 📥 Download Excel Report")
                    with st.spinner("Building Excel..."):
                        excel_bytes = build_excel(results)

                    st.download_button(
                        label="⬇️ Download Amazon_Ads_Analysis.xlsx",
                        data=excel_bytes,
                        file_name="Amazon_Ads_Analysis.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary",
                        use_container_width=True,
                    )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — ADMIN  (admin role only)
# ══════════════════════════════════════════════════════════════════════════════
if tab_admin is not None:
    with tab_admin:
        st.markdown("# ⚙️ Admin")
        st.markdown(
            f"<p style='color:{T['text_secondary']};'>Maintenance tools. Use with care.</p>",
            unsafe_allow_html=True,
        )
        st.divider()

        st.markdown("### 🗑️ Reset Data")

        from db.database import get_conn as _get_conn

        def _count(table):
            conn = _get_conn()
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.close()
            return n

        rec_count = _count("recommendations")
        log_count = _count("change_log")

        st.markdown(
            f"Current records: &nbsp;"
            f"<strong>{rec_count:,}</strong> recommendations &nbsp;·&nbsp; "
            f"<strong>{log_count:,}</strong> change log entries",
            unsafe_allow_html=True,
        )
        st.divider()

        col_r, col_c = st.columns(2)

        with col_r:
            st.markdown("#### Recommendations")
            del_scope = st.radio(
                "Scope",
                ["All", "Auto only", "Manual only"],
                horizontal=True,
                key="del_rec_scope",
                label_visibility="collapsed",
            )
            confirm_recs = st.checkbox("Yes, delete selected recommendations", key="confirm_recs")
            if st.button("🗑️ Delete Recommendations", type="primary", disabled=not confirm_recs):
                conn = _get_conn()
                with conn:
                    if del_scope == "Auto only":
                        conn.execute("DELETE FROM recommendations WHERE source = 'auto'")
                    elif del_scope == "Manual only":
                        conn.execute("DELETE FROM recommendations WHERE source = 'manual'")
                    else:
                        conn.execute("DELETE FROM recommendations")
                conn.close()
                st.success(f"✅ Deleted recommendations ({del_scope.lower()}).")
                st.rerun()

        with col_c:
            st.markdown("#### Change Log")
            confirm_log = st.checkbox("Yes, delete all change log entries", key="confirm_log")
            if st.button("🗑️ Delete All Change Log", type="primary", disabled=not confirm_log):
                conn = _get_conn()
                with conn:
                    conn.execute("DELETE FROM change_log")
                conn.close()
                st.success(f"✅ Deleted {log_count:,} change log entries.")
                st.rerun()

        # ── Performance Snapshots ─────────────────────────────────────────────
        st.divider()
        st.markdown("### 📸 Performance Snapshots")
        st.markdown(
            f"<p style='color:{T['text_secondary']};font-size:0.9rem;'>"
            "Snapshots are saved per marketplace automatically on every analysis run. "
            "Reset a marketplace when you want a fresh comparison baseline — e.g. after a major campaign restructure. "
            "Each marketplace is fully independent.</p>",
            unsafe_allow_html=True,
        )

        _snap_summary = get_snapshot_summary()

        if not _snap_summary:
            st.info("No snapshots stored yet. Run an analysis to create the first baseline.")
        else:
            # Summary table
            import pandas as _spd
            _snap_df = _spd.DataFrame(_snap_summary).rename(columns={
                "marketplace":    "Marketplace",
                "distinct_dates": "Snapshots",
                "campaign_count": "Campaigns",
                "latest_date":    "Latest snapshot",
            })
            st.dataframe(_snap_df, use_container_width=True, hide_index=True)

            st.markdown("#### Reset snapshots for a marketplace")
            _snap_markets = [r["marketplace"] for r in _snap_summary]
            _reset_market = st.selectbox(
                "Select marketplace to reset",
                ["— All marketplaces —"] + _snap_markets,
                key="reset_snap_market",
            )
            confirm_snaps = st.checkbox("Yes, I want to reset these snapshots", key="confirm_snaps")
            if st.button("🗑️ Reset Snapshots", type="primary", disabled=not confirm_snaps):
                if _reset_market == "— All marketplaces —":
                    reset_snapshots()
                    st.success("✅ All snapshots deleted across all marketplaces.")
                else:
                    reset_snapshots(_reset_market)
                    st.success(f"✅ Snapshots for **{_reset_market}** deleted. Next upload will be the new baseline.")
                st.rerun()

        # ── Session Management ────────────────────────────────────────────────
        st.divider()
        st.markdown("### 🔌 Session Management")
        st.markdown(
            f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>"
            "End a team member's active session. They will be logged out on their next page load.</p>",
            unsafe_allow_html=True,
        )

        _all_users = list(st.secrets["auth"]["credentials"]["usernames"].keys())
        _team_users = [u for u in _all_users if u != current_username]  # can't end your own session here
        _pending_logout = list_force_logout_users()

        if _team_users:
            _sess_cols = st.columns(len(_team_users))
            for _col, _uname in zip(_sess_cols, _team_users):
                _uinfo = st.secrets["auth"]["credentials"]["usernames"][_uname]
                _is_pending = _uname in _pending_logout
                with _col:
                    st.markdown(
                        f"<div style='border:1px solid {T['card_border']};border-radius:8px;"
                        f"padding:0.75rem 1rem;background:{T['card_bg']};'>"
                        f"<strong>{_uinfo['name']}</strong><br>"
                        f"<span style='font-size:0.75rem;color:{T['text_secondary']};'>{_uinfo['role']}</span><br>"
                        + (f"<span style='font-size:0.72rem;color:{T['score_lo']};'>⏳ Pending logout</span>" if _is_pending else "")
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
                    if _is_pending:
                        if st.button(f"↩️ Cancel", key=f"cancel_logout_{_uname}"):
                            _c = _get_conn()
                            with _c:
                                _c.execute("DELETE FROM force_logout WHERE username=?", (_uname,))
                            _c.close()
                            st.rerun()
                    else:
                        if st.button(f"🔌 End Session", key=f"end_session_{_uname}"):
                            flag_force_logout(_uname)
                            st.success(f"✅ {_uinfo['name']}'s session will end on next page load.")
                            st.rerun()

        # ── Password Reset ────────────────────────────────────────────────────
        st.divider()
        st.markdown("### 🔑 Reset Password")
        st.markdown(
            f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>"
            "Set a new password for any team member. Takes effect immediately on next login.</p>",
            unsafe_allow_html=True,
        )

        import bcrypt as _bcrypt

        def _update_secrets_password(username: str, new_hash: str) -> bool:
            """Rewrite secrets.toml replacing only the target user's hashed_password line."""
            import os as _os
            _path = _os.path.join(_os.path.dirname(__file__), ".streamlit", "secrets.toml")
            if not _os.path.exists(_path):
                return False
            lines = open(_path).readlines()
            in_block = False
            new_lines = []
            for line in lines:
                if f"[auth.credentials.usernames.{username}]" in line:
                    in_block = True
                elif line.strip().startswith("[") and in_block:
                    in_block = False
                if in_block and line.strip().startswith("hashed_password"):
                    line = f'hashed_password  = "{new_hash}"\n'
                new_lines.append(line)
            with open(_path, "w") as f:
                f.writelines(new_lines)
            return True

        _pw_user = st.selectbox("User", _all_users,
                                format_func=lambda u: st.secrets["auth"]["credentials"]["usernames"][u]["name"],
                                key="pw_reset_user")
        _pw1, _pw2 = st.columns(2)
        with _pw1:
            _new_pw = st.text_input("New Password", type="password", key="pw_reset_new")
        with _pw2:
            _confirm_pw = st.text_input("Confirm Password", type="password", key="pw_reset_confirm")

        if st.button("🔑 Reset Password", type="primary"):
            if not _new_pw:
                st.error("Enter a new password.")
            elif _new_pw != _confirm_pw:
                st.error("Passwords do not match.")
            elif len(_new_pw) < 8:
                st.error("Password must be at least 8 characters.")
            else:
                _new_hash = _bcrypt.hashpw(_new_pw.encode(), _bcrypt.gensalt()).decode()
                _ok = _update_secrets_password(_pw_user, _new_hash)
                if _ok:
                    _uname_display = st.secrets["auth"]["credentials"]["usernames"][_pw_user]["name"]
                    st.success(f"✅ Password for {_uname_display} updated. They can log in with the new password immediately.")
                else:
                    st.error("Could not find secrets.toml on this server. Update the file manually.")
