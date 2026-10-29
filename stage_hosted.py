from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "hosted/src/langgraph-run-responses"

if __name__ == "__main__":
    (TARGET / "data").mkdir(exist_ok=True)
    for source, target in (
        (ROOT / "domain.py", TARGET / "domain.py"),
        (ROOT / "data/scenario.json", TARGET / "data/scenario.json"),
    ):
        shutil.copy2(source, target)
    print("Datos y cálculos originales copiados al paquete del hosted agent.")
