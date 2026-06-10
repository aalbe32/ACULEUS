"""Cross-platform venv setup. Run once before starting the project.

    python setup.py

Creates ./venv if it doesn't exist and installs requirements.txt into it.
"""
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).parent
VENV_DIR = ROOT / "venv"
REQUIREMENTS = ROOT / "requirements.txt"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def activate_hint() -> str:
    if os.name == "nt":
        return r"venv\Scripts\activate"
    return "source venv/bin/activate"


def main() -> int:
    if VENV_DIR.exists():
        print(f"venv already exists at {VENV_DIR}")
    else:
        print(f"Creating venv at {VENV_DIR}...")
        venv.create(VENV_DIR, with_pip=True, upgrade_deps=True)

    py = str(venv_python())

    if REQUIREMENTS.exists():
        print("Installing requirements...")
        subprocess.check_call([py, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    else:
        print("No requirements.txt found — skipping install.")

    print("\nDone. Activate the venv with:")
    print(f"  {activate_hint()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())