# -*- coding: utf-8 -*-
"""永慶精簡卡（og 退路）的防呆：抓回來的不是這戶的物件頁、或價格讀成幾萬塊 → 當抓取失敗，不做假卡。
2026-10-29：8XXgYStm 一次抓 13 戶，其中一戶出現「物件・3 萬」假卡（同一戶事後重抓是正常的 1,068 萬）。"""
try:
    from . import _sguard
except ImportError:
    import _sguard
import unittest

I = _sguard.load_index()


def page(og_url, title, desc):
    return ('<html><head>'
            '<meta property="og:url" content="%s">'
            '<meta property="og:title" content="%s">'
            '<meta property="og:description" content="%s">'
            '<meta property="og:image" content="//yccdn.yungching.com.tw/v1/image/?key=abc&amp;width={0}&amp;height={1}">'
            '</head><body></body></html>') % (og_url, title, desc)


class YcOgGuardTest(unittest.TestCase):

    def test_redirected_page_is_dropped(self):
        h = page('https://buy.yungching.com.tw/', '物件 | 買房 | 永慶房屋', '全台超過 3 萬筆物件，買房找永慶')
        r = I._fetch_one_yungching_og('7511508', h)
        self.assertIn('error', r)

    def test_other_listing_page_is_dropped(self):
        h = page('https://buy.yungching.com.tw/house/7000001', '別戶 | 某社區 | 買房', '出售房屋位於台中市南屯區，2房，1,068萬')
        self.assertIn('error', I._fetch_one_yungching_og('7511508', h))

    def test_tiny_price_is_dropped(self):
        h = page('https://buy.yungching.com.tw/house/7511508', '物件 | 買房 | 永慶房屋', '超過 3 萬筆物件')
        self.assertIn('error', I._fetch_one_yungching_og('7511508', h))

    def test_real_listing_still_makes_lite_card(self):
        h = page('https://buy.yungching.com.tw/house/7511508', '南屯文心豐樂 | 文心豐樂 | 買房 | 永慶房屋',
                 '出售房屋位於台中市南屯區永春東路，3房，總價1,068萬')
        r = I._fetch_one_yungching_og('7511508', h)
        self.assertNotIn('error', r)
        self.assertTrue(r['lite'])
        self.assertEqual(r['price'], 1068)
        self.assertEqual(r['gallery'], ['https://yccdn.yungching.com.tw/v1/image/?key=abc&width=1024&height=768'])

    def test_listing_without_price_still_allowed(self):
        h = page('https://buy.yungching.com.tw/house/7511508', '南屯文心豐樂 | 文心豐樂 | 買房', '出售房屋位於台中市南屯區永春東路，3房')
        r = I._fetch_one_yungching_og('7511508', h)
        self.assertNotIn('error', r)
        self.assertEqual(r['price'], 0)


if __name__ == '__main__':
    unittest.main()
