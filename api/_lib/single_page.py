# -*- coding: utf-8 -*-
"""單一物件的客戶頁 —— 緊湊資訊版型（single-v2）。

景泰 2026-08-14：
  「單筆的就另外做一個版本，一頁式的，不要再用這個網頁」
  「我不要原本系統的那個美編，空位太多了」
  並指定仿照有巢氏官方分享頁（x.ychouse.tw）的資訊密度。

所以這支刻意不沿用主檔那套北歐風大留白卡片，改成資訊密集的表格式版面：

    照片（滿版、不留白）→ 標題／地址 → 三欄關鍵數字
    → 屋況（兩欄表）→ 謄本資料（兩欄表）→ 房屋描述
    → 相簿全展開 → 原始刊登連結 → 地圖 → 底部固定聯絡列

2026-10-03 cards-v2（CONTRACT §5、01 §4.8）：
  · 標題用 p['title']（結構化名稱）、內文用 p['intro']（洗白後）；仲介原標題 caseName／原文 des 一律不上頁
  · 地圖只用 map_q 文字查詢（台中市＋區＋路），不用精準座標、不用 staticMap
  · 原始刊登連結區包 <!--z:links-->；.foot、.bar 兩塊聯絡區包 <!--z:contact-->（給 mp_audit 分區稽核）
  · 所有插入的欄位一律 html.escape；租屋分支（mode='rent'：月租 元/月、管理費含否、押金）

⛔ 客戶頁絕不出現（景泰明確要求）：
   物件編號 showCaseNo／caseKey、委託類型（專任／一般約）、刊登門市名稱與
   電話、外連永慶官方前台的連結（原始刊登連結只放連結區）。地址一律只到路段，不給巷弄門牌。
   承辦人一律是登入者本人。
"""
import re
from html import escape
from urllib.parse import quote

MAPS_KEY_FALLBACK = ''
LINK_TEXT_FMT = '原始刊登 {n}'           # 與 mp_lexicon.LINK_TEXT_FMT 同值
RENDER = 'single-v2'


def _e(v):
    """插進 HTML 的文字一律跳脫（含引號）。"""
    return escape('' if v is None else str(v), quote=True)


def _t(v, suffix='', dash='—'):
    """空值一律顯示破折號，不要留白也不要硬掰。"""
    if v is None:
        return dash
    s = str(v).strip()
    if s in ('', '0', '0.0', 'None', '-'):
        return dash
    return _e(s) + _e(suffix)


