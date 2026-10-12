"""Registered, forward-only schema migrations. No invented legacy format support."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from atlasweave.domain.version import Version
from atlasweave.services.project_codec import thaw

CURRENT_FORMAT = "1.0.0"


class MigrationError(ValueError):
    pass


@dataclass
class MigrationRegistry:
    steps: dict[str, tuple[str, Callable]] = field(default_factory=dict)

    def register(self, source: str, target: str, transform: Callable) -> None:
        if not Version.parse(source) < Version.parse(target):
            raise MigrationError("迁移目标版本必须高于来源版本")
        if source in self.steps:
            raise MigrationError("来源版本已经注册迁移器")
        self.steps[source] = target, transform

    def migrate(self, documents: Mapping, version: str) -> tuple[dict, tuple[str, ...]]:
        data = thaw(documents)
        history = []
        current = Version.parse(CURRENT_FORMAT)
        while Version.parse(version) < current:
            if version not in self.steps:
                raise MigrationError(f"尚无 {version} 的迁移器；原文件未改动，请使用兼容版本应用。")
            target, transform = self.steps[version]
            if Version.parse(target) > current:
                raise MigrationError("迁移器目标超出了当前支持版本")
            result = transform(data)
            if not isinstance(result, dict) or "project.json" not in result:
                raise MigrationError("迁移器必须返回完整 JSON 文档集合")
            data = result
            data["project.json"]["format_version"] = target
            history.append(f"{version} → {target}")
            version = target
        return data, tuple(history)
