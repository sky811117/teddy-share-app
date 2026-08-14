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

# 競品站解析器(信義/住商/台灣房屋/591/東森/中信/21世紀/太平洋/全國/大家/樂屋/好房)
# 放 api/_competitors.py(底線檔=Vercel 打包但不當 function)。載入失敗不影響主流程。
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import competitors as _COMP
except Exception as _e:
    print(f"competitors module load failed: {_e}")
    _COMP = None

GITHUB_OWNER = "sky811117"
GITHUB_REPO = "teddy-shares"
# 客戶看到的 URL 走 teddy-website Cloudflare Pages，內部 reverse-proxy 到 GitHub Pages
# 流量算進個人網站 + URL 更漂亮（藏掉 sky811117）
PAGES_BASE_URL = "https://teddy-website-blog.pages.dev/share"

# Google Maps Embed API key（卡片底下的「在地圖上看路段位置」用）
# Console: https://console.cloud.google.com/google/maps-apis/credentials
# 需 enable 「Maps Embed API」並鎖 HTTP referrer = https://teddy-website-blog.pages.dev/*
GOOGLE_MAPS_EMBED_KEY = os.getenv("GOOGLE_MAPS_EMBED_KEY", "AIzaSyCr3x28KwPxPnRGsqXSjZC4zxF-7deAK5E")

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

