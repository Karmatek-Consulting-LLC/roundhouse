"""Pipe Python source through `ruff format -` for canonical formatting.

ruff is a native binary - no Python dependency. If it's missing or errors out
for any reason, format() returns the original source unchanged. We never fail
a codegen / deploy because of a formatter hiccup."""
from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def format_python(py: str) -> str:
    if not py.strip():
        return py
    try:
        result = subprocess.run(
            ["ruff", "format", "-"],
            input=py,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("PythonFormatter: ruff not invocable (%s); returning unformatted output", e)
        return py
    if result.returncode != 0 or not result.stdout:
        logger.warning(
            "PythonFormatter: ruff format failed exit=%s stderr=%s",
            result.returncode,
            result.stderr.strip(),
        )
        return py
    return result.stdout
