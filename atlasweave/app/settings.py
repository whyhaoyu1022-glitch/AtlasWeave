"""Atomic, versioned user settings. Project data does not belong here."""

import base64
import binascii
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class SettingsError(Exception):
    """Settings could not be read or saved safely."""


@dataclass(frozen=True)
class WindowSettings:
    geometry: bytes = b""


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._document: dict = {}
        self._writable = True

    def load(self) -> WindowSettings:
        if not self.path.exists():
            return WindowSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError("设置格式或版本不受支持")
            encoded = raw.get("window_geometry", "")
            if not isinstance(encoded, str):
                raise ValueError("窗口位置格式无效")
            geometry = base64.b64decode(encoded, validate=True)
            self._document = raw  # Preserve fields added by other versions.
            return WindowSettings(geometry=geometry)
        except (OSError, ValueError, binascii.Error) as exc:
            # Never overwrite a corrupt/unknown settings file with defaults on exit.
            self._writable = False
            logger.exception("Settings could not be loaded: %s", self.path)
            raise SettingsError("无法读取应用设置，本次使用默认布局；原设置文件将保留。") from exc

    def save(self, settings: WindowSettings) -> None:
        if not self._writable:
            raise SettingsError("原设置文件需要检查，本次未覆盖它。")
        document = dict(self._document)
        document.update(
            version=1, window_geometry=base64.b64encode(settings.geometry).decode("ascii")
        )
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".settings-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._document = document
        except OSError as exc:
            logger.exception("Settings could not be saved: %s", self.path)
            raise SettingsError("无法保存应用设置，请检查目录权限和磁盘空间。") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove temporary settings file: %s", temporary)
