# -*- coding: utf-8 -*-
"""
yungching_recommend: 永慶房屋推薦配貨 pipeline。

流程:
  1. fetch_anchor(short_url): 從 ychouse 短網址抓 ycut anchor 物件 metadata
  2. parse_list(html): 解析永慶 buy.yungching.com.tw 區域 list SSR HTML
  3. parse_detail(html): 解析永慶 /house/{id} 詳情頁
  4. recommend(short_url): 主入口 — 框檔次 + decoy 配貨

配貨邏輯 (v2 「框檔次 + decoy」):
  西屯標「3房」的物件 598 萬~7200 萬都有 (老公寓 vs 豪宅)，純房型配對會把
  老公寓塞給看電梯大樓的客人。所以先用「坪數 + 類型 + 房型」框出同一檔次，
  再在檔次內 decoy 配貨 (4 便宜 + 1 貴)。

  框檔次條件 (階梯 fallback，撈不夠逐步放寬):
    L1: 同區 + 同類型 + 同房型 + 坪數±35% + 價格 0.6~1.4x
    L2: 放寬坪數到 ±50%
    L3: 房型 ±1 房 (3房 -> 2/3/4房)
    L4: 擴到鄰近區 (西屯->南屯/北屯->台中其他區)
  decoy 配貨 (最終池內):
    cheap : 價格 < anchor，由高到低 (越接近 anchor 越好) 取 4 筆
    pricey: 價格 > anchor，由低到高 (越接近 anchor 越好) 取 1 筆
"""
import re
import json
import html as _html
from html import unescape

import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor


# ============================================================================
# Section 1: fetch_anchor — 從 ychouse 短網址抓 ycut 物件 metadata
# ============================================================================

def fetch_anchor(short_url: str) -> dict:
    """
    從 ychouse 短網址抓物件 metadata。

    Args:
        short_url: e.g. "https://x.ychouse.tw/743CgK"
                   會 301 redirect 到 https://www.ycut.com.tw/case-info/<uuid>

    Returns:
        dict 含 price_wan / room_count / district / type / image_url /
        community_name / address / anchor_id / age_years / city
    """
    resp = requests.get(
        short_url,
        allow_redirects=True,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/124.0.0.0 Safari/537.36'
        },
        timeout=30,
    )
    resp.raise_for_status()
    resp.encoding = 'utf-8'  # ycut 不發 charset header,強制 UTF-8 避免中文亂碼
    html = resp.text
    final_url = resp.url  # e.g. https://www.ycut.com.tw/case-info/1bcb8e21-...

    # 1. 抓 <script id="ng-state" type="application/json">{...}</script>
    m = re.search(
        r'<script[^>]*\bid="ng-state"[^>]*>(\{.*?\})</script>',
        html,
        re.DOTALL,
    )
    if not m:
        raise ValueError("找不到 ng-state JSON block（頁面結構可能改版了）")

    raw_json = m.group(1)
    state = json.loads(raw_json)

    # 2. 在 state 內找 /api/Case/Info/Share/ 那一筆 entry 的 data
    data = None
    for entry in state.values():
        if not isinstance(entry, dict):
            continue
        body = entry.get("b")
        if not isinstance(body, dict):
            continue
        method = body.get("method", "")
        if "/api/Case/Info/Share/" in method:
            data = body.get("data")
            break

    if data is None:
        raise ValueError("ng-state 內找不到 /api/Case/Info/Share/ 那筆")

    # 3. 解析欄位
    addr_simp = data.get("addrSimp", "") or ""
    district_match = re.match(r'([^市縣]+[市縣])([^區鄉鎮市]+[區鄉鎮市])', addr_simp)
    city = district_match.group(1) if district_match else ""
    district = district_match.group(2) if district_match else ""

    case_name = data.get("caseName", "") or ""
    rm = data.get("rm")
    if rm is not None:
        room_count = f"{rm}房"
    else:
        rm_match = re.search(r'(\d+)房', case_name)
        room_count = rm_match.group(1) + "房" if rm_match else ""

    # anchor_id：優先用 final_url 的 uuid,fallback 用 caseCode
    anchor_id_match = re.search(r'/case-info/([0-9a-f-]{36})', final_url)
    anchor_id = anchor_id_match.group(1) if anchor_id_match else data.get("caseCode", "")

    bui_year = data.get("buiYear")
    try:
        age_years = float(bui_year) if bui_year not in (None, "") else None
    except (TypeError, ValueError):
        age_years = None

    images = data.get("images") or []
    image_urls = []
    for _im in images:
        _u = _im.get("imgUrl", "") or ""
        if _u:  # ycut 預設縮圖 600x450 太糊，放大到 1280x960
            _u = re.sub(r'([?&])width=\d+', r'\g<1>width=1280', _u)
            _u = re.sub(r'([?&])height=\d+', r'\g<1>height=960', _u)
            image_urls.append(_u)
    image_url = image_urls[0] if image_urls else ""

    # 數值欄位 (ycut ng-state data dict)，全部 try 容錯，抓不到給 None
    def _f(key):
        try:
            v = data.get(key)
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _i(key):
        v = _f(key)
        return int(v) if v is not None else None

    hall_count = _i("livingRm")
    bath_count = _i("bathRm")

    # 樓層: floorSt~floorEn / upFloor。單層 6/21，跨層 6-8/21
    fl_st, fl_en, fl_up = _i("floorSt"), _i("floorEn"), _i("upFloor")
    if fl_st and fl_up:
        floor = (f"{fl_st}/{fl_up}" if (not fl_en or fl_en == fl_st)
                 else f"{fl_st}-{fl_en}/{fl_up}")
    else:
        floor = None

    # 車位 (隱私: 只給類型，不給車位編號 parkingNO)
    parking = None
    if (data.get("parkingSpace") or "").strip() in ("有", "Y", "1"):
        pm = (data.get("parkingMode") or "").replace("/", "").strip()
        parking = f"{pm}車位" if pm else "有車位"

    # 管理費
    mg_expense = _i("mgExpense")
    mg_code = data.get("mgCode") or ""
    mg_fee = (f"{mg_expense:,} 元/{mg_code}" if (mg_expense and mg_code)
              else (f"{mg_expense:,} 元" if mg_expense else None))

    result = {
        "price_wan": int(data["price"]) if data.get("price") is not None else None,
        "room_count": room_count,
        "hall_count": hall_count,
        "bath_count": bath_count,
        "district": district,
        "city": city,
        "type": data.get("typeCode", ""),
        "use_code": data.get("useCode", ""),
        "image_url": image_url,
        "image_urls": image_urls,
        "community_name": data.get("buildingName", ""),
        "address": addr_simp,
        "anchor_id": anchor_id,
        "age_years": age_years,
        "main_area_ping": _f("buiMPin"),          # 主建物坪
        "building_area_ping": _f("buiTotPin"),    # 權狀/建物總坪
        "land_area_ping": _f("landShPin"),        # 土地持分坪
        "public_area_ping": _f("buiPubPin"),      # 公設坪
        "balcony_ping": _f("porchPin"),           # 陽台坪
        "rainproof_ping": _f("rainproofPin"),     # 雨遮坪
        "public_ratio": _f("pubRate"),            # 公設比 %
        "floor": floor,
        "parking": parking,
        "mg_fee": mg_fee,
        "pri_school": data.get("priSchoolName") or None,
        "jun_school": data.get("junSchoolName") or None,
        "selling_point": (data.get("caseSpec") or "").strip() or None,
        "build_date": data.get("twDate") or None,
    }
    return result


