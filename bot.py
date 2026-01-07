import os
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

ADMINS = [
    int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip()
]

DATA_DIR = "data"
TEMP_DIR = "temp/files"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"

APPLICATION_TTL_DAYS = 7

# ================== ЛОГИ ==================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ================== FASTAPI ==================

fastapi_app = FastAPI()
application: Application | None = None

# ================== УТИЛИТЫ ==================

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def normalize_cadastre(text: str):
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 12:
        return None
    return f"{digits[0:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

def cleanup_old_applications():
    apps = load_json(APPLICATIONS_FILE, {})
    now = datetime.now(timezone.utc)
    changed = False

    for uid in list(apps.keys()):
        created = datetime.fromisoformat(apps[uid]["created_at"])
        if now - created > timedelta(days=APPLICATION_TTL_DAYS):
            for f in apps[uid].get("files", []):
                try:
                    os.remove(f["path"])
                except Exception:
                    pass
            del apps[uid]
            changed = True

    if changed:
        save_json(APPLICATIONS_FILE, apps)

# ================== КОМАНДЫ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["step"] = "flat"
    await update.message.reply_text("🏠 Введите номер квартиры:")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    apps = load_json(APPLICATIONS_FILE, {})
    app = apps.get(str(update.effective_user.id))
    if not app:
        await update.message.reply_text("❌ Заявка не найдена.")
        return
    await update.message.reply_text(f"📄 Статус: {app['status']}")

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Данные используются только для проверки\n"
        "⏳ Хранятся не более 7 дней"
    )

async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["step"] = "contact_admin"
    await update.message.reply_text("✉️ Напишите сообщение администратору")

# ================== СООБЩЕНИЯ ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step")

    if step == "flat":
        context.user_data["flat"] = update.message.text.strip()
        context.user_data["step"] = "cadastre"
        await update.message.reply_text("Введите кадастровый номер:")
        return

    if step == "cadastre":
        norm = normalize_cadastre(update.message.text)
        if not norm:
            await update.message.reply_text("❌ Не удалось распознать номер")
            return

        context.user_data["cadastre_norm"] = norm
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да", callback_data="cad_ok"),
                InlineKeyboardButton("❌ Нет", callback_data="cad_no"),
            ]
        ])
        await update.message.reply_text(f"`{norm}`\nВерно?", reply_markup=kb, parse_mode="Markdown")

    if step == "contact_admin":
        for admin in ADMINS:
            await context.bot.send_message(admin, update.message.text)
        context.user_data.clear()
        await update.message.reply_text("✅ Отправлено")

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cad_ok":
        await submit_application(query, context)

    if query.data == "cad_no":
        context.user_data["step"] = "cadastre"
        await query.edit_message_text("Введите номер ещё раз")

# ================== ЗАЯВКА ==================

async def submit_application(source, context):
    user_id = source.from_user.id
    apps = load_json(APPLICATIONS_FILE, {})

    apps[str(user_id)] = {
        "flat": context.user_data.get("flat"),
        "cadastre": context.user_data.get("cadastre_norm"),
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    save_json(APPLICATIONS_FILE, apps)

    for admin in ADMINS:
        await context.bot.send_message(
            admin,
            f"🆕 Заявка\n👤 {user_id}\n🏠 {apps[str(user_id)]['flat']}\n📄 {apps[str(user_id)]['cadastre']}"
        )

    await source.edit_message_text("⏳ Заявка отправлена")
    context.user_data.clear()

# ================== WEBHOOK ==================

@fastapi_app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

# ================== START ==================

def setup_bot():
    global application
    ensure_dirs()
    cleanup_old_applications()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("faq", faq))
    application.add_handler(CommandHandler("contact", contact))

    application.add_handler(CallbackQueryHandler(callbacks))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Render вызывает это автоматически
setup_bot()
