# -*- coding: utf-8 -*-
"""share app cards-v2（CONTRACT §5、01 §12.2）離線測試。

涵蓋：dry_run 單筆＋多筆 HTML 跑 audit_html 0 命中、同事頁無「景泰」、XSS 跳脫、非白名單網址被拒、
X-Share-Key ENFORCE=0／1、urls_text 舊路徑（假 fetch）、租屋分支、格式錯 400、稽核命中 422。
github_push／notion_log_snapshot 一律換成假的（不出網、不推 GitHub）。
"""
try:
    from . import _sguard
except ImportError:
    import _sguard

import hashlib
import io
import os
import re
import unittest
from unittest import mock
from urllib.parse import quote

I = _sguard.load_index()
L, SC, A = I._L, I._SC, I._A
from _lib import single_page as SP                         # noqa: E402

OWNER = {'agent_name': '陳景泰', 'agent_license': '114年登字第488296號', 'phone': '0920-118-756', 'line': 'sky811117'}
MATE = {'agent_name': '王大明', 'agent_license': '113年登字第123456號', 'phone': '0911-222-333', 'line': 'wangdm88'}
YC_URL = 'https://buy.yungching.com.tw/house/1234567'
S591_URL = 'https://sale.591.com.tw/home/house/detail/2/12345678.html'
RK_URL = 'https://www.rakuya.com.tw/sell_item/info?ehid=0123456789abcde'
IMG591 = 'https://img1.591.com.tw/house/2025/09/12/abc.jpg'
IMGYC = 'https://yccdn.yungching.com.tw/v1/image/?key=NF58&width=1024'
OWNER_MARKS = ('景泰', '0920-118-756', '0920118756', 'sky811117', 'nov__817')


def card(**kw):
    c = {'_card': True, 'og_title': '總太聚作 3樓 3房', 'community_display': '總太聚作', 'address': '北屯區崇德路二段',
         'price': 1980, 'price_range': [1950, 1980], 'area': 46.98, 'main_area': 25.1, 'layout': '3房2廳2衛',
         'age': 18.0, 'floor': '3/15F', 'floor_total': 15, 'building_type': '大樓', 'parking': '坡道平面',
         'has_parking': True, 'og_image': IMG591, 'gallery': [IMG591, IMGYC],
         'intro': '邊間三房採光好\n近公園，生活機能便利', 'links': [{'n': 1, 'url': S591_URL}, {'n': 2, 'url': YC_URL}],
         'mode': 'sale', 'rent': None, 'slug': 'qsAbc123', 'map_q': '台中市北屯區崇德路二段', 'vr_url': None,
         'src_url': [S591_URL], 'mp_version': L.MP_VERSION}
    c.update(kw)
    return c


def card2(**kw):
    c = card(og_title='西屯區市政路 大樓 12樓 2房', community_display=None, address='西屯區市政路', price=1350,
             price_range=None, floor='12/24F', floor_total=24, layout='2房2廳1衛', age=9.0, has_parking=False,
             parking='', og_image=IMGYC, gallery=[IMGYC], intro='住商混合大樓，近全聯門市步行3分鐘',
             links=[{'n': 1, 'url': RK_URL}], slug='qsDef456', map_q='台中市西屯區市政路', src_url=[RK_URL])
    c.update(kw)
    return c


def single(**kw):
    p = {'title': '總太聚作 3樓 3房', 'caseName': '總太聚作 3樓 3房', 'subtitle': '北屯三房邊間採光好',
         'address': '台中市北屯區崇德路二段', 'road': '崇德路二段', 'district': '北屯區', 'county': '台中市',
         'community': '總太聚作', 'price': 1980, 'pin': 46.98, 'pinAll': {'regArea': 46.98, 'mainArea': 25.1},
         'mainArea': 25.1, 'floor': '3/15', 'age': 18, 'manage': {'manageExpense': '2,500元/月'},
         'parking': ['坡道平面'], 'school': [{'name': '仁美國小'}, {'name': '崇德國中'}],
         'pattern': {'room': 3, 'livingRoom': 2, 'bathRoom': 2}, 'intro': '邊間三房採光好\n近公園',
         'photos': ['https://yccdn.yungching.com.tw/v1/image/?key=AAA'], 'links': [{'n': 1, 'url': YC_URL}],
         'map_q': '台中市北屯區崇德路二段'}
    p.update(kw)
    return p


