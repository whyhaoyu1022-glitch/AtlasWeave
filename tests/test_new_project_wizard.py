from pathlib import Path

from atlasweave.ui.main_window import NewProjectDialog, _safe_project_filename


def test_project_filename_is_safe_for_windows():
    assert _safe_project_filename('北境: "试作" ') == "北境_ _试作_"
    assert _safe_project_filename("CON") == "_CON"


def test_wizard_defaults_and_project_snapshot(qtbot, tmp_path):
    dialog = NewProjectDialog()
    qtbot.addWidget(dialog)
    dialog.path_edit.setText(str(tmp_path))
    assert dialog.stack.count() == 4
    assert dialog.level_list.count() == 4
    dialog.author_edit.setText("作者")
    dialog.description_edit.setText("说明")
    project = dialog.project()
    assert project.metadata["author"] == "作者"
    assert project.description == "说明"
    assert len(project.hierarchy_levels) == 4
    assert dialog.target_path().parent == Path(tmp_path)


def test_wizard_level_move_and_remove_preserve_minimum(qtbot):
    dialog = NewProjectDialog()
    qtbot.addWidget(dialog)
    dialog.level_list.setCurrentRow(3)
    dialog._move_level(-1)
    assert dialog.level_list.item(2).text() == "县级"
    for _ in range(8):
        dialog.level_list.setCurrentRow(0)
        dialog._remove_level()
    assert dialog.level_list.count() == 1
    dialog._remove_level()
    assert dialog.level_list.count() == 1
