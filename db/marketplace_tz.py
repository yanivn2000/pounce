"""
db/marketplace_tz.py — the local time zone of each Amazon marketplace.

orders.order_date is the purchase day in the marketplace's local time (as Seller
Central shows it); orders.order_datetime keeps the exact purchase moment in UTC.
North-America marketplaces use Pacific time, matching Seller Central's NA reports.
"""

import pandas as pd

MARKETPLACE_TZ = {
    "amazon.com":    "America/Los_Angeles",
    "amazon.ca":     "America/Los_Angeles",
    "amazon.com.mx": "America/Los_Angeles",
    "amazon.com.br": "America/Sao_Paulo",
    "amazon.co.uk":  "Europe/London",
    "amazon.ie":     "Europe/Dublin",
    "amazon.de":     "Europe/Berlin",
    "amazon.fr":     "Europe/Paris",
    "amazon.it":     "Europe/Rome",
    "amazon.es":     "Europe/Madrid",
    "amazon.nl":     "Europe/Amsterdam",
    "amazon.se":     "Europe/Stockholm",
    "amazon.pl":     "Europe/Warsaw",
    "amazon.com.be": "Europe/Brussels",
    "amazon.com.tr": "Europe/Istanbul",
    "amazon.eg":     "Africa/Cairo",
    "amazon.ae":     "Asia/Dubai",
    "amazon.sa":     "Asia/Riyadh",
    "amazon.in":     "Asia/Kolkata",
    "amazon.sg":     "Asia/Singapore",
    "amazon.co.jp":  "Asia/Tokyo",
    "amazon.com.au": "Australia/Sydney",
}


def marketplace_tz(marketplace) -> str:
    """IANA zone for a marketplace; unknown channels stay on UTC."""
    return MARKETPLACE_TZ.get(str(marketplace or "").strip().lower(), "UTC")


def local_order_dates(utc_ts: pd.Series, marketplaces: pd.Series) -> pd.Series:
    """tz-aware UTC timestamps → 'YYYY-MM-DD' in each row's marketplace local time
    (DST-correct). NaT stays NaN."""
    out = pd.Series(index=utc_ts.index, dtype=object)
    tzs = marketplaces.map(marketplace_tz)
    for tz in tzs.unique():
        m = tzs == tz
        out[m] = utc_ts[m].dt.tz_convert(tz).dt.strftime("%Y-%m-%d")
    return out
