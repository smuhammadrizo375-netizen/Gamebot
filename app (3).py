import os,time,math,json,random,secrets,sqlite3,threading,hmac,hashlib,urllib.request,urllib.parse as up
from flask import Flask,request,jsonify,Response
B=os.path.dirname(os.path.abspath(__file__))
DBP=os.environ.get("DB_PATH",B+"/data.db");TOK=os.environ.get("BOT_TOKEN","")
ADM=os.environ.get("ADMIN_CODE","ayubrizofactory");GUEST=os.environ.get("ALLOW_GUEST","1")=="1"
app=Flask(__name__,static_folder=B+"/static");L=threading.RLock();K=.12
DEF=dict(min_bet=1000,max_bet=1000000,house_edge=4,pvp_fee=5,start_balance=10000,crash_wait=7)
def q(s,a=(),one=False):
    with L:
        c=sqlite3.connect(DBP);c.row_factory=sqlite3.Row
        try:r=[dict(x) for x in c.execute(s,a).fetchall()];c.commit()
        finally:c.close()
    return (r[0] if r else None) if one else r
q("create table if not exists u(id text primary key,name text,un text,ph text,bal int,won int default 0,games int default 0,ban int default 0)")
for t in ["st(k text primary key,v text)","promo(code text primary key,amt int,mx int,used int default 0)","pu(code text,uid text,primary key(code,uid))",
"task(id integer primary key,title text,rew int,url text,chat text)","td(tid int,uid text,primary key(tid,uid))","ch(id integer primary key,chat text)",
"cb(id integer primary key,rid int,uid text,amt int,auto real,s text,m real,pay int)",
"duel(id integer primary key,cu text,cn text,bet int,s text,ju text,jn text,win text)"]:q("create table if not exists "+t)
q("update cb set s='refund' where s='open'")
def S(k):
    r=q("select v from st where k=?",(k,),True);return float(r["v"]) if r else DEF[k]
class E(Exception):pass
@app.errorhandler(E)
def eh(e):a=e.args;return jsonify(error=a[0],**(a[2] if len(a)>2 else {})),(a[1] if len(a)>1 else 400)
def init_ok(init):
    d=dict(up.parse_qsl(init,keep_blank_values=True))
    if TOK:
        h=d.pop("hash","");c="\n".join(f"{k}={v}" for k,v in sorted(d.items()))
        sec=hmac.new(b"WebAppData",TOK.encode(),hashlib.sha256).digest()
        if not hmac.compare_digest(hmac.new(sec,c.encode(),hashlib.sha256).hexdigest(),h):raise E("auth",401)
    return json.loads(d.get("user","{}"))
CA={}
def member(chat,uid):
    if not TOK or not uid.startswith("tg"):return True
    k=(chat,uid)
    if k in CA and time.time()-CA[k][0]<60:return CA[k][1]
    try:
        r=json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{TOK}/getChatMember?chat_id={up.quote(chat)}&user_id={uid[2:]}",timeout=6))
        ok=r["result"]["status"] in("creator","administrator","member","restricted")
    except Exception:ok=False
    CA[k]=(time.time(),ok);return ok
def missing(uid):return [c["chat"] for c in q("select chat from ch") if not member(c["chat"],uid)]
def auth(sub=False):
    i=request.headers.get("X-Init","")
    try:
        if i:
            u=init_ok(i);uid="tg%s"%u["id"];nm=(u.get("first_name","")+" "+u.get("last_name","")).strip();un=u.get("username","");ph=u.get("photo_url","")
        else:
            g=request.headers.get("X-Guest","")
            if not GUEST or not g.isalnum() or len(g)>30:raise E("auth",401)
            uid="g"+g;nm=up.unquote(request.headers.get("X-Name","Mehmon"))[:30];un="";ph=""
    except E:raise
    except Exception:raise E("auth",401)
    with L:
        q("insert or ignore into u(id,name,un,ph,bal) values(?,?,?,?,?)",(uid,nm,un,ph,int(S("start_balance"))))
        q("update u set name=?,un=?,ph=? where id=?",(nm,un,ph,uid))
        r=q("select * from u where id=?",(uid,),True)
    if r["ban"]:raise E("Siz bloklangansiz",403)
    if sub:
        m=missing(uid)
        if m:raise E("sub",403,{"channels":m})
    return r
