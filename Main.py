import os
import sqlite3
import json
import urllib.request
from datetime import datetime
from threading import Thread, Lock
from types import SimpleNamespace

import telebot
from telebot import types
from flask import Flask


# ============================================================
#                    TRADSCALE BOT CONFIG
# ============================================================

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "638640702"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DB_PATH = os.getenv("TRADSCALE_DB", "tradscale.db")

# کانال برای پست خودکار (اختیاری)
CHANNEL_ID = os.getenv("CHANNEL_ID", "@neowavetradescale")

if not API_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")

bot = telebot.TeleBot(API_TOKEN, parse_mode="HTML")
app = Flask("TradScale")

db_lock = Lock()
user_states = {}

# ---------- لینک‌های پیش‌فرض ----------
DEFAULT_CHANNEL = "https://t.me/neowavetradescale"
DEFAULT_GROUP   = "https://t.me/tradescalegroup"
DEFAULT_YOUTUBE = "https://youtube.com/@neowave-forex-trader?si=z2pMVBRzf9uoEVpd"

ANALYSIS_CATEGORIES = ["Forex", "Crypto", "Indices", "Commodities", "Other"]
TUTORIAL_CATEGORIES = [
    "Technical", "SmartMoney", "Risk",
    "Psychology", "Prop", "System", "NeoWave", "Other"
]


# ============================================================
#                         DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT, first_name TEXT, last_name TEXT,
                created_at TEXT, updated_at TEXT,
                xp INTEGER DEFAULT 0,
                level TEXT DEFAULT 'Beginner'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, symbol TEXT, direction TEXT,
                account_size REAL, risk_percent REAL, entry REAL,
                stop_loss REAL, take_profit REAL, position_size REAL,
                risk_amount REAL, potential_profit REAL, rr REAL,
                setup TEXT, reason TEXT, emotion_before TEXT,
                result TEXT DEFAULT 'OPEN', pnl REAL DEFAULT 0,
                screenshot_file_id TEXT, created_at TEXT, closed_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS prop_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, name TEXT, account_size REAL,
                profit_target_percent REAL, max_drawdown_percent REAL,
                daily_drawdown_percent REAL, starting_balance REAL,
                current_balance REAL, created_at TEXT, updated_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, admin_message_id INTEGER,
                user_message_id INTEGER, message_type TEXT DEFAULT 'contact',
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, link TEXT, category TEXT DEFAULT 'Forex',
                description TEXT, created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tutorials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, content TEXT, link TEXT,
                category TEXT DEFAULT 'Technical', created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analysis_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, symbol TEXT, timeframe TEXT,
                note TEXT, admin_message_id INTEGER, user_message_id INTEGER,
                status TEXT DEFAULT 'PENDING', created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY, value TEXT
            )
        """)

        # Migration
        try:
            cur.execute("ALTER TABLE admin_messages ADD COLUMN message_type TEXT DEFAULT 'contact'")
        except sqlite3.OperationalError:
            pass

        # ثبت لینک‌های پیش‌فرض (فقط یک‌بار)
        cur.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('channel_link', ?)",
                    (DEFAULT_CHANNEL,))
        cur.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('group_link', ?)",
                    (DEFAULT_GROUP,))
        cur.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('youtube_link', ?)",
                    (DEFAULT_YOUTUBE,))

        conn.commit()
        conn.close()


def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def get_setting(key, default=""):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        row = cur.fetchone()
        conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO bot_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
        conn.commit()
        conn.close()


# ---------- USERS ----------

def ensure_user(message):
    user = message.from_user
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
        exists = cur.fetchone()
        if exists:
            cur.execute("""
                UPDATE users SET username=?, first_name=?, last_name=?, updated_at=?
                WHERE user_id=?
            """, (user.username, user.first_name, user.last_name, now(), user.id))
        else:
            cur.execute("""
                INSERT INTO users (user_id, username, first_name, last_name,
                                   created_at, updated_at, xp, level)
                VALUES (?, ?, ?, ?, ?, ?, 0, 'Beginner')
            """, (user.id, user.username, user.first_name, user.last_name, now(), now()))
        conn.commit()
        conn.close()


def add_xp(user_id, amount):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT xp FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return
        xp = int(row["xp"]) + amount
        if xp >= 1000:
            level = "TradScale Pro"
        elif xp >= 500:
            level = "Consistent Trader"
        elif xp >= 200:
            level = "Disciplined Trader"
        else:
            level = "Beginner"
        cur.execute("UPDATE users SET xp=?, level=?, updated_at=? WHERE user_id=?",
                    (xp, level, now(), user_id))
        conn.commit()
        conn.close()


def get_user_profile(user_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
    return row


# ============================================================
#                         HELPERS
# ============================================================

def set_state(user_id, state, data=None):
    user_states[user_id] = {"state": state, "data": data or {}}


def get_state(user_id):
    return user_states.get(user_id, {})


def clear_state(user_id):
    user_states.pop(user_id, None)


def is_cancel(text):
    return text and text.strip().lower() in ["/cancel", "cancel", "لغو", "❌ لغو"]


def safe_float(value):
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def fmt_number(value, decimals=2):
    try:
        return f"{float(value):,.{decimals}f}"
    except Exception:
        return "-"


def is_admin(chat_id):
    return chat_id == ADMIN_CHAT_ID


def cancel_message(message):
    clear_state(message.from_user.id)
    bot.send_message(message.chat.id,
                     "❌ عملیات لغو شد.\n\nاز منوی اصلی می‌تونی دوباره شروع کنی.")
    show_main_menu(message.chat.id)


def call_to_message(call):
    """پیام جعلی که از طرف کاربر واقعی است، نه ربات.
    این تابع باگ اصلی state را در دکمه‌های شیشه‌ای حل می‌کند."""
    return SimpleNamespace(
        from_user=call.from_user,
        chat=call.message.chat,
        message_id=call.message.message_id,
        text=call.message.text or "",
        content_type="text",
    )


# ============================================================
#                         MAIN MENU
# ============================================================

def main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)

    markup.add(
        types.InlineKeyboardButton("📊 تحلیل‌ها", callback_data="menu_analyses"),
        types.InlineKeyboardButton("🎓 آموزش‌ها", callback_data="menu_tutorials")
    )
    markup.add(
        types.InlineKeyboardButton("💬 ارتباط با استاد", callback_data="contact_admin"),
        types.InlineKeyboardButton("📝 درخواست تحلیل", callback_data="menu_request_analysis")
    )
    markup.add(
        types.InlineKeyboardButton("🔗 لینک گروه و کانال", callback_data="menu_links")
    )
    markup.add(
        types.InlineKeyboardButton("📈 ابزارهای معاملاتی", callback_data="menu_tools"),
        types.InlineKeyboardButton("🤖 دستیار AI", callback_data="menu_ai")
    )
    markup.add(
        types.InlineKeyboardButton("⚙️ تنظیمات", callback_data="menu_settings"),
        types.InlineKeyboardButton("🔧 پنل مدیریت", callback_data="admin_panel")
    )
    return markup


def show_main_menu(chat_id):
    bot.send_message(
        chat_id,
        "<b>📈 TradScale</b>\n\n"
        "به دستیار معاملاتی TradScale خوش اومدی.\n\n"
        "اینجا فقط قرار نیست درباره Trading صحبت کنیم؛\n"
        "قرارِ اینه که <b>بهتر فکر کنیم، بهتر ریسک کنیم و منظم‌تر معامله کنیم.</b>\n\n"
        "از منوی زیر انتخاب کن:",
        reply_markup=main_keyboard()
    )


def show_tools_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Risk Calculator", callback_data="menu_risk"),
        types.InlineKeyboardButton("🏆 Prop Challenge", callback_data="menu_prop")
    )
    markup.add(
        types.InlineKeyboardButton("📔 Trading Journal", callback_data="menu_journal"),
        types.InlineKeyboardButton("📈 Dashboard", callback_data="menu_dashboard")
    )
    markup.add(
        types.InlineKeyboardButton("🧠 Psychology Check", callback_data="menu_psychology"),
        types.InlineKeyboardButton("🎓 Academy", callback_data="menu_academy")
    )
    markup.add(
        types.InlineKeyboardButton("📚 Content Hub", callback_data="menu_content")
    )
    markup.add(
        types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
    )
    bot.send_message(chat_id, "📈 <b>ابزارهای معاملاتی</b>\n\nیک بخش انتخاب کن:", reply_markup=markup)


# ============================================================
#                    ANALYSES (user)
# ============================================================

def show_analyses_menu(chat_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT category FROM analyses ORDER BY category")
        rows = cur.fetchall()
        conn.close()

    if not rows:
        bot.send_message(
            chat_id,
            "📊 <b>تحلیل‌ها</b>\n\n"
            "در حال حاضر تحلیلی ثبت نشده است.\n"
            "به زودی تحلیل‌های جدید اینجا قرار می‌گیرند.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
            )
        )
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    for row in rows:
        cat = row["category"]
        markup.add(types.InlineKeyboardButton(
            f"📁 {cat}", callback_data=f"analysis_cat_{cat}"
        ))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu"))

    bot.send_message(chat_id, "📊 <b>تحلیل‌ها</b>\n\nدسته را انتخاب کن:", reply_markup=markup)


def show_analyses_by_category(chat_id, category):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM analyses WHERE category = ? ORDER BY id DESC", (category,))
        rows = cur.fetchall()
        conn.close()

    if not rows:
        bot.send_message(chat_id, f"📁 <b>{category}</b>\n\nتحلیلی در این دسته ثبت نشده.")
        return

    markup = types.InlineKeyboardMarkup()
    for row in rows:
        if row["link"]:
            markup.add(types.InlineKeyboardButton(f"📄 {row['name']}", url=row["link"]))
        else:
            markup.add(types.InlineKeyboardButton(
                f"📄 {row['name']} (بدون لینک)",
                callback_data=f"analysis_noop_{row['id']}"
            ))
    markup.add(types.InlineKeyboardButton("◀️ بازگشت", callback_data="menu_analyses"))

    bot.send_message(chat_id, f"📁 <b>{category}</b>\n\n{len(rows)} تحلیل:", reply_markup=markup)


# ============================================================
#                    TUTORIALS (user)
# ============================================================

def show_tutorials_menu(chat_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT category FROM tutorials ORDER BY category")
        rows = cur.fetchall()
        conn.close()

    if not rows:
        bot.send_message(
            chat_id,
            "🎓 <b>آموزش‌ها</b>\n\n"
            "در حال حاضر آموزشی ثبت نشده است.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
            )
        )
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    for row in rows:
        markup.add(types.InlineKeyboardButton(
            f"📁 {row['category']}", callback_data=f"tutorial_cat_{row['category']}"
        ))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu"))
    bot.send_message(chat_id, "🎓 <b>آموزش‌ها</b>\n\nدسته را انتخاب کن:", reply_markup=markup)


def show_tutorials_by_category(chat_id, category):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tutorials WHERE category = ? ORDER BY id DESC", (category,))
        rows = cur.fetchall()
        conn.close()

    if not rows:
        bot.send_message(chat_id, f"📁 <b>{category}</b>\n\nآموزشی در این دسته ثبت نشده.")
        return

    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup.add(types.InlineKeyboardButton(
            f"📚 {row['title']}", callback_data=f"tutorial_view_{row['id']}"
        ))
    markup.add(types.InlineKeyboardButton("◀️ بازگشت", callback_data="menu_tutorials"))
    bot.send_message(chat_id, f"📁 <b>{category}</b>\n\n{len(rows)} آموزش:", reply_markup=markup)


def show_tutorial_detail(chat_id, tutorial_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tutorials WHERE id = ?", (tutorial_id,))
        row = cur.fetchone()
        conn.close()

    if not row:
        bot.send_message(chat_id, "❌ آموزش پیدا نشد.")
        return

    text = f"📚 <b>{row['title']}</b>\n\n"
    if row["content"]:
        text += row["content"] + "\n\n"
    if row["link"]:
        text += f"🔗 <a href='{row['link']}'>مشاهده منبع</a>"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "◀️ بازگشت", callback_data=f"tutorial_cat_{row['category']}"
    ))
    bot.send_message(chat_id, text, reply_markup=markup)


# ============================================================
#                    LINKS
# ============================================================

def show_links(chat_id):
    channel_link = get_setting("channel_link", DEFAULT_CHANNEL)
    group_link   = get_setting("group_link",   DEFAULT_GROUP)
    youtube_link = get_setting("youtube_link", DEFAULT_YOUTUBE)

    markup = types.InlineKeyboardMarkup()
    if channel_link:
        markup.add(types.InlineKeyboardButton("📢 کانال تلگرام", url=channel_link))
    if group_link:
        markup.add(types.InlineKeyboardButton("👥 گروه تلگرام", url=group_link))
    if youtube_link:
        markup.add(types.InlineKeyboardButton("▶️ YouTube", url=youtube_link))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu"))

    bot.send_message(
        chat_id,
        "🔗 <b>لینک گروه و کانال</b>\n\nبرای عضویت روی دکمه‌های زیر بزن:",
        reply_markup=markup,
        disable_web_page_preview=True
    )


# ============================================================
#                    REQUEST ANALYSIS
# ============================================================

def start_request_analysis(message):
    set_state(message.from_user.id, "req_symbol")
    bot.send_message(
        message.chat.id,
        "📝 <b>درخواست تحلیل</b>\n\nمرحله 1 از 3\n\nنماد:\n<code>XAUUSD</code>"
    )


def request_analysis_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    if not state.get("state", "").startswith("req_"):
        return False
    if is_cancel(message.text):
        cancel_message(message)
        return True
    current = state["state"]
    data = state["data"]

    if current == "req_symbol":
        data["symbol"] = message.text.strip().upper()
        set_state(user_id, "req_tf", data)
        bot.send_message(message.chat.id, "مرحله 2 از 3\n\n⏱ تایم‌فریم:\n<code>1D</code>")
        return True

    if current == "req_tf":
        data["tf"] = message.text.strip()
        set_state(user_id, "req_note", data)
        bot.send_message(message.chat.id, "مرحله 3 از 3\n\n✍️ توضیحات یا <code>-</code>:")
        return True

    if current == "req_note":
        data["note"] = message.text.strip()
        send_analysis_request_to_admin(message, data)
        clear_state(user_id)
        return True

    return True


def send_analysis_request_to_admin(message, data):
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else "N/A"

    header = (
        "📝 <b>درخواست تحلیل جدید</b>\n\n"
        f"👤 Name: {message.from_user.first_name or '-'}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"🔗 Username: {username}\n\n"
        "━━━━━━━━━━━━━━\n"
        f"📊 Symbol: <b>{data['symbol']}</b>\n"
        f"⏱ Timeframe: <b>{data['tf']}</b>\n"
        f"📝 Note: {data['note']}\n"
        "━━━━━━━━━━━━━━\n\n"
        "👉 برای پاسخ، روی همین پیام <b>Reply</b> کنید."
    )

    try:
        info_msg = bot.send_message(ADMIN_CHAT_ID, header)
        with db_lock:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO admin_messages
                (user_id, admin_message_id, user_message_id, message_type, created_at)
                VALUES (?, ?, ?, 'analysis_request', ?)
            """, (user_id, info_msg.message_id, message.message_id, now()))
            cur.execute("""
                INSERT INTO analysis_requests
                (user_id, symbol, timeframe, note, admin_message_id, user_message_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
            """, (user_id, data['symbol'], data['tf'], data['note'],
                  info_msg.message_id, message.message_id, now()))
            conn.commit()
            conn.close()
        bot.reply_to(message,
            "✅ درخواست تحلیل شما برای استاد ارسال شد.\n"
            "پاسخ مستقیم برای شما ارسال می‌شود.")
    except Exception as e:
        print("Analysis request error:", e)
        bot.reply_to(message, "❌ خطا در ارسال درخواست.")


