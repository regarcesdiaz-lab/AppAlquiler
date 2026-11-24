"""Load the demo organization into the development database.

Usage (inside Docker):
    docker compose exec api python -m scripts.seed_demo           # create it
    docker compose exec api python -m scripts.seed_demo --reset   # delete and recreate it

The demo owner can sign in with DEMO_OWNER_EMAIL and DEMO_PASSWORD (development only: this
script refuses to run with APP_ENV=production, so the password never exists there).
"""

import argparse
import sys

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.core.timezone import local_today
from app.demo import DEMO_OWNER_EMAIL, DemoAlreadySeededError, demo_organization_id, seed_demo
from app.models import User
from app.services.account import purge_organization

# Not a secret: a fixed sign-in for the local demo data.
DEMO_PASSWORD = "demo-alquiler-2026"


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
        owner = db.get(User, result.owner_id)
        assert owner is not None
        owner.password_hash = hash_password(DEMO_PASSWORD)

    scenarios = sorted(result.leases.values())
    print(f"Demo organization {result.organization_id} created.")
    print(f"Leases by scenario: {', '.join(scenarios)}; 1 vacant unit.")
    print(f"Sign in: {DEMO_OWNER_EMAIL} / {DEMO_PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
