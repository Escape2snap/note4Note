# collegeNotes

大学课程笔记，Markdown 编写，经 `md2tex` → `tex2pdf` 导出为 PDF（A4/B5）。
工具与排版风格取自 [noteANote](https://github.com/Escape2snap/noteANote) 的导出流水线，拆成了两个独立的阶段，再用 `export.py` 串成一步。

## 结构

```
collegeNotes/
├── *.md                    # 笔记正文（README.md / CLAUDE.md 不参与导出）
├── .fileSearch/            # fileSearch 运行时生成（已加入 .gitignore）
├── tools/
│   ├── export.py           # 一步导出：md2tex → tex2pdf
│   ├── md2tex.py           # Markdown → .tex（pandoc + LaTeX 头 + Unicode 过滤器）
│   ├── tex2pdf.py          # .tex → .pdf（xelatex，自动补跑目录/交叉引用）
│   ├── list-spacing.tex    # LaTeX 头：页眉页脚、目录页码、表格底色、pgfplots/tikz
│   ├── unicode2latex.lua   # Unicode 上下标/箭头 → LaTeX
│   └── fileSearch/         # 本地文档内容搜索 API（独立工具）
│       ├── fileSearch.py   # 启动入口
│       ├── file_search/    # 转换、索引、搜索、HTTP API
│       └── README.md       # API 与配置说明
└── output/
    ├── <timestamp>/        # 每次转换的 .tex 与 .pdf
    └── *.latest.*          # 最新一次的副本
```

## 使用

### 一步导出（推荐）

```bash
python3 tools/export.py                    # 全部笔记 → A4 PDF（带目录）
python3 tools/export.py --b5               # A4 + B5
python3 tools/export.py --no-toc           # 无目录版本
python3 tools/export.py 高等数学            # 只导出指定笔记
python3 tools/export.py --once             # xelatex 只跑一遍
```

`export.py` 支持 md2tex 与 tex2pdf 的全部开关（`--b5`、`--no-toc`、`--keep-header`、`--once`），
转换失败会直接停止，不会去编译旧的 `.tex`。
等价于 `python3 tools/md2tex.py [选项] && python3 tools/tex2pdf.py`。

### 分步执行

```bash
# 1. Markdown → LaTeX
python3 tools/md2tex.py                    # 全部笔记，A4，带目录
python3 tools/md2tex.py --b5               # 同时生成 B5
python3 tools/md2tex.py --no-toc           # 生成不带目录的版本（.notoc）
python3 tools/md2tex.py 高等数学            # 指定笔记（支持前缀匹配）
python3 tools/md2tex.py ./focus/xxx.md     # 任意路径的笔记

# 2. LaTeX → PDF
python3 tools/tex2pdf.py                   # 编译最新一次转换的全部 .tex
python3 tools/tex2pdf.py 高等数学           # 只编译指定笔记
python3 tools/tex2pdf.py --once            # 只跑一遍 xelatex
```

三个脚本都带执行权限，也可以直接 `./tools/export.py`、`./tools/md2tex.py`、`./tools/tex2pdf.py`。

## 文件内容搜索（fileSearch）

`tools/fileSearch` 是一个独立的本地搜索工具：把 DOCX/PPTX/TXT/Markdown/PDF 等文件转成
txt 缓存到 `./.fileSearch`，加载进内存后通过 JSON API 做关键词搜索，支持跨行命中、
上下文窗口、按页/按行读取。配置和缓存也都在 `./.fileSearch` 下，下次无参数启动自动加载。

```bash
# 启动（默认 http://127.0.0.1:5872）
python3 tools/fileSearch/fileSearch.py

# 导入当前目录下的文档
curl -sS -X POST http://127.0.0.1:5872/api/import \
  -H 'Content-Type: application/json' \
  -d '{"path": ".", "recursive": true}'

# 搜索
curl -sS --get http://127.0.0.1:5872/api/search \
  --data-urlencode 'q=科学'
```

端口、监听地址、上下文窗口可用 `--port` / `--host` / `--context-chars` 指定，并会写回
`./.fileSearch/config.json`。完整依赖、API、分页规则和安全说明见
[fileSearch README](tools/fileSearch/README.md)。

> 默认无鉴权且只监听 `127.0.0.1`。不要把无鉴权端口直接暴露到不可信网络；鉴权在 TODO 中。

## 命名规则

| 阶段 | 产物 |
|------|------|
| md2tex | `name.YYYYmmdd.HHMMSS.a4.tex`、`name.YYYYmmdd.HHMMSS.b5.tex` |
| md2tex `--no-toc` | 同上，但多一个 `.notoc` 后缀：`name.YYYYmmdd.HHMMSS.a4.notoc.tex` |
| tex2pdf | 同名 `.pdf`（与 `.tex` 放在一起） |
| 最新副本 | `output/name.latest.a4.pdf`、`output/name.latest.a4.notoc.pdf` 等 |

### 不带目录的版本

`--no-toc` 会在本次转换中**只生成**无目录的 `.tex`，文件名带 `.notoc`，
因此不会覆盖已生成的带目录版本；要两种都留，就跑两次：

```bash
python3 tools/md2tex.py                    # 带目录
python3 tools/md2tex.py --no-toc           # 不带目录
python3 tools/tex2pdf.py                   # 一起编译
```

无目录版本没有 `\tableofcontents`，也就没有罗马数字页码和"正文从奇数页开始"的留白，
正文直接从第 1 页排起，适合打印或快速翻阅。

## 排版约定（沿用 noteANote）

- **纸张/边距**：A4 或 B5，margin 2cm，`twoside` 双面
- **字体**：Noto Serif CJK SC（正文 + 中文），STIX Two Math（数学，脚本自动探测本机可用名）
- **目录**：独立罗马数字页码；正文阿拉伯数字，并从奇数页开始（必要时留一页空白）
- **页眉**：灰底当前章节（`\rightmark`，即本页第一个标题），向页边外延伸 1cm
- **页脚**：外侧页码，内侧导出时间（奇数页左内右外，偶数页镜像）
- **表格**：`gray!12` 交替行底色，`\tabcolsep=10pt`
- **列表**：`itemsep=0.5em`、`parsep=0.3em`（取消 pandoc 的 `tightlist` 紧排，行间留白）
- **图**：pgfplots + tikz（positioning / trees / arrows.meta）
- **标题**：`toc-depth=3`；`#` 学科、`##` 章节、`###` 小节

## 写笔记的几条硬规则

- 公式一律用 `$...$` / `$$...$$`，不要写纯文本公式
- 标题、段落后面紧跟列表或表格时，中间必须空一行，否则 pandoc 不识别块级语法
- 流程/实验步骤用表格或列表，不用代码块（等宽字体排版效果差）
- 示意图优先 `tikzpicture`，其次 markdown 嵌套列表，不要 ASCII 字符画
- 标题里避免 `₂` `→` 等符号（会进 PDF 书签，`\texorpdfstring` 容易报错）

## 与源仓库的两处差异

1. `unicode2latex.lua` 的箭头改成 `pandoc.RawInline("latex", ...)` 输出。原实现把 `\ensuremath{...}` 塞进 `Str`，LaTeX 写出器会转义反斜杠，PDF 里显示的是 `\textbackslash ensuremath\{...\}` 这串字面文本。
2. `list-spacing.tex` 的三级标题都写 `\markboth{#1}{#1}`。原实现只给 `\section` 设 left、给 `\subsection` 设 right，页面正好从 `\section` 起头时 `\rightmark` 为空，页眉会变成空灰块。

## 依赖

```bash
pandoc --version          # 需要 pandoc 3.x
xelatex --version         # TeX Live
pdfinfo -v 2>&1 | head -1 # poppler-utils（用于统计页数，可选）
fc-match "Noto Serif CJK SC"
fc-match "STIX Two Math"
```

字体名不一致时，改 `tools/md2tex.py` 顶部的 `CJK_FONT` / `MAIN_FONT` / `MATH_FONT_CANDIDATES`。

### fileSearch

- **Python 3.10+**；无第三方 Python 包，不需要 pip 安装。
- **PDF 可选依赖**：`pdftotext`（Debian/Ubuntu 包名 `poppler-utils`）。不导入 PDF 时不需要；
  上面的 `pdfinfo` 也来自同一个包。
- DOCX/PPTX 解析使用标准库，不需要 `python-docx`、`python-pptx`。
- 测试使用标准库 `unittest`，不需要 pytest。

```bash
# 可选：PDF 支持
sudo apt-get install -y poppler-utils
```

详细依赖与 API 见 [tools/fileSearch/README.md](tools/fileSearch/README.md)。
