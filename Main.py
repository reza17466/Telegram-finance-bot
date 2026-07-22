import os
import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I am alive!"

def run():
  app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
    
# تنظیمات لاگ برای عیب‌یابی
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# خواندن توکن و آیدی ادمین از متغیرهای محیطی سرور
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_CHAT_ID")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ['🚀 شروع آموزش', '💎 مشاوره اختصاصی'],
        ['❓ سوال از من', '📊 کانال تلگرام'],
        ['📺 یوتیوب', '📸 اینستاگرام'],
        ['👤 درباره من', '⚠️ هشدار ریسک']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "سلام! به دستیار هوشمند خوش آمدید.\nچطور می‌توانم به شما کمک کنم؟",
        reply_markup=reply_markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.message.from_user

    if text == '🚀 شروع آموزش':
        await update.message.reply_text("برای شروع آموزش‌ها، می‌توانید از پلی‌لیست یوتیوب ما استفاده کنید یا در کانال عضو شوید.")
    elif text == '💎 مشاوره اختصاصی':
        await update.message.reply_text("لطفاً نام و حوزه فعالیت خود را بفرستید تا برای وقت مشاوره با شما هماهنگ شود.")
    elif text == '📊 کانال تلگرام':
        await update.message.reply_text("لینک کانال ما: [لینک را اینجا بگذارید]")
    elif text == '❓ سوال از من':
        await update.message.reply_text("سوال خود را بنویسید، من آن را برای ادمین ارسال می‌کنم.")
    else:
        # فوروارد پیام برای ادمین (اگر آیدی ادمین ست شده باشد)
        if ADMIN_ID:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📩 پیام جدید از {user.first_name} (@{user.username}):\n\n{text}"
            )
            await update.message.reply_text("پیام شما دریافت شد و به زودی پاسخ داده می‌شود.")

if __name__ == '__main__':
    if not TOKEN:
        print("خطا: توکن ربات یافت نشد!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        print("ربات روشن شد...")
        keep_alive()
# خط بعدی همون دستور استارت قبلی خودت باشه
        app.run_polling()
