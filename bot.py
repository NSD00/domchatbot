# ===============================
# Домовой бот верификации
# Версия: 1.0.2
# PTB: 22.x
# ===============================

import os
import json
import logging
from datetime import datetime, timedelta, UTC
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ========= КОНФИГ =========

BOT_VERSION = "1.0.2"
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x]

DATA_DIR = "data"
FILES_DIR = f"{DATA_DIR}/files"
APPS_FILE = f"{DATA_DIR}/applications.json"
BANS_FILE = f"{DATA_DIR}/bans.json"

AUTO_CLEAN_DAYS = 30

logging.basicConfig(level=logging.INFO)

# ========= УТИЛИТЫ =========

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

def is_admin(uid: int) -> bool:
    return uid in ADMINS

def normalize_cadastre(text: str) -> str | None:
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) < 12:
        return None
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

def now():
    return datetime.now(UTC)

# ========= КЛАВИАТУРЫ =========

USER_MENU = ReplyKeyboardMarkup(
    [
        ["📝 Подать новую заявку"],
        ["📄 Статус заявки"],
        ["❓ Помощь", "✉️ Связь с администратором"],
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Заявки", "📊 Статистика"],
        ["🔄 Перезапустить бота"],
    ],
    resize_keyboard=True
)

def admin_actions(uid, banned):
    buttons = [
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{uid}")
        ],
        [
            InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{uid}")
        ]
    ]
    if banned:
        buttons.append([InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock:{uid}")])
    else:
        buttons.append([InlineKeyboardButton("🚫 Заблокировать", callback_data=f"block:{uid}")])
    return InlineKeyboardMarkup(buttons)

def confirm_cadastre_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Верно", callback_data="cad_yes"),
            InlineKeyboardButton("❌ Нет", callback_data="cad_no")
        ]
    ])

# ========= ТЕКСТЫ =========

HELP_TEXT = (
    "❓ *Помощь*\n\n"
    "Кадастровый номер используется *только для проверки*, "
    "что вы проживаете в доме.\n\n"
    "Он *не даёт доступа* к собственности и *безопасен*.\n\n"
    "Вы можете отправить:\n"
    "• текст\n"
    "• фото документа\n"
    "• PDF файл"
)

AUTO_HELP = ["зачем", "кадастр", "кадастров"]

STATUS_MAP = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
}

# ========= СТАРТ =========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id

    if is_admin(uid):
        await update.message.reply_text(
            f"👋 Админ-панель\nВерсия {BOT_VERSION}",
            reply_markup=ADMIN_MENU
        )
        return

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "ℹ️ Все данные используются *только для проверки*.\n\n"
        "Введите номер квартиры:",
        reply_markup=USER_MENU,
        parse_mode="Markdown"
    )
    context.user_data["step"] = "flat"

# ========= СООБЩЕНИЯ =========

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    text = update.message.text if update.message else ""

    bans = load_json(BANS_FILE, {})
    if str(uid) in bans:
        await update.message.reply_text("🚫 Вы заблокированы администратором.")
        return

    # авто-помощь
    if any(k in text.lower() for k in AUTO_HELP):
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return

    step = context.user_data.get("step")

    if text == "📝 Подать новую заявку":
        context.user_data.clear()
        await update.message.reply_text("Введите номер квартиры:")
        context.user_data["step"] = "flat"
        return

    if text == "❓ Помощь":
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return

    if text == "📄 Статус заявки":
        apps = load_json(APPS_FILE, {})
        app = apps.get(str(uid))
        if not app:
            await update.message.reply_text("❌ Заявка не найдена.")
        else:
            msg = f"📄 Статус: {STATUS_MAP[app['status']]}"
            if app.get("reason"):
                msg += f"\nПричина: {app['reason']}"
            await update.message.reply_text(msg)
        return

    if text == "✉️ Связь с администратором":
        context.user_data["contact_admin"] = True
        await update.message.reply_text("✍️ Напишите сообщение администратору:")
        return

    if context.user_data.get("contact_admin"):
        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"✉️ Сообщение от пользователя\n"
                f"Имя: {user.full_name}\n"
                f"Ник: @{user.username}\n\n{text}"
            )
        await update.message.reply_text("✅ Сообщение отправлено.")
        context.user_data.pop("contact_admin")
        return

    # шаги заявки
    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cadastre"
        await update.message.reply_text(
            "Введите кадастровый номер или отправьте фото / PDF документа:"
        )
        return

    if step == "cadastre":
        norm = normalize_cadastre(text)
        if not norm:
            await update.message.reply_text("❌ Не удалось распознать кадастр.")
            return
        context.user_data["cadastre_raw"] = text
        context.user_data["cadastre"] = norm
        await update.message.reply_text(
            f"📄 Кадастровый номер:\n`{norm}`\n\nВерно?",
            reply_markup=confirm_cadastre_keyboard(),
            parse_mode="Markdown"
        )
        return

# ========= ФАЙЛЫ =========

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    if context.user_data.get("step") != "cadastre":
        return

    file = None
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        ext = "jpg"
    elif update.message.document:
        file = await update.message.document.get_file()
        ext = update.message.document.file_name.split(".")[-1]
    else:
        return

    filename = f"{uid}_{int(now().timestamp())}.{ext}"
    path = f"{FILES_DIR}/{filename}"
    await file.download_to_drive(path)

    context.user_data["file"] = path
    await update.message.reply_text("📎 Файл получен. Заявка будет отправлена администратору.")
    await submit_application(update, context)

# ========= CALLBACK =========

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cad_yes":
        await submit_application(update, context)
        return

    if data == "cad_no":
        context.user_data.pop("cadastre", None)
        await query.message.reply_text("Введите кадастровый номер заново:")
        return

# ========= ОТПРАВКА ЗАЯВКИ =========

async def submit_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    apps = load_json(APPS_FILE, {})

    apps[str(uid)] = {
        "user_id": uid,
        "name": user.full_name,
        "username": user.username,
        "flat": context.user_data.get("flat"),
        "cadastre": context.user_data.get("cadastre"),
        "file": context.user_data.get("file"),
        "status": "pending",
        "created": now().isoformat(),
    }

    save_json(APPS_FILE, apps)

    bans = load_json(BANS_FILE, {})
    banned = str(uid) in bans

    for admin in ADMINS:
        text = (
            f"🆕 Новая заявка\n\n"
            f"👤 Имя: {user.full_name}\n"
            f"🔹 Ник: @{user.username}\n"
            f"🏠 Квартира: {apps[str(uid)]['flat']}\n"
            f"📄 Кадастр: {apps[str(uid)]['cadastre'] or '—'}"
        )
        await context.bot.send_message(
            admin,
            text,
            reply_markup=admin_actions(uid, banned)
        )
        if apps[str(uid)].get("file"):
            await context.bot.send_document(admin, open(apps[str(uid)]["file"], "rb"))

    context.user_data.clear()
    await update.message.reply_text("⏳ Заявка отправлена на проверку.")

# ========= MAIN =========

def main():
    ensure_dirs()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
