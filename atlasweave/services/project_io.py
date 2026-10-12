"""Safe `.atlasweave` container IO, migrations, backups and recent projects."""

import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from atlasweave.domain.project import Project
from atlasweave.domain.validation import ValidationReport, validate_project
from atlasweave.domain.version import Version
from atlasweave.services.migrations import CURRENT_FORMAT, MigrationError, MigrationRegistry
from atlasweave.services.project_codec import (
    UnsupportedDataError,
    decode_project,
    encode_project,
)
from atlasweave.services.thumbnail import render_thumbnail

logger = logging.getLogger(__name__)
REQUIRED_MEMBERS = frozenset(("project.json", "topology.json", "styles.json", "thumbnail.png"))
MAX_MEMBER_BYTES = 256 * 1024 * 1024


class ProjectIOError(Exception):
    """A user-actionable project IO failure; target content is kept intact."""


@dataclass(frozen=True)
class LoadedProject:
    project: Project
    path: Path
    read_only: bool = False
    warning: str | None = None
    migration_history: tuple[str, ...] = ()
    validation: ValidationReport | None = None
    attachments: dict[str, bytes] | None = None
    document_extra: dict | None = None


@dataclass(frozen=True)
class SaveResult:
    path: Path
    backup_paths: tuple[Path, ...] = ()
    warning: str | None = None
    validation: ValidationReport | None = None


def _json_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


