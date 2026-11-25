"""Outbound WhatsApp messages per organization in a month (template vs session), for pricing.

Usage (inside Docker):
    docker compose exec api python -m scripts.messaging_usage --month 2026-11
"""

import argparse
import re
from collections import defaultdict

from app.core.db import SessionLocal
from app.core.timezone import local_today
from app.services.usage import all_organizations_month

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", default=local_today().strftime("%Y-%m"), help="AAAA-MM")
    args = parser.parse_args()
    if not MONTH.match(args.month):
        parser.error("--month must look like 2026-11")

    with SessionLocal() as db:
        rows = all_organizations_month(db, args.month)
    if not rows:
        print(f"No outbound WhatsApp messages in {args.month}.")
        return 0
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"template": 0, "session": 0})
    print(f"{'Organization':40} {'Delivery':9} {'Kind':22} {'Messages':>8}")
    for name, row in rows:
        print(f"{name[:40]:40} {row.delivery:9} {row.kind or '-':22} {row.messages:>8}")
        totals[name][row.delivery] += row.messages
    print()
    print(f"{'Organization':40} {'Templates':>9} {'Session':>8} {'Total':>8}")
    for name, total in totals.items():
        all_messages = total["template"] + total["session"]
        print(f"{name[:40]:40} {total['template']:>9} {total['session']:>8} {all_messages:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
