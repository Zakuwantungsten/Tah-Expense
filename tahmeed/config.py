import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI: str = os.getenv("MONGODB_URI", "")
DB_NAME: str = os.getenv("DB_NAME", "tahmeed_expense")
APP_NAME = "Tahmeed Expense"
APP_VERSION = "1.0.0"
