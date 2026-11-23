# -*- coding: utf-8 -*-
# O'YIN BOT (DEMO) - Crash + PvP + Admin panel (/ayubrizofactory)
# Pydroid 3: pip dan "flask" o'rnating, keyin Run. Brauzerda: http://127.0.0.1:5000
# 2-chi test foydalanuvchi: http://127.0.0.1:5000/?u=2&n=Ali
import os, time, json, math, random, sqlite3, threading, hmac, hashlib
import urllib.request, urllib.parse
from functools import wraps
from flask import Flask, request, jsonify, session, redirect, render_template_string

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")   # Render > Environment ga yozing
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")   # Render > Environment da o'zgartiring
SECRET = os.environ.get("SECRET", "o'zgartiring-uzun-sir")
DB = os.environ.get("DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "game.db")

app = Flask(__name__)
app.secret_key = SECRET
lock = threading.RLock()

DEFAULTS = {"min_bet": "1000", "start_balance": "10000", "commission": "5",
            "house_edge": "3", "channels": ""}

def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript("""
        create table if not exists users(id integer primary key, name text, username text, photo text,
            balance integer default 0, banned integer default 0, games integer default 0,
            wins integer default 0, created integer);
        create table if not exists settings(k text primary key, v text);
        create table if not exists promos(code text primary key, amount integer, max_uses integer, used integer default 0);
        create table if not exists promo_used(code text, uid integer, primary key(code, uid));
        create table if not exists tasks(id integer primary key autoincrement, title text, reward integer, url text);
        create table if not exists task_done(task_id integer, uid integer, primary key(task_id, uid));
        create table if not exists duels(id integer primary key autoincrement, creator integer, cname text,
            bet integer, status text, joiner integer, jname text, winner integer, created integer);
        """)

def S(k):
    with db() as c:
        r = c.execute("select v from settings where k=?", (k,)).fetchone()
    return r["v"] if r else DEFAULTS[k]

# ---------------- AUTH ----------------
def verify(init):
    try:
        d = dict(urllib.parse.parse_qsl(init))
        h = d.pop("hash", "")
        s = "\n".join(f"{k}={d[k]}" for k in sorted(d))
        key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        if hmac.new(key, s.encode(), hashlib.sha256).hexdigest() != h:
            return None
        return json.loads(d["user"])
    except Exception:
        return None

def auth():
    init = request.headers.get("X-Init", "")
    if init:
        if BOT_TOKEN:
            u = verify(init)
        else:
            try:
                u = json.loads(dict(urllib.parse.parse_qsl(init)).get("user", "{}"))
            except Exception:
                u = None
        if not u or "id" not in u:
            return None
        uid = u["id"]
        name = (u.get("first_name", "") + " " + u.get("last_name", "")).strip() or "User"
        un, photo = u.get("username", ""), u.get("photo_url", "")
    elif not BOT_TOKEN:  # faqat demo/test rejim
        try:
            uid = int(request.headers.get("X-Uid", "0"))
        except Exception:
            uid = 0
        if not uid:
            return None
        name = urllib.parse.unquote(request.headers.get("X-Name", "Test"))[:30]
        un, photo = "", ""
    else:
        return None
    start = int(S("start_balance"))
    with lock, db() as c:
        r = c.execute("select id from users where id=?", (uid,)).fetchone()
        if not r:
            c.execute("insert into users(id,name,username,photo,balance,created) values(?,?,?,?,?,?)",
                      (uid, name, un, photo, start, int(time.time())))
        else:
            c.execute("update users set name=?,username=?,photo=? where id=?", (name, un, photo, uid))
        return c.execute("select * from users where id=?", (uid,)).fetchone()

sub_cache = {}
def channels():
    return [x.strip() for x in S("channels").split(",") if x.strip()]

def check_sub(uid):
    chs = channels()
    if not chs or not BOT_TOKEN:
        return True
    t = sub_cache.get(uid)
    if t and time.time() - t < 60:
        return True
    for ch in chs:
        try:
            url = "https://api.telegram.org/bot%s/getChatMember?chat_id=%s&user_id=%s" % (
                BOT_TOKEN, urllib.parse.quote(ch), uid)
            r = json.load(urllib.request.urlopen(url, timeout=5))
            if not (r["ok"] and r["result"]["status"] in ("member", "administrator", "creator")):
                return False
        except Exception:
            return False
    sub_cache[uid] = time.time()
    return True

def api(f):
    @wraps(f)
    def w(*a, **k):
        u = auth()
        if not u:
            return jsonify(ok=False, err="Avtorizatsiya xato"), 401
        if u["banned"]:
            return jsonify(ok=False, err="Siz bloklangansiz"), 403
        if not check_sub(u["id"]):
            return jsonify(ok=False, err="sub", channels=channels()), 403
        return f(u, *a, **k)
    return w

def balance(uid):
    with db() as c:
        return c.execute("select balance from users where id=?", (uid,)).fetchone()["balance"]

def add_bal(c, uid, n):
    c.execute("update users set balance=balance+? where id=?", (n, uid))

def get_bet():
    try:
        b = int((request.get_json(silent=True) or {}).get("bet", 0))
    except Exception:
        return None, "Stavka xato"
    if b < int(S("min_bet")):
        return None, "Minimal stavka: %s" % S("min_bet")
    return b, None

# ---------------- CRASH ----------------
crash = {"phase": "wait", "start": time.time() + 6, "round": 1, "point": 2.0, "t0": 0,
         "end": 0, "bets": {}, "history": []}

def mult(t):
    return round(math.exp(0.07 * max(t, 0)), 2)

def new_point():
    r = random.random()
    he = float(S("house_edge")) / 100
    return max(1.0, min(round((1 - he) / (1 - r), 2), 200.0))

def loop():
    while True:
        time.sleep(0.1)
        try:
            with lock:
                now, c = time.time(), crash
                if c["phase"] == "wait" and now >= c["start"]:
                    c["phase"], c["t0"], c["point"] = "run", now, new_point()
                elif c["phase"] == "run":
                    if mult(now - c["t0"]) >= c["point"]:
                        c["phase"], c["end"] = "crashed", now
                        c["history"] = ([c["point"]] + c["history"])[:12]
                elif c["phase"] == "crashed" and now >= c["end"] + 4:
                    c.update(phase="wait", start=now + 6, round=c["round"] + 1, bets={})
        except Exception as e:
            print("loop error", e)

@app.route("/api/crash/state")
@api
def crash_state(u):
    with lock:
        c, now = crash, time.time()
        if c["phase"] == "wait":
            m = 1.0
        elif c["phase"] == "run":
            m = min(mult(now - c["t0"]), c["point"])
        else:
            m = c["point"]
        mine = c["bets"].get(u["id"])
        bets = [{"n": b["name"], "a": b["amount"], "c": b["cash"]} for b in c["bets"].values()]
        return jsonify(ok=True, phase=c["phase"], mult=m, left=max(0, c["start"] - now),
                       hist=c["history"], bets=bets[:30],
                       mine=mine and {"a": mine["amount"], "c": mine["cash"]},
                       balance=balance(u["id"]), min_bet=int(S("min_bet")))

@app.route("/api/crash/bet", methods=["POST"])
@api
def crash_bet(u):
    bet, err = get_bet()
    if err:
        return jsonify(ok=False, err=err)
    with lock, db() as c:
        if crash["phase"] != "wait":
            return jsonify(ok=False, err="Raund boshlangan, keyingisini kuting")
        if u["id"] in crash["bets"]:
            return jsonify(ok=False, err="Siz allaqachon stavka qo'ygansiz")
        if balance(u["id"]) < bet:
            return jsonify(ok=False, err="Balans yetarli emas")
        add_bal(c, u["id"], -bet)
        c.execute("update users set games=games+1 where id=?", (u["id"],))
        crash["bets"][u["id"]] = {"name": u["name"], "amount": bet, "cash": None}
    return jsonify(ok=True)

@app.route("/api/crash/cashout", methods=["POST"])
@api
def crash_cash(u):
    with lock, db() as c:
        b = crash["bets"].get(u["id"])
        if crash["phase"] != "run" or not b or b["cash"] is not None:
            return jsonify(ok=False, err="Olib bo'lmaydi")
        m = mult(time.time() - crash["t0"])
        if m >= crash["point"]:
            return jsonify(ok=False, err="Kech qoldingiz")
        b["cash"] = m
        win = int(b["amount"] * m)
        add_bal(c, u["id"], win)
        c.execute("update users set wins=wins+1 where id=?", (u["id"],))
    return jsonify(ok=True, win=win, mult=m)

# ---------------- PVP (duel) ----------------
@app.route("/api/duel/list")
@api
def duel_list(u):
    with db() as c:
        op = [dict(r) for r in c.execute("select id,creator,cname,bet from duels where status='open' order by id desc limit 30")]
        rc = [dict(r) for r in c.execute(
            "select id,cname,jname,bet,winner from duels where status='done' and (creator=? or joiner=?) order by id desc limit 5",
            (u["id"], u["id"]))]
    return jsonify(ok=True, open=op, recent=rc, me=u["id"], balance=balance(u["id"]), min_bet=int(S("min_bet")))

@app.route("/api/duel/create", methods=["POST"])
@api
def duel_create(u):
    bet, err = get_bet()
    if err:
        return jsonify(ok=False, err=err)
    with lock, db() as c:
        if balance(u["id"]) < bet:
            return jsonify(ok=False, err="Balans yetarli emas")
        add_bal(c, u["id"], -bet)
        c.execute("insert into duels(creator,cname,bet,status,created) values(?,?,?,?,?)",
                  (u["id"], u["name"], bet, "open", int(time.time())))
    return jsonify(ok=True)

@app.route("/api/duel/cancel", methods=["POST"])
@api
def duel_cancel(u):
    did = (request.get_json(silent=True) or {}).get("id")
    with lock, db() as c:
        d = c.execute("select * from duels where id=? and status='open' and creator=?", (did, u["id"])).fetchone()
        if not d:
            return jsonify(ok=False, err="Topilmadi")
        c.execute("update duels set status='cancel' where id=?", (did,))
        add_bal(c, u["id"], d["bet"])
    return jsonify(ok=True)

@app.route("/api/duel/join", methods=["POST"])
@api
def duel_join(u):
    did = (request.get_json(silent=True) or {}).get("id")
    with lock, db() as c:
        d = c.execute("select * from duels where id=? and status='open'", (did,)).fetchone()
        if not d:
            return jsonify(ok=False, err="Duel allaqachon yopilgan")
        if d["creator"] == u["id"]:
            return jsonify(ok=False, err="O'z duelingizga kira olmaysiz")
        if balance(u["id"]) < d["bet"]:
            return jsonify(ok=False, err="Balans yetarli emas")
        add_bal(c, u["id"], -d["bet"])
        winner = random.choice([d["creator"], u["id"]])
        loser = u["id"] if winner == d["creator"] else d["creator"]
        pot = d["bet"] * 2
        prize = pot - int(pot * float(S("commission")) / 100)
        add_bal(c, winner, prize)
        c.execute("update users set games=games+1 where id in (?,?)", (d["creator"], u["id"]))
        c.execute("update users set wins=wins+1 where id=?", (winner,))
        c.execute("update duels set status='done',joiner=?,jname=?,winner=? where id=?",
                  (u["id"], u["name"], winner, did))
    return jsonify(ok=True, won=(winner == u["id"]), prize=prize)

# ---------------- PROFIL / REYTING / VAZIFA / PROMO ----------------
@app.route("/api/me")
@api
def me(u):
    with db() as c:
        r = c.execute("select * from users where id=?", (u["id"],)).fetchone()
        rank = c.execute("select count(*)+1 n from users where balance>? and banned=0", (r["balance"],)).fetchone()["n"]
    return jsonify(ok=True, id=r["id"], name=r["name"], username=r["username"], photo=r["photo"],
                   balance=r["balance"], games=r["games"], wins=r["wins"], rank=rank)

@app.route("/api/top")
@api
def top(u):
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "select id,name,balance from users where banned=0 order by balance desc limit 20")]
    return jsonify(ok=True, top=rows, me=u["id"])

