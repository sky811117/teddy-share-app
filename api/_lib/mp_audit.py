# -*- coding: utf-8 -*-
"""整頁洩漏稽核（01 §8.5，命中就擋；fail-closed）。H 與 S\\api\\_lib\\ 一字不差。

兩道關：查詢台送出前跑 audit_props；share app 推 GitHub 前跑 audit_html（上游 repo 公開，推上去收不回來）。
分區：<!--z:links-->…<!--/z:links--> 是連結區；<!--z:contact-->…<!--/z:contact--> 是聯絡區（單筆頁 .foot、.bar 都要包）。
     其餘都是本文區；<title>、alt 算本文區，meta（og:title、og:description、description）比照本文區（A15）。
不掃：src、og:image、script／style 內容（TRACK_API、Maps embed）。
命中紀錄只放規則 id 與遮蔽後的片段，不存秘密原文。
⛔ 只准 import 標準函式庫、mp_lexicon、mp_scrub。
"""
import copy
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

try:
    from . import mp_lexicon as L
    from . import mp_scrub as SC
except ImportError:
    import mp_lexicon as L
    import mp_scrub as SC

MP_VERSION = '20261002.1'            # 與 mp_lexicon.MP_VERSION 同值


@dataclass
class Hit:
    rule: str                        # 'A1'..'A16'
    where: str                       # 例 'props[1].intro'、'html:title'、'html:meta[og:title]'
    snippet: str = ''                # 遮蔽後的片段（不含秘密原文）

    def to_dict(self):
        return {'rule': self.rule, 'where': self.where, 'snippet': self.snippet}


@dataclass
class AuditResult:
    ok: bool = True
    hits: list = field(default_factory=list)

    def to_dict(self):
        return {'ok': self.ok, 'hits': [h.to_dict() for h in self.hits]}


def _mask_snip(s, keep=2, width=14) -> str:
    """片段遮蔽：留前 2 個字，其餘文字數字換成 *。"""
    s = ' '.join(str(s or '').split())[:width]
    return s[:keep] + re.sub(r'[^\W_]', '*', s[keep:])


def describe(hit) -> str:
    """給內部看的白話：「第 2 筆的 intro：疑似電話（09**…）」。"""
    m = re.match(r'props\[(\d+)\]\.?(.*)', hit.where or '')
    where = ('第 %d 筆的 %s' % (int(m.group(1)) + 1, m.group(2) or '欄位')) if m else (hit.where or '')
    return '%s：%s（%s）' % (where, L.AUDIT_TEXT.get(hit.rule, hit.rule), hit.snippet)


# ───────────────────────── 稽核情境 ─────────────────────────
def _lic_no(s):
    m = re.search(r'字第\s*(\d+)\s*號', SC.skel(s or ''))
    return m.group(1) if m else None


def _url_key(u) -> str:
    u = (u or '').strip()
    if u.startswith('//'):
        u = 'https:' + u
    try:
        p = urlsplit(u)
    except ValueError:
        return u
    host = (p.hostname or '').lower()
    path = p.path.rstrip('/')
    return '%s://%s%s%s' % ((p.scheme or 'https').lower(), host, path, ('?' + p.query) if p.query else '')


def _host_ok(host, domains) -> bool:
    host = (host or '').lower().rstrip('.')
    return any(host == d or host.endswith('.' + d) for d in domains)


