"""
Vercel serverless function — 客戶推薦頁產生器後端

接收前端表單 POST → 抓 ycut → 產 HTML → push 到 sky811117/teddy-shares
回傳客戶分享 URL
"""
import os
import re
import json
import base64
import secrets
import datetime
import urllib.request
import urllib.error
import concurrent.futures
from flask import Flask, request, jsonify, send_from_directory

GITHUB_OWNER = "sky811117"
GITHUB_REPO = "teddy-shares"
PAGES_BASE_URL = f"https://{GITHUB_OWNER}.github.io/{GITHUB_REPO}"

DEFAULT_CONTACT = {
    "company": "有巢氏房屋台中世界之心加盟店",
    "company_full": "一品不動產經紀股份有限公司",
    "phone": "0920-118-756",
    "phone_raw": "0920118756",
    "line": "sky811117",
    "line_url": "https://line.me/ti/p/~sky811117",
    "ig": "@nov__817",
    "ig_url": "https://instagram.com/nov__817",
    "broker_name": "黃永隆",
    "broker_license": "113彰縣字第324號",
    "agent_name": "陳景泰",
    "agent_license": "114年登字第488296號",
}


# ============== Parser ==============

def parse_ycut_html(html, slug):
    def og(prop):
        m = re.search(rf'<meta property="{re.escape(prop)}" content="([^"]+)"', html)
        return m.group(1).replace("&amp;", "&") if m else None

    def field(label):
        pat = rf'<span[^>]*tit_gray[^>]*>\s*{re.escape(label)}\s*</span>(?:\s*<!---->)?\s*<span[^>]*>(.*?)</span>'
        m = re.search(pat, html, re.DOTALL)
        if not m:
            return None
        val = m.group(1)
        val = re.sub(r"<!--[^>]*-->|<!---->", "", val)
        val = re.sub(r"<[^>]+>", "", val)
        return re.sub(r"\s+", " ", val).strip()

    addr_m = re.search(r'<div[^>]*class="[^"]*col-8[^"]*font16[^"]*"[^>]*>([^<]+)</div>', html)
    address = addr_m.group(1).strip() if addr_m else ""

    floor_str = field("樓層") or ""
    fm = re.match(r"(\d+)(?:\s*~\s*\d+)?\s*/\s*(\d+)", floor_str)
    floor = int(fm.group(1)) if fm else 0
    floor_total = int(fm.group(2)) if fm else 0

    area_str = field("建物總坪") or field("建坪") or ""
    am = re.search(r"([\d.]+)\s*坪", area_str)
    area = float(am.group(1)) if am else 0.0
    pm = re.search(r"含車位\s*([\d.]+)\s*坪", area_str)
    parking_area_num = float(pm.group(1)) if pm else 0.0

    def num(v):
        if not v:
            return 0.0
        m = re.search(r"([\d.]+)", v)
        return float(m.group(1)) if m else 0.0

    main_only = num(field("-主建物"))
    sub_only = num(field("-附屬建物"))
    main_area = round(main_only + sub_only, 3)
    age = num(field("屋齡"))
    community = field("社區") or ""

    parking_type = (field("停車方式") or field("車位") or "坡道平面").replace("/", "")
    parking_num = field("車位編號") or ""
    parking = f"{parking_type} {parking_num}".strip()

    desc = og("og:description") or ""
    pm2 = re.search(r"([\d,]+)\s*萬", desc)
    price = int(pm2.group(1).replace(",", "")) if pm2 else 0

    return {
        "slug": slug,
        "community_display": community,
        "price": price,
        "floor": floor,
        "floor_total": floor_total,
        "area": area,
        "main_area": main_area,
        "age": age,
        "parking": parking,
        "parking_area": f"{parking_area_num} 坪" if parking_area_num else "含於主建",
        "address": address,
        "og_image": og("og:image"),
        "og_title": og("og:title"),
        "og_description": desc,
    }


