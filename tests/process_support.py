"""Keep test subprocesses on this snapshot, including with an editable borrowed venv."""

import sys
from pathlib import Path

import pydantic

ROOT = Path(__file__).resolve().parents[1]


def python_module(module: str, *arguments: str) -> list[str]:
    # -S prevents editable .pth hooks from selecting another checkout. Locate
    # dependencies from the already-loaded interpreter rather than a host path.
    paths = [str(ROOT / "src"), str(ROOT), str(Path(pydantic.__file__).resolve().parents[1])]
    bootstrap = (
        "import runpy,sys;from pathlib import Path;"
        f"sys.path[:0]={paths!r};"
        "import researchflow;"
        f"assert Path(researchflow.__file__).resolve().is_relative_to(Path({str(ROOT / 'src')!r}));"
        f"sys.argv={[module, *arguments]!r};"
        f"runpy.run_module({module!r},run_name='__main__')"
    )
    return [sys.executable, "-I", "-S", "-B", "-c", bootstrap]
