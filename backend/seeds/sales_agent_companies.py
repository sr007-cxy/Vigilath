"""Seed sales_agent_companies -- default candidate company pool for the Sales Agent.

Idempotent: only fills in missing companies/contacts and normalized fields; existing rows don't overwrite content edited by ops or humans.

Usage (after alembic upgrade head):
    cd backend && .venv/bin/python -m seeds.sales_agent_companies
"""
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    # Load geo.database first so geo.__init__ completes ORM registration; otherwise importing
    # agents.sales_agent.service first triggers geo.__init__ via models -> geo.database,
    # which then reverse-imports sales_agent.models before it finishes initializing.
    from geo.database import SessionLocal
    from agents.sales_agent.service.companies import import_sales_agent_seed_companies

    db = SessionLocal()
    try:
        result = import_sales_agent_seed_companies(db, user_id=None)
        print(
            "[seed:sales_agent_companies] "
            f"status={result['status']} requested={result['requested_count']} "
            f"inserted={result['inserted_count']} updated={result['updated_count']} "
            f"contacts+={result['contact_count_delta']} blocked={result['blocked_count']}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