class _Base(unittest.TestCase):

    def setUp(self):
        self.client = I.app.test_client()
        self.pushed, self.snaps = [], []
        for name, fn in (('github_push', self._push), ('notion_log_snapshot', self._snap)):
            pt = mock.patch.object(I, name, side_effect=fn)
            pt.start()
            self.addCleanup(pt.stop)
        env = mock.patch.dict(os.environ, {'GITHUB_TOKEN': 'test-token'})
        env.start()
        self.addCleanup(env.stop)
        self.net0 = len(_sguard.attempts())

    def tearDown(self):
        self.assertEqual(_sguard.attempts()[self.net0:], [], '測試期間不准連外網')

    def _push(self, path, content, message, token, update=False):
        self.pushed.append({'path': path, 'content': content, 'message': message, 'update': update})
        return {}

    def _snap(self, share_id, name, props):
        self.snaps.append((share_id, name, props))
        return len(props)

    def body(self, contact=OWNER, props=None, **kw):
        b = dict(contact, name='王先生', need='北屯三房', mp_version=L.MP_VERSION)
        if props is not None:
            b['props'] = props
        b.update(kw)
        return b

    def post(self, body, headers=None):
        r = self.client.post('/api/publish', json=body, headers=headers or {})
        return r.status_code, r.get_json()

    def publish_html(self, body, headers=None):
        """實際走推送分支（假 github_push），回 (html, 回應 JSON)。"""
        code, j = self.post(dict(body, dry_run=False), headers)
        self.assertEqual(code, 200, j)
        return self.pushed[-1]['content'], j

    def audit(self, html, contact, allowed=(), names=('王先生', '找房需求：北屯三房')):
        allow = {'names': list(names), 'owner': contact['agent_name'] == '陳景泰'}
        return A.audit_html(html, contact, frozenset(allowed), allow)


# ───────────────────────── dry_run＋整頁稽核 0 命中 ─────────────────────────
class DryRunAuditTest(_Base):

    def test_dry_run_cards_owner_and_mate(self):
        for contact in (OWNER, MATE):
            code, j = self.post(self.body(contact, [card(), card2()], dry_run=True))
            self.assertEqual(code, 200, j)
            self.assertEqual(j['render'], 'cards-v2')
            self.assertEqual(j['build'], I.BUILD)
            self.assertEqual(j['leak'], [], (contact['agent_name'], j))
            self.assertGreater(j['html_len'], 1000)
        self.assertEqual(self.pushed, [], 'dry_run 不准推 GitHub')
        self.assertEqual(self.snaps, [])

    def test_dry_run_single_owner_and_mate(self):
        for contact in (OWNER, MATE):
            code, j = self.post(self.body(contact, [single()], dry_run=True))
            self.assertEqual(code, 200, j)
            self.assertEqual(j['render'], 'single-v2')
            self.assertEqual(j['leak'], [], (contact['agent_name'], j))
        self.assertEqual(self.pushed, [])

    def test_pushed_cards_html_audit_zero(self):
        """推上去的那份 HTML 拿出來獨立再跑一次 audit_html：0 命中。"""
        for contact in (OWNER, MATE):
            html, j = self.publish_html(self.body(contact, [card(), card2()]))
            res = self.audit(html, contact, {S591_URL, YC_URL, RK_URL})
            self.assertTrue(res.ok, [(h.rule, h.where, h.snippet) for h in res.hits])
            self.assertEqual(j['render'], 'cards-v2')
            self.assertTrue(j['share_id'].startswith('qs'))
            self.assertIn('<meta name="x-render" content="cards-v2">', html)
            self.assertIn('<meta name="referrer" content="no-referrer">', html)
            self.assertIn('<meta name="x-share-promo"', html)          # stamp_promo 有蓋
            self.assertEqual(html.count('<!--z:links-->'), 2)
            self.assertEqual(html.count('<!--z:contact-->'), 2)
            self.assertIn('rel="nofollow noopener noreferrer">原始刊登 1</a>', html)
            self.assertIn('原始刊登 2</a>', html)
            self.assertIn('各刊登開價 1,950–1,980 萬', html)
            self.assertIn('本頁物件資訊整理自公開刊登資料', html)

    def test_pushed_single_html_audit_zero(self):
        for contact in (OWNER, MATE):
            html, j = self.publish_html(self.body(contact, [single()]))
            res = self.audit(html, contact, {YC_URL})
            self.assertTrue(res.ok, [(h.rule, h.where, h.snippet) for h in res.hits])
            self.assertEqual(j['render'], 'single-v2')
            self.assertIn('<meta name="x-render" content="single-v2">', html)
            self.assertEqual(html.count('<!--z:contact-->'), 2)         # .foot、.bar 兩塊
            self.assertIn('<!--z:links-->', html)
            self.assertIn('原始刊登 1</a>', html)

    def test_snapshot_keeps_src_urls_internal(self):
        html, j = self.publish_html(self.body(OWNER, [card(), card2()]))
        self.assertEqual(len(self.snaps), 1)
        sid, name, props = self.snaps[0]
        self.assertEqual(sid, j['share_id'])
        self.assertEqual(props[0]['detail_url'], S591_URL)
        self.assertIn(YC_URL, props[0]['src_urls_all'])
        self.assertNotIn('class="card-cta"', html)                          # 卡片不另外外連（只有連結區）
        self.assertEqual(html.count('href="%s"' % S591_URL), 1)


