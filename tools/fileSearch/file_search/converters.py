"""Document to plain-text converters used by fileSearch.

The OOXML readers intentionally use only the Python standard library. They do
not try to fully re-create Word/PowerPoint layout; they preserve the text and
the page boundaries that the file itself records.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DC_NS = "http://purl.org/dc/elements/1.1/"

W = "{" + W_NS + "}"
A = "{" + A_NS + "}"
P = "{" + P_NS + "}"
R = "{" + R_NS + "}"
DC = "{" + DC_NS + "}"

LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".ppt"})

PLAIN_TEXT_EXTENSIONS = {
    ".txt",
    ".text",
    ".md",
    ".markdown",
    ".mdown",
    ".mkd",
    ".rst",
    ".log",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".tex",
}


class ConversionError(RuntimeError):
    """Raised when a source file cannot be converted to text."""


@dataclass(frozen=True, slots=True)
class ConvertedDocument:
    pages: tuple[str, ...]
    format: str
    title: str | None = None
    encoding: str | None = None


class _HtmlTextExtractor(HTMLParser):
    """A tolerant HTML-to-text extractor with block-level line breaks."""

    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }
    _SKIP_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\t")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.parts.append(data)

    def get_title(self) -> str | None:
        title = " ".join("".join(self.title_parts).split()).strip()
        return title or None


def supported_extension(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _clean_page(page: str) -> str:
    page = page.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [line.rstrip() for line in page.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _clean_pages(pages: list[str]) -> tuple[str, ...]:
    if not pages:
        return ("",)
    return tuple(_clean_page(page) for page in pages)


def _split_text_pages(text: str) -> tuple[str, ...]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pages = text.split("\f")
    # A page separator at the very end (often followed by a newline from the
    # converter) does not represent another page. A genuinely blank final page
    # is preserved as one additional form feed ("a\f\f" -> ["a", ""]).
    if len(pages) > 1 and not pages[-1].strip():
        pages.pop()
    return _clean_pages(pages)


def _decode_bytes(data: bytes) -> tuple[str, str]:
    """Decode a text file using a small, dependency-free fallback list."""

    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith(b"\xff\xfe\x00\x00") or data.startswith(b"\x00\x00\xfe\xff"):
        return data.decode("utf-32"), "utf-32"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"

    for encoding in ("utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    # latin-1 never fails for bytes, but keep a clear terminal error anyway.
    raise ConversionError("无法判断文本编码，请先把文件转成 UTF-8")


def _read_text_file(path: Path) -> tuple[str, str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ConversionError(f"无法读取文件: {exc}") from exc
    text, encoding = _decode_bytes(data)
    return text, encoding


def _convert_plain(path: Path) -> ConvertedDocument:
    text, encoding = _read_text_file(path)
    return ConvertedDocument(
        pages=_split_text_pages(text),
        format=path.suffix.lower().lstrip("."),
        encoding=encoding,
    )


def _convert_html(path: Path) -> ConvertedDocument:
    text, encoding = _read_text_file(path)
    parser = _HtmlTextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # HTMLParser is tolerant, but be defensive.
        raise ConversionError(f"HTML 解析失败: {exc}") from exc
    return ConvertedDocument(
        pages=_split_text_pages("".join(parser.parts)),
        format="html",
        title=parser.get_title(),
        encoding=encoding,
    )


def _core_title(archive: zipfile.ZipFile) -> str | None:
    try:
        raw = archive.read("docProps/core.xml")
    except KeyError:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    for element in root.iter():
        if element.tag == DC + "title" and element.text:
            title = " ".join(element.text.split()).strip()
            if title:
                return title
    return None


def _inline_text(paragraph: ET.Element) -> str:
    """Extract text from a Word paragraph, ignoring page-break side effects."""

    parts: list[str] = []
    for node in paragraph.iter():
        tag = node.tag
        if tag == W + "t":
            parts.append(node.text or "")
        elif tag == W + "tab":
            parts.append("\t")
        elif tag in {W + "br", W + "cr"}:
            parts.append("\n")
        elif tag == W + "noBreakHyphen":
            parts.append("-")
        elif tag == W + "softHyphen":
            parts.append("")
    return "".join(parts)


class _DocxTextExtractor:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self._page_has_text = False

    def _write(self, text: str) -> None:
        if not text:
            return
        self.parts.append(text)
        if text.strip():
            self._page_has_text = True

    def _page_break(self) -> None:
        if not self._page_has_text:
            return
        self.parts.append("\f")
        self._page_has_text = False

    def extract(self, root: ET.Element) -> str:
        body = root.find(W + "body")
        if body is None:
            raise ConversionError("DOCX 中缺少 word/document.xml 的正文")
        self._render_blocks(body)
        return "".join(self.parts)

    def _render_blocks(self, container: ET.Element) -> None:
        for child in list(container):
            if child.tag == W + "p":
                self._render_paragraph(child)
            elif child.tag == W + "tbl":
                self._render_table(child)
            elif child.tag == W + "sectPr":
                continue
            elif child.find(".//" + W + "p") is not None or child.find(
                ".//" + W + "tbl"
            ) is not None:
                # Handles content controls and other block wrappers.
                self._render_blocks(child)

    def _render_paragraph(self, paragraph: ET.Element) -> None:
        props = paragraph.find(W + "pPr")
        if props is not None and props.find(W + "pageBreakBefore") is not None:
            self._page_break()

        for node in paragraph.iter():
            tag = node.tag
            if tag == W + "t":
                self._write(node.text or "")
            elif tag == W + "tab":
                self._write("\t")
            elif tag == W + "br":
                if node.get(W + "type") == "page":
                    self._page_break()
                else:
                    self._write("\n")
            elif tag == W + "cr":
                self._write("\n")
            elif tag == W + "lastRenderedPageBreak":
                self._page_break()
            elif tag == W + "noBreakHyphen":
                self._write("-")
        self._write("\n")

    def _render_table(self, table: ET.Element) -> None:
        for row in table.findall(W + "tr"):
            cells: list[str] = []
            for cell in row.findall(W + "tc"):
                paragraph_texts: list[str] = []
                for paragraph in cell.iter(W + "p"):
                    text = _inline_text(paragraph).strip()
                    if text:
                        paragraph_texts.append(" ".join(text.splitlines()))
                cells.append(" ".join(paragraph_texts).strip())
            self._write("	".join(cells) + "\n")


def _convert_docx(path: Path) -> ConvertedDocument:
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                raw = archive.read("word/document.xml")
            except KeyError as exc:
                raise ConversionError(
                    "不是有效的 DOCX：缺少 word/document.xml"
                ) from exc
            title = _core_title(archive)
    except zipfile.BadZipFile as exc:
        raise ConversionError("不是有效的 DOCX（ZIP 结构损坏）") from exc
    except OSError as exc:
        raise ConversionError(f"无法读取 DOCX: {exc}") from exc

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ConversionError(f"DOCX 文档 XML 解析失败: {exc}") from exc

    text = _DocxTextExtractor().extract(root)
    return ConvertedDocument(
        pages=_split_text_pages(text),
        format="docx",
        title=title,
    )


def _pptx_slide_paths(archive: zipfile.ZipFile) -> list[str]:
    names = set(archive.namelist())
    ordered: list[str] = []

    try:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        rels_root = ET.fromstring(
            archive.read("ppt/_rels/presentation.xml.rels")
        )
        rels: dict[str, str] = {}
        for rel in rels_root:
            rel_id = rel.get("Id")
            target = rel.get("Target")
            if rel_id and target:
                rels[rel_id] = target

        for slide_id in presentation.iter(P + "sldId"):
            rel_id = slide_id.get(R + "id")
            if not rel_id or rel_id not in rels:
                continue
            target = rels[rel_id].replace("\\", "/")
            if target.startswith("/"):
                target = target.lstrip("/")
            else:
                target = posixpath.normpath(posixpath.join("ppt", target))
            if target in names and target not in ordered:
                ordered.append(target)
    except (KeyError, ET.ParseError):
        ordered = []

    if not ordered:
        slide_pattern = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
        candidates: list[tuple[int, str]] = []
        for name in names:
            match = slide_pattern.match(name)
            if match:
                candidates.append((int(match.group(1)), name))
        ordered = [name for _, name in sorted(candidates)]

    return ordered


def _pptx_paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == A + "t":
            parts.append(node.text or "")
        elif node.tag == A + "br":
            parts.append("\n")
        elif node.tag == A + "tab":
            parts.append("\t")
    return "".join(parts)


def _convert_pptx(path: Path) -> ConvertedDocument:
    try:
        with zipfile.ZipFile(path) as archive:
            slide_paths = _pptx_slide_paths(archive)
            if not slide_paths:
                raise ConversionError("PPTX 中没有找到幻灯片")
            title = _core_title(archive)
            pages: list[str] = []
            for slide_path in slide_paths:
                try:
                    root = ET.fromstring(archive.read(slide_path))
                except KeyError as exc:
                    raise ConversionError(f"PPTX 缺少幻灯片: {slide_path}") from exc
                except ET.ParseError as exc:
                    raise ConversionError(
                        f"PPTX 幻灯片 XML 解析失败 ({slide_path}): {exc}"
                    ) from exc
                lines: list[str] = []
                for paragraph in root.iter(A + "p"):
                    line = _pptx_paragraph_text(paragraph).strip()
                    if line:
                        lines.append(line)
                pages.append("\n".join(lines))
    except zipfile.BadZipFile as exc:
        raise ConversionError("不是有效的 PPTX（ZIP 结构损坏）") from exc
    except OSError as exc:
        raise ConversionError(f"无法读取 PPTX: {exc}") from exc

    return ConvertedDocument(
        pages=_clean_pages(pages),
        format="pptx",
        title=title,
    )


def _pdf_pages(path: Path) -> tuple[str, ...]:
    executable = shutil.which("pdftotext")
    if not executable:
        raise ConversionError(
            "PDF 转换需要 poppler-utils 中的 pdftotext，请先安装后再试"
        )

    try:
        result = subprocess.run(
            [executable, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConversionError(f"无法运行 pdftotext: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ConversionError(f"pdftotext 转换失败: {detail or '未知错误'}")

    text = result.stdout.decode("utf-8", errors="replace")
    return _split_text_pages(text)


def _convert_pdf(path: Path) -> ConvertedDocument:
    return ConvertedDocument(
        pages=_pdf_pages(path),
        format="pdf",
        encoding="utf-8",
    )


def _soffice_executable() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def _legacy_dependency_help() -> str:
    return (
        "旧版 .doc/.ppt 转换需要 LibreOffice 和 poppler-utils。"
        "Debian/Ubuntu 可安装：sudo apt-get install -y --no-install-recommends "
        "libreoffice-writer libreoffice-impress poppler-utils"
    )


def _convert_legacy_office(
    path: Path,
    temp_dir: Path,
) -> ConvertedDocument:
    """Convert binary Word/PowerPoint through LibreOffice's PDF exporter.

    The source is copied into a private work directory under .fileSearch/tmp so
    LibreOffice's lock files, user profile, caches and PDF output never land
    next to the source document. The PDF is then split into physical pages by
    pdftotext.
    """

    soffice = _soffice_executable()
    pdftotext = shutil.which("pdftotext")
    if not soffice or not pdftotext:
        raise ConversionError(_legacy_dependency_help())

    suffix = path.suffix.lower()
    temp_dir = temp_dir.expanduser().resolve()
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        work_dir = Path(
            tempfile.mkdtemp(prefix=f"legacy-{suffix[1:]}-", dir=str(temp_dir))
        )
    except OSError as exc:
        raise ConversionError(
            f"无法创建旧版 Office 转换临时目录 {temp_dir}: {exc}"
        ) from exc

    try:
        input_file = work_dir / f"input{suffix}"
        output_dir = work_dir / "out"
        output_dir.mkdir()
        home_dir = work_dir / "home"
        config_dir = work_dir / "config"
        cache_dir = work_dir / "cache"
        run_tmp_dir = work_dir / "tmp"
        for directory in (home_dir, config_dir, cache_dir, run_tmp_dir):
            directory.mkdir()
        profile_dir = work_dir / "profile"

        try:
            shutil.copyfile(path, input_file)
        except OSError as exc:
            raise ConversionError(f"无法复制旧版 Office 文件到临时目录: {exc}") from exc

        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home_dir),
                "XDG_CONFIG_HOME": str(config_dir),
                "XDG_CACHE_HOME": str(cache_dir),
                "TMPDIR": str(run_tmp_dir),
                "SAL_USE_VCLPLUGIN": "svp",
            }
        )
        command = [
            soffice,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
            "--norestore",
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(input_file),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=300,
                cwd=str(work_dir),
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(
                "LibreOffice 转换超时（300 秒），文件可能过大或已损坏"
            ) from exc
        except OSError as exc:
            raise ConversionError(f"无法运行 LibreOffice: {exc}") from exc

        pdf_files = sorted(output_dir.glob("*.pdf"))
        if result.returncode != 0 or not pdf_files:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            detail = stderr or stdout or "LibreOffice 没有生成 PDF"
            detail = " ".join(detail.split())
            if len(detail) > 500:
                detail = detail[:500] + "…"
            raise ConversionError(f"LibreOffice 转换失败: {detail}")

        pages = _pdf_pages(pdf_files[0])
        return ConvertedDocument(
            pages=pages,
            format=suffix.lstrip("."),
            encoding="utf-8",
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


_CONVERTERS: dict[str, Callable[[Path], ConvertedDocument]] = {
    ".docx": _convert_docx,
    ".pptx": _convert_pptx,
    ".pdf": _convert_pdf,
    ".html": _convert_html,
    ".htm": _convert_html,
}

for _suffix in PLAIN_TEXT_EXTENSIONS:
    _CONVERTERS[_suffix] = _convert_plain

SUPPORTED_EXTENSIONS = frozenset(_CONVERTERS) | LEGACY_OFFICE_EXTENSIONS


def convert_file(
    path: Path,
    temp_dir: Path | None = None,
) -> ConvertedDocument:
    """Convert one supported file to pages of plain text.

    temp_dir is supplied by DocumentStore so legacy Office conversion only
    writes its work files under .fileSearch/tmp. When called directly it falls
    back to ./.fileSearch/tmp in the current execution directory.
    """

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        accepted = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ConversionError(
            f"不支持的文件类型 {suffix or '(无扩展名)'}；支持: {accepted}"
        )
    if not path.is_file():
        raise ConversionError(f"文件不存在或不是普通文件: {path}")

    try:
        if suffix in LEGACY_OFFICE_EXTENSIONS:
            work_root = (
                temp_dir
                if temp_dir is not None
                else Path.cwd() / ".fileSearch" / "tmp"
            )
            converted = _convert_legacy_office(path, work_root)
        else:
            converted = _CONVERTERS[suffix](path)
    except ConversionError:
        raise
    except ET.ParseError as exc:
        raise ConversionError(f"文档 XML 解析失败: {exc}") from exc
    except Exception as exc:
        raise ConversionError(f"转换失败: {exc}") from exc

    if not converted.pages:
        converted = ConvertedDocument(
            pages=("",),
            format=converted.format,
            title=converted.title,
            encoding=converted.encoding,
        )
    return converted