def add(uid,n):q("update u set bal=bal+? where id=?",(n,uid))
def chk(u,a):
    if a<S("min_bet"):raise E("Minimal stavka %d"%S("min_bet"))
    if a>S("max_bet"):raise E("Maksimal stavka %d"%S("max_bet"))
    if a>u["bal"]:raise E("Balans yetarli emas")
# ---------- CRASH ----------
R={};H=[]
def new_round():
    r=random.random();e=S("house_edge")/100
    cp=min(1000.0,max(1.0,math.floor((1-e)/(1-r)*100)/100));n=time.time();f=n+S("crash_wait")
    c=f+(math.log(cp)/K if cp>1 else 0)
    R.clear();R.update(id=int(n*1000),cp=cp,fly=f,crash=c,end=c+4,done=False)
def advance():
    with L:
        n=time.time()
        if not R:new_round();return
        if not R["done"] and n>=R["crash"]:
            for b in q("select * from cb where rid=? and s='open'",(R["id"],)):
                if b["auto"] and 1<b["auto"]<=R["cp"]:pay(b,b["auto"])
            q("update cb set s='lost',m=0,pay=0 where rid=? and s='open'",(R["id"],));R["done"]=True;H.append(R["cp"]);del H[:-15]
        if n>=R["end"]:new_round()
def pay(b,m):
    w=int(b["amt"]*m);add(b["uid"],w);q("update cb set s='won',m=?,pay=? where id=?",(m,w,b["id"]));q("update u set won=won+? where id=?",(w,b["uid"]))
def cur():return math.exp(K*(time.time()-R["fly"]))
@app.get("/api/crash")
def c_state():
    u=auth();advance();n=time.time()
    ph="wait" if n<R["fly"] else "fly" if n<R["crash"] else "crashed"
    my=q("select * from cb where rid=? and uid=?",(R["id"],u["id"]),True)
    bs=q("select u.name n,cb.amt a,cb.s s,cb.m m from cb join u on u.id=cb.uid where rid=? order by cb.id desc limit 20",(R["id"],))
    return jsonify(ph=ph,el=n-R["fly"],cp=R["cp"] if ph=="crashed" else None,my=my,bets=bs,hist=H,bal=u["bal"])
@app.post("/api/crash/bet")
def c_bet():
    u=auth(True);advance();d=request.json;a=int(d.get("amt",0))
    with L:
        if time.time()>=R["fly"]:raise E("Raund boshlangan, keyingisini kuting")
        if q("select 1 from cb where rid=? and uid=?",(R["id"],u["id"])):raise E("Siz allaqachon stavka qo'ygansiz")
        chk(u,a);add(u["id"],-a);q("update u set games=games+1 where id=?",(u["id"],))
        q("insert into cb(rid,uid,amt,auto,s) values(?,?,?,?,'open')",(R["id"],u["id"],a,float(d.get("auto") or 0)))
    return jsonify(ok=1)
@app.post("/api/crash/cash")
def c_cash():
    u=auth();advance()
    with L:
        b=q("select * from cb where rid=? and uid=? and s='open'",(R["id"],u["id"]),True);n=time.time()
        if not b or not(R["fly"]<=n<R["crash"]):raise E("Kech qoldingiz")
        pay(b,math.floor(cur()*100)/100)
    return jsonify(ok=1)
