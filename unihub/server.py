"""UniHub: локальный сервер + SQLite. Только стандартная библиотека Python (pip ничего ставить не нужно)."""
import json, sqlite3, time, mimetypes, os, sys, re, hashlib, hmac, secrets, datetime
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "site")
DB_PATH = os.environ.get("UNIHUB_DB", os.path.join(ROOT, "unihub.db"))
CATEGORIES_POST = {"Study", "Question", "Housing", "Marketplace", "Events", "Campus Life"}
CATEGORIES_PRODUCT = {"Electronics", "Books", "Dorm stuff", "Clothes", "Other"}
UNIVERSITIES = {"AITU", "ENU", "MNU", "Nazarbayev University", "Other"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name TEXT NOT NULL, last_name TEXT NOT NULL, birth_date TEXT NOT NULL,
  university TEXT NOT NULL, login TEXT NOT NULL UNIQUE COLLATE NOCASE, email TEXT,
  pw_salt BLOB NOT NULL, pw_hash BLOB NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, university TEXT NOT NULL, category TEXT NOT NULL,
  text TEXT NOT NULL, likes INTEGER NOT NULL DEFAULT 0, comments INTEGER NOT NULL DEFAULT 0,
  followed INTEGER NOT NULL DEFAULT 0, tone TEXT NOT NULL DEFAULT 'blue',
  time_label TEXT, sort_key INTEGER NOT NULL, is_mine INTEGER NOT NULL DEFAULT 0,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, category TEXT NOT NULL, price INTEGER NOT NULL,
  seller TEXT NOT NULL, university TEXT NOT NULL, condition TEXT NOT NULL,
  photo INTEGER NOT NULL DEFAULT 0, is_mine INTEGER NOT NULL DEFAULT 0,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS students(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, university TEXT NOT NULL, course TEXT NOT NULL, year TEXT NOT NULL,
  subjects TEXT NOT NULL, format TEXT NOT NULL, bio TEXT NOT NULL, tone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roommates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, university TEXT NOT NULL, budget INTEGER NOT NULL, district TEXT NOT NULL,
  smoking TEXT NOT NULL, pets TEXT NOT NULL, lifestyle TEXT NOT NULL, sleep TEXT NOT NULL,
  bio TEXT NOT NULL, tone TEXT NOT NULL
);
"""

class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try: return super().__exit__(*args)
        finally: self.close()

def connect():
    c = sqlite3.connect(DB_PATH, timeout=15, factory=Connection)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
        features.init(c)
        for t in ("posts", "products"):  # миграция старой базы без user_id
            if "user_id" not in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]:
                c.execute(f"ALTER TABLE {t} ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
                c.execute(f"DELETE FROM {t} WHERE is_mine=1")


SESSION_TTL = 30 * 24 * 3600
PUBLIC_PAGES = {"/login.html", "/register.html"}
fails = {}  # (login, ip) -> [timestamps]

def hash_pw(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)

DUMMY_SALT = os.urandom(16)

def short_name(first, last):
    return f"{first} {last[0].upper()}."

def user_json(u):
    return {"bio": u["bio"], "interests": u["interests"], "community": u["community"] or u["university"], "id": u["id"], "first_name": u["first_name"], "last_name": u["last_name"], "birth_date": u["birth_date"],
            "university": u["university"], "login": u["login"], "email": u["email"], "created_at": u["created_at"],
            "short_name": short_name(u["first_name"], u["last_name"])}

def user_from_token(token):
    if not token: return None
    th = hashlib.sha256(token.encode()).hexdigest()
    with connect() as c:
        return c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.created_at>?",
                         (th, int(time.time()) - SESSION_TTL)).fetchone()

def new_session(c, user_id):
    token = secrets.token_urlsafe(32)
    c.execute("DELETE FROM sessions WHERE created_at<?", (int(time.time()) - SESSION_TTL,))
    c.execute("INSERT INTO sessions(token_hash,user_id,created_at) VALUES(?,?,?)",
              (hashlib.sha256(token.encode()).hexdigest(), user_id, int(time.time())))
    return token

NAME_OK = lambda v: all(ch.isalpha() or ch in " -'" for ch in v)

def ago(ts):
    m = max(0, (int(time.time()) - ts) // 60)
    if m < 1: return "just now"
    if m < 60: return f"{m} min ago"
    if m < 1440: return f"{m // 60} h ago"
    return f"{m // 1440} d ago"

def post_json(r, me=None):
    d = dict(r)
    return {"id": d["id"], "name": d["name"], "university": d["university"], "category": d["category"],
            "text": d["text"], "likes": d["likes"], "comments": d["comments"], "followed": bool(d["followed"]),
            "tone": d["tone"], "time": d["time_label"] or ago(d["sort_key"]), "mine": me is not None and d["user_id"] == me}

def product_json(r, me=None):
    d = dict(r)
    return {"id": d["id"], "name": d["name"], "category": d["category"], "price": d["price"], "seller": d["seller"],
            "university": d["university"], "condition": d["condition"], "photo": bool(d["photo"]),
            "mine": me is not None and d["user_id"] == me}

def get_all(me):
    with connect() as c:
        return features.data(c, me)

class Bad(Exception):
    def __init__(self, msg, field=None, code=400):
        super().__init__(msg); self.field = field; self.code = code

def need_str(d, k, maxlen):
    v = d.get(k)
    if not isinstance(v, str) or not v.strip(): raise Bad(f"Field '{k}' is required", k)
    v = v.strip()
    if len(v) > maxlen: raise Bad(f"Field '{k}' is too long (max {maxlen})")
    return v

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stderr.write("  %s\n" % (fmt % a))

    def send_json(self, code, obj, cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise Bad("Content-Type must be application/json")
        try: n = int(self.headers.get("Content-Length") or 0)
        except ValueError: raise Bad("Invalid content length")
        if n < 0 or n > 3000000: raise Bad("Request too large")
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise Bad("Invalid JSON")
        if not isinstance(d, dict): raise Bad("Invalid JSON")
        return d

    def token(self):
        try:
            return SimpleCookie(self.headers.get("Cookie") or "")["session"].value
        except (KeyError, ValueError):
            return None

    def user(self):
        return user_from_token(self.token())

    def same_origin(self):
        o = self.headers.get("Origin")
        return not o or o.split("://", 1)[-1] == self.headers.get("Host")

    def session_cookie(self, token, max_age=SESSION_TTL):
        return f"session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}"

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health": return self.send_json(200, {"app": "UniHub", "version": 2, "instance": hashlib.sha256(DB_PATH.encode()).hexdigest()[:16]})
        if path == "/api/me":
            u = self.user()
            return self.send_json(200, user_json(u)) if u else self.send_json(401, {"error": "Not signed in"})
        if path == "/api/data":
            u = self.user()
            if not u: return self.send_json(401, {"error": "Not signed in"})
            return self.send_json(200, get_all(u["id"]))
        if path == "/": path = "/index.html"
        if path.endswith(".html") and path not in PUBLIC_PAGES and not self.user():
            self.send_response(302); self.send_header("Location", "/login.html"); self.end_headers(); return
        f = os.path.normpath(os.path.join(SITE, path.lstrip("/")))
        if os.path.commonpath([SITE, f]) != SITE or not os.path.isfile(f):
            self.send_response(404); self.end_headers(); return
        data = open(f, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(f)[0] or "application/octet-stream") + ("; charset=utf-8" if f.endswith((".html", ".js", ".css")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def register(self, d):
        first = need_str(d, "first_name", 50); last = need_str(d, "last_name", 50)
        if not NAME_OK(first): raise Bad("First name may contain only letters, spaces, hyphens", "first_name")
        if not NAME_OK(last): raise Bad("Last name may contain only letters, spaces, hyphens", "last_name")
        try:
            bd = datetime.date.fromisoformat(need_str(d, "birth_date", 10))
        except ValueError:
            raise Bad("Enter a valid date of birth", "birth_date")
        if not datetime.date(1920, 1, 1) <= bd <= datetime.date.today():
            raise Bad("Enter a valid date of birth", "birth_date")
        uni = d.get("university")
        if uni not in UNIVERSITIES: raise Bad("Choose your university", "university")
        login = need_str(d, "login", 30)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", login):
            raise Bad("Login: 3–30 characters, only Latin letters, digits, . _ -", "login")
        pw = d.get("password")
        if not isinstance(pw, str) or not 8 <= len(pw) <= 128:
            raise Bad("Password must be 8–128 characters", "password")
        if d.get("email") is not None and not isinstance(d.get("email"), str): raise Bad("Invalid email", "email")
        email = (d.get("email") or "").strip() or None
        if email and (len(email) > 120 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)):
            raise Bad("Enter a valid email or leave the field empty", "email")
        salt = os.urandom(16)
        with connect() as c:
            try:
                cur = c.execute("INSERT INTO users(first_name,last_name,birth_date,university,login,email,pw_salt,pw_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                (first, last, bd.isoformat(), uni, login, email, salt, hash_pw(pw, salt), int(time.time())))
            except sqlite3.IntegrityError:
                raise Bad("This login is already taken", "login", 409)
            token = new_session(c, cur.lastrowid)
            u = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        return self.send_json(201, user_json(u), self.session_cookie(token))

    def login(self, d):
        login = str(d.get("login") or "").strip()[:30]
        pw = d.get("password") if isinstance(d.get("password"), str) else ""
        key = (login.lower(), self.client_address[0]); now = time.time()
        fails[key] = [t for t in fails.get(key, []) if now - t < 300]
        if len(fails[key]) >= 5:
            raise Bad("Too many attempts. Try again in a few minutes.", None, 429)
        with connect() as c:
            u = c.execute("SELECT * FROM users WHERE login=?", (login,)).fetchone()
            ok = hmac.compare_digest(hash_pw(pw[:128], u["pw_salt"] if u else DUMMY_SALT), u["pw_hash"] if u else b"\0" * 32) and u is not None
            if not ok:
                fails[key].append(now)
                raise Bad("Wrong login or password", None, 401)
            fails.pop(key, None)
            token = new_session(c, u["id"])
        return self.send_json(200, user_json(u), self.session_cookie(token))

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if not self.same_origin(): raise Bad("Bad origin", None, 403)
            d = self.body()
            if path == "/api/register": return self.register(d)
            if path == "/api/login": return self.login(d)
            if path == "/api/logout":
                t = self.token()
                if t:
                    with connect() as c: c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(t.encode()).hexdigest(),))
                return self.send_json(200, {"ok": True}, self.session_cookie("", 0))
            me = self.user()
            if not me: raise Bad("Not signed in", None, 401)
            result = features.post(path, d, me)
            if result is not None: return self.send_json(*result)
            uni = d.get("university")
            if uni not in UNIVERSITIES: raise Bad("Unknown university")
            author = short_name(me["first_name"], me["last_name"])
            with connect() as c:
                if path == "/api/posts":
                    cat = d.get("category")
                    if cat not in CATEGORIES_POST: raise Bad("Unknown category")
                    text = need_str(d, "text", 500)
                    cur = c.execute("INSERT INTO posts(name,university,category,text,tone,sort_key,is_mine,user_id) VALUES(?,?,?,?, 'blue',?,1,?)",
                                    (author, uni, cat, text, int(time.time()), me["id"]))
                    row = c.execute("SELECT * FROM posts WHERE id=?", (cur.lastrowid,)).fetchone()
                    return self.send_json(201, post_json(row, me["id"]))
                if path == "/api/products":
                    cat = d.get("category")
                    if cat not in CATEGORIES_PRODUCT: raise Bad("Unknown category")
                    name = need_str(d, "name", 80)
                    cond = need_str(d, "condition", 40)
                    price = features.number(d, "price")
                    if not 0 <= price <= 10_000_000: raise Bad("Price is out of range")
                    cur = c.execute("INSERT INTO products(name,category,price,seller,university,condition,is_mine,user_id) VALUES(?,?,?,?,?,?,1,?)",
                                    (name, cat, price, author, uni, cond, me["id"]))
                    row = c.execute("SELECT * FROM products WHERE id=?", (cur.lastrowid,)).fetchone()
                    return self.send_json(201, product_json(row, me["id"]))
            self.send_json(404, {"error": "Not found"})
        except Bad as e:
            self.send_json(e.code, {"error": str(e), "field": e.field})

    def do_DELETE(self):
        if not self.same_origin(): return self.send_json(403, {"error": "Bad origin"})
        me = self.user()
        if not me: return self.send_json(401, {"error": "Not signed in"})
        result = features.delete(urlparse(self.path).path, me)
        if result is not None: return self.send_json(*result)
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] in ("posts", "products") and parts[2].isdigit():
            with connect() as c:  # удалять можно только свои записи
                cur = c.execute(f"DELETE FROM {parts[1]} WHERE id=? AND user_id=?", (int(parts[2]), me["id"]))
            return self.send_json(200 if cur.rowcount else 404, {"deleted": cur.rowcount})
        self.send_json(404, {"error": "Not found"})

import features
features.s = sys.modules[__name__]

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"UniHub: http://{host}:{port}   (база: {DB_PATH})\nОстановить: Ctrl+C")
    ThreadingHTTPServer((host, port), H).serve_forever()
