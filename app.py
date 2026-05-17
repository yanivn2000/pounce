"""
app.py — Amazon Ads Placement Analyzer
Streamlit web app for triple gifted advertising team.
"""

import streamlit as st
import streamlit_authenticator as stauth
import tempfile
import os
import uuid
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
# One-time migration from CSV to DB on startup
if products_exist() and not products_exist_db():
    migrate_csv_to_db()
from db.database import init_db
from db.importer import import_orders_csv, save_recommendation, update_recommendation_outcome
from db.queries import (
    get_sales_matrix, get_weekly_summary, get_recommendations_history,
    get_change_log, get_marketplaces, get_order_date_range, count_orders,
    get_units_matrix, get_weekly_units_matrix, get_weekly_units_matrix_yoy,
)

init_db()

# ── Session isolation ─────────────────────────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

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

authenticator.login(location="main")
authentication_status = st.session_state.get("authentication_status")
current_username      = st.session_state.get("username", "")

if authentication_status is False:
    st.error("Incorrect username or password.")
    st.stop()
elif authentication_status is None:
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
    tab_analysis, tab_products, tab_sales, tab_recs, tab_admin = st.tabs([
        "📊 Analysis", "📦 Products & Costs", "📈 Sales Dashboard", "📋 Recommendations", "⚙️ Admin"
    ])
