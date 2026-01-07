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
BOT_VERSION = "1.4.1-stable"

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x]

DATA_DIR = "data"
FILES_DIR = f"{DATA_DIR}/files"
APPS_FILE = f"{DATA_DIR}/applications.json"
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
    return uid in load_json(BLACKLIST_FILE, [])

def normalize_cadastre(text: str):
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) < 12:
        return None
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

def cleanup_old_apps():
    apps = load_json(APPS_FILE, {})
    now = datetime.now(UTC)
    changed = False

    for uid in list(apps.keys()):
        created = datetime.fromisoformat(apps[uid]["created_at"])
        if now - created > timedelta(days=AUTO_CLEAN_DAYS):
            if apps[uid].get("file"):
                try:
                    os.remove(apps[uid]["file"])
                except:
                    pass
            del apps[uid]
            changed = True

    if changed:
        save_json(APPS_FILE, apps)

# ================== ТЕКСТЫ ==================

HELP_TEXT = (
    "❓ *Помощь*\n\n"
    "Кадастровый номер нужен *только* для подтверждения проживания.\n"
    "🔒 Он безопасен и не даёт доступа к собственности.\n"
    "👤 Его видит только администратор дома."
)

AUTO_HELP = ["зачем", "почему", "кадастр", "кадастров"]

STATUS_TEXT = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
}

# ================== МЕНЮ ==================

USER_MENU = ReplyKeyboardMarkup(
    [
        ["📄 Статус заявки"],
        ["❓ Помощь", "📨 Написать администратору"],
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Заявки", "📊 Статистика"],
        ["📦 Экспорт JSON"],
    ],
    resize_keyboard=True
)

def admin_buttons(uid: str, blocked: bool):
    row3 = (
        InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock:{uid}")
        if blocked
        else InlineKeyboardButton("🚫 Заблокировать", callback_data=f"block:{uid}")
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{uid}")
        ],
        [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{uid}")],
        [row3]
    ])

def cad_confirm():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Верно", callback_data="cad_ok"),
            InlineKeyboardButton("❌ Нет", callback_data="cad_no"),
        ]
    ])

# ================== START ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_apps()
    context.user_data.clear()
    user = update.effective_user

    if is_blocked(user.id):
        await update.message.reply_text("🚫 Вы заблокированы.")
        return

    if is_admin(user.id):
        await update.message.reply_text(
            f"👋 Админ-панель\nВерсия: {BOT_VERSION}",
            reply_markup=ADMIN_MENU
        )
        return

    context.user_data["step"] = "flat"
    await update.message.reply_text(
        "👋 Добро пожаловать!\n\nВведите номер квартиры:",
        reply_markup=USER_MENU
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

    # ---------- USER ----------
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

        if text == "📨 Написать администратору":
            context.user_data["step"] = "contact"
            await update.message.reply_text("Напишите сообщение администратору:")
            return

        if step == "contact":
            for admin in ADMINS:
                await context.bot.send_message(
                    admin,
                    f"✉️ Сообщение от пользователя\n"
                    f"👤 {user.full_name}\n"
                    f"🔹 Ник: @{user.username}\n"
                    f"ID: {user.id}\n\n{text}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")]
                    ])
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
                    "❌ Не удалось распознать.\n"
                    "Введите номер ещё раз или отправьте фото / PDF."
                )
                return

            context.user_data["cad"] = norm
            await update.message.reply_text(
                f"📄 Кадастровый номер:\n`{norm}`\n\nВерно?",
                parse_mode="Markdown",
                reply_markup=cad_confirm()
            )
            return

    # ---------- ADMIN ----------
    if is_admin(user.id):
        if text == "📋 Заявки":
            for uid, app in apps.items():
                blocked = is_blocked(int(uid))
                msg = (
                    f"👤 {app['name']}\n"
                    f"🔹 Ник: @{app.get('username')}\n"
                    f"🏠 Квартира: {app.get('flat')}\n"
                    f"📄 Кадастр:\n`{app.get('cadastre','—')}`\n"
                    f"📌 Статус: {app['status']}"
                )
                await context.bot.send_message(
                    user.id,
                    msg,
                    parse_mode="Markdown",
                    reply_markup=admin_buttons(uid, blocked)
                )
            return

        if text == "📊 Статистика":
            total = len(apps)
            p = sum(1 for a in apps.values() if a["status"].startswith("⏳"))
            a = sum(1 for a in apps.values() if a["status"].startswith("✅"))
            r = sum(1 for a in apps.values() if a["status"].startswith("❌"))
            await update.message.reply_text(
                f"📊 Статистика\n\n"
                f"Всего: {total}\n"
                f"⏳ Ожидают: {p}\n"
                f"✅ Приняты: {a}\n"
                f"❌ Отклонены: {r}"
            )
            return

        if text == "📦 Экспорт JSON":
            await context.bot.send_document(
                user.id,
                document=open(APPS_FILE, "rb"),
                caption="📦 Экспорт заявок"
            )
            return