class _Ctx:
    def __init__(self, contact, allowed_urls, allow, secrets, entities):
        contact = contact or {}
        allow = allow or {}
        self.agent_name = (contact.get('agent_name') or '').strip()
        self.agent_license = (contact.get('agent_license') or '').strip()
        self.phone = SC._norm_phone(SC.skel(contact.get('phone') or ''))
        self.line = (contact.get('line') or '').strip().lstrip('@').lower()
        # share app 的 contact 可能帶 IG（DEFAULT_CONTACT 'ig'）；聯絡區允許登入者自己的 IG
        self.ig = (contact.get('ig') or '').strip().lstrip('@').lower()
        # 景泰本人頁：呼叫端說是（沒說就看姓名）而且聯絡區姓名真的是景泰，兩個都成立才算（從嚴）
        said = bool(allow.get('owner')) if 'owner' in allow else True
        self.owner = said and L.OWNER_NAME in self.agent_name
        self.names = [str(n).strip() for n in (allow.get('names') or ()) if n and str(n).strip()]
        broker_lic = (contact.get('broker_license') or L.DEFAULT_BROKER_LICENSE)
        self.lic_ok = {x for x in (_lic_no(self.agent_license), _lic_no(broker_lic)) if x}
        self.companies = [c for c in (list(L.DEFAULT_COMPANY_NAMES) + [contact.get('company') or '',
                                                                      contact.get('company_full') or '']) if c]
        own = {self.phone, self.line} - {''}
        self.own_ids = ({self.line, self.ig} | ({'nov__817', 'sky811117'} if self.owner else set())) - {''}
        self.secrets = {str(s).strip().lower() for s in (secrets or ()) if s and str(s).strip()} - own
        self.secret_phones = {s for s in self.secrets if s.isdigit()}
        self.secret_ids = self.secrets - self.secret_phones
        self.entities = SC._norm_entities(entities)
        self.allowed = {_url_key(u) for u in (allowed_urls or ())}


def _strip_companies(text, cx):
    """聯絡區／og:site_name：本店公司名（含簡寫）允許 → 先拿掉再查品牌與刊登店名。"""
    for c in list(cx.companies) + list(L.OWN_BRAND_WORDS):
        pat = r'\s*'.join(re.escape(ch) for ch in c if not ch.isspace())
        text = re.sub(pat, ' ', text)
    return text


