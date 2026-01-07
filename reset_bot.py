from telegram import Bot
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(token=BOT_TOKEN)

# Удаляем старые webhook и pending updates
bot.delete_webhook(drop_pending_updates=True)
print("✅ Старые webhook и updates удалены. Теперь можно безопасно запускать polling.")