# ───────────────────────── 同事頁 ─────────────────────────
class ColleagueTest(_Base):

    def test_colleague_pages_have_no_teddy(self):
        for props in ([card(), card2()], [card()], [single()]):
            html, _ = self.publish_html(self.body(MATE, props))
            for mk in OWNER_MARKS:
                self.assertNotIn(mk, html, '同事頁出現 %s' % mk)
            self.assertIn('王大明', html)

    def test_colleague_like_is_bookmark_only(self):
        html, _ = self.publish_html(self.body(MATE, [card(), card2()]))
        self.assertIn('var LIKE_NOTIFY = false;', html)
        self.assertIn("'已收藏'", html)
        self.assertNotIn('footer-site-btn', html.split('</style>', 1)[1])   # 官網推廣不出現
        html2, _ = self.publish_html(self.body(OWNER, [card(), card2()]))
        self.assertIn('var LIKE_NOTIFY = true;', html2)
        self.assertIn('認識景泰', html2)                                    # 景泰本人頁照舊有官網推廣

    def test_colleague_missing_line_falls_back_and_is_blocked(self):
        """同事沒填 LINE → contact 補成景泰的 → A13 擋下（422），不推 GitHub。"""
        mate = dict(MATE)
        mate.pop('line')
        code, j = self.post(dict(self.body(mate, [card()]), dry_run=False))
        self.assertEqual(code, 422, j)
        self.assertEqual(j['error'], 'audit')
        self.assertIn('A13', {h['rule'] for h in j['hits']})
        self.assertEqual(self.pushed, [])

    def test_theme_and_signature_defaults(self):
        html, _ = self.publish_html(self.body(MATE, [card(), card2()]))
        self.assertIn('精選物件 · ', html)
        self.assertIn('<div class="footer-title">王大明 · ', html)


# ───────────────────────── XSS ─────────────────────────
class XssTest(_Base):

    def test_card_fields_escaped_in_template(self):
        p = {'community_display': '"><script>alert(1)</script>', 'og_title': '<img src=x onerror=alert(1)>',
             'address': '北屯區<b>崇德路</b>', 'price': 1000, 'area': 30, 'layout': '<svg onload=alert(1)>',
             'building_type': '大樓"><i>', 'slug': 'a"b', 'gallery': [], 'note': '<script>n()</script>',
             'parking': '<u>坡道</u>', 'floor': 3, 'floor_total': 15}
        h = I.card_html(p, {'agent_name': '<b>王</b>'})
        for bad in ('<script', '<img src=x', '<svg', '<b>', '<u>', '<i>', 'a"b'):
            self.assertNotIn(bad, h)
        self.assertIn('&lt;script&gt;', h)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', h)

    def test_props_title_script_never_rendered(self):
        evil = card(og_title='<script>alert(1)</script>總太聚作 3樓', community_display='"><img src=x onerror=alert(1)>',
                    intro='<script>alert(2)</script>邊間採光好')
        html, _ = self.publish_html(self.body(OWNER, [evil, card2()]))
        self.assertNotIn('alert(', html)
        self.assertNotIn('onerror', html)
        self.assertEqual(len(re.findall(r'<script\b', html)), 1)            # 只有模板自己那支

    def test_client_name_cannot_break_script(self):
        sc, _, _ = I._sanitize_card(card())
        evil = '</script><script>alert(1)</script>'
        html = I.gen_html({'name': evil, 'need': '"><img src=x>', 'share_id': 'qsX', 'contact': I.build_contact(OWNER)},
                          [I._card_to_prop(sc, 0)])
        self.assertNotIn('<script>alert(1)', html)
        self.assertNotIn('<img src=x>', html)
        self.assertEqual(len(re.findall(r'<script\b', html)), 1)
        self.assertIn('\\u003c/script\\u003e', html)                         # CLIENT_NAME 的 JSON 有跳脫

    def test_single_title_escaped(self):
        h = SP.render({'title': '"><script>alert(1)</script>', 'address': '台中市北屯區崇德路二段',
                       'intro': '<img src=x onerror=1>'}, I.build_contact(OWNER), 'KEY', '<b>王</b>', '')
        self.assertNotIn('<script>alert', h)
        self.assertNotIn('<img src=x', h)
        self.assertNotIn('<b>王', h)
        self.assertIn('&lt;script&gt;', h)

    def test_single_props_script_title_dropped(self):
        html, _ = self.publish_html(self.body(OWNER, [single(title='<script>alert(1)</script>',
                                                             caseName='<script>alert(1)</script>')]))
        self.assertNotIn('alert(', html)


# ───────────────────────── 網址白名單 ─────────────────────────
class UrlWhitelistTest(_Base):

    def test_non_whitelisted_images_rejected(self):
        bad = card(og_image='https://evil.example.com/a.jpg',
                   gallery=['https://evil.example.com/b.jpg', 'http://img1.591.com.tw/http.jpg', 'javascript:alert(1)',
                            'https://img1.591.com.tw/ok.jpg"onerror="x', '//evil.example.com/c.jpg',
                            'https://user:pw@img1.591.com.tw/d.jpg', 'https://img1.591.com.tw.evil.com/e.jpg', IMGYC])
        sc, _, _ = I._sanitize_card(bad)
        self.assertEqual(sc['gallery'], [IMGYC])
        self.assertEqual(sc['og_image'], IMGYC)
        html, _ = self.publish_html(self.body(OWNER, [bad, card2()]))
        for frag in ('evil.example.com', 'http://img1', 'javascript:', 'onerror', 'user:pw', '591.com.tw.evil'):
            self.assertNotIn(frag, html)

    def test_links_and_vr_whitelist(self):
        c = card(links=[{'n': 1, 'url': 'https://www.sinyi.com.tw/buy/house/123'}, {'n': 2, 'url': S591_URL},
                        {'n': 3, 'url': 'http://buy.yungching.com.tw/house/1'}, {'n': 4, 'url': S591_URL}],
                 vr_url='https://evil.example.com/vr')
        sc, _, _ = I._sanitize_card(c)
        self.assertEqual(sc['links'], [{'n': 1, 'url': S591_URL}])            # 非白名單／非 https 拿掉、去重、重編
        self.assertIsNone(sc['vr_url'])
        sc2, _, _ = I._sanitize_card(card(vr_url='https://livetour.istaging.com/abc'))
        self.assertEqual(sc2['vr_url'], 'https://livetour.istaging.com/abc')

    def test_591_images_get_referrer_policy(self):
        html, _ = self.publish_html(self.body(OWNER, [card(), card2()]))
        self.assertIn('src="%s" loading="lazy" decoding="async" referrerpolicy="strict-origin-when-cross-origin"'
                      % IMG591, html)
        self.assertIn('referrerpolicy="no-referrer" alt=""', html)
        self.assertNotRegex(html, r'<img(?![^>]*referrerpolicy)[^>]*src="https')

    def test_single_photos_whitelist_and_no_geo(self):
        p = single(photos=['https://evil.example.com/x.jpg', 'https://yccdn.yungching.com.tw/v1/image/?key=AAA'],
                   geo={'latitude': 24.181234, 'longitude': 120.654321},
                   staticMap='https://maps.googleapis.com/maps/api/staticmap?center=24.181234,120.654321')
        html, _ = self.publish_html(self.body(OWNER, [p]))
        self.assertNotIn('evil.example.com', html)
        self.assertNotIn('24.181234', html)
        self.assertNotIn('staticmap', html.lower())
        self.assertIn('q=' + quote('台中市北屯區崇德路二段', safe=''), html)   # 地圖只用路名文字查詢