# ───────────────────────── 文字檢查（A1–A13） ─────────────────────────
def _text_hits(text, where, cx, zone='body', rule_override=None):
    """zone：'body'（本文、title、alt、props 欄位）、'contact'、'links'（連結區非連結文字）、'site'（og:site_name）。"""
    hits = []
    if not text or not SC._has_content(text):
        return hits

    def add(rule, snip):
        if rule_override:
            hits.append(Hit(rule_override, where, rule + ':' + _mask_snip(snip)))
        else:
            hits.append(Hit(rule, where, _mask_snip(snip)))

    norm = SC._nfkc(text)
    # 第 3 份：數字之間的換行拿掉（號碼拆成好幾行、<br> 隔開的「0912<br>345<br>678」）
    sk = (SC.skel(text), SC.skel(text, keep_space=True), SC.skel_joined(text))
    contact = zone == 'contact'
    owner_phone = '0920118756'

    # A1 電話（聯絡區只准登入者的號碼）
    seen = set()
    for v in sk:
        for rx in (L.PHONE_RE, L.PHONE_LOOSE_RE):
            for m in rx.finditer(v):
                p = SC._norm_phone(m.group(0))
                if p in seen:
                    continue
                seen.add(p)
                if contact and p == cx.phone:
                    continue
                if not cx.owner and p == owner_phone:
                    add('A13', p)
                else:
                    add('A1', p)
    # A2 LINE（聯絡區允許「LINE」字樣，帳號要等於登入者）
    if contact:
        for m in L.LINE_ID_RE.finditer(norm):
            v = next((g for g in m.groups() if g), '').strip('._-').lower()
            if v and v not in cx.own_ids:
                add('A2', v)
    else:
        m = L.LINE_RE.search(norm)
        if m:
            add('A2', m.group(0))
    # A3 稱謂人名（payload 的 name／need 原值豁免；聯絡區是範本產生的，不查）
    if not contact:
        t3 = norm
        for n in cx.names:
            t3 = t3.replace(SC._nfkc(n), ' ')
        m = L.NAME_RE.search(t3)
        if m:
            add('A3', m.group(0))
    # A4 證號：只准在聯絡區，而且要等於登入者或經紀人的證號
    if contact:
        for m in L.LICENSE_NO_RE.finditer(norm):
            if _lic_no(m.group(0)) not in cx.lic_ok:
                add('A4', m.group(0))
    else:
        m = L.LICENSE_RE.search(norm)
        if m:
            add('A4', m.group(0))
    # A5 品牌／通用公司樣式（保護詞先遮；聯絡區與 og:site_name 允許本店公司名）
    t5 = _strip_companies(norm, cx) if zone in ('contact', 'site') else norm
    masked = SC._Masker().mask(t5)
    m = L.BRAND_RE.search(masked)
    if not m and zone in ('body', 'links'):                  # 「信 義 房 屋」「信義房．屋」（聯絡區是範本產生的，不攤平）
        m = L.BRAND_RE.search(SC.brand_flat(masked))
    if m:
        add('A5', m.group(0))
    # A6 網址、網域、@帳號（連結區以外；自家網域允許；聯絡區允許登入者 LINE、景泰本人的 IG）
    if zone != 'links':
        t6 = norm
        for d in L.OWN_DOMAINS:
            t6 = re.sub(r'(?i)(?:https?://)?' + re.escape(d) + r'[^\s<>"\']*', ' ', t6)
        if contact:
            t6 = L.AT_ACCOUNT_RE.sub(lambda mm: ' ' if mm.group(0)[1:].lower() in cx.own_ids else mm.group(0), t6)
            t6 = re.sub(r'(?i)(?:https?://)?(?:line\.me|lin\.ee)/[^\s]*', lambda mm: ' ' if cx.line and cx.line in
                        mm.group(0).lower() else mm.group(0), t6)
        m = (L.DOMAIN_RE.search(t6) or L.AT_ACCOUNT_RE.search(t6) or re.search(r'(?i)https?://|www\.', t6)
             or L.CASE_NO_RE.search(t6))
        if m:
            add('A6', m.group(0))
    # A7 委託字眼
    m = L.MD_RE.search(norm)
    if m:
        add('A7', m.group(0))
    # A8 門牌巷弄號（國道1號、1號出口不算）
    m = L.ADDR_DETAIL_RE.search(norm) or L.ADDR_DETAIL_RE.search(sk[1])
    if m:
        add('A8', m.group(0))
    # A9 未完工建設（未遮罩原文）
    m = L.UNBUILT_RE.search(norm)
    if m:
        add('A9', m.group(0))
    # A10 強誇大詞、估價
    m = L.STRONG_HYPE_RE.search(SC._Masker().mask(norm))
    if m:
        add('A10', m.group(0))
    # A11 洗白時抓到的秘密（聯絡區以外都不能出現）
    if not contact:
        for p in cx.secret_phones:
            if any(p in v for v in sk):
                add('A11', p)
                break
        low = norm.lower()
        for sid in cx.secret_ids:
            if re.search(r'(?<![a-z0-9._-])' + re.escape(sid) + r'(?![a-z0-9._-])', low):
                add('A11', sid)
                break
    # A12 刊登店名（連結區以外）
    if zone != 'links' and cx.entities:
        flat = SC._flat(t5)
        for e in cx.entities:
            if e in flat:
                add('A12', e)
                break
    # A13 同事頁不能出現景泰的資料（姓名、電話、LINE、IG）
    if not cx.owner and not any(h.rule == 'A13' or h.snippet.startswith('A13') for h in hits):
        low = norm.lower()
        for mk in L.OWNER_MARKERS:
            digits = mk.replace('-', '')
            if mk.lower() in low or (digits.isdigit() and any(digits in v for v in sk)):
                add('A13', mk)
                break
    return hits


# ───────────────────────── audit_props ─────────────────────────
_INTERNAL_RE = re.compile('|'.join(re.escape(x) for x in L.INTERNAL_MARKERS if x != 'cid')
                          + r'|(?<![A-Za-z0-9])cid(?![a-z])'
                          + r'|(?<![A-Za-z0-9])(?:591[rb]?|ycr?|rk[rb]?|hpr?|ext):[A-Za-z0-9]')
