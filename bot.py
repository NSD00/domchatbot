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

# ================== ТЕКСТЫ ==================

HELP_TEXT = (
    "❓ *Помощь*\n\n"
    "📄 *Зачем нужен кадастровый номер?*\n"
    "Кадастровый номер используется только для подтверждения, "
    "что вы действительно проживаете в доме.\n\n"
    "🔒 Он не даёт доступа к собственности и не несёт рисков.\n\n"
    "👤 Эти данные видит только администратор дома."
)

AUTO_HELP_KEYWORDS = [
    "зачем",
    "зачем кадастров",
    "для чего кадастров",
    "кадастровый зачем",
]

STATUS_TEXT = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
}

# ================== МЕНЮ ==================

USER_MENU = ReplyKeyboardMarkup(
    [
        ["📝 Подать заявку заново"],
        ["📄 Статус заявки"],
        ["❓ Помощь", "✉️ Связь с админом"],
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["📋 Список заявок"]],
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

def admin_reply_templates(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Проверка занимает время", callback_data=f"reply_tpl:wait:{uid}")],
        [InlineKeyboardButton("ℹ️ Кадастровый номер безопасен", callback_data=f"reply_tpl:safe:{uid}")],
    ])

def admin_reject_reasons(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Недостаточно данных", callback_data=f"reject_reason:data:{uid}")],
        [InlineKeyboardButton("❌ Данные не подтверждены", callback_data=f"reject_reason:verify:{uid}")],
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
        "Введите номер квартиры для подачи заявки:",
        reply_markup=USER_MENU
    )

# ================== СООБЩЕНИЯ ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.lower()
    step = context.user_data.get("step")

    # автоответ на "зачем"
    if any(k in text for k in AUTO_HELP_KEYWORDS):
        await reply(update, HELP_TEXT, parse_mode="Markdown")
        return

    if is_admin(user.id):
        if text == "📋 список заявок":
            apps = load_json(APPLICATIONS_FILE, {})
            for uid, app in apps.items():
                await context.bot.send_message(
                    user.id,
                    f"👤 {app['name']}\n"
                    f"🏠 {app['flat']}\n"
                    f"📄 {app['cadastre']}\n"
                    f"📌 {app['status']}",
                    reply_markup=admin_buttons(uid)
                )
        return

    if text == "❓ помощь":
        await reply(update, HELP_TEXT, parse_mode="Markdown")
        return

    if text == "📄 статус заявки":
        apps = load_json(APPLICATIONS_FILE, {})
        app = apps.get(str(user.id))
        if not app:
            await reply(update, "❌ Заявка не найдена.")
        else:
            await reply(update, f"📄 Статус: {app['status']}")
        return

    if text == "📝 подать заявку заново":
        context.user_data.clear()
        context.user_data["step"] = "flat"
        await reply(update, "Введите номер квартиры:")
        return

    if step == "flat":
        context.user_data["flat"] = update.message.text
        context.user_data["step"] = "cadastre"
        await reply(update, "Введите кадастровый номер:")
        return

    if step == "cadastre":
        norm = normalize_cadastre(update.message.text)
        if not norm:
            await reply(update, "❌ Неверный формат.")
            return

        apps = load_json(APPLICATIONS_FILE, {})
        apps[str(user.id)] = {
            "user_id": user.id,
            "name": user.full_name,
            "username": user.username,
            "flat": context.user_data["flat"],
            "cadastre": norm,
            "status": STATUS_TEXT["pending"],
            "created_at": datetime.now(UTC).isoformat(),
        }
        save_json(APPLICATIONS_FILE, apps)

        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"🆕 Новая заявка\n\n"
                f"{user.full_name}\n"
                f"🏠 {context.user_data['flat']}\n"
                f"📄 {norm}",
                reply_markup=admin_buttons(user.id)
            )

        context.user_data.clear()
        await reply(update, "⏳ Заявка отправлена.")
        return

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    apps = load_json(APPLICATIONS_FILE, {})

    if parts[0] == "reply":
        uid = parts[1]
        await query.message.reply_text(
            "Выберите шаблон ответа:",
            reply_markup=admin_reply_templates(uid)
        )
        return

    if parts[0] == "reply_tpl":
        _, tpl, uid = parts
        text = {
            "wait": "⏳ Ваша заявка находится на рассмотрении.",
            "safe": "ℹ️ Кадастровый номер безопасен и используется только для проверки.",
        }[tpl]
        await context.bot.send_message(int(uid), text)
        return

    if parts[0] == "reject":
        uid = parts[1]
        await query.message.reply_text(
            "Выберите причину отказа:",
            reply_markup=admin_reject_reasons(uid)
        )
        return

    if parts[0] == "reject_reason":
        _, _, uid = parts
        apps[uid]["status"] = STATUS_TEXT["rejected"]
        save_json(APPLICATIONS_FILE, apps)
        await context.bot.send_message(int(uid), "❌ Заявка отклонена.")
        await query.edit_message_text("❌ Заявка отклонена.")
        return

    if parts[0] == "approve":
        uid = parts[1]
        apps[uid]["status"] = STATUS_TEXT["approved"]
        save_json(APPLICATIONS_FILE, apps)
        await context.bot.send_message(int(uid), "✅ Заявка одобрена.")
        await query.edit_message_text("✅ Заявка одобрена.")
        return

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
