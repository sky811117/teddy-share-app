# -*- coding: utf-8 -*-
"""類型 chip／卡片 data-type 分類：各來源的型態寫法要歸到 大樓／華廈／透天／別墅／公寓，商用與套房才是其他。
2026-10-29 景泰：「為什麼有其他?」—— 永慶、查詢台寫「電梯大樓」，舊版只認一字不差的「大樓」，整頁都掉進其他。"""
try:
    from . import _sguard
except ImportError:
    import _sguard
import unittest

I = _sguard.load_index()


class TypeBucketTest(unittest.TestCase):

    def b(self, bt, rent=False, **kw):
        return I._type_bucket(dict(building_type=bt, **kw), rent)

    def test_exact_names_unchanged(self):
        for t in I.TYPE_ORDER:
            self.assertEqual(self.b(t), t)

    def test_source_spellings(self):
        cases = {'電梯大樓': '大樓', '住宅大樓': '大樓', ' 電梯大樓 ': '大樓', '華廈': '華廈',
                 '透天厝': '透天', '透天別墅': '透天', '別墅': '別墅', '公寓': '公寓', '公寓(5樓含以下無電梯)': '公寓'}
        for bt, want in cases.items():
            self.assertEqual(self.b(bt), want, bt)

    def test_not_home_stays_other(self):
        for bt in ('店面', '辦公大樓', '商辦', '廠房', '土地', '車位', '套房', '大樓套房', '', None, '其他'):
            self.assertEqual(self.b(bt), '其他', bt)

    def test_rent_cat_still_wins(self):
        self.assertEqual(self.b('電梯大樓', rent=True, rent_cat='rent_suite'), '獨立套房')
        self.assertEqual(self.b('電梯大樓', rent=True), '大樓')

    def test_chip_counts_match_cards(self):
        """chip 的數字跟卡片 data-type 用同一套分類：電梯大樓 3 戶、透天厝 1 戶 → 大樓 3、透天 1、沒有其他。"""
        props = []
        for i, bt in enumerate(('電梯大樓', '電梯大樓', '電梯大樓', '透天厝')):
            props.append({'slug': 'q%d' % i, 'og_title': '測試社區 %d樓 2房' % (i + 2), 'community_display': '測試社區',
                          'price': 1000 + i, 'area': 30.0, 'layout': '2房2廳1衛', 'floor': i + 2, 'floor_total': 15,
                          'age': 5.0, 'address': '台中市北屯區崇德路二段', 'building_type': bt, 'gallery': []})
        html = I.gen_html({'name': '', 'need': '', 'share_id': 'tYpE0001', 'contact': I.build_contact({})}, props)
        self.assertEqual(html.count('data-type="大樓"'), 3)
        self.assertEqual(html.count('data-type="透天"'), 1)
        self.assertNotIn('data-type="其他"', html)


if __name__ == '__main__':
    unittest.main()
