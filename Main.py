import os
import sqlite3
import math
import json
import urllib.request
import urllib.error
from datetime import datetime
from threading import Thread, Lock

import telebot
from telebot import types
from flask import Flask


# ============================================================
#                    TRADSCALE BOT CONFIG
# ============================================================

API_TOKEN = os.getenv("BOT_TOKEN")

# Admin ID provided by you
ADMIN_CHAT_ID = 638640702

# Optional AI key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Optional AI model
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# Database
DB_PATH = os.getenv("TRADSCALE_DB", "tradscale.db")

if not API_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set."
    )

bot = telebot.TeleBot(API_TOKEN, parse_mode="HTML")
app = Flask("TradScale")

db_lock = Lock()

# User states in memory
# For production, important long-lived states are also saved in DB.
user_states = {}


# ============================================================
#                         DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TEXT,
                updated_at TEXT,
                xp INTEGER DEFAULT 0,
                level TEXT DEFAULT 'Beginner'
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT,
                direction TEXT,
                account_size REAL,
                risk_percent REAL,
                entry REAL,
                stop_loss REAL,
                take_profit REAL,
                position_size REAL,
                risk_amount REAL,
                potential_profit REAL,
                rr REAL,
                setup TEXT,
                reason TEXT,
                emotion_before TEXT,
                result TEXT DEFAULT 'OPEN',
                pnl REAL DEFAULT 0,
                screenshot_file_id TEXT,
                created_at TEXT,
                closed_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS prop_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT,
                account_size REAL,
                profit_target_percent REAL,
                max_drawdown_percent REAL,
                daily_drawdown_percent REAL,
                starting_balance REAL,
                current_balance REAL,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                admin_message_id INTEGER,
                user_message_id INTEGER,
                created_at TEXT
            )
        """)

        conn.commit()
        conn.close()


def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def ensure_user(message):
    user = message.from_user

    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user.id,)
        )

        exists = cur.fetchone()

        if exists:
            cur.execute("""
                UPDATE users
                SET username = ?,
                    first_name = ?,
                    last_name = ?,
                    updated_at = ?
                WHERE user_id = ?
            """, (
                user.username,
                user.first_name,
                user.last_name,
                now(),
                user.id
            ))
        else:
            cur.execute("""
                INSERT INTO users (
                    user_id,
                    username,
                    first_name,
                    last_name,
                    created_at,
                    updated_at,
                    xp,
                    level
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, 'Beginner')
            """, (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
                now(),
                now()
            ))

        conn.commit()
        conn.close()


def add_xp(user_id, amount):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT xp FROM users WHERE user_id = ?",
            (user_id,)
        )
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

        cur.execute("""
            UPDATE users
            SET xp = ?, level = ?, updated_at = ?
            WHERE user_id = ?
        """, (
            xp,
            level,
            now(),
            user_id
        ))

        conn.commit()
        conn.close()


def get_user_profile(user_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        )

        row = cur.fetchone()
        conn.close()

        return row


# ============================================================
#                         HELPERS
# ============================================================

def set_state(user_id, state, data=None):
    user_states[user_id] = {
        "state": state,
        "data": data or {}
    }


def get_state(user_id):
    return user_states.get(user_id, {})


def clear_state(user_id):
    user_states.pop(user_id, None)


def is_cancel(text):
    return text and text.strip().lower() in [
        "/cancel",
        "cancel",
        "لغو",
        "❌ لغو"
    ]


def cancel_message(message):
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "❌ عملیات لغو شد.\n\n"
        "از منوی اصلی می‌تونی دوباره شروع کنی."
    )
    show_main_menu(message.chat.id)


def safe_float(value):
    try:
        value = str(value).replace(",", "").strip()
        return float(value)
    except Exception:
        return None


def fmt_number(value, decimals=2):
    try:
        return f"{float(value):,.{decimals}f}"
    except Exception:
        return "-"


# ============================================================
#                         MAIN MENU
# ============================================================

def main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)

    markup.add(
        types.InlineKeyboardButton(
            "📊 Risk Calculator",
            callback_data="menu_risk"
        ),
        types.InlineKeyboardButton(
            "🏆 Prop Challenge",
            callback_data="menu_prop"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "📔 Trading Journal",
            callback_data="menu_journal"
        ),
        types.InlineKeyboardButton(
            "📈 My Dashboard",
            callback_data="menu_dashboard"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🧠 Psychology Check",
            callback_data="menu_psychology"
        ),
        types.InlineKeyboardButton(
            "🎓 Academy",
            callback_data="menu_academy"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "📚 TradScale Content",
            callback_data="menu_content"
        ),
        types.InlineKeyboardButton(
            "🤖 AI Assistant",
            callback_data="menu_ai"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "💬 Contact Admin",
            callback_data="contact_admin"
        ),
        types.InlineKeyboardButton(
            "⚙️ Settings",
            callback_data="menu_settings"
        )
    )

    return markup


def show_main_menu(chat_id):
    bot.send_message(
        chat_id,
        """
<b>📈 TradScale</b>

به دستیار معاملاتی TradScale خوش اومدی.

اینجا فقط قرار نیست درباره Trading صحبت کنیم؛
قرارِ اینه که <b>بهتر فکر کنیم، بهتر ریسک کنیم و منظم‌تر معامله کنیم.</b>

از منوی زیر انتخاب کن:
""",
        reply_markup=main_keyboard()
    )


# ============================================================
#                          START
# ============================================================

@bot.message_handler(commands=["start"])
def start_command(message):
    ensure_user(message)
    clear_state(message.from_user.id)

    welcome = """
🚀 <b>Welcome to TradScale</b>

8+ Years in Forex & Crypto
🧠 NeoWave × Smart Money
🏆 Real Prop Trading Experience
💰 Risk Management | Psychology | Execution

<b>Learn • Trade • Scale</b>

