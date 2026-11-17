"""Load the demo organization into the development database.

Usage (inside Docker):
    docker compose exec api python -m scripts.seed_demo           # create it
    docker compose exec api python -m scripts.seed_demo --reset   # delete and recreate it
"""

import argparse
import sys

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.timezone import local_today
from app.demo import DemoAlreadySeededError, demo_organization_id, seed_demo
from app.services.account import purge_organization


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete the demo data first")
    args = parser.parse_args()

    if get_settings().APP_ENV == "production":
        print("Refusing to load demo data with APP_ENV=production.", file=sys.stderr)
        return 1

    with SessionLocal() as db, db.begin():
        if args.reset and (org_id := demo_organization_id(db)) is not None:
            purge_organization(db, org_id)
            print("Previous demo data deleted.")
        try:
            result = seed_demo(db, local_today())
        except DemoAlreadySeededError as exc:
            print(exc, file=sys.stderr)
            return 1

    scenarios = sorted(result.leases.values())
    print(f"Demo organization {result.organization_id} created.")
    print(f"Leases by scenario: {', '.join(scenarios)}; 1 vacant unit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
