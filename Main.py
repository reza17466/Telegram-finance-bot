import os
import telebot
from flask import Flask
from threading import Thread

# تنظیمات توکن و آیدی ادمین
API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = 638640702  # حتماً به صورت عدد (بدون دابل کوتیشن) باشد

bot = telebot.TeleBot(API_TOKEN)
app = Flask('')

# --- بخش وب‌سرور برای زنده نگه داشتن ---
@app.route('/')
def home():
    return "Bot is Alive!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- دستور شروع ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = telebot.types.InlineKeyboardMarkup()
    btn1 = telebot.types.InlineKeyboardButton("آموزش نئوویو 📊", url="https://youtube.com/@Koroush_fx")
    btn2 = telebot.types.InlineKeyboardButton("کانال تلگرام 📢", url="https://t.me/Arshive_koroush_fx")
    btn3 = telebot.types.InlineKeyboardButton("مشاوره اختصاصی 👤", callback_data="contact_admin")
    markup.add(btn1, btn2)
    markup.add(btn3)
    
    bot.reply_to(message, "سلام! به ربات آموزشی خوش آمدید. برای ارتباط با ما یا دریافت مشاوره، روی گزینه زیر کلیک کنید:", reply_markup=markup)

# مدیریت دکمه شیشه‌ای مشاوره
@bot.callback_query_handler(func=lambda call: call.data == "contact_admin")
def contact_admin_callback(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "💬 لطفاً سوال یا پیام خود را همینجا بفرستید تا مستقیماً به ادمین ارسال شود:")

# --- سیستم فوروارد پیام کاربران به ادمین ---
@bot.message_handler(func=lambda message: True)
def forward_to_admin(message):
    # اگر پیام از طرف خود ادمین نبود، فوروارد کن
    if message.chat.id != ADMIN_CHAT_ID:
        # فوروارد هوشمند پیام کاربر به ادمین
        bot.forward_message(ADMIN_CHAT_ID, message.chat.id, message.message_id)
        # اطلاع به کاربر که پیامش رفت
        bot.reply_to(message, "✅ پیام شما با موفقیت به ادمین ارسال شد. به زودی پاسخ داده می‌شود.")
    else:
        # اگر ادمین ریپلای کرد به یک پیام فوروارد شده (برای پاسخ به کاربر)
        if message.reply_to_message and message.reply_to_message.forward_from:
            target_user_id = message.reply_to_message.forward_from.id
            bot.send_message(target_user_id, f"پاسخ ادمین:\n\n{message.text}")
            bot.reply_to(message, "📤 پاسخ شما برای کاربر ارسال شد.")

# --- اجرای همزمان ---
if __name__ == "__main__":
    keep_alive()
    print("Bot is running...")
    bot.infinity_polling()