# ================== FILE ==================

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id):
        return

    file = update.message.document or update.message.photo[-1]
    tg_file = await file.get_file()

    path = f"{FILES_DIR}/{user.id}_{int(datetime.now().timestamp())}"
    await tg_file.download_to_drive(path)

    apps = load_json(APPS_FILE, {})
    apps[str(user.id)] = {
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username,
        "flat": context.user_data.get("flat"),
        "file": path,
        "status": STATUS_TEXT["pending"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    save_json(APPS_FILE, apps)

    for admin in ADMINS:
        await context.bot.send_photo(
            admin,
            photo=open(path, "rb"),
            caption=(
                f"🆕 Заявка\n"
                f"👤 {user.full_name}\n"
                f"🔹 Ник: @{user.username}\n"
                f"🏠 Квартира: {context.user_data.get('flat')}"
            ),
            reply_markup=admin_buttons(str(user.id), False)
        )

    context.user_data.clear()
    await update.message.reply_text(
        "📎 Файл получен.\n⏳ Заявка отправлена администратору."
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
            "created_at": datetime.now(UTC).isoformat(),
        }
        save_json(APPS_FILE, apps)

        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"🆕 Новая заявка\n"
                f"👤 {u.full_name}\n"
                f"🔹 Ник: @{u.username}\n"
                f"🏠 {context.user_data['flat']}\n"
                f"📄 `{context.user_data['cad']}`",
                parse_mode="Markdown",
                reply_markup=admin_buttons(str(u.id), False)
            )

        context.user_data.clear()
        await q.edit_message_text("⏳ Заявка отправлена.")
        return

    if data == "cad_no":
        context.user_data.pop("cad", None)
        await q.edit_message_text("Введите кадастровый номер заново:")
        return

    action, uid = data.split(":")

    if action == "block":
        if int(uid) not in blacklist:
            blacklist.append(int(uid))
            save_json(BLACKLIST_FILE, blacklist)
        await q.edit_message_text("🚫 Пользователь заблокирован.")
        return

    if action == "unblock":
        if int(uid) in blacklist:
            blacklist.remove(int(uid))
            save_json(BLACKLIST_FILE, blacklist)
        await q.edit_message_text("🔓 Пользователь разблокирован.")
        return

    if action == "approve":
        apps[uid]["status"] = STATUS_TEXT["approved"]
        save_json(APPS_FILE, apps)
        await context.bot.send_message(int(uid), "✅ Ваша заявка одобрена.")
        await q.edit_message_text("✅ Заявка одобрена.")
        return

    if action == "reject":
        apps[uid]["status"] = STATUS_TEXT["rejected"]
        save_json(APPS_FILE, apps)
        await context.bot.send_message(int(uid), "❌ Ваша заявка отклонена.")
        await q.edit_message_text("❌ Заявка отклонена.")
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
