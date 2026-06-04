"""Regen 所有「主建坪」label 的舊頁面 — Vercel 部署沒生效，先本地補產"""
import os, sys, re, json, base64, subprocess, urllib.request, time
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
from index import fetch_full_batch, gen_html, DEFAULT_CONTACT  # noqa

# 從 GitHub 列出 teddy-shares 所有 share 頁面，挑出含「主建坪」的
print("[1] 列出 teddy-shares 所有 share id")
tree = json.loads(urllib.request.urlopen(
    "https://api.github.com/repos/sky811117/teddy-shares/git/trees/main?recursive=1",
    timeout=30
).read())
share_ids = sorted({
    t["path"].split("/")[0]
    for t in tree["tree"]
    if "/" in t["path"] and t["path"].endswith("/index.html")
})
print(f"    共 {len(share_ids)} 個 share")

print("[2] 篩出含「主建坪」的舊頁面")
bad = []
for sid in share_ids:
    try:
        h = urllib.request.urlopen(
            f"https://sky811117.github.io/teddy-shares/{sid}/", timeout=20
        ).read().decode("utf-8", errors="ignore")
        nested_title = "戶整理 的專屬精選" in h or "戶整理 的 " in h
        if "萬 / 主建坪" in h or nested_title:
            bad.append(sid)
            why = "主建坪" if "萬 / 主建坪" in h else "title 巢狀"
            print(f"    ❌ {sid} ({why})")
    except Exception as e:
        print(f"    ! {sid}: {e}")
print(f"    需修 {len(bad)} 個")

if not bad:
    print("沒有需要修的，結束")
    sys.exit(0)

# 對每個 bad share 重產
for sid in bad:
    print(f"\n=== regen {sid} ===")
    page_url = f"https://sky811117.github.io/teddy-shares/{sid}/"
    html_old = urllib.request.urlopen(page_url, timeout=30).read().decode("utf-8")

    # 從 GitHub commit 歷史抓 client name（最初的 add: 訊息「add: <name> N 戶 (<sid>)」）
    commits_url = f"https://api.github.com/repos/sky811117/teddy-shares/commits?path={sid}/index.html&per_page=20"
    commits = json.loads(urllib.request.urlopen(commits_url, timeout=20).read())
    client_name = ""
    for c in reversed(commits):  # oldest first
        msg = c["commit"]["message"]
        nm = re.match(r"add:\s*(.+?)\s+\d+\s*戶\s*\(", msg)
        if nm:
            client_name = nm.group(1).strip()
            break
    # need 從現在的 for-client 抓（如果有合理的）
    fc = re.search(
        r'<div class="for-client">給\s*.+?\s*的專屬精選\s*·\s*(.+?)</div>',
        html_old, re.DOTALL
    )
    need = "找房需求"
    if fc:
        cand = fc.group(1).strip()
        # 過濾掉 nested 壞資料
        if cand.startswith("找房需求") and "的" not in cand:
            need = cand

    slugs, seen = [], set()
    for m in re.finditer(r"https://x\.ychouse\.tw/([A-Za-z0-9]+)", html_old):
        s = m.group(1)
        if s not in seen:
            seen.add(s)
            slugs.append(s)
    print(f"    client={client_name!r} slugs={len(slugs)}")

    properties = fetch_full_batch(slugs)
    print(f"    fetch 成功 {len(properties)}")
    if not properties:
        print("    ⚠️ 物件全部下架/抓不到，跳過此頁（保留舊版）")
        continue

    new_html = gen_html({
        "name": client_name,
        "need": need,
        "share_id": sid,
        "contact": DEFAULT_CONTACT,
    }, properties)

    if "萬 / 主建坪" in new_html or "萬 / 權狀坪" not in new_html:
        print("    ❌ 本地 gen_html 有問題，跳過")
        continue

    # PUT 到 GitHub
    api_path = f"repos/sky811117/teddy-shares/contents/{sid}/index.html"
    sha_json = subprocess.run(
        ["gh", "api", api_path], capture_output=True, text=True, encoding="utf-8"
    )
    sha = json.loads(sha_json.stdout).get("sha") if sha_json.returncode == 0 else None

    payload = {
        "message": f"regen: {sid} 主建坪→權狀坪 ({len(properties)} 戶)",
        "content": base64.b64encode(new_html.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha
    result = subprocess.run(
        ["gh", "api", "-X", "PUT", api_path, "--input", "-"],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"    ❌ push 失敗: {result.stderr[:300]}")
        continue
    print(f"    ✅ {page_url}")
    time.sleep(1)
