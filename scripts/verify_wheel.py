"""Install the built wheel in isolation and exercise its real entry point."""

import os
import subprocess
import tempfile
import venv
from pathlib import Path


def main() -> None:
    wheels = list(Path("dist").glob("atlasweave-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one AtlasWeave wheel in dist/")
    wheel = wheels[0].resolve()
    with tempfile.TemporaryDirectory(prefix="atlasweave-wheel-") as temp:
        root = Path(temp)
        environment = root / "venv"
        venv.create(environment, with_pip=True)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [str(python), "-m", "pip", "install", str(wheel)],
            check=True,
            timeout=240,
        )
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONUTF8": "1"}
        env.pop("PYTHONPATH", None)
        subprocess.run(
            [str(python), "-m", "atlasweave", "--smoke-test", "--data-dir", str(root / "data")],
            cwd=root,
            env=env,
            check=True,
            timeout=30,
        )
        assert (root / "data/config/settings.json").is_file()
        assert "Application stopped | exit=0" in (root / "data/logs/atlasweave.log").read_text(
            encoding="utf-8"
        )
        print("Installed wheel: launch, render, settings and clean exit verified.")


if __name__ == "__main__":
    main()
