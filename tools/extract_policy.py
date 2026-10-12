#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把源 PDF 抽取成「规范化字符流」与「分段草案」。

本脚本是整条流水线的机械底座：它负责把 5900 字的正文从 PDF 里搬出来，
并断言搬运过程没有丢字、没有乱码。它**不负责**内容的改写——改写发生在
src/policy.body.html（人工校订的真源）。

三个 flag 缺一不可，理由见 config 的 baseline_extract 与 README：
  -layout    保留物理版式（表格列位置）
  -eol unix  默认输出是 CRLF，不加则每行带 \\r，所有匹配失准
  -nopgbrk   默认插入 15 个分页符，不加会把跨页句子切两半且不报错

输出（全部落在 build/，该目录不入库）：
  policy.raw.txt       原始提取结果（原样保存，便于人工核对）
  policy.readable.txt  分段草案：空格已折叠，一段一行，供人工阅读
  policy.stream.txt    去空白字符流：校验与替换清单的比对基准
  extract_report.txt   指标报告与「分段存疑」清单
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# ——— 路径：一律由 __file__ 推导，避免依赖当前工作目录 ———
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "policy.json"
BUILD_DIR = ROOT / "build"

# ——— 分段启发式的常量。这些是「排版尺度」而非「业务参数」，故以命名常量集中于此 ———
# 判定「整行排满」的右边界阈值：实测正文满行的右边界落在 35~41 之间
FULL_RIGHT_EDGE_MIN = 35
# 居中标题的最小缩进：实测「温馨提示」缩进 33、「《CCCAT 犭犭猫》隐私政策」缩进 20
CENTERED_MIN_INDENT = 20
# 续行的缩进量。实测缩进 5 的行**全部**是续行（Word 悬挂缩进）
CONTINUATION_INDENT = 5
# 判定「前行勉强不到满行」的容差：落在这个区间里的边界会被标为可疑
BORDERLINE_SLACK = 4
# 句末标点：出现在行尾时，该行有可能是段落结尾
SENTENCE_END_CHARS = "。！？"
# 页码伪行的最大位数
PAGE_NUMBER_MAX_DIGITS = 2

# 判定「目录块」所需的最少连续章标题数
TOC_MIN_RUN = 3
# 中文数字，按章号先后排列
CN_DIGITS = "一二三四五六七八九十"
# 目录块之外、页面 <h1> 已承载的居中大标题
DOC_TITLE = "《CCCAT 犭犭猫》隐私政策"

# 段落起点的结构标记。命中即一定是新段落（且多为标题）
MARKER_PATTERNS = [
    r"^温馨提示$",
    r"^[一二三四五六七八九十]+、",       # 章标题
    r"^（[一二三四五六七八九十]+）",       # 小节，如「（一）我们如何收集您的个人信息」
    r"^\d+\.\s",                        # 编号条目，如「1. 完成注册」
    r"^\(\d+\)",                        # 括号条目，如「(1) 与我们履行法律法规规定的义务相关的；」
    r"^[⚫•·]",                         # 圆点列表项
    r"^(更新日期|生效日期|名称|地址|联系电话|联系邮箱)：",
    r"^《.+》隐私政策$",                  # 文中居中大标题
]

# CJK 系字符：用于「汉字之间的排版伪空格」判定
CJK_RANGES = [
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x4E00, 0x9FFF),   # CJK 统一表意文字（含游戏名里的 U+72AD 犭）
    (0xF900, 0xFAFF),   # CJK 兼容表意文字
    (0x3000, 0x303F),   # CJK 标点（《》【】、。，等）
    (0xFF00, 0xFFEF),   # 全角形式（：（）等）
]

# 空格折叠时必须保住原样的串（它们内部含**有意义**的空格）
PROTECTED_STRINGS = [
    "CCCAT 犭犭猫",
    "https://www.aboutcookies.org/",
    "未满 14 周岁",
    "已满 14 周岁",
    "2026 年",
]


def load_config() -> dict:
    """读取集中配置。所有魔法值都住在这里。"""
    with CONFIG_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


CONFIG = load_config()
# 宽度判据定不下来的段落边界，由 config 显式指定（对应 extract_report 的存疑清单）。
FORCE_BREAK_PREFIXES = CONFIG["segmentation"]["force_break_prefixes"]