از اینجا می‌تونی ابزارهای معاملاتی TradScale رو استفاده کنی.
"""

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "🎓 شروع آموزش",
            callback_data="menu_academy"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "📢 کانال تلگرام",
            url="https://t.me/Arshive_koroush_fx"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "▶️ YouTube",
            url="https://youtube.com/@Koroush_fx"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "💬 ارتباط با ادمین",
            callback_data="contact_admin"
        )
    )

    bot.send_message(
        message.chat.id,
        welcome,
        reply_markup=markup
    )


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

    if data == "main_menu":
        clear_state(user_id)
        show_main_menu(call.message.chat.id)

    elif data == "menu_risk":
        start_risk_calculator(call.message)

    elif data == "menu_prop":
        show_prop_menu(call.message.chat.id)

    elif data == "menu_journal":
        show_journal_menu(call.message.chat.id)

    elif data == "menu_dashboard":
        show_dashboard(call.message.chat.id)

    elif data == "menu_psychology":
        start_psychology(call.message)

    elif data == "menu_academy":
        show_academy(call.message.chat.id)

    elif data == "menu_content":
        show_content_menu(call.message.chat.id)

    elif data == "menu_ai":
        start_ai_assistant(call.message)

    elif data == "contact_admin":
        start_contact_admin(call.message)

    elif data == "menu_settings":
        show_settings(call.message.chat.id)

    elif data == "risk_again":
        start_risk_calculator(call.message)

    elif data == "prop_new":
        start_prop_creation(call.message)

    elif data == "prop_list":
        show_prop_accounts(call.message.chat.id)

    elif data.startswith("prop_view_"):
        prop_id = int(data.split("_")[-1])
        show_prop_details(call.message.chat.id, prop_id)

    elif data.startswith("prop_delete_"):
        prop_id = int(data.split("_")[-1])
        delete_prop(call.message.chat.id, prop_id)

    elif data == "journal_new":
        start_journal(call.message)

    elif data == "journal_open":
        show_open_trades(call.message.chat.id)

    elif data == "journal_history":
        show_trade_history(call.message.chat.id)

    elif data.startswith("close_trade_"):
        trade_id = int(data.split("_")[-1])
        start_close_trade(call.message, trade_id)

    elif data == "dashboard_refresh":
        show_dashboard(call.message.chat.id)

    elif data.startswith("academy_"):
        academy_topic(call.message.chat.id, data.replace("academy_", ""))

    elif data.startswith("content_"):
        content_topic(call.message.chat.id, data.replace("content_", ""))

    elif data == "psych_again":
        start_psychology(call.message)

    elif data == "ai_new":
        start_ai_assistant(call.message)

    elif data == "settings":
        show_settings(call.message.chat.id)


# ============================================================
#                       RISK CALCULATOR
# ============================================================

def start_risk_calculator(message):
    clear_state(message.from_user.id)

    set_state(
        message.from_user.id,
        "risk_account_size"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "❌ لغو",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        message.chat.id,
        """
📊 <b>Risk Calculator</b>

مرحله 1 از 6

💰 موجودی حساب را وارد کن.

مثال:
<code>10000</code>
""",
        reply_markup=markup
    )


def risk_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)

    if state.get("state", "").startswith("risk_") is False:
        return False

    if is_cancel(message.text):
        cancel_message(message)
        return True

    current = state["state"]
    data = state["data"]

    if current == "risk_account_size":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(
                message,
                "❌ عدد نامعتبر است. مثلاً <code>10000</code> وارد کن."
            )
            return True

        data["account_size"] = value
        set_state(user_id, "risk_percent", data)

        bot.send_message(
            message.chat.id,
            "مرحله 2 از 6\n\n"
            "📉 درصد ریسک معامله را وارد کن.\n\n"
            "مثال: <code>1</code> برای 1%"
        )
        return True

    if current == "risk_percent":
        value = safe_float(message.text)

        if value is None or value <= 0 or value > 100:
            bot.reply_to(
                message,
                "❌ درصد ریسک نامعتبر است."
            )
            return True

        data["risk_percent"] = value
        set_state(user_id, "risk_entry", data)

        bot.send_message(
            message.chat.id,
            "مرحله 3 از 6\n\n"
            "🎯 Entry Price را وارد کن."
        )
        return True

    if current == "risk_entry":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ Entry نامعتبر است.")
            return True

        data["entry"] = value
        set_state(user_id, "risk_sl", data)

        bot.send_message(
            message.chat.id,
            "مرحله 4 از 6\n\n"
            "🛑 Stop Loss را وارد کن."
        )
        return True

    if current == "risk_sl":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ Stop Loss نامعتبر است.")
            return True

        data["sl"] = value
        set_state(user_id, "risk_tp", data)

        bot.send_message(
            message.chat.id,
            "مرحله 5 از 6\n\n"
            "🎯 Take Profit را وارد کن."
        )
        return True

    if current == "risk_tp":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ Take Profit نامعتبر است.")
            return True

        data["tp"] = value
        set_state(user_id, "risk_direction", data)

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "🟢 LONG",
                callback_data="risk_long"
            ),
            types.InlineKeyboardButton(
                "🔴 SHORT",
                callback_data="risk_short"
            )
        )

        bot.send_message(
            message.chat.id,
            "مرحله 6 از 6\n\n"
            "نوع معامله را انتخاب کن:",
            reply_markup=markup
        )

        return True

    return True


@bot.callback_query_handler(func=lambda call: call.data in ["risk_long", "risk_short"])
def risk_direction_callback(call):
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

    user_id = call.from_user.id
    state = get_state(user_id)

    if state.get("state") != "risk_direction":
        return

    data = state["data"]

    direction = "LONG" if call.data == "risk_long" else "SHORT"

    account = data["account_size"]
    risk_percent = data["risk_percent"]
    entry = data["entry"]
    sl = data["sl"]
    tp = data["tp"]

    risk_amount = account * risk_percent / 100

    if direction == "LONG":
        stop_distance = entry - sl
        target_distance = tp - entry
    else:
        stop_distance = sl - entry
        target_distance = entry - tp

    if stop_distance <= 0:
        bot.send_message(
            call.message.chat.id,
            "❌ برای این جهت معامله، Stop Loss در سمت اشتباه قرار دارد."
        )
        clear_state(user_id)
        return

    if target_distance <= 0:
        bot.send_message(
            call.message.chat.id,
            "❌ برای این جهت معامله، Take Profit در سمت اشتباه قرار دارد."
        )
        clear_state(user_id)
        return

    position_size = risk_amount / stop_distance
    potential_profit = position_size * target_distance
    rr = target_distance / stop_distance

    result = f"""
📊 <b>TradScale Risk Calculator</b>

━━━━━━━━━━━━━━
💰 Account: <b>{fmt_number(account)}</b>
⚠️ Risk: <b>{fmt_number(risk_percent)}%</b>
💵 Risk Amount: <b>{fmt_number(risk_amount)}</b>

📍 Entry: <b>{fmt_number(entry)}</b>
🛑 Stop Loss: <b>{fmt_number(sl)}</b>
🎯 Take Profit: <b>{fmt_number(tp)}</b>

📏 Stop Distance: <b>{fmt_number(stop_distance)}</b>
📐 Target Distance: <b>{fmt_number(target_distance)}</b>

📦 Position Size: <b>{fmt_number(position_size, 6)}</b>

⚖️ Risk / Reward:
<b>1 : {fmt_number(rr, 2)}</b>

💵 Potential Profit:
<b>{fmt_number(potential_profit)}</b>

━━━━━━━━━━━━━━

⚠️ این محاسبه آموزشی است و بسته به Contract Size، Pip Value،
نوع Symbol و قوانین Broker ممکن است نیاز به تنظیم داشته باشد.
"""

    bot.send_message(
        call.message.chat.id,
        result,
        reply_markup=types.InlineKeyboardMarkup()
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "🔄 محاسبه جدید",
            callback_data="risk_again"
        ),
        types.InlineKeyboardButton(
            "🏠 منوی اصلی",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        call.message.chat.id,
        "یک گزینه انتخاب کن:",
        reply_markup=markup
    )

    add_xp(user_id, 5)
    clear_state(user_id)


# ============================================================
#                       PROP MANAGER
# ============================================================

def show_prop_menu(chat_id):
    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "➕ Challenge جدید",
            callback_data="prop_new"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "📋 Challengeهای من",
            callback_data="prop_list"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏠 منوی اصلی",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        chat_id,
        """
🏆 <b>Prop Challenge Manager</b>

اینجا می‌تونی Challenge خودت رو ثبت و مدیریت کنی.

