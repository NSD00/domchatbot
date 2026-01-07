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

BOT_TOKEN = "8456384113:AAG3KchiRZkyRaVxVC3HqfiaIKLRAJM6j5c"

ADMINS = [5546945332]

DATA_DIR = "data"
TEMP_DIR = "temp/files"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"

APPLICATION_TTL_DAYS = 7

# ================== ЛОГИ ==================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

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
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) < 12:
        return None
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

async def send(update: Update, text: str, **kwargs):
    if update.message:
        await update.message.reply_text(text, **kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)

# ================== МЕНЮ ==================

USER_MENU = ReplyKeyboardMarkup(
    [
        ["📄 Статус заявки"],
        ["❓ FAQ", "✉️ Связь с админом"],
    ],
    resize_keyboard=True
)

def admin_buttons(user_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"admin_approve:{user_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"admin_reject:{user_id}")
        ],
        [
            InlineKeyboardButton("✉️ Ответить", callback_data=f"admin_reply:{user_id}")
        ]
    ])

# ================== СТАРТ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["step"] = "flat"

    await send(
        update,
        "👋 Добро пожаловать!\n\n"
        "Этот бот нужен для подтверждения проживания\n"
        "и получения доступа к домовому чату.\n\n"
        "Пожалуйста, введите номер вашей квартиры:",
        reply_markup=USER_MENU
    )

# ================== FAQ / СТАТУС / КОНТАКТ ==================

async def show_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(
        update,
        "❓ *Частые вопросы*\n\n"
        "• Данные используются только для проверки\n"
        "• Хранятся не более 7 дней\n"
        "• Администратор видит только необходимую информацию\n"
        "• После одобрения вы получите ссылку на чат",
        parse_mode="Markdown"
    )

async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    apps = load_json(APPLICATIONS_FILE, {})
    app = apps.get(str(update.effective_user.id))

    if not app:
        await send(update, "❌ У вас нет активной заявки.")
        return

    await send(update, f"📄 Статус вашей заявки: *{app['status']}*", parse_mode="Markdown")

async def contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["step"] = "contact_admin"
    await send(update, "✉️ Напишите сообщение администратору:")

# ================== СООБЩЕНИЯ ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    step = context.user_data.get("step")

    if text == "❓ FAQ":
        await show_faq(update, context)
        return

    if text == "📄 Статус заявки":
        await show_status(update, context)
        return

    if text == "✉️ Связь с админом":
        await contact_admin(update, context)
        return

    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cadastre"
        await send(update, "Введите кадастровый номер:")
        return

    if step == "cadastre":
        norm = normalize_cadastre(text)
        if not norm:
            await send(update, "❌ Не удалось распознать номер. Попробуйте ещё раз.")
            return

        context.user_data["cadastre"] = norm
        await submit_application(update, context)
        return

    if step == "contact_admin":
        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"✉️ Сообщение от пользователя:\n"
                f"ID: {update.effective_user.id}\n"
                f"Имя: {update.effective_user.full_name}\n"
                f"@{update.effective_user.username}\n\n"
                f"{text}"
            )
        await send(update, "✅ Сообщение отправлено.")
        context.user_data.clear()

# ================== ЗАЯВКА ==================

async def submit_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    apps = load_json(APPLICATIONS_FILE, {})

    apps[str(user.id)] = {
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username,
        "flat": context.user_data["flat"],
        "cadastre": context.user_data["cadastre"],
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(),
    }

    save_json(APPLICATIONS_FILE, apps)

    for admin in ADMINS:
        await context.bot.send_message(
            admin,
            f"🆕 Новая заявка\n\n"
            f"👤 {user.full_name}\n"
            f"🆔 ID: {user.id}\n"
            f"🔗 @{user.username}\n"
            f"🏠 Квартира: {context.user_data['flat']}\n"
            f"📄 Кадастр: {context.user_data['cadastre']}",
            reply_markup=admin_buttons(user.id)
        )

    await send(update, "⏳ Заявка отправлена на проверку.")
    context.user_data.clear()

# ================== CALLBACK АДМИНА ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, uid = query.data.split(":")
    apps = load_json(APPLICATIONS_FILE, {})
    app = apps.get(uid)

    if not app:
        await query.edit_message_text("⚠️ Заявка не найдена.")
        return

    if action == "admin_approve":
        app["status"] = "approved"
        await context.bot.send_message(int(uid), "✅ Ваша заявка одобрена.")

    elif action == "admin_reject":
        app["status"] = "rejected"
        await context.bot.send_message(int(uid), "❌ Ваша заявка отклонена.")

    elif action == "admin_reply":
        context.user_data["reply_to"] = int(uid)
        await query.message.reply_text("✉️ Напишите ответ пользователю:")

    save_json(APPLICATIONS_FILE, apps)
    await query.edit_message_text("✔️ Решение сохранено.")

# ================== MAIN ==================

def main():
    ensure_dirs()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
