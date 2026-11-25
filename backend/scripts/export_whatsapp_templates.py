"""Export the WhatsApp templates in Meta's "create message template" format.

Usage (inside Docker):
    docker compose exec api python -m scripts.export_whatsapp_templates > plantillas.json
    docker compose exec api python -m scripts.export_whatsapp_templates --language es_MX

Each item is the JSON body for POST /<WABA_ID>/message_templates. Templates with a document
header need a sample PDF handle (Resumable Upload API) in place of <HANDLE_DE_UN_PDF_DE_EJEMPLO>.
"""

import argparse
import json
import sys

from app.channels.whatsapp.templates import catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--language", help="language code for Meta (default: from the file)")
    args = parser.parse_args()
    json.dump(catalog().export_for_meta(args.language), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
