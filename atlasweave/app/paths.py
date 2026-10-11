"""Resolve per-user paths without relying on the working directory."""

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QStandardPaths


@dataclass(frozen=True)
class AppPaths:
    config: Path
    logs: Path

    @classmethod
    def resolve(cls, data_dir: Path | None = None) -> "AppPaths":
        if data_dir is not None:
            root = data_dir.expanduser().resolve()
            return cls(root / "config", root / "logs")
        config = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
        data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        if not config or not data:
            raise OSError("操作系统未提供可用的用户数据目录。")
        return cls(Path(config), Path(data) / "logs")
