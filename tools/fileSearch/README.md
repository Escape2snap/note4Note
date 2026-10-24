# fileSearch

本地文件文档内容搜索 API。默认打开 `http://127.0.0.1:5872`，所有接口返回 JSON，
配置、索引和 txt 缓存统一放在执行目录的 `./.fileSearch` 下。

- 导入 `.pptx`、`.docx`、`.txt`、`.md`、`.pdf`、`.html` 等文件，并缓存为 txt
- 启动时把 txt 加载进内存，搜索不重复读盘
- 关键词搜索，支持跨行命中和可配置的上下文窗口
- 返回文件名、页码、起始/结束行号、上下文和可直接高亮的片段
- 支持读取某一页、某一行或连续多行
- 支持列出已导入文件、重新索引、删除索引（不删源文件）
- 无鉴权，默认只监听回环地址；鉴权见下方 TODO

## 依赖

### 运行依赖

- **Python 3.10+**
- **无第三方 Python 包**：DOCX/PPTX 使用标准库直接读取 OOXML；不需要安装
  `python-docx`、`python-pptx`，也不需要 pip 镜像。
- **PDF 可选依赖**：`pdftotext`（Debian/Ubuntu 包名 `poppler-utils`）。
  只有导入 PDF 时才需要；文件会按 PDF 物理页切分。

```bash
# 可选：启用 PDF 支持（Debian / Ubuntu）
sudo apt-get update
sudo apt-get install -y poppler-utils

# 检查
pdftotext -v
```

### 开发/测试依赖

无。测试使用 Python 标准库 `unittest`，不需要 pytest。

### 本仓库其他工具的依赖

本工具和笔记导出工具链互相独立。导出 PDF 需要的 pandoc、XeLaTeX 等依赖见仓库根目录
[README.md](../../README.md)；fileSearch 本身不需要这些依赖。

## 快速开始

在任意目录执行：

```bash
# 推荐：用 Python 启动（任何时候都能用）
python3 /path/to/collegeNotes/tools/fileSearch/fileSearch.py

# 也可以直接执行
/path/to/collegeNotes/tools/fileSearch/fileSearch.py
```

例如在仓库根目录：

```bash
cd ~/note4Note
python3 tools/fileSearch/fileSearch.py
# 第一次运行会自动创建 ./.fileSearch/config.json
# 监听 http://127.0.0.1:5872
```

首次运行不会自动导入文件。用导入接口指定文件或目录：

```bash
# 导入单个文件（相对路径按启动时的执行目录解析）
curl -sS -X POST http://127.0.0.1:5872/api/import \
  -H 'Content-Type: application/json' \
  -d '{"path": "README.md"}'

# 递归导入当前目录下所有受支持的文件
# .fileSearch、.git、.hg、.svn、__pycache__ 会被跳过
curl -sS -X POST http://127.0.0.1:5872/api/import \
  -H 'Content-Type: application/json' \
  -d '{"path": ".", "recursive": true}'
```

搜索：

```bash
curl -sS --get http://127.0.0.1:5872/api/search \
  --data-urlencode 'q=科学' \
  --data-urlencode 'context=30'
```

也可以直接 POST JSON：

```bash
curl -sS -X POST http://127.0.0.1:5872/api/search \
  -H 'Content-Type: application/json' \
  -d '{"q": "科学", "limit": 10, "context": 40}'
```

下面这个例子同时指定文件、页码和上下文窗口：

```bash
curl -sS -X POST http://127.0.0.1:5872/api/search \
  -H 'Content-Type: application/json' \
  -d '{"q": "目标词", "file": "notes.md", "page": 2, "context": 20}'
```

## 命令行参数

```text
python3 tools/fileSearch/fileSearch.py [选项]

--host IP             监听地址；默认取 config.json，首次为 127.0.0.1
--port PORT           监听端口；默认取 config.json，首次为 5872
--context-chars N     搜索上下文窗口；默认取 config.json，首次为 60
--import PATH [PATH ...]
                      启动前导入文件/目录，可重复；目录递归扫描
--reindex             启动前检查源文件变化并重建缓存
--data-dir DIR        数据目录；默认是执行目录下的 .fileSearch
--version             显示版本
```

命令行传入的 `--host`、`--port`、`--context-chars` 会写回
`.fileSearch/config.json`；下次不带参数启动时会自动加载。如果只是临时使用另一套设置，
可以用 `--data-dir /tmp/my-fileSearch-data` 指向临时数据目录，避免改动当前目录的配置。

