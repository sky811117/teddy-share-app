# -*- coding: utf-8 -*-
"""
猜你喜歡 推薦頁 renderer (完整規格版)。

render_recommend_page(data, contact, share_id, client_name) -> html_str

設計 (2026-06-02 景泰拍板):
  - 資訊要完全、全部不能少 -> anchor + 候選都列完整規格表。
  - anchor 大卡: 高解析首圖 + 完整規格 (全顯示)。
  - 候選卡: 核心資訊 + 「看完整規格」展開完整 (= 點進去詳細)，並 track 展開事件。
  - 候選不放同業浮水印照片、不外連永慶，CTA 全回景泰。
  - 隱私: 車位編號 / 完整門牌不顯示 (parser 已過濾)。
"""
import html as _html
import json


def _esc(s):
    if s is None:
        return ""
    return _html.escape(str(s))


def _ping(v):
    return f"{v} 坪" if v not in (None, "", 0) else None


def _floor(v):
    return f"{v}F" if v else None


def _layout(item):
    """3房 + 2廳 + 2衛 -> '3房2廳2衛'。"""
    room = (item.get("room_count") or "").strip()
    if room and not room.endswith("房"):
        room = room + "房" if room.isdigit() else room
    h = item.get("hall_count")
    b = item.get("bath_count")
    if not room:
        return None
    s = room
    if h is not None:
        s += f"{h}廳"
    if b is not None:
        s += f"{b}衛"
    return s


def _spec_table(rows):
    """rows = [(label, value), ...]，只列 value 非空。"""
    cells = ''.join(
        f'<div class="spec-row"><span class="spec-k">{_esc(k)}</span>'
        f'<span class="spec-v">{_esc(v)}</span></div>'
        for k, v in rows if v not in (None, "", 0)
    )
    return f'<div class="spec-table">{cells}</div>'


def _anchor_specs(a):
    rows = [("總價", f"{a['price_wan']:,} 萬" if a.get('price_wan') else None)]
    if a.get('price_wan') and a.get('building_area_ping'):
        rows.append(("單價", f"約 {round(a['price_wan']/a['building_area_ping'],1)} 萬/坪（權狀）"))
    rows.append(("格局", _layout(a)))
    rows.append(("權狀坪", _ping(a.get('building_area_ping'))))
    rows.append(("主建坪", _ping(a.get('main_area_ping'))))
    if a.get('public_area_ping'):
        pr = f"（公設比 {a['public_ratio']}%）" if a.get('public_ratio') else ""
        rows.append(("公設坪", f"{a['public_area_ping']} 坪{pr}"))
    rows.append(("陽台", _ping(a.get('balcony_ping'))))
    rows.append(("雨遮", _ping(a.get('rainproof_ping'))))
    rows.append(("土地持分", _ping(a.get('land_area_ping'))))
    rows.append(("樓層", _floor(a.get('floor'))))
    rows.append(("屋齡", f"{a['age_years']} 年" if a.get('age_years') else None))
    rows.append(("車位", a.get('parking')))
    rows.append(("管理費", a.get('mg_fee')))
    t, uc = a.get('type', ''), a.get('use_code', '')
    rows.append(("類型", f"{t}／{uc}" if (t and uc) else (t or uc or None)))
    sch = '／'.join(x for x in [a.get('pri_school'), a.get('jun_school')] if x)
    rows.append(("學區", sch or None))
    rows.append(("地址", a.get('address')))
    rows.append(("建照", a.get('build_date')))
    return rows


def _cand_specs(c):
    rows = [("總價", f"{c['price_wan']:,} 萬" if c.get('price_wan') else None)]
    if c.get('unit_price_wan_per_ping'):
        rows.append(("單價", f"約 {c['unit_price_wan_per_ping']} 萬/坪（權狀）"))
    rows.append(("格局", _layout(c)))
    rows.append(("權狀坪", _ping(c.get('building_area_ping'))))
    rows.append(("主建坪", _ping(c.get('main_area_ping'))))
    if c.get('parking_area_ping'):
        rows.append(("含車位坪", _ping(c.get('parking_area_ping'))))
    rows.append(("土地坪", _ping(c.get('land_area_ping'))))
    rows.append(("樓層", _floor(c.get('floor'))))
    age = c.get('age_years') or c.get('building_age')
    rows.append(("屋齡", f"{age} 年" if age else None))
    rows.append(("車位", c.get('parking')))
    rows.append(("類型", c.get('case_type')))
    rows.append(("地址", c.get('address_text') or c.get('street')))
    return rows


