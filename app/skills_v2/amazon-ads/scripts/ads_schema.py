"""The Sponsored Products bulk-sheet schema, in one place.

Split out of ``ads_bulk.py`` to keep both files under the repo's per-file
line limit. It is only the SHAPE of the sheet — column positions, entity
and state enums, and the display->API token maps — so the command
implementations in ``ads_bulk.py`` read as operations rather than as
column arithmetic.

The upload path is the reason the token maps exist: an export DISPLAYS
localised values, but Amazon's uploader accepts only the English API
tokens its own ``Config`` sheet lists. A zh_CN account rejected an upload
carrying localised match tokens with "0 of N uploaded".
"""

import re


# --- 52-column schema, addressed positionally (§ mechanics 4c) ---------
class Col:
    PRODUCT = 0
    ENTITY = 1
    OPERATION = 2
    CAMPAIGN_ID = 3
    AD_GROUP_ID = 4
    PORTFOLIO_ID = 5
    AD_ID = 6
    KEYWORD_ID = 7
    PRODUCT_TARGETING_ID = 8
    CAMPAIGN_NAME = 9
    AD_GROUP_NAME = 10
    CAMPAIGN_NAME_INFO = 11
    AD_GROUP_NAME_INFO = 12
    PORTFOLIO_NAME_INFO = 13
    START_DATE = 14
    END_DATE = 15
    TARGETING_TYPE = 16
    STATE = 17
    CAMPAIGN_STATE_INFO = 18
    AD_GROUP_STATE_INFO = 19
    DAILY_BUDGET = 20
    SKU = 21
    ASIN_INFO = 22
    ELIGIBILITY_INFO = 23
    INELIGIBILITY_REASON_INFO = 24
    AD_GROUP_DEFAULT_BID = 25
    AD_GROUP_DEFAULT_BID_INFO = 26
    BID = 27
    KEYWORD_TEXT = 28
    NATIVE_LANGUAGE_KEYWORD = 29
    NATIVE_LANGUAGE_LOCALE = 30
    MATCH_TYPE = 31
    BIDDING_STRATEGY = 32
    PLACEMENT = 33
    PERCENTAGE = 34
    PRODUCT_TARGETING_EXPR = 35
    RESOLVED_TARGETING_EXPR_INFO = 36
    AUDIENCE_ID = 37
    SHOPPER_COHORT_PCT = 38
    SHOPPER_COHORT_TYPE = 39
    SEGMENT_NAME_INFO = 40
    IMPRESSIONS = 41
    CLICKS = 42
    CTR = 43
    SPEND = 44
    SALES = 45
    ORDERS = 46
    UNITS = 47
    CONVERSION_RATE = 48
    ACOS = 49
    CPC = 50
    ROAS = 51


NUM_COLS = 52

# Sheet names by locale (English first). Fallback: the 52-column sheet.
SHEET_NAMES = ('Sponsored Products Campaigns', '商品推广活动')

# Entity enum values by locale (add localisations as observed).
ENTITY = {
    'campaign': ('Campaign', '广告活动'),
    'bidding_adjustment': ('Bidding Adjustment', '竞价调整'),
    'ad_group': ('Ad Group', '广告组'),
    'product_ad': ('Product Ad', '商品广告'),
    'keyword': ('Keyword', '关键词'),
    'negative_keyword': ('Negative Keyword', '否定关键词'),
    'campaign_negative_keyword': (
        'Campaign Negative Keyword',
        '广告活动否定关键词',
    ),
    'product_targeting': ('Product Targeting', '商品定向'),
}

# IMPORTANT — upload uses ENGLISH API tokens, NOT the localised strings
# the export DISPLAYS. The export's own `Config` sheet lists the valid
# upload values (SponsoredProducts*States/OperationNames/TargetingTypes/
# MatchTypes/Strategys) and they are all English. So a keyword the export
# shows as `广泛` / `精准` must be written back as `broad` / `exact`, or
# Amazon rejects the row. Verified live: a zh_CN account rejected an
# upload carrying localised match tokens with "0 of N uploaded".
STATE_ENABLED = 'enabled'
STATE_PAUSED = 'paused'

# Config: SponsoredProductsCreateCampaignTargetingTypes = AUTO | MANUAL.
TARGETING_MANUAL = 'MANUAL'
TARGETING_AUTO = 'AUTO'

# Config: SponsoredProductsCreateKeywordMatchTypes = exact|phrase|broad.
# Map the localised DISPLAY tokens seen in exports -> the API token.
MATCH_TYPE_API = {
    'broad': 'broad',
    'phrase': 'phrase',
    'exact': 'exact',
    '广泛': 'broad',
    '词组': 'phrase',
    '精准': 'exact',
}


def match_type_api(display):
    """Normalise an export's (possibly localised) match type to the API
    token Amazon's uploader accepts. Falls back to a lowercased value."""
    if display is None:
        return None
    key = str(display).strip()
    return MATCH_TYPE_API.get(key, key.lower())


# Config: *States = enabled|paused|archived. Map localised display -> API.
STATE_API = {
    'enabled': 'enabled',
    'paused': 'paused',
    'archived': 'archived',
    '已启用': 'enabled',
    '已暂停': 'paused',
    '已归档': 'archived',
}


def state_api(display):
    """Normalise an export's (possibly localised) state to the API token."""
    if display is None:
        return None
    return STATE_API.get(str(display).strip(), str(display).strip().lower())


# Header row validation dictionary (position -> accepted labels). Only a
# few load-bearing columns; parsing never depends on this.
HEADER_CHECK = {
    Col.ENTITY: ('Entity', '实体层级'),
    Col.OPERATION: ('Operation', '操作'),
    Col.CAMPAIGN_NAME: ('Campaign Name', '广告活动名称'),
    Col.SKU: ('SKU',),
    Col.BID: ('Bid', '竞价'),
    Col.MATCH_TYPE: ('Match Type', '匹配类型'),
}

ASIN_RE = re.compile(r'^B0[0-9A-Z]{8}$')


def looks_like_asin(value):
    """True if value matches an Amazon ASIN (B0 + 8 alphanumerics)."""
    return bool(ASIN_RE.match(str(value or '').strip().upper()))