def _fetch_one_full(slug):
    url = f"https://x.ychouse.tw/{slug}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return {"slug": slug, "error": str(e)}
    try:
        return parse_ycut_html(html, slug)
    except Exception as e:
        return {"slug": slug, "error": f"parse 失敗: {e}"}


def fetch_full_batch(slugs):
    raw = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for r in pool.map(_fetch_one_full, slugs):
            if r.get("error") or not r.get("community_display") or not r.get("price"):
                continue
            raw.append(r)

    items, seen_fp = [], {}
    for it in raw:
        fp = (
            it.get("community_display", "").strip(),
            it.get("floor", 0),
            it.get("floor_total", 0),
            round(it.get("area", 0), 2),
            it.get("price", 0),
        )
        if fp in seen_fp:
            continue
        seen_fp[fp] = it["slug"]
        items.append(it)
    return items


def extract_urls(text):
    found = re.findall(r"https?://x\.ychouse\.tw/(\w+)", text or "")
    seen, out = set(), []
    for s in found:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def gen_share_id():
    return re.sub(r"[^a-zA-Z0-9]", "", secrets.token_urlsafe(8))[:8]


# ============== HTML 賣點清理 ==============

def clean_tagline(og_title):
    if not og_title:
        return ""
    s = og_title.strip().strip("【】")
    s = re.sub(r"^\^[^專]*", "", s)
    s = re.sub(r"^[\U0001F300-\U0001FAFF☀-➿✀-➿🎀🌞🔥]+", "", s)
    s = re.sub(r"^(專簽|專營|四品牌獨家|品牌獨家|獨家)[/／｜| ]?", "", s)
    s = re.sub(r"[\U0001F300-\U0001FAFF☀-➿✀-➿🎀🌞🔥]+$", "", s).strip()
    s = re.sub(r"[┃｜|/／◆]+", " · ", s)
    s = re.sub(r"\s*·\s*", " · ", s)
    return re.sub(r"\s+", " ", s).strip(" ·")


# ============== HTML 產生 ==============

def card_html(p):
    img = p.get("og_image") or ""
    tagline = clean_tagline(p.get("og_title", "")) or p.get("community_display", "")
    return f'''
    <div class="card">
      <div class="card-image" style="background-image: url('{img}');"></div>
      <div class="card-content">
        <div class="card-tagline">{tagline}</div>
        <div class="card-price-row">
          <div><span class="card-price">{p["price"]:,}</span><span class="card-price-unit">萬 含車位</span></div>
          <div class="card-floor">{p["floor"]}F / {p["floor_total"]}F</div>
        </div>
        <div class="card-spec">
          <div class="spec-item"><span class="spec-label">權狀坪數</span><span class="spec-value highlight">{p["area"]} 坪</span></div>
          <div class="spec-item"><span class="spec-label">主+附</span><span class="spec-value">{p["main_area"]} 坪</span></div>
          <div class="spec-item"><span class="spec-label">格局</span><span class="spec-value">3房2廳2衛</span></div>
          <div class="spec-item"><span class="spec-label">屋齡</span><span class="spec-value">{p["age"]} 年</span></div>
          <div class="spec-item"><span class="spec-label">車位</span><span class="spec-value">{p["parking"]}</span></div>
          <div class="spec-item"><span class="spec-label">車位坪數</span><span class="spec-value">{p["parking_area"]}</span></div>
        </div>
        <div class="card-address">{p["address"]}</div>
        <a class="card-cta" href="https://x.ychouse.tw/{p["slug"]}" target="_blank" rel="noopener">看完整資訊 與 全部照片 <span class="card-cta-arrow">→</span></a>
      </div>
    </div>'''


