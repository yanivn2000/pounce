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
from db.amazon_module import init_amazon_tables, parse_amazon_transactions, insert_transactions, get_amazon_report
from db.performance import save_performance_snapshot, get_performance_alerts, get_bad_roas_campaigns, reset_snapshots, get_snapshot_count, get_snapshot_summary
from db.bid_changes import (
    record_bid_changes, record_placement_snapshots,
    get_bid_history, get_bid_effectiveness, get_last_effectiveness_bulk,
    get_all_bid_changes, get_untreated_losing, save_recommendation_note, save_campaign_note,
    log_manual_bid_change, get_campaigns_with_snapshots, delete_bid_change,
    get_unified_changes,
    get_all_bid_effectiveness,
)
from db.settings import get_alert_thresholds, save_setting
from db.inventory import (
    import_fba_csv, import_awd_csv, import_spm_csv, import_whcn_csv,
    upsert_manual_inventory, save_sku_mapping,
    get_inventory_overview, get_avg_daily_sales, get_inventory_alerts, get_sold_units,
    get_latest_inventory, LOCATIONS, FBA_LOCATIONS,
)
from db.importer import import_orders_csv, save_recommendation, save_recommendations_batch, update_recommendation_outcome
from db.fba_fees import (
    import_fee_preview_csv, get_fba_fees_map, get_all_fba_fees_df, clear_all_fba_fees,
    get_pick_pack_anomalies,
    get_fx_rates, get_fx_rates_df, save_fx_rate,
)
from db.queries import (
    get_sales_matrix, get_weekly_summary, get_recommendations_history,
    get_change_log, get_marketplaces, get_order_date_range, count_orders,
    get_units_matrix, get_weekly_units_matrix, get_weekly_units_matrix_yoy,
    search_orders_by_address, get_noted_recommendations,
)
from db.bundles import (
    import_bundle_csv, count_bundle_rows, get_bundle_date_range,
    get_bundle_summary, get_bundle_units_matrix, get_bundle_revenue_matrix,
    get_bundle_daily_trend, get_bundle_per_asin_trend, clear_all_bundles,
    fetch_bundle_images, get_bundle_asins,
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
    get_sku_supplier_map, get_sku_supplier_cost_map, get_asin_cost_map,
    get_asin_image_map, fetch_and_store_all_images,
)
from db.shipments import (
    get_shipments, get_shipment, save_shipment, delete_shipment, mark_shipped,
    get_shipment_lines, save_shipment_lines,
    get_stock_to_be_shipped, get_next_shipment_name,
    get_available_per_sku_excluding, get_packing_list,
    get_overview_shipment_data,
)
from labels import generate_carton_labels_pdf
from db.returns import (
    import_returns_csv, get_return_rate_report, get_returns_date_range,
    get_return_country_breakdown, get_available_countries, clear_all_returns,
    get_upload_meta, COUNTRY_FLAG, MARKETPLACE_TO_COUNTRY,
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

/* ── Base font ─────────────────────────────────────────────────── */
html, body, [class*="css"] {{ font-family: 'IBM Plex Sans', sans-serif; font-size: 13px; }}

/* ── Headings — compact, not massive ───────────────────────────── */
h1 {{ font-family:'IBM Plex Mono',monospace !important; letter-spacing:-0.03em;
      font-size:1.35rem !important; font-weight:700 !important; margin:0 0 4px !important; line-height:1.2 !important; }}
h2 {{ font-family:'IBM Plex Mono',monospace !important; letter-spacing:-0.02em;
      font-size:1.1rem !important;  font-weight:700 !important; margin:0 0 3px !important; line-height:1.2 !important; }}
h3 {{ font-family:'IBM Plex Mono',monospace !important; letter-spacing:-0.02em;
      font-size:0.95rem !important; font-weight:600 !important; margin:0 0 2px !important; line-height:1.2 !important; }}
h4, h5, h6 {{ font-family:'IBM Plex Sans',sans-serif !important;
      font-size:0.85rem !important; font-weight:600 !important; margin:0 0 2px !important; line-height:1.2 !important; }}
p {{ margin:0 0 4px !important; line-height:1.45 !important; }}

/* ── Page container — much less top padding ─────────────────────── */
.block-container {{ padding-top: 1rem !important; padding-bottom: 0.5rem !important;
                    max-width: 1400px !important; }}

/* ── Vertical widget gaps — very tight ──────────────────────────── */
div[data-testid="stVerticalBlock"] > div {{ gap: 0.2rem !important; }}
div[data-testid="column"] > div[data-testid="stVerticalBlock"] > div {{ gap: 0.15rem !important; }}

/* ── Tabs — smaller text, less padding ──────────────────────────── */
[data-testid="stTabs"] [role="tablist"] {{ gap: 0 !important; border-bottom: 1px solid #d0d7de; margin-bottom: 6px !important; }}
[data-testid="stTabs"] [role="tab"] {{ font-size: 0.8rem !important; padding: 5px 14px !important;
                                        font-weight: 500 !important; }}
[data-testid="stTabs"] [data-baseweb="tab-panel"] {{ padding-top: 4px !important; }}

/* ── Dividers — slim, low margin ────────────────────────────────── */
hr {{ margin: 6px 0 !important; border-color: #e6e8ea !important; }}

/* ── Streamlit native metrics — compact ─────────────────────────── */
[data-testid="stMetric"] {{ padding: 6px 8px !important; }}
[data-testid="stMetricLabel"] {{ font-size: 0.72rem !important; }}
[data-testid="stMetricValue"] {{ font-size: 1.1rem !important; }}

/* ── Expanders — tighter header ─────────────────────────────────── */
[data-testid="stExpander"] summary {{ padding: 6px 10px !important; font-size: 0.82rem !important; }}
[data-testid="stExpander"] [data-testid="stVerticalBlock"] {{ padding: 6px 4px !important; }}

/* ── Alerts / info / success / warning / error ──────────────────── */
[data-testid="stAlert"] {{ padding: 6px 12px !important; font-size: 0.82rem !important; margin: 2px 0 !important; }}

/* ── Buttons — slightly shorter ─────────────────────────────────── */
[data-testid="stButton"] > button {{ padding: 4px 14px !important; font-size: 0.82rem !important; }}

/* ── Selectbox / number_input / text_input labels ───────────────── */
[data-testid="stSelectbox"] label,
[data-testid="stNumberInput"] label,
[data-testid="stTextInput"] label,
[data-testid="stTextArea"] label,
[data-testid="stCheckbox"] label,
[data-testid="stMultiSelect"] label {{ font-size: 0.78rem !important; font-weight: 600 !important;
                                        margin-bottom: 1px !important; color: {T['text_secondary']} !important; }}

/* ── Input fields — compact height ──────────────────────────────── */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input {{ padding: 4px 8px !important; font-size: 0.82rem !important; }}
[data-testid="stSelectbox"] [data-baseweb="select"] {{ font-size: 0.82rem !important; }}

/* ── Captions ────────────────────────────────────────────────────── */
[data-testid="stCaptionContainer"] {{ font-size: 0.74rem !important; color: {T['text_secondary']} !important; }}

/* ── Compact dataframe rows ─────────────────────────────────────── */
[data-testid="stDataFrame"] .dvn-scroller [role="gridcell"],
[data-testid="stDataFrame"] .dvn-scroller [role="columnheader"] {{
    padding-top: 2px !important; padding-bottom: 2px !important;
    min-height: unset !important; line-height: 1.3 !important; font-size: 0.8rem !important;
}}

/* ── Top nav (Streamlit header) — hide deploy button clutter ────── */
[data-testid="stToolbar"] {{ display: none !important; }}
[data-testid="stDecoration"] {{ display: none !important; }}

/* ── Custom component classes ───────────────────────────────────── */
.metric-card {{
    background: {T['card_bg']}; border: 1px solid {T['card_border']};
    border-radius: 6px; padding: 0.3rem 0.75rem; text-align: center;
}}
.metric-val {{
    font-family: 'IBM Plex Mono', monospace; font-size: 1rem;
    font-weight: 600; color: {T['metric_val']}; margin: 0;
}}
.metric-label {{
    font-size: 0.64rem; color: {T['text_secondary']};
    text-transform: uppercase; letter-spacing: 0.07em; margin: 1px 0 0;
}}
.alert-box {{
    background: {T['alert_bg']}; border: 1px solid {T['alert_border']};
    border-radius: 6px; padding: 0.5rem 0.75rem; margin: 0.25rem 0;
    font-size: 0.82rem; color: {T['alert_text']};
}}
.score-hi  {{ color: {T['score_hi']}  !important; font-weight: 600; }}
.score-mid {{ color: {T['score_mid']} !important; font-weight: 600; }}
.score-lo  {{ color: {T['score_lo']}  !important; font-weight: 600; }}
.tag-sp   {{ background:{T['tag_sp_bg']};   color:{T['tag_sp_text']};   padding:1px 6px; border-radius:4px; font-size:0.72rem; }}
.tag-sb   {{ background:{T['tag_sb_bg']};   color:{T['tag_sb_text']};   padding:1px 6px; border-radius:4px; font-size:0.72rem; }}
.tag-auto {{ background:{T['tag_auto_bg']}; color:{T['tag_auto_text']}; padding:1px 6px; border-radius:4px; font-size:0.72rem; }}
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
    tab_ads, tab_sales, tab_inv, tab_profit, tab_amazon, tab_cashflow, tab_admin = st.tabs([
        "📣 Ads", "📈 Sales Dashboard", "📦 Inventory", "📦 Products",
        "🛒 Amazon Transactions", "💰 Cash Forecast", "⚙️ Admin"
    ])
else:
    tab_ads, tab_sales, tab_inv, tab_profit, tab_amazon, tab_cashflow = st.tabs([
        "📣 Ads", "📈 Sales Dashboard", "📦 Inventory", "📦 Products",
        "🛒 Amazon Transactions", "💰 Cash Forecast"
    ])
    tab_admin = None

# Analysis content moved into Ads tab below

# ══════════════════════════════════════════════════════════════════════════════
# TAB — INVENTORY
# ══════════════════════════════════════════════════════════════════════════════
with tab_inv:
    _inv_overview_tab, _inv_upload_tab, _inv_manual_tab, _inv_prod_tab, _inv_stock_tab, _inv_ship_tab, _inv_returns_tab = st.tabs([
        "📊 Overview", "📤 Upload Data", "✏️ Manual Entry", "📦 Productions", "🗂️ Stock to be Shipped", "🚢 Shipments", "↩️ Returns"
    ])

    # ── OVERVIEW ─────────────────────────────────────────────────────────────
    with _inv_overview_tab:
        st.markdown("# 📦 Inventory Overview")
        _cost_map_raw = get_cost_map_db()
        # get_inventory_overview expects {asin: landed_cost} not the full cost dict
        _cost_map_inv = {asin: v.get("landed_cost", 0) for asin, v in _cost_map_raw.items()}
        _avg_sales    = get_avg_daily_sales(days=30)
        _overview     = get_inventory_overview(_cost_map_inv, _avg_sales)

        # ── Compare to Sold form ─────────────────────────────────────────────
        st.markdown("#### 📊 Compare to Sold")
        _MARKETPLACES = ["amazon.com", "amazon.ca", "amazon.co.uk", "walmart.com"]
        _cs1, _cs2, _cs3, _cs4 = st.columns([2, 2, 2, 4])
        with _cs1:
            _sold_mkt = st.selectbox(
                "Marketplace", _MARKETPLACES, key="ovv_sold_mkt",
                label_visibility="visible",
            )
        with _cs2:
            _sold_start = st.date_input(
                "Start date",
                value=date.today().replace(day=1),
                key="ovv_sold_start",
            )
        with _cs3:
            _sold_end = st.date_input(
                "End date",
                value=date.today(),
                key="ovv_sold_end",
            )
        with _cs4:
            st.markdown("<div style='margin-top:28px'>", unsafe_allow_html=True)
            _show_sold = st.checkbox("Show Sold column", value=True, key="ovv_show_sold")
            st.markdown("</div>", unsafe_allow_html=True)

        if _overview.empty:
            st.info("No inventory data yet. Go to **Upload Data** or **Manual Entry** to add stock.")
        else:
            # ── Stock to Ship + per-draft-shipment columns ────────────────────
            _shp_data = get_overview_shipment_data()

            _STS_COL = "Stock to Ship"
            _overview[_STS_COL] = (
                _overview["asin"].map(_shp_data["stock_to_ship"]).fillna(0).astype(int)
            )

            _shipment_cols: list[str] = []
            for _shp in _shp_data["shipments"]:
                _sc = _shp["name"]
                _overview[_sc] = (
                    _overview["asin"].map(_shp["units"]).fillna(0).astype(int)
                )
                _shipment_cols.append(_sc)

            # ── Region grouping ───────────────────────────────────────────────
            # Map a free-text shipment destination to a region code.
            def _dest_region(dest: str) -> str:
                d  = (dest or "").lower().strip()
                # UK / EU — check first so "amazon.co.uk" doesn't match ".com"
                if d in ("uk", "gb", "united kingdom", "europe", "eu") or any(
                    x in d for x in ["amazon.co.uk", "amazon.de", "amazon.fr",
                                     "amazon.it", "amazon.es", "amazon.nl",
                                     "amazon.se", "amazon.pl",
                                     " uk", "united kingdom", " eu", "europe"]
                ):
                    return "UK"
                if d in ("ca", "canada") or any(
                    x in d for x in ["amazon.ca", " ca ", "canada"]
                ):
                    return "CA"
                if d in ("us", "usa", "united states") or any(
                    x in d for x in ["amazon.com", "walmart", " us", "usa", "united states"]
                ):
                    return "US"
                # fall back to "US" if blank / unrecognised
                return "US"

            # Build region → [col_name] mapping
            _REGION_BASE: dict[str, list[str]] = {
                "US": [],
                "CA": [],
                "UK": [],
                "China": [],
            }
            for _loc, _meta in LOCATIONS.items():
                if _loc == "PRODUCTION":
                    continue
                if _loc not in _overview.columns:
                    continue
                _col_label = _meta["label"]
                if _loc in ("FBA_US", "AWD_US"):
                    _REGION_BASE["US"].append(_col_label)
                elif _loc in ("FBA_CA",):
                    _REGION_BASE["CA"].append(_col_label)
                elif _loc in ("FBA_UK", "3PL_UK"):
                    _REGION_BASE["UK"].append(_col_label)
                elif _loc in ("AWD_CN", "WH_CN"):
                    _REGION_BASE["China"].append(_col_label)

            # Assign shipment columns to regions by destination
            for _shp in _shp_data["shipments"]:
                _rg = _dest_region(_shp["destination"])
                if _rg in _REGION_BASE:
                    _REGION_BASE[_rg].append(_shp["name"])
                else:
                    _REGION_BASE["US"].append(_shp["name"])  # safe default

            _REGION_LABELS = {
                "US":    "🇺🇸 US",
                "CA":    "🇨🇦 CA",
                "UK":    "🇬🇧 UK",
                "China": "🏭 China",
            }

            # ── Region toggle checkboxes ──────────────────────────────────────
            st.markdown(
                "<p style='font-size:0.8rem;color:#888;margin-bottom:2px;'>"
                "☑ Toggle regions included in <strong>Total</strong> &amp; <strong>Value $</strong> "
                "(unchecked columns are hidden)</p>",
                unsafe_allow_html=True,
            )
            _sts_checked = st.checkbox(_STS_COL, value=True, key="ovv_tog_sts",
                                       help="Unallocated production units ready to ship")

            _reg_cols_ui = st.columns(len(_REGION_BASE))
            _active_regions: set[str] = set()
            for _ri, (_rcode, _rcols) in enumerate(_REGION_BASE.items()):
                with _reg_cols_ui[_ri]:
                    _rlabel = _REGION_LABELS[_rcode]
                    _col_hint = ", ".join(_rcols) if _rcols else "—"
                    _checked  = st.checkbox(
                        _rlabel,
                        value=bool(_rcols),          # off by default if no columns
                        disabled=not _rcols,
                        key=f"ovv_tog_reg_{_rcode}",
                        help=f"Columns: {_col_hint}",
                    )
                    if _checked:
                        _active_regions.add(_rcode)

            # Expand active regions → active individual columns
            _active_cols: list[str] = []
            if _sts_checked:
                _active_cols.append(_STS_COL)
            for _rcode, _rcols in _REGION_BASE.items():
                if _rcode in _active_regions:
                    _active_cols.extend(_rcols)

            # All columns to show in the table (active only)
            _display_cols = ["asin", "title"]
            _counting_cols = []
            for _loc, _meta in LOCATIONS.items():
                if _loc == "PRODUCTION":
                    continue
                _col_label = _meta["label"]
                if _loc in _overview.columns:
                    _overview[_col_label] = _overview[_loc].fillna(0).astype(int)
                if _col_label in _active_cols:
                    _display_cols.append(_col_label)
                    _counting_cols.append(_col_label)
            if _sts_checked:
                _display_cols.append(_STS_COL)
                _counting_cols.append(_STS_COL)
            for _sc in _shipment_cols:
                if _sc in _active_cols:
                    _display_cols.append(_sc)
                    _counting_cols.append(_sc)

            # ── Total + Value based on active (checked) cols ──────────────────
            _avail_active = [c for c in _active_cols if c in _overview.columns]
            _overview["Total"] = (
                _overview[_avail_active].fillna(0).sum(axis=1).astype(int)
                if _avail_active else 0
            )

            # Image column
            _asin_img_map  = get_asin_image_map()
            _overview["Image"] = _overview["asin"].str.upper().map(_asin_img_map).fillna("")

            # Value $: build cost map from products_catalog (primary) +
            # product_costs landed_cost (override if available).
            _cat_cost_map  = get_asin_cost_map()                      # {ASIN: unit_cost}
            _prod_cost_map = {k.upper(): v.get("landed_cost", 0)
                              for k, v in _cost_map_raw.items() if v.get("landed_cost")}
            _unit_cost_map = {**_cat_cost_map, **_prod_cost_map}      # product_costs wins
            _overview["Value $"] = (
                _overview["asin"].str.upper().map(_unit_cost_map).fillna(0)
                * _overview["Total"]
            ).round(0).astype(int)

            # ── Sold column ───────────────────────────────────────────────────
            if _show_sold:
                _sold_map = get_sold_units(
                    _sold_mkt,
                    str(_sold_start),
                    str(_sold_end),
                )
                _overview["Sold"] = (
                    _overview["asin"].str.upper().map(
                        {k.upper(): v for k, v in _sold_map.items()}
                    ).fillna(0).astype(int)
                )

            # Only show active (checked) cols + always-visible cols
            _sold_cols = (["Sold"] if _show_sold and "Sold" in _overview.columns else [])
            _show_cols = (["Image", "asin", "title"]
                          + [c for c in _active_cols if c in _overview.columns]
                          + ["Total"]
                          + _sold_cols
                          + ["Value $"])

            # ── Totals row ────────────────────────────────────────────────────
            _skip     = {"ASIN", "Title", "asin", "title"}
            _num_cols = [c for c in _show_cols
                         if c not in _skip and pd.api.types.is_numeric_dtype(_overview[c])]
            _total_row = {c: "" for c in _show_cols}
            for _id_col in ("asin", "ASIN"):
                if _id_col in _total_row:
                    _total_row[_id_col] = "TOTAL"
            for c in _num_cols:
                _total_row[c] = int(_overview[c].fillna(0).sum())
            _display_df = pd.concat(
                [_overview[_show_cols], pd.DataFrame([_total_row])],
                ignore_index=True
            )

            def _color_total_row(row):
                if row.get("ASIN") == "TOTAL" or row.get("asin") == "TOTAL":
                    return ["background-color:#f0f0f0;font-weight:700"] * len(row)
                return [""] * len(row)

            _styler = _display_df.style.apply(_color_total_row, axis=1)
            st.dataframe(_styler, use_container_width=True, hide_index=True,
                         column_config={
                             "Image":   st.column_config.ImageColumn("", width=55),
                             "asin":    st.column_config.TextColumn("ASIN", width=115),
                             "title":   st.column_config.TextColumn("Product", width=200),
                             "Value $": st.column_config.NumberColumn(format="$%d"),
                         })

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

    # ── Editable Suppliers table (fixed-key pattern — no scroll reset) ───────────
    _SEST = "sup_state"
    _SEK  = "sup_ed"   # fixed editor key; never rotated between edits
    _sup_categories = ["mugs", "socks", "silicon", "other"]
    _sup_types      = ["Direct Manufacturer", "Agent / Intermediary"]

    if _SEST not in st.session_state:
        st.session_state[_SEST] = [
            {
                "_id":      s["id"],
                "Select":   False,
                "Name":     s.get("name") or "",
                "Category": s.get("category") or "",
                "Type":     "Direct Manufacturer" if s.get("is_manufacturer") else "Agent / Intermediary",
                "Contact":  s.get("contact_person") or "",
                "Email":    s.get("email") or "",
                "Tel":      s.get("tel") or "",
                "Address":  s.get("address") or "",
                "Notes":    s.get("notes") or "",
            }
            for s in _sup_list
        ]

    @st.fragment
    def _sup_editor_fragment():
        _df_rows = [{k: v for k, v in r.items() if k != "_id"}
                    for r in st.session_state[_SEST]]
        _SCHEMA_SUP = {
            "Select": pd.Series(dtype=bool), "Name": pd.Series(dtype=str),
            "Category": pd.Series(dtype=str), "Type": pd.Series(dtype=str),
            "Contact": pd.Series(dtype=str), "Email": pd.Series(dtype=str),
            "Tel": pd.Series(dtype=str), "Address": pd.Series(dtype=str),
            "Notes": pd.Series(dtype=str),
        }
        full_df = pd.DataFrame(_df_rows) if _df_rows else pd.DataFrame(_SCHEMA_SUP)

        st.data_editor(
            full_df,
            use_container_width=True,
            num_rows="dynamic",
            key=_SEK,
            hide_index=True,
            height=400,
            column_config={
                "Select":   st.column_config.CheckboxColumn("✔", default=False, width=50),
                "Name":     st.column_config.TextColumn("Name", width=180),
                "Category": st.column_config.SelectboxColumn("Category", options=_sup_categories, width=110),
                "Type":     st.column_config.SelectboxColumn("Type", options=_sup_types, width=190),
                "Contact":  st.column_config.TextColumn("Contact", width=140),
                "Email":    st.column_config.TextColumn("Email", width=180),
                "Tel":      st.column_config.TextColumn("Tel", width=120),
                "Address":  st.column_config.TextColumn("Address", width=200),
                "Notes":    st.column_config.TextColumn("Notes", width=180),
            },
        )

        # Compute effective state (base + current editor diffs) for button logic
        _diffs        = st.session_state.get(_SEK, {})
        _edit_map     = {int(k): v for k, v in (_diffs.get("edited_rows") or {}).items()}
        _deleted_base = set(_diffs.get("deleted_rows") or [])
        _added        = _diffs.get("added_rows") or []

        def _eff_sup_rows():
            _out = []
            for _i, _r in enumerate(st.session_state[_SEST]):
                if _i in _deleted_base:
                    continue
                _merged = dict(_r)
                if _i in _edit_map:
                    _merged.update(_edit_map[_i])
                _out.append(_merged)
            for _a in _added:
                _out.append({
                    "_id": None, "Select": bool(_a.get("Select", False)),
                    "Name":     str(_a.get("Name") or "").strip(),
                    "Category": str(_a.get("Category") or ""),
                    "Type":     str(_a.get("Type") or "Agent / Intermediary"),
                    "Contact":  str(_a.get("Contact") or ""),
                    "Email":    str(_a.get("Email") or ""),
                    "Tel":      str(_a.get("Tel") or ""),
                    "Address":  str(_a.get("Address") or ""),
                    "Notes":    str(_a.get("Notes") or ""),
                })
            return _out

        _selected = [r for r in _eff_sup_rows() if r.get("Select")]
        _sb1, _sb2, _ = st.columns([2, 2, 6])

        with _sb1:
            if st.button("💾 Save All", type="primary", key="sup_save_all"):
                try:
                    _rows_to_save = _eff_sup_rows()
                    _orig_ids = {r["_id"] for r in _rows_to_save if r.get("_id")}
                    _db_ids   = {s["id"] for s in get_suppliers()}
                    for _did in (_db_ids - _orig_ids):
                        delete_supplier(_did)
                    for _r in _rows_to_save:
                        if not str(_r.get("Name") or "").strip():
                            continue
                        upsert_supplier(
                            name=_r["Name"].strip(),
                            category=_r["Category"] or "other",
                            is_manufacturer=1 if _r["Type"] == "Direct Manufacturer" else 0,
                            notes=_r["Notes"].strip() or None,
                            address=_r["Address"].strip() or None,
                            contact_person=_r["Contact"].strip() or None,
                            email=_r["Email"].strip() or None,
                            tel=_r["Tel"].strip() or None,
                            supplier_id=_r.get("_id"),
                        )
                    st.session_state.pop(_SEST, None)
                    st.session_state.pop(_SEK, None)
                    st.success("✅ All suppliers saved.")
                    st.rerun()
                except Exception as _e:
                    st.error(f"Save failed: {_e}")

        with _sb2:
            if st.button("🗑️ Delete selected", disabled=(not _selected), key="sup_delete_sel"):
                _sel_ids = {r["_id"] for r in _selected if r.get("_id")}
                for _did in _sel_ids:
                    delete_supplier(_did)
                st.session_state.pop(_SEST, None)
                st.session_state.pop(_SEK, None)
                st.rerun()

    _sup_editor_fragment()

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

    # ── Editable Items table (fixed-key pattern — no scroll reset) ───────────────
    _IEST = "item_state"   # session_state key: list of row dicts (includes _id)
    _IEK  = "item_ed"      # fixed editor key; never rotated between edits

    def _isf(v, d=0.0):
        try:
            return float(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else d
        except (TypeError, ValueError):
            return d

    _item_currencies  = ["USD", "GBP", "EUR", "CAD", "AUD"]
    _sup_name_opts    = ["—"] + [s["name"] for s in _sup_list_for_items]
    _sup_ids_by_name  = {s["name"]: s["id"] for s in _sup_list_for_items}

    if _IEST not in st.session_state:
        st.session_state[_IEST] = [
            {
                "_id":         i["id"],
                "Select":      False,
                "Part ID":     i.get("part_id") or "",
                "Name":        i.get("name") or "",
                "Supplier":    i.get("supplier_name") or "—",
                "Mfg ($)":     float(i.get("manufacturer_cost") or 0),
                "Svc ($)":     float(i.get("service_cost") or 0),
                "Weight (g)":  float(i.get("net_weight_grams") or 0),
                "HS (NA)":     i.get("hst_code_na") or "",
                "HS (UK)":     i.get("hst_code_uk") or "",
                "Currency":    i.get("currency") or "USD",
                "Notes":       i.get("notes") or "",
            }
            for i in _item_list
        ]

    @st.fragment
    def _item_editor_fragment():
        _df_rows = []
        for _r in st.session_state[_IEST]:
            _row = {k: v for k, v in _r.items() if k != "_id"}
            _row["Total ($)"] = round(
                float(_row.get("Mfg ($)") or 0) + float(_row.get("Svc ($)") or 0), 2
            )
            _df_rows.append(_row)
        _SCHEMA_ITEM = {
            "Select": pd.Series(dtype=bool),
            "Part ID": pd.Series(dtype=str), "Name": pd.Series(dtype=str),
            "Supplier": pd.Series(dtype=str),
            "Mfg ($)": pd.Series(dtype=float), "Svc ($)": pd.Series(dtype=float),
            "Total ($)": pd.Series(dtype=float),
            "Weight (g)": pd.Series(dtype=float),
            "HS (NA)": pd.Series(dtype=str), "HS (UK)": pd.Series(dtype=str),
            "Currency": pd.Series(dtype=str), "Notes": pd.Series(dtype=str),
        }
        full_df = pd.DataFrame(_df_rows) if _df_rows else pd.DataFrame(_SCHEMA_ITEM)

        st.data_editor(
            full_df,
            use_container_width=True,
            num_rows="dynamic",
            key=_IEK,
            hide_index=True,
            height=740,
            disabled=["Total ($)"],
            column_config={
                "Select":     st.column_config.CheckboxColumn("✔", default=False, width=50),
                "Part ID":    st.column_config.TextColumn("Part ID", width=110),
                "Name":       st.column_config.TextColumn("Name", width=220),
                "Supplier":   st.column_config.SelectboxColumn("Supplier", options=_sup_name_opts, width=160),
                "Mfg ($)":    st.column_config.NumberColumn("Mfg ($)", format="$%.2f", min_value=0.0, width=90),
                "Svc ($)":    st.column_config.NumberColumn("Svc ($)", format="$%.2f", min_value=0.0, width=90),
                "Total ($)":  st.column_config.NumberColumn("Total ($)", format="$%.2f", width=95),
                "Weight (g)": st.column_config.NumberColumn("Weight (g)", format="%.1f", min_value=0.0, width=95),
                "HS (NA)":    st.column_config.TextColumn("HS (NA)", width=100),
                "HS (UK)":    st.column_config.TextColumn("HS (UK)", width=100),
                "Currency":   st.column_config.SelectboxColumn("Currency", options=_item_currencies, width=95),
                "Notes":      st.column_config.TextColumn("Notes", width=180),
            },
        )

        # Compute effective state (base + current editor diffs) for button logic
        _diffs        = st.session_state.get(_IEK, {})
        _edit_map     = {int(k): v for k, v in (_diffs.get("edited_rows") or {}).items()}
        _deleted_base = set(_diffs.get("deleted_rows") or [])
        _added        = _diffs.get("added_rows") or []

        def _eff_item_rows():
            _out = []
            for _i, _r in enumerate(st.session_state[_IEST]):
                if _i in _deleted_base:
                    continue
                _merged = dict(_r)
                if _i in _edit_map:
                    _merged.update(_edit_map[_i])
                _out.append(_merged)
            for _a in _added:
                _out.append({
                    "_id": None, "Select": bool(_a.get("Select", False)),
                    "Part ID":    str(_a.get("Part ID") or "").strip(),
                    "Name":       str(_a.get("Name") or "").strip(),
                    "Supplier":   str(_a.get("Supplier") or "—"),
                    "Mfg ($)":    _isf(_a.get("Mfg ($)")),
                    "Svc ($)":    _isf(_a.get("Svc ($)")),
                    "Weight (g)": _isf(_a.get("Weight (g)")),
                    "HS (NA)":    str(_a.get("HS (NA)") or ""),
                    "HS (UK)":    str(_a.get("HS (UK)") or ""),
                    "Currency":   str(_a.get("Currency") or "USD"),
                    "Notes":      str(_a.get("Notes") or ""),
                })
            return _out

        _selected = [r for r in _eff_item_rows() if r.get("Select")]
        _ib1, _ib2, _ = st.columns([2, 2, 6])

        with _ib1:
            if st.button("💾 Save All", type="primary", key="item_save_all"):
                try:
                    _rows_to_save = _eff_item_rows()
                    _orig_ids = {r["_id"] for r in _rows_to_save if r.get("_id")}
                    _db_ids   = {i["id"] for i in get_items()}
                    for _did in (_db_ids - _orig_ids):
                        delete_item(_did)
                    for _r in _rows_to_save:
                        if not str(_r.get("Name") or "").strip():
                            continue
                        _sup = _r.get("Supplier")
                        upsert_item(
                            data={
                                "part_id":           _r["Part ID"].strip() or None,
                                "name":              _r["Name"].strip(),
                                "item_type":         "other",
                                "supplier_id":       _sup_ids_by_name.get(_sup) if _sup and _sup != "—" else None,
                                "manufacturer_cost": _r["Mfg ($)"],
                                "service_cost":      _r["Svc ($)"],
                                "net_weight_grams":  _r["Weight (g)"] or None,
                                "hst_code_na":       _r["HS (NA)"].strip() or None,
                                "hst_code_uk":       _r["HS (UK)"].strip() or None,
                                "currency":          _r["Currency"] or "USD",
                                "notes":             _r["Notes"].strip() or None,
                            },
                            item_id=_r.get("_id"),
                        )
                    st.session_state.pop(_IEST, None)
                    st.session_state.pop(_IEK, None)
                    st.success("✅ All items saved.")
                    st.rerun()
                except Exception as _e:
                    st.error(f"Save failed: {_e}")

        with _ib2:
            if st.button("🗑️ Delete selected", disabled=(not _selected), key="item_delete_sel"):
                _sel_ids = {r["_id"] for r in _selected if r.get("_id")}
                for _did in _sel_ids:
                    delete_item(_did)
                st.session_state.pop(_IEST, None)
                st.session_state.pop(_IEK, None)
                st.rerun()

    _item_editor_fragment()

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

    # ── Editable products table (fixed-key pattern — no scroll reset) ────────────
    _CEST = "cat_state"   # session_state key: list of row dicts (includes _id)
    _CEK  = "cat_ed"      # fixed editor key; never rotated between edits

    def _sf(v, d=0.0):
        try:
            return float(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else d
        except (TypeError, ValueError):
            return d

    def _si(v, d=0):
        try:
            return int(_sf(v, d))
        except (TypeError, ValueError):
            return d

    _part_id_opts = ["—"] + [i["part_id"] for i in _items_for_cat if i.get("part_id")]

    if _CEST not in st.session_state:
        _rows = []
        for _cp in _cat_list:
            _bd = calc_product_cost(_cp["id"])
            _rows.append({
                "_id":        _cp["id"],
                "Select":     False,
                "Name":       _cp.get("name") or "",
                "ASIN":       _cp.get("asin") or "",
                "SKU":        _cp.get("sku")  or "",
                "UPC":        _cp.get("upc")  or "",
                "Type":       _cp.get("product_type") or "",
                "Part 1":     _cp.get("part_id_1") or "—",
                "Part 2":     _cp.get("part_id_2") or "—",
                "W (cm)":     _sf(_cp.get("width_cm")),
                "L (cm)":     _sf(_cp.get("length_cm")),
                "H (cm)":     _sf(_cp.get("height_cm")),
                "Ctn Units":  _si(_cp.get("carton_units")),
                "Ctn L":      _sf(_cp.get("carton_length_cm")),
                "Ctn W":      _sf(_cp.get("carton_width_cm")),
                "Ctn H":      _sf(_cp.get("carton_height_cm")),
                "NW (kg)":    _sf(_cp.get("carton_nw_kg")),
                "GW (kg)":    _sf(_cp.get("carton_gw_kg")),
                "CBM":        _sf(_cp.get("carton_cbm")),
                "New?":       bool(_cp.get("is_new_product", 0)),
                "Notes":      _cp.get("notes") or "",
                "Img URL":    _cp.get("image_url") or "",
                "Mfg ($)":    round(_sf(_bd.get("total_manufacturer", 0) if _bd else 0), 2),
                "Svc ($)":    round(_sf(_bd.get("total_service", 0) if _bd else 0), 2),
                "Total ($)":  round(
                    _sf(_bd.get("total_manufacturer", 0) if _bd else 0) +
                    _sf(_bd.get("total_service", 0) if _bd else 0), 2
                ),
                "Wt (g)":     round(_sf(_bd.get("total_weight_gr", 0) if _bd else 0), 1),
                "Landed ($)": round(_sf(_bd.get("landed_cost", 0) if _bd else 0), 2),
            })
        st.session_state[_CEST] = _rows

    @st.fragment
    def _cat_editor_fragment():
        _df_rows = [{k: v for k, v in r.items() if k != "_id"}
                    for r in st.session_state[_CEST]]
        _SCHEMA_CAT = {
            "Select": pd.Series(dtype=bool), "Name": pd.Series(dtype=str),
            "ASIN": pd.Series(dtype=str), "SKU": pd.Series(dtype=str),
            "UPC": pd.Series(dtype=str), "Type": pd.Series(dtype=str),
            "Part 1": pd.Series(dtype=str), "Part 2": pd.Series(dtype=str),
            "W (cm)": pd.Series(dtype=float), "L (cm)": pd.Series(dtype=float),
            "H (cm)": pd.Series(dtype=float), "Ctn Units": pd.Series(dtype=int),
            "Ctn L": pd.Series(dtype=float), "Ctn W": pd.Series(dtype=float),
            "Ctn H": pd.Series(dtype=float),
            "NW (kg)": pd.Series(dtype=float), "GW (kg)": pd.Series(dtype=float),
            "CBM": pd.Series(dtype=float), "New?": pd.Series(dtype=bool),
            "Notes": pd.Series(dtype=str), "Img URL": pd.Series(dtype=str),
            "Mfg ($)": pd.Series(dtype=float), "Svc ($)": pd.Series(dtype=float),
            "Total ($)": pd.Series(dtype=float),
            "Wt (g)": pd.Series(dtype=float), "Landed ($)": pd.Series(dtype=float),
        }
        full_df = pd.DataFrame(_df_rows) if _df_rows else pd.DataFrame(_SCHEMA_CAT)

        st.data_editor(
            full_df,
            use_container_width=True,
            num_rows="dynamic",
            key=_CEK,
            disabled=["Mfg ($)", "Svc ($)", "Total ($)", "Wt (g)", "Landed ($)"],
            hide_index=True,
            height=740,
            column_config={
                "Select":     st.column_config.CheckboxColumn("✔", default=False, width=50),
                "Name":       st.column_config.TextColumn("Name", width=200),
                "ASIN":       st.column_config.TextColumn("ASIN", width=110),
                "SKU":        st.column_config.TextColumn("SKU", width=110),
                "UPC":        st.column_config.TextColumn("UPC", width=110),
                "Type":       st.column_config.SelectboxColumn("Type", options=_prod_types, width=140),
                "Part 1":     st.column_config.SelectboxColumn("Part 1", options=_part_id_opts, width=130),
                "Part 2":     st.column_config.SelectboxColumn("Part 2", options=_part_id_opts, width=130),
                "W (cm)":     st.column_config.NumberColumn("W (cm)", format="%.1f", width=75),
                "L (cm)":     st.column_config.NumberColumn("L (cm)", format="%.1f", width=75),
                "H (cm)":     st.column_config.NumberColumn("H (cm)", format="%.1f", width=75),
                "Ctn Units":  st.column_config.NumberColumn("Ctn Units", step=1, width=90),
                "Ctn L":      st.column_config.NumberColumn("Ctn L", format="%.1f", width=70),
                "Ctn W":      st.column_config.NumberColumn("Ctn W", format="%.1f", width=70),
                "Ctn H":      st.column_config.NumberColumn("Ctn H", format="%.1f", width=70),
                "NW (kg)":    st.column_config.NumberColumn("NW (kg)", format="%.3f", width=80),
                "GW (kg)":    st.column_config.NumberColumn("GW (kg)", format="%.3f", width=80),
                "CBM":        st.column_config.NumberColumn("CBM", format="%.4f", width=75),
                "New?":       st.column_config.CheckboxColumn("New?", width=60),
                "Notes":      st.column_config.TextColumn("Notes", width=160),
                "Img URL":    st.column_config.TextColumn("Image URL", width=200,
                                  help="Paste any image URL. Leave blank to use Amazon CDN auto-URL."),
                "Mfg ($)":    st.column_config.NumberColumn("Mfg ($)", format="$%.2f", width=90),
                "Svc ($)":    st.column_config.NumberColumn("Svc ($)", format="$%.2f", width=90),
                "Total ($)":  st.column_config.NumberColumn("Total ($)", format="$%.2f", width=95),
                "Wt (g)":     st.column_config.NumberColumn("Wt (g)", format="%.0f", width=70),
                "Landed ($)": st.column_config.NumberColumn("Landed ($)", format="$%.2f", width=95),
            },
        )

        # Compute effective state (base + current editor diffs) for button logic
        _diffs        = st.session_state.get(_CEK, {})
        _edit_map     = {int(k): v for k, v in (_diffs.get("edited_rows") or {}).items()}
        _deleted_base = set(_diffs.get("deleted_rows") or [])
        _added        = _diffs.get("added_rows") or []

        def _eff_cat_rows():
            _out = []
            for _i, _r in enumerate(st.session_state[_CEST]):
                if _i in _deleted_base:
                    continue
                _merged = dict(_r)
                if _i in _edit_map:
                    _merged.update(_edit_map[_i])
                _out.append(_merged)
            for _a in _added:
                _out.append({
                    "_id": None, "Select": bool(_a.get("Select", False)),
                    "Name":      str(_a.get("Name") or "").strip(),
                    "ASIN":      str(_a.get("ASIN") or "").strip(),
                    "SKU":       str(_a.get("SKU")  or "").strip(),
                    "UPC":       str(_a.get("UPC")  or "").strip(),
                    "Type":      str(_a.get("Type") or ""),
                    "Part 1":    str(_a.get("Part 1") or "—"),
                    "Part 2":    str(_a.get("Part 2") or "—"),
                    "W (cm)":    _sf(_a.get("W (cm)")),
                    "L (cm)":    _sf(_a.get("L (cm)")),
                    "H (cm)":    _sf(_a.get("H (cm)")),
                    "Ctn Units": _si(_a.get("Ctn Units")),
                    "Ctn L":     _sf(_a.get("Ctn L")),
                    "Ctn W":     _sf(_a.get("Ctn W")),
                    "Ctn H":     _sf(_a.get("Ctn H")),
                    "NW (kg)":   _sf(_a.get("NW (kg)")),
                    "GW (kg)":   _sf(_a.get("GW (kg)")),
                    "CBM":       _sf(_a.get("CBM")),
                    "New?":      bool(_a.get("New?", False)),
                    "Notes":     str(_a.get("Notes") or ""),
                    "Img URL":   str(_a.get("Img URL") or ""),
                    "Mfg ($)": 0.0, "Svc ($)": 0.0, "Total ($)": 0.0,
                    "Wt (g)": 0.0, "Landed ($)": 0.0,
                })
            return _out

        _selected = [r for r in _eff_cat_rows() if r.get("Select")]
        _bc1, _bc2, _bc3, _ = st.columns([2, 2, 2, 4])

        with _bc1:
            if st.button("💾 Save All", type="primary", key="cat_save_all"):
                try:
                    _rows_to_save = _eff_cat_rows()
                    _orig_ids = {r["_id"] for r in _rows_to_save if r.get("_id")}
                    _db_ids   = {p["id"] for p in get_products_catalog()}
                    for _did in (_db_ids - _orig_ids):
                        delete_product_catalog(_did)
                    for _r in _rows_to_save:
                        if not str(_r.get("Name") or "").strip():
                            continue
                        _p1, _p2 = _r.get("Part 1"), _r.get("Part 2")
                        upsert_product_catalog(
                            data={
                                "asin":             _r["ASIN"].strip().upper() or None,
                                "sku":              _r["SKU"].strip() or None,
                                "upc":              _r["UPC"].strip() or None,
                                "name":             _r["Name"].strip(),
                                "product_type":     _r["Type"] or None,
                                "width_cm":         _r["W (cm)"] or None,
                                "length_cm":        _r["L (cm)"] or None,
                                "height_cm":        _r["H (cm)"] or None,
                                "is_new_product":   1 if _r.get("New?") else 0,
                                "notes":            _r["Notes"].strip() or None,
                                "carton_units":     _r["Ctn Units"] or None,
                                "carton_length_cm": _r["Ctn L"] or None,
                                "carton_width_cm":  _r["Ctn W"] or None,
                                "carton_height_cm": _r["Ctn H"] or None,
                                "carton_nw_kg":     _r["NW (kg)"] or None,
                                "carton_gw_kg":     _r["GW (kg)"] or None,
                                "carton_cbm":       _r["CBM"] or None,
                                "part_id_1": _p1 if _p1 and _p1 != "—" else None,
                                "part_id_2": _p2 if _p2 and _p2 != "—" else None,
                                "image_url": _r.get("Img URL", "").strip() or None,
                            },
                            product_id=_r.get("_id"),
                        )
                    st.session_state.pop(_CEST, None)
                    st.session_state.pop(_CEK, None)
                    st.success("✅ All products saved.")
                    st.rerun()
                except Exception as _e:
                    st.error(f"Save failed: {_e}")

        with _bc2:
            if st.button("📋 Duplicate selected", disabled=(not _selected), key="cat_duplicate"):
                _eff = _eff_cat_rows()
                _new_rows = []
                for _r in _eff:
                    if _r.get("Select"):
                        _dup = dict(_r)
                        _dup.update({"_id": None, "ASIN": "", "SKU": "", "Name": "", "Select": False})
                        _new_rows.append(_dup)
                # Rebuild _CEST from effective state (deselected) + duplicates
                _base = [{**_r, "Select": False} for _r in _eff]
                st.session_state[_CEST] = _base + _new_rows
                st.session_state.pop(_CEK, None)
                st.rerun()

        with _bc3:
            if st.button("🗑️ Delete selected", disabled=(not _selected), key="cat_delete_sel"):
                _sel_ids = {r["_id"] for r in _selected if r.get("_id")}
                for _did in _sel_ids:
                    delete_product_catalog(_did)
                st.session_state.pop(_CEST, None)
                st.session_state.pop(_CEK, None)
                st.rerun()

    _cat_editor_fragment()

    # ── Auto-fetch product images ──────────────────────────────────────────────
    st.divider()
    _img_col1, _img_col2 = st.columns([3, 7])
    with _img_col1:
        if st.button("🔍 Fetch Missing Product Images", key="cat_fetch_images",
                     help="Visits each Amazon listing and saves the product image URL. "
                          "Only runs for products without a stored image URL."):
            with st.spinner("Fetching images from Amazon listings…"):
                _img_results = fetch_and_store_all_images()
            _img_ok  = [a for a, v in _img_results.items() if v != "NOT_FOUND"]
            _img_err = [a for a, v in _img_results.items() if v == "NOT_FOUND"]
            if not _img_results:
                st.info("All products already have image URLs — nothing to fetch.")
            else:
                if _img_ok:
                    st.success(f"✅ Fetched images for {len(_img_ok)} product(s): {', '.join(_img_ok)}")
                if _img_err:
                    st.warning(f"⚠️ Could not find image for: {', '.join(_img_err)}. Paste the URL manually in the Image URL column.")
            st.session_state.pop(_CEST, None)   # refresh catalog view
            st.rerun()
    with _img_col2:
        st.caption(
            "Retrieves the real Amazon image URL for each product (from the listing page) "
            "and saves it permanently. After fetching, images will appear in Overview and Returns tables.  \n"
            "You can also paste a URL directly in the **Image URL** column of the table above."
        )

    # ── Cost Breakdown ─────────────────────────────────────────────────────────
    if _cat_list:
        st.divider()
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

    # ── Post-action messages ───────────────────────────────────────────────────
    if st.session_state["catalog_delete_msg"]:
        st.success(st.session_state["catalog_delete_msg"])
        st.session_state["catalog_delete_msg"] = None

    st.divider()
    st.markdown("#### 🗑️ Delete All Products")
    _del_all_confirm = st.checkbox("Confirm — delete ALL products", key="del_all_cat_confirm")
    if st.button("🗑️ Delete All Products", disabled=not _del_all_confirm, type="primary", key="del_all_cat_btn"):
        try:
            delete_all_products_catalog()
            st.session_state.pop(_CEST, None)
            st.session_state.pop(_CEK, None)
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
        # ── Anomaly detection ─────────────────────────────────────────────
        _fba_anomalies = get_pick_pack_anomalies()
        if not _fba_anomalies.empty:
            _fba_img_map = get_asin_image_map()
            with st.expander(
                f"⚠️ Pick & Pack discrepancies — {len(_fba_anomalies)} ASIN(s) may need remeasurement",
                expanded=True,
            ):
                st.caption(
                    "These ASINs have a Pick & Pack fee that differs from the most common fee "
                    "for their size tier on that marketplace. Open an Amazon case to request remeasurement."
                )
                _anom_display = _fba_anomalies.copy()
                _anom_display.insert(0, "Image",
                    _anom_display["asin"].str.upper().map(_fba_img_map).fillna(""))
                st.dataframe(
                    _anom_display.rename(columns={
                        "asin":                        "ASIN",
                        "marketplace":                 "Marketplace",
                        "size_tier":                   "Size Tier",
                        "pick_pack_fee":               "Their Fee ($)",
                        "expected_fee":                "Expected Fee ($)",
                        "currency":                    "Currency",
                        "total_in_marketplace_tier":   "ASINs in Group",
                    }),
                    column_config={
                        "Image": st.column_config.ImageColumn("Image", width=60),
                    },
                    use_container_width=True,
                    hide_index=True,
                )

        st.divider()

        # ── Marketplace filter ────────────────────────────────────────────
        _fba_mps = ["All"] + sorted(_fba_df["marketplace"].dropna().unique().tolist())
        _fba_mp_sel = st.selectbox("Marketplace", _fba_mps, key="fba_mp_filter")
        if _fba_mp_sel != "All":
            _fba_df = _fba_df[_fba_df["marketplace"] == _fba_mp_sel]

        # Attach product images from catalog
        _fba_img_map = get_asin_image_map()
        _fba_df = _fba_df.copy()
        _fba_df.insert(0, "Image", _fba_df["asin"].str.upper().map(_fba_img_map).fillna(""))

        st.markdown(f"**{len(_fba_df):,} rows** · last updated from Fee Preview report")
        st.dataframe(
            _fba_df.rename(columns={
                "asin":          "ASIN",
                "marketplace":   "Marketplace",
                "size_tier":     "Size Tier",
                "pick_pack_fee": "Pick & Pack ($)",
                "referral_fee":  "Referral ($)",
                "currency":      "Currency",
                "updated_at":    "Updated",
            }),
            column_config={
                "Image": st.column_config.ImageColumn("Image", width=60),
            },
            use_container_width=True,
            hide_index=True,
        )

# ══════════════════════════════════════════════════════════════════════════════
# TAB — PRODUCTION
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# INVENTORY SUB-TAB — PRODUCTIONS
# ══════════════════════════════════════════════════════════════════════════════
with _inv_prod_tab:
    st.markdown(
        f"<p style='font-size:1.1rem;font-weight:700;margin:0 0 4px;'>🏭 Productions &nbsp;"
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
        st.session_state.pop("prod_lines_state_new", None)
        st.session_state.pop("prod_lines_ed_new", None)

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
            sku_info              = get_sku_catalog_info()
            sku_supplier_map      = get_sku_supplier_map()
            sku_supplier_cost_map = get_sku_supplier_cost_map()

            def _make_full_row(sku, num_cartons):
                info = sku_info.get(sku or "", {})
                cu    = info.get("carton_units", 0) or 0
                units = cu * num_cartons
                prod_cost = round(info.get("unit_mfg", 0.0) * units, 2)
                svc_cost  = round(info.get("unit_svc", 0.0) * units, 2)
                return {
                    "SKU":               sku or "",
                    "# Cartons":         num_cartons,
                    "Product":           info.get("name", "") if sku else "",
                    "# Units":           units,
                    "Product Cost ($)":  prod_cost,
                    "Service Cost ($)":  svc_cost,
                    "Total Cost ($)":    round(prod_cost + svc_cost, 2),
                    "Net Weight (kg)":   round(info.get("nw_kg", 0.0) * num_cartons, 2),
                    "Gross Weight (kg)": round(info.get("gw_kg", 0.0) * num_cartons, 2),
                    "CBM":               round(info.get("cbm",  0.0) * num_cartons, 3),
                }

            _state_key = f"prod_lines_state_{ctx}"
            _ek        = f"prod_lines_ed_{ctx}"   # fixed key — never rotated

            def _safe_int(v):
                """int() that returns 0 for None / NaN / bad values."""
                try:
                    return 0 if v is None or (isinstance(v, float) and pd.isna(v)) else int(v)
                except (TypeError, ValueError):
                    return 0

            # Initialise state from DB on first load (or after Save/Delete clears it)
            if _state_key not in st.session_state:
                if sel_prod:
                    _db_lines = get_production_lines(sel_prod["id"])
                    st.session_state[_state_key] = [
                        {"SKU": ln["sku"] or "", "# Cartons": int(ln["num_cartons"] or 0)}
                        for ln in _db_lines
                    ]
                else:
                    st.session_state[_state_key] = []

            # ── Fixed-key, stable-data pattern ────────────────────────────────
            # Root cause of "table refreshes": every time full_df changes,
            # Streamlit sends new Arrow data to the frontend and the data_editor
            # component re-renders — even with the same widget key.
            #
            # Solution: keep full_df constant for cell edits.
            #   • edited_rows (SKU / # Cartons changes) → do NOT absorb into
            #     _state_key.  full_df stays identical → no Arrow update sent →
            #     component does not re-render → no scroll reset.
            #   • added_rows / deleted_rows (structural) → absorb into
            #     _state_key, pop the key, let editor re-init from new full_df.
            #     One reset per structural change is acceptable.
            #
            # Totals + Save read from _eff_rows() = base state merged with
            # current editor diffs, so they are always accurate.
            _diffs    = st.session_state.get(_ek, {})
            _edit_map = {int(k): v for k, v in (_diffs.get("edited_rows") or {}).items()}
            _del_base = set(_diffs.get("deleted_rows") or [])
            _added    = _diffs.get("added_rows") or []

            # Trigger re-render when # Cartons or SKU changes (not just add/delete)
            # so computed columns (Units, Cost, Weight) update as soon as user leaves the cell
            _has_data_change = any(
                "# Cartons" in chg or "SKU" in chg
                for chg in _edit_map.values()
            )

            if _del_base or _added or _has_data_change:
                # Absorb all edits into stable state and reset editor so full_df rebuilds
                _s = {i: dict(r) for i, r in enumerate(st.session_state[_state_key])
                      if i not in _del_base}
                for _ri, _chg in _edit_map.items():
                    if _ri in _s:
                        _s[_ri].update({k: v for k, v in _chg.items()
                                        if k in ("SKU", "# Cartons")})
                _merged = [_s[k] for k in sorted(_s)]
                for _a in _added:
                    _merged.append({
                        "SKU":       str(_a.get("SKU") or "").strip(),
                        "# Cartons": _safe_int(_a.get("# Cartons")),
                    })
                st.session_state[_state_key] = _merged
                st.session_state.pop(_ek, None)   # deletion allowed by Streamlit
                _edit_map = {}                    # diffs are now in _state_key

            # Build full_df from the STABLE base state.
            # For cell edits this is identical to the previous render →
            # Streamlit sends no Arrow update → component doesn't re-render.
            _cur_list = st.session_state[_state_key]

            # Build sorted list of unique suppliers for SKUs in this production
            _prod_sups: list[str] = []
            for _r in _cur_list:
                for _sup in sku_supplier_map.get(_r.get("SKU") or "", []):
                    if _sup and _sup not in _prod_sups:
                        _prod_sups.append(_sup)
            _prod_sups.sort()

            _SCHEMA = {
                "Select": pd.Series(dtype=bool),
                "SKU": pd.Series(dtype=str), "# Cartons": pd.Series(dtype=int),
                "Product": pd.Series(dtype=str), "# Units": pd.Series(dtype=int),
                "Product Cost ($)": pd.Series(dtype=float),
                "Service Cost ($)": pd.Series(dtype=float),
                "Total Cost ($)": pd.Series(dtype=float),
                "Net Weight (kg)": pd.Series(dtype=float),
                "Gross Weight (kg)": pd.Series(dtype=float),
                "CBM": pd.Series(dtype=float),
            }
            _full_rows = [{"Select": False, **_make_full_row(r["SKU"], _safe_int(r.get("# Cartons")))}
                          for r in _cur_list]
            full_df = pd.DataFrame(_full_rows) if _full_rows else pd.DataFrame(_SCHEMA)

            _COMPUTED = ["Product", "# Units", "Product Cost ($)", "Service Cost ($)",
                         "Total Cost ($)", "Net Weight (kg)", "Gross Weight (kg)", "CBM"]

            # ── Supplier filter ────────────────────────────────────────────────
            if _prod_sups:
                _sup_filter = st.selectbox(
                    "View by supplier",
                    options=["All suppliers"] + _prod_sups,
                    key=f"prod_sup_filter_{ctx}",
                )
            else:
                _sup_filter = "All suppliers"

            if _sup_filter == "All suppliers":
                # Full editable table
                st.caption("Click + (bottom-left) to add a row · select a row and press Delete/Backspace to remove it")
                st.data_editor(
                    full_df,
                    use_container_width=True,
                    num_rows="dynamic",
                    key=_ek,
                    disabled=_COMPUTED,
                    height=740,
                    column_config={
                        "Select":            st.column_config.CheckboxColumn("✔", default=False, width=50),
                        "SKU":               st.column_config.SelectboxColumn("SKU", options=all_skus, required=True, width=180),
                        "# Cartons":         st.column_config.NumberColumn("# Cartons", min_value=0, step=1, width=110),
                        "Product":           st.column_config.TextColumn("Product", width=200),
                        "# Units":           st.column_config.NumberColumn("# Units", width=85),
                        "Product Cost ($)":  st.column_config.NumberColumn("Product Cost ($)", format="$%.2f", width=130),
                        "Service Cost ($)":  st.column_config.NumberColumn("Service Cost ($)", format="$%.2f", width=125),
                        "Total Cost ($)":    st.column_config.NumberColumn("Total Cost ($)", format="$%.2f", width=120),
                        "Net Weight (kg)":   st.column_config.NumberColumn("Net Weight (kg)", format="%.2f kg", width=120),
                        "Gross Weight (kg)": st.column_config.NumberColumn("Gross Weight (kg)", format="%.2f kg", width=130),
                        "CBM":               st.column_config.NumberColumn("CBM", format="%.3f", width=75),
                    },
                )
                # Rows the user has ticked — read from editor diffs (stable-data pattern:
                # Select lives in edited_rows, not in _state_key or full_df)
                _selected_idxs = {i for i, chg in _edit_map.items()
                                   if chg.get("Select", False)}
            else:
                # Read-only filtered view for selected supplier
                _flt_schema = {
                    "SKU": pd.Series(dtype=str),
                    "Product": pd.Series(dtype=str),
                    "# Cartons": pd.Series(dtype=int),
                    "# Units": pd.Series(dtype=int),
                    "Product Cost ($)": pd.Series(dtype=float),
                    "Service Cost ($)": pd.Series(dtype=float),
                    "Total Cost ($)": pd.Series(dtype=float),
                    "Net Weight (kg)": pd.Series(dtype=float),
                    "Gross Weight (kg)": pd.Series(dtype=float),
                    "CBM": pd.Series(dtype=float),
                }
                _flt_rows = []
                for _row in _full_rows:
                    _sku = _row.get("SKU") or ""
                    if _sup_filter not in sku_supplier_map.get(_sku, []):
                        continue
                    _sc     = sku_supplier_cost_map.get(_sku, {}).get(_sup_filter, {})
                    _nc     = _row["# Cartons"]
                    _cu     = (sku_info.get(_sku, {}).get("carton_units") or 0)
                    _nu     = _cu * _nc
                    _u_mfg  = _sc.get("unit_mfg",   0.0)
                    _u_svc  = _sc.get("unit_svc",   0.0)
                    _u_nw   = _sc.get("unit_nw_kg", 0.0)
                    _flt_pc = round(_u_mfg * _nu, 2)
                    _flt_sc = round(_u_svc * _nu, 2)
                    _flt_rows.append({
                        "SKU":                _sku,
                        "Product":            _row["Product"],
                        "# Cartons":          _nc,
                        "# Units":            _nu,
                        "Product Cost ($)":   _flt_pc,
                        "Service Cost ($)":   _flt_sc,
                        "Total Cost ($)":     round(_flt_pc + _flt_sc, 2),
                        "Net Weight (kg)":    round(_u_nw  * _nu, 2),
                        "Gross Weight (kg)":  0.0,
                        "CBM":                0.0,
                    })
                _flt_df = pd.DataFrame(_flt_rows) if _flt_rows else pd.DataFrame(_flt_schema)
                st.caption(f"Showing SKUs supplied by **{_sup_filter}** (read-only · switch to 'All suppliers' to edit)")
                st.dataframe(
                    _flt_df,
                    use_container_width=True,
                    height=740,
                    hide_index=True,
                    column_config={
                        "Product Cost ($)":  st.column_config.NumberColumn(format="$%.2f"),
                        "Service Cost ($)":  st.column_config.NumberColumn(format="$%.2f"),
                        "Total Cost ($)":    st.column_config.NumberColumn(format="$%.2f"),
                        "Net Weight (kg)":   st.column_config.NumberColumn(format="%.2f kg"),
                        "Gross Weight (kg)": st.column_config.NumberColumn(format="%.2f kg"),
                        "CBM":               st.column_config.NumberColumn(format="%.3f"),
                    },
                )
                _selected_idxs = set()   # no selection in filtered view

            # Effective rows = base state merged with any unsaved cell edits.
            # Used for totals display and Save — always reflects what the user sees.
            def _eff_rows():
                _out = []
                for _i, _r in enumerate(st.session_state[_state_key]):
                    _m = dict(_r)
                    if _i in _edit_map:
                        _m.update({k: v for k, v in _edit_map[_i].items()
                                   if k in ("SKU", "# Cartons")})
                    _out.append(_m)
                return _out

            # Totals — computed from live effective rows, always accurate.
            # When a supplier filter is active, show item-level subtotal for that
            # supplier only (costs/NW from their items; GW and CBM are zeroed).
            _tot_cartons = _tot_units = 0
            _tot_prod = _tot_svc = _tot_nw = _tot_gw = _tot_cbm = 0.0
            _is_filtered = (_sup_filter != "All suppliers")
            for _r in _eff_rows():
                _sku = _r["SKU"]
                if not _sku:
                    continue
                # Skip rows not belonging to the selected supplier
                if _is_filtered and _sup_filter not in sku_supplier_map.get(_sku, []):
                    continue
                _nc   = _safe_int(_r.get("# Cartons"))
                _info = sku_info.get(_sku, {})
                _cu   = _info.get("carton_units", 0) or 0
                _u    = _cu * _nc
                _tot_cartons += _nc
                _tot_units   += _u
                if _is_filtered:
                    # Use item-level costs/NW for this supplier
                    _sc = sku_supplier_cost_map.get(_sku, {}).get(_sup_filter, {})
                    _tot_prod += _sc.get("unit_mfg",   0.0) * _u
                    _tot_svc  += _sc.get("unit_svc",   0.0) * _u
                    _tot_nw   += _sc.get("unit_nw_kg", 0.0) * _u
                    # GW and CBM are whole-carton attributes — not split by supplier
                else:
                    _tot_prod += _info.get("unit_mfg", 0.0) * _u
                    _tot_svc  += _info.get("unit_svc", 0.0) * _u
                    _tot_nw   += _info.get("nw_kg", 0.0) * _nc
                    _tot_gw   += _info.get("gw_kg", 0.0) * _nc
                    _tot_cbm  += _info.get("cbm",   0.0) * _nc

            if _tot_cartons:
                if _is_filtered:
                    _tot_label = f"**📊 {_sup_filter.upper()} SUBTOTAL**"
                    _tot_line  = (
                        f"{_tot_label} · "
                        f"{_tot_cartons} cartons · "
                        f"{_tot_units} units · "
                        f"\\${_tot_prod:.2f} prod cost · "
                        f"\\${_tot_svc:.2f} svc cost · "
                        f"\\${_tot_prod + _tot_svc:.2f} total cost · "
                        f"{_tot_nw:.2f} kg NW"
                    )
                else:
                    _tot_line = (
                        f"**📊 TOTAL** · "
                        f"{_tot_cartons} cartons · "
                        f"{_tot_units} units · "
                        f"\\${_tot_prod:.2f} prod cost · "
                        f"\\${_tot_svc:.2f} svc cost · "
                        f"\\${_tot_prod + _tot_svc:.2f} total cost · "
                        f"{_tot_nw:.2f} kg NW · "
                        f"{_tot_gw:.2f} kg GW · "
                        f"{_tot_cbm:.3f} CBM"
                    )
                st.markdown(_tot_line)

            # ── Buttons ────────────────────────────────────────────────────────
            _sb1, _sb2, _sb3, _ = st.columns([2, 2, 2, 4])
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
                                [r for r in _eff_rows() if r["SKU"]],
                            )
                            # Clear state so next load re-reads fresh from DB
                            st.session_state.pop(_state_key, None)
                            st.session_state.pop(_ek, None)
                            st.session_state["prod_saved_id"] = prod_id
                            st.success(f"✅ '{prod_name.strip()}' saved.")
                            st.rerun()
                        except Exception as _pe:
                            st.error(f"Save failed: {_pe}")

            with _sb2:
                if st.button("🗑️ Delete Selected",
                             disabled=not _selected_idxs,
                             key=f"prod_del_sel_{ctx}"):
                    st.session_state[_state_key] = [
                        r for i, r in enumerate(st.session_state[_state_key])
                        if i not in _selected_idxs
                    ]
                    st.session_state.pop(_ek, None)   # structural change → reset editor
                    st.rerun()

            with _sb3:
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
                        st.session_state.pop(_state_key, None)
                        st.session_state.pop(_ek, None)
                        st.rerun()
                with _dc2:
                    if st.button("Cancel", key=f"prod_del_no_{ctx}"):
                        st.session_state.pop(f"prod_del_confirm_{ctx}", None)
                        st.rerun()

    with _pcol_form:
        _prod_form(_sel_prod, _all_skus)


# ══════════════════════════════════════════════════════════════════════════════
# INVENTORY SUB-TAB 2 — STOCK TO BE SHIPPED
# ══════════════════════════════════════════════════════════════════════════════
with _inv_stock_tab:
    st.markdown(
        f"<p style='font-size:1.1rem;font-weight:700;margin:0 0 4px;'>🗂️ Stock to be Shipped &nbsp;"
        f"<span style='font-size:0.78rem;font-weight:400;color:{T['text_secondary']};'>"
        f"Unallocated cartons across all productions (excludes all shipments, draft and shipped).</span></p>",
        unsafe_allow_html=True,
    )

    _stock_rows = get_stock_to_be_shipped()

    if not _stock_rows:
        st.info("No stock available. Add productions with SKUs first.")
    else:
        # Strip internal _ fields before display
        _stock_display = [{k: v for k, v in r.items() if not k.startswith("_")}
                          for r in _stock_rows]
        _stock_df = pd.DataFrame(_stock_display)

        # Totals row
        _s_tot_cartons = sum(r["Available Cartons"] for r in _stock_rows)
        _s_tot_units   = sum(r["# Units"] for r in _stock_rows)
        _s_tot_prod    = sum(r["Product Cost ($)"] for r in _stock_rows)
        _s_tot_svc     = sum(r["Service Cost ($)"] for r in _stock_rows)
        _s_tot_nw      = sum(r["Net Weight (kg)"] for r in _stock_rows)
        _s_tot_gw      = sum(r["Gross Weight (kg)"] for r in _stock_rows)
        _s_tot_cbm     = sum(r["CBM"] for r in _stock_rows)

        st.dataframe(
            _stock_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "SKU":               st.column_config.TextColumn("SKU", width=120),
                "Product":           st.column_config.TextColumn("Product", width=200),
                "Available Cartons": st.column_config.NumberColumn("Available Cartons", width=130),
                "# Units":           st.column_config.NumberColumn("# Units", width=85),
                "Product Cost ($)":  st.column_config.NumberColumn("Product Cost ($)", format="$%.2f", width=130),
                "Service Cost ($)":  st.column_config.NumberColumn("Service Cost ($)", format="$%.2f", width=125),
                "Net Weight (kg)":   st.column_config.NumberColumn("Net Weight (kg)", format="%.2f kg", width=120),
                "Gross Weight (kg)": st.column_config.NumberColumn("Gross Weight (kg)", format="%.2f kg", width=130),
                "CBM":               st.column_config.NumberColumn("CBM", format="%.3f", width=75),
            },
        )
        st.markdown(
            f"**📊 TOTAL** · {_s_tot_cartons} cartons · {_s_tot_units} units · "
            f"\\${_s_tot_prod:.2f} prod cost · \\${_s_tot_svc:.2f} svc cost · "
            f"{_s_tot_nw:.2f} kg NW · {_s_tot_gw:.2f} kg GW · {_s_tot_cbm:.3f} CBM"
        )


# ══════════════════════════════════════════════════════════════════════════════
# INVENTORY SUB-TAB 3 — SHIPMENTS
# ══════════════════════════════════════════════════════════════════════════════
with _inv_ship_tab:
    st.markdown(
        f"<p style='font-size:1.1rem;font-weight:700;margin:0 0 4px;'>🚢 Shipments &nbsp;"
        f"<span style='font-size:0.78rem;font-weight:400;color:{T['text_secondary']};'>"
        f"Create shipments and allocate cartons from available stock.</span></p>",
        unsafe_allow_html=True,
    )

    _all_shipments  = get_shipments()
    _ship_names     = [s["name"] for s in _all_shipments]
    _ship_col_list, _ship_col_form = st.columns([1, 3], gap="large")

    with _ship_col_list:
        st.markdown("""<div id="ship-list-nav-marker"></div>
<style>
div:has(#ship-list-nav-marker) ~ div button {
    text-align: left !important;
    justify-content: flex-start !important;
    padding-left: 0.6rem !important;
}
</style>""", unsafe_allow_html=True)
        st.markdown("#### Shipments")
        if st.button("➕ New Shipment", key="new_shipment_btn", use_container_width=True):
            _new_name = get_next_shipment_name()
            _new_id   = save_shipment({"name": _new_name})
            st.session_state["ship_sel_id"] = _new_id
            st.rerun()

        _draft_ships    = [_s for _s in _all_shipments if _s["status"] != "shipped"]
        _shipped_ships  = [_s for _s in _all_shipments if _s["status"] == "shipped"]

        for _s in _draft_ships:
            if st.button(f"🟡 {_s['name']}", key=f"ship_sel_{_s['id']}", use_container_width=True):
                st.session_state["ship_sel_id"] = _s["id"]
                st.rerun()

        if _shipped_ships:
            st.divider()
            st.caption("Shipped")
            for _s in _shipped_ships:
                if st.button(f"🟢 {_s['name']}", key=f"ship_sel_{_s['id']}", use_container_width=True):
                    st.session_state["ship_sel_id"] = _s["id"]
                    st.rerun()

    _sel_ship_id = st.session_state.get("ship_sel_id")
    _sel_ship    = get_shipment(_sel_ship_id) if _sel_ship_id else None

    @st.fragment
    def _ship_form(sel_ship, all_skus):
        if not sel_ship:
            st.info("Select a shipment from the left, or create a new one.")
            return

        _sid    = sel_ship["id"]
        _locked = sel_ship["status"] == "shipped"
        _ctx    = str(_sid)

        # ── Header ────────────────────────────────────────────────────────────
        _sh1, _sh2 = st.columns([2, 2])
        with _sh1:
            _ship_name = st.text_input(
                "Shipment name",
                value=sel_ship["name"],
                disabled=_locked,
                key=f"ship_name_{_ctx}",
            )
        with _sh2:
            _ship_dest = st.text_input(
                "Destination",
                value=sel_ship.get("destination") or "",
                placeholder="e.g. Amazon US FBA",
                disabled=_locked,
                key=f"ship_dest_{_ctx}",
            )
        _ship_address = st.text_input(
            "Address",
            value=sel_ship.get("address") or "",
            placeholder="e.g. 1 Commerce Dr, Carlisle, PA 17015, USA",
            max_chars=100,
            disabled=_locked,
            key=f"ship_address_{_ctx}",
        )
        _ship_notes = st.text_input(
            "Notes (optional)",
            value=sel_ship.get("notes") or "",
            disabled=_locked,
            key=f"ship_notes_{_ctx}",
        )

        if _locked:
            st.success("✅ This shipment has been marked as **Shipped** and is locked.")

        # ── Sub-tabs: Edit vs Packing List ────────────────────────────────────
        _ship_edit_tab, _ship_pl_tab = st.tabs(["✏️ Edit Lines", "📋 Packing List"])

        # ── Shared data for both tabs ─────────────────────────────────────────
        if not all_skus:
            st.info("No SKUs in the catalog yet.")
            return

        sku_info   = get_sku_catalog_info()
        # Available per SKU excluding this shipment's own lines
        _avail_map = get_available_per_sku_excluding(_sid)

        # ── PACKING LIST tab ──────────────────────────────────────────────────
        with _ship_pl_tab:
            _pl_rows = get_packing_list(_sid)
            if not _pl_rows:
                st.info("Add lines to the shipment first.")
            else:
                _ship_title   = sel_ship.get("name", "")
                _ship_dest_pl = sel_ship.get("destination") or ""
                st.markdown(
                    f"### 📋 Packing List — {_ship_title}"
                    + (f" · {_ship_dest_pl}" if _ship_dest_pl else "")
                )

                # ── Build flat table: one row per (SKU × item) ────────────────
                # GW and CBM repeat for each item of the same SKU (SKU-level values)
                # NW is per-item: net_wt_g_per_unit × qty / 1000
                _tbl_rows = []
                for _r in _pl_rows:
                    _tbl_rows.append({
                        "Item":        _r["item_name"] or "—",
                        "Product":     _r["product"],
                        "Ctns":        _r["num_cartons"],
                        "Qty (pcs)":   _r["qty_total"],
                        "GW (kg)":     _r["total_gw_kg"],
                        "NW (kg)":     round(_r["net_wt_g_per_unit"] * _r["qty_total"] / 1000, 2),
                        "CBM":         _r["total_cbm"],
                        "HS Code (NA)": _r["hst_code_na"],
                        "HS Code (UK)": _r["hst_code_uk"],
                    })

                _pl_df = pd.DataFrame(_tbl_rows)

                # ── Grand totals (SKU-level values deduplicated) ───────────────
                _seen = set()
                _tot_ctns = _tot_gw = _tot_cbm = 0.0
                _tot_qty = _tot_nw = 0.0
                for _r in _pl_rows:
                    if _r["sku"] not in _seen:
                        _seen.add(_r["sku"])
                        _tot_ctns += _r["num_cartons"]
                        _tot_gw   += _r["total_gw_kg"]
                        _tot_cbm  += _r["total_cbm"]
                    _tot_qty += _r["qty_total"]
                    _tot_nw  += round(_r["net_wt_g_per_unit"] * _r["qty_total"] / 1000, 2)

                st.markdown(
                    f"**{int(_tot_ctns)} Ctns · {int(_tot_qty)} pcs · "
                    f"GW {_tot_gw:.2f} kg · NW {_tot_nw:.2f} kg · {_tot_cbm:.3f} CBM**"
                )

                _no_items = [r["Item"] for r in _tbl_rows if r["Item"] == "—"]
                if _no_items:
                    st.warning(
                        f"⚠️ {len(_no_items)} line(s) have no items linked. "
                        "Add Part ID 1/2 to those products in the Products tab."
                    )

                st.dataframe(
                    _pl_df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Item":          st.column_config.TextColumn("Item",        width=180),
                        "Product":       st.column_config.TextColumn("Product",     width=260),
                        "Ctns":          st.column_config.NumberColumn("Ctns",      format="%d",      width=65),
                        "Qty (pcs)":     st.column_config.NumberColumn("Qty (pcs)", format="%d",      width=90),
                        "GW (kg)":       st.column_config.NumberColumn("GW (kg)",   format="%.2f",    width=90),
                        "NW (kg)":       st.column_config.NumberColumn("NW (kg)",   format="%.2f",    width=90),
                        "CBM":           st.column_config.NumberColumn("CBM",       format="%.3f",    width=75),
                        "HS Code (NA)":  st.column_config.TextColumn("HS Code (NA)", width=115),
                        "HS Code (UK)":  st.column_config.TextColumn("HS Code (UK)", width=115),
                    },
                )

                st.caption(
                    "GW = gross weight per SKU (all cartons) · "
                    "NW = net item weight for customs declaration · "
                    "GW/NW repeat per row when a SKU has multiple items"
                )

                # ── Download CSV ───────────────────────────────────────────────
                st.download_button(
                    "⬇️ Download Packing List CSV",
                    data=_pl_df.to_csv(index=False),
                    file_name=f"packing_list_{_ship_title}.csv",
                    mime="text/csv",
                    key=f"dl_pl_{_sid}",
                )

        # ── EDIT LINES tab ────────────────────────────────────────────────────
        with _ship_edit_tab:

            def _make_ship_row(sku, num_cartons):
                info  = sku_info.get(sku or "", {})
                cu    = info.get("carton_units", 0) or 0
                units = cu * num_cartons
                avail = _avail_map.get(sku, 0) if sku else 0
                return {
                    "SKU":               sku or "",
                    "# Cartons":         num_cartons,
                    "Available":         avail,
                    "Product":           info.get("name", "") if sku else "",
                    "# Units":           units,
                    "Product Cost ($)":  round(info.get("unit_mfg", 0.0) * units, 2),
                    "Service Cost ($)":  round(info.get("unit_svc", 0.0) * units, 2),
                    "Net Weight (kg)":   round(info.get("nw_kg", 0.0) * num_cartons, 2),
                    "Gross Weight (kg)": round(info.get("gw_kg", 0.0) * num_cartons, 2),
                    "CBM":               round(info.get("cbm",  0.0) * num_cartons, 3),
                }

            _sstate_key = f"ship_lines_state_{_ctx}"
            _sek        = f"ship_lines_ed_{_ctx}"

            def _safe_int_s(v):
                try:
                    return 0 if v is None or (isinstance(v, float) and pd.isna(v)) else int(v)
                except (TypeError, ValueError):
                    return 0

            if _sstate_key not in st.session_state:
                _db_lines = get_shipment_lines(_sid)
                st.session_state[_sstate_key] = [
                    {"SKU": ln["sku"] or "", "# Cartons": int(ln["num_cartons"] or 0)}
                    for ln in _db_lines
                ]

            # Selective stable-data:
            #   • Carton edits  → absorbed immediately → computed columns update, validation runs
            #   • SKU-only edits → stay in _sedit_map  → base df unchanged → no scroll reset
            #   • Structural (add/delete) → absorbed immediately
            _sdiffs    = st.session_state.get(_sek, {})
            _sedit_map = {int(k): v for k, v in (_sdiffs.get("edited_rows") or {}).items()}
            _sdel_base = set(_sdiffs.get("deleted_rows") or [])
            _sadded    = _sdiffs.get("added_rows") or []

            _has_carton_edit = any("# Cartons" in chg for chg in _sedit_map.values())

            if _sdel_base or _sadded or _has_carton_edit:
                # Absorb everything for this rerun (SKU + carton + structural)
                _ss = {i: dict(r) for i, r in enumerate(st.session_state[_sstate_key])
                       if i not in _sdel_base}
                for _ri, _chg in _sedit_map.items():
                    if _ri in _ss:
                        _ss[_ri].update({k: v for k, v in _chg.items()
                                         if k in ("SKU", "# Cartons")})
                _smerged = [_ss[k] for k in sorted(_ss)]
                for _a in _sadded:
                    _smerged.append({
                        "SKU":       str(_a.get("SKU") or "").strip(),
                        "# Cartons": _safe_int_s(_a.get("# Cartons")),
                    })
                st.session_state[_sstate_key] = _smerged
                st.session_state.pop(_sek, None)
                _sedit_map = {}

            _scur_list = st.session_state[_sstate_key]

            _SSCHEMA = {
                "Select": pd.Series(dtype=bool),
                "SKU": pd.Series(dtype=str), "# Cartons": pd.Series(dtype=int),
                "Available": pd.Series(dtype=int),
                "Product": pd.Series(dtype=str), "# Units": pd.Series(dtype=int),
                "Product Cost ($)": pd.Series(dtype=float),
                "Service Cost ($)": pd.Series(dtype=float),
                "Net Weight (kg)": pd.Series(dtype=float),
                "Gross Weight (kg)": pd.Series(dtype=float),
                "CBM": pd.Series(dtype=float),
            }
            _sfull_rows = [{"Select": False, **_make_ship_row(r["SKU"], _safe_int_s(r.get("# Cartons")))}
                           for r in _scur_list]
            _sfull_df   = pd.DataFrame(_sfull_rows) if _sfull_rows else pd.DataFrame(_SSCHEMA)

            # Select is editable; everything else except SKU and # Cartons is computed/read-only
            _SCOMPUTED = ["Available", "Product", "# Units", "Product Cost ($)",
                          "Service Cost ($)", "Net Weight (kg)", "Gross Weight (kg)", "CBM"]

            # Rows checked in the Select column (for Delete Selected)
            _selected_idxs = {i for i, chg in _sedit_map.items() if chg.get("Select")}

            # Effective rows = absorbed state + any pending SKU-only edits
            def _seff_rows():
                _out = []
                for _i, _r in enumerate(st.session_state[_sstate_key]):
                    _m = dict(_r)
                    if _i in _sedit_map:
                        _m.update({k: v for k, v in _sedit_map[_i].items()
                                   if k in ("SKU", "# Cartons")})
                    _out.append(_m)
                return _out

            # Validation (live — runs whenever carton change triggers a rerun)
            _val_errors = []
            for _vrow in _seff_rows():
                _vsku = str(_vrow.get("SKU") or "").strip()
                _vnc  = _safe_int_s(_vrow.get("# Cartons"))
                if not _vsku or _vnc <= 0:
                    continue
                _vmax = _avail_map.get(_vsku, 0)
                if _vnc > _vmax:
                    _val_errors.append(
                        f"🚫 **{_vsku}**: {_vnc} cartons requested — only **{_vmax}** available"
                    )
            _has_errors = bool(_val_errors)
            for _ve in _val_errors:
                st.error(_ve)

            _ship_col_cfg = {
                "Select":            st.column_config.CheckboxColumn("✔", default=False, width=40),
                "SKU":               st.column_config.SelectboxColumn("SKU", options=all_skus, required=True, width=150),
                "# Cartons":         st.column_config.NumberColumn("# Cartons", min_value=0, step=1, width=100),
                "Available":         st.column_config.NumberColumn("Available Stock", width=115),
                "Product":           st.column_config.TextColumn("Product", width=220),
                "# Units":           st.column_config.NumberColumn("# Units", width=80),
                "Product Cost ($)":  st.column_config.NumberColumn("Product Cost ($)", format="$%.2f", width=125),
                "Service Cost ($)":  st.column_config.NumberColumn("Service Cost ($)", format="$%.2f", width=120),
                "Net Weight (kg)":   st.column_config.NumberColumn("Net Weight (kg)", format="%.2f kg", width=120),
                "Gross Weight (kg)": st.column_config.NumberColumn("Gross Weight (kg)", format="%.2f kg", width=130),
                "CBM":               st.column_config.NumberColumn("CBM", format="%.3f", width=75),
            }

            if _locked:
                st.dataframe(
                    _sfull_df.drop(columns=["Select"], errors="ignore"),
                    use_container_width=True,
                    hide_index=True,
                    height=800,
                    column_config={k: v for k, v in _ship_col_cfg.items() if k != "Select"},
                )
            else:
                st.data_editor(
                    _sfull_df,
                    use_container_width=True,
                    num_rows="dynamic",
                    key=_sek,
                    disabled=_SCOMPUTED,
                    height=800,
                    column_config=_ship_col_cfg,
                )
                if st.button(
                    "🗑️ Delete Selected",
                    key=f"ship_del_sel_{_ctx}",
                    disabled=not _selected_idxs,
                    help="Check ✔ on rows you want to remove, then click here",
                ):
                    st.session_state[_sstate_key] = [
                        r for i, r in enumerate(st.session_state[_sstate_key])
                        if i not in _selected_idxs
                    ]
                    st.session_state.pop(_sek, None)
                    st.rerun()

            # ── Totals ────────────────────────────────────────────────────────
            _stot_cartons = _stot_units = 0
            _stot_prod = _stot_svc = _stot_nw = _stot_gw = _stot_cbm = 0.0
            for _r in _seff_rows():
                _sku_t = _r.get("SKU") or ""
                if not _sku_t:
                    continue
                _nc_t  = _safe_int_s(_r.get("# Cartons"))
                _inf_t = sku_info.get(_sku_t, {})
                _cu_t  = _inf_t.get("carton_units", 0) or 0
                _u_t   = _cu_t * _nc_t
                _stot_cartons += _nc_t
                _stot_units   += _u_t
                _stot_prod    += _inf_t.get("unit_mfg", 0.0) * _u_t
                _stot_svc     += _inf_t.get("unit_svc", 0.0) * _u_t
                _stot_nw      += _inf_t.get("nw_kg", 0.0) * _nc_t
                _stot_gw      += _inf_t.get("gw_kg", 0.0) * _nc_t
                _stot_cbm     += _inf_t.get("cbm",   0.0) * _nc_t
            if _stot_cartons:
                st.markdown(
                    f"**📊 TOTAL** · {_stot_cartons} cartons · {_stot_units} units · "
                    f"\\${_stot_prod:.2f} prod cost · \\${_stot_svc:.2f} svc cost · "
                    f"{_stot_nw:.2f} kg NW · {_stot_gw:.2f} kg GW · {_stot_cbm:.3f} CBM"
                )

            # ── Create Labels (always available) ──────────────────────────────
            st.divider()
            _lbl_col, _csv_col, _wfs_col, _ = st.columns([2, 2, 2, 4])

            # Shared lines for both buttons
            if _locked:
                _export_lines = get_shipment_lines(_sid)
            else:
                _export_lines = [
                    {"sku": r.get("SKU", ""), "num_cartons": _safe_int_s(r.get("# Cartons"))}
                    for r in _seff_rows() if r.get("SKU")
                ]

            with _lbl_col:
                _lbl_ship = {
                    "name":        sel_ship.get("name", ""),
                    "destination": _ship_dest if not _locked else (sel_ship.get("destination") or ""),
                    "address":     _ship_address if not _locked else (sel_ship.get("address") or ""),
                }
                _lbl_pdf = generate_carton_labels_pdf(_lbl_ship, _export_lines, sku_info)
                st.download_button(
                    "🏷️ Create Labels",
                    data=_lbl_pdf,
                    file_name=f"{sel_ship['name']}_labels.pdf",
                    mime="application/pdf",
                    key=f"ship_labels_{_ctx}",
                    use_container_width=True,
                )

            with _csv_col:
                # Box content CSV (inches + lbs)
                _CM_TO_IN = 0.393701
                _KG_TO_LB = 2.20462
                _csv_rows = []
                for _lr in _export_lines:
                    _lsku = (_lr.get("sku") or "").strip()
                    _lnc  = int(_lr.get("num_cartons") or 0)
                    if not _lsku or _lnc <= 0:
                        continue
                    _inf  = sku_info.get(_lsku, {})
                    _cu   = int(_inf.get("carton_units") or 0)
                    _csv_rows.append({
                        "Merchant SKU":    _lsku,
                        "Quantity":        _cu * _lnc,
                        "Units per box":   _cu,
                        "Number of boxes": _lnc,
                        "Box length (in)": round((_inf.get("length_cm") or 0.0) * _CM_TO_IN, 2),
                        "Box width (in)":  round((_inf.get("width_cm")  or 0.0) * _CM_TO_IN, 2),
                        "Box height (in)": round((_inf.get("height_cm") or 0.0) * _CM_TO_IN, 2),
                        "Box weight (lb)": round((_inf.get("gw_kg")     or 0.0) * _KG_TO_LB, 2),
                    })
                _csv_bytes = pd.DataFrame(_csv_rows).to_csv(index=False).encode()
                st.download_button(
                    "📦 Box Content CSV (in/lb)",
                    data=_csv_bytes,
                    file_name=f"{sel_ship['name']}_box_content.csv",
                    mime="text/csv",
                    key=f"ship_csv_{_ctx}",
                    use_container_width=True,
                )

            with _wfs_col:
                # WFS box content — Excel (.xlsx) so Product ID leading zeros are preserved
                import io as _io
                from openpyxl import Workbook as _WB
                from openpyxl.styles import numbers as _opxl_num

                _WFS_HEADERS = [
                    "Product type ID",
                    "Product ID",
                    "SKU",
                    "Item name",
                    "Item Qty (Total # of Sellable Units)",
                    "Vendor pack Qty (# of Cases)",
                    "Inner pack Qty (Sellable Units per Case)",
                ]
                _wb = _WB()
                _ws = _wb.active
                _ws.append(_WFS_HEADERS)

                for _lr in _export_lines:
                    _lsku = (_lr.get("sku") or "").strip()
                    _lnc  = int(_lr.get("num_cartons") or 0)
                    if not _lsku or _lnc <= 0:
                        continue
                    _inf  = sku_info.get(_lsku, {})
                    _cu   = int(_inf.get("carton_units") or 0)
                    _upc  = str(_inf.get("upc") or "").strip()
                    _product_id = f"00{_upc}" if _upc else ""
                    _ws.append([
                        "GTIN",
                        _product_id,
                        _lsku,
                        _inf.get("name") or "",
                        _cu * _lnc,
                        _lnc,
                        _cu,
                    ])

                # Force Product ID column (col B = 2) to text so Excel
                # preserves leading zeros
                for _cell in _ws["B"][1:]:   # skip header
                    _cell.number_format = "@"

                _wfs_buf = _io.BytesIO()
                _wb.save(_wfs_buf)
                _wfs_buf.seek(0)

                st.download_button(
                    "📦 Box Content CSV WFS",
                    data=_wfs_buf.getvalue(),
                    file_name=f"{sel_ship['name']}_box_content_wfs.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"ship_wfs_{_ctx}",
                    use_container_width=True,
                )

            # ── Buttons ───────────────────────────────────────────────────────
            if not _locked:
                _sb1, _sb2, _sb3, _ = st.columns([2, 2, 2, 4])
                with _sb1:
                    if st.button("💾 Save", type="primary", key=f"ship_save_{_ctx}",
                                 disabled=_has_errors,
                                 help="Fix carton errors before saving" if _has_errors else None):
                        try:
                            save_shipment({
                                "id":          _sid,
                                "name":        _ship_name.strip(),
                                "destination": _ship_dest.strip() or None,
                                "address":     _ship_address.strip() or None,
                                "notes":       _ship_notes.strip() or None,
                            })
                            _lines_to_save = [
                                {**r, "# Cartons": min(
                                    _safe_int_s(r.get("# Cartons")),
                                    _avail_map.get(str(r.get("SKU") or "").strip(), 0)
                                )}
                                for r in _seff_rows() if r.get("SKU")
                            ]
                            save_shipment_lines(_sid, _lines_to_save)
                            st.session_state.pop(_sstate_key, None)
                            st.session_state.pop(_sek, None)
                            st.success("✅ Shipment saved.")
                            st.rerun()
                        except Exception as _se:
                            st.error(f"Save failed: {_se}")

                with _sb2:
                    if st.button("✅ Mark as Shipped", key=f"ship_mark_{_ctx}"):
                        st.session_state[f"ship_confirm_{_ctx}"] = True

                with _sb3:
                    if st.button("🗑️ Delete", key=f"ship_del_{_ctx}"):
                        st.session_state[f"ship_del_confirm_{_ctx}"] = True

                if st.session_state.get(f"ship_confirm_{_ctx}"):
                    st.warning("Mark as **Shipped**? This cannot be undone.")
                    _mc1, _mc2, _ = st.columns([2, 2, 6])
                    with _mc1:
                        if st.button("Yes, ship it", type="primary", key=f"ship_yes_{_ctx}"):
                            save_shipment_lines(_sid, [r for r in _seff_rows() if r.get("SKU")])
                            mark_shipped(_sid)
                            st.session_state.pop(f"ship_confirm_{_ctx}", None)
                            st.session_state.pop(_sstate_key, None)
                            st.session_state.pop(_sek, None)
                            st.success("🚢 Shipment marked as shipped.")
                            st.rerun()
                    with _mc2:
                        if st.button("Cancel", key=f"ship_cancel_{_ctx}"):
                            st.session_state.pop(f"ship_confirm_{_ctx}", None)
                            st.rerun()

                if st.session_state.get(f"ship_del_confirm_{_ctx}"):
                    st.warning(f"Delete **{sel_ship['name']}**?")
                    _dc1, _dc2, _ = st.columns([2, 2, 6])
                    with _dc1:
                        if st.button("Yes, delete", type="primary", key=f"ship_del_yes_{_ctx}"):
                            delete_shipment(_sid)
                            st.session_state.pop("ship_sel_id", None)
                            st.session_state.pop(_sstate_key, None)
                            st.session_state.pop(_sek, None)
                            st.rerun()
                    with _dc2:
                        if st.button("Cancel", key=f"ship_del_no_{_ctx}"):
                            st.session_state.pop(f"ship_del_confirm_{_ctx}", None)
                            st.rerun()

    with _ship_col_form:
        _ship_form(_sel_ship, _all_skus)


    # ── RETURNS ───────────────────────────────────────────────────────────────
    with _inv_returns_tab:
        st.markdown("# ↩️ Amazon FBA Returns")

        # ── Upload ────────────────────────────────────────────────────────────
        with st.expander("📤 Upload Returns CSV", expanded=False):
            st.markdown(
                "Download the **FBA Customer Returns** report from Seller Central "
                "(*Reports → Fulfillment → Customer Concessions → FBA Customer Returns*).  \n"
                "The country is **auto-detected** from the Fulfillment Center ID — "
                "just pick the region (NA or EU) as a fallback for unrecognised FCs."
            )
            _ru1, _ru2, _ru3, _ru4 = st.columns([2, 2, 2, 1])
            with _ru4:
                _ret_upload_region = st.selectbox(
                    "Region",
                    options=["NA", "EU"],
                    key="ret_region_hint",
                    help="Only used as a fallback when the FC code cannot be mapped to a country.",
                )
            with _ru2:
                _ret_rpt_from = st.date_input(
                    "Report from",
                    value=date.today().replace(day=1),
                    key="ret_rpt_from",
                    help="Start date of the report period you downloaded from Seller Central.",
                )
            with _ru3:
                _ret_rpt_to = st.date_input(
                    "Report to",
                    value=date.today(),
                    key="ret_rpt_to",
                    help="End date of the report period you downloaded from Seller Central.",
                )
            with _ru1:
                _ret_file = st.file_uploader(
                    "Choose CSV file",
                    type=["csv", "txt"],
                    key=f"ret_upload_{st.session_state.get('ret_upload_key', 0)}",
                )
            if _ret_file is not None:
                if st.button("⬆️ Import Returns", key="ret_import_btn", type="primary"):
                    try:
                        _ret_df = pd.read_csv(_ret_file, sep=None, engine="python",
                                              encoding_errors="replace")
                        _ret_imported, _ret_warns = import_returns_csv(
                            _ret_df, _ret_upload_region,
                            report_from=str(_ret_rpt_from),
                            report_to=str(_ret_rpt_to),
                        )
                        if _ret_warns:
                            st.warning(f"⚠️ {'; '.join(_ret_warns[:5])}")
                        if _ret_imported:
                            st.success(
                                f"✅ Imported {_ret_imported} return records "
                                f"({_ret_upload_region} · {_ret_rpt_from} → {_ret_rpt_to})."
                            )
                        else:
                            st.info("No new records imported (all may be duplicates).")
                        st.session_state["ret_upload_key"] = st.session_state.get("ret_upload_key", 0) + 1
                        st.rerun()
                    except Exception as _re:
                        st.error(f"Import failed: {_re}")

            st.divider()
            st.markdown("**🗑️ Danger Zone**")
            if st.button("Delete All Returns Data", key="ret_del_all_btn",
                         help="Permanently removes every return record from the database."):
                st.session_state["ret_del_all_confirm"] = True

            if st.session_state.get("ret_del_all_confirm"):
                st.warning("This will permanently delete **all** returns records. Are you sure?")
                _rd1, _rd2, _ = st.columns([2, 2, 6])
                with _rd1:
                    if st.button("Yes, delete all", type="primary", key="ret_del_all_yes"):
                        _deleted = clear_all_returns()
                        st.session_state.pop("ret_del_all_confirm", None)
                        st.success(f"✅ Deleted {_deleted:,} records.")
                        st.rerun()
                with _rd2:
                    if st.button("Cancel", key="ret_del_all_cancel"):
                        st.session_state.pop("ret_del_all_confirm", None)
                        st.rerun()

        # ── Uploaded data coverage ────────────────────────────────────────────
        _ret_meta = get_upload_meta()
        if _ret_meta:
            _cov_parts = []
            for _rg in ("NA", "EU"):
                _m = _ret_meta.get(_rg)
                if _m and _m.get("report_from") and _m.get("report_to"):
                    _cov_parts.append(
                        f"**{_rg}** {_m['report_from']} → {_m['report_to']}"
                    )
            if _cov_parts:
                st.info("📅 Uploaded data covers: " + " &nbsp;|&nbsp; ".join(_cov_parts))

        st.divider()

        # ── Filters ───────────────────────────────────────────────────────────
        _ret_min_date, _ret_max_date = get_returns_date_range()
        _ret_avail_countries = get_available_countries()   # ["CA","DE","FR", ...]

        _ret_f1, _ret_f2, _ret_f3, _ret_f4, _ = st.columns([1.5, 2, 2, 2, 2])
        with _ret_f1:
            _ret_region_filter = st.selectbox(
                "Region",
                options=["All", "NA", "EU"],
                key="ret_region_sel",
            )
        with _ret_f2:
            _country_options = ["All"] + [
                f"{COUNTRY_FLAG.get(c, '')} {c}"
                for c in _ret_avail_countries
            ]
            _ret_country_disp = st.selectbox(
                "Country",
                options=_country_options,
                key="ret_country_sel",
            )
            # Strip flag prefix back to code
            _ret_country = None if _ret_country_disp == "All" else _ret_country_disp.split()[-1]
        with _ret_f3:
            _ret_start = st.date_input(
                "From",
                value=date.fromisoformat(_ret_min_date) if _ret_min_date else date.today() - timedelta(days=90),
                key="ret_start_date",
            )
        with _ret_f4:
            _ret_end = st.date_input(
                "To",
                value=date.fromisoformat(_ret_max_date) if _ret_max_date else date.today(),
                key="ret_end_date",
            )

        # ── Country breakdown mini-table ───────────────────────────────────────
        _ret_country_df = get_return_country_breakdown(str(_ret_start), str(_ret_end))
        if not _ret_country_df.empty:
            with st.expander("🌍 Returns by Country", expanded=False):
                st.dataframe(
                    _ret_country_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Country":       st.column_config.TextColumn("Country",        width=90),
                        "Total":         st.column_config.NumberColumn("Total",         width=70),
                        "🔴 Amazon":     st.column_config.NumberColumn("🔴 Amazon",     width=90),
                        "🟠 Mfg Defect": st.column_config.NumberColumn("🟠 Mfg Defect", width=110),
                        "⚪ Customer":   st.column_config.NumberColumn("⚪ Customer",    width=100),
                        "Other":         st.column_config.NumberColumn("Other",          width=65),
                    },
                )

        st.divider()

        # ── Report ────────────────────────────────────────────────────────────
        _ret_report = get_return_rate_report(
            region=_ret_region_filter if _ret_region_filter != "All" else None,
            country=_ret_country,
            start_date=str(_ret_start),
            end_date=str(_ret_end),
        )

        if _ret_report.empty:
            st.info("No returns data found for the selected filters. Upload a returns CSV to get started.")
        else:
            # Summary KPIs
            _ret_total_returned = int(_ret_report["Returns"].sum())
            _ret_total_sold     = int(_ret_report["Units Sold"].sum())
            _ret_overall_rate   = round(_ret_total_returned / _ret_total_sold * 100, 1) if _ret_total_sold else 0.0
            _ret_amazon_cnt     = int(_ret_report["🔴 Amazon"].sum())
            _ret_defect_cnt     = int(_ret_report["🟠 Mfg Defect"].sum())
            _ret_cust_cnt       = int(_ret_report["⚪ Customer"].sum())

            _rk1, _rk2, _rk3, _rk4, _rk5 = st.columns(5)
            _rk1.metric("Total Returns",    f"{_ret_total_returned:,}")
            _rk2.metric("Units Sold",        f"{_ret_total_sold:,}")
            _rk3.metric("Overall Rate",      f"{_ret_overall_rate}%")
            _rk4.metric("🔴 Contact Amazon", f"{_ret_amazon_cnt:,}")
            _rk5.metric("🟠 Mfg Defects",   f"{_ret_defect_cnt:,}")

            st.divider()

            # Color-code Return Rate %
            def _ret_rate_color(rate):
                if rate is None:
                    return "color: #888"
                if rate >= 5:
                    return "color: #e05252; font-weight: 700"
                if rate >= 2:
                    return "color: #e09c52; font-weight: 600"
                return "color: #52a852"

            # Display the report table
            _ret_display = _ret_report.copy()
            _ret_img_map  = get_asin_image_map()
            _ret_display.insert(0, "Image",
                _ret_display["ASIN"].str.upper().map(_ret_img_map).fillna(""))
            _ret_display["Return Rate %"] = _ret_display["Return Rate %"].apply(
                lambda x: f"{x:.1f}%" if x is not None else "N/A"
            )
            _ret_display["Units Sold"] = _ret_display["Units Sold"].apply(
                lambda x: f"{x:,}" if x else "—"
            )

            st.dataframe(
                _ret_display.style.apply(
                    lambda _col: [_ret_rate_color(
                        float(_v.replace("%", "")) if isinstance(_v, str) and _v.endswith("%") else None
                    ) for _v in _col]
                    if _col.name == "Return Rate %"
                    else [""] * len(_col),
                    axis=0,
                ),
                use_container_width=True,
                hide_index=True,
                height=500,
                column_config={
                    "Image":          st.column_config.ImageColumn("", width=55),
                    "ASIN":           st.column_config.TextColumn("ASIN",          width=110),
                    "Product":        st.column_config.TextColumn("Product",        width=200),
                    "Units Sold":     st.column_config.TextColumn("Units Sold",     width=90),
                    "Returns":        st.column_config.NumberColumn("Returns",      width=80),
                    "Return Rate %":  st.column_config.TextColumn("Return Rate %",  width=105),
                    "Top Reason":     st.column_config.TextColumn("Top Reason",     width=160),
                    "Action":         st.column_config.TextColumn("Action",         width=185),
                    "🔴 Amazon":      st.column_config.NumberColumn("🔴 Amazon",    width=90),
                    "🟠 Mfg Defect":  st.column_config.NumberColumn("🟠 Mfg Defect", width=105),
                    "⚪ Customer":    st.column_config.NumberColumn("⚪ Customer",   width=100),
                    "Other":          st.column_config.NumberColumn("Other",         width=70),
                },
            )

            st.caption(
                "**🔴 ≥ 5%** critical &nbsp;|&nbsp; **🟠 2–5%** elevated &nbsp;|&nbsp; **🟢 < 2%** normal &nbsp;|&nbsp; "
                "**🔴 Contact Amazon** — FC/carrier damage &nbsp;|&nbsp; "
                "**🟠 Contact Manufacturer** — product defect/quality &nbsp;|&nbsp; "
                "**⚪ Normal Return** — customer preference"
            )

            st.download_button(
                "⬇️ Download Returns Report CSV",
                data=_ret_report.to_csv(index=False),
                file_name=f"returns_report_{_ret_start}_{_ret_end}.csv",
                mime="text/csv",
                key="ret_dl_btn",
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SALES DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
with tab_sales:
    # ── View toggle: Orders vs Bundles ────────────────────────────────────────
    _sales_view = st.radio(
        "View",
        ["📊 Orders", "📦 Bundles"],
        horizontal=True,
        key="sales_view_toggle",
        label_visibility="collapsed",
    )

    st.markdown("<div style='margin:4px 0 8px;'></div>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # BUNDLES VIEW
    # ══════════════════════════════════════════════════════════════════════════
    if _sales_view == "📦 Bundles":
        st.markdown(
            f"<p style='font-size:1.1rem;font-weight:700;margin:0 0 4px;'>📦 Bundle Sales &nbsp;"
            f"<span style='font-size:0.78rem;font-weight:400;color:{T['text_secondary']};'>"
            f"Units and revenue from Amazon Bundle Performance reports.</span></p>",
            unsafe_allow_html=True,
        )

        # ── Import ────────────────────────────────────────────────────────────
        with st.expander("📤 Import Bundle Performance CSV", expanded=(count_bundle_rows() == 0)):
            st.markdown(
                f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>"
                "Download from Seller Central → Reports → Business Reports → "
                "<b>Bundle Performance</b>. Duplicates are handled automatically.</p>",
                unsafe_allow_html=True,
            )
            _bundle_file = st.file_uploader("Upload Bundle CSV", type=["csv", "txt"], key="bundle_csv")
            if _bundle_file and st.button("📥 Import Bundles", type="primary", key="bundle_import_btn"):
                with st.spinner("Importing..."):
                    _bn, _bw = import_bundle_csv(_bundle_file)
                for w in _bw[:5]:
                    st.warning(w)
                if _bn > 0:
                    st.success(f"✅ Imported {_bn} rows.")
                    st.rerun()
                else:
                    st.error("No rows imported. Check warnings above.")

        _b_total = count_bundle_rows()
        if _b_total == 0:
            st.info("No bundle data yet. Import an Amazon Bundle Performance CSV above.")
        else:
            _b_min, _b_max = get_bundle_date_range()

            # ── Image fetch ───────────────────────────────────────────────────
            _b_img_map_check = get_asin_image_map()
            _b_missing_imgs  = [a for a in get_bundle_asins() if a not in _b_img_map_check]
            if _b_missing_imgs:
                _bimg_col1, _bimg_col2 = st.columns([3, 1])
                with _bimg_col1:
                    st.caption(f"🖼️ {len(_b_missing_imgs)} bundle ASIN(s) are missing product images: "
                               f"{', '.join(_b_missing_imgs)}")
                with _bimg_col2:
                    if st.button("🔍 Fetch Bundle Images", key="bundle_fetch_imgs"):
                        with st.spinner(f"Fetching images for {len(_b_missing_imgs)} ASIN(s)…"):
                            _bimg_results = fetch_bundle_images()
                        _bfound = sum(1 for v in _bimg_results.values() if v != "NOT_FOUND")
                        st.success(f"✅ Found {_bfound} / {len(_bimg_results)} images.")
                        st.rerun()

            # ── Period selector ───────────────────────────────────────────────
            _bp_col1, _bp_col2, _bp_col3 = st.columns([2, 2, 2])
            with _bp_col1:
                _b_period = st.selectbox(
                    "Period", [30, 60, 90, 180, 365], index=2,
                    format_func=lambda v: f"{v} days", key="bundle_period"
                )
            with _bp_col2:
                _b_metric = st.radio(
                    "Metric", ["Units", "Revenue"], horizontal=True, key="bundle_metric"
                )

            # ── KPI bar ───────────────────────────────────────────────────────
            _bsum = get_bundle_summary()
            _total_units   = int(_bsum["total_units"].sum())   if not _bsum.empty else 0
            _total_revenue = float(_bsum["total_revenue"].sum()) if not _bsum.empty else 0
            _num_asins     = len(_bsum) if not _bsum.empty else 0

            _bk1, _bk2, _bk3, _bk4 = st.columns(4)
            _bk1.markdown(f'<div class="metric-card"><p class="metric-val">{_total_units:,}</p><p class="metric-label">Bundles Sold</p></div>', unsafe_allow_html=True)
            _bk2.markdown(f'<div class="metric-card"><p class="metric-val">${_total_revenue:,.0f}</p><p class="metric-label">Total Revenue</p></div>', unsafe_allow_html=True)
            _bk3.markdown(f'<div class="metric-card"><p class="metric-val">{_num_asins}</p><p class="metric-label">Bundle ASINs</p></div>', unsafe_allow_html=True)
            _bk4.markdown(f'<div class="metric-card"><p class="metric-val">{_b_min} → {_b_max}</p><p class="metric-label">Date Range</p></div>', unsafe_allow_html=True)

            st.markdown("<div style='margin:10px 0 4px;'></div>", unsafe_allow_html=True)

            # ── Trend chart ───────────────────────────────────────────────────
            _b_trend_all = get_bundle_daily_trend(days=_b_period)
            _b_trend_per = get_bundle_per_asin_trend(days=_b_period)

            if not _b_trend_per.empty:
                _b_chart_df = _b_trend_per.pivot_table(
                    index="sale_date", columns="bundle_asin",
                    values="bundles_sold", aggfunc="sum", fill_value=0
                )
                _b_chart_df.index.name = "Date"
                st.line_chart(_b_chart_df, use_container_width=True, height=200)

            # ── Matrix table ──────────────────────────────────────────────────
            if _b_metric == "Units":
                _b_matrix = get_bundle_units_matrix(days=_b_period)
                _val_label = "units"
            else:
                _b_matrix = get_bundle_revenue_matrix(days=_b_period)
                _val_label = "$"

            if not _b_matrix.empty:
                # Attach product images from catalog
                _b_img_map = get_asin_image_map()
                _b_matrix.insert(0, "img", _b_matrix["bundle_asin"].map(_b_img_map))

                _b_date_cols = [c for c in _b_matrix.columns
                                if c not in ("bundle_asin", "title", "img", "Total")]
                _b_col_cfg = {
                    "img":          st.column_config.ImageColumn("", width=55),
                    "bundle_asin":  st.column_config.TextColumn("ASIN",    width="small"),
                    "title":        st.column_config.TextColumn("Title",   width="large"),
                    "Total":        st.column_config.NumberColumn("Total", format="%d" if _b_metric == "Units" else "$%.2f"),
                }
                for _dc in _b_date_cols:
                    _b_col_cfg[_dc] = st.column_config.NumberColumn(
                        _dc, format="%d" if _b_metric == "Units" else "$%.2f"
                    )

                st.dataframe(
                    _b_matrix,
                    use_container_width=True,
                    hide_index=True,
                    column_config=_b_col_cfg,
                )

                # ── Download ──────────────────────────────────────────────────
                st.download_button(
                    "⬇️ Download as CSV",
                    data=_b_matrix.drop(columns=["img"], errors="ignore").to_csv(index=False).encode(),
                    file_name=f"bundle_sales_{_b_metric.lower()}.csv",
                    mime="text/csv",
                    key="bundle_dl",
                )

            # ── Danger zone ───────────────────────────────────────────────────
            with st.expander("🗑️ Danger Zone", expanded=False):
                st.warning("This will permanently delete **all** bundle data.")
                if st.button("🗑️ Delete All Bundle Data", type="secondary", key="bundle_clear"):
                    _del_n = clear_all_bundles()
                    st.success(f"Deleted {_del_n} rows.")
                    st.rerun()

    # ══════════════════════════════════════════════════════════════════════════
    # ORDERS VIEW (original content)
    # ══════════════════════════════════════════════════════════════════════════

    if _sales_view == "📊 Orders":
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

            # ── Order Search ──────────────────────────────────────────────────────
            with st.expander("🔍 Order Lookup — search by recipient / address / zip", expanded=False):
                st.markdown(
                    f"<p style='color:{T['text_secondary']};font-size:0.84rem;margin:0 0 10px;'>"
                    "Find a specific order when a customer contacts you with their address details. "
                    "Leave any field blank to ignore it. Returns up to 500 matches.</p>",
                    unsafe_allow_html=True,
                )
                _sc1, _sc2, _sc3 = st.columns(3)
                with _sc1:
                    _srch_name    = st.text_input("👤 Recipient / buyer name", key="srch_name",
                                                   placeholder="e.g. John Smith")
                    _srch_country = st.text_input("🌍 Country (code or name)", key="srch_country",
                                                   placeholder="e.g. US  or  United States")
                with _sc2:
                    _srch_address = st.text_input("🏠 Address / City / State", key="srch_address",
                                                   placeholder="e.g. Main St  or  Brooklyn")
                    _srch_zip     = st.text_input("📮 Zip / Postal code", key="srch_zip",
                                                   placeholder="e.g. 10001")
                with _sc3:
                    _srch_from = st.date_input("📅 Order from", value=None, key="srch_from")
                    _srch_to   = st.date_input("📅 Order to",   value=None, key="srch_to")

                _srch_btn = st.button("🔍 Search Orders", type="primary", key="srch_btn")

                if _srch_btn:
                    _any_filter = any([
                        _srch_name.strip(), _srch_address.strip(),
                        _srch_zip.strip(), _srch_country.strip(),
                        _srch_from, _srch_to,
                    ])
                    if not _any_filter:
                        st.warning("Please enter at least one search term.")
                    else:
                        with st.spinner("Searching..."):
                            _srch_df = search_orders_by_address(
                                name        = _srch_name,
                                address     = _srch_address,
                                zip_code    = _srch_zip,
                                country     = _srch_country,
                                date_from   = str(_srch_from) if _srch_from else "",
                                date_to     = str(_srch_to)   if _srch_to   else "",
                            )
                        if _srch_df.empty:
                            st.info("No orders found matching your search.")
                        else:
                            st.success(f"Found **{len(_srch_df)}** order line(s).")
                            # Friendly column labels
                            _srch_display = _srch_df.rename(columns={
                                "order_id":         "Order ID",
                                "order_date":       "Date",
                                "asin":             "ASIN",
                                "title":            "Product",
                                "marketplace":      "Marketplace",
                                "quantity":         "Qty",
                                "item_price":       "Price",
                                "currency":         "Currency",
                                "order_status":     "Status",
                                "ship_name":        "Ship-to Name",
                                "buyer_name":       "Buyer Name",
                                "ship_address_1":   "Address",
                                "ship_city":        "City",
                                "ship_state":       "State",
                                "ship_postal_code": "Zip",
                                "ship_country":     "Country",
                                "buyer_email":      "Email",
                            })
                            st.dataframe(
                                _srch_display,
                                use_container_width=True,
                                hide_index=True,
                                column_config={
                                    "Order ID":   st.column_config.TextColumn("Order ID", width="medium"),
                                    "Date":       st.column_config.TextColumn("Date",     width="small"),
                                    "Product":    st.column_config.TextColumn("Product",  width="large"),
                                    "Qty":        st.column_config.NumberColumn("Qty",    width="small"),
                                    "Price":      st.column_config.NumberColumn("Price",  format="%.2f"),
                                    "Address":    st.column_config.TextColumn("Address",  width="medium"),
                                },
                            )
                            # Download button
                            _csv_bytes = _srch_display.to_csv(index=False).encode()
                            st.download_button(
                                "⬇️ Download results as CSV",
                                data=_csv_bytes,
                                file_name="order_search_results.csv",
                                mime="text/csv",
                                key="srch_download",
                            )

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

            # ── ASIN / product search + pending toggle ────────────────────────────
            _dash_s1, _dash_s2 = st.columns([5, 2])
            with _dash_s1:
                _asin_search = st.text_input(
                    "🔍 Search ASIN or product name",
                    value="",
                    placeholder="Filter rows by ASIN or title…",
                    key="dash_asin_search",
                )
            with _dash_s2:
                st.markdown("<div style='margin-top:28px'>", unsafe_allow_html=True)
                _include_pending = st.checkbox(
                    "Include Pending orders",
                    value=True,
                    key="dash_include_pending",
                    help="Pending orders are real orders but not yet shipped. "
                         "Uncheck to see confirmed/shipped only.",
                )
                st.markdown("</div>", unsafe_allow_html=True)

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
                matrix = get_units_matrix(marketplace=sel_market, days=days_back_raw,
                                          include_pending=_include_pending)
                threshold = 30
            elif yoy_mode:
                matrix, ly_matrix = get_weekly_units_matrix_yoy(
                    marketplace=sel_market, weeks=days_back_raw,
                    include_pending=_include_pending,
                )
                threshold = 20
            else:
                matrix = get_weekly_units_matrix(marketplace=sel_market, weeks=days_back_raw,
                                                 include_pending=_include_pending)
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

                # Insert image thumbnail as column 0, then "Last Change"
                _sales_img_map = get_asin_image_map()
                display_marked.insert(0, "Image", [
                    _sales_img_map.get(str(asin).upper(), "")
                    for asin, _ in display_marked.index
                ])
                display_marked.insert(1, "Last Change", [
                    _last_change_map.get(str(asin), "—")
                    for asin, _ in display_marked.index
                ])

                # Store date columns as integers (enables numeric sort).
                # Changed cells are still highlighted yellow by _color_matrix below.
                for col in date_cols:
                    col_idx = display_marked.columns.get_loc(col)
                    for pos, (asin, title) in enumerate(display_marked.index):
                        val = display_marked.iloc[pos, col_idx]
                        try:
                            display_marked.iloc[pos, col_idx] = int(float(val)) if pd.notna(val) else 0
                        except (ValueError, TypeError):
                            display_marked.iloc[pos, col_idx] = 0

                # ── Per-ASIN row totals — inserted right after "Last Change" ──────
                asin_row_totals = matrix[date_cols].sum(axis=1).astype(int)
                _lc_pos = display_marked.columns.get_loc("Last Change")
                display_marked.insert(_lc_pos + 1, "Total", asin_row_totals.values)

                col_cfg = {
                    "Image":       st.column_config.ImageColumn("",           width=55),
                    "asin":        st.column_config.TextColumn("ASIN",        width=120),
                    "title":       st.column_config.TextColumn("Title",       width=200),
                    "Last Change": st.column_config.TextColumn("Last Change", width=220),
                    "Total":       st.column_config.NumberColumn("Total",      width=90),
                }
                for _dc in date_cols:
                    col_cfg[_dc] = st.column_config.NumberColumn(_dc, width=80)

                # ── Totals row — prepended as row 0 in the same dataframe ─────────
                _col_sums    = {col: int(matrix[col].sum()) for col in date_cols}
                _grand_total = sum(_col_sums.values())
                _totals_row  = pd.DataFrame(
                    [{
                        "Image":       "",
                        "Last Change": "",
                        "Total":       _grand_total,
                        **{col: _col_sums[col] for col in date_cols},
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
        _recs_view, _history_view, _alerts_view, _analysis_view = st.tabs(["🔧 Workbench", "📜 History", "🔔 Alerts", "📤 Upload Reports"])

        with _recs_view:
            # ── WORKBENCH ─────────────────────────────────────────────────────
            st.markdown("# 🔧 Campaign Workbench")
            st.markdown(
                f"<p style='color:{T['text_secondary']};'>Auto-generated placement bid recommendations. "
                f"Select a row to see bid history, effectiveness, and add a note.</p>",
                unsafe_allow_html=True
            )

            # ── Filters ───────────────────────────────────────────────────────
            rhf1, rhf2, rhf3, rhf4, rhf5 = st.columns(5)
            with rhf1:
                rh_market = st.selectbox("Marketplace", ["all", "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au", "amazon.de"], key="rh_market")
                rh_market = None if rh_market == "all" else rh_market
            with rhf2:
                hide_paused = st.checkbox(
                    "⏸ Hide paused",
                    value=True,
                    key="rh_hide_paused",
                    help="Hide campaigns whose End Date is 21+ days old (likely paused)"
                )
            with rhf3:
                show_critical_recs = st.checkbox(
                    "🚨 Critical only",
                    value=False,
                    key="rh_critical",
                    help="🔴 Losing money (ROAS < breakeven)  ·  🟢 High-opportunity"
                )
            with rhf4:
                show_pending = st.checkbox("⏰ Pending review only", value=False)
            with rhf5:
                show_untreated = st.checkbox(
                    "🕰️ Untreated only",
                    value=False,
                    key="rh_untreated",
                    help="Losing campaigns with no bid action in the last 30 days"
                )

            recs_df = get_recommendations_history(marketplace=rh_market)
            # Auto-generated only
            if not recs_df.empty and "source" in recs_df.columns:
                recs_df = recs_df[recs_df["source"].fillna("auto") == "auto"]

            if not recs_df.empty:
                recs_df["score"] = pd.to_numeric(recs_df["score"], errors="coerce").fillna(0)

                # Hide paused
                if hide_paused and "end_date" in recs_df.columns:
                    _cutoff = str(date.today() - timedelta(days=21))
                    _has_end = recs_df["end_date"].notna() & (recs_df["end_date"] != "")
                    recs_df = recs_df[~(_has_end & (recs_df["end_date"] < _cutoff))]

                # Critical only
                if show_critical_recs:
                    _rsn_col   = recs_df["reasoning"].fillna("").str.upper()
                    _is_losing = _rsn_col.str.startswith("LOSING")
                    _is_oppty  = recs_df["score"] >= 70
                    recs_df = recs_df[_is_losing | _is_oppty]

                # Pending review only
                if show_pending:
                    _today_s = str(date.today())
                    recs_df = recs_df[recs_df["review_date"].fillna("") <= _today_s]

                # Untreated only — losing + no bid action in last 30 days
                if show_untreated:
                    _untreated_keys = get_untreated_losing(marketplace=rh_market)
                    recs_df = recs_df[
                        recs_df.apply(
                            lambda r: (r["campaign_name"], r["placement_type"], r["marketplace"])
                                      in _untreated_keys,
                            axis=1
                        )
                    ]

                # ── Sort: Isolation (losing) first by loss size, then Optimization by opportunity
                def _sort_key(row):
                    rsn = str(row.get("reasoning") or "").upper()
                    spend = float(row.get("spend") or 0)
                    if rsn.startswith("LOSING"):
                        # Extract ROAS gap from reasoning if possible — use score as proxy
                        return (0, -(row.get("score") or 0), -spend)
                    else:
                        return (1, -(row.get("score") or 0), -spend)

                recs_df = recs_df.iloc[
                    pd.DataFrame([_sort_key(r) for r in recs_df.to_dict("records")],
                                 columns=["_tier", "_score", "_spend"])
                    .sort_values(["_tier", "_score", "_spend"]).index
                ].reset_index(drop=True)

            def _fmt_change(row):
                action = str(row.get("recommended_action") or "").strip()
                mult   = row.get("recommended_multiplier")
                try:
                    pct = int(round(float(mult)))
                except (TypeError, ValueError):
                    pct = None
                if action.lower() == "increase" and pct is not None:
                    return f"+{pct}%"
                elif action.lower() in ("decrease", "reduce to 0%") and pct is not None:
                    return f"{pct}%"
                return action or "—"

            if recs_df.empty:
                st.info("No recommendations yet. Run an analysis to generate them.")
            else:
                recs_display = recs_df.copy()
                recs_display["change"] = recs_display.apply(_fmt_change, axis=1)
                # Notes column (may not exist in old rows)
                if "notes" not in recs_display.columns:
                    recs_display["notes"] = ""

                # ── Last Result column (effectiveness of most recent bid action) ──
                _eff_map = get_last_effectiveness_bulk(marketplace=rh_market)
                recs_display["last_result"] = recs_display.apply(
                    lambda r: _eff_map.get(
                        (r["campaign_name"], r["placement_type"], r["marketplace"]), "—"
                    ),
                    axis=1,
                )

                _ads_img_map = get_asin_image_map()
                recs_display.insert(0, "image",
                    recs_display["asin"].str.upper().map(_ads_img_map).fillna(""))

                display_cols = [
                    "image", "date_given", "end_date", "asin", "marketplace", "campaign_name",
                    "placement_type", "campaign_type", "change", "last_result", "reasoning", "notes"
                ]
                existing_cols = [c for c in display_cols if c in recs_display.columns]

                st.markdown(
                    f"<p style='font-size:0.8rem;color:{T['text_secondary']};margin-bottom:4px;'>"
                    "💡 Select a row to view bid history and add a note. "
                    "<b>Last Result</b> = ROAS change after the most recent bid action.</p>",
                    unsafe_allow_html=True,
                )
                _sel = st.dataframe(
                    recs_display[existing_cols],
                    use_container_width=True,
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    column_config={
                        "image":          st.column_config.ImageColumn("", width=55),
                        "date_given":     st.column_config.TextColumn("Date", width=95),
                        "end_date":       st.column_config.TextColumn("📅 End Date", width=95),
                        "asin":           st.column_config.TextColumn("ASIN", width=110),
                        "campaign_name":  st.column_config.TextColumn("Campaign", width=240),
                        "placement_type": st.column_config.TextColumn("Placement", width=120),
                        "campaign_type":  st.column_config.TextColumn("Type", width=55),
                        "change":         st.column_config.TextColumn("Rec. Change", width=100),
                        "last_result":    st.column_config.TextColumn("Last Result", width=105),
                        "reasoning":      st.column_config.TextColumn("Reasoning", width=340),
                        "notes":          st.column_config.TextColumn("📝 Notes", width=180),
                    },
                    key="recs_table_sel",
                )

                # ── Selection panel ────────────────────────────────────────────
                _sel_rows = _sel.selection.rows if _sel and hasattr(_sel, "selection") else []
                if _sel_rows and _sel_rows[0] < len(recs_df):
                    _sel_data  = recs_df.iloc[_sel_rows[0]].to_dict()
                    _sel_id    = int(_sel_data.get("id") or 0)
                    _sel_camp  = str(_sel_data.get("campaign_name") or "")
                    _sel_place = str(_sel_data.get("placement_type") or "")
                    _sel_mkt   = str(_sel_data.get("marketplace") or "")

                    # Card
                    st.markdown(
                        f"<div style='background:{T['card_bg']};border:1px solid {T['card_border']};"
                        f"border-radius:8px;padding:0.6rem 1rem;margin:8px 0;font-size:0.85rem;'>"
                        f"<strong>{_sel_camp[:70]}</strong> &nbsp;·&nbsp; {_sel_place} &nbsp;·&nbsp; {_sel_mkt}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                    # ── Unified: Note + Applied in one action ──────────────────
                    _raw_note = _sel_data.get("notes")
                    _existing_note = "" if (_raw_note is None or (isinstance(_raw_note, float) and pd.isna(_raw_note))) else str(_raw_note)
                    _rec_after  = int(round(float(_sel_data.get("recommended_multiplier") or 0)))
                    _rec_before = int(round(float(_sel_data.get("current_multiplier")     or 0)))

                    # ── Narrow log panel (≈ 1/3 width) ───────────────────────
                    _log_col, _ = st.columns([1, 2])
                    with _log_col:
                        st.markdown("##### ✍️ Document This Change")

                        # — Note —
                        st.markdown("**📝 Note**")
                        _note_val = st.text_area(
                            "Note",
                            value=_existing_note,
                            placeholder="e.g. Seasonal campaign, competitor left…",
                            label_visibility="collapsed",
                            key=f"note_{_sel_id}",
                            height=80,
                        )
                        _apply_all = st.checkbox(
                            "Apply note to all placements of this campaign",
                            value=True,
                            key=f"note_all_{_sel_id}",
                        )

                        st.divider()

                        # — Applied in Amazon? —
                        st.markdown(
                            f"**☑️ Applied in Amazon?** &nbsp; "
                            f"<span style='color:#888;font-size:0.85em'>rec: {_fmt_change(_sel_data)}</span>",
                            unsafe_allow_html=True,
                        )
                        _did_apply = st.checkbox(
                            "Yes, I changed the bid",
                            value=False,
                            key=f"did_apply_{_sel_id}",
                        )
                        if _did_apply:
                            _ab1, _ab2 = st.columns(2)
                            with _ab1:
                                _applied_before = st.number_input(
                                    "Was %", min_value=0, max_value=900,
                                    value=_rec_before, step=10,
                                    key=f"applied_before_{_sel_id}",
                                )
                            with _ab2:
                                _applied_after = st.number_input(
                                    "Now %", min_value=0, max_value=900,
                                    value=_rec_after, step=10,
                                    key=f"applied_after_{_sel_id}",
                                )
                        else:
                            _applied_before = _rec_before
                            _applied_after  = _rec_after

                    # ── Single Save button (same 1/3 width) ────────────────────
                    with st.columns([1, 2])[0]:
                      if st.button("💾 Save", key=f"save_btn_{_sel_id}",
                                   type="primary", use_container_width=True):
                        _note_saved = False
                        _bid_saved  = False
                        _msg_parts  = []

                        # Save note if not empty
                        if _note_val.strip():
                            if _apply_all:
                                _n = save_campaign_note(_sel_camp, _sel_mkt, _note_val)
                                _msg_parts.append(f"note saved to {_n} placements")
                            else:
                                save_recommendation_note(_sel_id, _note_val)
                                _msg_parts.append("note saved")
                            _note_saved = True

                        # Log bid change only if checkbox ticked
                        if _did_apply:
                            _applied_status = log_manual_bid_change(
                                campaign_name=_sel_camp,
                                placement_type=_sel_place,
                                marketplace=_sel_mkt,
                                change_date=str(date.today()),
                                bid_before=int(_applied_before),
                                bid_after=int(_applied_after),
                                notes=_note_val.strip() or None,
                            )
                            if _applied_status == "saved":
                                _msg_parts.append(f"{_applied_before}% → {_applied_after}% logged")
                                _bid_saved = True
                            elif _applied_status == "duplicate":
                                _msg_parts.append("bid change already logged for today")
                            else:
                                st.error(_applied_status)

                        if _note_saved or _bid_saved:
                            st.success("✅ " + " · ".join(_msg_parts))
                            st.rerun()
                        elif not _note_val.strip() and not _did_apply:
                            st.warning("Nothing to save — add a note or tick 'Applied in Amazon?'")

                    # ── Bid history + effectiveness timeline ───────────────────
                    with st.container():
                        st.markdown("**📈 Bid History & Effectiveness**")
                        _bid_eff = get_bid_effectiveness(_sel_camp, _sel_place, _sel_mkt)
                        if not _bid_eff:
                            st.caption("No bid changes detected yet for this campaign/placement.")
                        else:
                            _cur_sym = "£" if "co.uk" in _sel_mkt else "$"
                            _bh_df = pd.DataFrame(_bid_eff)
                            # Format display columns
                            _bh_df["bid_before"]     = _bh_df["bid_before"].apply(lambda x: f"{int(x)}%")
                            _bh_df["bid_after"]      = _bh_df["bid_after"].apply(lambda x: f"{int(x)}%")
                            _bh_df["roas_at_change"] = _bh_df["roas_at_change"].apply(lambda x: f"{x:.2f}x")
                            _bh_df["roas_after"]     = _bh_df["roas_after"].apply(
                                lambda x: f"{x:.2f}x" if x is not None else "⏳"
                            )
                            _bh_df["delta"] = _bh_df["delta"].apply(
                                lambda x: (f"+{x:.2f}x" if x > 0 else f"{x:.2f}x") if x is not None else "—"
                            )
                            _bh_df["profit"] = _bh_df["profit"].apply(
                                lambda x: f"{_cur_sym}{x:.2f}" if x else "—"
                            )
                            st.dataframe(
                                _bh_df[[
                                    "report_date", "bid_before", "bid_after",
                                    "roas_at_change", "roas_after", "delta", "result",
                                    "spend", "purchases", "profit",
                                ]],
                                use_container_width=True,
                                hide_index=True,
                                column_config={
                                    "report_date":    st.column_config.TextColumn("Date",       width=95),
                                    "bid_before":     st.column_config.TextColumn("Before",     width=70),
                                    "bid_after":      st.column_config.TextColumn("After",      width=70),
                                    "roas_at_change": st.column_config.TextColumn("ROAS then",  width=85),
                                    "roas_after":     st.column_config.TextColumn("ROAS next",  width=85),
                                    "delta":          st.column_config.TextColumn("Δ ROAS",     width=80),
                                    "result":         st.column_config.TextColumn("Result",     width=65),
                                    "spend":          st.column_config.NumberColumn("Spend",    format=f"{_cur_sym}%.2f", width=80),
                                    "purchases":      st.column_config.NumberColumn("Purchases",width=85),
                                    "profit":         st.column_config.TextColumn("Profit",     width=80),
                                },
                            )
                            st.caption(
                                "**Result:** ✅ ROAS improved · ❌ worsened · "
                                "➡️ flat · ⏳ awaiting next upload"
                            )

                    # ── Debug cost breakdown panel ─────────────────────────────
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
                                    height=480,
                                    column_config={
                                        "Item":  st.column_config.TextColumn(width=200),
                                        "Value": st.column_config.TextColumn(width=150),
                                        "Notes": st.column_config.TextColumn(width=300),
                                    },
                                )

        with _history_view:
            # ── HISTORY content ───────────────────────────────────────────────
            st.markdown("## 📊 Change Log & Impact")
            st.markdown(
                f"<p style='color:{T['text_secondary']};font-size:0.9rem;margin:0 0 12px;'>"
                "Every documented bid change and campaign note — with ROAS, spend, and orders "
                "<b>before</b> (Period 1) and <b>after</b> (Period 2) to measure real impact.</p>",
                unsafe_allow_html=True,
            )

            # ── Manual Bid Change Logger ───────────────────────────────────────
            with st.expander("➕ Log a Retroactive Bid Change", expanded=False):
                _log_camps = get_campaigns_with_snapshots()
                _log_markets_all = []
                try:
                    _lm_conn = __import__("db.database", fromlist=["get_conn"]).get_conn()
                    _log_markets_all = sorted(
                        r[0] for r in _lm_conn.execute(
                            "SELECT DISTINCT marketplace FROM placement_snapshots ORDER BY marketplace"
                        ).fetchall()
                    )
                    _lm_conn.close()
                except Exception:
                    pass

                _lc1, _lc2 = st.columns(2)
                with _lc1:
                    _log_mkt = st.selectbox(
                        "Marketplace",
                        _log_markets_all or ["amazon.com"],
                        key="log_bc_marketplace",
                    )
                    _log_date = st.date_input(
                        "Date of change",
                        value=None,
                        key="log_bc_date",
                        help="The date you applied the change in Amazon's Advertising Console.",
                    )
                    _log_pl = st.selectbox(
                        "Placement",
                        ["Top of Search", "Rest of Search", "Product Pages"],
                        key="log_bc_placement",
                    )

                with _lc2:
                    # If we have campaign names from snapshots, show a selectbox with free-text option
                    _log_camp_input = st.selectbox(
                        "Campaign",
                        [""] + get_campaigns_with_snapshots(_log_mkt),
                        key="log_bc_campaign",
                        help="Pick from campaigns that already have uploaded report data.",
                    )
                    _lb3, _lb4 = st.columns(2)
                    with _lb3:
                        _log_bid_before = st.number_input(
                            "Bid % Before",
                            min_value=0, max_value=900, value=0, step=10,
                            key="log_bc_before",
                            help="e.g. 0 = no adjustment, 100 = +100%",
                        )
                    with _lb4:
                        _log_bid_after = st.number_input(
                            "Bid % After",
                            min_value=0, max_value=900, value=0, step=10,
                            key="log_bc_after",
                            help="e.g. 50 = +50%, 0 = removed adjustment",
                        )

                _log_note = st.text_area(
                    "📝 Note (optional)",
                    placeholder="e.g. Reduced bid after ROAS dropped below breakeven…",
                    key="log_bc_note",
                    height=68,
                )

                st.caption(
                    "💡 ROAS and spend for Period 1 will be pulled automatically "
                    "from the most recent uploaded report on or before the change date."
                )

                if st.button("💾 Save Bid Change", key="log_bc_save", type="primary"):
                    if not _log_camp_input:
                        st.error("Please select a campaign.")
                    elif _log_date is None:
                        st.error("Please pick a date.")
                    elif _log_bid_before == _log_bid_after:
                        st.error("Before and After bid % are the same — nothing to record.")
                    else:
                        _log_status = log_manual_bid_change(
                            campaign_name=_log_camp_input,
                            placement_type=_log_pl,
                            marketplace=_log_mkt,
                            change_date=str(_log_date),
                            bid_before=int(_log_bid_before),
                            bid_after=int(_log_bid_after),
                            notes=_log_note.strip() or None,
                        )
                        if _log_status == "saved":
                            st.success(
                                f"✅ Logged: **{_log_camp_input}** / {_log_pl} — "
                                f"{_log_bid_before}% → {_log_bid_after}% on {_log_date}"
                            )
                            st.rerun()
                        elif _log_status == "duplicate":
                            st.warning("⚠️ This exact change was already recorded.")
                        else:
                            st.error(_log_status)

            st.divider()

            # ── Marketplace filter ─────────────────────────────────────────────
            _uc_all = get_unified_changes()
            _bc_mp  = "All"
            if not _uc_all.empty:
                _uc_markets = ["All"] + sorted(_uc_all["marketplace"].dropna().unique().tolist())
                _bc_mp = st.selectbox("Marketplace", _uc_markets, key="history_marketplace_filter")

            _imp_df = get_all_bid_effectiveness(None if _bc_mp == "All" else _bc_mp)
            if _imp_df.empty:
                st.info("No bid changes to show impact for yet.")
            else:
                # Summary KPIs
                _n_total    = len(_imp_df)
                _n_improved = (_imp_df["result"] == "✅").sum()
                _n_worsened = (_imp_df["result"] == "❌").sum()
                _n_flat     = (_imp_df["result"] == "➡️").sum()
                _n_pending  = (_imp_df["result"] == "⏳").sum()
                _n_noted    = (_imp_df["result"] == "📝").sum()

                _ik1, _ik2, _ik3, _ik4, _ik5, _ik6 = st.columns(6)
                _ik1.metric("Total",        _n_total)
                _ik2.metric("✅ Improved",  _n_improved)
                _ik3.metric("❌ Worsened",  _n_worsened)
                _ik4.metric("➡️ Flat",      _n_flat)
                _ik5.metric("⏳ Pending",   _n_pending)
                _ik6.metric("📝 Note only", _n_noted)

                st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)

                # Result filter
                _imp_filter = st.multiselect(
                    "Filter by result",
                    ["✅", "❌", "➡️", "⏳", "📝"],
                    default=[],
                    key="impact_filter",
                    placeholder="All results",
                )
                _imp_show = _imp_df[_imp_df["result"].isin(_imp_filter)] if _imp_filter else _imp_df

                _cur_sym = "£" if (_bc_mp not in ("All", "amazon.com", None)) else "$"

                st.dataframe(
                    _imp_show,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "change_date":   st.column_config.TextColumn("Date",         width=100),
                        "campaign":      st.column_config.TextColumn("Campaign",     width=230),
                        "placement":     st.column_config.TextColumn("Placement",    width=110),
                        "marketplace":   st.column_config.TextColumn("Marketplace",  width=100),
                        "notes":         st.column_config.TextColumn("📝 Note",      width=180),
                        "bid_before":    st.column_config.NumberColumn("Was %",      format="%d%%", width=65),
                        "bid_after":     st.column_config.NumberColumn("Now %",      format="%d%%", width=65),
                        "roas_p1":       st.column_config.NumberColumn("ROAS P1",   format="%.2fx", width=82),
                        "spend_p1":      st.column_config.NumberColumn("Spend P1",  format=f"{_cur_sym}%.2f", width=85),
                        "purchases_p1":  st.column_config.NumberColumn("Orders P1", format="%d",   width=78),
                        "p2_date":       st.column_config.TextColumn("P2 Date",     width=95),
                        "roas_p2":       st.column_config.NumberColumn("ROAS P2",   format="%.2fx", width=82),
                        "spend_p2":      st.column_config.NumberColumn("Spend P2",  format=f"{_cur_sym}%.2f", width=85),
                        "purchases_p2":  st.column_config.NumberColumn("Orders P2", format="%d",   width=78),
                        "delta_roas":    st.column_config.NumberColumn("Δ ROAS",    format="%+.2fx", width=82),
                        "result":        st.column_config.TextColumn("Result",      width=60),
                    },
                )

                st.download_button(
                    "⬇️ Download Impact Report",
                    data=_imp_show.to_csv(index=False).encode(),
                    file_name="bid_impact_report.csv",
                    mime="text/csv",
                    key="impact_dl",
                )

                # ── Delete a logged bid-change entry ──────────────────────────
                _bc_only = _imp_df[_imp_df["bid_before"].notna()].copy()
                if not _bc_only.empty:
                    def _fmt_bid(v):
                        try:
                            return f"{int(round(float(v)))}%"
                        except (TypeError, ValueError):
                            return "N/A"
                    with st.expander("🗑️ Delete a logged bid change", expanded=False):
                        _bc_only["_label"] = (
                            _bc_only["change_date"].astype(str) + "  ·  " +
                            _bc_only["campaign"].str[:50] + "  ·  " +
                            _bc_only["placement"] + "  ·  " +
                            _bc_only["bid_before"].apply(_fmt_bid) + " → " +
                            _bc_only["bid_after"].apply(_fmt_bid)
                        )
                        _del_choice = st.selectbox(
                            "Select entry to delete",
                            options=_bc_only.index.tolist(),
                            format_func=lambda i: _bc_only.loc[i, "_label"],
                            key="del_bc_choice",
                        )
                        _del_row = _bc_only.loc[_del_choice]
                        if st.button("🗑️ Delete", key="del_bc_confirm", type="secondary"):
                            _n_del = delete_bid_change(
                                campaign_name=_del_row["campaign"],
                                placement_type=_del_row["placement"],
                                marketplace=_del_row["marketplace"],
                                report_date=_del_row["change_date"],
                            )
                            if _n_del:
                                st.success("✅ Deleted.")
                                st.rerun()
                            else:
                                st.error("Not found — may already be deleted.")

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

                _bad_roas_alerts = get_bad_roas_campaigns(_alerts_market)

                _ac1, _ac2, _ac3, _ac4 = st.columns(4)
                _ac1.metric("📸 Snapshots stored",
                    _alerts_get_conn().execute(
                        "SELECT COUNT(DISTINCT snapshot_date) FROM campaign_performance WHERE marketplace = ?",
                        (_alerts_market,)
                    ).fetchone()[0]
                )
                _ac2.metric("🔴 Regressions",    len(_neg_alerts))
                _ac3.metric("🟢 Improvements",   len(_pos_alerts))
                _ac4.metric("🟠 Chronic Bad ROAS", len(_bad_roas_alerts))

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
                            st.code(_bf["campaign"], language=None)
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
                            st.code(_imp["campaign"], language=None)
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

                # ── Chronic Bad ROAS block ─────────────────────────────────────
                if _bad_roas_alerts:
                    st.divider()
                    st.warning(
                        f"🟠 **{len(_bad_roas_alerts)} placement(s) with chronic bad ROAS** — "
                        f"below breakeven in both the current and previous snapshot (no dramatic change, just consistently losing)."
                    )
                    for _br in _bad_roas_alerts:
                        with st.expander(
                            f"🟠 {_br['campaign']} — {_br['placement']} "
                            f"(ROAS {_br['before_roas']}x → {_br['after_roas']}x, breakeven {_br['breakeven_roas']}x)",
                            expanded=False
                        ):
                            st.code(_br["campaign"], language=None)
                            _br1, _br2, _br3, _br4 = st.columns(4)
                            _br1.metric("ROAS (prev)",    f"{_br['before_roas']}x")
                            _br2.metric("ROAS (latest)",  f"{_br['after_roas']}x",
                                        delta=f"{_br['after_roas'] - _br['before_roas']:+.2f}x",
                                        delta_color="off")
                            _br3.metric("Breakeven ROAS", f"{_br['breakeven_roas']}x")
                            _br4.metric("Spend",          f"${_br['spend']:.2f}")
                            st.caption(
                                f"Purchases: {_br['purchases']} · "
                                f"Profit: ${_br['total_profit']:.0f} · "
                                f"Snapshots: {_br['before_date']} → {_br['after_date']}"
                            )
                            st.caption("💡 Review placement bid or consider pausing this placement.")

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
                    record_placement_snapshots(results, _snap_date, detected_marketplace)
                    record_bid_changes(results, _snap_date, detected_marketplace)

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
# TAB 5 — AMAZON TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════════
with tab_amazon:
    from db.amazon_module import render_amazon_upload_ui, init_amazon_tables, MONTHS
    from db.database import get_conn as _amz_get_conn

    _amz_conn = _amz_get_conn()
    init_amazon_tables(_amz_conn)   # no-op if tables exist

    st.markdown("## 💰 Amazon P&L")

    # ── FX: pounce.db schema = marketplace/rate  where rate = local-per-USD ──────
    # e.g. amazon.ca=1.36 means 1 USD = 1.36 CAD  →  to_usd = amount / rate
    _fx_mp = {r[0]: r[1] for r in _amz_conn.execute(
        "SELECT marketplace, rate FROM fx_rates"
    ).fetchall()}
    _CURRENCY_TO_MP = {
        "USD": "amazon.com",    "CAD": "amazon.ca",
        "GBP": "amazon.co.uk", "EUR": "amazon.de",
        "AUD": "amazon.com.au","SEK": "amazon.se",
        "PLN": "amazon.pl",    "MXN": "amazon.com.mx",
        "BRL": "amazon.com.br",
    }

    def _to_usd(amount, currency):
        mp   = _CURRENCY_TO_MP.get(currency)
        rate = _fx_mp.get(mp) if mp else None
        if rate:
            return amount / rate
        return amount  # unknown currency — pass through as-is

    # ── Upload section ────────────────────────────────────────────────────────────
    with st.expander("📤 Upload Transaction View", expanded=False):
        render_amazon_upload_ui(_amz_conn)

    st.divider()

    # ── Check data ────────────────────────────────────────────────────────────────
    _tx_count = _amz_conn.execute("SELECT COUNT(*) FROM amazon_transactions").fetchone()[0]
    if _tx_count == 0:
        st.info("No transactions yet — upload Transaction View files above.")
    else:
        # ── Filters ───────────────────────────────────────────────────────────────
        _amz_years = [r[0] for r in _amz_conn.execute(
            "SELECT DISTINCT year FROM amazon_transactions ORDER BY year DESC"
        ).fetchall()]
        _amz_all_mps = [r[0] for r in _amz_conn.execute(
            "SELECT DISTINCT marketplace FROM amazon_transactions ORDER BY marketplace"
        ).fetchall()]

        _fa1, _fa2 = st.columns([1, 3])
        with _fa1:
            _sel_year = st.selectbox("Year", _amz_years, key="amz_year")
        with _fa2:
            _sel_mps = st.multiselect(
                "Marketplaces", _amz_all_mps, default=_amz_all_mps, key="amz_mps",
                placeholder="All marketplaces"
            )
        if not _sel_mps:
            _sel_mps = _amz_all_mps

        _mp_ph = ",".join("?" * len(_sel_mps))
        _amz_params = [_sel_year] + _sel_mps

        # ── Aggregate by type/month/currency ──────────────────────────────────────
        _rows = _amz_conn.execute(f"""
            SELECT tx_type, month, currency,
                   SUM(gross_sales)   AS gross,
                   SUM(promo_rebates) AS promos,
                   SUM(amazon_fees)   AS fees,
                   SUM(withheld_tax)  AS withheld,
                   SUM(net_total)     AS net,
                   COUNT(*)           AS cnt
            FROM amazon_transactions
            WHERE year=?
              AND tx_type NOT IN ('Transfer','Debt')
              AND marketplace IN ({_mp_ph})
            GROUP BY tx_type, month, currency
        """, _amz_params).fetchall()

        # Totals + monthly buckets
        _gross_tot = _promos_tot = _fees_tot = _net_tot = _ref_tot = 0.0
        _monthly = {m: {"gross": 0.0, "fees": 0.0, "refunds": 0.0, "net": 0.0}
                    for m in MONTHS}

        for r in _rows:
            m, cur = r["month"], r["currency"]
            if m not in MONTHS:
                continue
            g   = _to_usd(r["gross"]   or 0, cur)
            f   = _to_usd(r["fees"]    or 0, cur)
            n   = _to_usd(r["net"]     or 0, cur)
            pr  = _to_usd(r["promos"]  or 0, cur)
            _net_tot   += n
            _fees_tot  += f
            _promos_tot += pr
            _monthly[m]["net"]  += n
            _monthly[m]["fees"] += f
            if r["tx_type"] == "Order":
                _gross_tot         += g
                _monthly[m]["gross"] += g
            if r["tx_type"] == "Refund":
                _ref_tot              += n
                _monthly[m]["refunds"] += abs(n)

        # ── KPI row ───────────────────────────────────────────────────────────────
        _k1, _k2, _k3, _k4, _k5 = st.columns(5)
        _k1.metric("💵 Gross Sales",  f"${_gross_tot:,.0f}")
        _k2.metric("🔁 Refunds",      f"${abs(_ref_tot):,.0f}")
        _k3.metric("💸 Amazon Fees",  f"${abs(_fees_tot):,.0f}")
        _k4.metric("🏷️ Promos",       f"${abs(_promos_tot):,.0f}")
        _k5.metric("✅ Net Received",  f"${_net_tot:,.0f}")

        st.markdown("<div style='margin:4px 0'></div>", unsafe_allow_html=True)

        # ── MoM Alerts ───────────────────────────────────────────────────────────
        def _build_alerts(conn, threshold, all_mps, ya, ma, yb, mb):
            """Compare month (ya,ma) [newer] vs (yb,mb) [baseline]."""
            _ST = "','".join([
                "FBA Inventory Fee","Versand durch Amazon Lagergebühr",
                "Tarifas de inventario de Logística de Amazon",
                "Frais de stock Expédié par Amazon","Costo di stoccaggio Logistica di Amazon",
            ])
            _AV = ("LOWER(product_details) LIKE '%advertis%' OR product_details='Werbekosten' "
                   "OR product_details LIKE '%publicité%' OR product_details LIKE '%pubblicità%' "
                   "OR product_details='Koszt reklamy'")

            def _usd_sum(rows):
                return sum(_to_usd(float(r[1] or 0), r[0]) for r in rows)

            def _q(sql, p):
                return conn.execute(sql, p).fetchall()

            def _vals(y, m_name, mp):
                p = [y, m_name, mp]
                sales = _usd_sum(_q(
                    "SELECT currency,SUM(gross_sales) FROM amazon_transactions "
                    "WHERE year=? AND month=? AND tx_type='Order' AND marketplace=? GROUP BY currency", p))
                fees = _usd_sum(_q(
                    "SELECT currency,SUM(ABS(amazon_fees)) FROM amazon_transactions "
                    "WHERE year=? AND month=? AND tx_type='Order' AND marketplace=? GROUP BY currency", p))
                refunds = _usd_sum(_q(
                    "SELECT currency,SUM(ABS(net_total)) FROM amazon_transactions "
                    "WHERE year=? AND month=? AND tx_type='Refund' AND marketplace=? GROUP BY currency", p))
                adv = _usd_sum(_q(
                    f"SELECT currency,SUM(ABS(net_total)) FROM amazon_transactions "
                    f"WHERE year=? AND month=? AND tx_type='Service Fee' AND ({_AV}) "
                    f"AND marketplace=? GROUP BY currency", p))
                storage = _usd_sum(_q(
                    f"SELECT currency,SUM(ABS(net_total)) FROM amazon_transactions "
                    f"WHERE year=? AND month=? AND tx_type IN ('{_ST}') "
                    f"AND LOWER(COALESCE(product_details,'')) NOT LIKE '%long%' "
                    f"AND marketplace=? GROUP BY currency", p))
                lt_storage = _usd_sum(_q(
                    f"SELECT currency,SUM(ABS(net_total)) FROM amazon_transactions "
                    f"WHERE year=? AND month=? AND tx_type IN ('{_ST}') "
                    f"AND LOWER(COALESCE(product_details,'')) LIKE '%long%' "
                    f"AND marketplace=? GROUP BY currency", p))
                coupons = _usd_sum(_q(
                    "SELECT currency,SUM(ABS(net_total)) FROM amazon_transactions "
                    "WHERE year=? AND month=? AND tx_type='Service Fee' "
                    "AND LOWER(product_details) LIKE '%coupon%' "
                    "AND marketplace=? GROUP BY currency", p))
                return {
                    "sales": sales, "fees": fees, "refunds": refunds,
                    "adv": adv, "storage": storage, "lt_storage": lt_storage,
                    "coupons": coupons,
                }

            ma_name = MONTHS[ma - 1]
            mb_name = MONTHS[mb - 1]
            alerts = []
            for mp in all_mps:
                va = _vals(ya, ma_name, mp)
                vb = _vals(yb, mb_name, mp)

                rate_metrics = []
                if vb["sales"] > 10 and va["sales"] > 10:
                    rate_metrics += [
                        ("fee_rate",    "🏦 Fee Rate",    va["fees"]/va["sales"],    vb["fees"]/vb["sales"],    True),
                        ("refund_rate", "🔁 Refund Rate", va["refunds"]/va["sales"], vb["refunds"]/vb["sales"], True),
                    ]

                abs_metrics = [
                    ("sales",      "💵 Sales",       va["sales"],      vb["sales"],      False),
                    ("adv",        "📢 Advertising", va["adv"],        vb["adv"],        True),
                    ("storage",    "📦 Storage",     va["storage"],    vb["storage"],    True),
                    ("lt_storage", "📦 LT Storage",  va["lt_storage"], vb["lt_storage"], True),
                    ("coupons",    "🏷️ Coupons",     va["coupons"],    vb["coupons"],    True),
                ]

                for mkey, mlabel, v_new, v_old, is_cost in rate_metrics + abs_metrics:
                    if v_old < 5:
                        continue
                    pct = (v_new - v_old) / v_old
                    if abs(pct) < threshold:
                        continue
                    bad = (pct > 0) == is_cost
                    is_rate = mkey in ("fee_rate", "refund_rate")
                    alerts.append({
                        "mp": mp, "metric": mlabel, "is_rate": is_rate,
                        "val_a": v_new, "val_b": v_old, "pct": pct, "bad": bad,
                    })

            return sorted(alerts, key=lambda x: abs(x["pct"]), reverse=True)

        with st.expander("🔔 Month-over-Month Alerts", expanded=True):
            from datetime import date as _date
            _today = _date.today()
            _cy, _cm = _today.year, _today.month

            def _prev_ym(y, m):
                return (y-1, 12) if m == 1 else (y, m-1)

            _py, _pm   = _prev_ym(_cy, _cm)   # last full month
            _p2y, _p2m = _prev_ym(_py, _pm)   # month before that

            _cmp_options = [
                "Last month vs previous month",
                "Last month vs same month last year",
                "Current month vs last month",
            ]
            _ctrl_col, _thr_col = st.columns([4, 1])
            with _ctrl_col:
                _cmp_mode = st.radio(
                    "Compare", _cmp_options, index=0,
                    horizontal=True, key="alert_cmp_mode",
                    label_visibility="collapsed",
                )
            with _thr_col:
                _alert_thr = st.number_input(
                    "Threshold %", min_value=5, max_value=200, value=50,
                    step=5, key="alert_threshold",
                    help="Alert when change exceeds this %"
                ) / 100.0

            # Resolve (year_a, month_a) [newer] vs (year_b, month_b) [baseline]
            if _cmp_mode == _cmp_options[0]:   # last month vs previous month
                _ya, _ma = _py,  _pm
                _yb, _mb = _p2y, _p2m
            elif _cmp_mode == _cmp_options[1]: # last month vs same month last year
                _ya, _ma = _py,      _pm
                _yb, _mb = _py - 1,  _pm
            else:                              # current month vs last month
                _ya, _ma = _cy, _cm
                _yb, _mb = _py, _pm

            _period_label = (
                f"{MONTHS[_mb-1]} {_yb} → {MONTHS[_ma-1]} {_ya}"
            )
            st.caption(f"📅 Comparing **{_period_label}**")

            _alerts = _build_alerts(_amz_conn, _alert_thr, _sel_mps, _ya, _ma, _yb, _mb)
            if not _alerts:
                st.success(f"✅ No significant changes above {int(_alert_thr*100)}% threshold.")
            else:
                _alert_cols = st.columns(min(len(_alerts), 3))
                for i, al in enumerate(_alerts):
                    pct_str = f"+{al['pct']*100:.0f}%" if al['pct'] > 0 else f"{al['pct']*100:.0f}%"
                    arrow   = "↑" if al['pct'] > 0 else "↓"
                    is_adv  = al["metric"] == "📢 Advertising"
                    color   = "#fff3cd" if is_adv else ("#ffd7d7" if al['bad'] else "#d4edda")
                    icon    = "🟠" if is_adv else ("🔴" if al['bad'] else "🟢")
                    if al.get("is_rate"):
                        val_fmt = f"{al['val_b']*100:.1f}% → {al['val_a']*100:.1f}%"
                    else:
                        val_fmt = f"${al['val_b']:,.0f} → ${al['val_a']:,.0f}"
                    _alert_cols[i % 3].markdown(
                        f"<div style='background:{color};padding:8px 10px;border-radius:6px;"
                        f"margin-bottom:6px;font-size:0.82rem'>"
                        f"{icon} <b>{al['mp']} · {al['metric']}</b><br>"
                        f"{arrow} {pct_str}<br>"
                        f"<span style='color:#555'>{val_fmt}</span>"
                        f"</div>", unsafe_allow_html=True
                    )

        # ── Chart controls ────────────────────────────────────────────────────────
        _METRIC_LABELS = {
            "💵 Sales":             "sales",
            "🔁 Refunds":           "refunds",
            "🏭 COGS":              "cogs",
            "📢 Advertising":       "advertising",
            "🏦 Amazon Fees":       "amazon_fees",
            "📦 Storage":           "storage",
            "📦 Long-term Storage": "lt_storage",
            "🧾 VAT / Tax":         "vat",
            "✅ Net Revenue":       "net",
            "🏷️ Promos":            "promos",
        }

        _gc1, _gc2 = st.columns([3, 1])
        with _gc1:
            _metric_label = st.radio(
                "Show", list(_METRIC_LABELS.keys()),
                horizontal=True, key="amz_metric",
                index=0,
            )
        with _gc2:
            _yoy = st.checkbox("Compare to last year", value=False, key="amz_yoy")

        _sel_metric = _METRIC_LABELS[_metric_label]

        def _monthly_metric(conn, year, metric, mps):
            """Return {month: usd_value} for the given metric/year/marketplaces."""
            # COGS: same cost map as Inventory tab (part_id_1/part_id_2 via calc_product_cost)
            # amazon_transactions → orders (asin+qty) → get_asin_cost_map() for unit cost
            if metric == "cogs":
                from db.amazon_module import get_sellerboard_cost_map as _get_sb_costs
                # Only use SellerBoard costs (full landed COGS per unit).
                # ASINs not in sellerboard_cogs contribute 0 (old DB costs excluded).
                _sb_map = _get_sb_costs(conn)        # {ASIN_UPPER: cost_usd}
                ph = ",".join("?" * len(mps))
                _unit_rows = conn.execute(f"""
                    SELECT at.month, o.asin, SUM(o.quantity) AS units
                    FROM amazon_transactions at
                    JOIN orders o ON o.order_id = at.order_id
                    WHERE at.year = ?
                      AND at.tx_type = 'Order'
                      AND at.marketplace IN ({ph})
                      AND o.order_status NOT IN ('Cancelled', 'Pending')
                    GROUP BY at.month, o.asin
                """, [year] + mps).fetchall()
                _monthly_cogs: dict = {}
                for _ur in _unit_rows:
                    _m = _ur[0]
                    if _m not in MONTHS:
                        continue
                    _asin = str(_ur[1] or "").upper()
                    _unit_cost = _sb_map.get(_asin, 0.0)
                    _monthly_cogs[_m] = _monthly_cogs.get(_m, 0.0) + float(_ur[2] or 0) * _unit_cost
                return _monthly_cogs

            ph = ",".join("?" * len(mps))
            p  = [year] + mps
            # Storage tx_type variants across all Amazon marketplaces
            _STORAGE_TYPES = "','".join([
                "FBA Inventory Fee",                               # EN
                "Versand durch Amazon Lagergebühr",                # DE
                "Tarifas de inventario de Logística de Amazon",    # ES
                "Frais de stock Expédié par Amazon",               # FR
                "Costo di stoccaggio Logistica di Amazon",         # IT
                "FBA Inventory Fee - Correction",
                "FBA Inventory Fee - Reversal",
            ])
            # Transfer/Debt equivalents to exclude from net
            _TRANSFER_TYPES = "','".join([
                "Transfer","Debt",
                "Übertrag","Verbindlichkeit",    # DE
                "Transfert","Solde négatif",      # FR
                "Transferir","Saldo descubierto", # ES
                "Overboeking","Schuld",           # NL
                "Saldo negativo",                  # IT
            ])
            # All queries group by month + currency so we can apply FX → USD
            _Q = {
                "sales":        f"SELECT month, currency, SUM(gross_sales)    FROM amazon_transactions WHERE year=? AND tx_type='Order' AND marketplace IN ({ph}) GROUP BY month, currency",
                "refunds":      f"SELECT month, currency, SUM(ABS(net_total)) FROM amazon_transactions WHERE year=? AND tx_type='Refund' AND marketplace IN ({ph}) GROUP BY month, currency",
                "amazon_fees":  f"SELECT month, currency, SUM(ABS(amazon_fees)) FROM amazon_transactions WHERE year=? AND tx_type='Order' AND marketplace IN ({ph}) GROUP BY month, currency",
                "advertising": f"""SELECT month, currency, SUM(ABS(net_total))
                    FROM amazon_transactions
                    WHERE year=? AND tx_type='Service Fee'
                      AND (
                        LOWER(product_details) LIKE '%advertis%'
                        OR product_details = 'Werbekosten'
                        OR product_details LIKE '%publicité%'
                        OR product_details LIKE '%pubblicità%'
                        OR product_details = 'Koszt reklamy'
                      )
                      AND marketplace IN ({ph})
                    GROUP BY month, currency""",
                "storage":    f"SELECT month, currency, SUM(ABS(net_total)) FROM amazon_transactions WHERE year=? AND tx_type IN ('{_STORAGE_TYPES}') AND LOWER(COALESCE(product_details,'')) NOT LIKE '%long%' AND marketplace IN ({ph}) GROUP BY month, currency",
                "lt_storage": f"SELECT month, currency, SUM(ABS(net_total)) FROM amazon_transactions WHERE year=? AND tx_type IN ('{_STORAGE_TYPES}') AND LOWER(COALESCE(product_details,'')) LIKE '%long%' AND marketplace IN ({ph}) GROUP BY month, currency",
                "vat":        f"SELECT month, currency, SUM(ABS(withheld_tax)) FROM amazon_transactions WHERE year=? AND marketplace IN ({ph}) GROUP BY month, currency",
                "net":        f"SELECT month, currency, SUM(net_total) FROM amazon_transactions WHERE year=? AND tx_type NOT IN ('{_TRANSFER_TYPES}') AND marketplace IN ({ph}) GROUP BY month, currency",
                "promos":     f"SELECT month, currency, SUM(ABS(promo_rebates)) FROM amazon_transactions WHERE year=? AND marketplace IN ({ph}) GROUP BY month, currency",
            }
            rows = conn.execute(_Q[metric], p).fetchall()
            _out: dict = {}
            for r in rows:
                _m, _cur, _val = r[0], r[1], float(r[2] or 0)
                if _m not in MONTHS:
                    continue
                _out[_m] = _out.get(_m, 0.0) + _to_usd(_val, _cur or "USD")
            return _out

        # Query data for chart
        _curr_vals = _monthly_metric(_amz_conn, _sel_year,     _sel_metric, _sel_mps)
        _prev_vals = _monthly_metric(_amz_conn, _sel_year - 1, _sel_metric, _sel_mps) if _yoy else {}

        _chart_months = [m for m in MONTHS if m in _curr_vals or m in _prev_vals]

        if _chart_months:
            import altair as alt
            _chart_rows = []
            for m in _chart_months:
                if m in _curr_vals:
                    _chart_rows.append({"Month": m, "Year": str(_sel_year),     "Value": _curr_vals[m]})
                if m in _prev_vals:
                    _chart_rows.append({"Month": m, "Year": str(_sel_year - 1), "Value": _prev_vals[m]})

            _chart_df = pd.DataFrame(_chart_rows)
            _year_domain = [str(_sel_year), str(_sel_year - 1)] if _yoy else [str(_sel_year)]
            _year_colors = ["#0969da", "#cf222e"] if _yoy else ["#0969da"]

            _chart = (
                alt.Chart(_chart_df)
                .mark_line(point=alt.OverlayMarkDef(size=60))
                .encode(
                    x=alt.X("Month:O", sort=MONTHS, title="",
                             axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("Value:Q", title="USD",
                             axis=alt.Axis(format="$,.0f", labelFontSize=11)),
                    color=alt.Color("Year:N",
                        scale=alt.Scale(domain=_year_domain, range=_year_colors),
                        legend=alt.Legend(title="Year", orient="top-right")
                    ),
                    tooltip=[
                        alt.Tooltip("Month:O"),
                        alt.Tooltip("Year:N"),
                        alt.Tooltip("Value:Q", format="$,.0f", title=_metric_label),
                    ]
                )
                .properties(height=280)
            )
            st.altair_chart(_chart, use_container_width=True)

        # ── Monthly summary table ─────────────────────────────────────────────────
        _active = [m for m in MONTHS if _monthly[m]["net"] != 0]
        if _active:
            with st.expander("📋 Monthly P&L Table", expanded=False):
                _mdf = pd.DataFrame([{
                    "Month":         m,
                    "Gross $":       round(_monthly[m]["gross"]),
                    "Refunds $":     round(_monthly[m]["refunds"]),
                    "Amazon Fees $": round(abs(_monthly[m]["fees"])),
                    "Net $":         round(_monthly[m]["net"]),
                } for m in _active])
                st.dataframe(
                    _mdf, use_container_width=True, hide_index=True,
                    column_config={
                        "Month":         st.column_config.TextColumn(width=60),
                        "Gross $":       st.column_config.NumberColumn(format="$%d", width=100),
                        "Refunds $":     st.column_config.NumberColumn(format="$%d", width=90),
                        "Amazon Fees $": st.column_config.NumberColumn(format="$%d", width=110),
                        "Net $":         st.column_config.NumberColumn(format="$%d", width=90),
                    }
                )

        # ── By marketplace ────────────────────────────────────────────────────────
        _mp_rows = _amz_conn.execute(f"""
            SELECT marketplace, currency,
                   SUM(CASE WHEN tx_type='Order' THEN gross_sales ELSE 0 END) AS gross,
                   SUM(CASE WHEN tx_type='Refund' THEN net_total  ELSE 0 END) AS refunds,
                   SUM(amazon_fees)  AS fees,
                   SUM(net_total)    AS net,
                   COUNT(*)          AS cnt
            FROM amazon_transactions
            WHERE year=?
              AND tx_type NOT IN ('Transfer','Debt')
              AND marketplace IN ({_mp_ph})
            GROUP BY marketplace, currency
            ORDER BY ABS(SUM(net_total)) DESC
        """, _amz_params).fetchall()

        if _mp_rows:
            st.markdown("**By Marketplace (USD)**")
            _mp_data = []
            for r in _mp_rows:
                cur = r["currency"]
                _mp_data.append({
                    "Marketplace":  r["marketplace"],
                    "Cur":          cur,
                    "Gross $":      round(_to_usd(r["gross"]   or 0, cur)),
                    "Refunds $":    round(abs(_to_usd(r["refunds"] or 0, cur))),
                    "Fees $":       round(abs(_to_usd(r["fees"]    or 0, cur))),
                    "Net $":        round(_to_usd(r["net"]     or 0, cur)),
                    "Txns":         r["cnt"],
                })
            st.dataframe(
                pd.DataFrame(_mp_data), use_container_width=True, hide_index=True,
                column_config={
                    "Marketplace": st.column_config.TextColumn(width=80),
                    "Cur":         st.column_config.TextColumn(width=50),
                    "Gross $":     st.column_config.NumberColumn(format="$%d", width=90),
                    "Refunds $":   st.column_config.NumberColumn(format="$%d", width=90),
                    "Fees $":      st.column_config.NumberColumn(format="$%d", width=90),
                    "Net $":       st.column_config.NumberColumn(format="$%d", width=90),
                    "Txns":        st.column_config.NumberColumn(format="%d",  width=70),
                }
            )

        # ── Transaction detail search ─────────────────────────────────────────────
        with st.expander("🔍 Transaction Search", expanded=False):
            _sq1, _sq2, _sq3 = st.columns(3)
            with _sq1:
                _s_type = st.selectbox("Type", ["All","Order","Refund","Amazon Fees",
                                                 "Service Fee","Adjustment","FBA Inventory Fee"],
                                       key="amz_s_type")
            with _sq2:
                _s_month = st.selectbox("Month", ["All"] + MONTHS, key="amz_s_month")
            with _sq3:
                _s_mp = st.selectbox("Marketplace", ["All"] + _sel_mps, key="amz_s_mp")

            _s_clauses = ["year=?"]
            _s_params  = [_sel_year]
            if _s_type  != "All": _s_clauses.append("tx_type=?");      _s_params.append(_s_type)
            if _s_month != "All": _s_clauses.append("month=?");        _s_params.append(_s_month)
            if _s_mp    != "All": _s_clauses.append("marketplace=?");  _s_params.append(_s_mp)

            _s_df = pd.read_sql_query(f"""
                SELECT tx_date AS Date, tx_type AS Type, marketplace AS Mkt,
                       currency AS Cur, product_details AS Description,
                       gross_sales AS Gross, promo_rebates AS Promos,
                       amazon_fees AS Fees, net_total AS Net
                FROM amazon_transactions
                WHERE {' AND '.join(_s_clauses)}
                ORDER BY tx_date DESC LIMIT 500
            """, _amz_conn, params=_s_params)

            st.caption(f"{len(_s_df):,} transactions (max 500)")
            st.dataframe(
                _s_df, use_container_width=True, hide_index=True,
                column_config={
                    "Date":        st.column_config.TextColumn(width=90),
                    "Type":        st.column_config.TextColumn(width=120),
                    "Mkt":         st.column_config.TextColumn(width=55),
                    "Cur":         st.column_config.TextColumn(width=45),
                    "Description": st.column_config.TextColumn(width=260),
                    "Gross":       st.column_config.NumberColumn(format="%.2f", width=80),
                    "Promos":      st.column_config.NumberColumn(format="%.2f", width=75),
                    "Fees":        st.column_config.NumberColumn(format="%.2f", width=75),
                    "Net":         st.column_config.NumberColumn(format="%.2f", width=75),
                }
            )

    _amz_conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — CASH FORECAST
# ══════════════════════════════════════════════════════════════════════════════
with tab_cashflow:
    from db.cashflow_module import (
        init_cashflow_tables, get_accounts, update_account_balance,
        get_items, add_item, update_item, delete_item, change_from_month, add_account,
        build_forecast, _CATEGORIES, MONTHS as CF_MONTHS, COMPANY_LABELS,
        get_setting, set_setting,
    )
    import sqlite3 as _cf_sqlite

    # Cache connection in session_state — avoids reconnecting on every render
    if "_cf_conn" not in st.session_state:
        _cf_conn_new = _cf_sqlite.connect(
            __import__("db.database", fromlist=["DB_PATH"]).DB_PATH,
            check_same_thread=False
        )
        init_cashflow_tables(_cf_conn_new)
        st.session_state["_cf_conn"] = _cf_conn_new
    _cf_conn = st.session_state["_cf_conn"]

    st.markdown("## 💰 Cash Forecast")

    # ── Settings bar (persisted in DB) ───────────────────────────────────────
    _s1, _s2, _s3, _s4 = st.columns([1, 1, 1, 1])
    with _s1:
        _cf_usd_nis = st.number_input(
            "USD / NIS rate", min_value=2.0, max_value=6.0,
            value=float(get_setting(_cf_conn, "usd_nis")),
            step=0.01, key="cf_usd_nis",
            help="Used to convert ILS ↔ USD in the merged total view"
        )
        if str(_cf_usd_nis) != st.session_state.get("_cf_saved_usd_nis", ""):
            set_setting(_cf_conn, "usd_nis", _cf_usd_nis)
            st.session_state["_cf_saved_usd_nis"] = str(_cf_usd_nis)
    with _s2:
        _cf_growth = st.number_input(
            "Amazon growth %", min_value=-50, max_value=200,
            value=int(get_setting(_cf_conn, "amz_growth")),
            step=5, key="cf_growth",
            help="Applied to last year's Amazon payout as forecast base"
        ) / 100.0
        _cf_growth_pct = int(_cf_growth * 100)
        if str(_cf_growth_pct) != st.session_state.get("_cf_saved_amz_growth", ""):
            set_setting(_cf_conn, "amz_growth", _cf_growth_pct)
            st.session_state["_cf_saved_amz_growth"] = str(_cf_growth_pct)
    with _s3:
        _horizon_opts = [6, 9, 12, 18, 24]
        _saved_horizon = int(get_setting(_cf_conn, "cf_months"))
        _cf_months = st.selectbox(
            "Forecast horizon", _horizon_opts,
            index=_horizon_opts.index(_saved_horizon) if _saved_horizon in _horizon_opts else 2,
            key="cf_months"
        )
        if str(_cf_months) != st.session_state.get("_cf_saved_cf_months", ""):
            set_setting(_cf_conn, "cf_months", _cf_months)
            st.session_state["_cf_saved_cf_months"] = str(_cf_months)
    with _s4:
        _cf_warn_usd = st.number_input(
            "⚠️ Low cash threshold (USD)", min_value=0,
            value=int(get_setting(_cf_conn, "warn_usd")),
            step=5000, key="cf_warn",
            help="Highlight months where total USD equivalent drops below this"
        )
        if str(_cf_warn_usd) != st.session_state.get("_cf_saved_warn_usd", ""):
            set_setting(_cf_conn, "warn_usd", _cf_warn_usd)
            st.session_state["_cf_saved_warn_usd"] = str(_cf_warn_usd)

    st.divider()

    # ── Sub-tabs ──────────────────────────────────────────────────────────────
    _cf_tab_forecast, _cf_tab_accounts, _cf_tab_items = st.tabs([
        "📊 Forecast", "🏦 Accounts & Balances", "📋 Scheduled Items"
    ])

    # ════════════════════════════════════════════════════════════════════════
    # FORECAST VIEW
    # ════════════════════════════════════════════════════════════════════════
    with _cf_tab_forecast:
        import pandas as _cf_pd

        _cf_accounts, _cf_result = build_forecast(
            _cf_conn, _cf_months, _cf_usd_nis, _cf_growth
        )

        # ── Single opening balance = all accounts converted to USD ────────
        def _to_usd_display(amount, currency, rate):
            if currency == "USD":  return amount
            if currency == "ILS":  return amount / rate if rate else 0.0
            return amount

        _opening_usd = sum(
            _to_usd_display(a[4], a[3], _cf_usd_nis) for a in _cf_accounts
        )

        if not _cf_result:
            st.info("No forecast data yet.")
        else:
            _col_labels = [f"{CF_MONTHS[r['month']-1]} {r['year']}" for r in _cf_result]

            # ── Collect all unique income / expense item names ─────────────
            _income_names  = []
            _expense_names = []
            for r in _cf_result:
                for f in r["flows"]:
                    if f["direction"] == "in"  and f["name"] not in _income_names:
                        _income_names.append(f["name"])
                    if f["direction"] == "out" and f["name"] not in _expense_names:
                        _expense_names.append(f["name"])

            # ── Build cash-flow statement table ───────────────────────────
            # Rows: Opening | income items | Total Income | expense items | Total Expenses | Closing
            _cf_row_labels = (
                ["📊 Opening Balance"]
                + [f"  ↑ {n}" for n in _income_names]
                + ["= Total Income"]
                + [f"  ↓ {n}" for n in _expense_names]
                + ["= Total Expenses"]
                + ["💰 Closing Balance"]
            )
            _cf_data   = {lbl: [] for lbl in _cf_row_labels}
            _closings  = []
            _warn_cols = []
            opening    = _opening_usd

            for r in _cf_result:
                # Map flows to USD amounts by name+direction
                flow_usd: dict[tuple, float] = {}
                for f in r["flows"]:
                    key = (f["direction"], f["name"])
                    amt = _to_usd_display(f["amount"], f["currency"], _cf_usd_nis)
                    flow_usd[key] = flow_usd.get(key, 0.0) + amt

                total_in  = sum(v for (d,_), v in flow_usd.items() if d == "in")
                total_out = sum(v for (d,_), v in flow_usd.items() if d == "out")
                closing   = opening + total_in - total_out
                _closings.append(closing)
                if closing < _cf_warn_usd:
                    _warn_cols.append(r["ym"])

                _cf_data["📊 Opening Balance"].append(opening)
                for n in _income_names:
                    _cf_data[f"  ↑ {n}"].append(flow_usd.get(("in", n), 0.0))
                _cf_data["= Total Income"].append(total_in)
                for n in _expense_names:
                    _cf_data[f"  ↓ {n}"].append(flow_usd.get(("out", n), 0.0))
                _cf_data["= Total Expenses"].append(total_out)
                _cf_data["💰 Closing Balance"].append(closing)
                opening = closing

            _df_cf = _cf_pd.DataFrame(_cf_data, index=_col_labels).T

            # ── Style ─────────────────────────────────────────────────────
            def _fmt_usd(v):
                if not isinstance(v, (int, float)):  return v
                if v == 0:  return "—"
                return f"${v:,.0f}" if v >= 0 else f"(${abs(v):,.0f})"

            def _style_cf(df):
                s = _cf_pd.DataFrame("", index=df.index, columns=df.columns)
                for col in df.columns:
                    # Opening / closing rows — bold
                    s.loc["📊 Opening Balance", col] = "font-weight:bold;background:#f0f4ff"
                    # Closing: green or red
                    closing_val = df.loc["💰 Closing Balance", col]
                    if isinstance(closing_val, (int, float)):
                        if closing_val < _cf_warn_usd:
                            s.loc["💰 Closing Balance", col] = "font-weight:bold;background:#ffd7d7"
                        else:
                            s.loc["💰 Closing Balance", col] = "font-weight:bold;background:#d4edda"
                    # Totals — slightly shaded
                    s.loc["= Total Income",    col] = "font-weight:bold;background:#f8fff8"
                    s.loc["= Total Expenses",  col] = "font-weight:bold;background:#fff8f8"
                return s

            st.dataframe(
                _df_cf.style.apply(_style_cf, axis=None).format(_fmt_usd),
                use_container_width=True,
                height=min(80 + 35 * len(_cf_row_labels), 700)
            )

            # ── Warnings ──────────────────────────────────────────────────
            if _warn_cols:
                _warn_labels = [f"{CF_MONTHS[int(c[5:7])-1]} {c[:4]}" for c in _warn_cols]
                st.warning(f"⚠️ Closing balance drops below ${_cf_warn_usd:,} in: **{', '.join(_warn_labels)}**")

            # ── Chart ─────────────────────────────────────────────────────
            st.markdown("#### 💰 Projected Closing Balance (USD)")
            _chart_df = _cf_pd.DataFrame(
                {"Closing Balance": _closings}, index=_col_labels
            )
            st.line_chart(_chart_df, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════════
    # ACCOUNTS & BALANCES — wrapped in @st.fragment for fast saves
    # ════════════════════════════════════════════════════════════════════════
    @st.fragment
    def _cf_accounts_fragment(cf_conn):
        from db.cashflow_module import get_accounts, update_account_balance, add_account

        _cf_accs = get_accounts(cf_conn)

        st.markdown("### Current Balances")
        st.caption("Update whenever you check your accounts (weekly recommended).")

        for acc in _cf_accs:
            aid, aname, acompany, acur, abal, alimit, asort, aupdated = acc
            sym = "$" if acur == "USD" else "₪"
            with st.form(f"cf_acc_form_{aid}", border=False):
                col_name, col_bal, col_btn = st.columns([3, 2, 1])
                col_name.markdown(
                    f"**{aname}** &nbsp; <span style='color:#888;font-size:0.8rem'>"
                    f"{acompany} · {acur}"
                    + (f" · limit {sym}{alimit:,.0f}" if alimit else "")
                    + f" · updated {aupdated[:10]}</span>",
                    unsafe_allow_html=True
                )
                new_bal = col_bal.number_input(
                    f"Balance {aname}", value=float(abal),
                    step=100.0, label_visibility="collapsed",
                    key=f"cf_bal_{aid}", format="%.0f"
                )
                if col_btn.form_submit_button("Save"):
                    update_account_balance(cf_conn, aid, new_bal)
                    st.success(f"✅ {aname} updated to {sym}{new_bal:,.0f}")
                    st.rerun(scope="fragment")

            if alimit and abal < -alimit:
                st.warning(f"⚠️ {aname} is below credit limit! {sym}{abal:,.0f} (limit {sym}{alimit:,.0f})")

        st.divider()
        st.markdown("### Add Account")
        with st.form("cf_add_account"):
            _ac1, _ac2, _ac3, _ac4, _ac5 = st.columns([2,1,1,1,1])
            _new_aname  = _ac1.text_input("Name", placeholder="e.g. Mercury")
            _new_acomp  = _ac2.selectbox("Company", ["LLC","IL"])
            _new_acur   = _ac3.selectbox("Currency", ["USD","ILS"])
            _new_abal   = _ac4.number_input("Balance", value=0.0, step=100.0, format="%.0f")
            _new_alimit = _ac5.number_input("Credit limit", value=0.0, step=1000.0, format="%.0f")
            if st.form_submit_button("➕ Add Account"):
                if _new_aname:
                    add_account(cf_conn, _new_aname, _new_acomp, _new_acur, _new_abal, _new_alimit)
                    st.success(f"✅ Account '{_new_aname}' added.")
                    st.rerun(scope="fragment")

    with _cf_tab_accounts:
        _cf_accounts_fragment(_cf_conn)

    # ════════════════════════════════════════════════════════════════════════
    # SCHEDULED ITEMS  — wrapped in @st.fragment so add/edit/delete only
    # reruns this section, not the entire app (skips build_forecast etc.)
    # ════════════════════════════════════════════════════════════════════════
    @st.fragment
    def _cf_items_fragment(cf_conn):
        from db.cashflow_module import (
            get_items, add_item, update_item, delete_item,
            change_from_month, _CATEGORIES,
        )
        import pandas as _cf_pd2

        _co_opts  = {"LLC": "EOS ONLINE LLC", "IL": "EOS TRADE LTD"}
        _today_ym = __import__("datetime").date.today().strftime("%Y-%m")
        _cf_items = get_items(cf_conn)

        # ── Items table ───────────────────────────────────────────────────
        if _cf_items:
            _item_rows = []
            for item in _cf_items:
                iid, name, direction, category, amount, currency, frequency, \
                    company, start_ym, end_ym, notes = item
                sym = "$" if currency == "USD" else "₪"
                _item_rows.append({
                    "ID": iid,
                    "↕": "↑" if direction == "in" else "↓",
                    "Name": name,
                    "Category": category,
                    "Amount": f"{sym}{amount:,.0f}",
                    "Freq": frequency.capitalize(),
                    "Company": _co_opts.get(company, company),
                    "From": start_ym,
                    "Until": end_ym or "∞",
                    "Notes": notes or "",
                })
            st.dataframe(
                _cf_pd2.DataFrame(_item_rows).drop(columns=["ID"]),
                use_container_width=True, hide_index=True
            )

            # ── Select item to act on ─────────────────────────────────────
            _sel_label = st.selectbox(
                "Select item to Edit / Change / Delete",
                [None] + [f"{r['ID']} – {r['↕']} {r['Name']}  ({r['From']} → {r['Until']})"
                          for r in _item_rows],
                key="cf_sel_item"
            )

            if _sel_label:
                _sel_id   = int(_sel_label.split(" – ")[0])
                _sel_item = next(i for i in _cf_items if i[0] == _sel_id)
                (_, _sname, _sdir, _scat, _samt, _scur,
                 _sfreq, _sco, _sstart, _send, _snotes) = _sel_item

                _act_tab_edit, _act_tab_change, _act_tab_delete = st.tabs(
                    ["✏️ Edit", "📅 Change from month", "🗑️ Delete"]
                )

                # ── EDIT ──────────────────────────────────────────────────
                with _act_tab_edit:
                    with st.form("cf_edit_item"):
                        _e1, _e2, _e3 = st.columns([3,1,1])
                        _ename = _e1.text_input("Name", value=_sname)
                        _edir  = _e2.selectbox("Direction", ["out","in"],
                            index=0 if _sdir=="out" else 1,
                            format_func=lambda x: "↓ Expense" if x=="out" else "↑ Income")
                        _ecat  = _e3.selectbox("Category", _CATEGORIES,
                            index=_CATEGORIES.index(_scat) if _scat in _CATEGORIES else 0)

                        _e4, _e5, _e6, _e7 = st.columns([1,1,1,2])
                        _eamt  = _e4.number_input("Amount", value=float(_samt),
                                                   min_value=0.0, step=100.0, format="%.0f")
                        _ecur  = _e5.selectbox("Currency", ["USD","ILS"],
                                               index=0 if _scur=="USD" else 1)
                        _efreq = _e6.selectbox("Frequency",
                            ["monthly","quarterly","annual","once"],
                            index=["monthly","quarterly","annual","once"].index(_sfreq),
                            format_func=lambda x: x.capitalize())
                        _eco_label = _e7.selectbox("Company", list(_co_opts.values()),
                            index=list(_co_opts.keys()).index(_sco) if _sco in _co_opts else 0)
                        _eco = next(k for k, v in _co_opts.items() if v == _eco_label)

                        _e8, _e9, _e10 = st.columns([1,1,2])
                        _estart = _e8.text_input("Start (YYYY-MM)", value=_sstart or "")
                        _eend   = _e9.text_input("End (YYYY-MM)", value=_send or "",
                                                  placeholder="blank = forever")
                        _enotes = _e10.text_input("Notes", value=_snotes or "")

                        if st.form_submit_button("💾 Save changes"):
                            update_item(cf_conn, _sel_id, _ename, _edir, _ecat,
                                        _eamt, _ecur, _efreq, _eco,
                                        _estart, _eend or None, _enotes or None)
                            st.success("✅ Item updated.")
                            st.rerun(scope="fragment")

                # ── CHANGE FROM MONTH ─────────────────────────────────────
                with _act_tab_change:
                    st.caption(
                        f"Closes **{_sname}** at the month before, then creates a new "
                        f"entry with the updated amount from the chosen month onwards."
                    )
                    with st.form("cf_change_from"):
                        _cf1, _cf2 = st.columns([1,1])
                        _chg_from = _cf1.text_input(
                            "Change from (YYYY-MM) *",
                            value=_today_ym,
                            help="New amount starts this month; old amount ends the month before"
                        )
                        _chg_amt = _cf2.number_input(
                            "New amount *",
                            value=float(_samt), min_value=0.0,
                            step=100.0, format="%.0f"
                        )
                        sym = "$" if _scur == "USD" else "₪"
                        st.caption(
                            f"Result: **{_sname}** {sym}{_samt:,.0f} until "
                            f"{_chg_from[:4]}-{int(_chg_from[5:7])-1 if len(_chg_from)==7 else '?':02d} "
                            f"→ {sym}{_chg_amt:,.0f} from {_chg_from} onwards"
                        )
                        if st.form_submit_button("✂️ Apply change"):
                            if _chg_from and _chg_amt > 0:
                                change_from_month(cf_conn, _sel_id, _chg_from, _chg_amt)
                                st.success(f"✅ Done — {_sname} split at {_chg_from}.")
                                st.rerun(scope="fragment")
                            else:
                                st.error("Month and new amount are required.")

                # ── DELETE ────────────────────────────────────────────────
                with _act_tab_delete:
                    st.warning(
                        f"Delete **{_sname}** ({_sstart} → {_send or '∞'})? "
                        f"This cannot be undone."
                    )
                    if st.button("🗑️ Confirm Delete", key="cf_confirm_del"):
                        delete_item(cf_conn, _sel_id)
                        st.success("Deleted.")
                        st.rerun(scope="fragment")

        st.divider()
        st.markdown("### ➕ Add Scheduled Item")

        with st.form("cf_add_item"):
            _i1, _i2, _i3 = st.columns([3,1,1])
            _iname = _i1.text_input("Name *", placeholder="e.g. Yaniv Salary")
            _idir  = _i2.selectbox("Direction", ["out","in"],
                                    format_func=lambda x: "↓ Expense" if x=="out" else "↑ Income")
            _icat  = _i3.selectbox("Category", _CATEGORIES)

            _i4, _i5, _i6, _i7 = st.columns([1,1,1,2])
            _iamount   = _i4.number_input("Amount *", min_value=0.0, step=100.0, format="%.0f")
            _icur      = _i5.selectbox("Currency", ["USD","ILS"])
            _ifreq     = _i6.selectbox("Frequency", ["monthly","quarterly","annual","once"],
                                        format_func=lambda x: x.capitalize())
            _ico_label = _i7.selectbox("Company", list(_co_opts.values()))
            _ico = next(k for k, v in _co_opts.items() if v == _ico_label)

            _i8, _i9, _i10 = st.columns([1,1,2])
            _istart = _i8.text_input("Start (YYYY-MM) *", value=_today_ym)
            _iend   = _i9.text_input("End (YYYY-MM)", placeholder="blank = forever")
            _inotes = _i10.text_input("Notes", placeholder="optional")

            if st.form_submit_button("➕ Add Item"):
                if _iname and _iamount > 0 and _istart:
                    add_item(cf_conn, _iname, _idir, _icat, _iamount, _icur,
                             _ifreq, _ico, _istart, _iend or None, _inotes or None)
                    st.success(f"✅ '{_iname}' added.")
                    st.rerun(scope="fragment")
                else:
                    st.error("Name, Amount and Start date are required.")

    with _cf_tab_items:
        _cf_items_fragment(_cf_conn)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — ADMIN  (admin role only)
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
