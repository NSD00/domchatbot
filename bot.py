import os
import json
import logging
from datetime import datetime, timedelta, UTC

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"

def cleanup_old_applications():
    apps = load_json(APPLICATIONS_FILE, {})
    now = datetime.now(UTC)
    changed = False

    for uid in list(apps.keys()):
        created = datetime.fromisoformat(apps[uid]["created_at"])
        if now - created > timedelta(days=APPLICATION_TTL_DAYS):
            for f in apps[uid].get("files", []):
                try:
                    os.remove(f["path"])
                except Exception:
                    pass
            del apps[uid]
            changed = True

    if changed:
        save_json(APPLICATIONS_FILE, apps)

# ================== SAFE ANSWER ==================

async def safe_reply(update: Update, text: str, **kwargs):
    if update.message:
        await update.message.reply_text(text, **kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)

# ================== МЕНЮ ==================

def user_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Статус заявки", callback_data="menu_status")],
        [InlineKeyboardButton("❓ FAQ", callback_data="menu_faq")],
        [InlineKeyboardButton("✉️ Связь с админом", callback_data="menu_contact")],
    ])

def admin_menu(user_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"admin_approve:{user_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"admin_reject:{user_id}"),
        ]
    ])

# ================== КОМАНДЫ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["step"] = "flat"

    await safe_reply(
        update,
        "🏠 *Верификация для домового чата*\n\n"
        "Введите номер квартиры:",
        parse_mode="Markdown"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    apps = load_json(APPLICATIONS_FILE, {})
    app = apps.get(str(update.effective_user.id))

    if not app:
        await safe_reply(update, "❌ У вас нет активной заявки.")
        return

    await safe_reply(
        update,
        f"📄 Статус заявки: *{app['status']}*",
        parse_mode="Markdown"
    )

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(
        update,
        "❓ *Частые вопросы*\n\n"
        "• Данные используются только для проверки\n"
        "• Хранятся не более 7 дней\n"
        "• Администратор не видит ваш username\n"
        "• После одобрения вы получите доступ к чату",
        parse_mode="Markdown"
    )

async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["step"] = "contact_admin"
    await safe_reply(update, "✉️ Напишите сообщение администратору:")

# ================== СООБЩЕНИЯ ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step")

    if step == "flat":
        context.user_data["flat"] = update.message.text.strip()
        context.user_data["step"] = "cadastre"
        await update.message.reply_text(
            "Введите кадастровый номер\nили отправьте фото / PDF документа:"
        )
        return

    if step == "cadastre":
        normalized = normalize_cadastre(update.message.text)
        if not normalized:
            await update.message.reply_text("❌ Не удалось распознать номер. Попробуйте ещё раз.")
            return

        context.user_data["cadastre_norm"] = normalized

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да", callback_data="cad_ok"),
                InlineKeyboardButton("❌ Нет", callback_data="cad_no"),
            ]
        ])

        await update.message.reply_text(
            f"Получилось:\n`{normalized}`\n\nВерно?",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return

    if step == "contact_admin":
        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"✉️ Сообщение от {update.effective_user.id}:\n\n{update.message.text}"
            )
        await update.message.reply_text("✅ Сообщение отправлено.")
        context.user_data.clear()

# ================== ФАЙЛЫ ==================

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = None
    ext = ""

    if update.message.photo:
        file = update.message.photo[-1]
        ext = "jpg"
    elif update.message.document:
        file = update.message.document
        ext = update.message.document.file_name.split(".")[-1]

    if not file:
        return

    tg_file = await file.get_file()
    path = f"{TEMP_DIR}/{file.file_id}.{ext}"
    await tg_file.download_to_drive(path)

    context.user_data.setdefault("files", []).append({
        "file_id": file.file_id,
        "path": path,
        "type": ext
    })

    await update.message.reply_text("📎 Файл принят.")
    await submit_application(update, context)

# ================== CALLBACKS ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cad_ok":
        await submit_application(update, context)

    elif query.data == "cad_no":
        context.user_data["step"] = "cadastre"
        await query.edit_message_text("Введите номер ещё раз:")

    elif query.data.startswith("menu_"):
        if query.data == "menu_status":
            await status(update, context)
        elif query.data == "menu_faq":
            await faq(update, context)
        elif query.data == "menu_contact":
            await contact(update, context)

    elif query.data.startswith("admin_"):
        if not is_admin(query.from_user.id):
            return

        action, target_id = query.data.split(":")
        apps = load_json(APPLICATIONS_FILE, {})
        app = apps.get(target_id)

        if not app or app["status"] != "pending":
            await query.edit_message_text("⚠️ Заявка уже обработана.")
            return

        app["status"] = "approved" if action.endswith("approve") else "rejected"
        app["processed_by"] = query.from_user.id
        save_json(APPLICATIONS_FILE, apps)

        await context.bot.send_message(
            int(target_id),
            "✅ Заявка одобрена." if app["status"] == "approved" else "❌ Заявка отклонена."
        )

        await query.edit_message_text("✔️ Решение сохранено.")

# ================== ОТПРАВКА ЗАЯВКИ ==================

async def submit_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    apps = load_json(APPLICATIONS_FILE, {})

    apps[str(user_id)] = {
        "user_id": user_id,
        "flat": context.user_data.get("flat"),
        "cadastre": context.user_data.get("cadastre_norm"),
        "files": context.user_data.get("files", []),
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(),
    }

    save_json(APPLICATIONS_FILE, apps)

    for admin in ADMINS:
        await context.bot.send_message(
            admin,
            f"🆕 Новая заявка\n\n"
            f"👤 {user_id}\n"
            f"🏠 Квартира: {apps[str(user_id)]['flat']}\n"
            f"📄 Кадастр: {apps[str(user_id)]['cadastre']}",
            reply_markup=admin_menu(user_id)
        )

    await safe_reply(update, "⏳ Заявка отправлена на проверку.", reply_markup=user_menu())
    context.user_data.clear()

# ================== MAIN ==================

def main():
    ensure_dirs()
    cleanup_old_applications()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("faq", faq))
    app.add_handler(CommandHandler("contact", contact))

    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
