"""
Fallback CLI script — run from the project root if the app's first-run
setup screen isn't accessible:

    python scripts/seed_admin.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tahmeed.db.connection import close
from tahmeed.services.auth import any_user_exists, create_user


async def main() -> None:
    if await any_user_exists():
        print("Users already exist. Use the app's login screen.")
        await close()
        return

    print("=== Create First Admin User ===")
    full_name = input("Full name : ").strip()
    username  = input("Username  : ").strip()
    password  = input("Password  : ").strip()

    if not all([full_name, username, password]):
        print("All fields are required. Aborting.")
        await close()
        return

    user = await create_user(username, password, "admin", full_name)
    print(f"\nAdmin '{user.username}' created successfully.")
    await close()


if __name__ == "__main__":
    asyncio.run(main())
