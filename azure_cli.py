from __future__ import annotations

import json
import subprocess
from typing import Any


def az(*args: str) -> Any:
    result = subprocess.run(["az", *args, "-o", "json"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None
