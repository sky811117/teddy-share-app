# -*- coding: utf-8 -*-
"""
猜你喜歡 推薦頁 renderer（lib 模組版，供 /api/recommend 呼叫）。

render_recommend_page(data, contact, share_id, client_name) -> html_str

設計 (2026-06-02 景泰拍板):
  - 候選「無照片資訊卡」(永慶照片中央大浮水印不能用)，decoy 靠數字對比。
  - anchor 主物件 = 景泰自家 ycut 圖 (無浮水印) -> 保留大圖。
  - 候選卡不外連永慶，CTA 全回景泰。嵌 tracking JS (停留 / 電話 / LINE)。
"""
import html as _html
import json


def _esc(s):
    if s is None:
        return ""
    return _html.escape(str(s))


def _anchor_card(anchor):
    if not anchor:
        return '<div class="anchor-empty">尚未取得物件資料</div>'
    img = _esc(anchor.get('image_url', ''))
    community = _esc(anchor.get('community_name', '社區資料整理中'))
    price = anchor.get('price_wan', 0) or 0
    room = _esc(anchor.get('room_count', ''))
    typ = _esc(anchor.get('type', ''))
    addr = _esc(anchor.get('address', ''))
    age = anchor.get('age_years')
    ping = anchor.get('main_area_ping')

    img_html = (f'<div class="anchor-img" style="background-image:url(\'{img}\')"></div>'
                if img else '<div class="anchor-img anchor-img--placeholder"></div>')
    chips = [f'<span class="chip">{room}</span>'] if room else []
    if typ:
        chips.append(f'<span class="chip">{_esc(typ)}</span>')
    if ping:
        chips.append(f'<span class="chip">{ping} 坪</span>')
    if age is not None:
        chips.append(f'<span class="chip">屋齡 {age} 年</span>')

    return f'''
    <div class="anchor-card">
      {img_html}
      <div class="anchor-body">
        <div class="anchor-label">您正在看的物件</div>
        <h2 class="anchor-community">{community}</h2>
        <div class="anchor-price">{price:,} 萬</div>
        <div class="anchor-meta">{''.join(chips)}</div>
        <div class="anchor-addr">{addr}</div>
      </div>
    </div>'''


