import os
import telebot
from flask import Flask
from threading import Thread

# تنظیمات توکن و ادمین
API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = "638640702" # آیدی عددی خودت
bot = telebot.TeleBot(API_TOKEN)
app = Flask('')

# --- بخش وب‌سرور برای زنده نگه داشتن ---
@app.route('/')
def home():
    return "Bot is Alive!"

def run_web_server():
    # پورت رندر معمولاً روی 10000 یا 8080 است
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- بخش دستورات ربات ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = telebot.types.InlineKeyboardMarkup()
    btn1 = telebot.types.InlineKeyboardButton("آموزش نئوویو 📊", url="https://youtube.com/@Koroush_fx")
    btn2 = telebot.types.InlineKeyboardButton("کانال تلگرام 📢", url="https://t.me/Arshive_koroush_fx")
    btn3 = telebot.types.InlineKeyboardButton("مشاوره اختصاصی 👤", callback_data="contact_admin")
    markup.add(btn1)
    markup.add(btn2)
    markup.add(btn3)
    
    bot.reply_to(message, "سلام! به ربات آموزشی خوش آمدید. یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "contact_admin")
def contact_admin(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "لطفاً پیام خود را بنویسید تا برای ادمین ارسال شود.")

@bot.message_handler(func=lambda message: True)
def forward_to_admin(message):
    if str(message.chat.id) != ADMIN_CHAT_ID:
        bot.forward_message(ADMIN_CHAT_ID, message.chat.id, message.message_id)
        bot.reply_to(message, "پیام شما دریافت شد و به زودی پاسخ داده می‌شود.")

# --- اجرای همزمان ---
if __name__ == "__main__":
    keep_alive() # اول وب‌سرور در پس‌زمینه روشن می‌شود
    print("Bot is running...")
    bot.infinity_polling() # سپس ربات وارد چرخه بی‌پایان می‌شود