def _sellpoint_html(text, label="物件特色"):
    if not text:
        return ''
    lines = [_esc(ln.strip()) for ln in str(text).split('\n') if ln.strip()]
    if not lines:
        return ''
    return f'<div class="sellpoint"><b>{label}</b><br>{"<br>".join(lines)}</div>'


def _photo_strip(urls, group_id, limit=24):
    """橫向 scroll 照片條，點圖開 lightbox 輪播。"""
    if not urls:
        return ''
    items = ''.join(
        f'<img class="ph" loading="lazy" src="{_esc(u)}" alt="" '
        f'onclick="openLB(\'{group_id}\',{i})">'
        for i, u in enumerate(urls[:limit])
    )
    return f'<div class="photo-strip">{items}</div>'


def _anchor_card(anchor):
    if not anchor:
        return '<div class="anchor-empty">尚未取得物件資料</div>'
    img = _esc(anchor.get('image_url', ''))
    community = _esc(anchor.get('community_name', '社區資料整理中'))
    price = anchor.get('price_wan', 0) or 0
    img_html = (f'<div class="anchor-img" style="background-image:url(\'{img}\')" onclick="openLB(\'anchor\',0)"></div>'
                if img else '<div class="anchor-img anchor-img--placeholder"></div>')
    return f'''
    <div class="anchor-card">
      {img_html}
      <div class="anchor-body">
        <div class="anchor-label">您正在看的物件</div>
        <h2 class="anchor-community">{community}</h2>
        <div class="anchor-price">{price:,} 萬</div>
        {_spec_table(_anchor_specs(anchor))}
        {_sellpoint_html(anchor.get('selling_point'))}
        {_photo_strip(anchor.get('image_urls') or [], 'anchor')}
      </div>
    </div>'''


def _candidate_card(c, kind, anchor_price, idx):
    community = _esc(c.get('community_name') or '社區資料整理中')
    price = c.get('price_wan', 0) or 0
    house_id = _esc(c.get('house_id', ''))
    if anchor_price and price:
        diff = abs((anchor_price - price) if kind == 'cheap' else (price - anchor_price))
    else:
        diff = 0
    if kind == 'cheap':
        chip = f'<span class="cand-chip cand-chip--cheap">💎 省 {diff:,} 萬</span>'
    else:
        chip = f'<span class="cand-chip cand-chip--pricey">🌟 多 {diff:,} 萬・升級選擇</span>'

    imgs = c.get('image_urls') or []
    # 封面優先用 list 主推照 (永慶選的封面實景照)，避免 detail 第一張是格局圖
    cover = _esc(c.get('cover_image_url') or (imgs[0] if imgs else ''))

    core = []
    g = _layout(c)
    if g:
        core.append(g)
    if c.get('building_area_ping'):
        core.append(f"權狀 {c['building_area_ping']}坪")
    age = c.get('age_years') or c.get('building_age')
    if age:
        core.append(f"屋齡 {age}年")
    core_str = ' · '.join(core)
    district = _esc((c.get('district') or '') + (c.get('street') or ''))
    district_html = f'<div class="cand-district">📍 {district}</div>' if district else ''

    cover_html = (f'<div class="cand-cover" style="background-image:url(\'{cover}\')" onclick="openLB(\'cand{idx}\',0)"></div>'
                  if cover else '<div class="cand-cover cand-cover--ph"></div>')
    detail = (_spec_table(_cand_specs(c)) + _sellpoint_html(c.get('title'), "物件特色")
              + _photo_strip(imgs, f'cand{idx}'))
    nphoto = len(imgs)
    toggle_label = f"看完整規格＋{nphoto} 張照片 ▾" if nphoto else "看完整規格 ▾"

    return f'''
    <div class="cand-card cand-card--{kind}">
      {cover_html}
      <div class="cand-body">
        {chip}
        <div class="cand-community">{community}</div>
        <div class="cand-price">{price:,} 萬</div>
        <div class="cand-core">{_esc(core_str)}</div>
        {district_html}
        <div class="cand-detail" id="d{idx}">{detail}</div>
        <button class="cand-toggle" onclick="toggleDetail({idx},'{_esc(community)}','{house_id}')">{toggle_label}</button>
      </div>
    </div>'''


