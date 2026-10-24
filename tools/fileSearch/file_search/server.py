"""JSON HTTP API for fileSearch."""

from __future__ import annotations

import json
import logging
import re
import socket
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .config import Config
from .search import (
    DEFAULT_CONTEXT_CHARS,
    DEFAULT_LIMIT,
    MAX_CONTEXT_CHARS,
    SearchError,
    search_documents,
)
from .store import DocumentNotFound, DocumentStore, StoreError

LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 1024 * 1024


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _query_first(query: Mapping[str, list[str]], *names: str) -> str | None:
    for name in names:
        values = query.get(name)
        if values:
            return values[0]
    return None


def _parse_int(
    value: Any,
    name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ApiError(400, "bad_request", f"{name} 必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "bad_request", f"{name} 必须是整数") from exc
    if isinstance(value, float) and value != parsed:
        raise ApiError(400, "bad_request", f"{name} 必须是整数")
    if minimum is not None and parsed < minimum:
        raise ApiError(400, "bad_request", f"{name} 必须 >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ApiError(400, "bad_request", f"{name} 必须 <= {maximum}")
    return parsed


def _parse_bool(value: Any, name: str, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    raise ApiError(400, "bad_request", f"{name} 必须是布尔值")


class FileSearchHandler(BaseHTTPRequestHandler):
    server_version = f"fileSearch/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------
    def log_message(self, fmt: str, *args: object) -> None:
        LOGGER.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(
            HTTPStatus.NO_CONTENT,
            None,
            extra_headers={
                "Allow": "GET, POST, DELETE, OPTIONS",
            },
        )

    def _handle(self, method: str) -> None:
        try:
            # TODO(auth): this tool deliberately has no authentication yet.
            # If auth is added, verify the request here before dispatching.
            self._dispatch(method)
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # pragma: no cover - defensive boundary
            LOGGER.exception("处理请求失败: %s %s", method, self.path)
            try:
                self._send_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "internal_error",
                    "服务器内部错误，请查看终端日志",
                )
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_json(
        self,
        status: int,
        payload: Any,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        if payload is None:
            body = b""
        else:
            body = (
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_error(self, status: int, code: str, message: str) -> None:
        self._send_json(
            status,
            {
                "error": {
                    "code": code,
                    "message": message,
                }
            },
        )

    def _read_json(self) -> Any:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in transfer_encoding:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "bad_request",
                "不支持 chunked 请求体，请提供 Content-Length",
            )
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(
                HTTPStatus.LENGTH_REQUIRED,
                "length_required",
                "POST 请求需要 Content-Length",
            )
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, "bad_request", "Content-Length 非法"
            ) from exc
        if length < 0:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, "bad_request", "Content-Length 非法"
            )
        if length > MAX_BODY_BYTES:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "payload_too_large",
                f"请求体不能超过 {MAX_BODY_BYTES} 字节",
            )
        body = self.rfile.read(length)
        if len(body) != length:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, "bad_request", "请求体不完整"
            )
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_json",
                f"请求体不是合法 UTF-8 JSON: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        query = parse_qs(parsed.query, keep_blank_values=True)

        if method == "OPTIONS":
            self.do_OPTIONS()
            return

        if method == "GET":
            if self._route_get(path, query):
                return
        elif method == "POST":
            if self._route_post(path, query):
                return
        elif method == "DELETE":
            if self._route_delete(path):
                return

        if self._is_known_path(path):
            raise ApiError(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "method_not_allowed",
                f"{path} 不支持 {method} 方法",
            )
        raise ApiError(HTTPStatus.NOT_FOUND, "not_found", f"接口不存在: {path}")

    def _is_known_path(self, path: str) -> bool:
        if path in {
            "/",
            "/health",
            "/api/health",
            "/api/config",
            "/api/files",
            "/api/search",
            "/api/import",
            "/api/reindex",
        }:
            return True
        return bool(
            re.fullmatch(r"/api/files/[^/]+", path)
            or re.fullmatch(r"/api/files/[^/]+/pages", path)
            or re.fullmatch(r"/api/files/[^/]+/pages/\d+", path)
            or re.fullmatch(r"/api/files/[^/]+/lines", path)
        )

    def _route_get(self, path: str, query: Mapping[str, list[str]]) -> bool:
        if path == "/":
            self._send_json(HTTPStatus.OK, self._api_index())
            return True
        if path in {"/health", "/api/health"}:
            self._send_json(HTTPStatus.OK, self._health_payload())
            return True
        if path == "/api/config":
            self._send_json(HTTPStatus.OK, self._config_payload())
            return True
        if path == "/api/files":
            documents = self.server.store.all_documents()
            self._send_json(
                HTTPStatus.OK,
                {
                    "count": len(documents),
                    "files": [
                        self.server.store.public_meta(doc.meta) for doc in documents
                    ],
                },
            )
            return True
        if path == "/api/search":
            self._search_from_query(query)
            return True

        match = re.fullmatch(r"/api/files/([^/]+)", path)
        if match:
            document = self._get_document(match.group(1))
            self._send_json(
                HTTPStatus.OK,
                {"file": self.server.store.public_meta(document.meta)},
            )
            return True

        match = re.fullmatch(r"/api/files/([^/]+)/pages", path)
        if match:
            document = self._get_document(match.group(1))
            pages = [
                {
                    "page": index + 1,
                    "line_count": len(document.lines[index]),
                    "char_count": len(document.pages[index]),
                }
                for index in range(len(document.pages))
            ]
            self._send_json(
                HTTPStatus.OK,
                {
                    "file_id": document.meta.id,
                    "file_name": document.meta.name,
                    "path": document.meta.relative_path,
                    "page_count": document.meta.page_count,
                    "pages": pages,
                },
            )
            return True

        match = re.fullmatch(r"/api/files/([^/]+)/pages/(\d+)", path)
        if match:
            document = self._get_document(match.group(1))
            page = int(match.group(2))
            self._send_page(document, page)
            return True

        match = re.fullmatch(r"/api/files/([^/]+)/lines", path)
        if match:
            document = self._get_document(match.group(1))
            self._send_lines(document, query)
            return True

        return False

    def _route_post(self, path: str, query: Mapping[str, list[str]]) -> bool:
        if path == "/api/search":
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise ApiError(400, "bad_request", "搜索请求必须是 JSON 对象")
            self._search_from_payload(payload)
            return True
        if path == "/api/import":
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise ApiError(400, "bad_request", "导入请求必须是 JSON 对象")
            self._import_payload(payload)
            return True
        if path == "/api/reindex":
            self._send_json(HTTPStatus.OK, self.server.store.reindex())
            return True
        return False

    def _route_delete(self, path: str) -> bool:
        match = re.fullmatch(r"/api/files/([^/]+)", path)
        if not match:
            return False
        doc_id = match.group(1)
        try:
            meta = self.server.store.delete(doc_id)
        except DocumentNotFound as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, "file_not_found", str(exc)) from exc
        except StoreError as exc:
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR, "store_error", str(exc)
            ) from exc
        self._send_json(
            HTTPStatus.OK,
            {
                "deleted": self.server.store.public_meta(meta),
                "source_preserved": True,
            },
        )
        return True

    # ------------------------------------------------------------------
    # Payloads
    # ------------------------------------------------------------------
    def _api_index(self) -> dict[str, Any]:
        return {
            "name": "fileSearch",
            "version": __version__,
            "description": "本地文件文档内容搜索 API",
            "authentication": {"enabled": False, "status": "todo"},
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/api/files",
                    "description": "列出已导入的文件",
                },
                {
                    "method": "GET",
                    "path": "/api/files/{id}",
                    "description": "查看单个文件信息",
                },
                {
                    "method": "GET",
                    "path": "/api/files/{id}/pages",
                    "description": "列出每页的行数和字符数",
                },
                {
                    "method": "GET",
                    "path": "/api/files/{id}/pages/{page}",
                    "description": "返回某一页的完整内容",
                },
                {
                    "method": "GET",
                    "path": "/api/files/{id}/lines?page=1&start=2&end=4",
                    "description": "返回某一页的一行或连续几行",
                },
                {
                    "method": "GET",
                    "path": "/api/search?q=关键词",
                    "description": "关键词搜索（支持跨行）",
                },
                {
                    "method": "POST",
                    "path": "/api/search",
                    "description": "关键词搜索，参数放在 JSON 中",
                },
                {
                    "method": "POST",
                    "path": "/api/import",
                    "description": "按路径导入文件或目录",
                },
                {
                    "method": "POST",
                    "path": "/api/reindex",
                    "description": "检查源文件变化并重建缓存",
                },
                {
                    "method": "DELETE",
                    "path": "/api/files/{id}",
                    "description": "仅从索引删除文件，不删除源文件",
                },
                {
                    "method": "GET",
                    "path": "/health",
                    "description": "健康检查",
                },
            ],
        }

    def _health_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "files": len(self.server.store.all_documents()),
            "uptime_seconds": round(time.monotonic() - self.server.started_at, 3),
        }

    def _config_payload(self) -> dict[str, Any]:
        config: Config = self.server.config
        return {
            "host": config.host,
            "port": self.server.server_address[1],
            "context_chars": config.context_chars,
            "data_dir": str(self.server.store.data_dir),
            "config_file": str(self.server.store.data_dir / "config.json"),
            "index_file": str(self.server.store.index_path),
            "cache_dir": str(self.server.store.cache_dir),
            "authentication": {"enabled": False, "status": "todo"},
        }

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def _search_from_query(self, query: Mapping[str, list[str]]) -> None:
        file_ref = _query_first(query, "file")
        documents = self._documents_for_filter(file_ref)
        context_value = _query_first(query, "context", "context_chars")
        page_value = _query_first(query, "page")
        self._run_search(
            documents,
            q=_query_first(query, "q", "query") or "",
            limit=_parse_int(
                _query_first(query, "limit"), "limit", default=DEFAULT_LIMIT
            ),
            offset=_parse_int(
                _query_first(query, "offset"), "offset", default=0
            ),
            context_chars=_parse_int(
                context_value,
                "context",
                default=self.server.config.context_chars,
                minimum=0,
                maximum=MAX_CONTEXT_CHARS,
            ),
            page=_parse_int(page_value, "page", default=None, minimum=1),
            case_sensitive=_parse_bool(
                _query_first(query, "case_sensitive"), "case_sensitive"
            ),
            file_ref=file_ref,
        )

    def _search_from_payload(self, payload: Mapping[str, Any]) -> None:
        file_ref = payload.get("file")
        if file_ref is not None and not isinstance(file_ref, str):
            raise ApiError(400, "bad_request", "file 必须是字符串")
        documents = self._documents_for_filter(file_ref)
        q = payload.get("q", payload.get("query", ""))
        if not isinstance(q, str):
            raise ApiError(400, "bad_request", "q 必须是字符串")
        self._run_search(
            documents,
            q=q,
            limit=_parse_int(
                payload.get("limit"), "limit", default=DEFAULT_LIMIT
            ),
            offset=_parse_int(payload.get("offset"), "offset", default=0),
            context_chars=_parse_int(
                payload.get("context", payload.get("context_chars")),
                "context",
                default=self.server.config.context_chars,
                minimum=0,
                maximum=MAX_CONTEXT_CHARS,
            ),
            page=_parse_int(payload.get("page"), "page", default=None, minimum=1),
            case_sensitive=_parse_bool(
                payload.get("case_sensitive"), "case_sensitive"
            ),
            file_ref=file_ref,
        )

    def _documents_for_filter(self, file_ref: str | None) -> tuple[Any, ...]:
        if not file_ref:
            return self.server.store.snapshot()
        matches = self.server.store.find(file_ref)
        if not matches:
            raise ApiError(
                HTTPStatus.NOT_FOUND,
                "file_not_found",
                f"找不到文件: {file_ref}",
            )
        return tuple(matches)

    def _run_search(
        self,
        documents: tuple[Any, ...],
        *,
        q: str,
        limit: int | None,
        offset: int | None,
        context_chars: int | None,
        page: int | None,
        case_sensitive: bool,
        file_ref: str | None,
    ) -> None:
        try:
            result = search_documents(
                documents,
                q,
                limit=limit if limit is not None else DEFAULT_LIMIT,
                offset=offset or 0,
                context_chars=(
                    context_chars
                    if context_chars is not None
                    else DEFAULT_CONTEXT_CHARS
                ),
                page=page,
                case_sensitive=case_sensitive,
            )
        except SearchError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "bad_request", str(exc)) from exc
        if file_ref:
            result["file"] = file_ref
        self._send_json(HTTPStatus.OK, result)

    # ------------------------------------------------------------------
    # Import / pages
    # ------------------------------------------------------------------
    def _import_payload(self, payload: Mapping[str, Any]) -> None:
        if "paths" in payload and "path" in payload:
            raise ApiError(
                400, "bad_request", "path 与 paths 只能提供一个"
            )
        raw_paths = payload.get("paths", payload.get("path"))
        if isinstance(raw_paths, str):
            paths = [raw_paths]
        elif isinstance(raw_paths, list) and raw_paths:
            if not all(isinstance(item, str) for item in raw_paths):
                raise ApiError(400, "bad_request", "paths 必须都是字符串")
            paths = raw_paths
        else:
            raise ApiError(400, "bad_request", "需要提供 path 或非空 paths 数组")

        recursive = _parse_bool(payload.get("recursive"), "recursive", default=True)
        try:
            imported, errors = self.server.store.import_paths(
                paths, recursive=recursive
            )
        except StoreError as exc:
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR, "store_error", str(exc)
            ) from exc

        response = {
            "count": len(imported),
            "imported": [
                self.server.store.public_meta(meta) for meta in imported
            ],
            "errors": errors,
        }
        status = HTTPStatus.OK if imported else HTTPStatus.BAD_REQUEST
        self._send_json(status, response)

    def _get_document(self, doc_id: str) -> Any:
        try:
            return self.server.store.get(doc_id)
        except DocumentNotFound as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, "file_not_found", str(exc)) from exc

    def _send_page(self, document: Any, page_number: int) -> None:
        page_count = len(document.pages)
        if not 1 <= page_number <= page_count:
            raise ApiError(
                HTTPStatus.NOT_FOUND,
                "page_not_found",
                f"页码超出范围: {page_number}（文件共 {page_count} 页）",
            )
        page_lines = document.lines[page_number - 1]
        page_text = document.pages[page_number - 1]
        self._send_json(
            HTTPStatus.OK,
            {
                "file_id": document.meta.id,
                "file_name": document.meta.name,
                "path": document.meta.relative_path,
                "page": page_number,
                "page_count": page_count,
                "line_count": len(page_lines),
                "char_count": len(page_text),
                "lines": [
                    {"line": index + 1, "text": text}
                    for index, text in enumerate(page_lines)
                ],
                "content": page_text,
            },
        )

    def _send_lines(
        self,
        document: Any,
        query: Mapping[str, list[str]],
    ) -> None:
        page_number = _parse_int(
            _query_first(query, "page"), "page", default=1, minimum=1
        )
        assert page_number is not None
        page_count = len(document.pages)
        if page_number > page_count:
            raise ApiError(
                HTTPStatus.NOT_FOUND,
                "page_not_found",
                f"页码超出范围: {page_number}（文件共 {page_count} 页）",
            )

        page_lines = document.lines[page_number - 1]
        total_lines = len(page_lines)
        line_value = _query_first(query, "line")
        start_value = _query_first(query, "start")
        end_value = _query_first(query, "end")

        if line_value is not None and (start_value is not None or end_value is not None):
            raise ApiError(
                400,
                "bad_request",
                "line 不能与 start/end 同时使用",
            )

        if line_value is not None:
            line_number = _parse_int(line_value, "line", minimum=1)
            assert line_number is not None
            if total_lines == 0 or line_number > total_lines:
                raise ApiError(
                    HTTPStatus.NOT_FOUND,
                    "line_not_found",
                    f"行号超出范围: {line_number}（本页共 {total_lines} 行）",
                )
            start_line = end_line = line_number
        else:
            if total_lines == 0:
                if start_value is not None or end_value is not None:
                    raise ApiError(
                        HTTPStatus.NOT_FOUND,
                        "line_not_found",
                        "本页没有文本行",
                    )
                start_line = end_line = 0
            else:
                start_line = (
                    _parse_int(start_value, "start", default=1, minimum=1)
                    if start_value is not None
                    else 1
                )
                assert start_line is not None
                end_default = start_line if start_value is not None else total_lines
                end_line = (
                    _parse_int(
                        end_value, "end", default=end_default, minimum=1
                    )
                    if end_value is not None
                    else end_default
                )
                assert end_line is not None
                if end_line < start_line:
                    raise ApiError(
                        400, "bad_request", "end 必须 >= start"
                    )
                if start_line > total_lines or end_line > total_lines:
                    raise ApiError(
                        HTTPStatus.NOT_FOUND,
                        "line_not_found",
                        f"行号超出范围: {start_line}..{end_line}（本页共 {total_lines} 行）",
                    )

        selected = [
            {"line": index + 1, "text": page_lines[index]}
            for index in range(start_line - 1, end_line)
        ] if start_line else []

        self._send_json(
            HTTPStatus.OK,
            {
                "file_id": document.meta.id,
                "file_name": document.meta.name,
                "path": document.meta.relative_path,
                "page": page_number,
                "page_count": page_count,
                "start_line": start_line,
                "end_line": end_line,
                "total_lines": total_lines,
                "line_count": len(selected),
                "lines": selected,
                "content": "\n".join(item["text"] for item in selected),
            },
        )


class FileSearchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False

    def __init__(
        self,
        server_address: tuple[str, int],
        store: DocumentStore,
        config: Config,
    ) -> None:
        self.store = store
        self.config = config
        self.started_at = time.monotonic()
        super().__init__(server_address, FileSearchHandler)


class FileSearchHTTPServerV6(FileSearchHTTPServer):
    address_family = socket.AF_INET6


def create_server(
    store: DocumentStore,
    config: Config,
    host: str | None = None,
    port: int | None = None,
) -> FileSearchHTTPServer:
    listen_host = host if host is not None else config.host
    listen_port = port if port is not None else config.port
    server_class = (
        FileSearchHTTPServerV6 if ":" in listen_host else FileSearchHTTPServer
    )
    server = server_class((listen_host, listen_port), store, config)
    server.started_at = time.monotonic()
    return server


def server_url(server: FileSearchHTTPServer) -> str:
    host, port = server.server_address[:2]
    if ":" in str(host):
        return f"http://[{host}]:{port}"
    return f"http://{host}:{port}"
