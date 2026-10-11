import os
from pathlib import Path

# Must precede pytest-qt's first QApplication, without masking caller overrides.
if os.environ.get("QT_QPA_PLATFORM", "offscreen") == "offscreen":
    # A relative path avoids the Windows drive colon being parsed as a Qt option separator.
    config = Path(os.path.relpath(Path(__file__).with_name("offscreen.json"))).as_posix()
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={config}"
