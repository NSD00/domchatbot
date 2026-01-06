import os
import json
import logging
from datetime import datetime, timedelta

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

BOT_TOKEN = "PASTE_BOT_TOKEN_HERE"

ADMINS = [
    5546945332,  # ID админа 1
#    222222222,  # ID админа 2
]

DATA_DIR = "data"
TEMP_DIR = "temp/files"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"
DIALOGS_FILE = f"{DATA_DIR}/dialogs.json"
STATS_FILE = f"{DATA_DIR}/stats.json"

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
    parts = []
    parts.append(digits[0:2])
    parts.append(digits[2:4])
    parts.append(digits[4:-3])
    parts.append(digits[-3:])
    return ":".join(parts)

def cleanup_old_applications():
    apps = load_json(APPLICATIONS_FILE, {})
    changed = False
    now = datetime.utcnow()

    for uid in list(apps.keys()):
        created = datetime.fromisoformat(apps[uid]["created_at"])
        if now - created > timedelta(days=APPLICATION_TTL_DAYS):
            # удаляем файлы
            for f in apps[uid].get("files", []):
                try:
                    os.remove(f["path"])
                except Exception:
                    pass
            del apps[uid]
            changed = True

    if changed:
        save_json(APPLICATIONS_FILE, apps)

# ================== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    apps = load_json(APPLICATIONS_FILE, {})

    if str(user_id) in apps and apps[str(user_id)]["status"] == "pending":
        await update.message.reply_text("⏳ У вас уже есть заявка на рассмотрении.")
        return

    context.user_data.clear()
    context.user_data["step"] = "flat"

    await update.message.reply_text(
        "🏠 Верификация для домового чата\n\n"
        "Введите номер квартиры:"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    apps = load_json(APPLICATIONS_FILE, {})
    app = apps.get(str(update.effective_user.id))

    if not app:
        await update.message.reply_text("❌ Заявка не найдена.")
        return

    await update.message.reply_text(
        f"📄 Статус заявки: **{app['status']}**",
        parse_mode="Markdown"
    )

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Частые вопросы:\n"
        "— Данные используются только для проверки\n"
        "— Хранятся не более 7 дней\n"
        "— Админ не видит ваш username"
    )

async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✉️ Напишите сообщение, я передам его администратору."
    )
    context.user_data["step"] = "contact_admin"

# ================== ОБРАБОТКА СООБЩЕНИЙ ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    step = context.user_data.get("step")

    if step == "flat":
        context.user_data["flat"] = update.message.text.strip()
        context.user_data["step"] = "cadastre"
        await update.message.reply_text(
            "Введите кадастровый номер\n"
            "или отправьте фото / PDF документа:"
        )
        return

    if step == "cadastre":
        text = update.message.text
        normalized = normalize_cadastre(text)

        if not normalized:
            await update.message.reply_text("❌ Не удалось распознать номер. Попробуйте ещё раз.")
            return

        context.user_data["cadastre_raw"] = text
        context.user_data["cadastre_norm"] = normalized

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да", callback_data="cad_ok"),
                InlineKeyboardButton("❌ Нет", callback_data="cad_no"),
            ]
        ])

        await update.message.reply_text(
            f"Получилось так:\n`{normalized}`\n\nВерно?",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return

    if step == "contact_admin":
        for admin in ADMINS:
            await context.bot.send_message(
                admin,
                f"✉️ Сообщение от пользователя {user_id}:\n\n{update.message.text}"
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

    await update.message.reply_text("📎 Файл принят. Заявка отправляется админу.")
    await submit_application(update, context)

# ================== CALLBACK ==================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "cad_ok":
        await submit_application(query, context)

    if query.data == "cad_no":
        context.user_data["step"] = "cadastre"
        await query.edit_message_text("Введите номер ещё раз.")

    if query.data.startswith("admin_"):
        if not is_admin(user_id):
            return

        action, target_id = query.data.split(":")
        apps = load_json(APPLICATIONS_FILE, {})
        app = apps.get(target_id)

        if not app or app["status"] != "pending":
            await query.edit_message_text("⚠️ Заявка уже обработана.")
            return

        if action == "admin_approve":
            app["status"] = "approved"
            app["processed_by"] = user_id

            await context.bot.send_message(
                int(target_id),
                "✅ Ваша заявка одобрена. Ссылка на чат у администратора."
            )

        if action == "admin_reject":
            app["status"] = "rejected"
            app["processed_by"] = user_id

            await context.bot.send_message(
                int(target_id),
                "❌ Заявка отклонена."
            )

        save_json(APPLICATIONS_FILE, apps)
        await query.edit_message_text(f"✔️ Решение принято администратором {user_id}")

# ================== ОТПРАВКА ЗАЯВКИ ==================

async def submit_application(source, context):
    user_id = source.from_user.id
    apps = load_json(APPLICATIONS_FILE, {})

    apps[str(user_id)] = {
        "user_id": user_id,
        "flat": context.user_data.get("flat"),
        "cadastre_raw": context.user_data.get("cadastre_raw"),
        "cadastre_normalized": context.user_data.get("cadastre_norm"),
        "files": context.user_data.get("files", []),
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }

    save_json(APPLICATIONS_FILE, apps)

    for admin in ADMINS:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Принять", callback_data=f"admin_approve:{user_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"admin_reject:{user_id}")
            ]
        ])

        await context.bot.send_message(
            admin,
            f"🆕 Новая заявка\n\n"
            f"👤 Пользователь: {user_id}\n"
            f"🏠 Квартира: {apps[str(user_id)]['flat']}\n"
            f"📄 Кадастр: {apps[str(user_id)]['cadastre_normalized']}",
            reply_markup=keyboard
        )

    if hasattr(source, "edit_message_text"):
        await source.edit_message_text("⏳ Заявка отправлена.")
    else:
        await source.message.reply_text("⏳ Заявка отправлена.")

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

    app.run_polling()

if __name__ == "__main__":
    main()