اطلاعاتی مثل:
• Account Size
• Profit Target
• Max Drawdown
• Daily Drawdown
• Current Balance

را وارد کن تا وضعیت Challenge برایت محاسبه شود.
""",
        reply_markup=markup
    )


def start_prop_creation(message):
    set_state(
        message.from_user.id,
        "prop_name"
    )

    bot.send_message(
        message.chat.id,
        """
🏆 ساخت Challenge جدید

مرحله 1 از 6

نام Challenge را وارد کن.

مثال:
<code>Prop Firm - 100K</code>
"""
    )


def prop_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)

    if not state.get("state", "").startswith("prop_"):
        return False

    if is_cancel(message.text):
        cancel_message(message)
        return True

    current = state["state"]
    data = state["data"]

    if current == "prop_name":
        data["name"] = message.text.strip()
        set_state(user_id, "prop_account", data)

        bot.send_message(
            message.chat.id,
            "مرحله 2 از 6\n\n"
            "💰 Account Size را وارد کن."
        )
        return True

    if current == "prop_account":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ عدد نامعتبر است.")
            return True

        data["account_size"] = value
        set_state(user_id, "prop_target", data)

        bot.send_message(
            message.chat.id,
            "مرحله 3 از 6\n\n"
            "🎯 Profit Target چند درصد است؟\n"
            "مثال: <code>10</code>"
        )
        return True

    if current == "prop_target":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ مقدار نامعتبر است.")
            return True

        data["target"] = value
        set_state(user_id, "prop_max_dd", data)

        bot.send_message(
            message.chat.id,
            "مرحله 4 از 6\n\n"
            "📉 Maximum Drawdown چند درصد است؟"
        )
        return True

    if current == "prop_max_dd":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ مقدار نامعتبر است.")
            return True

        data["max_dd"] = value
        set_state(user_id, "prop_daily_dd", data)

        bot.send_message(
            message.chat.id,
            "مرحله 5 از 6\n\n"
            "📉 Daily Drawdown چند درصد است؟"
        )
        return True

    if current == "prop_daily_dd":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ مقدار نامعتبر است.")
            return True

        data["daily_dd"] = value
        set_state(user_id, "prop_balance", data)

        bot.send_message(
            message.chat.id,
            "مرحله 6 از 6\n\n"
            "💵 موجودی فعلی Challenge را وارد کن."
        )
        return True

    if current == "prop_balance":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ موجودی نامعتبر است.")
            return True

        data["balance"] = value

        with db_lock:
            conn = get_db()
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO prop_accounts (
                    user_id,
                    name,
                    account_size,
                    profit_target_percent,
                    max_drawdown_percent,
                    daily_drawdown_percent,
                    starting_balance,
                    current_balance,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                data["name"],
                data["account_size"],
                data["target"],
                data["max_dd"],
                data["daily_dd"],
                data["account_size"],
                data["balance"],
                now(),
                now()
            ))

            prop_id = cur.lastrowid

            conn.commit()
            conn.close()

        clear_state(user_id)
        add_xp(user_id, 15)

        bot.send_message(
            message.chat.id,
            "✅ Challenge با موفقیت ثبت شد."
        )

        show_prop_details(
            message.chat.id,
            prop_id
        )

        return True

    return True


def show_prop_accounts(chat_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM prop_accounts
            WHERE user_id = ?
            ORDER BY id DESC
        """, (chat_id,))

        rows = cur.fetchall()
        conn.close()

    if not rows:
        bot.send_message(
            chat_id,
            "هنوز هیچ Challengeای ثبت نکردی."
        )
        return

    markup = types.InlineKeyboardMarkup()

    for row in rows:
        markup.add(
            types.InlineKeyboardButton(
                f"🏆 {row['name']}",
                callback_data=f"prop_view_{row['id']}"
            )
        )

    markup.add(
        types.InlineKeyboardButton(
            "➕ Challenge جدید",
            callback_data="prop_new"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏠 منوی اصلی",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        chat_id,
        "📋 <b>Challengeهای تو:</b>",
        reply_markup=markup
    )


def show_prop_details(chat_id, prop_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM prop_accounts
            WHERE id = ? AND user_id = ?
        """, (
            prop_id,
            chat_id
        ))

        row = cur.fetchone()
        conn.close()

    if not row:
        bot.send_message(chat_id, "❌ Challenge پیدا نشد.")
        return

    starting = float(row["starting_balance"])
    balance = float(row["current_balance"])
    target_percent = float(row["profit_target_percent"])
    max_dd = float(row["max_drawdown_percent"])
    daily_dd = float(row["daily_drawdown_percent"])

    pnl = balance - starting
    pnl_percent = (pnl / starting) * 100

    target_amount = starting * target_percent / 100
    target_balance = starting + target_amount

    max_dd_amount = starting * max_dd / 100
    daily_dd_amount = starting * daily_dd / 100

    remaining_target = max(0, target_balance - balance)

    if pnl_percent >= target_percent:
        status = "🎉 TARGET REACHED"
    elif pnl_percent <= -max_dd:
        status = "🛑 MAX DRAWDOWN BREACHED"
    else:
        status = "🟢 ACTIVE"

    text = f"""
🏆 <b>{row['name']}</b>

Status: <b>{status}</b>

━━━━━━━━━━━━━━
💰 Starting Balance:
<b>{fmt_number(starting)}</b>

💵 Current Balance:
<b>{fmt_number(balance)}</b>

📈 Current P/L:
<b>{fmt_number(pnl)}</b>
(<b>{fmt_number(pnl_percent)}%</b>)

🎯 Profit Target:
<b>{fmt_number(target_percent)}%</b>

🎯 Target Balance:
<b>{fmt_number(target_balance)}</b>

⏳ Remaining to Target:
<b>{fmt_number(remaining_target)}</b>

📉 Max Drawdown:
<b>{fmt_number(max_dd)}%</b>
≈ {fmt_number(max_dd_amount)}

📉 Daily Drawdown:
<b>{fmt_number(daily_dd)}%</b>
≈ {fmt_number(daily_dd_amount)}
━━━━━━━━━━━━━━
"""

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "🗑 حذف",
            callback_data=f"prop_delete_{prop_id}"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏠 منوی اصلی",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        chat_id,
        text,
        reply_markup=markup
    )


def delete_prop(chat_id, prop_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM prop_accounts
            WHERE id = ? AND user_id = ?
        """, (
            prop_id,
            chat_id
        ))

        conn.commit()
        conn.close()

    bot.send_message(
        chat_id,
        "🗑 Challenge حذف شد."
    )

    show_prop_menu(chat_id)


# ============================================================
#                        TRADING JOURNAL
# ============================================================

def show_journal_menu(chat_id):
    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "➕ ثبت معامله جدید",
            callback_data="journal_new"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🟡 معاملات باز",
            callback_data="journal_open"
        ),
        types.InlineKeyboardButton(
            "📚 تاریخچه",
            callback_data="journal_history"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "📈 Dashboard",
            callback_data="menu_dashboard"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏠 منوی اصلی",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        chat_id,
        """
📔 <b>Trading Journal</b>

هر معامله را ثبت کن تا بعداً بتوانی رفتار و عملکردت را بررسی کنی.

اطلاعاتی مثل:
• Entry / SL / TP
• Risk
• Setup
• دلیل ورود
• Emotion
• Result
• P/L
• Screenshot

ثبت می‌شود.
""",
        reply_markup=markup
    )


