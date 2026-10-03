# -*- coding: utf-8 -*-
"""客戶版洗白管線（01 §8.2 S0–S9、§8.4 標題／地址）。H 與 S\\api\\_lib\\ 一字不差。

⛔ 只准 import 標準函式庫與 mp_lexicon（S 端沒有 mp_config、mp_types）。
原則（04 §5.1）：
  · 以「行／句／條列段」為單位整個刪，句子中間不挖字（避免「商辦專業如春房屋歡迎配件」挖成「商配件」）；
    只有固定白名單詞（誇大詞 REPL、委託字眼、Emoji）可以詞級刪。
  · 未完工建設在「還沒遮罩的原文」上做子句級刪除（S2）；保護詞先遮罩再偵測聯絡個資（S3→S4）。
  · 要能重複執行：scrub_body(scrub_body(x).text) 的結果不變（內部跑到不再變為止）。
  · 每一刀都記進 removed；找到的電話、LINE id 正規化後放進 secrets（給稽核 A11）。
"""
import html as _html
import math
import re
import unicodedata
from dataclasses import dataclass, field

try:
    from . import mp_lexicon as L
except ImportError:
    import mp_lexicon as L

MP_VERSION = '20261002.1'            # 與 mp_lexicon.MP_VERSION 同值

S = L.SENTINEL
_PASSES_MAX = 4                      # 跑到結果不再變為止（保證冪等），最多幾輪
_NOT_CONTACT = ('unbuilt', 'rent')   # 子句級規則：不算聯絡個資（不觸發整行複檢、簽名區截尾）

# HTML → 純文字
_TAG_BLOCK_RE = re.compile(r'(?is)<(script|style|noscript|template)\b.*?</\1\s*>')
_COMMENT_RE = re.compile(r'(?s)<!--.*?-->')
_BR_RE = re.compile(r'(?i)<br\s*/?>|</(?:p|div|li|h[1-6]|tr|ul|ol|section|article|table|blockquote|dd|dt)\s*>'
                    r'|<(?:p|li|div|tr)(?:\s[^<>]*)?>')
_TAG_RE = re.compile(r'</?[A-Za-z][A-Za-z0-9:-]*(?:\s[^<>]*)?/?>')
_BRK = '\x00'                                    # 標籤換行的暫存記號（相鄰的併成一個換行）
_BRK_RUN_RE = re.compile(_BRK + r'(?:\s*' + _BRK + r')*')
_ZW_RE = re.compile('[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u180e\xad\ufe0e\ufe0f\u20e3]')
_PUA_RE = re.compile('[\ue000-\uf8ff]')
_CONTENT_RE = re.compile('[^\\W_]|[\ue000-\uf8ff]')          # 任何文字或數字（含全形）＋遮罩字元
# S9 孤兒分隔符
_LEAD_SEP_RE = re.compile(r'^[\s、，,；;：:｜|/／·・．\-－—~～。！？!?]+')     # 行首孤兒（含刪詞後留下的「！」）
_TAIL_SEP_RE = re.compile(r'[\s、，,；;：:｜|/／·・\-－—~～]+$')
_DUP_SEP_RE = re.compile(r'([、，,；;｜|/／·])(?:\s*[、，,；;｜|/／·])+')
_SEP_BEFORE_END_RE = re.compile(r'[、，,；;｜|]+\s*([。！？])')
_EMPTY_BRACKET_RE = re.compile(r'【\s*】|「\s*」|（\s*）|\(\s*\)|《\s*》|〈\s*〉|『\s*』|\[\s*\]')
_SPACES_RE = re.compile(r'[ \t]{2,}')
_SENT_IN_UNIT_RE = re.compile(r'(?<=[。！？；｜])')
_BULLET_DIGIT_RE = re.compile(r'(?<=\d)\s*[｜|/／●★◆✔]\s*(?=\d)')   # 整行複檢：條列符號拆開的號碼
# clean_sep（ad_law_cleaner 原樣）
_SEP_CLASS = '[' + L.SEP + ']'
_CS_LEFT_RE = re.compile(_SEP_CLASS + r'\s*' + S)
_CS_RIGHT_RE = re.compile(S + r'\s*' + _SEP_CLASS)
_CS_MARK_RE = re.compile(r'([' + L.MARK + r']\s*){2,}')


@dataclass
class ScrubResult:
    text: str = ''
    removed: list = field(default_factory=list)   # [(單位原文, rule_id)]；rule_id 白話見 mp_lexicon.RULE_TEXT
    secrets: set = field(default_factory=set)     # 正規化後的電話（純數字）與 LINE id（小寫）


