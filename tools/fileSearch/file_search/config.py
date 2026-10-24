"""Persistent fileSearch configuration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .utils import atomic_write_text

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5872
DEFAULT_CONTEXT_CHARS = 60
CONFIG_VERSION = 1


class ConfigError(ValueError):
    """Raised when a config file or a config value is invalid."""


@dataclass(slots=True)
class Config:
    """User-editable settings persisted in .fileSearch/config.json."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    context_chars: int = DEFAULT_CONTEXT_CHARS
    version: int = CONFIG_VERSION

    def validate(self) -> None:
        if not isinstance(self.host, str):
            raise ConfigError("host 必须是字符串")
        self.host = self.host.strip()
        if not self.host or any(ch.isspace() for ch in self.host):
            raise ConfigError("host 不能为空，也不能包含空白字符")
        if "/" in self.host or "://" in self.host:
            raise ConfigError("host 只填写监听地址，不要带协议或路径")

        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise ConfigError("port 必须是整数")
        if not 1 <= self.port <= 65535:
            raise ConfigError("port 必须在 1..65535 之间")

        if isinstance(self.context_chars, bool) or not isinstance(
            self.context_chars, int
        ):
            raise ConfigError("context_chars 必须是整数")
        if not 0 <= self.context_chars <= 5000:
            raise ConfigError("context_chars 必须在 0..5000 之间")

        if self.version != CONFIG_VERSION:
            raise ConfigError(
                f"不支持的配置版本: {self.version!r}（当前支持 {CONFIG_VERSION}）"
            )

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        return {
            "version": data["version"],
            "host": data["host"],
            "port": data["port"],
            "context_chars": data["context_chars"],
        }

    @classmethod
    def from_dict(cls, raw: object) -> "Config":
        if not isinstance(raw, dict):
            raise ConfigError("配置文件顶层必须是 JSON 对象")

        known = {"version", "host", "port", "context_chars"}
        values: dict[str, object] = {}
        for key in known:
            if key in raw:
                values[key] = raw[key]

        config = cls(
            host=values.get("host", DEFAULT_HOST),  # type: ignore[arg-type]
            port=values.get("port", DEFAULT_PORT),  # type: ignore[arg-type]
            context_chars=values.get(
                "context_chars", DEFAULT_CONTEXT_CHARS
            ),  # type: ignore[arg-type]
            version=values.get("version", CONFIG_VERSION),  # type: ignore[arg-type]
        )
        config.validate()
        # Unknown keys are ignored for forward compatibility. Keep them out of
        # the normalized file written below, so the file remains predictable.
        return config

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            return cls()

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"无法读取配置文件 {path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise ConfigError(f"配置文件必须使用 UTF-8 编码 {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置文件不是合法 JSON {path}: {exc}") from exc
        return cls.from_dict(raw)

    def save(self, path: Path, tmp_dir: Path | None = None) -> None:
        self.validate()
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, payload, tmp_dir=tmp_dir)
