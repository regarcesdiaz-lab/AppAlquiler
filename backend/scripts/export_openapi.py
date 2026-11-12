"""Write the FastAPI OpenAPI schema to backend/openapi.json without starting the server.

Usage (from the repo root or backend/):  python backend/scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.main import create_app  # noqa: E402


def main() -> None:
    schema = create_app().openapi()
    output = BACKEND_DIR / "openapi.json"
    # Always LF so the file is byte-identical on Windows and in CI (drift check).
    output.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"OpenAPI schema written to {output}")


if __name__ == "__main__":
    main()