_INTERNAL_URL_RE = re.compile('|'.join(re.escape(x) for x in L.INTERNAL_MARKERS if x not in ('cid', '591:')))
_SKIP_TEXT_KEYS = ('slug', 'mode', 'mp_version')


def _walk(v, path):
    if isinstance(v, str):
        yield path, v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _walk(x, '%s.%s' % (path, k))
    elif isinstance(v, (list, tuple)):
        for j, x in enumerate(v):
            yield from _walk(x, '%s[%d]' % (path, j))


def _is_url(s) -> bool:
    return bool(re.match(r'(?i)\s*(?:https?:)?//', s or ''))


def audit_props(props, contact, path, *, allowed_urls=frozenset(), secrets=(), entities=(), allow=None) -> AuditResult:
    """查詢台送出前的稽核。path 'single'|'card'；A16 鍵名白名單；文字欄位跑 A1–A13；網址只准平台與自家網域。
    fail-closed：稽核本身出例外 → 當作沒過。"""
    try:
        return _audit_props(props, contact, path, allowed_urls, secrets, entities, allow)
    except Exception as e:                                   # noqa: BLE001（稽核壞掉一律擋）
        return AuditResult(False, [Hit('A16', 'props', '稽核失敗：%s' % type(e).__name__)])


def _audit_props(props, contact, path, allowed_urls, secrets, entities, allow) -> AuditResult:
    cx = _Ctx(contact, allowed_urls, allow, secrets, entities)
    hits = []
    keys_ok = set(L.SINGLE_KEYS) if path == 'single' else set(L.CARD_KEYS) | {'_card'}
    if path not in ('single', 'card') or not isinstance(props, list) or not props:
        return AuditResult(False, [Hit('A16', 'props', '格式不對')])
    for i, p in enumerate(props):
        base = 'props[%d]' % i
        if not isinstance(p, dict):
            hits.append(Hit('A16', base, '不是物件'))
            continue
        for k in p:
            if k not in keys_ok:
                hits.append(Hit('A16', '%s.%s' % (base, k), _mask_snip(k, keep=4)))
        if path == 'card' and p.get('_card') is not True:
            hits.append(Hit('A16', base + '._card', '缺 _card'))
        if path == 'single' and p.get('caseName') and p.get('title') and p['caseName'] != p['title']:
            hits.append(Hit('A16', base + '.caseName', 'caseName≠title'))
        for k, v in p.items():
            if k not in keys_ok or k == '_card':
                continue
            where0 = '%s.%s' % (base, k)
            if k == 'links':
                for j, ln in enumerate(v if isinstance(v, list) else [v]):
                    url = ln.get('url') if isinstance(ln, dict) else ln
                    if not isinstance(url, str) or _url_key(url) not in cx.allowed:
                        hits.append(Hit('A14', '%s[%d]' % (where0, j), _mask_snip(url, keep=12, width=40)))
                continue
            for where, s in _walk(v, where0):
                if _is_url(s):
                    # 網址：只准平台與自家網域；照片網址的亂碼可能剛好拼出「-cid」，只查不會撞到的內部字樣
                    m = _INTERNAL_URL_RE.search(s)
                    if m:
                        hits.append(Hit('A16', where, _mask_snip(m.group(0), keep=4)))
                        continue
                    try:
                        host = urlsplit(s.strip() if not s.strip().startswith('//') else 'https:' + s.strip()).hostname
                    except ValueError:
                        host = None
                    domains = L.CARD_URL_HOSTS + (L.VR_HOSTS if k == 'vr_url' else ())
                    if not _host_ok(host, domains):
                        hits.append(Hit('A6', where, _mask_snip(host or s, keep=6, width=30)))
                    continue
                m = _INTERNAL_RE.search(s)
                if m:
                    hits.append(Hit('A16', where, _mask_snip(m.group(0), keep=4)))
                    continue
                if k in _SKIP_TEXT_KEYS:
                    continue
                hits.extend(_text_hits(s, where, cx, 'body'))
    return AuditResult(not hits, hits)