# ---------- PVP (duel) ----------
def settle(d,ju,jn):
    w=random.choice([d["cu"],ju]);p=int(d["bet"]*2*(1-S("pvp_fee")/100))
    add(w,p);q("update u set won=won+? where id=?",(p,w));q("update duel set s='done',ju=?,jn=?,win=? where id=?",(ju,jn,w,d["id"]))
    q("update u set games=games+1 where id in(?,?)",(d["cu"],ju));return w
@app.get("/api/pvp")
def p_list():
    u=auth();return jsonify(open=q("select id,cu,cn,bet from duel where s='open' order by id desc limit 30"),
    mine=q("select cn,jn,bet,win from duel where s='done' and (cu=? or ju=?) order by id desc limit 8",(u["id"],u["id"])),me=u["id"],bal=u["bal"])
@app.post("/api/pvp/new")
def p_new():
    u=auth(True);a=int(request.json.get("bet",0))
    with L:
        chk(u,a)
        if q("select count(*) c from duel where s='open' and cu=?",(u["id"],),True)["c"]>=3:raise E("Ko'pi bilan 3 ta ochiq duel")
        add(u["id"],-a);q("insert into duel(cu,cn,bet,s) values(?,?,?,'open')",(u["id"],u["name"],a))
    return jsonify(ok=1)
@app.post("/api/pvp/join")
def p_join():
    u=auth(True);i=int(request.json["id"])
    with L:
        d=q("select * from duel where id=? and s='open'",(i,),True)
        if not d:raise E("Duel topilmadi")
        if d["cu"]==u["id"]:raise E("O'z duelingizga qo'shilib bo'lmaydi")
        chk(u,d["bet"]);add(u["id"],-d["bet"]);w=settle(d,u["id"],u["name"])
    return jsonify(win=w==u["id"],bet=d["bet"])
@app.post("/api/pvp/cancel")
def p_cancel():
    u=auth();i=int(request.json["id"])
    with L:
        d=q("select * from duel where id=? and s='open' and cu=?",(i,u["id"]),True)
        if d:add(u["id"],d["bet"]);q("update duel set s='cancel' where id=?",(i,))
    return jsonify(ok=1)
@app.post("/api/pvp/bot")
def p_bot():
    u=auth(True);a=int(request.json.get("bet",0))
    with L:
        chk(u,a);add(u["id"],-a);q("update u set games=games+1 where id=?",(u["id"],));w=random.random()<.5
        if w:p=int(a*2*(1-S("pvp_fee")/100));add(u["id"],p);q("update u set won=won+? where id=?",(p,u["id"]))
    return jsonify(win=w,bet=a)
# ---------- PROFIL / VAZIFA / REYTING ----------
@app.get("/api/me")
def me():
    u=auth();r=q("select count(*)+1 r from u where bal>?",(u["bal"],),True)["r"]
    return jsonify(u=u,rank=r,min=S("min_bet"),max=S("max_bet"),sub=missing(u["id"]))
@app.get("/api/rating")
def rating():auth();return jsonify(q("select name,un,bal from u where ban=0 order by bal desc limit 20"))
@app.post("/api/promo")
def promo():
    u=auth(True);c=request.json.get("code","").strip().upper()
    with L:
        p=q("select * from promo where code=?",(c,),True)
        if not p or p["used"]>=p["mx"]:raise E("Promokod yaroqsiz")
        if q("select 1 from pu where code=? and uid=?",(c,u["id"])):raise E("Siz bu kodni ishlatgansiz")
        q("insert into pu values(?,?)",(c,u["id"]));q("update promo set used=used+1 where code=?",(c,));add(u["id"],p["amt"])
    return jsonify(amt=p["amt"])
@app.get("/api/tasks")
def tasks():
    u=auth();return jsonify(q("select t.id,title,rew,url,(select count(*) from td where tid=t.id and uid=?) done from task t",(u["id"],)))