@app.route("/api/tasks")
@api
def tasks(u):
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "select t.id,t.title,t.reward,t.url,(select count(*) from task_done d where d.task_id=t.id and d.uid=?) done from tasks t",
            (u["id"],))]
    return jsonify(ok=True, tasks=rows)

@app.route("/api/task/claim", methods=["POST"])
@api
def task_claim(u):
    tid = (request.get_json(silent=True) or {}).get("id")
    with lock, db() as c:
        t = c.execute("select * from tasks where id=?", (tid,)).fetchone()
        if not t:
            return jsonify(ok=False, err="Vazifa topilmadi")
        if c.execute("select 1 from task_done where task_id=? and uid=?", (tid, u["id"])).fetchone():
            return jsonify(ok=False, err="Allaqachon olingan")
        c.execute("insert into task_done values(?,?)", (tid, u["id"]))
        add_bal(c, u["id"], t["reward"])
    return jsonify(ok=True, reward=t["reward"])

@app.route("/api/promo", methods=["POST"])
@api
def promo(u):
    code = str((request.get_json(silent=True) or {}).get("code", "")).strip().upper()
    with lock, db() as c:
        p = c.execute("select * from promos where code=?", (code,)).fetchone()
        if not p:
            return jsonify(ok=False, err="Promokod topilmadi")
        if p["used"] >= p["max_uses"]:
            return jsonify(ok=False, err="Promokod tugagan")
        if c.execute("select 1 from promo_used where code=? and uid=?", (code, u["id"])).fetchone():
            return jsonify(ok=False, err="Siz allaqachon ishlatgansiz")
        c.execute("insert into promo_used values(?,?)", (code, u["id"]))
        c.execute("update promos set used=used+1 where code=?", (code,))
        add_bal(c, u["id"], p["amount"])
    return jsonify(ok=True, amount=p["amount"])

