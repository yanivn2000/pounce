"""
app.py — Amazon Ads Placement Analyzer
Streamlit web app for triple gifted advertising team.
"""

import streamlit as st
import tempfile
import os
from analyzer import analyze, TARGET_ROAS, LOW_IMPR_THRESHOLD
from claude_client import generate_comments
from excel_builder import build_excel

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Amazon Ads Analyzer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}

.main { background-color: #0d1117; }

.stApp {
    background: #0d1117;
    color: #e6edf3;
}

h1, h2, h3 {
    font-family: 'IBM Plex Mono', monospace !important;
    letter-spacing: -0.03em;
}

.metric-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 1.2rem 1.5rem;
    text-align: center;
}
.metric-val {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 2rem;
    font-weight: 600;
    color: #58a6ff;
    margin: 0;
}
.metric-label {
    font-size: 0.75rem;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 4px 0 0;
}
.alert-box {
    background: #3d1f00;
    border: 1px solid #d29922;
    border-radius: 6px;
    padding: 0.8rem 1rem;
    margin: 0.4rem 0;
    font-size: 0.85rem;
    color: #e3b341;
}
.score-hi  { color: #3fb950; font-weight: 600; }
.score-mid { color: #d29922; font-weight: 600; }
.score-lo  { color: #f85149; font-weight: 600; }
.tag-sp { background:#1f3a5f; color:#58a6ff; padding:2px 8px; border-radius:4px; font-size:0.75rem; }
.tag-sb { background:#3d1f5f; color:#bc8cff; padding:2px 8px; border-radius:4px; font-size:0.75rem; }
.tag-auto { background:#1f3a1f; color:#3fb950; padding:2px 8px; border-radius:4px; font-size:0.75rem; }

div[data-testid="stSidebar"] {
    background: #0d1117;
    border-right: 1px solid #30363d;
}
</style>
""", unsafe_allow_html=True)

# ── Sidebar — settings ────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Settings")
    st.divider()

    api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        placeholder="sk-ant-...",
        help="Required to generate AI comments. Leave blank to skip AI comments.",
    )

    st.markdown("### Analysis Parameters")
    target_roas = st.number_input(
        "Target ROAS", min_value=1.0, max_value=20.0,
        value=float(TARGET_ROAS), step=0.5,
        help="Campaigns above this ROAS get bid raise recommendations."
    )
    low_impr = st.number_input(
        "Low Impressions Alert Threshold (Top)",
        min_value=100, max_value=50000,
        value=LOW_IMPR_THRESHOLD, step=100,
        help="Top of Search impressions below this trigger an alert."
    )

    st.divider()
    st.markdown(
        "<div style='color:#8b949e;font-size:0.75rem;'>triple gifted · Amazon Ads Analyzer<br>Powered by Claude API</div>",
        unsafe_allow_html=True
    )

# ── Main UI ───────────────────────────────────────────────────────────────────
st.markdown("# 📊 Amazon Ads Placement Analyzer")
st.markdown(
    "<p style='color:#8b949e;font-size:0.95rem;'>Upload your 30-day placement reports. "
    "Get scores, bid recommendations, alerts, and AI-generated comments.</p>",
    unsafe_allow_html=True
)
st.divider()

col1, col2 = st.columns(2)
with col1:
    sp_file = st.file_uploader(
        "📦 Sponsored Products — Placement Report (.xlsx)",
        type=["xlsx"],
        key="sp"
    )
with col2:
    sb_file = st.file_uploader(
        "🏷️ Sponsored Brands — Campaign Placement Report (.xlsx)",
        type=["xlsx"],
        key="sb"
    )

st.divider()

if sp_file and sb_file:
    if st.button("🚀 Run Analysis", type="primary", use_container_width=True):

        with st.spinner("Loading and analyzing data..."):
            # Save uploads to temp files
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f_sp:
                f_sp.write(sp_file.read())
                sp_path = f_sp.name
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f_sb:
                f_sb.write(sb_file.read())
                sb_path = f_sb.name

            results = analyze(sp_path, sb_path, target_roas, low_impr)

            os.unlink(sp_path)
            os.unlink(sb_path)

        # ── Generate AI comments ─────────────────────────────────────────
        if api_key:
            st.markdown("### 🤖 Generating AI Comments...")
            progress_bar = st.progress(0)
            status_text  = st.empty()

            def on_progress(i, total, camp):
                pct = i / total if total > 0 else 0
                progress_bar.progress(pct)
                status_text.markdown(
                    f"<span style='color:#8b949e;font-size:0.8rem;'>"
                    f"({i}/{total}) {camp}</span>",
                    unsafe_allow_html=True
                )

            results = generate_comments(results, api_key, target_roas,
                                        progress_callback=on_progress)
            progress_bar.progress(1.0)
            status_text.markdown(
                "<span style='color:#3fb950;font-size:0.8rem;'>✓ Comments ready</span>",
                unsafe_allow_html=True
            )
        else:
            st.info("ℹ️ No API key provided — skipping AI comments. "
                    "Add your Anthropic API key in the sidebar to enable them.")

        # ── Summary metrics ──────────────────────────────────────────────
        st.divider()
        st.markdown("### 📈 Summary")

        sp_count  = sum(1 for r in results if r.ad_type == "SP")
        sb_count  = sum(1 for r in results if r.ad_type == "SB")
        hi_count  = sum(1 for r in results if r.score >= 80)
        alert_cnt = sum(1 for r in results if r.alert)

        mc1, mc2, mc3, mc4 = st.columns(4)
        for col, val, label in [
            (mc1, sp_count,  "SP Campaigns"),
            (mc2, sb_count,  "SB Campaigns"),
            (mc3, hi_count,  "Score ≥ 80 (Invest)"),
            (mc4, alert_cnt, "🚨 Alerts"),
        ]:
            col.markdown(
                f'<div class="metric-card">'
                f'<p class="metric-val">{val}</p>'
                f'<p class="metric-label">{label}</p>'
                f'</div>',
                unsafe_allow_html=True
            )

        # ── Alerts ───────────────────────────────────────────────────────
        alerts = [r for r in results if r.alert]
        if alerts:
            st.divider()
            st.markdown("### 🚨 Campaigns Requiring Immediate Attention")
            st.markdown(
                "<p style='color:#8b949e;font-size:0.85rem;'>High score + low Top impressions — raise bid now.</p>",
                unsafe_allow_html=True
            )
            for r in alerts[:10]:
                score_cls = "score-hi" if r.score >= 70 else "score-mid"
                tag = f'<span class="tag-sp">SP</span>' if r.ad_type == "SP" else f'<span class="tag-sb">SB</span>'
                auto_tag = '<span class="tag-auto">AUTO</span>' if r.targeting == "Auto" else ""
                st.markdown(
                    f'<div class="alert-box">'
                    f'{tag} {auto_tag} '
                    f'<strong>{r.campaign}</strong> — '
                    f'Score: <span class="{score_cls}">{r.score}</span> — '
                    f'{r.alert}<br>'
                    f'<span style="color:#8b949e;font-size:0.8rem;">Bid rec: {r.bid_rec}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )

        # ── Top performers table ──────────────────────────────────────────
        st.divider()
        st.markdown("### 🏆 All Campaigns — Ranked by Score")

        import pandas as pd
        table_data = []
        for r in results:
            table_data.append({
                "Campaign": r.campaign,
                "Type": r.ad_type,
                "Targeting": r.targeting,
                "Score": r.score,
                "Label": r.score_label,
                "Top ROAS": round(r.top.roas, 2) if r.top.roas else None,
                "Rest ROAS": round(r.rest.roas, 2) if r.rest.roas else None,
                "Top Impr.": r.top.impressions,
                "Bid Rec": r.bid_rec,
                "Alert": "🚨" if r.alert else "",
            })

        df_display = pd.DataFrame(table_data)
        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Score": st.column_config.ProgressColumn(
                    "Score", min_value=0, max_value=100, format="%d"
                ),
                "Top Impr.": st.column_config.NumberColumn(format="%d"),
            }
        )

        # ── Download button ──────────────────────────────────────────────
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

else:
    st.markdown(
        """
        <div style='text-align:center;padding:3rem;color:#8b949e;'>
            <div style='font-size:3rem;margin-bottom:1rem;'>📂</div>
            <p style='font-size:1.1rem;'>Upload both placement report files above to get started.</p>
            <p style='font-size:0.85rem;'>
                <strong style='color:#e6edf3;'>SP Report:</strong> 
                Campaign Manager → Reports → Sponsored Products → Placement<br>
                <strong style='color:#e6edf3;'>SB Report:</strong> 
                Campaign Manager → Reports → Sponsored Brands → Campaign Placement
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )
