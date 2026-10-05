# -*- coding: utf-8 -*-
"""591 租屋（rent.591.com.tw）離線測試（2026-10-05）。

涵蓋：網址辨識（含 App 分享短網址看 salt 不看 s）、__NUXT__ 解析（假頁面）、屋況介紹子句級過濾
（租屋限制／跳單暗示／591 站內聯絡／同業口號／房東自稱／刊登者名字）、刊登者黑名單、屋齡月數、
依物件編號去重、租售混貼 400、租屋頁整頁稽核 0 命中、縣轄市行政區。全程不連外網。
"""
try:
    from . import _sguard
except ImportError:
    import _sguard

import json
import os
import unittest
from unittest import mock

I = _sguard.load_index()
from _lib import competitors as C                          # noqa: E402

OWNER = {'agent_name': '陳景泰', 'agent_license': '114年登字第488296號', 'phone': '0920-118-756', 'line': 'sky811117'}


def nuxt_page(**kw):
    d = {
        'title': '中山路全聯對面家樂福童醫院', 'status': 'open', 'deposit': '二個月', 'kind': 2,
        'price': '7,000', 'priceUnit': '元/月',
        'breadcrumb': [{'name': '台中市'}, {'name': '沙鹿區'}],
        'tags': [{'value': '拎包入住'}, {'value': '屋主直租'}, {'value': '影片賞屋'}, {'value': '近捷運'}],
        'info': [{'name': '類型', 'value': '獨立套房'}, {'name': '使用坪數', 'value': '8坪'},
                 {'name': '樓層', 'value': '3F/7F'}, {'name': '型態', 'value': '電梯大樓'}],
        'costData': {'data': [{'name': '租金', 'value': '7,000元/月'}, {'name': '押金', 'value': '二個月'},
                              {'name': '租金含', 'value': '管理費、水費、網路'}]},
        'service': {'facility': [{'name': '冰箱', 'active': 1}, {'name': '沙發', 'active': 0}],
                    'descData': [{'label': '最短租期', 'value': '一年'}, {'label': '性別', 'value': '此房屋限女生租住'},
                                 {'label': '身份要求', 'value': '學生、上班族'}, {'label': '開伙', 'value': '不可開伙'}]},
        'infoData': {'data': [{'name': '屋齡', 'value': '2個月'}]},
        'positionRound': {'communityName': '全聯對面', 'communityId': 0, 'address': '沙鹿區中山路二段388巷36號'},
        'remark': {'content': '採光好，近靜宜大學<br />限租女生，學生或上班族<br />本物件免付服務費並協助申請租金補貼'
                              '<br />我是房東阿明<br />屋主親自出租，不收仲介費<br />歡迎來電 0929-026-060'},
        'linkInfo': {'name': '仲介: 檳城妹仔：張嘉恩', 'imName': '張女士', 'mobile': '0929-026-060',
                     'roleTxt': '經紀業: 信富地產有限公司'},
    }
    d.update(kw)
    album = [{'type': 3, 'isCover': 0, 'photo': 'https://img2.591.com.tw/house/a/2.jpg!1000x.water2.jpg'},
             {'type': 3, 'isCover': 1, 'photo': 'https://img1.591.com.tw/house/a/1.jpg!1000x.water2.jpg'},
             {'type': 6, 'isCover': 0, 'photo': 'https://img1.591.com.tw/house/a/plan.jpg!1000x.water2.jpg'}]
    nuxt = {'data': {'$abc': {'status': 1, 'data': d}}, 'pinia': {'album': {'albumData': {'items': album}}}}
    return '<html><script>window.__NUXT__=%s</script></html>' % json.dumps(nuxt, ensure_ascii=False)


class TestRentUrls(unittest.TestCase):

    def test_scan_url_forms(self):
        txt = ('a https://rent.591.com.tw/21971367 b https://rent.591.com.tw/rent-detail-21823314.html '
               'c https://m.591.com.tw/v2/rent/21969871 d https://rent.591.com.tw/home/21914849 '
               'e https://www.591.com.tw/1R?salt=Sr0OQ&s= 列表 https://rent.591.com.tw/list?region=8')
        urls = [u for _p, u, _b in sorted(C.scan_external(txt))]
        self.assertEqual(len(urls), 5)
        self.assertTrue(urls[-1].startswith('https://www.591.com.tw/1R?salt=Sr0OQ'))

    def test_share_links_glued_together_split(self):
        txt = 'https://www.591.com.tw/1R?salt=Sr0OQ&s=https://www.591.com.tw/1R?salt=Zz9Aa&s=21974048'
        urls = [u for _p, u, _b in sorted(C.scan_external(txt))]
        self.assertEqual(urls, ['https://www.591.com.tw/1R?salt=Sr0OQ&s=',
                                'https://www.591.com.tw/1R?salt=Zz9Aa&s=21974048'])

    def test_id_ignores_s_param(self):
        self.assertEqual(C._rent591_id('https://rent.591.com.tw/21971367?s=18981357'), '21971367')
        self.assertEqual(C._rent591_id('https://www.591.com.tw/1R?salt=Sr0OQ&s=18981357'), '')

    def test_share_link_uses_redirect_not_s(self):
        with mock.patch.object(C, '_rent591_resolve_share', return_value='21971367') as rs, \
                mock.patch.object(C, '_fetch', return_value=nuxt_page()) as f:
            d = C._p_rent591('https://www.591.com.tw/1R?salt=Sr0OQ&s=18981357')
        rs.assert_called_once()
        self.assertEqual(f.call_args[0][0], 'https://rent.591.com.tw/21971367')
        self.assertEqual(d['rent_id'], '21971367')


class TestRentParse(unittest.TestCase):

    def parse(self, **kw):
        with mock.patch.object(C, '_fetch', return_value=nuxt_page(**kw)):
            return C.fetch_external('https://rent.591.com.tw/21971367')

    def test_fields(self):
        d = self.parse()
        self.assertEqual(d['mode'], 'rent')
        self.assertEqual(d['price'], 7000)
        self.assertEqual((d['floor'], d['floor_total']), (3, 7))
        self.assertEqual(d['address'], '台中市沙鹿區中山路二段')             # 巷弄號砍掉
        self.assertEqual(d['rent_comm'], '')                             # communityId=0 的自由填寫不算社區
        self.assertEqual(d['rent']['deposit'], 2)
        self.assertTrue(d['rent']['mgmt_incl'])
        self.assertEqual(d['rent']['equip'], ['冰箱'])
        self.assertNotIn('屋主直租', d['rent']['tags'])
        self.assertNotIn('近捷運', d['rent']['tags'])
        self.assertIn('不可開伙', d['rent']['tags'])
        self.assertEqual(d['age'], 0.2)                                   # 「2個月」不是 2 年
        self.assertEqual(d['og_image'], 'https://img1.591.com.tw/house/a/1.jpg!1000x.jpg')   # 封面排第一
        self.assertEqual(len(d['gallery']), 2)                            # type=6 不收
        self.assertIn('檳城妹仔', d['scrub_entities'])

    def test_intro_drops_limits_bypass_names_and_contact(self):
        intro = self.parse()['intro']
        self.assertIn('採光好', intro)
        self.assertIn('學生或上班族', intro)
        for bad in ('限租女生', '免付服務費', '房東阿明', '親自出租', '仲介費', '0929', '歡迎來電'):
            self.assertNotIn(bad, intro)

    def test_nothing_from_linkinfo_or_restrictions_in_output(self):
        blob = json.dumps({k: v for k, v in self.parse().items() if k != 'scrub_entities'}, ensure_ascii=False)
        for bad in ('檳城妹仔', '張嘉恩', '嘉恩', '信富地產', '0929', '限女生', '身份要求', '學生、上班族'):
            self.assertNotIn(bad, blob)

    def test_closed_listing_skipped(self):
        self.assertIn('error', self.parse(status='close'))

    def test_registered_community_kept(self):
        d = self.parse(positionRound={'communityName': '鄉林皇居', 'communityId': 22471, 'address': '西屯區臺灣大道三段'})
        self.assertEqual(d['rent_comm'], '鄉林皇居')

    def test_age_variants(self):
        for raw, want in (('15年', 15.0), ('1年3個月', 1.2), ('-', 0.0)):
            d = self.parse(infoData={'data': [{'name': '屋齡', 'value': raw}]})
            self.assertEqual(d['age'], want, raw)


def biz_page(**kw):
    d = {
        'basicData': {'id': '21437774', 'kindStr': '店面', 'region': '台中市', 'section': '北區'},
        'baseInfo': {
            'title': '(專)梅亭東街黃金店面', 'status': 'open', 'price': {'value': '35,000', 'unit': '元/月'},
            'deposit': '押金二個月',
            'mainInfo': [{'label': '使用坪數', 'value': '28.56'}, {'label': '樓層', 'value': '1F/5F'}],
            'labelInfo': {'left': [{'label': '最短租期', 'value': '一年'}, {'label': '權狀坪數', 'value': '30坪'}],
                          'right': [{'label': '租金', 'value': '不含水電費'}, {'label': '登記', 'value': '可工商登記'},
                                    {'label': '管理費', 'value': '1,200元/月'}],
                          'bottom': [{'label': '臨路路寬', 'value': '梅亭東街(12米)'}, {'label': '使用分區', 'value': '暫未填寫'}]},
            'tags': [{'name': '可登記'}, {'name': '免租1個月'}]},
        'facilityInfo': {'facility': [{'name': '獨立出入口', 'active': 1}, {'name': '電梯', 'active': 0}]},
        'linkInfo': {'linkman': '林先生', 'imName': '林靖崴', 'econName': '館前不動產有限公司', 'mobile': '0922-972-222',
                     'companyname': '', 'subcompanyname': ''},
        'remark': '<p>三角窗店面，人潮多，２４小時川流不息</p><p>林靖崴為您服務 0922-972-222</p>',
        'mapInfo': {'address': {'desc': '北區梅亭東街25號'}},
    }
    d.update(kw)
    album = [{'type': 3, 'isCover': 1, 'photo': 'https://img1.591.com.tw/house/b/1.jpg!1000x.water2.jpg'}]
    nuxt = {'pinia': {'business-rent-detail': {'detailInfo': d}, 'album': {'albumData': {'items': album}}}}
    return '<html><script>window.__NUXT__=%s</script></html>' % json.dumps(nuxt, ensure_ascii=False)


class TestBusinessRent(unittest.TestCase):
    """591 商用租屋 business.591.com.tw/rent/{id}（景泰 2026-10-05 貼店面出租被回「找不到物件網址」）。"""

    def parse(self, **kw):
        with mock.patch.object(C, '_fetch', return_value=biz_page(**kw)):
            return C.fetch_external('https://business.591.com.tw/rent/21437774')

    def test_url_recognised(self):
        urls = [u for _p, u, _b in C.scan_external('看 https://business.591.com.tw/rent/21728726 這間')]
        self.assertEqual(urls, ['https://business.591.com.tw/rent/21728726'])

    def test_fields(self):
        d = self.parse()
        self.assertEqual(d['mode'], 'rent')
        self.assertEqual(d['price'], 35000)
        self.assertEqual(d['address'], '台中市北區梅亭東街')
        self.assertEqual((d['floor'], d['floor_total']), (1, 5))
        self.assertEqual(d['rent_cat'], 'shop')
        self.assertEqual(d['rent_id'], 'b21437774')
        self.assertEqual(d['parking'], '')
        r = d['rent']
        self.assertEqual((r['deposit'], r['mgmt'], r['min_lease']), (2, 1200, '一年'))
        self.assertIn('租金不含水電費', r['tags'])
        self.assertEqual(r['equip'], ['獨立出入口'])
        self.assertEqual(r['extra'], [['權狀坪數', '30坪'], ['臨路路寬', '梅亭東街(12米)']])
        self.assertIn('２４小時川流不息', d['intro'])              # 「24小時」不是聯絡資訊時不截斷
        for bad in ('林靖崴', '0922', '館前'):
            self.assertNotIn(bad, d['intro'])
        self.assertIn('館前不動產', d['scrub_entities'])

    def test_page(self):
        with mock.patch.object(C, '_fetch', return_value=biz_page()):
            props = I.fetch_full_batch(I.extract_refs('https://business.591.com.tw/rent/21437774'))
        ents = set()
        found = I._scrub_fetched(props, ents)
        html = I.gen_html({'name': '', 'need': '', 'share_id': 'TB', 'contact': I.DEFAULT_CONTACT,
                           'mode': I._page_mode(props)}, props)
        self.assertTrue(I._audit_page(html, I.DEFAULT_CONTACT, (), [], found,
                                      I._audit_entities(ents, props, I.DEFAULT_CONTACT)).ok)
        self.assertIn('35,000', html)
        self.assertIn('元/月', html)
        self.assertIn('梅亭東街(12米)', html)
        self.assertIn('北區梅亭東街 店面', html)
        self.assertNotIn('25號', html)


class TestBusinessRentRound3(unittest.TestCase):
    """第三輪審查（商用租屋）抓到的漏網：房仲署名區、商辦房號、分公司地名、土地面積、平台標籤。"""

    def test_agent_signature_after_divider_cut(self):
        html = ('店面方正採光好<br>可做餐飲<br>------------------<br>我是阿詠，陪你一起找到「理想家」！'
                '<br>現在就點擊「發送訊息」，我們約個時間喝杯咖啡聊聊吧！<br>永慶14期山西榮德店 欣岳不動產')
        self.assertEqual(C._clean_rent_remark(html), '店面方正採光好\n可做餐飲')

    def test_nickname_entities(self):
        ents = C._rent591_entities({'name': '仲介: 王新詠', 'roleTxt': '華府房屋仲介股份有限公司'})
        for e in ('阿詠', '小詠', '華府房屋'):
            self.assertIn(e, ents)
        self.assertEqual(C._rent_clause_drop('我是阿詠，服務熱忱', ents), '服務熱忱')

    def test_unit_codes_removed(self):
        out = C._clean_rent_remark('【空間編號：12-8@9】一間有溫度的獨立空間<br>📍物件編號：12-8@9<br>【474-11@3】永春捷運旁')
        self.assertEqual(out, '一間有溫度的獨立空間')

    def test_branch_place_name_not_blacklisted(self):
        ents = C._rent591_entities({'certificateTxt': '億錢朝富不動產 / 分公司：七期市政'})
        self.assertNotIn('七期市政', ents)
        self.assertEqual(C._rent_clause_drop('位於台中七期市政北二路', ents), '位於台中七期市政北二路')

    def test_short_lane_numbers_do_not_eat_lines(self):
        self.assertEqual(C._clean_rent_remark('12坪大空間<br>近9號公園', addr_nums=['12', '9']), '12坪大空間\n近9號公園')

    def test_land_area_and_tags(self):
        page = biz_page(
            basicData={'id': '21851246', 'kindStr': '土地', 'region': '台中市'},
            baseInfo={'title': '大雅雙面臨路590坪', 'status': 'open', 'price': {'value': '94,000', 'unit': '元/月'},
                      'deposit': '押金二個月',
                      'mainInfo': [{'label': '土地面積', 'value': '590'}, {'label': '使用分區', 'value': '農業區'}],
                      'labelInfo': {'bottom': [{'label': '最短租期', 'value': '一年'}]},
                      'tags': [{'name': '7日上新'}, {'name': '有水電'}]},
            mapInfo={'address': {'desc': '大雅區大林路號'}})
        with mock.patch.object(C, '_fetch', return_value=page):
            d = C.fetch_external('https://business.591.com.tw/rent/21851246')
        self.assertEqual(d['area'], 590.0)
        self.assertFalse(d['lite'])
        self.assertEqual(d['address'], '台中市大雅區大林路')
        self.assertEqual(d['rent_cat'], 'land')
        self.assertNotIn('7日上新', d['rent']['tags'])
        self.assertIn(['使用分區', '農業區'], d['rent']['extra'])
        props = [dict(d)]
        I._scrub_fetched(props)
        self.assertIn('590坪', props[0]['og_title'])


LEGACY_PAGE = '''<html><head>
<meta property="og:image" content="https://img2.591.com.tw/house/2026/08/25/cover1.jpg!1000x.water2.jpg" />
</head><body>
<a href="//business.591.com.tw/?type=1&kind=12&regionid=8&section=106">住辦</a> &gt;
<span class="addr">台中市太<ide></ide>平區立功路190巷</span>
<div class="imgList"><textarea class="datalazyload"><ol>
<li><img src="https://img1.591.com.tw/house/2026/08/25/cover1.jpg!94x68.jpg"></li>
<li><img src="https://img2.591.com.tw/house/2026/08/25/p2.jpeg!400x300.jpeg"></li></ol></textarea></div>
<ul class="clearfix labelList labelList-1">
<li class="clearfix"><div class="one">押金</div><div class="two"><span>：</span><em title="x">二<prk></prk>個<prk></prk>月</em></div>
<li class="clearfix"><div class="one"><f></f>車 位</div><div class="two"><span>：</span><em title="無">無</em></div>
<li class="clearfix"><div class="one">性別要求</div><div class="two"><span>：</span><em title="x">限女生</em></div>
<li class="clearfix"><div class="one">管理費</div><div class="two"><span>：</span><em title="x">6000元/月</em></div>
<li class="clearfix"><div class="one">最短租期</div><div class="two"><span>：</span><em title="x">3<sovxjx></sovxjx>年</em></div>
<li class="clearfix"><div class="one">開伙</div><div class="two"><span>：</span><em title="x">可以</em></div>
<li class="clearfix"><div class="one">養寵物</div><div class="two"><span>：</span><em title="x">不可以</em></div>
</ul>
<div class="houseIntro" onselectstart="return false;"><p>透天空間＋大樓服務</p><p>190巷口就是公車站</p><p>限女生承租</p></div>
<!-- 新聞入口 -->
<div class="price clearfix"><i>6<wp></wp>0,000 <b>元/月</b></i></div>
<n-average-rent-price price="60,000" area="0"></n-average-rent-price>
<ul class="attr"><li>格局&nbsp;:&nbsp;&nbsp;4房2廳5衛5陽台</li><li>坪數&nbsp;:&nbsp;&nbsp;92.13坪</li>
<li><j></j>樓層&nbsp;:&nbsp;&nbsp;整棟/4F</li><li>型態&nbsp;:&nbsp;&nbsp;<pr></pr>透天厝</li><li>現況&nbsp;:&nbsp;&nbsp;住辦</li>
<li><dwbr></dwbr>社區&nbsp;:&nbsp;&nbsp;微笑莊園香榭區</li></ul>
<div class="avatarRight"><div style="margin-top: 13px;"><i>王新詠</i>（仲介）</div></div>
</body></html>'''


class TestLegacyRentPage(unittest.TestCase):
    """591 舊版租屋頁（住辦 302 到 rent-detail-{id}.html，沒有 __NUXT__、字中間塞空標籤）。"""

    def parse(self):
        with mock.patch.object(C, '_fetch', return_value=LEGACY_PAGE):
            return C.fetch_external('https://business.591.com.tw/rent/21893496')

    def test_fields(self):
        d = self.parse()
        self.assertEqual(d['mode'], 'rent')
        self.assertEqual(d['price'], 60000)
        self.assertEqual(d['area'], 92.13)
        self.assertEqual(d['layout'], '4房2廳5衛')
        self.assertEqual(d['floor_text'], '整棟/4F')
        self.assertEqual(d['building_type'], '住辦')
        self.assertEqual(d['address'], '台中市太平區立功路')
        self.assertEqual(d['rent_comm'], '微笑莊園香榭區')
        r = d['rent']
        self.assertEqual((r['deposit'], r['mgmt'], r['min_lease']), (2, 6000, '3年'))
        self.assertEqual(r['tags'], ['可開伙', '不可養寵物'])
        self.assertEqual(d['gallery'], ['https://img2.591.com.tw/house/2026/08/25/cover1.jpg!1000x.jpg',
                                        'https://img2.591.com.tw/house/2026/08/25/p2.jpeg!1000x.jpg'])
        self.assertIn('阿詠', d['scrub_entities'])

    def test_intro_and_no_leaks(self):
        d = self.parse()
        self.assertEqual(d['intro'], '透天空間＋大樓服務')                 # 190 巷號那行、限女生那行都拿掉
        blob = json.dumps({k: v for k, v in d.items() if k != 'scrub_entities'}, ensure_ascii=False)
        for bad in ('王新詠', '限女生', '190'):
            self.assertNotIn(bad, blob)

    def test_page_title_says_zhuban(self):
        with mock.patch.object(C, '_fetch', return_value=LEGACY_PAGE):
            props = I.fetch_full_batch(I.extract_refs('https://business.591.com.tw/rent/21893496'))
        I._scrub_fetched(props)
        self.assertIn('住辦', props[0]['og_title'])
        self.assertNotIn('辦公', props[0]['og_title'])


class TestAddressPlaceNames(unittest.TestCase):

    def test_brand_word_inside_place_name_kept(self):
        for a in ('台北市信義區松德路', '台北市大安區信義路四段', '台中市西屯區永慶路'):
            self.assertEqual(C._scrub_addr(a), a)
        self.assertEqual(C._scrub_addr('信義房屋 北屯區崇德路'), '北屯區崇德路')

    def test_placeholder_intro_dropped(self):
        self.assertEqual(C._clean_rent_remark('暫未添加說明'), '')


class TestClauseDrop(unittest.TestCase):

    def test_drops(self):
        ents = C._rent591_entities({'name': '仲介: 檳城妹仔：張嘉恩', 'imName': '張女士',
                                    'roleTxt': '經紀業: 信富地產有限公司'})
        for t in ('本套房限租女性', '只租給女性上班族', '限單身女性承租', '租客限定女性', '限國人承租', '僅限台灣籍',
                  '限40歲以下承租', '【限學生承租】', '學生勿擾', '免收租屋仲介費', '本物件無仲介費', '零服務費',
                  '不收取任何服務報酬', '房東直接出租', '屋主親自出租', '房東黃伯伯人很好', '我是房東阿明',
                  '請透過591站內信聯繫', '買賣、委託、代管、代租、包租', '我是馬來西亞 檳城妹仔嘉恩', '信富地產為您把關'):
            self.assertEqual(C._rent_clause_drop(t, ents), '', t)

    def test_keeps(self):
        for t in ('管理費含在租金內', '房東人很好', '房東阿姨人很好', '適合新手媽媽', '提供免費網路', '無管理費',
                  '男女皆可', '不限男女', '適合學生、上班族', '押金二個月', '屋主小坪數'):
            self.assertEqual(C._rent_clause_drop(t), t, t)

    def test_entities_skip_generic_honorific(self):
        ents = C._rent591_entities({'name': '代理人: 顏小姐', 'imName': '李先生', 'roleTxt': '2020年加入591'})
        self.assertEqual(ents, [])

    def test_entities_skip_branch_place_and_short_english(self):
        ents = C._rent591_entities({'name': '仲介: Vivian Lin', 'imName': 'Sky',
                                    'certificateTxt': '公司名：晨佳租賃不動產 分公司：南區 經紀業：晨佳租賃不動產有限公司'})
        self.assertIn('Vivian Lin', ents)
        self.assertIn('晨佳租賃不動產', ents)
        for bad in ('南區', 'Lin', 'Sky', 'Vivian'):
            self.assertNotIn(bad, ents)

    def test_two_char_name_only_in_self_intro(self):
        ents = C._rent591_entities({'name': '屋主: 陳建國'})
        self.assertEqual(C._rent_clause_drop('近建國市場，生活機能好', ents), '近建國市場，生活機能好')
        self.assertEqual(C._rent_clause_drop('有問題找建國', ents), '')


class TestRemarkCleaner(unittest.TestCase):

    def test_enclosed_cjk_kept_as_text(self):
        out = C._clean_rent_remark('🉑租補<br>🈲菸<br>🈲️寵<br>車位：無車位（社區🉑租）')
        self.assertEqual(out.split('\n')[:3], ['可租補', '禁菸', '禁寵'])
        self.assertIn('社區可租', out)
        self.assertIn('禁寵', I._SC.scrub_body(out, '591', deal='rent').text)

    def test_room_codes_and_lane_numbers_dropped(self):
        out = C._clean_rent_remark('採光好<br>預期租金：<br>116-2b 6300<br>116-3a 8000<br>近一中商圈<br>樓下116號是早餐店',
                                   addr_nums=['116'])
        self.assertNotIn('116', out)
        self.assertIn('採光好', out)
        self.assertIn('近一中商圈', out)

    def test_agency_signature_block_cut(self):
        html = ('精裝大三房＋平面車位<br>交通便利<br>🏠 台中全區專業代租 / 代管<br>🎀 親愛的房東您好，若您有出租需求，'
                '<br>🎀 歡迎與我聯繫<br>✨【芯築夢家｜專業房產規劃顧問 Summer芯】<br>✨ 台中各區租屋・土地買賣服務')
        out = C._clean_rent_remark(html)
        self.assertIn('精裝大三房', out)
        for bad in ('芯築夢家', 'Summer', '親愛的房東', '與我聯繫', '買賣服務'):
            self.assertNotIn(bad, out)

    def test_truncate_at_sentence_and_drop_dangling_heading(self):
        body = '<br>'.join(['這是第%d段介紹，內容完整。' % i for i in range(40)] + ['衣：時尚精品與生活購物機能'])
        out = C._clean_rent_remark(body)
        self.assertLessEqual(len(out), 500)
        self.assertTrue(out.endswith('。'), out[-20:])
        self.assertEqual(C._clean_rent_remark('採光好<br>Kontak'), '採光好')


def rent_prop(rid, **kw):
    p = {'source': 'external', 'brand': '591', 'slug': 'x' + rid, 'community_display': '東區福仁街 分租套房',
         'price': 7500, 'floor': 5, 'floor_total': 5, 'area': 8.0, 'layout': '', 'building_type': '分租套房',
         'address': '台中市東區福仁街', 'og_image': 'https://img1.591.com.tw/house/a/1.jpg!1000x.jpg',
         'gallery': ['https://img1.591.com.tw/house/a/1.jpg!1000x.jpg'], 'og_title': '', 'mode': 'rent',
         'rent': {'rent': 7500, 'deposit': 2, 'mgmt_incl': True, 'incl': '管理費、網路', 'min_lease': '一年',
                  'equip': ['冰箱', '冷氣'], 'tags': ['拎包入住']},
         'rent_cat': 'rent_share', 'rent_comm': '', 'rent_id': rid, 'scrub_entities': ['檳城妹仔'],
         'intro': '採光好，近夜市', 'detail_url': None}
    p.update(kw)
    return p


class TestRentPage(unittest.TestCase):

    def setUp(self):
        self.client = I.app.test_client()
        self.net0 = len(_sguard.attempts())
        for name in ('github_push', 'notion_log_snapshot'):
            pt = mock.patch.object(I, name, return_value={})
            pt.start()
            self.addCleanup(pt.stop)
        env = mock.patch.dict(os.environ, {'GITHUB_TOKEN': 'test-token'})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.assertEqual(_sguard.attempts()[self.net0:], [], '測試期間不准連外網')

    def test_dedup_by_listing_id(self):
        refs = [{'source': 'external', 'url': 'u1'}, {'source': 'external', 'url': 'u2'}, {'source': 'external', 'url': 'u3'}]
        rets = {'u1': rent_prop('22110623'), 'u2': rent_prop('22109849'), 'u3': rent_prop('22110623', slug='xdup')}
        with mock.patch.object(I, '_fetch_one_ref', side_effect=lambda r: dict(rets[r['url']])):
            items = I.fetch_full_batch(refs)
        self.assertEqual(sorted(p['rent_id'] for p in items), ['22109849', '22110623'])

    def test_publish_rent_page(self):
        with mock.patch.object(I, 'fetch_full_batch', return_value=[rent_prop('1'), rent_prop('2', price=12000)]):
            r = self.client.post('/api/publish', json=dict(OWNER, name='王先生', urls_text='https://rent.591.com.tw/21971367',
                                                          dry_run=True))
        j = r.get_json()
        self.assertEqual(r.status_code, 200, j)
        self.assertEqual(j['leak'], [])

    def test_rent_html(self):
        props = [rent_prop('1'), rent_prop('2', price=12000, rent_cat='rent_whole', layout='2房1廳1衛')]
        ents = set()
        found = I._scrub_fetched(props, ents)
        self.assertEqual(ents, {'檳城妹仔'})
        self.assertTrue(all('scrub_entities' not in p for p in props))
        html = I.gen_html({'name': '王先生', 'need': '', 'share_id': 'T1', 'contact': I.DEFAULT_CONTACT,
                           'mode': I._page_mode(props)}, props)
        self.assertTrue(I._audit_page(html, I.DEFAULT_CONTACT, (), ['王先生'], found, ents).ok)
        self.assertIn('元/月', html)
        self.assertIn('精選租屋', html)
        self.assertIn('租金含', html)
        self.assertIn('提供設備', html)
        self.assertNotIn('<div class="card-unit-price">', html)
        self.assertIn('data-type="分租套房"', html)
        self.assertIn('東區福仁街 整層出租 2房', html)

    def test_rent_type_chips_match_cards(self):
        """類型 chip 的數字要跟卡片 data-type 對得上（審查抓到：舊寫法 chip 寫「其他 9」點下去 0 戶）。"""
        cats = ['rent_share', 'rent_suite', 'rent_room', 'rent_whole', 'parking', 'rent_share', 'rent_suite', 'rent_share', '']
        props = [rent_prop(str(22000000 + i), rent_cat=c, price=7000 + i * 500, slug='x%d' % i) for i, c in enumerate(cats)]
        I._scrub_fetched(props)
        html = I.gen_html({'name': '', 'need': '', 'share_id': 'T3', 'contact': I.DEFAULT_CONTACT, 'mode': 'rent'}, props)
        import re as _re
        chips = dict(_re.findall(r'data-filter-type="([^"_]+)">[^<]*<span class="nav-chip-count">(\d+)</span>', html))
        cards = _re.findall(r'<div class="card" [^>]*data-type="([^"]+)"', html)
        self.assertTrue(chips, 'rent page with 9 listings should show type chips')
        for t, n in chips.items():
            self.assertEqual(cards.count(t), int(n), t)
        self.assertEqual(chips.get('分租套房'), '3')

    def test_audit_catches_poster_name(self):
        props = [rent_prop('1', intro='房東是檳城妹仔'), rent_prop('2')]
        ents = set()
        found = I._scrub_fetched(props, ents)
        props[0]['intro'] = '房東是檳城妹仔'                      # 模擬洗白漏網 → 稽核要擋
        html = I.gen_html({'name': '', 'need': '', 'share_id': 'T2', 'contact': I.DEFAULT_CONTACT, 'mode': 'rent'}, props)
        self.assertFalse(I._audit_page(html, I.DEFAULT_CONTACT, (), [], found, ents).ok)

    def test_own_listing_poster_not_blocked(self):
        """景泰自己刊在 591 的租屋：刊登者＝自己，名字／店名在聯絡區合法出現，不能被 A12 擋。"""
        props = [rent_prop('1', scrub_entities=['陳景泰', '景泰', '有巢氏房屋']), rent_prop('2')]
        ents = set()
        found = I._scrub_fetched(props, ents)
        html = I.gen_html({'name': '', 'need': '', 'share_id': 'T4', 'contact': I.DEFAULT_CONTACT, 'mode': 'rent'}, props)
        self.assertTrue(I._audit_page(html, I.DEFAULT_CONTACT, (), [], found,
                                      I._audit_entities(ents, props, I.DEFAULT_CONTACT)).ok)

    def test_poster_name_equal_to_district_not_blocked(self):
        props = [rent_prop('1', scrub_entities=['東區福仁街']), rent_prop('2', scrub_entities=[])]
        ents = set()
        I._scrub_fetched(props, ents)
        self.assertEqual(I._audit_entities(ents, props, I.DEFAULT_CONTACT), set())

    def test_query_station_rent_cards_keep_building_type_chips(self):
        """查詢台租屋卡沒有 rent_cat → 類型照 building_type 分（不能全部變「其他」）。"""
        props = [rent_prop(str(23000000 + i), rent_cat=None, building_type=bt, slug='q%d' % i, price=15000 + i * 100)
                 for i, bt in enumerate(['公寓'] * 5 + ['華廈'] * 3)]
        for p in props:
            p.pop('rent_cat')
        html = I.gen_html({'name': '', 'need': '', 'share_id': 'T5', 'contact': I.DEFAULT_CONTACT, 'mode': 'rent'}, props)
        import re as _re
        chips = dict(_re.findall(r'data-filter-type="([^"_]+)">[^<]*<span class="nav-chip-count">(\d+)</span>', html))
        self.assertEqual(chips, {'公寓': '5', '華廈': '3'})

    def test_mixed_sale_rent_400(self):
        sale = rent_prop('9', mode=None, rent=None, price=1980, community_display='總太聚作', rent_id='')
        for ep in ('/api/preview', '/api/publish'):
            with mock.patch.object(I, 'fetch_full_batch', return_value=[rent_prop('1'), sale]):
                r = self.client.post(ep, json=dict(OWNER, urls_text='https://rent.591.com.tw/21971367', dry_run=True))
            self.assertEqual(r.status_code, 400)
            self.assertIn('分成兩頁', r.get_json()['error'])

    def test_preview_price_text(self):
        with mock.patch.object(I, 'fetch_full_batch', return_value=[rent_prop('1')]):
            j = self.client.post('/api/preview', json={'urls_text': 'https://rent.591.com.tw/21971367'}).get_json()
        self.assertEqual(j['items'][0]['price_text'], '7,500 元/月')


class TestDistrict(unittest.TestCase):

    def test_parse_district(self):
        for a, want in (('台中市北屯區崇德路', '北屯區'), ('台中市北區崇德路', '北區'), ('新竹縣竹北市光明六路', '竹北市'),
                        ('南投縣草屯鎮中正路', '草屯鎮'), ('北屯區崇德路', '北屯區'), ('新竹市東區光復路', '東區'),
                        ('台中市市政路', '台中市'), ('', '其他'), ('中區市府路', '中區'), ('西區市府路一段', '西區'),
                        ('台中市中區市府路', '中區'), ('406台中市北屯區崇德路', '北屯區'), ('臺中市西屯區市政路', '西屯區'),
                        ('台東縣台東市中華路', '台東市'), ('彰化縣員林市中山路', '員林市')):
            self.assertEqual(I.parse_district(a), want, a)


if __name__ == '__main__':
    unittest.main()
