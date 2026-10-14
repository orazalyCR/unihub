-- Postgres schema for the Vercel deployment. One-to-one translation of the
-- SQLite schema in unihub/server.py + unihub/features.py, already folded
-- flat (no incremental ALTER TABLE migrations needed: this database starts
-- empty every time). Applied once by app.py on cold start.
--
-- citext gives the `login` column the same case-insensitive comparison that
-- SQLite's `COLLATE NOCASE` gave it, with zero changes needed to any query.
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE IF NOT EXISTS users(
  id SERIAL PRIMARY KEY,
  first_name TEXT NOT NULL, last_name TEXT NOT NULL, birth_date TEXT NOT NULL,
  university TEXT NOT NULL, login CITEXT NOT NULL UNIQUE, email TEXT,
  pw_salt BYTEA NOT NULL, pw_hash BYTEA NOT NULL, created_at BIGINT NOT NULL,
  bio TEXT NOT NULL DEFAULT '', interests TEXT NOT NULL DEFAULT '', community TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts(
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL, university TEXT NOT NULL, category TEXT NOT NULL,
  text TEXT NOT NULL, likes INTEGER NOT NULL DEFAULT 0, comments INTEGER NOT NULL DEFAULT 0,
  followed INTEGER NOT NULL DEFAULT 0, tone TEXT NOT NULL DEFAULT 'blue',
  time_label TEXT, sort_key BIGINT NOT NULL, is_mine INTEGER NOT NULL DEFAULT 0,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS products(
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL, category TEXT NOT NULL, price INTEGER NOT NULL,
  seller TEXT NOT NULL, university TEXT NOT NULL, condition TEXT NOT NULL,
  photo INTEGER NOT NULL DEFAULT 0, is_mine INTEGER NOT NULL DEFAULT 0,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  image TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Available'
);
CREATE TABLE IF NOT EXISTS students(
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL, university TEXT NOT NULL, course TEXT NOT NULL, year TEXT NOT NULL,
  subjects TEXT NOT NULL, format TEXT NOT NULL, bio TEXT NOT NULL, tone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roommates(
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL, university TEXT NOT NULL, budget INTEGER NOT NULL, district TEXT NOT NULL,
  smoking TEXT NOT NULL, pets TEXT NOT NULL, lifestyle TEXT NOT NULL, sleep TEXT NOT NULL,
  bio TEXT NOT NULL, tone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries(
  id SERIAL PRIMARY KEY, kind TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  university TEXT NOT NULL, payload TEXT NOT NULL, created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS reactions(
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
  PRIMARY KEY(user_id,post_id)
);
CREATE TABLE IF NOT EXISTS comments(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
  text TEXT NOT NULL, created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS follows(
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, target_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY(user_id,target_id), CHECK(user_id!=target_id)
);
CREATE TABLE IF NOT EXISTS responses(
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, entry_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
  status TEXT NOT NULL, PRIMARY KEY(user_id,entry_id)
);
CREATE TABLE IF NOT EXISTS messages(
  id SERIAL PRIMARY KEY, sender_id INTEGER REFERENCES users(id) ON DELETE CASCADE, recipient_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  text TEXT NOT NULL, created_at BIGINT NOT NULL, read_at BIGINT, CHECK(sender_id!=recipient_id)
);
CREATE TABLE IF NOT EXISTS notifications(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, text TEXT NOT NULL, href TEXT NOT NULL,
  created_at BIGINT NOT NULL, read_at BIGINT
);
CREATE INDEX IF NOT EXISTS messages_recipient ON messages(recipient_id,id);
CREATE INDEX IF NOT EXISTS entries_kind ON entries(kind,university);
CREATE INDEX IF NOT EXISTS notifications_user ON notifications(user_id,id);