# ============================================================================
# Section 2: parse_list — 永慶 list SSR HTML 解析
# ============================================================================

_NUM_RE = re.compile(r"[\d.]+")
_PRICE_RE = re.compile(r"[\d,]+")
_DISTRICT_RE = re.compile(r"([一-鿿]{1,3}[市縣])([一-鿿]{1,4}區)")


def _text(node) -> str:
    return node.get_text(strip=True) if node is not None else ""


def _first_float(s: str):
    if not s:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _parse_price_wan(price_text: str, origin_text: str = ""):
    """`<div class="price">10,800</div>` -> 10800 (萬)."""
    for src in (price_text, origin_text):
        if not src:
            continue
        m = _PRICE_RE.search(src)
        if m:
            try:
                return int(m.group().replace(",", ""))
            except ValueError:
                continue
    return None


def _parse_room_count(room_text: str):
    """`5房(室)2廳4.5衛` -> '5房'."""
    if not room_text:
        return ""
    m = re.search(r"(\d+)\s*房", room_text)
    if m:
        return f"{m.group(1)}房"
    return ""


def _parse_main_area(main_area_text: str):
    """`主+陽89.16` or `主75.15` -> float (坪)."""
    return _first_float(main_area_text) if main_area_text else None


def _parse_district(address_text: str):
    """`台北市中山區朱崙街` -> '中山區'."""
    if not address_text:
        return ""
    m = _DISTRICT_RE.search(address_text)
    if m:
        return m.group(2)
    m2 = re.search(r"([一-鿿]{1,4}區)", address_text)
    return m2.group(1) if m2 else ""


def _clean_image_url(url: str) -> str:
    return _html.unescape(url.strip()) if url else ""


