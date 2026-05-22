"""Small JSON atomic writer used by the migration runner.

Standalone so migrations do not depend on private helpers from store.py.
The semantics match _write_json_atomic in store.py: write to a tempfile
in the same directory, fsync via close, then os.replace into place.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        delete=False,
        encoding="utf-8",
        prefix=".tmp-",
        suffix=path.suffix,
    ) as tmp:
        json.dump(payload, tmp, indent=2, ensure_ascii=False, sort_keys=True)
        tmp.write("\n")
        tmp_path = tmp.name
    os.replace(tmp_path, path)