def start_journal(message):
    set_state(
        message.from_user.id,
        "journal_symbol"
    )

    bot.send_message(
        message.chat.id,
        """
📔 <b>New Trade</b>

مرحله 1 از 10

نماد را وارد کن.

مثال:
<code>XAUUSD</code>
یا
<code>BTCUSDT</code>
"""
    )


def journal_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)

    if not state.get("state", "").startswith("journal_"):
        return False

    if is_cancel(message.text):
        cancel_message(message)
        return True

    current = state["state"]
    data = state["data"]

    if current == "journal_symbol":
        data["symbol"] = message.text.strip().upper()
        set_state(user_id, "journal_direction", data)

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "🟢 LONG",
                callback_data="journal_long"
            ),
            types.InlineKeyboardButton(
                "🔴 SHORT",
                callback_data="journal_short"
            )
        )

        bot.send_message(
            message.chat.id,
            "مرحله 2 از 10\n\nنوع معامله:",
            reply_markup=markup
        )
        return True

    if current == "journal_account":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ عدد نامعتبر است.")
            return True

        data["account"] = value
        set_state(user_id, "journal_risk", data)

        bot.send_message(
            message.chat.id,
            "مرحله 4 از 10\n\n"
            "⚠️ درصد ریسک معامله:"
        )
        return True

    if current == "journal_risk":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ مقدار نامعتبر است.")
            return True

        data["risk"] = value
        set_state(user_id, "journal_entry", data)

        bot.send_message(
            message.chat.id,
            "مرحله 5 از 10\n\n"
            "📍 Entry:"
        )
        return True

    if current == "journal_entry":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ Entry نامعتبر است.")
            return True

        data["entry"] = value
        set_state(user_id, "journal_sl", data)

        bot.send_message(
            message.chat.id,
            "مرحله 6 از 10\n\n"
            "🛑 Stop Loss:"
        )
        return True

    if current == "journal_sl":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ SL نامعتبر است.")
            return True

        data["sl"] = value
        set_state(user_id, "journal_tp", data)

        bot.send_message(
            message.chat.id,
            "مرحله 7 از 10\n\n"
            "🎯 Take Profit:"
        )
        return True

    if current == "journal_tp":
        value = safe_float(message.text)

        if value is None or value <= 0:
            bot.reply_to(message, "❌ TP نامعتبر است.")
            return True

        data["tp"] = value
        set_state(user_id, "journal_setup", data)

        bot.send_message(
            message.chat.id,
            "مرحله 8 از 10\n\n"
            "🧠 Setup / Strategy را بنویس.\n\n"
            "مثال:\n"
            "<code>Liquidity Sweep + BOS</code>"
        )
        return True

    if current == "journal_setup":
        data["setup"] = message.text.strip()
        set_state(user_id, "journal_reason", data)

        bot.send_message(
            message.chat.id,
            "مرحله 9 از 10\n\n"
            "✍️ دلیل ورودت به معامله را بنویس."
        )
        return True

    if current == "journal_reason":
        data["reason"] = message.text.strip()
        set_state(user_id, "journal_emotion", data)

        bot.send_message(
            message.chat.id,
            """
مرحله 10 از 10

🧠 وضعیت ذهنی قبل از ورود چه بود؟

مثلاً:
• Calm
• Confident
• FOMO
• Fear
• Revenge
• Uncertain
"""
        )
        return True

    if current == "journal_emotion":
        data["emotion"] = message.text.strip()

        account = data["account"]
        risk = data["risk"]
        entry = data["entry"]
        sl = data["sl"]
        tp = data["tp"]
        direction = data["direction"]

        risk_amount = account * risk / 100

        if direction == "LONG":
            stop_distance = entry - sl
            target_distance = tp - entry
        else:
            stop_distance = sl - entry
            target_distance = entry - tp

        rr = 0

        if stop_distance > 0 and target_distance > 0:
            rr = target_distance / stop_distance

        position_size = (
            risk_amount / stop_distance
            if stop_distance > 0
            else 0
        )

        with db_lock:
            conn = get_db()
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO trades (
                    user_id,
                    symbol,
                    direction,
                    account_size,
                    risk_percent,
                    entry,
                    stop_loss,
                    take_profit,
                    position_size,
                    risk_amount,
                    potential_profit,
                    rr,
                    setup,
                    reason,
                    emotion_before,
                    result,
                    pnl,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 0, ?)
            """, (
                user_id,
                data["symbol"],
                direction,
                account,
                risk,
                entry,
                sl,
                tp,
                position_size,
                risk_amount,
                risk_amount * rr,
                rr,
                data["setup"],
                data["reason"],
                data["emotion"],
                now()
            ))

            trade_id = cur.lastrowid

            conn.commit()
            conn.close()

        add_xp(user_id, 20)
        clear_state(user_id)

        bot.send_message(
            message.chat.id,
            f"""
✅ <b>Trade ثبت شد.</b>

📌 {data['symbol']} — {direction}

Entry: <b>{fmt_number(entry)}</b>
SL: <b>{fmt_number(sl)}</b>
TP: <b>{fmt_number(tp)}</b>

⚠️ Risk: <b>{fmt_number(risk)}%</b>
💵 Risk Amount: <b>{fmt_number(risk_amount)}</b>

⚖️ R:R:
<b>1 : {fmt_number(rr)}</b>

📦 Position Size:
<b>{fmt_number(position_size, 6)}</b>

🧠 Emotion:
<b>{data['emotion']}</b>

Trade ID:
<code>{trade_id}</code>
"""
        )

        markup = types.InlineKeyboardMarkup()

        markup.add(
            types.InlineKeyboardButton(
                "📸 اضافه کردن Screenshot",
                callback_data=f"add_screenshot_{trade_id}"
            )
        )

        markup.add(
            types.InlineKeyboardButton(
                "🟢 بستن معامله",
                callback_data=f"close_trade_{trade_id}"
            )
        )

        markup.add(
            types.InlineKeyboardButton(
                "📔 Journal",
                callback_data="menu_journal"
            )
        )

        bot.send_message(
            message.chat.id,
            "چه کاری می‌خوای انجام بدی؟",
            reply_markup=markup
        )

        return True

    return True


@bot.callback_query_handler(
    func=lambda call: call.data in ["journal_long", "journal_short"]
)
def journal_direction_callback(call):
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

    user_id = call.from_user.id
    state = get_state(user_id)

    if state.get("state") != "journal_direction":
        return

    data = state["data"]
    data["direction"] = (
        "LONG"
        if call.data == "journal_long"
        else "SHORT"
    )

    set_state(
        user_id,
        "journal_account",
        data
    )

    bot.send_message(
        call.message.chat.id,
        "مرحله 3 از 10\n\n"
        "💰 Account Size:"
    )


def show_open_trades(chat_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM trades
            WHERE user_id = ?
              AND result = 'OPEN'
            ORDER BY id DESC
        """, (chat_id,))

        rows = cur.fetchall()
        conn.close()

    if not rows:
        bot.send_message(
            chat_id,
            "🟡 هیچ معامله بازی نداری."
        )
        return

    for row in rows:
        markup = types.InlineKeyboardMarkup()

        markup.add(
            types.InlineKeyboardButton(
                "🔴 Close Trade",
                callback_data=f"close_trade_{row['id']}"
            )
        )

        bot.send_message(
            chat_id,
            f"""
🟡 <b>Open Trade #{row['id']}</b>

{row['symbol']} — {row['direction']}

Entry: {fmt_number(row['entry'])}
SL: {fmt_number(row['stop_loss'])}
TP: {fmt_number(row['take_profit'])}

Risk: {fmt_number(row['risk_percent'])}%
R:R: 1:{fmt_number(row['rr'])}

Setup:
{row['setup']}
""",
            reply_markup=markup
        )