def _parse_card_age(case_info_node):
    """從 `<div class="case-info">` 內找出 `N年` 那個 span (無 class) -> float."""
    if case_info_node is None:
        return None
    for sp in case_info_node.find_all("span"):
        t = sp.get_text(strip=True)
        m = re.match(r"^([\d.]+)\s*年$", t)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def parse_list(html_str: str):
    """Parse buy.yungching.com.tw list SSR HTML.

    Returns list[dict] with keys:
      house_id, title, price_wan, room_count, main_area_ping,
      district, cover_image_url, case_type, age_years
    Skeleton loader cards (no href) are skipped.

    case_type / age_years 是 v2 框檔次的關鍵欄位 — list card 上就有
    `<span class="caseType">電梯大樓</span>` + 一個無 class 的 `15.6年` span，
    不用打 detail 頁就能框檔次。
    """
    soup = BeautifulSoup(html_str, "html.parser")
    out = []

    for card in soup.find_all("yc-ng-buy-house-card"):
        link = card.find("a", href=re.compile(r"^house/\d+"))
        if link is None:
            continue  # skeleton loader
        m_id = re.search(r"house/(\d+)", link.get("href", ""))
        if not m_id:
            continue
        house_id = m_id.group(1)

        title = _text(card.find("div", class_="caseName"))
        addr_text = _text(card.find("span", class_="address"))
        m_addr = _DISTRICT_RE.search(addr_text) if addr_text else None
        if m_addr:
            city, district = m_addr.group(1), m_addr.group(2)
        else:
            mc = re.search(r"([一-鿿]{1,3}[市縣])", addr_text or "")
            city = mc.group(1) if mc else ""
            district = _parse_district(addr_text)
        main_area_ping = _parse_main_area(_text(card.find("span", class_="mainArea")))
        building_area_ping = _first_float(_text(card.find("span", class_="regArea")))  # 建坪/權狀
        room_count = _parse_room_count(_text(card.find("span", class_="room")))
        price_wan = _parse_price_wan(
            _text(card.find("div", class_="price")),
            _text(card.find("span", class_="origin-price")),
        )
        community_name = _text(card.find("span", class_="community")) or None
        parking = _text(card.find("span", class_="car")) or None

        case_info = card.find("div", class_="case-info")
        case_type = _text(card.find("span", class_="caseType")) or None
        age_years = _parse_card_age(case_info)

        img_node = card.find("img")
        cover_image_url = _clean_image_url(img_node.get("src", "")) if img_node else ""

        out.append({
            "house_id": house_id,
            "title": title,
            "price_wan": price_wan,
            "room_count": room_count,
            "main_area_ping": main_area_ping,
            "building_area_ping": building_area_ping,
            "district": district,
            "city": city,
            "community_name": community_name,
            "parking": parking,
            "case_type": case_type,
            "age_years": age_years,
            "cover_image_url": cover_image_url,
        })

    return out


# ============================================================================
# Section 3: parse_detail — 永慶 /house/{id} 詳情頁解析
# ============================================================================

def _strip_tags(s: str) -> str:
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.DOTALL)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _find_class_text(html: str, cls: str):
    pat = re.compile(
        r'<(\w+)[^>]*class="[^"]*\b' + re.escape(cls) + r'\b[^"]*"[^>]*>(.*?)</\1>',
        re.DOTALL,
    )
    m = pat.search(html)
    return _strip_tags(m.group(2)) if m else None


def _to_float(s):
    if s is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group(0)) if m else None


def _to_int(s):
    f = _to_float(s)
    return int(f) if f is not None else None