# ============================================================
#                    CONTACT ADMIN
# ============================================================

def start_contact_admin(message):
    set_state(message.from_user.id, "contact_admin")
    bot.send_message(
        message.chat.id,
        "💬 <b>ارتباط با استاد</b>\n\n"
        "پیامت را همینجا بنویس.\n"
        "پیام مستقیماً برای استاد ارسال می‌شود.\n\n"
        "برای لغو: <code>/cancel</code>"
    )


def send_to_admin(message):
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else "بدون Username"

    header = (
        "📩 <b>New TradScale Message</b>\n\n"
        f"👤 Name: {message.from_user.first_name or '-'}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"🔗 Username: {username}\n"
        "━━━━━━━━━━━━━━\n"
        "👉 برای پاسخ، روی پیام کپی‌شده <b>Reply</b> کنید."
    )

    try:
        bot.send_message(ADMIN_CHAT_ID, header)
        copied = bot.copy_message(ADMIN_CHAT_ID, message.chat.id, message.message_id)
        with db_lock:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO admin_messages
                (user_id, admin_message_id, user_message_id, message_type, created_at)
                VALUES (?, ?, ?, 'contact', ?)
            """, (user_id, copied.message_id, message.message_id, now()))
            conn.commit()
            conn.close()
        bot.reply_to(message,
            "✅ پیام شما برای استاد ارسال شد.\n"
            "به محض پاسخ، برای شما ارسال می‌شود.")
    except Exception as e:
        print("Admin forward error:", e)
        bot.reply_to(message, "❌ در ارسال پیام مشکلی پیش آمد.")

    clear_state(user_id)


# ============================================================
#                    ADMIN REPLY
# ============================================================

def handle_admin_reply(message):
    if message.chat.id != ADMIN_CHAT_ID:
        return False
    if not message.reply_to_message:
        return False

    replied_id = message.reply_to_message.message_id

    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id, message_type FROM admin_messages
            WHERE admin_message_id = ?
            ORDER BY id DESC LIMIT 1
        """, (replied_id,))
        row = cur.fetchone()
        if row and row["message_type"] == "analysis_request":
            cur.execute("UPDATE analysis_requests SET status='ANSWERED' WHERE admin_message_id=?",
                        (replied_id,))
            conn.commit()
        conn.close()

    if not row:
        return False

    target = row["user_id"]

    try:
        if message.content_type == "text":
            bot.send_message(target, "👤 <b>پاسخ TradScale:</b>\n\n" + message.text)
        elif message.content_type == "photo":
            bot.send_photo(target, message.photo[-1].file_id,
                caption="👤 <b>پاسخ TradScale:</b>\n\n" + (message.caption or ""))
        elif message.content_type == "document":
            bot.send_document(target, message.document.file_id,
                caption="👤 <b>پاسخ TradScale:</b>\n\n" + (message.caption or ""))
        elif message.content_type == "voice":
            bot.send_voice(target, message.voice.file_id)
        else:
            bot.send_message(target, "👤 <b>پاسخ TradScale:</b>\n\nادمین پیام جدیدی فرستاده است.")
        bot.reply_to(message, "📤 پاسخ برای کاربر ارسال شد.")
    except Exception as e:
        print("Admin reply error:", e)
        bot.reply_to(message, "❌ ارسال پاسخ ناموفق بود.")

    return True


