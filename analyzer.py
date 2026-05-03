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
    alert: str = ''
    comment: str = ''       # filled by Claude API
    total_roas: float = 0.0


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


def load_and_aggregate(path: str, sales_col: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    df.columns = df.columns.str.strip()
    df = df.rename(columns={sales_col: 'Sales', 'Campaign Name': 'Campaign'})

    order_cols = [c for c in df.columns if 'Orders' in c and 'Day' in c and '#' in c]
    if order_cols:
        df = df.rename(columns={order_cols[0]: 'Orders'})
    else:
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


def score_campaign(sub: pd.DataFrame, target_roas: float = TARGET_ROAS) -> int:
    top_roas   = _get(sub, 'Top', 'ROAS') or 0
    rest_roas  = _get(sub, 'Rest', 'ROAS') or 0
    top_orders = _get(sub, 'Top', 'Orders') or 0
    top_ctr    = _get(sub, 'Top', 'CTR') or 0
    total_spend = sub['Spend'].sum()
    total_sales = sub['Sales'].sum()
    total_roas  = total_sales / total_spend if total_spend > 0 else 0

    s = 0

    # Overall ROAS (35 pts)
    if total_roas >= 6:   s += 35
    elif total_roas >= 4: s += 25
    elif total_roas >= target_roas: s += 15

    # Top ROAS (35 pts)
    if top_roas >= 8:   s += 35
    elif top_roas >= 6: s += 28
    elif top_roas >= 4: s += 20
    elif top_roas >= target_roas: s += 10

    # Top vs Rest advantage (15 pts)
    if top_roas > 0 and rest_roas > 0:
        ratio = top_roas / rest_roas
        if ratio >= 2:     s += 15
        elif ratio >= 1.5: s += 10
        elif ratio >= 1.1: s += 6
        elif ratio >= 0.9: s += 3

    # CTR at Top (10 pts)
    if top_ctr >= 0.02:   s += 10
    elif top_ctr >= 0.01: s += 6
    elif top_ctr >= 0.005: s += 3

    # Volume (5 pts)
    if top_orders >= 10:  s += 5
    elif top_orders >= 5: s += 3
    elif top_orders >= 1: s += 1

    return min(s, 100)


def score_label(s: int) -> str:
    if s >= 80: return "Invest aggressively"
    if s >= 60: return "Worth it — scale gradually"
    if s >= 40: return "Test before scaling"
    return "Not recommended now"


def bid_recommendation(sub: pd.DataFrame, target_roas: float = TARGET_ROAS) -> str:
    parts = []
    total_spend = sub['Spend'].sum()
    total_sales = sub['Sales'].sum()
    avg_roas = total_sales / total_spend if total_spend > 0 else 0

    for pl in ['Top', 'Rest', 'Product']:
        roas  = _get(sub, pl, 'ROAS') or 0
        sales = _get(sub, pl, 'Sales') or 0
        spend = _get(sub, pl, 'Spend') or 0
        if spend == 0:
            continue
        if sales == 0:
            parts.append(f"{pl}: no sales — don't raise")
            continue
        if roas >= target_roas:
            ratio = roas / avg_roas if avg_roas > 0 else 1
            if pl == 'Top':
                top_impr  = _get(sub, 'Top', 'Impressions') or 0
                total_impr = sub['Impressions'].sum()
                top_share = top_impr / total_impr if total_impr > 0 else 0
                if ratio >= 1.5 and top_share < 0.15:
                    pct = min(int((ratio - 1) * 100), 100)
                elif ratio >= 1.2:
                    pct = min(int((ratio - 1) * 80), 70)
                else:
                    pct = max(min(int((ratio - 1) * 60), 30), 10)
            else:
                pct = min(max(int((roas / target_roas - 1) * 30), 10), 50)
            parts.append(f"{pl}: +{pct}%")
        else:
            parts.append(f"{pl}: 0% (ROAS {roas:.1f} < target)")

    return " | ".join(parts) if parts else "—"


def alert_message(sub: pd.DataFrame, sc: int,
                  low_impr: int = LOW_IMPR_THRESHOLD) -> str:
    top_impr = _get(sub, 'Top', 'Impressions') or 0
    if sc >= 1 and top_impr < low_impr:
        return (f"Low Top impressions ({int(top_impr):,}) — "
                f"raise bid to capture more Top of Search")
    return ""


def analyze(sp_path: str, sb_path: str,
            target_roas: float = TARGET_ROAS,
            low_impr: int = LOW_IMPR_THRESHOLD) -> list[CampaignResult]:
    """
    Main entry point.
    Returns a list of CampaignResult (comment field left empty — filled by Claude).
    """
    sp_grp = load_and_aggregate(sp_path, '7 Day Total Sales')
    sb_grp = load_and_aggregate(sb_path, '14 Day Total Sales')

    # Detect Auto campaigns in SP
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
        r = CampaignResult(
            campaign=camp,
            ad_type='SP',
            targeting=sp_types.get(camp, 'Manual'),
            top=_build_placement(sub, 'Top'),
            rest=_build_placement(sub, 'Rest'),
            product=_build_placement(sub, 'Product'),
            score=sc,
            score_label=score_label(sc),
            bid_rec=bid_recommendation(sub, target_roas),
            alert=alert_message(sub, sc, low_impr),
            total_roas=total_sales / total_spend if total_spend > 0 else 0,
        )
        results.append(r)

    for camp in sb_grp['Campaign'].unique():
        sub = sb_grp[sb_grp['Campaign'] == camp].set_index('PL').drop(columns=['Campaign'])
        sc = score_campaign(sub, target_roas)
        total_spend = sub['Spend'].sum()
        total_sales = sub['Sales'].sum()
        r = CampaignResult(
            campaign=camp,
            ad_type='SB',
            targeting='Brand (SB)',
            top=_build_placement(sub, 'Top'),
            rest=_build_placement(sub, 'Rest'),
            product=_build_placement(sub, 'Product'),
            score=sc,
            score_label=score_label(sc),
            bid_rec=bid_recommendation(sub, target_roas),
            alert=alert_message(sub, sc, low_impr),
            total_roas=total_sales / total_spend if total_spend > 0 else 0,
        )
        results.append(r)

    results.sort(key=lambda x: -x.score)
    return results


def analyze_with_products(sp_path: str, sb_path: str,
                           target_roas: float = TARGET_ROAS,
                           low_impr: int = LOW_IMPR_THRESHOLD,
                           cost_map: dict = None) -> list[CampaignResult]:
    """
    Same as analyze() but uses per-ASIN break-even ROAS when available.
    Price is calculated dynamically from report: Total Sales / Total Orders.
    Falls back to global target_roas for campaigns without ASIN match.
    cost_map = {asin: {product_cost, shipping_cost, customs_cost, fba_fee, landed_cost}}
    """
    if cost_map is None:
        cost_map = {}

    AMAZON_FEE_PCT = 0.15

    def get_avg_price(sub: pd.DataFrame) -> float:
        """Calculate average price per order from report data."""
        total_sales  = sub['Sales'].sum()
        total_orders = sub['Orders'].sum()
        if total_orders > 0:
            return total_sales / total_orders
        return 0.0

    def get_campaign_target(campaign_name: str, avg_price: float) -> float:
        """Match ASIN in campaign name to break-even ROAS using dynamic price."""
        if cost_map and avg_price > 0:
            for asin, costs in cost_map.items():
                if asin.upper() in campaign_name.upper():
                    landed_cost = costs['landed_cost']
                    fba_fee     = costs['fba_fee']
                    amazon_fee  = avg_price * AMAZON_FEE_PCT
                    margin      = avg_price - landed_cost - fba_fee - amazon_fee
                    if margin > 0:
                        return round(avg_price / margin, 2)
        return target_roas

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
        avg_price   = get_avg_price(sub)
        camp_target = get_campaign_target(camp, avg_price)
        sc = score_campaign(sub, camp_target)
        total_spend = sub['Spend'].sum()
        total_sales = sub['Sales'].sum()
        r = CampaignResult(
            campaign=camp,
            ad_type='SP',
            targeting=sp_types.get(camp, 'Manual'),
            top=_build_placement(sub, 'Top'),
            rest=_build_placement(sub, 'Rest'),
            product=_build_placement(sub, 'Product'),
            score=sc,
            score_label=score_label(sc),
            bid_rec=bid_recommendation(sub, camp_target),
            alert=alert_message(sub, sc, low_impr),
            total_roas=total_sales / total_spend if total_spend > 0 else 0,
        )
        results.append(r)

    for camp in sb_grp['Campaign'].unique():
        sub = sb_grp[sb_grp['Campaign'] == camp].set_index('PL').drop(columns=['Campaign'])
        avg_price   = get_avg_price(sub)
        camp_target = get_campaign_target(camp, avg_price)
        sc = score_campaign(sub, camp_target)
        total_spend = sub['Spend'].sum()
        total_sales = sub['Sales'].sum()
        r = CampaignResult(
            campaign=camp,
            ad_type='SB',
            targeting='Brand (SB)',
            top=_build_placement(sub, 'Top'),
            rest=_build_placement(sub, 'Rest'),
            product=_build_placement(sub, 'Product'),
            score=sc,
            score_label=score_label(sc),
            bid_rec=bid_recommendation(sub, camp_target),
            alert=alert_message(sub, sc, low_impr),
            total_roas=total_sales / total_spend if total_spend > 0 else 0,
        )
        results.append(r)

    results.sort(key=lambda x: -x.score)
    return results