def _candidate_card(c, kind, anchor_price):
    community = _esc(c.get('community_name') or '社區資料整理中')
    price = c.get('price_wan', 0) or 0
    room = _esc(c.get('room_count', ''))
    ping = c.get('main_area_ping')
    age = c.get('age_years')
    if age is None:
        age = c.get('building_age')
    district = _esc(c.get('district') or '')

    if anchor_price and price:
        diff = (anchor_price - price) if kind == 'cheap' else (price - anchor_price)
    else:
        diff = 0
    diff = abs(diff)

    if kind == 'cheap':
        chip = f'<span class="cand-chip cand-chip--cheap">💎 省 {diff:,} 萬</span>'
    else:
        chip = f'<span class="cand-chip cand-chip--pricey">🌟 多 {diff:,} 萬・升級選擇</span>'

    parts = []
    if room:
        parts.append(room)
    if ping:
        parts.append(f"{ping} 坪")
    if age is not None:
        parts.append(f"屋齡 {age} 年")
    meta = ' · '.join(parts)
    district_html = f'<div class="cand-district">📍 {district}</div>' if district else ''

    return f'''
    <div class="cand-card cand-card--{kind}">
      {chip}
      <div class="cand-community">{community}</div>
      <div class="cand-price">{price:,} 萬</div>
      <div class="cand-meta">{_esc(meta)}</div>
      {district_html}
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


def _footer(contact):
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
    return f'''
    <div class="footer">
      <div class="footer-tagline">為您智能挑選</div>
      <div class="footer-name">{agent} 房仲</div>
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
(function(){
  var IS_ADMIN=false;
  try{ IS_ADMIN=(localStorage.getItem('teddy_admin')==='1'); }catch(e){}
  var SHARE_ID=__SID__;
  var CLIENT_NAME=__CNAME__;
  var TRACK_API='https://teddy-share-app.vercel.app/api/track';
  var startTime=Date.now();
  var visitSent=false;
  function postTrack(p){
    if(IS_ADMIN)return;
    try{ fetch(TRACK_API,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(p),keepalive:true}); }catch(e){}
  }
  function trackVisit(){
    if(visitSent)return;
    var d=Math.round((Date.now()-startTime)/1000);
    if(d<3)return;
    visitSent=true;
    postTrack({client:CLIENT_NAME,share_id:SHARE_ID,duration:d,url:location.href,referrer:document.referrer||''});
  }
  function trackCta(t){
    var d=Math.round((Date.now()-startTime)/1000);
    postTrack({client:CLIENT_NAME,share_id:SHARE_ID,cta_type:t,duration:d,url:location.href,referrer:document.referrer||''});
  }
  document.querySelectorAll('.cta-btn--tel').forEach(function(el){el.addEventListener('click',function(){trackCta('phone');});});
  document.querySelectorAll('.cta-btn--line').forEach(function(el){el.addEventListener('click',function(){trackCta('line');});});
  window.addEventListener('beforeunload',trackVisit);
  document.addEventListener('visibilitychange',function(){if(document.hidden)trackVisit();});
  setTimeout(trackVisit,20000);
})();
</script>'''.replace('__SID__', sid).replace('__CNAME__', cname)


def render_recommend_page(data, contact, share_id, client_name=''):
    """data = recommend() 輸出 (anchor/cheap/pricey)；contact = DEFAULT_CONTACT。"""
    anchor = data.get('anchor') or {}
    cheap = data.get('cheap', [])
    pricey = data.get('pricey', [])
    anchor_price = anchor.get('price_wan')

    if cheap or pricey:
        cards = ''.join(_candidate_card(c, 'cheap', anchor_price) for c in cheap)
        cards += ''.join(_candidate_card(c, 'pricey', anchor_price) for c in pricey)
        grid = f'<div class="cards-grid">{cards}</div>' + _cta(contact)
    else:
        grid = _empty_state() + _cta(contact)

    return _PAGE_TEMPLATE.replace('__ANCHOR__', _anchor_card(anchor)) \
                         .replace('__GRID__', grid) \
                         .replace('__FOOTER__', _footer(contact)) \
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
  .anchor-card { background: #fff; border-radius: 16px; overflow: hidden;
    box-shadow: 0 2px 12px rgba(60,45,20,0.08); margin-bottom: 36px; border: 1px solid rgba(212,185,150,0.4); }
  .anchor-img { width: 100%; aspect-ratio: 4/3; background-size: cover; background-position: center; background-color: #e8e0d3; }
  .anchor-img--placeholder { background: linear-gradient(135deg,#e8e0d3 0%,#d4b996 100%); }
  .anchor-body { padding: 24px 20px; }
  .anchor-label { display: inline-block; background: #D4B996; color: #fff; padding: 4px 12px;
    border-radius: 20px; font-size: 13px; margin-bottom: 10px; letter-spacing: 1px; }
  .anchor-community { font-size: 24px; font-weight: 700; margin-bottom: 8px; }
  .anchor-price { font-size: 32px; font-weight: 800; color: #6B8E23; margin-bottom: 12px; }
  .anchor-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
  .chip { display: inline-block; background: #F5F1EB; border: 1px solid #D4B996; color: #6b5b3a;
    padding: 4px 12px; border-radius: 16px; font-size: 14px; }
  .anchor-addr { font-size: 15px; color: #7a6f5e; }
  .section-header { display: flex; align-items: center; gap: 12px; margin: 32px 0 16px; }
  .section-header h3 { font-size: 20px; font-weight: 700; white-space: nowrap; }
  .section-line { flex: 1; height: 1px; background: #D4B996; opacity: 0.5; }
  .cards-grid { display: grid; gap: 16px; grid-template-columns: 1fr; }
  @media (min-width:640px){ .cards-grid{ grid-template-columns:1fr 1fr; } }
  @media (min-width:960px){ .cards-grid{ grid-template-columns:1fr 1fr 1fr; } }
  .cand-card { position: relative; background: #fff; border-radius: 14px; padding: 20px 20px 18px 24px;
    box-shadow: 0 2px 10px rgba(60,45,20,0.07); border: 1px solid rgba(212,185,150,0.3);
    border-left: 5px solid #D4B996; transition: transform .2s ease, box-shadow .2s ease; }
  .cand-card:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(60,45,20,0.12); }
  .cand-card--cheap { border-left-color: #6B8E23; }
  .cand-card--pricey { border-left-color: #6A5ACD; }
  .cand-chip { display: inline-block; padding: 5px 13px; border-radius: 16px; color: #fff;
    font-size: 15px; font-weight: 700; margin-bottom: 12px; }
  .cand-chip--cheap { background: #6B8E23; }
  .cand-chip--pricey { background: #6A5ACD; }
  .cand-community { font-size: 19px; font-weight: 700; margin-bottom: 4px; }
  .cand-price { font-size: 28px; font-weight: 800; color: #2C2C2C; margin-bottom: 8px; }
  .cand-meta { font-size: 16px; color: #5a5040; margin-bottom: 6px; }
  .cand-district { font-size: 15px; color: #7a6f5e; }
  .cta-banner { margin-top: 28px; padding: 28px 20px; background: #fff; border-radius: 16px;
    text-align: center; border: 1px solid rgba(212,185,150,0.4); box-shadow: 0 2px 12px rgba(60,45,20,0.06); }
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
  .footer { margin-top: 40px; padding: 24px 16px; background: #fff; border-radius: 14px;
    text-align: center; border: 1px solid rgba(212,185,150,0.4); }
  .footer-tagline { font-size: 15px; color: #6b5b3a; margin-bottom: 14px; }
  .footer-name { font-size: 19px; font-weight: 700; margin-bottom: 8px; }
  .footer-contact { font-size: 16px; color: #2C2C2C; line-height: 2; }
  .footer-contact a { color: #6B8E23; }
  .footer-disclaim { font-size: 12px; color: #a89c83; margin-top: 14px; line-height: 1.7; }
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
  __TRACKING__
</body>
</html>'''