def parse_detail(html_str: str) -> dict:
    """Parse a buy.yungching.com.tw /house/{id} SSR detail page."""
    out = {
        "title": None,
        "subtitle": None,
        "price_wan": None,                  # int, e.g. 4080
        "main_area_ping": None,             # float (主+平)
        "building_area_ping": None,         # float (建物總坪)
        "land_area_ping": None,             # float (土地坪)
        "unit_price_wan_per_ping": None,    # float
        "room_count": None,                 # str: "--" or "3"
        "hall_count": None,                 # int
        "bath_count": None,                 # int
        "building_age": None,               # float (years)
        "floor": None,                      # str, e.g. "1/7"
        "case_type": None,                  # 店面 / 公寓 / 大樓 ...
        "community_name": None,
        "city": None,
        "district": None,
        "street": None,
        "address_text": None,
        "case_id": None,                    # YC...
        "house_id": None,                   # URL /house/{id}
        "parking": None,                    # 車位類型 (平面/坡道/機械...)
        "parking_area_ping": None,          # 含車位坪數
        "image_urls": [],                   # list[str]
    }

    # ---- JSON-LD ---------------------------------------------------
    ld_blocks = re.findall(
        r'<script type="application/ld\+json">(\{.*?\})</script>',
        html_str,
        re.DOTALL,
    )
    breadcrumb = None
    for block in ld_blocks:
        try:
            obj = json.loads(block)
        except Exception:
            continue
        t = obj.get("@type")
        if t == "Product":
            out["case_id"] = obj.get("productID")
            offers = obj.get("offers") or {}
            if isinstance(offers, dict) and offers.get("price") is not None:
                out["price_wan"] = _to_int(offers.get("price"))
            mh = re.search(r"/house/(\d+)", obj.get("url") or "")
            if mh:
                out["house_id"] = mh.group(1)
        elif t == "BreadcrumbList":
            breadcrumb = obj.get("itemListElement") or []

    if breadcrumb:
        names = [it.get("name") for it in breadcrumb if isinstance(it, dict)]
        if len(names) >= 2:
            out["city"] = names[1]
        if len(names) >= 3:
            out["district"] = names[2]
        if len(names) >= 4:
            out["street"] = names[3]
        if len(names) >= 5:
            out["community_name"] = names[4]

    # ---- title / subtitle / address --------------------------------
    out["title"] = _find_class_text(html_str, "case-name") or out["title"]
    out["subtitle"] = _find_class_text(html_str, "case-desc")
    addr = _find_class_text(html_str, "address-text")
    if addr:
        out["address_text"] = addr

    # ---- price fallback (JSON-LD usually wins) ---------------------
    if out["price_wan"] is None:
        ptxt = _find_class_text(html_str, "price")
        if ptxt:
            out["price_wan"] = _to_int(ptxt)

    # ---- 屋況區塊 ---------------------------------------------------
    out["case_type"] = _find_class_text(html_str, "case-type")
    age_text = _find_class_text(html_str, "age")
    if age_text:
        out["building_age"] = _to_float(age_text)

    floor_text = _find_class_text(html_str, "floor")
    if floor_text:
        mf = re.search(r"\d+\s*/\s*\d+", floor_text)
        out["floor"] = mf.group(0).replace(" ", "") if mf else floor_text.replace("樓", "").strip()

    regarea = _find_class_text(html_str, "regarea")
    if regarea:
        mb = re.search(r"建物\s*([\d.]+)\s*坪", regarea)
        if mb:
            out["building_area_ping"] = float(mb.group(1))
        mm = re.search(r"主\+平\s*([\d.]+)\s*坪", regarea)
        if mm:
            out["main_area_ping"] = float(mm.group(1))

    room_text = _find_class_text(html_str, "room")
    if room_text:
        mr = re.search(r"(--|\d+)\s*房", room_text)
        if mr:
            out["room_count"] = mr.group(1)
        mh = re.search(r"(\d+)\s*廳", room_text)
        if mh:
            out["hall_count"] = int(mh.group(1))
        mw = re.search(r"(\d+)\s*衛", room_text)
        if mw:
            out["bath_count"] = int(mw.group(1))

    # ---- 細節欄位 (單價 / 土地坪) 走整頁文本兜底 ------------------
    text = _strip_tags(html_str)
    mu = re.search(r"單價\s*([\d.,]+)\s*萬\s*/\s*坪", text)
    if mu:
        out["unit_price_wan_per_ping"] = float(mu.group(1).replace(",", ""))
    if out["building_area_ping"] is None:
        mb2 = re.search(r"建物坪數\s*([\d.]+)\s*坪", text)
        if mb2:
            out["building_area_ping"] = float(mb2.group(1))
    ml = re.search(r"土地坪數\s*([\d.]+)\s*坪", text)
    if ml:
        out["land_area_ping"] = float(ml.group(1))

    # 車位類型 + 含車位坪 (永慶 detail 文本)
    mp = re.search(r"(坡道平面|坡道機械|升降平面|升降機械|平面|機械|塔式)(?:固定)?車位", text)
    if mp:
        out["parking"] = mp.group(1) + "車位"
    mpa = re.search(r"含車位\s*([\d.]+)\s*坪", text)
    if mpa:
        out["parking_area_ping"] = float(mpa.group(1))

    # 單價兜底: 沒抓到就用 總價/權狀坪 自算 (權狀單價)
    if out["unit_price_wan_per_ping"] is None and out["price_wan"] and out["building_area_ping"]:
        out["unit_price_wan_per_ping"] = round(out["price_wan"] / out["building_area_ping"], 1)

    # ---- 照片 photo-swiper -----------------------------------------
    swiper = re.search(
        r'class="photo-swiper[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html_str,
        re.DOTALL,
    )
    img_haystack = swiper.group(1) if swiper else html_str
    img_urls = re.findall(
        r'<img[^>]*\bsrc="(https://yccdn\.yungching\.com\.tw/v1/image/\?key=[^"]+)"',
        img_haystack,
    )
    seen = set()
    uniq = []
    for u in img_urls:
        uc = unescape(u)
        if uc in seen:
            continue
        seen.add(uc)
        uniq.append(uc)
    out["image_urls"] = uniq

    return out


# ============================================================================
# Section 4: recommend — 主入口 (框檔次 + decoy 配貨)
# ============================================================================

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
       'AppleWebKit/537.36 (KHTML, like Gecko) '
       'Chrome/124.0.0.0 Safari/537.36')