# ============================================================
#                    ADMIN PANEL
# ============================================================

def show_admin_panel(chat_id):
    if not is_admin(chat_id):
        bot.send_message(chat_id, "⛔ دسترسی ندارید.")
        return

    channel_link = get_setting("channel_link", DEFAULT_CHANNEL)
    group_link   = get_setting("group_link",   DEFAULT_GROUP)
    youtube_link = get_setting("youtube_link", DEFAULT_YOUTUBE)

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ تحلیل جدید", callback_data="admin_add_analysis"),
        types.InlineKeyboardButton("📊 لیست تحلیل‌ها", callback_data="admin_list_analyses")
    )
    markup.add(
        types.InlineKeyboardButton("➕ آموزش جدید", callback_data="admin_add_tutorial"),
        types.InlineKeyboardButton("📚 لیست آموزش‌ها", callback_data="admin_list_tutorials")
    )
    markup.add(
        types.InlineKeyboardButton("📢 لینک کانال", callback_data="admin_set_channel"),
        types.InlineKeyboardButton("👥 لینک گروه", callback_data="admin_set_group")
    )
    markup.add(
        types.InlineKeyboardButton("▶️ لینک یوتیوب", callback_data="admin_set_youtube")
    )
    markup.add(
        types.InlineKeyboardButton("📮 ارسال منو به کانال", callback_data="admin_post_channel"),
        types.InlineKeyboardButton("📌 پین منو در کانال", callback_data="admin_pin_channel")
    )
    markup.add(
        types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
    )

    bot.send_message(
        chat_id,
        "🔧 <b>پنل مدیریت TradScale</b>\n\n"
        f"📢 کانال: <code>{channel_link}</code>\n"
        f"👥 گروه: <code>{group_link}</code>\n"
        f"▶️ یوتیوب: <code>{youtube_link}</code>\n\n"
        "یکی از گزینه‌ها را انتخاب کن:",
        reply_markup=markup,
        disable_web_page_preview=True
    )


def admin_add_analysis_start(chat_id):
    set_state(ADMIN_CHAT_ID, "admin_analysis_name")
    bot.send_message(chat_id,
        "➕ <b>تحلیل جدید</b>\n\nمرحله 1 از 3\n\nنام تحلیل:\n<code>XAUUSD 1D</code>")


def admin_add_tutorial_start(chat_id):
    set_state(ADMIN_CHAT_ID, "admin_tutorial_title")
    bot.send_message(chat_id,
        "➕ <b>آموزش جدید</b>\n\nمرحله 1 از 4\n\nعنوان آموزش:")


