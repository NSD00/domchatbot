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
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x]

DATA_DIR = "data"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"

logging.basicConfig(level=logging.INFO)

# ================== УТИЛИТЫ ==================

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)

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

def normalize_cadastre(text: str):
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) < 12:
        return None
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

async def reply(update: Update, text: str, **kwargs):
    if update.message:
        await update.message.reply_text(text, **kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)

# ================== МЕНЮ ==================

USER_MENU = ReplyKeyboardMarkup(
    [
        ["📝 Подать заявку заново"],
        ["📄 Статус заявки"],
        ["🆘 Помощь", "✉️ Связь с админом"],
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Список заявок"],
    ],
    resize_keyboard=True
)

def admin_buttons(uid: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{uid}")
        ],
        [
            InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{uid}")
        ]
    ])

# ================== СТАРТ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    if is_admin(update.effective_user.id):
        await reply(update, "👋 Админ-панель", reply_markup=ADMIN_MENU)
        return

    context.user_data["step"] = "flat"
    await reply(
        update,
        "👋 Добро пожаловать!\n\n"
        "Этот бот нужен для подтверждения проживания\n"
        "и доступа к домовому чату.\n\n"
        "Введите номер квартиры:",
        reply_markup=USER_MENU
    )

# ================== ПОМОЩЬ / FAQ ==================

HELP_TEXT = (
    "🆘 *Помощь*\n\n"
    "❓ *Зачем кадастровый номер?*\n"
    "Он нужен для подтверждения, что вы действительно "
    "являетесь жильцом дома.\n\n"
    "🔒 *Безопасность*\n"
    "Данные видит только администратор и только для проверки.\n\n"
    "⏳ *Сколько хранятся данные?*\n"
    "Минимальное время, только до принятия решения.\n\n"
    "После одобрения вы получите ссылку на чат."
)

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, HELP_TEXT, parse_mode="Markdown")

async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    apps = load_json(APPLICATIONS_FILE, {})
    app = apps.get(str(update.effective_user.id))

    if not app:
        await reply(update, "❌ У вас нет активной заявки.")
        return

    await reply(update, f"📄 Статус заявки: *{app['status']}*", parse_mode="Markdown")

# ================== СООБЩЕНИЯ ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    step = context.user_data.get("step")

    # ---------- АДМИН ----------
    if is_admin(user.id):
        if text == "📋 Список заявок":
            apps = load_json(APPLICATIONS_FILE, {})
            if not apps:
                await reply(update, "Заявок нет.")
                return

            for uid, app in apps.items():
                await context.bot.send_message(
                    user.id,
                    f"👤 {app['name']}\n"
                    f"@{app['username']}\n"
                    f"🏠 Квартира: {app['flat']}\n"
                    f"📄 Кадастр: {app['cadastre']}\n"
                    f"📌 Статус: {app['status']}",
                    reply_markup=admin_buttons(uid)
                )
        elif "reply_to" in context.user_data:
            uid = context.user_data.pop("reply_to")
            await context.bot.send_message(uid, f"✉️ Ответ администратора:\n\n{text}")
            await reply(update, "✅ Ответ отправлен.")
        return

    # ---------- ПОЛЬЗОВАТЕЛЬ ----------
    if text == "🆘 Помощь":
        await show_help(update, context)
        return

    if text == "📄 Статус заявки":
        await show_status(update, context)
        return

    if text == "📝 Подать заявку заново":
        context.user_data.clear()
        context.user_data["step"] = "flat"
        await reply(update, "Введите номер квартиры:")
        return

    if text == "✉️ Связь с админом":
        context.user_data["step"] = "contact_admin"
        await reply(update, "Напишите сообщение администратору:")
        return

    if step == "contact_admin":
        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"✉️ Сообщение от пользователя\n\n"
                f"{user.full_name}\n"
                f"@{user.username}\n"
                f"ID: {user.id}\n\n{text}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")]
                ])
            )
        context.user_data["step"] = None
        await reply(update, "✅ Сообщение отправлено.")
        return

    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cadastre"
        await reply(update, "Введите кадастровый номер:")
        return

    if step == "cadastre":
        norm = normalize_cadastre(text)
        if not norm:
            await reply(update, "❌ Неверный формат. Попробуйте ещё раз.")
            return

        apps = load_json(APPLICATIONS_FILE, {})
        apps[str(user.id)] = {
            "user_id": user.id,
            "name": user.full_name,
            "username": user.username,
            "flat": context.user_data["flat"],
            "cadastre": norm,
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
        }
        save_json(APPLICATIONS_FILE, apps)

        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"🆕 Новая заявка\n\n"
                f"{user.full_name}\n"
                f"@{user.username}\n"
                f"🏠 Квартира: {context.user_data['flat']}\n"
                f"📄 Кадастр: {norm}",
                reply_markup=admin_buttons(user.id)
            )

        context.user_data.clear()
        await reply(update, "⏳ Заявка отправлена.")
        return

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, uid = query.data.split(":")
    apps = load_json(APPLICATIONS_FILE, {})

    if action == "reply":
        context.user_data["reply_to"] = int(uid)
        await query.message.reply_text("Введите ответ пользователю:")
        return

    app = apps.get(uid)
    if not app:
        await query.edit_message_text("Заявка не найдена.")
        return

    if action == "approve":
        app["status"] = "approved"
        await context.bot.send_message(int(uid), "✅ Ваша заявка одобрена.")

    elif action == "reject":
        app["status"] = "rejected"
        await context.bot.send_message(int(uid), "❌ Ваша заявка отклонена.")

    save_json(APPLICATIONS_FILE, apps)
    await query.edit_message_text("✔️ Решение сохранено.")

# ================== MAIN ==================

def main():
    ensure_dirs()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
