"""
Pytest configuration for the Anchor test suite.

Sets minimal environment variables so settings load without a real .env file.
All integration tests that need a live DB/broker should be marked `live`
and excluded from CI with: pytest -m "not live"
"""
import os

# Provide defaults so Settings doesn't fail validation during import
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SECRET_KEY", "test_secret_key_for_unit_tests_only")
os.environ.setdefault("OANDA_API_KEY", "test_key")
os.environ.setdefault("OANDA_ACCOUNT_ID", "test_account")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("APP_ENV", "development")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: requires live OANDA connection and real database")
