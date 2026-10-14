"""UniHub API on Vercel: Flask + Postgres (Neon), deployed alongside the
static frontend in public/ (copied from unihub/site at build time, see
build_static.py). This is a deliberately close port of unihub/server.py --
same routes, same validation, same session/cookie model -- with only the
storage layer swapped (dbshim.py) and the HTTP plumbing translated from
BaseHTTPRequestHandler to Flask, since Vercel has no persistent process or
disk. Static HTML pages are served straight from Vercel's CDN (see
vercel.json / public/), so there is no server-side auth-gate redirect here;
app.js already falls back to a client-side redirect to login.html on 401.
"""
import hashlib, hmac, json, os, pathlib, re, secrets, time, datetime, sys
from flask import Flask, request, jsonify

from dbshim import connect, IntegrityError

app = Flask(__name__)
app.json.ensure_ascii = False

CATEGORIES_POST = {"Study", "Question", "Housing", "Marketplace", "Events", "Campus Life"}
CATEGORIES_PRODUCT = {"Electronics", "Books", "Dorm stuff", "Clothes", "Other"}
UNIVERSITIES = {"AITU", "ENU", "MNU", "Nazarbayev University", "Other"}

SESSION_TTL = 30 * 24 * 3600
fails = {}  # (login, ip) -> [timestamps]; per-instance only, see README-VERCEL.md

_SCHEMA_SQL = (pathlib.Path(__file__).parent / "schema_postgres.sql").read_text(encoding="utf-8")
_schema_ready = False


def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    with connect() as c:
        c.executescript(_SCHEMA_SQL)
    _schema_ready = True


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
    if not token:
        return None
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


# features.py needs these names on this module, same trick as server.py/features.py.
sys.modules[__name__].connect = connect
sys.modules[__name__].Bad = Bad
sys.modules[__name__].need_str = need_str
sys.modules[__name__].NAME_OK = NAME_OK
sys.modules[__name__].hash_pw = hash_pw
sys.modules[__name__].short_name = short_name
sys.modules[__name__].user_json = user_json
sys.modules[__name__].post_json = post_json
sys.modules[__name__].product_json = product_json
sys.modules[__name__].UNIVERSITIES = UNIVERSITIES
sys.modules[__name__].CATEGORIES_POST = CATEGORIES_POST
sys.modules[__name__].CATEGORIES_PRODUCT = CATEGORIES_PRODUCT

import vercel_features as features
features.s = sys.modules[__name__]


def send_json(code, obj, set_cookie=None):
    resp = jsonify(obj)
    resp.status_code = code
    resp.headers["Cache-Control"] = "no-store"
    if set_cookie:
        name, value, max_age = set_cookie
        resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="Lax", path="/", secure=True)
    return resp


def parse_body():
    if "application/json" not in (request.headers.get("Content-Type") or ""):
        raise Bad("Content-Type must be application/json")
    n = request.content_length or 0
    if n < 0 or n > 3000000:
        raise Bad("Request too large")
    try:
        d = request.get_json(force=True, silent=False)
    except Exception:
        raise Bad("Invalid JSON")
    if not isinstance(d, dict):
        raise Bad("Invalid JSON")
    return d


def current_user():
    return user_from_token(request.cookies.get("session"))


def same_origin():
    o = request.headers.get("Origin")
    return not o or o.split("://", 1)[-1] == request.headers.get("Host")


def session_cookie(token, max_age=SESSION_TTL):
    return ("session", token, max_age)


def register(d):
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
        except IntegrityError:
            raise Bad("This login is already taken", "login", 409)
        token = new_session(c, cur.lastrowid)
        u = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return send_json(201, user_json(u), session_cookie(token))


def login(d):
    login_ = str(d.get("login") or "").strip()[:30]
    pw = d.get("password") if isinstance(d.get("password"), str) else ""
    key = (login_.lower(), request.remote_addr); now = time.time()
    fails[key] = [t for t in fails.get(key, []) if now - t < 300]
    if len(fails[key]) >= 5:
        raise Bad("Too many attempts. Try again in a few minutes.", None, 429)
    with connect() as c:
        u = c.execute("SELECT * FROM users WHERE login=?", (login_,)).fetchone()
        ok = hmac.compare_digest(hash_pw(pw[:128], u["pw_salt"] if u else DUMMY_SALT), u["pw_hash"] if u else b"\0" * 32) and u is not None
        if not ok:
            fails[key].append(now)
            raise Bad("Wrong login or password", None, 401)
        fails.pop(key, None)
        token = new_session(c, u["id"])
    return send_json(200, user_json(u), session_cookie(token))


@app.route("/api/<path:subpath>", methods=["GET", "POST", "DELETE"])
def api_dispatch(subpath):
    ensure_schema()
    path = "/api/" + subpath

    if request.method == "GET":
        if path == "/api/health":
            return send_json(200, {"app": "UniHub", "version": 2, "instance": "vercel"})
        if path == "/api/me":
            u = current_user()
            return send_json(200, user_json(u)) if u else send_json(401, {"error": "Not signed in"})
        if path == "/api/data":
            u = current_user()
            if not u: return send_json(401, {"error": "Not signed in"})
            return send_json(200, get_all(u["id"]))
        return send_json(404, {"error": "Not found"})

    if request.method == "DELETE":
        if not same_origin(): return send_json(403, {"error": "Bad origin"})
        me = current_user()
        if not me: return send_json(401, {"error": "Not signed in"})
        result = features.delete(path, me)
        if result is not None: return send_json(*result)
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] in ("posts", "products") and parts[2].isdigit():
            with connect() as c:
                cur = c.execute(f"DELETE FROM {parts[1]} WHERE id=? AND user_id=?", (int(parts[2]), me["id"]))
            return send_json(200 if cur.rowcount else 404, {"deleted": cur.rowcount})
        return send_json(404, {"error": "Not found"})

    # POST
    try:
        if not same_origin(): raise Bad("Bad origin", None, 403)
        d = parse_body()
        if path == "/api/register": return register(d)
        if path == "/api/login": return login(d)
        if path == "/api/logout":
            t = request.cookies.get("session")
            if t:
                with connect() as c:
                    c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(t.encode()).hexdigest(),))
            return send_json(200, {"ok": True}, ("session", "", 0))
        me = current_user()
        if not me: raise Bad("Not signed in", None, 401)
        result = features.post(path, d, me)
        if result is not None: return send_json(*result)
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
                return send_json(201, post_json(row, me["id"]))
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
                return send_json(201, product_json(row, me["id"]))
        return send_json(404, {"error": "Not found"})
    except Bad as e:
        return send_json(e.code, {"error": str(e), "field": e.field})