else:
    tab_analysis, tab_products, tab_sales, tab_recs = st.tabs([
        "📊 Analysis", "📦 Products & Costs", "📈 Sales Dashboard", "📋 Recommendations"
    ])
    tab_admin = None

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
with tab_analysis:
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
        st.warning("⚠️ No product cost data found. Using default ROAS target. Go to **Products & Costs** tab to set up.")
        cost_map = {}

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        sp_file = st.file_uploader("📦 Sponsored Products — Placement Report (.xlsx)", type=["xlsx"], key="sp")
    with col2:
        sb_file = st.file_uploader("🏷️ Sponsored Brands — Campaign Placement Report (.xlsx)", type=["xlsx"], key="sb")

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
                    results = analyze_with_products(sp_path, sb_path, target_roas, low_impr, cost_map, min_margin_pct, detected_marketplace)
                finally:
                    for p in [sp_path, sb_path]:
                        if os.path.exists(p): os.unlink(p)

            # ── Auto-save recommendations to DB ──────────────────────────────
            today_str   = str(date.today())
            review_str  = str(date.today() + timedelta(days=14))
            saved_count = 0
            cost_map_for_asin = cost_map  # already loaded above
            for r in results:
                asin = next(
                    (a for a in cost_map_for_asin if a.upper() in r.campaign.upper()),
                    None
                )
                for pl_rec in r.bid_recs_data:
                    save_recommendation({
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
                    })
                    saved_count += 1
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
# TAB 2 — PRODUCTS & COSTS (per marketplace, DB-backed)
# ══════════════════════════════════════════════════════════════════════════════
with tab_products:
    st.markdown("# 📦 Products & Costs")
    st.markdown(
        f"<p style='color:{T['text_secondary']};'>Product costs are shared across all marketplaces. "
        f"Set them once and they apply everywhere.</p>",
        unsafe_allow_html=True
    )
    st.markdown(
        f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>"
        f"<strong>Break-even ROAS</strong> = Price ÷ (Price − Landed Cost − FBA Fee − Amazon 15%)</p>",
        unsafe_allow_html=True
    )
    st.divider()

    # ── Upload CSV ────────────────────────────────────────────────────────────
    with st.expander("📤 Import CSV", expanded=not products_exist_db()):
        template_path = os.path.join(os.path.dirname(__file__), "data", "products_template.csv")
        if os.path.exists(template_path):
            with open(template_path, "rb") as f:
                st.download_button(
                    "⬇️ Download CSV Template",
                    data=f.read(),
                    file_name="pounce_products_template.csv",
                    mime="text/csv",
                )

        uploaded_csv = st.file_uploader("Upload filled CSV", type=["csv"], key="products_csv")
        if uploaded_csv:
            df_imported, err = import_csv(uploaded_csv)
            if err:
                st.error(f"❌ {err}")
            else:
                st.success(f"✅ {len(df_imported)} products ready to import.")
                st.dataframe(df_imported, use_container_width=True, hide_index=True)
                if st.button("💾 Save to DB", type="primary"):
                    save_products_db(df_imported)
                    st.success(f"✅ Saved {len(df_imported)} products.")
                    st.rerun()

    st.divider()

    # ── View & Edit ───────────────────────────────────────────────────────────
    df_products = load_products_db()

    if df_products.empty:
        st.info("No products yet. Upload a CSV above or add rows manually below.")
        df_products = pd.DataFrame(columns=DB_COLUMNS)
    else:
        st.markdown("### Current Products")
        display_df = df_products.copy()
        display_df["Landed Cost"] = display_df.apply(lambda r: round(calc_landed_cost(r), 2), axis=1)
        display_df["Break-even ROAS"] = "Calculated from report"
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### ✏️ Edit Products")
    st.markdown(
        f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>"
        f"Edit directly in the table. Changes are saved per marketplace.</p>",
        unsafe_allow_html=True
    )

    edited_df = st.data_editor(
        df_products,
        use_container_width=True,
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "ASIN":          st.column_config.TextColumn("ASIN", help="Amazon ASIN — must appear in campaign name"),
            "Product Name":  st.column_config.TextColumn("Product Name"),
            "Product Cost":  st.column_config.NumberColumn("Product Cost $", format="$%.2f", min_value=0.0),
            "Shipping Cost": st.column_config.NumberColumn("Shipping Cost $", format="$%.2f", min_value=0.0),
            "Customs Cost":  st.column_config.NumberColumn("Customs Cost $", format="$%.2f", min_value=0.0),
            "FBA Fee":       st.column_config.NumberColumn("FBA Fee $", format="$%.2f", min_value=0.0),
        }
    )

    if not edited_df.empty:
        st.markdown("#### 📐 Cost Breakdown Preview")
        st.markdown(
            f"<p style='color:{T['text_secondary']};font-size:0.83rem;'>"
            f"Break-even ROAS calculated during analysis using avg price from your report.</p>",
            unsafe_allow_html=True
        )
        preview_rows = []
        for _, row in edited_df.iterrows():
            lc  = calc_landed_cost(row)
            fba = float(row.get("FBA Fee") or 0)
            preview_rows.append({
                "ASIN":             str(row.get("ASIN") or ""),
                "Product":          str(row.get("Product Name") or ""),
                "Product Cost":     f"${float(row.get('Product Cost') or 0):.2f}",
                "Shipping Cost":    f"${float(row.get('Shipping Cost') or 0):.2f}",
                "Customs Cost":     f"${float(row.get('Customs Cost') or 0):.2f}",
                "Landed Cost":      f"${lc:.2f}",
                "FBA Fee":          f"${fba:.2f}",
                "Total Fixed Cost": f"${lc + fba:.2f}",
            })
        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)

    if st.button("💾 Save Changes", type="primary", use_container_width=True):
        save_products_db(edited_df)
        st.success("✅ Product costs saved. Will be used in next analysis run.")
        st.rerun()

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

            # Freeze asin + title by setting them as the DataFrame index
            display = matrix.set_index(["asin", "title"])

            # Pre-format cell values: numbers with commas, ⚑ appended for changed cells
            display_marked = display.copy().astype(object)
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

            def _color_matrix(df):
                styles = pd.DataFrame("", index=df.index, columns=df.columns)
                for col in date_cols:
                    if col not in df.columns:
                        continue
                    for pos, idx in enumerate(matrix.index):
                        p = pct_indexed.loc[idx, col]
                        try:
                            p = float(p)
                        except (TypeError, ValueError):
                            continue
                        col_loc = styles.columns.get_loc(col)
                        if p >= threshold:
                            styles.iloc[pos, col_loc] = "background-color:#1a7f3733;color:#1a7f37;font-weight:600"
                        elif p <= -threshold:
                            styles.iloc[pos, col_loc] = "background-color:#cf222e22;color:#cf222e;font-weight:600"
                if change_set:
                    for pos, (asin, title) in enumerate(df.index):
                        for col in date_cols:
                            if col in df.columns and (str(asin), col) in change_set:
                                col_loc = styles.columns.get_loc(col)
                                if styles.iloc[pos, col_loc] == "":
                                    styles.iloc[pos, col_loc] = "background-color:#fff3b0;color:#7d4e00;font-weight:700"
                return styles

            styled = display_marked.style.apply(_color_matrix, axis=None)
            col_cfg = {
                "asin":  st.column_config.TextColumn("ASIN",  width=120),
                "title": st.column_config.TextColumn("Title", width=240),
            }
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
                cl_type = st.selectbox("Change Type", ["price", "image", "title", "deal", "listing", "other"])
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
# TAB 4 — RECOMMENDATIONS HISTORY
# ══════════════════════════════════════════════════════════════════════════════
with tab_recs:
    st.markdown("# 📋 Recommendations History")
    st.markdown(
        f"<p style='color:{T['text_secondary']};'>Track placement bid recommendations over time "
        f"and record outcomes after the review window.</p>",
        unsafe_allow_html=True
    )
    st.divider()

    # ── Save recommendation manually ──────────────────────────────────────────
    with st.expander("➕ Log a Recommendation"):
        with st.form("rec_form"):
            rc1, rc2 = st.columns(2)
            with rc1:
                r_date    = st.date_input("Date Given", value=date.today())
                r_asin    = st.text_input("ASIN (optional)", placeholder="B0XXXXXXXX")
                r_camp    = st.text_input("Campaign Name")
                r_place   = st.selectbox("Placement", ["Top of Search", "Rest of Search", "Product Pages"])
            with rc2:
                r_type    = st.selectbox("Campaign Type", ["SP", "SB"])
                r_cur_mul = st.number_input("Current Multiplier %", min_value=0, max_value=900, value=0)
                r_action  = st.selectbox("Recommended Action", ["Increase", "Decrease", "Disable", "Keep", "Brand awareness only"])
                r_rec_mul = st.number_input("Recommended Multiplier %", min_value=0, max_value=900, value=0)

            r_mkt = st.selectbox("Marketplace", ["amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au", "amazon.de"], key="rec_mkt")
            r_reasoning = st.text_area("Reasoning / Notes")
            r_review = st.date_input("Review Date", value=date.today() + timedelta(days=14))

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
                })
                st.success("✅ Recommendation saved.")
                st.rerun()

    st.divider()

    # ── Filter + list ─────────────────────────────────────────────────────────
    rhf1, rhf2 = st.columns(2)
    with rhf1:
        rh_market = st.selectbox("Filter by Marketplace", ["all", "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au", "amazon.de"], key="rh_market")
        rh_market = None if rh_market == "all" else rh_market
    with rhf2:
        show_pending = st.checkbox("Show only pending review", value=False)

    recs_df = get_recommendations_history(marketplace=rh_market)

    if recs_df.empty:
        st.info("No recommendations logged yet. Run an analysis or add one manually above.")
    else:
        if show_pending:
            today_str = str(date.today())
            recs_df = recs_df[
                (recs_df["review_date"].fillna("") <= today_str) &
                (recs_df["outcome"].isna() | (recs_df["outcome"] == ""))
            ]

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
            "id", "date_given", "score", "asin", "marketplace", "campaign_name",
            "placement_type", "campaign_type", "change",
            "reasoning", "review_date", "outcome"
        ]
        existing_cols = [c for c in display_cols if c in recs_display.columns]
        st.dataframe(
            recs_display[existing_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "change":    st.column_config.TextColumn("Change",    width=90),
                "reasoning": st.column_config.TextColumn("Reasoning", width=400),
            }
        )

        # ── Record outcome ────────────────────────────────────────────────────
        st.divider()
        st.markdown("### ✅ Record Outcome")
        oc1, oc2, oc3 = st.columns([1, 3, 1])
        with oc1:
            outcome_id = st.number_input("Rec ID", min_value=1, step=1)
        with oc2:
            outcome_text = st.text_input("Outcome", placeholder="e.g. ROAS improved from 2.1 to 3.4")
        with oc3:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("💾 Save Outcome"):
                if outcome_text.strip():
                    update_recommendation_outcome(int(outcome_id), outcome_text.strip())
                    st.success("✅ Outcome recorded.")
                    st.rerun()
                else:
                    st.warning("Enter an outcome first.")

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
            confirm_recs = st.checkbox("Yes, delete all recommendations", key="confirm_recs")
            if st.button("🗑️ Delete All Recommendations", type="primary", disabled=not confirm_recs):
                conn = _get_conn()
                with conn:
                    conn.execute("DELETE FROM recommendations")
                conn.close()
                st.success(f"✅ Deleted {rec_count:,} recommendations.")
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
