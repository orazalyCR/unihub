"""Run this once you have a real Neon connection string, BEFORE connecting
the project to Vercel, to confirm the schema and the dbshim translation
layer actually work against real Postgres (not just the local mock tests).

Usage:
    set DATABASE_URL=postgresql://...        (Windows cmd)
    $env:DATABASE_URL="postgresql://..."      (PowerShell)
    python verify_postgres.py
"""
import os
import sys

if not os.environ.get("DATABASE_URL"):
    print("Set the DATABASE_URL environment variable first (the POOLED connection")
    print("string from your Neon project -> Connect -> Pooled connection).")
    sys.exit(1)

import dbshim  # noqa: E402

print("1. Applying schema_postgres.sql ...")
schema = open("schema_postgres.sql", encoding="utf-8").read()
with dbshim.connect() as c:
    c.executescript(schema)
print("   OK")

print("2. Registering a throwaway test user ...")
import time, os as _os
salt = _os.urandom(16)
login = f"verify_{int(time.time())}"
with dbshim.connect() as c:
    cur = c.execute(
        "INSERT INTO users(first_name,last_name,birth_date,university,login,email,pw_salt,pw_hash,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("Verify", "Script", "2000-01-01", "AITU", login, None, salt, b"x" * 32, int(time.time())),
    )
    user_id = cur.lastrowid
    assert isinstance(user_id, int), f"expected an integer id back via RETURNING, got {user_id!r}"
print(f"   OK (id={user_id})")

print("3. Case-insensitive login lookup (citext) ...")
with dbshim.connect() as c:
    row = c.execute("SELECT * FROM users WHERE login=?", (login.upper(),)).fetchone()
    assert row is not None, "citext lookup failed -- CREATE EXTENSION citext may not have applied"
    assert row["id"] == user_id
    assert row["pw_hash"] == b"x" * 32, f"bytea round-trip mismatch: {row['pw_hash']!r}"
print("   OK")

print("4. Insert into a table with NO id column (follows) must not error ...")
with dbshim.connect() as c:
    c.execute(
        "INSERT INTO users(first_name,last_name,birth_date,university,login,email,pw_salt,pw_hash,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("Verify2", "Script", "2000-01-01", "AITU", login + "_b", None, salt, b"y" * 32, int(time.time())),
    )
    user2_id = c.execute("SELECT id FROM users WHERE login=?", (login + "_b",)).fetchone()["id"]
    c.execute("INSERT INTO follows VALUES(?,?)", (user_id, user2_id))
    following = {r[0] for r in c.execute("SELECT target_id FROM follows WHERE user_id=?", (user_id,))}
    assert following == {user2_id}
print("   OK")

print("5. Cleaning up test rows ...")
with dbshim.connect() as c:
    c.execute("DELETE FROM users WHERE id IN (?,?)", (user_id, user2_id))
print("   OK")

print("\nAll checks passed -- DATABASE_URL is correctly wired for the Vercel deployment.")