# ───────────────────────── X-Share-Key ─────────────────────────
class ShareKeyTest(_Base):

    def test_enforce_0_logs_and_allows(self):
        with mock.patch.dict(os.environ, {'SHARE_PUBLISH_KEY': 'k3y', 'SHARE_KEY_ENFORCE': '0'}):
            code, j = self.post(self.body(OWNER, [card()], dry_run=True))
            self.assertEqual(code, 200, j)
            code, j = self.post(self.body(OWNER, [card()], dry_run=True), {'X-Share-Key': 'wrong'})
            self.assertEqual(code, 200, j)
        code, j = self.post(self.body(OWNER, [card()], dry_run=True))     # 沒設環境變數＝預設 0
        self.assertEqual(code, 200, j)

    def test_enforce_1(self):
        with mock.patch.dict(os.environ, {'SHARE_PUBLISH_KEY': 'k3y', 'SHARE_KEY_ENFORCE': '1'}):
            for hdr in ({}, {'X-Share-Key': 'wrong'}, {'X-Share-Key': 'k3y '}):
                code, j = self.post(self.body(OWNER, [card()]), hdr)
                self.assertEqual(code, 401, (hdr, j))
                self.assertIn('error', j)
            code, j = self.post(self.body(OWNER, [single()], dry_run=True), {'X-Share-Key': 'k3y'})
            self.assertEqual(code, 200, j)
            code, j = self.post(dict(self.body(OWNER, [card(), card2()])), {'X-Share-Key': 'k3y'})
            self.assertEqual(code, 200, j)
            self.assertTrue(j['share_id'].startswith('qs'))
        self.assertEqual(len(self.pushed), 1)

    def test_enforce_1_without_server_key_blocks(self):
        with mock.patch.dict(os.environ, {'SHARE_KEY_ENFORCE': '1'}):
            os.environ.pop('SHARE_PUBLISH_KEY', None)
            code, j = self.post(self.body(OWNER, [card()], dry_run=True), {'X-Share-Key': 'anything'})
            self.assertEqual(code, 401, j)

    def test_share_id_prefixes(self):
        for _ in range(3000):
            self.assertFalse(I.gen_share_id().lower().startswith('qs'))
        for _ in range(200):
            sid = I.gen_props_share_id()
            self.assertRegex(sid, r'^qs[A-Za-z0-9]{8}$')

    def test_props_update_keeps_share_id(self):
        key = {'X-Share-Key': 'k3y'}
        with mock.patch.dict(os.environ, {'SHARE_PUBLISH_KEY': 'k3y', 'SHARE_KEY_ENFORCE': '0'}):
            html, j = self.publish_html(self.body(OWNER, [card()], share_id='qsKeep1234'), key)
            self.assertEqual(j['share_id'], 'qsKeep1234')
            self.assertTrue(self.pushed[-1]['update'])
            code, j = self.post(self.body(OWNER, [card()], share_id='../evil'), key)
            self.assertEqual(code, 400, j)

    def test_overwrite_needs_key_even_when_enforce_0(self):
        """2026-10-03 紅隊：ENFORCE=0 期間也不准匿名覆蓋既有頁（props 帶 share_id、urls_text 覆蓋 qs*）。"""
        n0 = len(self.pushed)
        with mock.patch.dict(os.environ, {'SHARE_PUBLISH_KEY': 'k3y', 'SHARE_KEY_ENFORCE': '0'}):
            for hdr in ({}, {'X-Share-Key': 'wrong'}):
                code, j = self.post(self.body(OWNER, [card()], share_id='qsKeep1234'), hdr)
                self.assertEqual(code, 401, j)
            code, j = self.post(self.body(OWNER, [card()]))                   # 新建頁照舊（ENFORCE=0 放行）
            self.assertEqual(code, 200, j)
        os.environ.pop('SHARE_PUBLISH_KEY', None)                              # 伺服器沒設金鑰 → 覆蓋一律擋
        code, j = self.post(self.body(OWNER, [card()], share_id='qsKeep1234'), {'X-Share-Key': 'x'})
        self.assertEqual(code, 401, j)
        self.assertEqual(len(self.pushed), n0 + 1)


# ───────────────────────── urls_text 舊路徑（景泰自己的表單） ─────────────────────────
def _fake_ref(ref):
    sid = ref['id'] if isinstance(ref, dict) else ref
    return {
        'source': 'ycut', 'slug': sid, 'detail_url': 'https://x.ychouse.tw/' + sid,
        'community_display': '總太聚作', 'price': 1980 if sid == 'abc123' else 1350, 'floor': 3, 'floor_total': 15,
        'area': 46.98, 'main_area': 25.1, 'age': 18.0, 'has_parking': True, 'parking': '坡道平面',
        'parking_area': '含於主建', 'building_type': '大樓', 'address': '台中市北屯區崇德路二段100巷5號',
        'layout': '3房2廳2衛', 'og_image': 'https://yccdn.yungching.com.tw/v1/image/?key=' + sid,
        'og_title': '專任｜總太聚作 景觀三房 最便宜 洽王小姐0912-345-678',
        'og_description': '洽 0912-345-678',
        'intro': '邊間三房採光好\n洽 0912-345-678 王小姐\n信義房屋 歡迎來電\n近捷運藍線預計通車，未來增值可期',
    }


