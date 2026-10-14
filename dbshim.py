"""A tiny compatibility layer that lets the existing SQLite-flavoured code in
server.py/features.py run unchanged against Postgres (Neon) instead of a
local unihub.db file -- which is required on Vercel, since serverless
functions have no persistent disk.

Design goal: change nothing about *how* the application code talks to the
database (still `with connect() as c:`, `c.execute(sql, params)` with `?`
placeholders, `row["col"]`, `row[0]`, `cur.lastrowid`, `cur.rowcount`,
`c.executescript(...)`) so the already-tested business logic in
vercel_features.py / app.py can stay a faithful port instead of a rewrite.
"""
import os
import re
import psycopg2
import psycopg2.extensions
import psycopg2.extras
import psycopg2.pool

DATABASE_URL = os.environ.get("DATABASE_URL")

# Re-exported so callers can catch it without importing psycopg2 themselves
# (mirrors sqlite3.IntegrityError, which the original code catches).
IntegrityError = psycopg2.IntegrityError

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set")
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, DATABASE_URL, sslmode="require")
    return _pool


def _plain(v):
    # psycopg2 returns BYTEA columns as memoryview by default; the app
    # treats pw_salt/pw_hash as plain bytes (hashlib.scrypt,
    # hmac.compare_digest), so normalize to bytes here, matching sqlite3.
    return bytes(v) if isinstance(v, memoryview) else v


class Row(dict):
    """Dict that also supports positional access (row[0]), like sqlite3.Row."""

    def __init__(self, mapping):
        super().__init__((k, _plain(v)) for k, v in mapping.items())

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


_PLACEHOLDER = re.compile(r"\?")
_INSERT = re.compile(r"(?is)^\s*INSERT\s+INTO\s+(\w+)")
_PRAGMA = re.compile(r"(?is)^\s*PRAGMA\b")

# Tables with a `SERIAL PRIMARY KEY id` column (see schema_postgres.sql).
# sessions/follows/reactions/responses are junction/keyed-by-value tables
# with no id column -- appending "RETURNING id" to an insert into one of
# those would be a Postgres error ("column id does not exist"), so only
# these get it, mirroring exactly what sqlite3's autoincrement rowid gives
# cur.lastrowid for free on every table.
_TABLES_WITH_ID = {
    "users", "posts", "products", "students", "roommates",
    "entries", "comments", "messages", "notifications",
}


class Cursor:
    def __init__(self, raw):
        self._raw = raw
        self.lastrowid = None
        self.rowcount = 0

    def execute(self, sql, params=()):
        if _PRAGMA.match(sql):
            return self  # Postgres always enforces FKs; nothing to toggle.
        translated = _PLACEHOLDER.sub("%s", sql)
        m = _INSERT.match(sql)
        if m and m.group(1).lower() in _TABLES_WITH_ID and "RETURNING" not in sql.upper():
            translated += " RETURNING id"
            self._raw.execute(translated, params)
            row = self._raw.fetchone()
            self.lastrowid = row["id"] if row else None
        else:
            self._raw.execute(translated, params)
        self.rowcount = self._raw.rowcount
        return self

    def fetchone(self):
        r = self._raw.fetchone()
        return Row(r) if r is not None else None

    def fetchall(self):
        return [Row(r) for r in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def execute(self, sql, params=()):
        cur = Cursor(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))
        return cur.execute(sql, params)

    def executescript(self, sql):
        cur = self._conn.cursor()
        cur.execute(sql)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            _get_pool().putconn(self._conn)


def connect():
    raw = _get_pool().getconn()
    return Connection(raw)