# ───────────────────────── 文字原語 ─────────────────────────
def clean_text(s) -> str:
    """HTML→純文字、刪零寬／方向控制／FE0E／FE0F／20E3／私用區；\\xa0、全形空白→半形空白。不做 NFKC。"""
    if s is None:
        return ''
    t = str(s).replace(_BRK, '')
    if '<' in t:
        t = _TAG_BLOCK_RE.sub('', t)
        t = _COMMENT_RE.sub('', t)
        t = _BR_RE.sub(_BRK, t)
        t = _TAG_RE.sub('', t)
        t = _BRK_RUN_RE.sub('\n', t)                 # 「</p><p>」只算一個換行
    if '&' in t:
        t = _html.unescape(t)
    t = t.replace('\r\n', '\n').replace('\r', '\n')
    t = L.INVISIBLE_RE.sub('', t)
    t = t.replace('\xa0', ' ').replace('　', ' ').replace('\t', ' ')
    t = _SPACES_RE.sub(' ', t)
    t = '\n'.join(x.strip() for x in t.split('\n'))
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def _nfkc(s) -> str:
    return _ZW_RE.sub('', unicodedata.normalize('NFKC', s or ''))


def skel(s, keep_space: bool = False) -> str:
    """偵測用副本：NFKC、中文／大寫／keycap 數字→阿拉伯（含電話諧音 洞么拐勾、數字旁的 O）、
    刪數字之間的分隔符（不跨行；跨行另由 skel_joined／_cross_line_phones 處理）。
    keep_space=True 時數字之間的空白保留（「0912-345-678 24小時」這種後面黏數字的寫法要靠它）。"""
    t = _nfkc(s)
    t = ''.join(L.SKEL_DIGITS.get(ch, ch) for ch in t)
    t = L.SKEL_O_RE.sub('0', t)
    t = L.SKEL_O_RE.sub('0', t)                       # 「OO12…」連著兩個 O：第二輪才接得上
    rx = L.DIGIT_SEP_NOSPACE_RE if keep_space else L.DIGIT_SEP_RE
    return rx.sub('', t)


_JOIN_NL_RE = re.compile(r'(?<=\d)[ \t\-.()（）—_~、·－–‐−･．,，]*\n[\s\-.()（）—_~、·－–‐−･．,，]*(?=\d)')


def skel_joined(s) -> str:
    """skel 之後再把「數字之間的換行」拿掉：號碼拆成好幾行（「0912\\n345\\n678」）也接得起來。只給偵測用。"""
    return _JOIN_NL_RE.sub('', skel(s))


def _phones_in(v) -> set:
    out = set()
    for rx in (L.PHONE_RE, L.PHONE_LOOSE_RE):
        for m in rx.finditer(v or ''):
            p = _norm_phone(m.group(0))
            if len(p) >= 9:
                out.add(p[:10] if p.startswith('09') else p)
    return out


def _cross_line_phones(sk_lines):
    """一行一行的 skel → (拆成好幾行的電話所在的行號集合, 號碼集合)。
    從「行尾是數字」的那行開始，往下接「整行只有數字與分隔符」的行（最後一行可以只接行首的數字），
    接起來是電話、而且單一行本身不是電話，就算拆行電話。"""
    idx_hit, found = set(), set()
    n = len(sk_lines)
    for i in range(n - 1):
        m = L.LINE_TAIL_DIGITS_RE.search(sk_lines[i])
        if not m:
            continue
        buf = re.sub(r'\D', '', m.group(0))
        idx = [i]
        j = i + 1
        while j < n and len(buf) < 14:
            s = sk_lines[j]
            if L.LINE_ONLY_DIGITS_RE.match(s):
                buf += re.sub(r'\D', '', s)
                idx.append(j)
                j += 1
                if _phones_in(buf):
                    break
                continue
            h = L.LINE_HEAD_DIGITS_RE.match(s)
            if h:
                buf += re.sub(r'\D', '', h.group(0))
                idx.append(j)
            break
        if len(idx) < 2:
            continue
        ph = _phones_in(buf)
        if ph and not any(_phones_in(sk_lines[k]) for k in idx):
            idx_hit.update(idx)
            found |= ph
    return idx_hit, found


_CN_UNIT = {'十': 10, '拾': 10, '百': 100, '佰': 100, '千': 1000, '仟': 1000}
_CN_TENS = {'廿': '二十', '卅': '三十', '卌': '四十'}


