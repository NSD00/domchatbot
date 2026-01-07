# version 1.0.3

import os
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

DATA_DIR = Path("data")
APPLICATIONS_FILE = DATA_DIR / "applications.json"
FILES_DIR = DATA_DIR / "files"
BLACKLIST_FILE = DATA_DIR / "blacklist.json"

logging.basicConfig(level=logging.INFO)

VERSION = "1.0.3"

# ================== УТИЛИТЫ ==================

def ensure_dirs():
    DATA_DIR.mkdir(exist_ok=True)
    FILES_DIR.mkdir(exist_ok=True)

def load_json(path, default=None):
    if not path.exists():
        return default or {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_admin(uid: int) -> bool:
    return uid in ADMINS

def normalize_cadastre(text: str):
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) < 12:
        return None
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

def cleanup_old_applications():
    apps = load_json(APPLICATIONS_FILE, {})
    now = datetime.now(timezone.utc)
    changed = False
    for uid in list(apps.keys()):
        created = datetime.fromisoformat(apps[uid].get("created_at"))
        if now - created > timedelta(days=30):
            apps.pop(uid)
            changed = True
    if changed:
        save_json(APPLICATIONS_FILE, apps)

def cleanup_files():
    if FILES_DIR.exists():
        for f in FILES_DIR.iterdir():
            try:
                f.unlink()
            except:
                pass

def load_blacklist():
    return set(load_json(BLACKLIST_FILE, []))

def save_blacklist(blist):
    save_json(BLACKLIST_FILE, list(blist))

async def reply(update: Update, text: str, **kwargs):
    if update.message:
        await update.message.reply_text(text, **kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)

# ================== ТЕКСТЫ ==================

HELP_TEXT = (
    "❓ Зачем нужен кадастровый номер?\n"
    "📌 По кадастровому номеру *невозможно* узнать:\n"
    "🧾 ФИО, дату рождения, паспортные данные.\n"
    "🔒 Данные *не дают* доступа к собственности.\n"
    "👤 Их видит *только* администратор дома.\n"
    "🗑 После сверки, все данные *удаляются*!"
)

STATUS_TEXT = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
    "blocked": "⛔ Заблокирован"
}

AUTO_HELP_KEYWORDS = [
    "зачем",
    "зачем кадастров",
    "для чего кадастров",
    "кадастровый зачем",
]

# ================== МЕНЮ ==================