@app.post("/api/tasks/claim")
def claim():
    u=auth(True);i=int(request.json["id"])
    with L:
        t=q("select * from task where id=?",(i,),True)
        if not t or q("select 1 from td where tid=? and uid=?",(i,u["id"])):raise E("Mavjud emas yoki bajarilgan")
        if t["chat"] and not member(t["chat"],u["id"]):raise E("Avval kanalga a'zo bo'ling")
        q("insert into td values(?,?)",(i,u["id"]));add(u["id"],t["rew"])
    return jsonify(rew=t["rew"])
# ---------- ADMIN ----------
TK=set()
@app.post("/api/admin/login")
def a_login():
    if request.json.get("code")!=ADM:raise E("Kod noto'g'ri",401)
    t=secrets.token_hex(16);TK.add(t);return jsonify(t=t)
def adm():
    if request.headers.get("X-Admin") not in TK:raise E("admin",401)
@app.get("/api/admin/stats")
def a_stats():
    adm();g=lambda s:q(s,(),True)
    return jsonify(users=g("select count(*) c from u")["c"],banned=g("select count(*) c from u where ban=1")["c"],bal=g("select coalesce(sum(bal),0) c from u")["c"],
    crash=g("select count(*) c from cb")["c"],wag=g("select coalesce(sum(amt),0) c from cb")["c"],paid=g("select coalesce(sum(pay),0) c from cb")["c"],duels=g("select count(*) c from duel where s='done'")["c"])
@app.get("/api/admin/users")
def a_users():adm();s="%"+request.args.get("s","")+"%";return jsonify(q("select id,name,un,bal,games,ban from u where name like ? or id like ? or un like ? order by bal desc limit 40",(s,s,s)))
@app.post("/api/admin/bal")
def a_bal():
    adm();d=request.json;n=int(d["amt"])
    if d["mode"]=="set":q("update u set bal=? where id=?",(n,d["id"]))
    elif d["mode"]=="all":q("update u set bal=bal+?",(n,))
    else:add(d["id"],n)
    return jsonify(ok=1)
@app.post("/api/admin/ban")
def a_ban():adm();d=request.json;q("update u set ban=? where id=?",(int(d["ban"]),d["id"]));return jsonify(ok=1)
@app.route("/api/admin/<what>",methods=["GET","POST"])
def a_crud(what):
    adm();T=dict(promo=("promo","code"),task=("task","id"),ch=("ch","id"))
    if what=="settings":
        if request.method=="POST":
            for k,v in request.json.items():
                if k in DEF:q("insert or replace into st values(?,?)",(k,str(v)))
        return jsonify({k:S(k) for k in DEF})
    if what not in T:raise E("yo'q",404)
    t,pk=T[what];d=request.json or {}
    if request.method=="POST":
        if d.get("del"):q(f"delete from {t} where {pk}=?",(d["del"],))
        elif what=="promo":q("insert or replace into promo(code,amt,mx,used) values(?,?,?,0)",(d["code"].upper(),int(d["amt"]),int(d["mx"])))
        elif what=="task":q("insert into task(title,rew,url,chat) values(?,?,?,?)",(d["title"],int(d["rew"]),d["url"],d.get("chat","")))
        else:q("insert into ch(chat) values(?)",(d["chat"],))
    return jsonify(q(f"select * from {t}"))
