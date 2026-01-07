# =========================================================
# Домовой бот верификации жильцов
# Версия: 1.0.2
# =========================================================

import os
import json
import logging
from datetime import datetime, timedelta, UTC

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
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x]

DATA_DIR = "data"
FILES_DIR = f"{DATA_DIR}/files"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"
BLACKLIST_FILE = f"{DATA_DIR}/blacklist.json"

BOT_VERSION = "1.0.2"

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

def is_admin(uid: int) -> bool:
    return uid in ADMINS

def is_blocked(uid: int) -> bool:
    blacklist = load_json(BLACKLIST_FILE, [])
    return uid in blacklist

def normalize_cadastre(text: str):
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) < 12:
        return None
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

def cleanup_old_applications():
    apps = load_json(APPLICATIONS_FILE, {})
    now = datetime.now(UTC)
    changed = False

    for uid in list(apps.keys()):
        created = datetime.fromisoformat(apps[uid]["created_at"])
        if now - created > timedelta(days=30):
            if apps[uid].get("file"):
                try:
                    os.remove(apps[uid]["file"])
                except FileNotFoundError:
                    pass
            del apps[uid]
            changed = True

    if changed:
        save_json(APPLICATIONS_FILE, apps)

# ================== ТЕКСТЫ ==================

HELP_TEXT = (
    "❓ *Помощь*\n\n"
    "📄 *Зачем нужен кадастровый номер?*\n"
    "Он используется только для подтверждения проживания.\n\n"
    "🔒 Данные не дают доступа к собственности.\n"
    "👤 Их видит только администратор дома."
)

STATUS_TEXT = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
    "blocked": "⛔ Заблокирован",
}

# ================== МЕНЮ ==================

USER_MENU = ReplyKeyboardMarkup(
    [
        ["📝 Подать новую заявку"],
        ["📄 Статус заявки"],
        ["❓ Помощь", "💬 Написать администратору"],
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Список заявок"],
        ["📊 Статистика"],
        ["📦 Экспорт JSON"],
    ],
    resize_keyboard=True
)

def admin_buttons(uid: str, blocked: bool):
    buttons = [
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{uid}"),
        ],
        [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{uid}")],
    ]
    if blocked:
        buttons.append([InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock:{uid}")])
    else:
        buttons.append([InlineKeyboardButton("⛔ Заблокировать", callback_data=f"block:{uid}")])
    return InlineKeyboardMarkup(buttons)

def confirm_cadastre_buttons():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Верно", callback_data="cad_ok"),
                InlineKeyboardButton("❌ Нет", callback_data="cad_no"),
            ]
        ]
    )

# ================== СТАРТ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_applications()
    context.user_data.clear()
    user = update.effective_user

    if is_admin(user.id):
        await update.message.reply_text(
            f"👋 Админ-панель\nВерсия бота: {BOT_VERSION}",
            reply_markup=ADMIN_MENU
        )
        return

    if is_blocked(user.id):
        await update.message.reply_text("⛔ Вы заблокированы администратором.")
        return

    context.user_data["step"] = "flat"
    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "🔒 Все данные используются только для проверки.\n\n"
        "🏠 Введите номер квартиры:",
        reply_markup=USER_MENU
    )