def _empty_state():
    return '''
    <div class="empty-state">
      <div class="empty-icon">·</div>
      <h3 class="empty-title">這間目前較為獨特</h3>
      <p class="empty-desc">同類型、相近價位的物件目前公開資料較少。我手上還有未公開的口袋名單，直接來電或加 LINE，我為您挑幾間合適的。</p>
    </div>'''


def _cta(contact):
    phone_raw = _esc(contact.get('phone_raw', '0920118756'))
    phone = _esc(contact.get('phone', '0920-118-756'))
    line_url = _esc(contact.get('line_url', 'https://line.me/ti/p/~sky811117'))
    return f'''
    <div class="cta-banner">
      <div class="cta-text">看中哪一間？還是想看更多同類型的選擇？</div>
      <div class="cta-sub">這些都能幫您安排看屋，由我一手服務到底</div>
      <div class="cta-btns">
        <a class="cta-btn cta-btn--tel" href="tel:{phone_raw}">📞 來電 {phone}</a>
        <a class="cta-btn cta-btn--line" href="{line_url}">💬 加 LINE 問我</a>
      </div>
    </div>'''


def _footer(contact, signature=''):
    company = _esc(contact.get('company', '有巢氏房屋台中世界之心加盟店'))
    company_full = _esc(contact.get('company_full', '一品不動產經紀股份有限公司'))
    phone_raw = _esc(contact.get('phone_raw', '0920118756'))
    phone = _esc(contact.get('phone', '0920-118-756'))
    line = _esc(contact.get('line', 'sky811117'))
    ig = _esc(contact.get('ig', '@nov__817'))
    ig_url = _esc(contact.get('ig_url', 'https://instagram.com/nov__817'))
    broker = _esc(contact.get('broker_name', '黃永隆'))
    broker_lic = _esc(contact.get('broker_license', '113彰縣字第324號'))
    agent = _esc(contact.get('agent_name', '陳景泰'))
    agent_lic = _esc(contact.get('agent_license', '114年登字第488296號'))
    sig_display = _esc(signature.strip()) if (signature and signature.strip()) else f'{agent} 房仲'
    return f'''
    <div class="footer">
      <div class="footer-tagline">為您智能挑選</div>
      <div class="footer-name">{sig_display}</div>
      <div class="footer-contact">
        電話 <a href="tel:{phone_raw}">{phone}</a><br>
        LINE：{line}　·　IG：<a href="{ig_url}" target="_blank">{ig}</a>
      </div>
      <div class="footer-disclaim">
        {company_full}（{company}）<br>
        經紀人 {broker} {broker_lic}　·　營業員 {agent} {agent_lic}
      </div>
    </div>'''


