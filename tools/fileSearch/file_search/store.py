"""Persistent cache and in-memory document index for fileSearch."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Sequence

from .converters import (
    ConversionError,
    ConvertedDocument,
    convert_file,
    supported_extension,
)
from .utils import atomic_write_text, document_id, sha256_file, utc_now

LOGGER = logging.getLogger(__name__)
INDEX_VERSION = 1
DOC_ID_RE = re.compile(r"^[0-9a-f]{24}$")


class StoreError(RuntimeError):
    """Base error for index/cache operations."""


class DocumentNotFound(StoreError):
    """Raised when a document id is not known."""


@dataclass(slots=True)
class DocumentMeta:
    id: str
    name: str
    path: str
    relative_path: str
    format: str
    size: int
    mtime_ns: int
    sha256: str
    page_count: int
    line_count: int
    char_count: int
    title: str | None
    encoding: str | None
    imported_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "DocumentMeta":
        if not isinstance(raw, dict):
            raise StoreError("索引中的文件条目不是 JSON 对象")

        required = {
            "id": str,
            "name": str,
            "path": str,
            "relative_path": str,
            "format": str,
            "size": int,
            "mtime_ns": int,
            "sha256": str,
            "page_count": int,
            "line_count": int,
            "char_count": int,
            "imported_at": str,
            "updated_at": str,
        }
        values: dict[str, Any] = {}
        for key, expected in required.items():
            value = raw.get(key)
            if not isinstance(value, expected) or isinstance(value, bool):
                raise StoreError(f"索引字段 {key!r} 缺失或类型错误")
            values[key] = value
        if not DOC_ID_RE.fullmatch(values["id"]):
            raise StoreError(f"索引中存在非法文件 id: {values['id']!r}")

        title = raw.get("title")
        encoding = raw.get("encoding")
        if title is not None and not isinstance(title, str):
            raise StoreError("索引字段 'title' 类型错误")
        if encoding is not None and not isinstance(encoding, str):
            raise StoreError("索引字段 'encoding' 类型错误")
        values["title"] = title
        values["encoding"] = encoding
        return cls(**values)


@dataclass(frozen=True, slots=True)
class Document:
    meta: DocumentMeta
    pages: tuple[str, ...]
    lines: tuple[tuple[str, ...], ...]


def _lines_for_pages(pages: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(page.split("\n")) if page else () for page in pages)


def _counts(pages: Sequence[str]) -> tuple[int, int, int]:
    line_count = sum(len(page.split("\n")) if page else 0 for page in pages)
    char_count = sum(len(page) for page in pages)
    return len(pages), line_count, char_count


class DocumentStore:
    """Owns .fileSearch/index.json, cache/*.txt and the in-memory index."""

    def __init__(self, root: Path, data_dir: Path) -> None:
        self.root = root.expanduser().resolve()
        self.data_dir = data_dir.expanduser().resolve()
        self.cache_dir = self.data_dir / "cache"
        self.tmp_dir = self.data_dir / "tmp"
        self.index_path = self.data_dir / "index.json"

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self._lock = RLock()
        self._documents: dict[str, Document] = {}
        self._load_index()

    # ------------------------------------------------------------------
    # Cache paths and persistence
    # ------------------------------------------------------------------
    def cache_path(self, doc_id: str) -> Path:
        if not DOC_ID_RE.fullmatch(doc_id):
            raise StoreError(f"非法文件 id: {doc_id!r}")
        return self.cache_dir / f"{doc_id}.txt"

    def _save_index_locked(self) -> None:
        documents = sorted(
            self._documents.values(),
            key=lambda doc: (doc.meta.relative_path.casefold(), doc.meta.name.casefold()),
        )
        payload = {
            "version": INDEX_VERSION,
            "updated_at": utc_now(),
            "documents": [doc.meta.to_dict() for doc in documents],
        }
        atomic_write_text(
            self.index_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            tmp_dir=self.tmp_dir,
        )

    def _load_index(self) -> None:
        if not self.index_path.exists():
            return

        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise StoreError(f"无法读取索引 {self.index_path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise StoreError(f"索引必须使用 UTF-8 编码 {self.index_path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise StoreError(f"索引不是合法 JSON {self.index_path}: {exc}") from exc

        if not isinstance(raw, dict) or raw.get("version") != INDEX_VERSION:
            raise StoreError(
                f"不支持的索引格式或版本: {self.index_path}（当前支持 {INDEX_VERSION}）"
            )
        entries = raw.get("documents")
        if not isinstance(entries, list):
            raise StoreError(f"索引缺少 documents 数组: {self.index_path}")

        changed = False
        for entry in entries:
            meta = DocumentMeta.from_dict(entry)
            cache_file = self.cache_path(meta.id)
            cache_text: str | None = None

            if cache_file.is_file():
                try:
                    cache_text = cache_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    LOGGER.warning("读取缓存失败 %s: %s", cache_file, exc)
            if cache_text is None:
                source = Path(meta.path)
                if not source.is_file() or not supported_extension(source):
                    LOGGER.warning(
                        "缓存与源文件均不可用，忽略文件: %s", meta.relative_path
                    )
                    changed = True
                    continue
                try:
                    meta, document, cache_text = self._prepare_document(source, meta)
                    atomic_write_text(
                        cache_file, cache_text, tmp_dir=self.tmp_dir
                    )
                    changed = True
                except (ConversionError, OSError, StoreError) as exc:
                    LOGGER.warning(
                        "重建缓存失败，忽略文件 %s: %s", meta.relative_path, exc
                    )
                    changed = True
                    continue

            pages = tuple(cache_text.split("\f"))
            _, line_count, char_count = _counts(pages)
            if (
                meta.page_count != len(pages)
                or meta.line_count != line_count
                or meta.char_count != char_count
            ):
                meta.page_count = len(pages)
                meta.line_count = line_count
                meta.char_count = char_count
                changed = True

            document = Document(
                meta=meta,
                pages=pages,
                lines=_lines_for_pages(pages),
            )
            if meta.id in self._documents:
                raise StoreError(f"索引中存在重复的文件 id: {meta.id}")
            self._documents[meta.id] = document

        if changed:
            self._save_index_locked()
        LOGGER.info("已加载 %d 个文件到内存", len(self._documents))

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------
    def _relative_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def _prepare_document(
        self,
        path: Path,
        existing: DocumentMeta | None = None,
    ) -> tuple[DocumentMeta, Document, str]:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise StoreError(f"文件不存在或不是普通文件: {path}")
        if not supported_extension(resolved):
            raise ConversionError(f"不支持的文件类型: {resolved.suffix or '(无扩展名)'}")

        converted: ConvertedDocument = convert_file(
            resolved, temp_dir=self.tmp_dir
        )
        stat = resolved.stat()
        digest = sha256_file(resolved)
        now = utc_now()
        doc_id = document_id(resolved)
        imported_at = existing.imported_at if existing else now
        pages = converted.pages
        page_count, line_count, char_count = _counts(pages)

        meta = DocumentMeta(
            id=doc_id,
            name=resolved.name,
            path=str(resolved),
            relative_path=self._relative_path(resolved),
            format=converted.format,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            sha256=digest,
            page_count=page_count,
            line_count=line_count,
            char_count=char_count,
            title=converted.title,
            encoding=converted.encoding,
            imported_at=imported_at,
            updated_at=now,
        )
        document = Document(
            meta=meta,
            pages=pages,
            lines=_lines_for_pages(pages),
        )
        return meta, document, "\f".join(pages)

    def _commit_document(
        self,
        document: Document,
        cache_text: str,
    ) -> None:
        cache_file = self.cache_path(document.meta.id)
        atomic_write_text(cache_file, cache_text, tmp_dir=self.tmp_dir)
        with self._lock:
            self._documents[document.meta.id] = document
            self._save_index_locked()

    # ------------------------------------------------------------------
    # Import / reindex / delete
    # ------------------------------------------------------------------
    def _is_inside_data_dir(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.data_dir)
            return True
        except ValueError:
            return False

    def _expand_import_paths(
        self,
        raw_paths: Sequence[str],
        recursive: bool,
    ) -> tuple[list[Path], list[dict[str, str]]]:
        files: dict[str, Path] = {}
        errors: list[dict[str, str]] = []

        for raw in raw_paths:
            if not isinstance(raw, str) or not raw.strip():
                errors.append({"path": str(raw), "message": "路径必须是非空字符串"})
                continue
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = self.root / candidate
            try:
                candidate = candidate.resolve()
            except OSError as exc:
                errors.append({"path": raw, "message": f"无法解析路径: {exc}"})
                continue

            if self._is_inside_data_dir(candidate):
                errors.append(
                    {"path": raw, "message": "不能导入 .fileSearch 数据目录内部的文件"}
                )
                continue
            if candidate.is_file():
                if supported_extension(candidate):
                    files[str(candidate)] = candidate
                else:
                    errors.append(
                        {
                            "path": raw,
                            "message": f"不支持的文件类型: {candidate.suffix or '(无扩展名)'}",
                        }
                    )
                continue
            if not candidate.is_dir():
                errors.append({"path": raw, "message": "路径不存在"})
                continue

            found_any = False
            walk_errors: list[OSError] = []

            def onerror(exc: OSError) -> None:
                walk_errors.append(exc)

            if recursive:
                walker = os.walk(candidate, followlinks=False, onerror=onerror)
            else:
                try:
                    walker = [
                        (
                            str(candidate),
                            [],
                            [
                                entry.name
                                for entry in candidate.iterdir()
                                if entry.is_file()
                            ],
                        )
                    ]
                except OSError as exc:
                    errors.append({"path": raw, "message": f"无法读取目录: {exc}"})
                    continue

            for dirpath, dirnames, filenames in walker:
                current = Path(dirpath)
                kept_dirs: list[str] = []
                for dirname in dirnames:
                    child = current / dirname
                    if dirname in {".git", ".hg", ".svn", "__pycache__"}:
                        continue
                    if self._is_inside_data_dir(child):
                        continue
                    kept_dirs.append(dirname)
                dirnames[:] = kept_dirs

                for filename in filenames:
                    path = current / filename
                    if self._is_inside_data_dir(path):
                        continue
                    if supported_extension(path):
                        try:
                            resolved = path.resolve()
                        except OSError:
                            continue
                        files[str(resolved)] = resolved
                        found_any = True

            for exc in walk_errors:
                errors.append({"path": raw, "message": f"读取目录时出错: {exc}"})
            if not found_any and not walk_errors:
                errors.append(
                    {
                        "path": raw,
                        "message": "目录中没有可导入的受支持文件",
                    }
                )

        return [files[key] for key in sorted(files)], errors

    def import_paths(
        self,
        raw_paths: Sequence[str],
        recursive: bool = True,
    ) -> tuple[list[DocumentMeta], list[dict[str, str]]]:
        paths, errors = self._expand_import_paths(raw_paths, recursive)
        imported: list[DocumentMeta] = []

        for path in paths:
            with self._lock:
                existing_doc = self._documents.get(document_id(path))
                existing_meta = existing_doc.meta if existing_doc else None
            try:
                meta, document, cache_text = self._prepare_document(
                    path, existing_meta
                )
                self._commit_document(document, cache_text)
                imported.append(meta)
                LOGGER.info(
                    "已导入 %s（%d 页 / %d 行）",
                    meta.relative_path,
                    meta.page_count,
                    meta.line_count,
                )
            except (ConversionError, StoreError, OSError) as exc:
                LOGGER.warning("导入失败 %s: %s", path, exc)
                errors.append({"path": str(path), "message": str(exc)})

        return imported, errors

    def reindex(self) -> dict[str, Any]:
        with self._lock:
            snapshot = [doc.meta for doc in self._documents.values()]

        updated = 0
        unchanged = 0
        missing = 0
        errors: list[dict[str, str]] = []

        for old_meta in snapshot:
            source = Path(old_meta.path)
            if not source.is_file():
                missing += 1
                continue
            try:
                if not supported_extension(source):
                    raise ConversionError("文件类型已不再受支持")
                if sha256_file(source) == old_meta.sha256:
                    unchanged += 1
                    continue
                meta, document, cache_text = self._prepare_document(source, old_meta)
                with self._lock:
                    current = self._documents.get(meta.id)
                    if current is not None and current.meta.sha256 == meta.sha256:
                        unchanged += 1
                        continue
                    cache_file = self.cache_path(meta.id)
                    atomic_write_text(cache_file, cache_text, tmp_dir=self.tmp_dir)
                    self._documents[meta.id] = document
                    updated += 1
            except (ConversionError, StoreError, OSError) as exc:
                LOGGER.warning("重建索引失败 %s: %s", old_meta.relative_path, exc)
                errors.append(
                    {"path": old_meta.relative_path, "message": str(exc)}
                )

        with self._lock:
            if updated:
                self._save_index_locked()

        return {
            "scanned": len(snapshot),
            "updated": updated,
            "unchanged": unchanged,
            "missing": missing,
            "errors": errors,
        }

    def delete(self, doc_id: str) -> DocumentMeta:
        with self._lock:
            document = self._documents.pop(doc_id, None)
            if document is None:
                raise DocumentNotFound(f"找不到文件: {doc_id}")
            self._save_index_locked()

        cache_file = self.cache_path(doc_id)
        try:
            cache_file.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            LOGGER.warning("删除缓存失败 %s: %s", cache_file, exc)
        return document.meta

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def all_documents(self) -> list[Document]:
        with self._lock:
            documents = list(self._documents.values())
        return sorted(
            documents,
            key=lambda doc: (doc.meta.relative_path.casefold(), doc.meta.name.casefold()),
        )

    def snapshot(self) -> tuple[Document, ...]:
        return tuple(self.all_documents())

    def get(self, doc_id: str) -> Document:
        with self._lock:
            document = self._documents.get(doc_id)
        if document is None:
            raise DocumentNotFound(f"找不到文件: {doc_id}")
        return document

    def find(self, reference: str) -> list[Document]:
        """Find by id, relative path, absolute path, or exact file name."""

        with self._lock:
            documents = list(self._documents.values())

        direct = [doc for doc in documents if doc.meta.id == reference]
        if direct:
            return direct

        candidates: list[Document] = []
        resolved: str | None = None
        try:
            candidate = Path(reference).expanduser()
            if not candidate.is_absolute():
                candidate = self.root / candidate
            resolved = str(candidate.resolve())
        except OSError:
            resolved = None

        for doc in documents:
            if reference in {doc.meta.relative_path, doc.meta.path, doc.meta.name}:
                candidates.append(doc)
            elif resolved is not None and doc.meta.path == resolved:
                candidates.append(doc)
        return candidates

    def public_meta(self, meta: DocumentMeta) -> dict[str, Any]:
        data = meta.to_dict()
        data["cache_file"] = str(self.cache_path(meta.id))
        data["source_exists"] = Path(meta.path).is_file()
        return data
