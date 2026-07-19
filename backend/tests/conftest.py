import os

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-only-secret-that-is-at-least-32-characters")