def _read_documents(path: Path) -> tuple[dict[str, dict], bytes, dict[str, bytes]]:
    if not path.is_file():
        raise ProjectIOError(f"工程文件不存在：{path}")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            if bad := archive.testzip():
                raise ProjectIOError(f"工程压缩包校验失败：{bad}")
            names = set(archive.namelist())
            missing = REQUIRED_MEMBERS - names
            if missing:
                raise ProjectIOError(f"工程缺少必要文件：{', '.join(sorted(missing))}")
            documents = {}
            for name in ("project.json", "topology.json", "styles.json"):
                info = archive.getinfo(name)
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ProjectIOError(f"工程成员过大：{name}")
                try:
                    documents[name] = json.loads(archive.read(name).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProjectIOError(f"工程 JSON 无法读取：{name}") from exc
            thumbnail = archive.read("thumbnail.png")
            if not thumbnail.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ProjectIOError("工程缩略图不是有效 PNG")
            attachments = {
                name: archive.read(name)
                for name in names
                if name.startswith("attachments/") and _safe_member_name(name)
            }
            return documents, thumbnail, attachments
    except zipfile.BadZipFile as exc:
        raise ProjectIOError("工程不是有效的 ZIP 容器") from exc
    except OSError as exc:
        raise ProjectIOError(f"无法读取工程文件：{path}") from exc


def _safe_member_name(name: str) -> bool:
    path = Path(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _write_archive(
    path: Path,
    documents: dict[str, dict],
    thumbnail: bytes,
    attachments: dict[str, bytes] | None = None,
) -> None:
    if not thumbnail.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ProjectIOError("缩略图必须是 PNG")
    try:
        with zipfile.ZipFile(
            path, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False
        ) as archive:
            for name, document in documents.items():
                archive.writestr(name, _json_bytes(document))
            archive.writestr("thumbnail.png", thumbnail)
            for name, content in (attachments or {}).items():
                if (
                    not _safe_member_name(name)
                    or name in REQUIRED_MEMBERS
                    or not name.startswith("attachments/")
                ):
                    raise ProjectIOError(f"附件路径不安全：{name}")
                archive.writestr(name, content)
        with zipfile.ZipFile(path, "r") as archive:
            if archive.testzip():
                raise ProjectIOError("临时工程写入后校验失败")
            if not REQUIRED_MEMBERS <= set(archive.namelist()):
                raise ProjectIOError("临时工程缺少必要成员")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ProjectIOError("无法写入工程临时文件") from exc


class ProjectStore:
    def __init__(
        self, *, migrations: MigrationRegistry | None = None, backup_count: int = 3
    ) -> None:
        if not 0 <= backup_count <= 10:
            raise ValueError("备份数量必须在 0–10 之间")
        self.migrations = migrations or MigrationRegistry()
        self.backup_count = backup_count
        self.read_only_paths: set[Path] = set()

    def load(self, path: Path) -> LoadedProject:
        path = Path(path).expanduser().resolve()
        documents, _, attachments = _read_documents(path)
        root = documents["project.json"]
        source_version = root.get("format_version")
        try:
            parsed_version = Version.parse(source_version)
        except Exception as exc:
            raise ProjectIOError("工程版本不是有效的语义版本") from exc
        current = Version.parse(CURRENT_FORMAT)
        history = ()
        read_only = False
        warning = None
        if parsed_version > current:
            read_only = True
            warning = (
                f"工程版本 {source_version} 高于当前支持版本 {CURRENT_FORMAT}，"
                "已只读打开，禁止覆盖原文件。"
            )
        elif parsed_version < current:
            try:
                documents, history = self.migrations.migrate(documents, source_version)
            except MigrationError as exc:
                raise ProjectIOError(str(exc)) from exc
        try:
            project, root_extra = decode_project(documents)
        except (KeyError, TypeError, ValueError, UnsupportedDataError) as exc:
            raise ProjectIOError(f"工程数据无法安全解析：{exc}") from exc
        report = validate_project(project)
        if read_only:
            self.read_only_paths.add(path)
        return LoadedProject(
            project, path, read_only, warning, history, report, attachments, root_extra
        )

    def save_loaded(self, loaded: LoadedProject, path: Path | None = None) -> SaveResult:
        """Save a loaded project while preserving optional attachment members."""
        return self.save(
            loaded.project,
            path or loaded.path,
            attachments=loaded.attachments,
            document_extra=loaded.document_extra,
        )

    def save(
        self,
        project: Project,
        path: Path,
        *,
        attachments: dict[str, bytes] | None = None,
        document_extra: dict | None = None,
    ) -> SaveResult:
        path = Path(path).expanduser().resolve()
        if path in self.read_only_paths:
            raise ProjectIOError("该工程来自更高版本，只读打开，禁止覆盖；请另存为新文件。")
        if path.suffix.lower() != ".atlasweave":
            raise ProjectIOError("工程文件扩展名必须是 .atlasweave")
        try:
            documents = encode_project(project, document_extra or {})
            thumbnail = render_thumbnail(project)
        except (ValueError, TypeError, UnsupportedDataError) as exc:
            raise ProjectIOError(f"工程无法序列化：{exc}") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        backups = []
        warning = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
            _write_archive(temporary, documents, thumbnail, attachments)
            # Reopen through the public loader before replacing user data.
            self.load(temporary)
            if path.exists() and self.backup_count:
                backups, warning = self._rotate_backups(path)
            os.replace(temporary, path)
            temporary = None
            return SaveResult(path, tuple(backups), warning, validate_project(project))
        except (OSError, ProjectIOError) as exc:
            raise ProjectIOError(f"保存失败，原工程仍保留：{exc}") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("无法清理工程临时文件：%s", temporary)

    def _rotate_backups(self, path: Path) -> tuple[list[Path], str | None]:
        backups = []
        warning = None
        for index in range(self.backup_count, 1, -1):
            current = path.with_name(f"{path.name}.bak{index}")
            previous = path.with_name(f"{path.name}.bak{index - 1}")
            try:
                if previous.exists():
                    if current.exists():
                        current.unlink()
                    shutil.copy2(previous, current)
                    backups.append(current)
            except OSError:
                logger.exception("备份轮换失败：%s", current)
                warning = "部分工程备份未能更新，主工程仍会继续保存。"
        try:
            newest = path.with_name(f"{path.name}.bak1")
            shutil.copy2(path, newest)
            backups.append(newest)
        except OSError:
            logger.exception("无法创建工程备份：%s", path)
            warning = "工程备份未能更新，主工程仍会继续保存。"
        return backups, warning


@dataclass(frozen=True)
class RecentProject:
    path: Path
    last_opened_at: datetime
    pinned: bool = False


class RecentProjects:
    def __init__(self, path: Path, *, limit: int = 20) -> None:
        self.path = Path(path)
        self.limit = max(1, min(limit, 100))

    def load(self) -> tuple[RecentProject, ...]:
        if not self.path.exists():
            return ()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            result = []
            for item in raw.get("projects", []):
                path = Path(item["path"])
                when = datetime.fromisoformat(item["last_opened_at"])
                if when.tzinfo is None:
                    continue
                result.append(RecentProject(path, when, bool(item.get("pinned", False))))
            return tuple(result[: self.limit])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            logger.exception("最近工程记录损坏：%s", self.path)
            return ()

    def touch(self, project_path: Path, *, pinned: bool | None = None) -> tuple[RecentProject, ...]:
        project_path = Path(project_path).expanduser().resolve()
        now = datetime.now(UTC)
        loaded = self.load()
        existing = next((item for item in loaded if item.path == project_path), None)
        old = [item for item in loaded if item.path != project_path]
        item = RecentProject(
            project_path, now, existing.pinned if pinned is None and existing else bool(pinned)
        )
        projects = [item, *old]
        projects = [
            *filter(lambda value: value.pinned, projects),
            *filter(lambda value: not value.pinned, projects),
        ]
        projects = projects[: self.limit]
        self._save(projects)
        return tuple(projects)

    def remove(self, project_path: Path) -> tuple[RecentProject, ...]:
        path = Path(project_path).expanduser().resolve()
        projects = tuple(item for item in self.load() if item.path != path)
        self._save(projects)
        return projects

    def _save(self, projects) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, prefix=".recent-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(
                    {
                        "version": 1,
                        "projects": [
                            {
                                "path": str(item.path),
                                "last_opened_at": item.last_opened_at.isoformat(),
                                "pinned": item.pinned,
                            }
                            for item in projects
                        ],
                    },
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
