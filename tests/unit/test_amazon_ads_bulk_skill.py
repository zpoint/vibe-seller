"""Unit tests for the amazon-ads skill's scripts/ads_bulk.py.

Pins the two invariants that make the bulk-sheet tool trustworthy:

* **Locale-generality** — the Sponsored Products sheet is parsed by
  fixed column *position*, never by header text. These tests build a
  synthetic workbook with **Chinese** headers and sheet name (the
  hardest case) and assert every operation still works.
* **The ASIN-as-SKU guard** — a Product Ad row whose SKU is an ASIN
  makes Amazon silently drop the whole ad group, so `clone-campaign`
  must refuse an ASIN in `--sku`.

Plus round-trip shape: clone emits paused Create rows with the SKU in
the SKU column and the source keywords cloned; bid-update emits scaled
Update rows.

All test data is fabricated — no real store, brand, campaign, SKU, or
ASIN values appear in this file.
"""

import argparse
import importlib.util
from pathlib import Path
import sys

import pytest

openpyxl = pytest.importorskip('openpyxl')

_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / 'app'
    / 'skills'
    / 'amazon-ads'
    / 'scripts'
    / 'ads_bulk.py'
)

_spec = importlib.util.spec_from_file_location('ads_bulk', _SKILL_PATH)
ads_bulk = importlib.util.module_from_spec(_spec)
sys.modules['ads_bulk'] = ads_bulk
_spec.loader.exec_module(ads_bulk)

Col = ads_bulk.Col
NUM_COLS = ads_bulk.NUM_COLS

# Localised (zh_CN) header row + sheet name — the stress case for the
# positional parser. Only positions matter; labels are decorative.
ZH_HEADER = [
    '产品',
    '实体层级',
    '操作',
    '广告活动编号',
    '广告组编号',
    '广告组合编号',
    '广告编号',
    '关键词编号',
    '商品投放 ID',
    '广告活动名称',
    '广告组名称',
    '广告活动名称（仅供参考）',
    '广告组名称（仅供参考）',
    '广告组合名称（仅供参考）',
    '开始日期',
    '结束日期',
    '投放类型',
    '状态',
    '广告活动状态（仅供参考）',
    '广告组状态（仅供参考）',
    '每日预算',
    'SKU',
    'ASIN（仅供参考）',
    '资格状态（仅供参考）',
    '不符合条件的原因（仅供参考）',
    '广告组默认竞价',
    '广告组默认竞价（仅供参考）',
    '竞价',
    '关键词文本',
    '母语关键词',
    '母语区域',
    '匹配类型',
    '竞价方案',
    '广告位',
    '百分比',
    '拓展商品投放编号',
    '拓展商品投放名称（仅供参考）',
    '受众编号',
    '购物者群体占比',
    '购物者群体类型',
    '站点名称（仅供参考）',
    '展示量',
    '点击量',
    '点击率',
    '花费',
    '销量',
    '订单数量',
    '商品数量',
    '转化率',
    'ACOS',
    'CPC',
    'ROAS',
]
assert len(ZH_HEADER) == NUM_COLS

ZH_SHEET = '商品推广活动'
SRC_CAMPAIGN = 'acme widgets 004 manual keyword US'


def _row(**kw):
    r = [None] * NUM_COLS
    for name, val in kw.items():
        r[getattr(Col, name)] = val
    return r


