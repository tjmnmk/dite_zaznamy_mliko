import os
import io
import csv
import sqlite3
import threading
from datetime import datetime, timedelta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, request, redirect, url_for, render_template, g, send_from_directory, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_wtf.csrf import CSRFProtect

import redis

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Umisteni databaze lze prepsat prostredim: export DITE_DB=/cesta/k/db.sqlite
DB_PATH = os.environ.get("DITE_DB", os.path.expanduser("~/dite.db"))

# Umisteni Redisu lze prepsat prostredim: export DITE_REDIS=redis://localhost:6379/0
REDIS_URL = os.environ.get("DITE_REDIS", "redis://localhost:6379/0")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# CSRF ochrana formulare (Flask-WTF)
csrf = CSRFProtect(app)

_redis = redis.from_url(REDIS_URL, decode_responses=False)

# Tajny klic pro session/CSRF sdileny vsem workery pres Redis.
# Lze prepsat prostredim: export DITE_SECRET_KEY=...
if os.environ.get("DITE_SECRET_KEY"):
    app.secret_key = os.environ["DITE_SECRET_KEY"]
else:
    # Atomicka inicializace: SETNX vlozi klic jen pokud jeste neexisti, pak GET nacte platnou hodnotu
    _redis.setnx(b"dite:secret_key", os.urandom(32))
    app.secret_key = _redis.get(b"dite:secret_key")


def graf_cache_get(klic):
    if _redis is None:
        return None
    return _redis.get(klic)


def graf_cache_set(klic, data):
    if _redis is None:
        return
    _redis.set(klic, data)


def graf_cache_invalidate():
    if _redis is None:
        return
    _redis.delete(b"graf:casovy", b"graf:denni", b"graf:hlava")


def _vygeneruj_grafy():
    """Pregeneruje vechny grafy do cache (volat v app kontextu)."""
    try:
        graf_png()
    except Exception:
        pass
    try:
        graf_denni_png()
    except Exception:
        pass
    try:
        graf_hlava_png()
    except Exception:
        pass


def graf_cache_refresh_async():
    """Smaže cache a na pozadí přegeneruje grafy, aby se na to nečekalo."""
    graf_cache_invalidate()
    t = threading.Thread(
        target=lambda: app.app_context().push() or _vygeneruj_grafy(),
        daemon=True,
    )
    t.start()


def pred_jakou_dobou(cas_str):
    """Vrati lidsky citelnou dobu od daneho casu, napr. 'před 2 h 15 min'."""
    if not cas_str:
        return None
    try:
        dt = datetime.strptime(cas_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    delta = datetime.now() - dt
    if delta < timedelta(0):
        delta = timedelta(0)
    dnu = delta.days
    hodin = delta.seconds // 3600
    minut = (delta.seconds % 3600) // 60
    if dnu > 0:
        return f"před {dnu} d {hodin} h"
    if hodin > 0:
        return f"před {hodin} h {minut} min"
    if minut > 0:
        return f"před {minut} min"
    return "právě teď"


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
            zvraceni INTEGER NOT NULL DEFAULT 0,
            pozice_hlavy INTEGER
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
        pozice_hlavy_raw = request.form.get("pozice_hlavy", "") or ""

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

        pozice_hlavy = None
        if chyba is None and pozice_hlavy_raw:
            try:
                pozice_hlavy = int(pozice_hlavy_raw)
                if not 0 <= pozice_hlavy <= 10:
                    chyba = "Pozice hlavy musí být 0-10."
            except ValueError:
                chyba = "Pozice hlavy musí být číslo."

        if chyba is None:
            db = get_db()
            db.execute(
                "INSERT INTO kojeni (cas, mnozstvi, stolice, moc, zvraceni, pozice_hlavy) VALUES (?, ?, ?, ?, ?, ?)",
                (cas, mnozstvi_int, stolice, moc, zvraceni, pozice_hlavy),
            )
            db.commit()
            graf_cache_refresh_async()
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
    db = get_db()
    zaznamy = db.execute(
        "SELECT * FROM kojeni ORDER BY datetime(cas) DESC"
    ).fetchall()
    posledni_krmeni = db.execute(
        "SELECT cas, mnozstvi FROM kojeni ORDER BY datetime(cas) DESC LIMIT 1"
    ).fetchone()
    posledni_moc = db.execute(
        "SELECT cas FROM kojeni WHERE moc = 1 ORDER BY datetime(cas) DESC LIMIT 1"
    ).fetchone()
    posledni_stolice = db.execute(
        "SELECT cas FROM kojeni WHERE stolice = 1 ORDER BY datetime(cas) DESC LIMIT 1"
    ).fetchone()
    return render_template(
        "prehled.html",
        zaznamy=zaznamy,
        posledni_krmeni=posledni_krmeni,
        posledni_moc=posledni_moc,
        posledni_stolice=posledni_stolice,
        krmeni_pred=pred_jakou_dobou(posledni_krmeni["cas"] if posledni_krmeni else None),
        moc_pred=pred_jakou_dobou(posledni_moc["cas"] if posledni_moc else None),
        stolice_pred=pred_jakou_dobou(posledni_stolice["cas"] if posledni_stolice else None),
    )


@app.route("/smazat/<int:zaznam_id>", methods=["POST"])
def smazat(zaznam_id):
    db = get_db()
    db.execute("DELETE FROM kojeni WHERE id = ?", (zaznam_id,))
    db.commit()
    graf_cache_refresh_async()
    return redirect(url_for("prehled"))


@app.route("/favicon.png")
def favicon():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "favicon.png", mimetype="image/png")