def cn2int(s):
    """中文／全形／混寫數字 → int；看不懂回 None。「八九０」→890、「十八」→18、「一百零五」→105。"""
    if s is None or isinstance(s, bool):
        return None
    if isinstance(s, int):
        return s
    t = unicodedata.normalize('NFKC', str(s)).strip().replace('兩', '二')
    for k, v in _CN_TENS.items():                    # 廿＝20、卅＝30、卌＝40
        t = t.replace(k, v)
    if not t:
        return None
    if re.fullmatch(r'[0-9]+', t):
        return int(t)
    if not any(ch in _CN_UNIT or ch in '萬' for ch in t):
        out = []
        for ch in t:
            if ch in '0123456789':
                out.append(ch)
            elif ch in L.CN_DIGITS:
                out.append(L.CN_DIGITS[ch])
            else:
                return None
        return int(''.join(out)) if out else None
    total = section = num = 0
    for ch in t:
        if ch in '0123456789':
            num = num * 10 + int(ch)
        elif ch in L.CN_DIGITS:
            num = int(L.CN_DIGITS[ch])
        elif ch in _CN_UNIT:
            section += (num or 1) * _CN_UNIT[ch]
            num = 0
        elif ch == '萬':
            total += (section + num) * 10000
            section = num = 0
        else:
            return None
    return total + section + num


def _has_content(s) -> bool:
    return bool(_CONTENT_RE.search(s or ''))


# ───────────────────────── 詞級：誇大詞（ad_law_cleaner 移植） ─────────────────────────
def apply_repl(text):
    """REPL_PRE＋REPL 逐條套用；回 (新字串, 被換掉的原文清單)。刪除的地方先放哨兵，給 clean_sep 收分隔符。"""
    removed = []
    for rx, rep in L.REPL_PRE_COMPILED + L.REPL_COMPILED:
        def _f(m, _rep=rep):
            removed.append(m.group(0))
            return m.expand(_rep)
        text = rx.sub(_f, text)
    return text, removed


