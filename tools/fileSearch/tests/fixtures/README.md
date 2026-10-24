# Legacy Office test fixtures

- `legacy_sample.doc.gz`：由 LibreOffice 从测试 DOCX 转出的 Word 97–2003 文档
- `legacy_sample.ppt.gz`：由 LibreOffice 从测试 PPTX 转出的 PowerPoint 97–2003 演示文稿

两个文件只用于验证 `.doc` / `.ppt` 转换，内容均为测试文本。测试通过 Python 标准库
`gzip` 解压到临时目录，源文件不进入仓库；生成夹具时使用了 python-docx / python-pptx
和 LibreOffice，但运行测试只依赖 LibreOffice（soffice）与 poppler（pdftotext）。