USER_MENU = ReplyKeyboardMarkup(
    [["📝 Подать заявку", "📄 Статус заявки"], ["❓ FAQ", "✉️ Написать админу"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup([["📋 Список заявок", "📊 Статистика"]], resize_keyboard=True)

def admin_buttons(uid: str, app_data):
    buttons = [
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{uid}")
        ],
        [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{uid}")]
    ]
    if app_data.get("blocked"):
        buttons.append([InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock:{uid}")])
    else:
        buttons.append([InlineKeyboardButton("⛔ Заблокировать", callback_data=f"block:{uid}")])
    return InlineKeyboardMarkup(buttons)

def admin_reply_templates(uid: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Проверка занимает время", callback_data=f"reply_tpl:wait:{uid}")],
        [InlineKeyboardButton("ℹ️ Кадастровый номер безопасен", callback_data=f"reply_tpl:safe:{uid}")]
    ])

def admin_reject_reasons(uid: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Недостаточно данных", callback_data=f"reject_reason:data:{uid}")],
        [InlineKeyboardButton("❌ Данные не подтверждены", callback_data=f"reject_reason:verify:{uid}")]
    ])

# ================== СТАРТ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_applications()
    cleanup_files()
    context.user_data.clear()
    user = update.effective_user

    if is_admin(user.id):
        await reply(update, f"👋 Админ-панель (v{VERSION})", reply_markup=ADMIN_MENU)
        return

    if str(user.id) in load_blacklist():
        await reply(update, "⛔ Вы заблокированы и не можете подавать заявки.")
        return

    context.user_data["step"] = "flat"
    await reply(update,
        "👋 Добро пожаловать!\n\nВведите номер квартиры для подачи заявки:",
        reply_markup=USER_MENU
    )

# ================== СОБЫТИЯ ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_applications()
    cleanup_files()
    user = update.effective_user
    text = update.message.text if update.message else ""
    step = context.user_data.get("step")
    apps = load_json(APPLICATIONS_FILE, {})

    # автоответ FAQ
    if text.lower() in AUTO_HELP_KEYWORDS:
        await reply(update, HELP_TEXT, parse_mode="Markdown")
        return

    # админ
    if is_admin(user.id):
        if text == "📋 Список заявок":
            if not apps:
                await reply(update, "Заявок нет.")
                return
            for uid, app in apps.items():
                blocked_status = "⛔" if app.get("blocked") else ""
                buttons = admin_buttons(uid, app)
                msg = (
                    f"👤 Имя: {app['name']}\n"
                    f"🔹 Ник: @{app.get('username','—')}\n"
                    f"🏠 Квартира: {app['flat']}\n"
                    f"📄 Кадастр: {app.get('cadastre','—')}\n"
                    f"📌 Статус: {app['status']} {blocked_status}"
                )
                await context.bot.send_message(user.id, msg, reply_markup=buttons)
            return

        if text == "📊 Статистика":
            total = len(apps)
            approved = sum(1 for a in apps.values() if a["status"] == STATUS_TEXT["approved"])
            rejected = sum(1 for a in apps.values() if a["status"] == STATUS_TEXT["rejected"])
            pending = sum(1 for a in apps.values() if a["status"] == STATUS_TEXT["pending"])
            blocked = sum(1 for a in apps.values() if a.get("blocked"))
            await reply(update,
                f"📊 Статистика заявок:\n"
                f"Всего: {total}\n"
                f"✅ Одобрено: {approved}\n"
                f"❌ Отклонено: {rejected}\n"
                f"⏳ На рассмотрении: {pending}\n"
                f"⛔ Заблокировано: {blocked}"
            )
            return

    # пользователь
    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cadastre"
        await reply(update, "Введите кадастровый номер или отправьте фото / PDF документа:")
        return

    if step == "cadastre":
        if update.message.document or update.message.photo:
            file_info = await update.message.document.get_file() if update.message.document else await update.message.photo[-1].get_file()
            file_path = FILES_DIR / f"{user.id}_{datetime.now().timestamp()}.dat"
            await file_info.download_to_drive(file_path)
            context.user_data["file"] = str(file_path)
            context.user_data["cadastre"] = None
            context.user_data["step"] = "confirm"
            await reply(update, "📎 Файл получен. Заявка будет отправлена администратору.\nПодтвердите отправку:")
            return
        else:
            norm = normalize_cadastre(text)
            if not norm:
                await reply(update, "❌ Неверный формат кадастра, попробуйте ещё раз.")
                return
            context.user_data["cadastre"] = norm
            context.user_data["step"] = "confirm"
            buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Верно", callback_data="submit_app"),
                    InlineKeyboardButton("❌ Не верно", callback_data="restart_app")
                ]
            ])
            await reply(update, f"📄 Кадастровый номер: {norm}\nПодтвердите:", reply_markup=buttons)
            return

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    apps = load_json(APPLICATIONS_FILE, {})
    blacklisted = load_blacklist()

    if query.data == "submit_app":
        user = query.from_user
        uid = str(user.id)
        apps[uid] = {
            "user_id": user.id,
            "name": user.full_name,
            "username": user.username,
            "flat": context.user_data.get("flat"),
            "cadastre": context.user_data.get("cadastre"),
            "file": context.user_data.get("file"),
            "status": STATUS_TEXT["pending"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        save_json(APPLICATIONS_FILE, apps)
        context.user_data.clear()
        # уведомление админов
        for admin in ADMINS:
            app_data = apps[uid]
            buttons = admin_buttons(uid, app_data)
            msg = (
                f"🆕 Новая заявка\n\n"
                f"👤 Имя: {app_data['name']}\n"
                f"🔹 Ник: @{app_data.get('username','—')}\n"
                f"🏠 Квартира: {app_data['flat']}\n"
                f"📄 Кадастр: {app_data.get('cadastre','—')}\n"
                f"📌 Статус: {app_data['status']}"
            )
            await context.bot.send_message(admin, msg, reply_markup=buttons)
        await query.edit_message_text("⏳ Заявка отправлена.")
        return

    if query.data == "restart_app":
        context.user_data.clear()
        await query.edit_message_text("Введите номер квартиры для подачи заявки:")
        return

# ================== MAIN ==================

def main():
    ensure_dirs()
    cleanup_old_applications()
    cleanup_files()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
