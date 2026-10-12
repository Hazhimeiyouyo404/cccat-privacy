#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验站点：证明「除改写清单外，一个字都没动」。

四个层次，缺一不可：
  1. 构建保真   index.html 的正文文本 == src/policy.body.html 的文本（逐字符）
  2. 改写受控   源 PDF 段落序列 与 页面段落序列，各自剔除清单声明的块之后必须相等。
                这是「除清单外一字未动」的机检形式——它也顺带证明了没有段落
                被漏掉、重复或重排。
  3. 不变量     字符集、联系方式、charset 位置等硬性事实
  4. 仓库守卫   绝不把 exe / mp4 这类大文件提交进仓库

用法：
  python tools/verify_site.py                 校验（默认）
  python tools/verify_site.py --emit-rewrites 重新推导改写清单写入 config/rewrites.json

关于 --emit-rewrites 的诚实说明：改写清单是**快照**。首次由本命令生成，
经人工复核后提交；此后它就冻结为期望值。改动正文后发现校验失败，
正确的反应是**先确认改动是想要的**，再重新生成并复核清单，
而不是见到失败就重跑生成器——那等于让校验自己判自己合格。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import lxml.html

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_policy  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "policy.json"
REWRITES_PATH = ROOT / "config" / "rewrites.json"
INDEX_PATH = ROOT / "index.html"
BODY_PATH = ROOT / "src" / "policy.body.html"
PDF_PATH = ROOT / "assets" / "privacy-policy.pdf"
# 正文容器的 id：校验只比对这个容器内部，页眉页脚目录不参与
BODY_ELEMENT_ID = "policy-body"


def nospace(text: str) -> str:
    """去掉全部空白字符。分段与折行是排版问题，不该干扰文本比对。"""
    return "".join(c for c in text if not c.isspace())


def fail(message: str) -> None:
    print()
    print("FAIL " + message)
    sys.exit(1)


def element_by_id(html_text: str, element_id: str):
    """按 id 取元素。用 XPath 而不是 cssselect：后者需要额外装 cssselect 包，
    为了一个选择器引入依赖不划算。"""
    nodes = lxml.html.fromstring(html_text).xpath(f"//*[@id='{element_id}']")
    if not nodes:
        fail(f"在 index.html 里找不到 id={element_id!r} 的元素，"
             f"正文可能没有包在预期容器里")
    return nodes[0]


def text_of_element(element) -> str:
    """元素内部的纯文本，含全部后代。"""
    return lxml.html.fromstring(
        lxml.html.tostring(element, encoding="unicode")).text_content()


def paragraphs_of(html_text: str, element_id: str = None) -> list:
    """取一段 HTML 的「段落」序列。

    段落 = 每个块级元素自己的文字。这里用 text_content() 按换行切分：
    lxml 会把 <p>/<li>/<h2> 之间渲染成换行，因此切出来的正是元素级文本。
    """
    if element_id:
        html_text = lxml.html.tostring(
            element_by_id(html_text, element_id), encoding="unicode")
    text = lxml.html.fromstring(html_text).text_content()
    return [nospace(line) for line in text.split("\n") if nospace(line)]


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def strip_blocks(sequence: list, blocks: list, key: str) -> list:
    """按清单把若干「块」从序列里删掉，返回剩下的部分。

    每块必须**原样、按序、恰好一次**地找到；找不到或匹配到多次都直接报错——
    宁可响亮地失败，也不要悄悄放行一个错位的清单。
    """
    out, cursor = [], 0
    for index, block in enumerate(blocks):
        target = block.get(key, [])
        if not target:
            continue
        matches = [start for start in range(cursor, len(sequence))
                   if sequence[start:start + len(target)] == target]
        if not matches:
            fail(f"改写清单第 {index} 块（{block.get('label', '?')}）的 {key} 在序列里找不到：\n"
                 f"    {target[0][:90]}")
        if len(matches) > 1:
            fail(f"改写清单第 {index} 块（{block.get('label', '?')}）的 {key} 在序列里出现多次，"
                 f"位置 {matches}，无法确定该剔哪一处")
        out.extend(sequence[cursor:matches[0]])
        cursor = matches[0] + len(target)
    out.extend(sequence[cursor:])
    return out