class UrlsTextTest(_Base):
    TEXT = 'https://x.ychouse.tw/abc123 https://x.ychouse.tw/def456'

    def setUp(self):
        super().setUp()
        pt = mock.patch.object(I, '_fetch_one_ref', side_effect=_fake_ref)
        pt.start()
        self.addCleanup(pt.stop)

    def test_urls_text_still_works_and_is_scrubbed(self):
        html, j = self.publish_html({'urls_text': self.TEXT, 'name': '王先生', 'need': '北屯三房'})
        self.assertEqual(j['count'], 2)
        self.assertEqual(j['mode'], 'created')
        self.assertEqual(j['render'], 'cards-v2')
        self.assertFalse(j['share_id'].lower().startswith('qs'))
        self.assertTrue(j['url'].endswith('/%s/' % j['share_id']))
        for bad in ('0912-345-678', '0912345678', '王小姐', '信義房屋', '藍線', '專任', '最便宜', '100巷', '增值'):
            self.assertNotIn(bad, html, bad)
        self.assertIn('總太聚作 3樓 3房', html)                              # og_title 換結構化名稱
        self.assertIn('邊間三房採光好', html)
        res = self.audit(html, OWNER, ())
        self.assertTrue(res.ok, [(h.rule, h.where) for h in res.hits])
        self.assertEqual(len(self.snaps), 1)

    def test_urls_text_dry_run(self):
        code, j = self.post({'urls_text': self.TEXT, 'name': '王先生', 'dry_run': True})
        self.assertEqual(code, 200, j)
        self.assertEqual(j['leak'], [])
        self.assertEqual(self.pushed, [])

    def test_urls_text_overwrite_qs_needs_key(self):
        with mock.patch.dict(os.environ, {'SHARE_PUBLISH_KEY': 'k3y', 'SHARE_KEY_ENFORCE': '0'}):
            code, j = self.post({'urls_text': self.TEXT, 'share_id': 'qsAbcdef12'})     # ENFORCE=0 也要金鑰
            self.assertEqual(code, 401, j)
        with mock.patch.dict(os.environ, {'SHARE_PUBLISH_KEY': 'k3y', 'SHARE_KEY_ENFORCE': '1'}):
            code, j = self.post({'urls_text': self.TEXT, 'share_id': 'qsAbcdef12'})
            self.assertEqual(code, 401, j)
            code, j = self.post({'urls_text': self.TEXT, 'share_id': 'qsAbcdef12'}, {'X-Share-Key': 'k3y'})
            self.assertEqual(code, 200, j)
            self.assertEqual(j['share_id'], 'qsAbcdef12')
            code, j = self.post({'urls_text': self.TEXT, 'share_id': 'Abcdef12'})      # 一般頁「回去修改」不需金鑰
            self.assertEqual(code, 200, j)
            self.assertEqual(j['mode'], 'updated')

    def test_urls_text_no_urls_400(self):
        code, j = self.post({'urls_text': '沒有網址'})
        self.assertEqual(code, 400, j)


# ───────────────────────── 紅隊實測句端到端（_sanitize_card → gen_html → _audit_page） ─────────────────────────
class RedTeamE2ETest(_Base):
    """2026-10-03 紅隊：這些內文原本會原樣上頁且稽核通過；現在要嘛洗掉、要嘛 422 不推。"""
    CASES = (('屋況佳\nＬｉｎｅ ａｂ１２３', ('ａｂ１２３', 'ab123')),
             ('本案由信義 房屋獨家銷售', ('信義',)),
             ('捷運明年通車，步行可達', ('明年通車',)),
             ('請洽\n0912\n345\n678', ('0912',)),
             ('屋況佳採光好\n聯絡人：王小明', ('王小明',)),
             ('屋主王大明自住保養好', ('王大明',)),
             ('邊間三房\n採光好\n生活機能佳\n王小明\n0912-345-678', ('王小明', '0912')),
             ('崇德路一段廿巷內', ('廿巷',)))

    def test_cases_never_reach_page(self):
        for intro, bad in self.CASES:
            with self.subTest(intro=intro):
                n0 = len(self.pushed)
                code, j = self.post(self.body(OWNER, [card(intro=intro)]))
                if code == 200:
                    html = self.pushed[-1]['content']
                    for b in bad:
                        self.assertNotIn(b, html, b)
                else:
                    self.assertEqual(code, 422, j)
                    self.assertEqual(len(self.pushed), n0)