def _make_export(path):
    """Write a synthetic zh_CN bulk export: 1 campaign + ad group +
    product ad + 2 keywords, with metrics on the campaign row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = ZH_SHEET
    ws.append(ZH_HEADER)
    ws.append(
        _row(
            ENTITY='广告活动',
            CAMPAIGN_ID='C1',
            CAMPAIGN_NAME=SRC_CAMPAIGN,
            STATE='已启用',
            DAILY_BUDGET=15.0,
            SPEND=100.0,
            SALES=250.0,
            ACOS=0.4,
        )
    )
    ws.append(
        _row(
            ENTITY='广告组',
            CAMPAIGN_ID='C1',
            CAMPAIGN_NAME_INFO=SRC_CAMPAIGN,
            AD_GROUP_NAME='ag1',
            STATE='已启用',
            AD_GROUP_DEFAULT_BID=0.8,
        )
    )
    ws.append(
        _row(
            ENTITY='商品广告',
            CAMPAIGN_ID='C1',
            CAMPAIGN_NAME_INFO=SRC_CAMPAIGN,
            STATE='已启用',
            SKU='WIDGET-004-Blue',
            ASIN_INFO='B0AAAAAAAA',
        )
    )
    ws.append(
        _row(
            ENTITY='关键词',
            CAMPAIGN_ID='C1',
            CAMPAIGN_NAME_INFO=SRC_CAMPAIGN,
            STATE='已启用',
            BID=2.0,
            KEYWORD_TEXT='widget',
            MATCH_TYPE='广泛',
        )
    )
    ws.append(
        _row(
            ENTITY='关键词',
            CAMPAIGN_ID='C1',
            CAMPAIGN_NAME_INFO=SRC_CAMPAIGN,
            STATE='已启用',
            BID=3.0,
            KEYWORD_TEXT='blue widget',
            MATCH_TYPE='精准',
        )
    )
    wb.save(path)


def _read_rows(path):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    return ws.title, rows[0], [list(r) for r in rows[1:]]


@pytest.mark.unit
class TestAsinGuard:
    def test_asin_detected(self):
        assert ads_bulk.looks_like_asin('B0ABCD1234')
        assert ads_bulk.looks_like_asin('b0abcd1234')  # case-insensitive

    def test_real_sku_not_flagged(self):
        assert not ads_bulk.looks_like_asin('WIDGET-006-Blue-M')
        assert not ads_bulk.looks_like_asin('GADGET-024')
        assert not ads_bulk.looks_like_asin('')


@pytest.mark.unit
class TestLocaleGeneralParsing:
    def test_find_sheet_by_localised_name(self, tmp_path):
        p = tmp_path / 'export.xlsx'
        _make_export(p)
        wb = openpyxl.load_workbook(p, data_only=True)
        assert ads_bulk.find_sp_sheet(wb).title == ZH_SHEET

    def test_entity_detected_from_chinese_tokens(self, tmp_path):
        p = tmp_path / 'export.xlsx'
        _make_export(p)
        _wb, _ws, _hdr, data = ads_bulk.load(str(p))
        kinds = {
            k: sum(1 for r in data if ads_bulk.is_entity(r, k))
            for k in ('campaign', 'ad_group', 'product_ad', 'keyword')
        }
        assert kinds == {
            'campaign': 1,
            'ad_group': 1,
            'product_ad': 1,
            'keyword': 2,
        }


@pytest.mark.unit
class TestCloneCampaign:
    def _args(self, tmp_path, **over):
        base = dict(
            file=str(tmp_path / 'export.xlsx'),
            src=SRC_CAMPAIGN,
            new='acme widgets 006 manual keyword US',
            ad_group=None,
            sku='WIDGET-006-Blue-M',
            asin='B0BBBBBBBB',
            daily_budget=1.0,
            default_bid=0.75,
            keyword_bid=None,
            bidding_strategy='Dynamic bids - down only',
            start_date='20260701',
            out=str(tmp_path / 'create.xlsx'),
        )
        base.update(over)
        return argparse.Namespace(**base)

    def test_refuses_asin_as_sku(self, tmp_path):
        _make_export(tmp_path / 'export.xlsx')
        with pytest.raises(SystemExit):
            ads_bulk.cmd_clone_campaign(self._args(tmp_path, sku='B0ABCD1234'))

    def test_emits_paused_create_rows_with_cloned_keywords(self, tmp_path):
        _make_export(tmp_path / 'export.xlsx')
        ads_bulk.cmd_clone_campaign(self._args(tmp_path))
        _title, header, rows = _read_rows(tmp_path / 'create.xlsx')
        # Header cloned verbatim (locale-preserving upload).
        assert list(header) == ZH_HEADER
        ents = [r[Col.ENTITY] for r in rows]
        assert ents == [
            'Campaign',
            'Ad Group',
            'Product Ad',
            'Keyword',
            'Keyword',
        ]
        assert all(r[Col.OPERATION] == 'Create' for r in rows)
        # State is the English API token, never the localised display one.
        assert all(r[Col.STATE] == 'paused' for r in rows)
        # Every Create row carries the placeholder Campaign Id linker, and
        # child rows the Ad Group Id, or Amazon rejects them.
        assert all(r[Col.CAMPAIGN_ID] for r in rows)
        assert all(r[Col.AD_GROUP_ID] for r in rows[1:])  # all but campaign
        # Campaign row: required Start Date + uppercase MANUAL targeting.
        assert rows[0][Col.START_DATE] == '20260701'
        assert rows[0][Col.TARGETING_TYPE] == 'MANUAL'
        assert rows[0][Col.DAILY_BUDGET] == 1.0
        # Product Ad SKU is the seller SKU (not the ASIN).
        assert rows[2][Col.SKU] == 'WIDGET-006-Blue-M'
        # Keyword match type NORMALISED to the English API token
        # (源 广泛/精准 -> broad/exact), not the localised display value.
        kw = [
            (r[Col.KEYWORD_TEXT], r[Col.MATCH_TYPE], r[Col.BID])
            for r in rows
            if r[Col.ENTITY] == 'Keyword'
        ]
        assert kw == [('widget', 'broad', 2.0), ('blue widget', 'exact', 3.0)]

    def test_missing_source_campaign_exits(self, tmp_path):
        _make_export(tmp_path / 'export.xlsx')
        with pytest.raises(SystemExit):
            ads_bulk.cmd_clone_campaign(
                self._args(tmp_path, src='no such campaign')
            )


@pytest.mark.unit
class TestBidUpdate:
    def _args(self, tmp_path, **over):
        base = dict(
            file=str(tmp_path / 'export.xlsx'),
            campaign=SRC_CAMPAIGN,
            scale=None,
            set_bid=None,
            out=str(tmp_path / 'bid.xlsx'),
        )
        base.update(over)
        return argparse.Namespace(**base)

    def test_scale_produces_update_rows(self, tmp_path):
        _make_export(tmp_path / 'export.xlsx')
        ads_bulk.cmd_bid_update(self._args(tmp_path, scale=0.5))
        _title, _header, rows = _read_rows(tmp_path / 'bid.xlsx')
        assert all(r[Col.OPERATION] == 'Update' for r in rows)
        # State is REQUIRED on Update rows, normalised to the API token
        # (source 已启用 -> enabled), never left blank or localised.
        assert all(r[Col.STATE] == 'enabled' for r in rows)
        bids = sorted(r[Col.BID] for r in rows)
        assert bids == [1.0, 1.5]  # 2.0*0.5, 3.0*0.5

    def test_set_bid_overrides_all(self, tmp_path):
        _make_export(tmp_path / 'export.xlsx')
        ads_bulk.cmd_bid_update(self._args(tmp_path, set_bid=1.25))
        _title, _header, rows = _read_rows(tmp_path / 'bid.xlsx')
        assert [r[Col.BID] for r in rows] == [1.25, 1.25]

    def test_no_lever_exits(self, tmp_path):
        _make_export(tmp_path / 'export.xlsx')
        with pytest.raises(SystemExit):
            ads_bulk.cmd_bid_update(self._args(tmp_path))


@pytest.mark.unit
class TestTokenNormalisation:
    """Upload needs English API tokens; exports DISPLAY localised ones.
    Verified live: a zh_CN account rejected localised tokens ('0 of N')."""

    def test_match_type_localised_to_api(self):
        assert ads_bulk.match_type_api('广泛') == 'broad'
        assert ads_bulk.match_type_api('词组') == 'phrase'
        assert ads_bulk.match_type_api('精准') == 'exact'

    def test_match_type_english_passthrough(self):
        assert ads_bulk.match_type_api('broad') == 'broad'
        assert ads_bulk.match_type_api('EXACT') == 'exact'
        assert ads_bulk.match_type_api(None) is None

    def test_state_localised_to_api(self):
        assert ads_bulk.state_api('已启用') == 'enabled'
        assert ads_bulk.state_api('已暂停') == 'paused'
        assert ads_bulk.state_api('已归档') == 'archived'
        assert ads_bulk.state_api('enabled') == 'enabled'


@pytest.mark.unit
class TestArchiveCampaign:
    def _args(self, tmp_path, **over):
        base = dict(
            file=str(tmp_path / 'export.xlsx'),
            campaign=SRC_CAMPAIGN,
            out=str(tmp_path / 'archive.xlsx'),
        )
        base.update(over)
        return argparse.Namespace(**base)

    def test_emits_archive_row_with_campaign_id(self, tmp_path):
        _make_export(tmp_path / 'export.xlsx')
        ads_bulk.cmd_archive_campaign(self._args(tmp_path))
        _title, _header, rows = _read_rows(tmp_path / 'archive.xlsx')
        assert len(rows) == 1
        r = rows[0]
        assert r[Col.ENTITY] == 'Campaign'
        assert r[Col.OPERATION] == 'Archive'
        # Archive keys off the REAL campaign id (from the export), the
        # only required field per Config.
        assert r[Col.CAMPAIGN_ID] == 'C1'

    def test_unknown_campaign_exits(self, tmp_path):
        _make_export(tmp_path / 'export.xlsx')
        with pytest.raises(SystemExit):
            ads_bulk.cmd_archive_campaign(
                self._args(tmp_path, campaign='no such campaign')
            )
