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

def _is_no_parking_text(value):
    normalized = re.sub(r"[\s　:：/／,，.。()（）-]+", "", value or "")
    return normalized in {
        "無",
        "無車位",
        "無停車位",
        "無汽車位",
        "沒有車位",
        "沒有停車位",
    }


def _parking_fields(area_str, parking_type_raw, parking_num_raw):
    """車位判定 — 收緊：只有「明確證據」才算有車位，避免「無車位」物件誤標「含於主建」。

    明確證據（任一）：
    - area_str 含「含車位 X 坪」格式（parking_area_num > 0）
    - area_str 含「含車位」字樣（雖無坪數但有明確標示）
    parking_type / parking_num 欄位本身**不**作為「有車位」的依據（可能是雜訊或誤填）。
    """
    parking_type_raw = (parking_type_raw or "").strip()
    parking_num = (parking_num_raw or "").strip()
    area_has_parking_text = bool(re.search(r"(?<!不)含車位", area_str or ""))
    pm = re.search(r"(?<!不)含車位\s*([\d.]+)\s*坪", area_str or "")
    parking_area_num = float(pm.group(1)) if pm else 0.0

    no_parking = _is_no_parking_text(parking_type_raw) or _is_no_parking_text(parking_num)
    has_parking = (
        not no_parking
        and (parking_area_num > 0 or area_has_parking_text)
    )
    if not has_parking:
        return False, "無車位", "無車位"

    parking_parts = [
        x for x in (parking_type_raw, parking_num)
        if x and not _is_no_parking_text(x)
    ]
    parking = " ".join(parking_parts) or "含車位"
    if parking_area_num > 0:
        parking_area = f"{parking_area_num:g} 坪"
    elif area_has_parking_text:
        parking_area = "含於主建"
    else:
        parking_area = "—"  # 安全網：理論不會 reach（has_parking 條件已保證）
    return True, parking, parking_area


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

    parking_type = field("停車方式") or field("車位") or ""
    parking_num = field("車位編號") or ""
    has_parking, parking, parking_area = _parking_fields(area_str, parking_type, parking_num)

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
        "has_parking": has_parking,
        "parking": parking,
        "parking_area": parking_area,
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
    # 非貪婪 + lookahead：slug 結束於下個 URL 或非英數字符 — 支援 URL 連在一起貼不分隔
    # 例如：「https://x.ychouse.tw/abc123https://x.ychouse.tw/def456」也能正確切成 abc123 + def456
    found = re.findall(r"https?://x\.ychouse\.tw/([A-Za-z0-9]+?)(?=https?://|[^A-Za-z0-9]|$)", text or "")
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
    district = parse_district(p.get("address", ""))
    tier_key = price_to_tier_key(p.get("price", 0))
    age_key = age_to_tier_key(p.get("age"))
    community = (p.get("community_display") or "").strip()
    has_parking = p.get("has_parking")
    if has_parking is None:
        has_parking = not (
            _is_no_parking_text(p.get("parking", ""))
            or _is_no_parking_text(p.get("parking_area", ""))
        )
    price_unit = "萬 含車位" if has_parking else "萬"
    unit_price = unit_price_per_main(p.get("price"), p.get("main_area"))
    unit_price_html = (
        f'<div class="card-unit-price">單坪 <strong>{unit_price:g}</strong> 萬 / 主建坪</div>'
        if unit_price else ''
    )
    img_html = (
        f'<img src="{img}" loading="lazy" decoding="async" alt="" />'
        if img else ''
    )
    return f'''
    <div class="card" data-district="{district}" data-price-tier="{tier_key}" data-age-tier="{age_key}" data-community="{community}">
      <div class="card-image">{img_html}</div>
      <div class="card-content">
        <div class="card-tagline">{tagline}</div>
        <div class="card-price-row">
          <div><span class="card-price">{p["price"]:,}</span><span class="card-price-unit">{price_unit}</span></div>
          <div class="card-floor">{p["floor"]}F / {p["floor_total"]}F</div>
        </div>
        {unit_price_html}
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


def parse_district(address):
    """從地址抓行政區（台灣），「台中市北屯區XX路」→「北屯區」/「台中市北區XX路」→「北區」"""
    if not address:
        return "其他"
    # 非貪婪：避免「台中市北區」整串吃成 1 個 match（要切成「台中市」+「北區」兩個）
    matches = re.findall(r'[一-龥]{1,4}?[區鄉鎮市]', address)
    if len(matches) >= 2:
        return matches[1]  # 第一個是縣市，第二個是區
    if matches:
        return matches[0]
    return "其他"


# 價格段 — 一致用於 client page 篩選 + dashboard 分析
PRICE_TIERS = [
    ("lt800", "< 800 萬", 0, 800),
    ("800-1200", "800-1200 萬", 800, 1200),
    ("1200-1500", "1200-1500 萬", 1200, 1500),
    ("1500-2000", "1500-2000 萬", 1500, 2000),
    ("gt2000", "> 2000 萬", 2000, 99999),
]

# 屋齡段 — BOSS 任務分段（市場精選模式核心 filter）
AGE_TIERS = [
    ("lt15", "< 15 年", 0, 15),
    ("15-20", "15-20 年", 15, 20),
    ("20-25", "20-25 年", 20, 25),
    ("25-30", "25-30 年", 25, 30),
    ("gt30", "> 30 年", 30, 999),
]


def price_to_tier_key(price):
    """價格 → tier key (給 data-price-tier 用)"""
    if not price:
        return "unknown"
    for key, _, lo, hi in PRICE_TIERS:
        if lo <= price < hi:
            return key
    return "unknown"


def age_to_tier_key(age):
    """屋齡 → tier key (給 data-age-tier 用)"""
    if age is None or age == "":
        return "unknown"
    try:
        a = float(age)
    except (TypeError, ValueError):
        return "unknown"
    for key, _, lo, hi in AGE_TIERS:
        if lo <= a < hi:
            return key
    return "unknown"


def unit_price_per_main(price, main_area):
    """單坪價（主建）= 總價 / 主建坪。回傳 None 表示無法計算。"""
    try:
        p = float(price or 0)
        m = float(main_area or 0)
        if p > 0 and m > 0:
            return round(p / m, 2)
    except (TypeError, ValueError):
        pass
    return None


def community_subsection_html(community, items):
    """單一社區的 sub-section（在區裡面）"""
    items_sorted = sorted(items, key=lambda x: x["price"])
    prices = [x["price"] for x in items_sorted]
    c_range = f"{prices[0]:,} 萬" if len(prices) == 1 else f"{prices[0]:,} ~ {prices[-1]:,} 萬"
    cards = "\n".join(card_html(p) for p in items_sorted)
    district = parse_district(items_sorted[0].get("address", "")) if items_sorted else "其他"
    return f'''
    <div class="community-sub" data-district="{district}" data-community="{community}">
      <div class="community-header">
        <h3 class="community-title">{community}</h3>
        <div class="community-meta">{len(items_sorted)} 戶 · {c_range}</div>
      </div>
      <div class="grid">{cards}
      </div>
    </div>'''


def district_section_html(district, communities_dict, anchor):
    """單一行政區的 section，內含多個社區 sub-section"""
    all_props = [p for comm in communities_dict.values() for p in comm]
    total_count = len(all_props)
    prices = sorted(p["price"] for p in all_props)
    price_range = f"{prices[0]:,} 萬" if len(prices) == 1 else f"{prices[0]:,} ~ {prices[-1]:,} 萬"
    ages = sorted({p["age"] for p in all_props if p.get("age")})
    age_text = ""
    if ages:
        age_text = f"屋齡 {ages[0]} 年" if len(ages) == 1 else f"屋齡 {ages[0]}~{ages[-1]} 年"

    # 社區按該社區最低價排序
    community_order = sorted(
        communities_dict.keys(),
        key=lambda c: min(p["price"] for p in communities_dict[c])
    )
    sub_sections = "\n".join(
        community_subsection_html(c, communities_dict[c]) for c in community_order
    )

    meta_parts = [f"<strong>{total_count} 戶</strong>", price_range]
    if age_text:
        meta_parts.append(age_text)
    meta_str = " · ".join(meta_parts)

    # 社區名稱預覽（讓 header 不空虛）
    community_chips = " · ".join(community_order)

    # 價格段分布（讓客戶一眼看出這區哪個預算最多選擇）— 也是可點擊的篩選器
    tier_counts = []
    for key, label, lo, hi in PRICE_TIERS:
        n = sum(1 for p in all_props if lo <= p.get("price", 0) < hi)
        if n > 0:
            tier_counts.append(
                f'<a class="price-tier-chip filter-chip" data-filter-price="{key}">'
                f'{label} <strong>{n}</strong></a>'
            )
    price_tier_html = ""
    if len(tier_counts) >= 2:
        price_tier_html = f'<div class="district-price-tiers">💰 點預算篩選：{" ".join(tier_counts)}</div>'

    chips_html = (
        f'<div class="district-communities">含 {len(community_order)} 個社區：{community_chips}</div>'
        + price_tier_html
    )

    return f'''
