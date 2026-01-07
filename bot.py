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

BOT_TOKEN = "8456384113:AAG3KchiRZkyRaVxVC3HqfiaIKLRAJM6j5c"
ADMINS = [5546945332]

DATA_DIR = "data"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"

# ================== ЛОГИ ==================

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

async def safe_reply(update: Update, text: str, **kwargs):
    if update.message:
        await update.message.reply_text(text, **kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)

# ================== МЕНЮ ==================

USER_MENU = ReplyKeyboardMarkup(
    [
        ["📄 Статус заявки"],
        ["🆘 Помощь", "✉️ Связь с админом"],
    ],
    resize_keyboard=True
)

def admin_buttons(user_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"approve:{user_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{user_id}")
        ],
        [
            InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user_id}")
        ]
    ])

# ================== СТАРТ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    if is_admin(update.effective_user.id):
        await safe_reply(update, "👋 Админ-панель")
        return

    context.user_data["step"] = "flat"
    await safe_reply(
        update,
        "👋 Добро пожаловать!\n\n"
        "Для доступа к домовому чату\n"
        "пожалуйста, заполните заявку.\n\n"
        "Введите номер квартиры:",
        reply_markup=USER_MENU
    )

# ================== ПОМОЩЬ / СТАТУС ==================

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(
        update,
        "🆘 Помощь\n\n"
        "• Данные используются только для проверки\n"
        "• Хранятся ограниченное время\n"
        "• Администратор видит имя и username",
    )

async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    apps = load_json(APPLICATIONS_FILE, {})
    app = apps.get(str(update.effective_user.id))

    if not app:
        await safe_reply(update, "❌ Активной заявки нет.")
        return

    await safe_reply(update, f"📄 Статус заявки: {app['status']}")

async def contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["return_step"] = context.user_data.get("step")
    context.user_data["step"] = "contact_admin"
    await safe_reply(update, "✉️ Напишите сообщение администратору:")

# ================== СООБЩЕНИЯ ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    step = context.user_data.get("step")

    if is_admin(user.id):
        if "reply_to" in context.user_data:
            uid = context.user_data.pop("reply_to")
            await context.bot.send_message(uid, f"✉️ Ответ администратора:\n\n{text}")
            await update.message.reply_text("✅ Ответ отправлен.")
        return

    if text == "🆘 Помощь":
        await show_help(update, context)
        return

    if text == "📄 Статус заявки":
        await show_status(update, context)
        return

    if text == "✉️ Связь с админом":
        await contact_admin(update, context)
        return

    if step == "contact_admin":
        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"✉️ Сообщение от пользователя\n\n"
                f"Имя: {user.full_name}\n"
                f"@{user.username}\n"
                f"ID: {user.id}\n\n{text}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")]
                ])
            )
        context.user_data["step"] = context.user_data.get("return_step")
        await safe_reply(update, "✅ Сообщение отправлено.")
        return

    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cadastre"
        await safe_reply(update, "Введите кадастровый номер:")
        return

    if step == "cadastre":
        norm = normalize_cadastre(text)
        if not norm:
            await safe_reply(update, "❌ Неверный формат. Попробуйте ещё раз.")
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
                f"👤 {user.full_name}\n"
                f"@{user.username}\n"
                f"ID: {user.id}\n"
                f"🏠 Квартира: {context.user_data['flat']}\n"
                f"📄 Кадастр: {norm}",
                reply_markup=admin_buttons(user.id)
            )

        context.user_data.clear()
        await safe_reply(update, "⏳ Заявка отправлена.")
        return

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, uid = query.data.split(":")
    apps = load_json(APPLICATIONS_FILE, {})
    app = apps.get(uid)

    if action == "reply":
        context.user_data["reply_to"] = int(uid)
        await query.message.reply_text("Введите ответ пользователю:")
        return

    if not app:
        await query.edit_message_text("Заявка не найдена.")
        return

    if action == "approve":
        app["status"] = "approved"
        await context.bot.send_message(int(uid), "✅ Ваша заявка одобрена.")

    if action == "reject":
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
