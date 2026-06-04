"""急修 mrRVQUnd + kxC6jRQA — 已發給客戶，只改這兩頁"""
import os, sys, re, json, base64, subprocess, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
from index import fetch_full_batch, gen_html, DEFAULT_CONTACT  # noqa

TARGETS = ["mrRVQUnd", "kxC6jRQA"]

for sid in TARGETS:
    print(f"\n=== {sid} ===")
    page_url = f"https://sky811117.github.io/teddy-shares/{sid}/"
    html_old = urllib.request.urlopen(page_url, timeout=30).read().decode("utf-8")

    # client name 從 GitHub commit 歷史拿（最原始的 add: 訊息）
    commits = json.loads(urllib.request.urlopen(
        f"https://api.github.com/repos/sky811117/teddy-shares/commits?path={sid}/index.html&per_page=20",
        timeout=20
    ).read())
    client_name = ""
    for c in reversed(commits):
        nm = re.match(r"add:\s*(.+?)\s+\d+\s*戶\s*\(", c["commit"]["message"])
        if nm:
            client_name = nm.group(1).strip()
            break

    # need 從原 for-client 抓（API 已經幫加「找房需求：」前綴）
    fc = re.search(
        r'<div class="for-client">給\s*.+?\s*的專屬精選\s*·\s*(.+?)</div>',
        html_old, re.DOTALL
    )
    need = fc.group(1).strip() if fc else "找房需求"

    print(f"  client={client_name!r}")
    print(f"  need={need!r}")

    slugs, seen = [], set()
    for m in re.finditer(r"https://x\.ychouse\.tw/([A-Za-z0-9]+)", html_old):
        s = m.group(1)
        if s not in seen:
            seen.add(s)
            slugs.append(s)
    print(f"  slugs={len(slugs)}")

    properties = fetch_full_batch(slugs)
    print(f"  fetch 成功 {len(properties)}")
    if not properties:
        print("  ⚠️ 抓不到物件，跳過")
        continue

    new_html = gen_html({
        "name": client_name,
        "need": need,
        "share_id": sid,
        "contact": DEFAULT_CONTACT,
    }, properties)

    z = new_html.count("萬 / 主建坪")
    q = new_html.count("萬 / 權狀坪")
    print(f"  驗證: 主建坪={z} 權狀坪={q}")
    if z > 0 or q == 0:
        print("  ❌ gen_html 還是有問題，中止")
        continue

    api_path = f"repos/sky811117/teddy-shares/contents/{sid}/index.html"
    sha_json = subprocess.run(["gh", "api", api_path], capture_output=True, text=True, encoding="utf-8")
    sha = json.loads(sha_json.stdout).get("sha") if sha_json.returncode == 0 else None

    payload = {
        "message": f"急修: {sid} 主建坪→權狀坪 ({len(properties)} 戶)",
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
        print(f"  ❌ push 失敗: {result.stderr[:300]}")
        continue
    print(f"  ✅ pushed: {page_url}")