def section_html(community, items, anchor):
    items_sorted = sorted(items, key=lambda x: x["price"])
    prices = [x["price"] for x in items_sorted]
    price_range = f"{prices[0]:,} 萬" if len(prices) == 1 else f"{prices[0]:,} ~ {prices[-1]:,} 萬"
    ages = sorted({x["age"] for x in items_sorted})
    age_text = f"{ages[0]} 年" if len(ages) == 1 else f"{ages[0]}~{ages[-1]} 年"
    cards = "\n".join(card_html(p) for p in items_sorted)
    return f'''
<section class="section" id="{anchor}">
  <div class="section-header">
    <h2 class="section-title">{community}</h2>
    <div class="section-meta">屋齡 {age_text} · <strong>{len(items_sorted)} 戶可選</strong> · {price_range}</div>
  </div>
  <div class="grid">{cards}
  </div>
</section>'''


def gen_html(client_data, properties):
    contact = client_data.get("contact", DEFAULT_CONTACT)
    theme = (client_data.get("theme") or "景泰精選").strip()
    signature = (client_data.get("signature") or contact.get("agent_name") or "陳景泰").strip()

    # 按社區分組
    groups, order = {}, []
    for i, p in enumerate(properties):
        c = p["community_display"]
        if c not in groups:
            groups[c] = []
            anchor = "c-" + re.sub(r"[^a-z0-9]", "", c.lower())[:8] + str(i)
            order.append((c, anchor))
        groups[c].append(p)
    order_sorted = sorted(order, key=lambda x: min(item["price"] for item in groups[x[0]]))

    nav_chips = "\n    ".join(
        f'<a class="nav-chip" href="#{anchor}">{community.replace("佳茂6962","")}<span class="nav-chip-count">{len(groups[community])}</span></a>'
        for community, anchor in order_sorted
    )
    sections = "\n".join(section_html(c, groups[c], a) for c, a in order_sorted)

    total_count = len(properties)
    community_count = len(groups)
    prices = sorted(p["price"] for p in properties)
    price_min, price_max = prices[0], prices[-1]
    today = datetime.date.today().strftime("%Y.%m.%d")

    return f'''<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{theme} · 給 {client_data["name"]} 的 {total_count} 戶整理</title>
<meta property="og:title" content="{theme} · 給 {client_data["name"]} 的 {total_count} 戶整理">
<meta property="og:description" content="{client_data["need"]}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;600;700;900&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #FAF7F2; --bg-soft: #F2EDE4; --card: #FFFFFF;
    --wood-deep: #8B7355; --wood-light: #D9C7B0;
    --accent: #C9785A; --accent-deep: #A85D3F;
    --text: #2C2620; --text-soft: #6B6258; --text-muted: #9A9088;
    --border: #E8DFD2;
    --shadow: 0 4px 16px rgba(139, 115, 85, 0.08);
    --shadow-hover: 0 16px 40px rgba(139, 115, 85, 0.18);
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html {{ scroll-behavior: smooth; scroll-padding-top: 90px; }}
  body {{
    font-family: 'Noto Sans TC', -apple-system, BlinkMacSystemFont, 'PingFang TC', sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.8;
    font-size: 17px; -webkit-font-smoothing: antialiased;
  }}
  .container {{ max-width: 1280px; margin: 0 auto; padding: 0 20px; }}
  .hero {{
    background: linear-gradient(135deg, rgba(242,237,228,0.85) 0%, rgba(232,223,210,0.95) 100%),
                url('https://images.unsplash.com/photo-1600585154340-be6161a56a0c?w=1600&q=80') center/cover;
    padding: 80px 20px 60px; text-align: center; border-bottom: 1px solid var(--border);
  }}
  .hero-tag {{
    display: inline-block; background: var(--wood-deep); color: #FFF;
    font-size: 16px; letter-spacing: 4px; padding: 9px 24px;
    border-radius: 24px; margin-bottom: 22px; font-weight: 500;
  }}
  .hero h1 {{ font-size: clamp(30px, 5.5vw, 50px); font-weight: 900; margin-bottom: 14px; line-height: 1.3; }}
  .hero .for-client {{ font-size: clamp(18px, 2.6vw, 22px); color: var(--accent-deep); font-weight: 600; margin-bottom: 24px; }}
  .hero .summary {{ font-size: clamp(16px, 2vw, 18px); color: var(--text-soft); max-width: 680px; margin: 0 auto; line-height: 1.9; }}
  .hero-stats {{ display: flex; justify-content: center; gap: 50px; margin-top: 36px; flex-wrap: wrap; }}
  .hero-stat-num {{ font-size: 40px; font-weight: 900; color: var(--wood-deep); line-height: 1; }}
  .hero-stat-label {{ font-size: 14px; color: var(--text-soft); margin-top: 8px; letter-spacing: 1.5px; }}
  .sticky-nav {{
    position: sticky; top: 0; z-index: 100; padding: 14px 0;
    background: rgba(250,247,242,0.97); backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px); border-bottom: 1px solid var(--border);
  }}
  .nav-scroll {{ display: flex; gap: 10px; overflow-x: auto; padding: 0 20px; scrollbar-width: none; }}
  .nav-scroll::-webkit-scrollbar {{ display: none; }}
  .nav-chip {{
    flex-shrink: 0; background: var(--card); border: 1px solid var(--border);
    color: var(--text-soft); font-size: 16px; font-weight: 500;
    padding: 10px 18px; border-radius: 22px; text-decoration: none;
    transition: all 0.2s; white-space: nowrap;
  }}
  .nav-chip:hover {{ background: var(--wood-deep); color: #FFF; border-color: var(--wood-deep); }}
  .nav-chip-count {{
    display: inline-block; background: var(--wood-light); color: var(--wood-deep);
    font-size: 14px; font-weight: 700; padding: 2px 9px; border-radius: 12px; margin-left: 8px;
  }}
  .nav-chip:hover .nav-chip-count {{ background: rgba(255,255,255,0.25); color: #FFF; }}
  .section {{ padding: 56px 0 30px; border-bottom: 1px dashed var(--border); }}
  .section:last-of-type {{ border-bottom: none; }}
  .section-header {{ display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 28px; flex-wrap: wrap; gap: 12px; }}
  .section-title {{ font-size: clamp(22px, 3.2vw, 30px); font-weight: 700; display: flex; align-items: center; gap: 12px; }}
  .section-title::before {{ content: ''; display: inline-block; width: 5px; height: 28px; background: var(--accent); border-radius: 3px; }}
  .section-meta {{ font-size: 16px; color: var(--text-muted); letter-spacing: 1px; }}
  .section-meta strong {{ color: var(--accent-deep); font-weight: 700; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 22px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 18px;
    box-shadow: var(--shadow); transition: all 0.3s; display: flex; flex-direction: column; overflow: hidden;
  }}
  .card:hover {{ transform: translateY(-6px); box-shadow: var(--shadow-hover); border-color: var(--wood-light); }}
  .card-image {{ width: 100%; height: 240px; background-size: cover; background-position: center; background-color: var(--bg-soft); border-bottom: 1px solid var(--border); position: relative; }}
  .card-image::after {{ content: ''; position: absolute; inset: 0; background: linear-gradient(180deg, transparent 50%, rgba(0,0,0,0.15) 100%); }}
  .card-content {{ padding: 22px; display: flex; flex-direction: column; flex: 1; }}
  .card-tagline {{ font-size: 17px; font-weight: 600; color: var(--accent-deep); margin-bottom: 14px; line-height: 1.5; min-height: 50px; }}
  .card-price-row {{ display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--bg-soft); gap: 10px; flex-wrap: wrap; }}
  .card-price {{ font-size: 42px; font-weight: 900; color: var(--accent-deep); line-height: 1; letter-spacing: -0.5px; }}
  .card-price-unit {{ font-size: 16px; color: var(--text-muted); margin-left: 5px; font-weight: 400; }}
  .card-floor {{ background: var(--bg-soft); color: var(--wood-deep); font-size: 17px; font-weight: 700; padding: 6px 14px; border-radius: 10px; }}
  .card-spec {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px 18px; margin-bottom: 18px; }}
  .spec-item {{ display: flex; flex-direction: column; gap: 3px; }}
  .spec-label {{ font-size: 14px; color: var(--text-muted); letter-spacing: 1px; }}
  .spec-value {{ font-size: 17px; color: var(--text); font-weight: 500; }}
  .spec-value.highlight {{ color: var(--wood-deep); font-weight: 700; }}
  .card-address {{ font-size: 16px; color: var(--text-soft); margin-bottom: 18px; padding-top: 16px; border-top: 1px solid var(--bg-soft); display: flex; align-items: flex-start; gap: 7px; line-height: 1.6; }}
  .card-address::before {{ content: '📍'; flex-shrink: 0; }}
  .card-cta {{ margin-top: auto; background: var(--wood-deep); color: #FFF; text-align: center; text-decoration: none; padding: 14px 18px; border-radius: 12px; font-size: 17px; font-weight: 700; letter-spacing: 1.5px; transition: all 0.2s; display: flex; align-items: center; justify-content: center; gap: 8px; }}
  .card-cta:hover {{ background: var(--accent); transform: translateY(-1px); }}
  .card-cta-arrow {{ transition: transform 0.2s; }}
  .card-cta:hover .card-cta-arrow {{ transform: translateX(4px); }}
  .footer {{ background: linear-gradient(135deg, #2C2620 0%, #3D352D 100%); color: #E8DFD2; padding: 60px 20px 36px; margin-top: 50px; }}
  .footer-content {{ max-width: 1200px; margin: 0 auto; text-align: center; }}
  .footer-title {{ font-size: 30px; font-weight: 700; color: #FFF; margin-bottom: 8px; letter-spacing: 6px; }}
  .footer-subtitle {{ font-size: 17px; color: var(--wood-light); margin-bottom: 32px; letter-spacing: 3px; }}
  .footer-contact {{ display: flex; justify-content: center; gap: 36px; flex-wrap: wrap; margin-bottom: 32px; }}
  .contact-item {{ display: flex; align-items: center; gap: 10px; font-size: 18px; color: #E8DFD2; text-decoration: none; transition: color 0.2s; }}
  .contact-item:hover {{ color: var(--accent); }}
  .contact-icon {{ font-size: 20px; }}
  .footer-license {{ font-size: 14px; color: var(--text-muted); letter-spacing: 1px; line-height: 2; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 24px; }}
  .footer-date {{ font-size: 13px; color: rgba(232,223,210,0.4); margin-top: 16px; letter-spacing: 2px; }}
  @media (max-width: 640px) {{
    .hero {{ padding: 50px 16px 36px; }}
    .hero-stats {{ gap: 28px; }}
    .hero-stat-num {{ font-size: 32px; }}
    .section {{ padding: 40px 0 22px; }}
    .grid {{ grid-template-columns: 1fr; gap: 16px; }}
    .card-image {{ height: 200px; }}
    .card-content {{ padding: 18px; }}
    .card-price {{ font-size: 36px; }}
    .card-tagline {{ font-size: 16px; min-height: auto; }}
    .footer-contact {{ flex-direction: column; gap: 16px; }}
    .footer-title {{ font-size: 26px; letter-spacing: 4px; }}
  }}
</style>
</head>
<body>

<section class="hero">
  <div class="hero-tag">{theme}</div>
  <h1>{total_count} 戶 客製整理</h1>
  <div class="for-client">給 {client_data["name"]} 的專屬精選 · {client_data["need"]}</div>
  <p class="summary">
    精選 {total_count} 個物件 · {community_count} 個社區<br>
    售價 {price_min:,} 萬 ~ {price_max:,} 萬
  </p>
  <div class="hero-stats">
    <div class="hero-stat"><div class="hero-stat-num">{total_count}</div><div class="hero-stat-label">精選戶數</div></div>
    <div class="hero-stat"><div class="hero-stat-num">{community_count}</div><div class="hero-stat-label">優質社區</div></div>
  </div>
</section>

<nav class="sticky-nav">
  <div class="nav-scroll">
    {nav_chips}
  </div>
</nav>

<div class="container">
{sections}
</div>

<footer class="footer">
  <div class="footer-content">
    <div class="footer-title">{signature}</div>
    <div class="footer-subtitle">{contact["company"]}</div>
    <div class="footer-contact">
      <a class="contact-item" href="tel:{contact["phone_raw"]}"><span class="contact-icon">📞</span><span>{contact["phone"]}</span></a>
      <a class="contact-item" href="{contact["line_url"]}" target="_blank"><span class="contact-icon">💬</span><span>LINE：{contact["line"]}</span></a>
      <a class="contact-item" href="{contact["ig_url"]}" target="_blank"><span class="contact-icon">📷</span><span>IG：{contact["ig"]}</span></a>
    </div>
    <div class="footer-license">
      不動產經紀人 {contact["broker_name"]} 證號 {contact["broker_license"]}<br>
      不動產營業員 {contact["agent_name"]} 證號 {contact["agent_license"]}<br>
      {contact["company_full"]}<br>
      本資訊以實際物件現況為準，最終以雙方議定條件為憑
    </div>
    <div class="footer-date">本頁產出日期：{today}</div>
  </div>
</footer>

<script>
(function() {{
  // 景泰本人排除 — 帶 ?admin=1 訪問會標記這台裝置為管理者，之後永遠不追蹤
  try {{
    var params = new URLSearchParams(location.search);
    if (params.get('admin') === '1') {{
      localStorage.setItem('teddy_admin', '1');
      // 清掉網址列的 ?admin=1 — 防止景泰不小心把預覽 URL 轉貼給客戶
      if (history.replaceState) {{
        history.replaceState(null, '', location.pathname);
      }}
      return;
    }}
    if (localStorage.getItem('teddy_admin') === '1') return;
  }} catch (e) {{}}

  var SHARE_ID = {json.dumps(client_data["share_id"])};
  var CLIENT_NAME = {json.dumps(client_data["name"])};
  var TRACK_API = 'https://teddy-share-app.vercel.app/api/track';
  var startTime = Date.now();
  var visitSent = false;

  function postTrack(payload) {{
    try {{
      fetch(TRACK_API, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(payload),
        keepalive: true
      }});
    }} catch (e) {{}}
  }}

  function trackVisit() {{
    if (visitSent) return;
    var duration = Math.round((Date.now() - startTime) / 1000);
    if (duration < 3) return;
    visitSent = true;
    postTrack({{
      client: CLIENT_NAME,
      share_id: SHARE_ID,
      duration: duration,
      url: location.href,
      referrer: document.referrer || ''
    }});
  }}

  function trackClick(slug, ycutUrl, name) {{
    // 點擊事件的「停留秒數」= 點擊當下他已在推薦頁讀多久（思考訊號）
    var elapsedSec = Math.round((Date.now() - startTime) / 1000);
    postTrack({{
      client: CLIENT_NAME,
      share_id: SHARE_ID,
      clicked_slug: slug,
      clicked_url: ycutUrl,
      clicked_name: name,
      duration: elapsedSec,
      url: location.href,
      referrer: document.referrer || ''
    }});
  }}

  // 綁定每張 card 的「看完整資訊」按鈕點擊事件
  document.querySelectorAll('.card-cta').forEach(function(link) {{
    link.addEventListener('click', function() {{
      var ycutUrl = link.getAttribute('href') || '';
      var m = ycutUrl.match(/x\\.ychouse\\.tw\\/(\\w+)/);
      if (!m) return;
      var slug = m[1];
      // 從同一張 card 裡撈 tagline 當「物件名稱」
      var card = link.closest('.card');
      var tagline = '';
      if (card) {{
        var t = card.querySelector('.card-tagline');
        if (t) tagline = (t.textContent || '').trim();
      }}
      trackClick(slug, ycutUrl, tagline);
    }});
  }});

  window.addEventListener('pagehide', trackVisit);
  window.addEventListener('beforeunload', trackVisit);
  // 也定期 ping 一次（讓還在閱讀的訪客也記到，避免關閉太快沒記到）
  setTimeout(trackVisit, 30000);
}})();
</script>

</body>
</html>
'''


