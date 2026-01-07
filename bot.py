import os
import json
import logging
import mimetypes
import pathlib
import re
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
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКИ ЛОГГИРОВАНИЯ ==================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ================== КОНФИГУРАЦИЯ ==================
BOT_VERSION = "1.1.0"
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

# Пути к данным
DATA_DIR = "data"
FILES_DIR = os.path.join(DATA_DIR, "files")
APPS_FILE = os.path.join(DATA_DIR, "applications.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

# Настройки
AUTO_CLEAN_DAYS = 30
MAX_FILE_SIZE_MB = 10
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.pdf', '.txt', '.doc', '.docx'}

# ================== УТИЛИТЫ ==================
def ensure_dirs() -> None:
    """Создает необходимые директории"""
    for directory in [DATA_DIR, FILES_DIR]:
        os.makedirs(directory, exist_ok=True)
    logger.info("Директории проверены/созданы")

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
    if not text or len(text) > 20:
        return False
    # Разрешаем цифры, буквы, дефисы, слэши, точки и пробелы
    pattern = r'^[a-zA-Zа-яА-Я0-9\-\/\.\s]+$'
    return bool(re.match(pattern, text))

def normalize_cadastre(text: str) -> Optional[str]:
    """Нормализует кадастровый номер"""
    # Убираем все кроме цифр
    digits = ''.join(c for c in text if c.isdigit())
    
    if len(digits) < 12 or len(digits) > 20:
        return None
    
    # Формат: XX:XX:XXXXXXX:XXX
    try:
        return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"
    except IndexError:
        return None

def safe_file_extension(filename: str) -> bool:
    """Проверяет безопасность расширения файла"""
    if not filename:
        return False
    ext = pathlib.Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, 
                       message: str, 
                       **kwargs) -> None:
    """Безопасная отправка сообщений администраторам"""
    if not ADMINS:
        logger.warning("Список администраторов пуст!")
        return
    
    success_count = 0
    for admin_id in ADMINS:
        try:
            await context.bot.send_message(admin_id, message, **kwargs)
            success_count += 1
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение админу {admin_id}: {e}")
    
    logger.info(f"Сообщения отправлены {success_count}/{len(ADMINS)} администраторам")