def clean_sep(text):
    """吃掉哨兵左右多餘的分隔符、收斂重複分隔符、清空括號（ad_law_cleaner 原樣）。"""
    for _ in range(6):
        text = _CS_LEFT_RE.sub(S, text)
        text = _CS_RIGHT_RE.sub(S, text)
    text = text.replace(S, '')
    text = re.sub(r'[｜\|]{2,}', '｜', text)
    text = re.sub(r'、{2,}', '、', text)
    text = re.sub(r'，{2,}', '，', text)
    text = re.sub(r'｜、|、｜', '｜', text)
    text = re.sub(r'，、|、，', '，', text)
    text = _CS_MARK_RE.sub(lambda m: m.group(0)[0] + ' ', text)
    text = _EMPTY_BRACKET_RE.sub('', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text


def strip_edges(s):
    """只清頭尾的分隔符／emoji／空白；保留！？等句末標點（ad_law_cleaner 原樣）。"""
    return s.strip(' \t　' + L.SEP + L.MARK)


_S_BEFORE_END_RE = re.compile(S + r'+(?=\s*[。！？；])')
_S_WHOLE_CLAUSE_RE = re.compile(r'([，,、；;｜|])\s*' + S + r'+\s*[，,、；;｜|]')


def _clean_sep_body(text):
    """內文版 clean_sep：句末標點前的哨兵直接拿掉（「格局完美。」→「格局。」不吃掉句號）；
    整個子句都被刪掉時留一個分隔符（「方正，增值潛力強，近公園」→「方正，近公園」，不黏成一句）。其餘同 clean_sep。"""
    text = _S_BEFORE_END_RE.sub('', text)
    text = _S_WHOLE_CLAUSE_RE.sub(r'\1', text)
    return clean_sep(text)


def _word_level(text, removed):
    """S7＋S8 詞級刪除：委託字眼先（「專任委託」整個吃掉，不留「委託」），再誇大詞。回新字串。"""
    md = L.MD_RE.findall(text)
    if md:
        removed.extend((w, 'md') for w in md)
        text = L.MD_RE.sub(S, text)
    text, words = apply_repl(text)
    if words:
        removed.extend((w, 'repl') for w in words)
    if md or words:
        text = _clean_sep_body(text)
    return text


# ───────────────────────── S3 保護詞遮罩 ─────────────────────────
class _Masker:
    """保護詞換成 U+E000 起的私用區字元；同一個詞用同一個字元，最後一次還原。"""

    def __init__(self):
        self.table = {}
        self.rev = {}

    def mask(self, text):
        def rep(m):
            seg = m.group(0)
            after = m.string[m.end():m.end() + L.MASK_LOOKAHEAD]
            if L.MASK_LOOKAHEAD_SEG_RE.search(seg) and L.MASK_LOOKAHEAD_RE.search(after):
                return seg                       # 「綠線延伸」「信義路延伸段」不遮
            if _mask_blocked(seg):
                return seg                       # 遮罩範圍內有品牌／房屋字樣不遮
            ch = self.rev.get(seg)
            if ch is None:
                ch = chr(L.MASK_BASE + len(self.table))
                self.table[ch] = seg
                self.rev[seg] = ch
            return ch
        return L.PROTECT_RE.sub(rep, text or '')

    def restore(self, text):
        return _PUA_RE.sub(lambda m: self.table.get(m.group(0), ''), text or '')


def _mask_blocked(seg) -> bool:
    s = L.MASK_INTRINSIC_RE.sub('', seg)
    return bool(L.AGENCY_WORD_RE.search(s) or L.BRAND_RE.search(s))


# ───────────────────────── 偵測（S2／S4 共用） ─────────────────────────
def _unbuilt_hit(text) -> bool:
    return bool(L.UNBUILT_RE.search(text or ''))


def _detect(masked, entities=()):
    """S4：在遮罩後的單位上找聯絡個資／品牌／話術；回第一個命中的 rule_id 或 None。"""
    if not masked or not _has_content(masked):
        return None
    norm = _nfkc(masked)
    sk = None
    for rule, rx, where in L.DETECT_TABLES:
        if where == 'skel':
            if sk is None:
                sk = (skel(masked), skel(masked, keep_space=True))
            if rx.search(sk[0]) or rx.search(sk[1]):
                return rule
        elif rx.search(norm):
            return rule
        if rule == 'B' and L.BRAND_RE.search(brand_flat(norm)):       # 「信 義 房 屋」「信義房．屋」
            return 'B'
    if entities:
        flat = _flat(norm)
        for e in entities:
            if e and e in flat:
                return 'E'
    return None


def _flat(s) -> str:
    """實體比對用：NFKC、去空白、小寫。"""
    return re.sub(r'\s+', '', _nfkc(s)).lower()


def brand_flat(s) -> str:
    """品牌比對用副本：去掉同一行內的空白與 ．·・（換行保留，不把上下兩行接成一個詞）。"""
    return L.BRAND_FLAT_RE.sub('', s or '')


def _norm_entities(entities):
    out = []
    for e in entities or ():
        f = _flat(str(e or ''))
        if len(f) >= L.ENTITY_MIN_LEN and f not in out:
            out.append(f)
    return out


def _norm_phone(d) -> str:
    d = re.sub(r'\D', '', d or '')
    if d.startswith('886') and len(d) in (11, 12):            # +886 手機／市話 → 0 開頭
        d = '0' + d[3:]
    return d


def find_secrets(text) -> set:
    """整段文字裡的電話（純數字）與 LINE id（小寫），給稽核 A11 用。"""
    out = set()
    lines = (text or '').split('\n')
    sks = []
    for line in lines:
        sk0 = skel(line)
        sks.append(sk0)
        for v in (sk0, skel(line, keep_space=True), _BULLET_DIGIT_RE.sub('', sk0)):
            out |= _phones_in(v)
        for m in L.LINE_ID_RE.finditer(_nfkc(line)):
            v = next((g for g in m.groups() if g), '')
            v = v.strip('._-').lower()
            # 純數字的短字串不當帳號（「ID 123」的 123 拿去比對會誤擋「123坪」）
            if len(v) >= 3 and (re.search(r'[a-z]', v) or len(v) >= 6):
                out.add(v)
    out |= _cross_line_phones(sks)[1]                   # 拆成好幾行的號碼
    return out


# ───────────────────────── S1 切單位 ─────────────────────────
class _Unit:
    __slots__ = ('prefix', 'text', 'dead', 'rule')

    def __init__(self, prefix, text):
        self.prefix = prefix
        self.text = text
        self.dead = False
        self.rule = None


def _split_line(line):
    """一行 → 刪除單位。超過 60 字用。！？；切句；3 個以上條列分隔符時每段一個單位（分隔符留在 prefix）。"""
    sents = [line] if len(line) <= L.UNIT_SPLIT_LEN else [x for x in L.SENT_SPLIT_RE.split(line) if x]
    units = []
    for sent in sents:
        parts = L.BULLET_SEP_RE.split(sent)
        if (len(parts) - 1) // 2 >= 3:
            units.append(_Unit('', parts[0]))
            for i in range(1, len(parts) - 1, 2):
                units.append(_Unit(parts[i], parts[i + 1]))
        else:
            units.append(_Unit('', sent))
    return units


def _join(units) -> str:
    return ''.join(u.prefix + u.text for u in units if not u.dead and u.text != '')


# ───────────────────────── S0 591 租屋 AI 四段 ─────────────────────────
def _filter_ai591(text, removed):
    lines = text.split('\n')
    heads = []
    for i, ln in enumerate(lines):
        m = L.AI591_HEAD_RE.match(ln)
        if m:
            heads.append((i, m.group(1)))
    names = {n for _, n in heads}
    if len(names) < 2 or not any(n in L.AI591_DROP for n in names):
        return text
    head_at = dict(heads)
    keep, out, cut = True, [], []
    for i, ln in enumerate(lines):
        if i in head_at:
            if cut:
                removed.append(('\n'.join(cut), 'ai591'))
                cut = []
            keep = head_at[i] in L.AI591_KEEP
        (out if keep else cut).append(ln)
    if cut:
        removed.append(('\n'.join(cut), 'ai591'))
    return '\n'.join(out)


# ───────────────────────── S2 未完工建設（子句級） ─────────────────────────
def _drop_clauses(text, hit, rule, removed):
    """子句級刪除：先用。！？；｜切句，句子命中再用，、切子句，只刪命中的子句（prep_pipeline 的做法）。"""
    if not hit(text):
        return text
    out = []
    for sent in _SENT_IN_UNIT_RE.split(text):
        if not sent:
            continue
        if not hit(sent):
            out.append(sent)
            continue
        end = sent[-1] if sent[-1] in L.SENT_END_CHARS else ''
        clauses = [c for c in L.CLAUSE_SPLIT_RE.split(sent) if c]
        bad = [c for c in clauses if hit(c)]
        if not bad:                                  # 命中跨子句 → 整句刪
            removed.append((sent, rule))
            continue
        removed.extend((c, rule) for c in bad)
        kept = ''.join(c for c in clauses if not hit(c)).rstrip('，,、 ')
        if kept and _has_content(kept):
            if end and not kept.endswith(end):
                kept += end
            if hit(kept):                            # 拼回來又湊成命中 → 整句刪
                removed.append((kept, rule))
                continue
            out.append(kept)
    return ''.join(out)


def _drop_unbuilt(text, removed):
    return _drop_clauses(text, _unbuilt_hit, 'unbuilt', removed)


def _rent_hit(text) -> bool:
    """租屋內文（02 §8.3）：國籍／族群／身分限制、性別限制（性別改由 parser 抽成結構化標籤，原句不留）。"""
    t = _nfkc(text)
    return bool(L.RENT_DISCRIM_RE.search(t) or L.RENT_GENDER_RE.search(t))


def _drop_rent(text, removed):
    return _drop_clauses(text, _rent_hit, 'rent', removed)


# ───────────────────────── S6 地址只留到路 ─────────────────────────
def _road_cut(text) -> str:
    return L.ROAD_DETAIL_RE.sub(r'\1', text or '')


def road_only(addr) -> str:
    """地址只留到路段：「台中市太平區光興路八九０南巷3弄6號」→「台中市太平區光興路」。"""
    t = ' '.join(clean_text(addr).split())
    t = L.VILLAGE_RE.sub('', t)
    m = L.ROAD_HEAD_RE.match(t)
    if m:
        return m.group(1).strip(' ，,、-')
    t = re.split(r'[0-9０-９一二三四五六七八九十廿卅卌百〇零○]+\s*[東西南北]?\s*(?:巷|弄|衖|號|樓|室)', t)[0]
    return t.strip(' ，,、-')


# ───────────────────────── S9 收尾 ─────────────────────────
def _tidy_line(line) -> str:
    t = _SPACES_RE.sub(' ', line.strip())
    t = _EMPTY_BRACKET_RE.sub('', t)
    t = _DUP_SEP_RE.sub(r'\1', t)
    t = _SEP_BEFORE_END_RE.sub(r'\1', t)
    t = _LEAD_SEP_RE.sub('', t)
    t = _TAIL_SEP_RE.sub('', t)
    return t.strip()


def _limit(lines, removed):
    if len(lines) > L.BODY_OUT_MAX_LINES:
        removed.append(('\n'.join(lines[L.BODY_OUT_MAX_LINES:]), 'trunc'))
        lines = lines[:L.BODY_OUT_MAX_LINES]
    out, total = [], 0
    for i, ln in enumerate(lines):
        add = len(ln) + (1 if out else 0)
        if total + add <= L.BODY_OUT_MAX_CHARS:
            out.append(ln)
            total += add
            continue
        budget = L.BODY_OUT_MAX_CHARS - total - (1 if out else 0)
        head = ''
        if budget > 0:
            cut = ln[:budget]
            k = max(cut.rfind(c) for c in L.SENT_END_CHARS)
            if k >= 0 and _has_content(cut[:k + 1]):
                head = cut[:k + 1]
                out.append(head)
        rest = ln[len(head):]
        removed.append(('\n'.join([rest] + lines[i + 1:]), 'trunc'))
        break
    return out


# ───────────────────────── 管線 ─────────────────────────
_SHORT_NAME_RE = re.compile(r'^[一-鿿]{2,4}$')


def _short_name_line(units, masker) -> bool:
    """這一行是不是單獨一個 2–4 個中文字、沒有標點、常見姓或 阿／小 開頭的短行（簽名姓名的樣子）。"""
    live = [u for u in units if u.text.strip()]
    if len(live) != 1 or live[0].dead:
        return False
    t = _nfkc(masker.restore(live[0].text)).strip()
    return bool(_SHORT_NAME_RE.match(t)) and (t[0] in L.SURNAMES or t[0] in '阿小')


def _pass(text, src, deal, ctx, res):
    removed = res.removed
    entities = _norm_entities((ctx or {}).get('entities'))
    t = text or ''
    # S0 截斷 → clean_text → 591 租屋 AI 文案只留兩段
    if len(t) > L.BODY_IN_MAX:
        removed.append((t[L.BODY_IN_MAX:], 'trunc'))
        t = t[:L.BODY_IN_MAX]
    t = clean_text(t)
    res.secrets |= find_secrets(t)
    if (str(src or '').startswith('591') or (ctx or {}).get('ai_text_591')):
        t = _filter_ai591(t, removed)

    rent = str(deal or '') == 'rent'
    masker = _Masker()
    lines = []
    for raw in t.split('\n'):
        if not raw.strip():
            continue
        units = _split_line(raw)                                   # S1
        for u in units:
            if not u.text.strip():
                continue
            new = _drop_unbuilt(u.text, removed)                   # S2（未遮罩原文）
            if not _has_content(new):
                u.dead, u.rule = True, 'unbuilt'
                continue
            if rent:                                               # 租屋：歧視／性別限制（子句級）
                new = _drop_rent(new, removed)
                if not _has_content(new):
                    u.dead, u.rule = True, 'rent'
                    continue
            cut = _road_cut(new)                                   # S6（未遮罩原文，路名才認得出來）
            if cut != new:
                removed.append((new, 'addr'))
            u.text = masker.mask(cut)                              # S3
        for u in units:                                            # S4
            if u.dead or not u.text.strip():
                continue
            rule = _detect(u.text, entities)
            if rule:
                u.dead, u.rule = True, rule
        if not any(u.rule and u.rule not in _NOT_CONTACT for u in units):
            whole = _join(units)
            sk0 = skel(whole)
            sk = (sk0, skel(whole, keep_space=True), _BULLET_DIGIT_RE.sub('', sk0))
            if any(rx.search(v) for rx in (L.PHONE_RE, L.PHONE_LOOSE_RE) for v in sk) \
                    or L.LINE_RE.search(_nfkc(whole)):            # 號碼被條列分隔符拆開 → 整行刪
                for u in units:
                    if not u.dead and u.text.strip():
                        u.dead, u.rule = True, 'P'
        lines.append(units)

    # S4 跨行複檢：號碼拆成好幾行（「0912\n345\n678」）→ 那幾行整行刪，號碼放進 secrets
    sk_lines = [skel(masker.restore(_join(units))) for units in lines]
    hit_idx, phones = _cross_line_phones(sk_lines)
    res.secrets |= phones
    for i in hit_idx:
        for u in lines[i]:
            if not u.dead and u.text.strip():
                u.dead, u.rule = True, 'P'
    # S4 簽名姓名：電話／LINE 那行的前一行或後一行只有 2–4 個中文字、沒標點、常見姓或 阿／小 開頭
    #   → 多半是簽名的姓名（「王小明\n0912…」），一起刪；「採光好」「近公園」這種不是姓開頭的留著
    for i, units in enumerate(lines):
        if not any(u.rule in ('P', 'L') for u in units):
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(lines) and _short_name_line(lines[j], masker):
                for u in lines[j]:
                    if not u.dead and u.text.strip():
                        u.dead, u.rule = True, 'N'

    # S5 簽名區截尾：最後 40% 內連續兩個單位被 S4 刪 → 從第一個起全部刪
    flat = [u for units in lines for u in units if u.text.strip()]
    n = len(flat)
    start = n - int(math.ceil(n * L.SIG_TAIL_RATIO))
    s4 = [bool(u.rule and u.rule not in _NOT_CONTACT) for u in flat]
    for i in range(max(start, 0), n - 1):
        if s4[i] and s4[i + 1]:
            for u in flat[i:]:
                if not u.dead:
                    u.dead, u.rule = True, 'sig'
            break
    for u in flat:
        if u.dead and u.rule not in _NOT_CONTACT:                 # 子句級的已經在 S2 記過
            removed.append((masker.restore(u.text), u.rule))

    # S7 誇大詞、S8 委託字眼（詞級；在遮罩後的文字上做，保護詞不會被動到；句末標點保留）
    for u in flat:
        if not u.dead:
            u.text = _word_level(u.text, removed)

    # 重組行 → 去 Emoji → S9 收尾
    out = []
    for units in lines:
        line = _join(units)
        em = L.EMOJI_RE.findall(line)
        if em:
            removed.append((''.join(em), 'emoji'))
            line = L.EMOJI_RE.sub(' ', line)
        line = _tidy_line(line)
        if _has_content(line):
            out.append(masker.restore(line))

    # 還原後再跑一次 S2、S4（命中的單位刪掉）、S6
    final = []
    for line in out:
        units = _split_line(line)
        for u in units:
            if not u.text.strip():
                continue
            if _unbuilt_hit(u.text) or _detect(_Masker().mask(u.text), entities) or (rent and _rent_hit(u.text)):
                u.dead = True
                removed.append((u.text, 'recheck'))
        line = _join(units)
        cut = _road_cut(line)
        if cut != line:
            removed.append((line, 'addr'))
        line = _tidy_line(cut)
        if _has_content(line):
            final.append(line)
    return '\n'.join(_limit(final, removed))


def scrub_body(text, src: str, deal: str = 'sale', ctx: dict = None) -> ScrubResult:
    """客戶版內文洗白（S0–S9）。deal='rent' 時另刪國籍／族群／身分與性別限制的子句（rule 'rent'）。ctx 可帶：
         'entities'：刊登店名／公司（≥3 字）清單，出現就整個單位刪（rule 'E'）
         'ai_text_591'：True＝不管 src 都套 591 AI 四段過濾
    冪等：內部重跑到結果不再改變（最多 _PASSES_MAX 輪）。"""
    res = ScrubResult()
    cur = '' if text is None else str(text)
    for _ in range(_PASSES_MAX):
        nxt = _pass(cur, src, deal, ctx, res)
        if nxt == cur:
            break
        cur = nxt
    res.text = cur
    return res


# ───────────────────────── 短欄位、標題 ─────────────────────────
def _dirty(t, strict: bool) -> bool:
    """骨架檢查：命中就不用（社區名、標籤、副標）。strict＝副標（品牌簡稱、「店」「營業員」都算）。"""
    if not t:
        return True
    if _unbuilt_hit(t) or L.ADDR_DETAIL_RE.search(t) or L.ADDR_DETAIL_RE.search(skel(t)):
        return True
    m = _Masker().mask(t)
    if _detect(m):
        return True
    norm = _nfkc(m)
    if L.AGENCY_WORD_RE.search(norm) or L.MD_RE.search(norm) or L.STRONG_HYPE_RE.search(norm):
        return True
    words = L.BRAND_SHORT if strict else L.BRAND_SHORT_SAFE
    if any(w in norm for w in words):
        return True
    if strict and L.SUB_DIRTY_RE.search(norm):
        return True
    return False


def _short_clean(s) -> str:
    t = ' '.join(_nfkc(clean_text(s)).split())
    t = L.EMOJI_RE.sub(' ', t)
    masker = _Masker()
    m = masker.mask(t)
    m = clean_sep(L.MD_RE.sub(S, m))
    m, _ = apply_repl(m)
    m = strip_edges(clean_sep(m))
    return ' '.join(masker.restore(m).split())


def scrub_short(s):
    """短欄位（社區名、特色標籤、車位文字）：洗完還有內容且骨架乾淨才回字串，否則 None。"""
    if s is None:
        return None
    t = _short_clean(s)
    if not _has_content(t) or _dirty(t, strict=False):
        return None
    return t


def _num_text(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return ''
    if f <= 0:
        return ''
    s = '%.2f' % round(f, 2)
    return s.rstrip('0').rstrip('.')


def _int_or_none(x):
    try:
        return int(x) if x is not None and x != '' else None
    except (TypeError, ValueError):
        return None


def _floor_text(fl) -> str:
    fl = fl or {}
    lo, hi = _int_or_none(fl.get('lo')), _int_or_none(fl.get('hi'))
    if lo is None and hi is None:
        return ''
    if lo is None:
        lo = hi

    def one(v):
        return 'B%d' % -v if v < 0 else str(v)
    if hi is not None and hi != lo:
        return '%s-%s樓' % (one(lo), one(hi))
    return '%s樓' % one(lo)


def structured_title(f: dict) -> str:
    """客戶頁標題一律結構化（01 §8.4、02 §8.4 表）。不出現精選／嚴選；預售不顯示社區名。"""
    f = f or {}
    cat = f.get('cat') or 'other'
    kind = (f.get('building_kind') or '').strip()
    district = (f.get('district') or '').strip()
    road = road_only(f.get('road') or '') if f.get('road') else ''
    place = district + road
    comm = None if f.get('presale') else f.get('community')
    comm = scrub_short(comm) if comm else None
    fl = f.get('floor') or {}
    ftxt = _floor_text(fl)
    rooms = _int_or_none(f.get('rooms'))
    rtxt = '%d房' % rooms if rooms else ''
    areas = f.get('areas') or {}
    total = _int_or_none(fl.get('total')) or _int_or_none(fl.get('hi'))

    if cat == 'condo':
        parts = [comm, ftxt, rtxt] if comm else [place, kind or '大樓', ftxt, rtxt]
    elif cat == 'suite':
        parts = [comm, ftxt, '套房'] if comm else [place, '套房', ftxt]
    elif cat == 'house':
        parts = [place, '透天', ('%d層' % total) if total else '', rtxt]
    elif cat == 'rent_whole':
        parts = [comm or place, '整層出租', rtxt]
    elif cat in ('rent_suite', 'rent_share', 'rent_room'):
        parts = [place, L.BUILDING_KIND_TEXT[cat]]
    elif cat == 'shop':
        parts = [place, '店面', ftxt]
    elif cat == 'office':
        reg = _num_text(areas.get('reg'))
        parts = [comm or place, '辦公', (reg + '坪') if reg else '']
    elif cat == 'factory':
        land = _num_text(areas.get('land'))
        parts = [place, '廠房', ('地%s坪' % land) if land else '']
    elif cat == 'land':
        land = _num_text(areas.get('land'))
        sect = scrub_short(f.get('land_section')) if f.get('land_section') else None
        zone = scrub_short(f.get('land_zone')) if f.get('land_zone') else None
        parts = [district + (sect or road), '土地', (land + '坪') if land else '', zone]
    elif cat == 'parking':
        parts = [place, '車位']
    else:
        parts = [comm or place, kind or L.BUILDING_KIND_TEXT.get(cat, '')]
    title = ' '.join(p for p in parts if p)
    title = L.TITLE_FORBIDDEN_RE.sub('', title)
    title = ' '.join(title.split())
    return title or (district + '物件' if district else '物件')


def _subtitle(raw):
    """仲介原標題 → 副標；洗不乾淨或字數不對回 None（04 §5.5 步驟 1–8）。"""
    if not raw:
        return None
    t = ' '.join(_nfkc(clean_text(raw)).split())               # 1 NFKC
    t = L.EMOJI_RE.sub(' ', t).strip()                          # 2 去 Emoji
    t = strip_edges(clean_sep(L.MD_RE.sub(S, t)))               # 3 去委託字眼
    t = L.PREFIX_RE.sub('', t)                                  # 4 去開頭前綴
    t, _ = apply_repl(t)                                        # 5 誇大詞
    t = strip_edges(clean_sep(t))
    for rx in L.SUB_STRIP_RES:                                  # 6 去「XX精選好屋」「嚴選」；7 去「承辦XX！」
        t = rx.sub('', t)
    t = re.sub(r'[┃｜|/／◆]+', ' · ', t)
    t = re.sub(r'\s*·\s*', ' · ', t)
    t = ' '.join(t.split()).strip(' ·')
    t = strip_edges(t)
    if not _has_content(t) or _unbuilt_hit(t) or _dirty(t, strict=True):   # 8 骨架檢查
        return None
    if not (L.TITLE_SUB_MIN <= len(t) <= L.TITLE_SUB_MAX):
        return None
    return t


def client_title(f: dict):
    """(結構化標題, 副標或 None)。副標＝仲介原標題 title_raw 洗過還乾淨的才用。"""
    title = structured_title(f)
    sub = _subtitle((f or {}).get('title_raw'))
    if sub and sub.replace(' ', '') == title.replace(' ', ''):
        sub = None
    return title, sub