# ================== СООБЩЕНИЯ ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    step = context.user_data.get("step")

    apps = load_json(APPLICATIONS_FILE, {})

    # ---------- блокировка ----------
    if is_blocked(user.id) and not is_admin(user.id):
        await update.message.reply_text("⛔ Вы заблокированы администратором.")
        return

    # ---------- помощь ----------
    if text.lower().startswith("зачем") or text == "❓ Помощь":
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return

    # ---------- статус ----------
    if text == "📄 Статус заявки":
        app = apps.get(str(user.id))
        if not app:
            await update.message.reply_text("❌ Заявка не найдена.")
            return
        msg = (
            "📄 *Ваша заявка*\n\n"
            f"📌 Статус: {app['status']}"
        )
        if app.get("reject_reason"):
            msg += f"\n❗ Причина: {app['reject_reason']}"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # ---------- связь с админом ----------
    if text == "💬 Написать администратору":
        context.user_data["step"] = "contact"
        await update.message.reply_text("✉️ Напишите сообщение администратору:")
        return

    if step == "contact":
        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"💬 Сообщение от пользователя\n\n"
                f"👤 Имя: {user.full_name}\n"
                f"🔹 Ник: @{user.username}\n"
                f"🆔 ID: {user.id}\n\n"
                f"{text}",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")]]
                )
            )
        context.user_data.clear()
        await update.message.reply_text("✅ Сообщение отправлено.")
        return

    # ---------- новая заявка ----------
    if text == "📝 Подать новую заявку":
        context.user_data.clear()
        context.user_data["step"] = "flat"
        await update.message.reply_text("🏠 Введите номер квартиры:")
        return

    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cadastre"
        await update.message.reply_text(
            "📄 Введите кадастровый номер или отправьте фото / PDF документа:"
        )
        return

    if step == "cadastre":
        norm = normalize_cadastre(text)
        if not norm:
            await update.message.reply_text("❌ Не удалось распознать формат. Попробуйте ещё раз.")
            return
        context.user_data["cadastre"] = norm
        await update.message.reply_text(
            f"📄 Кадастровый номер:\n`{norm}`\n\nВерно?",
            parse_mode="Markdown",
            reply_markup=confirm_cadastre_buttons()
        )
        return

# ================== ФАЙЛЫ ==================

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    step = context.user_data.get("step")

    if step != "cadastre":
        return

    file = update.message.document or update.message.photo[-1]
    file_obj = await file.get_file()

    filename = f"{FILES_DIR}/{user.id}_{file.file_unique_id}"
    await file_obj.download_to_drive(filename)

    context.user_data["file"] = filename

    await update.message.reply_text("📎 Файл получен. Заявка будет отправлена администратору.")

    await submit_application(update, context)

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    apps = load_json(APPLICATIONS_FILE, {})
    blacklist = load_json(BLACKLIST_FILE, [])

    # ---------- подтверждение кадастра ----------
    if data == "cad_ok":
        await submit_application(query, context)
        return

    if data == "cad_no":
        context.user_data["step"] = "cadastre"
        await query.message.reply_text("Введите кадастровый номер заново:")
        return

    cmd, uid = data.split(":")

    # ---------- блокировка ----------
    if cmd == "block":
        if int(uid) not in blacklist:
            blacklist.append(int(uid))
            save_json(BLACKLIST_FILE, blacklist)
        await query.edit_message_text("⛔ Пользователь заблокирован.")
        return

    if cmd == "unblock":
        blacklist.remove(int(uid))
        save_json(BLACKLIST_FILE, blacklist)
        await query.edit_message_text("🔓 Пользователь разблокирован.")
        return

# ================== ОТПРАВКА ЗАЯВКИ ==================

async def submit_application(update, context):
    user = update.effective_user
    apps = load_json(APPLICATIONS_FILE, {})

    apps[str(user.id)] = {
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username,
        "flat": context.user_data.get("flat"),
        "cadastre": context.user_data.get("cadastre"),
        "file": context.user_data.get("file"),
        "status": STATUS_TEXT["pending"],
        "created_at": datetime.now(UTC).isoformat(),
    }

    save_json(APPLICATIONS_FILE, apps)

    for admin in ADMINS:
        await context.bot.send_message(
            admin,
            (
                "🆕 Новая заявка\n\n"
                f"👤 Имя: {user.full_name}\n"
                f"🔹 Ник: @{user.username}\n"
                f"🏠 Квартира: {apps[str(user.id)]['flat']}\n"
                f"📄 Кадастр: `{apps[str(user.id)]['cadastre'] or '—'}`\n"
                f"📌 Статус: {STATUS_TEXT['blocked'] if is_blocked(user.id) else STATUS_TEXT['pending']}"
            ),
            parse_mode="Markdown",
            reply_markup=admin_buttons(str(user.id), is_blocked(user.id))
        )

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
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