# ============== GitHub Push ==============

def github_push(path, content, message, token):
    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(body).encode("utf-8"),
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


# ============== Flask App ==============

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/publish", methods=["POST", "OPTIONS"])
def publish_endpoint():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip() or "客戶"
        need = (body.get("need") or "").strip() or "找房需求"
        if not need.startswith("找房需求"):
            need = f"找房需求：{need}"
        text = body.get("urls_text", "")
        theme = (body.get("theme") or "").strip()
        signature = (body.get("signature") or "").strip()

        slugs = extract_urls(text)
        if not slugs:
            return jsonify({"error": "找不到任何 ycut 短網址（https://x.ychouse.tw/...）"}), 400

        properties = fetch_full_batch(slugs)
        if not properties:
            return jsonify({"error": "全部物件抓取失敗（可能 URL 已失效）"}), 400

        share_id = gen_share_id()
        client_data = {
            "name": name,
            "need": need,
            "share_id": share_id,
            "contact": DEFAULT_CONTACT,
            "theme": theme,
            "signature": signature,
        }
        html = gen_html(client_data, properties)

        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return jsonify({"error": "Server 缺 GITHUB_TOKEN 環境變數"}), 500

        try:
            github_push(
                f"{share_id}/index.html",
                html,
                f"add: {name} {len(properties)} 戶 ({share_id})",
                token,
            )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")[:300]
            return jsonify({"error": f"GitHub push 失敗 ({e.code}): {err_body}"}), 500

        return jsonify({
            "url": f"{PAGES_BASE_URL}/{share_id}/",
            "share_id": share_id,
            "count": len(properties),
            "client": name,
        })
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "ts": datetime.datetime.now().isoformat()})


