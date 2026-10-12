#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把模板、正文真源与集中配置合成 index.html。

数据流：
  src/page.template.html  ─┐
  src/policy.body.html    ─┼─►  index.html
  config/policy.json      ─┘

纪律：index.html 是**生成物**，永不手改；要改内容就改 src/policy.body.html，
要改样式就改 assets/style.css，要改标题/联系方式就改 config/policy.json。
这样「生成的东西」与「手改的东西」不会互相覆盖。
"""

import html
import json
import re
import sys
from pathlib import Path

import lxml.html

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "policy.json"
TEMPLATE_PATH = ROOT / "src" / "page.template.html"
BODY_PATH = ROOT / "src" / "policy.body.html"
OUTPUT_PATH = ROOT / "index.html"


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


def build_toc(body_html: str) -> str:
    """由正文的 <h2> 生成目录列表项。

    目录**取自正文标题**而不是另抄一份，从而不可能与正文不一致。
    代价是与源 PDF 目录里那句 `三、 我们如何使用 Cookies` 不同——
    源目录漏了「和同类技术」四个字，正文标题才是完整的。这一处差异
    在校验里由 config/rewrites.json 显式声明，不是悄悄改的。
    """
    document = lxml.html.fromstring(body_html)
    items = []
    for heading in document.xpath("//h2[@id]"):
        anchor = heading.get("id")
        text = heading.text_content().strip()
        items.append(f'    <li><a href="#{anchor}">{html.escape(text)}</a></li>')
    if not items:
        raise SystemExit("正文里没有找到任何带 id 的 <h2>，目录会生成成空列表")
    return "\n".join(items)


def main() -> int:
    config = load_config()
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    body = BODY_PATH.read_text(encoding="utf-8")
    meta = config["meta"]
    contact = config["contact"]
    paths = config["paths"]

    replacements = {
        "lang": meta["lang"],
        "title": html.escape(meta["title"]),
        "description": html.escape(meta["description"]),
        "game_name": html.escape(meta["game_name"]),
        "updated": html.escape(meta["updated"]),
        "effective": html.escape(meta["effective"]),
        "style_href": paths["style_href"],
        "favicon": paths["favicon"],
        "header_image": paths["header_image"],
        "pdf_asset": paths["pdf_asset"],
        "repo_url": paths["repo_url"],
        "contact_name": html.escape(contact["name"]),
        "contact_address": html.escape(contact["address"]),
        "contact_email": html.escape(contact["email"]),
        "toc": build_toc(body),
        "body": body.strip(),
    }

    output = template
    for key, value in replacements.items():
        output = output.replace("{{" + key + "}}", value)

    leftover = re.findall(r"\{\{[a-z_]+\}\}", output)
    if leftover:
        raise SystemExit(f"模板占位符没有被替换：{sorted(set(leftover))}")

    OUTPUT_PATH.write_text(output, encoding="utf-8", newline="\n")
    print(f"已生成 {OUTPUT_PATH.relative_to(ROOT)}（{len(output)} 字符，"
          f"{len(output.encode('utf-8'))} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
