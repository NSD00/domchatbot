import os
import json
import logging
import pathlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== ЛОГИ ==================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================== КОНФИГ ==================
BOT_VERSION = "1.1.7"  # НЕ МЕНЯЮ, по твоей просьбе
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

DATA_DIR = "data"
FILES_DIR = f"{DATA_DIR}/files"
APPS_FILE = f"{DATA_DIR}/applications.json"
BLACKLIST_FILE = f"{DATA_DIR}/blacklist.json"

AUTO_CLEAN_DAYS = 30

# ================== УТИЛИТЫ ==================
def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FILES_DIR, exist_ok=True)

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_admin(uid: int) -> bool:
    return uid in ADMINS

def is_blocked(uid: int) -> bool:
    return uid in load_json(BLACKLIST_FILE, [])

def normalize_cadastre(text: str) -> Optional[str]:
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) < 12:
        return None
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

def cleanup_old_apps():
    apps = load_json(APPS_FILE, {})
    now = datetime.now(timezone.utc)
    changed = False

    for uid in list(apps.keys()):
        created = datetime.fromisoformat(apps[uid]["created_at"])
        if now - created > timedelta(days=AUTO_CLEAN_DAYS):
            file = apps[uid].get("file")
            if file and os.path.exists(file):
                try:
                    os.remove(file)
                except:
                    pass
            del apps[uid]
            changed = True

    if changed:
        save_json(APPS_FILE, apps)

# ================== ТЕКСТЫ ==================
HELP_TEXT = (
    "❓ *Зачем нужен кадастровый номер?*\n\n"
    "Кадастровый номер нужен для подтверждения\n"
    "проживания Вас в доме.\n\n"
    "📌 По кадастровому номеру *невозможно* узнать:\n"
    "🧾 ФИО, дату рождения, паспортные данные\n"
    "🔒 Данные *не дают* доступа к собственности\n"
    "👤 Их видит *только* администратор дома\n"
    "🗑 После сверки все данные *удаляются* автоматически!\n\n"
    "📋 *Процесс подачи заявки:*\n"
    "1. Введите номер квартиры\n"
    "2. Введите или отправьте файл с кадастровым номером\n"
    "3. Подтвердите данные\n"
    "4. Ожидайте рассмотрения администратором"
)

STATUS_TEXT = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
}

AUTO_HELP = ["зачем", "почему", "кадастр", "кадастров", "помощь"]

# ================== КЛАВИАТУРЫ ==================
USER_MENU = ReplyKeyboardMarkup(
    [
        ["📄 Статус заявки"],
        ["❓ Помощь", "📨 Написать админу"],
    ],
    resize_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Список заявок", "📊 Статистика"],
        ["📦 Экспорт JSON"],
    ],
    resize_keyboard=True,
)

def cad_confirm():
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Верно", callback_data="cad_ok"),
            InlineKeyboardButton("❌ Нет", callback_data="cad_no"),
        ]]
    )

def admin_buttons(uid: str, blocked: bool):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{uid}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{uid}"),
            ],
            [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{uid}")],
            [
                InlineKeyboardButton(
                    "🔓 Разблокировать" if blocked else "⛔ Заблокировать",
                    callback_data=f"{'unblock' if blocked else 'block'}:{uid}",
                )
            ],
        ]
    )

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_apps()
    context.user_data.clear()
    user = update.effective_user

    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text("🚫 Вы заблокированы.")
        return

    if is_admin(user.id):
        await update.message.reply_text(
            f"👑 Админ-панель\nВерсия: {BOT_VERSION}",
            reply_markup=ADMIN_MENU,
        )
        return

    context.user_data["step"] = "flat"
    await update.message.reply_text(
        "👋 Добро пожаловать!\n\nВведите номер квартиры:",
        reply_markup=USER_MENU,
    )