## 配置文件与数据目录

首次运行会创建：

```text
执行目录/
├── .fileSearch/
│   ├── config.json          # 监听地址、端口、上下文窗口
│   ├── index.json           # 已导入文件元数据和缓存对应关系
│   ├── cache/
│   │   └── <id>.txt         # 转换后的纯文本；\f 分页
│   └── tmp/                 # 原子写入使用的临时文件，正常情况下为空
├── notes.md                 # 源文件，不会被修改
└── ...
```

`.fileSearch/config.json` 示例：

```json
{
  "version": 1,
  "host": "127.0.0.1",
  "port": 5872,
  "context_chars": 60
}
```

- 删除某个缓存 txt 后，下次启动会尝试从源文件重建。
- 源文件被删除但缓存还在时，仍可以搜索缓存内容。
- 修改源文件后，调用 `POST /api/reindex`，或重启时加 `--reindex` 更新缓存。
- 索引和缓存都写在 `.fileSearch` 内；导出工具等生成的其他文件不受影响。

## API

所有接口返回 `application/json; charset=utf-8`。页码、行号、列号从 1 开始；
`highlight.start/end` 是相对于 `snippet` 的 0 起始、结束开区间偏移。
请求失败时返回：

```json
{
  "error": {
    "code": "bad_request",
    "message": "具体错误信息"
  }
}
```

### 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | API 名称、版本和接口列表 |
| GET | `/health` | 健康检查 |
| GET | `/api/config` | 当前生效的配置和路径 |
| GET | `/api/files` | 列出所有已导入文件 |
| GET | `/api/files/{id}` | 单个文件信息 |
| GET | `/api/files/{id}/pages` | 每页的行数、字符数 |
| GET | `/api/files/{id}/pages/{page}` | 某一页的完整内容 |
| GET | `/api/files/{id}/lines?page=1&line=3` | 某一行的内容 |
| GET | `/api/files/{id}/lines?page=1&start=3&end=5` | 连续几行的内容 |
| GET | `/api/search?q=...` | 搜索 |
| POST | `/api/search` | 搜索，参数用 JSON |
| POST | `/api/import` | 按路径导入文件或目录 |
| POST | `/api/reindex` | 检查源文件并重建缓存 |
| DELETE | `/api/files/{id}` | 从索引删除，保留源文件 |

### 导入

`POST /api/import`

```json
{
  "path": "notes/",
  "recursive": true
}
```

也支持 `paths` 数组：

```json
{
  "paths": ["notes/a.md", "slides/b.pptx", "docs/"],
  "recursive": true
}
```

- 相对路径按启动时的执行目录解析；绝对路径也可以。
- `recursive` 默认 `true`。
- 文件按绝对路径去重；同一个文件重复导入会更新缓存。
- 成功时返回 `{"count": n, "imported": [...], "errors": [...]}`。
- 全部失败时返回 HTTP 400，但仍是结构化 JSON；失败列表在 `errors`。
- 当前只支持「服务器本机路径导入」，不支持 multipart 文件上传。

### 搜索

`GET /api/search` 参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `q` | 必填 | 关键词/短语；最长 500 字符；默认不区分大小写 |
| `limit` | 20 | 返回条数，1..200 |
| `offset` | 0 | 跳过条数 |
| `context` | 配置值（60） | 上下文窗口字符数，0..5000；也可用 `context_chars` |
| `page` | 无 | 只搜指定页 |
| `file` | 无 | 文件 id、相对路径、绝对路径或文件名 |
| `case_sensitive` | false | 是否区分大小写 |

`POST /api/search` 接受相同的字段，字段名是 `q` 或 `query`。

搜索响应示例（字段有删减）：

```json
{
  "query": "科学",
  "total": 2,
  "returned": 2,
  "offset": 0,
  "limit": 20,
  "context_chars": 30,
  "case_sensitive": false,
  "has_more": false,
  "results": [
    {
      "file_id": "0123456789abcdef01234567",
      "file_name": "notes.md",
      "path": "notes.md",
      "format": "md",
      "page": 1,
      "line": 2,
      "end_line": 3,
      "start_column": 7,
      "end_column": 1,
      "match": "科学",
      "snippet": "…这是一个学科科\n学…",
      "highlight": {"start": 7, "end": 10},
      "context_lines": [
        {"line": 2, "text": "这是一个学科科"},
        {"line": 3, "text": "学"}
      ],
      "page_count": 2
    }
  ]
}
```

