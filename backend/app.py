import os
from functools import wraps

import psycopg2
from flask import Flask, redirect, render_template, request, session, url_for
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
            return ("仅审评员可操作香气词库与交评", 403)
        return fn(*args, **kwargs)

    return wrap


def get_terms(cur):
    cur.execute("SELECT id, term FROM aroma_terms ORDER BY id")
    return cur.fetchall()


def get_min_terms(cur):
    cur.execute("SELECT value FROM app_settings WHERE key = 'min_aroma_terms'")
    row = cur.fetchone()
    return row["value"] if row else 0


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
        terms = [t["term"] for t in get_terms(cur)]
        min_terms = get_min_terms(cur)
    return render_template(
        "home.html",
        rows=rows,
        terms=terms,
        min_terms=min_terms,
        can_write=session.get("role") == "writer",
    )


@app.get("/aroma-library")
@login_required
def aroma_library():
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        terms = get_terms(cur)
        min_terms = get_min_terms(cur)
    return render_template(
        "aroma_library.html",
        terms=terms,
        min_terms=min_terms,
        can_write=session.get("role") == "writer",
    )


@app.post("/aroma-library/terms")
@writer_required
def add_term():
    term = request.form.get("term", "").strip()
    if not term:
        return ("词条不能为空", 400)
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            cur.execute("INSERT INTO aroma_terms (term) VALUES (%s)", (term,))
            conn.commit()
        except psycopg2.IntegrityError:
            return ("该香气词已在词库中", 400)
    return redirect(url_for("aroma_library"))


@app.post("/aroma-library/terms/<int:term_id>/delete")
@writer_required
def delete_term(term_id):
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 只删词库条目；已存档审评行的 aroma_terms 快照不受影响
        cur.execute("DELETE FROM aroma_terms WHERE id = %s", (term_id,))
        conn.commit()
    return redirect(url_for("aroma_library"))


@app.post("/aroma-library/min-terms")
@writer_required
def set_min_terms():
    raw = request.form.get("min_terms", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return ("最少词数必须是整数", 400)
    if value < 0:
        return ("最少词数不能为负", 400)
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO app_settings (key, value) VALUES ('min_aroma_terms', %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            (value,),
        )
        conn.commit()
    return redirect(url_for("aroma_library"))


@app.post("/cuppings")
@writer_required
def create():
    aroma = float(request.form["aroma"])
    taste = float(request.form["taste"])
    liquor = float(request.form["liquor"])
    lot = request.form["lot"].strip()
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        valid_terms = {t["term"] for t in get_terms(cur)}
        minimum = get_min_terms(cur)

        # 勾选原文去重后必须全部来自当前词库，且数量不少于审评员设定的最少词数
        selected = list(dict.fromkeys(request.form.getlist("aroma_terms")))
        invalid = [t for t in selected if t not in valid_terms]
        if invalid:
            return ("香气词必须来自香气词库", 400)
        if len(selected) < minimum:
            return (f"香气词至少勾选 {minimum} 个，当前仅勾选 {len(selected)} 个", 400)

        verdict, note, score = weigh(aroma, taste, liquor)
        cur.execute(
            """INSERT INTO cuppings (lot, aroma, taste, liquor, score, verdict, note, created_by, aroma_terms)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (lot, aroma, taste, liquor, score, verdict, note, session["user"], selected),
        )
        row = cur.fetchone()
        conn.commit()
    if request.headers.get("HX-Request"):
        return render_template("_row.html", row=row)
    return redirect(url_for("home"))
