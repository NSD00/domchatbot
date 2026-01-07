import os
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    InputFile
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
TEMP_DIR = f"{DATA_DIR}/temp"
APPLICATIONS_FILE = f"{DATA_DIR}/applications.json"
BLACKLIST_FILE = f"{DATA_DIR}/blacklist.json"

logging.basicConfig(level=logging.INFO)

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

def clean_old_applications(days=30):
    apps = load_json(APPLICATIONS_FILE, {})
    changed = False
    now = datetime.now(timezone.utc)
    for uid in list(apps.keys()):
        created_at = datetime.fromisoformat(apps[uid]["created_at"])
        if now - created_at > timedelta(days=days):
            # удаляем временные файлы
            temp_file = apps[uid].get("temp_file")
            if temp_file and os.path.exists(temp_file):
                os.remove(temp_file)
            apps.pop(uid)
            changed = True
    if changed:
        save_json(APPLICATIONS_FILE, apps)

def clean_temp_files():
    for f in os.listdir(TEMP_DIR):
        fp = os.path.join(TEMP_DIR, f)
        if os.path.isfile(fp):
            os.remove(fp)

def add_to_blacklist(uid):
    blacklist = load_json(BLACKLIST_FILE, [])
    if uid not in blacklist:
        blacklist.append(uid)
        save_json(BLACKLIST_FILE, blacklist)

def remove_from_blacklist(uid):
    blacklist = load_json(BLACKLIST_FILE, [])
    if uid in blacklist:
        blacklist.remove(uid)
        save_json(BLACKLIST_FILE, blacklist)

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
    [["📋 Список заявок"], ["📊 Статистика"]],
    resize_keyboard=True
)

def admin_buttons(uid: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{uid}")
        ],
        [
            InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{uid}"),
            InlineKeyboardButton("🚫 Бан", callback_data=f"ban:{uid}")
        ]
    ])

def admin_reply_templates(uid: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Проверка занимает время", callback_data=f"reply_tpl:wait:{uid}")],
        [InlineKeyboardButton("ℹ️ Кадастровый номер безопасен", callback_data=f"reply_tpl:safe:{uid}")],
    ])

def admin_reject_reasons(uid: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Недостаточно данных", callback_data=f"reject_reason:data:{uid}")],
        [InlineKeyboardButton("❌ Данные не подтверждены", callback_data=f"reject_reason:verify:{uid}")],
    ])

def cadastre_confirm_buttons(uid: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да", callback_data=f"cadastre_confirm:yes:{uid}"),
            InlineKeyboardButton("❌ Нет", callback_data=f"cadastre_confirm:no:{uid}")
        ]
    ])

def choose_input_buttons():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Ввести кадастр", callback_data="input_method:text"),
            InlineKeyboardButton("Прислать фото/PDF", callback_data="input_method:file")
        ]
    ])

# ================== СТАРТ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_dirs()
    clean_old_applications()
    context.user_data.clear()
    user = update.effective_user

    blacklist = load_json(BLACKLIST_FILE, [])
    if user.id in blacklist:
        await reply(update, "🚫 Вы заблокированы и не можете подавать заявки.")
        return

    if is_admin(user.id):
        await reply(update, "👋 Админ-панель", reply_markup=ADMIN_MENU)
        return

    context.user_data["step"] = "choose_input"
    await reply(update,
        "👋 Добро пожаловать!\n\n"
        "Для подачи заявки выберите способ предоставления данных:",
        reply_markup=choose_input_buttons()
    )

# ================== СОБЫТИЯ ==================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text if update.message else ""
    step = context.user_data.get("step")
    apps = load_json(APPLICATIONS_FILE, {})

    blacklist = load_json(BLACKLIST_FILE, [])
    if user.id in blacklist:
        await reply(update, "🚫 Вы заблокированы.")
        return

    if text.lower() and any(k in text.lower() for k in AUTO_HELP_KEYWORDS):
        await reply(update, HELP_TEXT, parse_mode="Markdown")
        return

    # ---------- пользователь ----------
    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cadastre"
        await reply(update, "Введите кадастровый номер:")
        return

    if step == "cadastre":
        norm = normalize_cadastre(text)
        if not norm:
            await reply(update, "❌ Неверный формат.")
            return
        context.user_data["cadastre_tmp"] = norm
        context.user_data["step"] = "cadastre_confirm"
        await reply(update,
            f"Вы ввели кадастровый номер: `{norm}`\nВерно?",
            parse_mode="Markdown",
            reply_markup=cadastre_confirm_buttons(str(user.id))
        )
        return

    if step == "file" and update.message.document:
        file = update.message.document
        file_path = os.path.join(TEMP_DIR, f"{user.id}_{file.file_name}")
        await file.get_file().download_to_drive(file_path)
        context.user_data["temp_file"] = file_path
        context.user_data["step"] = "file_confirm"
        await reply(update, f"Файл `{file.file_name}` получен. Отправляем на проверку?", parse_mode="Markdown",
                    reply_markup=cadastre_confirm_buttons(str(user.id)))
        return

# ================== CALLBACK ==================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    apps = load_json(APPLICATIONS_FILE, {})

    # ---------- выбор метода ввода ----------
    if parts[0] == "input_method":
        method = parts[1]
        if method=="text":
            await query.message.reply_text("Введите номер квартиры:")
            query._bot_data.user_data[query.from_user.id]["step"] = "flat"
        else:
            await query.message.reply_text("Отправьте фото или PDF файл:")
            query._bot_data.user_data[query.from_user.id]["step"] = "file"
        return

    # ---------- подтверждение кадастра или файла ----------
    if parts[0] == "cadastre_confirm":
        uid = parts[2]
        user_data = context.user_data
        user = query.from_user
        if parts[1]=="yes":
            app_data = {
                "user_id": user.id,
                "name": user.full_name,
                "username": user.username,
                "flat": user_data.get("flat"),
                "status": STATUS_TEXT["pending"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if user_data.get("cadastre_tmp"):
                app_data["cadastre"] = user_data.get("cadastre_tmp")
            if user_data.get("temp_file"):
                app_data["temp_file"] = user_data.get("temp_file")
            apps[str(user.id)] = app_data
            save_json(APPLICATIONS_FILE, apps)

            for admin in ADMINS:
                buttons = admin_buttons(str(user.id))
                msg = f"🆕 Новая заявка\n👤 {user.full_name}\n"
                if "flat" in user_data: msg += f"🏠 {user_data['flat']}\n"
                if "cadastre_tmp" in user_data: msg += f"📄 `{user_data['cadastre_tmp']}`\n"
                if "temp_file" in user_data: msg += f"📎 Файл прикреплён"
                await context.bot.send_message(admin, msg, parse_mode="Markdown", reply_markup=buttons)

            user_data.clear()
            await query.edit_message_text("⏳ Заявка отправлена.")
        else:
            user_data["step"] = "cadastre" if user_data.get("cadastre_tmp") else "file"
            await query.edit_message_text("Введите кадастровый номер заново или отправьте файл.")
        return

# ================== MAIN ==================
def main():
    ensure_dirs()
    clean_old_applications()
    clean_temp_files()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
