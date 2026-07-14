import os
import io
import sqlite3
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, request, redirect, url_for, render_template, g, send_from_directory, Response
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Umisteni databaze lze prepsat prostredim: export DITE_DB=/cesta/k/db.sqlite
DB_PATH = os.environ.get("DITE_DB", os.path.expanduser("~/dite.db"))

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


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
    return redirect(url_for("prehled"))


@app.route("/favicon.png")
def favicon():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "favicon.png", mimetype="image/png")


@app.route("/graf")
def graf():
    zaznamy = get_db().execute(
        "SELECT cas, mnozstvi FROM kojeni ORDER BY datetime(cas) ASC"
    ).fetchall()
    return render_template("graf.html", ma_data=len(zaznamy) > 0)


@app.route("/graf.png")
def graf_png():
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
    buf.seek(0)
    return Response(buf, mimetype="image/png")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