def _num(v, suffix='', dash='—', nd=2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return dash
    if f != f or f <= 0:
        return dash
    s = '%.*f' % (nd, f)
    # 只在有小數點時才砍尾零 —— 否則 nd=0 的整數會被 rstrip('0') 吃掉位數
    # （2026-08-17：950 萬曾被顯示成 95 萬，就是這裡少了這道判斷）
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s + _e(suffix)


def _money(v, dash='—'):
    """整數加千分位（月租 18,000）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return dash
    if f != f or f <= 0:
        return dash
    return '{:,}'.format(int(round(f)))


def _rows(pairs):
    """兩欄表：只輸出有值的列，避免整片破折號。（值已跳脫）"""
    out = []
    for label, val in pairs:
        if val in (None, '', '—'):
            continue
        out.append('<div class="r"><span class="k">%s</span><span class="v">%s</span></div>'
                   % (_e(label), val))
    return ''.join(out)


def _addr_road_only(addr, road, district, county):
    """只到路段。客戶拿完整門牌一搜就找到刊登店，這是景泰最在意的一條。"""
    a = (addr or '').strip()
    if a:
        # 保險：把巷/弄/號之後全部砍掉（含中文數字）
        a = re.split(r'[\d０-９一二三四五六七八九十百〇零○]*\s*(?:巷|弄|號)', a)[0].strip()
    if not a:
        a = ' '.join(x for x in ((county or ''), (district or ''), (road or '')) if x)
    return a.strip()


def _ref(u):
    """591 圖床不帶 Referer 會 403 → 覆蓋頁面 no-referrer；其他一律 no-referrer。"""
    m = re.match(r'(?i)^(?:https?:)?//([^/:?#]+)', u or '')
    host = (m.group(1) if m else '').lower()
    return 'strict-origin-when-cross-origin' if (host == '591.com.tw' or host.endswith('.591.com.tw')) \
        else 'no-referrer'


def _img(u, cls='', alt=''):
    return '<img%s referrerpolicy="%s" src="%s" loading="lazy" alt="%s">' % (
        (' class="%s"' % cls) if cls else '', _ref(u), _e(u), _e(alt))


def _links_html(links):
    """原始刊登連結區：文字一律「原始刊登 N」、rel 三件套；包 z:links。"""
    links = [ln for ln in (links or []) if isinstance(ln, dict) and ln.get('url')]
    if not links:
        return ''
    items = ''.join('<a class="src" href="%s" target="_blank" rel="nofollow noopener noreferrer">%s</a>'
                    % (_e(ln['url']), _e(LINK_TEXT_FMT.format(n=i))) for i, ln in enumerate(links, 1))
    return ('<!--z:links--><section class="links"><h2>原始刊登連結 <span class="cnt">%d 則</span></h2>'
            '<div class="lk">%s</div><div class="lk-note">原始刊登連結為第三方網站，資訊以現場及正式文件為準。</div>'
            '</section><!--/z:links-->' % (len(links), items))


def _rent_rows(rent):
    """租屋：管理費（含或另計）、押金、車位費、最短租期。"""
    rent = rent if isinstance(rent, dict) else {}
    rows = []
    if rent.get('mgmt_incl') is True:
        rows.append(('管理費', '含在租金內'))
    elif rent.get('mgmt'):
        rows.append(('管理費', '另計 %s 元/月' % _money(rent['mgmt'])))
    elif rent.get('mgmt_incl') is False:
        rows.append(('管理費', '另計'))
    dep = rent.get('deposit')
    if isinstance(dep, (int, float)) and not isinstance(dep, bool) and dep > 0:
        rows.append(('押金', '%s 個月' % _num(dep)))
    elif isinstance(dep, str) and dep.strip():
        rows.append(('押金', _e(dep.strip())))
    if rent.get('parking_fee'):
        rows.append(('車位費', '%s 元/月' % _money(rent['parking_fee'])))
    ml = rent.get('min_lease')
    if isinstance(ml, (int, float)) and not isinstance(ml, bool) and ml > 0:
        rows.append(('最短租期', '%s 個月' % _num(ml)))
    elif isinstance(ml, str) and ml.strip():
        rows.append(('最短租期', _e(ml.strip())))
    return rows


def render(p, contact, maps_key='', client_name='', need=''):
    """p＝查詢台送來、share app 已依 SINGLE_KEYS 白名單洗過的欄位（mp_share.yc_single）。"""
    pin_all = p.get('pinAll') if isinstance(p.get('pinAll'), dict) else {}
    manage = p.get('manage') if isinstance(p.get('manage'), dict) else {}
    photos = [x for x in (p.get('photos') or []) if isinstance(x, str) and x][:30]
    layout_img = p.get('layout') if isinstance(p.get('layout'), str) else ''
    layout_img = layout_img.strip()
    rent_mode = p.get('mode') == 'rent'
    rent = p.get('rent') if isinstance(p.get('rent'), dict) else {}

    title = (p.get('title') or p.get('community') or '物件')
    title = str(title).strip() or '物件'
    subtitle = str(p.get('subtitle') or '').strip()
    addr = _addr_road_only(p.get('address'), p.get('road'),
                           p.get('district'), p.get('county'))

    price = p.get('price')
    last_price = p.get('lastPrice')
    try:
        drop = (float(last_price) - float(price)) if last_price and price and not rent_mode else 0
    except (TypeError, ValueError):
        drop = 0

    # ── 三欄關鍵數字 ────────────────────────────────────────────
    if rent_mode:
        price_cell = ('月租', '<b>%s</b><span class="u">元/月</span>' % _money(price or rent.get('rent')))
    else:
        price_cell = ('總價', '<b>%s</b><span class="u">萬</span>' % _num(price, nd=0)
                      + ('<i class="drop">↓ 降 %s 萬</i>' % _num(drop, nd=0) if drop > 0 else ''))
    head_cells = [
        price_cell,
        ('建坪', '<b>%s</b><span class="u">坪</span>' % _num(p.get('pin'))),
        ('格局', '<b>%s</b>' % _t(_layout_text(p), dash='—')),
    ]
    head_html = ''.join('<div class="hc"><span class="hk">%s</span><span class="hv">%s</span></div>'
                        % (_e(k), v) for k, v in head_cells)

    # ── 屋況 ───────────────────────────────────────────────────
    park_list = p.get('parking') or []
    if isinstance(park_list, list):
        park_txt = _e('、'.join(str(x) for x in park_list if x))
    else:
        park_txt = _t(park_list)
    cond_pairs = [] if rent_mode else [('單價', _num(p.get('unitPrice'), ' 萬 / 坪'))]
    cond_pairs += [
        ('登記用途', _t(p.get('regUse'))),
        ('型態', _t(p.get('caseType'))),
        ('樓層', _t(p.get('floor'), ' 樓')),
        ('屋齡', _num(p.get('age'), ' 年', nd=1)),
        ('社區', _t(p.get('community'))),
        ('朝向', _t(p.get('dirFace'))),
        ('主要建材', _t(p.get('buiStrn'))),
        ('電梯', _num(p.get('elevator'), ' 部', nd=0)),
        ('車位', park_txt or '—'),
    ]
    if rent_mode:
        cond_pairs += _rent_rows(rent)
    else:
        cond_pairs += [('管理費', _t(manage.get('manageExpense')))]
    cond_pairs += [
        ('管理方式', _t(manage.get('manageType'))),
        ('小學學區', _school(p, '國小')),
        ('國中學區', _school(p, '國中')),
    ]
    cond = _rows(cond_pairs)
    tags = [t for t in (rent.get('tags') or []) if isinstance(t, str) and t.strip()] if rent_mode else []
    tags_html = ('<div class="tags">%s</div>' % ''.join('<span>%s</span>' % _e(t) for t in tags[:12])) if tags else ''

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
    deed_html = '<section><h2>謄本資料</h2><div class="tbl">%s</div></section>' % deed if deed else ''

    # ── 房屋描述（洗白後的 intro；仲介原文 des 不上頁）──────────────
    intro = str(p.get('intro') or '').strip()
    intro_html = ''
    if intro:
        intro_html = '<section><h2>房屋描述</h2><div class="desc">%s</div></section>' % _e(intro).replace('\n', '<br>')

    # ── 相簿（全展開，不用點）──────────────────────────────────
    gal = ''
    imgs = ([layout_img] if layout_img else []) + [x for x in photos if x != layout_img]
    if imgs:
        gal = ('<section><h2>物件照片 <span class="cnt">%d 張</span></h2>'
               '<div class="gal">%s</div></section>'
               % (len(imgs), ''.join(_img(u) for u in imgs)))

    # ── 地圖：只用路名文字查詢（不給精準座標、不用 staticMap）────────────
    map_q = str(p.get('map_q') or addr or '').strip()
    map_html = ''
    if maps_key and map_q:
        map_html = ('<section><h2>地圖</h2><div class="map">'
                    '<iframe loading="lazy" referrerpolicy="no-referrer-when-downgrade" title="路段位置地圖" '
                    'src="https://www.google.com/maps/embed/v1/place?key=%s&amp;q=%s"></iframe>'
                    '</div></section>' % (_e(quote(str(maps_key), safe='')), _e(quote(map_q, safe=''))))

    hero_img = photos[0] if photos else (layout_img or '')
    sub = ' · '.join(x for x in ((client_name and '給 %s' % client_name), need) if x)
    if rent_mode:
        og_desc = '%s｜月租 %s 元' % (addr, _money(price or rent.get('rent')))
    else:
        og_desc = '%s｜%s 萬' % (addr, _num(price, nd=0))

    return _SHELL % {
        'title': _e(title),
        'og_img': _e(hero_img),
        'og_desc': _e(og_desc),
        'hero': _img(hero_img, cls='hero') if hero_img else '',
        'h1': _e(title),
        'subtitle': ('<div class="sub2">%s</div>' % _e(subtitle)) if subtitle and subtitle != title else '',
        'addr': _e(addr),
        'sub': ('<div class="sub">%s</div>' % _e(sub)) if sub else '',
        'head': head_html,
        'cond': cond,
        'tags': tags_html,
        'deed': deed_html,
        'intro': intro_html,
        'gal': gal,
        'links': _links_html(p.get('links')),
        'map': map_html,
        'render': RENDER,
        'agent': _e(contact.get('agent_name', '')),
        'company': _e(contact.get('company', '')),
        'phone': _e(contact.get('phone', '')),
        'phone_raw': _e(contact.get('phone_raw', '')),
        'line_url': _e(contact.get('line_url', '')),
        'line_id': _e(contact.get('line', '')),
        'broker': _e(contact.get('broker_name', '')),
        'broker_lic': _e(contact.get('broker_license', '')),
        'agent_lic': _e(contact.get('agent_license', '')),
        'company_full': _e(contact.get('company_full', '')),
    }


def _layout_text(p):
    pat = p.get('pattern') if isinstance(p.get('pattern'), dict) else {}
    try:
        r = int(float(pat.get('room') or 0))
        h = int(float(pat.get('livingRoom') or 0))
        b = int(float(pat.get('bathRoom') or 0))
    except (TypeError, ValueError):
        return ''
    if not r:
        return ''
    out = '%d房' % r
    if h:
        out += '%d廳' % h
    if b:
        out += '%d衛' % b
    return out


def _school(p, kind):
    for s in (p.get('school') or []):
        name = (s.get('name') or s.get('schoolName') or '') if isinstance(s, dict) else str(s)
        if kind in name:
            return _e(name)
    return ''


_SHELL = '''<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
<meta name="x-render" content="%(render)s">
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
.sub2{margin-top:4px;color:#3d454e;font-size:17px;font-weight:600}
.addr{margin-top:5px;color:#5b636d;font-size:16px}
.sub{margin-top:6px;color:#1f7a4d;font-size:15.5px;font-weight:600}
/* 三欄關鍵數字 */
.key{display:grid;grid-template-columns:repeat(3,1fr);border-bottom:1px solid #e6e9ed}
.hc{padding:12px 10px;text-align:center;border-right:1px solid #eef1f4}
.hc:last-child{border-right:0}
.hk{display:block;font-size:14px;color:#7c848d;margin-bottom:3px}
.hv b{font-size:23px;font-weight:800;color:#c62828;letter-spacing:.3px}
.hv .u{font-size:14px;color:#c62828;margin-left:2px}
.hv .drop{display:block;font-style:normal;font-size:14px;color:#c62828;margin-top:2px}
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
/* 租屋標籤（新元素 ≥17px） */
.tags{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.tags span{font-size:17px;color:#1f7a4d;background:#eaf5ef;border-radius:16px;padding:3px 12px}
/* 相簿全展開 */
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}
.gal img{width:100%%;aspect-ratio:4/3;object-fit:cover;border-radius:6px;background:#e7eaee;display:block}
/* 原始刊登連結（新元素 ≥17px） */
.lk{display:flex;flex-wrap:wrap;gap:8px}
.lk a.src{font-size:17px;font-weight:700;color:#1f7a4d;text-decoration:none;border:1.5px solid #bfe0cd;
  border-radius:8px;padding:8px 14px;background:#f5fbf7}
.lk-note{margin-top:8px;font-size:17px;color:#5b636d;line-height:1.6}
.map{border-radius:8px;overflow:hidden;border:1px solid #e6e9ed}
.map iframe{width:100%%;height:260px;border:0;display:block}
/* 底部固定聯絡列 */
.bar{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid #dfe3e8;
  box-shadow:0 -2px 12px rgba(0,0,0,.09);display:flex;align-items:center;gap:10px;
  padding:9px 14px;z-index:50}
.bar .me{flex:1;min-width:0;line-height:1.35}
.bar .nm{font-weight:800;font-size:16.5px}
.bar .co{font-size:14px;color:#7c848d;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar a.call{background:#1f7a4d;color:#fff;text-decoration:none;font-weight:800;font-size:16px;
  padding:11px 16px;border-radius:8px;white-space:nowrap}
.bar a.line{background:#06c755;color:#fff;text-decoration:none;font-weight:800;font-size:16px;
  padding:11px 14px;border-radius:8px;white-space:nowrap}
.foot{padding:14px 16px 20px;font-size:14px;color:#7c848d;line-height:1.8;background:#fff}
@media(max-width:640px){
  /* 底部列改顯示完整號碼後會變長,縮字級與內距,避免把承辦人姓名擠掉 */
  .bar a.call{font-size:14.5px;padding:10px 11px}
  .bar a.line{font-size:14.5px;padding:10px 11px}
  .bar .nm{font-size:15.5px}
  .tbl{grid-template-columns:1fr}
  h1{font-size:19.5px}
  .hv b{font-size:21px}
  .gal{grid-template-columns:repeat(3,1fr);gap:5px}
}
</style></head><body>
<div class="wrap">
%(hero)s
<div class="head"><h1>%(h1)s</h1>%(subtitle)s<div class="addr">📍 %(addr)s</div>%(sub)s</div>
<div class="key">%(head)s</div>
<section><h2>屋況</h2><div class="tbl">%(cond)s</div>%(tags)s</section>
%(deed)s
%(intro)s
%(gal)s
%(links)s
%(map)s
<!--z:contact-->
<div class="foot">
  不動產經紀人 %(broker)s 證號 %(broker_lic)s<br>
  不動產營業員 %(agent)s 證號 %(agent_lic)s<br>
  %(company_full)s<br>
  本資訊以實際物件現況為準，最終以雙方議定條件為憑
</div>
<!--/z:contact-->
</div>
<!--z:contact-->
<div class="bar">
  <div class="me"><div class="nm">%(agent)s</div><div class="co">%(company)s</div></div>
  <a class="line" href="%(line_url)s" target="_blank" rel="noopener">LINE</a>
  <a class="call" href="tel:%(phone_raw)s">📞 %(phone)s</a>
</div>
<!--/z:contact-->
</body></html>'''