def derive_blocks(source: list, published: list, config: dict) -> list:
    """由源序列与页面序列推导出改写清单。仅供 --emit-rewrites 使用。

    块的中文标签取自 config.rewrite_labels（人工撰写），
    这样重新生成本清单时不会把写好的说明冲掉；
    条数与块数对不上时退回自动标签，并提示人工补齐。
    """
    import difflib
    labels = config.get("rewrite_labels", {}).get("labels", [])
    matcher = difflib.SequenceMatcher(None, source, published, autojunk=False)
    blocks = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed, added = source[i1:i2], published[j1:j2]
        index = len(blocks)
        label = labels[index] if index < len(labels) else (removed[0] if removed else added[0])[:40]
        blocks.append({
            "label": label,
            "change": {"replace": "改写", "delete": "删除", "insert": "新增"}[tag],
            "removed": removed,
            "added": added,
        })

    if len(blocks) != len(labels):
        print(f"注意：改写块有 {len(blocks)} 个，而 config.rewrite_labels 写了 "
              f"{len(labels)} 条，请补齐或删除多余的标签。")
    return blocks


def check_build_fidelity(body_html: str, index_html: str) -> list:
    """层次 1：页面上正文的文本必须与正文真源逐字符相同。"""
    expected = nospace(lxml.html.fromstring(body_html).text_content())
    actual = nospace(text_of_element(element_by_id(index_html, "policy-body")))
    if expected != actual:
        import difflib
        diff = list(difflib.unified_diff(
            [expected[i:i + 60] for i in range(0, len(expected), 60)],
            [actual[i:i + 60] for i in range(0, len(actual), 60)],
            lineterm="", n=1))
        fail("index.html 的正文与 src/policy.body.html 不一致（构建过程改动了正文）：\n"
             + "\n".join(diff[:40]))
    return [f"  OK  index.html 正文 == src/policy.body.html（{len(expected)} 字符，逐字符相同）"]


def check_rewrites(source: list, published: list, rewrites: dict) -> list:
    """层次 2：剔除清单声明的块后，源序列与页面序列必须完全相同。"""
    blocks = rewrites["blocks"]
    source_rest = strip_blocks(source, blocks, "removed")
    published_rest = strip_blocks(published, blocks, "added")
    if source_rest != published_rest:
        import difflib
        diff = []
        for line in difflib.unified_diff(
                [p[:70] for p in source_rest], [p[:70] for p in published_rest],
                fromfile="源 PDF 剔除清单后", tofile="页面剔除清单后", lineterm="", n=1):
            diff.append(line)
        fail("剔除改写清单后，源文本与页面文本仍有差异——说明有清单之外改动：\n"
             + "\n".join(diff[:50]))

    removed_total = sum(len(b.get("removed", [])) for b in blocks)
    added_total = sum(len(b.get("added", [])) for b in blocks)
    return [f"  OK  {len(blocks)} 个改写块：删/改 {removed_total} 段，新增 {added_total} 段",
            f"  OK  剔除清单后两侧均为 {len(source_rest)} 段，逐段相同（无清单外改动）"]