def admin_list_analyses(chat_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM analyses ORDER BY id DESC")
        rows = cur.fetchall()
        conn.close()

    if not rows:
        bot.send_message(chat_id, "📊 هیچ تحلیلی ثبت نشده.")
        return

    text = "📊 <b>لیست تحلیل‌ها</b>\n\n"
    for row in rows:
        text += f"#{row['id']} — <b>{row['name']}</b> ({row['category']})\n"
        if row['link']:
            text += f"   🔗 {row['link']}\n"
        text += "\n"

    bot.send_message(chat_id, text[:4000])

    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [
        types.InlineKeyboardButton(f"🗑 #{r['id']}", callback_data=f"admin_del_analysis_{r['id']}")
        for r in rows
    ]
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    markup.add(types.InlineKeyboardButton("◀️ بازگشت", callback_data="admin_panel"))
    bot.send_message(chat_id, "برای حذف، روی شماره بزن:", reply_markup=markup)


def admin_list_tutorials(chat_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tutorials ORDER BY id DESC")
        rows = cur.fetchall()
        conn.close()

    if not rows:
        bot.send_message(chat_id, "📚 هیچ آموزشی ثبت نشده.")
        return

    text = "📚 <b>لیست آموزش‌ها</b>\n\n"
    for row in rows:
        text += f"#{row['id']} — <b>{row['title']}</b> ({row['category']})\n"
        if row['link']:
            text += f"   🔗 {row['link']}\n"
        text += "\n"

    bot.send_message(chat_id, text[:4000])

    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [
        types.InlineKeyboardButton(f"🗑 #{r['id']}", callback_data=f"admin_del_tutorial_{r['id']}")
        for r in rows
    ]
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    markup.add(types.InlineKeyboardButton("◀️ بازگشت", callback_data="admin_panel"))
    bot.send_message(chat_id, "برای حذف، روی شماره بزن:", reply_markup=markup)


def admin_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    current = state.get("state", "")
    if not current.startswith("admin_"):
        return False
    if is_cancel(message.text):
        cancel_message(message)
        return True

    data = state["data"]

    if current == "admin_analysis_name":
        data["name"] = message.text.strip()
        set_state(user_id, "admin_analysis_link", data)
        bot.send_message(message.chat.id, "مرحله 2 از 3\n\n🔗 لینک تحلیل یا <code>-</code>:")
        return True

    if current == "admin_analysis_link":
        link = message.text.strip()
        data["link"] = "" if link == "-" else link
        set_state(user_id, "admin_analysis_category", data)
        markup = types.InlineKeyboardMarkup(row_width=2)
        for cat in ANALYSIS_CATEGORIES:
            markup.add(types.InlineKeyboardButton(
                f"📁 {cat}", callback_data=f"admin_cat_analysis_{cat}"
            ))
        bot.send_message(message.chat.id, "مرحله 3 از 3\n\n📁 دسته‌بندی:", reply_markup=markup)
        return True

    if current == "admin_tutorial_title":
        data["title"] = message.text.strip()
        set_state(user_id, "admin_tutorial_content", data)
        bot.send_message(message.chat.id, "مرحله 2 از 4\n\n✍️ محتوا یا <code>-</code>:")
        return True

    if current == "admin_tutorial_content":
        content = message.text.strip()
        data["content"] = "" if content == "-" else content
        set_state(user_id, "admin_tutorial_link", data)
        bot.send_message(message.chat.id, "مرحله 3 از 4\n\n🔗 لینک یا <code>-</code>:")
        return True

    if current == "admin_tutorial_link":
        link = message.text.strip()
        data["link"] = "" if link == "-" else link
        set_state(user_id, "admin_tutorial_category", data)
        markup = types.InlineKeyboardMarkup(row_width=2)
        for cat in TUTORIAL_CATEGORIES:
            markup.add(types.InlineKeyboardButton(
                f"📁 {cat}", callback_data=f"admin_cat_tutorial_{cat}"
            ))
        bot.send_message(message.chat.id, "مرحله 4 از 4\n\n📁 دسته‌بندی:", reply_markup=markup)
        return True

    if current in ("admin_set_group", "admin_set_channel", "admin_set_youtube"):
        link = message.text.strip()
        key_map = {
            "admin_set_group": "group_link",
            "admin_set_channel": "channel_link",
            "admin_set_youtube": "youtube_link",
        }
        set_setting(key_map[current], link)
        clear_state(user_id)
        bot.send_message(message.chat.id, f"✅ لینک ذخیره شد:\n<code>{link}</code>",
                         disable_web_page_preview=True)
        show_admin_panel(message.chat.id)
        return True

    return True


# ---------- پست منو به کانال ----------

def build_channel_menu():
    """دکمه‌های شیشه‌ای برای پست کانال."""
    channel_link = get_setting("channel_link", DEFAULT_CHANNEL)
    group_link   = get_setting("group_link",   DEFAULT_GROUP)
    youtube_link = get_setting("youtube_link", DEFAULT_YOUTUBE)
    bot_username = bot.get_me().username
    bot_url = f"https://t.me/{bot_username}"

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 تحلیل‌ها", url=f"{bot_url}?start=analyses"),
        types.InlineKeyboardButton("🎓 آموزش‌ها", url=f"{bot_url}?start=tutorials")
    )
    markup.add(
        types.InlineKeyboardButton("📝 درخواست تحلیل", url=f"{bot_url}?start=req"),
        types.InlineKeyboardButton("💬 ارتباط با استاد", url=f"{bot_url}?start=contact")
    )
    if channel_link:
        markup.add(types.InlineKeyboardButton("📢 کانال", url=channel_link))
    if group_link:
        markup.add(types.InlineKeyboardButton("👥 گروه", url=group_link))
    if youtube_link:
        markup.add(types.InlineKeyboardButton("▶️ YouTube", url=youtube_link))
    return markup


def post_channel_menu(chat_id):
    try:
        me = bot.get_me()
        msg = bot.send_message(
            CHANNEL_ID,
            "📌 <b>منوی TradScale</b>\n\n"
            "برای دسترسی سریع، روی دکمه‌های زیر بزن:\n"
            f"👉 @{me.username}",
            reply_markup=build_channel_menu(),
            disable_web_page_preview=True
        )
        bot.send_message(
            chat_id,
            f"✅ پست به کانال ارسال شد.\n"
            f"Message ID: <code>{msg.message_id}</code>\n\n"
            "برای پین کردن، روی «📌 پین منو در کانال» بزن."
        )
    except Exception as e:
        print("Channel post error:", e)
        bot.send_message(
            chat_id,
            f"❌ خطا در ارسال به کانال:\n<code>{e}</code>\n\n"
            "مطمئن شو:\n"
            "1. ربات ادمین کانال است\n"
            "2. `CHANNEL_ID` در Environment تنظیم شده\n"
            "3. دسترسی Post Messages دارد"
        )


def pin_channel_menu(chat_id):
    try:
        msg = bot.send_message(
            CHANNEL_ID,
            "📌 <b>منوی TradScale (Pin)</b>\n\n"
            "برای دسترسی سریع روی دکمه‌های زیر بزن:",
            reply_markup=build_channel_menu(),
            disable_web_page_preview=True
        )
        bot.pin_chat_message(
            chat_id=CHANNEL_ID,
            message_id=msg.message_id,
            disable_notification=True
        )
        bot.send_message(chat_id, "✅ منو پین شد.")
    except Exception as e:
        print("Pin error:", e)
        bot.send_message(chat_id, f"❌ خطا در پین:\n<code>{e}</code>")


# ============================================================
#                          START
# ============================================================

@bot.message_handler(commands=["start"])
def start_command(message):
    ensure_user(message)
    clear_state(message.from_user.id)

    # هندل پارامترهای deep-link
    args = message.text.split()
    if len(args) > 1:
        payload = args[1].strip()
        if payload == "analyses":
            show_analyses_menu(message.chat.id)
            return
        if payload == "tutorials":
            show_tutorials_menu(message.chat.id)
            return
        if payload == "req":
            start_request_analysis(message)
            return
        if payload == "contact":
            start_contact_admin(message)
            return

    welcome = (
        "🚀 <b>Welcome to TradScale</b>\n\n"
        "8+ Years in Forex & Crypto\n"
        "🧠 NeoWave × Smart Money\n"
        "🏆 Real Prop Trading Experience\n"
        "💰 Risk Management | Psychology | Execution\n\n"
        "<b>Learn • Trade • Scale</b>\n\n"
        "از اینجا می‌تونی ابزارهای معاملاتی TradScale رو استفاده کنی."
    )

    channel_link = get_setting("channel_link", DEFAULT_CHANNEL)
    group_link   = get_setting("group_link",   DEFAULT_GROUP)
    youtube_link = get_setting("youtube_link", DEFAULT_YOUTUBE)

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("📊 تحلیل‌ها", callback_data="menu_analyses"),
        types.InlineKeyboardButton("🎓 آموزش‌ها", callback_data="menu_tutorials")
    )
    markup.add(
        types.InlineKeyboardButton("📝 درخواست تحلیل", callback_data="menu_request_analysis"),
        types.InlineKeyboardButton("💬 ارتباط با استاد", callback_data="contact_admin")
    )
    if channel_link:
        markup.add(types.InlineKeyboardButton("📢 کانال تلگرام", url=channel_link))
    if group_link:
        markup.add(types.InlineKeyboardButton("👥 گروه تلگرام", url=group_link))
    if youtube_link:
        markup.add(types.InlineKeyboardButton("▶️ YouTube", url=youtube_link))
    markup.add(types.InlineKeyboardButton("🏠 منوی کامل", callback_data="main_menu"))

    bot.send_message(message.chat.id, welcome, reply_markup=markup,
                     disable_web_page_preview=True)


@bot.message_handler(commands=["menu"])
def menu_command(message):
    ensure_user(message)
    clear_state(message.from_user.id)
    show_main_menu(message.chat.id)


