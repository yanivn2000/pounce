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


MONTHLY_SUMMARY_SYSTEM = """You are an expert Amazon business analyst writing a monthly \
executive summary for the brand owner.

You receive a JSON object with pre-computed, authoritative numbers:
- metrics: P&L lines with current month, previous month and same-month-last-year values,
  plus month-over-month (mom) and year-over-year (yoy) absolute and % changes, and
  flags ("good"/"bad"/null) marking changes the system already deemed dramatic.
- product_movers: vs the PREVIOUS month, each product identified by asin/sku (titles
  are near-identical). gainers/losers are EXISTING SKUs (sold in both periods) so they
  reflect genuine improvement/decline; new_products (no sales in the baseline) and
  dropped are listed SEPARATELY — do not conflate a brand-new SKU with an improvement.
- product_movers_yoy: the same, but vs the SAME MONTH LAST YEAR. For this seasonal
  gift business this is the more meaningful product comparison — call it out, and keep
  "improved existing SKUs" distinct from "new SKUs launched since last year".
- theme_movers: curated gift-niche themes (e.g. Couples, Grandparents) with this
  month's revenue vs last month, revenue-per-SKU, margin and YoY. These are across
  ALL marketplaces regardless of scope.

Hard rules:
- Use ONLY the numbers provided. NEVER invent, estimate or recompute any figure.
  All arithmetic is already done — quote the given values exactly.
- All money values are in USD. Format as $ with thousands separators, no decimals.
- Lead with the headline: total income and profit direction vs last month and last year.
- Call out every metric flagged "bad" as a concern, and flagged "good" as a positive.
- Name the top 2-3 product movers (gainers and losers) with their sales change,
  and separately highlight the year-over-year product movers (product_movers_yoy),
  since holidays recur in the same month each year.
- Add a short "Themes" note: the standout theme(s) by revenue and by revenue-per-SKU,
  and any theme with a big month-over-month swing.
- If cogs_available is false, say profit is shown before COGS (equals net payout) and
  do not describe it as final profit.
- Be concise and scannable. Output GitHub-flavored markdown: a one-line headline in bold,
  then short sections with bullet points. No preamble, no JSON, ~180 words max.
- Write in English.
"""


def generate_monthly_summary(
    stats: dict,
    api_key: str,
    model: str = "claude-opus-4-8",
) -> str:
    """Turn the deterministic monthly stats (see db.summary_module) into a
    plain-English executive summary. All numbers come from `stats`; Claude
    only writes the narrative."""
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=1000,
        system=MONTHLY_SUMMARY_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(stats, ensure_ascii=False)}],
    )
    return message.content[0].text.strip()


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