<section class="district-section" id="{anchor}" data-district="{district}">
  <div class="district-header">
    <h2 class="district-title">{district}</h2>
    <div class="district-meta">{meta_str}</div>
  </div>
  {chips_html}
  {sub_sections}
</section>'''


def gen_html(client_data, properties):
    contact = client_data.get("contact", DEFAULT_CONTACT)
    theme = (client_data.get("theme") or "景泰精選").strip()
    signature = (client_data.get("signature") or contact.get("agent_name") or "陳景泰").strip()

    # 兩層分組：行政區 → 社區
    districts = {}
    for p in properties:
        district = parse_district(p.get("address", ""))
        community = p.get("community_display", "未知社區")
        if district not in districts:
            districts[district] = {}
        if community not in districts[district]:
            districts[district][community] = []
        districts[district][community].append(p)

    # 行政區按該區最低價排序
    district_order = sorted(
        districts.keys(),
        key=lambda d: min(p["price"] for comm in districts[d].values() for p in comm)
    )
    district_anchors = {}
    for i, d in enumerate(district_order):
        anchor_id = "d-" + re.sub(r"[^a-z0-9一-龥]", "", d.lower()) + str(i)
        district_anchors[d] = anchor_id

    # nav chips 按行政區（變成篩選器，點了 → 只顯示那區）
    nav_chips = "\n    ".join(
        f'<a class="nav-chip filter-chip" data-filter-district="{d}">{d}'
        f'<span class="nav-chip-count">{sum(len(c) for c in districts[d].values())}</span></a>'
        for d in district_order
    )

    # 全部價格段 chip 列（給 sticky-nav 第二行）
    all_price_chips = []
    for key, label, lo, hi in PRICE_TIERS:
        n = sum(1 for d in districts.values() for c in d.values() for p in c if lo <= p.get("price", 0) < hi)
        if n > 0:
            all_price_chips.append(
                f'<a class="nav-chip price-nav-chip filter-chip" data-filter-price="{key}">'
                f'{label}<span class="nav-chip-count">{n}</span></a>'
            )
    price_filter_chips = "\n    ".join(all_price_chips)

    # 屋齡段 chip 列（給 sticky-nav 第三行）— BOSS 任務市場精選核心 filter
    all_age_chips = []
    for key, label, lo, hi in AGE_TIERS:
        n = 0
        for d in districts.values():
            for c in d.values():
                for p in c:
                    try:
                        a = float(p.get("age") or 0)
                    except (TypeError, ValueError):
                        continue
                    if lo <= a < hi:
                        n += 1
        if n > 0:
            all_age_chips.append(
                f'<a class="nav-chip age-nav-chip filter-chip" data-filter-age="{key}">'
                f'{label}<span class="nav-chip-count">{n}</span></a>'
            )
    age_filter_chips = "\n    ".join(all_age_chips)
    has_age_filter = len(all_age_chips) >= 2  # 只有 1 段就不顯示 filter（沒意義）

    sections = "\n".join(
        district_section_html(d, districts[d], district_anchors[d])
        for d in district_order
    )

    total_count = len(properties)
    community_count = sum(len(c) for c in districts.values())
    district_count = len(districts)
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
  .top-contact {{
    background: linear-gradient(180deg, #FFFFFF 0%, var(--bg-soft) 100%);
    padding: 32px 20px; border-bottom: 1px solid var(--border);
    position: relative;
  }}
  .top-contact::before {{
    content: ''; position: absolute; top: 0; left: 50%; transform: translateX(-50%);
    width: 60px; height: 3px; background: var(--accent); border-radius: 0 0 4px 4px;
  }}
  .top-contact-inner {{ max-width: 680px; margin: 0 auto; text-align: center; }}
  .top-contact-name {{
    font-size: 20px; font-weight: 700; color: var(--text); letter-spacing: 5px;
    margin-bottom: 6px;
  }}
  .top-contact-role {{
    font-size: 13px; color: var(--text-muted); letter-spacing: 2px; font-weight: 500;
    display: block; margin-top: 2px;
  }}
  .top-contact-btns {{
    display: flex; gap: 12px; margin: 20px auto 0; flex-wrap: wrap;
    justify-content: center; max-width: 560px;
  }}
  .top-cta {{
    flex: 1 1 220px; padding: 16px 22px; border-radius: 14px;
    font-size: 17px; font-weight: 700; text-decoration: none; letter-spacing: 1.5px;
    transition: all 0.2s; box-shadow: 0 3px 12px rgba(139,115,85,0.12);
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  }}
  .top-cta:hover {{ transform: translateY(-2px); box-shadow: 0 8px 22px rgba(139,115,85,0.22); }}
  .top-cta.primary {{ background: var(--wood-deep); color: #FFF; border: 1.5px solid var(--wood-deep); }}
  .top-cta.primary:hover {{ background: var(--accent-deep); border-color: var(--accent-deep); }}
  .top-cta.line {{ background: #06C755; color: #FFF; border: 1.5px solid #06C755; }}
  .top-cta.line:hover {{ background: #05a847; border-color: #05a847; }}
  .top-contact-hint {{
    font-size: 14px; color: var(--text-soft); margin-top: 16px;
    letter-spacing: 0.5px; font-weight: 500;
  }}
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
  .nav-chip.active {{ background: var(--accent-deep); color: #FFF; border-color: var(--accent-deep); }}
  .nav-chip.active .nav-chip-count {{ background: rgba(255,255,255,0.3); color: #FFF; }}
  .nav-label {{ font-size: 13px; color: var(--text-muted); letter-spacing: 1.5px; padding: 10px 4px; flex-shrink: 0; font-weight: 600; }}
  .price-nav-chip {{ background: rgba(201,120,90,0.06); border-color: rgba(201,120,90,0.4); }}
  .age-nav-chip {{ background: rgba(139,115,85,0.06); border-color: rgba(139,115,85,0.4); color: var(--wood-deep); }}
  .age-nav-chip:hover {{ background: var(--wood-deep); color: #FFF; border-color: var(--wood-deep); }}
  .age-nav-chip.active {{ background: var(--wood-deep); color: #FFF; border-color: var(--wood-deep); }}
  .filter-chip {{ cursor: pointer; user-select: none; }}
  .filter-reset {{ background: var(--accent); color: #FFF; border-color: var(--accent); }}
  .filter-reset:hover {{ background: var(--accent-deep); border-color: var(--accent-deep); }}
  .filter-summary {{
    text-align: center; padding: 10px 16px; font-size: 14px; color: var(--accent-deep);
    background: var(--bg-soft); border-radius: 12px; margin: 10px 20px 0;
    font-weight: 600; letter-spacing: 0.5px;
  }}
  .price-tier-chip {{
    display: inline-block; padding: 4px 12px; margin: 2px;
    background: rgba(201,120,90,0.15); border: 1px solid rgba(201,120,90,0.3);
    border-radius: 16px; color: var(--accent-deep); text-decoration: none;
    font-size: 14px; font-weight: 600; transition: all 0.15s;
    cursor: pointer; user-select: none;
  }}
  .price-tier-chip:hover {{ background: var(--accent); color: #FFF; border-color: var(--accent); }}
  .price-tier-chip.active {{ background: var(--accent-deep); color: #FFF; border-color: var(--accent-deep); }}
  .price-tier-chip.active strong {{ color: #FFF; }}
  /* 行政區 (top-level section) */
  .district-section {{ padding: 50px 0 20px; border-bottom: 2px solid var(--border); }}
  .district-section:last-of-type {{ border-bottom: none; padding-bottom: 24px; }}
  .district-header {{
    display: flex; align-items: baseline; justify-content: space-between;
    margin-bottom: 26px; flex-wrap: wrap; gap: 12px;
    padding: 18px 22px;
    background: linear-gradient(90deg, var(--bg-soft) 0%, rgba(242,237,228,0.3) 100%);
    border-radius: 14px; border-left: 6px solid var(--accent);
  }}
  .district-title {{
    font-size: clamp(24px, 4vw, 34px); font-weight: 900; letter-spacing: 2px;
    color: var(--text); display: flex; align-items: center; gap: 10px;
  }}
  .district-title::before {{ content: '🏷️'; font-size: 0.85em; }}
  .district-meta {{ font-size: 16px; color: var(--text-soft); letter-spacing: 1px; font-weight: 500; }}
  .district-meta strong {{ color: var(--accent-deep); font-weight: 800; font-size: 18px; }}
  .district-communities {{
    font-size: 14px; color: var(--text-soft); letter-spacing: 0.5px;
    margin: 14px 4px 4px 4px; padding: 10px 16px;
    background: rgba(217,199,176,0.18); border-radius: 10px;
    font-weight: 500; line-height: 1.7;
  }}
  .district-price-tiers {{
    font-size: 14px; color: var(--accent-deep); letter-spacing: 0.5px;
    margin: 8px 4px 4px 4px; padding: 10px 16px;
    background: rgba(201,120,90,0.10); border-radius: 10px;
    font-weight: 500; line-height: 1.7;
  }}
  .district-price-tiers strong {{ color: var(--accent-deep); font-weight: 800; }}

  /* 社區 (sub-section under district) */
  .community-sub {{ padding: 22px 0 14px; }}
  .community-header {{
    display: flex; align-items: baseline; justify-content: space-between;
    margin-bottom: 18px; flex-wrap: wrap; gap: 10px;
  }}
  .community-title {{
    font-size: clamp(19px, 3vw, 24px); font-weight: 700; color: var(--text);
    display: flex; align-items: center; gap: 12px;
  }}
  .community-title::before {{
    content: ''; display: inline-block; width: 4px; height: 22px;
    background: var(--wood-light); border-radius: 2px;
  }}
  .community-meta {{ font-size: 14px; color: var(--text-muted); letter-spacing: 1px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 22px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 18px;
    box-shadow: var(--shadow); transition: all 0.3s; display: flex; flex-direction: column; overflow: hidden;
  }}
  .card:hover {{ transform: translateY(-6px); box-shadow: var(--shadow-hover); border-color: var(--wood-light); }}
  .card-image {{ width: 100%; height: 240px; background-color: var(--bg-soft); border-bottom: 1px solid var(--border); position: relative; overflow: hidden; }}
  .card-image img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
  .card-image::after {{ content: ''; position: absolute; inset: 0; background: linear-gradient(180deg, transparent 50%, rgba(0,0,0,0.15) 100%); pointer-events: none; }}
  .card-content {{ padding: 22px; display: flex; flex-direction: column; flex: 1; }}
  .card-tagline {{ font-size: 17px; font-weight: 600; color: var(--accent-deep); margin-bottom: 14px; line-height: 1.5; min-height: 50px; }}
  .card-price-row {{ display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--bg-soft); gap: 10px; flex-wrap: wrap; }}
  .card-price {{ font-size: 42px; font-weight: 900; color: var(--accent-deep); line-height: 1; letter-spacing: -0.5px; }}
  .card-price-unit {{ font-size: 16px; color: var(--text-muted); margin-left: 5px; font-weight: 400; }}
  .card-unit-price {{
    background: rgba(201,120,90,0.10); color: var(--accent-deep);
    font-size: 15px; font-weight: 600; padding: 8px 14px; border-radius: 10px;
    margin-bottom: 16px; letter-spacing: 0.5px; display: flex; align-items: center; gap: 6px;
  }}
  .card-unit-price strong {{ font-size: 19px; font-weight: 900; }}
  .card-unit-price::before {{ content: '💎'; font-size: 14px; }}
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
  .back-to-top {{
    position: fixed; bottom: 24px; right: 24px; width: 52px; height: 52px;
    border-radius: 50%; background: var(--wood-deep); color: #FFF;
    border: 2px solid #FFF; font-size: 24px; font-weight: 700; cursor: pointer;
    box-shadow: 0 6px 18px rgba(139,115,85,0.35); z-index: 200;
    opacity: 0; pointer-events: none; transition: all 0.25s;
    display: flex; align-items: center; justify-content: center;
  }}
  .back-to-top.show {{ opacity: 1; pointer-events: auto; }}
  .back-to-top:hover {{ background: var(--accent); transform: translateY(-3px); box-shadow: 0 10px 24px rgba(139,115,85,0.5); }}
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
    精選 {total_count} 個物件 · {district_count} 區 · {community_count} 個社區<br>
    售價 {price_min:,} 萬 ~ {price_max:,} 萬
  </p>
  <div class="hero-stats">
    <div class="hero-stat"><div class="hero-stat-num">{total_count}</div><div class="hero-stat-label">精選戶數</div></div>
    <div class="hero-stat"><div class="hero-stat-num">{district_count}</div><div class="hero-stat-label">行政區</div></div>
    <div class="hero-stat"><div class="hero-stat-num">{community_count}</div><div class="hero-stat-label">優質社區</div></div>
  </div>
</section>

<section class="top-contact">
  <div class="top-contact-inner">
    <div class="top-contact-name">陳景泰　<span class="top-contact-role">{contact["company"]}</span></div>
    <div class="top-contact-btns">
      <a class="top-cta primary" href="tel:{contact["phone_raw"]}">📞 {contact["phone"]}</a>
      <a class="top-cta line" href="{contact["line_url"]}" target="_blank" rel="noopener">💬 LINE 我（{contact["line"]}）</a>
    </div>
    <div class="top-contact-hint">看到喜歡的物件 → 直接打給我或 LINE 我，幫你約看 ✨</div>
  </div>
</section>

<nav class="sticky-nav">
  <div class="nav-scroll">
    <span class="nav-label">🏷️ 區域</span>
    <a class="nav-chip filter-chip filter-reset" data-filter-reset="1" style="display:none">✕ 清除</a>
    {nav_chips}
  </div>
  <div class="nav-scroll" style="margin-top: 8px;">
    <span class="nav-label">💰 預算</span>
    {price_filter_chips}
  </div>
  {'<div class="nav-scroll" style="margin-top: 8px;"><span class="nav-label">🏠 屋齡</span>' + age_filter_chips + '</div>' if has_age_filter else ''}
  <div class="filter-summary" id="filter-summary" style="display:none"></div>
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

<button class="back-to-top" id="back-to-top" aria-label="回到頂部">↑</button>

<script>
(function() {{
  // 景泰本人排除 + 解除標記機制（注意：admin 模式仍要讓 filter / 互動 JS 跑，只是不發追蹤）
  var IS_ADMIN = false;
  try {{
    var params = new URLSearchParams(location.search);

    // ?clear_admin=1 → 解除 admin 標記（給誤寄 ?admin=1 URL 給客戶後挽救用）
    if (params.get('clear_admin') === '1') {{
      localStorage.removeItem('teddy_admin');
      if (history.replaceState) {{
        history.replaceState(null, '', location.pathname);
      }}
    }}

    // ?admin=1 → 標記這台裝置為管理者
    if (params.get('admin') === '1') {{
      localStorage.setItem('teddy_admin', '1');
      if (history.replaceState) {{
        history.replaceState(null, '', location.pathname);
      }}
    }}

    IS_ADMIN = (localStorage.getItem('teddy_admin') === '1');
  }} catch (e) {{}}

  var SHARE_ID = {json.dumps(client_data["share_id"])};
  var CLIENT_NAME = {json.dumps(client_data["name"])};
  var TRACK_API = 'https://teddy-share-app.vercel.app/api/track';
  var startTime = Date.now();
  var visitSent = false;

  function postTrack(payload) {{
    if (IS_ADMIN) return;  // admin 不發追蹤，但 filter / 互動 JS 仍正常運作
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

  function trackCta(ctaType) {{
    var elapsedSec = Math.round((Date.now() - startTime) / 1000);
    postTrack({{
      client: CLIENT_NAME,
      share_id: SHARE_ID,
      cta_type: ctaType,
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

  // 綁定上方 CTA 按鈕（電話 + LINE）— 真正轉換訊號
  document.querySelectorAll('.top-cta.primary, .card-cta-phone').forEach(function(el) {{
    el.addEventListener('click', function() {{ trackCta('phone'); }});
  }});
  document.querySelectorAll('.top-cta.line, .card-cta-line').forEach(function(el) {{
    el.addEventListener('click', function() {{ trackCta('line'); }});
  }});

  // ============== 區域 + 預算 + 屋齡 篩選邏輯 ==============
  var activeDistrict = null;
  var activePrice = null;
  var activeAge = null;

  function applyFilters() {{
    var visibleCount = 0;
    document.querySelectorAll('.card').forEach(function(card) {{
      var d = card.dataset.district;
      var p = card.dataset.priceTier;
      var a = card.dataset.ageTier;
      var match = (
        (!activeDistrict || activeDistrict === d) &&
        (!activePrice || activePrice === p) &&
        (!activeAge || activeAge === a)
      );
      card.style.display = match ? '' : 'none';
      if (match) visibleCount++;
    }});
    // 隱藏空的社區 sub-section
    document.querySelectorAll('.community-sub').forEach(function(s) {{
      var v = s.querySelectorAll('.card[style=""], .card:not([style])').length;
      s.style.display = v > 0 ? '' : 'none';
    }});
    // 隱藏空的區段
    document.querySelectorAll('.district-section').forEach(function(s) {{
      var v = s.querySelectorAll('.card[style=""], .card:not([style])').length;
      s.style.display = v > 0 ? '' : 'none';
    }});
    // 更新 chip active 狀態
    document.querySelectorAll('[data-filter-district]').forEach(function(c) {{
      c.classList.toggle('active', c.dataset.filterDistrict === activeDistrict);
    }});
    document.querySelectorAll('[data-filter-price]').forEach(function(c) {{
      c.classList.toggle('active', c.dataset.filterPrice === activePrice);
    }});
    document.querySelectorAll('[data-filter-age]').forEach(function(c) {{
      c.classList.toggle('active', c.dataset.filterAge === activeAge);
    }});
    // Reset 按鈕 + summary 顯示
    var any = activeDistrict || activePrice || activeAge;
    var resetBtn = document.querySelector('[data-filter-reset]');
    if (resetBtn) resetBtn.style.display = any ? '' : 'none';
    var summary = document.getElementById('filter-summary');
    if (summary) {{
      if (any) {{
        var parts = [];
        if (activeDistrict) parts.push('區域：' + activeDistrict);
        if (activePrice) {{
          var pchip = document.querySelector('[data-filter-price="' + activePrice + '"]');
          parts.push('預算：' + (pchip ? pchip.textContent.replace(/\\d+$/, '').trim() : activePrice));
        }}
        if (activeAge) {{
          var achip = document.querySelector('[data-filter-age="' + activeAge + '"]');
          parts.push('屋齡：' + (achip ? achip.textContent.replace(/\\d+$/, '').trim() : activeAge));
        }}
        summary.textContent = '🔍 ' + parts.join(' + ') + ' → ' + visibleCount + ' 戶符合';
        summary.style.display = '';
      }} else {{
        summary.style.display = 'none';
      }}
    }}
  }}

  document.querySelectorAll('[data-filter-district]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var d = this.dataset.filterDistrict;
      activeDistrict = activeDistrict === d ? null : d;
      applyFilters();
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }});
  }});
  document.querySelectorAll('[data-filter-price]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var p = this.dataset.filterPrice;
      activePrice = activePrice === p ? null : p;
      applyFilters();
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }});
  }});
  document.querySelectorAll('[data-filter-age]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var a = this.dataset.filterAge;
      activeAge = activeAge === a ? null : a;
      applyFilters();
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }});
  }});
  document.querySelectorAll('[data-filter-reset]').forEach(function(btn) {{
    btn.addEventListener('click', function(e) {{
      e.preventDefault();
      activeDistrict = null;
      activePrice = null;
      activeAge = null;
      applyFilters();
    }});
  }});

  // 回到頂部按鈕
  var backBtn = document.getElementById('back-to-top');
  if (backBtn) {{
    window.addEventListener('scroll', function() {{
      if (window.scrollY > 400) {{
        backBtn.classList.add('show');
      }} else {{
        backBtn.classList.remove('show');
      }}
    }});
    backBtn.addEventListener('click', function() {{
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }});
  }}

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

def github_push(path, content, message, token, update=False):
    """PUT contents to GitHub. update=True 時先抓 sha，可覆蓋既有檔案。"""
    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    sha = None
    if update:
        try:
            req_get = urllib.request.Request(
                api_url,
                method="GET",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req_get, timeout=10) as r:
                sha = json.loads(r.read().decode("utf-8")).get("sha")
        except Exception:
            sha = None

    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha

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

        # 寫物件快照到 Notion（不阻塞主流程，失敗也不影響 client 回應）
        try:
            notion_log_snapshot(share_id, name, properties)
        except Exception as e:
            print(f"snapshot logging error: {e}")

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


def notion_log_snapshot(share_id, client_name, properties_list):
    """為某個 share 把所有物件當下資料存進 Notion 當快照（事件類型=snapshot）

    用途：物件之後賣掉、ycut 連結 404，dashboard 仍可用快照還原物件名稱+縮圖
    """
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return 0

    def text_prop(s):
        return {"rich_text": [{"text": {"content": (s or "")[:1900]}}]} if s else {"rich_text": []}

    written = 0
    for p in properties_list:
        slug = p.get("slug", "")
        if not slug:
            continue
        target_url = f"https://x.ychouse.tw/{slug}"
        # 摘要：北屯區 · 建興大樓 950萬 3F/7 30坪 12年
        district = parse_district(p.get("address", ""))
        summary_parts = [
            district if district != "其他" else "",
            (p.get("community_display") or "").strip(),
            f"{p['price']}萬" if p.get("price") else "",
            f"{p.get('floor')}F/{p.get('floor_total')}" if p.get("floor") else "",
            f"{p.get('area')}坪" if p.get("area") else "",
            f"{p.get('age')}年" if p.get("age") else "",
        ]
        summary = " · ".join(s for s in summary_parts if s)

        properties = {
            "客戶": {"title": [{"text": {"content": (client_name or "未知")[:200]}}]},
            "share_id": text_prop(share_id),
            "點擊物件": {"url": target_url[:1900]},
            "物件摘要": text_prop(summary),
            "物件首圖": {"url": (p.get("og_image") or "")[:1900] or None},
            "事件類型": {"select": {"name": "snapshot"}},
        }
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
                if r.status in (200, 201):
                    written += 1
        except Exception as e:
            print(f"snapshot write failed for {slug}: {e}")
    return written


def notion_log_visit(client_name, share_id, user_agent, ip_geo, duration, page_url, referrer, clicked_slug="", clicked_url="", clicked_name="", cta_type=""):
    """寫一筆訪問記錄到 Notion DB（失敗就靜默吃掉，不影響客戶體驗）

    cta_type 有值 → 客戶點 CTA 按鈕（phone/line）
    clicked_slug 有值 → 客戶點某個物件去看完整資訊（click 事件）
    都沒有 → 客戶開了客戶頁本身（visit 事件）
    """
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return False

    def text_prop(s):
        return {"rich_text": [{"text": {"content": (s or "")[:1900]}}]} if s else {"rich_text": []}

    # 事件類型優先順序：cta > click > visit
    summary = ""
    if cta_type:
        event_type = "cta"
        if cta_type == "phone":
            summary = "📞 電話 CTA 點擊"
        elif cta_type == "line":
            summary = "💬 LINE CTA 點擊"
        else:
            summary = f"CTA: {cta_type}"
        clicked_prop = {"url": None}
    elif clicked_slug:
        target_url = (clicked_url or f"https://x.ychouse.tw/{clicked_slug}")[:1900]
        clicked_prop = {"url": target_url}
        event_type = "click"
    else:
        clicked_prop = {"url": None}
        event_type = "visit"

    properties = {
        "客戶": {"title": [{"text": {"content": (client_name or "未知")[:200]}}]},
        "share_id": text_prop(share_id),
        "裝置": text_prop(user_agent),
        "IP 粗略地點": text_prop(ip_geo),
        "停留秒數": {"number": int(duration) if duration else 0},
        "referrer": text_prop(referrer),
        "點擊物件": clicked_prop,
        "事件類型": {"select": {"name": event_type}},
    }
    if summary:
        properties["物件摘要"] = text_prop(summary)
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
        cta_type = (body.get("cta_type") or "").strip()

        # 從 Vercel 自動 header 拿訪客 IP 粗略地理位置
        city = request.headers.get("X-Vercel-IP-City", "")
        country = request.headers.get("X-Vercel-IP-Country", "")
        ip_geo = parse_geo(city, country)
        user_agent = parse_ua(request.headers.get("User-Agent", "")[:300])

        ok = notion_log_visit(client_name, share_id, user_agent, ip_geo, duration, page_url, referrer, clicked_slug, clicked_url, clicked_name, cta_type)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ============== Stats Dashboard ==============

def notion_query_all(limit=1000):
    """Fetch all rows from Notion DB, paginated. Returns raw page objects."""
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return []
    results = []
    cursor = None
    while True:
        body = {"page_size": 100, "sorts": [{"timestamp": "created_time", "direction": "descending"}]}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"Notion query failed: {e}")
            break
        results.extend(data.get("results", []))
        if len(results) >= limit or not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results[:limit]


def _row_field(row, prop_name, kind):
    """Extract one value from a Notion row property"""
    p = row.get("properties", {}).get(prop_name, {})
    if kind == "title":
        arr = p.get("title", [])
        return arr[0].get("plain_text", "") if arr else ""
    if kind == "rich_text":
        arr = p.get("rich_text", [])
        return arr[0].get("plain_text", "") if arr else ""
    if kind == "url":
        return p.get("url") or ""
    if kind == "number":
        return p.get("number") or 0
    if kind == "select":
        s = p.get("select")
        return s.get("name", "") if s else ""
    return ""


def _normalize_property_url(url):
    """把舊資料的 slug-only 統一成完整 ycut URL"""
    if not url:
        return ""
    if url.startswith("http"):
        return url
    return f"https://x.ychouse.tw/{url}"


def _slug_from_url(url):
    """從 URL 抓 slug，失敗回傳完整 URL 當顯示文字"""
    if not url:
        return ""
    m = re.search(r'/([A-Za-z0-9_-]+)/?$', url)
    return m.group(1) if m else url


def parse_property_meta(summary):
    """從物件摘要 '北屯區 · 建興大樓 980萬 3F/7 30坪 12年' 抽出屬性"""
    if not summary:
        return {}
    meta = {}
    # 非貪婪 — 跟 parse_district 同邏輯
    m = re.search(r'([一-龥]{1,4}?[區鄉鎮市])', summary)
    if m:
        meta["district"] = m.group(1)
    m = re.search(r'(\d+)\s*萬', summary)
    if m:
        meta["price"] = int(m.group(1))
    m = re.search(r'([\d.]+)\s*坪', summary)
    if m:
        meta["area"] = float(m.group(1))
    m = re.search(r'([\d.]+)\s*年', summary)
    if m:
        meta["age"] = float(m.group(1))
    return meta


def compute_stats(rows):
    """Aggregate stats from raw Notion rows"""
    from datetime import datetime, timedelta, timezone

    parsed = []
    for r in rows:
        parsed.append({
            "client": _row_field(r, "客戶", "title") or "未知",
            "share_id": _row_field(r, "share_id", "rich_text"),
            "device": _row_field(r, "裝置", "rich_text"),
            "ip_geo": _row_field(r, "IP 粗略地點", "rich_text"),
            "duration": _row_field(r, "停留秒數", "number"),
            "clicked_url": _normalize_property_url(_row_field(r, "點擊物件", "url")),
            "summary": _row_field(r, "物件摘要", "rich_text"),
            "image": _row_field(r, "物件首圖", "url"),
            "event_type": _row_field(r, "事件類型", "select"),
            "page_url": _row_field(r, "訪問 URL", "url"),
            "referrer": _row_field(r, "referrer", "rich_text"),
            "time": r.get("created_time", ""),
        })

    # 物件快照表（URL → {summary, image}），用於補強 click 事件的物件資訊
    # snapshot + backfill 都當 snapshot 處理
    # parsed 已按建立時間 desc 排序 → 最新的會先被處理 → not in snapshots 才設值 = 最新覆蓋舊
    snapshots = {}
    for v in parsed:
        et = v["event_type"]
        if et in ("snapshot", "backfill") or (not et and v["summary"]):
            url = v["clicked_url"]
            if url and url not in snapshots:
                snapshots[url] = {"summary": v["summary"] or "", "image": v["image"] or ""}

    # 推算 event_type（舊資料沒事件類型欄位）
    def infer_type(v):
        if v["event_type"]:
            return v["event_type"]
        if v["clicked_url"]:
            return "click"
        return "visit"

    visits = [v for v in parsed if infer_type(v) == "visit"]
    clicks = [v for v in parsed if infer_type(v) == "click"]
    ctas = [v for v in parsed if infer_type(v) == "cta"]
    cta_phone = sum(1 for v in ctas if "電話" in (v.get("summary") or ""))
    cta_line = sum(1 for v in ctas if "LINE" in (v.get("summary") or ""))

    # 物件熱度榜（每個物件被點幾次）
    click_counts = {}
    for v in clicks:
        url = v["clicked_url"]
        if not url:
            continue
        if url not in click_counts:
            snap = snapshots.get(url, {})
            click_counts[url] = {
                "url": url,
                "count": 0,
                "summary": snap.get("summary") or "",
                "image": snap.get("image") or "",
            }
        click_counts[url]["count"] += 1
    top_properties = sorted(click_counts.values(), key=lambda x: -x["count"])[:15]

    # 各貼文表現（按 share_id 聚合）
    posts = {}
    for v in parsed:
        sid = v["share_id"]
        if not sid or infer_type(v) == "snapshot":
            continue
        if sid not in posts:
            posts[sid] = {
                "share_id": sid,
                "client": v["client"],
                "visits": 0,
                "clicks": 0,
                "latest": v["time"],
                "click_counts": {},
            }
        if infer_type(v) == "click":
            posts[sid]["clicks"] += 1
            url = v["clicked_url"]
            posts[sid]["click_counts"][url] = posts[sid]["click_counts"].get(url, 0) + 1
        else:
            posts[sid]["visits"] += 1
        if v["time"] > posts[sid]["latest"]:
            posts[sid]["latest"] = v["time"]
    posts_list = list(posts.values())
    for p in posts_list:
        if p["click_counts"]:
            top_url, top_count = max(p["click_counts"].items(), key=lambda x: x[1])
            snap = snapshots.get(top_url, {})
            p["top_property_summary"] = snap.get("summary") or top_url[-20:]
            p["top_property_count"] = top_count
            p["top_property_url"] = top_url
        else:
            p["top_property_summary"] = "—"
            p["top_property_count"] = 0
            p["top_property_url"] = ""
    posts_list.sort(key=lambda p: p["latest"], reverse=True)

    # 裝置分布
    device_counts = {}
    for v in parsed:
        if infer_type(v) == "snapshot":
            continue
        d = v["device"] or "未知"
        device_counts[d] = device_counts.get(d, 0) + 1
    devices = sorted(device_counts.items(), key=lambda x: -x[1])

    # 最近 7 天統計
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    recent_visits = 0
    recent_clicks = 0
    for v in parsed:
        if not v["time"]:
            continue
        try:
            t = datetime.fromisoformat(v["time"].replace("Z", "+00:00"))
        except Exception:
            continue
        if t < week_ago:
            continue
        et = infer_type(v)
        if et == "visit":
            recent_visits += 1
        elif et == "click":
            recent_clicks += 1

    # 屋齡 / 價格 / 區域分析（依快照資料聚合）
    age_buckets = {"< 5 年": 0, "5-10 年": 0, "10-20 年": 0, "> 20 年": 0, "未知": 0}
    price_buckets = {"< 800 萬": 0, "800-1200 萬": 0, "1200-1800 萬": 0, "> 1800 萬": 0, "未知": 0}
    district_clicks = {}
    for url, info in click_counts.items():
        meta = parse_property_meta(info.get("summary") or "")
        cnt = info["count"]
        age = meta.get("age")
        if age is None:
            age_buckets["未知"] += cnt
        elif age < 5:
            age_buckets["< 5 年"] += cnt
        elif age < 10:
            age_buckets["5-10 年"] += cnt
        elif age <= 20:
            age_buckets["10-20 年"] += cnt
        else:
            age_buckets["> 20 年"] += cnt

        price = meta.get("price")
        if price is None:
            price_buckets["未知"] += cnt
        elif price < 800:
            price_buckets["< 800 萬"] += cnt
        elif price < 1200:
            price_buckets["800-1200 萬"] += cnt
        elif price < 1800:
            price_buckets["1200-1800 萬"] += cnt
        else:
            price_buckets["> 1800 萬"] += cnt

        district = meta.get("district") or "未知"
        district_clicks[district] = district_clicks.get(district, 0) + cnt

    district_list = sorted(district_clicks.items(), key=lambda x: -x[1])

    return {
        "total_visits": len(visits),
        "total_clicks": len(clicks),
        "recent_visits": recent_visits,
        "recent_clicks": recent_clicks,
        "post_count": len(posts),
        "top_properties": top_properties,
        "posts": posts_list[:30],
        "devices": devices,
        "snapshots_count": len(snapshots),
        "cta_phone": cta_phone,
        "cta_line": cta_line,
        "cta_total": len(ctas),
        "age_buckets": age_buckets,
        "price_buckets": price_buckets,
        "district_clicks": district_list,
    }


def render_stats_html(stats):
    """產生 dashboard HTML"""
    from datetime import datetime, timezone, timedelta
    tw_now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")

    # 物件熱度榜
    if stats["top_properties"]:
        max_count = stats["top_properties"][0]["count"]
        top_html = ""
        for i, p in enumerate(stats["top_properties"]):
            pct = int(p["count"] / max_count * 100) if max_count else 0
            slug = _slug_from_url(p["url"])
            # 名稱優先順序：物件摘要 → 物件 {slug}（避免顯示成代碼）
            label = p["summary"] or f"物件 {slug}（無快照資料）"
            img = p["image"] or ""
            # 圖片 onerror → 顯示「已下架」fallback，避免破圖
            img_html = (
                f'<img src="{img}" loading="lazy" onerror="this.parentElement.innerHTML=\'<div class=no-img>已下架</div>\'">'
                if img else '<div class="no-img">—</div>'
            )
            top_html += f'''
            <div class="prop-row">
              <div class="prop-rank">{i+1}</div>
              <div class="prop-img">{img_html}</div>
              <div class="prop-info">
                <a class="prop-label" href="{p["url"]}" target="_blank" rel="noopener">{label}</a>
                <div class="bar-wrap"><div class="bar" style="width:{pct}%"></div><span class="count">{p["count"]} 點</span></div>
              </div>
            </div>'''
    else:
        top_html = '<div class="empty">還沒有點擊資料</div>'

    # 各貼文表現
    if stats["posts"]:
        posts_html = ""
        for p in stats["posts"]:
            try:
                t = datetime.fromisoformat(p["latest"].replace("Z", "+00:00"))
                latest_tw = (t + timedelta(hours=8)).strftime("%m/%d %H:%M")
            except Exception:
                latest_tw = "—"
            # 最熱物件 fallback：summary 沒值就顯示 slug
            tp_summary = p["top_property_summary"]
            if tp_summary == "—" and p["top_property_url"]:
                tp_summary = f"物件 {_slug_from_url(p['top_property_url'])}"
            top_label = tp_summary
            if p["top_property_url"]:
                top_label = f'<a href="{p["top_property_url"]}" target="_blank">{top_label}</a> ({p["top_property_count"]})'
            posts_html += f'''
            <tr>
              <td><strong>{p["client"]}</strong><div class="sub">{p["share_id"]}</div></td>
              <td class="num">{p["visits"]}</td>
              <td class="num">{p["clicks"]}</td>
              <td>{top_label}</td>
              <td class="sub">{latest_tw}</td>
            </tr>'''
    else:
        posts_html = '<tr><td colspan="5" class="empty">還沒有資料</td></tr>'

    # 裝置分布
    total_dev = sum(c for _, c in stats["devices"]) or 1
    dev_html = ""
    for d, c in stats["devices"][:10]:
        pct = int(c / total_dev * 100)
        dev_html += f'<div class="dev-row"><span>{d}</span><span class="bar-wrap"><span class="bar" style="width:{pct}%"></span><span class="count">{c} ({pct}%)</span></span></div>'
    if not dev_html:
        dev_html = '<div class="empty">還沒有資料</div>'

    # 屋齡 / 價格段分析 helper
    def render_buckets(buckets):
        total = sum(v for v in buckets.values() if v) or 1
        rows = ""
        for label, c in buckets.items():
            if c == 0:
                continue
            pct = int(c / total * 100)
            rows += f'<div class="dev-row"><span>{label}</span><span class="bar-wrap"><span class="bar" style="width:{pct}%"></span><span class="count">{c} ({pct}%)</span></span></div>'
        return rows or '<div class="empty">還沒有資料</div>'

    age_html = render_buckets(stats["age_buckets"])
    price_html = render_buckets(stats["price_buckets"])

    # 區域熱度
    if stats["district_clicks"]:
        max_d = stats["district_clicks"][0][1]
        district_html = ""
        for name, c in stats["district_clicks"][:10]:
            pct = int(c / max_d * 100)
            district_html += f'<div class="dev-row"><span>{name}</span><span class="bar-wrap"><span class="bar" style="width:{pct}%"></span><span class="count">{c} 點</span></span></div>'
    else:
        district_html = '<div class="empty">還沒有資料</div>'

    return f'''<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📊 統計儀表板 — 景泰客戶頁</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;600;700;900&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #FAF7F2; --bg-soft: #F2EDE4; --card: #FFFFFF;
    --wood-deep: #8B7355; --wood-light: #D9C7B0;
    --accent: #C9785A; --accent-deep: #A85D3F;
    --text: #2C2620; --text-soft: #6B6258; --text-muted: #9A9088;
    --border: #E8DFD2;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Noto Sans TC', -apple-system, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.7;
    font-size: 16px; padding: 30px 16px 80px;
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; }}
  header {{ text-align: center; margin-bottom: 30px; }}
  h1 {{ font-size: 30px; font-weight: 900; letter-spacing: 1px; }}
  .updated {{ font-size: 13px; color: var(--text-muted); margin-top: 6px; }}
  .back-link {{ display: inline-block; margin-top: 10px; color: var(--accent-deep); text-decoration: none; font-weight: 500; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 30px; }}
  .stat-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 20px; text-align: center; box-shadow: 0 2px 8px rgba(139,115,85,0.04); }}
  .stat-label {{ font-size: 14px; color: var(--text-muted); letter-spacing: 1px; }}
  .stat-value {{ font-size: 36px; font-weight: 900; color: var(--wood-deep); margin-top: 6px; line-height: 1; }}
  .stat-sub {{ font-size: 13px; color: var(--accent-deep); margin-top: 4px; }}
  section.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 26px; margin-bottom: 22px; }}
  h2 {{ font-size: 22px; font-weight: 700; margin-bottom: 18px; display: flex; align-items: center; gap: 10px; }}
  h2::before {{ content: ''; width: 5px; height: 26px; background: var(--accent); border-radius: 3px; }}
  .prop-row {{ display: flex; align-items: center; gap: 14px; padding: 12px 0; border-bottom: 1px dashed var(--bg-soft); }}
  .prop-row:last-child {{ border-bottom: none; }}
  .prop-rank {{ font-size: 22px; font-weight: 900; color: var(--wood-deep); width: 30px; text-align: center; }}
  .prop-img {{ width: 80px; height: 60px; border-radius: 8px; overflow: hidden; flex-shrink: 0; background: var(--bg-soft); }}
  .prop-img img {{ width: 100%; height: 100%; object-fit: cover; }}
  .prop-img .no-img {{ display: flex; align-items: center; justify-content: center; height: 100%; color: var(--text-muted); }}
  .prop-info {{ flex: 1; min-width: 0; }}
  .prop-label {{ font-size: 15px; color: var(--text); font-weight: 600; text-decoration: none; display: block; margin-bottom: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .prop-label:hover {{ color: var(--accent-deep); }}
  .bar-wrap {{ display: flex; align-items: center; gap: 10px; }}
  .bar {{ height: 8px; background: linear-gradient(90deg, var(--wood-light), var(--accent)); border-radius: 4px; min-width: 4px; }}
  .count {{ font-size: 13px; color: var(--text-soft); font-weight: 600; flex-shrink: 0; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 12px 8px; text-align: left; border-bottom: 1px solid var(--bg-soft); font-size: 14px; }}
  th {{ font-size: 13px; color: var(--text-muted); letter-spacing: 1px; font-weight: 600; }}
  td.num {{ font-weight: 700; color: var(--wood-deep); font-size: 18px; text-align: center; width: 70px; }}
  .sub {{ font-size: 12px; color: var(--text-muted); }}
  .empty {{ text-align: center; padding: 30px; color: var(--text-muted); }}
  .dev-row {{ display: flex; align-items: center; justify-content: space-between; padding: 8px 0; gap: 14px; font-size: 14px; }}
  .dev-row .bar-wrap {{ flex: 1; max-width: 60%; }}
  .dev-row .bar {{ flex: 1; }}
  a {{ color: var(--accent-deep); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  @media (max-width: 600px) {{
    .prop-img {{ width: 60px; height: 45px; }}
    h1 {{ font-size: 24px; }}
    .stat-value {{ font-size: 28px; }}
    th, td {{ padding: 8px 4px; font-size: 13px; }}
    td.num {{ font-size: 16px; width: 50px; }}
  }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>📊 統計儀表板</h1>
  <div class="updated">最後更新：{tw_now}（台北時間）</div>
  <a class="back-link" href="/">← 回表單</a>
</header>

<div class="stats-grid">
  <div class="stat-box">
    <div class="stat-label">總訪問</div>
    <div class="stat-value">{stats["total_visits"]}</div>
    <div class="stat-sub">最近 7 天 +{stats["recent_visits"]}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">總點擊</div>
    <div class="stat-value">{stats["total_clicks"]}</div>
    <div class="stat-sub">最近 7 天 +{stats["recent_clicks"]}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">貼文數</div>
    <div class="stat-value">{stats["post_count"]}</div>
    <div class="stat-sub">{stats["snapshots_count"]} 個物件快照</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">CTA 轉換</div>
    <div class="stat-value">{stats["cta_total"]}</div>
    <div class="stat-sub">📞 {stats["cta_phone"]} · 💬 {stats["cta_line"]}</div>
  </div>
</div>

<section class="card">
  <h2>🎯 客戶意願度漏斗</h2>
  <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px;">
    <div class="stat-box" style="background: var(--bg-soft);">
      <div class="stat-label">訪問</div>
      <div class="stat-value" style="font-size: 28px;">{stats["total_visits"]}</div>
      <div class="stat-sub">看了客戶頁</div>
    </div>
    <div class="stat-box" style="background: var(--bg-soft);">
      <div class="stat-label">物件點擊</div>
      <div class="stat-value" style="font-size: 28px;">{stats["total_clicks"]}</div>
      <div class="stat-sub">{int(stats["total_clicks"] / stats["total_visits"] * 100) if stats["total_visits"] else 0}% 點進物件</div>
    </div>
    <div class="stat-box" style="background: var(--bg-soft);">
      <div class="stat-label">📞 點電話</div>
      <div class="stat-value" style="font-size: 28px; color: var(--accent-deep);">{stats["cta_phone"]}</div>
      <div class="stat-sub">{int(stats["cta_phone"] / stats["total_visits"] * 100) if stats["total_visits"] else 0}% 強訊號</div>
    </div>
    <div class="stat-box" style="background: var(--bg-soft);">
      <div class="stat-label">💬 點 LINE</div>
      <div class="stat-value" style="font-size: 28px; color: #06C755;">{stats["cta_line"]}</div>
      <div class="stat-sub">{int(stats["cta_line"] / stats["total_visits"] * 100) if stats["total_visits"] else 0}% 強訊號</div>
    </div>
  </div>
  <div style="margin-top: 14px; font-size: 13px; color: var(--text-muted); text-align: center;">
    📞 / 💬 點擊 = 強烈購買訊號（這位客戶真的想跟你聊）
  </div>
</section>

<section class="card">
  <h2>🔥 物件熱度榜（總點擊 TOP 15）</h2>
  {top_html}
</section>

<section class="card">
  <h2>📅 各貼文表現</h2>
  <table>
    <thead><tr><th>貼文</th><th>訪問</th><th>點擊</th><th>最熱物件</th><th>最近</th></tr></thead>
    <tbody>{posts_html}</tbody>
  </table>
</section>

<section class="card">
  <h2>🏘️ 區域熱度（首購族最在意哪邊）</h2>
  {district_html}
</section>

<section class="card">
  <h2>💰 價格段熱度</h2>
  {price_html}
</section>

<section class="card">
  <h2>📅 屋齡段熱度</h2>
  {age_html}
</section>

<section class="card">
  <h2>📱 裝置分布</h2>
  {dev_html}
</section>

<div style="text-align:center; margin-top: 26px;">
  <a href="https://www.notion.so/c87cef4ae8df43f7983d50b238c7fc30" target="_blank">📋 開原始 Notion DB</a>
  &nbsp;·&nbsp;
  <a href="/">回表單</a>
</div>

</div>
</body>
</html>'''


@app.route("/api/regen", methods=["GET", "POST"])
def regen_endpoint():
    """重新生成既有 share_id 的客戶頁（同 URL 升級到最新版面）

    用途：景泰加新功能後想讓舊客戶頁也享有 → 一鍵 regen，URL 不變客戶不用換
    入口：?share_id=XXX 或 POST {"share_id": "XXX"}
         ?share_id=all → 一次處理 Notion 看到的所有 share_id
    """
    share_id = request.args.get("share_id") or (request.get_json(silent=True) or {}).get("share_id", "")
    share_id = (share_id or "").strip()
    if not share_id:
        return jsonify({"error": "需要 share_id 參數（?share_id=XXX 或 ?share_id=all）"}), 400

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return jsonify({"error": "no GITHUB_TOKEN"}), 500

    rows = notion_query_all(limit=2000)

    # 先聚合：每個 share_id → {client, slugs}
    share_map = {}
    for r in rows:
        sid = _row_field(r, "share_id", "rich_text")
        if not sid:
            continue
        if sid not in share_map:
            share_map[sid] = {"client": "客戶", "slugs": set()}
        client = _row_field(r, "客戶", "title")
        if client and client not in ("未知", "歷史回填"):
            share_map[sid]["client"] = client
        et = _row_field(r, "事件類型", "select")
        url = _normalize_property_url(_row_field(r, "點擊物件", "url"))
        if et in ("snapshot", "backfill", "click") and url:
            m = re.search(r'/([A-Za-z0-9_-]+)/?$', url)
            if m:
                share_map[sid]["slugs"].add(m.group(1))

    targets = list(share_map.keys()) if share_id == "all" else [share_id]

    results = []
    for sid in targets:
        info = share_map.get(sid)
        if not info or not info["slugs"]:
            results.append({"share_id": sid, "ok": False, "error": "找不到物件資料"})
            continue

        try:
            properties = fetch_full_batch(list(info["slugs"]))
        except Exception as e:
            results.append({"share_id": sid, "ok": False, "error": f"fetch 失敗: {e}"})
            continue
        if not properties:
            results.append({"share_id": sid, "ok": False, "error": "全部物件已下架"})
            continue

        client_data = {
            "name": info["client"],
            "need": "找房需求",
            "share_id": sid,
            "contact": DEFAULT_CONTACT,
        }
        html = gen_html(client_data, properties)

        try:
            github_push(
                f"{sid}/index.html",
                html,
                f"regen: {sid} ({len(properties)} 戶) 升級到最新版面",
                token,
                update=True,
            )
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")[:200] if hasattr(e, "read") else str(e)
            results.append({"share_id": sid, "ok": False, "error": f"github push: {e.code} {err}"})
            continue
        except Exception as e:
            results.append({"share_id": sid, "ok": False, "error": f"push 失敗: {e}"})
            continue

        # 順便寫新快照（會有最新摘要含區域）
        try:
            notion_log_snapshot(sid, info["client"], properties)
        except Exception:
            pass

        results.append({
            "share_id": sid,
            "ok": True,
            "url": f"{PAGES_BASE_URL}/{sid}/",
            "client": info["client"],
            "count": len(properties),
        })

    return jsonify({
        "processed": len(results),
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    })


@app.route("/api/backfill", methods=["GET"])
def backfill_endpoint():
    """歷史回填 — 對舊的點擊 URL 重新從 ycut 抓資料寫進 Notion 當快照

    用途：新增「物件快照」機制之前的舊資料，物件熱度榜會顯示「物件 8j3D8L（無快照資料）」，
    跑這支 endpoint 一次，沒賣掉的物件都會補上社區/價格/坪數+首圖
    """
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return jsonify({"error": "no notion config"}), 500

    force = request.args.get("force") == "1"
    rows = notion_query_all(limit=2000)
    snapshot_urls = set()
    click_urls = set()
    for r in rows:
        clicked = _normalize_property_url(_row_field(r, "點擊物件", "url"))
        event_type = _row_field(r, "事件類型", "select")
        summary = _row_field(r, "物件摘要", "rich_text")
        if event_type in ("snapshot", "backfill") or summary:
            if clicked:
                snapshot_urls.add(clicked)
        elif event_type == "click" or (not event_type and clicked):
            click_urls.add(clicked)

    # ?force=1 → 不管已有快照，全部 click URL 重抓；compute_stats 會自動用最新的覆蓋舊的
    missing = sorted(click_urls) if force else sorted(click_urls - snapshot_urls)
    written, failed = 0, 0
    detail = []
    for url in missing:
        m = re.search(r'/([A-Za-z0-9_-]+)/?$', url)
        if not m:
            failed += 1
            continue
        slug = m.group(1)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                html = r.read().decode("utf-8", errors="ignore")
            parsed = parse_ycut_html(html, slug)
        except Exception as e:
            detail.append(f"❌ {slug}: {type(e).__name__}")
            failed += 1
            continue

        if not parsed.get("community_display") or not parsed.get("price"):
            detail.append(f"⚠️ {slug}: 解析失敗（可能已下架）")
            failed += 1
            continue

        # 寫入快照（標記 event_type=backfill 區分原生 snapshot）
        n = _write_backfill_snapshot(slug, parsed)
        if n:
            detail.append(f"✅ {slug}: {parsed.get('community_display')} {parsed.get('price')}萬")
            written += n
        else:
            detail.append(f"❌ {slug}: Notion 寫入失敗")
            failed += 1

    return jsonify({
        "checked_clicks": len(click_urls),
        "already_snapshotted": len(snapshot_urls),
        "needs_backfill": len(missing),
        "written": written,
        "failed": failed,
        "detail": detail[:50],
    })


def _write_backfill_snapshot(slug, parsed):
    """寫一筆 event_type=backfill 的快照"""
    target_url = f"https://x.ychouse.tw/{slug}"
    district = parse_district(parsed.get("address", ""))
    summary_parts = [
        district if district != "其他" else "",
        (parsed.get("community_display") or "").strip(),
        f"{parsed['price']}萬" if parsed.get("price") else "",
        f"{parsed.get('floor')}F/{parsed.get('floor_total')}" if parsed.get("floor") else "",
        f"{parsed.get('area')}坪" if parsed.get("area") else "",
        f"{parsed.get('age')}年" if parsed.get("age") else "",
    ]
    summary = " · ".join(s for s in summary_parts if s)

    properties = {
        "客戶": {"title": [{"text": {"content": "歷史回填"[:200]}}]},
        "點擊物件": {"url": target_url[:1900]},
        "物件摘要": {"rich_text": [{"text": {"content": summary[:1900]}}]} if summary else {"rich_text": []},
        "物件首圖": {"url": (parsed.get("og_image") or "")[:1900] or None},
        "事件類型": {"select": {"name": "backfill"}},
    }
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
        with urllib.request.urlopen(req, timeout=10) as r:
            return 1 if r.status in (200, 201) else 0
    except Exception as e:
        print(f"backfill write failed for {slug}: {e}")
        return 0


@app.route("/stats", methods=["GET"])
def stats_endpoint():
    rows = notion_query_all(limit=1000)
    stats = compute_stats(rows)
    return render_stats_html(stats), 200, {"Content-Type": "text/html; charset=utf-8"}


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