# 台中市鄰近區 map (L4 用)。key=錨點區 -> 由近到遠的鄰區 list。
# 不在 map 內的城市/區 fallback 成「同市其他區」(由 _TC_DISTRICTS 補)。
_NEIGHBOR_MAP = {
    "西屯區": ["南屯區", "北屯區", "西區", "北區", "中區", "南區", "東區"],
    "南屯區": ["西屯區", "南區", "西區", "北屯區", "中區", "東區", "北區"],
    "北屯區": ["西屯區", "北區", "東區", "潭子區", "西區", "南屯區", "中區"],
    "西區":   ["中區", "北區", "南區", "西屯區", "南屯區", "東區", "北屯區"],
    "北區":   ["西區", "中區", "北屯區", "東區", "西屯區", "南屯區", "南區"],
    "南區":   ["西區", "中區", "東區", "南屯區", "北區", "西屯區", "北屯區"],
    "東區":   ["中區", "南區", "北區", "北屯區", "西區", "太平區", "南屯區"],
    "中區":   ["西區", "北區", "東區", "南區", "南屯區", "西屯區", "北屯區"],
}

# 台中市完整行政區 (L4 fallback 補無 map 的區用)
_TC_DISTRICTS = [
    "中區", "東區", "南區", "西區", "北區", "北屯區", "西屯區", "南屯區",
    "太平區", "大里區", "霧峰區", "烏日區", "豐原區", "后里區", "石岡區",
    "東勢區", "和平區", "新社區", "潭子區", "大雅區", "神岡區", "大肚區",
    "沙鹿區", "龍井區", "梧棲區", "清水區", "大甲區", "外埔區", "大安區",
]

# 彰化縣 26 鄉鎮市 (景泰老家，偶爾會有彰化物件)
_CHANGHUA_DISTRICTS = [
    "彰化市", "員林市", "和美鎮", "鹿港鎮", "溪湖鎮", "二林鎮", "田中鎮", "北斗鎮",
    "線西鄉", "伸港鄉", "福興鄉", "秀水鄉", "花壇鄉", "芬園鄉", "大村鄉", "埔鹽鄉",
    "埔心鄉", "永靖鄉", "社頭鄉", "二水鄉", "田尾鄉", "埤頭鄉", "芳苑鄉", "大城鄉",
    "竹塘鄉", "溪州鄉",
]

# city -> 全區清單。鄰近區 fallback 用「同縣市其他區」，不再寫死台中。
_CITY_DISTRICTS = {
    "台中市": _TC_DISTRICTS,
    "彰化縣": _CHANGHUA_DISTRICTS,
}


def build_filter_url(city, district=None, room=None, price_min_wan=None,
                     price_max_wan=None, area_min_ping=None, area_max_ping=None,
                     building_type=None) -> str:
    """組永慶買方平台 (buy.yungching.com.tw) list URL。

    ⚠️ recon:filter 階段確認: 永慶不支援路徑型 / query string 篩選，
    這個 function 只組「地區層級」list URL，價格/房型/坪數/類型的篩選一律
    在 Python 端對 parse_list() 的結果做。多餘參數只是保留簽名相容、不進 URL。

    Example:
        build_filter_url("台中市", "西屯區")
        -> https://buy.yungching.com.tw/list/%E5%8F%B0%E4%B8%AD%E5%B8%82-%E8%A5%BF%E5%B1%AF%E5%8D%80-_c
    """
    import urllib.parse
    location = f"{city}-{district}-" if district else f"{city}-"
    location_encoded = urllib.parse.quote(location, safe='')
    return f"https://buy.yungching.com.tw/list/{location_encoded}_c"


def _normalize_type(t: str) -> str:
    """把 ycut typeCode (大樓/華廈/公寓/透天...) 跟永慶 caseType
    (電梯大樓/華廈/公寓/透天厝/別墅...) 對齊成同一個粗分類，好做同類型比對。

    大樓類: 大樓 / 電梯大樓
    透天類: 透天 / 透天厝 / 別墅 / 樓中樓
    華廈   : 華廈
    公寓   : 公寓
    其餘原樣回傳 (套房/店面/辦公/土地 等)
    """
    if not t:
        return ""
    t = t.strip()
    if "大樓" in t:
        return "大樓"
    if ("透天" in t) or ("別墅" in t) or ("樓中樓" in t):
        return "透天"
    if "華廈" in t:
        return "華廈"
    if "公寓" in t:
        return "公寓"
    return t


def _room_num(room_count: str):
    """'3房' -> 3 ; 抓不到 -> None."""
    if not room_count:
        return None
    m = re.search(r"(\d+)", room_count)
    return int(m.group(1)) if m else None


def _fetch_list(city, district, warnings) -> list:
    """抓某 (city, district) 的永慶 list page 並 parse。失敗回 []。"""
    url = build_filter_url(city, district)
    try:
        resp = requests.get(url, headers={'User-Agent': _UA}, timeout=30)
        resp.raise_for_status()
        resp.encoding = 'utf-8'
        cands = parse_list(resp.text)
        warnings.append(f"list {city}-{district or '全市'}: 抓到 {len(cands)} 筆 ({url})")
        return cands
    except Exception as e:
        warnings.append(f"list {city}-{district or '全市'} fetch 失敗: {e}")
        return []