# ============================================================
#                      CALLBACK ROUTER
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
def callback_router(call):
    user_id = call.from_user.id
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    data = call.data
    chat_id = call.message.chat.id
    fake = call_to_message(call)  # ⭐ پیام جعلی از طرف کاربر

    if data == "main_menu":
        clear_state(user_id)
        show_main_menu(chat_id)
    elif data == "menu_tools":
        show_tools_menu(chat_id)
    elif data == "menu_analyses":
        show_analyses_menu(chat_id)
    elif data == "menu_tutorials":
        show_tutorials_menu(chat_id)
    elif data == "menu_links":
        show_links(chat_id)
    elif data == "menu_request_analysis":
        start_request_analysis(fake)

    elif data.startswith("analysis_cat_"):
        show_analyses_by_category(chat_id, data.replace("analysis_cat_", ""))
    elif data.startswith("analysis_noop_"):
        bot.send_message(chat_id, "این تحلیل فعلاً لینک ندارد.")

    elif data.startswith("tutorial_cat_"):
        show_tutorials_by_category(chat_id, data.replace("tutorial_cat_", ""))
    elif data.startswith("tutorial_view_"):
        show_tutorial_detail(chat_id, int(data.split("_")[-1]))

    elif data == "admin_panel":
        show_admin_panel(chat_id)
    elif data == "admin_add_analysis":
        admin_add_analysis_start(chat_id)
    elif data == "admin_add_tutorial":
        admin_add_tutorial_start(chat_id)
    elif data == "admin_list_analyses":
        admin_list_analyses(chat_id)
    elif data == "admin_list_tutorials":
        admin_list_tutorials(chat_id)
    elif data == "admin_set_group":
        set_state(user_id, "admin_set_group")
        bot.send_message(chat_id, "👥 لینک جدید گروه را بفرست:")
    elif data == "admin_set_channel":
        set_state(user_id, "admin_set_channel")
        bot.send_message(chat_id, "📢 لینک جدید کانال را بفرست:")
    elif data == "admin_set_youtube":
        set_state(user_id, "admin_set_youtube")
        bot.send_message(chat_id, "▶️ لینک جدید یوتیوب را بفرست:")
    elif data == "admin_post_channel":
        post_channel_menu(chat_id)
    elif data == "admin_pin_channel":
        pin_channel_menu(chat_id)

    elif data.startswith("admin_cat_analysis_"):
        category = data.replace("admin_cat_analysis_", "")
        state = get_state(user_id)
        if state.get("state") != "admin_analysis_category":
            return
        d = state["data"]
        with db_lock:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO analyses (name, link, category, created_at)
                VALUES (?, ?, ?, ?)
            """, (d["name"], d.get("link", ""), category, now()))
            conn.commit()
            conn.close()
        clear_state(user_id)
        bot.send_message(chat_id, f"✅ تحلیل «{d['name']}» در «{category}» ثبت شد.")
        show_admin_panel(chat_id)

    elif data.startswith("admin_cat_tutorial_"):
        category = data.replace("admin_cat_tutorial_", "")
        state = get_state(user_id)
        if state.get("state") != "admin_tutorial_category":
            return
        d = state["data"]
        with db_lock:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO tutorials (title, content, link, category, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (d["title"], d.get("content", ""), d.get("link", ""), category, now()))
            conn.commit()
            conn.close()
        clear_state(user_id)
        bot.send_message(chat_id, f"✅ آموزش «{d['title']}» در «{category}» ثبت شد.")
        show_admin_panel(chat_id)

    elif data.startswith("admin_del_analysis_"):
        aid = int(data.split("_")[-1])
        with db_lock:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("DELETE FROM analyses WHERE id = ?", (aid,))
            conn.commit()
            conn.close()
        bot.send_message(chat_id, f"🗑 تحلیل #{aid} حذف شد.")
        admin_list_analyses(chat_id)

    elif data.startswith("admin_del_tutorial_"):
        tid = int(data.split("_")[-1])
        with db_lock:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("DELETE FROM tutorials WHERE id = ?", (tid,))
            conn.commit()
            conn.close()
        bot.send_message(chat_id, f"🗑 آموزش #{tid} حذف شد.")
        admin_list_tutorials(chat_id)

    # ---------- TOOLS ----------
    elif data == "menu_risk":
        start_risk_calculator(fake)
    elif data == "menu_prop":
        show_prop_menu(chat_id)
    elif data == "menu_journal":
        show_journal_menu(chat_id)
    elif data == "menu_dashboard":
        show_dashboard(chat_id)
    elif data == "menu_psychology":
        start_psychology(fake)
    elif data == "menu_academy":
        show_academy(chat_id)
    elif data == "menu_content":
        show_content_menu(chat_id)
    elif data == "menu_ai":
        start_ai_assistant(fake)
    elif data == "contact_admin":
        start_contact_admin(fake)
    elif data == "menu_settings":
        show_settings(chat_id)

    # ---------- INNER ----------
    elif data == "risk_again":
        start_risk_calculator(fake)
    elif data == "psych_again":
        start_psychology(fake)
    elif data == "ai_new":
        start_ai_assistant(fake)
    elif data == "prop_new":
        start_prop_creation(fake)
    elif data == "prop_list":
        show_prop_accounts(chat_id)
    elif data.startswith("prop_view_"):
        show_prop_details(chat_id, int(data.split("_")[-1]))
    elif data.startswith("prop_delete_"):
        delete_prop(chat_id, int(data.split("_")[-1]))
    elif data == "journal_new":
        start_journal(fake)
    elif data == "journal_open":
        show_open_trades(chat_id)
    elif data == "journal_history":
        show_trade_history(chat_id)
    elif data.startswith("close_trade_"):
        start_close_trade(fake, int(data.split("_")[-1]))
    elif data == "dashboard_refresh":
        show_dashboard(chat_id)
    elif data.startswith("academy_"):
        academy_topic(chat_id, data.replace("academy_", ""))
    elif data.startswith("content_"):
        content_topic(chat_id, data.replace("content_", ""))
    elif data == "settings":
        show_settings(chat_id)


# ============================================================
#                    RISK CALCULATOR
# ============================================================

def start_risk_calculator(message):
    clear_state(message.from_user.id)
    set_state(message.from_user.id, "risk_account_size")
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="main_menu"))
    bot.send_message(message.chat.id,
        "📊 <b>Risk Calculator</b>\n\nمرحله 1 از 6\n\n💰 موجودی حساب:\n<code>10000</code>",
        reply_markup=markup)


def risk_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    if not state.get("state", "").startswith("risk_"):
        return False
    if is_cancel(message.text):
        cancel_message(message)
        return True
    current = state["state"]
    data = state["data"]

    if current == "risk_account_size":
        v = safe_float(message.text)
        if v is None or v <= 0:
            bot.reply_to(message, "❌ عدد نامعتبر است.")
            return True
        data["account_size"] = v
        set_state(user_id, "risk_percent", data)
        bot.send_message(message.chat.id, "مرحله 2 از 6\n\n📉 درصد ریسک:")
        return True
    if current == "risk_percent":
        v = safe_float(message.text)
        if v is None or v <= 0 or v > 100:
            bot.reply_to(message, "❌ درصد نامعتبر.")
            return True
        data["risk_percent"] = v
        set_state(user_id, "risk_entry", data)
        bot.send_message(message.chat.id, "مرحله 3 از 6\n\n🎯 Entry:")
        return True
    if current == "risk_entry":
        v = safe_float(message.text)
        if v is None or v <= 0:
            bot.reply_to(message, "❌ Entry نامعتبر.")
            return True
        data["entry"] = v
        set_state(user_id, "risk_sl", data)
        bot.send_message(message.chat.id, "مرحله 4 از 6\n\n🛑 Stop Loss:")
        return True
    if current == "risk_sl":
        v = safe_float(message.text)
        if v is None or v <= 0:
            bot.reply_to(message, "❌ SL نامعتبر.")
            return True
        data["sl"] = v
        set_state(user_id, "risk_tp", data)
        bot.send_message(message.chat.id, "مرحله 5 از 6\n\n🎯 Take Profit:")
        return True
    if current == "risk_tp":
        v = safe_float(message.text)
        if v is None or v <= 0:
            bot.reply_to(message, "❌ TP نامعتبر.")
            return True
        data["tp"] = v
        set_state(user_id, "risk_direction", data)
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🟢 LONG", callback_data="risk_long"),
            types.InlineKeyboardButton("🔴 SHORT", callback_data="risk_short")
        )
        bot.send_message(message.chat.id, "مرحله 6 از 6\n\nنوع معامله:", reply_markup=markup)
        return True
    return True


@bot.callback_query_handler(func=lambda call: call.data in ["risk_long", "risk_short"])
def risk_direction_callback(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    user_id = call.from_user.id
    state = get_state(user_id)
    if state.get("state") != "risk_direction":
        return
    d = state["data"]
    direction = "LONG" if call.data == "risk_long" else "SHORT"
    account, risk_pct = d["account_size"], d["risk_percent"]
    entry, sl, tp = d["entry"], d["sl"], d["tp"]
    risk_amount = account * risk_pct / 100
    if direction == "LONG":
        stop_dist = entry - sl; target_dist = tp - entry
    else:
        stop_dist = sl - entry; target_dist = entry - tp
    if stop_dist <= 0:
        bot.send_message(call.message.chat.id, "❌ SL در سمت اشتباه است.")
        clear_state(user_id); return
    if target_dist <= 0:
        bot.send_message(call.message.chat.id, "❌ TP در سمت اشتباه است.")
        clear_state(user_id); return
    pos_size = risk_amount / stop_dist
    potential_profit = pos_size * target_dist
    rr = target_dist / stop_dist
    result = (
        "📊 <b>TradScale Risk Calculator</b>\n\n"
        "━━━━━━━━━━━━━━\n"
        f"💰 Account: <b>{fmt_number(account)}</b>\n"
        f"⚠️ Risk: <b>{fmt_number(risk_pct)}%</b>\n"
        f"💵 Risk Amount: <b>{fmt_number(risk_amount)}</b>\n\n"
        f"📍 Entry: <b>{fmt_number(entry)}</b>\n"
        f"🛑 SL: <b>{fmt_number(sl)}</b>\n"
        f"🎯 TP: <b>{fmt_number(tp)}</b>\n\n"
        f"📦 Position Size: <b>{fmt_number(pos_size, 6)}</b>\n\n"
        f"⚖️ R:R: <b>1 : {fmt_number(rr, 2)}</b>\n"
        f"💵 Potential Profit: <b>{fmt_number(potential_profit)}</b>\n"
        "━━━━━━━━━━━━━━"
    )
    bot.send_message(call.message.chat.id, result)
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🔄 محاسبه جدید", callback_data="risk_again"),
        types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
    )
    bot.send_message(call.message.chat.id, "ادامه:", reply_markup=markup)
    add_xp(user_id, 5)
    clear_state(user_id)


# ============================================================
#                    PROP MANAGER
# ============================================================

def show_prop_menu(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Challenge جدید", callback_data="prop_new"))
    markup.add(types.InlineKeyboardButton("📋 Challengeهای من", callback_data="prop_list"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu"))
    bot.send_message(chat_id, "🏆 <b>Prop Challenge Manager</b>", reply_markup=markup)


def start_prop_creation(message):
    set_state(message.from_user.id, "prop_name")
    bot.send_message(message.chat.id,
        "🏆 مرحله 1 از 6\n\nنام Challenge:\n<code>Prop Firm - 100K</code>")


def prop_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    if not state.get("state", "").startswith("prop_"):
        return False
    if is_cancel(message.text):
        cancel_message(message); return True
    current = state["state"]; data = state["data"]

    if current == "prop_name":
        data["name"] = message.text.strip()
        set_state(user_id, "prop_account", data)
        bot.send_message(message.chat.id, "مرحله 2 از 6\n\n💰 Account Size:")
        return True
    if current == "prop_account":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["account_size"] = v
        set_state(user_id, "prop_target", data)
        bot.send_message(message.chat.id, "مرحله 3 از 6\n\n🎯 Profit Target %:")
        return True
    if current == "prop_target":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["target"] = v
        set_state(user_id, "prop_max_dd", data)
        bot.send_message(message.chat.id, "مرحله 4 از 6\n\n📉 Max Drawdown %:")
        return True
    if current == "prop_max_dd":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["max_dd"] = v
        set_state(user_id, "prop_daily_dd", data)
        bot.send_message(message.chat.id, "مرحله 5 از 6\n\n📉 Daily Drawdown %:")
        return True
    if current == "prop_daily_dd":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["daily_dd"] = v
        set_state(user_id, "prop_balance", data)
        bot.send_message(message.chat.id, "مرحله 6 از 6\n\n💵 موجودی فعلی:")
        return True
    if current == "prop_balance":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["balance"] = v
        with db_lock:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO prop_accounts (user_id, name, account_size,
                    profit_target_percent, max_drawdown_percent,
                    daily_drawdown_percent, starting_balance, current_balance,
                    created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, data["name"], data["account_size"], data["target"],
                  data["max_dd"], data["daily_dd"], data["account_size"],
                  data["balance"], now(), now()))
            prop_id = cur.lastrowid
            conn.commit(); conn.close()
        clear_state(user_id); add_xp(user_id, 15)
        bot.send_message(message.chat.id, "✅ Challenge ثبت شد.")
        show_prop_details(message.chat.id, prop_id)
        return True
    return True


def show_prop_accounts(chat_id):
    with db_lock:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM prop_accounts WHERE user_id = ? ORDER BY id DESC", (chat_id,))
        rows = cur.fetchall(); conn.close()
    if not rows:
        bot.send_message(chat_id, "هنوز Challengeای ثبت نکردی."); return
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup.add(types.InlineKeyboardButton(f"🏆 {row['name']}", callback_data=f"prop_view_{row['id']}"))
    markup.add(types.InlineKeyboardButton("➕ Challenge جدید", callback_data="prop_new"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu"))
    bot.send_message(chat_id, "📋 <b>Challengeهای تو:</b>", reply_markup=markup)


def show_prop_details(chat_id, prop_id):
    with db_lock:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM prop_accounts WHERE id = ? AND user_id = ?", (prop_id, chat_id))
        row = cur.fetchone(); conn.close()
    if not row:
        bot.send_message(chat_id, "❌ پیدا نشد."); return
    starting = float(row["starting_balance"]); balance = float(row["current_balance"])
    target_pct = float(row["profit_target_percent"])
    max_dd = float(row["max_drawdown_percent"]); daily_dd = float(row["daily_drawdown_percent"])
    pnl = balance - starting; pnl_pct = (pnl / starting) * 100
    target_balance = starting + (starting * target_pct / 100)
    remaining = max(0, target_balance - balance)
    if pnl_pct >= target_pct: status = "🎉 TARGET REACHED"
    elif pnl_pct <= -max_dd: status = "🛑 MAX DRAWDOWN BREACHED"
    else: status = "🟢 ACTIVE"
    text = (
        f"🏆 <b>{row['name']}</b>\n\nStatus: <b>{status}</b>\n\n"
        "━━━━━━━━━━━━━━\n"
        f"💰 Starting: <b>{fmt_number(starting)}</b>\n"
        f"💵 Current: <b>{fmt_number(balance)}</b>\n"
        f"📈 P/L: <b>{fmt_number(pnl)}</b> (<b>{fmt_number(pnl_pct)}%</b>)\n"
        f"🎯 Target Balance: <b>{fmt_number(target_balance)}</b>\n"
        f"⏳ Remaining: <b>{fmt_number(remaining)}</b>\n"
        f"📉 Max DD: <b>{fmt_number(max_dd)}%</b>\n"
        f"📉 Daily DD: <b>{fmt_number(daily_dd)}%</b>\n"
        "━━━━━━━━━━━━━━"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🗑 حذف", callback_data=f"prop_delete_{prop_id}"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu"))
    bot.send_message(chat_id, text, reply_markup=markup)


def delete_prop(chat_id, prop_id):
    with db_lock:
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM prop_accounts WHERE id = ? AND user_id = ?", (prop_id, chat_id))
        conn.commit(); conn.close()
    bot.send_message(chat_id, "🗑 حذف شد.")
    show_prop_menu(chat_id)


# ============================================================
#                    TRADING JOURNAL
# ============================================================

def show_journal_menu(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ ثبت معامله جدید", callback_data="journal_new"))
    markup.add(
        types.InlineKeyboardButton("🟡 معاملات باز", callback_data="journal_open"),
        types.InlineKeyboardButton("📚 تاریخچه", callback_data="journal_history")
    )
    markup.add(types.InlineKeyboardButton("📈 Dashboard", callback_data="menu_dashboard"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu"))
    bot.send_message(chat_id, "📔 <b>Trading Journal</b>", reply_markup=markup)


def start_journal(message):
    set_state(message.from_user.id, "journal_symbol")
    bot.send_message(message.chat.id,
        "📔 مرحله 1 از 10\n\nنماد:\n<code>XAUUSD</code>")


def journal_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    if not state.get("state", "").startswith("journal_"):
        return False
    if is_cancel(message.text):
        cancel_message(message); return True
    current = state["state"]; data = state["data"]

    if current == "journal_symbol":
        data["symbol"] = message.text.strip().upper()
        set_state(user_id, "journal_direction", data)
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🟢 LONG", callback_data="journal_long"),
            types.InlineKeyboardButton("🔴 SHORT", callback_data="journal_short")
        )
        bot.send_message(message.chat.id, "مرحله 2 از 10\n\nنوع:", reply_markup=markup)
        return True
    if current == "journal_account":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["account"] = v; set_state(user_id, "journal_risk", data)
        bot.send_message(message.chat.id, "مرحله 4 از 10\n\n⚠️ درصد ریسک:")
        return True
    if current == "journal_risk":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["risk"] = v; set_state(user_id, "journal_entry", data)
        bot.send_message(message.chat.id, "مرحله 5 از 10\n\n📍 Entry:")
        return True
    if current == "journal_entry":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["entry"] = v; set_state(user_id, "journal_sl", data)
        bot.send_message(message.chat.id, "مرحله 6 از 10\n\n🛑 SL:")
        return True
    if current == "journal_sl":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["sl"] = v; set_state(user_id, "journal_tp", data)
        bot.send_message(message.chat.id, "مرحله 7 از 10\n\n🎯 TP:")
        return True
    if current == "journal_tp":
        v = safe_float(message.text)
        if v is None or v <= 0: bot.reply_to(message, "❌ نامعتبر."); return True
        data["tp"] = v; set_state(user_id, "journal_setup", data)
        bot.send_message(message.chat.id, "مرحله 8 از 10\n\n🧠 Setup:")
        return True
    if current == "journal_setup":
        data["setup"] = message.text.strip()
        set_state(user_id, "journal_reason", data)
        bot.send_message(message.chat.id, "مرحله 9 از 10\n\n✍️ دلیل ورود:")
        return True
    if current == "journal_reason":
        data["reason"] = message.text.strip()
        set_state(user_id, "journal_emotion", data)
        bot.send_message(message.chat.id, "مرحله 10 از 10\n\n🧠 Emotion:")
        return True
    if current == "journal_emotion":
        data["emotion"] = message.text.strip()
        account = data["account"]; risk = data["risk"]
        entry = data["entry"]; sl = data["sl"]; tp = data["tp"]
        direction = data["direction"]
        risk_amount = account * risk / 100
        if direction == "LONG":
            stop_dist = entry - sl; target_dist = tp - entry
        else:
            stop_dist = sl - entry; target_dist = entry - tp
        rr = 0
        if stop_dist > 0 and target_dist > 0:
            rr = target_dist / stop_dist
        pos_size = risk_amount / stop_dist if stop_dist > 0 else 0
        with db_lock:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO trades (user_id, symbol, direction, account_size,
                    risk_percent, entry, stop_loss, take_profit, position_size,
                    risk_amount, potential_profit, rr, setup, reason,
                    emotion_before, result, pnl, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 0, ?)
            """, (user_id, data["symbol"], direction, account, risk, entry, sl,
                  tp, pos_size, risk_amount, risk_amount * rr, rr,
                  data["setup"], data["reason"], data["emotion"], now()))
            trade_id = cur.lastrowid
            conn.commit(); conn.close()
        add_xp(user_id, 20); clear_state(user_id)
        bot.send_message(message.chat.id,
            f"✅ Trade #{trade_id} ثبت شد.\n\n"
            f"📌 {data['symbol']} — {direction}\n"
            f"Risk: {fmt_number(risk)}% | R:R 1:{fmt_number(rr)}")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📸 Screenshot", callback_data=f"add_screenshot_{trade_id}"))
        markup.add(types.InlineKeyboardButton("🔴 بستن", callback_data=f"close_trade_{trade_id}"))
        markup.add(types.InlineKeyboardButton("📔 Journal", callback_data="menu_journal"))
        bot.send_message(message.chat.id, "ادامه:", reply_markup=markup)
        return True
    return True