# ───────────────────────── /api/regen、/api/recommend（2026-10-03 紅隊） ─────────────────────────
class RegenRecommendTest(_Base):

    def test_regen_needs_key_and_refuses_all(self):
        with mock.patch.object(I, 'notion_query_all', side_effect=AssertionError('不該查 Notion')):
            r = self.client.get('/api/regen?share_id=Abcdef12')
            self.assertEqual(r.status_code, 401)
            with mock.patch.dict(os.environ, {'SHARE_PUBLISH_KEY': 'k3y'}):
                r = self.client.get('/api/regen?share_id=all', headers={'X-Share-Key': 'k3y'})
                self.assertEqual(r.status_code, 400)
                self.assertIn('暫停', r.get_json()['error'])
        self.assertEqual(self.pushed, [])

    def test_regen_only_owner_pages(self):
        rows = [{'properties': {}}]

        def field(r, name, kind):
            return {'share_id': 'Abcdef12', '客戶': '王先生', '事件類型': 'snapshot',
                    '點擊物件': 'https://x.ychouse.tw/abc123'}.get(name)
        mate_page = '<html><a href="tel:0911222333">0911-222-333</a> LINE：wangdm88</html>'
        with mock.patch.dict(os.environ, {'SHARE_PUBLISH_KEY': 'k3y'}), \
                mock.patch.object(I, 'notion_query_all', return_value=rows), \
                mock.patch.object(I, '_row_field', side_effect=field), \
                mock.patch.object(I, 'github_get_text', return_value=mate_page), \
                mock.patch.object(I, 'fetch_full_batch', side_effect=AssertionError('不該抓物件')):
            j = self.client.get('/api/regen?share_id=Abcdef12', headers={'X-Share-Key': 'k3y'}).get_json()
        self.assertEqual(j['succeeded'], 0)
        self.assertIn('不是景泰', j['results'][0]['error'])
        self.assertEqual(self.pushed, [])

    def test_recommend_disabled(self):
        r = self.client.post('/api/recommend', json={'anchor_url': 'https://x.ychouse.tw/abc'})
        self.assertEqual(r.status_code, 410)
        self.assertEqual(r.get_json()['code'], 'recommend_disabled')
        self.assertEqual(self.pushed, [])


# ───────────────────────── 租屋分支 ─────────────────────────
class RentTest(_Base):

    def rent_card(self, **kw):
        c = card(mode='rent', og_title='北屯區崇德路二段 整層出租 3房', community_display=None, price=18000,
                 price_range=None, links=[{'n': 1, 'url': 'https://rent.591.com.tw/12345678'}],
                 rent={'rent': 18000, 'mgmt': 1500, 'mgmt_incl': False, 'deposit': 2, 'parking_fee': None,
                       'min_lease': 12, 'tags': ['可寵物', '可開伙'], 'evil': 'x'}, slug='qsRent01')
        c.update(kw)
        return c

    def test_rent_cards(self):
        cards = [self.rent_card(), self.rent_card(price=26000, rent={'rent': 26000, 'mgmt_incl': True},
                                                  slug='qsRent02', og_title='西屯區市政路 整層出租 2房',
                                                  address='西屯區市政路', map_q='台中市西屯區市政路')]
        code, j = self.post(self.body(OWNER, cards, dry_run=True))
        self.assertEqual(code, 200, j)
        self.assertEqual(j['leak'], [])
        self.assertEqual(j['mode'], 'rent')
        html, _ = self.publish_html(self.body(MATE, cards))
        self.assertIn('18,000</span><span class="card-price-unit">元/月', html)
        self.assertIn('另計 1,500 元/月', html)
        self.assertIn('含在租金內', html)
        self.assertIn('2 個月', html)
        self.assertIn('12 個月', html)
        self.assertIn('可寵物', html)
        self.assertIn('data-price-tier="r-15-20k"', html)
        self.assertIn('data-price-tier="r-20-30k"', html)
        self.assertIn('精選租屋 · ', html)
        self.assertNotIn('萬 含車位', html)
        self.assertNotIn('單坪 <strong>', html)
        self.assertNotIn('evil', html)
        res = self.audit(html, MATE, {'https://rent.591.com.tw/12345678'})
        self.assertTrue(res.ok, [(h.rule, h.where) for h in res.hits])

    def test_mixed_modes_400(self):
        code, j = self.post(self.body(OWNER, [card(), self.rent_card()]))
        self.assertEqual(code, 400, j)

    def test_single_page_rent_branch(self):
        h = SP.render({'title': '北屯區崇德路二段 整層出租 3房', 'address': '台中市北屯區崇德路二段', 'price': 18000,
                       'mode': 'rent', 'rent': {'rent': 18000, 'mgmt_incl': True, 'deposit': 2, 'tags': ['可寵物']},
                       'map_q': '台中市北屯區崇德路二段'}, I.build_contact(OWNER), 'KEY')
        self.assertIn('月租', h)
        self.assertIn('18,000', h)
        self.assertIn('元/月', h)
        self.assertIn('含在租金內', h)
        self.assertIn('2 個月', h)
        self.assertIn('可寵物', h)
        self.assertNotIn('萬 / 坪', h)


