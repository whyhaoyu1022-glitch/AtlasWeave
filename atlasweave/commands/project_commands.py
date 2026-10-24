"""Qt command adapters for immutable project snapshots."""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtGui import QUndoCommand


class ProjectSnapshotCommand(QUndoCommand):
    """Apply immutable project snapshots while keeping selection state reversible."""

    def __init__(
        self,
        window,
        before_project,
        after_project,
        before_selection: Iterable[str],
        after_selection: Iterable[str],
        label: str,
    ) -> None:
        super().__init__(label)
        self._window = window
        self._before_project = before_project
        self._after_project = after_project
        self._before_selection = tuple(before_selection)
        self._after_selection = tuple(after_selection)
        self._has_run = False

    def redo(self) -> None:
        message = self.text() if not self._has_run else f"重做：{self.text()}"
        self._has_run = True
        self._window._apply_history_snapshot(
            self._after_project,
            self._after_selection,
            message,
        )

    def undo(self) -> None:
        self._window._apply_history_snapshot(
            self._before_project,
            self._before_selection,
            f"撤销：{self.text()}",
        )
