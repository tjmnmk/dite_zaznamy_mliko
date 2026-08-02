import os
import io
import sqlite3
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, request, redirect, url_for, render_template, g, send_from_directory, Response
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    import redis
except ImportError:
    redis = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Umisteni databaze lze prepsat prostredim: export DITE_DB=/cesta/k/db.sqlite
DB_PATH = os.environ.get("DITE_DB", os.path.expanduser("~/dite.db"))

# Umisteni Redisu lze prepsat prostredim: export DITE_REDIS=redis://localhost:6379/0
REDIS_URL = os.environ.get("DITE_REDIS", "redis://localhost:6379/0")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

_redis = None
if redis is not None:
    try:
        _redis = redis.from_url(REDIS_URL, decode_responses=False)
        _redis.ping()
    except Exception:
        _redis = None


GRAF_CACHE_TTL = 900  # 15 minut


def graf_cache_get(klic):
    if _redis is None:
        return None
    return _redis.get(klic)


def graf_cache_set(klic, data):
    if _redis is None:
        return
    _redis.setex(klic, GRAF_CACHE_TTL, data)


def graf_cache_invalidate():
    if _redis is None:
        return
    _redis.delete(b"graf:casovy", b"graf:denni")


def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kojeni (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cas TEXT NOT NULL,
            mnozstvi INTEGER NOT NULL,
            stolice INTEGER NOT NULL DEFAULT 0,
            moc INTEGER NOT NULL DEFAULT 0,
            zvraceni INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def posledni_mnozstvi():
    row = get_db().execute(
        "SELECT mnozstvi FROM kojeni ORDER BY datetime(cas) DESC LIMIT 1"
    ).fetchone()
    return row["mnozstvi"] if row else 0


@app.route("/", methods=["GET", "POST"])
def formular():
    ulozeno = False
    chyba = None
    if request.method == "POST":
        mnozstvi = request.form.get("mnozstvi", "0") or "0"
        stolice = 1 if request.form.get("stolice") else 0
        moc = 1 if request.form.get("moc") else 0
        zvraceni = 1 if request.form.get("zvraceni") else 0
        cas_raw = request.form.get("cas", "").strip()

        # Validace casu - ocekavame format YYYY-MM-DDTHH:MM (z datetime-local)
        cas = None
        if cas_raw:
            try:
                dt = datetime.strptime(cas_raw, "%Y-%m-%dT%H:%M")
                cas = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                chyba = "Neplatný formát času."
        else:
            cas = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if chyba is None:
            try:
                mnozstvi_int = int(mnozstvi)
                if mnozstvi_int < 0:
                    chyba = "Množství nemůže být záporné."
            except ValueError:
                chyba = "Množství musí být číslo."

        if chyba is None:
            db = get_db()
            db.execute(
                "INSERT INTO kojeni (cas, mnozstvi, stolice, moc, zvraceni) VALUES (?, ?, ?, ?, ?)",
                (cas, mnozstvi_int, stolice, moc, zvraceni),
            )
            db.commit()
            graf_cache_invalidate()
            ulozeno = True
    aktualni_cas = datetime.now().strftime("%Y-%m-%dT%H:%M")
    return render_template(
        "formular.html",
        predvyplneno=posledni_mnozstvi(),
        aktualni_cas=aktualni_cas,
        ulozeno=ulozeno,
        chyba=chyba,
    )


@app.route("/prehled")
def prehled():
    zaznamy = get_db().execute(
        "SELECT * FROM kojeni ORDER BY datetime(cas) DESC"
    ).fetchall()
    return render_template("prehled.html", zaznamy=zaznamy)


@app.route("/smazat/<int:zaznam_id>", methods=["POST"])
def smazat(zaznam_id):
    db = get_db()
    db.execute("DELETE FROM kojeni WHERE id = ?", (zaznam_id,))
    db.commit()
    graf_cache_invalidate()
    return redirect(url_for("prehled"))


@app.route("/favicon.png")
def favicon():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "favicon.png", mimetype="image/png")