def _passes_tier(c, anchor_type_norm, room_set, area_lo, area_hi, price_lo, price_hi,
                 anchor_city='', b_lo=None, b_hi=None):
    """單筆候選是否符合某層框檔次 (同縣市 + 同類型 + 房型 + 主建坪 + 權狀坪 + 價格)。"""
    if not c.get('price_wan'):
        return False
    if not (price_lo <= c['price_wan'] <= price_hi):
        return False
    # 跨縣市排除 (永慶 list 偶爾混入跨市推薦物件，如台北豪宅塞進台中 list)
    if anchor_city and c.get('city') and c['city'] != anchor_city:
        return False
    # 同類型 (anchor_type_norm 為空就不卡類型)
    if anchor_type_norm:
        if _normalize_type(c.get('case_type') or "") != anchor_type_norm:
            return False
    # 房型
    if room_set is not None:
        rn = _room_num(c.get('room_count') or "")
        if rn is None or rn not in room_set:
            return False
    # 主建坪 (抓不到坪數的，類型/房型/價格都對就放行，不因缺欄位誤殺)
    area = c.get('main_area_ping')
    if area is not None:
        if not (area_lo <= area <= area_hi):
            return False
    # 權狀坪 (擋豪宅: 主建坪像普通 3 房但權狀超大 = 不同檔次，如寶徠 102 權狀坪)
    b = c.get('building_area_ping')
    if b_lo is not None and b is not None:
        if not (b_lo <= b <= b_hi):
            return False
    return True


def _decoy_split(pool, anchor_price):
    """在 pool 內做 decoy 配貨。
    cheap : price < anchor，由高到低 (越接近 anchor 越好) 取 4。
    pricey: price > anchor，由低到高 (越接近 anchor 越好) 取 1。
    去重 (house_id)。
    """
    seen = set()
    seen_comm = set()
    dedup = []
    for c in pool:
        hid = c.get('house_id')
        comm = (c.get('community_name') or '').strip()
        if hid in seen:
            continue
        if comm and comm in seen_comm:
            continue  # 同社區只取一戶，避免「春風得意」出現兩次的重複感
        seen.add(hid)
        if comm:
            seen_comm.add(comm)
        dedup.append(c)

    cheap_pool = [c for c in dedup if c['price_wan'] < anchor_price]
    pricey_pool = [c for c in dedup if c['price_wan'] > anchor_price]
    # 多取 buffer (cheap 6 / pricey 2)，enrich 後過濾跨市再截前 4/1，避免剔除後不足
    cheap = sorted(cheap_pool, key=lambda c: -c['price_wan'])[:6]
    pricey = sorted(pricey_pool, key=lambda c: c['price_wan'])[:2]
    return cheap, pricey