# ============== 永慶房產集團官方前台（有巢氏 / 台慶 / 永義 + 永慶直營）==============
# 景泰的店＝有巢氏台中世界之心加盟店，店碼 0423120888。
# 官方前台 house 頁客戶點進去會看到「承辦店 + 承辦業務電話」——別店/別品牌的物件會露出它店資料，
# 所以：① 帶入只抓物件本身，絕不抓 ShopInfo/Ivr ② 影片/AI 只掛本店 ③ 看完整資訊絕不外連官方前台。
UTRUST_TEDDY_STORE = "0423120888"
# 加盟平台(共用 /api/v2/Buy/Detail)：buy.<host>/house/<caseSId>  域名→品牌
PLATFORM_BRANDS = {
    "u-trust.com.tw": "有巢氏",
    "taiching.com.tw": "台慶",
    "yungyi-house.com.tw": "永義",
}
# 永慶直營(buy.yungching.com.tw)物件資料加密(/api/v2/house 回 base64 密文)，
# 只能靠 og 標籤出「照片+名稱+地址」精簡卡
YUNGCHING_HOST = "yungching.com.tw"


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
    """車位判定 — 客人推薦頁要 chip 篩有/無車位，所以放寬：

    判 has_parking=True 條件（任一即算有車位）:
    - area_str 含「含車位 X 坪」格式
    - area_str 含「含車位」字樣
    - parking_type 或 parking_num 有值且非「無車位」字眼
    判 has_parking=False 條件：
    - parking_type 或 parking_num 明確寫「無車位」
    - 或上述條件全不滿足
    """
    parking_type_raw = (parking_type_raw or "").strip()
    parking_num = (parking_num_raw or "").strip()
    area_has_parking_text = bool(re.search(r"(?<!不)含車位", area_str or ""))
    pm = re.search(r"(?<!不)含車位\s*([\d.]+)\s*坪", area_str or "")
    parking_area_num = float(pm.group(1)) if pm else 0.0

    no_parking = _is_no_parking_text(parking_type_raw) or _is_no_parking_text(parking_num)
    parking_field_has_value = bool(parking_type_raw or parking_num) and not no_parking
    has_parking = (
        not no_parking
        and (parking_area_num > 0 or area_has_parking_text or parking_field_has_value)
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
    building_type = (field("型態") or "").strip()

    # 格局 HTML 結構跟其他欄位不一樣：<span tit_gray>格局</span><div tit_red>5房(室)3廳5衛</div>
    layout_m = re.search(
        r'<span[^>]*tit_gray[^>]*>\s*格局\s*</span>\s*<div[^>]*tit_red[^>]*>\s*([^<]+?)\s*</div>',
        html, re.DOTALL,
    )
    layout = layout_m.group(1).strip() if layout_m else ""
    layout = re.sub(r"\(室\)", "", layout)
    layout = re.sub(r"\s+", "", layout)

    desc = og("og:description") or ""
    pm2 = re.search(r"([\d,]+)\s*萬", desc)
    price = int(pm2.group(1).replace(",", "")) if pm2 else 0

    return {
        "source": "ycut",
        "slug": slug,
        "detail_url": f"https://x.ychouse.tw/{slug}",
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
        "building_type": building_type,
        "address": address,
        "layout": layout,
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


# ---------- buy.u-trust 官方前台解析 ----------

def _http_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def _fill_pic(u):
    """yccdn 圖片網址：補 https、把未填模板 width={0}&height={1} 換成真實尺寸(否則回 500 破圖)。"""
    if not u:
        return ""
    u = ("https:" + u) if u.startswith("//") else u
    return u.replace("{0}", "1024").replace("{1}", "768")


def _platform_shop(host, case_id):
    """查承辦店 → 回 (is_own_store, shop_name)。抓失敗一律當『非本店』(安全：不掛多媒體)。
    is_own 只有『有巢氏(u-trust) + 景泰店碼』才成立 — 台慶/永義/永慶都不是景泰的店。

    ⚠️ 這支只拿來判斷多媒體掛不掛，店名/電話/logo 絕不進客戶頁。
    """
    try:
        d = (_http_json(f"https://buy.{host}/api/v2/Shop/ShopInfo?caseSId={case_id}", timeout=8) or {}).get("data") or {}
    except Exception:
        return False, ""
    shop_url = d.get("shopUrl") or ""
    is_own = (host == "u-trust.com.tw") and (UTRUST_TEDDY_STORE in shop_url)
    return is_own, (d.get("shopName") or "")


def parse_platform_data(data, case_id, host, is_own_store, shop_name, brand):
    """把 buy.<host>/api/v2/Buy/Detail 的 data 轉成統一物件 schema(對齊 parse_ycut_html)。
    有巢氏/台慶/永義共用此路徑。"""
    def n(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    community = (data.get("communityInfo") or {}).get("buildingName") or data.get("caseName") or ""
    price = int(n(data.get("price")))
    floor = int(n(data.get("fromFloor")))
    floor_total = int(n(data.get("upFloor")))
    pin = data.get("pinInfo") or {}
    area = round(n(pin.get("regArea")), 2)
    main_area = round(n(pin.get("mainArea")) + n(pin.get("totalAuxiArea")), 2)
    age = int(n(data.get("buildAge")))

    pinfo = data.get("parkingInfo") or {}
    has_parking = bool(pinfo.get("isParking"))
    if has_parking:
        parking = (pinfo.get("parkingName") or "").strip() or "含車位"
        cm = re.search(r"([\d.]+)\s*坪", str(pin.get("publicCarArea") or ""))
        parking_area = f"{float(cm.group(1)):g} 坪" if cm else "含於主建"
    else:
        parking, parking_area = "無車位", "無車位"

    room, living, bath = int(n(data.get("room"))), int(n(data.get("livingRoom"))), int(n(data.get("bathRoom")))
    layout = "".join(p for p in (
        f"{room}房" if room else "",
        f"{living}廳" if living else "",
        f"{bath}衛" if bath else "",
    ) if p)

    pics = [_fill_pic(pp.get("photoUrl") or "") for pp in ((data.get("pictureInfo") or {}).get("pictures") or [])]
    pics = [u for u in pics if u]

    vr = (data.get("iStagingUrl") or "").strip() or (data.get("iStaging3DUrl") or "").strip()
    video = (data.get("introVideo") or "").strip()
    ai_video = (data.get("aiUrl") or "").strip()

    last_price = int(n(data.get("lastPrice")))
    is_discount = bool(data.get("isDiscount")) and price > 0 and last_price > price

    return {
        "source": "utrust",                  # 加盟平台共用值(有巢氏/台慶/永義)
        "brand": brand,
        "host": host,
        "case_id": str(case_id),
        "slug": f"c{case_id}",               # 合成 id，給追蹤/愛心用
        "detail_url": None,                  # 一律不外連官方前台(會露出承辦店電話)
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
        "building_type": (data.get("caseTypeName") or "").strip(),
        "address": (data.get("address") or "").strip(),
        "layout": layout,
        "og_image": pics[0] if pics else "",
        "og_title": data.get("caseName") or "",
        "og_description": data.get("caseDes") or "",
        # ---- 加值(善用官方前台)----
        "gallery": pics[:8],
        "is_own_store": is_own_store,
        "store_name": shop_name,             # 只給預覽標示用，不進客戶頁
        # VR 環景放寬:別店也給(360 內裝環景，靜態頁未見承辦店電話；景泰 2026-07-17 拍板)
        "vr_url": vr,
        # 影片(YouTube 可能是對方頻道/品牌)、AI 導覽(可能有業務浮水印)→ 仍只本店
        "video_url": video if is_own_store else "",
        "ai_video_url": ai_video if is_own_store else "",
        "last_price": last_price,
        "is_discount": is_discount,
    }


def _fetch_one_platform(host, case_id):
    """加盟平台(有巢氏/台慶/永義)：走 buy.<host>/api/v2/Buy/Detail。"""
    base = f"https://buy.{host}/api/v2"
    try:
        resp = _http_json(f"{base}/Buy/Detail?caseSId={case_id}") or {}
        data = resp.get("data") or {}
        if resp.get("status") != "Success" or not data:
            return {"slug": f"c{case_id}", "error": "官方前台抓取失敗"}
    except Exception as e:
        return {"slug": f"c{case_id}", "error": str(e)}
    is_own, shop_name = _platform_shop(host, case_id)
    brand = PLATFORM_BRANDS.get(host, "永慶房產集團")
    try:
        return parse_platform_data(data, case_id, host, is_own, shop_name, brand)
    except Exception as e:
        return {"slug": f"c{case_id}", "error": f"parse 失敗: {e}"}


def _fetch_one_yungching(case_id):
    """永慶直營(buy.yungching.com.tw)：先走 SSR 詳情頁完整解析出完整卡，
    解析不到才退回 og 精簡卡（行為與 2026-08-14 之前相同，不會更糟）。

    2026-08-14：原本註解說「資料加密只能出精簡卡」——那是指 /api/v2/house 回密文，
    但詳情頁 SSR HTML 本身就有完整欄位（JSON-LD + class 標記），
    _lib.yungching_recommend.parse_detail() 早就寫好那套解析了。零新依賴。

    ⚠️ 客戶頁一律不給的東西（沿用本檔既有原則，別加回來）：
      · address 只到「縣市+區+路段」，不給完整門牌 —— 客戶拿門牌一搜就找到刊登店
      · detail_url=None，不外連官方前台（那頁會露出承辦店名與業務電話）
      · store_name 只寫品牌不寫門市，不抓 ShopInfo / Ivr
    """
    url = f"https://buy.{YUNGCHING_HOST}/house/{case_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return {"slug": f"yc{case_id}", "error": str(e)}

    try:
        from _lib.yungching_recommend import parse_detail
        d = parse_detail(html) or {}
        if d.get("price_wan"):
            return _yungching_full_card(case_id, d)
    except Exception as e:
        print(f"yungching parse_detail failed, fallback to og: {e}")

    return _fetch_one_yungching_og(case_id, html)


def _yungching_full_card(case_id, d):
    """把 parse_detail 的欄位對映成推薦頁的完整卡。"""
    gal = [_fill_pic(u) for u in (d.get("image_urls") or [])]
    gal = [g for g in gal if g][:24]

    fl = ft = 0
    fm = re.match(r"(\d+)\s*/\s*(\d+)", str(d.get("floor") or ""))
    if fm:
        fl, ft = int(fm.group(1)), int(fm.group(2))

    # 地址只到路段（不含巷弄號）——這是「客戶不會繞過你去找刊登店」的關鍵
    addr = " ".join(x for x in (d.get("city"), d.get("district"), d.get("street")) if x)

    rooms = d.get("room_count")
    layout = ""
    if rooms and str(rooms) != "--":
        layout = f"{rooms}房"
        if d.get("hall_count"):
            layout += f"{d['hall_count']}廳"
        if d.get("bath_count"):
            layout += f"{d['bath_count']}衛"

    pk = d.get("parking") or ""
    pa = d.get("parking_area_ping")
    if pk or pa:
        has_parking, parking = True, (pk or "含車位")
        parking_area = f"{pa:g} 坪" if pa else "含於主建"
    else:
        has_parking, parking, parking_area = False, "無車位", "無車位"

    title = d.get("title") or ""
    return {
        "source": "yungching",
        "brand": "永慶",
        "host": YUNGCHING_HOST,
        "case_id": str(case_id),
        "slug": f"yc{case_id}",
        "detail_url": None,
        "lite": False,
        "community_display": d.get("community_name") or title or "永慶物件",
        "price": d.get("price_wan") or 0,
        "floor": fl,
        "floor_total": ft,
        "area": d.get("building_area_ping") or 0.0,
        "main_area": d.get("main_area_ping") or 0.0,
        "age": d.get("building_age") or 0,
        "has_parking": has_parking,
        "parking": parking,
        "parking_area": parking_area,
        "building_type": d.get("case_type") or "",
        "address": addr,
        "layout": layout,
        "og_image": gal[0] if gal else "",
        "og_title": title,
        "og_description": d.get("subtitle") or "",
        "gallery": gal,
        "is_own_store": False,
        "store_name": "永慶房屋",
        "vr_url": "", "video_url": "", "ai_video_url": "",
        "last_price": 0, "is_discount": False,
        "unit_price": d.get("unit_price_wan_per_ping") or 0,
        "land_area": d.get("land_area_ping") or 0,
    }


def _fetch_one_yungching_og(case_id, html):
    """退路：SSR 解析不出來時的 og 精簡卡（原本的行為）。"""

    def og(prop):
        m = re.search(rf'<meta property="{re.escape(prop)}" content="([^"]+)"', html)
        return m.group(1).replace("&amp;", "&") if m else ""

    title, desc, img = og("og:title"), og("og:description"), _fill_pic(og("og:image"))
    if not title and not img:
        return {"slug": f"yc{case_id}", "error": "永慶頁解析失敗"}

    # og:title 尾巴是「… | 買房 | 永慶房屋」— 砍掉品牌/樣板段，避免幫競品打廣告
    # ⚠️ 集團五個品牌名都要列全,漏一個就會留在客戶看到的標題尾巴上
    #    （2026-08-14 實測漏了「永慶不動產」,客戶頁標題結尾就掛著它）
    _JUNK = {"買房", "賣屋", "租屋",
             "永慶房屋", "永慶不動產", "永慶房產集團", "永慶",
             "台慶不動產", "台慶", "永義房屋", "永義",
             "有巢氏房屋", "有巢氏", "永慶房仲網"}
    segs = [s.strip() for s in re.split(r"[|｜]", title) if s.strip() and s.strip() not in _JUNK]
    title = " · ".join(segs)
    community = (segs[0] if segs else "") or "永慶物件"
    am = re.search(r"位於(.+?)[，,、]", desc)
    address = am.group(1).strip() if am else ""
    rm = re.search(r"(\d+)\s*房", desc)
    layout = f"{rm.group(1)}房" if rm else ""
    pm = re.search(r"([\d,]+)\s*萬", f"{desc} {title}")
    price = int(pm.group(1).replace(",", "")) if pm else 0

    return {
        "source": "yungching",
        "brand": "永慶",
        "host": YUNGCHING_HOST,
        "case_id": str(case_id),
        "slug": f"yc{case_id}",
        "detail_url": None,
        "lite": True,                        # 精簡卡:資料加密，只有照片+名稱+地址
        "community_display": community,
        "price": price,
        "floor": 0, "floor_total": 0, "area": 0.0, "main_area": 0.0, "age": 0,
        "has_parking": None, "parking": "", "parking_area": "",
        "building_type": "", "address": address, "layout": layout,
        "og_image": img, "og_title": title, "og_description": desc,
        "gallery": [img] if img else [],
        "is_own_store": False, "store_name": "永慶房屋",
        "vr_url": "", "video_url": "", "ai_video_url": "",
        "last_price": 0, "is_discount": False,
    }


def _fetch_one_ref(ref):
    """ref = {'source': 'ycut'|'utrust'|'yungching', 'id':..., 'host':...}。相容：字串→ycut slug。"""
    if isinstance(ref, str):
        return _fetch_one_full(ref)
    s = ref.get("source")
    if s == "utrust":
        host = ref.get("host", "u-trust.com.tw")
        r = _fetch_one_platform(host, ref["id"])
        # 集團四品牌共用 caseSId,但只有「該品牌自己簽的委託」才會在該品牌站上架。
        # 例:太子雲世紀同一戶,景泰簽的 7070179 有巢氏站查得到,永慶直營的人簽的
        # 7451461 就只在永慶站。有巢氏站查無時退回永慶直營,避免整張卡開天窗。
        if (not r or r.get("error")) and host != YUNGCHING_HOST:
            fb = _fetch_one_yungching(ref["id"])
            if fb and not fb.get("error"):
                return fb
        return r
    if s == "yungching":
        return _fetch_one_yungching(ref["id"])
    if s == "external":
        if _COMP:
            return _COMP.fetch_external(ref["url"])
        return {"slug": "", "error": "competitors module unavailable"}
    return _fetch_one_full(ref["id"])


def _merge_dup(keep, other):
    """同一物件被同時貼了 ycut + 官方前台 → 合併進 keep(先到的那筆，就地改)。
    外連走 ycut(乾淨、掛景泰)；美圖/相簿/多媒體走 utrust。"""
    y = keep if keep.get("source") == "ycut" else (other if other.get("source") == "ycut" else None)
    u = keep if keep.get("source") == "utrust" else (other if other.get("source") == "utrust" else None)
    if y and y.get("detail_url"):
        keep["detail_url"] = y["detail_url"]
        keep["slug"] = y.get("slug", keep.get("slug"))   # 追蹤沿用 ycut slug
    if u:
        if u.get("og_image"):
            keep["og_image"] = u["og_image"]
        for k in ("gallery", "vr_url", "video_url", "ai_video_url",
                  "is_own_store", "store_name", "last_price", "is_discount", "case_id"):
            if u.get(k):
                keep[k] = u[k]
        for k in ("main_area", "layout", "parking", "parking_area", "building_type", "og_description"):
            if not keep.get(k) and u.get(k):
                keep[k] = u[k]
        keep["source"] = "merged"
    return keep


def fetch_full_batch(refs):
    raw = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for r in pool.map(_fetch_one_ref, refs):
            if r.get("error") or not r.get("community_display"):
                continue
            if not r.get("price") and not r.get("lite"):   # 精簡卡(永慶直營)允許無價格
                continue
            # 欄位級電話硬保險:社區/標題/地址/型態的電話一律清(連區塊標題都乾淨,競品業務電話零外洩)
            for _k in ("community_display", "og_title", "address", "building_type", "layout"):
                if r.get(_k):
                    r[_k] = _CARD_PHONE_RE.sub("", str(r[_k])).strip(" ·,，、-")
            raw.append(r)

    items, by_fp = [], {}
    for it in raw:
        fp = (
            it.get("community_display", "").strip(),
            it.get("floor", 0),
            it.get("floor_total", 0),
            round(it.get("area", 0), 2),
            it.get("price", 0),
        )
        if fp in by_fp:
            _merge_dup(by_fp[fp], it)   # 就地合併(by_fp[fp] 已在 items 內)
            continue
        by_fp[fp] = it
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


_REF_RE = re.compile(
    r"https?://buy\.(?P<host>u-trust\.com\.tw|taiching\.com\.tw|yungyi-house\.com\.tw|yungching\.com\.tw)/house/(?P<cid>\d+)"
    r"|https?://x\.ychouse\.tw/(?P<slug>[A-Za-z0-9]+?)(?=https?://|[^A-Za-z0-9]|$)"
)


def extract_refs(text):
    """混貼辨識：ycut 短網址 + 永慶集團官方前台(有巢氏/台慶/永義/永慶直營)都吃，保留貼上順序、去重。
    回傳 [{'source': 'ycut'|'utrust'|'yungching', 'id':..., 'host':...}, ...]"""
    text = text or ""
    hits = []  # (start_pos, key, ref)
    for m in _REF_RE.finditer(text):
        host = m.group("host")
        if host:
            cid = m.group("cid")
            src = "yungching" if host == YUNGCHING_HOST else "utrust"
            hits.append((m.start(), (host, cid), {"source": src, "id": cid, "host": host}))
        else:
            slug = m.group("slug")
            hits.append((m.start(), ("ycut", slug), {"source": "ycut", "id": slug}))
    # 競品站(信義/住商/…)：交給 _competitors 掃描，依位置一起排序去重
    if _COMP:
        try:
            for pos, url, brand in _COMP.scan_external(text):
                hits.append((pos, ("ext", url), {"source": "external", "brand": brand, "url": url}))
        except Exception as e:
            print(f"scan_external error: {e}")
    hits.sort(key=lambda x: x[0])
    refs, seen = [], set()
    for _pos, key, ref in hits:
        if key not in seen:
            seen.add(key)
            refs.append(ref)
    return refs


def gen_share_id():
    return re.sub(r"[^a-zA-Z0-9]", "", secrets.token_urlsafe(8))[:8]


# ============== HTML 賣點清理 ==============

def clean_tagline(og_title):
    if not og_title:
        return ""
    s = og_title.strip().strip("【】")
    s = re.sub(r"^\^[^專]*", "", s)
    s = re.sub(r"^[\U0001F300-\U0001FAFF☀-➿✀-➿🎀🌞🔥]+", "", s)
    # 委託類型字眼一律清掉 —— 那是刊登業務寫在廣告標題裡的內部資訊,
    # 客戶不需要知道這件案子是專任還是一般約(景泰 2026-08-14 明確要求)。
    # 原本只清「專簽/獨家」且只清開頭,實測「專任 · 富宇松禾苑…」整個漏出去。
    _MD = r"(專任委託|專任|專簽|專委|專營|專賣|四品牌獨家|四品牌|品牌獨家|獨家|獨賣|獨售)"
    s = re.sub(r"^\s*" + _MD + r"\s*[/／｜|·、\-]*\s*", "", s)          # 開頭
    s = re.sub(r"\s*[·｜|/／、]\s*" + _MD + r"(?=\s|$)", "", s)         # 中間或結尾的獨立片段
    s = re.sub(r"[\U0001F300-\U0001FAFF☀-➿✀-➿🎀🌞🔥]+$", "", s).strip()
    s = re.sub(r"[┃｜|/／◆]+", " · ", s)
    s = re.sub(r"\s*·\s*", " · ", s)
    return re.sub(r"\s+", " ", s).strip(" ·")


# ============== HTML 產生 ==============

# 物件卡「電話硬保險」：卡片本體(非景泰聯絡區)絕不該出現任何電話。
# 不管來源/parser 怎麼變，卡片輸出前一律把台灣電話格式清掉 → 競品業務電話零外洩。
# (景泰自己的電話在 hero/footer 聯絡區塊，不經過 card_html，不受影響)
_CARD_PHONE_RE = re.compile(
    r'(?<![\d])'
    r'(?:'
    r'\(0\d{1,2}\)\s?\d{3,4}[-\s]?\d{3,4}'      # (04)2315-0909
    r'|0\d{1,2}[-\s]\d{3,4}[-\s]?\d{3,4}'        # 04-2315-0909 / 04-23150909
    r'|09\d{2}[-\s]?\d{3}[-\s]?\d{3}'            # 0955-729-125
    r'|09\d{8}'                                   # 0955729125
    r')(?:\s*[#轉分機]+\s*\d{1,5})?'
    r'(?![\d])'
)


def _strip_card_phones(card_html_str):
    return _CARD_PHONE_RE.sub("", card_html_str or "")


def card_html(p):
    # 硬保險:卡片本體輸出前一律清掉任何電話號碼(競品業務電話零外洩)
    return _strip_card_phones(_card_html_raw(p))


def _card_html_raw(p):
    img = p.get("og_image") or ""
    tagline = clean_tagline(p.get("og_title", "")) or p.get("community_display", "")
    district = parse_district(p.get("address", ""))
    tier_key = price_to_tier_key(p.get("price", 0))
    age_key = age_to_tier_key(p.get("age"))
    community = (p.get("community_display") or "").strip()

    # 永慶直營精簡卡:物件資料加密，只有封面+名稱+格局+地址(封面可點放大)，不硬塞空的價格/坪數欄位。
    # 刻意不顯示「永慶」品牌名給客戶 — 跟不露承辦店一致，不幫別家打廣告。
    if p.get("lite"):
        slug = p.get("slug", "")
        layout_l = (p.get("layout") or "").strip()
        addr_l = (p.get("address") or "").strip()
        if img:
            img_l = f'<img src="{img}" loading="lazy" decoding="async" alt="" />'
        elif p.get("no_clean_photo") or p.get("source") == "external":
            img_l = '<div class="card-noimg"><span class="card-noimg-ic">📸</span><span>更多實景照片 · 洽景泰</span></div>'
        else:
            img_l = ''
        price_l = (f'<div class="card-price-row"><div><span class="card-price">{p["price"]:,}</span>'
                   f'<span class="card-price-unit">萬</span></div></div>') if p.get("price") else ''
        layout_l2 = layout_l + (("　·　屋齡 %s 年" % p["age"]) if p.get("age") else "")
        layout_html = f'<div class="card-lite-spec">🛏️ {layout_l2}</div>' if layout_l else ''
        addr_html = f'<div class="card-address">{addr_l}</div>' if addr_l else ''
        return f'''
    <div class="card" data-slug="{slug}" data-district="{district}" data-price-tier="{tier_key}" data-age-tier="{age_key}" data-parking="" data-type="{(p.get("building_type") or "其他").strip() or "其他"}" data-unit-price-tier="" data-rooms="{layout_to_rooms_key(p.get("layout"))}" data-community="{community}">
      <div class="card-image">{img_l}</div>
      <div class="card-content">
        <div class="card-tagline">{tagline}</div>
        {price_l}
        {layout_html}
        {addr_html}
        <div class="card-actions">
          <button class="card-like card-like-full" data-slug="{slug}" data-name="{tagline}" aria-label="我喜歡這間" type="button">
            <span class="card-like-icon">♡</span>
            <span class="card-like-text">我喜歡</span>
          </button>
        </div>
      </div>
    </div>'''

    has_parking = p.get("has_parking")
    if has_parking is None:
        has_parking = not (
            _is_no_parking_text(p.get("parking", ""))
            or _is_no_parking_text(p.get("parking_area", ""))
        )
    # 透天/別墅不標「含車位」(車位欄位一律不顯示,價格也不標)
    _house = any(k in (p.get("building_type") or "") for k in ("透天", "別墅", "透店", "店透", "農舍"))
    price_unit = "萬 含車位" if (has_parking and not _house) else "萬"
    unit_price = unit_price_per_area(p.get("price"), p.get("area"))
    unit_price_html = (
        f'<div class="card-unit-price">單坪 <strong>{unit_price:g}</strong> 萬 / 權狀坪</div>'
        if unit_price else ''
    )
    if img:
        img_html = f'<img src="{img}" loading="lazy" decoding="async" alt="" />'
    elif p.get("no_clean_photo") or p.get("source") == "external":
        # 競品站的圖有品牌浮水印不放 → 乾淨佔位(景泰可另補自家實拍)
        img_html = '<div class="card-noimg"><span class="card-noimg-ic">📸</span><span>更多實景照片 · 洽景泰</span></div>'
    else:
        img_html = ''
    note = (p.get("note") or "").strip()
    if note:
        safe_note = (note
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("\n", "<br>"))
        note_html = f'<div class="card-note"><div class="card-note-label">📝 景泰提醒</div><div class="card-note-body">{safe_note}</div></div>'
    else:
        note_html = ''
    parking_attr = 'yes' if has_parking else 'no'
    building_type = (p.get("building_type") or "").strip()
    type_attr = building_type if building_type else "其他"
    unit_tier_key = unit_price_to_tier_key(unit_price)
    rooms_key = layout_to_rooms_key(p.get("layout"))
    slug = p.get("slug", "")

    # 降價徽章(官方前台有原價且高於現價才顯示)
    badge_html = ''
    if p.get("is_discount") and p.get("last_price") and p.get("price"):
        cut = p["last_price"] - p["price"]
        if cut > 0:
            badge_html = f'<div class="card-badge">↓ 降 {cut:,} 萬</div>'

    # 相簿橫向縮圖(官方前台多圖，≥2 張才顯示；只含物件實景，不含它店資料)
    gallery = [g for g in (p.get("gallery") or []) if g]
    gallery_html = ''
    photocount_html = ''
    if len(gallery) > 1:
        # 全部攤開（上限 24 張純粹是防呆），客戶往下滑就看完，不必橫滑也不必點
        thumbs = ''.join(
            f'<img src="{u}" loading="lazy" decoding="async" alt="" />' for u in gallery[:24]
        )
        gallery_html = f'<div class="card-gallery" aria-label="物件實景照片">{thumbs}</div>'
        photocount_html = f'<div class="card-photo-count">📷 {len(gallery)} 張實景</div>'

    # 多媒體按鈕(VR/影片/AI — 只有景泰本店的物件才掛，避免露出它店品牌)
    media_btns = []
    if p.get("vr_url"):
        media_btns.append(f'<a class="card-media-btn" href="{p["vr_url"]}" target="_blank" rel="noopener" data-media="vr">🏠 VR 環景</a>')
    if p.get("video_url"):
        media_btns.append(f'<a class="card-media-btn" href="{p["video_url"]}" target="_blank" rel="noopener" data-media="video">▶ 物件影片</a>')
    if p.get("ai_video_url"):
        media_btns.append(f'<a class="card-media-btn" href="{p["ai_video_url"]}" target="_blank" rel="noopener" data-media="ai">✨ AI 導覽</a>')
    media_html = f'<div class="card-media">{"".join(media_btns)}</div>' if media_btns else ''

    # 「看完整資訊」— 有 detail_url(ycut/合併) 才外連；純官方前台/競品不外連(改軟性洽詢)
    detail_url = p.get("detail_url")
    if detail_url:
        cta_html = (f'<a class="card-cta" href="{detail_url}" target="_blank" rel="noopener">'
                    f'看完整資訊 與 全部照片 <span class="card-cta-arrow">→</span></a>')
    else:
        cta_html = ''   # 無外連時不放軟性 CTA(景泰要求),只留「我喜歡」
    like_full = '' if detail_url else ' card-like-full'

    # 規格欄位改條件式:有值才顯示(競品/官方前台缺的欄位不留空格)
    _cells = []
    if p.get("area"):
        _cells.append(f'<div class="spec-item"><span class="spec-label">權狀坪數</span><span class="spec-value highlight">{p["area"]} 坪</span></div>')
    if p.get("main_area"):
        _cells.append(f'<div class="spec-item"><span class="spec-label">主+附</span><span class="spec-value">{p["main_area"]} 坪</span></div>')
    if (p.get("layout") or "").strip():
        _cells.append(f'<div class="spec-item"><span class="spec-label">格局</span><span class="spec-value">{p["layout"]}</span></div>')
    if p.get("age"):
        _cells.append(f'<div class="spec-item"><span class="spec-label">屋齡</span><span class="spec-value">{p["age"]} 年</span></div>')
    # 透天/別墅本來就自帶車庫,車位欄位意義不大又常誤判 → 一律不顯示車位/車位坪數
    _is_house = any(k in (p.get("building_type") or "") for k in ("透天", "別墅", "透店", "店透", "農舍"))
    if not _is_house:
        _pk = (str(p.get("parking") or "")).strip()
        if _pk:
            _cells.append(f'<div class="spec-item"><span class="spec-label">車位</span><span class="spec-value">{_pk}</span></div>')
        _pa = (str(p.get("parking_area") or "")).strip()
        if _pa and _pa not in ("無車位", "—"):
            _cells.append(f'<div class="spec-item"><span class="spec-label">車位坪數</span><span class="spec-value">{_pa}</span></div>')
    spec_cells = "".join(_cells)

    if p.get("floor_total"):
        floor_html = f'<div class="card-floor">{p["floor"]}F / {p["floor_total"]}F</div>'
    elif p.get("floor"):
        floor_html = f'<div class="card-floor">{p["floor"]}F</div>'
    else:
        floor_html = ''

    # 物件介紹(remark 洗白後)—有值才顯示，inline style 不依賴 CSS 區
    _intro = (p.get("intro") or "").strip()
    if _intro:
        _si = (_intro.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"))
        intro_html = ('<div class="card-intro" style="margin:12px 0;padding:13px 15px;'
                      'background:var(--bg-soft);border-radius:10px;border-left:3px solid var(--wood-light);">'
                      '<div style="font-size:13px;font-weight:700;color:var(--wood-deep);margin-bottom:6px;">📋 物件介紹</div>'
                      f'<div style="font-size:14px;line-height:1.75;color:#5a4c38;white-space:normal;">{_si}</div></div>')
    else:
        intro_html = ''

    return f'''
    <div class="card" data-slug="{slug}" data-district="{district}" data-price-tier="{tier_key}" data-age-tier="{age_key}" data-parking="{parking_attr}" data-type="{type_attr}" data-unit-price-tier="{unit_tier_key}" data-rooms="{rooms_key}" data-community="{community}">
      <div class="card-image">{badge_html}{photocount_html}{img_html}</div>
      <div class="card-content">
        <div class="card-tagline">{tagline}</div>
        <div class="card-price-row">
          <div><span class="card-price">{p["price"]:,}</span><span class="card-price-unit">{price_unit}</span></div>
          {floor_html}
        </div>
        {unit_price_html}
        {intro_html}
        <div class="card-spec">{spec_cells}</div>
        {gallery_html}
        <div class="card-address">{p["address"]}</div>
        <div class="card-map">
          <div class="card-map-head"><span class="card-map-icon">🗺️</span><span class="card-map-label">路段位置</span></div>
          <div class="card-map-frame"><iframe data-q="{p["address"]}" loading="lazy" referrerpolicy="no-referrer-when-downgrade" title="路段位置地圖"></iframe></div>
        </div>
        {note_html}
        {media_html}
        <div class="card-actions">
          {cta_html}
          <button class="card-like{like_full}" data-slug="{slug}" data-name="{tagline}" aria-label="我喜歡這間" type="button">
            <span class="card-like-icon">♡</span>
            <span class="card-like-text">我喜歡</span>
          </button>
        </div>
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

# 單坪價段 — 給客人按「權狀坪單價」分段選
UNIT_PRICE_TIERS = [
    ("lt25", "< 25 萬/坪", 0, 25),
    ("25-30", "25-30 萬/坪", 25, 30),
    ("30-35", "30-35 萬/坪", 30, 35),
    ("35-45", "35-45 萬/坪", 35, 45),
    ("gt45", "> 45 萬/坪", 45, 9999),
]

# 房數段 — 從 layout 字串 "3房2廳2衛" 抽第一個數字
ROOMS_TIERS = [
    ("1", "1 房", 1, 2),
    ("2", "2 房", 2, 3),
    ("3", "3 房", 3, 4),
    ("4", "4 房", 4, 5),
    ("gt5", "5 房以上", 5, 99),
]


def layout_to_rooms_key(layout):
    """格局字串 → rooms tier key。'3房2廳2衛' → '3'；'6房...' → 'gt5'；空/解析失敗 → 'unknown'"""
    if not layout:
        return "unknown"
    m = re.match(r"(\d+)\s*房", layout)
    if not m:
        return "unknown"
    n = int(m.group(1))
    if n <= 0:
        return "unknown"
    if n >= 5:
        return "gt5"
    return str(n)


def unit_price_to_tier_key(unit_price):
    """單坪價 → tier key"""
    if unit_price is None or unit_price <= 0:
        return "unknown"
    for key, _, lo, hi in UNIT_PRICE_TIERS:
        if lo <= unit_price < hi:
            return key
    return "unknown"


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


def unit_price_per_area(price, area):
    """單坪價（權狀）= 總價 / 權狀坪數。回傳 None 表示無法計算。"""
    try:
        p = float(price or 0)
        a = float(area or 0)
        if p > 0 and a > 0:
            return round(p / a, 2)
    except (TypeError, ValueError):
        pass
    return None


def community_subsection_html(community, items):
    """單一社區的 sub-section（在區裡面）— 物件按單坪價低到高（fallback: 總價低到高）"""
    def _sort_key(x):
        up = unit_price_per_area(x.get("price"), x.get("area"))
        # 算得出單坪價的優先按單坪價排，算不出的用大數字推到後面再用總價排
        return (0, up) if up is not None else (1, x.get("price", 0))
    items_sorted = sorted(items, key=_sort_key)
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
    """單一行政區的 section，內含多個社區 sub-section
    分兩組：住宅（大樓/華廈/公寓/未知）+ 透天/別墅
    （透天獨立拉出來避免壓掉其他類型）"""
    all_props = [p for comm in communities_dict.values() for p in comm]
    total_count = len(all_props)
    prices = sorted(p["price"] for p in all_props)
    price_range = f"{prices[0]:,} 萬" if len(prices) == 1 else f"{prices[0]:,} ~ {prices[-1]:,} 萬"
    ages = sorted({p["age"] for p in all_props if p.get("age")})
    age_text = ""
    if ages:
        age_text = f"屋齡 {ages[0]} 年" if len(ages) == 1 else f"屋齡 {ages[0]}~{ages[-1]} 年"

    # 分兩組：住宅 vs 透天/別墅
    TOWNHOUSE = {'透天', '別墅'}
    residential_props = [p for p in all_props if (p.get('building_type') or '').strip() not in TOWNHOUSE]
    townhouse_props = [p for p in all_props if (p.get('building_type') or '').strip() in TOWNHOUSE]

    def group_by_community(props):
        d = {}
        for p in props:
            c = (p.get('community_display') or '').strip() or '其他'
            d.setdefault(c, []).append(p)
        return d

    def render_group(props, label, emoji):
        if not props:
            return ''
        comm_dict = group_by_community(props)
        order = sorted(comm_dict.keys(), key=lambda c: min(p["price"] for p in comm_dict[c]))
        subs = "\n".join(community_subsection_html(c, comm_dict[c]) for c in order)
        return f'''
  <div class="type-group">
    <div class="type-group-header">{emoji} {label}（{len(props)} 戶）</div>
    {subs}
  </div>'''

    sub_sections = (
        render_group(residential_props, '住宅', '🏢') +
        render_group(townhouse_props, '透天 / 別墅', '🏠')
    )

    # community_order 用全部物件（給 chip 預覽用）
    community_order = sorted(
        communities_dict.keys(),
        key=lambda c: min(p["price"] for p in communities_dict[c])
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
    gen_date = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    theme = f"{theme} · {gen_date}"
    signature = f"{signature} · {gen_date}"

    # 官網推廣區塊(認識景泰/房仲官網)顯示條件:製作人是景泰本人 且 沒勾「隱藏官網推廣」。
    # 白牌給別人用(名字非景泰) 或 明確要求隱藏 → 都不出現。
    _is_teddy = (contact.get("agent_name") or "").strip() in ("", "陳景泰")
    _show_promo = _is_teddy and not client_data.get("hide_promo")
    footer_site_html = (
        '<div class="footer-site">'
        '<div class="footer-site-title">🏡 想看更多好屋？歡迎逛逛我的房仲官網</div>'
        '<div class="footer-site-btns">'
        '<a class="footer-site-btn primary" href="https://teddy-website-blog.pages.dev/properties" target="_blank" rel="noopener">在售物件</a>'
        '<a class="footer-site-btn" href="https://teddy-website-blog.pages.dev/about" target="_blank" rel="noopener">認識景泰</a>'
        '<a class="footer-site-btn" href="https://teddy-website-blog.pages.dev/" target="_blank" rel="noopener">房仲官網</a>'
        '</div></div>'
    ) if _show_promo else ''
    # IG 是景泰個人帳號(無表單欄位可換)→ 白牌 或 隱藏推廣時 都不出現
    ig_html = (
        f'<a class="contact-item" href="{contact["ig_url"]}" target="_blank"><span class="contact-icon">📷</span><span>IG：{contact["ig"]}</span></a>'
        if _show_promo else ''
    )

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

    # 行政區順序（景泰指定）：北屯 太平 北區 西屯 南屯 南區 東區 大里 烏日 後面隨意按最低價
    PRIORITY_ORDER = ['北屯區', '太平區', '北區', '西屯區', '南屯區', '南區', '東區', '大里區', '烏日區']
    def district_sort_key(d):
        if d in PRIORITY_ORDER:
            return (0, PRIORITY_ORDER.index(d))
        # 不在優先列表的（西區/潭子等）按該區最低價排
        min_price = min(p["price"] for comm in districts[d].values() for p in comm)
        return (1, min_price)
    district_order = sorted(districts.keys(), key=district_sort_key)
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

    # 單坪價段 chip — 給客人按權狀坪單價分段篩
    all_unit_chips = []
    for key, label, lo, hi in UNIT_PRICE_TIERS:
        n = 0
        for d in districts.values():
            for c in d.values():
                for p in c:
                    up = unit_price_per_area(p.get("price"), p.get("area"))
                    if up is not None and lo <= up < hi:
                        n += 1
        if n > 0:
            all_unit_chips.append(
                f'<a class="nav-chip unit-nav-chip filter-chip" data-filter-unit="{key}">'
                f'{label}<span class="nav-chip-count">{n}</span></a>'
            )
    unit_filter_chips = "\n    ".join(all_unit_chips)
    has_unit_filter = len(all_unit_chips) >= 2

    # 房數 chip — 給客人按「幾房」篩
    all_rooms_chips = []
    for key, label, lo, hi in ROOMS_TIERS:
        n = 0
        for d in districts.values():
            for c in d.values():
                for p in c:
                    rk = layout_to_rooms_key(p.get("layout"))
                    if rk == "unknown":
                        continue
                    # gt5 一律 hi=99；其他 key 是純數字
                    if key == "gt5":
                        if rk == "gt5":
                            n += 1
                    elif rk == key:
                        n += 1
        if n > 0:
            all_rooms_chips.append(
                f'<a class="nav-chip rooms-nav-chip filter-chip" data-filter-rooms="{key}">'
                f'{label}<span class="nav-chip-count">{n}</span></a>'
            )
    rooms_filter_chips = "\n    ".join(all_rooms_chips)
    has_rooms_filter = len(all_rooms_chips) >= 2

    # 車位 chip 列（給 sticky-nav 第四行）
    yes_count = sum(1 for d in districts.values() for c in d.values() for p in c if p.get("has_parking"))
    no_count = sum(1 for d in districts.values() for c in d.values() for p in c if not p.get("has_parking"))
    parking_chips_list = []
    if yes_count > 0:
        parking_chips_list.append(
            f'<a class="nav-chip parking-nav-chip filter-chip" data-filter-parking="yes">有車位<span class="nav-chip-count">{yes_count}</span></a>'
        )
    if no_count > 0:
        parking_chips_list.append(
            f'<a class="nav-chip parking-nav-chip filter-chip" data-filter-parking="no">無車位<span class="nav-chip-count">{no_count}</span></a>'
        )
    parking_filter_chips = "\n    ".join(parking_chips_list)
    has_parking_filter = len(parking_chips_list) >= 1  # 永遠顯示車位 chip（即使全部同邊）

    # 物件類型 chip（大樓 / 華廈 / 透天 / 別墅 / 公寓 / 其他）
    TYPE_ORDER = ['大樓', '華廈', '透天', '別墅', '公寓', '其他']
    type_counts = {t: 0 for t in TYPE_ORDER}
    for d in districts.values():
        for c in d.values():
            for p in c:
                bt = (p.get('building_type') or '').strip() or '其他'
                # 把 ycut 變體歸類進主類別
                if bt not in TYPE_ORDER:
                    bt = '其他'
                type_counts[bt] += 1
    type_chips_list = []
    type_total = sum(type_counts.values())
    if type_total > 0:
        # 「全部」chip 放最前 — 客人篩到 0 筆時可一鍵回全部類型
        type_chips_list.append(
            f'<a class="nav-chip type-nav-chip filter-chip type-all-chip" data-filter-type="__all__">全部<span class="nav-chip-count">{type_total}</span></a>'
        )
    for t in TYPE_ORDER:
        n = type_counts.get(t, 0)
        if n > 0:
            type_chips_list.append(
                f'<a class="nav-chip type-nav-chip filter-chip" data-filter-type="{t}">{t}<span class="nav-chip-count">{n}</span></a>'
            )
    type_filter_chips = "\n    ".join(type_chips_list)
    has_type_filter = len(type_chips_list) >= 2  # 全部 + 至少 1 種類型才顯示

    total_count = len(properties)

    # 單一物件走專屬的一頁式版型（景泰 2026-08-14：「單筆的就另外做一個版本，
    # 一頁式的，不要再用這個網頁」）。多物件那套的行政區標題、社區分組、
    # 「含 1 個社區」、「住宅（1 戶）」對單一物件全是累贅，而且社區各包一個
    # .grid 會讓唯一那張卡卡在三欄的第一欄、右邊白掉一大片。
    if total_count == 1:
        sections = '<div class="single-wrap">%s</div>' % card_html(properties[0])
    else:
        sections = "\n".join(
            district_section_html(d, districts[d], district_anchors[d])
            for d in district_order
        )

    community_count = sum(len(c) for c in districts.values())
    district_count = len(districts)
    prices = sorted(p["price"] for p in properties)
    price_min, price_max = prices[0], prices[-1]
    today = datetime.date.today().strftime("%Y.%m.%d")

    # 預覽卡（客戶在 LINE / 訊息裡先看到的那張）——第一張有效照片當封面。
    # 沒有 og:image 的話那張卡只有文字，客戶滑過去不會點。
    og_cover = ""
    for _p in properties:
        og_cover = (_p.get("og_image") or "").strip() or next(
            (g for g in (_p.get("gallery") or []) if g), "")
        if og_cover:
            break
    _pr = (f"{price_min:,}–{price_max:,} 萬" if price_min != price_max
           else f"{price_min:,} 萬")
    # 沒填客戶稱呼 = 通用連結（可以轉發給多人），標題與內文都不出現「給 XX 的」
    _nm = (client_data.get("name") or "").strip()
    _nd = (client_data.get("need") or "").strip()
    og_desc = '｜'.join(x for x in (_nd, f'{total_count} 戶 · {_pr}') if x)
    page_title = (f'{theme} · 給 {_nm} 的 {total_count} 戶整理' if _nm
                  else f'{theme} · {total_count} 戶精選')
    hero_h1 = f'{total_count} 戶 客製整理' if _nm else f'{total_count} 戶 精選'
    for_client_line = ' · '.join(
        x for x in ((f'給 {_nm} 的專屬精選' if _nm else ''), _nd) if x)

    # 單物件的 hero：不要「1 區 · 1 個社區」「1 行政區 1 優質社區」這種湊數統計
    if total_count == 1:
        _p0 = properties[0]
        _loc = _p0.get('community_display') or _p0.get('address') or ''
        hero_summary = '%s<br>售價 %s 萬' % (_loc, format(price_min, ','))
        hero_stats_html = ''
    else:
        hero_summary = ('精選 %d 個物件 · %d 區 · %d 個社區<br>售價 %s 萬 ~ %s 萬'
                        % (total_count, district_count, community_count,
                           format(price_min, ','), format(price_max, ',')))
        hero_stats_html = (
            '<div class="hero-stats">'
            '<div class="hero-stat"><div class="hero-stat-num">%d</div>'
            '<div class="hero-stat-label">精選戶數</div></div>'
            '<div class="hero-stat"><div class="hero-stat-num">%d</div>'
            '<div class="hero-stat-label">行政區</div></div>'
            '<div class="hero-stat"><div class="hero-stat-num">%d</div>'
            '<div class="hero-stat-label">優質社區</div></div></div>'
            % (total_count, district_count, community_count))

    # 篩選列：戶數少的時候整條不出現。
    # 景泰：「不要做選擇式的，做成一頁式的」—— 1～7 戶客戶直接往下滑就看完了，
    # 擺一排「區域/預算/類型/車位」的篩選 chips 只是逼客戶先做選擇。
    # 戶數多才留著（不然 20 戶硬滑也不好找）。要調就改這個數字。
    NAV_MIN = 8
    sticky_nav_html = ''
    if total_count >= NAV_MIN:
        _rows = [
            '<div class="nav-scroll"><span class="nav-label">🏷️ 區域</span>'
            '<a class="nav-chip filter-chip filter-reset" data-filter-reset="1" '
            'style="display:none">✕ 清除篩選</a>' + nav_chips + '</div>',
            '<div class="nav-scroll" style="margin-top:8px"><span class="nav-label">💰 預算</span>'
            + price_filter_chips + '</div>',
        ]
        for _flag, _label, _chips in (
            (has_age_filter, '🏠 屋齡', age_filter_chips),
            (has_unit_filter, '💎 單坪', unit_filter_chips),
            (has_rooms_filter, '🛏️ 房數', rooms_filter_chips),
            (has_type_filter, '🏢 類型', type_filter_chips),
            (has_parking_filter, '🚗 車位', parking_filter_chips),
        ):
            if _flag:
                _rows.append('<div class="nav-scroll" style="margin-top:8px">'
                             '<span class="nav-label">%s</span>%s</div>' % (_label, _chips))
        _rows.append('<div class="filter-summary" id="filter-summary" style="display:none"></div>')
        sticky_nav_html = ('<nav class="sticky-nav" id="sticky-nav">' + ''.join(_rows) + '</nav>')

    return f'''<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex,nofollow">
<title>{page_title}</title>
<meta property="og:title" content="{page_title}">
<meta property="og:description" content="{og_desc}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{contact["company"]}">
<meta property="og:image" content="{og_cover}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{page_title}">
<meta name="twitter:description" content="{og_desc}">
<meta name="twitter:image" content="{og_cover}">
<meta name="theme-color" content="#C9785A">
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
    font-family: -apple-system, BlinkMacSystemFont, 'PingFang TC', 'Microsoft JhengHei', 'Noto Sans CJK TC', 'Heiti TC', sans-serif;
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
    padding: 14px 0;
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
  /* 車位 chip 沿用基底 .nav-chip 樣式（沒選=米色底，選了=橘紅 active）— 不再用綠色，跟其他一致 */
  .filter-chip {{ cursor: pointer; user-select: none; }}
  .filter-reset {{ background: #d9534f !important; color: #FFF !important; border-color: #d9534f !important; font-weight: 700 !important; }}
  .filter-reset:hover {{ background: #c9302c !important; border-color: #c9302c !important; }}
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

  /* 類型分組（住宅 vs 透天/別墅）— 避免透天壓掉其他類型 */
  .type-group {{ margin-top: 18px; }}
  .type-group-header {{
    font-size: 18px; font-weight: 700; color: var(--accent-deep);
    margin: 14px 4px 8px 4px; padding: 10px 16px;
    background: linear-gradient(90deg, rgba(201,120,90,0.12), rgba(201,120,90,0.04));
    border-left: 4px solid var(--accent-deep);
    border-radius: 6px; letter-spacing: 1px;
  }}

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
  /* auto-fit（不是 auto-fill）：只有一兩戶的社區，卡片會把空欄收掉撐開，
     不會卡在三欄的第一欄、右邊白掉一大片。 */
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 22px; }}
  /* 單一物件專用：置中、限寬，不套多物件那套分組版面 */
  .single-wrap {{ max-width: 760px; margin: 0 auto; }}
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
  .card-address {{ font-size: 16px; color: var(--text-soft); margin-bottom: 14px; padding-top: 16px; border-top: 1px solid var(--bg-soft); display: flex; align-items: flex-start; gap: 7px; line-height: 1.6; }}
  .card-address::before {{ content: '📍'; flex-shrink: 0; }}
  /* 地圖直接顯示，不再需要點開（原本是 details 摺疊，客戶要點才看得到） */
  .card-map {{ margin-bottom: 18px; }}
  .card-map-head {{ font-size: 15px; color: var(--wood-deep); padding: 10px 14px; background: var(--bg-soft); border-radius: 10px 10px 0 0; font-weight: 600; display: flex; align-items: center; gap: 8px; }}
  .card-map-icon {{ flex-shrink: 0; }}
  .card-map-label {{ flex: 1; }}
  .card-map-frame {{ border-radius: 0 0 10px 10px; overflow: hidden; border: 1px solid var(--bg-soft); }}
  .card-map-frame iframe {{ width: 100%; height: 220px; border: 0; display: block; }}
  .card-actions {{ display: flex; gap: 8px; margin-top: auto; }}
  .card-cta {{ flex: 1; background: var(--wood-deep); color: #FFF; text-align: center; text-decoration: none; padding: 14px 18px; border-radius: 12px; font-size: 17px; font-weight: 700; letter-spacing: 1.5px; transition: all 0.2s; display: flex; align-items: center; justify-content: center; gap: 8px; }}
  .card-cta:hover {{ background: var(--accent); transform: translateY(-1px); }}
  .card-cta-arrow {{ transition: transform 0.2s; }}
  .card-cta:hover .card-cta-arrow {{ transform: translateX(4px); }}
  .card-like {{ flex-shrink: 0; background: #FFF; border: 2px solid var(--wood-deep); color: var(--wood-deep); border-radius: 12px; padding: 12px 14px; font-size: 16px; font-weight: 700; cursor: pointer; display: flex; align-items: center; gap: 5px; transition: all 0.2s; font-family: inherit; }}
  .card-like:hover {{ background: var(--bg-soft); }}
  .card-like.liked {{ background: #FFEDED; border-color: #E74C3C; color: #E74C3C; cursor: default; }}
  .card-like-icon {{ font-size: 20px; line-height: 1; transition: transform 0.2s; }}
  .card-like.liked .card-like-icon {{ transform: scale(1.2); }}
  .card-like-full {{ flex: 1; justify-content: center; }}
  .card-cta-soft {{ background: var(--bg-soft) !important; color: var(--wood-deep) !important; border: 1.5px dashed var(--wood-light); letter-spacing: 0.5px !important; cursor: default; }}
  .card-cta-soft:hover {{ background: var(--bg-soft) !important; transform: none !important; }}
  .card-lite-spec {{ display: inline-block; font-size: 16px; color: var(--wood-deep); background: var(--bg-soft); padding: 8px 14px; border-radius: 10px; margin-bottom: 14px; font-weight: 600; }}
  /* 降價徽章 */
  .card-badge {{ position: absolute; top: 12px; left: 12px; z-index: 2; display: inline-flex; align-items: center; gap: 4px; background: #E74C3C; color: #FFF; font-size: 14px; font-weight: 800; padding: 5px 13px; border-radius: 20px; letter-spacing: 0.5px; box-shadow: 0 3px 10px rgba(231,76,60,0.40); }}
  /* 官方前台相簿橫向縮圖(可點放大) */
  /* 相簿：全部攤開成網格，客戶一路往下滑就看完 —— 不用橫滑、不用點開。
     （原本是 overflow-x 橫向捲動的 120×90 小縮圖，客戶要滑要點才看得到後面的） */
  .card-gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 6px; padding: 2px 2px 10px; margin-bottom: 12px; }}
  .card-gallery img {{ width: 100%; height: 88px; object-fit: cover; border-radius: 7px; background: var(--bg-soft); border: 1px solid var(--border); cursor: zoom-in; transition: transform 0.15s; }}
  .card-gallery img:hover {{ transform: scale(1.05); }}
  .card-gallery::-webkit-scrollbar {{ height: 6px; }}
  .card-gallery::-webkit-scrollbar-track {{ background: var(--bg-soft); border-radius: 3px; }}
  .card-gallery::-webkit-scrollbar-thumb {{ background: var(--wood-light); border-radius: 3px; }}
  .card-image img {{ cursor: zoom-in; }}
  /* 封面右下「照片張數」提示(點看大圖) */
  .card-photo-count {{ position: absolute; right: 12px; bottom: 12px; z-index: 2; display: inline-flex; align-items: center; gap: 5px; background: rgba(0,0,0,0.60); color: #FFF; font-size: 14px; font-weight: 700; padding: 5px 12px; border-radius: 20px; letter-spacing: 0.5px; pointer-events: none; }}
  /* 無乾淨圖佔位(競品圖有浮水印不放) */
  .card-noimg {{ width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; background: linear-gradient(135deg, #F3EDE3 0%, #E8DECB 100%); color: var(--wood-deep); font-size: 16px; font-weight: 600; letter-spacing: 1px; text-align: center; padding: 12px; }}
  .card-noimg-ic {{ font-size: 34px; opacity: 0.7; }}
  /* 多媒體按鈕列(VR / 影片 / AI) */
  .card-media {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }}
  .card-media-btn {{ display: inline-flex; align-items: center; gap: 6px; background: var(--bg-soft); color: var(--wood-deep); border: 1.5px solid var(--wood-light); border-radius: 10px; padding: 9px 14px; font-size: 15px; font-weight: 700; text-decoration: none; transition: all 0.2s; }}
  .card-media-btn:hover {{ background: var(--wood-deep); color: #FFF; border-color: var(--wood-deep); transform: translateY(-1px); }}
  /* 照片燈箱(點圖全螢幕看大圖) */
  .lb-overlay {{ position: fixed; inset: 0; z-index: 10000; background: rgba(20,16,12,0.94); display: none; align-items: center; justify-content: center; opacity: 0; transition: opacity 0.2s; }}
  .lb-overlay.show {{ display: flex; opacity: 1; }}
  .lb-img {{ max-width: 94vw; max-height: 86vh; object-fit: contain; border-radius: 8px; box-shadow: 0 10px 40px rgba(0,0,0,0.5); user-select: none; }}
  .lb-close {{ position: absolute; top: 16px; right: 20px; width: 46px; height: 46px; border-radius: 50%; border: none; background: rgba(255,255,255,0.15); color: #FFF; font-size: 30px; line-height: 1; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background 0.2s; }}
  .lb-close:hover {{ background: rgba(255,255,255,0.30); }}
  .lb-nav {{ position: absolute; top: 50%; transform: translateY(-50%); width: 54px; height: 54px; border-radius: 50%; border: none; background: rgba(255,255,255,0.15); color: #FFF; font-size: 34px; line-height: 1; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background 0.2s; }}
  .lb-nav:hover {{ background: rgba(255,255,255,0.30); }}
  .lb-prev {{ left: 16px; }}
  .lb-next {{ right: 16px; }}
  .lb-counter {{ position: absolute; bottom: 22px; left: 50%; transform: translateX(-50%); color: #FFF; font-size: 16px; font-weight: 600; letter-spacing: 1px; background: rgba(0,0,0,0.40); padding: 6px 16px; border-radius: 20px; }}
  @media (max-width: 640px) {{
    .card-actions {{ flex-direction: column; }}
    .card-like {{ width: 100%; justify-content: center; padding: 13px 18px; }}
    .card-gallery {{ grid-template-columns: repeat(3, 1fr); gap: 5px; }}  /* 手機一排三張，照樣全部攤開 */
    .card-gallery img {{ width: 100%; height: 76px; border-radius: 6px; }}
    .lb-nav {{ width: 44px; height: 44px; font-size: 28px; }}
    .lb-close {{ top: 10px; right: 12px; }}
  }}
  .teddy-toast {{ position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(120px); background: rgba(44,38,32,0.96); color: #FFF; padding: 14px 22px; border-radius: 28px; font-size: 16px; font-weight: 500; z-index: 9999; opacity: 0; transition: all 0.3s ease; box-shadow: 0 8px 28px rgba(0,0,0,0.28); max-width: calc(100vw - 40px); text-align: center; pointer-events: none; }}
  .teddy-toast.show {{ transform: translateX(-50%) translateY(0); opacity: 1; }}
  .footer {{ background: linear-gradient(135deg, #2C2620 0%, #3D352D 100%); color: #E8DFD2; padding: 60px 20px 36px; margin-top: 50px; }}
  .footer-content {{ max-width: 1200px; margin: 0 auto; text-align: center; }}
  .footer-title {{ font-size: 30px; font-weight: 700; color: #FFF; margin-bottom: 8px; letter-spacing: 6px; }}
  .footer-subtitle {{ font-size: 17px; color: var(--wood-light); margin-bottom: 32px; letter-spacing: 3px; }}
  .footer-contact {{ display: flex; justify-content: center; gap: 36px; flex-wrap: wrap; margin-bottom: 32px; }}
  .contact-item {{ display: flex; align-items: center; gap: 10px; font-size: 18px; color: #E8DFD2; text-decoration: none; transition: color 0.2s; }}
  .contact-item:hover {{ color: var(--accent); }}
  .contact-icon {{ font-size: 20px; }}
  .footer-site {{ margin-bottom: 32px; padding: 22px 18px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12); border-radius: 14px; }}
  .footer-site-title {{ font-size: 17px; color: var(--wood-light); font-weight: 700; margin-bottom: 16px; letter-spacing: 1px; line-height: 1.6; }}
  .footer-site-btns {{ display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; }}
  .footer-site-btn {{ display: inline-block; padding: 12px 22px; border-radius: 24px; font-size: 16px; font-weight: 700; border: 1.5px solid var(--accent); color: #FFF; background: transparent; text-decoration: none; transition: all 0.2s; }}
  .footer-site-btn.primary {{ background: var(--accent); border-color: var(--accent); }}
  .footer-site-btn:hover {{ background: var(--accent); border-color: var(--accent); transform: translateY(-2px); }}
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

  /* 景泰備註區(螢幕版)— 公開賣點補充,有填才出現 */
  .card-note {{
    background: linear-gradient(135deg, #FFF8EE 0%, #FFEFD9 100%);
    border-left: 4px solid var(--accent);
    border-radius: 0 10px 10px 0;
    padding: 14px 16px;
    margin-bottom: 16px;
    box-shadow: inset 0 0 0 1px rgba(201,120,90,0.15);
  }}
  .card-note-label {{
    font-size: 13px; font-weight: 700; color: var(--accent-deep);
    letter-spacing: 1.5px; margin-bottom: 6px;
  }}
  .card-note-body {{
    font-size: 16px; color: var(--text); line-height: 1.75;
    white-space: pre-line;
  }}

  /* 浮動列印按鈕 — 你跟客戶都能按,觸發 A4 print 版型 */
  .print-fab {{
    position: fixed; bottom: 24px; right: 92px;
    background: var(--accent-deep); color: #FFF;
    border: 2px solid #FFF; border-radius: 28px;
    padding: 12px 22px; font-size: 16px; font-weight: 700;
    letter-spacing: 1.5px; cursor: pointer;
    box-shadow: 0 6px 18px rgba(168,93,63,0.4);
    z-index: 200; font-family: inherit;
    transition: all 0.25s;
  }}
  .print-fab:hover {{ transform: translateY(-3px); box-shadow: 0 10px 24px rgba(168,93,63,0.55); }}
  @media (max-width: 640px) {{
    .print-fab {{ bottom: 86px; right: 16px; padding: 10px 16px; font-size: 14px; letter-spacing: 1px; }}
  }}

  /* ============== 列印 / 存 PDF 版型(1 筆 / A4 主推案精美版) ============== */
  @media print {{
    @page {{ size: A4 portrait; margin: 12mm 12mm 14mm 12mm; }}
    html, body {{ background: #FFF !important; color: #000 !important; }}
    body {{ font-size: 12pt; line-height: 1.55; }}
    /* 砍掉螢幕專屬元素 */
    .sticky-nav, .print-fab, .back-to-top, .card-actions, .card-like,
    .hero-stats, .teddy-toast, .footer-site, .footer-date,
    .summary, .for-client {{ display: none !important; }}
    .container {{ padding: 0 !important; max-width: none !important; }}
    /* hero 縮成第一頁標頭一條 */
    .hero {{ background: none !important; padding: 0 0 6mm 0 !important; border-bottom: 2px solid #000; margin-bottom: 6mm; text-align: left !important; }}
    .hero-tag {{ background: none !important; color: #000 !important; padding: 0 !important; font-size: 13pt !important; letter-spacing: 4px !important; }}
    .hero h1 {{ font-size: 22pt !important; color: #000 !important; margin: 3mm 0 0 0 !important; letter-spacing: 2px; }}
    /* 行政區 / 社區 header 在列印時全部塌縮 — 每筆物件獨立成頁,不需要分組 */
    .district-section, .type-group, .community-sub {{ display: block !important; padding: 0 !important; margin: 0 !important; background: none !important; }}
    .district-header, .district-meta, .district-communities, .district-price-tiers,
    .type-group-header, .community-header {{ display: none !important; }}
    .grid {{ display: block !important; gap: 0 !important; }}
    /* 每張卡片 = 1 張 A4 */
    .card {{
      page-break-inside: avoid !important;
      page-break-after: always !important;
      break-inside: avoid !important;
      break-after: page !important;
      box-shadow: none !important;
      border: 1.5px solid #333 !important;
      border-radius: 0 !important;
      margin: 0 0 6mm 0 !important;
      display: block !important;
      transform: none !important;
    }}
    .card:last-of-type {{ page-break-after: auto !important; break-after: auto !important; }}
    .card-image {{ width: 100% !important; height: 92mm !important; border-bottom: 1.5px solid #333 !important; }}
    .card-image::after {{ display: none !important; }}
    .card-image img {{ width: 100% !important; height: 100% !important; object-fit: cover !important; }}
    .card-content {{ padding: 6mm 8mm !important; }}
    .card-tagline {{
      font-size: 17pt !important; color: #000 !important;
      font-weight: 800 !important; min-height: auto !important;
      margin-bottom: 5mm !important; line-height: 1.35 !important;
    }}
    .card-price-row {{ margin-bottom: 4mm !important; padding-bottom: 3mm !important; border-bottom: 1px solid #999 !important; gap: 6mm !important; }}
    .card-price {{ font-size: 32pt !important; color: #000 !important; line-height: 1 !important; }}
    .card-price-unit {{ font-size: 13pt !important; color: #333 !important; }}
    .card-floor {{ background: #F2EDE4 !important; color: #000 !important; font-size: 13pt !important; padding: 2mm 5mm !important; border-radius: 2mm !important; }}
    .card-unit-price {{
      background: #F8F4ED !important; color: #000 !important;
      font-size: 12pt !important; padding: 2.5mm 5mm !important;
      margin-bottom: 4mm !important; border-radius: 2mm !important;
    }}
    .card-unit-price strong {{ font-size: 14pt !important; }}
    .card-spec {{ gap: 3mm 8mm !important; margin-bottom: 5mm !important; }}
    .spec-label {{ font-size: 10pt !important; color: #555 !important; }}
    .spec-value {{ font-size: 13pt !important; color: #000 !important; }}
    .spec-value.highlight {{ color: #000 !important; font-weight: 800 !important; }}
    .card-address {{
      font-size: 11pt !important; color: #000 !important;
      padding-top: 3mm !important; border-top: 1px solid #999 !important;
      margin-bottom: 4mm !important;
    }}
    .card-note {{
      background: #FFF8EE !important;
      border-left: 3pt solid #A85D3F !important;
      padding: 4mm 5mm !important; margin-bottom: 4mm !important;
      box-shadow: none !important; border-radius: 0 !important;
    }}
    .card-note-label {{ font-size: 10pt !important; color: #A85D3F !important; letter-spacing: 1pt; }}
    .card-note-body {{ font-size: 12pt !important; color: #000 !important; line-height: 1.7 !important; }}
    /* 頁尾證號 — 最後一頁印 */
    .footer {{ background: none !important; color: #000 !important; padding: 4mm 0 0 0 !important; margin-top: 6mm !important; border-top: 1.5px solid #000 !important; page-break-inside: avoid; }}
    .footer-content {{ text-align: left !important; max-width: none !important; }}
    .footer-title {{ font-size: 14pt !important; color: #000 !important; letter-spacing: 2px !important; margin-bottom: 1mm !important; }}
    .footer-subtitle {{ font-size: 10pt !important; color: #333 !important; margin-bottom: 4mm !important; letter-spacing: 1pt !important; }}
    .footer-contact {{ display: flex !important; gap: 6mm !important; margin-bottom: 3mm !important; justify-content: flex-start !important; flex-wrap: wrap !important; }}
    .contact-item {{ font-size: 11pt !important; color: #000 !important; gap: 2mm !important; }}
    .footer-license {{ font-size: 9.5pt !important; color: #333 !important; padding-top: 3mm !important; border-top: 1px solid #BBB !important; line-height: 1.7 !important; }}
    a, a:visited {{ color: #000 !important; text-decoration: none !important; }}
  }}
</style>
</head>
<body>

<button class="print-fab" onclick="window.print()" type="button" title="列印成 A4 售資 / 存 PDF">📄 列印 / 存 PDF</button>

<section class="hero">
  <div class="hero-tag">{theme}</div>
  <h1>{hero_h1}</h1>
  <div class="for-client">{for_client_line}</div>
  <p class="summary">{hero_summary}</p>
  {hero_stats_html}
</section>

<section class="top-contact">
  <div class="top-contact-inner">
    <div class="top-contact-name">{contact["agent_name"]}　<span class="top-contact-role">{contact["company"]}</span></div>
    <div class="top-contact-btns">
      <a class="top-cta primary" href="tel:{contact["phone_raw"]}">📞 {contact["phone"]}</a>
      <a class="top-cta line" href="{contact["line_url"]}" target="_blank" rel="noopener">💬 LINE 我（{contact["line"]}）</a>
    </div>
    <div class="top-contact-hint">看到喜歡的物件 → 直接打給我或 LINE 我，幫你約看 ✨</div>
  </div>
</section>

{sticky_nav_html}

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
      {ig_html}
    </div>
    {footer_site_html}
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

  // 從一張 card 撈 slug + tagline
  function cardInfo(el) {{
    var card = el.closest('.card');
    var slug = card ? (card.dataset.slug || '') : '';
    var tagline = '';
    if (card) {{ var t = card.querySelector('.card-tagline'); if (t) tagline = (t.textContent || '').trim(); }}
    return {{ slug: slug, tagline: tagline }};
  }}

  // 綁定每張 card 的「看完整資訊」按鈕點擊事件(純洽詢的軟提示不是 <a>，自動略過)
  document.querySelectorAll('a.card-cta').forEach(function(link) {{
    link.addEventListener('click', function() {{
      var info = cardInfo(link);
      if (!info.slug) return;
      trackClick(info.slug, link.getAttribute('href') || '', info.tagline);
    }});
  }});

  // 多媒體點擊(VR / 影片 / AI 導覽)= 高強度看屋興趣訊號
  document.querySelectorAll('.card-media-btn').forEach(function(link) {{
    link.addEventListener('click', function() {{
      var info = cardInfo(link);
      if (!info.slug) return;
      var kind = link.getAttribute('data-media') || 'media';
      trackClick(info.slug, link.getAttribute('href') || '', info.tagline + ' [' + kind + ']');
    }});
  }});

  // ============== 照片燈箱 ── 點封面/縮圖全螢幕看大圖 + 左右滑 ==============
  (function() {{
    var overlay = document.createElement('div');
    overlay.className = 'lb-overlay';
    overlay.innerHTML =
      '<button class="lb-close" aria-label="關閉">&times;</button>' +
      '<button class="lb-nav lb-prev" aria-label="上一張">&#8249;</button>' +
      '<img class="lb-img" alt="物件照片" />' +
      '<button class="lb-nav lb-next" aria-label="下一張">&#8250;</button>' +
      '<div class="lb-counter"></div>';
    document.body.appendChild(overlay);
    var lbImg = overlay.querySelector('.lb-img');
    var lbCounter = overlay.querySelector('.lb-counter');
    var lbPrev = overlay.querySelector('.lb-prev');
    var lbNext = overlay.querySelector('.lb-next');
    var photos = [], idx = 0;

    function hi(u) {{ return (u || '').replace('width=1024', 'width=1600').replace('height=768', 'height=1200'); }}
    function show(i) {{
      if (!photos.length) return;
      idx = (i + photos.length) % photos.length;
      lbImg.src = hi(photos[idx]);
      var multi = photos.length > 1;
      lbCounter.textContent = (idx + 1) + ' / ' + photos.length;
      lbPrev.style.display = multi ? '' : 'none';
      lbNext.style.display = multi ? '' : 'none';
      lbCounter.style.display = multi ? '' : 'none';
    }}
    function openLb(list, start) {{
      photos = list || [];
      if (!photos.length) return;
      show(start || 0);
      overlay.classList.add('show');
      document.body.style.overflow = 'hidden';
    }}
    function closeLb() {{
      overlay.classList.remove('show');
      document.body.style.overflow = '';
      lbImg.src = '';
    }}
    function photosOf(card) {{
      var g = card.querySelectorAll('.card-gallery img'), arr = [];
      if (g.length) {{ g.forEach(function(im) {{ arr.push(im.getAttribute('src')); }}); }}
      else {{ var c = card.querySelector('.card-image img'); if (c) arr.push(c.getAttribute('src')); }}
      return arr;
    }}
    document.querySelectorAll('.card-image img').forEach(function(im) {{
      im.addEventListener('click', function() {{
        var card = im.closest('.card'); if (card) openLb(photosOf(card), 0);
      }});
    }});
    document.querySelectorAll('.card-gallery img').forEach(function(im) {{
      im.addEventListener('click', function() {{
        var card = im.closest('.card'); if (!card) return;
        var list = photosOf(card), start = list.indexOf(im.getAttribute('src'));
        openLb(list, start < 0 ? 0 : start);
      }});
    }});
    overlay.querySelector('.lb-close').addEventListener('click', closeLb);
    lbPrev.addEventListener('click', function(e) {{ e.stopPropagation(); show(idx - 1); }});
    lbNext.addEventListener('click', function(e) {{ e.stopPropagation(); show(idx + 1); }});
    overlay.addEventListener('click', function(e) {{ if (e.target === overlay) closeLb(); }});
    document.addEventListener('keydown', function(e) {{
      if (!overlay.classList.contains('show')) return;
      if (e.key === 'Escape') closeLb();
      else if (e.key === 'ArrowLeft') show(idx - 1);
      else if (e.key === 'ArrowRight') show(idx + 1);
    }});
    var tx = 0;
    overlay.addEventListener('touchstart', function(e) {{ tx = e.changedTouches[0].clientX; }}, {{passive: true}});
    overlay.addEventListener('touchend', function(e) {{
      var dx = e.changedTouches[0].clientX - tx;
      if (Math.abs(dx) > 40 && photos.length > 1) show(idx + (dx < 0 ? 1 : -1));
    }}, {{passive: true}});
  }})();

  // 綁定上方 CTA 按鈕（電話 + LINE）— 真正轉換訊號
  document.querySelectorAll('.top-cta.primary, .card-cta-phone').forEach(function(el) {{
    el.addEventListener('click', function() {{ trackCta('phone'); }});
  }});
  document.querySelectorAll('.top-cta.line, .card-cta-line').forEach(function(el) {{
    el.addEventListener('click', function() {{ trackCta('line'); }});
  }});

  // ============== 愛心按鈕 ── 客戶按下就推 TG 給景泰 ==============
  function trackLike(slug, name) {{
    var elapsedSec = Math.round((Date.now() - startTime) / 1000);
    postTrack({{
      client: CLIENT_NAME,
      share_id: SHARE_ID,
      like_slug: slug,
      like_name: name,
      duration: elapsedSec,
      url: location.href,
      referrer: document.referrer || ''
    }});
  }}

  function showTeddyToast(msg) {{
    var t = document.createElement('div');
    t.className = 'teddy-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(function() {{ t.classList.add('show'); }});
    setTimeout(function() {{
      t.classList.remove('show');
      setTimeout(function() {{ t.remove(); }}, 320);
    }}, 2800);
  }}

  document.querySelectorAll('.card-like').forEach(function(btn) {{
    var slug = btn.getAttribute('data-slug') || '';
    var likeKey = 'teddy_like_' + SHARE_ID + '_' + slug;
    var iconEl = btn.querySelector('.card-like-icon');
    var textEl = btn.querySelector('.card-like-text');

    // 已按過 — 顯示已喜歡狀態（換裝置會重置，這是 OK 的）
    try {{
      if (localStorage.getItem(likeKey) === '1') {{
        btn.classList.add('liked');
        if (iconEl) iconEl.textContent = '❤';
        if (textEl) textEl.textContent = '已喜歡';
      }}
    }} catch (e) {{}}

    btn.addEventListener('click', function() {{
      if (btn.classList.contains('liked')) return;
      btn.classList.add('liked');
      if (iconEl) iconEl.textContent = '❤';
      if (textEl) textEl.textContent = '已喜歡';
      try {{ localStorage.setItem(likeKey, '1'); }} catch (e) {{}}
      var name = btn.getAttribute('data-name') || '';
      trackLike(slug, name);
      showTeddyToast('已通知景泰 ❤️ 他會盡快與你聯絡');
    }});
  }});

  // ============== 區域 + 預算 + 屋齡 + 單坪 + 房數 + 類型 + 車位 篩選邏輯 ==============
  var activeDistrict = null;
  var activePrice = null;
  var activeAge = null;
  var activeUnit = null;
  var activeRooms = null;
  var activeType = null;
  var activeParking = null;

  function applyFilters() {{
    var visibleCount = 0;
    document.querySelectorAll('.card').forEach(function(card) {{
      var d = card.dataset.district;
      var p = card.dataset.priceTier;
      var a = card.dataset.ageTier;
      var u = card.dataset.unitPriceTier;
      var rm = card.dataset.rooms;
      var ty = card.dataset.type;
      var pk = card.dataset.parking;
      var match = (
        (!activeDistrict || activeDistrict === d) &&
        (!activePrice || activePrice === p) &&
        (!activeAge || activeAge === a) &&
        (!activeUnit || activeUnit === u) &&
        (!activeRooms || activeRooms === rm) &&
        (!activeType || activeType === ty) &&
        (!activeParking || activeParking === pk)
      );
      card.style.display = match ? '' : 'none';
      if (match) visibleCount++;
    }});
    // 隱藏空的社區 sub-section
    document.querySelectorAll('.community-sub').forEach(function(s) {{
      var v = s.querySelectorAll('.card[style=""], .card:not([style])').length;
      s.style.display = v > 0 ? '' : 'none';
    }});
    // 隱藏空的 type-group（住宅 / 透天分組）— 沒這層會留住「🏢 住宅」空標題造成大塊空白
    document.querySelectorAll('.type-group').forEach(function(g) {{
      var v = g.querySelectorAll('.card[style=""], .card:not([style])').length;
      g.style.display = v > 0 ? '' : 'none';
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
    document.querySelectorAll('[data-filter-unit]').forEach(function(c) {{
      c.classList.toggle('active', c.dataset.filterUnit === activeUnit);
    }});
    document.querySelectorAll('[data-filter-rooms]').forEach(function(c) {{
      c.classList.toggle('active', c.dataset.filterRooms === activeRooms);
    }});
    document.querySelectorAll('[data-filter-type]').forEach(function(c) {{
      // 「全部」chip 在 activeType=null（沒選任何類型）時 active
      var isAll = c.dataset.filterType === '__all__';
      var isActive = isAll ? (activeType === null) : (c.dataset.filterType === activeType);
      c.classList.toggle('active', isActive);
    }});
    document.querySelectorAll('[data-filter-parking]').forEach(function(c) {{
      c.classList.toggle('active', c.dataset.filterParking === activeParking);
    }});
    // 篩選 summary + 清除按鈕顯示
    var any = activeDistrict || activePrice || activeAge || activeUnit || activeRooms || activeType || activeParking;
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
        if (activeUnit) {{
          var uchip = document.querySelector('[data-filter-unit="' + activeUnit + '"]');
          parts.push('單坪：' + (uchip ? uchip.textContent.replace(/\\d+$/, '').trim() : activeUnit));
        }}
        if (activeRooms) {{
          var rchip = document.querySelector('[data-filter-rooms="' + activeRooms + '"]');
          parts.push('房數：' + (rchip ? rchip.textContent.replace(/\\d+$/, '').trim() : activeRooms));
        }}
        if (activeType) {{
          parts.push('類型：' + activeType);
        }}
        if (activeParking) {{
          parts.push('車位：' + (activeParking === 'yes' ? '有車位' : '無車位'));
        }}
        summary.textContent = '🔍 ' + parts.join(' + ') + ' → ' + visibleCount + ' 戶符合';
        summary.style.display = '';
      }} else {{
        summary.style.display = 'none';
      }}
    }}
  }}

  // scroll 到 chip 篩選條那一段（chip 條已不 sticky，永遠在頁面固定位置）
  function scrollToFilterNav() {{
    var nav = document.querySelector('.sticky-nav');
    if (!nav) return;
    var targetY = nav.getBoundingClientRect().top + window.scrollY;
    window.scrollTo({{ top: targetY, behavior: 'smooth' }});
  }}

  document.querySelectorAll('[data-filter-district]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var d = this.dataset.filterDistrict;
      activeDistrict = activeDistrict === d ? null : d;
      applyFilters();
      scrollToFilterNav();
    }});
  }});
  document.querySelectorAll('[data-filter-price]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var p = this.dataset.filterPrice;
      activePrice = activePrice === p ? null : p;
      applyFilters();
      scrollToFilterNav();
    }});
  }});
  document.querySelectorAll('[data-filter-age]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var a = this.dataset.filterAge;
      activeAge = activeAge === a ? null : a;
      applyFilters();
      scrollToFilterNav();
    }});
  }});
  document.querySelectorAll('[data-filter-unit]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var u = this.dataset.filterUnit;
      activeUnit = activeUnit === u ? null : u;
      applyFilters();
      scrollToFilterNav();
    }});
  }});
  document.querySelectorAll('[data-filter-rooms]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var rm = this.dataset.filterRooms;
      activeRooms = activeRooms === rm ? null : rm;
      applyFilters();
      scrollToFilterNav();
    }});
  }});
  document.querySelectorAll('[data-filter-type]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var ty = this.dataset.filterType;
      // 「全部」chip 直接清空 activeType（不是 toggle）
      if (ty === '__all__') {{
        activeType = null;
      }} else {{
        activeType = activeType === ty ? null : ty;
      }}
      applyFilters();
      scrollToFilterNav();
    }});
  }});
  document.querySelectorAll('[data-filter-parking]').forEach(function(chip) {{
    chip.addEventListener('click', function(e) {{
      e.preventDefault();
      var pk = this.dataset.filterParking;
      activeParking = activeParking === pk ? null : pk;
      applyFilters();
      scrollToFilterNav();
    }});
  }});
  // 清除全部篩選按鈕（只在有 filter active 時顯示）
  document.querySelectorAll('[data-filter-reset]').forEach(function(btn) {{
    btn.addEventListener('click', function(e) {{
      e.preventDefault();
      activeDistrict = null;
      activePrice = null;
      activeAge = null;
      activeUnit = null;
      activeRooms = null;
      activeType = null;
      activeParking = null;
      applyFilters();
      scrollToFilterNav();
    }});
  }});

  // sticky-nav 維持永遠顯示（景泰決策：不 auto-hide 容易困惑）
  // 客人要回頂部看 chip → 點右下「↑」按鈕

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
      scrollToFilterNav();  // 回到 chip 篩選條（不是頁面最頂的 hero）
    }});
  }}

  window.addEventListener('pagehide', trackVisit);
  window.addEventListener('beforeunload', trackVisit);
  // 也定期 ping 一次（讓還在閱讀的訪客也記到，避免關閉太快沒記到）
  setTimeout(trackVisit, 30000);

  // 路段地圖：客戶捲到附近就自動載入，不用點任何東西。
  // （原本是 details 摺疊、展開才載入 —— 客戶得先點才看得到地圖）
  // 仍然只在快進入畫面時才灌 src，避免一次開十幾個 Google Maps iframe 把頁面拖慢。
  var MAPS_EMBED_KEY = {json.dumps(GOOGLE_MAPS_EMBED_KEY)};
  function loadMap(f) {{
    if (f && !f.src && f.dataset.q) {{
      f.src = 'https://www.google.com/maps/embed/v1/place?key=' + MAPS_EMBED_KEY + '&q=' + encodeURIComponent(f.dataset.q);
    }}
  }}
  var mapFrames = document.querySelectorAll('.card-map iframe[data-q]');
  if ('IntersectionObserver' in window) {{
    var mo = new IntersectionObserver(function(entries) {{
      entries.forEach(function(e) {{
        if (!e.isIntersecting) return;
        loadMap(e.target);
        mo.unobserve(e.target);
      }});
    }}, {{ rootMargin: '300px' }});
    mapFrames.forEach(function(f) {{ mo.observe(f); }});
  }} else {{
    mapFrames.forEach(loadMap);
  }}
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


def build_contact(body):
    """從 publish payload 組出 contact dict — 留空欄位 fallback 到 DEFAULT_CONTACT"""
    contact = dict(DEFAULT_CONTACT)
    agent_name = (body.get("agent_name") or "").strip()
    agent_license = (body.get("agent_license") or "").strip()
    phone = (body.get("phone") or "").strip()
    line = (body.get("line") or "").strip()

    if agent_name:
        contact["agent_name"] = agent_name
    if agent_license:
        contact["agent_license"] = agent_license
    if phone:
        contact["phone"] = phone
        contact["phone_raw"] = re.sub(r"[^\d+]", "", phone) or phone
    if line:
        line_id = line.lstrip("@").strip()
        contact["line"] = line_id
        contact["line_url"] = f"https://line.me/ti/p/~{line_id}"
    return contact


@app.route("/api/publish", methods=["POST", "OPTIONS"])
def publish_endpoint():
    # v2 完整版：quickjs 解析 + card_html 📋物件介紹 render（改此行強制 Vercel 重 build 本 function）
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(silent=True) or {}
        # 允許不填客戶稱呼 —— 空的就是通用連結,頁面不會出現「給 XX 的」,可轉發給多人
        name = (body.get("name") or "").strip()
        # 需求也允許空:空就整行不出現,不要讓「找房需求」四個字孤零零掛在頁面上
        need = (body.get("need") or "").strip()
        if need and not need.startswith("找房需求"):
            need = f"找房需求：{need}"
        text = body.get("urls_text", "")
        theme = (body.get("theme") or "").strip()
        signature = (body.get("signature") or "").strip()
        contact = build_contact(body)

        refs = extract_refs(text)
        if not refs:
            return jsonify({"error": "找不到任何物件網址（ycut https://x.ychouse.tw/... 或官方前台 https://buy.u-trust.com.tw/house/...）"}), 400

        properties = fetch_full_batch(refs)
        if not properties:
            return jsonify({"error": "全部物件抓取失敗（可能 URL 已失效）"}), 400

        # 注入每筆物件的「景泰備註」— 前端 /api/preview 後讓景泰填的賣點補充
        notes = body.get("notes") or {}
        if isinstance(notes, dict):
            for p in properties:
                n = (notes.get(p.get("slug")) or "").strip()
                if n:
                    p["note"] = n

        # 「回去修改」模式 — 前端帶 share_id 過來就覆蓋既有檔案，不開新連結
        incoming_share_id = (body.get("share_id") or "").strip()
        is_update = False
        if incoming_share_id and re.fullmatch(r"[A-Za-z0-9]{1,16}", incoming_share_id):
            share_id = incoming_share_id
            is_update = True
        else:
            share_id = gen_share_id()

        client_data = {
            "name": name,
            "need": need,
            "share_id": share_id,
            "contact": contact,
            "theme": theme,
            "signature": signature,
            "hide_promo": bool(body.get("hide_promo")),
        }
        html = gen_html(client_data, properties)

        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return jsonify({"error": "Server 缺 GITHUB_TOKEN 環境變數"}), 500

        try:
            github_push(
                f"{share_id}/index.html",
                html,
                (f"update: {name} {len(properties)} 戶 ({share_id})"
                    if is_update else
                    f"add: {name} {len(properties)} 戶 ({share_id})"),
                token,
                update=is_update,
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
            "mode": "updated" if is_update else "created",
        })
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "ts": datetime.datetime.now().isoformat(), "build": "2026-06-02-recommend"})


@app.route("/api/preview", methods=["POST", "OPTIONS"])
def preview_endpoint():
    """前端「預覽 & 加備註」階段 — 只 fetch ycut 物件、回精簡 JSON 給前端渲染備註欄,不 push GitHub。"""
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(silent=True) or {}
        text = body.get("urls_text", "")
        refs = extract_refs(text)
        if not refs:
            return jsonify({"error": "找不到任何物件網址(ycut https://x.ychouse.tw/... 或官方前台 https://buy.u-trust.com.tw/house/...)"}), 400
        properties = fetch_full_batch(refs)
        if not properties:
            return jsonify({"error": "全部物件抓取失敗(可能 URL 已失效)"}), 400
        items = []
        for p in properties:
            source = p.get("source", "ycut")
            brand = p.get("brand") or "官方前台"
            # 來源標示 — 讓景泰預覽時一眼看清品牌/是不是自己店、多媒體會不會掛
            if source == "merged":
                src_label, src_tone = "🔗 已合併 ycut＋官方前台", "ok"
            elif source == "utrust" and p.get("is_own_store"):
                src_label, src_tone = f"✅ {brand} · 你的店", "ok"
            elif source == "utrust":
                src_label, src_tone = f"⚠️ {brand} · 別店：{p.get('store_name') or '未知'}（無外連，影片/AI 不帶）", "warn"
            elif source == "yungching":
                src_label, src_tone = "⚠️ 永慶直營（資料加密：僅照片+名稱+地址，無外連）", "warn"
            elif source == "external":
                _b = p.get("brand") or "競品站"
                _photo = "含乾淨照片" if not p.get("no_clean_photo") else "無競品照片(建議補自家圖)"
                src_label, src_tone = f"🔁 {_b} · 已洗淨變你的（{_photo}）", "warn"
            else:
                src_label, src_tone = "", ""
            has_media = bool(p.get("vr_url") or p.get("video_url") or p.get("ai_video_url"))
            items.append({
                "slug": p["slug"],
                "tagline": clean_tagline(p.get("og_title", "")) or p.get("community_display", ""),
                "community": p.get("community_display", ""),
                "price": p.get("price", 0),
                "address": p.get("address", ""),
                "og_image": p.get("og_image", ""),
                "area": p.get("area", 0),
                "floor": p.get("floor", 0),
                "floor_total": p.get("floor_total", 0),
                "layout": p.get("layout", ""),
                "source": source,
                "src_label": src_label,
                "src_tone": src_tone,
                "has_media": has_media,
                "gallery_count": len(p.get("gallery") or []),
                "intro_len": len(p.get("intro") or ""),
            })
        return jsonify({"items": items, "count": len(items)})
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/recommend", methods=["POST", "OPTIONS"])
def recommend_endpoint():
    """猜你喜歡：吃 anchor 短網址 → 框檔次 decoy 配貨 → 渲染資訊卡頁 → push GitHub。"""
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(silent=True) or {}
        anchor_url = (body.get("anchor_url") or body.get("url") or "").strip()
        client_name = (body.get("name") or "").strip()
        signature_input = (body.get("signature") or "").strip()
        if "ychouse.tw" not in anchor_url:
            return jsonify({"error": "請貼 ycut 短網址（https://x.ychouse.tw/...）"}), 400

        # lazy import pipeline (api/_lib/，底線目錄 = Vercel 打包但不當 function)
        import sys
        _HERE = os.path.dirname(os.path.abspath(__file__))  # api/
        if _HERE not in sys.path:
            sys.path.insert(0, _HERE)
        from _lib.yungching_recommend import recommend as yc_recommend
        from _lib.recommend_render import render_recommend_page

        data = yc_recommend(anchor_url)
        anchor = data.get("anchor") or {}
        if not anchor.get("price_wan"):
            return jsonify({
                "error": "anchor 物件抓取失敗（URL 可能失效，或非台中物件）",
                "warnings": data.get("warnings", [])[:5],
            }), 400

        share_id = gen_share_id()
        contact = build_contact(body)
        html = render_recommend_page(data, contact, share_id, client_name, signature_input)

        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return jsonify({"error": "Server 缺 GITHUB_TOKEN 環境變數"}), 500
        github_push(
            f"{share_id}/index.html", html,
            f"recommend: {anchor.get('community_name', '?')} {anchor.get('price_wan')}萬 ({share_id})",
            token,
        )

        return jsonify({
            "url": f"{PAGES_BASE_URL}/{share_id}/",
            "share_id": share_id,
            "anchor": anchor.get("community_name"),
            "anchor_price": anchor.get("price_wan"),
            "cheap": len(data.get("cheap", [])),
            "pricey": len(data.get("pricey", [])),
            "tier": data.get("tier_conditions", {}).get("final_level"),
        })
    except Exception as e:
        import traceback
        return jsonify({
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc()[-800:],
        }), 500


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
        # 內部 dashboard 參照用(非客戶頁)：優先 detail_url，官方前台來源退回 house 頁(用正確品牌域名)
        target_url = (
            p.get("detail_url")
            or p.get("src_url")   # 競品來源網址(供日後重做,只存後台)
            or (f"https://buy.{p.get('host', 'u-trust.com.tw')}/house/{p['case_id']}" if p.get("case_id") else f"https://x.ychouse.tw/{slug}")
        )
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


def notion_log_visit(client_name, share_id, user_agent, ip_geo, duration, page_url, referrer, clicked_slug="", clicked_url="", clicked_name="", cta_type="", like_slug="", like_name=""):
    """寫一筆訪問記錄到 Notion DB（失敗就靜默吃掉，不影響客戶體驗）

    like_slug 有值 → 客戶按愛心（最強訊號，會推 TG）
    cta_type 有值 → 客戶點 CTA 按鈕（phone/line）
    clicked_slug 有值 → 客戶點某個物件去看完整資訊（click 事件）
    都沒有 → 客戶開了客戶頁本身（visit 事件）
    """
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return False

    def text_prop(s):
        return {"rich_text": [{"text": {"content": (s or "")[:1900]}}]} if s else {"rich_text": []}

    # 事件類型優先順序：like > cta > click > visit
    summary = ""
    if like_slug:
        event_type = "like"
        target_url = f"https://x.ychouse.tw/{like_slug}"[:1900]
        clicked_prop = {"url": target_url}
        summary = f"❤️ 客戶按愛心：{like_name}" if like_name else "❤️ 客戶按愛心"
    elif cta_type:
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
        like_slug = (body.get("like_slug") or "").strip()
        like_name = (body.get("like_name") or "").strip()

        # 從 Vercel 自動 header 拿訪客 IP 粗略地理位置
        city = request.headers.get("X-Vercel-IP-City", "")
        country = request.headers.get("X-Vercel-IP-Country", "")
        ip_geo = parse_geo(city, country)
        user_agent = parse_ua(request.headers.get("User-Agent", "")[:300])

        ok = notion_log_visit(client_name, share_id, user_agent, ip_geo, duration, page_url, referrer, clicked_slug, clicked_url, clicked_name, cta_type, like_slug, like_name)

        # 客戶按愛心 → 立刻推 TG 給景泰（強訊號）
        if like_slug:
            telegram_notify_like(client_name, like_slug, like_name, page_url, duration, ip_geo, user_agent)

        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def telegram_notify_like(client_name, like_slug, like_name, page_url, duration, ip_geo, user_agent):
    """客戶按愛心 → 推 TG「泰迪的小聲音」（環境變數沒設就 silent skip）"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    try:
        dur = int(duration or 0)
    except Exception:
        dur = 0
    if dur >= 60:
        duration_text = f"{dur // 60} 分 {dur % 60} 秒"
    else:
        duration_text = f"{dur} 秒"

    safe_name = (client_name or "客戶").replace("<", "").replace(">", "")
    safe_obj = (like_name or "某物件").replace("<", "").replace(">", "")
    lines = [
        f"❤️ <b>{safe_name}</b> 對下面這間按了愛心",
        "",
        f"📌 <b>{safe_obj}</b>",
        f"🔗 https://x.ychouse.tw/{like_slug}",
        "",
        f"⏱️ 看了 {duration_text} 才按下",
    ]
    if ip_geo:
        lines.append(f"📍 {ip_geo}")
    if user_agent:
        lines.append(f"📱 {user_agent}")
    if page_url:
        lines.append("")
        lines.append(f"📋 從這個推薦頁：{page_url}")
    lines.append("")
    lines.append("💡 趁熱聯絡客戶")
    msg = "\n".join(lines)

    body = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception as e:
        print(f"TG like notify failed: {type(e).__name__}: {e}")
        return False


# ============== Stats Dashboard ==============

def notion_query_all(limit=None, days_back=None):
    """Fetch all rows from Notion DB, paginated. Returns raw page objects.
    limit=None → 跑到 has_more=false 為止（拿全部資料）
    limit=N → 拿到 N 筆就停（給有需要 quick query 的 caller 用）
    days_back=N → Notion 端就 filter created_time 最近 N 天（大幅減少 paginate 次數）
    days_back=None → 不 filter 時間"""
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return []
    results = []
    cursor = None

    filter_obj = None
    if days_back is not None:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
        filter_obj = {
            "timestamp": "created_time",
            "created_time": {"on_or_after": cutoff},
        }

    while True:
        body = {"page_size": 100, "sorts": [{"timestamp": "created_time", "direction": "descending"}]}
        if filter_obj:
            body["filter"] = filter_obj
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
        if not data.get("has_more"):
            break
        if limit is not None and len(results) >= limit:
            break
        cursor = data.get("next_cursor")
    return results[:limit] if limit is not None else results


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
    # 不在這裡截斷 — render 時依「全部 vs 單篇模式」決定截 15 還是顯示全部
    top_properties = sorted(click_counts.values(), key=lambda x: -x["count"])

    # 各貼文表現（按 share_id 聚合）— 只算真實客戶事件 (visit / click / cta)
    # 排除 snapshot / backfill / publish 等系統紀錄
    REAL_EVENTS = {"visit", "click", "cta"}
    posts = {}
    for v in parsed:
        sid = v["share_id"]
        et = infer_type(v)
        if not sid or et not in REAL_EVENTS:
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
        if et == "click":
            posts[sid]["clicks"] += 1
            url = v["clicked_url"]
            posts[sid]["click_counts"][url] = posts[sid]["click_counts"].get(url, 0) + 1
        elif et == "visit":
            posts[sid]["visits"] += 1
        # cta 不增加 visits 也不增加 clicks，但會更新 latest
        if v["time"] > posts[sid]["latest"]:
            posts[sid]["latest"] = v["time"]
    # 過濾「沒任何客戶互動」的 share_id（visits=0 且 clicks=0 不該出現在 stats）
    posts_list = [p for p in posts.values() if p["visits"] > 0 or p["clicks"] > 0]
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

    # 時間範圍切換器（保留 share_id 參數）
    days_back = stats.get("days_back")
    sid_qs = f"&share_id={stats['share_id_filter']}" if stats.get("share_id_filter") else ""
    range_options = [(15, "15 天"), (30, "30 天"), (90, "90 天"), (None, "全部")]
    range_links = []
    for val, label in range_options:
        is_active = (val == days_back)
        href = f"/stats?days={'all' if val is None else val}{sid_qs}"
        cls = "range-link active" if is_active else "range-link"
        range_links.append(f'<a class="{cls}" href="{href}">{label}</a>')
    range_html = f'<div class="range-bar">時間範圍：{"".join(range_links)}</div>'

    # 物件熱度榜：全部模式截 15，單篇模式顯示全部 (景泰要看單篇所有點擊明細)
    is_single = bool(stats.get("share_id_filter"))
    top_props_to_show = stats["top_properties"] if is_single else stats["top_properties"][:15]
    if top_props_to_show:
        max_count = top_props_to_show[0]["count"]
        top_html = ""
        for i, p in enumerate(top_props_to_show):
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
              <td><strong>{p["client"]}</strong><div class="sub"><a href="/stats?share_id={p["share_id"]}">{p["share_id"]} →</a></div></td>
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
    font-family: -apple-system, BlinkMacSystemFont, 'PingFang TC', 'Microsoft JhengHei', 'Noto Sans CJK TC', 'Heiti TC', sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.7;
    font-size: 16px; padding: 30px 16px 80px;
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; }}
  header {{ text-align: center; margin-bottom: 30px; }}
  h1 {{ font-size: 30px; font-weight: 900; letter-spacing: 1px; }}
  .updated {{ font-size: 13px; color: var(--text-muted); margin-top: 6px; }}
  .back-link {{ display: inline-block; margin-top: 10px; color: var(--accent-deep); text-decoration: none; font-weight: 500; }}
  .range-bar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 14px; font-size: 14px; color: var(--text-muted); }}
  .range-link {{ display: inline-block; padding: 6px 14px; border-radius: 999px; border: 1px solid var(--border); color: var(--text); text-decoration: none; font-weight: 500; background: #fff; }}
  .range-link:hover {{ background: var(--bg-soft); }}
  .range-link.active {{ background: var(--accent-deep); border-color: var(--accent-deep); color: #fff; }}
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
  <h1>📊 統計儀表板{f' — {stats["share_id_filter"]} 單筆' if stats.get("share_id_filter") else ''}</h1>
  <div class="updated">最後更新：{tw_now}（台北時間）</div>
  {f'<a class="back-link" href="/stats">← 回全部統計</a>' if stats.get("share_id_filter") else '<a class="back-link" href="/">← 回表單</a>'}
  {range_html}
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
  <h2>🔥 {'客人點過的物件（這篇全部）' if stats.get("share_id_filter") else '物件熱度榜（總點擊 TOP 15）'}</h2>
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
    # ?share_id=XXX 只看該筆 stats；沒帶 → 全部 share_id 一覽（每筆一行）
    # ?days=N → 只看最近 N 天（預設 15）；?days=all → 全部歷史
    share_id_filter = (request.args.get("share_id") or "").strip()
    days_param = (request.args.get("days") or "15").strip().lower()
    if days_param == "all":
        days_back = None
    else:
        try:
            days_back = max(1, int(days_param))
        except ValueError:
            days_back = 15

    rows = notion_query_all(days_back=days_back)
    if share_id_filter:
        rows = [r for r in rows
                if _row_field(r, "share_id", "rich_text") == share_id_filter]
    stats = compute_stats(rows)
    stats["share_id_filter"] = share_id_filter or None
    stats["days_back"] = days_back  # render 用來高亮當前選項
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
