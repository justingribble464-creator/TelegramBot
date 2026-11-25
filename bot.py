import asyncio, random, sqlite3, string, time
from datetime import datetime
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes)

# ================= CONFIG =================
BOT_TOKEN  = "9010283186:AAEzHy4wO6fPNHEelHEZm5CDl-E8k24YYjE"
OXAPAY_KEY = "Z03HJZ-RULWBG-XWFRRB-MO2LZ9"
OWNER_ID   = 9295849570
CHAT_ID    = -5482812761
MIN_BET, MAX_BET = 1.0, 2500.0
DB = "casino.db"
OXA = "https://api.oxapay.com"
COINS = {"BTC":"bc1q86cnjkhwesemhmug9ee3x6rmndprh06mma35jp",
         "ETH":"0x871C2bD5eF8C06aEa4dBF1fFe147160B7d88fdd0",
         "USDT":"0x871C2bD5eF8C06aEa4dBF1fFe147160B7d88fdd0",
         "SOL":"FJQcPje7XNgRLcY23eHqMBjPTgR4hYxHvDspKd9g7VsF",
         "XRP":"rh8EEFB3nfA7StoM4nFUWJqNQYwkdnG1zb"}
XRP_TAG = "133085363"

# ================= DB =================
def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
def init_db():
    c = db(); c.executescript("""
    CREATE TABLE IF NOT EXISTS users(uid INTEGER PRIMARY KEY, pid TEXT UNIQUE, name TEXT,
      balance REAL DEFAULT 0, banned INTEGER DEFAULT 0, join_date TEXT, first_game TEXT,
      last_game TEXT, games INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, wagered REAL DEFAULT 0, won REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS house(id INTEGER PRIMARY KEY, balance REAL);
    CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, game TEXT,
      bet REAL, result TEXT, payout REAL, ts TEXT);
    CREATE TABLE IF NOT EXISTS dep(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, track TEXT,
      coin TEXT, amount REAL, status TEXT, ts TEXT);
    CREATE TABLE IF NOT EXISTS wd(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, coin TEXT,
      addr TEXT, amount REAL, status TEXT, ts TEXT);
    CREATE TABLE IF NOT EXISTS sessions(uid INTEGER PRIMARY KEY, state TEXT, data TEXT);
    INSERT OR IGNORE INTO house(id,balance) VALUES(1,0);
    """); c.commit(); c.close()
def new_pid(): return "".join(random.choices(string.digits, k=8))
def get_user(uid, name=""):
    c = db()
    c.execute("INSERT OR IGNORE INTO users(uid,pid,name,join_date) VALUES(?,?,?,?)",
              (uid, new_pid(), name, datetime.utcnow().isoformat()))
    if name: c.execute("UPDATE users SET name=? WHERE uid=?", (name, uid))
    c.commit(); r = c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone(); c.close(); return dict(r)
def set_bal(uid, v):
    c = db(); c.execute("UPDATE users SET balance=? WHERE uid=?", (max(0.0,v),uid)); c.commit(); c.close()
def add_bal(uid, v): set_bal(uid, get_user(uid)["balance"]+v)
def house_bal():
    c = db(); r = c.execute("SELECT balance FROM house WHERE id=1").fetchone(); c.close(); return r["balance"]
def add_house(v):
    c = db(); c.execute("UPDATE house SET balance=balance+? WHERE id=1",(v,)); c.commit(); c.close()
def log_game(uid, game, bet, result, payout):
    now = datetime.utcnow().isoformat(); c = db()
    c.execute("INSERT INTO history(uid,game,bet,result,payout,ts) VALUES(?,?,?,?,?,?)",(uid,game,bet,result,payout,now))
    c.execute("""UPDATE users SET games=games+1,wagered=wagered+?,wins=wins+?,won=won+?,last_game=?,
        first_game=COALESCE(first_game,?) WHERE uid=?""",(bet,1 if payout>0 else 0,payout,now,now,uid))
    c.commit(); c.close()
def settle(uid, bet, payout):
    u = get_user(uid); set_bal(uid, u["balance"]-bet+payout); add_house(bet-payout)
def sess_set(uid, state, data=""):
    c = db(); c.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)",(uid,state,data)); c.commit(); c.close()
def sess_get(uid):
    c = db(); r = c.execute("SELECT * FROM sessions WHERE uid=?",(uid,)).fetchone(); c.close(); return dict(r) if r else None
def sess_clear(uid):
    c = db(); c.execute("DELETE FROM sessions WHERE uid=?",(uid,)); c.commit(); c.close()

# ================= OxaPay =================
async def oxa(path, payload):
    async with httpx.AsyncClient(timeout=20) as h:
        r = await h.post(OXA+path, json={**payload,"merchant_api_key":OXAPAY_KEY}); return r.json()
async def ltc_price():
    try:
        async with httpx.AsyncClient(timeout=10) as h:
            r = await h.get("https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=usd")
            return r.json()["litecoin"]["usd"]
    except: return 80.0
def money(v): return f"${v:,.2f}"

# ================= Guards =================
def guard(uid):
    if get_user(uid)["banned"]: return "🚫 You are banned."
    return None
def bet_check(uid, bet):
    u = get_user(uid)
    if bet < MIN_BET: return f"❌ Min bet {money(MIN_BET)}."
    if bet > MAX_BET: return f"❌ Max bet {money(MAX_BET)}."
    if u["balance"] < bet: return f"❌ Not enough. You have {money(u['balance'])}."
    return None
def parse_bet(s):
    try: return float(s)
    except: return None

# ================= MENU =================
GAMES = [("🎲 Dice","g_dice"),("🃏 Blackjack","g_bj"),("🏀 Basketball","g_bb"),("🪙 Coinflip","g_coin"),
         ("⚽ Soccer","g_soccer"),("💣 Mines","g_mines"),("🎡 Roulette","g_roul"),("🐔 Chicken Cross","g_chick"),
         ("🗼 Tower","g_tower")]
async def start(update, ctx):
    u = update.effective_user; get_user(u.id, u.first_name)
    kb = [[InlineKeyboardButton("🎮 Games",callback_data="games")],
          [InlineKeyboardButton("💰 Balance",callback_data="bal"),InlineKeyboardButton("👤 Profile",callback_data="prof")]]
    await update.message.reply_text(f"🏠 *Welcome to PURE Casino, {u.first_name}!*\n\n🆔 ID: `{get_user(u.id)['pid']}`\n\n"
        "Use /games to browse.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
async def games_cmd(update, ctx):
    rows, row = [], []
    for i,(label,cb) in enumerate(GAMES):
        row.append(InlineKeyboardButton(label,callback_data=cb))
        if (i+1)%2==0: rows.append(row); row=[]
    if row: rows.append(row)
    await update.message.reply_text("🎮 *Choose a game:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

async def games_cb(update, ctx):
    q = update.callback_query; await q.answer()
    cb = q.data.replace("g_","")
    guide = {
      "dice":"🎲 *Dice*\n/ dice <bet> — then pick mode, dice count, rounds, and who rolls first.",
      "bj":"🃏 *Black