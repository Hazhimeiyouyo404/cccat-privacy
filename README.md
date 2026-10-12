# 《CCCAT 犭犭猫》隐私政策发布页

《CCCAT 犭犭猫》（Godot 单机游戏）隐私政策的公开网页。

- 站点地址：<https://hazhimeiyou404.github.io/cccat-privacy/>
- 源码仓库：<https://github.com/Hazhimeiyou404/cccat-privacy>

> **这个页面不只是 PDF 的搬运。** 源文件 `privacy-policy-template.pdf` 是一份**填了一半的通用手游模板**：
> 它声称会收集手机号与登录密码、实名认证收集身份证件号码、收集设备 ID 与 IP、有充值消费与广告 SDK。
> 而本游戏是**单机游戏，不联网、不写存档、不接入任何 SDK**（全部经代码实测确认）。
> 原样发布等于公开声明收集身份证号。所以本页在保留模板骨架的前提下做了**诚实化改写**，
> 每一处改动都登记在 `config/rewrites.json` 里并受机器校验约束。

## 目录结构

```
index.html                  【生成物】唯一入口，永不手改
assets/
  style.css                 样式（零依赖：无 CDN、无 web font、无 JS）
  cccat-title.png           页眉标题图（由 CCCAT标题.png 改名而来）
  favicon.png               站点图标（由 猫图标.png 改名而来）
  privacy-policy.pdf        【重新生成】从改写后的页面打印，不是源模板
src/
  page.template.html        页面骨架
  policy.body.html          【正文真源】唯一可以手改的地方
config/
  policy.json               全部常量：标题、日期、联系方式、路径、基线指标
  rewrites.json             改写清单（源文件与页面的全部差异）
tools/
  extract_policy.py         PDF → 规范化段落 + 基线校验
  build_site.py             模板 + 正文 + 配置 → index.html
  verify_site.py            四层校验（见下）
  print_pdf.py              用无头 Edge 把页面打印成 PDF
build/                      【不入库】中间产物与预览截图
```

## 纪律：生成的东西不手改，手改的东西不生成

| 想改什么 | 改哪里 | 然后 |
|---|---|---|
| 正文内容 | `src/policy.body.html` | `python tools/build_site.py` |
| 样式 | `assets/style.css` | 无需重新生成 |
| 标题 / 日期 / 联系方式 | `config/policy.json` | `python tools/build_site.py` |
| 页面结构 | `src/page.template.html` | `python tools/build_site.py` |

**`index.html` 永远不要手改**——下次生成会覆盖掉。

## 重新生成

```bash
python tools/extract_policy.py    # 从 PDF 重新提取并校验基线（一般不必跑）
python tools/build_site.py        # 生成 index.html
python tools/verify_site.py       # 四层校验，必须 PASS
python tools/print_pdf.py         # 重新生成 assets/privacy-policy.pdf
```

## 校验做了什么（`tools/verify_site.py`）

不满足于「页面能打开」这种假绿，分四层：

1. **构建保真** —— `index.html` 正文与 `src/policy.body.html` 逐字符相同（去空白后）。
2. **改写受控** —— 源 PDF 的段落序列与页面段落序列，各自剔除 `config/rewrites.json` 声明的块之后**必须完全相等**。
   这一条同时排除了「有段落被漏掉」「被重复」「被重排」和「有清单外改动」四种情况。
3. **不变量** —— `charset` 声明必须落在前 1024 字节内（否则简中 Windows 会按 GBK 猜编码，整页乱码）；
   `犭` 出现次数必须正好是游戏名次数的两倍（不允许有零星的 `犭`）；
   页面里不允许出现 `【】`、手机号、模板占位「无」、`aboutcookies` 外链。
4. **仓库守卫** —— 被 git 跟踪的文件不得是 `.exe` / `.mp4`，单文件不得超过 1 MiB。

`config/rewrites.json` 是**快照**：首次由 `verify_site.py --emit-rewrites` 生成，经人工复核后冻结。
校验失败时，正确的反应是**先确认那个改动是不是你想要的**，再决定重新生成清单——
见到失败就重跑生成器，等于让校验自己判自己合格。

## 部署（GitHub Pages）

GitHub Pages 从分支直接构建，**push 后自动生效，不需要手动点「更新」**
（这点与已停服的 Gitee Pages 不同——后者每次更新都要回网页点一下）。

首次开启（只需一次）：

1. 在 GitHub 上新建**公开**仓库 `cccat-privacy`（不要勾选 Add a README，否则要先 `git pull --rebase`）。
2. 推送：`git remote add origin git@github.com:Hazhimeiyou404/cccat-privacy.git && git push -u origin main`
3. 仓库 `Settings → Pages`：Source 选 `Deploy from a branch`，分支选 `main`，目录选 `/`。

之后每次改动：改 `src/` 或 `config/` → 跑生成与校验 → `git push`，一两分钟后自动更新。

## 需要注意

- **这不是法律意见。** 改写依据是「这份代码实际做了什么」，合规性仍需自行把关。
  一旦给游戏加上账号、联网、统计或支付功能，本政策必须同步更新——否则就从「诚实」变回「不实」。
- **源 PDF 里的手机号无法真正撤回。** 它存在于源文件里（可能已经流出），本页与本站 PDF 都已不含它，
  但源文件本身不归本站管。
- **源 PDF 不随站点分发。** 它写着收集手机号与身份证号，与改写后的页面互相矛盾。
  `assets/privacy-policy.pdf` 是从页面重新打印的，内容与页面一致。