def show_trade_history(chat_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM trades
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 20
        """, (chat_id,))

        rows = cur.fetchall()
        conn.close()

    if not rows:
        bot.send_message(
            chat_id,
            "📚 هنوز معامله‌ای در Journal ثبت نکردی."
        )
        return

    for row in rows:
        result = row["result"]

        if result == "WIN":
            icon = "🟢"
        elif result == "LOSS":
            icon = "🔴"
        elif result == "BE":
            icon = "🟡"
        else:
            icon = "⚪"

        bot.send_message(
            chat_id,
            f"""
{icon} <b>Trade #{row['id']}</b>

{row['symbol']} — {row['direction']}

Result: <b>{result}</b>
P/L: <b>{fmt_number(row['pnl'])}</b>
R:R: 1:{fmt_number(row['rr'])}

Setup:
{row['setup']}

Emotion:
{row['emotion_before']}
"""
        )


def start_close_trade(message, trade_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM trades
            WHERE id = ? AND user_id = ?
        """, (
            trade_id,
            message.from_user.id
        ))

        row = cur.fetchone()
        conn.close()

    if not row:
        bot.send_message(
            message.chat.id,
            "❌ Trade پیدا نشد."
        )
        return

    set_state(
        message.from_user.id,
        "close_trade_result",
        {
            "trade_id": trade_id
        }
    )

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "🟢 WIN",
            callback_data=f"result_win_{trade_id}"
        ),
        types.InlineKeyboardButton(
            "🔴 LOSS",
            callback_data=f"result_loss_{trade_id}"
        ),
        types.InlineKeyboardButton(
            "🟡 BE",
            callback_data=f"result_be_{trade_id}"
        )
    )

    bot.send_message(
        message.chat.id,
        f"Trade #{trade_id}\n\nنتیجه را انتخاب کن:",
        reply_markup=markup
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("result_")
)
def result_callback(call):
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

    parts = call.data.split("_")
    result = parts[1].upper()
    trade_id = int(parts[2])

    set_state(
        call.from_user.id,
        "close_trade_pnl",
        {
            "trade_id": trade_id,
            "result": result
        }
    )

    bot.send_message(
        call.message.chat.id,
        "💵 مقدار P/L این معامله را وارد کن.\n\n"
        "مثال:\n"
        "<code>250</code>\n\n"
        "برای ضرر عدد منفی وارد کن:\n"
        "<code>-150</code>"
    )


def close_trade_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)

    if state.get("state") != "close_trade_pnl":
        return False

    if is_cancel(message.text):
        cancel_message(message)
        return True

    pnl = safe_float(message.text)

    if pnl is None:
        bot.reply_to(message, "❌ مقدار P/L نامعتبر است.")
        return True

    trade_id = state["data"]["trade_id"]
    result = state["data"]["result"]

    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE trades
            SET result = ?,
                pnl = ?,
                closed_at = ?
            WHERE id = ? AND user_id = ?
        """, (
            result,
            pnl,
            now(),
            trade_id,
            user_id
        ))

        conn.commit()
        conn.close()

    add_xp(user_id, 15)
    clear_state(user_id)

    bot.send_message(
        message.chat.id,
        f"""
✅ Trade #{trade_id} بسته شد.

Result: <b>{result}</b>
P/L: <b>{fmt_number(pnl)}</b>

📔 این معامله در Journal ذخیره شد.
"""
    )

    show_dashboard(message.chat.id)

    return True


# ============================================================
#                         DASHBOARD
# ============================================================

def show_dashboard(chat_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM trades
            WHERE user_id = ?
        """, (chat_id,))

        rows = cur.fetchall()
        conn.close()

    profile = get_user_profile(chat_id)

    total = len(rows)

    closed = [
        r for r in rows
        if r["result"] in ("WIN", "LOSS", "BE")
    ]

    wins = len([
        r for r in closed
        if r["result"] == "WIN"
    ])

    losses = len([
        r for r in closed
        if r["result"] == "LOSS"
    ])

    bes = len([
        r for r in closed
        if r["result"] == "BE"
    ])

    pnl = sum(float(r["pnl"] or 0) for r in closed)

    win_rate = (
        wins / len(closed) * 100
        if closed
        else 0
    )

    avg_rr = (
        sum(float(r["rr"] or 0) for r in closed) / len(closed)
        if closed
        else 0
    )

    setups = {}

    for row in closed:
        setup = row["setup"] or "Unknown"
        setups[setup] = setups.get(setup, 0) + 1

    best_setup = "-"
    if setups:
        best_setup = max(
            setups,
            key=setups.get
        )

    text = f"""
📈 <b>TradScale Dashboard</b>

━━━━━━━━━━━━━━
👤 Level:
<b>{profile['level'] if profile else 'Beginner'}</b>

🏅 XP:
<b>{profile['xp'] if profile else 0}</b>

━━━━━━━━━━━━━━
📔 Total Trades:
<b>{total}</b>

🟢 Wins:
<b>{wins}</b>

🔴 Losses:
<b>{losses}</b>

🟡 Break Even:
<b>{bes}</b>

📊 Win Rate:
<b>{fmt_number(win_rate)}%</b>

⚖️ Average R:R:
<b>1 : {fmt_number(avg_rr)}</b>

💰 Total P/L:
<b>{fmt_number(pnl)}</b>

🔥 Most Used Setup:
<b>{best_setup}</b>

━━━━━━━━━━━━━━
"""

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "📔 Journal",
            callback_data="menu_journal"
        ),
        types.InlineKeyboardButton(
            "🔄 Refresh",
            callback_data="dashboard_refresh"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏠 Main Menu",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        chat_id,
        text,
        reply_markup=markup
    )


# ============================================================
#                     PSYCHOLOGY CHECK
# ============================================================

def start_psychology(message):
    set_state(
        message.from_user.id,
        "psych_q1",
        {}
    )

    bot.send_message(
        message.chat.id,
        """
🧠 <b>TradScale Psychology Check</b>

قبل از معامله، اول وضعیت خودت را بررسی کن.

سؤال 1:

چرا می‌خواهی وارد این معامله شوی؟

1️⃣ Setup معتبر
2️⃣ FOMO
3️⃣ Revenge
4️⃣ جبران ضرر قبلی
5️⃣ مطمئن نیستم

فقط شماره را بفرست.
"""
    )


