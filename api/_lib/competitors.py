# -*- coding: utf-8 -*-
"""競品房仲物件解析器集合 — 由 build workflow 產出、整合。
每支 _p_<fn>(url) 回傳原始欄位；fetch_external() 統一包裝(照片政策+洗白保險絲)。
只用標準庫。客戶頁絕不出現競品品牌/業務/電話。"""
import re, json, time, hashlib, urllib.request, urllib.parse
import html as _html

# ==================== 信義 ====================
# -*- coding: utf-8 -*-
"""信義房屋 (sinyi.com.tw) 物件解析器 — self-contained, 只用 Python 標準庫。
可直接貼進客戶推薦頁後端使用。
主來源：<script id="__NEXT_DATA__"> 內
props.initialReduxState.buyReducer.contentData；抓不到再退 og meta。"""



def _p_sinyi(url):
    """抓取信義房屋物件詳情頁，回傳統一 schema 的 dict。
    競品品牌 / 業務 / 電話 / 門市 / 委託編號等一律不放進回傳欄位（圖片 URL 例外，依需求保留原樣）。"""
    # ---- 固定回傳 schema（拿不到就留預設值，絕不漏 key）----
    result = {
        "community_display": "",
        "price": 0,
        "floor": 0,
        "floor_total": 0,
        "area": 0.0,
        "main_area": 0.0,
        "age": 0.0,
        "layout": "",
        "building_type": "",
        "address": "",
        "parking": "無車位",
        "has_parking": False,
        "cover_image": "",
        "gallery": [],
        "og_title": "",
        "og_description": "",
    }

    # 需要主動洗掉的競品品牌字樣（信義為同業，客戶頁絕不出現）
    _BRAND_TOKENS = [
        "信義房屋", "信義不動產", "信義代銷", "信義房仲", "SINYI", "Sinyi", "sinyi", "信義",
    ]

    def _scrub_brand(text):
        """移除競品品牌字樣、電話、業務/門市/加盟店字樣，並清掉殘留分隔符。"""
        if not text:
            return ""
        t = str(text)
        for b in _BRAND_TOKENS:
            t = re.sub(r"\s*[-–—｜|/·、,，]\s*" + re.escape(b), "", t)
            t = t.replace(b, "")
        # 電話號碼
        t = re.sub(r"0\d{1,3}[-\s]?\d{5,8}(?:[-#轉分機]\d+)?", "", t)
        t = re.sub(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}", "", t)
        # 業務 / 門市 / 加盟店殘句
        t = re.sub(r"[^\s，。｜|]*(?:加盟店|直營店|門市|經紀人|營業員|仲介經紀)[^\s，。｜|]*", "", t)
        # 清掉殘留分隔符 / 空白
        t = re.sub(r"\s*[-–—]\s*$", "", t)
        t = re.sub(r"[｜|]{2,}", "｜", t)
        t = re.sub(r"^[\s｜|,，、。/·-]+", "", t)
        t = re.sub(r"[\s｜|,，、/·-]+$", "", t)
        t = re.sub(r"[ \t]{2,}", " ", t)
        return t.strip()

    def _to_int(v):
        try:
            s = re.sub(r"[^\d.\-]", "", str(v))
            if s in ("", "-", ".", "-."):
                return 0
            return int(float(s))
        except Exception:
            return 0

    def _to_float(v):
        try:
            m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
            return float(m.group(0)) if m else 0.0
        except Exception:
            return 0.0

    def _clean_address(addr):
        """砍到路段級：移除巷 / 弄 / 號 / 之N 等門牌（信義原本已是路段級，防呆再洗一次）。"""
        if not addr:
            return ""
        a = str(addr).strip()
        a = re.split(r"\d+\s*(?:巷|弄|號|之)", a)[0]
        a = re.sub(r"\d+\s*$", "", a)
        return a.strip("，,、 ")

    _TYPE_KEYWORDS = [
        "電梯大樓", "電梯華廈", "透天厝", "透天別墅", "別墅", "透天",
        "華廈", "電梯", "大樓", "公寓", "套房", "店面", "店住",
        "辦公", "廠辦", "廠房", "倉庫", "農舍", "土地", "農地", "車位",
    ]
    # 信義 houselandtype 代碼對照（M 已實測 = 華廈；其餘走關鍵字掃描備援）
    _LANDTYPE_CODE = {"M": "華廈"}

    def _building_type(landtype_list, og_title, og_desc):
        # 1) 先掃 og:title / og:description（信義標題會寫「XX區<型態>房屋出售」，最直接）
        for src in (og_title, og_desc):
            if not src:
                continue
            for kw in _TYPE_KEYWORDS:
                if kw in src:
                    return kw
        # 2) 代碼對照
        if isinstance(landtype_list, list) and landtype_list:
            code = str(landtype_list[0]).strip()
            if code in _LANDTYPE_CODE:
                return _LANDTYPE_CODE[code]
        return ""

    # ---------- 抓網頁（帶瀏覽器 UA）----------
    html = ""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        html = raw.decode("utf-8", errors="replace")
    except Exception:
        return result  # 抓不到就回預設，不炸

    # ---------- og meta（備援）----------
    def _og(prop):
        m = re.search(
            r'<meta[^>]+property=["\']%s["\'][^>]+content=["\'](.*?)["\']' % re.escape(prop),
            html, re.S,
        )
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']%s["\']' % re.escape(prop),
                html, re.S,
            )
        return m.group(1).strip() if m else ""

    og_title_raw = _og("og:title")
    og_desc_raw = _og("og:description")
    og_image = _og("og:image")

    result["og_title"] = _scrub_brand(og_title_raw)
    result["og_description"] = _scrub_brand(og_desc_raw)

    # ---------- 主來源：__NEXT_DATA__ ----------
    cd = None
    try:
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html, re.S,
        )
        if m:
            data = json.loads(m.group(1))
            cd = (
                data.get("props", {})
                .get("initialReduxState", {})
                .get("buyReducer", {})
                .get("contentData")
            )
    except Exception:
        cd = None

    if isinstance(cd, dict):
        # 社區 / 建案名（洗掉品牌 / 業務 / 加盟店字樣）
        try:
            comm = _scrub_brand(cd.get("commName") or "")
            if not comm:
                comm = _scrub_brand(cd.get("name") or "")
            result["community_display"] = comm
        except Exception:
            pass

        # 總價（萬）
        try:
            result["price"] = _to_int(cd.get("totalPrice"))
        except Exception:
            pass

        # 樓別 / 總樓
        try:
            result["floor"] = _to_int(cd.get("floor"))
            result["floor_total"] = _to_int(cd.get("floors"))
        except Exception:
            pass

        # 權狀總坪
        try:
            result["area"] = _to_float(cd.get("areaBuilding"))
        except Exception:
            pass

        # 主+附（信義 pingUsed = 主+陽，最貼近；備援用 areaInfo 主建物+陽台）
        try:
            ma = _to_float(cd.get("pingUsed"))
            if ma <= 0:
                info = cd.get("areaInfo") or []
                s = 0.0
                for it in info:
                    if isinstance(it, dict) and it.get("title") in ("主建物", "陽台", "附屬建物", "露台", "雨遮"):
                        s += _to_float(it.get("area"))
                ma = s
            result["main_area"] = ma
        except Exception:
            pass

        # 屋齡（新成屋 / 空 = 0）
        try:
            result["age"] = _to_float(cd.get("age"))
        except Exception:
            pass

        # 格局
        try:
            result["layout"] = (cd.get("layout") or cd.get("totalLayout") or "").strip()
        except Exception:
            pass

        # 型態
        try:
            result["building_type"] = _building_type(
                cd.get("houselandtype"), og_title_raw, og_desc_raw
            )
        except Exception:
            pass

        # 地址（路段級）
        try:
            result["address"] = _clean_address(cd.get("address"))
        except Exception:
            pass

        # 車位
        try:
            pk = (cd.get("parking") or "").strip()
            is_pk = bool(cd.get("isParking"))
            if is_pk and pk:
                result["parking"] = pk
                result["has_parking"] = True
            elif pk and pk not in ("無", "無車位"):
                result["parking"] = pk
                result["has_parking"] = True
            else:
                result["parking"] = "無車位"
                result["has_parking"] = False
        except Exception:
            pass

        # 圖片：封面 + 相簿（最多 12 張，原始 URL 不處理浮水印）
        try:
            imgs = [str(x) for x in (cd.get("images") or []) if x]
            if imgs:
                result["cover_image"] = imgs[0]
                result["gallery"] = imgs[:12]
        except Exception:
            pass

    # ---------- 封面 / 型態的 og 備援 ----------
    if not result["cover_image"] and og_image:
        result["cover_image"] = og_image
    if not result["gallery"] and result["cover_image"]:
        result["gallery"] = [result["cover_image"]]
    if not result["building_type"]:
        result["building_type"] = _building_type(None, og_title_raw, og_desc_raw)

    return result


if __name__ == "__main__":
    import io
    out = parse("https://www.sinyi.com.tw/buy/house/9452BZ")
    print(json.dumps(out, ensure_ascii=False, indent=1))

# ==================== 住商 ====================
# -*- coding: utf-8 -*-
"""
住商不動產 (hbhousing.com.tw) 物件詳情頁 parser。
自足 (self-contained)，只用 Python 標準庫 (re / json / urllib)。
用法: data = parse("https://www.hbhousing.com.tw/detail?sn=MS142490")
"""


def _p_hb(url):
    # --- 固定輸出 schema（拿不到就填空字串 / 0 / None，不漏 key）---
    result = {
        "community_display": "",
        "price": 0,
        "floor": 0,
        "floor_total": 0,
        "area": 0.0,
        "main_area": 0.0,
        "age": 0.0,
        "layout": "",
        "building_type": "",
        "address": "",
        "parking": "無車位",
        "has_parking": False,
        "cover_image": "",
        "gallery": [],
        "og_title": "",
        "og_description": "",
    }

    # ---------- 1. 抓 HTML（帶瀏覽器 UA）----------
    html = ""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-TW,zh;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
        html = raw.decode("utf-8", errors="replace")
    except Exception:
        return result  # 連線失敗也回完整 schema，不炸

    # ---------- 小工具 ----------
    def _to_int(v):
        try:
            if v is None or v == "":
                return 0
            return int(float(str(v).replace(",", "").strip()))
        except Exception:
            return 0

    def _to_float(v):
        try:
            if v is None or v == "":
                return 0.0
            return float(str(v).replace(",", "").strip())
        except Exception:
            return 0.0

    # 掃 og / meta content（兩種屬性順序都吃）
    def _meta(prop, attr="property"):
        try:
            m = re.search(
                r'<meta[^>]+' + attr + r'=["\']' + re.escape(prop) +
                r'["\'][^>]+content=["\']([^"\']*)["\']', html, re.I)
            if not m:
                m = re.search(
                    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+' + attr +
                    r'=["\']' + re.escape(prop) + r'["\']', html, re.I)
            return m.group(1).strip() if m else ""
        except Exception:
            return ""

    # ---------- 建立「品牌 / 業務 / 門市 / 競品網域」洗白詞庫 ----------
    brand_tokens = [
        "住商不動產", "住商", "住商機構", "hbhousing.com.tw", "hbhousing",
        "住商不動產房屋網", "房屋買賣就找",
    ]

    def _add_token(t):
        if t and isinstance(t, str):
            t = t.strip()
            if len(t) >= 2 and t not in brand_tokens:
                brand_tokens.append(t)

    # 洗白：把品牌/業務/門市字樣、電話、網域從文字欄位清掉
    def _scrub(s):
        if not s or not isinstance(s, str):
            return ""
        out = s
        # 先砍尾巴品牌（例："… - 住商不動產"）
        out = re.sub(r'\s*[-—|｜/／]\s*住商[^\-—|｜/／]*$', '', out)
        # 砍含品牌 / 房屋網 / "就找" 的行銷子句
        out = re.sub(r'[。.．]?[^。.．，,、；;]*(?:住商|房屋網|就找|hbhousing)[^。.．，,、；;]*',
                     '', out, flags=re.I)
        # 逐一移除詞庫（業務名 / 門市名 / 公司名等）
        for t in brand_tokens:
            out = out.replace(t, "")
        # 電話號碼
        out = re.sub(r'0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{3,4}', '', out)
        out = re.sub(r'09\d{2}[-\s]?\d{3}[-\s]?\d{3}', '', out)
        # 收尾：清多餘標點與空白
        out = re.sub(r'\s+', ' ', out)
        out = out.strip(" 　-—|｜/／，,、。.．；;")
        return out

    # ---------- 2. 解析 __NUXT_DATA__（欄位最全；Nuxt flat index-ref）----------
    nx = {}  # 已 deref 的物件欄位
    try:
        mnx = re.search(
            r'<script[^>]+id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>',
            html, re.S)
        if mnx:
            arr = json.loads(mnx.group(1))
            if isinstance(arr, list):
                # Nuxt flat 序列化：物件的 value 都是指向 arr 的整數 ref
                def deref(ref):
                    if isinstance(ref, int) and 0 <= ref < len(arr):
                        return arr[ref]
                    return ref

                # 目標 sn（用網址參數輔助定位正確物件 dict，避免抓到相關推薦）
                target_sn = ""
                try:
                    q = urllib.parse.urlparse(url).query
                    target_sn = (urllib.parse.parse_qs(q).get("sn", [""])[0]
                                 or "").strip()
                except Exception:
                    target_sn = ""

                interest = {
                    "sn", "objName", "price", "area", "mainArea",
                    "affiliatedArea", "room", "hall", "bath", "floor",
                    "floorTotal", "age", "style", "parking", "doorplate",
                    "road", "cityArea", "community",
                }
                best = None
                best_score = -1
                # 用 key 特徵定位物件 dict（勿寫死 index，索引會隨改版漂移）
                for el in arr:
                    if not isinstance(el, dict):
                        continue
                    score = len(interest & set(el.keys()))
                    if score < 5:
                        continue
                    try:
                        if target_sn and deref(el.get("sn")) == target_sn:
                            score += 100
                    except Exception:
                        pass
                    if score > best_score:
                        best_score = score
                        best = el

                if best is not None:
                    wanted = [
                        "sn", "objName", "community", "price", "area",
                        "mainArea", "affiliatedArea", "publicFacilityArea",
                        "room", "hall", "bath", "floor", "floorTotal", "age",
                        "style", "objType", "parking", "parking_YN",
                        "parkingMethod", "doorplate", "road", "cityArea",
                        "city", "district", "special", "completionDate",
                    ]
                    for k in wanted:
                        if k in best:
                            try:
                                nx[k] = deref(best.get(k))
                            except Exception:
                                nx[k] = None
                    # 相簿 photo1..photo20
                    photos = []
                    for i in range(1, 21):
                        pk = "photo%d" % i
                        if pk in best:
                            try:
                                pv = deref(best.get(pk))
                            except Exception:
                                pv = None
                            if pv and isinstance(pv, str) and pv.startswith("http"):
                                photos.append(pv)
                    nx["_photos"] = photos
                    # 收集競品業務 / 門市 / 公司字樣進洗白詞庫
                    try:
                        bc = deref(best.get("brokerCard"))
                        if isinstance(bc, dict):
                            for bk in ("name", "storeName", "company"):
                                _add_token(deref(bc.get(bk)))
                    except Exception:
                        pass
    except Exception:
        nx = {}

    # ---------- 3. 解析 JSON-LD（Product / Residence 命名穩定，當退路）----------
    ld_product = {}
    ld_residence = {}
    try:
        for block in re.findall(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.S):
            try:
                obj = json.loads(block)
            except Exception:
                continue
            graph = obj.get("@graph") if isinstance(obj, dict) else None
            nodes = graph if isinstance(graph, list) else [obj]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                t = node.get("@type")
                if t == "Product":
                    ld_product = node
                elif t == "Residence":
                    ld_residence = node
    except Exception:
        pass

    # ---------- 4. 逐欄位組裝（NUXT 優先，退 JSON-LD，退 og）----------
    # 4-1 社區 / 顯示名（砍品牌，隱私安全）
    try:
        community = ""
        c = nx.get("community")
        if c and isinstance(c, str) and c.strip():
            community = c.strip()
        elif nx.get("objName"):
            community = str(nx.get("objName")).strip()
        elif ld_residence.get("name"):
            community = str(ld_residence.get("name")).strip()
        result["community_display"] = _scrub(community)
    except Exception:
        pass

    # 4-2 總價（萬）
    try:
        if nx.get("price") not in (None, ""):
            result["price"] = _to_int(nx.get("price"))
        else:
            p = (ld_product.get("offers") or {}).get("price")
            result["price"] = _to_int(_to_float(p) / 10000.0) if p else 0
    except Exception:
        pass

    # 4-3 樓別 / 總樓
    try:
        result["floor"] = _to_int(nx.get("floor"))
        result["floor_total"] = _to_int(nx.get("floorTotal"))
    except Exception:
        pass

    # 4-4 權狀總坪 / 主+附
    try:
        result["area"] = _to_float(nx.get("area"))
        result["main_area"] = round(
            _to_float(nx.get("mainArea")) + _to_float(nx.get("affiliatedArea")), 2)
    except Exception:
        pass

    # 4-5 屋齡（新成屋=0）
    try:
        result["age"] = _to_float(nx.get("age"))
    except Exception:
        pass

    # 4-6 格局
    try:
        r = _to_int(nx.get("room"))
        h = _to_int(nx.get("hall"))
        b = _to_int(nx.get("bath"))
        if r or h or b:
            result["layout"] = "%d房%d廳%d衛" % (r, h, b)
        elif nx.get("special"):
            result["layout"] = re.sub(r'\(.*?\)', '', str(nx.get("special")))
    except Exception:
        pass

    # 4-7 型態
    try:
        st = nx.get("style") or ""
        result["building_type"] = str(st).strip() if st else ""
    except Exception:
        pass

    # 4-8 地址（路段級；砍巷弄號門牌）
    def _road_level(addr):
        if not addr:
            return ""
        a = str(addr)
        a = re.split(r'\d+\s*巷', a)[0]
        a = re.split(r'\d+\s*弄', a)[0]
        a = re.split(r'\d+\s*號', a)[0]
        a = re.sub(r'\d+\s*[-－]?\s*\d*\s*樓.*$', '', a)
        return a.strip()
    try:
        addr = ""
        ca = nx.get("cityArea")
        rd = nx.get("road")
        if ca and rd:
            addr = str(ca).strip() + str(rd).strip()
        elif nx.get("city") or nx.get("district") or rd:
            addr = "%s%s%s" % (nx.get("city") or "", nx.get("district") or "",
                               rd or "")
        elif ld_residence.get("address"):
            addr = (ld_residence.get("address") or {}).get("streetAddress", "")
        result["address"] = _road_level(addr)
    except Exception:
        pass

    # 4-9 車位
    try:
        pk = nx.get("parking")
        pk_yn = nx.get("parking_YN")
        has = False
        if pk_yn in (1, "1", True):
            has = True
        if pk and str(pk).strip() not in ("", "無", "無車位", "0", "None"):
            has = True
        result["has_parking"] = has
        result["parking"] = (str(pk).strip() if (has and pk) else
                             ("有車位" if has else "無車位"))
    except Exception:
        pass

    # 4-10 封面圖 + 相簿（原樣 URL，不去浮水印、不判乾淨）
    try:
        cover = _meta("og:image")
        if not cover:
            imgs = ld_product.get("image") or ld_residence.get("image") or []
            if isinstance(imgs, list) and imgs:
                cover = imgs[0]
            elif nx.get("_photos"):
                cover = nx["_photos"][0]
        result["cover_image"] = cover or ""

        gallery = list(nx.get("_photos") or [])
        if not gallery:
            imgs = ld_residence.get("image") or ld_product.get("image") or []
            if isinstance(imgs, list):
                gallery = [x for x in imgs if isinstance(x, str) and x.startswith("http")]
        seen, clean = set(), []
        for g in gallery:
            if g and g not in seen:
                seen.add(g)
                clean.append(g)
        result["gallery"] = clean[:12]
    except Exception:
        pass

    # 4-11 og 備援（砍品牌）
    try:
        result["og_title"] = _scrub(_meta("og:title"))
        desc = _meta("og:description") or _meta("description", "name")
        result["og_description"] = _scrub(desc)
    except Exception:
        pass

    return result


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(parse("https://www.hbhousing.com.tw/detail?sn=MS142490"),
                     ensure_ascii=False, indent=2))