def run_pdftotext(pdf: Path, flags: list) -> bytes:
    """调用 pdftotext，返回原始字节。交由调用方按需解码。"""
    result = subprocess.run(
        ["pdftotext"] + flags + [str(pdf), "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"pdftotext 失败（退出码 {result.returncode}）：{result.stderr.decode('utf-8', 'replace')}")
    return result.stdout


def assert_pdftotext_version(expected: str) -> None:
    """钉住 pdftotext 版本。

    Xpdf 4.00 与 Poppler 的 pdftotext 输出**不一样**（例如 -bbox 只有 Poppler 有），
    换机器跑出不同结果会让校验误报，所以版本不符就直接报错，而不是静默继续。
    """
    result = subprocess.run(["pdftotext", "-v"], capture_output=True)
    banner = (result.stdout + result.stderr).decode("utf-8", "replace")
    if expected not in banner:
        raise SystemExit(f"pdftotext 版本不符：期望含 {expected!r}，实际为 {banner.strip()[:120]!r}")


def is_cjk(ch: str) -> bool:
    """判断是否为 CJK 系字符。"""
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in CJK_RANGES)


def collapse_spaces(text: str) -> str:
    """折叠 -layout 产生的排版伪空格，同时保住有意义的空格。

    难点是两类空格方向相反：
      该删：`接受程 度 或 拒 绝 我 们 的 Cookies`——汉字之间的对齐填充
      该留：`CCCAT 犭犭猫`、`未满 14 周岁`、URL

    规则：删除「前后非空白字符同属 CJK 系」的空格。
    实现必须**单趟扫描字符数组**，不能只跑一遍 re.sub：
      re.sub 处理 `A B C` 时删掉第一个空格后，第二个空格的左邻才变成非空格，
      需要重新判定，多趟正则既啰嗦又容易漏。
    保护串用哨兵摘出来，不指望规则永远对。
    """
    # 第一步：把保护串换成不含空格的哨兵
    sentinels = {}
    for idx, protected in enumerate(PROTECTED_STRINGS):
        token = f"\x00P{idx}\x00"
        if protected in text:
            sentinels[token] = protected
            text = text.replace(protected, token)

    # 第二步：单趟扫描，删掉「CJK 空格 CJK」形态里的空格
    chars = list(text)
    out = []
    for i, ch in enumerate(chars):
        if ch == " ":
            prev = next((c for c in reversed(out) if not c.isspace()), None)
            nxt = next((c for c in chars[i + 1:] if not c.isspace()), None)
            if prev is not None and nxt is not None and is_cjk(prev) and is_cjk(nxt):
                continue  # 丢掉这个排版伪空格
        out.append(ch)
    text = "".join(out)

    # 第三步：还原保护串
    for token, protected in sentinels.items():
        text = text.replace(token, protected)
    return text


def strip_page_numbers(lines: list, expected: int) -> tuple:
    """剔除页码伪行，并断言剔掉的恰好是 1..expected。

    为什么不用缩进判据：实测页码通常是缩进 69，但**第 5 页的页码缩进只有 20**。
    按「缩进 >= 40」剔除会漏掉它，页面上会永久留下一个孤零零的 `5`。
    页码是逐页递增的，用顺序计数器既准确又自带校验。
    """
    kept, dropped = [], []
    pattern = re.compile(r"^\d{1,%d}$" % PAGE_NUMBER_MAX_DIGITS)
    for line in lines:
        stripped = line.strip()
        if pattern.match(stripped):
            dropped.append(int(stripped))
        else:
            kept.append(line)

    if len(dropped) != expected:
        raise SystemExit(f"页码伪行数不符：期望 {expected}，实际 {len(dropped)}（{dropped}）")
    if dropped != list(range(1, expected + 1)):
        raise SystemExit(f"页码序列不是 1..{expected} 严格递增，实际为 {dropped}——判据不安全，需人工核查")
    return kept, dropped


def join_lines(parts: list) -> str:
    """把段落的各行接成一整段，并**按需**补回空格。

    中文换行不需要在接缝处补空格（`收集、使` + `用、存储` → `收集、使用、存储`），
    但拉丁词被行尾切断时必须补（`Flash` + `Cookies` → `Flash Cookies`）。
    规则：接缝两侧都是 ASCII 字母/数字时补一个空格，其余情况直接相接。
    """
    out = ""
    for part in parts:
        if out and out[-1].isascii() and out[-1].isalnum() \
                and part[0].isascii() and part[0].isalnum():
            out += " "
        out += part
    return out


def right_edge(line: str) -> int:
    """行内容的右边界列号（含缩进）。满行判定用它而非内容长度，"""
    return len(line.rstrip())


def matches_marker(stripped: str) -> bool:
    """是否命中段落起点标记。"""
    return any(re.match(p, stripped) for p in MARKER_PATTERNS)


def segment_paragraphs(lines: list) -> tuple:
    """把内容行重组为段落。返回 (段落列表, 存疑边界列表)。

    这是全任务最容易出错的一步，我如实说明它的强度：
    本文件里段内换行与段间边界的**缩进都是 0**，没有空行区分，也没有几何信号可用
    （本机 pdftotext 是 Xpdf 4.00，不支持 Poppler 的 -bbox 逐行 y 坐标）。
    所以这是启发式，**不是确定性重建**：标记规则能覆盖约 8 成边界，
    剩下的靠「前行是否排满」猜测，猜不准的会进存疑清单，由人工在
    src/policy.body.html 里定稿。
    """
    content = [(i, l) for i, l in enumerate(lines) if l.strip()]
    paragraph_lines, uncertain = [], []
    current = None
    prev_line = None
    prev_was_centered = False

    for index, line in content:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        is_marker = matches_marker(stripped)
        is_centered = indent >= CENTERED_MIN_INDENT

        if current is None:
            starts_new = True
        elif is_marker or is_centered:
            starts_new = True
        elif any(stripped.startswith(p) for p in FORCE_BREAK_PREFIXES):
            # 宽度判据定不下来的边界，由 config 显式指定（见 extract_report 的存疑清单）。
            # 这些是「跟前一行排满程度无关、按语义必须另起一段」的位置。
            starts_new = True
        elif prev_was_centered:
            # 居中的行一定是独立标题，其下一行必定另起一段。
            # 这一条不能省：居中标题的**缩进会被算进右边界**（「温馨提示」
            # 缩进 33 + 4 字 = 37），宽度判据会把它当成排满的行，
            # 于是把紧随其后的正文并进标题里。
            starts_new = True
        else:
            # 前行排满 → 本行是续行；否则视为新段落。
            #
            # 这里**不需要**为「前行是标记行」开特例。我一度加过「标记行之后
            # 必定起新段」，结果把被行尾切断的列表项切坏：
            #   `1. 为向您提供游戏服务…收集、使` ／ `用、存储必要的信息。`
            # 前者是标记行，但它**排满了一行**，它的续行理应并回去。
            # 宽度判据同时覆盖了两种情形——短标记行（`1. 完成注册`）后面起新段，
            # 长标记行（被切断的列表项）后面是续行。
            prev_right = right_edge(prev_line)
            starts_new = prev_right < FULL_RIGHT_EDGE_MIN
            # 只记录**真正可疑**的两类边界，不要把正常的满行续接也报出来
            # （那会让清单淹没在噪音里，等于没报）：
            #   a) 判为续行，但前一行以句末标点收尾 —— 它可能是段落结尾
            #   b) 判为新段，但前行右边界只是勉强不到满行 —— 可能是误判
            prev_stripped = prev_line.strip()
            if not starts_new and prev_stripped and prev_stripped[-1] in SENTENCE_END_CHARS:
                uncertain.append(("疑漏分段", index, prev_stripped[-20:], stripped[:20]))
            elif starts_new and prev_right >= FULL_RIGHT_EDGE_MIN - BORDERLINE_SLACK:
                uncertain.append(("疑误分段", index, prev_stripped[-20:], stripped[:20]))

        if starts_new:
            if current is not None:
                paragraph_lines.append(current)
            current = [stripped]
        else:
            current.append(stripped)

        prev_line = line
        prev_was_centered = is_centered

    if current is not None:
        paragraph_lines.append(current)

    # 先按缝补空格拼接，再折叠排版伪空格——顺序不能反：
    # 反过来的话，`Flash`/`Cookies` 已经被拼成一个词，无从判断该不该补空格。
    paragraphs = [collapse_spaces(join_lines(parts)) for parts in paragraph_lines]
    return paragraphs, uncertain


def chapter_index(paragraph: str):
    """返回段落开头的章号（一=0 … 十=9），不是章标题则返回 None。"""
    match = re.match(r"^([一二三四五六七八九十])、", paragraph)
    return CN_DIGITS.index(match.group(1)) if match else None


def split_off_toc(paragraphs: list) -> tuple:
    """把开头的目录块从正文段落里摘出来。返回 (正文段落, 目录段落)。

    判定用「连续 >=3 个章标题」：目录是连续 10 行章标题，而正文里的章标题之间
    必然隔着正文内容，不可能连续出现。这个判据有两个好处：
      - 不会把正文标题误当成目录删掉；
      - 不依赖「目录文字与正文标题一致」——实测第三节并不一致
        （目录写 `我们如何使用 Cookies`，正文写 `我们如何使用 Cookies 和同类技术`）。
    摘要：目录改由模板统一生成成导航列表，正文里不再保留，
    否则同一标题会出现两次、并且 `id` 重复（同一文档里 id 必须唯一）。

    **遇到章号不递增就断开**：正文第一章的标题紧跟在目录的「十、」后面，
    只按「连续章标题」切分会让两者连成一段 11 个的连续块，把正文的「一、」
    连同目录一起删掉（实测踩到过）。目录的章号是 一..十 递增，
    正文标题从「一」重启，用递增性切分正好把两者分开。
    """
    keep, toc = [], []
    index = 0
    while index < len(paragraphs):
        if chapter_index(paragraphs[index]) is None:
            keep.append(paragraphs[index])
            index += 1
            continue
        run_start, last = index, -1
        while index < len(paragraphs):
            current = chapter_index(paragraphs[index])
            if current is None or current <= last:
                break
            last = current
            index += 1
        run = paragraphs[run_start:index]
        (toc if len(run) >= TOC_MIN_RUN else keep).extend(run)
    return keep, toc


def escape_html(text: str) -> str:
    """转义 HTML 特殊字符。正文里出现 & < > 时不会破坏结构。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def classify(paragraph: str, config: dict) -> tuple:
    """判断段落该渲染成什么元素。返回 (类别, 锚点 id)。

    **显示文本一律用段落原文，不剥掉 `一、`/`（一）`/`1.` 这些前缀。**
    剥前缀会让这些字符从页面文本里消失，破坏「一个字都没动」的可验证性；
    而且原样保留本来就更忠实——原文的章节号就长这样。

    为什么用「是否以句末标点收尾」来区分标题与列表项：
    `1. 完成注册` 与 `1. 我们会根据本政策的约定…进行使用。` 的**前缀完全一样**，
    只能靠结尾判断——短标题不收尾，列表项以。；：收尾。实测本文件全部适用。
    """
    if paragraph == "温馨提示":
        return ("h2", "tips")
    if paragraph == config["meta"]["title"]:
        # 页面 <h1> 已承载这个标题，正文里不再重复，避免同一标题出现两次
        return ("skip", None)

    match = re.match(r"^([一二三四五六七八九十]+)、", paragraph)
    if match:
        return ("h2", config["chapter_ids"].get(match.group(1)))

    if re.match(r"^（[一二三四五六七八九十]+）", paragraph):
        return ("h3", None)

    if re.match(r"^\d+\.\s", paragraph):
        if paragraph[-1] in "。；：！？":
            return ("list-ol", None)   # 有序列表项
        return ("h3", None)            # 短标题

    if re.match(r"^\(\d+\)", paragraph):
        return ("list-paren", None)
    if re.match(r"^[⚫•·]", paragraph):
        return ("list-bullet", None)
    return ("p", None)


def emit_body_html(paragraphs: list, config: dict) -> str:
    """把段落列表渲染成正文 HTML。

    要点：列表项**保留原文的编号字面量**（`(1)`、`1.`、`⚫`）而不交给 <ol> 自动编号。
    这样渲染出来的字符与源 PDF 完全一致，校验时不需要为「编号被 CSS 吃掉」
    单独开豁免；代价是需要用 list-style:none 配悬挂缩进，样式见 style.css。
    """
    parts = []
    open_list = None  # 当前未闭合的列表类别

    def close_list():
        nonlocal open_list
        if open_list:
            parts.append("</ul>")
            open_list = None

    for paragraph in paragraphs:
        kind, anchor = classify(paragraph, config)
        text = escape_html(paragraph)

        if kind not in ("list-ol", "list-paren", "list-bullet"):
            close_list()

        if kind == "skip":
            continue
        if kind == "h2":
            attr = f' id="{anchor}"' if anchor else ""
            parts.append(f"<h2{attr}>{text}</h2>")
        elif kind == "h3":
            parts.append(f"<h3>{text}</h3>")
        elif kind == "p":
            parts.append(f"<p>{text}</p>")
        else:
            if open_list != kind:
                close_list()
                parts.append('<ul class="plain">')
                open_list = kind
            parts.append(f"  <li>{text}</li>")

    close_list()
    return "\n".join(parts) + "\n"


def count_metrics(text: str) -> dict:
    """统计基线指标。全部从 config 读期望值，代码里不写死数字。"""
    return {
        "char_total": len(text),
        "utf8_bytes": len(text.encode("utf-8")),
        "cjk": sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF),
        "non_whitespace": sum(1 for c in text if not c.isspace()),
    }


def assert_baseline(raw_text: str, default_text: str, stream: str, baseline: dict,
                    page_number_lines: int) -> list:
    """断言全部基线指标。任一不符即说明提取被损坏，立即报错退出。"""
    lines = []
    measured = {"page_number_lines": page_number_lines}

    # 原始提取结果的指标
    measured.update(count_metrics(raw_text))
    # 默认输出（不加 flag）的两项：证明「为什么必须加 -eol unix / -nopgbrk」
    measured["default_crlf"] = default_text.count("\r\n")
    measured["default_formfeeds"] = default_text.count("\f")
    measured["pages"] = measured["default_formfeeds"] + 1
    # 字符流（去空白）上的指标：这些不受换行/空格影响
    for key, ch in [("radical_72ad", "犭"), ("wu_total", "无"),
                    ("bracket_open", "【"), ("bracket_close", "】")]:
        measured[key] = stream.count(ch)
    for key, ch in [("ellipsis_dots", "…"), ("name_full", "犭犭猫"),
                    ("aboutcookies", "aboutcookies")]:
        measured[key] = stream.count(ch)
    measured["phone"] = stream.count("18024569629")

    for key, expected in baseline.items():
        if key.startswith("_"):
            continue
        actual = measured.get(key)
        status = "OK " if actual == expected else "!! "
        lines.append(f"  {status}{key:20s} 期望 {expected:>6}  实际 {actual}")
        if actual != expected:
            raise SystemExit(
                "基线指标不符，提取可能已损坏：\n" + "\n".join(lines) +
                f"\n（不符项：{key} 期望 {expected} 实际 {actual}）"
            )
    return lines


def assert_tables(raw_text: str, stream: str, tables: dict) -> list:
    """断言两张表的内容都被提取到，防止表格被静默丢弃。

    为什么用「片段」而不是「整格」断言：实测 SDK 表里单元格会跨行折行，
    且折行后的续行与**其他列**的续行落在同一行（`【SDK 名` 与 `设备型号…、【数`
    同行，`称】` 与 `据类型】` 同行）。因此没有任何一个整格在任何线性提取里
    是连续的，整格断言必然假失败。详见 config.tables 的说明字段。

    表格内容不靠列对齐数学重建：实测表头行与数据行的 token 起始列并不一致。
    """
    lines = []
    # 断言 anchor 在**原始版式**里存在，证明表确实在那里
    haystack_raw = "".join(c for c in raw_text if not c.isspace())
    haystack = "".join(c for c in stream if not c.isspace())
    for name, table in tables.items():
        if name.startswith("_"):
            continue
        if table["anchor"].replace(" ", "") not in haystack_raw:
            raise SystemExit(f"表 {name} 的定位锚丢失：{table['anchor']!r}")
        for fragment in table["required_fragments"]:
            needle = fragment.replace(" ", "")
            if needle not in haystack:
                raise SystemExit(f"表 {name} 的内容片段丢失：{fragment!r}")
        lines.append(f"  OK  {name}: 锚点命中，{len(table['required_fragments'])} 个片段齐全，"
                     f"另声明 {len(table['intentionally_dropped'])} 项有意丢弃")
    return lines


def extract(config: dict = None) -> dict:
    """执行完整提取并校验，返回全部产物（纯计算，不写文件）。

    与 main() 分开是为了让 verify_site.py 能直接调用它——
    校验必须基于**当场重新提取**的结果，而不是可能已经过期的中间文件。
    校验脚本读着一份陈旧产物报「全部通过」，是最没价值的绿。
    """
    config = config or CONFIG
    pdf = Path(config["source"]["pdf"])
    if not pdf.exists():
        raise SystemExit(f"源 PDF 不存在：{pdf}")
    flags = config["source"]["extract_flags"]
    baseline = config["baseline_extract"]

    version_note = []
    try:
        assert_pdftotext_version(config["source"]["pdftotext_version"])
    except SystemExit as exc:
        version_note.append(f"  !! {exc}")

    # 默认输出：仅用于证明「为什么必须加那两个 flag」
    default_text = run_pdftotext(pdf, []).decode("utf-8", "replace")
    raw_text = run_pdftotext(pdf, flags).decode("utf-8", "replace")

    lines = raw_text.split("\n")
    lines, dropped = strip_page_numbers(lines, baseline["page_number_lines"])

    paragraphs, uncertain = segment_paragraphs(lines)
    body_paragraphs, toc_paragraphs = split_off_toc(paragraphs)

    # 字符流：段落按原顺序接起来，只关心字符及其顺序，不关心分行。
    # 这是「一个字都没丢」的比对基准。注意它包含**目录块**——
    # 目录虽然不进正文，但它是源文件的一部分，必须在比对基准里，
    # 才能在改写清单里被显式声明为「已移除」。
    stream = "".join(paragraphs)

    # 校验：被保护的串里的空格是**有意义**的，折叠规则不能把它们吃掉。
    # 实测规则本身已能保住它们，这里是防止将来有人改了 CJK 字符类而悄悄改坏。
    for protected in PROTECTED_STRINGS:
        if protected in raw_text and protected not in stream:
            raise SystemExit(f"保护串被空格折叠破坏：{protected!r}")

    report = ["=== 提取报告 ===", "", "— 版本 —"]
    report += version_note or [f"  OK  pdftotext {config['source']['pdftotext_version']}"]
    report += ["", f"— 页码伪行 — 剔除 {len(dropped)} 行：{dropped}", "",
               "— 基线指标 —"]
    report += assert_baseline(raw_text, default_text, stream, baseline, len(dropped))
    report += ["", "— 表格 —"]
    report += assert_tables(raw_text, stream, config["tables"])
    report += ["", f"— 段落 — 共 {len(paragraphs)} 段",
               f"— 分段存疑 {len(uncertain)} 处（需人工在 src/policy.body.html 定稿）—"]
    for kind, index, prev_tail, cur_head in uncertain:
        report.append(f"  [{kind}] 行 {index}: 「…{prev_tail}」 │ 「{cur_head}…」")

    return {
        "raw_text": raw_text,
        "stream": stream,
        "dropped": dropped,
        "paragraphs": paragraphs,
        "body_paragraphs": body_paragraphs,
        "toc_paragraphs": toc_paragraphs,
        "uncertain": uncertain,
        "report": "\n".join(report),
        "config": config,
    }


def main() -> int:
    BUILD_DIR.mkdir(exist_ok=True)
    result = extract()
    config = result["config"]

    (BUILD_DIR / "policy.raw.txt").write_text(result["raw_text"], encoding="utf-8", newline="\n")
    (BUILD_DIR / "policy.stream.txt").write_text(
        "".join(c for c in result["stream"] if not c.isspace()), encoding="utf-8", newline="\n")
    (BUILD_DIR / "policy.readable.txt").write_text(
        "\n".join(result["paragraphs"]), encoding="utf-8", newline="\n")
    # 段落清单：校验脚本用它做「逐段对照」。
    (BUILD_DIR / "policy.paragraphs.json").write_text(
        json.dumps(result["paragraphs"], ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    # HTML 草稿：正文元素由标记规则自动映射而来。
    # 它的价值在于**保留的文字是逐字搬过来的**，不是手抄；
    # 人工只需在此基础上做改写，从而把抄写错误降到零。
    (BUILD_DIR / "policy.draft.html").write_text(
        emit_body_html(result["body_paragraphs"], config), encoding="utf-8", newline="\n")
    (BUILD_DIR / "policy.toc.txt").write_text(
        "\n".join(result["toc_paragraphs"]), encoding="utf-8", newline="\n")
    (BUILD_DIR / "extract_report.txt").write_text(result["report"], encoding="utf-8", newline="\n")

    print(result["report"])
    print()
    print(f"已写出：{BUILD_DIR}/ 下的 raw / readable / stream / paragraphs.json / "
          f"draft.html / toc.txt / extract_report.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
