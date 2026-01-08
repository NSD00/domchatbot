import os
import json
import logging
import pathlib
import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder,
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ================== НАСТРОЙКИ ЛОГГИРОВАНИЯ ==================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================== КОНФИГУРАЦИЯ ==================
BOT_VERSION = "1.1.8"
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

# Пути к данным
DATA_DIR = "data"
FILES_DIR = os.path.join(DATA_DIR, "files")
APPS_FILE = os.path.join(DATA_DIR, "applications.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")

# Настройки
AUTO_CLEAN_DAYS = 30

# Шаблоны причин отклонения
REJECT_TEMPLATES = [
    "❌ Неверный кадастровый номер",
    "❌ Нечитаемое фото/документ",
    "❌ Несоответствие данных"
]

# Типовые ответы для администратора
REPLY_TEMPLATES = [
    "✅ Заявка будет рассмотрена в течение 24 часов",
    "📋 Необходимо предоставить дополнительные документы",
    "🔄 Проверяем информацию, ожидайте",
    "📞 Свяжемся с вами для уточнения деталей"
]

# ================== УТИЛИТЫ ==================
def ensure_dirs() -> None:
    """Создает необходимые директории"""
    for directory in [DATA_DIR, FILES_DIR]:
        os.makedirs(directory, exist_ok=True)

def load_json(path: str, default) -> Any:
    """Загружает JSON файл"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Ошибка загрузки {path}: {e}")
        return default

def save_json(path: str, data: Any) -> bool:
    """Сохраняет данные в JSON файл"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except (IOError, TypeError) as e:
        logger.error(f"Ошибка сохранения {path}: {e}")
        return False

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    return user_id in ADMINS

def is_blocked(user_id: int) -> bool:
    """Проверяет, заблокирован ли пользователь"""
    return user_id in load_json(BLACKLIST_FILE, [])

def validate_flat_number(text: str) -> bool:
    """Проверяет валидность номера квартиры"""
    text = text.strip()
    if not text or len(text) > 10:
        return False
    
    # Разрешаем цифры и необязательную букву в конце
    pattern = r'^\d+[a-zA-Zа-яА-ЯёЁ]?$'
    return bool(re.match(pattern, text))

def normalize_cadastre(text: str) -> Optional[str]:
    """Нормализует кадастровый номер"""
    digits = ''.join(c for c in text if c.isdigit())
    
    if len(digits) < 12 or len(digits) > 20:
        return None
    
    try:
        return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"
    except IndexError:
        return None

def cleanup_old_apps() -> int:
    """Удаляет старые заявки"""
    apps = load_json(APPS_FILE, {})
    now = datetime.now(timezone.utc)
    removed_count = 0
    
    for uid, data in list(apps.items()):
        try:
            created_str = data.get("created_at")
            if not created_str:
                continue
                
            created = datetime.fromisoformat(created_str)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            
            if now - created > timedelta(days=AUTO_CLEAN_DAYS):
                file_path = data.get("file")
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                
                del apps[uid]
                removed_count += 1
                
        except (KeyError, ValueError, AttributeError) as e:
            logger.error(f"Ошибка при очистке заявки {uid}: {e}")
            if uid in apps:
                del apps[uid]
                removed_count += 1
    
    if removed_count > 0:
        save_json(APPS_FILE, apps)
    
    return removed_count

# ================== ТЕКСТОВЫЕ КОНСТАНТЫ ==================
HELP_TEXT = (
    "❓ *Зачем нужен кадастровый номер?*\n\n"
    "📌 *По кадастровому номеру невозможно узнать:*\n"
    "• 🧾 ФИО, дату рождения, паспортные данные\n"
    "• 🔒 Данные не дают доступа к собственности\n"
    "• 👤 Их видит только администратор дома\n"
    "• 🗑 После сверки все данные удаляются автоматически!\n\n"
    "📋 *Процесс подачи заявки:*\n"
    "1. Введите номер квартиры\n"
    "2. Введите или отправьте файл с кадастровым номером\n"
    "3. Подтвердите данные\n"
    "4. Ожидайте рассмотрения администратором"
)

