import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# tests run against an isolated SQLite database unless told otherwise
os.environ.setdefault("PC_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("PC_SECRET_KEY", "test-secret-key-for-unit-tests-only-0123456789")
os.environ.setdefault("PC_ENV", "test")
