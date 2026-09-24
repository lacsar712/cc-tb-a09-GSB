import os
from functools import wraps

import psycopg2
from flask import Flask, abort, redirect, render_template, request, session, url_for
from psycopg2.extras import RealDictCursor

from rules import weigh

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tea-cupping-dev-secret")

ACCOUNTS = {
    "taster": {"password": "tea123456", "role": "writer"},
    "observer": {"password": "look123456", "role": "reader"},
}


def db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def login_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrap


def writer_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "writer":
            abort(403)
        return fn(*args, **kwargs)

    return wrap


def writer_required_htmx(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if session.get("role") != "writer":
            return ("仅审评员可操作", 403)
        return fn(*args, **kwargs)

    return wrap


@app.get("/health")
def health():
    return {"status": "ok", "service": "tea-blend-cupping"}


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        account = ACCOUNTS.get(name)
        if not account or account["password"] != request.form.get("password", ""):
            error = "用户名或密码错误"
        else:
            session["user"] = name
            session["role"] = account["role"]
            return redirect(url_for("home"))
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def home():
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM cuppings ORDER BY id DESC")
        rows = cur.fetchall()
        cur.execute("SELECT term FROM aroma_terms ORDER BY id")
        terms = [r["term"] for r in cur.fetchall()]
        cur.execute("SELECT value FROM app_settings WHERE key = 'min_terms'")
        setting = cur.fetchone()
    min_terms = int(setting["value"]) if setting else 0
    can_write = session.get("role") == "writer"
    return render_template(
        "home.html",
        rows=rows,
        can_write=can_write,
        terms=terms,
        min_terms=min_terms,
    )


@app.post("/cuppings")
@login_required
@writer_required_htmx
def create():
    aroma = float(request.form["aroma"])
    taste = float(request.form["taste"])
    liquor = float(request.form["liquor"])
    lot = request.form["lot"].strip()
    # 去重、保持勾选顺序
    selected = list(dict.fromkeys(request.form.getlist("aroma_terms")))
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT term FROM aroma_terms")
        library = {r["term"] for r in cur.fetchall()}
        cur.execute("SELECT value FROM app_settings WHERE key = 'min_terms'")
        setting = cur.fetchone()
    min_terms = int(setting["value"]) if setting else 0
    # 词必须来自词库
    if not library.issuperset(selected):
        return ("香气词必须来自香气词库", 400)
    # 至少勾选审评员设定的最少词数
    if len(selected) < min_terms:
        return (f"至少勾选 {min_terms} 个香气词（当前 {len(selected)} 个）", 400)
    verdict, note, score = weigh(aroma, taste, liquor)
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO cuppings (lot, aroma, taste, liquor, score, verdict, note, created_by, selected_terms)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (lot, aroma, taste, liquor, score, verdict, note, session["user"], selected),
        )
        row = cur.fetchone()
        conn.commit()
    if request.headers.get("HX-Request"):
        return render_template("_row.html", row=row)
    return redirect(url_for("detail", cupping_id=row["id"]))


@app.get("/library")
@login_required
def library():
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM aroma_terms ORDER BY id")
        terms = cur.fetchall()
        cur.execute("SELECT value FROM app_settings WHERE key = 'min_terms'")
        setting = cur.fetchone()
    min_terms = int(setting["value"]) if setting else 0
    return render_template(
        "library.html",
        terms=terms,
        min_terms=min_terms,
        can_write=session.get("role") == "writer",
        message=request.args.get("message", ""),
    )


@app.post("/library/terms")
@login_required
@writer_required_htmx
def add_term():
    term = request.form.get("term", "").strip()
    if not term:
        return ("词条不能为空", 400)
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            cur.execute(
                "INSERT INTO aroma_terms (term, created_by) VALUES (%s, %s)",
                (term, session["user"]),
            )
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return ("该词条已存在", 400)
    return redirect(url_for("library", message="已添加词条"))


@app.post("/library/terms/<int:term_id>/delete")
@login_required
@writer_required_htmx
def delete_term(term_id):
    with db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM aroma_terms WHERE id = %s", (term_id,))
        conn.commit()
    return redirect(url_for("library", message="已删除词条"))


@app.post("/library/min-terms")
@login_required
@writer_required_htmx
def set_min_terms():
    try:
        value = int(request.form.get("min_terms", ""))
    except ValueError:
        return ("最少词数必须是不小于 0 的整数", 400)
    if value < 0:
        return ("最少词数必须是不小于 0 的整数", 400)
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app_settings (key, value) VALUES ('min_terms', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (str(value),),
        )
        conn.commit()
    return redirect(url_for("library", message="最少词数已更新"))


@app.get("/cuppings/<int:cupping_id>")
@login_required
def detail(cupping_id):
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM cuppings WHERE id = %s", (cupping_id,))
        row = cur.fetchone()
    if row is None:
        abort(404)
    return render_template("detail.html", row=row)