@app.route("/deni-prehled")
def deni_prehled():
    radky = get_db().execute(
        "SELECT substr(cas, 1, 10) AS den, COUNT(*) AS pocet, SUM(mnozstvi) AS celkem "
        "FROM kojeni GROUP BY substr(cas, 1, 10) ORDER BY den DESC"
    ).fetchall()
    dny = [{"den": r["den"], "pocet": r["pocet"], "celkem": r["celkem"]} for r in radky]
    return render_template("deni_prehled.html", dny=dny)


@app.route("/graf")
def graf():
    zaznamy = get_db().execute(
        "SELECT cas, mnozstvi FROM kojeni ORDER BY datetime(cas) ASC"
    ).fetchall()
    return render_template("graf.html", ma_data=len(zaznamy) > 0)


@app.route("/graf.png")
def graf_png():
    cached = graf_cache_get(b"graf:casovy")
    if cached is not None:
        return Response(cached, mimetype="image/png")

    zaznamy = get_db().execute(
        "SELECT cas, mnozstvi FROM kojeni ORDER BY datetime(cas) ASC"
    ).fetchall()

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    if zaznamy:
        casy = [datetime.strptime(r["cas"], "%Y-%m-%d %H:%M:%S") for r in zaznamy]
        mnozstvi = [r["mnozstvi"] for r in zaznamy]
        ax.plot(casy, mnozstvi, marker="o", linewidth=2, color="#2563eb")
        ax.fill_between(casy, mnozstvi, alpha=0.15, color="#2563eb")
        ax.set_ylabel("Množství (ml)")
        ax.set_xlabel("Čas")
        ax.set_title("Množství mléka v čase")
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate()
    else:
        ax.text(0.5, 0.5, "Žádné záznamy", ha="center", va="center", fontsize=16, color="#888")
        ax.set_axis_off()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    png = buf.getvalue()
    graf_cache_set(b"graf:casovy", png)
    return Response(png, mimetype="image/png")


@app.route("/graf-denni.png")
def graf_denni_png():
    cached = graf_cache_get(b"graf:denni")
    if cached is not None:
        return Response(cached, mimetype="image/png")

    radky = get_db().execute(
        "SELECT substr(cas, 1, 10) AS den, COUNT(*) AS pocet, SUM(mnozstvi) AS celkem "
        "FROM kojeni GROUP BY substr(cas, 1, 10) ORDER BY den ASC"
    ).fetchall()

    fig, ax1 = plt.subplots(figsize=(8, 4), dpi=150)
    if radky:
        dny = [r["den"] for r in radky]
        pocet = [r["pocet"] for r in radky]
        celkem = [r["celkem"] for r in radky]

        x = range(len(dny))
        ax1.bar(x, celkem, color="#2563eb", alpha=0.85, label="Mléko (ml)")
        ax1.set_ylabel("Mléko (ml)", color="#2563eb")
        ax1.tick_params(axis="y", labelcolor="#2563eb")
        ax1.set_xticks(list(x))
        ax1.set_xticklabels(dny, rotation=45, ha="right")
        ax1.grid(True, alpha=0.3, axis="y")

        ax2 = ax1.twinx()
        ax2.plot(x, pocet, marker="o", linewidth=2, color="#dc2626", label="Počet kojení")
        ax2.set_ylabel("Počet kojení", color="#dc2626")
        ax2.tick_params(axis="y", labelcolor="#dc2626")

        ax1.set_title("Denní spotřeba mléka a počet kojení")
        fig.tight_layout()
    else:
        ax1.text(0.5, 0.5, "Žádné záznamy", ha="center", va="center", fontsize=16, color="#888")
        ax1.set_axis_off()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    png = buf.getvalue()
    graf_cache_set(b"graf:denni", png)
    return Response(png, mimetype="image/png")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