# ───────────────────────── audit_html ─────────────────────────
_SKIP_TAGS = {'script', 'style', 'noscript', 'template', 'svg'}
_BLOCK_TAGS = {'p', 'div', 'br', 'li', 'ul', 'ol', 'section', 'article', 'header', 'footer', 'nav', 'main', 'aside',
               'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'td', 'th', 'table', 'tbody', 'thead', 'dd', 'dt', 'dl',
               'blockquote', 'figure', 'figcaption', 'button', 'form', 'label', 'hr', 'img', 'iframe', 'select',
               'option', 'textarea', 'input', 'details', 'summary'}
_META_BODY = ('og:title', 'og:description', 'description', 'twitter:title', 'twitter:description')
_ZONE_RE = re.compile(r'^\s*(/?)z:(links|contact)\s*$')


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.zone_err = []
        self.skip = 0
        self.in_title = False
        self.title = []
        self.texts = {'body': [], 'contact': [], 'links': []}
        self.metas = []
        self.alts = []
        self.anchors = []
        self.cur_a = None

    @property
    def zone(self):
        return self.stack[-1] if self.stack else 'body'

    def handle_comment(self, data):
        m = _ZONE_RE.match(data or '')
        if not m:
            return
        if m.group(1):
            if self.stack and self.stack[-1] == m.group(2):
                self.stack.pop()
            else:
                self.zone_err.append('多出 /z:' + m.group(2))
        else:
            if self.stack:
                self.zone_err.append('z:%s 包在 z:%s 裡' % (m.group(2), self.stack[-1]))
            self.stack.append(m.group(2))

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag in _SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == 'title':
            self.in_title = True
        elif tag == 'meta':
            name = (a.get('property') or a.get('name') or '').strip().lower()
            if name:
                self.metas.append((name, a.get('content') or ''))
        elif tag == 'img':
            if a.get('alt'):
                self.alts.append((self.zone, a['alt']))
        elif tag == 'a':
            self.cur_a = {'zone': self.zone, 'href': a.get('href'), 'rel': a.get('rel') or '', 'text': []}
        if tag in _BLOCK_TAGS:
            self.texts[self.zone].append('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag == 'a':
            self.handle_endtag('a')

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == 'title':
            self.in_title = False
        elif tag == 'a' and self.cur_a is not None:
            self.anchors.append(self.cur_a)
            self.cur_a = None
        if tag in _BLOCK_TAGS:
            self.texts[self.zone].append('\n')

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title.append(data)
            return
        self.texts[self.zone].append(data)
        if self.cur_a is not None:
            self.cur_a['text'].append(data)


def _contact_link_ok(href, host, cx) -> bool:
    low = href.lower()
    if _host_ok(host, ('line.me', 'lin.ee')):
        return bool(cx.line) and cx.line in low
    if _host_ok(host, ('instagram.com',)):
        ids = ({'nov__817'} if cx.owner else set()) | ({cx.ig} if cx.ig else set())
        path = urlsplit(href).path.strip('/').split('/')[0].lower() if '//' in href else ''
        return path in ids
    return False


def _href_hits(anchors, cx):
    hits = []
    for a in anchors:
        href = (a['href'] or '').strip()
        zone = a['zone']
        text = ' '.join(''.join(a['text']).split())
        if zone == 'links':
            if not href or _url_key(href) not in cx.allowed:
                hits.append(Hit('A14', 'html:links', _mask_snip(href, keep=12, width=40)))
            if not L.LINK_TEXT_RE.match(text):
                hits.append(Hit('A14', 'html:links', _mask_snip(text or '(空)', keep=4)))
            rel = set((a['rel'] or '').lower().split())
            if not set(L.LINK_REL_REQUIRED) <= rel:
                hits.append(Hit('A14', 'html:links', 'rel=' + (a['rel'] or '(空)')))
            continue
        if not href or href.startswith('#'):
            continue
        low = href.lower()
        if not cx.owner and any(mk.lower().replace('-', '') in SC.skel(low) for mk in L.OWNER_MARKERS):
            hits.append(Hit('A13', 'html:href', _mask_snip(href, keep=8, width=30)))
            continue
        if low.startswith('tel:'):
            p = SC._norm_phone(SC.skel(href[4:]))
            if not p:
                hits.append(Hit('A13' if zone == 'contact' else 'A1', 'html:href', 'tel 空白'))   # 聯絡電話沒填
            elif not (zone == 'contact' and p == cx.phone):
                hits.append(Hit('A1', 'html:href', _mask_snip(p)))
            continue
        if low.startswith(('mailto:', 'sms:', 'javascript:', 'data:')):
            hits.append(Hit('A6', 'html:href', _mask_snip(href, keep=8, width=30)))
            continue
        if re.match(r'(?i)(?:https?:)?//', href):
            try:
                host = urlsplit(href if not href.startswith('//') else 'https:' + href).hostname or ''
            except ValueError:
                host = ''
            if _host_ok(host, L.PAGE_LINK_HOSTS):
                continue
            if zone == 'contact' and _contact_link_ok(href, host, cx):
                continue
            if _url_key(href) in cx.allowed:
                hits.append(Hit('A14', 'html:href', '原始連結只能放連結區'))
            else:
                hits.append(Hit('A6', 'html:href', _mask_snip(host or href, keep=6, width=30)))
            continue
        if re.match(r'^[a-z][a-z0-9+.-]*:', low):
            hits.append(Hit('A6', 'html:href', _mask_snip(href, keep=8, width=30)))
        # 其他（相對路徑）＝同站，放行
    return hits


def audit_html(html, contact, allowed_urls, allow=None, *, secrets=(), entities=()) -> AuditResult:
    """share app 推 GitHub 前的整頁稽核。allow＝{'names': [payload name, need], 'owner': bool}。
    fail-closed：解析或稽核本身出例外 → 當作沒過。"""
    try:
        return _audit_html(html, contact, allowed_urls, allow, secrets, entities)
    except Exception as e:                                   # noqa: BLE001（稽核壞掉一律擋）
        return AuditResult(False, [Hit('A13', 'html', '稽核失敗：%s' % type(e).__name__)])


def _audit_html(html, contact, allowed_urls, allow, secrets, entities) -> AuditResult:
    cx = _Ctx(contact, allowed_urls, allow, secrets, entities)
    sc = _Scan()
    try:
        sc.feed(html or '')
        sc.close()
    except Exception as e:                                   # 解析壞掉 → 擋
        return AuditResult(False, [Hit('A13', 'html', '解析失敗：%s' % type(e).__name__)])
    hits = []
    if sc.zone_err or sc.stack:
        hits.append(Hit('A13', 'html:zones', '分區標記不完整'))
    body = ''.join(sc.texts['body'])
    contact_t = ''.join(sc.texts['contact'])
    links_t = ''.join(sc.texts['links'])
    for a in sc.anchors:                                     # 連結區的連結文字由 A14 管，這裡不重複查
        if a['zone'] == 'links':
            for piece in a['text']:
                links_t = links_t.replace(piece, ' ', 1)
    hits += _text_hits(body, 'html:body', cx, 'body')
    hits += _text_hits(''.join(sc.title), 'html:title', cx, 'body')
    for zone, alt in sc.alts:
        hits += _text_hits(alt, 'html:alt', cx, 'contact' if zone == 'contact' else 'body')
    hits += _text_hits(contact_t, 'html:contact', cx, 'contact')
    hits += _text_hits(links_t, 'html:links', cx, 'links')
    for name, content in sc.metas:
        if name in _META_BODY:
            hits += _text_hits(content, 'html:meta[%s]' % name, cx, 'body', rule_override='A15')
        elif name == 'og:site_name':
            hits += _text_hits(content, 'html:meta[og:site_name]', cx, 'site', rule_override='A15')
    hits += _href_hits(sc.anchors, cx)
    # A13 聯絡區要等於登入者聯絡資料，證號不能空白
    contact_hrefs = ' '.join((a['href'] or '') for a in sc.anchors if a['zone'] == 'contact').lower()
    csk = SC.skel(contact_t) + ' ' + SC.skel(contact_hrefs)
    if not contact_t.strip():
        hits.append(Hit('A13', 'html:contact', '沒有聯絡區'))
    else:
        lic = _lic_no(cx.agent_license)
        if lic:
            lic_in = lic in SC.skel(contact_t)
        else:
            lic_in = bool(cx.agent_license) and re.sub(r'\s', '', cx.agent_license) in re.sub(r'\s', '', contact_t)
        if not cx.agent_license or not lic_in:
            hits.append(Hit('A13', 'html:contact', '證號空白或不符'))
        if cx.agent_name and cx.agent_name not in SC._nfkc(contact_t):
            hits.append(Hit('A13', 'html:contact', '姓名不符'))
        if cx.phone and cx.phone not in csk:
            hits.append(Hit('A13', 'html:contact', '電話不符'))
        if cx.line and cx.line not in (SC._nfkc(contact_t).lower() + ' ' + contact_hrefs):
            hits.append(Hit('A13', 'html:contact', 'LINE 不符'))
    return AuditResult(not hits, hits)


# ───────────────────────── 降級（01 §8.5 三步） ─────────────────────────
_WHERE_RE = re.compile(r'^props\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*)')


def _fallback_title(p, path) -> bool:
    """第 2 步：社區名拿掉、標題改成「區＋路」版本。回這次有沒有改到東西。"""
    tkey = 'og_title' if path == 'card' else 'title'
    ckey = 'community_display' if path == 'card' else 'community'
    title = str(p.get(tkey) or '')
    comm = p.get(ckey)
    place = SC.road_only(p.get('address') or '') or ((p.get('district') or '') + (p.get('road') or ''))
    if comm and str(comm) in title:
        new = title.replace(str(comm), place)
    else:
        new = place or '物件'
    new = ' '.join(new.split()) or '物件'
    changed = new != title or bool(comm)
    p[tkey] = new
    if path == 'card':
        p['community_display'] = None
    else:
        p.pop('community', None)
        p.pop('subtitle', None)
        p['caseName'] = new
    return changed


def degrade(props, hits, path):
    """命中後的降級（01 §8.5）：回 (新 props, 第幾步)。
       1＝拿掉命中筆的 intro／subtitle／features 等自由文字；2＝換成結構化（區＋路）標題、拿掉社區名；
       3＝修不了，該擋（A16、網址、地址、聯絡區…）。呼叫端每降一步要重新稽核，最多 3 輪。"""
    props = copy.deepcopy(props)
    if not hits:
        return props, 0
    fields = {}
    for h in hits:
        m = _WHERE_RE.match(h.where or '')
        if not m or h.rule == 'A16' or int(m.group(1)) >= len(props):
            return props, 3
        fields.setdefault(int(m.group(1)), set()).add(m.group(2))
    hit_keys = set().union(*fields.values())
    free = set(L.FREE_TEXT_KEYS)
    if hit_keys & free:
        for i, ks in fields.items():
            if not ks & free:
                continue
            p = props[i]
            for k in L.FREE_TEXT_KEYS:
                if k in p:
                    if path == 'card' and k in L.CARD_KEYS:
                        p[k] = ''
                    else:
                        del p[k]
        return props, 1
    if hit_keys <= set(L.TITLE_KEYS):
        changed = False
        for i in fields:
            changed = _fallback_title(props[i], path) or changed
        return props, (2 if changed else 3)
    return props, 3
