# -*- coding: utf-8 -*-
"""單一物件的客戶頁 —— 緊湊資訊版型。

景泰 2026-08-14：
  「單筆的就另外做一個版本，一頁式的，不要再用這個網頁」
  「我不要原本系統的那個美編，空位太多了」
  並指定仿照有巢氏官方分享頁（x.ychouse.tw）的資訊密度。

所以這支刻意不沿用主檔那套北歐風大留白卡片，改成資訊密集的表格式版面：

    照片（滿版、不留白）→ 標題／地址 → 三欄關鍵數字
    → 屋況（兩欄表）→ 謄本資料（兩欄表）→ 房屋描述
    → 相簿全展開 → 地圖 → 底部固定聯絡列

⛔ 客戶頁絕不出現（景泰明確要求）：
   物件編號 showCaseNo／caseKey、委託類型（專任／一般約）、刊登門市名稱與
   電話、外連永慶官方前台的連結。地址一律只到路段，不給巷弄門牌。
   承辦人一律是景泰本人。
"""
import re
from html import escape

MAPS_KEY_FALLBACK = ''


def _t(v, suffix='', dash='—'):
    """空值一律顯示破折號，不要留白也不要硬掰。"""
    if v is None:
        return dash
    s = str(v).strip()
    if s in ('', '0', '0.0', 'None', '-'):
        return dash
    return escape(s) + suffix


