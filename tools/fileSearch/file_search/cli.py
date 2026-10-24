"""Command-line entry point for fileSearch."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import (
    DEFAULT_CONTEXT_CHARS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    Config,
    ConfigError,
)
from .server import create_server, server_url
from .store import DocumentStore, StoreError

LOGGER = logging.getLogger(__name__)
DEFAULT_DATA_DIRNAME = ".fileSearch"


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1..65535 之间")
    return port


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是整数") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("必须 >= 0")
    return number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fileSearch.py",
        description=(
            "本地文件文档搜索 API。默认读取 ./.fileSearch/config.json，"
            "没有配置文件时使用 127.0.0.1:5872。"
        ),
    )
    parser.add_argument(
        "--host",
        metavar="IP",
        help=f"监听地址（默认取配置，首次为 {DEFAULT_HOST}）",
    )
    parser.add_argument(
        "--port",
        type=_port,
        metavar="PORT",
        help=f"监听端口（默认取配置，首次为 {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--context-chars",
        type=_non_negative_int,
        metavar="N",
        help=f"搜索结果上下文窗口（默认取配置，首次为 {DEFAULT_CONTEXT_CHARS}）",
    )
    parser.add_argument(
        "--import",
        dest="imports",
        action="append",
        nargs="+",
        metavar="PATH",
        help="启动前导入文件或目录，可重复指定；目录会递归扫描",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="启动前检查源文件变化并重建缓存",
    )
    parser.add_argument(
        "--data-dir",
        metavar="DIR",
        help=f"数据目录，默认是执行目录下的 {DEFAULT_DATA_DIRNAME}",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging()

    root = Path.cwd().resolve()
    data_dir = (
        Path(args.data_dir).expanduser().resolve()
        if args.data_dir
        else root / DEFAULT_DATA_DIRNAME
    )
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"无法创建数据目录 {data_dir}: {exc}", file=sys.stderr)
        return 1

    config_path = data_dir / "config.json"
    try:
        config = Config.load(config_path)
        if args.host is not None:
            config.host = args.host
        if args.port is not None:
            config.port = args.port
        if args.context_chars is not None:
            config.context_chars = args.context_chars
        config.validate()
        config.save(config_path, tmp_dir=data_dir / "tmp")
    except (ConfigError, OSError) as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    try:
        store = DocumentStore(root=root, data_dir=data_dir)
    except StoreError as exc:
        print(f"索引错误: {exc}", file=sys.stderr)
        return 2

    if args.reindex:
        result = store.reindex()
        LOGGER.info(
            "重建索引: 检查 %d，更新 %d，未变 %d，源文件缺失 %d，错误 %d",
            result["scanned"],
            result["updated"],
            result["unchanged"],
            result["missing"],
            len(result["errors"]),
        )
        for error in result["errors"]:
            LOGGER.warning("%s: %s", error["path"], error["message"])

    import_paths = [
        item for group in (args.imports or []) for item in group
    ]
    if import_paths:
        imported, errors = store.import_paths(import_paths, recursive=True)
        LOGGER.info("启动前导入 %d 个文件", len(imported))
        for error in errors:
            LOGGER.warning("导入失败 %s: %s", error["path"], error["message"])

    try:
        server = create_server(store, config)
    except OSError as exc:
        print(
            f"无法监听 {config.host}:{config.port}: {exc}\n"
            "端口可能已被占用，可用 --port 换一个端口。",
            file=sys.stderr,
        )
        return 1

    if config.host not in {"127.0.0.1", "::1", "localhost"}:
        LOGGER.warning(
            "API 当前没有鉴权，且监听在 %s；请只在对可信网络开放时这样使用。",
            config.host,
        )

    LOGGER.info("fileSearch %s", __version__)
    LOGGER.info("数据目录: %s", data_dir)
    LOGGER.info("已加载文件: %d", len(store.all_documents()))
    LOGGER.info("监听地址: %s", server_url(server))
    LOGGER.info("API 首页: %s/", server_url(server))
    LOGGER.info("按 Ctrl+C 停止")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
