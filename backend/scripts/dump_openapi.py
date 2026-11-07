"""Prints the API's OpenAPI schema as JSON. Used to generate the web client's types."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from docuflow.main import create_app


def main() -> int:
    services = SimpleNamespace(
        settings=SimpleNamespace(log_level="WARNING", environment="test", max_upload_bytes=1),
        db=MagicMock(),
        audit=MagicMock(),
        keys=MagicMock(),
        redis=MagicMock(),
        store=MagicMock(),
        objects=MagicMock(),
    )
    sys.stdout.write(json.dumps(create_app(services).openapi(), indent=2, sort_keys=True))  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