def psychology_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)

    if state.get("state", "").startswith("psych_") is False:
        return False

    if is_cancel(message.text):
        cancel_message(message)
        return True

    current = state["state"]
    data = state["data"]

    if current == "psych_q1":
        data["q1"] = message.text.strip()

        set_state(
            user_id,
            "psych_q2",
            data
        )

        bot.send_message(
            message.chat.id,
            """
سؤال 2:

آیا Entry، Stop Loss و Risk مشخص هستند؟

1️⃣ بله
2️⃣ خیر
"""
        )
        return True

    if current == "psych_q2":
        data["q2"] = message.text.strip()

        set_state(
            user_id,
            "psych_q3",
            data
        )

        bot.send_message(
            message.chat.id,
            """
سؤال 3:

اگر این معامله Loss شود، از نظر ذهنی آماده‌ای آن را بپذیری؟

1️⃣ بله
2️⃣ خیر
"""
        )
        return True

    if current == "psych_q3":
        data["q3"] = message.text.strip()

        set_state(
            user_id,
            "psych_q4",
            data
        )

        bot.send_message(
            message.chat.id,
            """
سؤال 4:

آیا این معامله دقیقاً طبق سیستم معاملاتی توست؟

1️⃣ بله
2️⃣ خیر
"""
        )
        return True

    if current == "psych_q4":
        data["q4"] = message.text.strip()

        q1 = data["q1"]
        q2 = data["q2"]
        q3 = data["q3"]
        q4 = data["q4"]

        score = 0

        if q1 == "1":
            score += 25

        if q2 == "1":
            score += 25

        if q3 == "1":
            score += 25

        if q4 == "1":
            score += 25

        if score >= 90:
            verdict = "🟢 READY"
            advice = (
                "از نظر این چک‌لیست، شرایط ذهنی و ساختاری "
                "مناسب به نظر می‌رسد."
            )
        elif score >= 70:
            verdict = "🟡 CAUTION"
            advice = (
                "قبل از ورود، مواردی که جواب منفی داده‌ای "
                "را دوباره بررسی کن."
            )
        else:
            verdict = "🔴 NOT READY"
            advice = (
                "بهتر است فعلاً وارد معامله نشوی و اول "
                "مشکل Setup / Risk / Psychology را بررسی کنی."
            )

        clear_state(user_id)

        bot.send_message(
            message.chat.id,
            f"""
🧠 <b>Psychology Check Result</b>

Score:
<b>{score}/100</b>

Status:
<b>{verdict}</b>

{advice}

━━━━━━━━━━━━━━

⚠️ این ابزار توصیه معاملاتی نیست؛
هدف آن کمک به ایجاد Discipline قبل از تصمیم‌گیری است.
""",
            reply_markup=types.InlineKeyboardMarkup()
        )

        add_xp(user_id, 10)

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "🔄 Check Again",
                callback_data="psych_again"
            ),
            types.InlineKeyboardButton(
                "🏠 Main Menu",
                callback_data="main_menu"
            )
        )

        bot.send_message(
            message.chat.id,
            "یک گزینه انتخاب کن:",
            reply_markup=markup
        )

        return True

    return True


# ============================================================
#                            ACADEMY
# ============================================================

def show_academy(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)

    topics = [
        ("📊 Technical Analysis", "technical"),
        ("🌊 NeoWave", "neowave"),
        ("🧠 Smart Money", "smartmoney"),
        ("💰 Risk Management", "risk"),
        ("🧠 Psychology", "psychology"),
        ("🏆 Prop Trading", "prop"),
        ("📔 Trading Journal", "journal"),
        ("⚙️ Trading System", "system"),
    ]

    for title, key in topics:
        markup.add(
            types.InlineKeyboardButton(
                title,
                callback_data=f"academy_{key}"
            )
        )

    markup.add(
        types.InlineKeyboardButton(
            "🏠 Main Menu",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        chat_id,
        """
🎓 <b>TradScale Academy</b>

دانش را مرحله به مرحله بساز.

موضوع موردنظر را انتخاب کن:
""",
        reply_markup=markup
    )


def academy_topic(chat_id, topic):
    lessons = {
        "technical": """
📊 <b>Technical Analysis</b>

در این بخش می‌تونی مباحثی مثل:

• Market Structure
• Support / Resistance
• Trend
• Price Action
• Liquidity
• Confirmation

را یاد بگیری.

محتوای آموزشی کامل‌تر در کانال TradScale منتشر می‌شود.
""",

        "neowave": """
🌊 <b>NeoWave</b>

تمرکز این بخش روی:

• Wave Structure
• Corrective Patterns
• Elliott Wave Relationships
• NeoWave Rules
• Structural Validation

است.

هدف: درک ساختار بازار، نه پیش‌بینی کورکورانه.
""",

        "smartmoney": """
🧠 <b>Smart Money</b>

موضوعات:

• Liquidity
• BOS
• CHoCH
• Order Blocks
• Imbalances
• Market Structure

همیشه ساختار را قبل از Entry بررسی کن.
""",

        "risk": """
💰 <b>Risk Management</b>

اصول پایه:

• Risk per Trade
• Position Sizing
• Risk / Reward
• Drawdown
• Daily Loss Limit
• Capital Preservation

اول سرمایه را حفظ کن؛ بعد به فکر رشد آن باش.
""",

        "psychology": """
🧠 <b>Trading Psychology</b>

موضوعات:

• FOMO
• Revenge Trading
• Overtrading
• Fear
• Greed
• Discipline
• Consistency

هدف Psychology حذف احساسات نیست؛
هدف، جلوگیری از تصمیم‌گیری مخرب تحت تأثیر احساسات است.
""",

        "prop": """
🏆 <b>Prop Trading</b>

موضوعات:

• Challenge Rules
• Daily Drawdown
• Maximum Drawdown
• Profit Target
• Risk Planning
• Consistency

هر Challenge قوانین خودش را دارد؛
قبل از معامله همیشه قوانین همان Firm را بررسی کن.
""",

        "journal": """
📔 <b>Trading Journal</b>

یک Journal خوب باید به تو نشان دهد:

• کجا خوب عمل کردی
• کجا از سیستم خارج شدی
• کدام Setup بهتر جواب می‌دهد
• کجا Overtrade می‌کنی
• چه زمانی از نظر ذهنی ضعیف‌تر هستی

Journal برای قضاوت تو نیست؛
برای شناخت بهتر رفتار معاملاتی توست.
""",

        "system": """
⚙️ <b>Trading System</b>

یک سیستم معاملاتی باید مشخص کند:

1. چه زمانی Market را بررسی می‌کنی؟
2. چه Setupهایی معتبر هستند؟
3. Entry چه شرایطی دارد؟
4. SL کجاست؟
5. Risk چقدر است؟
6. چه زمانی از معامله خارج می‌شوی؟
7. چه زمانی اصلاً نباید معامله کنی؟

سیستم خوب، تصمیم‌گیری را از حالت احساسی خارج می‌کند.
"""
    }

    text = lessons.get(
        topic,
        "این درس هنوز در حال آماده‌سازی است."
    )

    bot.send_message(
        chat_id,
        text
    )


# ============================================================
#                       CONTENT HUB
# ============================================================

def show_content_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)

    markup.add(
        types.InlineKeyboardButton(
            "📊 Analysis",
            callback_data="content_analysis"
        ),
        types.InlineKeyboardButton(
            "🎓 Education",
            callback_data="content_education"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏆 Prop",
            callback_data="content_prop"
        ),
        types.InlineKeyboardButton(
            "🧠 Psychology",
            callback_data="content_psychology"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🌊 NeoWave",
            callback_data="content_neowave"
        ),
        types.InlineKeyboardButton(
            "💰 Risk",
            callback_data="content_risk"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏠 Main Menu",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        chat_id,
        """
📚 <b>TradScale Content Hub</b>

دسته‌بندی موردنظرت را انتخاب کن.
""",
        reply_markup=markup
    )


def content_topic(chat_id, topic):
    texts = {
        "analysis": "📊 بخش Analysis — تحلیل‌ها و سناریوهای بازار در کانال منتشر می‌شوند.",
        "education": "🎓 بخش Education — آموزش‌های ساختاری و تجربی TradScale.",
        "prop": "🏆 بخش Prop — تجربه‌ها و آموزش‌های مربوط به Prop Trading.",
        "psychology": "🧠 بخش Psychology — ذهنیت، Discipline و Psychology.",
        "neowave": "🌊 بخش NeoWave — ساختار بازار و Wave Analysis.",
        "risk": "💰 بخش Risk — مدیریت سرمایه و کنترل Drawdown."
    }

    bot.send_message(
        chat_id,
        texts.get(topic, "این بخش در حال آماده‌سازی است.")
    )


# ============================================================
#                         AI ASSISTANT
# ============================================================

def start_ai_assistant(message):
    if not OPENAI_API_KEY:
        bot.send_message(
            message.chat.id,
            """
🤖 <b>TradScale AI Assistant</b>

این بخش در ساختار Bot فعال شده، اما برای استفاده از AI باید:

<code>OPENAI_API_KEY</code>

را در Environment Variables قرار بدهی.

بعد از تنظیم API Key، کاربر می‌تواند درباره:

• Trading Psychology
• Risk Management
• Market Structure
• Journal Review
• Trade Planning
• Educational Questions

از AI سؤال کند.

⚠️ AI قرار نیست سیگنال Buy/Sell بدهد.
"""
        )
        return

    set_state(
        message.from_user.id,
        "ai_question"
    )

    bot.send_message(
        message.chat.id,
        """
🤖 <b>TradScale AI Assistant</b>

سؤالت را بفرست.

مثلاً:

• «این معامله چرا خارج از پلن محسوب می‌شود؟»
• «R:R مناسب یعنی چه؟»
• «چطور Journal خودم را بررسی کنم؟»
• «فرق BOS و CHoCH چیست؟»

⚠️ AI جایگزین تحلیل و تصمیم‌گیری شخصی نیست.
"""
    )


def ask_openai(question):
    if not OPENAI_API_KEY:
        return None

    system_prompt = """
You are TradScale AI Assistant.

You are an educational trading assistant.

Your responsibilities:
- Explain trading concepts.
- Help users review their trading process.
- Explain risk management.
- Explain psychology.
- Help with journaling.
- Help structure scenarios.

You MUST NOT:
- Promise profits.
- Guarantee outcomes.
- Tell users to buy or sell a specific asset.
- Present financial advice as certainty.
- Encourage excessive risk.

If a user asks for a specific trade signal, explain the educational framework
instead of giving a direct BUY/SELL command.

Answer clearly and concisely.
"""

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": question
            }
        ],
        "max_output_tokens": 800
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=45
        ) as response:

            raw = response.read().decode("utf-8")
            result = json.loads(raw)

            # Responses API output extraction
            if "output_text" in result:
                return result["output_text"]

            texts = []

            for item in result.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        texts.append(
                            content.get("text", "")
                        )

            if texts:
                return "\n".join(texts)

            return "AI پاسخی برنگرداند."

    except Exception as e:
        print("OpenAI Error:", e)
        return (
            "❌ در ارتباط با AI مشکلی پیش آمد. "
            "لطفاً کمی بعد دوباره تلاش کن."
        )