# ==================== 台灣房屋 ====================
# -*- coding: utf-8 -*-
"""
台灣房屋 (twhg.com.tw) 物件頁 parser。
自足 / 只用 Python 標準庫 (re, json, urllib, gzip, html)。
直接貼進後端即可：  data = parse("https://www.twhg.com.tw/buy/TB02524357")
"""


def _p_twhg(url):
    # ---- 固定輸出 schema（拿不到就給空字串 / 0 / None / []）----
    out = {
        "community_display": "",
        "price": 0,
        "floor": 0,
        "floor_total": 0,
        "area": 0.0,
        "main_area": 0.0,
        "age": 0.0,
        "layout": "",
        "building_type": "",
        "address": "",
        "parking": "無車位",
        "has_parking": False,
        "cover_image": "",
        "gallery": [],
        "og_title": "",
        "og_description": "",
    }

    # ---------- 內部工具 ----------
    def _brand_scrub(s):
        """砍掉品牌 / 業務 / 加盟店 / 競品網域字樣。"""
        if not s:
            return ""
        s = str(s)
        for bad in ["台灣房屋", "台湾房屋", "twhg.com.tw", "twhg",
                    "特許加盟", "加盟店", "直營店", "不動產經紀"]:
            s = s.replace(bad, "")
        s = re.sub(r"[｜|]\s*$", "", s).strip(" ｜|-、,")
        return s.strip()

    def _to_int(v):
        try:
            if v is None or v == "":
                return 0
            m = re.search(r"-?\d+", str(v))
            return int(m.group()) if m else 0
        except Exception:
            return 0

    def _to_float(v):
        try:
            if v is None or v == "":
                return 0.0
            m = re.search(r"-?\d+(?:\.\d+)?", str(v))
            return float(m.group()) if m else 0.0
        except Exception:
            return 0.0

    def _cut_address(a):
        """砍成路段級：去掉巷 / 弄 / 號 / 樓 / 之X 及其後所有門牌。保留 路/街/段/大道。"""
        if not a:
            return ""
        a = _html.unescape(str(a)).strip()
        # 從 巷/弄/號 (含前面的中文或阿拉伯數字) 起整段砍掉
        a = re.sub(r"[0-9０-９零一二三四五六七八九十百兩]*\s*(巷|弄|號|樓|F|f).*$", "", a)
        # 收尾殘留的分隔符 / 之 / -數字
        a = re.sub(r"[\-之，,、\s]+$", "", a)
        return _brand_scrub(a).strip()

    def _detect_building_type(*texts):
        blob = " ".join([t for t in texts if t])
        # 具體型態優先於「土地」（新別墅常同時標『土地出售』）
        order = [
            ("別墅", "別墅"), ("透天厝", "透天"), ("透天", "透天"),
            ("電梯大樓", "電梯大樓"), ("大樓", "大樓"), ("華廈", "華廈"),
            ("公寓", "公寓"), ("套房", "套房"),
            ("店面", "店面"), ("店舖", "店面"), ("店鋪", "店面"),
            ("辦公", "辦公"), ("事務所", "辦公"),
            ("廠房", "廠房"), ("廠辦", "廠辦"), ("倉庫", "倉庫"),
            ("農地", "農地"), ("建地", "建地"), ("車位", "車位"),
            ("土地", "土地"),
        ]
        for kw, label in order:
            if kw in blob:
                return label
        return ""

    KIND_MAP = {  # 備援：twhg kind 型態碼（keyword 抓不到時才用）
        6: "透天",
    }

    # ---------- devalue (Nuxt3 __NUXT_DATA__) 解參照 ----------
    _UNWRAP = {"ShallowReactive", "Reactive", "Ref", "ShallowRef",
               "EmptyRef", "EmptyShallowRef", "Object", "NuxtError"}

    def _unflatten(flat):
        hydrated = {}

        def hydrate(index):
            if not isinstance(index, int):
                return index
            if index == -1:      # undefined
                return None
            if index == -2:      # hole
                return None
            if index == -3:      # NaN
                return None
            if index == -4:
                return float("inf")
            if index == -5:
                return float("-inf")
            if index == -6:
                return -0.0
            if index in hydrated:
                return hydrated[index]
            if index < 0 or index >= len(flat):
                return None
            value = flat[index]
            if isinstance(value, list):
                if len(value) and isinstance(value[0], str):
                    t = value[0]
                    if t in _UNWRAP:
                        hydrated[index] = None
                        hydrated[index] = hydrate(value[1]) if len(value) > 1 else None
                        return hydrated[index]
                    if t == "null":            # null-prototype 物件 (key,val,key,val)
                        obj = {}
                        hydrated[index] = obj
                        i = 1
                        while i + 1 < len(value):
                            obj[value[i]] = hydrate(value[i + 1])
                            i += 2
                        return obj
                    if t == "Set":
                        hydrated[index] = [hydrate(x) for x in value[1:]]
                        return hydrated[index]
                    if t == "Map":
                        d = {}
                        hydrated[index] = d
                        i = 1
                        while i + 1 < len(value):
                            d[hydrate(value[i])] = hydrate(value[i + 1])
                            i += 2
                        return d
                    if t == "Date":
                        hydrated[index] = value[1] if len(value) > 1 else None
                        return hydrated[index]
                    if t == "BigInt":
                        hydrated[index] = _to_int(value[1]) if len(value) > 1 else 0
                        return hydrated[index]
                    if t == "RegExp":
                        hydrated[index] = None
                        return None
                    # 未知 wrapper → 盡量解開
                    hydrated[index] = None
                    hydrated[index] = hydrate(value[1]) if len(value) > 1 else None
                    return hydrated[index]
                # 一般陣列（元素皆為索引）
                arr = [None] * len(value)
                hydrated[index] = arr
                for i, n in enumerate(value):
                    arr[i] = hydrate(n)
                return arr
            if isinstance(value, dict):
                obj = {}
                hydrated[index] = obj
                for k, v in value.items():
                    obj[k] = hydrate(v)
                return obj
            hydrated[index] = value
            return value

        return hydrate(0)

    def _find_main(node):
        """深度搜尋唯一含 mandate_number 的 dict。"""
        if isinstance(node, dict):
            if "mandate_number" in node and "current_price" in node:
                return node
            for v in node.values():
                r = _find_main(v)
                if r is not None:
                    return r
        elif isinstance(node, list):
            for v in node:
                r = _find_main(v)
                if r is not None:
                    return r
        return None

    # ---------- 抓網頁 ----------
    def _fetch(u):
        req = urllib.request.Request(u, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Accept": ("text/html,application/xhtml+xml,application/xml;"
                       "q=0.9,image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        elif "deflate" in enc:
            try:
                raw = zlib.decompress(raw)
            except Exception:
                try:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    pass
        return raw.decode("utf-8", "replace")

    try:
        page = _fetch(url)
    except Exception:
        return out  # 連不上 → 回空 schema，不炸

    # ---------- og meta（備援，先抓好）----------
    def _og(prop):
        for pat in (
            r'<meta[^>]+property=["\']og:%s["\'][^>]+content=["\'](.*?)["\']' % prop,
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:%s["\']' % prop,
        ):
            m = re.search(pat, page, re.S | re.I)
            if m:
                return _html.unescape(m.group(1)).strip()
        return ""

    og_title = _og("title")
    og_desc = _og("description")
    og_image = _og("image")
    # 備援用 og 內容：保留標題/描述文字，但砍掉品牌字樣（無任何欄位可含品牌名）
    out["og_title"] = _brand_scrub(og_title)
    out["og_description"] = _brand_scrub(og_desc)

    # ---------- 主源：Nuxt __NUXT_DATA__ ----------
    obj = None
    try:
        mnx = re.search(
            r'<script[^>]+id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>',
            page, re.S)
        if mnx:
            flat = json.loads(mnx.group(1))
            obj = _find_main(_unflatten(flat))
    except Exception:
        obj = None

    if isinstance(obj, dict):
        try:
            # 價格
            out["price"] = _to_int(obj.get("current_price"))

            # 樓層 "1-5/5樓" / "3/10樓"
            fl = str(obj.get("floor") or "")
            if fl:
                parts = fl.split("/")
                left = parts[0]
                right = parts[1] if len(parts) > 1 else ""
                mlo = re.search(r"-?\d+", left)
                if mlo:
                    out["floor"] = int(mlo.group())
                elif "地下" in left or "B" in left.upper():
                    mb = re.search(r"\d+", left)
                    out["floor"] = -int(mb.group()) if mb else 0
                out["floor_total"] = _to_int(right or left)

            # 坪數
            full_b = _to_float(obj.get("full_building_ping"))
            bab = _to_float(obj.get("building_and_balcony_ping"))
            land = _to_float(obj.get("land_ping"))
            area = full_b or bab or land
            out["area"] = round(area, 2)
            main = (_to_float(obj.get("building_main_ping"))
                    + _to_float(obj.get("affiliated_ping"))
                    + _to_float(obj.get("affiliated_other_ping"))
                    + _to_float(obj.get("balcony_ping")))
            out["main_area"] = round(main, 2)

            # 屋齡（空 = 全新 = 0）
            out["age"] = _to_float(obj.get("house_year"))

            # 格局
            out["layout"] = _brand_scrub(obj.get("house_layout") or "")

            # 型態
            btype = _detect_building_type(
                obj.get("building_use_name") or "",
                obj.get("name") or "",
                obj.get("full_name") or "",
                og_title,
            )
            if not btype:
                btype = KIND_MAP.get(_to_int(obj.get("kind")), "")
            out["building_type"] = btype

            # 地址（砍成路段級）
            out["address"] = _cut_address(obj.get("address") or "")

            # 車位
            sp = str(obj.get("show_parking") or "").strip()
            pp = _to_float(obj.get("parking_ping"))
            has_p = bool(pp > 0) or (sp not in ("", "無車位", "無") and "無車位" not in sp)
            out["has_parking"] = has_p
            out["parking"] = sp if sp else ("有車位" if has_p else "無車位")

            # 社區 / 建案名（砍品牌、砍業務、砍加盟店）
            out["community_display"] = _brand_scrub(obj.get("community_name") or "")

            # 圖片
            imgs = obj.get("images")
            if isinstance(imgs, list):
                clean = [i for i in imgs if isinstance(i, str) and i.startswith("http")]
                if clean:
                    out["cover_image"] = clean[0]
                    out["gallery"] = clean[:12]
        except Exception:
            pass

    # ---------- og 備援補洞 ----------
    if not out["cover_image"] and og_image:
        out["cover_image"] = og_image
        if not out["gallery"]:
            out["gallery"] = [og_image]

    if not out["building_type"]:
        out["building_type"] = _detect_building_type(og_title, og_desc)

    if not out["address"]:
        # 從 og:description 撈「XX市XX區…」
        m = re.search(r"([一-鿿]{1,4}[市縣][一-鿿]{1,4}區[一-鿿0-9０-９]{0,10})",
                      og_desc + " " + og_title)
        if m:
            out["address"] = _cut_address(m.group(1))

    if not out["price"]:
        m = re.search(r"總價\s*([0-9,]+)", og_desc)
        if m:
            out["price"] = _to_int(m.group(1))

    return out


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    r = parse("https://www.twhg.com.tw/buy/TB02524357")
    print(json.dumps(r, ensure_ascii=False, indent=2))

# ==================== 591 ====================
# -*- coding: utf-8 -*-
"""591 售屋網 (sale.591.com.tw) 物件詳情 parser。
只用 Python 標準庫，self-contained。整合進客戶推薦頁後端用。
主來源: bff-house.591.com.tw JSON API；退回 og meta。
"""

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 需要主動過濾掉的房仲品牌 / 平台字樣（不可出現在對客戶顯示的欄位）
_BRAND_TOKENS = [
    "591售屋網", "591房屋交易網", "591租屋網", "591",
    "信義房屋", "信義", "永慶房屋", "永慶", "有巢氏房屋", "有巢氏",
    "住商不動產", "住商", "台灣房屋", "東森房屋", "東森",
    "中信房屋", "中信", "21世紀不動產", "21世紀", "太平洋房屋", "太平洋",
    "全國不動產", "全國", "大家房屋", "樂屋網", "樂屋", "好房網", "好房",
    "值班先生", "值班小姐",
]


def _fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "replace")


def _to_int(v):
    try:
        if isinstance(v, (int, float)):
            return int(v)
        m = re.search(r"-?\d+", str(v).replace(",", ""))
        return int(m.group()) if m else 0
    except Exception:
        return 0


def _to_float(v):
    try:
        if isinstance(v, (int, float)):
            return float(v)
        m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
        return float(m.group()) if m else 0.0
    except Exception:
        return 0.0


def _strip_tags(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", str(s))
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def _scrub_brand(s):
    """從對客戶顯示的欄位移除品牌 / 業務姓名 / 電話。"""
    if not s:
        return ""
    s = str(s)
    # 先整段移除 591 描述常見的品牌結尾句
    s = re.sub(r"[，,、]?\s*(更多[^，,。]*?)?[，,、]?\s*就上.*$", "", s)
    for tok in _BRAND_TOKENS:
        s = s.replace(tok, "")
    # 移除電話號碼
    s = re.sub(r"0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{3,4}", "", s)
    # 移除殘留的分隔符號
    s = re.sub(r"[，,、/|\-\s]+$", "", s)
    s = re.sub(r"^[\s\-|、,，/]+", "", s)
    return s.strip()


def _road_level(addr):
    """砍掉巷弄號門牌，只留到 路 / 段 / 街 / 大道。"""
    if not addr:
        return ""
    a = _strip_tags(addr)
    # 砍掉「數字+巷/弄/號」及其後所有內容（段用中文數字，保留）
    a = re.sub(r"\d+\s*(巷|弄|號|樓|室|附\d+號).*$", "", a)
    a = re.sub(r"之\d+.*$", "", a)
    return a.strip("，, -")


def _flatten_info(info):
    """591 info = {"1":{"Layout":{name,value},...},"2":{...}} -> {key: value_text}."""
    flat = {}
    if isinstance(info, dict):
        groups = info.values()
    elif isinstance(info, list):
        groups = info
    else:
        return flat
    for grp in groups:
        if isinstance(grp, dict):
            for k, item in grp.items():
                if isinstance(item, dict) and "value" in item:
                    flat[k] = item.get("value")
    return flat


def _blank():
    return {
        "community_display": "", "price": 0, "floor": 0, "floor_total": 0,
        "area": 0.0, "main_area": 0.0, "age": 0.0, "layout": "",
        "building_type": "", "address": "", "parking": "無車位",
        "has_parking": False, "cover_image": "", "gallery": [],
        "og_title": "", "og_description": "",
    }


def _parse_from_api(data):
    out = _blank()
    ware = data.get("ware", {}) or {}
    info = _flatten_info(data.get("info", {}))

    # 社區 / 建案名（砍品牌 / 業務字樣）
    comm = ware.get("community_name") or ware.get("c_name") or ""
    if not comm:
        cobj = data.get("community") or {}
        if isinstance(cobj, dict):
            comm = cobj.get("name", "")
    out["community_display"] = _scrub_brand(_strip_tags(comm))

    # 總價（萬）
    out["price"] = _to_int(ware.get("price"))

    # 樓別 / 總樓（優先解析 info.Floor，如 "5F/13F"、"整棟/4F"、"1-3F/3F"）
    fl = _strip_tags(info.get("Floor", "")) if info.get("Floor") else ""
    floor = floor_total = 0
    if "/" in fl:
        left, right = fl.split("/", 1)
        # 整棟 / 全棟 / 非數字 -> 0（無單一樓別）
        mL = re.search(r"\d+", left)
        floor = int(mL.group()) if (mL and "整" not in left and "全" not in left) else 0
        floor_total = _to_int(right)
    if not floor_total:
        floor_total = _to_int(ware.get("allfloor"))
    if not floor:
        wf = _to_int(ware.get("floor"))
        floor = 0 if wf >= 99 else wf   # 591 以 99 代表整棟/透天全棟
    out["floor"] = floor
    out["floor_total"] = floor_total

    # 權狀總坪
    out["area"] = _to_float(ware.get("area"))

    # 主+附（主建物 + 陽台附屬）；賣方隱藏時為 0
    out["main_area"] = round(
        _to_float(ware.get("mainarea")) + _to_float(ware.get("balcony_area")), 2)

    # 屋齡（年）；新成屋 = 0
    age = _to_float(ware.get("houseage")) + _to_float(ware.get("houseage_month")) / 12.0
    out["age"] = round(age, 1)

    # 格局
    layout = info.get("Layout")
    if not layout:
        r, h, t = _to_int(ware.get("room")), _to_int(ware.get("hall")), _to_int(ware.get("toilet"))
        if r or h or t:
            layout = "%d房%d廳%d衛" % (r, h, t)
    out["layout"] = _strip_tags(layout) if layout else ""

    # 型態（華廈 / 電梯大樓 / 透天 …）
    out["building_type"] = _strip_tags(info.get("Shape", "")) or ""

    # 地址（路段級）
    addr = info.get("zAddress") or ""
    if not addr:
        # 從 street_name 組（缺縣市 / 行政區前綴時盡量退回原字串）
        addr = ware.get("street_name", "") or ""
    out["address"] = _scrub_brand(_road_level(addr))

    # 車位
    car_txt = _strip_tags(info.get("CarPlace", "")) if info.get("CarPlace") else ""
    has_park = _to_int(ware.get("cartplace")) > 0
    if car_txt and ("無" in car_txt and len(car_txt) <= 3):
        has_park = False
    if has_park:
        out["parking"] = car_txt or "含車位"
        out["has_parking"] = True
    else:
        out["parking"] = "無車位"
        out["has_parking"] = False

    # 封面圖（原樣 URL）
    cover = data.get("ogImage") or ""
    pics = data.get("pic") or []
    if not cover and pics:
        p0 = pics[0]
        cover = p0.get("big") or p0.get("medium") or p0.get("src") or ""
    if not cover and ware.get("cover"):
        cover = "https://img1.591.com.tw/house/" + str(ware.get("cover"))
    out["cover_image"] = cover

    # 相簿（最多 12 張，原樣 URL）
    gallery = []
    for p in pics:
        if not isinstance(p, dict):
            continue
        u = p.get("big") or p.get("medium") or p.get("src") or ""
        if u:
            gallery.append(u)
        if len(gallery) >= 12:
            break
    out["gallery"] = gallery

    # og 備援（原始文字，僅去除平台品牌字樣以符合過濾鐵則）
    out["og_title"] = _scrub_brand(_strip_tags(data.get("title", "")))
    out["og_description"] = _scrub_brand(_strip_tags(data.get("description", "")))
    return out


def _parse_from_html(html):
    """API 失效時的退路：只靠 server-rendered og meta。"""
    out = _blank()

    def meta(prop):
        m = re.search(
            r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)["\']' % re.escape(prop),
            html)
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(prop),
                html)
        return m.group(1) if m else ""

    title = meta("og:title") or ""
    desc = meta("og:description") or ""
    img = meta("og:image") or ""

    out["og_title"] = _scrub_brand(_strip_tags(title))
    out["og_description"] = _scrub_brand(_strip_tags(desc))
    out["cover_image"] = img

    # 從 og:description 盡量擷取：總價 / 坪數 / 社區 / 行政區
    mp = re.search(r"總價\s*([\d,]+)\s*萬", desc)
    if mp:
        out["price"] = _to_int(mp.group(1))
    ma = re.search(r"面積\s*([\d.]+)\s*坪", desc)
    if ma:
        out["area"] = _to_float(ma.group(1))
    mc = re.search(r"位於([^，,。]+?)(?:，|,|。|更多|$)", desc)
    if mc:
        out["community_display"] = _scrub_brand(_strip_tags(mc.group(1)))
    mr = re.search(r"([一-鿿]{2,3}[市縣][一-鿿]{1,4}區)", desc)
    if mr:
        out["address"] = _scrub_brand(mr.group(1))

    # 社區名退回：n-house-entrust-entry community-name="..."
    if not out["community_display"]:
        mcn = re.search(r'community-name=["\']([^"\']+)["\']', html)
        if mcn:
            out["community_display"] = _scrub_brand(_strip_tags(mcn.group(1)))

    # 型態 / 車位：server-rendered detail-house-item
    for m in re.finditer(
            r'detail-house-key">(.*?)</div>.*?detail-house-value">(.*?)</div>',
            html, re.S):
        k = _strip_tags(m.group(1))
        v = _strip_tags(m.group(2))
        if k == "型態" and v:
            out["building_type"] = v
        elif k == "車位" and v:
            if "無" in v and len(v) <= 3:
                out["parking"], out["has_parking"] = "無車位", False
            else:
                out["parking"], out["has_parking"] = v, True
    return out


def _p_h591(url):
    """輸入 591 售屋網物件 URL，回傳固定 key 的 dict。"""
    result = _blank()

    # 抓物件 id
    m = re.search(r"/detail/\d+/(\d+)\.html", url) or re.search(r"(\d{6,})", url)
    house_id = m.group(1) if m else ""

    # 主來源：BFF JSON API（僅需瀏覽器 UA，無需 cookie / CSRF）
    if house_id:
        try:
            api = "https://bff-house.591.com.tw/v1/web/sale/detail?id=%s&__v__=1" % house_id
            data = json.loads(_fetch(api))
            if isinstance(data, dict) and data.get("ware"):
                return _parse_from_api(data)
        except Exception:
            pass

    # 退路：og meta
    try:
        html = _fetch(url)
        result = _parse_from_html(html)
    except Exception:
        pass
    return result


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    u = sys.argv[1] if len(sys.argv) > 1 else "https://sale.591.com.tw/home/house/detail/2/20533726.html"
    print(json.dumps(parse(u), ensure_ascii=False, indent=2))

# ==================== 中信 ====================
# -*- coding: utf-8 -*-


def _p_ct(url):
    """
    解析「中信房屋」(buy.cthouse.com.tw) 物件詳情頁,回傳統一結構 dict。
    詳情頁為完全 SSR,urllib 一次即可取得全部欄位(不需瀏覽器/不需呼叫 API)。
    只用 Python 標準庫 (re / json / urllib),self-contained,可直接貼進別的檔案跑。
    任何欄位失敗都被 try/except 包住,不會整個炸掉;回傳固定 key 結構。
    """
    # ---- 統一輸出結構 (拿不到就給空字串 / 0 / None / 空 list,絕不漏 key) ----
    result = {
        "community_display": "",
        "price": 0,
        "floor": 0,
        "floor_total": 0,
        "area": 0.0,
        "main_area": 0.0,
        "age": 0.0,
        "layout": "",
        "building_type": "",
        "address": "",
        "parking": "無車位",
        "has_parking": False,
        "cover_image": "",
        "gallery": [],
        "og_title": "",
        "og_description": "",
    }

    # ---- 抓網頁 (帶瀏覽器 User-Agent) ----
    html = ""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-TW,zh;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        try:
            html = raw.decode("utf-8")
        except UnicodeDecodeError:
            html = raw.decode("utf-8", errors="ignore")
    except Exception:
        return result

    if not html:
        return result

    # ---- 去標籤純文字 (正規化空白),供 body 欄位 regex ----
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    def _search(pattern, src, group=1, flags=0):
        try:
            m = re.search(pattern, src, flags)
            if m:
                return m.group(group).strip()
        except Exception:
            pass
        return None

    def _scrub(s):
        """主動過濾:品牌名/業務/電話/門市/委託編號/競品網域,絕不外流到輸出。"""
        if not s:
            return ""
        out = s
        # 競品品牌 / 門市 / 加盟店 字樣
        out = re.sub(r"中信房屋(仲介)?", "", out)
        out = re.sub(r"中信房仲", "", out)
        out = re.sub(r"中信不動產", "", out)
        out = re.sub(r"中信", "", out)
        out = re.sub(r"[一-鿿]{2,}?(?:加盟店|直營店|分店|門市)", "", out)
        # 競品網域
        out = re.sub(r"(https?://)?(www\.|buy\.)?cthouse\.com\.tw\S*", "", out)
        out = re.sub(r"hbhousing\.com\.tw", "", out)
        # 電話 (市話 / 手機 / 免付費)
        out = re.sub(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}", "", out)
        out = re.sub(r"0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{3,4}", "", out)
        out = re.sub(r"0800[-\s]?\d{3}[-\s]?\d{3}", "", out)
        # 委託編號 (cthouse 物件多為 8 位純數字;保守只砍明確標注者)
        out = re.sub(r"物件編號[：:]?\s*\d+", "", out)
        out = re.sub(r"委託(書)?編號[：:]?\s*\S+", "", out)
        # 收尾:多餘的連接符 / 空白
        out = re.sub(r"[\-–—|、,，]\s*$", "", out.strip())
        out = re.sub(r"^\s*[\-–—|、,，]", "", out.strip())
        out = re.sub(r"\s{2,}", " ", out)
        return out.strip()

    # =========================================================
    # (A) og / meta
    # =========================================================
    og_title = ""
    og_desc = ""
    keywords = ""
    og_images = []
    try:
        for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
            tag = m.group(0)
            prop = _search(r'(?:property|name)\s*=\s*["\']([^"\']+)["\']', tag)
            content = _search(r'content\s*=\s*["\']([^"\']*)["\']', tag)
            if content is None:
                continue
            key = (prop or "").lower()
            if key == "og:title":
                og_title = content
            elif key == "og:description":
                og_desc = content
            elif key == "description" and not og_desc:
                og_desc = content
            elif key == "keywords":
                keywords = content
            elif key == "og:image":
                og_images.append(content)
    except Exception:
        pass

    # og 欄位供備援,但仍要洗掉品牌 / 電話 / 門市 / 委託編號
    result["og_title"] = _scrub(og_title)
    result["og_description"] = _scrub(og_desc)

    # =========================================================
    # (B) JSON-LD (取 @type = 住宅/物件 那塊,拿案名 + 街道)
    # =========================================================
    ld_name = ""
    ld_street = ""
    residence_types = (
        "SingleFamilyResidence", "Residence", "House", "Apartment",
        "Product", "Place", "Offer",
    )
    try:
        for m in re.finditer(
            r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.S | re.I,
        ):
            block = m.group(1).strip()
            try:
                data = json.loads(block)
            except Exception:
                continue
            candidates = []
            if isinstance(data, dict):
                if "@graph" in data and isinstance(data["@graph"], list):
                    candidates.extend(data["@graph"])
                else:
                    candidates.append(data)
            elif isinstance(data, list):
                candidates.extend(data)
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                t = c.get("@type", "")
                if isinstance(t, list):
                    t = " ".join(str(x) for x in t)
                if any(rt in str(t) for rt in residence_types):
                    name = c.get("name")
                    if name and not ld_name:
                        ld_name = str(name).strip()
                    addr = c.get("address")
                    if isinstance(addr, dict):
                        sa = addr.get("streetAddress")
                        if sa and not ld_street:
                            ld_street = str(sa).strip()
    except Exception:
        pass

    # =========================================================
    # (C) body 固定文字欄位
    # =========================================================
    # --- 總價 (萬) ---
    price_str = (
        _search(r"房屋總價\s*([\d,]+)\s*萬", text)
        or _search(r"總價[：:]\s*([\d,]+)\s*萬", text)
        or _search(r"([\d,]+)\s*萬", text)
    )
    if price_str:
        try:
            result["price"] = int(price_str.replace(",", ""))
        except Exception:
            pass

    # --- 權狀總坪 (建物登記) ---
    area_str = (
        _search(r"建物登記[：:]?\s*([\d.]+)\s*坪", text)
        or _search(r"主\+陽[：:]?\s*([\d.]+)\s*坪", text)
    )
    if area_str:
        try:
            result["area"] = float(area_str)
        except Exception:
            pass

    # --- 主建物 + 附屬建物 = main_area ---
    main_v = _search(r"主建物小計[：:]?\s*([\d.]+)\s*坪", text)
    attach_v = _search(r"附屬建物小計[：:]?\s*([\d.]+)\s*坪", text)
    try:
        m_area = 0.0
        got = False
        if main_v:
            m_area += float(main_v); got = True
        if attach_v:
            m_area += float(attach_v); got = True
        if got:
            result["main_area"] = round(m_area, 2)
    except Exception:
        pass

    # --- 格局 (X房Y廳Z衛) ---
    try:
        gm = re.search(
            r"格局[：:]?\s*(\d+)\s*房(?:\s*(\d+)\s*廳)?(?:\s*(\d+)\s*衛)?",
            text,
        )
        if gm:
            parts = []
            if gm.group(1):
                parts.append(gm.group(1) + "房")
            if gm.group(2):
                parts.append(gm.group(2) + "廳")
            if gm.group(3):
                parts.append(gm.group(3) + "衛")
            result["layout"] = "".join(parts)
    except Exception:
        pass

    # --- 樓層 / 總樓 ---
    fm = re.search(r"(\d+)\s*樓\s*/\s*共\s*(\d+)\s*樓", text)
    if not fm:
        fm = re.search(r"所在樓層[：:]?\s*(\d+)\s*樓\s*/\s*共\s*(\d+)\s*樓", text)
    if fm:
        try:
            result["floor"] = int(fm.group(1))
            result["floor_total"] = int(fm.group(2))
        except Exception:
            pass

    # --- 屋齡 (X年Y個月 -> float 年;新成屋=0) ---
    if re.search(r"新成屋|預售", text):
        result["age"] = 0.0
    am = re.search(r"屋齡[：:]?\s*(\d+)\s*年(?:\s*(\d+)\s*個月)?", text)
    if not am:
        am = re.search(r"(\d+)\s*年\s*(\d+)\s*個月", text)
    if am:
        try:
            years = int(am.group(1))
            months = int(am.group(2)) if am.lastindex and am.group(2) else 0
            result["age"] = round(years + months / 12.0, 2)
        except Exception:
            pass

    # --- 型態 (透天厝 / 公寓 / 華廈 / 電梯大樓 ...) ---
    btype = ""
    m = re.search(r"^\s*([一-鿿]+?)出售", og_title)
    if m:
        btype = m.group(1)
    if not btype and keywords:
        m = re.search(r"[市縣]([一-鿿]+?)出售", keywords)
        if m:
            btype = m.group(1)
    result["building_type"] = btype

    # --- 地址 (路段級,砍巷弄號門牌) ---
    # 縣市 (from keywords)
    city = ""
    m = re.search(r"([一-鿿]{2,3}[市縣])", keywords)
    if m:
        city = m.group(1)
    # 行政區 (from og:title / ld name,排除等於縣市者)
    district = ""
    for src in (og_title, ld_name):
        if not src:
            continue
        for dm in re.finditer(r"([一-鿿]{1,4}?(?:區|鄉|鎮|市))", src):
            cand = dm.group(1)
            if cand and cand != city and not cand.endswith("縣"):
                district = cand
                break
        if district:
            break
    # 街道 (from body 地址 或 ld streetAddress)
    street = _search(r"地址[：:]\s*([^\s|<>]+)", text) or ld_street or ""
    # 砍到路 / 段 / 街 / 大道,移除巷弄號門牌
    if street:
        sm = re.search(r"^(.*?(?:段|路|街|大道))", street)
        if sm:
            street = sm.group(1)
        else:
            street = re.split(r"\d|巷|弄|號", street)[0]
        street = street.strip()
    result["address"] = "".join([city, district, street])

    # --- 車位 ---
    car = _search(r"車位描述[：:]\s*([^ |<>]+)", text)
    if car:
        result["parking"] = car
        result["has_parking"] = ("無" not in car) and (car != "")
    else:
        result["parking"] = "無車位"
        result["has_parking"] = False

    # =========================================================
    # 圖片 (cover + gallery),原樣填,不去浮水印 / 不判斷乾淨與否
    # og:image 有兩個:第1個=真封面(img.hbhousing.com.tw);第2個=中信 fb_share logo → 要取第1個
    # =========================================================
    cover = ""
    for img in og_images:
        if "img.hbhousing.com.tw" in img:
            cover = img
            break
    if not cover:
        for img in og_images:
            if "cthouse.com.tw/images" not in img and "fb_share" not in img:
                cover = img
                break
    if not cover and og_images:
        cover = og_images[0]
    result["cover_image"] = cover

    gallery = []
    try:
        if cover and "img.hbhousing.com.tw" in cover:
            # 以封面檔名去掉尾端字母後綴當 prefix,只收「本物件」相簿圖(排除推薦物件)
            base = re.sub(r"[a-zA-Z]+\.jpg$", "", cover)
            if base and base != cover:
                pat = re.escape(base) + r"[a-zA-Z]+\.jpg"
                for u in re.findall(pat, html):
                    if u not in gallery:
                        gallery.append(u)
        if not gallery:
            # fallback: 全頁 hbhousing 圖床圖,去重
            for u in re.findall(
                r"https?://img\.hbhousing\.com\.tw/pictures/[^\s\"'<>\\)]+\.jpg", html
            ):
                if u not in gallery:
                    gallery.append(u)
    except Exception:
        pass
    # 封面排第一
    if cover and cover in gallery:
        gallery.remove(cover)
        gallery.insert(0, cover)
    result["gallery"] = gallery[:12]

    # 最後防線:文字欄位再洗一次品牌 / PII (不動圖片 URL)
    for k in ("community_display", "building_type", "address", "layout", "parking"):
        result[k] = _scrub(result[k]) if result[k] else result[k]
    if not result["parking"]:
        result["parking"] = "無車位"

    return result

# ==================== 21世紀 ====================
# -*- coding: utf-8 -*-


def _p_c21(url):
    """
    Parse a 21世紀不動產 (century21.com.tw) buypage listing into a unified dict.

    Standard library only. Self-contained. Server-side rendered HTML,
    extracted with regex (no __NEXT_DATA__/__NUXT__/JSON-LD on this site).
    """

    # ---- fixed output schema (defaults) ----
    result = {
        "community_display": "",
        "price": 0,
        "floor": 0,
        "floor_total": 0,
        "area": 0.0,
        "main_area": 0.0,
        "age": 0.0,
        "layout": "",
        "building_type": "",
        "address": "",
        "parking": "無車位",
        "has_parking": False,
        "cover_image": "",
        "gallery": [],
        "og_title": "",
        "og_description": "",
    }

    # ---- helpers ---------------------------------------------------------
    def _clean(s):
        if s is None:
            return ""
        s = re.sub(r"<[^>]+>", " ", s)          # strip tags
        s = s.replace("&nbsp;", " ").replace("&amp;", "&")
        s = s.replace("&quot;", '"').replace("&#039;", "'").replace("&lt;", "<").replace("&gt;", ">")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _scrub(s):
        """Remove brand / franchise-store / agent / phone info from free text."""
        if not s:
            return ""
        # phone numbers (landline / mobile)
        s = re.sub(r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{3,4}", "", s)
        s = re.sub(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}", "", s)
        # agent contact intros  e.g. 洽詢黃亭雁 / 聯絡林永中 / 專員王小明
        s = re.sub(r"(?:洽詢|聯絡|聯繫|請找|請洽|業務|專員|經紀人|服務專員|房仲)\s*[一-鿿]{2,4}",
                   "", s)
        # franchise store names  e.g. 文心捷運加盟店 / XX直營店 / XX分店
        s = re.sub(r"[一-鿿0-9A-Za-z]{1,12}?(?:加盟店|直營店|分店|門市)", "", s)
        # brand terms
        for term in ["21世紀不動產", "21世紀", "Century 21", "CENTURY 21", "Century21",
                     "century21.com.tw", "century21"]:
            s = s.replace(term, "")
        s = re.sub(r"\s+", " ", s).strip(" -：:｜|、,。.")
        return s.strip()

    _CN_NUM = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    def _cn_floor(s):
        # single simple chinese-numeral floor like "一樓" / "十樓" / "十四樓"
        m = re.search(r"([一二兩三四五六七八九十]+)\s*樓", s)
        if not m:
            return 0
        t = m.group(1)
        if t in _CN_NUM:
            return _CN_NUM[t]
        if "十" in t:
            parts = t.split("十")
            tens = _CN_NUM.get(parts[0], 1) if parts[0] else 1
            ones = _CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
            return tens * 10 + ones
        return 0

    def _to_int_price(s):
        if not s:
            return 0
        s = s.replace(",", "")
        total = 0.0
        m_yi = re.search(r"([\d.]+)\s*億", s)
        if m_yi:
            total += float(m_yi.group(1)) * 10000
        m_wan = re.search(r"([\d.]+)\s*萬", s)
        if m_wan:
            total += float(m_wan.group(1))
        if total == 0:
            m = re.search(r"[\d.]+", s)
            if m:
                total = float(m.group(0))
        return int(round(total))

    def _to_float(s):
        if not s:
            return 0.0
        m = re.search(r"[\d.]+", s.replace(",", ""))
        return float(m.group(0)) if m else 0.0

    # ---- fetch -----------------------------------------------------------
    html = ""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        html = raw.decode("utf-8", errors="replace")
    except Exception:
        return result

    if not html:
        return result

    # ---- listing id from URL --------------------------------------------
    listing_id = ""
    try:
        m = re.search(r"/buypage/(\d+)", url)
        if m:
            listing_id = m.group(1)
    except Exception:
        listing_id = ""

    # ---- structured spec rows -------------------------------------------
    fields = {}
    try:
        pat = re.compile(
            r'<h6 class="col-5"><span>(.*?)</span></h6>\s*'
            r'<div[^>]*class="[^"]*val">(.*?)</div>',
            re.S,
        )
        for label, val in pat.findall(html):
            fields[_clean(label)] = _clean(val)
    except Exception:
        fields = {}

    # ---- og / title -----------------------------------------------------
    og_img = ""
    og_desc_raw = ""
    title_raw = ""
    try:
        m = re.search(r'<meta property="og:image"\s+content="([^"]*)"', html)
        if m:
            og_img = m.group(1).strip()
    except Exception:
        pass
    try:
        m = re.search(r'<meta property="og:description"\s+content="([^"]*)"', html)
        if m:
            og_desc_raw = _clean(m.group(1))
    except Exception:
        pass
    try:
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        if m:
            title_raw = _clean(m.group(1))
    except Exception:
        pass

    # ---- price ----------------------------------------------------------
    try:
        result["price"] = _to_int_price(fields.get("總價", ""))
    except Exception:
        pass

    # ---- building_type --------------------------------------------------
    try:
        bt = fields.get("類型/現況", "")
        bt = re.sub(r"\[.*?\]", "", bt)        # drop [住宅] tag
        result["building_type"] = bt.strip()
    except Exception:
        pass

    # ---- address (road-level; cut lane/alley/number) --------------------
    try:
        addr = _scrub(fields.get("地址", ""))
        addr = re.split(r"\d*\s*巷|\d*\s*弄|\d[\d\-之]*\s*號", addr)[0].strip()
        result["address"] = addr
    except Exception:
        pass

    # ---- area (權狀總坪) -------------------------------------------------
    try:
        result["area"] = _to_float(fields.get("坪數", "") or fields.get("建物登記面積", ""))
    except Exception:
        pass

    # ---- main_area (主建物 + 附屬建物) ----------------------------------
    try:
        main = _to_float(fields.get("主建物面積", ""))
        annex = _to_float(fields.get("附屬建物面積", ""))
        result["main_area"] = round(main + annex, 2)
    except Exception:
        pass

    # ---- floor / floor_total -------------------------------------------
    try:
        fl = fields.get("樓層", "")
        nums = re.findall(r"\d+", fl)
        if nums:
            result["floor"] = int(nums[0])
            if len(nums) > 1:
                result["floor_total"] = int(nums[1])
        else:
            result["floor"] = _cn_floor(fl)
    except Exception:
        pass

    # ---- layout ---------------------------------------------------------
    try:
        result["layout"] = fields.get("格局", "").replace("(室)", "").strip()
    except Exception:
        pass

    # ---- age ------------------------------------------------------------
    try:
        age_raw = fields.get("屋齡", "")
        if ("新成屋" in age_raw) or ("預售" in age_raw):
            result["age"] = 0.0
        else:
            result["age"] = _to_float(age_raw)
    except Exception:
        pass

    # ---- parking --------------------------------------------------------
    try:
        pk = fields.get("車位", "").strip()
        pub_pk = _to_float(fields.get("公設車位", ""))
        if (not pk) or pk in ("無", "無車位", "0"):
            if pub_pk > 0:
                result["parking"] = "有車位"
                result["has_parking"] = True
            else:
                result["parking"] = "無車位"
                result["has_parking"] = False
        else:
            mp = re.search(r"[\(（]([^)）]+)[\)）]", pk)   # e.g. 有 (坡道-平面)
            result["parking"] = mp.group(1).strip() if mp else pk
            result["has_parking"] = True
    except Exception:
        pass

    # ---- community_display (from title marketing name) ------------------
    try:
        name = title_raw
        # drop brand suffix " - 21世紀不動產"
        name = re.sub(r"\s*[-｜|]\s*21世紀不動產\s*$", "", name)
        name = re.sub(r"\s*-\s*21世紀不動產\s*$", "", name)
        # take first segment before separator (selling-point split)
        seg = re.split(r"[｜|/、,，\s]", name.strip(), 1)[0]
        # strip leading emoji / decorative symbols, keep CJK + alnum
        seg = re.sub(r"^[^一-鿿A-Za-z0-9]+", "", seg)
        seg = re.sub(r"[^一-鿿A-Za-z0-9]+$", "", seg)
        result["community_display"] = _scrub(seg)
    except Exception:
        pass

    # ---- cover image ----------------------------------------------------
    try:
        cover = og_img
        if cover.startswith("//"):
            cover = "https:" + cover
        elif cover.startswith("/"):
            cover = "https://www.century21.com.tw" + cover
        result["cover_image"] = cover.strip()
    except Exception:
        pass

    # ---- gallery --------------------------------------------------------
    try:
        store = ""
        m = re.search(r"/uploads/([^/]+)/" + re.escape(listing_id) + r"/", html)
        if m:
            store = m.group(1)
        gallery = []
        if store and listing_id:
            seen = set()
            gpat = re.compile(
                r"/uploads/" + re.escape(store) + "/" + re.escape(listing_id)
                + r"/([A-Z])i\.jpg", re.I)
            letters = []
            for mm in gpat.finditer(html):
                L = mm.group(1).upper()
                if L not in seen:
                    seen.add(L)
                    letters.append(L)
            letters.sort()
            for L in letters:
                gallery.append("https://www.century21.com.tw/uploads/%s/%s/%si.jpg"
                               % (store, listing_id, L))
        # fallback: derive from cover url pattern .../Ai.jpg
        if not gallery and result["cover_image"]:
            base = re.sub(r"[A-Z]i\.jpg.*$", "", result["cover_image"])
            if base.endswith("/"):
                gallery = [result["cover_image"]]
        result["gallery"] = gallery[:12]
    except Exception:
        pass

    # ---- og_title / og_description (scrubbed backups) ------------------
    try:
        result["og_title"] = _scrub(re.sub(r"\s*-\s*21世紀不動產\s*$", "", title_raw))
    except Exception:
        pass
    try:
        result["og_description"] = _scrub(og_desc_raw)
    except Exception:
        pass

    return result

# ==================== 太平洋 ====================


def _p_pacific(url):
    """
    Parse a 太平洋房屋 (pacific.com.tw) object-detail page into a normalized dict.

    Data source: the site's internal JSON API (the HTML is AngularJS-rendered).
    Falls back to og meta only if the API is unreachable. Standard library only.
    """
    # ---- fixed output skeleton (every key always present) ----
    out = {
        "community_display": "",
        "price": 0,
        "floor": 0,
        "floor_total": 0,
        "area": 0.0,
        "main_area": 0.0,
        "age": 0.0,
        "layout": "",
        "building_type": "",
        "address": "",
        "parking": "無車位",
        "has_parking": False,
        "cover_image": "",
        "gallery": [],
        "og_title": "",
        "og_description": "",
    }

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    API_AUTH = "Basic cHJtczpwcm1z"  # base64("prms:prms"), fixed public key on every page

    # ---------- helpers ----------
    def _to_float(v):
        try:
            if v is None:
                return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            m = re.search(r"-?\d+(?:\.\d+)?", str(v))
            return float(m.group(0)) if m else 0.0
        except Exception:
            return 0.0

    def _to_int(v):
        return int(round(_to_float(v)))

    def _http_get(u, want_json=False):
        req = urllib.request.Request(u, headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*" if want_json else "text/html,*/*",
            "Authorization": API_AUTH,
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        # responses may carry a UTF-8 BOM
        text = raw.decode("utf-8-sig", errors="replace")
        return json.loads(text) if want_json else text

    def _clean_community(name):
        if not name:
            return ""
        s = str(name)
        # take the chunk before the first decorative separator (marketing taglines
        # are appended after these, e.g. "國泰香榭※台中版民生社區")
        s = re.split(r"[※●★☆◆◇■□▲△▽▼♦♥❤‧・｜|/\\\n\r\t　]+", s)[0]
        # strip unambiguous multi-char brand / franchise strings (safe substrings)
        brand_blobs = [
            "太平洋房屋", "信義房屋", "住商不動產", "台灣房屋", "東森房屋", "中信房屋",
            "21世紀不動產", "全國不動產", "大家房屋", "樂屋網", "好房網", "永慶房屋",
            "有巢氏房屋", "加盟店", "直營店", "房仲", "仲介",
        ]
        for b in brand_blobs:
            s = s.replace(b, "")
        # strip bare own-brand token only at boundaries (avoid butchering community names)
        s = re.sub(r"^\s*太平洋\s*", "", s)
        s = re.sub(r"\s*太平洋\s*$", "", s)
        # trailing generic suffix only if it dangles at the very end
        s = re.sub(r"(房屋|不動產)$", "", s)
        # tidy leftover leading/trailing punctuation & spaces
        s = s.strip(" -_.、,，。~～　")
        return s.strip()

    def _clean_address(addr):
        if not addr:
            return ""
        s = str(addr).strip()
        # keep only up to street level: drop everything from 巷/弄/號 onward
        s = re.split(r"[巷弄號]", s)[0]
        # drop any trailing house-number digits left behind (arabic / fullwidth)
        s = re.sub(r"[0-9０-９\-‐–—之]+$", "", s)
        return s.strip()

    def _parse_age(age_str):
        if age_str is None:
            return 0.0
        s = str(age_str)
        if any(k in s for k in ("新成屋", "新成", "全新", "預售")):
            return 0.0
        years = 0.0
        my = re.search(r"(\d+(?:\.\d+)?)\s*年", s)
        if my:
            years = float(my.group(1))
        mm = re.search(r"(\d+)\s*(?:個月|月)", s)
        if mm:
            years += int(mm.group(1)) / 12.0
        if years == 0.0:
            years = _to_float(s)
        return round(years, 1)

    def _og(html, prop):
        for pat in (
            r'<meta[^>]+property=["\']og:%s["\'][^>]+content=["\']([^"\']*)["\']' % prop,
            r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:%s["\']' % prop,
        ):
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    # ---------- extract saleID ----------
    sale_id = None
    m = re.search(
        r"https?://(?:www\.)?pacific\.com\.tw/(?:m/Object/Detail|Object/ObjectDetail/?)\?saleID=([A-Za-z0-9]+)",
        url,
    )
    if m:
        sale_id = m.group(1)
    else:  # generic fallback on the saleID query param
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            if qs.get("saleID"):
                sale_id = qs["saleID"][0]
        except Exception:
            sale_id = None
    if not sale_id:
        return out

    # ---------- primary source: detail API ----------
    d = None
    try:
        d = _http_get(
            "https://www.pacific.com.tw/api/ObjectAPI/GetObjectDetail/%s" % sale_id,
            want_json=True,
        )
        if not isinstance(d, dict):
            d = None
    except Exception:
        d = None

    if d is not None:
        try:
            # idx > 0 means live listing; 0 means delisted / not found
            if _to_int(d.get("idx")) <= 0:
                return out

            # community / building name (drop brand & franchise wording)
            raw_name = d.get("community") or d.get("objectName") or ""
            out["community_display"] = _clean_community(raw_name)

            # price (萬)
            out["price"] = _to_int(d.get("sellTotalPrice"))

            # floors
            out["floor"] = _to_int(d.get("onWhichFloor"))
            ft = _to_int(d.get("buildingAboveFloor"))
            if ft <= 0:
                ft = _to_int(d.get("maxFloor"))
            out["floor_total"] = ft

            # areas (坪)
            out["area"] = round(_to_float(d.get("totalArea")), 2)
            out["main_area"] = round(
                _to_float(d.get("mainBuildArea")) + _to_float(d.get("outbuildingArea")), 2
            )

            # age
            out["age"] = _parse_age(d.get("objectAge"))

            # layout
            r = _to_int(d.get("layoutRoom"))
            h = _to_int(d.get("layoutHall"))
            t = _to_int(d.get("layoutToilet"))
            layout = ""
            if r > 0:
                layout += "%d房" % r
            if h > 0:
                layout += "%d廳" % h
            if t > 0:
                layout += "%d衛" % t
            out["layout"] = layout

            # building type
            out["building_type"] = (d.get("attributName") or "").strip()

            # address (street level only)
            out["address"] = _clean_address(d.get("address"))

            # parking
            stall = _to_float(d.get("ownerStallArea")) + _to_float(d.get("pubStallArea"))
            if stall > 0:
                out["parking"] = "含車位(約%s坪)" % round(stall, 2)
                out["has_parking"] = True
            else:
                out["parking"] = "無車位"
                out["has_parking"] = False
        except Exception:
            pass

    # ---------- gallery / cover from picture API ----------
    try:
        pics = _http_get(
            "https://www.pacific.com.tw/api/ObjectAPI/GetObjectPicture/%s" % sale_id,
            want_json=True,
        )
        if isinstance(pics, list) and pics:
            urls = []
            for p in pics:
                if isinstance(p, dict):
                    u = (p.get("sysFileName") or "").strip()
                    if u and u.lower().startswith("http"):
                        urls.append(u)
            if urls:
                out["cover_image"] = urls[0]
                out["gallery"] = urls[:12]
    except Exception:
        pass

    # ---------- og meta fallback (best-effort raw values for 備援) ----------
    try:
        html = _http_get(
            "https://www.pacific.com.tw/Object/ObjectDetail/?saleID=%s" % sale_id,
            want_json=False,
        )
        out["og_title"] = _og(html, "title")
        out["og_description"] = _og(html, "description")
        # last-resort fill if API gave nothing useful
        if not out["community_display"] and out["og_title"]:
            out["community_display"] = _clean_community(out["og_title"])
    except Exception:
        pass

    return out

# ==================== 全國 ====================
# -*- coding: utf-8 -*-


def _p_nra(url):
    """
    Parser for 全國不動產 (nra.com.tw) 買屋 detail pages.
    Pure stdlib. Self-contained. Returns a fixed-key dict.

    Example URL:
      https://www.nra.com.tw/buying/bsearch_detail.php?num=675687
    """

    # ---- fixed output shape (never drop a key) ----
    out = {
        "community_display": "",
        "price": 0,
        "floor": 0,
        "floor_total": 0,
        "area": 0.0,
        "main_area": 0.0,
        "age": 0.0,
        "layout": "",
        "building_type": "",
        "address": "",
        "parking": "無車位",
        "has_parking": False,
        "cover_image": "",
        "gallery": [],
        "og_title": "",
        "og_description": "",
    }

    # ---------------- helpers ----------------
    def _fetch(u):
        req = urllib.request.Request(u, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Referer": "https://www.nra.com.tw/",
            "Accept-Language": "zh-TW,zh;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        return raw.decode("utf-8", "replace")

    def _unent(s):
        # minimal HTML entity + tag cleanup (avoid importing html module)
        if not s:
            return ""
        s = re.sub(r"<[^>]+>", "", s)
        for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
                     ("&apos;", "'"), ("　", " ")):
            s = s.replace(a, b)
        return s.strip()

    def _grab(label, h):
        # matches  LABEL：<span> VALUE </span>  (fullwidth or half colon)
        m = re.search(re.escape(label) + r"\s*[:：]\s*<span>\s*(.*?)\s*</span>",
                      h, re.S)
        return _unent(m.group(1)) if m else ""

    def _first_float(s):
        m = re.search(r"\d[\d,]*\.?\d*", s or "")
        if not m:
            return None
        try:
            return float(m.group(0).replace(",", ""))
        except ValueError:
            return None

    def _first_int(s):
        v = _first_float(s)
        return int(v) if v is not None else None

    def _strip_brand(s):
        # remove brand / store / agent noise from a display name
        if not s:
            return ""
        s = _unent(s)
        # kill known brand prefixes / phrases
        for pat in (r"全國不動產買好屋", r"全國不動產", r"全國房屋網",
                    r"全國購", r"全國房仲", r"全國"):
            s = re.sub(pat, "", s)
        # kill 加盟店 / 分店 / 公司 tokens
        s = re.sub(r"[一-鿿]{0,10}(加盟店|直營店|分店)", "", s)
        s = re.sub(r"[一-鿿]{2,20}(不動產|房屋)?仲介(有限|股份有限)?公司", "", s)
        # trim leftover separators
        s = s.strip(" -_｜|、,，:：/\\　")
        return s.strip()

    # ---------------- fetch ----------------
    try:
        html = _fetch(url)
    except Exception:
        return out

    # object id from URL (used to filter gallery images)
    num = ""
    try:
        q = urllib.parse.urlparse(url).query
        num = urllib.parse.parse_qs(q).get("num", [""])[0]
    except Exception:
        num = ""
    if not num:
        m = re.search(r"[?&]num=(\d+)", url)
        num = m.group(1) if m else ""

    # ---------------- og meta ----------------
    try:
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
                      html, re.S)
        if m:
            out["og_title"] = _unent(m.group(1))
    except Exception:
        pass
    try:
        m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
                      html, re.S)
        if m:
            out["og_description"] = _unent(m.group(1))
    except Exception:
        pass

    og_image = ""
    try:
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']',
                      html, re.S)
        if m:
            og_image = _unent(m.group(1))
            out["cover_image"] = og_image
    except Exception:
        pass

    # ---------------- top_left block: id / marketing title / price ----------------
    marketing_title = ""
    try:
        mb = re.search(r'<div class="top_left">(.*?)</div>', html, re.S)
        if mb:
            spans = re.findall(r"<span>\s*(.*?)\s*</span>", mb.group(1), re.S)
            spans = [_unent(x) for x in spans]
            # spans typically = [物件編號, 行銷標題, 總價]
            for sp in spans:
                if "萬" in sp and out["price"] == 0:
                    v = _first_int(sp)
                    if v:
                        out["price"] = v
            # marketing title = the span that is neither the id nor the price
            for sp in spans:
                if sp and not sp.isdigit() and "萬" not in sp:
                    marketing_title = sp
                    break
    except Exception:
        pass

    # price fallback: any "NNN萬" near the top of body
    if out["price"] == 0:
        try:
            m = re.search(r"([\d,]+)\s*萬", html)
            if m:
                out["price"] = int(m.group(1).replace(",", ""))
        except Exception:
            pass

    # ---------------- community_display ----------------
    # this site has NO community field -> best available name = cleaned marketing title
    disp = _strip_brand(marketing_title)
    if not disp:
        # fallback: og:title with brand stripped
        disp = _strip_brand(out["og_title"])
    out["community_display"] = disp

    # ---------------- detail rows (LABEL：<span>VALUE</span>) ----------------
    # area (權狀建物坪) = 建物登記
    v = _first_float(_grab("建物登記", html))
    if v is not None:
        out["area"] = v

    # main_area = 主建物面積 + 附屬建物面積
    ma = _first_float(_grab("主建物面積", html)) or 0.0
    fu = _first_float(_grab("附屬建物面積", html)) or 0.0
    main = round(ma + fu, 2)
    if main > 0:
        out["main_area"] = main
    # fallback: if 權狀 area missing, use 主+附+共
    if out["area"] == 0.0:
        gp = _first_float(_grab("共有部分面積", html)) or 0.0
        tot = round(ma + fu + gp, 2)
        if tot > 0:
            out["area"] = tot

    # layout
    lay = _grab("格局", html)
    if lay:
        out["layout"] = lay

    # building_type (型態)
    bt = _grab("類型", html)
    if bt:
        out["building_type"] = bt

    # age: "32年04個月" -> 32.33 ; 新成屋/預售 -> 0
    age_raw = _grab("屋齡", html)
    if age_raw:
        ym = re.search(r"(\d+)\s*年", age_raw)
        mm = re.search(r"(\d+)\s*(?:個月|月)", age_raw)
        yy = int(ym.group(1)) if ym else 0
        mo = int(mm.group(1)) if mm else 0
        if yy or mo:
            out["age"] = round(yy + mo / 12.0, 2)
        elif re.search(r"新成屋|預售", age_raw):
            out["age"] = 0.0

    # floor / floor_total : "所在樓層請電洽/地上10層"
    fl_raw = _grab("樓層/總樓高", html)
    if fl_raw:
        parts = fl_raw.split("/")
        # current floor = first segment (may be 請電洽 -> 0)
        cf = _first_int(parts[0]) if parts else None
        if cf is not None:
            out["floor"] = cf
        # total floor = segment containing 地上, else last segment
        total_seg = ""
        for p in parts:
            if "地上" in p:
                total_seg = p
                break
        if not total_seg and len(parts) > 1:
            total_seg = parts[-1]
        ft = _first_int(total_seg)
        if ft is not None:
            out["floor_total"] = ft

    # parking
    pk = _grab("車位", html)
    if pk and pk not in ("無", "無車位", "-", ""):
        out["parking"] = pk
        out["has_parking"] = True
    else:
        out["parking"] = "無車位"
        out["has_parking"] = False

    # ---------------- address (road-level) ----------------
    addr = ""
    # 1) dedicated span.add (cleanest)
    m = re.search(r'<span class="add">\s*(.*?)\s*</span>', html, re.S)
    if m:
        addr = _unent(m.group(1))
    # 2) fallback: google map init &address=...&
    if not addr:
        m = re.search(r"[?&]address=([^&\"']+)", html)
        if m:
            addr = _unent(urllib.parse.unquote(m.group(1)))
    # strip lane/alley/number -> keep to 路/街/段
    if addr:
        # cut everything from the first 巷/弄/號 onward
        addr = re.split(r"\d*\s*[巷弄號]", addr)[0]
        # also drop trailing standalone digits (e.g. "...路 76")
        addr = re.sub(r"\s*\d+\s*$", "", addr).strip()
        out["address"] = addr

    # ---------------- gallery (distinct upload/house/{num}_*.jpg) ----------------
    try:
        gal = []
        if num:
            pat = re.compile(r"upload/house/" + re.escape(num) + r"_\d+\.jpg")
        else:
            pat = re.compile(r"upload/house/\d+_\d+\.jpg")
        for m in pat.finditer(html):
            rel = m.group(0)
            full = "https://www.nra.com.tw/" + rel
            if full not in gal:
                gal.append(full)
            if len(gal) >= 12:
                break
        out["gallery"] = gal
        # cover fallback if og:image was missing
        if not out["cover_image"] and gal:
            out["cover_image"] = gal[0]
    except Exception:
        pass

    return out


if __name__ == "__main__":
    d = parse("https://www.nra.com.tw/buying/bsearch_detail.php?num=675687")
    print(json.dumps(d, ensure_ascii=False, indent=2))

# ==================== 大家 ====================
# -*- coding: utf-8 -*-


def _p_gh(url):
    """
    大家房屋 (great-home.com.tw) 物件詳情頁 parser。
    只用 Python 標準庫，self-contained，可直接複製貼上使用。
    網站為 jQuery + ASP.NET 伺服器端整頁渲染，urllib 即可拿到全部資料。
    回傳固定 key 的 dict；任何欄位失敗都不會讓整個函式炸掉。
    """

    # ---- 固定 key 的預設骨架（抓不到就維持預設）----
    result = {
        "community_display": "",
        "price": 0,
        "floor": 0,
        "floor_total": 0,
        "area": 0.0,
        "main_area": 0.0,
        "age": 0.0,
        "layout": "",
        "building_type": "",
        "address": "",
        "parking": "無車位",
        "has_parking": False,
        "cover_image": "",
        "gallery": [],
        "og_title": "",
        "og_description": "",
    }

    # ---- 品牌 / 業務 / 門市 字樣過濾（絕不能出現在回傳欄位）----
    _BRAND_WORDS = [
        "大家房屋房屋網", "大家房屋", "大家網友", "大家",
        "great-home", "great_home",
        "買屋就找", "找房子", "房屋網", "房仲", "房屋仲介",
        "加盟店", "值班人員", "不動產仲介有限公司", "仲介有限公司",
    ]

    def _clean_brand(s):
        if not s:
            return ""
        s = str(s)
        # 砍掉電話（含分機）
        s = re.sub(r"0\d[\d\-]{5,}(?:\s*分機\s*\d+)?", "", s)
        s = re.sub(r"\d{2,4}-\d{6,8}(?:轉\d+)?", "", s)
        # 砍掉整句行銷結尾（例：買屋就找大家房屋網）
        s = re.sub(r"[買賣租]屋就找[^,，、]*", "", s)
        # 砍掉「XX加盟店 / XX不動產仲介有限公司」殘句
        s = re.sub(r"[一-龥A-Za-z0-9]{0,10}加盟店", "", s)
        s = re.sub(r"[一-龥]{0,12}不動產仲介有限公司", "", s)
        for w in _BRAND_WORDS:
            s = s.replace(w, "")
        # 丟掉被清空 / 只剩品牌殘字的逗號分段，再重組
        parts = [p.strip() for p in re.split(r"[,，]", s)]
        parts = [p for p in parts if p and p not in ("網", "屋", "-", "─")]
        s = ",".join(parts)
        s = re.sub(r"\s+", " ", s).strip(" ,，、-─—\t\r\n")
        return s

    def _strip_tags(s):
        return re.sub(r"<[^>]+>", " ", s or "")

    def _to_float(s):
        m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
        return float(m.group(0)) if m else 0.0

    def _to_int(s):
        m = re.search(r"-?\d+", str(s).replace(",", ""))
        return int(m.group(0)) if m else 0

    # 全形數字 -> 半形
    _FW = {ord(c): ord(h) for c, h in zip("０１２３４５６７８９", "0123456789")}
    _CN_NUM = {"0": "〇", "1": "一", "2": "二", "3": "三", "4": "四",
               "5": "五", "6": "六", "7": "七", "8": "八", "9": "九", "10": "十"}

    def _num_to_cn(n):
        return _CN_NUM.get(n, n)

    def _normalize_address(addr):
        if not addr:
            return ""
        addr = _strip_tags(addr)
        addr = addr.translate(_FW)            # 全形數字轉半形
        # 路段級：砍掉巷 / 弄 / 號 / 樓 及其後所有內容（只留到路 / 段 / 街）
        addr = re.split(r"[0-9]*巷|[0-9]*弄|[0-9]+號|[0-9]+樓|之\d+", addr)[0]
        # 「N段」數字轉中文（配合站方習慣：中華路一段）
        addr = re.sub(r"([0-9]{1,2})段", lambda m: _num_to_cn(m.group(1)) + "段", addr)
        addr = re.sub(r"\s+", "", addr).strip(" ,，、-")
        return _clean_brand(addr)

    def _find(pat, text, flags=re.S, group=1):
        m = re.search(pat, text, flags)
        return m.group(group).strip() if m else ""

    # ---------- 抓網頁 ----------
    html = ""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/125.0.0.0 Safari/537.36"),
                "Accept-Language": "zh-TW,zh;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
        html = raw.decode("utf-8", errors="replace")
    except Exception:
        return result  # 連不上就回預設骨架

    # ---------- (A) <head> meta ----------
    try:
        og_img = _find(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
        if not og_img:
            og_img = _find(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
        if og_img:
            if og_img.startswith("//"):
                og_img = "https:" + og_img
            og_img = og_img.split("?")[0]
            result["cover_image"] = og_img
    except Exception:
        pass

    meta_desc = ""
    try:
        meta_desc = _find(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', html)
        result["og_description"] = _clean_brand(meta_desc)
    except Exception:
        pass

    # og:title 站方沒有 -> 用 <title> 當備援（砍品牌 / 前綴）
    try:
        raw_title = _find(r"<title>(.*?)</title>", html)
        raw_title = re.sub(r"\s+", " ", raw_title).strip()
        raw_title = re.sub(r"^買屋[─\-\s]*", "", raw_title)
        result["og_title"] = _clean_brand(raw_title)
    except Exception:
        pass

    # ---------- (B) <body> 伺服器渲染 ----------

    # 總價（萬）
    try:
        result["price"] = _to_int(_find(r'class=["\']hightlightprice["\'][^>]*>([^<]+)<', html))
    except Exception:
        pass

    # 樓層 / 總樓
    try:
        block = _find(r"<td>樓層</td>\s*<td[^>]*>(.*?)</td>", html)
        block = _strip_tags(block)
        mm = re.search(r"(-?\d+)\s*/\s*(\d+)", block)
        if mm:
            result["floor"] = int(mm.group(1))
            result["floor_total"] = int(mm.group(2))
        else:
            # 備援：摘要列 icon_floor 「6 / 19 樓」
            fb = _strip_tags(_find(r'icon_floor["\'][^>]*>(.*?)</li>', html))
            mm2 = re.search(r"(-?\d+)\s*/\s*(\d+)", fb)
            if mm2:
                result["floor"] = int(mm2.group(1))
                result["floor_total"] = int(mm2.group(2))
    except Exception:
        pass

    # 面積：權狀總坪（建物面積 / 建物坪數總計）
    try:
        area = _find(r"<td>建物坪數總計</td>\s*<td[^>]*>([^<]+)</td>", html)
        if not area:
            area = _find(r"<td>建物面積</td>\s*<td[^>]*>([^<]*?坪)", html)
        result["area"] = _to_float(area)
    except Exception:
        pass

    # 主 + 附 建物
    try:
        main = _to_float(_find(r"主建物</td>\s*<td[^>]*>([^<]*?坪)", html))
        sub = _to_float(_find(r"附屬建物</td>\s*<td[^>]*>([^<]*?坪)", html))
        result["main_area"] = round(main + sub, 2)
    except Exception:
        pass

    # 屋齡（新成屋 = 0）
    try:
        age_raw = _find(r"<td>屋齡</td>\s*<td[^>]*>(.*?)</td>", html)
        age_raw = _strip_tags(age_raw)
        if ("新成屋" in age_raw) or ("預售" in age_raw) or ("--" in age_raw):
            result["age"] = 0.0
        else:
            result["age"] = _to_float(age_raw)
    except Exception:
        pass

    # 格局：房 / 廳 / 衛
    try:
        room = _strip_tags(_find(r'icon_room["\'][^>]*>(.*?)</li>', html))
        mm = re.search(r"(\d+)\s*房.*?(\d+)\s*廳.*?(\d+)\s*衛", room)
        if not mm:
            mm = re.search(r"(\d+)\s*房\s*/\s*(\d+)\s*廳\s*/\s*(\d+)\s*衛", meta_desc)
        if mm:
            result["layout"] = "%s房%s廳%s衛" % (mm.group(1), mm.group(2), mm.group(3))
    except Exception:
        pass

    # 型態（華廈 / 大樓 / 透天 …）
    try:
        bt = ""
        icon_age = _strip_tags(_find(r'icon_age["\'][^>]*>(.*?)</li>', html))
        m_bt = re.search(r"[\d.]+年\s*(\S+)", icon_age)
        if m_bt:
            bt = m_bt.group(1)
        if not bt:
            # 備援：description 型態段
            parts = [p.strip() for p in meta_desc.split(",")]
            for p in parts:
                if p in ("大樓", "華廈", "公寓", "透天", "透天厝", "套房", "別墅", "廠辦", "辦公", "店面", "土地"):
                    bt = p
                    break
        result["building_type"] = _clean_brand(bt)
    except Exception:
        pass

    # 地址（路段級）：優先 body item_add
    try:
        addr = _find(r'class=["\']item_add["\']>([^<]+)<', html)
        if not addr:
            # 備援：keywords 第一欄「縣市區 路段,賣點」
            kw = _find(r'<meta[^>]+name=["\']keywords["\'][^>]+content=["\']([^"\']*)["\']', html)
            addr = kw.split(",")[0] if kw else ""
        result["address"] = _normalize_address(addr)
    except Exception:
        pass

    # 車位
    try:
        pk = _strip_tags(_find(r"<td>車位</td>\s*<td[^>]*>(.*?)</td>", html)).strip()
        pk = re.sub(r"\s+", "", pk)
        if pk in ("", "--", "無", "無車位", "0"):
            result["parking"] = "無車位"
            result["has_parking"] = False
        else:
            result["parking"] = _clean_brand(pk) or "無車位"
            result["has_parking"] = result["parking"] != "無車位"
    except Exception:
        pass

    # 社區 / 建案名 -> community_display（社區欄=-- 時退回賣點名）
    try:
        comm = _strip_tags(_find(r"<td>社區</td>\s*<td[^>]*>(.*?)</td>", html)).strip()
        comm = re.sub(r"\s+", "", comm)
        if comm in ("", "--", "無"):
            comm = ""
        if not comm:
            comm = _find(r'class=["\']item_name["\']>([^<]+)<', html)
        result["community_display"] = _clean_brand(comm)
    except Exception:
        pass

    # ---------- 相簿（全圖）----------
    try:
        raw_imgs = re.findall(r"//img\.great-home\.com\.tw/pictures/[^\s\"'>)]+\.(?:jpg|jpeg|png|webp)", html, re.I)
        seen = []
        for u in raw_imgs:
            base = u.split("?")[0]
            full = "https:" + base
            if full not in seen:
                seen.append(full)
        # 封面排第一
        cov = result["cover_image"]
        if cov and cov in seen:
            seen.remove(cov)
            seen.insert(0, cov)
        result["gallery"] = seen[:12]
        if not result["cover_image"] and seen:
            result["cover_image"] = seen[0]
    except Exception:
        pass

    return result

# ==================== 樂屋 ====================
# -*- coding: utf-8 -*-
def _p_rakuya(url):
    """
    樂屋網 (rakuya.com.tw) sell_item parser -- self-contained, stdlib only.

    抓取策略（穩健度由高到低，全部包 try/except，任何一步失敗都不會整個炸掉）：
      1. POST /gtm-data/item-data-layer/detail   -> 可靠的文字欄位（price/坪數/屋齡/區域/型態/車位tag），
         此端點對純程式請求開放（帶完整瀏覽器標頭即回 200）。
      2. GET  /sell_item/api/item-environment/list -> 路段名（itemRoad）。
      3. 盡力抓詳情頁 HTML（被 Cloudflare 保護、資料中心 IP 常吃 403，但在客戶端後端
         或未被挑戰時可通）-> 解析 window.itemInfo / JSON-LD / og meta，補齊「封面圖、相簿、
         精準格局(廳/衛/陽)、樓別/總樓、主+附坪」等 API 拿不到的欄位。
    回傳固定 key 的 dict；競品業務資訊(itemContact.sellerInfo) 一律不讀、不回傳。
    """
    import re, json, time, urllib.request

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    BASE = "https://www.rakuya.com.tw"

    # ---- 固定輸出骨架（拿不到就維持預設，不漏 key）----
    data = {
        "community_display": "", "price": 0, "floor": 0, "floor_total": 0,
        "area": 0.0, "main_area": 0.0, "age": 0.0, "layout": "",
        "building_type": "", "address": "", "parking": "無車位", "has_parking": False,
        "cover_image": "", "gallery": [], "og_title": "", "og_description": "",
    }

    # 需主動過濾的品牌 / 競品字樣
    BRANDS = ["樂屋網", "樂屋", "永慶房屋", "永慶", "信義房屋", "信義", "住商不動產", "住商",
              "台灣房屋", "東森房屋", "東森", "中信房屋", "中信", "21世紀不動產", "21世紀",
              "太平洋房屋", "太平洋", "全國不動產", "大家房屋", "有巢氏房屋", "有巢氏", "好房網"]

    # ---------- 小工具 ----------
    def _to_float(s):
        if s is None:
            return None
        m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
        return float(m.group()) if m else None

    def _to_int(s):
        f = _to_float(s)
        return int(round(f)) if f is not None else None

    def _scrub(text):
        """移除品牌名、電話、加盟店/仲介字樣，回傳乾淨字串。"""
        if not text:
            return ""
        t = str(text)
        t = re.sub(r"^\s*\[[^\]]*\]\s*", "", t)          # 去掉開頭 [樂屋網] 之類
        for b in BRANDS:
            t = t.replace(b, "")
        t = re.sub(r"0\d[\d\-\(\)\s]{6,}\d", "", t)       # 去電話號碼
        t = re.sub(r"(直營店|加盟店|不動產經紀|房仲|仲介經紀).*$", "", t)
        return re.sub(r"\s{2,}", " ", t).strip(" -｜|·．.、，,")

    def _strip_addr(a):
        """砍到路段級：只留到 街/路/段/大道/路X段，去掉巷弄號門牌樓。"""
        if not a:
            return ""
        a = _scrub(a)
        base = a
        # 先保留到「街/路/段/大道」，再砍掉其後的「巷/弄/號/樓/之X」
        m2 = re.search(r"^(.*?(?:[一二三四五六七八九十\d]+段|大道|路|街))", a)
        if m2:
            base = m2.group(1)
        base = re.split(r"\d+巷|\d+弄|\d+號|之\d+|\d+樓", base)[0]
        return base.strip()

    def _extract_js_object(html, marker):
        """從 HTML 找 `marker` 後第一個平衡的大括號 {...}，回傳 dict（考慮字串內的括號/跳脫）。"""
        idx = html.find(marker)
        if idx < 0:
            return None
        start = html.find("{", idx)
        if start < 0:
            return None
        depth, i, in_str, esc, quote = 0, start, False, False, ""
        while i < len(html):
            c = html[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == quote:
                    in_str = False
            else:
                if c in ('"', "'"):
                    in_str, quote = True, c
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        blob = html[start:i + 1]
                        try:
                            return json.loads(blob)
                        except Exception:
                            return None
            i += 1
        return None

    def _http(u, method="GET", body=None, ctype=None, doc=False, timeout=25):
        if doc:
            headers = {
                "User-Agent": UA,
                "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                           "image/avif,image/webp,*/*;q=0.8"),
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                "Referer": BASE + "/",
                "sec-ch-ua": '"Chromium";v="125", "Not.A/Brand";v="24", "Google Chrome";v="125"',
                "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        else:
            headers = {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                "Referer": BASE + "/sell_item/info",
                "sec-ch-ua": '"Chromium";v="125", "Not.A/Brand";v="24", "Google Chrome";v="125"',
                "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin", "X-Requested-With": "XMLHttpRequest",
            }
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(u, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return raw.decode("utf-8", "replace")

    # ---------- 取得 ehid ----------
    m = re.search(r"ehid=([0-9a-fA-F]{15})", url)
    if not m:
        return data
    ehid = m.group(1).lower()
    ref = BASE + "/sell_item/info?ehid=" + ehid

    city = region = road = ""

    # ---------- (1) GTM data-layer（可靠文字欄位）----------
    try:
        txt = _http(BASE + "/gtm-data/item-data-layer/detail", "POST",
                    json.dumps({"ehid": ehid}).encode("utf-8"), "application/json")
        item = json.loads(txt)["dataLayers"][0]["ecommerce"]["items"][0]

        comm = _scrub(item.get("item_category3"))
        if comm:
            data["community_display"] = comm

        p = item.get("price")
        if p:
            data["price"] = int(round(float(p) / 10000.0))

        a = _to_float(item.get("item_variant"))
        if a:
            data["area"] = a
        ms = _to_float(item.get("object_main_size"))
        if ms:
            data["main_area"] = ms   # 僅主建物（API 無附屬，HTML 會再補成主+附）

        ag = _to_float(item.get("age"))
        if ag is not None and str(item.get("object_type3", "")).find("預售") < 0:
            data["age"] = ag

        bt = _scrub(item.get("item_category5"))
        if bt:
            data["building_type"] = bt

        bed = _to_int(item.get("bedrooms"))
        if bed is not None:
            data["layout"] = "%d房" % bed

        city = str(item.get("item_category", "") or "")
        region = re.sub(r"^\d+", "", str(item.get("item_category2", "") or ""))

        tags = str(item.get("object_tag", "") or "")
        if "車位" in tags:
            data["has_parking"] = True
            mt = re.search(r"([^,，]*車位)", tags)
            data["parking"] = mt.group(1) if mt else "有車位"
    except Exception:
        pass

    # ---------- (2) 環境 API（路段）----------
    try:
        txt = _http(BASE + "/sell_item/api/item-environment/list?ehid=" + ehid)
        road = str(json.loads(txt).get("data", {}).get("itemRoad", "") or "")
    except Exception:
        pass

    if not data["address"]:
        data["address"] = _strip_addr((city or "") + (region or "") + (road or ""))

    # ---------- (3) 盡力抓詳情頁 HTML（補圖片/精準格局/樓層/主+附坪）----------
    html = None
    for _ in range(4):
        try:
            h = _http(ref, doc=True)
            if h and "itemInfo" in h:
                html = h
                break
        except Exception:
            pass
        time.sleep(0.4)

    if html:
        # og meta（備援 + og_title/og_description）
        try:
            def _og(prop):
                mm = re.search(r'<meta[^>]+property=["\']og:%s["\'][^>]+content=["\']([^"\']*)["\']' % prop, html) \
                    or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:%s["\']' % prop, html)
                return mm.group(1) if mm else ""
            og_img = _og("image")
            data["og_title"] = _scrub(_og("title"))
            data["og_description"] = _scrub(_og("description"))
            if og_img:
                data["cover_image"] = og_img
        except Exception:
            pass

        # JSON-LD（備援）
        ld_prod = None
        try:
            for blk in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
                try:
                    obj = json.loads(blk.strip())
                except Exception:
                    continue
                graph = obj.get("@graph", [obj]) if isinstance(obj, dict) else []
                for node in graph:
                    t = node.get("@type")
                    if t == "Product" or (isinstance(t, list) and "Product" in t):
                        ld_prod = node
                        break
        except Exception:
            pass

        # window.itemInfo（最完整，優先覆蓋）
        ii = None
        try:
            ii = _extract_js_object(html, "window.itemInfo")
        except Exception:
            ii = None

        if ii:
            try:
                title = ii.get("title", {}) or {}
                price = ii.get("price", {}) or {}
                detail = ii.get("detail", {}) or {}
                images = ii.get("images", {}) or {}

                comm = _scrub(title.get("community"))
                if comm:
                    data["community_display"] = comm

                addr = _strip_addr(title.get("address"))
                if addr:
                    data["address"] = addr

                pv = _to_int(price.get("price"))
                if pv:
                    data["price"] = pv
                av = _to_float(price.get("totalsize"))
                if av:
                    data["area"] = av

                main = _to_float(detail.get("mainSize")) or 0.0
                sub = _to_float(detail.get("subSize")) or 0.0
                if main:
                    data["main_area"] = round(main + sub, 2)   # 主 + 附

                agev = _to_float(detail.get("ageValue"))
                if agev is not None:
                    data["age"] = 0.0 if str(detail.get("agetype", "")).find("新成") >= 0 else agev

                # 格局：房/廳/衛/陽
                bed = _to_int(detail.get("patternBedrooms")) or 0
                liv = _to_int(detail.get("patternLivingrooms")) or 0
                bath = _to_int(detail.get("patternBathrooms")) or 0
                balc = _to_int(detail.get("patternBalconies")) or 0
                parts = []
                if bed:
                    parts.append("%d房" % bed)
                if liv:
                    parts.append("%d廳" % liv)
                if bath:
                    parts.append("%d衛" % bath)
                if balc:
                    parts.append("%d陽台" % balc)
                if parts:
                    data["layout"] = "".join(parts)

                use = _scrub(detail.get("itemUseType"))
                if use:
                    data["building_type"] = use.split("/")[-1].strip() or use

                # 樓層：純數字才當樓別；B1/整棟等 -> 0
                trans = str(detail.get("transFloors", "") or "")
                sur = _to_int(detail.get("surFloors")) or _to_int(detail.get("maxFloors"))
                if sur:
                    data["floor_total"] = sur
                if re.fullmatch(r"\d+", trans.strip()):
                    data["floor"] = int(trans.strip())
                else:
                    data["floor"] = 0

                # 車位
                pk = str(detail.get("parking", "") or "")
                pkt = _scrub(detail.get("parkingType"))
                memo = str(price.get("priceParkingMemo", "") or "")
                if "無" in pk:
                    data["has_parking"] = False
                    data["parking"] = "無車位"
                elif "有" in pk or pkt or "含車位" in memo:
                    data["has_parking"] = True
                    data["parking"] = pkt or "有車位"

                # 圖片：photo[] 為相簿，[0] 為封面
                photos = images.get("photo", []) or []
                urls = []
                for ph in photos:
                    u = ph.get("url") if isinstance(ph, dict) else ph
                    if u:
                        urls.append(u)
                if urls:
                    data["cover_image"] = urls[0]
                    data["gallery"] = urls[:12]
            except Exception:
                pass

        # 若 itemInfo 缺，用 JSON-LD 補
        if ld_prod:
            try:
                if not data["cover_image"]:
                    imgs = ld_prod.get("image") or []
                    if isinstance(imgs, str):
                        imgs = [imgs]
                    if imgs:
                        data["cover_image"] = imgs[0]
                        if not data["gallery"]:
                            data["gallery"] = imgs[:12]
                if not data["price"]:
                    lp = (ld_prod.get("offers") or {}).get("price")
                    if lp:
                        data["price"] = int(round(float(lp) / 10000.0))
                if not data["main_area"]:
                    fs = (ld_prod.get("floorSize") or {}).get("value")
                    if fs:
                        data["main_area"] = _to_float(fs)
                if not data["address"]:
                    ad = ld_prod.get("address") or {}
                    data["address"] = _strip_addr(
                        (ad.get("addressLocality", "") or "") +
                        (ad.get("addressRegion", "") or "") +
                        (ad.get("streetAddress", "") or ""))
                if not data["layout"]:
                    b = _to_int(ld_prod.get("numberOfBedrooms")) or 0
                    ba = _to_int(ld_prod.get("numberOfBathroomsTotal")) or 0
                    seg = (("%d房" % b) if b else "") + (("%d衛" % ba) if ba else "")
                    if seg:
                        data["layout"] = seg
            except Exception:
                pass

    # ---------- 收尾：型別 / 過濾保險 ----------
    try:
        data["community_display"] = _scrub(data["community_display"])
        data["area"] = float(data["area"] or 0.0)
        data["main_area"] = float(data["main_area"] or 0.0)
        data["age"] = float(data["age"] or 0.0)
        data["price"] = int(data["price"] or 0)
        data["floor"] = int(data["floor"] or 0)
        data["floor_total"] = int(data["floor_total"] or 0)
        data["has_parking"] = bool(data["has_parking"])
        if not data["parking"]:
            data["parking"] = "無車位" if not data["has_parking"] else "有車位"
        data["gallery"] = [g for g in (data["gallery"] or []) if g][:12]
    except Exception:
        pass

    return data

# ==================== 好房 ====================
def _p_housefun(url):
    """
    好房網 (housefun.com.tw) 買屋物件頁 parser。
    只用 Python 標準庫 (re / json / urllib)。self-contained，可直接複製使用。
    回傳固定 key 的 dict；抓不到的欄位填 空字串 / 0 / None，不漏 key。
    主資料來源：頁面 server-rendered 的單一 JSON-LD (@graph)；抓不到再退 og meta。
    """
    import re
    import json
    from urllib import request as _request
    from urllib.parse import urlparse

    # ---- 固定回傳骨架 ----
    result = {
        "community_display": "",
        "price": 0,
        "floor": 0,
        "floor_total": 0,
        "area": 0.0,
        "main_area": 0.0,
        "age": 0.0,
        "layout": "",
        "building_type": "",
        "address": "",
        "parking": "無車位",
        "has_parking": False,
        "cover_image": "",
        "gallery": [],
        "og_title": "",
        "og_description": "",
    }

    # ---- 小工具：HTML entity 反轉義（只用 re，不 import html）----
    def _unescape(s):
        if not s:
            return ""
        def _rep(m):
            code = m.group(1)
            try:
                if code[:1] in ("x", "X"):
                    return chr(int(code[1:], 16))
                return chr(int(code))
            except Exception:
                return m.group(0)
        s = re.sub(r"&#([xX]?[0-9a-fA-F]+);", _rep, s)
        for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                     ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
                     ("&nbsp;", " ")):
            s = s.replace(a, b)
        return s

    # ---- 小工具：URL 協定正規化（//、http → https，避免混合內容被瀏覽器擋）----
    def _fix_url(u):
        if not u:
            return ""
        u = _unescape(u).strip()
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("http://"):
            return "https://" + u[len("http://"):]
        return u

    # ---- 競品品牌 / 仲介 / 門市 字樣過濾表（任何欄位都不可外洩）----
    _BRANDS = [
        "好房網買屋", "好房網售屋", "好房網", "好房", "housefun", "Housefun", "HouseFun",
        "永慶不動產", "永慶房屋", "永慶", "信義房屋", "信義房仲", "住商不動產", "住商",
        "台灣房屋", "東森房屋", "中信房屋", "21世紀不動產", "太平洋房屋", "太平洋房仲",
        "有巢氏房屋", "有巢氏", "全國不動產", "大家房屋", "力霸房屋", "群義房屋",
        "加盟店", "直營店", "房仲", "仲介",
    ]

    def _strip_brands(t, extra=None):
        """移除競品品牌 / 電話 / 門市字樣。"""
        if not t:
            return ""
        t = _unescape(t)
        brands = (extra or []) + _BRANDS
        for b in brands:
            if b:
                t = t.replace(b, "")
        # 電話（市話 / 手機）
        t = re.sub(r"09\d{2}[\-\s]?\d{3}[\-\s]?\d{3}", "", t)
        t = re.sub(r"0\d[\d\-\s()（）]{6,}\d", "", t)
        # 收尾：壓縮分隔符與空白
        t = re.sub(r"[|｜]{2,}", "|", t)
        t = re.sub(r"\s{2,}", " ", t)
        return t.strip(" |｜-、,，　")

    # ---- 抓取頁面 ----
    try:
        req = _request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/122.0.0.0 Safari/537.36"),
            "Accept-Language": "zh-TW,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        with _request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
        html_text = raw.decode("utf-8", "replace")
    except Exception:
        return result  # 抓不到 → 回空骨架，不炸

    # ---- meta 讀取 ----
    def _meta(prop):
        try:
            m = re.search(
                r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop) +
                r'["\'][^>]*?content=["\']([^"\']*)["\']', html_text, re.I)
            if not m:
                m = re.search(
                    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*?(?:property|name)=["\']' +
                    re.escape(prop) + r'["\']', html_text, re.I)
            return _unescape(m.group(1)) if m else ""
        except Exception:
            return ""

    # ---- 解析 JSON-LD @graph ----
    web_node = res_node = prod_node = None
    try:
        for m in re.finditer(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html_text, re.DOTALL | re.I):
            try:
                obj = json.loads(m.group(1).strip())
            except Exception:
                continue
            graph = obj.get("@graph") if isinstance(obj, dict) else None
            if not graph and isinstance(obj, dict):
                graph = [obj]
            if not graph:
                continue
            for node in graph:
                if not isinstance(node, dict):
                    continue
                ntype = node.get("@type", "")
                if isinstance(ntype, list):
                    ntype = ntype[0] if ntype else ""
                if ntype == "WebPage" and web_node is None:
                    web_node = node
                elif ntype == "Residence" and res_node is None:
                    res_node = node
                elif ntype == "Product" and prod_node is None:
                    prod_node = node
            if res_node or prod_node:
                break
    except Exception:
        pass

    # ---- additionalProperty 取值 helper（value 可能是 str 或 {value:...}）----
    def _ap(node, name):
        if not node:
            return ""
        try:
            for p in node.get("additionalProperty", []) or []:
                if isinstance(p, dict) and p.get("name") == name:
                    v = p.get("value")
                    if isinstance(v, dict):
                        vv = v.get("value")
                        return str(vv).strip() if vv is not None else ""
                    if isinstance(v, str):
                        return v.strip()
                    return str(v).strip() if v is not None else ""
        except Exception:
            pass
        return ""

    def _first_float(s):
        m = re.search(r"-?\d+(?:\.\d+)?", s or "")
        return float(m.group(0)) if m else None

    def _first_int(s):
        m = re.search(r"-?\d+", s or "")
        return int(m.group(0)) if m else None

    # 競品仲介品牌（動態，來自 JSON-LD brand.name）→ 加進過濾表
    agent_brand = ""
    try:
        if prod_node:
            bn = prod_node.get("brand")
            if isinstance(bn, dict):
                agent_brand = (bn.get("name") or "").strip()
            elif isinstance(bn, str):
                agent_brand = bn.strip()
    except Exception:
        pass
    extra_brands = [agent_brand] if agent_brand else []

    # ---- community_display（社區/建案，洗掉品牌/業務/加盟店）----
    try:
        community = _ap(res_node, "community")
        result["community_display"] = _strip_brands(community, extra_brands)
    except Exception:
        pass

    # ---- price（總價，元 → 萬）----
    try:
        price_raw = None
        if prod_node:
            offers = prod_node.get("offers") or {}
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                price_raw = offers.get("price")
        if price_raw is not None and str(price_raw).strip() != "":
            result["price"] = int(round(float(str(price_raw).replace(",", "")) / 10000))
    except Exception:
        pass

    # ---- floor / floor_total（Product additionalProperty「樓層」= "4/6樓"）----
    try:
        floor_str = _ap(prod_node, "樓層") or _ap(res_node, "floorLevel")
        m = re.search(r"(-?\d+)\s*/\s*(-?\d+)", floor_str)
        if not m:
            m = re.search(r"(-?\d+)\s*/\s*(-?\d+)\s*樓", html_text)
        if m:
            result["floor"] = int(m.group(1))
            result["floor_total"] = int(m.group(2))
        else:
            fi = _first_int(floor_str)
            if fi is not None:
                result["floor"] = fi
    except Exception:
        pass

    # ---- area（權狀總坪 = floorSize / 建物坪數）----
    try:
        area = _first_float(_ap(res_node, "floorSize"))
        if area is None:
            m = re.search(r"建物坪數[：:]\s*([\d.]+)\s*坪", html_text)
            if m:
                area = float(m.group(1))
        if area is not None:
            result["area"] = area
    except Exception:
        pass

    # ---- main_area（主+附）----
    try:
        appu = 0.0
        m = re.search(r"附屬建物[：:]\s*([\d.]+)\s*坪", html_text)
        if m:
            appu = float(m.group(1))
        main_bldg = 0.0
        # 主建物：優先「主+陽」-「陽台」
        m1 = re.search(r"主[+＋]陽[：:]\s*([\d.]+)\s*坪", html_text)
        m2 = re.search(r"陽台[：:]\s*([\d.]+)\s*坪", html_text)
        if m1:
            bal = float(m2.group(1)) if m2 else 0.0
            main_bldg = round(float(m1.group(1)) - bal, 2)
        # 退：權狀 - 共用 - 附屬
        if main_bldg <= 0:
            mg = re.search(r"共用坪數[：:]\s*([\d.]+)\s*坪", html_text)
            if result["area"] and mg:
                main_bldg = round(result["area"] - float(mg.group(1)) - appu, 2)
        # 退：description「建坪X坪」
        if main_bldg <= 0:
            mg = re.search(r"建坪\s*([\d.]+)\s*坪", html_text)
            if mg:
                main_bldg = float(mg.group(1))
        if main_bldg > 0 or appu > 0:
            result["main_area"] = round(max(main_bldg, 0.0) + appu, 2)
    except Exception:
        pass

    # ---- age（屋齡年數；新成屋=0）----
    try:
        age = _first_float(_ap(res_node, "yearBuilt"))
        if age is None:
            age = _first_float(_ap(prod_node, "屋齡"))
        if age is None:
            m = re.search(r"屋齡[：:]?\s*([\d.]+)\s*年", html_text)
            if m:
                age = float(m.group(1))
        if age is not None:
            result["age"] = age
    except Exception:
        pass

    # ---- layout（X房X廳X衛）----
    try:
        def _n(x):
            v = _first_int(x)
            return v if v and v > 0 else 0
        r = _n(_ap(res_node, "numberOfRooms"))
        lv = _n(_ap(res_node, "numberOfLivingRoomTotal"))
        ba = _n(_ap(res_node, "numberOfBathRoomsTotal"))
        parts = ""
        if r:
            parts += "%d房" % r
        if lv:
            parts += "%d廳" % lv
        if ba:
            parts += "%d衛" % ba
        result["layout"] = parts
    except Exception:
        pass

    # ---- building_type（型態）----
    try:
        bt = _ap(res_node, "caseType")
        if not bt and prod_node:
            cat = prod_node.get("category", "") or ""
            if "|" in cat:
                bt = cat.split("|")[-1].strip()
        result["building_type"] = (bt or "").strip()
    except Exception:
        pass

    # ---- address（路段級；砍巷弄號門牌）----
    def _strip_addr(a):
        if not a:
            return ""
        a = _unescape(a).strip()
        a = re.split(r"\d+\s*巷", a)[0]
        a = re.split(r"\d+\s*弄", a)[0]
        a = re.split(r"\d+\s*號", a)[0]
        a = re.split(r"之\s*\d+", a)[0]
        a = re.sub(r"\d+\s*$", "", a)  # 去尾端門牌數字（段用中文數字，不受影響）
        return a.strip(" ,，、-")
    try:
        addr = ""
        if res_node:
            a = res_node.get("address") or {}
            if isinstance(a, dict):
                addr = a.get("streetAddress") or a.get("addressLocality") or ""
            elif isinstance(a, str):
                addr = a
        result["address"] = _strip_addr(addr)
    except Exception:
        pass

    # ---- parking / has_parking ----
    try:
        pk = _ap(res_node, "parking").strip()
        if pk:
            result["parking"] = _strip_brands(pk, extra_brands) or "無車位"
            result["has_parking"] = result["parking"] != "無車位"
        else:
            result["parking"] = "無車位"
            result["has_parking"] = False
    except Exception:
        pass

    # ---- cover_image / gallery ----
    try:
        images = []
        if prod_node:
            img = prod_node.get("image")
            if isinstance(img, list):
                images = [x for x in img if isinstance(x, str) and x.strip()]
            elif isinstance(img, str) and img.strip():
                images = [img.strip()]
        images = [_fix_url(x) for x in images]
        seen, uniq = set(), []
        for x in images:
            if x and x not in seen:
                seen.add(x)
                uniq.append(x)
        if not uniq:
            og_img = _fix_url(_meta("og:image"))
            if og_img:
                uniq = [og_img]
        if uniq:
            result["cover_image"] = uniq[0]
            result["gallery"] = uniq[:12]
    except Exception:
        pass

    # ---- og_title（供備援；管線切段、洗競品段落，仍保留物件描述段）----
    try:
        ogt = _meta("og:title")
        if ogt:
            brands_all = extra_brands + _BRANDS
            segs = re.split(r"\s*[|｜]\s*", _unescape(ogt))
            keep = []
            for s in segs:
                s = s.strip()
                if not s:
                    continue
                if any(b and b in s for b in brands_all):
                    continue
                if s in ("出售", "售屋", "買屋", "出租"):
                    continue
                keep.append(s)
            result["og_title"] = " | ".join(keep)
    except Exception:
        pass

    # ---- og_description（供備援；洗競品/電話）----
    try:
        result["og_description"] = _strip_brands(_meta("og:description"), extra_brands)
    except Exception:
        pass

    return result



# ==================== 東森房屋 ====================
def _p_et(url):
    result = {"community_display": "", "price": 0, "floor": 0, "floor_total": 0,
              "area": 0.0, "main_area": 0.0, "age": 0.0, "layout": "", "building_type": "",
              "address": "", "parking": "無車位", "has_parking": False,
              "cover_image": "", "gallery": [], "og_title": "", "og_description": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=20) as r:
            h = r.read().decode("utf-8", "replace")
    except Exception:
        return result

    def og(p):
        m = re.search(r'<meta property="%s" content="([^"]*)"' % re.escape(p), h)
        return m.group(1).strip() if m else ""

    def cell(label):
        m = re.search(re.escape(label) + r'[：:]?\s*</div>\s*<div class="d-table-cell"[^>]*>(.*?)</div>', h, re.S)
        if not m:
            return ""
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', m.group(1))).strip()

    ogt, ogd = og("og:title"), og("og:description")
    result["og_title"] = ogt.split(" - ")[0].strip()
    ma = re.search(r'』\s*(.+)$', ogd)
    addr = ma.group(1).strip() if ma else ""
    addr = re.split(r'\d+\s*(?:巷|弄|號|之)', addr)[0]
    result["address"] = re.sub(r'\d+\s*$', '', addr).strip("，,、 ")
    result["community_display"] = cell("社區") or result["og_title"]
    pm = re.search(r'([\d,]+)\s*萬', cell("總價"))
    result["price"] = int(pm.group(1).replace(",", "")) if pm else 0
    fm = re.match(r'(\d+)\D+(\d+)', cell("樓層"))
    if fm:
        result["floor"] = int(fm.group(1))
        result["floor_total"] = int(fm.group(2))
    am = re.search(r'([\d.]+)', cell("屋齡"))
    result["age"] = float(am.group(1)) if am else 0.0
    result["building_type"] = cell("類型") or cell("型態")
    for lab in ("建物總坪數", "建物坪數", "總坪數", "權狀坪數"):
        vm = re.search(r'([\d.]+)', cell(lab))
        if vm:
            result["area"] = float(vm.group(1))
            break
    lm = re.search(r'(\d+)\s*房\s*/?\s*(\d+)\s*廳\s*/?\s*(\d+)\s*衛', h)
    if lm:
        result["layout"] = "%s房%s廳%s衛" % (lm.group(1), lm.group(2), lm.group(3))
    pk = cell("車位")
    if pk and "無" not in pk:
        result["parking"] = pk
        result["has_parking"] = True
    imgs, seen = [], set()
    for u in re.findall(r'https://img\.etwarm\.com\.tw/[^\s"\'<>]+?\.jpg', h):
        base = u.split("?")[0]
        if base not in seen:
            seen.add(base)
            imgs.append(u)
    if imgs:
        result["cover_image"] = imgs[0]
        result["gallery"] = imgs[:12]
    return result


# ==================== 註冊表 + 統一包裝 ====================
# (regex, 品牌, parser)。regex 只到能唯一辨識的最短前綴 + id 型別。
_ADAPTERS = [
    (re.compile(r"https?://(?:www\.)?sinyi\.com\.tw/buy/house/[A-Za-z0-9]+"), "信義", _p_sinyi),
    (re.compile(r"https?://(?:www\.)?hbhousing\.com\.tw/[Dd]etail/?\?[^\s\"'<>]*sn=[A-Za-z0-9]+"), "住商", _p_hb),
    (re.compile(r"https?://(?:www\.)?twhg\.com\.tw/buy/[A-Za-z]{2}\d+"), "台灣房屋", _p_twhg),
    (re.compile(r"https?://sale\.591\.com\.tw/home/house/detail/\d+/\d+\.html"), "591", _p_h591),
    (re.compile(r"https?://(?:www\.)?etwarm\.com\.tw/houses/(?:buy|rent)/\d+(?:/\d+)?"), "東森", _p_et),
    (re.compile(r"https?://buy\.cthouse\.com\.tw/house/\d+\.html"), "中信", _p_ct),
    (re.compile(r"https?://(?:www\.)?century21\.com\.tw/buypage/\d+"), "21世紀", _p_c21),
    (re.compile(r"https?://(?:www\.)?pacific\.com\.tw/(?:m/Object/Detail|Object/ObjectDetail/?)\?[^\s\"'<>]*saleID=[A-Za-z0-9]+"), "太平洋", _p_pacific),
    (re.compile(r"https?://(?:www\.)?nra\.com\.tw/buying/bsearch_detail\.php\?[^\s\"'<>]*num=\d+"), "全國", _p_nra),
    (re.compile(r"https?://(?:www\.)?great-home\.com\.tw/[Dd]etail/?\?[^\s\"'<>]*sn=[A-Za-z0-9]+"), "大家", _p_gh),
    (re.compile(r"https?://(?:www|m)\.rakuya\.com\.tw/sell_item/info\?[^\s\"'<>]*ehid=[0-9a-f]{15}"), "樂屋", _p_rakuya),
    (re.compile(r"https?://buy\.housefun\.com\.tw/buy/house/\d+"), "好房", _p_housefun),
]

# 圖乾淨(無品牌浮水印)的競品 → 可用其圖；其餘一律不放競品圖
_CLEAN_IMG_BRANDS = {"樂屋"}

# 洗白:所有同業品牌字樣 + 門市/業務/電話 → 客戶頁絕不出現
_BRAND_TOKENS = [
    "信義房屋", "信義不動產", "信義", "SINYI", "Sinyi", "sinyi",
    "住商不動產", "住商", "大家房屋", "great-home",
    "台灣房屋", "twhg", "東森房屋", "東森", "中信房屋", "中信", "cthouse",
    "21世紀不動產", "21世紀", "century21", "太平洋房屋", "太平洋", "pacific",
    "全國不動產", "全國房屋", "好房網", "housefun", "樂屋網", "樂屋", "rakuya",
    "591售屋網", "591房屋交易網", "591房仲網", "591",
    "永慶", "有巢氏", "台慶", "永義", "yungching", "u-trust",
    # 入口網/平台通用尾綴
    "售屋網", "房仲網", "房屋交易網", "房產網", "不動產網",
]
_SHOP_WORDS = re.compile(r"[^\s，。｜|/、]*(?:加盟店|直營店|門市|不動產經紀|房屋仲介|經紀人|營業員|仲介)[^\s，。｜|/、]*")
_PHONE_RE = re.compile(r"(?:0\d{1,3}[-\s]?\d{5,8}(?:[-#轉分機]\d+)?|09\d{2}[-\s]?\d{3}[-\s]?\d{3})")


def _scrub(text):
    if not text:
        return ""
    t = str(text)
    for b in _BRAND_TOKENS:
        t = t.replace(b, "")
    t = _SHOP_WORDS.sub("", t)
    t = _PHONE_RE.sub("", t)
    t = re.sub(r'[│┃｜|/／·、,，\-–—]{2,}', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t)
    return t.strip(" ·-–—,，、|｜/")


def _scrub_addr(a):
    a = _scrub(a)
    a = re.split(r'\d+\s*(?:巷|弄|號|之)', a)[0]
    return re.sub(r'\d+\s*$', '', a).strip("，,、 ")


def _clean_name(s):
    """把 og:title 洗成乾淨的物件名(去型態前綴/地址前後段/出售樣板/品牌/入口網尾綴)。"""
    s = _scrub(s)
    s = re.sub(r'^[^,，｜|]{2,6}出售[,，]\s*', '', s)          # 去開頭「透天厝出售,」
    s = re.sub(r'^\s*臺?台?[一-鿿]{1,3}[縣市][一-鿿]{1,4}[區鄉鎮市][^,，｜|]{0,10}[,，]\s*', '', s)  # 去開頭地址段
    s = re.split(r'[｜|]', s)[0]                                # ｜後段通常是地址/品牌
    # 去尾段「- 591售屋網 / - XX房仲網 / - XX房屋出售 / - XX網」等平台/出售尾綴
    s = re.sub(r'\s*[-–—]\s*\d*\s*[一-鿿A-Za-z]{0,6}(?:售屋網|房仲網|房屋網|房屋交易網|房產網|不動產網|房屋出售|土地出售|店面出售|出售|網)\s*$', '', s)
    s = re.sub(r'(?:房屋出售|土地出售|店面出售|出售|買房|賣屋|物件詳情)\s*$', '', s)
    return s.strip(" -–—,，、｜|")


def match_external(url):
    for rx, brand, fn in _ADAPTERS:
        if rx.search(url):
            return brand, fn
    return None, None


def scan_external(text):
    """回傳 [(start_pos, url, brand)]，供 extract_refs 依位置合併去重。"""
    hits = []
    for rx, brand, fn in _ADAPTERS:
        for m in rx.finditer(text or ""):
            hits.append((m.start(), m.group(0), brand))
    return hits


def fetch_external(url):
    """統一包裝:呼叫對應 parser → 洗白 → 套照片政策 → 回統一 schema(對齊 card_html)。"""
    brand, fn = match_external(url)
    if not fn:
        return {"error": "no adapter for url"}
    try:
        d = fn(url) or {}
    except Exception as e:
        return {"error": "parse fail: %s" % e}
    if not d.get("community_display") and not d.get("og_title") and not d.get("price"):
        return {"error": "empty parse"}

    # 景泰要求:照片直接擷取顯示(私下給客戶看、他自己約看房)。文字層仍剝品牌/業務/電話。
    cover = d.get("cover_image") or ""
    gallery = [g for g in (d.get("gallery") or []) if g]

    has_specs = bool(d.get("price")) and bool(d.get("area")) and bool(d.get("layout"))
    import hashlib
    slug = "x" + hashlib.md5(url.encode("utf-8")).hexdigest()[:10]

    # 物件名:社區名 → 標題 → (都空)用「地區路段+型態+格局」組描述名,不要掉到無意義「精選物件」
    _name = _clean_name(d.get("community_display") or "") or _clean_name(d.get("og_title") or "")
    if not _name:
        _road = re.sub(r'^[^縣市]*[縣市]', '', _scrub_addr(d.get("address") or "")).strip()  # 去縣市前綴,留 區+路段
        _bt = _scrub(d.get("building_type") or "").strip()
        _lay = (d.get("layout") or "").strip()
        _name = " ".join(x for x in (_road, _bt, _lay) if x).strip() or "精選物件"

    return {
        "source": "external",
        "brand": brand,
        "lite": not has_specs,
        "slug": slug,
        "detail_url": None,             # 一律不外連競品站(客戶頁)
        "src_url": url,                  # 來源網址:只存 Notion 私人後台供日後重做,不進客戶頁
        "community_display": _name,
        "price": int(d.get("price") or 0),
        "floor": int(d.get("floor") or 0),
        "floor_total": int(d.get("floor_total") or 0),
        "area": float(d.get("area") or 0),
        "main_area": float(d.get("main_area") or 0),
        "age": float(d.get("age") or 0),
        "layout": (d.get("layout") or "").strip(),
        "building_type": _scrub(d.get("building_type") or ""),
        "address": _scrub_addr(d.get("address") or ""),
        "parking": (d.get("parking") or ""),
        "parking_area": "",
        "has_parking": d.get("has_parking"),
        "og_image": cover,
        "gallery": gallery[:12],
        "og_title": _clean_name(d.get("og_title") or "") or _clean_name(d.get("community_display") or ""),
        "og_description": "",
        "is_own_store": False,
        "store_name": brand,
        "vr_url": "", "video_url": "", "ai_video_url": "",
        "last_price": 0, "is_discount": False,
        "no_clean_photo": (not cover),   # card 用來決定放不放「洽景泰」佔位
    }
