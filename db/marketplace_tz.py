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


# A purchase-date that states its zone ("…T23:15:02+00:00", "…Z"). Amazon's reports do;
# hand-made sheets often don't, and their clock time is not known to be UTC.
ZONED_RE = r"(?:Z|[+-]\d{2}:?\d{2})\s*$"

# Stored-row twin of is_local_convertible(): order_datetime is kept as UTC
# 'YYYY-MM-DD HH:MM:SS', and date-only values are stored with a bare 00:00:00.
STORED_HAS_TIME_SQL = ("(order_datetime IS NOT NULL AND order_datetime <> '' "
                       "AND substr(order_datetime, 12) <> '00:00:00')")


def is_local_convertible(raw: pd.Series, utc_ts: pd.Series) -> pd.Series:
    """Where a purchase moment can be placed in local time: the source value states
    its zone and isn't exactly 00:00:00 UTC (same rule as STORED_HAS_TIME_SQL, so
    import, backfill and the dashboard agree on which rows were converted)."""
    zoned = raw.astype(str).str.contains(ZONED_RE, regex=True, na=False)
    return zoned & utc_ts.notna() & (utc_ts.dt.strftime("%H:%M:%S") != "00:00:00")


def local_day_end_utc(day, marketplace) -> pd.Timestamp:
    """The UTC moment at which local calendar `day` ends in the marketplace's zone."""
    nxt = pd.Timestamp(day).normalize() + pd.Timedelta(days=1)
    return nxt.tz_localize(marketplace_tz(marketplace), nonexistent="shift_forward",
                           ambiguous=False).tz_convert("UTC")


def local_order_dates(utc_ts: pd.Series, marketplaces: pd.Series) -> pd.Series:
    """tz-aware UTC timestamps → 'YYYY-MM-DD' in each row's marketplace local time
    (DST-correct). NaT stays NaN."""
    out = pd.Series(index=utc_ts.index, dtype=object)
    tzs = marketplaces.map(marketplace_tz)
    for tz in tzs.unique():
        m = tzs == tz
        out[m] = utc_ts[m].dt.tz_convert(tz).dt.strftime("%Y-%m-%d")
    return out