# ---------------- ADMIN PANEL ----------------
ADMIN = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Admin</title>
<style>body{font-family:sans-serif;background:#10131a;color:#e8ecf4;margin:0;padding:12px}
a{color:#6cf}input,button,textarea{padding:8px;margin:3px;border-radius:8px;border:1px solid #334;background:#1b2030;color:#fff}
button{background:#2a7fff;border:0}.tabs a{display:inline-block;padding:8px 12px;background:#1b2030;border-radius:8px;margin:2px}
.card{background:#1b2030;border-radius:12px;padding:12px;margin:8px 0}table{width:100%;border-collapse:collapse}
td,th{padding:6px;border-bottom:1px solid #334;font-size:13px;text-align:left}.msg{color:#7f7}.red{background:#d33}</style></head><body>
<h2>🛠 ayubrizofactory</h2>
{% if not ok %}
<form method=post><input type=hidden name=a value=login>
<input type=password name=pw placeholder=Parol><button>Kirish</button></form>
{% else %}
<div class=tabs>{% for t,n in [('stats','📊 Statistika'),('users','👥 Userlar'),('promos','🎁 Promokod'),('tasks','✅ Vazifalar'),('settings','⚙️ Sozlama')] %}
<a href="?tab={{t}}">{{n}}</a>{% endfor %}
<form method=post style=display:inline><input type=hidden name=a value=logout><button class=red>Chiqish</button></form></div>
{% if msg %}<p class=msg>{{msg}}</p>{% endif %}

{% if tab=='stats' %}<div class=card>
{% for k,v in stats.items() %}<p>{{k}}: <b>{{v}}</b></p>{% endfor %}</div>

{% elif tab=='users' %}
<form method=get><input type=hidden name=tab value=users><input name=q value="{{q}}" placeholder="ID yoki ism"><button>Qidirish</button></form>
<div class=card style="overflow:auto"><table><tr><th>ID</th><th>Ism</th><th>Balans</th><th>O'yin</th><th>Amal</th></tr>
{% for u in users %}<tr><td>{{u.id}}</td><td>{{u.name}} {% if u.banned %}🚫{% endif %}</td><td>{{u.balance}}</td><td>{{u.games}}/{{u.wins}}</td><td>
<form method=post><input type=hidden name=a value=give><input type=hidden name=uid value={{u.id}}>
<input name=amount type=number placeholder="+/- miqdor" style="width:90px"><button>Berish</button></form>
<form method=post><input type=hidden name=a value=ban><input type=hidden name=uid value={{u.id}}>
<button class=red>{{ 'Ochish' if u.banned else 'Ban' }}</button></form></td></tr>{% endfor %}</table></div>

{% elif tab=='promos' %}
<div class=card><form method=post><input type=hidden name=a value=promo_add>
<input name=code placeholder=KOD><input name=amount type=number placeholder=Miqdor>
<input name=max_uses type=number placeholder="Necha kishi"><button>Qo'shish</button></form></div>
<div class=card><table><tr><th>Kod</th><th>Miqdor</th><th>Ishlatildi</th><th></th></tr>
{% for p in promos %}<tr><td>{{p.code}}</td><td>{{p.amount}}</td><td>{{p.used}}/{{p.max_uses}}</td><td>
<form method=post><input type=hidden name=a value=promo_del><input type=hidden name=code value={{p.code}}><button class=red>O'chir</button></form></td></tr>{% endfor %}</table></div>

{% elif tab=='tasks' %}
<div class=card><form method=post><input type=hidden name=a value=task_add>
<input name=title placeholder="Vazifa nomi"><input name=reward type=number placeholder=Mukofot>
<input name=url placeholder="Havola (https://t.me/...)"><button>Qo'shish</button></form></div>
<div class=card><table>{% for t in tasks %}<tr><td>{{t.title}}</td><td>{{t.reward}}</td><td>
<form method=post><input type=hidden name=a value=task_del><input type=hidden name=id value={{t.id}}><button class=red>O'chir</button></form></td></tr>{% endfor %}</table></div>

{% elif tab=='settings' %}
<div class=card><form method=post><input type=hidden name=a value=settings>
<p>Minimal stavka<br><input name=min_bet value="{{st.min_bet}}"></p>
<p>Yangi user boshlang'ich balansi<br><input name=start_balance value="{{st.start_balance}}"></p>
<p>PvP komissiya (%)<br><input name=commission value="{{st.commission}}"></p>
<p>Crash house edge (%)<br><input name=house_edge value="{{st.house_edge}}"></p>
<p>Majburiy obuna kanallari (vergul bilan: @kanal1,@kanal2)<br><input name=channels value="{{st.channels}}" style="width:90%"></p>
<button>Saqlash</button></form></div>
{% endif %}{% endif %}</body></html>"""

@app.route("/ayubrizofactory", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        a = request.form.get("a")
        if a == "login":
            if request.form.get("pw") == ADMIN_PASSWORD:
                session["adm"] = 1
            return redirect("/ayubrizofactory")
        if not session.get("adm"):
            return redirect("/ayubrizofactory")
        if a == "logout":
            session.clear()
            return redirect("/ayubrizofactory")
        f, tab, msg = request.form, "stats", "Bajarildi"
        try:
            with lock, db() as c:
                if a == "give":
                    tab = "users"
                    c.execute("update users set balance=max(0,balance+?) where id=?", (int(f["amount"]), int(f["uid"])))
                elif a == "ban":
                    tab = "users"
                    c.execute("update users set banned=1-banned where id=?", (int(f["uid"]),))
                elif a == "promo_add":
                    tab = "promos"
                    c.execute("insert or replace into promos(code,amount,max_uses,used) values(?,?,?,0)",
                              (f["code"].strip().upper(), int(f["amount"]), int(f["max_uses"])))
                elif a == "promo_del":
                    tab = "promos"
                    c.execute("delete from promos where code=?", (f["code"],))
                elif a == "task_add":
                    tab = "tasks"
                    c.execute("insert into tasks(title,reward,url) values(?,?,?)", (f["title"], int(f["reward"]), f["url"]))
                elif a == "task_del":
                    tab = "tasks"
                    c.execute("delete from tasks where id=?", (int(f["id"]),))
                elif a == "settings":
                    tab = "settings"
                    for k in DEFAULTS:
                        c.execute("insert or replace into settings values(?,?)", (k, f.get(k, DEFAULTS[k]).strip()))
        except Exception as e:
            msg = "Xato: %s" % e
        return redirect("/ayubrizofactory?tab=%s&msg=%s" % (tab, urllib.parse.quote(msg)))

    tab, q = request.args.get("tab", "stats"), request.args.get("q", "").strip()
    d = dict(ok=bool(session.get("adm")), tab=tab, msg=request.args.get("msg", ""), q=q,
             stats={}, users=[], promos=[], tasks=[], st={k: S(k) for k in DEFAULTS})
    if d["ok"]:
        with db() as c:
            one = lambda s: c.execute(s).fetchone()[0] or 0
            d["stats"] = {"Foydalanuvchilar": one("select count(*) from users"),
                          "Bloklanganlar": one("select count(*) from users where banned=1"),
                          "Jami balans": one("select sum(balance) from users"),
                          "Jami o'yinlar": one("select sum(games) from users"),
                          "Tugagan PvP duellar": one("select count(*) from duels where status='done'"),
                          "Ochiq PvP duellar": one("select count(*) from duels where status='open'"),
                          "Promokodlar": one("select count(*) from promos"),
                          "Bajarilgan vazifalar": one("select count(*) from task_done")}
            if tab == "users":
                if q:
                    d["users"] = c.execute("select * from users where cast(id as text)=? or name like ? limit 50",
                                           (q, "%" + q + "%")).fetchall()
                else:
                    d["users"] = c.execute("select * from users order by id desc limit 50").fetchall()
            d["promos"] = c.execute("select * from promos").fetchall()
            d["tasks"] = c.execute("select * from tasks").fetchall()
    return render_template_string(ADMIN, **d)

# ---------------- WEB APP (frontend) ----------------
INDEX = r"""<!doctype html><html lang=uz><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Game Bot</title><script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,sans-serif;background:#0f1220;color:#eef;padding-bottom:70px}
header{display:flex;align-items:center;gap:10px;padding:12px;background:#171b2e;position:sticky;top:0;z-index:5}
header img{width:38px;height:38px;border-radius:50%;background:#2a3050}#nm{flex:1;font-weight:600}
#bal{background:#2a7fff22;border:1px solid #2a7fff;border-radius:20px;padding:6px 12px;font-weight:700}
.v{display:none;padding:12px}.v.on{display:block}
.card{background:#171b2e;border-radius:14px;padding:12px;margin:8px 0}
button{background:#2a7fff;color:#fff;border:0;border-radius:10px;padding:12px;font-size:15px;font-weight:600;width:100%;margin:4px 0}
button:disabled{opacity:.4}button.g{background:#1fb86a}button.r{background:#e5484d}button.s{width:auto;padding:8px 12px;font-size:13px}
input{width:100%;padding:12px;border-radius:10px;border:1px solid #2a3050;background:#0f1220;color:#fff;font-size:16px}
#mult{font-size:56px;font-weight:800;text-align:center;padding:26px 0}.wait{color:#aab}.run{color:#1fb86a}.crash{color:#e5484d}
.row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #222a44;font-size:14px}
.chips span{display:inline-block;padding:3px 8px;border-radius:8px;margin:2px;background:#222a44;font-size:12px}
nav{position:fixed;bottom:0;left:0;right:0;display:flex;background:#171b2e;border-top:1px solid #222a44}
nav div{flex:1;text-align:center;padding:10px 0;font-size:12px;color:#889}nav div.on{color:#6cf}nav b{display:block;font-size:20px}
#toast{position:fixed;top:70px;left:50%;transform:translateX(-50%);background:#000c;padding:10px 16px;border-radius:10px;display:none;z-index:9}
#sub{display:none;padding:20px;text-align:center}
#stage{position:relative;height:300px;border-radius:20px;overflow:hidden;background-color:#15171c;
background-image:radial-gradient(circle,#8a93ff 2px,transparent 3px),radial-gradient(circle,#e8b4a0 3px,transparent 4px),radial-gradient(circle,#6aa8ff 2px,transparent 3px);
background-size:140px 120px,210px 170px,170px 200px;background-position:10px 20px,60px 90px,120px 40px;animation:drift 14s linear infinite}
@keyframes drift{to{background-position:10px 140px,60px 260px,120px 240px}}
#stage #mult{position:absolute;top:12px;left:0;right:0;padding:0;font-size:46px;text-shadow:0 0 18px #000;z-index:2}
#ph{position:absolute;top:70px;left:0;right:0;text-align:center;color:#9ab;font-size:14px;z-index:2}
#rocket{position:absolute;width:130px;height:90px;font-size:52px;line-height:1;transition:left .3s linear,bottom .3s linear}
#rocket.run{animation:bob .6s ease-in-out infinite alternate}
@keyframes bob{to{transform:translateY(-7px)}}
#rocket .rk{width:100%;height:100%;display:block;object-fit:contain;filter:drop-shadow(0 0 10px #ff9a3c66)}
#rocket .boom{font-size:60px;width:90px;height:90px;object-fit:contain}
.autorow{display:flex;align-items:center;gap:10px;margin:12px 4px}
.autorow input[type=number]{width:80px;padding:8px}
.sw{position:relative;width:52px;height:26px;display:inline-block}
.sw input{display:none}
.sw span{position:absolute;top:0;left:0;right:0;bottom:0;background:#2a3050;border-radius:20px;transition:.2s}
.sw span:before{content:"";position:absolute;width:20px;height:20px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s}
.sw input:checked+span{background:#1fb86a;box-shadow:0 0 12px #1fb86a88}
.sw input:checked+span:before{transform:translateX(26px)}
.chips{display:flex;overflow-x:auto;gap:8px;margin:10px 0;padding-bottom:4px}
.chips .hc{flex:none;margin:0;padding:10px 16px;border-radius:16px;font-weight:700;font-size:15px;border:2px solid #2d4a8a;background:#1b2440;color:#cfe}
.chips .hc.c2{border-color:#3b82f6}.chips .hc.c3{border-color:#8b5cf6;background:#251b44}
.chips .hc.c4{border-color:#f5b301;background:#3a2d08;color:#ffd666}
.chips .hc.last{border-color:#34d399;background:#12352f;box-shadow:0 0 12px #34d39966}
.betrow{display:flex;gap:6px;align-items:center}.betrow input{flex:1;min-width:0}.betrow button{margin:0}
button.big{background:linear-gradient(90deg,#0ea5ff,#2563eb);border-radius:28px;padding:20px;font-size:20px;margin:10px 0;box-shadow:0 6px 24px #2563eb55}
button.big.cash{background:linear-gradient(90deg,#22c55e,#16a34a);box-shadow:0 6px 24px #16a34a55}
button.big:disabled{background:#2a3050;box-shadow:none;color:#889;opacity:1}
#betbox{background:#171b2e;border-radius:22px;padding:8px 10px}
.pc{display:flex;align-items:center;gap:12px;background:#222f3a;border:1px solid #2d4a5a;border-radius:18px;padding:10px;margin:8px 0}
.pav{width:48px;height:48px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:20px;flex:none}
.pi{flex:1;min-width:0}.pn{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pch{display:inline-block;background:#1c3a4a;color:#6cf;border-radius:10px;padding:3px 8px;font-size:13px;margin-top:4px}
.pw{padding:10px 14px;border-radius:14px;background:#1c3a4a;font-weight:700;white-space:nowrap}
.pw.ok{color:#4ade80}.pw.bad{color:#e5484d}.pw.dim{color:#9ab}

</style></head><body>
<header><img id=av><div id=nm>...</div><div id=bal>0</div></header>
<div id=toast></div>
<div id=sub><h3>Majburiy obuna</h3><div id=subl></div><button onclick="location.reload()">Tekshirish</button></div>
<div id=app>
<div class="v on" id=v_crash>
 <div id=stage><div id=mult class=wait>...</div><div id=ph></div><div id=rocket class=wait></div></div>
 <div class=autorow><label class=sw><input type=checkbox id=auto><span></span></label>
  <input id=autox type=number step=0.1 value=2><b>x avto-yechish</b></div>
 <div class=chips id=hist></div>
 <div class=betrow><input id=bet type=number inputmode=numeric value=1000>
  <button class="s" onclick="setBet(1000)">1000</button><button class="s" onclick="setBet(5000)">5000</button>
  <button class="s" onclick="setBet(10000)">10K</button><button class="s" onclick="mulBet(2)">x2</button></div>
 <button id=mainbtn class=big onclick="mainClick()" disabled>...</button>
 <div id=betbox><div id=bets></div></div>
</div>
<div class=v id=v_pvp>
 <div class=card><b>⚔️ Duel yaratish</b><input id=pbet type=number value=1000>
  <button class=g onclick="duelCreate()">Duel ochish</button></div>
 <div class=card><b>Ochiq duellar</b><div id=duels></div></div>
 <div class=card><b>Oxirgi natijalar</b><div id=drec></div></div>
</div>
<div class=v id=v_tasks>
 <div class=card><b>🎁 Promokod</b><input id=pc placeholder="Promokod"><button onclick="promo()">Faollashtirish</button></div>
 <div class=card><b>✅ Vazifalar</b><div id=tl></div></div>
</div>
<div class=v id=v_top><div class=card><b>🏆 Reyting (TOP 20)</b><div id=tp></div></div></div>
<div class=v id=v_me><div class=card id=prof></div></div>
</div>
<nav id=nav>
<div class=on data-t=crash><b>🚀</b>Crash</div><div data-t=pvp><b>⚔️</b>PvP</div><div data-t=tasks><b>✅</b>Vazifa</div>
<div data-t=top><b>🏆</b>Reyting</div><div data-t=me><b>👤</b>Profil</div></nav>
<script>
const tg=window.Telegram&&Telegram.WebApp; if(tg&&tg.initData){tg.ready();tg.expand();}
const P=new URLSearchParams(location.search);
let UID=P.get('u')||localStorage.getItem('uid')||String(Math.floor(Math.random()*9e6+1e6));
let UNAME=P.get('n')||localStorage.getItem('un')||('Test'+UID);
localStorage.setItem('uid',UID);localStorage.setItem('un',UNAME);
const $=id=>document.getElementById(id), fmt=n=>Number(n).toLocaleString('ru-RU');
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let tab='crash',stop=false;
function toast(t){const e=$('toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',2200);}
async function api(p,body){
 const h={'Content-Type':'application/json'};
 if(tg&&tg.initData)h['X-Init']=tg.initData;else{h['X-Uid']=UID;h['X-Name']=encodeURIComponent(UNAME);}
 try{const r=await fetch(p,{method:body?'POST':'GET',headers:h,body:body?JSON.stringify(body):undefined});
  const j=await r.json();
  if(j.err==='sub'){stop=true;$('app').style.display='none';$('nav').style.display='none';$('sub').style.display='block';
   $('subl').innerHTML=j.channels.map(c=>'<p><a style="color:#6cf" href="https://t.me/'+esc(c.replace('@',''))+'">'+esc(c)+'</a></p>').join('');}
  return j;}catch(e){return{ok:false,err:'Tarmoq xatosi'};}
}
function setBet(n){$('bet').value=n}function mulBet(k){$('bet').value=Math.floor($('bet').value*k)}
document.querySelectorAll('#nav div').forEach(d=>d.onclick=()=>{
 tab=d.dataset.t;document.querySelectorAll('#nav div').forEach(x=>x.classList.toggle('on',x===d));
 document.querySelectorAll('.v').forEach(v=>v.classList.toggle('on',v.id==='v_'+tab));load();});
let CS=null,autoFired=false,lastPh='';
function hue(s){let h=0;for(const c of String(s))h=(h*31+c.charCodeAt(0))%360;return h;}
async function crashCash(){const j=await api('/api/crash/cashout',{});toast(j.ok?('+'+fmt(j.win)+' ('+j.mult+'x)'):j.err);}
async function mainClick(){
 if(!CS)return;
 if(CS.phase==='wait'&&!CS.mine){const j=await api('/api/crash/bet',{bet:+$('bet').value});if(!j.ok)toast(j.err);}
 else if(CS.phase==='run'&&CS.mine&&CS.mine.c==null){crashCash();}
}
const RK_SVG='<svg viewBox="0 0 210 120" class=rk><defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffd54a"/><stop offset=".55" stop-color="#ffa022"/><stop offset="1" stop-color="#ef6a14"/></linearGradient><linearGradient id="lg" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#2f9e44"/><stop offset="1" stop-color="#7fd35a"/></linearGradient><linearGradient id="fl" x1="1" y1="0" x2="0" y2="0"><stop offset="0" stop-color="#ffe066"/><stop offset="1" stop-color="#ff7a1a" stop-opacity="0"/></linearGradient></defs><path d="M44 58 C20 50 4 56 0 62 C12 70 28 66 44 66Z" fill="url(#fl)" opacity=".8"/><path d="M50 52 C34 34 14 30 8 36 C14 48 32 54 52 60Z" fill="url(#lg)"/><path d="M50 70 C36 86 16 92 10 88 C14 76 32 68 52 64Z" fill="url(#lg)"/><path d="M48 61 C30 56 14 58 10 62 C16 70 32 68 48 64Z" fill="#3fae4f"/><path d="M46 52 C62 24 135 28 205 66 C140 98 66 98 46 74 C40 66 40 58 46 52Z" fill="url(#cg)" stroke="#d9600f" stroke-width="2"/><g stroke="#d9600f" stroke-width="2" fill="none" stroke-linecap="round" opacity=".7"><path d="M80 80 C84 84 90 86 96 86"/><path d="M118 76 C124 80 130 80 136 80"/><path d="M150 70 C154 72 160 72 164 72"/><path d="M70 62 C74 64 80 64 84 62"/></g><path d="M62 42 C80 36 110 38 128 50 C112 56 80 54 62 48Z" fill="#ffe27a" opacity=".55"/><path d="M86 52 C86 30 120 28 124 52 C112 58 98 58 86 52Z" fill="#4aa8ff" stroke="#fff" stroke-width="3"/><ellipse cx="72" cy="22" rx="16" ry="6" transform="rotate(-28 72 22)" fill="#b9b3d3" stroke="#8d86b0" stroke-width="1.5"/><ellipse cx="72" cy="22" rx="10" ry="3" transform="rotate(-28 72 22)" fill="#f3a9b8"/><ellipse cx="82" cy="12" rx="16" ry="6" transform="rotate(-12 82 12)" fill="#b9b3d3" stroke="#8d86b0" stroke-width="1.5"/><ellipse cx="82" cy="12" rx="10" ry="3" transform="rotate(-12 82 12)" fill="#f3a9b8"/><ellipse cx="104" cy="34" rx="22" ry="19" fill="#c4bedf" stroke="#8d86b0" stroke-width="1.5"/><ellipse cx="112" cy="40" rx="9" ry="7" fill="#e6e2f3"/><circle cx="102" cy="28" r="3.6" fill="#4a3f4f"/><circle cx="114" cy="30" r="3.6" fill="#4a3f4f"/><circle cx="103" cy="27" r="1.2" fill="#fff"/><circle cx="115" cy="29" r="1.2" fill="#fff"/><ellipse cx="118" cy="37" rx="3" ry="2.2" fill="#e57b94"/><circle cx="97" cy="37" r="3.5" fill="#f3a9b8" opacity=".8"/><ellipse cx="98" cy="58" rx="9" ry="5" fill="#c4bedf" stroke="#8d86b0" stroke-width="1.5"/><ellipse cx="116" cy="60" rx="9" ry="5" fill="#c4bedf" stroke="#8d86b0" stroke-width="1.5"/></svg>';
function placeRocket(s){
 const r=$('rocket');
 if(lastPh!==s.phase){
  r.innerHTML=s.phase==='crashed'?'<img class=boom src="/static/boom.png" onerror="this.outerHTML=\'<span class=boom>💥</span>\'">':'<img class=rk src="/static/rocket.png" onerror="this.outerHTML=RK_SVG">';
  r.className=s.phase;lastPh=s.phase;
 }
 let p=0;if(s.phase!=='wait')p=Math.min(1,Math.log(Math.max(s.mult,1))/Math.log(12));
 r.style.left=(8+p*58)+'%';r.style.bottom=(8+p*48)+'%';
}
async function crashTick(){
 if(stop||tab!=='crash')return;const s=await api('/api/crash/state');if(!s.ok)return;CS=s;
 $('bal').textContent=fmt(s.balance);const m=$('mult');
 if(s.phase==='wait'){m.textContent=s.left.toFixed(1)+'s';m.className='wait';$('ph').textContent='Stavkalar qabul qilinmoqda';autoFired=false;}
 else if(s.phase==='run'){m.textContent=s.mult.toFixed(2)+'x';m.className='run';$('ph').textContent='';}
 else{m.textContent=s.mult.toFixed(2)+'x';m.className='crash';$('ph').textContent='CRASH!';}
 placeRocket(s);
 const mine=s.mine,b=$('mainbtn');let t='',on=false,cash=false;
 if(s.phase==='wait'){if(mine){t='✅ Stavka qabul qilindi';}else{t="Stavka qo'yish";on=true;}}
 else if(s.phase==='run'){
  if(mine&&mine.c==null){t='Olish: '+fmt(Math.floor(mine.a*s.mult));on=true;cash=true;}
  else if(mine){t='Yechildi +'+fmt(Math.floor(mine.a*mine.c));}
  else{t='Keyingi raundni kuting';}
 }else{t='Keyingi raundni kuting';}
 b.textContent=t;b.disabled=!on;b.className='big'+(cash?' cash':'');
 if($('auto').checked&&mine&&mine.c==null&&s.phase==='run'&&!autoFired&&s.mult>=+$('autox').value){autoFired=true;crashCash();}
 $('hist').innerHTML=s.hist.map((h,i)=>'<span class="hc '+(h<2?'c1':h<5?'c2':h<10?'c3':'c4')+(i===0?' last':'')+'">'+h.toFixed(2)+'x</span>').join('');
 $('bets').innerHTML=s.bets.map(x=>{
  const win=x.c?Math.floor(x.a*x.c):0;
  const right=x.c?'<span class="pw ok">⭐ '+fmt(win)+'</span>':(s.phase==='crashed'?'<span class="pw bad">✖</span>':(s.phase==='run'?'<span class="pw dim">⭐ '+fmt(Math.floor(x.a*s.mult))+'</span>':'<span class="pw dim">—</span>'));
  return '<div class=pc><div class=pav style="background:hsl('+hue(x.n)+',55%,38%)">'+esc(String(x.n).charAt(0).toUpperCase())+'</div><div class=pi><div class=pn>'+esc(x.n)+'</div><div class=pch>⭐ '+fmt(x.a)+(x.c?' ×'+x.c.toFixed(2):'')+'</div></div>'+right+'</div>';
 }).join('')||'<div style="color:#889;padding:8px">Hozircha yoq</div>';
}
async function duelLoad(){const j=await api('/api/duel/list');if(!j.ok)return;$('bal').textContent=fmt(j.balance);
 $('duels').innerHTML=j.open.map(d=>'<div class=row><span>'+esc(d.cname)+' — '+fmt(d.bet)+'</span>'+
  (d.creator===j.me?'<button class="s r" onclick="duelCancel('+d.id+')">Bekor</button>':'<button class="s g" onclick="duelJoin('+d.id+')">Kirish</button>')+'</div>').join('')||'<div style="color:#889">Ochiq duel yo\'q</div>';
 $('drec').innerHTML=j.recent.map(d=>'<div class=row><span>'+esc(d.cname)+' vs '+esc(d.jname)+'</span><span>'+(d.winner===j.me?'✅ Yutdi':'❌ Yutqazdi')+' '+fmt(d.bet)+'</span></div>').join('')||'—';}
async function duelCreate(){const j=await api('/api/duel/create',{bet:+$('pbet').value});toast(j.ok?'Duel ochildi':j.err);duelLoad();}
async function duelCancel(id){const j=await api('/api/duel/cancel',{id});toast(j.ok?'Bekor qilindi':j.err);duelLoad();}
async function duelJoin(id){const j=await api('/api/duel/join',{id});toast(j.ok?(j.won?'🎉 Yutdingiz +'+fmt(j.prize):'😔 Yutqazdingiz'):j.err);duelLoad();}
async function tasksLoad(){const j=await api('/api/tasks');if(!j.ok)return;
 $('tl').innerHTML=j.tasks.map(t=>'<div class=row><span>'+esc(t.title)+'<br><small>+'+fmt(t.reward)+'</small></span><span>'+
  (t.done?'✅':'<button class="s" onclick="doTask('+t.id+',\''+esc(t.url)+'\')">Bajarish</button>')+'</span></div>').join('')||'<div style="color:#889">Vazifa yo\'q</div>';}
async function doTask(id,url){if(url){tg&&tg.openLink?tg.openLink(url):window.open(url,'_blank');}
 setTimeout(async()=>{const j=await api('/api/task/claim',{id});toast(j.ok?'+'+fmt(j.reward):j.err);tasksLoad();me0();},3000);}
async function promo(){const j=await api('/api/promo',{code:$('pc').value});toast(j.ok?'+'+fmt(j.amount):j.err);me0();}
async function topLoad(){const j=await api('/api/top');if(!j.ok)return;
 $('tp').innerHTML=j.top.map((u,i)=>'<div class=row><span>'+(['🥇','🥈','🥉'][i]||(i+1)+'.')+' '+esc(u.name)+(u.id===j.me?' (siz)':'')+'</span><b>'+fmt(u.balance)+'</b></div>').join('');}
async function me0(){const j=await api('/api/me');if(!j.ok)return;
 $('nm').textContent=j.name;$('bal').textContent=fmt(j.balance);
 if(j.photo)$('av').src=j.photo;
 $('prof').innerHTML='<h3>'+esc(j.name)+'</h3><div class=row><span>Username</span><span>'+(j.username?'@'+esc(j.username):'—')+'</span></div>'+
 '<div class=row><span>ID</span><span>'+j.id+'</span></div><div class=row><span>Balans</span><b>'+fmt(j.balance)+'</b></div>'+
 '<div class=row><span>O\'yinlar</span><span>'+j.games+'</span></div><div class=row><span>G\'alabalar</span><span>'+j.wins+'</span></div>'+
 '<div class=row><span>Reyting o\'rni</span><span>#'+j.rank+'</span></div>';}
function load(){me0();if(tab==='pvp')duelLoad();if(tab==='tasks')tasksLoad();if(tab==='top')topLoad();}
setInterval(crashTick,300);setInterval(()=>{if(tab==='pvp')duelLoad();},3000);load();
</script></body></html>"""

@app.route("/")
def index():
    return INDEX

_booted = False
def boot():
    global _booted
    if _booted:
        return
    _booted = True
    init_db()
    threading.Thread(target=loop, daemon=True).start()

boot()   # gunicorn bilan ham, python app.py bilan ham ishga tushadi

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
