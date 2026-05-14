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
    bid_recs_data: list = field(default_factory=list)  # structured per-placement recs
    alert: str = ''
    comment: str = ''       # filled by Claude API
    total_roas: float = 0.0
    marketplace: str = 'amazon.com'


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

    # Handle empty or missing Placement column gracefully
    if 'Placement' not in df.columns or df.empty:
        return pd.DataFrame(columns=['Campaign', 'PL', 'Impressions',
                                     'Clicks', 'Spend', 'Sales', 'Orders',
                                     'CTR', 'CPC', 'ROAS', 'ACOS'])

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
    fba_pct    = fba_fee / avg_price
    max_acos   = 1 - min_margin_pct - amazon_fee_pct - fba_pct
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
    Recommend bid adjustments per placement.
    Returns (display_string, structured_list).
    structured_list has one dict per placement with spend > 0:
      {placement_type, recommended_action, recommended_multiplier, reasoning, spend, roas, orders}
    """
    parts = []
    structured = []
    total_spend = sub['Spend'].sum()
    total_sales = sub['Sales'].sum()
    avg_roas = total_sales / total_spend if total_spend > 0 else 0

    # Calculate margin floor ROAS
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


def analyze_with_products(sp_path: str, sb_path: str,
                           target_roas: float = TARGET_ROAS,
                           low_impr: int = LOW_IMPR_THRESHOLD,
                           cost_map: dict = None,
                           min_margin_pct: float = 0.25,
                           marketplace: str = 'amazon.com') -> list[CampaignResult]:
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

    def get_campaign_data(campaign_name: str, avg_price: float) -> tuple[float, dict | None]:
        """
        Returns (campaign_target_roas, cost_data_or_none).
        Matches ASIN in campaign name to break-even ROAS using dynamic price.
        """
        if cost_map and avg_price > 0:
            for asin, costs in cost_map.items():
                if asin.upper() in campaign_name.upper():
                    landed_cost = costs['landed_cost']
                    fba_fee     = costs['fba_fee']
                    amazon_fee  = avg_price * AMAZON_FEE_PCT
                    margin      = avg_price - landed_cost - fba_fee - amazon_fee
                    be_roas     = round(avg_price / margin, 2) if margin > 0 else target_roas
                    return be_roas, costs
        return target_roas, None

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
        avg_price              = get_avg_price(sub)
        camp_target, cost_data = get_campaign_data(camp, avg_price)
        sc                     = score_campaign(sub, camp_target)
        total_spend            = sub['Spend'].sum()
        total_sales            = sub['Sales'].sum()
        bid_str, bid_data      = bid_recommendation(sub, camp_target, min_margin_pct, cost_data, avg_price)
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
            marketplace=marketplace,
        )
        results.append(r)

    for camp in sb_grp['Campaign'].unique():
        sub = sb_grp[sb_grp['Campaign'] == camp].set_index('PL').drop(columns=['Campaign'])
        avg_price              = get_avg_price(sub)
        camp_target, cost_data = get_campaign_data(camp, avg_price)
        sc                     = score_campaign(sub, camp_target)
        total_spend            = sub['Spend'].sum()
        total_sales            = sub['Sales'].sum()
        bid_str, bid_data      = bid_recommendation(sub, camp_target, min_margin_pct, cost_data, avg_price)
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
            marketplace=marketplace,
        )
        results.append(r)

    results.sort(key=lambda x: -x.score)
    return results