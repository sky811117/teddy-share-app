# -*- coding: utf-8 -*-
"""
原地重生「猜你喜歡」頁 IWLdMnhJ — URL 不變,只換新版內容(補滿5筆 + 案名 fallback)。

關鍵:這頁刻意不外連永慶/ycut,原始 anchor 短網址撈不到。
改從「既有頁面已渲染的規格 + 照片」把 anchor dict 重建回來,
餵給升級後的 recommend(short_url=None, anchor=重建dict) 重跑配貨。

用法:
  python regen_recommend_IWLdMnhJ.py          # 只生成本機 HTML + 印摘要 (不推線上)
  python regen_recommend_IWLdMnhJ.py --push    # 生成後覆寫推上 GitHub Pages (URL 不變)
"""
import os, sys, re, html, json, base64, subprocess, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api", "_lib"))
from _lib.yungching_recommend import recommend           # noqa
from _lib.recommend_render import render_recommend_page   # noqa
from index import DEFAULT_CONTACT                          # noqa

SHARE_ID = "IWLdMnhJ"
SRC_URL = f"https://sky811117.github.io/teddy-shares/{SHARE_ID}/"
PAGE_URL = f"https://teddy-website-blog.pages.dev/share/{SHARE_ID}/"
REPO = "sky811117/teddy-shares"


def _f(s):
    if s is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group()) if m else None


def _i(s):
    f = _f(s)
    return int(f) if f is not None else None


def parse_anchor_from_page(page_html):
    """從既有 IWLdMnhJ 頁面把 anchor dict 重建回來。"""
    # anchor 規格表在「同類型・其他選擇」分隔線之前
    # 注意: 要抓 body 裡的 <div class="section-header">,不是 <head> CSS 的 .section-header
    cut = page_html.find('<div class="section-header">')
    head = page_html[:cut] if cut > 0 else page_html

    rows = {}
    for k, v in re.findall(r'spec-k">([^<]*)</span><span class="spec-v">([^<]*)</span>', head):
        rows.setdefault(k.strip(), html.unescape(v).strip())

    a = {}
    a["price_wan"] = _i(rows.get("總價"))
    a["building_area_ping"] = _f(rows.get("權狀坪"))
    a["main_area_ping"] = _f(rows.get("主建坪"))
    a["land_area_ping"] = _f(rows.get("土地持分") or rows.get("土地坪"))

    layout = rows.get("格局", "")
    mr = re.search(r"(\d+)房", layout); a["room_count"] = (mr.group(1) + "房") if mr else ""
    mh = re.search(r"(\d+)廳", layout); a["hall_count"] = int(mh.group(1)) if mh else None
    mb = re.search(r"(\d+)衛", layout); a["bath_count"] = int(mb.group(1)) if mb else None

    fl = rows.get("樓層", ""); a["floor"] = fl.replace("F", "").strip() or None
    a["age_years"] = _f(rows.get("屋齡"))

    typ = rows.get("類型", "")
    parts = [p.strip() for p in re.split(r"[／/]", typ) if p.strip()]
    a["type"] = parts[0] if parts else ""
    a["use_code"] = parts[1] if len(parts) > 1 else ""

    sch = rows.get("學區", "")
    sp = [p.strip() for p in re.split(r"[／/]", sch) if p.strip()]
    a["pri_school"] = sp[0] if sp else None
    a["jun_school"] = sp[1] if len(sp) > 1 else None

    addr = rows.get("地址", "")
    a["address"] = addr
    mcity = re.match(r"([^市縣]+[市縣])", addr)
    city = mcity.group(1) if mcity else "台中市"
    rest = addr[len(city):] if mcity else addr
    mdist = re.match(r"([^區鄉鎮市]+?[區鄉鎮市])", rest)
    district = mdist.group(1) if mdist else ""
    street = rest[len(district):] if mdist else ""
    a["city"], a["district"] = city, district
    a["build_date"] = rows.get("建照") or None

    # 物件特色 (anchor sellpoint)
    msp = re.search(r'class="sellpoint"><b>物件特色</b><br>(.*?)</div>', head, re.DOTALL)
    if msp:
        txt = re.sub(r"<br\s*/?>", "\n", msp.group(1))
        txt = re.sub(r"<[^>]+>", "", txt)
        a["selling_point"] = html.unescape(txt).strip()
    else:
        a["selling_point"] = None

    # anchor 照片 (ycut 簽章圖,仍可載入) — 收 anchor 區段內所有 ycut v1/image
    # 注意: 封面在 cloudfps.ycut.com.tw、相簿可能在 www/別的子網域,要吃任何 *.ycut.com.tw
    imgs, seen = [], set()
    for u in re.findall(r'https://[\w.-]+\.ycut\.com\.tw/v1/image/\?key=[^"\')\s]+', head):
        u = html.unescape(u)
        if u not in seen:
            seen.add(u); imgs.append(u)
    a["image_urls"] = imgs
    a["image_url"] = imgs[0] if imgs else ""

    # 無社區名 → 用路段當標題 (路段可公開、隱私安全),走升級後 render 的 case_name fallback
    a["community_name"] = ""
    a["case_name"] = (district + street).strip() or None

    # 配貨 / 渲染還會讀到的欄位,沒有就 None,維持原樣
    for k in ("public_area_ping", "public_ratio", "balcony_ping", "rainproof_ping",
              "parking", "mg_fee", "anchor_id"):
        a.setdefault(k, None)
    return a