# ============== Notion 訪問追蹤 ==============

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "")


# UA / 地點解析（把長字串轉人話，景泰看了才不會頭痛）
TW_CITIES = {
    "Taipei": "台北市", "New Taipei": "新北市", "Taoyuan": "桃園市",
    "Taichung": "台中市", "Tainan": "台南市", "Kaohsiung": "高雄市",
    "Keelung": "基隆市", "Hsinchu": "新竹", "Chiayi": "嘉義",
    "Changhua": "彰化縣", "Yunlin": "雲林縣", "Nantou": "南投縣",
    "Miaoli": "苗栗縣", "Yilan": "宜蘭縣", "Hualien": "花蓮縣",
    "Taitung": "台東縣", "Pingtung": "屏東縣", "Penghu": "澎湖縣",
    "Kinmen": "金門縣", "Lienchiang": "連江縣",
}

COUNTRIES = {
    "TW": "台灣", "US": "美國", "JP": "日本", "KR": "韓國",
    "CN": "中國", "HK": "香港", "SG": "新加坡", "MY": "馬來西亞",
}


def parse_ua(ua):
    if not ua:
        return ""
    if ua.startswith("curl/") or "bot" in ua.lower() or "spider" in ua.lower():
        return f"機器人 ({ua[:40]})"

    if "iPhone" in ua:
        os_name, device = "iPhone", "手機"
    elif "iPad" in ua:
        os_name, device = "iPad", "平板"
    elif "Android" in ua:
        os_name = "Android"
        device = "手機" if "Mobile" in ua else "平板"
    elif "Windows" in ua:
        os_name, device = "Windows", "桌機"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        os_name, device = "Mac", "桌機"
    elif "Linux" in ua:
        os_name, device = "Linux", "桌機"
    else:
        os_name, device = "", ""

    if "Line/" in ua:
        browser = "LINE 內建"
    elif "FBAN" in ua or "FBAV" in ua:
        browser = "FB 內建"
    elif "Instagram" in ua:
        browser = "IG 內建"
    elif "Edg/" in ua:
        m = re.search(r"Edg/(\d+)", ua)
        browser = f"Edge {m.group(1)}" if m else "Edge"
    elif "Chrome/" in ua:
        m = re.search(r"Chrome/(\d+)", ua)
        browser = f"Chrome {m.group(1)}" if m else "Chrome"
    elif "Firefox/" in ua:
        m = re.search(r"Firefox/(\d+)", ua)
        browser = f"Firefox {m.group(1)}" if m else "Firefox"
    elif "Safari/" in ua:
        m = re.search(r"Version/(\d+)", ua)
        browser = f"Safari {m.group(1)}" if m else "Safari"
    else:
        browser = ""

    parts = [p for p in (os_name, browser, device) if p]
    return " · ".join(parts) or ua[:60]