@app.route("/export.csv")
def export_csv():
    zaznamy = get_db().execute(
        "SELECT cas, mnozstvi, stolice, moc, zvraceni, pozice_hlavy FROM kojeni ORDER BY datetime(cas) ASC"
    ).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["čas", "množství (ml)", "stolice", "moč", "zvracení", "pozice hlavy"])
    for r in zaznamy:
        writer.writerow([r["cas"], r["mnozstvi"], r["stolice"], r["moc"], r["zvraceni"], r["pozice_hlavy"]])
    csv_data = buf.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=kojeni_export.csv"},
    )


@app.route("/deni-prehled")
def deni_prehled():
    radky = get_db().execute(
        "SELECT substr(cas, 1, 10) AS den, COUNT(*) AS pocet, SUM(mnozstvi) AS celkem, "
        "AVG(pozice_hlavy) AS prumer_hlavy "
        "FROM kojeni GROUP BY substr(cas, 1, 10) ORDER BY den DESC"
    ).fetchall()
    dny = [{"den": r["den"], "pocet": r["pocet"], "celkem": r["celkem"],
            "prumer_hlavy": round(r["prumer_hlavy"]) if r["prumer_hlavy"] is not None else None}
            for r in radky]
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
        "SELECT cas, mnozstvi, pozice_hlavy FROM kojeni ORDER BY datetime(cas) ASC"
    ).fetchall()

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    if zaznamy:
        casy = [datetime.strptime(r["cas"], "%Y-%m-%d %H:%M:%S") for r in zaznamy]
        mnozstvi = [r["mnozstvi"] for r in zaznamy]
        ax.plot(casy, mnozstvi, marker="o", linewidth=2, color="#2563eb")
        ax.fill_between(casy, mnozstvi, alpha=0.15, color="#2563eb")

        # Barevne body podle pozice hlavy (cervena = leva, modra = prava)
        for r in zaznamy:
            if r["pozice_hlavy"] is not None:
                barva = "#dc2626" if r["pozice_hlavy"] <= 5 else "#1d4ed8"
                ax.plot(datetime.strptime(r["cas"], "%Y-%m-%d %H:%M:%S"), r["mnozstvi"],
                        marker="o", markersize=10, color=barva, zorder=5)

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


@app.route("/graf-hlava.png")
def graf_hlava_png():
    cached = graf_cache_get(b"graf:hlava")
    if cached is not None:
        return Response(cached, mimetype="image/png")

    zaznamy = get_db().execute(
        "SELECT cas, pozice_hlavy FROM kojeni WHERE pozice_hlavy IS NOT NULL ORDER BY datetime(cas) ASC"
    ).fetchall()

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    if zaznamy:
        casy = [datetime.strptime(r["cas"], "%Y-%m-%d %H:%M:%S") for r in zaznamy]
        hlavy = [r["pozice_hlavy"] for r in zaznamy]
        ax.plot(casy, hlavy, marker="o", linewidth=2, color="#16a34a")
        ax.fill_between(casy, hlavy, alpha=0.15, color="#16a34a")
        ax.set_ylabel("Pozice hlavy")
        ax.set_xlabel("Čas")
        ax.set_title("Pozice hlavy v čase (z pohledu rodiče)")
        ax.set_ylim(-0.5, 10.5)
        ax.set_yticks(range(0, 11))
        ax.set_yticklabels(["levá", "", "", "", "", "", "", "", "", "", "pravá"])
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
    graf_cache_set(b"graf:hlava", png)
    return Response(png, mimetype="image/png")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
