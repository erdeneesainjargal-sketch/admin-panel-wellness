import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import bcrypt
import requests
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from jinja2 import DictLoader

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]

# Улаанбаатарын цаг (UTC+8)
MN_TZ = timezone(timedelta(hours=8))

app = Flask(__name__)
# Серверт SECRET_KEY заавал тохируулна. Үгүй бол gunicorn-ы процесс бүр өөр түлхүүртэй болж, нэвтрэлт санамсаргүй алдагдана.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

PLATFORM_NAMES = {
    "facebook": "Фэйсбүүк",
    "instagram": "Инстаграм",
    "tiktok": "ТикТок",
    "website": "Вэбсайт",
}

BOOKING_STATUSES = {
    "pending": "Хүлээгдэж буй",
    "confirmed": "Баталгаажсан",
    "completed": "Дууссан",
    "cancelled": "Цуцлагдсан",
}

# Ноорог дээрх товч: (шинэ төлөв, амжилттай болсны мэдэгдэл)
DRAFT_ACTIONS = {
    "approve": ("published", "Пост батлагдаж, нийтлэгдсэн төлөвтэй боллоо."),
    "reject": ("rejected", "Пост татгалзагдлаа."),
}

# Зүүн талын цэс: (хуудасны нэр, харагдах бичиг)
MENU = [
    ("content", "Контент"),
    ("facebook", "Facebook"),
    ("chat", "Чат"),
    ("bookings", "Захиалга"),
    ("staff", "Ажилтан"),
]

# Нэмэх, засах, устгах, хайх боломжтой цэсүүдийн тохиргоо.
# fields: маягтын талбарууд; search: хайлт хийх баганууд;
# scope: энэ цэс зөвхөн ямар мөрүүдтэй ажиллах вэ.
RESOURCES = {
    "content": {
        "table": "content_drafts",
        "scope": {"status": "eq.draft"},
        "order": "created_at.desc",
        "search": ["title", "body"],
        "label_field": "title",
        "touch_updated_at": True,
        "fields": [
            {"name": "title", "label": "Гарчиг", "type": "text", "required": True},
            {"name": "body", "label": "Агуулга", "type": "textarea", "required": False, "empty": ""},
            {"name": "platform", "label": "Платформ", "type": "select", "required": True,
             "options": PLATFORM_NAMES, "default": "facebook"},
        ],
        "add_label": "Шинэ пост",
        "add_title": "Шинэ ноорог пост",
        "edit_title": "Ноорог пост засах",
        "noun": "постыг",
        "created_msg": "Шинэ ноорог пост нэмэгдлээ.",
        "updated_msg": "Пост хадгалагдлаа.",
        "deleted_msg": "Пост устгагдлаа.",
    },
    "bookings": {
        "table": "bookings",
        "scope": {},
        "order": "booking_time.asc",
        "search": ["customer_name", "phone", "email", "service", "note"],
        "label_field": "customer_name",
        "fields": [
            {"name": "customer_name", "label": "Үйлчлүүлэгчийн нэр", "type": "text", "required": True},
            {"name": "phone", "label": "Утас", "type": "tel", "required": True},
            {"name": "email", "label": "И-мэйл", "type": "email", "required": False},
            {"name": "service", "label": "Үйлчилгээ", "type": "text", "required": True},
            {"name": "booking_time", "label": "Огноо, цаг", "type": "datetime-local", "required": True},
            {"name": "status", "label": "Төлөв", "type": "select", "required": True,
             "options": BOOKING_STATUSES, "default": "pending"},
            {"name": "note", "label": "Тэмдэглэл", "type": "textarea", "required": False},
        ],
        "add_label": "Шинэ захиалга",
        "add_title": "Шинэ захиалга",
        "edit_title": "Захиалга засах",
        "noun": "захиалгыг",
        "created_msg": "Шинэ захиалга нэмэгдлээ.",
        "updated_msg": "Захиалга хадгалагдлаа.",
        "deleted_msg": "Захиалга устгагдлаа.",
    },
    "staff": {
        "table": "staff",
        "scope": {},
        "order": "full_name.asc",
        "search": ["full_name", "position", "phone", "email"],
        "label_field": "full_name",
        "fields": [
            {"name": "full_name", "label": "Овог нэр", "type": "text", "required": True},
            {"name": "position", "label": "Албан тушаал", "type": "text", "required": True},
            {"name": "phone", "label": "Утас", "type": "tel", "required": True},
            {"name": "email", "label": "И-мэйл", "type": "email", "required": False},
            {"name": "hired_on", "label": "Ажилд орсон огноо", "type": "date", "required": True},
        ],
        "add_label": "Шинэ ажилтан",
        "add_title": "Шинэ ажилтан нэмэх",
        "edit_title": "Ажилтны мэдээлэл засах",
        "noun": "ажилтныг",
        "created_msg": "Шинэ ажилтан нэмэгдлээ.",
        "updated_msg": "Ажилтны мэдээлэл хадгалагдлаа.",
        "deleted_msg": "Ажилтан устгагдлаа.",
    },
}