def check_invariants(index_html: str, config: dict) -> list:
    """层次 3：硬性不变量。"""
    invariants = config["published_invariants"]
    lines = []
    raw_bytes = INDEX_PATH.read_bytes()

    # charset 必须出现在文件前若干字节内：GitHub Pages 不一定发 charset 响应头，
    # 这个 meta 是浏览器判断编码的唯一依据。放太后会导致整页乱码。
    limit = invariants["charset_must_be_within_bytes"]
    position = raw_bytes.find(b'<meta charset="utf-8">')
    if position < 0 or position >= limit:
        fail(f'<meta charset="utf-8"> 的位置是 {position}，必须落在前 {limit} 字节内')
    lines.append(f"  OK  charset 声明位于第 {position} 字节（< {limit}）")

    # 「犭」只应成对出现在游戏名里：任何零星的 犭 都说明有字被改坏
    radical = index_html.count("犭")
    name = index_html.count("犭犭猫")
    if radical != name * 2:
        fail(f"「犭」出现 {radical} 次，而游戏名「犭犭猫」出现 {name} 次——"
             f"存在不成对的 犭，说明游戏名被改坏")
    lines.append(f"  OK  「犭」{radical} 次全部成对出现在游戏名中（{name} 处）")

    # 正文里的游戏名次数（配置期望值）
    body_text = text_of_element(element_by_id(index_html, "policy-body"))
    body_names = body_text.count("犭犭猫")
    expected_names = invariants.get("body_name_occurrences")
    if expected_names is not None and body_names != expected_names:
        fail(f"正文里游戏名出现 {body_names} 次，期望 {expected_names} 次")
    lines.append(f"  OK  正文里游戏名出现 {body_names} 次（期望 {expected_names}）")

    # 禁用串
    for forbidden in invariants["forbidden_strings"]:
        count = index_html.count(forbidden)
        if count:
            fail(f"页面里仍然出现禁用串 {forbidden!r}（{count} 次）")
    lines.append(f"  OK  {len(invariants['forbidden_strings'])} 个禁用串均未出现"
                 f"（含【】、手机号、模板占位「无」、aboutcookies 外链）")

    # 联系方式
    email = config["contact"]["email"]
    if email not in index_html:
        fail(f"页面里找不到联系邮箱 {email!r}")
    lines.append(f"  OK  联系邮箱 {email} 已出现")

    return lines


def check_repo_guards(config: dict) -> list:
    """层次 4：仓库守卫——绝不把大体积二进制提交进仓库。"""
    guards = config["guards"]
    result = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return ["  --  还没有 git 仓库，跳过仓库守卫（建仓后重跑）"]

    tracked = [line for line in result.stdout.splitlines() if line.strip()]
    lines = []
    for name in tracked:
        suffix = Path(name).suffix.lower()
        if suffix in guards["forbidden_extensions"]:
            fail(f"仓库里跟踪了禁止提交的文件类型：{name}")
        size = (ROOT / name).stat().st_size
        if size > guards["max_blob_bytes"]:
            fail(f"被跟踪的文件 {name} 有 {size} 字节，超过上限 "
                 f"{guards['max_blob_bytes']}")
    biggest = max(((ROOT / n).stat().st_size, n) for n in tracked)
    lines.append(f"  OK  {len(tracked)} 个被跟踪文件，无禁止类型，"
                 f"最大 {biggest[1]}（{biggest[0]} 字节）")
    return lines


def check_url_consistency(config: dict, index_html: str) -> list:
    """断言配置里的仓库地址/站点地址与 git remote 指向同一个账号。

    这条守卫来自一次真实事故：GitHub 用户名是 Hazhimeiyouyo404（16 字符），
    我把它误抄成 Hazhimeiyou404（漏掉中间一节 "yo"，14 字符），于是 remote
    指向一个不存在的仓库、页面页脚也印着错地址，一路到 push 才暴露。
    用户名这种一眼看不出差别的字符串，靠手工核对是不靠谱的——
    让配置跟 remote 直接对账，才拦得住。
    """
    result = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return ["  --  没有 origin remote，跳过地址一致性检查"]

    remote_url = result.stdout.strip()
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", remote_url)
    if not match:
        return [f"  --  origin 地址 {remote_url!r} 解析不出账号/仓库名，跳过"]
    owner, repo = match.group(1), match.group(2)

    problems = []
    if f"/{owner}/{repo}" not in config["paths"]["repo_url"]:
        problems.append(f"repo_url 与 remote 不一致："
                        f"配置写 {config['paths']['repo_url']}，remote 指向 {owner}/{repo}")
    if f"{owner.lower()}.github.io/{repo}" not in config["paths"]["site_url"].lower():
        problems.append(f"site_url 与 remote 不一致："
                        f"配置写 {config['paths']['site_url']}，按 remote 应为 "
                        f"{owner.lower()}.github.io/{repo}")
    if config["paths"]["repo_url"] not in index_html:
        problems.append("index.html 页脚里没有出现配置声明的 repo_url（忘了重新生成？）")
    if problems:
        fail("\n".join(problems))
    return [f"  OK  配置地址与 remote 一致：{owner}/{repo}",
            f"      repo_url = {config['paths']['repo_url']}",
            f"      site_url = {config['paths']['site_url']}"]