STATUS_TEXT = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
}

AUTO_HELP_KEYWORDS = ["зачем", "почему", "кадастр", "кадастров", "помощь", "справка"]

# ================== КЛАВИАТУРЫ ==================
USER_MENU = ReplyKeyboardMarkup(
    [
        ["📄 Статус заявки"],
        ["❓ Помощь", "📨 Написать админу"]
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Список заявок", "📊 Статистика"],
        ["📦 Экспорт JSON"]
    ],
    resize_keyboard=True
)

def create_new_app_keyboard() -> ReplyKeyboardMarkup:
    """Создает клавиатуру для подачи новой заявки"""
    return ReplyKeyboardMarkup(
        [["📝 Подать новую заявку"]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def create_cad_confirm_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для подтверждения кадастрового номера"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, всё верно", callback_data="cad_ok"),
            InlineKeyboardButton("❌ Нет, исправить", callback_data="cad_no")
        ]
    ])

def create_admin_buttons(app_id: str, blocked: bool = False) -> InlineKeyboardMarkup:
    """Создает инлайн-кнопки для администрирования заявки"""
    buttons = [
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{app_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{app_id}")
        ],
        [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{app_id}")],
    ]
    
    if blocked:
        buttons.append([InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock:{app_id}")])
    else:
        buttons.append([InlineKeyboardButton("⛔ Заблокировать", callback_data=f"block:{app_id}")])
    
    return InlineKeyboardMarkup(buttons)

def create_reject_templates_keyboard(app_id: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру с шаблонами причин отклонения"""
    buttons = []
    for template in REJECT_TEMPLATES:
        callback_data = f"reject_template:{app_id}:{hash(template) % 10000}"
        buttons.append([InlineKeyboardButton(template, callback_data=callback_data)])
    buttons.append([InlineKeyboardButton("✏️ Своя причина", callback_data=f"reject_custom:{app_id}")])
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"cancel:{app_id}")])
    return InlineKeyboardMarkup(buttons)

def create_reply_templates_keyboard(target_user_id: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру с типовыми ответами"""
    buttons = []
    for template in REPLY_TEMPLATES:
        callback_data = f"reply_template:{target_user_id}:{hash(template) % 10000}"
        buttons.append([InlineKeyboardButton(template, callback_data=callback_data)])
    buttons.append([InlineKeyboardButton("✏️ Свой ответ", callback_data=f"reply_custom:{target_user_id}")])
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"cancel_reply:{target_user_id}")])
    return InlineKeyboardMarkup(buttons)

# ================== ОСНОВНЫЕ ОБРАБОТЧИКИ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    
    if not context.user_data.get("step"):
        context.user_data.clear()
    
    # Проверка блокировки для обычных пользователей
    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text(f"🚫 Вы заблокированы.\n👨‍💻 Ник: @{user.username or '—'}\n🆔 ID: {user.id}")
        return
    
    cleanup_old_apps()
    
    if is_admin(user.id):
        # Для админов показываем информацию об обновлении
        update_info = (
            f"👑 *Административная панель*\n"
            f"🔄 Обновлено до версии: `{BOT_VERSION}`\n\n"
            f"*Что нового:*\n"
            f"• 📋 Улучшена структура заявок\n"
            f"• ✉️ Добавлены типовые ответы\n"
            f"• ↩️ Кнопки отмены действий\n"
            f"• 🛠 Исправлены мелкие ошибки"
        )
        
        await update.message.reply_text(
            update_info,
            parse_mode="Markdown",
            reply_markup=ADMIN_MENU
        )
    else:
        await update.message.reply_text(
            "👋 *Добро пожаловать!*\n\nВведите номер вашей квартиры:",
            parse_mode="Markdown",
            reply_markup=USER_MENU
        )
        context.user_data["step"] = "flat"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    
    # Проверка блокировки для обычных пользователей
    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text(f"🚫 Вы заблокированы.\n👨‍💻 Ник: @{user.username or '—'}\n🆔 ID: {user.id}")
        return
    
    text = update.message.text.strip()
    text_lower = text.lower()
    
    if any(keyword in text_lower for keyword in AUTO_HELP_KEYWORDS):
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return
    
    if not is_admin(user.id):
        await handle_user_message(update, context, text, text_lower)
        return
    
    await handle_admin_message(update, context, text)

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                             text: str, text_lower: str) -> None:
    """Обработка сообщений от обычных пользователей"""
    user = update.effective_user
    step = context.user_data.get("step")
    
    if text == "📄 Статус заявки":
        apps = load_json(APPS_FILE, {})
        app = apps.get(str(user.id))
        if not app:
            await update.message.reply_text("📭 У вас нет активных заявок.")
        else:
            status_msg = f"📋 *Ваша заявка*\n\n🏠 Квартира: {app.get('flat', '—')}\n📌 Статус: {app.get('status', '—')}"
            if app.get("reject_reason"):
                status_msg += f"\n\n*Причина отклонения:*\n{app['reject_reason']}"
                # Добавляем кнопку для новой заявки после отклонения
                if app.get("status") == STATUS_TEXT["rejected"]:
                    await update.message.reply_text(
                        status_msg,
                        parse_mode="Markdown",
                        reply_markup=create_new_app_keyboard()
                    )
                    return
            await update.message.reply_text(status_msg, parse_mode="Markdown")
        return
    
    if text == "📨 Написать админу":
        context.user_data["step"] = "contact"
        await update.message.reply_text("✉️ *Напишите ваше сообщение администратору:*", parse_mode="Markdown")
        return
    
    if text == "❓ Помощь":
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return
    
    if text == "📝 Подать новую заявку":
        context.user_data.clear()
        await update.message.reply_text(
            "👋 *Начинаем новую заявку!*\n\nВведите номер вашей квартиры:",
            parse_mode="Markdown",
            reply_markup=USER_MENU
        )
        context.user_data["step"] = "flat"
        return
    
    if step == "contact":
        contact_msg = (
            f"✉️ *Сообщение от пользователя*\n\n"
            f"👤 Имя: {user.full_name}\n"
            f"👨‍💻 Ник: @{user.username if user.username else '—'}\n"
            f"🆔 ID: {user.id}\n\n"
            f"📝 Сообщение:\n{text}"
        )
        
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(
                    admin_id,
                    contact_msg,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")
                    ]])
                )
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения админу {admin_id}: {e}")
        
        context.user_data.clear()
        await update.message.reply_text("✅ *Сообщение отправлено!*", parse_mode="Markdown", reply_markup=USER_MENU)
        return
    
    if step == "flat":
        # Проверка номера квартиры
        if not validate_flat_number(text):
            await update.message.reply_text(
                "❌ *Неверный формат номера квартиры.*\n\n"
                "Допустимые форматы:\n"
                "• Только цифры: 12, 105, 25\n"
                "• Цифры с буквой в конце: 12А, 25Б, 7В\n\n"
                "Пожалуйста, введите номер квартиры еще раз:",
                parse_mode="Markdown"
            )
            return
        
        context.user_data["flat"] = text.strip()
        context.user_data["step"] = "cad"
        await update.message.reply_text(
            "📄 *Введите кадастровый номер или отправьте файл (фото/PDF):*",
            parse_mode="Markdown"
        )
        return
    
    if step == "cad":
        cadastre = normalize_cadastre(text)
        
        if not cadastre:
            await update.message.reply_text(
                "❌ *Не удалось распознать кадастровый номер.*\n\n"
                "Введите номер в формате:\n"
                "`XX:XX:XXXXXXX:XXX`\n\n"
                "Или отправьте фото/PDF документа с номером.",
                parse_mode="Markdown"
            )
            return
        
        context.user_data["cad"] = cadastre
        
        confirm_text = (
            f"📋 *Проверьте введенные данные:*\n\n"
            f"🏠 Квартира: {context.user_data['flat']}\n"
            f"📄 Кадастр: {cadastre}\n\n"
            f"Всё верно?"
        )
        
        await update.message.reply_text(
            confirm_text,
            parse_mode="Markdown",
            reply_markup=create_cad_confirm_keyboard()
        )
        return

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                              text: str) -> None:
    """Обработка сообщений от администраторов"""
    user = update.effective_user
    apps = load_json(APPS_FILE, {})
    
    if text == "📋 Список заявок":
        if not apps:
            await update.message.reply_text("📭 Нет активных заявок.")
            return
        
        for uid, app in apps.items():
            blocked = is_blocked(int(uid))
            
            app_text = (
                f"👤 Имя: {app.get('name', '—')}\n"
                f"👨‍💻 Ник: @{app.get('username', '—')}\n"
                f"🆔 ID: {uid}\n"
                f"🏠 Квартира: {app.get('flat', '—')}\n"
            )
            
            if app.get("cadastre"):
                app_text += f"📄 Кадастр: `{app['cadastre']}`\n\n"
            else:
                app_text += "\n"
            
            app_text += f"📌 Статус: {app.get('status', '—')}"
            
            # Добавляем причину отклонения, если есть
            if app.get("reject_reason") and app.get("status") == STATUS_TEXT["rejected"]:
                app_text += f"\n\n*Причина отклонения:*\n{app['reject_reason']}"
            
            if blocked:
                app_text += "\n\n⛔ *Заблокирован*"
            
            if app.get("file") and os.path.exists(app["file"]):
                try:
                    with open(app["file"], "rb") as f:
                        await context.bot.send_photo(
                            user.id,
                            photo=f,
                            caption=app_text,
                            parse_mode="Markdown",
                            reply_markup=create_admin_buttons(uid, blocked)
                        )
                except Exception as e:
                    logger.error(f"Ошибка отправки фото: {e}")
                    await context.bot.send_message(
                        user.id,
                        app_text + f"\n\n⚠️ Фото не загружено: {e}",
                        parse_mode="Markdown",
                        reply_markup=create_admin_buttons(uid, blocked)
                    )
            else:
                await context.bot.send_message(
                    user.id,
                    app_text,
                    parse_mode="Markdown",
                    reply_markup=create_admin_buttons(uid, blocked)
                )
        return
    
    if text == "📊 Статистика":
        total = len(apps)
        pending = sum(1 for a in apps.values() if a.get("status") == STATUS_TEXT["pending"])
        approved = sum(1 for a in apps.values() if a.get("status") == STATUS_TEXT["approved"])
        rejected = sum(1 for a in apps.values() if a.get("status") == STATUS_TEXT["rejected"])
        
        stats_text = (
            f"📊 *Статистика заявок*\n\n"
            f"📈 Всего заявок: *{total}*\n"
            f"⏳ На рассмотрении: *{pending}*\n"
            f"✅ Одобрено: *{approved}*\n"
            f"❌ Отклонено: *{rejected}*"
        )
        
        await update.message.reply_text(stats_text, parse_mode="Markdown")
        return
    
    if text == "📦 Экспорт JSON":
        if os.path.exists(APPS_FILE):
            try:
                with open(APPS_FILE, "rb") as f:
                    await context.bot.send_document(
                        user.id,
                        document=f,
                        filename="applications.json"
                    )
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка экспорта: {e}")
        return

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик файлов"""
    user = update.effective_user
    
    # Проверка блокировки для обычных пользователей
    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text(f"🚫 Вы заблокированы.\n👨‍💻 Ник: @{user.username or '—'}\n🆔 ID: {user.id}")
        return
    
    if context.user_data.get("step") != "cad":
        await update.message.reply_text("⚠️ Сначала введите номер квартиры.")
        return
    
    if update.message.document:
        file = update.message.document
        file_type = "document"
    elif update.message.photo:
        file = update.message.photo[-1]
        file_type = "photo"
    else:
        return
    
    try:
        # Скачиваем файл
        timestamp = int(datetime.now().timestamp())
        if file_type == "document":
            ext = pathlib.Path(file.file_name or "file").suffix or ".dat"
        else:
            ext = ".jpg"
        
        safe_filename = f"{user.id}_{timestamp}{ext}"
        file_path = os.path.join(FILES_DIR, safe_filename)
        
        tg_file = await file.get_file()
        await tg_file.download_to_drive(file_path)
    except Exception as e:
        logger.error(f"Ошибка загрузки файла: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке файла.")
        return
    
    # Сохраняем заявку
    apps = load_json(APPS_FILE, {})
    
    apps[str(user.id)] = {
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username,
        "flat": context.user_data.get("flat", ""),
        "cadastre": context.user_data.get("cad", ""),
        "file": file_path,
        "status": STATUS_TEXT["pending"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    if save_json(APPS_FILE, apps):
        # Уведомляем администраторов
        app_info = (
            f"🆕 *Новая заявка (файл):*\n\n"
            f"👤 Имя: {user.full_name}\n"
            f"👨‍💻 Ник: @{user.username if user.username else '—'}\n"
            f"🆔 ID: {user.id}\n"
            f"🏠 Квартира: {context.user_data.get('flat', '—')}\n"
            f"📄 Кадастр: `{context.user_data.get('cad', '—')}`"
        )
        
        for admin_id in ADMINS:
            try:
                # Отправляем файл администратору
                if file_type == "photo":
                    with open(file_path, "rb") as photo_file:
                        await context.bot.send_photo(
                            admin_id,
                            photo=photo_file,
                            caption=app_info,
                            parse_mode="Markdown",
                            reply_markup=create_admin_buttons(str(user.id), False)
                        )
                else:
                    with open(file_path, "rb") as doc_file:
                        await context.bot.send_document(
                            admin_id,
                            document=doc_file,
                            caption=app_info,
                            parse_mode="Markdown",
                            reply_markup=create_admin_buttons(str(user.id), False)
                        )
            except Exception as e:
                logger.error(f"Ошибка отправки файла админу {admin_id}: {e}")
                # Если не удалось отправить файл, отправляем текстовое сообщение
                try:
                    await context.bot.send_message(
                        admin_id,
                        app_info + f"\n📎 Файл не отправлен: {e}",
                        parse_mode="Markdown",
                        reply_markup=create_admin_buttons(str(user.id), False)
                    )
                except:
                    pass
        
        context.user_data.clear()
        await update.message.reply_text(
            "✅ *Файл получен! Заявка отправлена на рассмотрение.*",
            parse_mode="Markdown",
            reply_markup=USER_MENU
        )
    else:
        await update.message.reply_text("❌ Ошибка при сохранении заявки.")

async def handle_user_callback(query, context, data, user):
    """Обработка callback'ов от пользователей"""
    if data == "cad_ok":
        apps = load_json(APPS_FILE, {})
        
        apps[str(user.id)] = {
            "user_id": user.id,
            "name": user.full_name,
            "username": user.username,
            "flat": context.user_data["flat"],
            "cadastre": context.user_data["cad"],
            "status": STATUS_TEXT["pending"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        if save_json(APPS_FILE, apps):
            # Уведомляем администраторов
            app_info = (
                f"🆕 *Новая заявка:*\n\n"
                f"👤 Имя: {user.full_name}\n"
                f"👨‍💻 Ник: @{user.username if user.username else '—'}\n"
                f"🆔 ID: {user.id}\n"
                f"🏠 Квартира: {context.user_data['flat']}\n"
                f"📄 Кадастр: `{context.user_data['cad']}`"
            )
            
            for admin_id in ADMINS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        app_info,
                        parse_mode="Markdown",
                        reply_markup=create_admin_buttons(str(user.id), False)
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
            
            context.user_data.clear()
            await query.edit_message_text("⏳ *Заявка отправлена администратору.*", parse_mode="Markdown")
        else:
            await query.edit_message_text("❌ Ошибка при сохранении заявки.")
        return
    
    elif data == "cad_no":
        context.user_data.pop("cad", None)
        await query.edit_message_text("*Введите кадастровый номер заново:*", parse_mode="Markdown")
        return

async def handle_admin_callback(query, context, data, user):
    """Обработка callback'ов от администраторов"""
    if not is_admin(user.id):
        await query.edit_message_text("❌ У вас нет прав для этого действия.")
        return
    
    # Обработка отмены действий
    if data.startswith("cancel:"):
        try:
            await query.edit_message_text("↩️ Действие отменено.")
        except:
            await context.bot.send_message(user.id, "↩️ Действие отменено.")
        return
    
    if data.startswith("cancel_reply:"):
        try:
            await query.edit_message_text("↩️ Ответ отменен.")
        except:
            await context.bot.send_message(user.id, "↩️ Ответ отменен.")
        return
    
    # Разбираем callback_data
    if ":" in data:
        parts = data.split(":", 2)
        action = parts[0]
        
        if len(parts) < 2:
            await query.edit_message_text("❌ Неверный формат команды.")
            return
        
        target_id = parts[1]
        
        # Обработка шаблонов причин отклонения
        if action == "reject_template":
            if len(parts) == 3:
                template_hash = parts[2]
                template_text = None
                
                for template in REJECT_TEMPLATES:
                    if str(hash(template) % 10000) == template_hash:
                        template_text = template
                        break
                
                if template_text:
                    await process_rejection(context, target_id, template_text, query)
                    return
        
        # Обработка типовых ответов
        if action == "reply_template":
            if len(parts) == 3:
                template_hash = parts[2]
                reply_text = None
                
                for template in REPLY_TEMPLATES:
                    if str(hash(template) % 10000) == template_hash:
                        reply_text = template
                        break
                
                if reply_text:
                    try:
                        await context.bot.send_message(
                            int(target_id),
                            f"✉️ *Сообщение от администратора:*\n\n{reply_text}",
                            parse_mode="Markdown"
                        )
                        try:
                            await query.edit_message_text(f"✅ *Ответ отправлен.*\n\n{reply_text}", parse_mode="Markdown")
                        except:
                            await context.bot.send_message(
                                user.id,
                                f"✅ *Ответ отправлен.*\n\n{reply_text}",
                                parse_mode="Markdown"
                            )
                    except Exception as e:
                        try:
                            await query.edit_message_text(f"❌ Не удалось отправить сообщение: {e}")
                        except:
                            await context.bot.send_message(user.id, f"❌ Не удалось отправить сообщение: {e}")
                    return
        
        apps = load_json(APPS_FILE, {})
        blacklist = load_json(BLACKLIST_FILE, [])
        
        try:
            target_id_int = int(target_id)
        except ValueError:
            await query.edit_message_text("❌ Неверный ID пользователя.")
            return
        
        # Получаем информацию о пользователе
        target_user_info = ""
        target_user_nick = ""
        if target_id in apps:
            target_user_info = f" ({apps[target_id].get('name', 'ID: ' + target_id)})"
            target_user_nick = apps[target_id].get('username', '—')
        
        if action == "block":
            if target_id_int not in blacklist:
                blacklist.append(target_id_int)
                if save_json(BLACKLIST_FILE, blacklist):
                    confirmation_text = (
                        f"⛔ *Пользователь заблокирован*\n"
                        f"👤 Имя: {apps[target_id].get('name', '—') if target_id in apps else '—'}\n"
                        f"👨‍💻 Ник: @{target_user_nick}\n"
                        f"🆔 ID: {target_id}"
                    )
                    try:
                        await query.edit_message_text(confirmation_text, parse_mode="Markdown")
                    except:
                        await context.bot.send_message(
                            user.id,
                            confirmation_text,
                            parse_mode="Markdown"
                        )
                else:
                    await query.edit_message_text("❌ Ошибка при сохранении черного списка.")
            else:
                try:
                    await query.edit_message_text(f"⚠️ Пользователь уже заблокирован{target_user_info}")
                except:
                    await context.bot.send_message(user.id, f"⚠️ Пользователь уже заблокирован{target_user_info}")
            return
        
        if action == "unblock":
            if target_id_int in blacklist:
                blacklist.remove(target_id_int)
                if save_json(BLACKLIST_FILE, blacklist):
                    confirmation_text = (
                        f"✅ *Пользователь разблокирован*\n"
                        f"👤 Имя: {apps[target_id].get('name', '—') if target_id in apps else '—'}\n"
                        f"👨‍💻 Ник: @{target_user_nick}\n"
                        f"🆔 ID: {target_id}"
                    )
                    try:
                        await query.edit_message_text(confirmation_text, parse_mode="Markdown")
                    except:
                        await context.bot.send_message(
                            user.id,
                            confirmation_text,
                            parse_mode="Markdown"
                        )
                else:
                    await query.edit_message_text("❌ Ошибка при сохранении черного списка.")
            else:
                try:
                    await query.edit_message_text(f"ℹ️ Пользователь не был заблокирован{target_user_info}")
                except:
                    await context.bot.send_message(user.id, f"ℹ️ Пользователь не был заблокирован{target_user_info}")
            return
        
        if action == "approve":
            if target_id in apps:
                apps[target_id]["status"] = STATUS_TEXT["approved"]
                if save_json(APPS_FILE, apps):
                    try:
                        await context.bot.send_message(
                            target_id_int,
                            "✅ *Ваша заявка одобрена!*",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления пользователю {target_id}: {e}")
                    
                    try:
                        await query.edit_message_text("✅ *Заявка одобрена.*", parse_mode="Markdown")
                    except:
                        await context.bot.send_message(
                            user.id,
                            "✅ *Заявка одобрена.*",
                            parse_mode="Markdown"
                        )
                else:
                    await query.edit_message_text("❌ Ошибка при сохранении заявки.")
            return
        
        if action == "reject":
            if target_id in apps:
                context.chat_data["pending_reject_app"] = target_id
                try:
                    await query.edit_message_text(
                        "📝 *Выберите причину отклонения:*",
                        parse_mode="Markdown",
                        reply_markup=create_reject_templates_keyboard(target_id)
                    )
                except:
                    await context.bot.send_message(
                        user.id,
                        "📝 *Выберите причину отклонения:*",
                        parse_mode="Markdown",
                        reply_markup=create_reject_templates_keyboard(target_id)
                    )
            return
        
        if action == "reply":
            if target_id in apps:
                context.chat_data["replying_to"] = target_id
                try:
                    await query.edit_message_text(
                        "✉️ *Выберите типовой ответ или введите свой:*",
                        parse_mode="Markdown",
                        reply_markup=create_reply_templates_keyboard(target_id)
                    )
                except:
                    await context.bot.send_message(
                        user.id,
                        "✉️ *Выберите типовой ответ или введите свой:*",
                        parse_mode="Markdown",
                        reply_markup=create_reply_templates_keyboard(target_id)
                    )
            return
        
        if action == "reject_custom":
            context.chat_data["rejecting_app"] = target_id
            try:
                await query.edit_message_text("✏️ *Введите свою причину отклонения:*", parse_mode="Markdown")
            except:
                await context.bot.send_message(
                    user.id,
                    "✏️ *Введите свою причину отклонения:*",
                    parse_mode="Markdown"
                )
            return
        
        if action == "reply_custom":
            context.chat_data["replying_to_custom"] = target_id
            try:
                await query.edit_message_text("✏️ *Введите свой ответ:*", parse_mode="Markdown")
            except:
                await context.bot.send_message(
                    user.id,
                    "✏️ *Введите свой ответ:*",
                    parse_mode="Markdown"
                )
            return
    
    await query.edit_message_text("❌ Неизвестная команда.")

async def process_rejection(context, app_id, reason, query=None):
    """Обработка отклонения заявки"""
    apps = load_json(APPS_FILE, {})
    
    if app_id in apps:
        apps[app_id]["status"] = STATUS_TEXT["rejected"]
        apps[app_id]["reject_reason"] = reason
        
        if save_json(APPS_FILE, apps):
            # Уведомляем пользователя с кнопкой для новой заявки
            try:
                await context.bot.send_message(
                    int(app_id),
                    f"❌ *Ваша заявка отклонена.*\n\n*Причина:* {reason}\n\n"
                    f"Вы можете подать новую заявку:",
                    parse_mode="Markdown",
                    reply_markup=create_new_app_keyboard()
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления об отклонении пользователю {app_id}: {e}")
            
            if query:
                try:
                    await query.edit_message_text(f"✅ *Заявка отклонена.*\nПричина: {reason}", parse_mode="Markdown")
                except:
                    await context.bot.send_message(
                        query.from_user.id,
                        f"✅ *Заявка отклонена.*\nПричина: {reason}",
                        parse_mode="Markdown"
                    )
            
            # Очищаем контекст
            context.chat_data.pop("pending_reject_app", None)
            return True
    
    return False

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Главный обработчик callback-запросов"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    # Разделяем обработку
    if data in ["cad_ok", "cad_no"]:
        await handle_user_callback(query, context, data, user)
    else:
        await handle_admin_callback(query, context, data, user)

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка ответов администратора"""
    user = update.effective_user
    text = update.message.text.strip()
    
    if not is_admin(user.id):
        return
    
    # Обработка своей причины отклонения
    if "rejecting_app" in context.chat_data:
        app_id = context.chat_data["rejecting_app"]
        if await process_rejection(context, app_id, text):
            await update.message.reply_text(f"✅ *Заявка отклонена.*\nПричина: {text}", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Ошибка при отклонении заявки.")
        context.chat_data.pop("rejecting_app", None)
        return
    
    # Обработка своего ответа пользователю
    if "replying_to_custom" in context.chat_data:
        target_id = context.chat_data["replying_to_custom"]
        
        try:
            await context.bot.send_message(
                int(target_id),
                f"✉️ *Сообщение от администратора:*\n\n{text}",
                parse_mode="Markdown"
            )
            await update.message.reply_text(f"✅ *Ответ отправлен.*\n\n{text}", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось отправить сообщение: {e}")
        
        context.chat_data.pop("replying_to_custom", None)
        return

# ================== ЗАПУСК БОТА ==================
async def main_async() -> None:
    """Основная асинхронная функция запуска бота"""
    if not BOT_TOKEN:
        logger.error("Токен бота не установлен!")
        return
    
    ensure_dirs()
    
    # Создаем приложение
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    
    # Упрощенный обработчик для администраторских ответов
    async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if is_admin(user.id) and ("rejecting_app" in context.chat_data or "replying_to_custom" in context.chat_data):
            await handle_admin_reply(update, context)
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler), group=1)
    
    # Обычные текстовые сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message), group=2)
    
    logger.info(f"Бот версии {BOT_VERSION} запускается...")
    
    # Запуск бота
    try:
        await app.initialize()
        await app.start()
        
        # Используем правильный метод запуска для python-telegram-bot 22.3.0
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        
        logger.info("Бот успешно запущен!")
        
        # Бесконечный цикл
        await asyncio.Event().wait()
        
    except Exception as e:
        logger.error(f"Критическая ошибка бота: {e}")
        raise
    finally:
        if app:
            try:
                await app.stop()
            except:
                pass

def main() -> None:
    """Точка входа"""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}")

if __name__ == "__main__":
    main()
