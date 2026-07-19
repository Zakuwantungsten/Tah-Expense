"""CLI helper — import Chart of Accounts into Items.

    python scripts/import_chart_of_accounts.py [path/to/Chart_of_Accounts.xlsx]
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tahmeed.db.connection import close
from tahmeed.services.chart_of_accounts_service import import_chart_of_accounts


async def main() -> None:
    default = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "Chart_of_Accounts.xlsx",
    )
    path = sys.argv[1] if len(sys.argv) > 1 else default
    if not os.path.isfile(path):
        print(f"File not found: {path}")
        await close()
        sys.exit(1)

    print(f"Importing Chart of Accounts from:\n  {path}")
    print("This replaces all existing items, sub-items, keyword rules, and mappings.")
    confirm = input("Continue? [y/N] ").strip().lower()
    if confirm not in {"y", "yes"}:
        print("Aborted.")
        await close()
        return

    result = await import_chart_of_accounts(path, replace_existing=True)
    print(f"\nImported {result['imported']:,} items.")
    print(f"Removed: {result['removed']}")
    await close()


if __name__ == "__main__":
    asyncio.run(main())