def check_pdf(config: dict) -> list:
    """检查随站点下载的 PDF 与页面内容一致。

    注意：这个 PDF 是**从改写后的页面重新生成**的，不是源模板。
    源模板里写着收集手机号与身份证号，与改写后的页面互相矛盾，
    因此绝不能原样随站点分发。
    """
    if not PDF_PATH.exists():
        return ["  --  assets/privacy-policy.pdf 尚未生成，跳过（跑 tools/print_pdf.sh）"]

    result = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", str(PDF_PATH), "-"], capture_output=True)
    if result.returncode != 0:
        fail("随站点分发的 PDF 无法被 pdftotext 解析")
    text = nospace(result.stdout.decode("utf-8", "replace"))

    for forbidden in ("【", "18024569629", "点击无", "共享给无"):
        if forbidden in text:
            fail(f"随站点分发的 PDF 里仍含 {forbidden!r}——它可能是未改写的源模板")
    if config["contact"]["email"].replace(" ", "") not in text:
        fail("随站点分发的 PDF 里找不到联系邮箱")
    if "CCCAT犭犭猫" not in text:
        fail("随站点分发的 PDF 里找不到游戏名")
    return [f"  OK  PDF 与页面一致：无占位符、无手机号、含邮箱与游戏名"
            f"（{PDF_PATH.stat().st_size} 字节）"]


def main() -> int:
    config = load_json(CONFIG_PATH)
    index_html = INDEX_PATH.read_text(encoding="utf-8")
    body_html = BODY_PATH.read_text(encoding="utf-8")

    # 当场重新提取，绝不读可能过期的中间产物
    source_paragraphs = [nospace(p) for p in extract_policy.extract(config)["paragraphs"]]
    published_paragraphs = paragraphs_of(index_html, BODY_ELEMENT_ID)

    if "--emit-rewrites" in sys.argv:
        blocks = derive_blocks(source_paragraphs, published_paragraphs, config)
        REWRITES_PATH.write_text(
            json.dumps({
                "_说明": "改写清单快照：源 PDF 的段落与页面段落之间的全部差异。"
                         "由 tools/verify_site.py --emit-rewrites 生成，经人工复核后冻结。"
                         "removed = 源文件里被删/被替换掉的段落；added = 页面上新增的段落。"
                         "顺序即文档顺序，不许重排。",
                "blocks": blocks,
            }, ensure_ascii=False, indent=1),
            encoding="utf-8", newline="\n")
        print(f"已写出 {REWRITES_PATH.relative_to(ROOT)}：{len(blocks)} 个改写块")
        for block in blocks:
            print(f"  [{block['change']}] {block['label']}")
        return 0

    if not REWRITES_PATH.exists():
        fail(f"缺少 {REWRITES_PATH.relative_to(ROOT)}。先跑 "
             f"`python tools/verify_site.py --emit-rewrites` 生成并复核。")
    rewrites = load_json(REWRITES_PATH)

    print("=== 站点校验 ===")
    print()
    print("— 层次 1：构建保真 —")
    report = check_build_fidelity(body_html, index_html)
    print("\n".join(report))
    print()
    print("— 层次 2：改写受控 —")
    report = check_rewrites(source_paragraphs, published_paragraphs, rewrites)
    print("\n".join(report))
    print()
    print("— 层次 3：不变量 —")
    report = check_invariants(index_html, config)
    print("\n".join(report))
    print()
    print("— 层次 4：仓库守卫 —")
    report = check_repo_guards(config)
    print("\n".join(report))
    print()
    print("— 地址一致性 —")
    report = check_url_consistency(config, index_html)
    print("\n".join(report))
    print()
    print("— 附件一致性 —")
    report = check_pdf(config)
    print("\n".join(report))
    print()
    print("PASS  全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