def _tracking_js(share_id, client_name):
    sid = json.dumps(share_id)
    cname = json.dumps(client_name or "FB猜你喜歡")
    return '''
<script>
  var SHARE_ID=__SID__;
  var CLIENT_NAME=__CNAME__;
  var TRACK_API='https://teddy-share-app.vercel.app/api/track';
  var IS_ADMIN=false; try{ IS_ADMIN=(localStorage.getItem('teddy_admin')==='1'); }catch(e){}
  var startTime=Date.now(), visitSent=false;
  function postTrack(p){ if(IS_ADMIN)return; try{ fetch(TRACK_API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p),keepalive:true}); }catch(e){} }
  function trackVisit(){ if(visitSent)return; var d=Math.round((Date.now()-startTime)/1000); if(d<3)return; visitSent=true; postTrack({client:CLIENT_NAME,share_id:SHARE_ID,duration:d,url:location.href,referrer:document.referrer||''}); }
  function trackCta(t){ var d=Math.round((Date.now()-startTime)/1000); postTrack({client:CLIENT_NAME,share_id:SHARE_ID,cta_type:t,duration:d,url:location.href,referrer:document.referrer||''}); }
  function toggleDetail(idx, name, houseId){
    var el=document.getElementById('d'+idx);
    var btn=el.nextElementSibling;
    var open=el.classList.toggle('show');
    btn.innerHTML = open ? '收合 ▴' : '看完整規格 ▾';
    if(open){ var d=Math.round((Date.now()-startTime)/1000);
      postTrack({client:CLIENT_NAME,share_id:SHARE_ID,clicked_slug:houseId,clicked_name:name,duration:d,url:location.href,referrer:document.referrer||''}); }
  }
  document.querySelectorAll('.cta-btn--tel').forEach(function(el){el.addEventListener('click',function(){trackCta('phone');});});
  document.querySelectorAll('.cta-btn--line').forEach(function(el){el.addEventListener('click',function(){trackCta('line');});});
  window.addEventListener('beforeunload',trackVisit);
  document.addEventListener('visibilitychange',function(){if(document.hidden)trackVisit();});
  setTimeout(trackVisit,20000);
</script>'''.replace('__SID__', sid).replace('__CNAME__', cname)


def render_recommend_page(data, contact, share_id, client_name='', signature=''):
    anchor = data.get('anchor') or {}
    cheap = data.get('cheap', [])
    pricey = data.get('pricey', [])
    anchor_price = anchor.get('price_wan')

    # 收集各 photo group 的 URL 陣列 (給 lightbox 輪播)
    groups = {}
    if anchor.get('image_urls'):
        groups['anchor'] = anchor['image_urls'][:24]
    gi = 0
    for c in cheap + pricey:
        imgs = c.get('image_urls') or []
        if not imgs and c.get('cover_image_url'):
            imgs = [c['cover_image_url']]  # enrich 失敗兜底，至少封面可點開
        if imgs:
            groups[f'cand{gi}'] = imgs[:24]
        gi += 1
    groups_json = json.dumps(groups)

    if cheap or pricey:
        cards, i = '', 0
        for c in cheap:
            cards += _candidate_card(c, 'cheap', anchor_price, i); i += 1
        for c in pricey:
            cards += _candidate_card(c, 'pricey', anchor_price, i); i += 1
        grid = f'<div class="cards-grid">{cards}</div>' + _cta(contact)
    else:
        grid = _empty_state() + _cta(contact)

    return _PAGE_TEMPLATE.replace('__ANCHOR__', _anchor_card(anchor)) \
                         .replace('__GRID__', grid) \
                         .replace('__FOOTER__', _footer(contact, signature)) \
                         .replace('__PHOTOGROUPS__', groups_json) \
                         .replace('__TRACKING__', _tracking_js(share_id, client_name))


