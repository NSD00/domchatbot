import os
import json
import logging
from datetime import datetime, timedelta, UTC

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
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

BOT_TOKEN = "PASTE_YOUR_TOKEN_HERE"

ADMINS = [
    5546945332,
]

DATA_DIR = "data"
TEMP_DIR = "temp/files"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"

APPLICATION_TTL_DAYS = 7

# ================== ЛОГИ ==================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
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
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 12:
        return None
    return ":".join([digits[0:2], digits[2:4], digits[4:-3], digits[-3:]])

def cleanup_old_applications():
    apps = load_json(APPLICATIONS_FILE, {})
    now = datetime.now(UTC)
    changed = False

    for uid in list(apps.keys()):
        created = datetime.fromisoformat(apps[uid]["created_at"])
        if now - created > timedelta(days=APPLICATION_TTL_DAYS):
            del apps[uid]
            changed = True

    if changed:
        save_json(APPLICATIONS_FILE, apps)

# ================== КЛАВИАТУРЫ ==================

def user_menu():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📝 Подать заявку")],
            [KeyboardButton("📄 Статус заявки")],
            [KeyboardButton("🆘 Помощь"), KeyboardButton("✉️ Связь с админом")],
        ],
        resize_keyboard=True,
    )

def admin_menu():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📥 Новые заявки")],
        ],
        resize_keyboard=True,
    )

# ================== СТАРТ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    context.user_data.clear()

    if is_admin(user.id):
        await update.message.reply_text(
            "👋 Админ-панель",
            reply_markup=admin_menu(),
        )
        return

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Этот бот нужен для верификации жителей домового чата.\n"
        "Вы можете подать заявку или связаться с администратором.",
        reply_markup=user_menu(),
    )

# ================== ПОМОЩЬ ==================

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 Помощь\n\n"
        "• Данные используются только для проверки\n"
        "• Хранятся не более 7 дней\n"
        "• Администратор видит только имя и username\n"
        "• Вы можете написать админу в любой момент",
        reply_markup=user_menu(),
    )

# ================== СТАТУС ==================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    apps = load_json(APPLICATIONS_FILE, {})
    app = apps.get(str(update.effective_user.id))

    if not app:
        await update.message.reply_text("❌ Заявка не найдена.", reply_markup=user_menu())
        return

    await update.message.reply_text(
        f"📄 Статус заявки: {app['status']}",
        reply_markup=user_menu(),
    )

# ================== СВЯЗЬ С АДМИНОМ ==================

async def contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["return_step"] = context.user_data.get("step")
    context.user_data["step"] = "contact_admin"

    await update.message.reply_text(
        "✉️ Напишите сообщение администратору:",
        reply_markup=user_menu(),
    )

# ================== ОБРАБОТКА ТЕКСТА ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    step = context.user_data.get("step")

    if is_admin(user.id):
        await update.message.reply_text("Используйте кнопки админа.")
        return

    # ===== Меню =====
    if text == "📝 Подать заявку":
        context.user_data["step"] = "flat"
        await update.message.reply_text("Введите номер квартиры:")
        return

    if text == "📄 Статус заявки":
        await status(update, context)
        return

    if text == "🆘 Помощь":
        await help_cmd(update, context)
        return

    if text == "✉️ Связь с админом":
        await contact_admin(update, context)
        return

    # ===== Связь с админом =====
    if step == "contact_admin":
        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"✉️ Сообщение от пользователя:\n"
                f"Имя: {user.full_name}\n"
                f"Username: @{user.username}\n"
                f"ID: {user.id}\n\n"
                f"{text}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")]
                ])
            )
        await update.message.reply_text("✅ Сообщение отправлено.")
        context.user_data["step"] = context.user_data.get("return_step")
        return

    # ===== Заявка =====
    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cadastre"
        await update.message.reply_text("Введите кадастровый номер:")
        return

    if step == "cadastre":
        norm = normalize_cadastre(text)
        if not norm:
            await update.message.reply_text("❌ Неверный формат. Попробуйте ещё раз.")
            return

        context.user_data["cadastre"] = norm

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Верно", callback_data="submit"),
                InlineKeyboardButton("❌ Исправить", callback_data="retry"),
            ]
        ])

        await update.message.reply_text(
            f"Кадастровый номер:\n`{norm}`\n\nПодтвердить?",
            parse_mode="Markdown",
            reply_markup=kb,
        )

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user

    if data == "retry":
        context.user_data["step"] = "cadastre"
        await query.edit_message_text("Введите кадастровый номер ещё раз:")
        return

    if data == "submit":
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
                f"Имя: {user.full_name}\n"
                f"Username: @{user.username}\n"
                f"ID: {user.id}\n"
                f"Квартира: {context.user_data['flat']}\n"
                f"Кадастр: {context.user_data['cadastre']}",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Принять", callback_data=f"approve:{user.id}"),
                        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{user.id}"),
                    ],
                    [
                        InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")
                    ]
                ])
            )

        context.user_data.clear()
        await query.edit_message_text("⏳ Заявка отправлена.")
        return

    # ===== Админ =====
    if not is_admin(user.id):
        return

    if data.startswith("reply:"):
        target = int(data.split(":")[1])
        context.user_data["admin_reply"] = target
        await query.message.reply_text("Введите ответ пользователю:")
        return

    if data.startswith("approve:") or data.startswith("reject:"):
        action, target = data.split(":")
        apps = load_json(APPLICATIONS_FILE, {})
        app = apps.get(target)

        if not app:
            await query.edit_message_text("Заявка не найдена.")
            return

        app["status"] = "approved" if action == "approve" else "rejected"
        save_json(APPLICATIONS_FILE, apps)

        await context.bot.send_message(
            int(target),
            "✅ Ваша заявка одобрена." if action == "approve" else "❌ Ваша заявка отклонена."
        )

        await query.edit_message_text("✔️ Решение сохранено.")

# ================== MAIN ==================

def main():
    ensure_dirs()
    cleanup_old_applications()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(callbacks))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
