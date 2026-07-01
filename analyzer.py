"""
analyzer.py
Core analysis logic — scoring, bid recommendations, alerts.
No Claude API calls here — pure deterministic logic.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

TARGET_ROAS = 3.0
LOW_IMPR_THRESHOLD = 1000

_CURRENCY_TO_MARKETPLACE = {
    "USD": "amazon.com",
    "CAD": "amazon.ca",
    "GBP": "amazon.co.uk",
    "EUR": "amazon.de",
    "AUD": "amazon.com.au",
}

_COUNTRY_TO_MARKETPLACE = {
    "United States": "amazon.com",
    "Canada":        "amazon.ca",
    "United Kingdom":"amazon.co.uk",
    "Germany":       "amazon.de",
    "Australia":     "amazon.com.au",
    "France":        "amazon.fr",
    "Italy":         "amazon.it",
    "Spain":         "amazon.es",
    "Mexico":        "amazon.com.mx",
    "Japan":         "amazon.co.jp",
    "India":         "amazon.in",
}


def detect_marketplace_from_xlsx(path: str) -> str:
    """
    Read the first data row of an Amazon ads report and detect marketplace from currency.
    Falls back to 'amazon.com' if not detectable.
    """
    try:
        df = pd.read_excel(path, nrows=5)
        df.columns = df.columns.str.strip()
        for col in ("Currency", "currency"):
            if col in df.columns:
                val = str(df[col].dropna().iloc[0]).strip().upper()
                return _CURRENCY_TO_MARKETPLACE.get(val, "amazon.com")
    except Exception:
        pass
    return "amazon.com"


def get_countries_from_report(path: str) -> dict[str, str]:
    """
    Returns {country_name: marketplace} for each unique country in the report.
    Returns empty dict if there is no Country column (single-country report).
    """
    try:
        df = pd.read_excel(path, nrows=5000)
        df.columns = df.columns.str.strip()
        col = next((c for c in df.columns if c.strip().lower() == "country"), None)
        if col is None:
            return {}
        countries = df[col].dropna().unique()
        result = {}
        for c in countries:
            c_str = str(c).strip()
            mp = _COUNTRY_TO_MARKETPLACE.get(c_str)
            if not mp:
                # fallback: detect from currency rows for this country
                sub = df[df[col] == c]
                for cur_col in ("Currency", "currency"):
                    if cur_col in df.columns:
                        cur_val = sub[cur_col].dropna().iloc[0] if not sub[cur_col].dropna().empty else None
                        if cur_val:
                            mp = _CURRENCY_TO_MARKETPLACE.get(str(cur_val).strip().upper(), "amazon.com")
                            break
                mp = mp or "amazon.com"
            result[c_str] = mp
        return result
    except Exception:
        return {}


PLACEMENT_MAP = {
    'Top of Search on Amazon': 'Top',
    'Rest of search on Amazon': 'Rest',
    'Product pages on Amazon': 'Product',
    'Off Amazon': 'Off',
}


@dataclass
class PlacementMetrics:
    impressions: int = 0
    clicks: int = 0
    spend: float = 0.0
    sales: float = 0.0
    orders: int = 0
    ctr: Optional[float] = None
    cpc: Optional[float] = None
    roas: Optional[float] = None
    acos: Optional[float] = None


@dataclass
class CampaignResult:
    campaign: str
    ad_type: str          # 'SP' or 'SB'
    targeting: str        # 'Auto' / 'Manual' / 'Brand (SB)'
    top: PlacementMetrics = field(default_factory=PlacementMetrics)
    rest: PlacementMetrics = field(default_factory=PlacementMetrics)
    product: PlacementMetrics = field(default_factory=PlacementMetrics)
    score: int = 0
    score_label: str = ''
    bid_rec: str = ''
    bid_recs_data: list = field(default_factory=list)   # structured per-placement recs
    alert: str = ''
    comment: str = ''       # filled by Claude API
    total_roas: float = 0.0
    marketplace: str = 'amazon.com'
    # ── New placement algorithm fields ────────────────────────────────────
    mode: str = ''                 # 'learning' | 'isolation' | 'optimization' | 'no_data'
    base_bid_change_pct: int = 0   # e.g. -40 means reduce all keyword bids 40%
    placement_algorithm: dict = field(default_factory=dict)  # full algo result dict
    is_critical: bool = False      # True = urgent action (risk or opportunity)
    is_paused: bool = False        # True = campaign ended before report window closed
    end_date: str = ''             # Campaign's last End Date from the report (YYYY-MM-DD)
    breakeven_roas: float = 0.0   # Calculated breakeven ROAS for this campaign (used for snapshots)


def _safe(v):
    """Return None if NaN, else the value."""
    if v is None:
        return None
    try:
        if np.isnan(v):
            return None
    except TypeError:
        pass
    return v


_PAUSED_LAG_DAYS = 7   # campaigns whose last End Date is ≥ this many days before the
                        # report's overall End Date are considered paused/archived


_EMPTY_AGG_COLS = ['Campaign', 'PL', 'Impressions', 'Clicks', 'Spend', 'Sales',
                   'Orders', 'CTR', 'CPC', 'ROAS', 'ACOS', 'BidAdj', 'is_paused']


def load_and_aggregate(path: str, sales_col: str, country_filter: str = None) -> pd.DataFrame:
    # No file supplied (e.g. no Sponsored Brands report for this marketplace) →
    # return an empty aggregate so analysis proceeds on the other ad type.
    if not path:
        return pd.DataFrame(columns=_EMPTY_AGG_COLS)

    df = pd.read_excel(path)
    df.columns = df.columns.str.strip()

    # Filter to a single country when processing multi-country reports
    if country_filter:
        _ccol = next((c for c in df.columns if c.strip().lower() == "country"), None)
        if _ccol:
            df = df[df[_ccol].astype(str).str.strip() == country_filter]

    # Handle empty or missing Placement column gracefully
    if 'Placement' not in df.columns or df.empty:
        return pd.DataFrame(columns=['Campaign', 'PL', 'Impressions',
                                     'Clicks', 'Spend', 'Sales', 'Orders',
                                     'CTR', 'CPC', 'ROAS', 'ACOS', 'BidAdj',
                                     'is_paused'])

    df = df.rename(columns={'Campaign Name': 'Campaign'})

    # Auto-detect sales column — handles different Amazon report formats:
    # "7 Day Total Sales", "7 Day Total Sales ($)", "14 Day Total Sales"
    if sales_col in df.columns:
        df = df.rename(columns={sales_col: 'Sales'})
    else:
        sales_candidates = [c for c in df.columns if 'Sales' in c and 'Day' in c]
        if sales_candidates:
            df = df.rename(columns={sales_candidates[0]: 'Sales'})
        else:
            raise ValueError(f"Could not find Sales column. Available: {list(df.columns)}")

    # Orders: try "7 Day Total Orders #" first, then "Purchases" (placement report format)
    order_cols = [c for c in df.columns if 'Orders' in c and 'Day' in c and '#' in c]
    if order_cols:
        df = df.rename(columns={order_cols[0]: 'Orders'})

    if 'Orders' not in df.columns or df['Orders'].sum() == 0:
        purchase_col = next((c for c in df.columns if c.lower() == 'purchases'), None)
        if purchase_col:
            df = df.rename(columns={purchase_col: 'Orders'})
        elif 'Orders' not in df.columns:
            df['Orders'] = 0

    df['Placement'] = df['Placement'].str.strip()
    df['PL'] = df['Placement'].map(PLACEMENT_MAP)

    g = df.groupby(['Campaign', 'PL']).agg(
        Impressions=('Impressions', 'sum'),
        Clicks=('Clicks', 'sum'),
        Spend=('Spend', 'sum'),
        Sales=('Sales', 'sum'),
        Orders=('Orders', 'sum'),
    ).reset_index()

    g['CTR']  = np.where(g['Impressions'] > 0, g['Clicks'] / g['Impressions'], np.nan)
    g['CPC']  = np.where(g['Clicks'] > 0, g['Spend'] / g['Clicks'], np.nan)
    g['ROAS'] = np.where(g['Spend'] > 0, g['Sales'] / g['Spend'], np.nan)
    g['ACOS'] = np.where(g['Sales'] > 0, g['Spend'] / g['Sales'], np.nan)

    # Capture current Bid Adjustment per placement (e.g. "200%" → 2.0)
    bid_adj_col = next((c for c in df.columns if 'bid adjustment' in c.lower()), None)
    if bid_adj_col:
        df['_BidAdj'] = pd.to_numeric(
            df[bid_adj_col].astype(str).str.replace('%', '').str.strip(),
            errors='coerce'
        ).fillna(0) / 100
        g_ba = df.groupby(['Campaign', 'PL'])['_BidAdj'].first().reset_index()
        g = g.merge(g_ba, on=['Campaign', 'PL'], how='left')
        g = g.rename(columns={'_BidAdj': 'BidAdj'})
    else:
        g['BidAdj'] = 0.0
    g['BidAdj'] = g['BidAdj'].fillna(0.0)

    # ── Paused campaign detection via End Date ─────────────────────────────
    # Amazon includes 'End Date' per row.  A campaign whose last End Date is
    # ≥ _PAUSED_LAG_DAYS before the report's overall End Date was stopped/paused
    # before the reporting window closed.
    if 'End Date' in df.columns:
        df['_EndDate'] = pd.to_datetime(df['End Date'], errors='coerce')
        report_end = df['_EndDate'].max()
        camp_end   = df.groupby('Campaign')['_EndDate'].max().reset_index()
        camp_end.columns = ['Campaign', '_CampEnd']
        camp_end['is_paused']  = (report_end - camp_end['_CampEnd']).dt.days >= _PAUSED_LAG_DAYS
        camp_end['end_date']   = camp_end['_CampEnd'].dt.strftime('%Y-%m-%d')
        g = g.merge(camp_end[['Campaign', 'is_paused', 'end_date']], on='Campaign', how='left')
        g['is_paused'] = g['is_paused'].fillna(False)
        g['end_date']  = g['end_date'].fillna('')
    else:
        g['is_paused'] = False
        g['end_date']  = ''

    return g


def _get(sub: pd.DataFrame, pl: str, col: str):
    if pl not in sub.index:
        return None
    return _safe(sub.loc[pl, col])


def _build_placement(sub: pd.DataFrame, pl: str) -> PlacementMetrics:
    if pl not in sub.index:
        return PlacementMetrics()
    r = sub.loc[pl]
    return PlacementMetrics(
        impressions=int(r['Impressions']),
        clicks=int(r['Clicks']),
        spend=float(r['Spend']),
        sales=float(r['Sales']),
        orders=int(r['Orders']),
        ctr=_safe(r['CTR']),
        cpc=_safe(r['CPC']),
        roas=_safe(r['ROAS']),
        acos=_safe(r['ACOS']),
    )


def score_campaign(sub: pd.DataFrame, target_roas: float = TARGET_ROAS,
                   breakeven_roas: float = None) -> int:
    """
    Score 0–100. breakeven_roas (from product costs) is used to penalise
    placements that are actively losing money weighted by their spend share.
    Falls back to target_roas if breakeven not available.
    """
    top_roas   = _get(sub, 'Top', 'ROAS') or 0
    rest_roas  = _get(sub, 'Rest', 'ROAS') or 0
    top_orders = _get(sub, 'Top', 'Orders') or 0
    top_ctr    = _get(sub, 'Top', 'CTR') or 0
    total_spend = sub['Spend'].sum()
    total_sales = sub['Sales'].sum()
    total_roas  = total_sales / total_spend if total_spend > 0 else 0
    be          = breakeven_roas if breakeven_roas else target_roas

    s = 0

    # Overall ROAS (35 pts)
    if total_roas >= 6:   s += 35
    elif total_roas >= 4: s += 25
    elif total_roas >= be: s += 15

    # Top ROAS (35 pts)
    if top_roas >= 8:   s += 35
    elif top_roas >= 6: s += 28
    elif top_roas >= 4: s += 20
    elif top_roas >= be: s += 10

    # Top vs Rest advantage (15 pts)
    if top_roas > 0 and rest_roas > 0:
        ratio = top_roas / rest_roas
        if ratio >= 2:     s += 15
        elif ratio >= 1.5: s += 10
        elif ratio >= 1.1: s += 6
        elif ratio >= 0.9: s += 3

    # CTR at Top (10 pts)
    if top_ctr >= 0.02:    s += 10
    elif top_ctr >= 0.01:  s += 6
    elif top_ctr >= 0.005: s += 3

    # Volume (5 pts)
    if top_orders >= 10:  s += 5
    elif top_orders >= 5: s += 3
    elif top_orders >= 1: s += 1

    # ── Breakeven penalty ─────────────────────────────────────────────────
    # Deduct points for each placement actively losing money (ROAS < breakeven),
    # weighted by that placement's share of total spend.
    # Max penalty: 30 pts (enough to demote a misleadingly high-scoring campaign).
    if total_spend > 0 and be > 0:
        for pl in ['Top', 'Rest', 'Product']:
            pl_roas  = _get(sub, pl, 'ROAS') or 0
            pl_spend = _get(sub, pl, 'Spend') or 0
            if pl_roas > 0 and pl_roas < be and pl_spend > 0:
                spend_weight  = pl_spend / total_spend        # 0–1
                deficit       = 1 - (pl_roas / be)            # 0–1 (deeper = worse)
                penalty       = round(deficit * spend_weight * 30)
                s             = max(0, s - penalty)

    # ── Confidence dampening ───────────────────────────────────────────────
    # ROAS from fewer than 30 purchases is statistically unreliable — a high
    # ROAS on 4 orders could easily be noise.  Scale the score down so low-data
    # campaigns never look as trustworthy as well-tested ones.
    #
    #   total purchases  |  dampen factor  |  example raw→adjusted
    #   0                |  0.40           |  90 → 36
    #   10               |  0.60           |  90 → 54
    #   20               |  0.80           |  90 → 72
    #   30+              |  1.00           |  90 → 90  (no change)
    total_orders = int(sub['Orders'].sum()) if 'Orders' in sub.columns else 0
    if total_orders < _MIN_PURCHASES_CONFIDENCE:
        confidence = total_orders / _MIN_PURCHASES_CONFIDENCE      # 0.0 – <1.0
        dampen     = 0.40 + 0.60 * confidence                      # 0.40 – <1.0
        s          = round(s * dampen)

    return min(s, 100)


def score_label(s: int) -> str:
    if s >= 80: return "Invest aggressively"
    if s >= 60: return "Worth it — scale gradually"
    if s >= 40: return "Test before scaling"
    return "Not recommended now"


def calc_max_roas_for_margin(avg_price: float, landed_cost: float,
                              fba_fee: float, min_margin_pct: float,
                              amazon_fee_pct: float = 0.15) -> float | None:
    """
    Calculate the minimum ROAS we can accept without dropping below min_margin_pct.
    Max ACOS = 1 - min_margin% - amazon_fee% - (fba_fee / price)
    Min acceptable ROAS = 1 / Max ACOS
    Returns None if we can't calculate (missing data).
    """
    if avg_price <= 0:
        return None
    fba_pct  = fba_fee / avg_price
    max_acos = 1 - min_margin_pct - amazon_fee_pct - fba_pct
    if max_acos <= 0:
        return None
    return round(1 / max_acos, 2)


_PL_LABEL = {
    'Top':     'Top of Search',
    'Rest':    'Rest of Search',
    'Product': 'Product Pages',
}


def bid_recommendation(sub: pd.DataFrame, target_roas: float = TARGET_ROAS,
                       min_margin_pct: float = 0.25,
                       cost_data: dict = None,
                       avg_price: float = 0.0) -> tuple[str, list[dict]]:
    """
    Legacy bid recommendation — kept for backward compatibility.
    Returns (display_string, structured_list).
    """
    parts = []
    structured = []
    total_spend = sub['Spend'].sum()
    total_sales = sub['Sales'].sum()
    avg_roas = total_sales / total_spend if total_spend > 0 else 0

    margin_floor_roas = None
    if cost_data and avg_price > 0:
        margin_floor_roas = calc_max_roas_for_margin(
            avg_price=avg_price,
            landed_cost=cost_data.get('landed_cost', 0),
            fba_fee=cost_data.get('fba_fee', 0),
            min_margin_pct=min_margin_pct,
        )
    else:
        top_roas = _get(sub, 'Top', 'ROAS') or 0
        if top_roas > 0:
            margin_floor_roas = top_roas * (1 - min_margin_pct)

    for pl in ['Top', 'Rest', 'Product']:
        roas   = _get(sub, pl, 'ROAS') or 0
        sales  = _get(sub, pl, 'Sales') or 0
        spend  = _get(sub, pl, 'Spend') or 0
        orders = _get(sub, pl, 'Orders') or 0
        if spend == 0:
            continue

        def _rec(action, multiplier, text):
            parts.append(f"{pl}: {text}")
            structured.append({
                "placement_type":         _PL_LABEL[pl],
                "recommended_action":     action,
                "recommended_multiplier": multiplier,
                "reasoning":              text,
                "spend":                  round(float(spend), 2),
                "roas":                   round(float(roas), 2) if roas else None,
                "orders":                 int(orders),
            })

        if sales == 0:
            _rec("No sales", 0, "no sales — don't raise")
            continue
        if orders < 3:
            _rec("Insufficient data", None,
                 f"insufficient data ({int(orders)} order{'s' if orders != 1 else ''}) — monitor before raising")
            continue
        if roas >= target_roas:
            ratio = roas / avg_roas if avg_roas > 0 else 1
            if pl == 'Top':
                top_impr   = _get(sub, 'Top', 'Impressions') or 0
                total_impr = sub['Impressions'].sum()
                top_share  = top_impr / total_impr if total_impr > 0 else 0
                if ratio >= 1.5 and top_share < 0.15:
                    pct = min(int((ratio - 1) * 100), 100)
                elif ratio >= 1.2:
                    pct = min(int((ratio - 1) * 80), 70)
                else:
                    pct = max(min(int((ratio - 1) * 60), 30), 10)
            else:
                pct = min(max(int((roas / target_roas - 1) * 30), 10), 50)

            if margin_floor_roas and roas > 0:
                estimated_new_roas = roas * (1 - (pct / 200))
                if estimated_new_roas < margin_floor_roas:
                    safe_pct = max(int((roas / margin_floor_roas - 1) * 100), 0)
                    if safe_pct == 0:
                        _rec("Keep", 0,
                             f"0% (margin floor reached — ROAS {roas:.1f} near limit {margin_floor_roas:.1f})")
                        continue
                    _rec("Increase", safe_pct,
                         f"+{safe_pct}% ⚠️ capped at margin floor (was +{pct}%)")
                    continue

            _rec("Increase", pct, f"+{pct}%")
        else:
            _rec("Keep", 0, f"0% (ROAS {roas:.1f} < target)")

    return (" | ".join(parts) if parts else "—"), structured


def alert_message(sub: pd.DataFrame, sc: int,
                  low_impr: int = LOW_IMPR_THRESHOLD) -> str:
    top_impr = _get(sub, 'Top', 'Impressions') or 0
    if sc >= 1 and top_impr < low_impr:
        return (f"Low Top impressions ({int(top_impr):,}) — "
                f"raise bid to capture more Top of Search")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# NEW PLACEMENT BID ALGORITHM
# ══════════════════════════════════════════════════════════════════════════════

_MIN_PURCHASES_DATA        = 4   # fewer than this → no_data (keep current)
_MIN_PURCHASES_CONFIDENCE  = 30


def _placement_confidence(purchases: int, impressions: int) -> float:
    """0.0–1.0 confidence based purely on purchase count."""
    return min(1.0, purchases / _MIN_PURCHASES_CONFIDENCE)


def _confidence_score_cap(confidence: float) -> int:
    if confidence == 0.0: return 0   # no data
    if confidence < 0.17: return 3   # <5 purchases
    if confidence < 0.50: return 5   # 5–15 purchases
    if confidence < 1.00: return 7   # 15–30 purchases
    return 10                        # 30+ purchases


def _isolation_mode(placements: list, profitable: list, losing: list,
                    breakeven_roas: float) -> dict:
    """Starve losing placements; protect & boost profitable ones."""
    worst_roas    = min(p['roas'] for p in losing)
    deficit_ratio = worst_roas / breakeven_roas if breakeven_roas > 0 else 0

    if deficit_ratio >= 0.75:   reduction_pct = 20
    elif deficit_ratio >= 0.50: reduction_pct = 40
    elif deficit_ratio >= 0.25: reduction_pct = 60
    else:                       reduction_pct = 75

    reduction_factor = 1 - (reduction_pct / 100)
    total_gap = sum(p['roas_gap'] for p in profitable) if profitable else 1

    result_placements = []
    for p in placements:
        if p['status'] == 'profitable':
            # Maintain effective bid after base reduction, then boost by ROAS weight
            raw_mult    = (1 + p['current_adj']) / reduction_factor - 1
            weight      = p['roas_gap'] / total_gap if total_gap > 0 else 1.0
            boosted     = raw_mult * (0.9 + 0.2 * weight)
            new_pct     = min(900, int(round(boosted * 100)))
            current_pct = int(round(p['current_adj'] * 100))
            if p['confidence'] < 0.5:
                new_pct = min(new_pct, current_pct + 50)  # cap low-confidence increase
            action = "Increase" if new_pct > current_pct else "Keep"
            reason = (
                f"PROFITABLE (ROAS {p['roas']:.2f} vs breakeven {breakeven_roas:.2f}). "
                f"Raise from {current_pct}% → {new_pct}% to maintain effective bid "
                f"while base is reduced {reduction_pct}%."
            )
            if p['confidence'] < 0.5:
                reason += f" ⚠️ Low confidence ({p['purchases']} purchases) — capped."

        elif p['status'] == 'losing':
            new_pct = 0
            action  = "Reduce to 0%"
            reason  = (
                f"LOSING (ROAS {p['roas']:.2f} vs breakeven {breakeven_roas:.2f}). "
                f"Set to 0% — starved by base bid reduction."
            )
        else:  # no_data
            new_pct = int(round(p['current_adj'] * 100))
            action  = "Keep"
            reason  = "Insufficient purchases (<4) - keep current."

        result_placements.append({**p,
            "recommended_action":     action,
            "recommended_multiplier": new_pct,
            "reasoning":              reason,
        })

    avg_conf = sum(p['confidence'] for p in placements) / len(placements)
    severity = max(0, min(7, int((1 - deficit_ratio) * 7)))
    score    = min(10, max(1, severity + int(avg_conf * 3)))

    worst_pl = min(losing, key=lambda p: p['roas'])
    summary  = (
        f"🔴 ISOLATION MODE — {len(losing)} placement(s) below breakeven ROAS ({breakeven_roas:.2f}). "
        f"Worst: {worst_pl['label']} ROAS {worst_pl['roas']:.2f} (deficit {deficit_ratio:.0%}). "
        f"Reduce all keyword bids by {reduction_pct}%."
    )
    return {"mode": "isolation", "base_bid_change_pct": -reduction_pct,
            "placements": result_placements, "score": score, "reasoning": summary}


def _optimization_mode(placements: list, profitable: list,
                       breakeven_roas: float) -> dict:
    """All placements profitable — shift more budget toward top performers."""
    if not profitable:
        # Still stamp every placement with required keys so downstream code is safe
        processed = [{**p,
            "recommended_action":     "Keep",
            "recommended_multiplier": int(round(p.get("current_adj", 0) * 100)),
            "reasoning":              "Insufficient purchases (<4) - keep current.",
        } for p in placements]
        return {"mode": "no_data", "base_bid_change_pct": 0,
                "placements": processed, "score": 0,
                "reasoning": "⚫ No placements have enough data yet (need 4+ purchases)."}

    total_gap = sum(p['roas_gap'] for p in profitable)
    result_placements = []

    for p in placements:
        if p['status'] == 'profitable':
            weight      = p['roas_gap'] / total_gap if total_gap > 0 else 1.0 / len(profitable)
            current_pct = int(round(p['current_adj'] * 100))
            if p['confidence'] < 0.17:   max_inc = 15
            elif p['confidence'] < 0.50: max_inc = 25
            else:                        max_inc = 50
            increase = min(int(weight * 50), max_inc)
            new_pct  = min(900, current_pct + increase)
            action   = "Increase" if increase > 0 else "Keep"
            reason   = (
                f"PROFITABLE (ROAS {p['roas']:.2f}, +{p['roas_gap']:.2f} above breakeven). "
                f"Increase {current_pct}% → {new_pct}%."
            )
            if p['confidence'] < 0.5:
                reason += f" ⚠️ Low confidence ({p['purchases']} purchases) — conservative increase."
        else:
            new_pct = int(round(p['current_adj'] * 100))
            action  = "Keep"
            reason  = "Insufficient purchases (<4) - keep current."

        result_placements.append({**p,
            "recommended_action":     action,
            "recommended_multiplier": new_pct,
            "reasoning":              reason,
        })

    avg_conf = sum(p['confidence'] for p in placements) / len(placements)
    avg_gap  = sum(p['roas_gap'] for p in profitable) / len(profitable)
    roas_sc  = min(5, int(avg_gap / breakeven_roas * 5)) if breakeven_roas > 0 else 3
    score    = min(10, max(1, roas_sc + int(avg_conf * 5)))

    best    = max(profitable, key=lambda p: p['roas'])
    summary = (
        f"🟢 OPTIMIZATION MODE — all placements above breakeven ({breakeven_roas:.2f}). "
        f"Best: {best['label']} ROAS {best['roas']:.2f}. Shifting budget toward top performers."
    )
    return {"mode": "optimization", "base_bid_change_pct": 0,
            "placements": result_placements, "score": score, "reasoning": summary}


def placement_bid_algorithm(sub: pd.DataFrame, breakeven_roas: float,
                             is_new_product: bool = False,
                             campaign_name: str = "") -> dict:
    """
    3-mode placement bid algorithm.
    Modes: learning (new product gate) | isolation | optimization | no_data
    sub must be indexed by PL and include BidAdj column (0-based float, 2.0 = 200%).
    Returns dict: mode, base_bid_change_pct, placements, score, reasoning.

    Brand campaigns (name contains 'BRAND') only support Top of Search
    bid adjustment in Amazon's UI — Product Pages is excluded automatically.
    """
    if is_new_product:
        return {"mode": "learning", "base_bid_change_pct": 0, "placements": [],
                "score": 0,
                "reasoning": "🚼 Product in launch phase. Algorithm suppressed. "
                             "Re-evaluate after 30+ reviews."}

    is_brand = "BRAND" in campaign_name.upper()
    # For Brand campaigns Amazon only exposes Top of Search as an adjustable placement
    eligible_placements = ['Top'] if is_brand else ['Top', 'Rest', 'Product']

    placements = []
    for pl in eligible_placements:
        if pl not in sub.index:
            continue
        r           = sub.loc[pl]
        impressions = int(r.get('Impressions', 0) or 0)
        purchases   = int(r.get('Orders', 0) or 0)
        spend       = float(r.get('Spend', 0) or 0)
        sales       = float(r.get('Sales', 0) or 0)
        roas_raw    = r.get('ROAS', None)
        roas        = float(roas_raw) if (roas_raw is not None and not pd.isna(roas_raw)) else 0.0
        bid_raw     = r.get('BidAdj', 0)
        bid_adj     = float(bid_raw) if (bid_raw is not None and not pd.isna(bid_raw)) else 0.0

        if spend == 0:
            continue

        confidence = _placement_confidence(purchases, impressions)
        roas_gap   = round(roas - breakeven_roas, 4)

        if purchases < _MIN_PURCHASES_DATA:
            status = 'no_data'
        elif roas_gap > 0:
            status = 'profitable'
        else:
            status = 'losing'

        placements.append({
            "pl":          pl,
            "label":       _PL_LABEL[pl],
            "impressions": impressions,
            "purchases":   purchases,
            "spend":       round(spend, 2),
            "sales":       round(sales, 2),
            "roas":        round(roas, 2),
            "roas_gap":    roas_gap,
            "current_adj": bid_adj,
            "confidence":  round(confidence, 2),
            "score_cap":   _confidence_score_cap(confidence),
            "status":      status,
        })

    if not placements:
        return {"mode": "no_data", "base_bid_change_pct": 0, "placements": [],
                "score": 0, "reasoning": "No placement spend data available."}

    profitable = [p for p in placements if p['status'] == 'profitable']
    losing     = [p for p in placements if p['status'] == 'losing']

    if losing:
        return _isolation_mode(placements, profitable, losing, breakeven_roas)
    else:
        return _optimization_mode(placements, profitable, breakeven_roas)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════════

def analyze(sp_path: str, sb_path: str,
            target_roas: float = TARGET_ROAS,
            low_impr: int = LOW_IMPR_THRESHOLD) -> list[CampaignResult]:
    """
    Main entry point (no product costs).
    Returns a list of CampaignResult (comment field left empty — filled by Claude).
    """
    sp_grp = load_and_aggregate(sp_path, '7 Day Total Sales')
    sb_grp = load_and_aggregate(sb_path, '14 Day Total Sales')

    df_sp = pd.read_excel(sp_path)
    df_sp.columns = df_sp.columns.str.strip()
    sp_types = {
        c: ('Auto' if 'AUTO' in c.upper() else 'Manual')
        for c in df_sp['Campaign Name'].unique()
    }

    results = []

    for camp in sp_grp['Campaign'].unique():
        sub = sp_grp[sp_grp['Campaign'] == camp].set_index('PL').drop(columns=['Campaign'])
        sc = score_campaign(sub, target_roas)
        total_spend = sub['Spend'].sum()
        total_sales = sub['Sales'].sum()
        bid_str, bid_data = bid_recommendation(sub, target_roas)
        r = CampaignResult(
            campaign=camp,
            ad_type='SP',
            targeting=sp_types.get(camp, 'Manual'),
            top=_build_placement(sub, 'Top'),
            rest=_build_placement(sub, 'Rest'),
            product=_build_placement(sub, 'Product'),
            score=sc,
            score_label=score_label(sc),
            bid_rec=bid_str,
            bid_recs_data=bid_data,
            alert=alert_message(sub, sc, low_impr),
            total_roas=total_sales / total_spend if total_spend > 0 else 0,
        )
        results.append(r)

    for camp in sb_grp['Campaign'].unique():
        sub = sb_grp[sb_grp['Campaign'] == camp].set_index('PL').drop(columns=['Campaign'])
        sc = score_campaign(sub, target_roas)
        total_spend = sub['Spend'].sum()
        total_sales = sub['Sales'].sum()
        bid_str, bid_data = bid_recommendation(sub, target_roas)
        r = CampaignResult(
            campaign=camp,
            ad_type='SB',
            targeting='Brand (SB)',
            top=_build_placement(sub, 'Top'),
            rest=_build_placement(sub, 'Rest'),
            product=_build_placement(sub, 'Product'),
            score=sc,
            score_label=score_label(sc),
            bid_rec=bid_str,
            bid_recs_data=bid_data,
            alert=alert_message(sub, sc, low_impr),
            total_roas=total_sales / total_spend if total_spend > 0 else 0,
        )
        results.append(r)

    results.sort(key=lambda x: -x.score)
    return results


def _is_critical(mode: str, score: int, is_paused: bool = False) -> bool:
    """
    A campaign is critical if immediate action could significantly move the needle
    — either because money is being lost (isolation) or because high-confidence
    performance means real upside is being left on the table (optimization ≥ 70).
    Paused campaigns are never critical — the user already took action.
    """
    if is_paused:
        return False
    if mode == "isolation":
        return True
    if mode == "optimization" and score >= 70:
        return True
    return False


def analyze_with_products(sp_path: str, sb_path: str,
                           target_roas: float = TARGET_ROAS,
                           low_impr: int = LOW_IMPR_THRESHOLD,
                           cost_map: dict = None,
                           min_margin_pct: float = 0.25,
                           marketplace: str = 'amazon.com',
                           fba_fees_map: dict = None,
                           fx_rate: float = 1.0,
                           country_filter: str = None) -> list[CampaignResult]:
    """
    Main entry point with per-ASIN product costs and new placement algorithm.
    cost_map = {asin: {product_cost, shipping_cost, customs_cost, fba_fee,
                       landed_cost, is_new_product}}
    Price is calculated dynamically from report: Total Sales / Total Orders.
    Falls back to global target_roas for campaigns without ASIN match.
    """
    if cost_map is None:
        cost_map = {}

    AMAZON_FEE_PCT = 0.15

    def get_avg_price(sub: pd.DataFrame) -> float:
        total_sales  = sub['Sales'].sum()
        total_orders = sub['Orders'].sum()
        return total_sales / total_orders if total_orders > 0 else 0.0

    def get_campaign_data(campaign_name: str, avg_price: float) -> tuple[float, dict | None, dict]:
        """Returns (breakeven_roas, cost_data_or_none, debug_info)."""
        if cost_map and avg_price > 0:
            for asin, costs in cost_map.items():
                if asin.upper() in campaign_name.upper():
                    landed_cost_usd = costs['landed_cost']          # product+ship+customs in USD
                    landed_local    = landed_cost_usd * fx_rate      # convert to local currency

                    # FBA pick & pack from imported fees, fallback to product_costs.fba_fee
                    fba_data  = (fba_fees_map or {}).get(asin.upper(), {})
                    pick_pack_fee_preview = fba_data.get('pick_pack_fee')
                    pick_pack = pick_pack_fee_preview if pick_pack_fee_preview else costs.get('fba_fee', 0)
                    pick_pack_source = "fee_preview" if pick_pack_fee_preview else ("manual" if costs.get('fba_fee', 0) else "none")

                    # Referral fee: use imported value if available, else 15% of avg_price
                    referral_fee_preview = fba_data.get('referral_fee')
                    referral  = referral_fee_preview if referral_fee_preview else (avg_price * AMAZON_FEE_PCT)
                    referral_source = "fee_preview" if referral_fee_preview else "15%_of_price"

                    total_costs_local = landed_local + pick_pack + referral
                    margin   = avg_price - total_costs_local
                    be_roas  = round(avg_price / margin, 2) if margin > 0 else target_roas

                    debug_info = {
                        "avg_price":          round(float(avg_price), 4),
                        "product_cost_usd":   round(float(costs.get('product_cost', 0)), 4),
                        "shipping_cost_usd":  round(float(costs.get('shipping_cost', 0)), 4),
                        "customs_cost_usd":   round(float(costs.get('customs_cost', 0)), 4),
                        "landed_cost_usd":    round(float(landed_cost_usd), 4),
                        "fx_rate":            round(float(fx_rate), 6),
                        "landed_local":       round(float(landed_local), 4),
                        "pick_pack_fee":      round(float(pick_pack), 4),
                        "pick_pack_source":   pick_pack_source,
                        "referral_fee":       round(float(referral), 4),
                        "referral_source":    referral_source,
                        "total_costs_local":  round(float(total_costs_local), 4),
                        "margin_local":       round(float(margin), 4),
                        "breakeven_roas":     float(be_roas),
                        "marketplace":        marketplace,
                    }
                    return be_roas, costs, debug_info
        return target_roas, None, {}

    sp_grp = load_and_aggregate(sp_path, '7 Day Total Sales', country_filter=country_filter)
    sb_grp = load_and_aggregate(sb_path, '14 Day Total Sales', country_filter=country_filter)

    # Campaign type lookup — derived from already-loaded sp_grp (no extra Excel read)
    sp_types = {
        c: ('Auto' if 'AUTO' in c.upper() else 'Manual')
        for c in sp_grp['Campaign'].unique()
    }

    results = []

    # Build fast per-campaign lookups
    _sp_paused   = (sp_grp[['Campaign', 'is_paused']].drop_duplicates()
                    .set_index('Campaign')['is_paused'].to_dict()
                    if 'is_paused' in sp_grp.columns else {})
    _sp_end_date = (sp_grp[['Campaign', 'end_date']].drop_duplicates()
                    .set_index('Campaign')['end_date'].to_dict()
                    if 'end_date' in sp_grp.columns else {})

    # Use groupby so each campaign sub-frame is sliced once, not via repeated full-scan
    for camp, sub_raw in sp_grp.groupby('Campaign', sort=False):
        sub = sub_raw.set_index('PL').drop(
            columns=['Campaign', 'is_paused', 'end_date'], errors='ignore')
        avg_price                        = get_avg_price(sub)
        camp_target, cost_data, camp_debug = get_campaign_data(camp, avg_price)
        paused                           = bool(_sp_paused.get(camp, False))
        camp_end_date                    = _sp_end_date.get(camp, '')
        sc                               = score_campaign(sub, camp_target, breakeven_roas=camp_target)
        total_spend                      = sub['Spend'].sum()
        total_sales                      = sub['Sales'].sum()
        bid_str, bid_data                = bid_recommendation(sub, camp_target, min_margin_pct, cost_data, avg_price)

        # New placement algorithm
        is_new      = cost_data.get('is_new_product', False) if cost_data else False
        algo_result = placement_bid_algorithm(sub, camp_target, is_new_product=is_new, campaign_name=camp)

        # Use new algorithm placements as bid_recs_data (fallback to legacy)
        algo_placements = algo_result.get('placements', [])
        bid_data_final  = [{
            "placement_type":         p.get('label', '—'),
            "recommended_action":     p.get('recommended_action', 'Keep'),
            "recommended_multiplier": p.get('recommended_multiplier', 0),
            "reasoning":              p.get('reasoning', ''),
            "spend":                  p.get('spend', 0),
            "roas":                   p.get('roas', 0),
            "orders":                 p.get('purchases', 0),
            "debug": {
                **camp_debug,
                "placement_roas":      p.get('roas', 0),
                "placement_spend":     p.get('spend', 0),
                "placement_purchases": p.get('purchases', 0),
                "confidence":          p.get('confidence', 0),
                "current_multiplier":  p.get('current_adj', 0),
            },
        } for p in algo_placements] if algo_placements else bid_data

        r = CampaignResult(
            campaign=camp,
            ad_type='SP',
            targeting=sp_types.get(camp, 'Manual'),
            top=_build_placement(sub, 'Top'),
            rest=_build_placement(sub, 'Rest'),
            product=_build_placement(sub, 'Product'),
            score=sc,
            score_label=score_label(sc),
            bid_rec=bid_str,
            bid_recs_data=bid_data_final,
            alert=alert_message(sub, sc, low_impr),
            total_roas=total_sales / total_spend if total_spend > 0 else 0,
            marketplace=marketplace,
            mode=algo_result.get('mode', ''),
            base_bid_change_pct=algo_result.get('base_bid_change_pct', 0),
            placement_algorithm=algo_result,
            is_paused=paused,
            end_date=camp_end_date,
            is_critical=_is_critical(algo_result.get('mode', ''), sc, paused),
            breakeven_roas=float(camp_target),
        )
        results.append(r)

    _sb_paused   = (sb_grp[['Campaign', 'is_paused']].drop_duplicates()
                    .set_index('Campaign')['is_paused'].to_dict()
                    if 'is_paused' in sb_grp.columns else {})
    _sb_end_date = (sb_grp[['Campaign', 'end_date']].drop_duplicates()
                    .set_index('Campaign')['end_date'].to_dict()
                    if 'end_date' in sb_grp.columns else {})

    for camp, sub_raw in sb_grp.groupby('Campaign', sort=False):
        sub = sub_raw.set_index('PL').drop(
            columns=['Campaign', 'is_paused', 'end_date'], errors='ignore')
        avg_price                          = get_avg_price(sub)
        camp_target, cost_data, camp_debug = get_campaign_data(camp, avg_price)
        paused                             = bool(_sb_paused.get(camp, False))
        camp_end_date                      = _sb_end_date.get(camp, '')
        sc                                 = score_campaign(sub, camp_target, breakeven_roas=camp_target)
        total_spend                        = sub['Spend'].sum()
        total_sales                        = sub['Sales'].sum()
        bid_str, bid_data                  = bid_recommendation(sub, camp_target, min_margin_pct, cost_data, avg_price)

        is_new      = cost_data.get('is_new_product', False) if cost_data else False
        algo_result = placement_bid_algorithm(sub, camp_target, is_new_product=is_new, campaign_name=camp)

        algo_placements = algo_result.get('placements', [])
        bid_data_final  = [{
            "placement_type":         p['label'],
            "recommended_action":     p['recommended_action'],
            "recommended_multiplier": p['recommended_multiplier'],
            "reasoning":              p['reasoning'],
            "spend":                  p['spend'],
            "roas":                   p['roas'],
            "orders":                 p['purchases'],
            "debug": {
                **camp_debug,
                "placement_roas":      p.get('roas', 0),
                "placement_spend":     p.get('spend', 0),
                "placement_purchases": p.get('purchases', 0),
                "confidence":          p.get('confidence', 0),
                "current_multiplier":  p.get('current_adj', 0),
            },
        } for p in algo_placements] if algo_placements else bid_data

        r = CampaignResult(
            campaign=camp,
            ad_type='SB',
            targeting='Brand (SB)',
            top=_build_placement(sub, 'Top'),
            rest=_build_placement(sub, 'Rest'),
            product=_build_placement(sub, 'Product'),
            score=sc,
            score_label=score_label(sc),
            bid_rec=bid_str,
            bid_recs_data=bid_data_final,
            alert=alert_message(sub, sc, low_impr),
            total_roas=total_sales / total_spend if total_spend > 0 else 0,
            marketplace=marketplace,
            mode=algo_result.get('mode', ''),
            base_bid_change_pct=algo_result.get('base_bid_change_pct', 0),
            placement_algorithm=algo_result,
            is_paused=paused,
            end_date=camp_end_date,
            is_critical=_is_critical(algo_result.get('mode', ''), sc, paused),
            breakeven_roas=float(camp_target),
        )
        results.append(r)

    results.sort(key=lambda x: -x.score)
    return results
