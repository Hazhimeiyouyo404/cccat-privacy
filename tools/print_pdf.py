#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用无头浏览器把 index.html 打印成 assets/privacy-policy.pdf。

为什么是「重新生成」而不是「沿用源 PDF」：
源模板 privacy-policy-template.pdf 里写着会收集手机号、身份证件号码，并做实名认证；
而改写后的页面说本游戏不收集任何个人信息。两者放在同一站点上会自相矛盾，
等于给审核方送上两份互相打架的材料。所以随站点分发的 PDF 必须从页面重新打印。

为什么用 Python 而不是 shell：本站路径含中文，且 Edge 需要**百分号编码过的**
file:// URI。bash 里手工拼路径会把 `E:/` 拼成 `/e/`、把 `file:///` 拼成 `file:////`，
实测两者都会让 Edge 报 ERR_FILE_NOT_FOUND。Path.as_uri() 一次做对。

本机没有 poppler / Ghostscript / ImageMagick，但有 Edge，它自带 --print-to-pdf。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"
OUTPUT = ROOT / "assets" / "privacy-policy.pdf"

# 按优先级排列的无头浏览器（Windows 上的常见安装位置）
BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str:
    for candidate in BROWSER_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise SystemExit(
        "找不到 Edge 或 Chrome，无法生成 PDF。\n"
        "装了其中任一后重跑，或手动用浏览器「打印为 PDF」保存到 " + str(OUTPUT)
    )


def main() -> int:
    if not PAGE.exists():
        raise SystemExit(f"{PAGE} 不存在，先跑 `python tools/build_site.py`")

    browser = find_browser()
    uri = PAGE.as_uri()          # 自动对中文路径做百分号编码
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        OUTPUT.unlink()          # 先删掉，否则「生成失败」时会把旧文件当成成功产物

    print(f"浏览器：{browser}")
    print(f"页面：  {uri}")
    print(f"输出：  {OUTPUT}")

    subprocess.run([
        browser,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",                  # 去掉自动加的页眉页脚（URL 与页码）
        "--run-all-compositor-stages-before-draw",  # 等图像渲染完再截，避免标题图缺失
        "--virtual-time-budget=8000",
        f"--print-to-pdf={OUTPUT}",
        uri,
    ], check=False)

    if not OUTPUT.exists() or OUTPUT.stat().st_size == 0:
        raise SystemExit("生成失败：PDF 不存在或为空。")

    print(f"完成：{OUTPUT.stat().st_size} 字节")
    print("接着跑 `python tools/verify_site.py`，确认 PDF 里没有残留的占位符与手机号。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