# ---------- Supabase холболт (REST API) ----------

def supabase_request(method, table, params, json=None):
    response = requests.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={"apikey": SUPABASE_SECRET_KEY, "Prefer": "return=representation"},
        params=params,
        json=json,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def supabase_get(table, params):
    """Supabase-ийн хүснэгтээс мөр уншина."""
    return supabase_request("GET", table, params)


def supabase_insert(table, values):
    """Шинэ мөр нэмж, нэмэгдсэн мөрийг буцаана."""
    return supabase_request("POST", table, {}, values)


def supabase_update(table, filters, values):
    """Шүүлтүүрт таарсан мөрүүдийг өөрчилж, өөрчлөгдсөн мөрүүдийг буцаана."""
    return supabase_request("PATCH", table, filters, values)


def supabase_delete(table, filters):
    """Шүүлтүүрт таарсан мөрүүдийг устгаж, устгагдсан мөрүүдийг буцаана."""
    return supabase_request("DELETE", table, filters)


def supabase_error_message(error):
    """Supabase-ийн алдааг энгийн монгол мэдэгдэл болгоно."""
    code = ""
    if isinstance(error, requests.HTTPError):
        try:
            code = error.response.json().get("code", "")
        except ValueError:
            pass
    if code == "23505":
        return "Ийм мэдээлэл (жишээ нь и-мэйл) аль хэдийн бүртгэгдсэн байна."
    if code == "23514":
        return "Оруулсан утга зөвшөөрөгдөөгүй байна."
    if code == "23502":
        return "Заавал бөглөх талбар хоосон байна."
    if code in ("42P01", "PGRST205"):
        return "Supabase дээр энэ хүснэгт олдсонгүй. SQL файлаа ажиллуулсан эсэхээ шалгана уу."
    return "Supabase-тэй холбогдоход алдаа гарлаа. Дахин оролдоно уу."


def find_user(email):
    rows = supabase_get("users", {
        "select": "id,email,password_hash,role",
        "email": f"eq.{email}",
        "limit": 1,
    })
    return rows[0] if rows else None


def search_filter(columns, query):
    """Хайлтын үгийг аль нэг баганаас (том жижиг үсэг ялгахгүй) хайх шүүлтүүр."""
    escaped = query.replace("\\", "\\\\").replace('"', '\\"')
    return "(" + ",".join(f'{col}.ilike."*{escaped}*"' for col in columns) + ")"


def list_items(cfg, query=""):
    params = {"select": "*", "order": cfg["order"], **cfg["scope"]}
    if query:
        params["or"] = search_filter(cfg["search"], query)
    return supabase_get(cfg["table"], params)


def get_item(cfg, item_id):
    rows = supabase_get(cfg["table"], {"select": "*", "id": f"eq.{item_id}", "limit": 1, **cfg["scope"]})
    return rows[0] if rows else None