def ai_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)

    if state.get("state") != "ai_question":
        return False

    if is_cancel(message.text):
        cancel_message(message)
        return True

    question = message.text.strip()

    bot.send_message(
        message.chat.id,
        "🤖 در حال بررسی سؤال..."
    )

    answer = ask_openai(question)

    if answer:
        bot.send_message(
            message.chat.id,
            "🤖 <b>TradScale AI</b>\n\n" + answer
        )

    clear_state(user_id)
    add_xp(user_id, 5)

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "🤖 سؤال جدید",
            callback_data="ai_new"
        ),
        types.InlineKeyboardButton(
            "🏠 منوی اصلی",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        message.chat.id,
        "یک گزینه انتخاب کن:",
        reply_markup=markup
    )

    return True


# ============================================================
#                       CONTACT ADMIN
# ============================================================

def start_contact_admin(message):
    set_state(
        message.from_user.id,
        "contact_admin"
    )

    bot.send_message(
        message.chat.id,
        """
💬 <b>ارتباط با TradScale</b>

پیامت را همینجا بنویس.

پیام مستقیماً برای ادمین ارسال می‌شود.

می‌توانی:
• سؤال
• پیشنهاد
• مشکل
• درخواست همکاری

را ارسال کنی.

برای لغو:
<code>/cancel</code>
"""
    )


def send_to_admin(message):
    user_id = message.from_user.id

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else "بدون Username"
    )

    header = f"""
📩 <b>New TradScale Message</b>

👤 Name:
{message.from_user.first_name or '-'}

🆔 User ID:
<code>{user_id}</code>

🔗 Username:
{username}

━━━━━━━━━━━━━━
"""

    # We use copy_message where possible.
    # This keeps text/photos/documents/media available.
    try:
        info_msg = bot.send_message(
            ADMIN_CHAT_ID,
            header
        )

        copied = bot.copy_message(
            ADMIN_CHAT_ID,
            message.chat.id,
            message.message_id
        )

        with db_lock:
            conn = get_db()
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO admin_messages (
                    user_id,
                    admin_message_id,
                    user_message_id,
                    created_at
                )
                VALUES (?, ?, ?, ?)
            """, (
                user_id,
                copied.message_id,
                message.message_id,
                now()
            ))

            conn.commit()
            conn.close()

        bot.reply_to(
            message,
            "✅ پیام شما برای ادمین TradScale ارسال شد.\n"
            "به محض پاسخ، پیام برای شما ارسال می‌شود."
        )

    except Exception as e:
        print("Admin forward error:", e)

        bot.reply_to(
            message,
            "❌ در ارسال پیام مشکلی پیش آمد. لطفاً دوباره تلاش کن."
        )

    clear_state(user_id)


# ============================================================
#                ADMIN REPLY SYSTEM
# ============================================================

def handle_admin_reply(message):
    if message.chat.id != ADMIN_CHAT_ID:
        return False

    if not message.reply_to_message:
        return False

    replied_message_id = message.reply_to_message.message_id

    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT user_id
            FROM admin_messages
            WHERE admin_message_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (replied_message_id,))

        row = cur.fetchone()
        conn.close()

    if not row:
        return False

    target_user_id = row["user_id"]

    try:
        if message.content_type == "text":
            bot.send_message(
                target_user_id,
                "👤 <b>پاسخ TradScale:</b>\n\n"
                + message.text
            )

        elif message.content_type == "photo":
            caption = (
                "👤 <b>پاسخ TradScale:</b>\n\n"
                + (message.caption or "")
            )

            bot.send_photo(
                target_user_id,
                message.photo[-1].file_id,
                caption=caption
            )

        elif message.content_type == "document":
            bot.send_document(
                target_user_id,
                message.document.file_id,
                caption=(
                    "👤 <b>پاسخ TradScale:</b>\n\n"
                    + (message.caption or "")
                )
            )

        elif message.content_type == "voice":
            bot.send_voice(
                target_user_id,
                message.voice.file_id
            )

        else:
            bot.send_message(
                target_user_id,
                "👤 <b>پاسخ TradScale:</b>\n\n"
                "ادمین یک پیام جدید برای شما ارسال کرده است."
            )

        bot.reply_to(
            message,
            "📤 پاسخ برای کاربر ارسال شد."
        )

    except Exception as e:
        print("Admin reply error:", e)

        bot.reply_to(
            message,
            "❌ ارسال پاسخ به کاربر ناموفق بود."
        )

    return True


# ============================================================
#                         SETTINGS
# ============================================================

def show_settings(chat_id):
    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "🗑 پاک کردن State",
            callback_data="settings_clear"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏠 Main Menu",
            callback_data="main_menu"
        )
    )

    bot.send_message(
        chat_id,
        """
