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

# ================== ВЕРСИЯ ==================
BOT_VERSION = "1.3.0-final"

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x]

DATA_DIR = "data"
FILES_DIR = f"{DATA_DIR}/files"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"
BLACKLIST_FILE = f"{DATA_DIR}/blacklist.json"

AUTO_CLEAN_DAYS = 30

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
        if now - created > timedelta(days=AUTO_CLEAN_DAYS):
            file_path = apps[uid].get("file_path")
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            del apps[uid]
            changed = True

    if changed:
        save_json(APPLICATIONS_FILE, apps)

async def reply(update: Update, text: str, **kwargs):
    if update.message:
        await update.message.reply_text(text, **kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)

# ================== ТЕКСТЫ ==================

HELP_TEXT = (
    "❓ *Зачем нужен кадастровый номер?*\n\n"
    "Он используется *только* для подтверждения, "
    "что вы проживаете в доме.\n\n"
    "🔒 Он не даёт доступа к собственности\n"
    "👤 Видит только администратор дома"
)

AUTO_HELP_KEYWORDS = ["зачем", "почему", "для чего", "кадастр"]

STATUS_TEXT = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
}

# ================== МЕНЮ ==================

USER_MENU = ReplyKeyboardMarkup(
    [
        ["📄 Статус заявки"],
        ["❓ Помощь", "✉️ Связь с админом"],
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["📋 Список заявок", "📊 Статистика"]],
    resize_keyboard=True
)

def admin_buttons(uid: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{uid}")
        ],
        [
            InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{uid}")
        ],
        [
            InlineKeyboardButton("🚫 Заблокировать", callback_data=f"block:{uid}"),
            InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock:{uid}")
        ]
    ])

def confirm_cadastre_buttons():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Верно", callback_data="cad_ok"),
            InlineKeyboardButton("❌ Нет", callback_data="cad_no"),
        ]
    ])

# ================== START ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_applications()
    context.user_data.clear()
    user = update.effective_user

    if is_blocked(user.id):
        await reply(update, "🚫 Вы заблокированы администратором.")
        return

    if is_admin(user.id):
        await reply(update, f"👋 Админ-панель\nВерсия: {BOT_VERSION}", reply_markup=ADMIN_MENU)
        return

    context.user_data["step"] = "flat"
    await reply(
        update,
        "👋 Добро пожаловать!\n\n"
        "Введите номер квартиры:",
        reply_markup=USER_MENU
    )

# ================== MESSAGE ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text_raw = update.message.text
    text = text_raw.lower()

    if is_blocked(user.id):
        return

    if any(k in text for k in AUTO_HELP_KEYWORDS):
        await reply(update, HELP_TEXT, parse_mode="Markdown")
        return

    apps = load_json(APPLICATIONS_FILE, {})
    step = context.user_data.get("step")

    # ---------- ADMIN ----------
    if is_admin(user.id):
        if text == "📋 список заявок":
            for uid, app in apps.items():
                msg = (
                    f"👤 {app['name']} @{app.get('username')}\n"
                    f"🏠 Квартира: {app['flat']}\n"
                    f"📄 Кадастр:\n`{app.get('cadastre','—')}`\n"
                    f"📌 Статус: {app['status']}"
                )
                await context.bot.send_message(
                    user.id,
                    msg,
                    parse_mode="Markdown",
                    reply_markup=admin_buttons(uid)
                )
            return

    # ---------- USER ----------
    if step == "flat":
        context.user_data["flat"] = text_raw
        context.user_data["step"] = "cadastre_or_file"
        await reply(update, "Введите кадастровый номер или отправьте фото / PDF документа:")
        return

    if step == "cadastre_or_file":
        norm = normalize_cadastre(text_raw)
        if not norm:
            await reply(update, "❌ Не удалось распознать номер. Попробуйте ещё раз или отправьте файл.")
            return

        context.user_data["cadastre"] = norm
        await reply(
            update,
            f"📄 Кадастровый номер:\n`{norm}`\n\nВерно?",
            parse_mode="Markdown",
            reply_markup=confirm_cadastre_buttons()
        )
        return

# ================== FILES ==================

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_blocked(user.id):
        return

    file = update.message.document or update.message.photo[-1]
    tg_file = await file.get_file()

    filename = f"{user.id}_{int(datetime.now().timestamp())}"
    path = f"{FILES_DIR}/{filename}"

    await tg_file.download_to_drive(path)

    apps = load_json(APPLICATIONS_FILE, {})
    apps[str(user.id)] = {
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username,
        "flat": context.user_data.get("flat"),
        "file_path": path,
        "status": STATUS_TEXT["pending"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    save_json(APPLICATIONS_FILE, apps)

    for admin in ADMINS:
        await context.bot.send_document(
            admin,
            document=open(path, "rb"),
            caption=f"🆕 Заявка\n👤 {user.full_name} @{user.username}\n🏠 {context.user_data.get('flat')}",
            reply_markup=admin_buttons(str(user.id))
        )

    context.user_data.clear()
    await reply(update, "📎 Файл получен.\n⏳ Заявка будет отправлена администратору.")

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    apps = load_json(APPLICATIONS_FILE, {})
    blacklist = load_json(BLACKLIST_FILE, [])

    if data == "cad_ok":
        user = query.from_user
        app = {
            "user_id": user.id,
            "name": user.full_name,
            "username": user.username,
            "flat": context.user_data["flat"],
            "cadastre": context.user_data["cadastre"],
            "status": STATUS_TEXT["pending"],
            "created_at": datetime.now(UTC).isoformat(),
        }
        apps[str(user.id)] = app
        save_json(APPLICATIONS_FILE, apps)

        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"🆕 Новая заявка\n👤 {user.full_name} @{user.username}\n🏠 {app['flat']}\n📄 `{app['cadastre']}`",
                parse_mode="Markdown",
                reply_markup=admin_buttons(str(user.id))
            )

        context.user_data.clear()
        await query.edit_message_text("⏳ Заявка отправлена.")
        return

    if data == "cad_no":
        context.user_data.pop("cadastre", None)
        await query.edit_message_text("Введите кадастровый номер заново:")
        return

    action, uid = data.split(":")
    app = apps.get(uid)

    if action == "block":
        if int(uid) not in blacklist:
            blacklist.append(int(uid))
            save_json(BLACKLIST_FILE, blacklist)
        await query.edit_message_text("🚫 Пользователь заблокирован.")
        return

    if action == "unblock":
        if int(uid) in blacklist:
            blacklist.remove(int(uid))
            save_json(BLACKLIST_FILE, blacklist)
        await query.edit_message_text("🔓 Пользователь разблокирован.")
        return

    if action == "approve":
        app["status"] = STATUS_TEXT["approved"]
        save_json(APPLICATIONS_FILE, apps)
        await context.bot.send_message(int(uid), "✅ Ваша заявка одобрена.")
        await query.edit_message_text("✅ Заявка одобрена.")
        return

    if action == "reject":
        app["status"] = STATUS_TEXT["rejected"]
        save_json(APPLICATIONS_FILE, apps)
        await context.bot.send_message(int(uid), "❌ Ваша заявка отклонена.")
        await query.edit_message_text("❌ Заявка отклонена.")
        return

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