def set_draft_status(draft_id, status):
    """Зөвхөн ноорог төлөвтэй постын төлөвийг солино. Өөрчлөгдсөн мөр байвал True."""
    rows = supabase_update(
        "content_drafts",
        {"id": f"eq.{draft_id}", "status": "eq.draft"},
        {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    return bool(rows)


def check_password(password, password_hash):
    """Нууц үгийг Python дээр bcrypt-ээр шалгана."""
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# Байхгүй и-мэйлээр нэвтрэх үед ч нууц үг шалгасан мэт хугацаа зарцуулна.
# Ингэснээр хариу ирэх хурдаар аль и-мэйл бүртгэлтэй байгааг таах боломжгүй.
DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt()).decode()


# ---------- CSRF хамгаалалт ----------
# Өөр сайтаас манай самбар руу нууцаар маягт илгээхээс хамгаална.

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf", "")
        if not secrets.compare_digest(token, session.get("_csrf", "")) or not token:
            abort(400)


# ---------- Маягт (форм) ----------

def parse_form(cfg, form):
    """Маягтаас ирсэн утгуудыг шалгаж, Supabase руу илгээх хэлбэрт оруулна."""
    data, errors = {}, []
    for field in cfg["fields"]:
        value = form.get(field["name"], "").strip()
        if not value:
            if field["required"]:
                errors.append(f"«{field['label']}» талбарыг бөглөнө үү.")
            data[field["name"]] = field.get("empty")
            continue
        if field["type"] == "select" and value not in field["options"]:
            errors.append(f"«{field['label']}» талбарын утга буруу байна.")
        elif field["type"] == "datetime-local":
            try:
                value = datetime.fromisoformat(value).replace(tzinfo=MN_TZ).isoformat()
            except ValueError:
                errors.append(f"«{field['label']}» талбарын огноо буруу байна.")
        data[field["name"]] = value
    if cfg.get("touch_updated_at"):
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
    return data, errors


def form_values(cfg, item):
    """Supabase-ийн мөрийг маягтад харуулах утга болгоно."""
    values = {}
    for field in cfg["fields"]:
        value = item.get(field["name"])
        if value and field["type"] == "datetime-local":
            value = to_mn_time(value).strftime("%Y-%m-%dT%H:%M")
        values[field["name"]] = value or ""
    return values


def default_values(cfg):
    return {field["name"]: field.get("default", "") for field in cfg["fields"]}


# ---------- Монгол хэлний шүүлтүүрүүд ----------

def to_mn_time(value):
    return datetime.fromisoformat(value).astimezone(MN_TZ)


@app.template_filter("platform")
def platform_name(value):
    return PLATFORM_NAMES.get(value, value)


@app.template_filter("booking_status")
def booking_status(value):
    return BOOKING_STATUSES.get(value, value)


@app.template_filter("mn_date")
def mn_date(value):
    """'2026-11-01T...' эсвэл '2026-11-01' -> '2026 оны 11-р сарын 1'"""
    d = to_mn_time(value) if "T" in value else datetime.fromisoformat(value)
    return f"{d.year} оны {d.month}-р сарын {d.day}"


@app.template_filter("mn_datetime")
def mn_datetime(value):
    """'2026-11-03T06:00:00+00:00' -> '2026.11.03 14:00'"""
    return to_mn_time(value).strftime("%Y.%m.%d %H:%M")


# ---------- Хуудсууд ----------

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "email" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def render_page(template, active, **context):
    return render_template(template, menu=MENU, active=active, email=session["email"], **context)


def render_list(resource, template):
    """Цэсний жагсаалтыг (хайлттай) харуулна."""
    query = request.args.get("q", "").strip()
    try:
        items, error = list_items(RESOURCES[resource], query), None
    except requests.RequestException as e:
        items, error = [], supabase_error_message(e)
    return render_page(template, resource, resource=resource, cfg=RESOURCES[resource],
                       items=items, q=query, error=error)


@app.route("/", methods=["GET", "POST"])
def login():
    if "email" in session:
        return redirect(url_for("content"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        try:
            user = find_user(email)
        except requests.RequestException:
            return render_template("login.html", error="Supabase-тэй холбогдож чадсангүй. Дахин оролдоно уу.")
        password_ok = check_password(password, user["password_hash"] if user else DUMMY_HASH)
        if user and password_ok:
            session.clear()
            session["email"] = user["email"]
            session["role"] = user["role"]
            return redirect(url_for("content"))
        error = "И-мэйл эсвэл нууц үг буруу байна."

    return render_template("login.html", error=error)


@app.route("/dashboard")
def dashboard():
    return redirect(url_for("content"))


@app.route("/content")
@login_required
def content():
    return render_list("content", "content.html")


@app.route("/content/<int:draft_id>/<any(approve, reject):action>", methods=["POST"])
@login_required
def change_draft_status(draft_id, action):
    status, message = DRAFT_ACTIONS[action]
    try:
        if set_draft_status(draft_id, status):
            flash(message, "success")
        else:
            flash("Пост олдсонгүй эсвэл аль хэдийн шийдвэрлэгдсэн байна.", "error")
    except requests.RequestException:
        flash("Төлөвийг сольж чадсангүй. Дахин оролдоно уу.", "error")
    return redirect(url_for("content"))


@app.route("/facebook")
@login_required
def facebook():
    return render_page("soon.html", "facebook", title="Facebook")


@app.route("/chat")
@login_required
def chat():
    return render_page("soon.html", "chat", title="Чат")


@app.route("/bookings")
@login_required
def bookings():
    return render_list("bookings", "bookings.html")


@app.route("/staff")
@login_required
def staff():
    return render_list("staff", "staff.html")


RESOURCE_RULE = "/<any(content, bookings, staff):resource>"


@app.route(RESOURCE_RULE + "/new", methods=["GET", "POST"])
@login_required
def create_item(resource):
    cfg = RESOURCES[resource]
    values, errors = default_values(cfg), []
    if request.method == "POST":
        values = request.form.to_dict()
        data, errors = parse_form(cfg, request.form)
        if not errors:
            try:
                supabase_insert(cfg["table"], {k: v for k, v in data.items() if v is not None})
                flash(cfg["created_msg"], "success")
                return redirect(url_for(resource))
            except requests.RequestException as e:
                errors.append(supabase_error_message(e))
    return render_page("form.html", resource, cfg=cfg, resource=resource,
                       title=cfg["add_title"], values=values, errors=errors)


@app.route(RESOURCE_RULE + "/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_item(resource, item_id):
    cfg = RESOURCES[resource]
    item = get_item(cfg, item_id)
    if item is None:
        flash("Ийм мөр олдсонгүй.", "error")
        return redirect(url_for(resource))

    values, errors = form_values(cfg, item), []
    if request.method == "POST":
        values = request.form.to_dict()
        data, errors = parse_form(cfg, request.form)
        if not errors:
            try:
                supabase_update(cfg["table"], {"id": f"eq.{item_id}", **cfg["scope"]}, data)
                flash(cfg["updated_msg"], "success")
                return redirect(url_for(resource))
            except requests.RequestException as e:
                errors.append(supabase_error_message(e))
    return render_page("form.html", resource, cfg=cfg, resource=resource,
                       title=cfg["edit_title"], values=values, errors=errors)


@app.route(RESOURCE_RULE + "/<int:item_id>/delete", methods=["GET", "POST"])
@login_required
def delete_item(resource, item_id):
    cfg = RESOURCES[resource]
    item = get_item(cfg, item_id)
    if item is None:
        flash("Ийм мөр олдсонгүй.", "error")
        return redirect(url_for(resource))

    # GET: эхлээд заавал асууна. POST: «Тийм, устгах» дарсны дараа л устгана.
    if request.method == "POST":
        try:
            supabase_delete(cfg["table"], {"id": f"eq.{item_id}", **cfg["scope"]})
            flash(cfg["deleted_msg"], "success")
        except requests.RequestException as e:
            flash(supabase_error_message(e), "error")
        return redirect(url_for(resource))
    return render_page("confirm_delete.html", resource, cfg=cfg, resource=resource,
                       label=item[cfg["label_field"]], item_id=item_id)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- HTML загварууд (брэндийн гарын авлагын дагуу) ----------

HEAD = """
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{{ url_for('static', filename='logo.png') }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Manrope:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --primary: #24483F;    /* Үндсэн */
    --secondary: #A9B9A5;  /* Хоёрдогч */
    --accent: #B48A54;     /* Онцлох */
    --base: #F7F4ED;       /* Суурь */
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: 'Manrope', sans-serif;
    background: var(--base);
    color: var(--primary);
    line-height: 1.6;
  }
  h1, h2, h3 { font-family: 'Cormorant Garamond', serif; font-weight: 600; line-height: 1.2; }
  .label { font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
  .card { background: #fff; border: 1px solid var(--secondary); border-radius: 4px; padding: 28px; }
  label { font-size: 13px; font-weight: 600; }
  input, select, textarea {
    width: 100%; padding: 12px 14px; margin: 6px 0 18px;
    font: inherit; color: var(--primary); background: var(--base);
    border: 1px solid var(--secondary); border-radius: 4px;
  }
  textarea { min-height: 120px; resize: vertical; }
  input:focus, select:focus, textarea:focus {
    outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(36,72,63,.12);
  }
  button, .btn {
    display: inline-block; padding: 11px 20px; font: inherit; font-size: 14px; font-weight: 600;
    letter-spacing: .03em; text-align: center; text-decoration: none; white-space: nowrap;
    background: var(--primary); color: var(--base); border: 1px solid var(--primary);
    border-radius: 4px; cursor: pointer;
  }
  button:hover, .btn:hover { background: #1b372f; color: var(--base); }
  .btn-outline { background: transparent; color: var(--primary); border-color: var(--secondary); }
  .btn-outline:hover { background: transparent; color: var(--accent); border-color: var(--accent); }
  .btn-danger { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn-danger:hover { background: #9a7444; border-color: #9a7444; color: #fff; }
  .btn-sm { padding: 6px 12px; font-size: 13px; }
  a { color: var(--primary); }
  a:hover { color: var(--accent); }
</style>
"""

LOGIN_HTML = """
<!doctype html>
<html lang="mn">
<head>""" + HEAD + """
<title>Нэвтрэх · The Wellness Shop</title>
<style>
  body { display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 16px; }
  .wrap { width: 100%; max-width: 400px; text-align: center; }
  .logo { width: 160px; height: 160px; margin-bottom: 28px; }
  .card { text-align: left; }
  .card h1 { margin: 0 0 4px; font-size: 32px; }
  .card .version { display: block; margin: 0 0 8px; font-size: 12px; opacity: .55; }
  .card p.sub { margin: 0 0 24px; font-size: 14px; color: var(--primary); opacity: .75; }
  .card button { width: 100%; padding: 13px; font-size: 15px; }
  .error {
    margin-bottom: 18px; padding: 10px 14px; font-size: 14px;
    border-left: 3px solid var(--accent); background: var(--base);
  }
</style>
</head>
<body>
  <div class="wrap">
    <img class="logo" src="{{ url_for('static', filename='logo.png') }}" alt="The Wellness Shop лого">
    <form class="card" method="post">
      <input type="hidden" name="_csrf" value="{{ csrf_token() }}">
      <h1>Админ самбар</h1>
      <small class="version">v0.2</small>
      <p class="sub">Үргэлжлүүлэхийн тулд нэвтэрнэ үү.</p>
      {% if error %}<div class="error">{{ error }}</div>{% endif %}
      <label for="email">И-мэйл хаяг</label>
      <input id="email" type="email" name="email" placeholder="tanii@email.mn" required autofocus>
      <label for="password">Нууц үг</label>
      <input id="password" type="password" name="password" required>
      <button type="submit">Нэвтрэх</button>
    </form>
  </div>
</body>
</html>
"""

# Нэвтэрсний дараах бүх хуудасны нийтлэг загвар (зүүн цэстэй)
LAYOUT_HTML = """
<!doctype html>
<html lang="mn">
<head>""" + HEAD + """
<title>{% block title %}{% endblock %} · The Wellness Shop</title>
<style>
  body { display: flex; min-height: 100vh; }
  aside {
    width: 240px; flex-shrink: 0; display: flex; flex-direction: column;
    background: #fff; border-right: 1px solid var(--secondary); padding: 24px 16px;
  }
  .brand { display: block; text-align: center; padding-bottom: 20px; margin-bottom: 16px;
           border-bottom: 1px solid var(--secondary); }
  .brand img { width: 96px; height: 96px; }
  nav a {
    display: block; padding: 11px 16px; margin-bottom: 4px; border-radius: 4px;
    font-weight: 600; text-decoration: none;
  }
  nav a:hover { background: var(--base); color: var(--primary); }
  nav a.active { background: var(--primary); color: var(--base); }
  .user { margin-top: auto; padding-top: 16px; border-top: 1px solid var(--secondary);
          font-size: 13px; word-break: break-all; }
  .user a { display: inline-block; margin-top: 6px; font-weight: 600; }
  main { flex: 1; min-width: 0; padding: 40px 32px; }
  .content { max-width: 960px; margin: 0 auto; }
  .page-title { display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
                border-bottom: 1px solid var(--secondary); margin-bottom: 24px; }
  .page-title h1 { margin: 0 0 12px; font-size: 40px; }
  .count { color: var(--accent); }
  .empty { text-align: center; padding: 48px 16px; }
  .flash { margin-bottom: 20px; padding: 12px 16px; font-size: 14px; background: #fff;
           border: 1px solid var(--secondary); border-left: 3px solid var(--primary); border-radius: 4px; }
  .flash.error { border-left-color: var(--accent); }
  .toolbar { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
  .toolbar form { display: flex; gap: 8px; flex: 1; min-width: 240px; margin: 0; }
  .toolbar input { margin: 0; background: #fff; }
  .row-actions { display: flex; gap: 6px; justify-content: flex-end; }
  @media (max-width: 760px) {
    body { flex-direction: column; }
    aside { width: auto; flex-direction: row; flex-wrap: wrap; align-items: center; gap: 8px;
            padding: 12px 16px; border-right: 0; border-bottom: 1px solid var(--secondary); }
    .brand { padding: 0; margin: 0; border: 0; }
    .brand img { width: 52px; height: 52px; }
    nav { display: flex; flex-wrap: wrap; gap: 4px; flex: 1; }
    nav a { margin: 0; padding: 8px 12px; font-size: 14px; }
    .user { margin: 0; padding: 0; border: 0; width: 100%; }
    main { padding: 24px 16px; }
    .page-title h1 { font-size: 32px; }
  }
</style>
{% block style %}{% endblock %}
</head>
<body>
  <aside>
    <a class="brand" href="{{ url_for('content') }}">
      <img src="{{ url_for('static', filename='logo.png') }}" alt="The Wellness Shop лого">
    </a>
    <nav>
      {% for endpoint, label in menu %}
        <a href="{{ url_for(endpoint) }}" class="{{ 'active' if endpoint == active }}">{{ label }}</a>
      {% endfor %}
    </nav>
    <div class="user">
      {{ email }}<br>
      <a href="{{ url_for('logout') }}">Гарах</a>
    </div>
  </aside>
  <main>
    <div class="content">
      {% for category, message in get_flashed_messages(with_categories=true) %}
        <div class="flash {{ category }}">{{ message }}</div>
      {% endfor %}
      {% if error %}<div class="flash error">{{ error }}</div>{% endif %}
      {% block content %}{% endblock %}
    </div>
  </main>
</body>
</html>
"""

# Хайлтын талбар ба «нэмэх» товч (жагсаалттай бүх цэсэнд)
TOOLBAR_HTML = """
<div class="toolbar">
  <form method="get" action="{{ url_for(resource) }}">
    <input type="search" name="q" value="{{ q }}" placeholder="Хайх...">
    <button type="submit" class="btn-outline">Хайх</button>
    {% if q %}<a class="btn btn-outline" href="{{ url_for(resource) }}">Цэвэрлэх</a>{% endif %}
  </form>
  <a class="btn" href="{{ url_for('create_item', resource=resource) }}">+ {{ cfg.add_label }}</a>
</div>
{% if q %}<p style="margin:-8px 0 16px; font-size:14px;">«{{ q }}» гэж хайхад {{ items|length }} илэрц олдлоо.</p>{% endif %}
"""

ROW_ACTIONS_HTML = """
<div class="row-actions">
  <a class="btn btn-outline btn-sm" href="{{ url_for('edit_item', resource=resource, item_id=item.id) }}">Засах</a>
  <a class="btn btn-outline btn-sm" href="{{ url_for('delete_item', resource=resource, item_id=item.id) }}">Устгах</a>
</div>
"""

CONTENT_HTML = """
{% extends "layout.html" %}
{% block title %}Контент{% endblock %}
{% block style %}
<style>
  .post { margin-bottom: 16px; }
  .post-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .post h2 { margin: 8px 0 8px; font-size: 26px; }
  .post p { margin: 0 0 16px; white-space: pre-line; }
  .tag { color: var(--accent); }
  .meta { font-size: 13px; opacity: .7; }
  .post-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .actions { display: flex; gap: 8px; }
  .actions form { margin: 0; }
</style>
{% endblock %}
{% block content %}
  <div class="page-title">
    <h1>Ноорог постууд</h1>
    <span class="label count">Нийт {{ items|length }}</span>
  </div>
  {% include "toolbar.html" %}
  {% for item in items %}
    <article class="card post">
      <div class="post-head">
        <span class="label tag">{{ item.platform|platform }}</span>
        {% include "row_actions.html" %}
      </div>
      <h2>{{ item.title }}</h2>
      <p>{{ item.body }}</p>
      <div class="post-footer">
        <div class="meta">Үүсгэсэн: {{ item.created_at|mn_date }}</div>
        <div class="actions">
          <form method="post" action="{{ url_for('change_draft_status', draft_id=item.id, action='reject') }}"
                onsubmit="return confirm('Энэ постыг татгалзах уу?');">
            <input type="hidden" name="_csrf" value="{{ csrf_token() }}">
            <button type="submit" class="btn-outline">Татгалзах</button>
          </form>
          <form method="post" action="{{ url_for('change_draft_status', draft_id=item.id, action='approve') }}">
            <input type="hidden" name="_csrf" value="{{ csrf_token() }}">
            <button type="submit">Батлах</button>
          </form>
        </div>
      </div>
    </article>
  {% else %}
    <div class="card empty">{{ 'Хайлтад тохирох пост олдсонгүй.' if q else 'Одоогоор ноорог пост алга байна.' }}</div>
  {% endfor %}
{% endblock %}
"""

SOON_HTML = """
{% extends "layout.html" %}
{% block title %}{{ title }}{% endblock %}
{% block content %}
  <div class="page-title">
    <h1>{{ title }}</h1>
  </div>
  <div class="card empty">
    <h2 style="margin:0 0 8px; font-size:32px;">Удахгүй</h2>
    <p style="margin:0; opacity:.75;">Энэ хэсэг одоогоор хийгдэж байна.</p>
  </div>
{% endblock %}
"""

TABLE_STYLE = """
<style>
  .table-wrap { overflow-x: auto; background: #fff; border: 1px solid var(--secondary); border-radius: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { padding: 12px 16px; text-align: left; vertical-align: top; }
  th { background: var(--base); border-bottom: 1px solid var(--secondary); white-space: nowrap; }
  tr + tr td { border-top: 1px solid var(--base); }
  td.nowrap { white-space: nowrap; }
  .muted { opacity: .55; }
  .status { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px;
            font-weight: 600; white-space: nowrap; }
  .status-pending   { background: var(--accent); color: #fff; }
  .status-confirmed { background: var(--primary); color: var(--base); }
  .status-completed { background: var(--secondary); color: var(--primary); }
  .status-cancelled { border: 1px solid var(--secondary); color: var(--primary); opacity: .7; }
</style>
"""

BOOKINGS_HTML = """
{% extends "layout.html" %}
{% block title %}Захиалга{% endblock %}
{% block style %}""" + TABLE_STYLE + """{% endblock %}
{% block content %}
  <div class="page-title">
    <h1>Захиалга</h1>
    <span class="label count">Нийт {{ items|length }}</span>
  </div>
  {% include "toolbar.html" %}
  {% if items %}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Огноо, цаг</th>
            <th>Үйлчлүүлэгч</th>
            <th>Утас</th>
            <th>И-мэйл</th>
            <th>Үйлчилгээ</th>
            <th>Төлөв</th>
            <th>Тэмдэглэл</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {% for item in items %}
            <tr>
              <td class="nowrap">{{ item.booking_time|mn_datetime }}</td>
              <td>{{ item.customer_name }}</td>
              <td class="nowrap">{{ item.phone }}</td>
              <td>{{ item.email or '—' }}</td>
              <td>{{ item.service }}</td>
              <td><span class="status status-{{ item.status }}">{{ item.status|booking_status }}</span></td>
              <td>{% if item.note %}{{ item.note }}{% else %}<span class="muted">—</span>{% endif %}</td>
              <td>{% include "row_actions.html" %}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <div class="card empty">{{ 'Хайлтад тохирох захиалга олдсонгүй.' if q else 'Одоогоор захиалга алга байна.' }}</div>
  {% endif %}
{% endblock %}
"""

STAFF_HTML = """
{% extends "layout.html" %}
{% block title %}Ажилтан{% endblock %}
{% block style %}""" + TABLE_STYLE + """{% endblock %}
{% block content %}
  <div class="page-title">
    <h1>Ажилтан</h1>
    <span class="label count">Нийт {{ items|length }}</span>
  </div>
  {% include "toolbar.html" %}
  {% if items %}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Овог нэр</th>
            <th>Албан тушаал</th>
            <th>Утас</th>
            <th>И-мэйл</th>
            <th>Ажилд орсон</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {% for item in items %}
            <tr>
              <td>{{ item.full_name }}</td>
              <td>{{ item.position }}</td>
              <td class="nowrap">{{ item.phone }}</td>
              <td>{% if item.email %}{{ item.email }}{% else %}<span class="muted">—</span>{% endif %}</td>
              <td class="nowrap">{{ item.hired_on|mn_date }}</td>
              <td>{% include "row_actions.html" %}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% elif not error %}
    <div class="card empty">{{ 'Хайлтад тохирох ажилтан олдсонгүй.' if q else 'Одоогоор ажилтан бүртгэгдээгүй байна.' }}</div>
  {% endif %}
{% endblock %}
"""

# Нэмэх, засах маягт (бүх цэсэнд нэг загвар)
FORM_HTML = """
{% extends "layout.html" %}
{% block title %}{{ title }}{% endblock %}
{% block content %}
  <div class="page-title">
    <h1>{{ title }}</h1>
  </div>
  {% for message in errors %}
    <div class="flash error">{{ message }}</div>
  {% endfor %}
  <form class="card" method="post" style="max-width:640px;">
    <input type="hidden" name="_csrf" value="{{ csrf_token() }}">
    {% for f in cfg.fields %}
      <label for="{{ f.name }}">{{ f.label }}{% if f.required %} *{% endif %}</label>
      {% if f.type == 'textarea' %}
        <textarea id="{{ f.name }}" name="{{ f.name }}" {{ 'required' if f.required }}>{{ values.get(f.name, '') }}</textarea>
      {% elif f.type == 'select' %}
        <select id="{{ f.name }}" name="{{ f.name }}" {{ 'required' if f.required }}>
          {% for key, text in f.options.items() %}
            <option value="{{ key }}" {{ 'selected' if values.get(f.name) == key }}>{{ text }}</option>
          {% endfor %}
        </select>
      {% else %}
        <input id="{{ f.name }}" name="{{ f.name }}" type="{{ f.type }}"
               value="{{ values.get(f.name, '') }}" {{ 'required' if f.required }}>
      {% endif %}
    {% endfor %}
    <div style="display:flex; gap:8px;">
      <button type="submit">Хадгалах</button>
      <a class="btn btn-outline" href="{{ url_for(resource) }}">Болих</a>
    </div>
  </form>
{% endblock %}
"""

# Устгахаас өмнө заавал асуух хуудас
CONFIRM_DELETE_HTML = """
{% extends "layout.html" %}
{% block title %}Устгах{% endblock %}
{% block content %}
  <div class="page-title">
    <h1>Устгахдаа итгэлтэй байна уу?</h1>
  </div>
  <div class="card" style="max-width:640px;">
    <p style="margin-top:0;">Та <strong>«{{ label }}»</strong> {{ cfg.noun }} устгах гэж байна.</p>
    <p>Устгасан мэдээллийг <strong>буцааж сэргээх боломжгүй</strong>.</p>
    <form method="post" style="display:flex; gap:8px; margin:24px 0 0;">
      <input type="hidden" name="_csrf" value="{{ csrf_token() }}">
      <button type="submit" class="btn-danger">Тийм, устгах</button>
      <a class="btn btn-outline" href="{{ url_for(resource) }}">Үгүй, буцах</a>
    </form>
  </div>
{% endblock %}
"""

app.jinja_loader = DictLoader({
    "login.html": LOGIN_HTML,
    "layout.html": LAYOUT_HTML,
    "toolbar.html": TOOLBAR_HTML,
    "row_actions.html": ROW_ACTIONS_HTML,
    "content.html": CONTENT_HTML,
    "soon.html": SOON_HTML,
    "bookings.html": BOOKINGS_HTML,
    "staff.html": STAFF_HTML,
    "form.html": FORM_HTML,
    "confirm_delete.html": CONFIRM_DELETE_HTML,
})


if __name__ == "__main__":
    # Debug горим алдааны үед кодын дэлгэрэнгүйг харуулдаг тул анхнаасаа унтраастай.
    # Хөгжүүлэлтийн үед асаах бол: $env:FLASK_DEBUG="1"; python app.py
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5000)