def parse_geo(city, country):
    city_zh = TW_CITIES.get(city, city) if city else ""
    country_zh = COUNTRIES.get(country, country) if country else ""
    return " · ".join(filter(None, [city_zh, country_zh]))


def notion_log_visit(client_name, share_id, user_agent, ip_geo, duration, page_url, referrer, clicked_slug="", clicked_url="", clicked_name=""):
    """寫一筆訪問記錄到 Notion DB（失敗就靜默吃掉，不影響客戶體驗）

    clicked_slug 有值 → 客戶點某個物件去看完整資訊（click 事件）
    clicked_slug 空 → 客戶開了客戶頁本身（visit 事件）
    """
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return False

    def text_prop(s):
        return {"rich_text": [{"text": {"content": (s or "")[:1900]}}]} if s else {"rich_text": []}

    # 「點擊物件」欄位：URL 類型，可直接點擊到 ycut
    if clicked_slug:
        target_url = (clicked_url or f"https://x.ychouse.tw/{clicked_slug}")[:1900]
        clicked_prop = {"url": target_url}
    else:
        clicked_prop = {"url": None}

    properties = {
        "客戶": {"title": [{"text": {"content": (client_name or "未知")[:200]}}]},
        "share_id": text_prop(share_id),
        "裝置": text_prop(user_agent),
        "IP 粗略地點": text_prop(ip_geo),
        "停留秒數": {"number": int(duration) if duration else 0},
        "referrer": text_prop(referrer),
        "點擊物件": clicked_prop,
    }
    if page_url:
        properties["訪問 URL"] = {"url": page_url[:1900]}

    body = {"parent": {"database_id": NOTION_DB_ID}, "properties": properties}
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status in (200, 201)
    except Exception as e:
        print(f"Notion log failed: {type(e).__name__}: {e}")
        return False