⚙️ <b>Settings</b>

TradScale Bot

برای حفظ اطلاعات Journal و Challengeها،
اطلاعات داخل دیتابیس ذخیره می‌شوند.
""",
        reply_markup=markup
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "settings_clear"
)
def settings_clear(call):
    clear_state(call.from_user.id)

    try:
        bot.answer_callback_query(
            call.id,
            "State cleared"
        )
    except:
        pass

    bot.send_message(
        call.message.chat.id,
        "✅ State فعلی پاک شد."
    )


# ============================================================
#                 SCREENSHOT SUPPORT
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("add_screenshot_")
)
def screenshot_request(call):
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

    trade_id = int(
        call.data.split("_")[-1]
    )

    set_state(
        call.from_user.id,
        "screenshot",
        {
            "trade_id": trade_id
        }
    )

    bot.send_message(
        call.message.chat.id,
        f"""
📸 Screenshot برای Trade #{trade_id}

عکس چارت را همینجا ارسال کن.

بعد از ارسال، Screenshot به همان معامله متصل می‌شود.
"""
    )


def screenshot_process(message):
    user_id = message.from_user.id
    state = get_state(user_id)

    if state.get("state") != "screenshot":
        return False

    if message.content_type != "photo":
        bot.reply_to(
            message,
            "❌ لطفاً Screenshot را به صورت عکس ارسال کن."
        )
        return True

    trade_id = state["data"]["trade_id"]
    file_id = message.photo[-1].file_id

    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE trades
            SET screenshot_file_id = ?
            WHERE id = ? AND user_id = ?
        """, (
            file_id,
            trade_id,
            user_id
        ))

        conn.commit()
        conn.close()

    clear_state(user_id)
    add_xp(user_id, 5)

    bot.send_message(
        message.chat.id,
        f"📸 Screenshot به Trade #{trade_id} اضافه شد."
    )

    return True


# ============================================================
#                  CENTRAL MESSAGE ROUTER
# ============================================================

@bot.message_handler(
    content_types=[
        "text",
        "photo",
        "document",
        "voice"
    ]
)
def all_messages(message):

    ensure_user(message)

    # Admin reply system must be checked first
    if message.chat.id == ADMIN_CHAT_ID:
        if handle_admin_reply(message):
            return

        # Don't process admin as a normal user
        return

    user_id = message.from_user.id

    # Commands
    if message.content_type == "text":

        if message.text.startswith("/start"):
            start_command(message)
            return

        if message.text.startswith("/menu"):
            menu_command(message)
            return

        if message.text.startswith("/cancel"):
            cancel_message(message)
            return

        if message.text.startswith("/stats"):
            show_dashboard(message.chat.id)
            return

        if message.text.startswith("/journal"):
            show_journal_menu(message.chat.id)
            return

        if message.text.startswith("/risk"):
            start_risk_calculator(message)
            return

        if message.text.startswith("/prop"):
            show_prop_menu(message.chat.id)
            return

        if message.text.startswith("/academy"):
            show_academy(message.chat.id)
            return

        if message.text.startswith("/contact"):
            start_contact_admin(message)
            return

    state = get_state(user_id)
    current_state = state.get("state", "")

    # Contact Admin
    if current_state == "contact_admin":
        send_to_admin(message)
        return

    # Risk
    if current_state.startswith("risk_"):
        risk_process(message)
        return

    # Prop
    if current_state.startswith("prop_"):
        prop_process(message)
        return

    # Journal
    if current_state.startswith("journal_"):
        journal_process(message)
        return

    # Close Trade
    if current_state == "close_trade_pnl":
        close_trade_process(message)
        return

    # Psychology
    if current_state.startswith("psych_"):
        psychology_process(message)
        return

    # AI
    if current_state == "ai_question":
        if message.content_type == "text":
            ai_process(message)
        else:
            bot.send_message(
                message.chat.id,
                "🤖 لطفاً سؤال AI را به صورت متن ارسال کن."
            )
        return

    # Screenshot
    if current_state == "screenshot":
        screenshot_process(message)
        return

    # If no active state:
    # Send menu instead of blindly forwarding everything.
    show_main_menu(message.chat.id)


# ============================================================
#                    FLASK KEEP ALIVE
# ============================================================

@app.route("/")
def home():
    return "TradScale Bot is Alive! 🚀"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "TradScale"
    }


def run_web_server():
    port = int(
        os.environ.get("PORT", 8080)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


def keep_alive():
    t = Thread(
        target=run_web_server,
        daemon=True
    )

    t.start()


# ============================================================
#                       BOT COMMANDS
# ============================================================

def setup_bot_commands():
    commands = [
        types.BotCommand(
            "start",
            "شروع ربات"
        ),
        types.BotCommand(
            "menu",
            "منوی اصلی"
        ),
        types.BotCommand(
            "risk",
            "Risk Calculator"
        ),
        types.BotCommand(
            "journal",
            "Trading Journal"
        ),
        types.BotCommand(
            "prop",
            "Prop Challenge"
        ),
        types.BotCommand(
            "stats",
            "Dashboard"
        ),
        types.BotCommand(
            "academy",
            "Academy"
        ),
        types.BotCommand(
            "contact",
            "ارتباط با ادمین"
        ),
        types.BotCommand(
            "cancel",
            "لغو عملیات"
        )
    ]

    try:
        bot.set_my_commands(commands)
    except Exception as e:
        print("Could not set bot commands:", e)


# ============================================================
#                         START BOT
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
    print("AI:", "Enabled" if OPENAI_API_KEY else "Disabled")
    print("Bot is running...")

    bot.infinity_polling(
        skip_pending=True,
        allowed_updates=[
            "message",
            "callback_query"
        ]
    )
