import os
import json
import logging
from datetime import datetime, UTC

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")

DATA_DIR = "data"
FILES_DIR = f"{DATA_DIR}/files"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"

logging.basicConfig(level=logging.INFO)

# ================== УТИЛИТЫ ==================

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FILES_DIR, exist_ok=True)

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def normalize_cadastre(raw: str) -> str | None:
    # заменяем всё на цифры
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) < 12:
        return None
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

# ================== КНОПКИ ==================

USER_MENU = ReplyKeyboardMarkup(
    [["📄 Статус заявки"]],
    resize_keyboard=True
)

def cadastre_confirm_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Верно", callback_data="cad_ok"),
            InlineKeyboardButton("❌ Исправить", callback_data="cad_fix"),
        ]
    ])

# ================== START ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["step"] = "flat"

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Этот бот используется для подтверждения проживания.\n"
        "Данные используются **только для проверки**.\n\n"
        "Введите номер квартиры:",
        reply_markup=USER_MENU
    )

# ================== ТЕКСТ ==================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    step = context.user_data.get("step")

    # статус
    if text == "📄 Статус заявки":
        apps = load_json(APPLICATIONS_FILE, {})
        app = apps.get(str(update.effective_user.id))
        if not app:
            await update.message.reply_text("❌ Заявка не найдена.")
        else:
            await update.message.reply_text(f"📄 Статус: {app['status']}")
        return

    # шаг: квартира
    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cadastre"
        await update.message.reply_text(
            "Введите кадастровый номер **или** отправьте фото / PDF документа:"
        )
        return

    # шаг: кадастр текстом
    if step == "cadastre":
        normalized = normalize_cadastre(text)
        if not normalized:
            await update.message.reply_text(
                "❌ Не удалось распознать кадастровый номер.\n"
                "Попробуйте ещё раз или отправьте фото / PDF."
            )
            return

        context.user_data["cadastre_raw"] = text
        context.user_data["cadastre_norm"] = normalized
        context.user_data["step"] = "cad_confirm"

        await update.message.reply_text(
            f"📄 Кадастровый номер:\n\n"
            f"`{normalized}`\n\n"
            f"Верно?",
            parse_mode="Markdown",
            reply_markup=cadastre_confirm_kb()
        )
        return

# ================== ФАЙЛЫ ==================

async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step")
    if step != "cadastre":
        return

    file = None
    file_name = None

    if update.message.photo:
        file = update.message.photo[-1]
        file_name = f"{update.effective_user.id}_{file.file_id}.jpg"

    elif update.message.document:
        file = update.message.document
        file_name = f"{update.effective_user.id}_{file.file_id}_{file.file_name}"

    if not file:
        return

    tg_file = await file.get_file()
    path = f"{FILES_DIR}/{file_name}"
    await tg_file.download_to_drive(path)

    context.user_data["file_path"] = path
    context.user_data["step"] = "ready"

    await update.message.reply_text(
        "📎 Файл получен.\n"
        "Заявка будет отправлена администратору."
    )

    await save_application(update, context)

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cad_fix":
        context.user_data["step"] = "cadastre"
        await query.message.reply_text("Введите кадастровый номер заново:")
        return

    if query.data == "cad_ok":
        context.user_data["step"] = "ready"
        await query.message.reply_text("✅ Принято. Заявка отправляется.")
        await save_application(update, context)

# ================== СОХРАНЕНИЕ ==================

async def save_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    apps = load_json(APPLICATIONS_FILE, {})

    apps[str(user.id)] = {
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username,
        "flat": context.user_data.get("flat"),
        "cadastre": context.user_data.get("cadastre_norm"),
        "file": context.user_data.get("file_path"),
        "status": "⏳ На рассмотрении",
        "created_at": datetime.now(UTC).isoformat(),
    }

    save_json(APPLICATIONS_FILE, apps)
    context.user_data.clear()

    if update.callback_query:
        await update.callback_query.message.reply_text("⏳ Заявка отправлена.")
    else:
        await update.message.reply_text("⏳ Заявка отправлена.")

# ================== MAIN ==================

def main():
    ensure_dirs()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_files))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
