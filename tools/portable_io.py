"""Write a complete JSON document before replacing the previous configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def atomic_json(path, value, *, indent=2):
    path = Path(path)
    document = json.dumps(value, indent=indent, ensure_ascii=False, allow_nan=False)
    descriptor, name = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(document)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