def _num(v, suffix='', dash='—', nd=2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return dash
    if f <= 0:
        return dash
    s = ('%.*f' % (nd, f)).rstrip('0').rstrip('.')
    return s + suffix


def _rows(pairs):
    """兩欄表：只輸出有值的列，避免整片破折號。"""
    out = []
    for label, val in pairs:
        if val in (None, '', '—'):
            continue
        out.append('<div class="r"><span class="k">%s</span><span class="v">%s</span></div>'
                   % (escape(label), val))
    return ''.join(out)


def _addr_road_only(addr, road, district, county):
    """只到路段。客戶拿完整門牌一搜就找到刊登店，這是景泰最在意的一條。"""
    a = (addr or '').strip()
    if a:
        # 保險：把巷/弄/號之後全部砍掉
        a = re.split(r'\d*\s*(?:巷|弄|號)', a)[0].strip()
    if not a:
        a = ' '.join(x for x in ((county or ''), (district or ''), (road or '')) if x)
    return a.strip()


def render(p, contact, maps_key='', client_name='', need=''):
    """p = 查詢台 /api/detail 的欄位（已在查詢台端剔除門市與判定）。"""
    pin_all = p.get('pinAll') or {}
    manage = p.get('manage') or {}
    geo = p.get('geo') or {}
    photos = [x for x in (p.get('photos') or []) if x][:30]
    layout_img = (p.get('layout') or '').strip()

    title = (p.get('caseName') or p.get('community') or '物件').strip()
    addr = _addr_road_only(p.get('address'), p.get('road'),
                           p.get('district'), p.get('county'))

    price = p.get('price')
    last_price = p.get('lastPrice')
    try:
        drop = (float(last_price) - float(price)) if last_price and price else 0
    except (TypeError, ValueError):
        drop = 0

    # ── 三欄關鍵數字 ────────────────────────────────────────────
    head_cells = [
        ('總價', '<b>%s</b><span class="u">萬</span>' % _num(price, nd=0)
         + ('<i class="drop">↓ 降 %s 萬</i>' % _num(drop, nd=0) if drop > 0 else '')),
        ('建坪', '<b>%s</b><span class="u">坪</span>' % _num(p.get('pin'))),
        ('格局', '<b>%s</b>' % _t(_layout_text(p), dash='—')),
    ]
    head_html = ''.join('<div class="hc"><span class="hk">%s</span><span class="hv">%s</span></div>'
                        % (k, v) for k, v in head_cells)

    # ── 屋況 ───────────────────────────────────────────────────
    park_list = p.get('parking') or []
    park_txt = '、'.join(x for x in park_list if x) if isinstance(park_list, list) else _t(park_list)
    cond = _rows([
        ('單價', _num(p.get('unitPrice'), ' 萬 / 坪')),
        ('登記用途', _t(p.get('regUse'))),
        ('型態', _t(p.get('caseType'))),
        ('樓層', _t(p.get('floor'), ' 樓')),
        ('屋齡', _num(p.get('age'), ' 年', nd=1)),
        ('社區', _t(p.get('community'))),
        ('朝向', _t(p.get('dirFace'))),
        ('主要建材', _t(p.get('buiStrn'))),
        ('電梯', _num(p.get('elevator'), ' 部', nd=0)),
        ('車位', park_txt or '—'),
        ('管理費', _t(manage.get('manageExpense'))),
        ('管理方式', _t(manage.get('manageType'))),
        ('小學學區', _school(p, '國小')),
        ('國中學區', _school(p, '國中')),
    ])

    # ── 謄本資料（坪數拆分）────────────────────────────────────
    deed = _rows([
        ('建物總坪', _num(pin_all.get('regArea') or p.get('pin'), ' 坪')),
        ('主建物', _num(pin_all.get('mainArea') or p.get('mainArea'), ' 坪')),
        ('附屬建物', _num(pin_all.get('totalAuxiArea'), ' 坪')),
        ('　陽台', _num(pin_all.get('porchArea'), ' 坪')),
        ('　雨遮', _num(pin_all.get('rainproofArea'), ' 坪')),
        ('共同使用', _num(pin_all.get('publicArea'), ' 坪')),
        ('地下室', _num(pin_all.get('basementArea'), ' 坪')),
        ('土地坪數', _num(pin_all.get('landArea'), ' 坪')),
    ])

    # ── 房屋描述 ───────────────────────────────────────────────
    intro = (p.get('des') or p.get('feature') or '').strip()
    intro_html = ''
    if intro:
        body = escape(intro).replace('\n', '<br>')
        intro_html = '<section><h2>房屋描述</h2><div class="desc">%s</div></section>' % body

    # ── 相簿（全展開，不用點）──────────────────────────────────
    gal = ''
    imgs = ([layout_img] if layout_img else []) + [x for x in photos if x != layout_img]
    if imgs:
        gal = ('<section><h2>物件照片 <span class="cnt">%d 張</span></h2>'
               '<div class="gal">%s</div></section>'
               % (len(imgs), ''.join('<img src="%s" loading="lazy" alt="">' % escape(u)
                                     for u in imgs)))

    # ── 地圖（直接顯示，不用點）────────────────────────────────
    map_html = ''
    if maps_key and (geo.get('latitude') or addr):
        q = ('%s,%s' % (geo['latitude'], geo['longitude'])) if geo.get('latitude') else addr
        map_html = ('<section><h2>地圖</h2><div class="map">'
                    '<iframe loading="lazy" referrerpolicy="no-referrer-when-downgrade" '
                    'src="https://www.google.com/maps/embed/v1/place?key=%s&q=%s"></iframe>'
                    '</div></section>' % (escape(maps_key), escape(q)))
    elif p.get('staticMap'):
        map_html = ('<section><h2>地圖</h2><div class="map">'
                    '<img src="%s" alt="位置圖"></div></section>' % escape(p['staticMap']))

    hero_img = photos[0] if photos else (layout_img or '')
    sub = ' · '.join(x for x in ((client_name and '給 %s' % client_name), need) if x)

    return _SHELL % {
        'title': escape(title),
        'og_img': escape(hero_img),
        'og_desc': escape('%s｜%s 萬' % (addr, _num(price, nd=0))),
        'hero': ('<img class="hero" src="%s" alt="">' % escape(hero_img)) if hero_img else '',
        'h1': escape(title),
        'addr': escape(addr),
        'sub': ('<div class="sub">%s</div>' % escape(sub)) if sub else '',
        'head': head_html,
        'cond': cond,
        'deed': deed,
        'intro': intro_html,
        'gal': gal,
        'map': map_html,
        'agent': escape(contact.get('agent_name', '')),
        'company': escape(contact.get('company', '')),
        'phone': escape(contact.get('phone', '')),
        'phone_raw': escape(contact.get('phone_raw', '')),
        'line_url': escape(contact.get('line_url', '')),
        'line_id': escape(contact.get('line', '')),
        'ig_url': escape(contact.get('ig_url', '')),
        'ig': escape(contact.get('ig', '')),
        'broker': escape(contact.get('broker_name', '')),
        'broker_lic': escape(contact.get('broker_license', '')),
        'agent_lic': escape(contact.get('agent_license', '')),
        'company_full': escape(contact.get('company_full', '')),
    }


def _layout_text(p):
    pat = p.get('pattern') or {}
    r, h, b = pat.get('room'), pat.get('livingRoom'), pat.get('bathRoom')
    if not r:
        return ''
    out = '%s房' % int(float(r))
    if h:
        out += '%s廳' % int(float(h))
    if b:
        out += '%s衛' % int(float(b))
    return out


def _school(p, kind):
    for s in (p.get('school') or []):
        name = (s.get('name') or s.get('schoolName') or '') if isinstance(s, dict) else str(s)
        if kind in name:
            return escape(name)
    return ''


_SHELL = '''<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>%(title)s</title>
<meta property="og:title" content="%(title)s">
<meta property="og:description" content="%(og_desc)s">
<meta property="og:image" content="%(og_img)s">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="%(og_img)s">
<meta name="theme-color" content="#1f7a4d">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font:17px/1.7 -apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;
  color:#1d2228;background:#f2f4f6;-webkit-font-smoothing:antialiased;padding-bottom:76px}
.wrap{max-width:720px;margin:0 auto;background:#fff}
/* 照片滿版、固定比例，不留灰邊 */
.hero{display:block;width:100%%;aspect-ratio:4/3;object-fit:cover;background:#e7eaee}
.head{padding:14px 16px 12px;border-bottom:1px solid #e6e9ed}
h1{font-size:21px;line-height:1.45;font-weight:800;letter-spacing:.2px}
.addr{margin-top:5px;color:#5b636d;font-size:16px}
.sub{margin-top:6px;color:#1f7a4d;font-size:15.5px;font-weight:600}
/* 三欄關鍵數字 */
.key{display:grid;grid-template-columns:repeat(3,1fr);border-bottom:1px solid #e6e9ed}
.hc{padding:12px 10px;text-align:center;border-right:1px solid #eef1f4}
.hc:last-child{border-right:0}
.hk{display:block;font-size:14px;color:#7c848d;margin-bottom:3px}
.hv b{font-size:23px;font-weight:800;color:#c62828;letter-spacing:.3px}
.hv .u{font-size:14px;color:#c62828;margin-left:2px}
.hv .drop{display:block;font-style:normal;font-size:13px;color:#c62828;margin-top:2px}
.hc:nth-child(2) .hv b,.hc:nth-child(3) .hv b{color:#1d2228;font-size:20px}
/* 區塊 */
section{padding:14px 16px;border-bottom:8px solid #f2f4f6}
h2{font-size:17px;font-weight:800;margin-bottom:10px;padding-left:9px;
  border-left:4px solid #1f7a4d;line-height:1.3}
h2 .cnt{font-size:14px;font-weight:500;color:#7c848d;margin-left:6px}
/* 兩欄資訊表：資訊密集，不留大片白 */
.tbl{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}
.r{display:flex;gap:8px;padding:7px 0;border-bottom:1px dotted #e2e6ea;font-size:16px}
.k{color:#7c848d;flex:none;min-width:74px}
.v{font-weight:600;word-break:break-all}
.desc{font-size:16.5px;line-height:1.85;color:#333a42;white-space:normal}
/* 相簿全展開 */
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}
.gal img{width:100%%;aspect-ratio:4/3;object-fit:cover;border-radius:6px;background:#e7eaee;display:block}
.map{border-radius:8px;overflow:hidden;border:1px solid #e6e9ed}
.map iframe,.map img{width:100%%;height:260px;border:0;display:block;object-fit:cover}
/* 底部固定聯絡列 */
.bar{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid #dfe3e8;
  box-shadow:0 -2px 12px rgba(0,0,0,.09);display:flex;align-items:center;gap:10px;
  padding:9px 14px;z-index:50}
.bar .me{flex:1;min-width:0;line-height:1.35}
.bar .nm{font-weight:800;font-size:16.5px}
.bar .co{font-size:13px;color:#7c848d;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar a.call{background:#1f7a4d;color:#fff;text-decoration:none;font-weight:800;font-size:16px;
  padding:11px 16px;border-radius:8px;white-space:nowrap}
.bar a.line{background:#06c755;color:#fff;text-decoration:none;font-weight:800;font-size:16px;
  padding:11px 14px;border-radius:8px;white-space:nowrap}
.foot{padding:14px 16px 20px;font-size:13.5px;color:#7c848d;line-height:1.8;background:#fff}
@media(max-width:640px){
  .tbl{grid-template-columns:1fr}
  h1{font-size:19.5px}
  .hv b{font-size:21px}
  .gal{grid-template-columns:repeat(3,1fr);gap:5px}
}
</style></head><body>
<div class="wrap">
%(hero)s
<div class="head"><h1>%(h1)s</h1><div class="addr">📍 %(addr)s</div>%(sub)s</div>
<div class="key">%(head)s</div>
<section><h2>屋況</h2><div class="tbl">%(cond)s</div></section>
<section><h2>謄本資料</h2><div class="tbl">%(deed)s</div></section>
%(intro)s
%(gal)s
%(map)s
<div class="foot">
  不動產經紀人 %(broker)s 證號 %(broker_lic)s<br>
  不動產營業員 %(agent)s 證號 %(agent_lic)s<br>
  %(company_full)s<br>
  本資訊以實際物件現況為準，最終以雙方議定條件為憑
</div>
</div>
<div class="bar">
  <div class="me"><div class="nm">%(agent)s</div><div class="co">%(company)s</div></div>
  <a class="line" href="%(line_url)s" target="_blank" rel="noopener">LINE</a>
  <a class="call" href="tel:%(phone_raw)s">電話諮詢</a>
</div>
</body></html>'''