def cleanup_old_apps() -> int:
    """Удаляет старые заявки и возвращает количество удаленных"""
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
            
            # Удаляем если прошло больше AUTO_CLEAN_DAYS дней
            if now - created > timedelta(days=AUTO_CLEAN_DAYS):
                # Удаляем файл если существует
                file_path = data.get("file")
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        logger.info(f"Удален файл: {file_path}")
                    except OSError as e:
                        logger.warning(f"Не удалось удалить файл {file_path}: {e}")
                
                # Удаляем запись
                del apps[uid]
                removed_count += 1
                
        except (KeyError, ValueError, AttributeError) as e:
            logger.error(f"Ошибка обработки записи {uid}: {e}")
            # Удаляем битую запись
            if uid in apps:
                del apps[uid]
                removed_count += 1
    
    if removed_count > 0:
        if save_json(APPS_FILE, apps):
            logger.info(f"Удалено {removed_count} старых заявок")
        else:
            logger.error("Не удалось сохранить данные после очистки")
    
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
    resize_keyboard=True,
    input_field_placeholder="Выберите действие..."
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Список заявок", "📊 Статистика"],
        ["🔄 Очистить старые", "📦 Экспорт JSON"]
    ],
    resize_keyboard=True
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
        [InlineKeyboardButton("✉️ Ответить пользователю", callback_data=f"reply:{app_id}")],
    ]
    
    if blocked:
        buttons.append([InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock:{app_id}")])
    else:
        buttons.append([InlineKeyboardButton("🚫 Заблокировать", callback_data=f"block:{app_id}")])
    
    return InlineKeyboardMarkup(buttons)

# ================== ОСНОВНЫЕ ОБРАБОТЧИКИ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    logger.info(f"Пользователь {user.id} ({user.username}) начал работу с ботом")
    
    # Очищаем только если не в процессе заполнения
    if not context.user_data.get("step"):
        context.user_data.clear()
    
    # Проверка блокировки
    if is_blocked(user.id):
        await update.message.reply_text(
            "🚫 *Вы заблокированы в системе.*\n\n"
            "Для разблокировки обратитесь к администратору.",
            parse_mode="Markdown"
        )
        return
    
    # Автоматическая очистка старых заявок
    cleaned = cleanup_old_apps()
    if cleaned > 0:
        logger.info(f"Автоматически очищено {cleaned} старых заявок")
    
    # Разное приветствие для админа и пользователя
    if is_admin(user.id):
        welcome_text = (
            f"👑 *Административная панель*\n"
            f"Версия бота: `{BOT_VERSION}`\n"
            f"ID: `{user.id}`\n\n"
            f"Выберите действие:"
        )
        await update.message.reply_text(welcome_text, 
                                       parse_mode="Markdown",
                                       reply_markup=ADMIN_MENU)
    else:
        welcome_text = (
            f"👋 *Добро пожаловать!*\n\n"
            f"Я помогу вам подать заявку.\n"
            f"Для начала введите *номер вашей квартиры*:"
        )
        await update.message.reply_text(welcome_text, 
                                       parse_mode="Markdown",
                                       reply_markup=USER_MENU)
        context.user_data["step"] = "flat"
        context.user_data["user_id"] = user.id

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    text = update.message.text.strip()
    text_lower = text.lower()
    
    logger.info(f"Сообщение от {user.id}: {text[:50]}...")
    
    # Проверка блокировки
    if is_blocked(user.id):
        return
    
    # ---------- АВТОМАТИЧЕСКИЕ ОТВЕТЫ ПО КЛЮЧЕВЫМ СЛОВАМ ----------
    if any(keyword in text_lower for keyword in AUTO_HELP_KEYWORDS):
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return
    
    # ---------- ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ----------
    if not is_admin(user.id):
        await handle_user_message(update, context, text, text_lower)
        return
    
    # ---------- АДМИНИСТРАТОРСКИЕ КОМАНДЫ ----------
    await handle_admin_message(update, context, text)

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                             text: str, text_lower: str) -> None:
    """Обработка сообщений от обычных пользователей"""
    user = update.effective_user
    step = context.user_data.get("step")
    apps = load_json(APPS_FILE, {})
    
    # Проверка существующей заявки
    existing_app = apps.get(str(user.id))
    if existing_app and existing_app["status"] == STATUS_TEXT["pending"]:
        if text != "📄 Статус заявки" and step != "contact":
            await update.message.reply_text(
                "⏳ *У вас уже есть активная заявка на рассмотрении.*\n\n"
                "Используйте кнопку '📄 Статус заявки' для проверки статуса "
                "или дождитесь решения администратора.",
                parse_mode="Markdown"
            )
            return
    
    # Меню пользователя
    if text == "📄 Статус заявки":
        app = apps.get(str(user.id))
        if not app:
            await update.message.reply_text(
                "📭 *Заявка не найдена.*\n\n"
                "У вас нет активных заявок. "
                "Для создания новой заявки начните с команды /start",
                parse_mode="Markdown"
            )
        else:
            status_msg = (
                f"📋 *Ваша заявка*\n\n"
                f"🏠 Квартира: {app.get('flat', '—')}\n"
                f"📅 Дата подачи: {app.get('created_at', '—')[:10]}\n"
                f"📌 Статус: {app.get('status', '—')}"
            )
            if app.get("reject_reason"):
                status_msg += f"\n\n*Причина отклонения:*\n{app['reject_reason']}"
            
            await update.message.reply_text(status_msg, parse_mode="Markdown")
        return
    
    if text == "📨 Написать админу":
        context.user_data["step"] = "contact"
        await update.message.reply_text(
            "✉️ *Напишите ваше сообщение администратору:*\n\n"
            "Опишите ваш вопрос или проблему, и мы обязательно ответим.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    if text == "❓ Помощь":
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return
    
    # Обработка по шагам
    if step == "contact":
        # Отправка сообщения администраторам
        contact_msg = (
            f"✉️ *Новое сообщение от пользователя*\n\n"
            f"👤 *Пользователь:* {user.full_name}\n"
            f"🔹 *Никнейм:* @{user.username if user.username else '—'}\n"
            f"🆔 *ID:* `{user.id}`\n\n"
            f"📝 *Сообщение:*\n{text}"
        )
        
        await notify_admins(
            context,
            contact_msg,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✉️ Ответить", 
                    callback_data=f"reply:{user.id}"
                )
            ]])
        )
        
        context.user_data.clear()
        await update.message.reply_text(
            "✅ *Сообщение отправлено!*\n\n"
            "Администратор ответит вам в ближайшее время.",
            parse_mode="Markdown",
            reply_markup=USER_MENU
        )
        logger.info(f"Пользователь {user.id} отправил сообщение админу")
        return
    
    if step == "flat":
        # Валидация номера квартиры
        if not validate_flat_number(text):
            await update.message.reply_text(
                "❌ *Неверный формат номера квартиры.*\n\n"
                "Допустимые символы: цифры, буквы, дефисы (-), слэши (/), точки (.)\n"
                "Максимальная длина: 20 символов\n\n"
                "Пожалуйста, введите номер квартиры еще раз:",
                parse_mode="Markdown"
            )
            return
        
        context.user_data["flat"] = text
        context.user_data["step"] = "cad"
        
        await update.message.reply_text(
            "📄 *Введите кадастровый номер или отправьте файл*\n\n"
            "Вы можете:\n"
            "• 📝 Написать номер текстом\n"
            "• 📎 Отправить фото/скан документа\n"
            "• 📄 Отправить PDF файл\n\n"
            "*Формат кадастрового номера:*\n"
            "`XX:XX:XXXXXXX:XXX` (только цифры)",
            parse_mode="Markdown"
        )
        return
    
    if step == "cad":
        # Попытка нормализовать кадастровый номер
        cadastre = normalize_cadastre(text)
        
        if not cadastre:
            await update.message.reply_text(
                "❌ *Не удалось распознать кадастровый номер.*\n\n"
                "Пожалуйста, введите номер в формате:\n"
                "`XX:XX:XXXXXXX:XXX`\n\n"
                "Или отправьте фото/PDF документа с номером.",
                parse_mode="Markdown"
            )
            return
        
        context.user_data["cad"] = cadastre
        
        # Показываем подтверждение с моноширинным форматированием
        confirm_text = (
            f"📋 *Проверьте введенные данные:*\n\n"
            f"🏠 *Квартира:* {context.user_data['flat']}\n"
            f"📄 *Кадастровый номер:*\n"
            f"```\n{cadastre}\n```\n\n"
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
        
        await update.message.reply_text(
            f"📋 *Активные заявки:* {len(apps)}\n"
            f"Используйте кнопки под каждой заявкой для управления.",
            parse_mode="Markdown"
        )
        
        for uid, app in apps.items():
            blocked = is_blocked(int(uid))
            status_text = app.get("status", "⏳ Неизвестно")
            
            # Форматируем сообщение с моноширинным кадастровым номером
            app_text = (
                f"👤 *{app.get('name', '—')}*\n"
                f"🔹 Ник: @{app.get('username', '—')}\n"
                f"🆔 ID: `{uid}`\n"
                f"🏠 Квартира: {app.get('flat', '—')}\n"
                f"📅 Дата: {app.get('created_at', '—')[:10]}\n"
                f"📌 Статус: {status_text}\n"
            )
            
            # Добавляем кадастровый номер с моноширинным форматированием
            if app.get("cadastre"):
                app_text += f"\n📄 *Кадастровый номер:*\n```\n{app['cadastre']}\n```\n"
            
            # Добавляем пометку о блокировке
            if blocked:
                app_text += "\n🚫 *Заблокирован*"
            
            # Отправляем с фото или без
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
                    logger.error(f"Ошибка отправки фото для заявки {uid}: {e}")
                    await context.bot.send_message(
                        user.id,
                        app_text + f"\n\n📎 Файл недоступен: {e}",
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
            f"❌ Отклонено: *{rejected}*\n\n"
            f"🗑 Автоочистка: каждые *{AUTO_CLEAN_DAYS}* дней"
        )
        
        await update.message.reply_text(stats_text, parse_mode="Markdown")
        return
    
    if text == "🔄 Очистить старые":
        cleaned = cleanup_old_apps()
        await update.message.reply_text(
            f"🧹 *Очистка завершена*\n\n"
            f"Удалено старых заявок: *{cleaned}*",
            parse_mode="Markdown"
        )
        return
    
    if text == "📦 Экспорт JSON":
        if not os.path.exists(APPS_FILE):
            await update.message.reply_text("❌ Файл с заявками не найден.")
            return
        
        try:
            await context.bot.send_document(
                user.id,
                document=open(APPS_FILE, "rb"),
                filename=f"applications_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                caption="📦 *Экспорт всех заявок*\nДата: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                parse_mode="Markdown"
            )
            logger.info(f"Админ {user.id} экспортировал данные")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка экспорта: {e}")
            logger.error(f"Ошибка экспорта для админа {user.id}: {e}")
        return

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик файлов (документы и фото)"""
    user = update.effective_user
    
    # Проверка блокировки
    if is_blocked(user.id):
        return
    
    # Проверяем, что пользователь на шаге "cad"
    if context.user_data.get("step") != "cad":
        await update.message.reply_text(
            "⚠️ *Сначала введите номер квартиры* используя команду /start",
            parse_mode="Markdown"
        )
        return
    
    # Получаем файл
    if update.message.document:
        file = update.message.document
        filename = file.file_name or "document"
    elif update.message.photo:
        file = update.message.photo[-1]
        filename = "photo.jpg"
    else:
        await update.message.reply_text("❌ Неподдерживаемый тип файла.")
        return
    
    # Проверка расширения файла
    if not safe_file_extension(filename):
        await update.message.reply_text(
            "❌ *Недопустимый тип файла.*\n\n"
            "Разрешенные форматы:\n"
            "• Изображения: JPG, JPEG, PNG\n"
            "• Документы: PDF, TXT, DOC, DOCX",
            parse_mode="Markdown"
        )
        return
    
    # Проверка размера файла
    if file.file_size and file.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await update.message.reply_text(
            f"❌ *Файл слишком большой.*\n\n"
            f"Максимальный размер: {MAX_FILE_SIZE_MB} МБ",
            parse_mode="Markdown"
        )
        return
    
    # Скачиваем файл
    try:
        ext = pathlib.Path(filename).suffix.lower()
        safe_filename = f"{user.id}_{int(datetime.now().timestamp())}{ext}"
        file_path = os.path.join(FILES_DIR, safe_filename)
        
        tg_file = await file.get_file()
        await tg_file.download_to_drive(file_path)
        
        logger.info(f"Файл сохранен: {file_path}")
    except Exception as e:
        await update.message.reply_text("❌ Ошибка при загрузке файла.")
        logger.error(f"Ошибка скачивания файла от {user.id}: {e}")
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
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    if save_json(APPS_FILE, apps):
        # Уведомляем администраторов
        app_info = (
            f"🆕 *Новая заявка (файл)*\n\n"
            f"👤 *Пользователь:* {user.full_name}\n"
            f"🔹 *Никнейм:* @{user.username if user.username else '—'}\n"
            f"🆔 *ID:* `{user.id}`\n"
            f"🏠 *Квартира:* {context.user_data.get('flat', '—')}\n"
        )
        
        if context.user_data.get("cad"):
            app_info += f"\n📄 *Кадастровый номер:*\n```\n{context.user_data['cad']}\n```\n"
        
        app_info += f"\n📎 *Файл:* {filename}"
        
        await notify_admins(
            context,
            app_info,
            parse_mode="Markdown",
            reply_markup=create_admin_buttons(str(user.id), False)
        )
        
        context.user_data.clear()
        await update.message.reply_text(
            "✅ *Файл получен!*\n\n"
            "📨 Заявка отправлена на рассмотрение администратору.\n"
            "Используйте кнопку '📄 Статус заявки' для отслеживания статуса.",
            parse_mode="Markdown",
            reply_markup=USER_MENU
        )
        logger.info(f"Пользователь {user.id} отправил заявку с файлом")
    else:
        await update.message.reply_text("❌ Ошибка при сохранении заявки.")
        # Удаляем файл если не удалось сохранить заявку
        try:
            os.remove(file_path)
        except:
            pass

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик callback-запросов от инлайн-кнопок"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    # Только администраторы могут использовать callback кнопки
    if not is_admin(user.id):
        await query.edit_message_text("❌ У вас нет прав для этого действия.")
        return
    
    logger.info(f"Callback от админа {user.id}: {data}")
    
    # Разделяем action и ID
    if ":" not in data:
        await query.edit_message_text("❌ Неверный формат команды.")
        return
    
    action, target_id = data.split(":", 1)
    
    # Загрузка данных
    apps = load_json(APPS_FILE, {})
    blacklist = load_json(BLACKLIST_FILE, [])
    target_id_int = int(target_id)
    
    # Обработка действий
    if action == "cad_ok":
        # Это действие должно быть доступно только пользователям, но оставим для безопасности
        await query.edit_message_text("Это действие доступно только при создании заявки.")
        return
    
    elif action == "cad_no":
        await query.edit_message_text("Это действие доступно только при создании заявки.")
        return
    
    elif action == "block":
        if target_id_int not in blacklist:
            blacklist.append(target_id_int)
            save_json(BLACKLIST_FILE, blacklist)
            await query.edit_message_text("🚫 *Пользователь заблокирован.*", parse_mode="Markdown")
            logger.info(f"Админ {user.id} заблокировал пользователя {target_id}")
        else:
            await query.edit_message_text("⚠️ Пользователь уже заблокирован.")
        return
    
    elif action == "unblock":
        if target_id_int in blacklist:
            blacklist.remove(target_id_int)
            save_json(BLACKLIST_FILE, blacklist)
            await query.edit_message_text("🔓 *Пользователь разблокирован.*", parse_mode="Markdown")
            logger.info(f"Админ {user.id} разблокировал пользователя {target_id}")
        else:
            await query.edit_message_text("⚠️ Пользователь не был заблокирован.")
        return
    
    elif action == "approve":
        if target_id in apps:
            apps[target_id]["status"] = STATUS_TEXT["approved"]
            apps[target_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_json(APPS_FILE, apps)
            
            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    target_id_int,
                    "🎉 *Ваша заявка одобрена!*\n\n"
                    "Администратор рассмотрел вашу заявку и принял положительное решение.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Не удалось уведомить пользователя {target_id}: {e}")
            
            await query.edit_message_text("✅ *Заявка одобрена.*\nПользователь уведомлен.", parse_mode="Markdown")
            logger.info(f"Админ {user.id} одобрил заявку {target_id}")
        else:
            await query.edit_message_text("❌ Заявка не найдена.")
        return
    
    elif action == "reject":
        if target_id in apps:
            # Запрашиваем причину отклонения
            context.chat_data["rejecting_app"] = target_id
            await query.edit_message_text(
                "📝 *Укажите причину отклонения заявки:*\n\n"
                "Это сообщение будет отправлено пользователю.",
                parse_mode="Markdown"
            )
            # Сохраняем message_id для редактирования
            context.chat_data["reject_message_id"] = query.message.message_id
        else:
            await query.edit_message_text("❌ Заявка не найдена.")
        return
    
    elif action == "reply":
        # Запрос на ответ пользователю
        context.chat_data["replying_to"] = target_id
        await query.edit_message_text(
            "✉️ *Введите ответ для пользователя:*",
            parse_mode="Markdown"
        )
        context.chat_data["reply_message_id"] = query.message.message_id
        return

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка ответов администратора (причины отклонения или ответы пользователям)"""
    user = update.effective_user
    text = update.message.text.strip()
    
    if not is_admin(user.id):
        return
    
    # Обработка причины отклонения
    if "rejecting_app" in context.chat_data:
        app_id = context.chat_data["rejecting_app"]
        apps = load_json(APPS_FILE, {})
        
        if app_id in apps:
            apps[app_id]["status"] = STATUS_TEXT["rejected"]
            apps[app_id]["reject_reason"] = text
            apps[app_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_json(APPS_FILE, apps)
            
            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    int(app_id),
                    f"❌ *Ваша заявка отклонена.*\n\n"
                    f"*Причина:*\n{text}\n\n"
                    f"При возникновении вопросов обратитесь к администратору.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Не удалось уведомить пользователя {app_id}: {e}")
            
            # Редактируем оригинальное сообщение
            try:
                await context.bot.edit_message_text(
                    f"❌ *Заявка отклонена.*\n\n"
                    f"*Причина отправлена пользователю:*\n{text}",
                    chat_id=user.id,
                    message_id=context.chat_data.get("reject_message_id"),
                    parse_mode="Markdown"
                )
            except:
                await update.message.reply_text(f"✅ Заявка отклонена. Причина: {text}")
            
            logger.info(f"Админ {user.id} отклонил заявку {app_id}: {text}")
        
        # Очищаем контекст
        context.chat_data.pop("rejecting_app", None)
        context.chat_data.pop("reject_message_id", None)
        return
    
    # Обработка ответа пользователю
    if "replying_to" in context.chat_data:
        target_id = context.chat_data["replying_to"]
        
        try:
            await context.bot.send_message(
                int(target_id),
                f"✉️ *Сообщение от администратора:*\n\n{text}",
                parse_mode="Markdown"
            )
            
            # Редактируем оригинальное сообщение
            try:
                await context.bot.edit_message_text(
                    f"✅ *Ответ отправлен пользователю.*",
                    chat_id=user.id,
                    message_id=context.chat_data.get("reply_message_id"),
                    parse_mode="Markdown"
                )
            except:
                await update.message.reply_text("✅ Ответ отправлен пользователю.")
            
            logger.info(f"Админ {user.id} ответил пользователю {target_id}")
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось отправить сообщение: {e}")
            logger.error(f"Ошибка отправки ответа пользователю {target_id}: {e}")
        
        # Очищаем контекст
        context.chat_data.pop("replying_to", None)
        context.chat_data.pop("reply_message_id", None)
        return

# ================== ЗАПУСК БОТА ==================
def main() -> None:
    """Основная функция запуска бота"""
    # Проверка токена
    if not BOT_TOKEN:
        logger.error("Токен бота не установлен! Укажите BOT_TOKEN в переменных окружения.")
        return
    
    # Проверка администраторов
    if not ADMINS:
        logger.warning("Список администраторов пуст! Укажите ADMINS в переменных окружения.")
    
    # Создание директорий
    ensure_dirs()
    
    # Инициализация приложения
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    
    # Текстовые обработчики (админские ответы имеют приоритет)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^📝|✉️'), handle_admin_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запуск бота
    logger.info(f"Бот версии {BOT_VERSION} запускается...")
    logger.info(f"Администраторы: {ADMINS}")
    
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    main()
