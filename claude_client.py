"""
claude_client.py
Calls Anthropic Claude API to generate plain-English comments
for each campaign result. Only comments & recommendations use AI —
all scoring and bid % logic is deterministic (see analyzer.py).
"""

import anthropic
import json
from analyzer import CampaignResult, TARGET_ROAS

SYSTEM_PROMPT = """You are an expert Amazon PPC advertising analyst.
You will receive structured campaign placement data and return a concise, 
plain-English comment explaining performance and what the advertiser should do.

Rules:
- Maximum 3 sentences per comment
- Always mention which placements are above/below ROAS target
- If Top of Search ROAS is strong but impressions are low, say raise the bid
- Product Pages cannot be disabled on Sponsored Brands campaigns — never suggest that
- Never mention New-to-Brand metrics
- Write in English only
- Be direct and actionable
- Return ONLY the comment text, no JSON, no preamble
"""


def _campaign_prompt(r: CampaignResult, target_roas: float) -> str:
    def fmt(m):
        return {
            "impressions": m.impressions,
            "clicks": m.clicks,
            "spend": round(m.spend, 2),
            "sales": round(m.sales, 2),
            "orders": m.orders,
            "roas": round(m.roas, 2) if m.roas else None,
            "ctr_pct": round(m.ctr * 100, 2) if m.ctr else None,
            "cpc": round(m.cpc, 2) if m.cpc else None,
        }

    data = {
        "campaign": r.campaign,
        "ad_type": r.ad_type,
        "targeting": r.targeting,
        "roas_target": target_roas,
        "overall_roas": round(r.total_roas, 2),
        "score": r.score,
        "bid_recommendation": r.bid_rec,
        "placements": {
            "top_of_search": fmt(r.top),
            "rest_of_search": fmt(r.rest),
            "product_pages": fmt(r.product),
        }
    }
    return json.dumps(data, indent=2)


def generate_comments(
    results: list[CampaignResult],
    api_key: str,
    target_roas: float = TARGET_ROAS,
    model: str = "claude-sonnet-4-6",
    progress_callback=None,
) -> list[CampaignResult]:
    """
    Fills the .comment field of each CampaignResult using Claude API.
    Returns the same list with comments populated.
    Uses individual calls per campaign to allow streaming progress updates.
    """
    client = anthropic.Anthropic(api_key=api_key)

    for i, r in enumerate(results):
        if progress_callback:
            progress_callback(i, len(results), r.campaign)

        try:
            message = client.messages.create(
                model=model,
                max_tokens=300,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": _campaign_prompt(r, target_roas)}
                ],
            )
            r.comment = message.content[0].text.strip()
        except Exception as e:
            r.comment = f"[Comment generation failed: {e}]"

    if progress_callback:
        progress_callback(len(results), len(results), "Done")

    return results