- 每条结果都包含 `file_name` 和相对路径 `path`。
- `line` / `end_line` 是**该页内**的 1 起始行号；`page` 是页号。
- `highlight` 直接用于切片 `snippet`。因为命中可能跨行，高亮区间里可能包含
  `\n`；做可见文字高亮时可以同时处理换行。
- `context_lines` 是按原文档行切开后的上下文，便于前端按行渲染。

### 页、行读取

```bash
# 第 2 页
curl -sS http://127.0.0.1:5872/api/files/<file_id>/pages/2

# 第 2 页第 5 行
curl -sS 'http://127.0.0.1:5872/api/files/<file_id>/lines?page=2&line=5'

# 第 2 页第 5..8 行
curl -sS 'http://127.0.0.1:5872/api/files/<file_id>/lines?page=2&start=5&end=8'

# 不写 start/end 时返回整页
curl -sS 'http://127.0.0.1:5872/api/files/<file_id>/lines?page=2'
```

`lines` 接口返回：

```json
{
  "file_id": "0123456789abcdef01234567",
  "file_name": "notes.md",
  "path": "notes.md",
  "page": 2,
  "page_count": 3,
  "start_line": 5,
  "end_line": 6,
  "total_lines": 20,
  "line_count": 2,
  "lines": [
    {"line": 5, "text": "第一行"},
    {"line": 6, "text": "第二行"}
  ],
  "content": "第一行\n第二行"
}
```

## 跨行搜索规则

搜索时会把**连续的非空行**拼成一个段落再匹配，所以：

```text
这是一个学科科
学
```

搜索 `科学` 会命中，结果的 `line=1`、`end_line=2`，高亮片段里保留原始换行。
**空行**是段落边界；跨空行不会被拼接，避免把两个不相关的段落误连在一起。

## 文件格式与分页

| 后缀 | 转换方式 | 页的定义 |
| --- | --- | --- |
| `.txt`, `.text`, `.md`, `.markdown`, `.mdown`, `.mkd`, `.rst`, `.log`, `.csv`, `.json`, `.yaml`, `.yml`, `.tex` | 按文本读取，自动尝试 UTF-8/UTF-16/UTF-32/GB18030/Big5 | 文本中的换页符 `\f`；没有则第 1 页 |
| `.docx` | 标准库读取 OOXML 段落、换行和表格文本 | 显式分页符、`lastRenderedPageBreak`、段落“段前分页”；没有则第 1 页 |
| `.pptx` | 标准库按 presentation 顺序读取幻灯片文本 | 每张幻灯片一页 |
| `.pdf` | `pdftotext -layout` | PDF 物理页 |
| `.html`, `.htm` | 标准库 HTMLParser 去标签 | `\f`；没有则第 1 页 |

补充说明：

- Markdown 按原始文本索引（保留标记符号），不做渲染。
- DOCX 不进行桌面排版，因此没有显式/渲染分页信息的文档会是一页。
- PPTX 只取幻灯片中的文本，不读取备注、图片 OCR、图表数据。
- 扫描版 PDF 需要 OCR 后才能得到文本；本工具不做 OCR。
- 缓存 txt 使用 `\f` 分隔页，页内用 `\n` 分行。可直接查看：

```bash
sed -n '1,60p' .fileSearch/cache/<id>.txt
```

## 安全与 TODO

当前设计面向本机使用：

- **无鉴权**：所有接口无需 Token。默认只监听 `127.0.0.1`。
- 如果改成 `--host 0.0.0.0` 或对外网开放，任何能访问端口的人都可以搜索本机已导入的文档。
- 导入接口可以读取服务器进程有权限读取的受支持文件；不要在有不可信用户的机器上监听公网地址。
- 没有开启 CORS；浏览器前端跨域调用需自行加代理或反向代理。

TODO：

- [ ] Token / Basic Auth 鉴权
- [ ] 可配置的导入根目录白名单
- [ ] 文件变更监听与自动重建
- [ ] 可选 Web 搜索界面
- [ ] OCR / 图片文字提取

## 测试

```bash
python3 -m unittest discover -s tools/fileSearch/tests -v
```

测试覆盖文本跨行、DOCX/PPTX 转换、分页/行读取、无鉴权 API、导入和配置持久化。