@bot.callback_query_handler(func=lambda call: call.data in ["journal_long", "journal_short"])
def journal_direction_callback(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    user_id = call.from_user.id
    state = get_state(user_id)
    if state.get("state") != "journal_direction":
        return
    data = state["data"]
    data["direction"] = "LONG" if call.data == "journal_long" else "SHORT"
    set_state(user_id, "journal_account", data)
    bot.send_message(call.message.chat.id, "مرحله 3 از 10\n\n💰 Account Size:")


def show_open_trades(chat_id):
    with db_lock:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM trades WHERE user_id=? AND result='OPEN' ORDER BY id DESC", (chat_id,))
        rows = cur.fetchall(); conn.close()
    if not rows:
        bot.send_message(chat_id, "🟡 هیچ معامله بازی نداری."); return
    for row in rows:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔴 Close", callback_data=f"close_trade_{row['id']}"))
        bot.send_message(chat_id,
            f"🟡 <b>Open Trade #{row['id']}</b>\n\n"
            f"{row['symbol']} — {row['direction']}\n"
            f"Entry: {fmt_number(row['entry'])}\n"
            f"SL: {fmt_number(row['stop_loss'])}\n"
            f"TP: {fmt_number(row['take_profit'])}\n"
            f"Risk: {fmt_number(row['risk_percent'])}%\n"
            f"R:R 1:{fmt_number(row['rr'])}",
            reply_markup=markup)


def show_trade_history(chat_id):
    with db_lock:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM trades WHERE user_id=? ORDER BY id DESC LIMIT 20", (chat_id,))
        rows = cur.fetchall(); conn.close()
    if not rows:
        bot.send_message(chat_id, "📚 هیچ معامله‌ای ثبت نکردی."); return
    for row in rows:
        icon = {"WIN": "🟢", "LOSS": "🔴", "BE": "🟡"}.get(row["result"], "⚪")
        bot.send_message(chat_id,
            f"{icon} <b>Trade #{row['id']}</b>\n\n"
            f"{row['symbol']} — {row['direction']}\n"
            f"Result: <b>{row['result']}</b>\n"
            f"P/L: <b>{fmt_number(row['pnl'])}</b>\n"
            f"R:R 1:{fmt_number(row['rr'])}")


def start_close_trade(message, trade_id):
    with db_lock:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM trades WHERE id=? AND user_id=?", (trade_id, message.from_user.id))
        row = cur.fetchone(); conn.close()
    if not row:
        bot.send_message(message.chat.id, "❌ Trade پیدا نشد."); return
    set_state(message.from_user.id, "close_trade_result", {"trade_id": trade_id})
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🟢 WIN", callback_data=f"result_win_{trade_id}"),
        types.InlineKeyboardButton("🔴 LOSS", callback_data=f"result_loss_{trade_id}"),
        types.InlineKeyboardButton("🟡 BE", callback_data=f"result_be_{trade_id}")
    )
    bot.send_message(message.chat.id, f"Trade #{trade_id}\n\nنتیجه:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("result_"))
def result_callback(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    parts = call.data.split("_")
    result = parts[1].upper()
    trade_id = int(parts[2])
    set_state(call.from_user.id, "close_trade_pnl",
              {"trade_id": trade_id, "result": result})
    bot.send_message(call.message.chat.id, "💵 P/L (منفی برای ضرر):")


def close_trade_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    if state.get("state") != "close_trade_pnl":
        return False
    if is_cancel(message.text):
        cancel_message(message); return True
    pnl = safe_float(message.text)
    if pnl is None:
        bot.reply_to(message, "❌ نامعتبر."); return True
    trade_id = state["data"]["trade_id"]; result = state["data"]["result"]
    with db_lock:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE trades SET result=?, pnl=?, closed_at=? WHERE id=? AND user_id=?",
                    (result, pnl, now(), trade_id, user_id))
        conn.commit(); conn.close()
    add_xp(user_id, 15); clear_state(user_id)
    bot.send_message(message.chat.id,
        f"✅ Trade #{trade_id} بسته شد.\nResult: <b>{result}</b>\nP/L: <b>{fmt_number(pnl)}</b>")
    show_dashboard(message.chat.id)
    return True


def show_dashboard(chat_id):
    with db_lock:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM trades WHERE user_id=?", (chat_id,))
        rows = cur.fetchall(); conn.close()
    profile = get_user_profile(chat_id)
    total = len(rows)
    closed = [r for r in rows if r["result"] in ("WIN", "LOSS", "BE")]
    wins = len([r for r in closed if r["result"] == "WIN"])
    losses = len([r for r in closed if r["result"] == "LOSS"])
    bes = len([r for r in closed if r["result"] == "BE"])
    pnl = sum(float(r["pnl"] or 0) for r in closed)
    win_rate = (wins / len(closed) * 100) if closed else 0
    avg_rr = (sum(float(r["rr"] or 0) for r in closed) / len(closed)) if closed else 0
    text = (
        "📈 <b>TradScale Dashboard</b>\n\n"
        "━━━━━━━━━━━━━━\n"
        f"👤 Level: <b>{profile['level'] if profile else 'Beginner'}</b>\n"
        f"🏅 XP: <b>{profile['xp'] if profile else 0}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📔 Total: <b>{total}</b> | 🟢 <b>{wins}</b> | 🔴 <b>{losses}</b> | 🟡 <b>{bes}</b>\n"
        f"📊 Win Rate: <b>{fmt_number(win_rate)}%</b>\n"
        f"⚖️ Avg R:R: <b>1 : {fmt_number(avg_rr)}</b>\n"
        f"💰 Total P/L: <b>{fmt_number(pnl)}</b>\n"
        "━━━━━━━━━━━━━━"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("📔 Journal", callback_data="menu_journal"),
        types.InlineKeyboardButton("🔄 Refresh", callback_data="dashboard_refresh")
    )
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
    bot.send_message(chat_id, text, reply_markup=markup)


# ============================================================
#                    PSYCHOLOGY
# ============================================================

def start_psychology(message):
    set_state(message.from_user.id, "psych_q1", {})
    bot.send_message(message.chat.id,
        "🧠 <b>Psychology Check</b>\n\nسؤال 1:\n\n"
        "چرا می‌خواهی وارد معامله شوی؟\n\n"
        "1️⃣ Setup\n2️⃣ FOMO\n3️⃣ Revenge\n4️⃣ جبران\n5️⃣ مطمئن نیستم\n\nفقط شماره.")


def psychology_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    if not state.get("state", "").startswith("psych_"):
        return False
    if is_cancel(message.text):
        cancel_message(message); return True
    current = state["state"]; data = state["data"]
    if current == "psych_q1":
        data["q1"] = message.text.strip()
        set_state(user_id, "psych_q2", data)
        bot.send_message(message.chat.id, "سؤال 2:\n\nEntry/SL/Risk مشخص؟\n1️⃣ بله\n2️⃣ خیر")
        return True
    if current == "psych_q2":
        data["q2"] = message.text.strip()
        set_state(user_id, "psych_q3", data)
        bot.send_message(message.chat.id, "سؤال 3:\n\nآماده Loss هستی؟\n1️⃣ بله\n2️⃣ خیر")
        return True
    if current == "psych_q3":
        data["q3"] = message.text.strip()
        set_state(user_id, "psych_q4", data)
        bot.send_message(message.chat.id, "سؤال 4:\n\nطبق سیستم است؟\n1️⃣ بله\n2️⃣ خیر")
        return True
    if current == "psych_q4":
        data["q4"] = message.text.strip()
        score = sum(25 for k in ["q1","q2","q3","q4"] if data[k] == "1")
        verdict = "🟢 READY" if score >= 90 else "🟡 CAUTION" if score >= 70 else "🔴 NOT READY"
        clear_state(user_id)
        bot.send_message(message.chat.id, f"🧠 Score: <b>{score}/100</b>\nStatus: <b>{verdict}</b>")
        add_xp(user_id, 10)
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔄 مجدد", callback_data="psych_again"),
            types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu"))
        bot.send_message(message.chat.id, "ادامه:", reply_markup=markup)
        return True
    return True


# ============================================================
#                    ACADEMY / CONTENT
# ============================================================

def show_academy(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for title, key in [
        ("📊 Technical", "technical"), ("🌊 NeoWave", "neowave"),
        ("🧠 Smart Money", "smartmoney"), ("💰 Risk Mgmt", "risk"),
        ("🧠 Psychology", "psychology"), ("🏆 Prop", "prop"),
        ("📔 Journal", "journal"), ("⚙️ System", "system"),
    ]:
        markup.add(types.InlineKeyboardButton(title, callback_data=f"academy_{key}"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
    bot.send_message(chat_id, "🎓 <b>Academy</b>", reply_markup=markup)


def academy_topic(chat_id, topic):
    lessons = {
        "technical": "📊 Technical Analysis\n\n• Market Structure\n• Support/Resistance\n• Trend\n• Price Action\n• Liquidity",
        "neowave": "🌊 NeoWave\n\n• Wave Structure\n• Corrective Patterns\n• Elliott Wave\n• NeoWave Rules",
        "smartmoney": "🧠 Smart Money\n\n• Liquidity\n• BOS\n• CHoCH\n• Order Blocks\n• Imbalances",
        "risk": "💰 Risk Management\n\n• Risk per Trade\n• Position Sizing\n• R:R\n• Drawdown",
        "psychology": "🧠 Psychology\n\n• FOMO\n• Revenge Trading\n• Overtrading\n• Discipline",
        "prop": "🏆 Prop Trading\n\n• Challenge Rules\n• Daily DD\n• Max DD\n• Profit Target",
        "journal": "📔 Journal\n\n• کجا خوب عمل کردی\n• کدام Setup جواب می‌دهد",
        "system": "⚙️ System\n\n1. زمان تحلیل\n2. Setup\n3. Entry\n4. SL\n5. Risk\n6. Exit",
    }
    bot.send_message(chat_id, lessons.get(topic, "در حال آماده‌سازی..."))


def show_content_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Analysis", callback_data="content_analysis"),
        types.InlineKeyboardButton("🎓 Education", callback_data="content_education"))
    markup.add(
        types.InlineKeyboardButton("🏆 Prop", callback_data="content_prop"),
        types.InlineKeyboardButton("🧠 Psychology", callback_data="content_psychology"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
    bot.send_message(chat_id, "📚 <b>Content Hub</b>", reply_markup=markup)


def content_topic(chat_id, topic):
    texts = {
        "analysis": "📊 Analysis — در کانال منتشر می‌شود.",
        "education": "🎓 Education — آموزش‌های TradScale.",
        "prop": "🏆 Prop — تجربه‌های Prop.",
        "psychology": "🧠 Psychology — ذهنیت معاملاتی.",
    }
    bot.send_message(chat_id, texts.get(topic, "در حال آماده‌سازی..."))


# ============================================================
#                    AI ASSISTANT
# ============================================================

def start_ai_assistant(message):
    if not OPENAI_API_KEY:
        bot.send_message(message.chat.id,
            "🤖 AI غیرفعال است.\n\n<code>OPENAI_API_KEY</code> را تنظیم کن.")
        return
    set_state(message.from_user.id, "ai_question")
    bot.send_message(message.chat.id, "🤖 <b>TradScale AI</b>\n\nسؤالت را بفرست.")


def ask_openai(question):
    if not OPENAI_API_KEY:
        return None
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": "You are TradScale AI Assistant. Educational only. Do NOT give BUY/SELL signals."},
            {"role": "user", "content": question}
        ],
        "max_output_tokens": 800
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {OPENAI_API_KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            result = json.loads(r.read().decode("utf-8"))
        if "output_text" in result:
            return result["output_text"]
        texts = []
        for item in result.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
        return "\n".join(texts) if texts else "پاسخی برنگشت."
    except Exception as e:
        print("OpenAI Error:", e)
        return "❌ خطا در ارتباط با AI."


def ai_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    if state.get("state") != "ai_question":
        return False
    if is_cancel(message.text):
        cancel_message(message); return True
    bot.send_message(message.chat.id, "🤖 در حال بررسی...")
    answer = ask_openai(message.text.strip())
    if answer:
        bot.send_message(message.chat.id, "🤖 <b>TradScale AI</b>\n\n" + answer)
    clear_state(user_id); add_xp(user_id, 5)
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🤖 سؤال جدید", callback_data="ai_new"),
        types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu"))
    bot.send_message(message.chat.id, "ادامه:", reply_markup=markup)
    return True


# ============================================================
#                    SETTINGS
# ============================================================

def show_settings(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🗑 پاک کردن State", callback_data="settings_clear"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
    bot.send_message(chat_id, "⚙️ <b>Settings</b>", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "settings_clear")
def settings_clear(call):
    clear_state(call.from_user.id)
    try: bot.answer_callback_query(call.id, "Cleared")
    except: pass
    bot.send_message(call.message.chat.id, "✅ State پاک شد.")


# ============================================================
#                    SCREENSHOT
# ============================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("add_screenshot_"))
def screenshot_request(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    trade_id = int(call.data.split("_")[-1])
    set_state(call.from_user.id, "screenshot", {"trade_id": trade_id})
    bot.send_message(call.message.chat.id, f"📸 Screenshot برای Trade #{trade_id}:")


def screenshot_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    if state.get("state") != "screenshot":
        return False
    if message.content_type != "photo":
        bot.reply_to(message, "❌ لطفاً عکس بفرست."); return True
    trade_id = state["data"]["trade_id"]
    file_id = message.photo[-1].file_id
    with db_lock:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE trades SET screenshot_file_id=? WHERE id=? AND user_id=?",
                    (file_id, trade_id, user_id))
        conn.commit(); conn.close()
    clear_state(user_id); add_xp(user_id, 5)
    bot.send_message(message.chat.id, f"📸 اضافه شد به Trade #{trade_id}.")
    return True


# ============================================================
#                  MESSAGE ROUTER
# ============================================================

@bot.message_handler(content_types=["text", "photo", "document", "voice"])
def all_messages(message):
    ensure_user(message)
    user_id = message.from_user.id

    if message.chat.id == ADMIN_CHAT_ID:
        if admin_process(message):
            return
        if handle_admin_reply(message):
            return
        if message.content_type == "text":
            if message.text.startswith("/admin"):
                show_admin_panel(message.chat.id)
                return
            if message.text.startswith("/menu"):
                show_main_menu(message.chat.id)
                return
        return

    if message.content_type == "text":
        if message.text.startswith("/start"):
            start_command(message); return
        if message.text.startswith("/menu"):
            menu_command(message); return
        if message.text.startswith("/cancel"):
            cancel_message(message); return
        if message.text.startswith("/stats"):
            show_dashboard(message.chat.id); return
        if message.text.startswith("/journal"):
            show_journal_menu(message.chat.id); return
        if message.text.startswith("/risk"):
            start_risk_calculator(message); return
        if message.text.startswith("/prop"):
            show_prop_menu(message.chat.id); return
        if message.text.startswith("/academy"):
            show_academy(message.chat.id); return
        if message.text.startswith("/contact"):
            start_contact_admin(message); return
        if message.text.startswith("/admin"):
            bot.send_message(message.chat.id, "⛔ دسترسی ندارید."); return

    state = get_state(user_id)
    current = state.get("state", "")

    if current == "contact_admin":
        send_to_admin(message); return
    if current.startswith("req_"):
        request_analysis_process(message); return
    if current.startswith("risk_"):
        risk_process(message); return
    if current.startswith("prop_"):
        prop_process(message); return
    if current.startswith("journal_"):
        journal_process(message); return
    if current == "close_trade_pnl":
        close_trade_process(message); return
    if current.startswith("psych_"):
        psychology_process(message); return
    if current == "ai_question":
        if message.content_type == "text":
            ai_process(message)
        else:
            bot.send_message(message.chat.id, "🤖 متن بفرست.")
        return
    if current == "screenshot":
        screenshot_process(message); return

    show_main_menu(message.chat.id)


# ============================================================
#                    FLASK KEEP ALIVE
# ============================================================

@app.route("/")
def home():
    return "TradScale Bot is Alive! 🚀"


@app.route("/health")
def health():
    return {"status": "ok", "bot": "TradScale"}


def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_web_server, daemon=True).start()


# ============================================================
#                    BOT COMMANDS
# ============================================================

def setup_bot_commands():
    cmds = [
        types.BotCommand("start", "شروع ربات"),
        types.BotCommand("menu", "منوی اصلی"),
        types.BotCommand("risk", "Risk Calculator"),
        types.BotCommand("journal", "Trading Journal"),
        types.BotCommand("prop", "Prop Challenge"),
        types.BotCommand("stats", "Dashboard"),
        types.BotCommand("academy", "Academy"),
        types.BotCommand("contact", "ارتباط با استاد"),
        types.BotCommand("admin", "پنل مدیریت"),
        types.BotCommand("cancel", "لغو عملیات"),
    ]
    try:
        bot.set_my_commands(cmds)
    except Exception as e:
        print("Could not set commands:", e)


# ============================================================
#                    START
# ============================================================

if __name__ == "__main__":
    print("====================================")
    print("      TradScale Bot Starting...")
    print("====================================")
    init_db()
    setup_bot_commands()
    keep_alive()
    print("Database: OK")
    print("Admin ID:", ADMIN_CHAT_ID)
    print("Channel:", CHANNEL_ID)
    print("AI:", "Enabled" if OPENAI_API_KEY else "Disabled")
    print("Bot is running...")
    bot.infinity_polling(skip_pending=True,
                         allowed_updates=["message", "callback_query"])