def recommend(short_url: str) -> dict:
    """
    主入口: 給 ychouse 短網址 → 框檔次 + decoy 配貨 (4 便宜 + 1 貴)。

    Args:
        short_url: e.g. "https://x.ychouse.tw/743CgK"

    Returns:
        dict {
            'anchor': {...},
            'cheap': [...],   # 同檔次、價格 < anchor，最多 4 筆
            'pricey': [...],  # 同檔次、價格 > anchor，最多 1 筆
            'warnings': [...],        # 每層 fallback log + 撈不夠告警
            'tier_conditions': {...}, # 最終實際採用的框檔次條件 + 用到第幾層
        }
    """
    warnings = []

    # ---- Step 1: 抓 anchor ----
    anchor = fetch_anchor(short_url)
    district = anchor.get('district') or ""
    city = anchor.get('city') or '台中市'
    anchor_price = anchor.get('price_wan')
    anchor_room = anchor.get('room_count') or ""
    anchor_area = anchor.get('main_area_ping')
    anchor_type_norm = _normalize_type(anchor.get('type') or "")
    anchor_room_num = _room_num(anchor_room)

    empty = {'anchor': anchor, 'cheap': [], 'pricey': [],
             'warnings': warnings, 'tier_conditions': {}}
    if not district:
        warnings.append("anchor district 是空的,無法構造永慶 list URL")
        return empty
    if anchor_price is None:
        warnings.append("anchor price 是 None,無法做價格 filter")
        return empty
    if anchor_area is None:
        warnings.append("⚠️ anchor 沒有 main_area_ping,坪數帶 filter 會被略過 (只靠類型/房型/價格框)")

    # ---- 共用的價格帶 (所有層一致: 0.6x ~ 1.4x，寬一點讓 decoy 有素材) ----
    price_lo = anchor_price * 0.6
    price_hi = anchor_price * 1.4

    # ---- Step 2: 抓同區 list (L1~L3 都用同區候選池) ----
    same_district = _fetch_list(city, district, warnings)

    # anchor 自己若也在永慶 list 內，先算一次 house_id 以便每層排除
    anchor_house_id = _list_id_of_anchor(anchor, same_district)
    if anchor_house_id:
        warnings.append(f"anchor 自己在永慶 list 內 (house_id={anchor_house_id})，配貨時排除")

    # ---- Step 3: 框檔次階梯 (保同房型優先：寧可跨鄰近區，也不輕易跨房型) ----
    # 客人只認房型 → 跨區比跨房型可接受。階梯:
    #   (1) 同房型: 同區±35% → 同區±50% → 逐步擴鄰近區±50%
    #   (2) 同房型(含跨區) 仍湊不到 2便宜+1貴，才放寬房型±1 (寧缺勿濫)
    def area_band(mult):
        if anchor_area is None:
            return (float('-inf'), float('inf'))
        return (anchor_area * (1 - mult), anchor_area * (1 + mult))

    if anchor_room_num is not None:
        room_exact = {anchor_room_num}
        room_pm1 = {anchor_room_num - 1, anchor_room_num, anchor_room_num + 1}
    else:
        room_exact = None  # 抓不到房型就不卡房型
        room_pm1 = None

    cheap, pricey = [], []
    final_level = final_label = None
    used_area_band = used_room_set = None

    # 同區 list 已抓；其他區 lazy 抓 + 快取，避免重複打
    list_cache = {district: same_district}

    def get_pool(districts):
        missing = [d for d in districts if d not in list_cache]
        if missing:
            # 未快取的區並行抓 (Vercel timeout 友善)
            with ThreadPoolExecutor(max_workers=min(6, len(missing))) as ex:
                results = list(ex.map(lambda x: _fetch_list(city, x, warnings), missing))
            for d, cands in zip(missing, results):
                list_cache[d] = cands
        pool = []
        for d in districts:
            pool.extend(list_cache.get(d, []))
        return pool

    # 權狀坪範圍 (擋豪宅: anchor 權狀 ±，比主建坪帶寬鬆以容同社區戶型差異)
    anchor_building = anchor.get('building_area_ping')
    b_lo = anchor_building * 0.5 if anchor_building else None
    b_hi = anchor_building * 1.6 if anchor_building else None

    def eval_tier(label, cands, room_set, ab, desc):
        lo, hi = ab
        pool = [c for c in cands
                if _passes_tier(c, anchor_type_norm, room_set, lo, hi, price_lo, price_hi,
                                 anchor_city=city, b_lo=b_lo, b_hi=b_hi)
                and c.get('house_id') != anchor_house_id]
        ch, pr = _decoy_split(pool, anchor_price)
        a_lo = "—" if lo == float('-inf') else f"{lo:.1f}"
        a_hi = "—" if hi == float('inf') else f"{hi:.1f}"
        warnings.append(
            f"[{label}] {desc} | 池 {len(pool)} 筆 "
            f"(坪[{a_lo},{a_hi}] 價[{price_lo:.0f},{price_hi:.0f}]) "
            f"-> cheap {len(ch)} / pricey {len(pr)}"
        )
        return ch, pr

    neighbors = _NEIGHBOR_MAP.get(district)
    if neighbors is None:
        # 非台中核心區 → 用「同縣市其他區」當鄰近池 (彰化等)，不誤跳台中
        same_city = _CITY_DISTRICTS.get(city, _TC_DISTRICTS)
        neighbors = [d for d in same_city if d != district]

    # 預熱：一次並行抓「同區 + 前 4 鄰區」填 cache，後續階梯 get_pool 全命中
    # (Vercel timeout 友善 — 把逐區序列抓壓成單次並行)
    get_pool([district] + neighbors[:4])

    def build_stages(room_set, tag):
        """一組階梯: 同區±35% → 同區±50% → 逐步擴鄰近區±50% (房型固定 room_set)。"""
        st = [
            (f"{tag}-同區±35%", room_set, area_band(0.35), [district],
             f"{tag}+同區+同類型+坪±35%"),
            (f"{tag}-同區±50%", room_set, area_band(0.50), [district],
             f"{tag}+同區+同類型+坪±50%"),
        ]
        acc = [district]
        for nb in neighbors:
            acc = acc + [nb]
            st.append((f"{tag}-擴{nb}±50%", room_set, area_band(0.50), list(acc),
                       f"{tag}+擴鄰近區(累加至{nb})+同類型+坪±50%"))
        return st

    def run_stages(stages, target_cheap):
        """跑一組階梯，更新 best (nonlocal)。湊滿 target_cheap 便宜 + 1 貴即 return True。"""
        nonlocal cheap, pricey, final_level, final_label, used_area_band, used_room_set
        for label, room_set, ab, districts, desc in stages:
            cands = get_pool(districts)
            ch, pr = eval_tier(label, cands, room_set, ab, desc)
            if len(ch) + len(pr) > len(cheap) + len(pricey):
                cheap, pricey = ch, pr
                final_level, final_label = label, desc
                used_area_band, used_room_set = ab, room_set
            if len(ch) >= target_cheap and len(pr) >= 1:
                return True
        return False

    # (1) 同房型: 同區 → 逐步擴鄰近區，盡量湊滿 4 便宜 + 1 貴
    run_stages(build_stages(room_exact, "同房型"), target_cheap=4)

    # (2) 同房型(含跨區) 仍湊不到 2 便宜 + 1 貴，才放寬房型±1 (純度優先、寧缺勿濫)
    if not (len(cheap) >= 2 and len(pricey) >= 1):
        warnings.append("同房型(含跨鄰近區)不足 cheap≥2 或無更貴同房型，放寬房型±1")
        run_stages(build_stages(room_pm1, "房型±1"), target_cheap=4)

    # ---- 撈不夠告警 ----
    if len(cheap) < 2:
        warnings.append(f"⚠️ 走完所有 fallback，cheap 仍只有 {len(cheap)} 筆 (<2)")
    if len(pricey) < 1:
        warnings.append(f"⚠️ 走完所有 fallback，pricey 仍 0 筆 (同檔次找不到更貴的)")

    # ---- Step 4: enrich cheap+pricey 補坪數/屋齡 (best-effort，並行，fail 不中斷) ----
    def _enrich_one(c):
        try:
            detail_resp = requests.get(
                f"https://buy.yungching.com.tw/house/{c['house_id']}",
                headers={'User-Agent': _UA},
                timeout=30,
            )
            detail_resp.raise_for_status()
            detail_resp.encoding = 'utf-8'
            detail = parse_detail(detail_resp.text)
            for k, v in detail.items():
                if k in c and c[k] not in (None, "", []):
                    continue  # list 已有值不覆蓋
                c[k] = v
            # city/district 例外: detail 比 list 準 (永慶 list 推薦位會把台北物件標成台中區)
            if detail.get('city'):
                c['city'] = detail['city']
            if detail.get('district'):
                c['district'] = detail['district']
        except Exception as e:
            c['enrich_error'] = str(e)

    _to_enrich = cheap + pricey
    if _to_enrich:
        with ThreadPoolExecutor(max_workers=min(6, len(_to_enrich))) as ex:
            list(ex.map(_enrich_one, _to_enrich))

    # enrich 後雙保險: detail breadcrumb 顯示跨縣市的剔除
    # (list city 可能顯示 list 所在區=台中，但 detail 真實是台北→這裡擋掉)
    if city:
        before = len(cheap) + len(pricey)
        cheap = [c for c in cheap if not (c.get('city') and c['city'] != city)]
        pricey = [c for c in pricey if not (c.get('city') and c['city'] != city)]
        dropped = before - len(cheap) - len(pricey)
        if dropped:
            warnings.append(f"⚠️ enrich 後剔除 {dropped} 筆跨縣市物件 (detail 顯示非 {city})")
    # buffer 過濾後截前 4 便宜 + 1 貴
    cheap = cheap[:4]
    pricey = pricey[:1]
    _to_enrich = cheap + pricey

    # 把封面實景照 (cover_image_url) 排到 image_urls 第 1 張
    # → 點封面開 lightbox 從實景照開始，不會先看到格局圖 (永慶 detail 常把格局圖排前)
    for c in _to_enrich:
        cov = c.get('cover_image_url')
        if cov:
            rest = [u for u in (c.get('image_urls') or []) if u != cov]
            c['image_urls'] = [cov] + rest

    # ---- tier_conditions 摘要 ----
    a_lo, a_hi = (used_area_band or (None, None))
    tier_conditions = {
        "final_level": final_level,
        "description": final_label,
        "anchor_type": anchor.get('type'),
        "anchor_type_normalized": anchor_type_norm,
        "anchor_room": anchor_room,
        "anchor_main_area_ping": anchor_area,
        "anchor_price_wan": anchor_price,
        "price_band_wan": [round(price_lo), round(price_hi)],
        "area_band_ping": (
            None if not used_area_band or a_lo in (float('-inf'),) else
            [round(a_lo, 1), round(a_hi, 1)]
        ),
        "room_set": (sorted(used_room_set) if used_room_set else None),
        "cheap_count": len(cheap),
        "pricey_count": len(pricey),
    }

    return {
        'anchor': anchor,
        'cheap': cheap,
        'pricey': pricey,
        'warnings': warnings,
        'tier_conditions': tier_conditions,
    }


def _list_id_of_anchor(anchor, cands):
    """anchor 自己若也在永慶 list 內，盡量找出它的 house_id 以便排除。
    ycut anchor 沒有永慶 house_id，只能靠 (社區名 + 價格) 模糊比對。
    比對不到回 None (不排除任何東西)。
    """
    a_name = (anchor.get('community_name') or "").strip()
    a_price = anchor.get('price_wan')
    if not a_name or a_price is None:
        return None
    for c in cands:
        title = c.get('title') or ""
        if a_name and a_name in title and c.get('price_wan') == a_price:
            return c.get('house_id')
    return None


# ============================================================================
# CLI smoke test
# ============================================================================

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://x.ychouse.tw/743CgK"
    result = recommend(test_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