@app.route("/api/track", methods=["POST", "OPTIONS"])
def track_endpoint():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(silent=True) or {}
        client_name = (body.get("client") or "").strip()
        share_id = (body.get("share_id") or "").strip()
        duration = body.get("duration", 0)
        page_url = body.get("url", "")
        referrer = body.get("referrer", "")
        clicked_slug = (body.get("clicked_slug") or "").strip()
        clicked_url = (body.get("clicked_url") or "").strip()
        clicked_name = (body.get("clicked_name") or "").strip()

        # 從 Vercel 自動 header 拿訪客 IP 粗略地理位置
        city = request.headers.get("X-Vercel-IP-City", "")
        country = request.headers.get("X-Vercel-IP-Country", "")
        ip_geo = parse_geo(city, country)
        user_agent = parse_ua(request.headers.get("User-Agent", "")[:300])

        ok = notion_log_visit(client_name, share_id, user_agent, ip_geo, duration, page_url, referrer, clicked_slug, clicked_url, clicked_name)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# Serve frontend index.html at root（Vercel new Python runtime 把 / 也送進 app）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@app.route("/", methods=["GET"])
def serve_index():
    return send_from_directory(_REPO_ROOT, "index.html")


@app.route("/<path:filename>", methods=["GET"])
def serve_static(filename):
    """fallback static serving — favicon、其他靜態檔"""
    try:
        return send_from_directory(_REPO_ROOT, filename)
    except Exception:
        return ("Not Found", 404)