# ================== MESSAGE ==================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    text_l = text.lower()
    step = context.user_data.get("step")
    apps = load_json(APPS_FILE, {})

    if any(k in text_l for k in AUTO_HELP):
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return

    if not is_admin(user.id):
        if text == "❓ Помощь":
            await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
            return

        if text == "📄 Статус заявки":
            app = apps.get(str(user.id))
            if not app:
                await update.message.reply_text("❌ Заявка не найдена.")
            else:
                msg = f"📄 Статус: {app['status']}"
                if app.get("reject_reason"):
                    msg += f"\nПричина: {app['reject_reason']}"
                await update.message.reply_text(msg)
            return

        if text == "📨 Написать админу":
            context.user_data["step"] = "contact"
            await update.message.reply_text("✉️ Напишите сообщение администратору:")
            return

        if step == "contact":
            for admin in ADMINS:
                await context.bot.send_message(
                    admin,
                    f"✉️ Сообщение от пользователя\n"
                    f"👤 Имя: {user.full_name}\n"
                    f"🔹 Ник: @{user.username}\n"
                    f"ID: {user.id}\n\n{text}",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")]]
                    ),
                )
            context.user_data.clear()
            await update.message.reply_text("✅ Сообщение отправлено.")
            return

        if step == "flat":
            context.user_data["flat"] = text
            context.user_data["step"] = "cad"
            await update.message.reply_text(
                "Введите кадастровый номер или отправьте фото / PDF документа:"
            )
            return

        if step == "cad":
            norm = normalize_cadastre(text)
            if not norm:
                await update.message.reply_text(
                    "❌ Не удалось распознать.\nВведите номер ещё раз или отправьте файл."
                )
                return

            context.user_data["cad"] = norm
            await update.message.reply_text(
                f"📄 Кадастровый номер:\n`{norm}`\n\nВерно?",
                parse_mode="Markdown",
                reply_markup=cad_confirm(),
            )
            return

# ================== FILE ==================
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id):
        return

    if context.user_data.get("step") != "cad":
        await update.message.reply_text("⚠️ Сначала введите номер квартиры.")
        return

    file = update.message.document or update.message.photo[-1]
    tg_file = await file.get_file()

    ext = pathlib.Path(file.file_name).suffix if update.message.document else ".jpg"
    path = f"{FILES_DIR}/{user.id}_{int(datetime.now().timestamp())}{ext}"
    await tg_file.download_to_drive(path)

    apps = load_json(APPS_FILE, {})
    apps[str(user.id)] = {
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username,
        "flat": context.user_data.get("flat"),
        "file": path,
        "status": STATUS_TEXT["pending"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_json(APPS_FILE, apps)

    for admin in ADMINS:
        await context.bot.send_document(
            admin,
            document=open(path, "rb"),
            caption=(
                f"🆕 Новая заявка\n\n"
                f"👤 Имя: {user.full_name}\n"
                f"🔹 Ник: @{user.username}\n"
                f"🏠 Квартира: {context.user_data.get('flat')}\n"
                f"📎 Подтверждение: файл"
            ),
            reply_markup=admin_buttons(str(user.id), False),
        )

    context.user_data.clear()
    await update.message.reply_text(
        "📎 Файл получен.\n⏳ Заявка отправлена администратору.",
        reply_markup=USER_MENU,
    )

# ================== CALLBACK ==================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    apps = load_json(APPS_FILE, {})
    blacklist = load_json(BLACKLIST_FILE, [])

    if data == "cad_ok":
        u = q.from_user
        apps[str(u.id)] = {
            "user_id": u.id,
            "name": u.full_name,
            "username": u.username,
            "flat": context.user_data["flat"],
            "cadastre": context.user_data["cad"],
            "status": STATUS_TEXT["pending"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        save_json(APPS_FILE, apps)

        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"🆕 Новая заявка\n\n"
                f"👤 Имя: {u.full_name}\n"
                f"🔹 Ник: @{u.username}\n"
                f"🏠 Квартира: {context.user_data['flat']}\n"
                f"📄 Кадастр: `{context.user_data['cad']}`",
                parse_mode="Markdown",
                reply_markup=admin_buttons(str(u.id), False),
            )

        context.user_data.clear()

        await q.edit_message_text(
            "⏳ *Заявка отправлена администратору.*\n\n"
            "📌 Вы можете проверить статус заявки в меню.",
            parse_mode="Markdown",
        )

        await context.bot.send_message(
            u.id,
            "📄 Заявка принята.\n⏳ Ожидайте проверки администратором.",
            reply_markup=USER_MENU,
        )
        return

    if data == "cad_no":
        context.user_data.pop("cad", None)
        await q.edit_message_text("Введите кадастровый номер заново:")
        return

# ================== MAIN ==================
def main():
    ensure_dirs()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info(f"Бот запущен. Версия {BOT_VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