# ---------- SAHIFALAR ----------
CSS='''<meta name=viewport content="width=device-width,initial-scale=1,user-scalable=no"><style>*{box-sizing:border-box}body{margin:0;background:#12141c;color:#eee;font:15px sans-serif;padding-bottom:70px}
.c{background:#1d2030;border-radius:12px;padding:12px;margin:10px}input,button{font:inherit;padding:10px;border-radius:8px;border:0;margin:3px 0}input{background:#2a2e45;color:#fff;width:100%}
button{background:#f90;color:#000;font-weight:700;cursor:pointer}button.g{background:#2a2e45;color:#fff}.r{display:flex;gap:6px;align-items:center}.r>*{flex:1}.s{flex:0 0 auto}
nav{position:fixed;bottom:0;left:0;right:0;display:flex;background:#1d2030}nav button{flex:1;background:0;color:#aaa;border-radius:0}nav .on{color:#f90}
.big{font-size:44px;font-weight:800;text-align:center}.sm{color:#999;font-size:13px}table{width:100%;border-collapse:collapse}td{padding:5px;border-bottom:1px solid #2a2e45}
#toast{position:fixed;top:10px;left:10px;right:10px;background:#f90;color:#000;padding:10px;border-radius:8px;display:none;z-index:9;text-align:center}</style><div id=toast></div>'''
JS0='''const $=i=>document.getElementById(i),esc=s=>String(s??"").replace(/[&<>"]/g,c=>"&#"+c.charCodeAt(0)+";");
function toast(m){const t=$("toast");t.textContent=m;t.style.display="block";setTimeout(()=>t.style.display="none",2500)}'''
INDEX=CSS+'''<script src=https://telegram.org/js/telegram-web-app.js></script><title>Rizo Games</title>
<div id=sub class=c style=display:none></div>
<div id=p0><div class=c><div class=r><b>💰 <span id=bal>0</span> ⭐ <span class=sm>(demo)</span></b><span class="sm s" id=hist></span></div>
<canvas id=cv width=340 height=210 style="width:100%;background:#12141c;border-radius:10px"></canvas><div class=big id=big>x1.00</div>
<div class=r><input id=amt type=number><input id=auto type=number step=.1 placeholder="Avto-yechish x"></div>
<div class=r><button class=g onclick="amt.value=MIN">Min</button><button class=g onclick="amt.value=amt.value*2">x2</button><button class=g onclick="amt.value=Math.floor(BAL)">Hammasi</button></div>
<button id=go style=width:100% onclick=go()>Stavka</button></div><div class=c id=bets></div></div>
<div id=p1 style=display:none><div class=c><input id=pb type=number><div class=r><button onclick="pv('new')">⚔️ Duel ochish</button><button class=g onclick="pv('bot')">🤖 Bot bilan</button></div>
<div class=sm>Yutgan 2x stavkadan komissiyani chegirib oladi (50/50)</div></div><div class=c id=dl></div><div class=c id=dh></div></div>
<div id=p2 style=display:none><div class=c id=tk></div></div><div id=p3 style=display:none><div class=c id=rt></div></div>
<div id=p4 style=display:none><div class=c id=pf></div><div class=c><input id=pc placeholder=Promokod><button style=width:100% onclick=promo()>Aktivlashtirish</button></div></div>
<nav><button onclick=tab(0)>🚀<br>Crash</button><button onclick=tab(1)>⚔️<br>PvP</button><button onclick=tab(2)>📋<br>Vazifa</button><button onclick=tab(3)>🏆<br>Reyting</button><button onclick=tab(4)>👤<br>Profil</button></nav>
<script>'''+JS0+'''
const tg=window.Telegram&&Telegram.WebApp;if(tg){tg.ready();tg.expand()}
const gid=localStorage.gid||(localStorage.gid=Math.random().toString(36).slice(2,12));
const gname=localStorage.gn||(localStorage.gn=prompt("Ismingiz:","Mehmon")||"Mehmon");
let MIN=1000,BAL=0,TAB=0,st={ph:"wait",el:-9,t0:0,my:null};const img=new Image();img.src="/static/rocket.png";
async function api(p,b){const h={"Content-Type":"application/json"};if(tg&&tg.initData)h["X-Init"]=tg.initData;else{h["X-Guest"]=gid;h["X-Name"]=encodeURIComponent(gname)}
 const r=await fetch(p,{method:b?"POST":"GET",headers:h,body:b?JSON.stringify(b):undefined});const j=await r.json().catch(()=>({error:"Xato"}));
 if(!r.ok){if(j.error=="sub")showSub(j.channels);else toast(j.error);throw j}return j}
function showSub(c){const s=$("sub");s.style.display="block";s.innerHTML="<b>Majburiy obuna:</b><br>"+c.map(x=>`<a style=color:#f90 href="https://t.me/${esc(x.replace("@",""))}" target=_blank>${esc(x)}</a>`).join("<br>")+"<br><button onclick=location.reload()>Tekshirish</button>"}
function tab(i){TAB=i;for(let k=0;k<5;k++)$("p"+k).style.display=k==i?"":"none";({1:pvl,2:tasks,3:rating,4:me})[i]&&({1:pvl,2:tasks,3:rating,4:me})[i]()}
function setBal(b){BAL=b;$("bal").textContent=b.toLocaleString()}
async function me(){const j=await api("/api/me");MIN=j.min;if(!amt.value)amt.value=pb.value=MIN;setBal(j.u.bal);if(j.sub.length)showSub(j.sub);
 $("pf").innerHTML=`<div class=r>${j.u.ph?`<img src="${esc(j.u.ph)}" width=60 style="border-radius:50%;flex:0 0 60px">`:"👤"}<div><b>${esc(j.u.name)}</b><br><span class=sm>${j.u.un?"@"+esc(j.u.un):""} ID: ${esc(j.u.id)}</span></div></div><p>💰 Balans: <b>${j.u.bal}</b> ⭐<br>🏆 Reyting o'rni: <b>#${j.rank}</b><br>🎮 O'yinlar: ${j.u.games}<br>💵 Jami yutuq: ${j.u.won}</p>`}
async function poll(){try{const j=await api("/api/crash");const lp=st.ph;st={ph:j.ph,el:j.el,cp:j.cp,my:j.my,t0:performance.now()-j.el*1000,t1:performance.now()};setBal(j.bal);
 $("hist").textContent=j.hist.slice(-5).map(x=>x.toFixed(2)+"x").join(" ");
 $("bets").innerHTML=j.bets.map(b=>`<div class=r><span>${esc(b.n)}</span><span>${b.a}</span><span>${b.s=="won"?"✅ x"+b.m:b.s=="lost"?"❌":"…"}</span></div>`).join("")||"<span class=sm>Hali stavka yo'q</span>";
 const g=$("go");if(st.my&&st.my.s=="open"&&j.ph=="fly"){g.textContent="YECHISH";g.style.background="#2ecc71"}else{g.style.background="#f90";g.textContent=st.my&&st.my.s=="open"?"Stavka qabul qilindi ✔":st.my&&st.my.s=="won"?"Yutdingiz +"+st.my.pay:"Stavka"}}catch(e){}}
async function go(){try{if(st.my&&st.my.s=="open"){if(st.ph=="fly")await api("/api/crash/cash",{})}else await api("/api/crash/bet",{amt:+amt.value,auto:+auto.value||0});poll()}catch(e){}}
function draw(){const x=cv.getContext("2d"),W=cv.width,H=cv.height;x.clearRect(0,0,W,H);let t=0,m=1;
 if(st.ph=="fly"){t=(performance.now()-st.t0)/1e3;m=Math.exp(.12*t)}else if(st.ph=="crashed"){m=st.cp;t=Math.log(m)/.12}
 const tx=Math.max(6,t),my=Math.max(2,m),P=u=>[20+u/tx*(W-90),H-30-(Math.exp(.12*u)-1)/(my-1)*(H-70)];
 x.strokeStyle="#f90";x.lineWidth=3;x.beginPath();x.moveTo(...P(0));for(let i=1;i<=40;i++)x.lineTo(...P(t*i/40));x.stroke();const[px,py]=P(t);
 if(st.ph=="crashed"){x.font="40px serif";x.fillText("💥",px-10,py+15)}else x.drawImage(img,px-20,py-35,75,48);
 $("big").style.color=st.ph=="crashed"?"#e74c3c":"#fff";$("big").textContent=st.ph=="wait"?"⏳ "+Math.max(0,Math.ceil(-st.el-(performance.now()-st.t1)/1e3))+" s":(st.ph=="crashed"?"💥 ":"")+"x"+m.toFixed(2);requestAnimationFrame(draw)}
async function pvl(){const j=await api("/api/pvp");setBal(j.bal);$("dl").innerHTML="<b>Ochiq duellar</b>"+(j.open.map(d=>`<div class=r><span>${esc(d.cn)}</span><b>${d.bet}</b>`+(d.cu==j.me?`<button class=g onclick="pc(${d.id})">Bekor</button>`:`<button onclick="pj(${d.id})">Qo'shilish</button>`)+"</div>").join("")||"<br><span class=sm>Hozircha yo'q</span>");
 $("dh").innerHTML="<b>Oxirgi o'yinlar</b>"+j.mine.map(d=>`<div class=sm>${esc(d.cn)} vs ${esc(d.jn)} — ${d.bet} ⭐</div>`).join("")}
async function pv(k){try{const j=await api("/api/pvp/"+k,{bet:+pb.value});if(k=="bot")toast(j.win?"🎉 Yutdingiz!":"😔 Yutqazdingiz");pvl()}catch(e){}}
async function pj(i){try{const j=await api("/api/pvp/join",{id:i});toast(j.win?"🎉 Yutdingiz!":"😔 Yutqazdingiz");pvl()}catch(e){}}
async function pc(i){await api("/api/pvp/cancel",{id:i});pvl()}
const opened={};
async function tasks(){const j=await api("/api/tasks");$("tk").innerHTML=j.map(t=>`<div class=r><span>${esc(t.title)}<br><span class=sm>+${t.rew} ⭐</span></span>`+(t.done?"✅":`<button onclick="doT(${t.id},'${esc(t.url)}')">${opened[t.id]?"Tekshirish":"Bajarish"}</button>`)+"</div>").join("")||"Vazifa yo'q"}
async function doT(i,u){if(!opened[i]){opened[i]=1;tg?tg.openLink(u):window.open(u);return tasks()}try{const j=await api("/api/tasks/claim",{id:i});toast("+"+j.rew+" ⭐");tasks()}catch(e){}}
async function rating(){const j=await api("/api/rating");$("rt").innerHTML="<table>"+j.map((r,i)=>`<tr><td>${i+1}</td><td>${esc(r.name)}</td><td>${r.bal} ⭐</td></tr>`).join("")+"</table>"}
async function promo(){try{const j=await api("/api/promo",{code:pc_.value});toast("+"+j.amt+" ⭐");me()}catch(e){}}
const pc_=$("pc");me();poll();setInterval(poll,600);setInterval(()=>TAB==1&&pvl(),3000);draw();
</script>'''
ADMIN=CSS+'''<title>Admin</title><div class=c><b>⚙️ Admin panel</b> <span class=sm>admin panel kodi: ayubrizofactory</span><div class=r style=flex-wrap:wrap>
<button class=g onclick=V('stats')>📊</button><button class=g onclick=V('users')>👥</button><button class=g onclick=V('promo')>🎟</button><button class=g onclick=V('task')>📋</button><button class=g onclick=V('ch')>📢</button><button class=g onclick=V('settings')>💰</button></div></div><div class=c id=v></div>
<script>'''+JS0+'''
let T=localStorage.adm||"";
async function A(p,b){const r=await fetch("/api/admin/"+p,{method:b?"POST":"GET",headers:{"Content-Type":"application/json","X-Admin":T},body:b?JSON.stringify(b):undefined});const j=await r.json();
 if(r.status==401&&p!="login"){await login();return A(p,b)}if(!r.ok){toast(j.error);throw j}return j}
async function login(){const c=prompt("Admin kodi:");const r=await fetch("/api/admin/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:c})});const j=await r.json();if(j.t){T=j.t;localStorage.adm=T}else{toast("Noto'g'ri kod");throw 0}}
const F=(id,ph,t)=>`<input id=${id} placeholder="${ph}" ${t?"type=number":""}>`;
async function V(w){const v=$("v");
 if(w=="stats"){const s=await A("stats");v.innerHTML=`👥 Foydalanuvchi: ${s.users} (ban: ${s.banned})<br>💰 Jami balans: ${s.bal}<br>🚀 Crash stavkalar: ${s.crash}<br>Tikilgan: ${s.wag} / To'langan: ${s.paid}<br>⚔️ PvP o'yinlar: ${s.duels}`}
 if(w=="users"){const u=await A("users?s="+encodeURIComponent(window.sq||""));v.innerHTML=F("sq","Qidirish (ism/ID)")+`<button onclick="sq_=sq.value;window.sq=sq.value;V('users')">🔍</button>${F("ga","Hammaga stars",1)}<button onclick="bal('all','',ga.value)">🎁 Hammaga berish</button><table>`+u.map(x=>`<tr><td>${esc(x.name)}<br><span class=sm>${esc(x.id)}</span></td><td>${x.bal}</td><td><button class=g onclick="b2('${esc(x.id)}')">±</button><button class=g onclick="bn('${esc(x.id)}',${x.ban?0:1})">${x.ban?"✅":"🚫"}</button></td></tr>`).join("")+"</table>"}
 if(w=="promo"||w=="task"||w=="ch"){const l=await A(w);const f={promo:F("a","Kod")+F("b","Stars",1)+F("c","Limit (nechta kishi)",1),task:F("a","Sarlavha")+F("b","Stars",1)+F("c","Havola https://t.me/...")+F("d","Kanal @username (ixtiyoriy tekshiruv)"),ch:F("a","@kanal_username (majburiy obuna)")}[w];
  const pk=w=="promo"?"code":"id";v.innerHTML=f+`<button style=width:100% onclick="sv('${w}')">➕ Qo'shish</button><table>`+l.map(x=>`<tr><td>${esc(x.code||x.title||x.chat)} ${x.amt!=null?x.amt+"⭐ "+x.used+"/"+x.mx:x.rew?x.rew+"⭐":""}</td><td><button class=g onclick="dl('${w}','${esc(x[pk])}')">🗑</button></td></tr>`).join("")+"</table>"}
 if(w=="settings"){const s=await A("settings"),n={min_bet:"Minimal stavka",max_bet:"Maksimal stavka",house_edge:"Crash komissiya %",pvp_fee:"PvP komissiya %",start_balance:"Boshlang'ich demo balans",crash_wait:"Raundlar orasi (s)"};
  v.innerHTML=Object.keys(n).map(k=>`<div class=sm>${n[k]}</div><input id=s_${k} type=number value=${s[k]}>`).join("")+`<button style=width:100% onclick=ss()>Saqlash</button>`}}
async function sv(w){const g=i=>$(i)?$(i).value:"";const d=w=="promo"?{code:g("a"),amt:g("b"),mx:g("c")}:w=="task"?{title:g("a"),rew:g("b"),url:g("c"),chat:g("d")}:{chat:g("a")};await A(w,d);V(w)}
async function dl(w,i){if(confirm("O'chirilsinmi?")){await A(w,{del:i});V(w)}}
async function ss(){const d={};document.querySelectorAll("[id^=s_]").forEach(e=>d[e.id.slice(2)]=e.value);await A("settings",d);toast("Saqlandi ✔")}
async function bal(m,id,a){await A("bal",{mode:m,id,amt:a});toast("Bajarildi ✔");V("users")}
function b2(id){const a=prompt("Stars miqdori (minus = ayirish, 'set:5000' = o'rnatish):");if(!a)return;a.startsWith("set:")?bal("set",id,a.slice(4)):bal("add",id,a)}
async function bn(id,b){await A("ban",{id,ban:b});V("users")}
V("stats");
</script>'''
@app.get("/")
def index():return Response(INDEX,mimetype="text/html")
@app.get("/admin")
def admin():return Response(ADMIN,mimetype="text/html")
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),threaded=True)