def extract_client_meta(page_html):
    """沿用原頁的 client_name / signature,不破壞追蹤分類。"""
    mc = re.search(r"var CLIENT_NAME=(\"[^\"]*\"|'[^']*');", page_html)
    client_name = json.loads(mc.group(1)) if mc and mc.group(1).startswith('"') else \
        (mc.group(1).strip("'") if mc else "")
    ms = re.search(r'class="footer-name">([^<]*)</div>', page_html)
    sig = (ms.group(1).strip() if ms else "")
    if sig.endswith(" 房仲"):
        sig = sig[:-3].strip()
    # 預設簽名(陳景泰)就不另外帶,讓 render 走預設
    signature = "" if sig in ("", "陳景泰") else sig
    return client_name, signature


def main():
    push = "--push" in sys.argv

    print(f"[1] 抓既有頁面 {SRC_URL}")
    req = urllib.request.Request(SRC_URL, headers={"User-Agent": "Mozilla/5.0"})
    page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")

    print("[2] 從頁面重建 anchor")
    anchor = parse_anchor_from_page(page)
    print(f"    {anchor['case_name']} | {anchor['price_wan']}萬 | {anchor['type']} | "
          f"{anchor['room_count']} | 主建{anchor['main_area_ping']}坪 | "
          f"{anchor['district']}({anchor['city']}) | 照片{len(anchor['image_urls'])}張")
    client_name, signature = extract_client_meta(page)
    print(f"    client_name={client_name!r} signature={signature!r}")

    print("[3] 跑新版配貨 (補滿到5 + 案名 fallback) — 會打永慶,稍等…")
    data = recommend(None, anchor=anchor)
    cheap, pricey = data.get("cheap", []), data.get("pricey", [])
    print(f"    便宜 {len(cheap)} + 貴 {len(pricey)} = {len(cheap)+len(pricey)} 筆")
    for c in cheap + pricey:
        nm = c.get("community_name") or c.get("title") or "?"
        print(f"      - {nm} | {c.get('price_wan')}萬 | {c.get('case_type')} | "
              f"{c.get('district','')}{c.get('street','')}")
    print("    --- 配貨 log ---")
    for w in data.get("warnings", []):
        print("      ", w)

    print("[4] 渲染 HTML (share_id 沿用,URL 不變)")
    html_out = render_recommend_page(data, DEFAULT_CONTACT, SHARE_ID, client_name, signature)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"regen_{SHARE_ID}_new.html")
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(html_out)
    ncards = html_out.count("cand-card cand-card--")
    leftovers = html_out.count("社區資料整理中")
    print(f"    寫出 {out_path} ({len(html_out)/1024:.1f}KB) | 候選卡 {ncards} 張 | "
          f"殘留『社區資料整理中』{leftovers} 處")

    if not push:
        print("\n✅ 本機生成完成,尚未推線上。確認沒問題後加 --push 覆寫上線(URL 不變)。")
        print(f"   本機預覽: file:///{out_path.replace(os.sep,'/')}")
        return

    print("[5] 覆寫推上 GitHub Pages …")
    api_path = f"repos/{REPO}/contents/{SHARE_ID}/index.html"
    sha_res = subprocess.run(["gh", "api", api_path], capture_output=True, text=True, encoding="utf-8")
    sha = json.loads(sha_res.stdout).get("sha") if sha_res.returncode == 0 else None
    payload = {
        "message": f"regen: {SHARE_ID} 補滿5筆 + 案名 fallback ({len(cheap)+len(pricey)} 筆)",
        "content": base64.b64encode(html_out.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha
    res = subprocess.run(["gh", "api", "-X", "PUT", api_path, "--input", "-"],
                         input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        print(f"❌ push 失敗(可能 AI 自我提權被擋): {res.stderr[:300]}")
        print("   → 請景泰本人手動跑一次 push,或我把 payload 交給你。")
        sys.exit(1)
    print(f"✅ 已推上線 {PAGE_URL} (約 30 秒邊快取後生效,URL 完全沒變)")


if __name__ == "__main__":
    main()
