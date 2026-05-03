"""
app.py — Amazon Ads Placement Analyzer
Streamlit web app for triple gifted advertising team.
"""

import streamlit as st
import tempfile
import os
import uuid
import pandas as pd
import io
from analyzer import analyze_with_products, TARGET_ROAS, LOW_IMPR_THRESHOLD
from claude_client import generate_comments
from excel_builder import build_excel
from products import (
    load_products, save_products, calc_breakeven_roas, calc_landed_cost,
    import_csv, get_cost_map, products_exist, COLUMNS
)

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
.metric-card {{
    background: {T['card_bg']}; border: 1px solid {T['card_border']};
    border-radius: 8px; padding: 1.2rem 1.5rem; text-align: center;
}}
.metric-val {{
    font-family: 'IBM Plex Mono', monospace; font-size: 2rem;
    font-weight: 600; color: {T['metric_val']}; margin: 0;
}}
.metric-label {{
    font-size: 0.75rem; color: {T['text_secondary']};
    text-transform: uppercase; letter-spacing: 0.08em; margin: 4px 0 0;
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

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🐾 Pounce")
    st.markdown(f"<p style='color:{T['text_secondary']};font-size:0.8rem;margin-top:-10px;'>Hunt down your best placements.</p>", unsafe_allow_html=True)
    st.divider()

    api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        placeholder="sk-ant-...",
        help="Required to generate AI comments. Leave blank to skip.",
    )

    st.markdown("### Analysis Parameters")
    target_roas = st.number_input(
        "Default Target ROAS",
        min_value=1.0, max_value=20.0,
        value=float(TARGET_ROAS), step=0.5,
        help="Used for campaigns without a matching ASIN in Products & Costs."
    )
    low_impr = st.number_input(
        "Low Impressions Alert (Top)",
        min_value=100, max_value=50000,
        value=LOW_IMPR_THRESHOLD, step=100,
    )

    st.divider()
    products_status = "✅ Loaded" if products_exist() else "⚠️ Not set up"
    st.markdown(f"**Product Costs:** {products_status}")
    st.markdown(
        f"<div style='color:{T['text_secondary']};font-size:0.75rem;margin-top:1rem;'>triple gifted · Pounce v1.0</div>",
        unsafe_allow_html=True
    )

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_analysis, tab_products = st.tabs(["📊 Analysis", "📦 Products & Costs"])

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

    if products_exist():
        cost_map = get_cost_map()
        st.success(f"✅ Product cost data loaded — break-even ROAS calculated dynamically from report prices for {len(cost_map)} products. Default ROAS target {target_roas} used for others.")
    else:
        st.warning("⚠️ No product cost data found. Using default ROAS target for all campaigns. Go to **Products & Costs** tab to set up.")
        cost_map = {}

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        sp_file = st.file_uploader("📦 Sponsored Products — Placement Report (.xlsx)", type=["xlsx"], key="sp")
    with col2:
        sb_file = st.file_uploader("🏷️ Sponsored Brands — Campaign Placement Report (.xlsx)", type=["xlsx"], key="sb")

    st.divider()

    if sp_file and sb_file:
        if st.button("🚀 Run Analysis", type="primary", use_container_width=True):

            with st.spinner("Loading and analyzing data..."):
                sp_path = os.path.join(SESSION_DIR, "sp_report.xlsx")
                sb_path = os.path.join(SESSION_DIR, "sb_report.xlsx")
                with open(sp_path, "wb") as f: f.write(sp_file.read())
                with open(sb_path, "wb") as f: f.write(sb_file.read())
                try:
                    results = analyze_with_products(sp_path, sb_path, target_roas, low_impr, cost_map)
                finally:
                    for p in [sp_path, sb_path]:
                        if os.path.exists(p): os.unlink(p)

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
# TAB 2 — PRODUCTS & COSTS
# ══════════════════════════════════════════════════════════════════════════════
with tab_products:
    st.markdown("# 📦 Products & Costs")
    st.markdown(
        f"<p style='color:{T['text_secondary']};'>Set your product costs once. "
        f"Pounce will use them to calculate a real break-even ROAS per campaign.</p>",
        unsafe_allow_html=True
    )
    st.markdown(
        f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>"
        f"<strong>Break-even ROAS</strong> = Price ÷ (Price − Landed Cost − FBA Fee − Amazon 15%)</p>",
        unsafe_allow_html=True
    )
    st.divider()

    # ── Upload CSV ────────────────────────────────────────────────────────────
    with st.expander("📤 Upload CSV to replace all products", expanded=not products_exist()):
        # Download template
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
                if st.button("💾 Save to server", type="primary"):
                    save_products(df_imported)
                    st.success("✅ Saved! Products will be used in next analysis run.")
                    st.rerun()

    st.divider()

    # ── View & Edit ───────────────────────────────────────────────────────────
    df_products = load_products()

    if df_products.empty:
        st.info("No products yet. Upload a CSV above or add rows manually below.")
        df_products = pd.DataFrame(columns=COLUMNS)
    else:
        # Show break-even ROAS for each product
        st.markdown("### Current Products")
        display_df = df_products.copy()
        display_df["Landed Cost"] = display_df.apply(
            lambda r: round(calc_landed_cost(r), 2), axis=1
        )
        display_df["Break-even ROAS"] = "Calculated from report"
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### ✏️ Edit Products")
    st.markdown(
        f"<p style='color:{T['text_secondary']};font-size:0.85rem;'>"
        f"Edit directly in the table below. Click Save when done.</p>",
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

    # Live cost preview (no price — calculated from report)
    if not edited_df.empty:
        st.markdown("#### 📐 Cost Breakdown Preview")
        st.markdown(
            f"<p style='color:{T['text_secondary']};font-size:0.83rem;'>"
            f"Break-even ROAS will be calculated during analysis using actual avg price from your report.</p>",
            unsafe_allow_html=True
        )
        preview_rows = []
        for _, row in edited_df.iterrows():
            asin = str(row.get("ASIN") or "")
            name = str(row.get("Product Name") or "")
            lc   = calc_landed_cost(row)
            fba  = float(row.get("FBA Fee") or 0)
            preview_rows.append({
                "ASIN":          asin,
                "Product":       name,
                "Product Cost":  f"${float(row.get('Product Cost') or 0):.2f}",
                "Shipping Cost": f"${float(row.get('Shipping Cost') or 0):.2f}",
                "Customs Cost":  f"${float(row.get('Customs Cost') or 0):.2f}",
                "Landed Cost":   f"${lc:.2f}",
                "FBA Fee":       f"${fba:.2f}",
                "Total Fixed Cost": f"${lc + fba:.2f}",
            })
        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)

    if st.button("💾 Save Changes", type="primary", use_container_width=True):
        save_products(edited_df)
        st.success("✅ Product costs saved to server. Will be used in next analysis run.")
        st.rerun()