# ───────────────────────── 格式、白名單外鍵、稽核擋下 ─────────────────────────
class FormatAndBlockTest(_Base):

    def test_bad_formats_400(self):
        cases = ([], [card(), single()], [single(), single()], [card()] * 21, 'x', [card(mode='buy')],
                 [dict(single(), _card=False)])
        for props in cases:
            code, j = self.post(self.body(OWNER, props))
            self.assertEqual(code, 400, (str(props)[:60], j))
        self.assertEqual(self.pushed, [])

    def test_unknown_keys_dropped(self):
        c = card(linkman='王小明', mobile='0912345678', contactStore='某某店', lat=24.123456)
        sc, dropped, _ = I._sanitize_card(c)
        self.assertEqual(set(dropped), {'linkman', 'mobile', 'contactStore', 'lat'})
        self.assertFalse(set(sc) - set(L.CARD_KEYS) - {'_card'})
        html, _ = self.publish_html(self.body(OWNER, [c, card2()]))
        for bad in ('王小明', '0912345678', '某某店', '24.123456'):
            self.assertNotIn(bad, html)

    def test_card_intro_rescrubbed(self):
        c = card(intro='邊間三房採光好\n洽 0912-345-678 王小姐 加LINE: abc123\n信義房屋 專任')
        html, _ = self.publish_html(self.body(OWNER, [c, card2()]))
        for bad in ('0912', '王小姐', 'abc123', '信義房屋', '專任'):
            self.assertNotIn(bad, html)
        self.assertIn('邊間三房採光好', html)

    def test_audit_hit_returns_422_and_does_not_push(self):
        """payload 的需求欄（不經洗白）帶電話 → audit_html 命中 → 422、不推。"""
        b = self.body(OWNER, [card(), card2()], need='北屯三房 請打0912345678')
        code, j = self.post(dict(b, dry_run=False))
        self.assertEqual(code, 422, j)
        self.assertEqual(j['error'], 'audit')
        self.assertTrue(j['hits'])
        self.assertEqual(set(j['hits'][0]), {'rule', 'where'})
        self.assertEqual(self.pushed, [])
        code, j = self.post(dict(b, dry_run=True))
        self.assertEqual(code, 200, j)
        self.assertIn('A1', {h['rule'] for h in j['leak']})

    def test_single_caseName_equals_title(self):
        p, dropped, _ = I._sanitize_single(single(geo={'latitude': 1}, staticMap='https://x', des='原文'))
        self.assertEqual(p['caseName'], p['title'])
        self.assertIn('geo', dropped)
        self.assertIn('staticMap', dropped)
        self.assertNotIn('des', p)

    def test_old_console_single_props_scrubbed(self):
        """舊版查詢台（只送 caseName／des 原文、geo、staticMap）也照樣洗乾淨、可上線。"""
        old = {'caseName': '專任｜總太聚作 景觀三房 洽王小姐', 'des': '邊間三房\n洽 0912-345-678 王小姐 信義房屋',
               'address': '台中市北屯區崇德路二段100巷5號', 'price': 1980, 'pin': 46.98,
               'photos': ['https://yccdn.yungching.com.tw/v1/image/?key=AAA'],
               'geo': {'latitude': 24.181234, 'longitude': 120.654321}, 'staticMap': 'https://x/staticmap'}
        html, j = self.publish_html(self.body(OWNER, [old]))
        for bad in ('王小姐', '0912', '信義房屋', '專任', '100巷', '24.181234', 'staticmap'):
            self.assertNotIn(bad, html)
        self.assertIn('<h1>台中市北屯區崇德路二段</h1>', html)
        self.assertIn('邊間三房', html)


# ───────────────────────── 其他 ─────────────────────────
class MiscTest(_Base):

    def test_health_build(self):
        r = self.client.get('/api/health')
        j = r.get_json()
        self.assertEqual(j['build'], I.BUILD)
        self.assertEqual(j['render'], 'cards-v2')
        self.assertEqual(j['mp_version'], L.MP_VERSION)

    def test_competitors_no_hardcoded_title(self):
        with io.open(os.path.join(_sguard.API, '_lib', 'competitors.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('七期精選商辦', src)

    def test_template_has_no_hardcoded_teddy_text(self):
        """寫死的「景泰」字樣（主題、落款、洽詢、提醒、愛心通知）都改成登入者。"""
        with io.open(os.path.join(_sguard.API, 'index.py'), encoding='utf-8') as f:
            src = f.read()
        for old in ('"景泰精選"', 'or "陳景泰"', '洽景泰', '📝 景泰提醒', '已通知景泰'):
            self.assertNotIn(old, src)

    def test_shared_files_match_h(self):
        h = os.path.join(os.path.expanduser('~'), 'Dropbox', '泰迪資料夾', 'yc_front_home')
        if not os.path.isdir(h):
            self.skipTest('本機沒有查詢台正本')
        for fn in ('mp_lexicon.py', 'mp_scrub.py', 'mp_audit.py'):
            with open(os.path.join(h, fn), 'rb') as f:
                a = hashlib.sha256(f.read()).hexdigest()
            with open(os.path.join(_sguard.API, '_lib', fn), 'rb') as f:
                b = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(a, b, fn + ' 與查詢台正本不一致（請重新複製）')


if __name__ == '__main__':
    unittest.main()