_PAGE_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>為您挑的更多選擇</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    background: #F5F1EB; color: #2C2C2C;
    font-family: -apple-system, BlinkMacSystemFont, "PingFang TC", "Microsoft JhengHei", "Noto Sans TC", sans-serif;
    font-size: 17px; line-height: 1.6; -webkit-font-smoothing: antialiased;
  }
  a { color: inherit; text-decoration: none; }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 24px 16px 80px; }
  .page-title { font-size: 28px; font-weight: 700; text-align: center; margin: 16px 0 8px; letter-spacing: 1px; }
  .page-subtitle { text-align: center; font-size: 15px; color: #7a6f5e; margin-bottom: 32px; }
  .anchor-card { background: #fff; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 12px rgba(60,45,20,.08); margin-bottom: 36px; border: 1px solid rgba(212,185,150,.4); }
  .anchor-img { width: 100%; aspect-ratio: 4/3; background-size: cover; background-position: center; background-color: #e8e0d3; }
  .anchor-img--placeholder { background: linear-gradient(135deg,#e8e0d3 0%,#d4b996 100%); }
  .anchor-body { padding: 22px 20px; }
  .anchor-label { display: inline-block; background: #D4B996; color: #fff; padding: 4px 12px; border-radius: 20px; font-size: 13px; margin-bottom: 10px; letter-spacing: 1px; }
  .anchor-community { font-size: 24px; font-weight: 700; margin-bottom: 6px; }
  .anchor-price { font-size: 32px; font-weight: 800; color: #6B8E23; margin-bottom: 6px; }
  .spec-table { margin-top: 12px; border-top: 1px solid #ece2d0; }
  .spec-row { display: flex; padding: 9px 2px; border-bottom: 1px solid #f2ecde; font-size: 16px; }
  .spec-k { width: 76px; flex-shrink: 0; color: #9a8f7a; }
  .spec-v { color: #2C2C2C; font-weight: 600; flex: 1; }
  .sellpoint { margin-top: 14px; font-size: 15px; color: #5d5340; line-height: 1.8; background: #faf6ee; padding: 12px 14px; border-radius: 10px; border: 1px solid #ece2d0; }
  .sellpoint b { color: #6B8E23; }
  .section-header { display: flex; align-items: center; gap: 12px; margin: 32px 0 16px; }
  .section-header h3 { font-size: 20px; font-weight: 700; white-space: nowrap; }
  .section-line { flex: 1; height: 1px; background: #D4B996; opacity: .5; }
  .cards-grid { display: grid; gap: 16px; grid-template-columns: 1fr; }
  @media (min-width:720px){ .cards-grid { grid-template-columns: 1fr 1fr; } }
  .cand-card { position: relative; background: #fff; border-radius: 14px; overflow: hidden; box-shadow: 0 2px 10px rgba(60,45,20,.07); border: 1px solid rgba(212,185,150,.3); border-left: 5px solid #D4B996; }
  .cand-cover { width: 100%; aspect-ratio: 4/3; background-size: cover; background-position: center; background-color: #e8e0d3; }
  .cand-cover--ph { background: linear-gradient(135deg,#e8e0d3,#d4b996); }
  .cand-body { padding: 18px 18px 16px 20px; }
  .photo-strip { display: flex; gap: 8px; overflow-x: auto; padding: 10px 0 4px; -webkit-overflow-scrolling: touch; }
  .photo-strip a { flex: 0 0 auto; }
  .photo-strip img { height: 150px; width: auto; border-radius: 8px; display: block; background: #eee; }
  .cand-card--cheap { border-left-color: #6B8E23; }
  .cand-card--pricey { border-left-color: #6A5ACD; }
  .cand-chip { display: inline-block; padding: 5px 13px; border-radius: 16px; color: #fff; font-size: 15px; font-weight: 700; margin-bottom: 12px; }
  .cand-chip--cheap { background: #6B8E23; }
  .cand-chip--pricey { background: #6A5ACD; }
  .cand-community { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
  .cand-price { font-size: 28px; font-weight: 800; color: #2C2C2C; margin-bottom: 8px; }
  .cand-core { font-size: 16px; color: #5a5040; margin-bottom: 6px; }
  .cand-district { font-size: 15px; color: #7a6f5e; }
  .cand-detail { display: none; }
  .cand-detail.show { display: block; }
  .cand-toggle { margin-top: 12px; width: 100%; background: #fbf8f1; border: 1px solid #D4B996; color: #6b5b3a; padding: 10px; border-radius: 18px; font-size: 15px; font-weight: 600; cursor: pointer; }
  .cand-toggle:active { background: #f3ead8; }
  .cta-banner { margin-top: 28px; padding: 28px 20px; background: #fff; border-radius: 16px; text-align: center; border: 1px solid rgba(212,185,150,.4); box-shadow: 0 2px 12px rgba(60,45,20,.06); }
  .cta-text { font-size: 20px; font-weight: 700; margin-bottom: 6px; }
  .cta-sub { font-size: 15px; color: #7a6f5e; margin-bottom: 18px; }
  .cta-btns { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; }
  .cta-btn { display: inline-block; padding: 13px 26px; border-radius: 26px; font-size: 17px; font-weight: 700; color: #fff; }
  .cta-btn--tel { background: #6B8E23; }
  .cta-btn--line { background: #06C755; }
  .empty-state { background: #fff; border-radius: 16px; padding: 40px 24px; text-align: center; border: 1px dashed #D4B996; }
  .empty-icon { font-size: 48px; color: #D4B996; margin-bottom: 12px; line-height: 1; }
  .empty-title { font-size: 20px; font-weight: 700; margin-bottom: 10px; }
  .empty-desc { font-size: 16px; color: #6b5b3a; max-width: 480px; margin: 0 auto; }
  .footer { margin-top: 40px; padding: 24px 16px; background: #fff; border-radius: 14px; text-align: center; border: 1px solid rgba(212,185,150,.4); }
  .footer-tagline { font-size: 15px; color: #6b5b3a; margin-bottom: 14px; }
  .footer-name { font-size: 19px; font-weight: 700; margin-bottom: 8px; }
  .footer-contact { font-size: 16px; color: #2C2C2C; line-height: 2; }
  .footer-contact a { color: #6B8E23; }
  .footer-disclaim { font-size: 12px; color: #a89c83; margin-top: 14px; line-height: 1.7; }
  .photo-strip img.ph { cursor: pointer; }
  .anchor-img, .cand-cover { cursor: pointer; }
  .lb { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.93); z-index: 9999; align-items: center; justify-content: center; }
  .lb.show { display: flex; }
  .lb img { max-width: 94vw; max-height: 82vh; border-radius: 6px; object-fit: contain; }
  .lb-close { position: absolute; top: 12px; right: 18px; color: #fff; font-size: 36px; line-height: 1; cursor: pointer; z-index: 2; }
  .lb-nav { position: absolute; top: 50%; transform: translateY(-50%); color: #fff; font-size: 48px; cursor: pointer; padding: 10px 16px; user-select: none; z-index: 2; opacity: .85; }
  .lb-prev { left: 2px; } .lb-next { right: 2px; }
  .lb-count { position: absolute; bottom: 16px; left: 0; right: 0; text-align: center; color: #fff; font-size: 16px; }
</style>
</head>
<body>
  <div class="wrap">
    <h1 class="page-title">為您挑的更多選擇</h1>
    <p class="page-subtitle">以您看的物件為基準，整理價格高低不同的同類型選擇</p>
    __ANCHOR__
    <div class="section-header">
      <h3>同類型・其他選擇</h3>
      <span class="section-line"></span>
    </div>
    __GRID__
    __FOOTER__
  </div>
  <div id="lightbox" class="lb" onclick="lbBg(event)">
    <span class="lb-close" onclick="closeLB()">✕</span>
    <span class="lb-nav lb-prev" onclick="lbNav(-1)">‹</span>
    <img id="lb-img" src="" alt="">
    <span class="lb-nav lb-next" onclick="lbNav(1)">›</span>
    <div class="lb-count" id="lb-count"></div>
  </div>
  <script>
    window.PHOTO_GROUPS = __PHOTOGROUPS__;
    var LB_g=null, LB_i=0;
    function openLB(g,i){ LB_g=g; LB_i=i; lbRender(); document.getElementById('lightbox').classList.add('show'); document.body.style.overflow='hidden'; }
    function lbRender(){ var a=window.PHOTO_GROUPS[LB_g]||[]; if(!a.length)return; document.getElementById('lb-img').src=a[LB_i]; document.getElementById('lb-count').textContent=(LB_i+1)+' / '+a.length; }
    function lbNav(d){ var a=window.PHOTO_GROUPS[LB_g]||[]; if(!a.length)return; LB_i=(LB_i+d+a.length)%a.length; lbRender(); }
    function closeLB(){ document.getElementById('lightbox').classList.remove('show'); document.body.style.overflow=''; }
    function lbBg(e){ if(e.target.id==='lightbox') closeLB(); }
    document.addEventListener('keydown',function(e){ if(!document.getElementById('lightbox').classList.contains('show'))return; if(e.key==='ArrowLeft')lbNav(-1); else if(e.key==='ArrowRight')lbNav(1); else if(e.key==='Escape')closeLB(); });
    (function(){ var tx=0, lb=document.getElementById('lightbox');
      lb.addEventListener('touchstart',function(e){ tx=e.changedTouches[0].clientX; },{passive:true});
      lb.addEventListener('touchend',function(e){ var dx=e.changedTouches[0].clientX-tx; if(Math.abs(dx)>40) lbNav(dx<0?1:-1); },{passive:true}); })();
  </script>
  __TRACKING__
</body>
</html>'''
