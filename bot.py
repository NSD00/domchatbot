import os
import json
import logging
import pathlib
import re
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

# Импорты для HTTP сервера
from aiohttp import web

# Импорты для Telegram бота
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup
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
import telegram.error

# ================== НАСТРОЙКИ ЛОГГИРОВАНИЯ ==================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================== КОНФИГУРАЦИЯ ==================
BOT_VERSION = "1.3.0"
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

# Пути к данным
DATA_DIR = "data"
FILES_DIR = os.path.join(DATA_DIR, "files")
CONTACT_FILES_DIR = os.path.join(DATA_DIR, "contact_files")
APPS_FILE = os.path.join(DATA_DIR, "applications.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")

# Настройки
AUTO_CLEAN_DAYS = 30
HTTP_PORT = int(os.getenv("PORT", "8080"))

# Шаблоны причин отклонения
REJECT_TEMPLATES = [
    "Неверный кадастровый номер",
    "Нечитаемое фото/документ",
    "Несоответствие данных",
]

# Типовые ответы для администратора
REPLY_TEMPLATES = [
    "Заявка будет рассмотрена в течение 24 часов",
    "Необходимо предоставить дополнительные документы",
    "Проверяем информацию, ожидайте",
    "Свяжемся с вами для уточнения деталей"
]

# Текстовые константы
HELP_TEXT = (
    "❓ *Зачем нужен кадастровый номер?*\n\n"
    "Кадастровый номер нужен для подтверждения проживания в доме.\n\n"
    "📌 По кадастровому номеру *невозможно* узнать:\n"
    "🧾 ФИО, дату рождения, паспортные данные\n"
    "🔒 Данные *не дают* доступа к собственности\n"
    "👤 Их видит *только* администратор домового чата\n"
    "🗑 После сверки все данные *удаляются* автоматически!\n\n"
    "📋 *Кадастровый номер можно найти:*\n"
    "1. В Выписке ЕГРН\n"
    "2. Договорей купли-продажи\n"
    "3. Договорей найма\n"
    "Если сомневаетесь, можете замазать все персональные данные."
)

STATUS_TEXT = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
}

AUTO_HELP_KEYWORDS = ["зачем", "почему", "кадастр", "кадастров", "помощь", "справка"]

# ================== HTTP СЕРВЕР ДЛЯ UPTIMEROBOT ==================
async def handle_health(request):
    """Обработчик health-check запросов для UptimeRobot"""
    return web.Response(text="🤖 Telegram Bot is running")

async def handle_stats(request):
    """Обработчик статистики"""
    try:
        apps = load_json(APPS_FILE, {})
        total = len(apps)
        pending = sum(1 for a in apps.values() if a.get("status") == STATUS_TEXT["pending"])
        approved = sum(1 for a in apps.values() if a.get("status") == STATUS_TEXT["approved"])
        rejected = sum(1 for a in apps.values() if a.get("status") == STATUS_TEXT["rejected"])
        
        stats = {
            "status": "running",
            "version": BOT_VERSION,
            "applications": {
                "total": total,
                "pending": pending,
                "approved": approved,
                "rejected": rejected
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return web.json_response(stats)
    except Exception as e:
        logger.error(f"Error in stats endpoint: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def start_http_server(port: int = 8080):
    """Запуск HTTP сервера для health checks"""
    app = web.Application()
    
    # Регистрируем маршруты
    app.router.add_get('/', handle_health)
    app.router.add_get('/health', handle_health)
    app.router.add_get('/ping', handle_health)
    app.router.add_get('/status', handle_health)
    app.router.add_get('/stats', handle_stats)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"✅ HTTP сервер запущен на порту {port}")
    logger.info(f"📡 Доступны endpoints: /health, /ping, /stats")
    
    return runner

# ================== УТИЛИТЫ ==================
def ensure_dirs() -> None:
    """Создает необходимые директории"""
    for directory in [DATA_DIR, FILES_DIR, CONTACT_FILES_DIR]:
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
    if not text:
        return False
    
    if len(text) > 4:
        return False
    
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
                # Удаляем файлы заявки
                file_path = data.get("file")
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                
                # Удаляем контактные файлы
                contact_files = data.get("contact_files", [])
                for contact_file in contact_files:
                    if os.path.exists(contact_file):
                        try:
                            os.remove(contact_file)
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

# ================== КЛАВИАТУРЫ ==================
def create_user_menu(user_id: Optional[int] = None) -> ReplyKeyboardMarkup:
    """Создает пользовательское меню с учетом статуса заявки"""
    apps = load_json(APPS_FILE, {})
    has_active_app = user_id and str(user_id) in apps
    
    if has_active_app:
        keyboard_buttons = [
            ["📋 Статус заявки"],
            ["📨 Написать админу"],
            ["❓ Помощь"]
        ]
    else:
        keyboard_buttons = [
            ["📝 Подать заявку"],
            ["📨 Написать админу"],
            ["❓ Помощь"]
        ]
    
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)

def create_user_menu_with_new_app() -> ReplyKeyboardMarkup:
    """Создает меню с кнопкой для новой заявки"""
    keyboard_buttons = [
        ["📝 Подать новую заявку"],
        ["📨 Написать админу"],
        ["❓ Помощь"]
    ]
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Список заявок", "📊 Статистика"],
        ["📦 Экспорт JSON"]
    ],
    resize_keyboard=True
)

def create_cad_confirm_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для подтверждения кадастрового номера"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Всё верно", callback_data="cad_ok"),
            InlineKeyboardButton("❌ Исправить", callback_data="cad_no")
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

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
async def show_context_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает контекстную помощь в зависимости от текущего этапа пользователя"""
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
    
    step = context.user_data.get("step")
    if step == "flat":
        await update.message.reply_text(
            "Пожалуйста, введите номер вашей квартиры:\n\n"
            "📌 *Как вводить:*\n"
            "• Просто цифры: 12\n"
            "• Цифры с буквой: 12А, 25Б",
            parse_mode="Markdown"
        )
    elif step == "cad":
        await update.message.reply_text(
            "Введите кадастровой номер или отправьте файл:\n\n"
            "📌 *Как вводить:*\n"
            "• Формат: XX:XX:XXXXXXX:XXX\n"
            "• Или отправьте фото/PDF документа",
            parse_mode="Markdown"
        )
    elif step == "contact":
        await update.message.reply_text(
            "Напишите сообщение или прикрепите файл:\n\n"
            "📌 *Как отправить:*\n"
            "Избегайте слов: зачем, почему, помощь, справка\n"
            "Иначе будет выведена справочная информация",
            parse_mode="Markdown"
        )

async def send_contact_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user) -> None:
    """Отправка сообщения администратору"""
    contact_data = context.user_data.get("contact_data", {})
    text = contact_data.get("text", "")
    files = contact_data.get("files", [])
    
    if not text and not files:
        await update.message.reply_text(
            "❌ Сообщение пустое. Напишите текст или прикрепите файл.",
            parse_mode="Markdown"
        )
        return
    
    full_contact_msg = (
        f"✉️ *Сообщение от пользователя*\n\n"
        f"👤 Имя: {user.full_name}\n"
        f"👨‍💻 Ник: @{user.username if user.username else '—'}\n"
        f"🆔 ID: {user.id}\n\n"
        f"📝 Сообщение:\n{text if text else '(без текста)'}"
    )
    
    if files:
        full_contact_msg += f"\n\n📎 Прикреплено файлов: {len(files)}"
    
    sent_to_admins = False
    for admin_id in ADMINS:
        try:
            admin_message = await context.bot.send_message(
                admin_id,
                full_contact_msg,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")
                ]])
            )
            
            # Отправляем файлы
            for file_path in files:
                try:
                    ext = pathlib.Path(file_path).suffix.lower()
                    if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                        with open(file_path, "rb") as photo_file:
                            await context.bot.send_photo(
                                admin_id,
                                photo=photo_file,
                                caption=f"Файл от пользователя {user.full_name}",
                                reply_to_message_id=admin_message.message_id
                            )
                    else:
                        with open(file_path, "rb") as doc_file:
                            await context.bot.send_document(
                                admin_id,
                                document=doc_file,
                                caption=f"Файл от пользователя {user.full_name}",
                                reply_to_message_id=admin_message.message_id
                            )
                except Exception as e:
                    logger.error(f"Ошибка отправки файла админу {admin_id}: {e}")
            
            sent_to_admins = True
                    
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения админу {admin_id}: {e}")
    
    # Очищаем временные файлы
    for file_path in files:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass
    
    # Возвращаем пользователя в главное меню
    context.user_data.clear()
    
    if sent_to_admins:
        await update.message.reply_text(
            "✅ Сообщение отправлено администратору!",
            parse_mode="Markdown",
            reply_markup=create_user_menu(user.id)
        )
    else:
        await update.message.reply_text(
            "❌ Не удалось отправить сообщение. Попробуйте позже.",
            parse_mode="Markdown",
            reply_markup=create_user_menu(user.id)
        )

async def process_rejection(context, app_id, reason, query=None) -> bool:
    """Обработка отклонения заявки"""
    apps = load_json(APPS_FILE, {})
    
    if app_id in apps:
        apps[app_id]["status"] = STATUS_TEXT["rejected"]
        apps[app_id]["reject_reason"] = reason
        
        if save_json(APPS_FILE, apps):
            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    int(app_id),
                    f"❌ *Ваша заявка отклонена.*\n\n*Причина:* {reason}",
                    parse_mode="Markdown",
                    reply_markup=create_user_menu_with_new_app()
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
            
            context.chat_data.pop("pending_reject_app", None)
            return True
    
    return False

async def notify_admins_about_new_app(context, user_id: int, user_name: str, username: str, 
                                     flat: str, cadastre: str, file_path: Optional[str] = None) -> None:
    """Уведомляет администраторов о новой заявке"""
    app_info = (
        f"🆕 *Новая заявка:*\n\n"
        f"👤 Имя: {user_name}\n"
        f"👨‍💻 Ник: @{username if username else '—'}\n"
        f"🆔 ID: {user_id}\n"
        f"🏠 Квартира: {flat}\n"
        f"📄 Кадастр: `{cadastre}`"
    )
    
    for admin_id in ADMINS:
        try:
            if file_path and os.path.exists(file_path):
                ext = pathlib.Path(file_path).suffix.lower()
                if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                    with open(file_path, "rb") as photo_file:
                        await context.bot.send_photo(
                            admin_id,
                            photo=photo_file,
                            caption=app_info,
                            parse_mode="Markdown",
                            reply_markup=create_admin_buttons(str(user_id), False)
                        )
                else:
                    with open(file_path, "rb") as doc_file:
                        await context.bot.send_document(
                            admin_id,
                            document=doc_file,
                            caption=app_info,
                            parse_mode="Markdown",
                            reply_markup=create_admin_buttons(str(user_id), False)
                        )
            else:
                await context.bot.send_message(
                    admin_id,
                    app_info,
                    parse_mode="Markdown",
                    reply_markup=create_admin_buttons(str(user_id), False)
                )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
            try:
                await context.bot.send_message(
                    admin_id,
                    app_info + f"\n📎 Файл не отправлен: {e}",
                    parse_mode="Markdown",
                    reply_markup=create_admin_buttons(str(user_id), False)
                )
            except:
                pass

# ================== ОСНОВНЫЕ ОБРАБОТЧИКИ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    
    if not context.user_data.get("step"):
        context.user_data.clear()
    
    # Проверка блокировки
    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text(
            "🚫 Вы заблокированы и не можете пользоваться ботом. "
            "Если Вы считаете, что заблокированы по ошибке, "
            "попросите соседа написать администратору домового чата."
        )
        
        # Автоматически закрываем все активные заявки
        apps = load_json(APPS_FILE, {})
        user_app = apps.get(str(user.id))
        if user_app and user_app.get("status") == STATUS_TEXT["pending"]:
            user_app["status"] = STATUS_TEXT["rejected"]
            user_app["reject_reason"] = "⛔ Пользователь заблокирован"
            save_json(APPS_FILE, apps)
        
        return
    
    cleanup_old_apps()
    
    if is_admin(user.id):
        update_info = (
            f"👑 *Административная панель*\n"
            f"🔄 Версия: `{BOT_VERSION}`\n"
            f"*Что нового в v1.3.0:*\n"
            f"• 📋 Кнопка 'Статус заявки' при активной заявке\n"
            f"• 🔄 Автоматическое обновление меню\n"
            f"• 🎯 Улучшенная навигация\n"
            f"• 🛠 Оптимизированный код"
        )
        
        await update.message.reply_text(
            update_info,
            parse_mode="Markdown",
            reply_markup=ADMIN_MENU
        )
    else:
        await update.message.reply_text(
            "👋 *Добро пожаловать в бот для жильцов дома! ЖК Якоби-Парк*\n\n"
            "Необходимо ввсести данные.",
            parse_mode="Markdown",
            reply_markup=create_user_menu(user.id)
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    
    # Проверка блокировки
    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text(
            "🚫 Вы заблокированы и не можете пользоваться ботом. "
            "Если Вы считаете, что заблокированы по ошибке, "
            "попросите соседа написать администратору домового чата."
        )
        
        # Автоматически закрываем все активные заявки
        apps = load_json(APPS_FILE, {})
        user_app = apps.get(str(user.id))
        if user_app and user_app.get("status") == STATUS_TEXT["pending"]:
            user_app["status"] = STATUS_TEXT["rejected"]
            user_app["reject_reason"] = "⛔ Пользователь заблокирован"
            save_json(APPS_FILE, apps)
        
        return
    
    text = update.message.text.strip()
    text_lower = text.lower()
    
    # Проверка на помощь
    if text == "❓ Помощь" or any(keyword in text_lower for keyword in AUTO_HELP_KEYWORDS):
        await show_context_help(update, context)
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
    
    apps = load_json(APPS_FILE, {})
    user_app = apps.get(str(user.id))
    
    # Проверка статуса заявки
    if text == "📋 Статус заявки":
        if not user_app:
            await update.message.reply_text(
                "📭 У вас нет активных заявок.",
                reply_markup=create_user_menu(user.id)
            )
            return
        
        status_msg = f"📋 *Ваша заявка*\n\n🏠 Квартира: {user_app.get('flat', '—')}\n📌 Статус: {user_app.get('status', '—')}"
        
        if user_app.get("reject_reason"):
            status_msg += f"\n\n*Причина отклонения:*\n{user_app['reject_reason']}"
        
        if user_app.get("status") in [STATUS_TEXT["approved"], STATUS_TEXT["rejected"]]:
            await update.message.reply_text(
                status_msg,
                parse_mode="Markdown",
                reply_markup=create_user_menu_with_new_app()
            )
        else:
            await update.message.reply_text(
                status_msg,
                parse_mode="Markdown",
                reply_markup=create_user_menu(user.id)
            )
        return
    
    if text == "📨 Написать админу":
        context.user_data["step"] = "contact"
        context.user_data["contact_data"] = {"text": "", "files": []}
        
        await update.message.reply_text(
            "✉️ *Напишите ваше сообщение администратору:*",
            parse_mode="Markdown"
        )
        return
    
    if text == "📝 Подать заявку" or text == "📝 Подать новую заявку":
        context.user_data.clear()
        await update.message.reply_text(
            "📝 *Подача заявки на вступление*\n\n"
            "Введите номер вашей квартиры:",
            parse_mode="Markdown"
        )
        context.user_data["step"] = "flat"
        return
    
    if step == "contact":
        context.user_data["contact_data"]["text"] = text
        await send_contact_message(update, context, user)
        return
    
    if step == "flat":
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
            "📄 Введите кадастровый номер или отправьте файл (фото/PDF):",
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
        blocked = len(load_json(BLACKLIST_FILE, []))
        
        stats_text = (
            f"📊 *Статистика заявок*\n\n"
            f"📈 Всего заявок: *{total}*\n"
            f"⏳ На рассмотрении: *{pending}*\n"
            f"✅ Одобрено: *{approved}*\n"
            f"❌ Отклонено: *{rejected}*\n"
            f"⛔ Заблокировано: *{blocked}*\n\n"
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
    
    # Проверка блокировки
    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text(
            "🚫 Вы заблокированы и не можете пользоваться ботом. "
            "Если Вы считаете, что заблокированы по ошибке, "
            "попросите соседа написать администратору домового чата."
        )
        
        # Автоматически закрываем все активные заявки
        apps = load_json(APPS_FILE, {})
        user_app = apps.get(str(user.id))
        if user_app and user_app.get("status") == STATUS_TEXT["pending"]:
            user_app["status"] = STATUS_TEXT["rejected"]
            user_app["reject_reason"] = "⛔ Пользователь заблокирован"
            save_json(APPS_FILE, apps)
        
        return
    
    step = context.user_data.get("step")
    
    # Обработка файлов для сообщения админу
    if step == "contact":
        if update.message.document:
            file = update.message.document
        elif update.message.photo:
            file = update.message.photo[-1]
        else:
            return
        
        try:
            # Скачиваем файл
            timestamp = int(datetime.now().timestamp())
            ext = pathlib.Path(file.file_name or "file").suffix or ".dat" if update.message.document else ".jpg"
            
            safe_filename = f"contact_{user.id}_{timestamp}{ext}"
            file_path = os.path.join(CONTACT_FILES_DIR, safe_filename)
            
            tg_file = await file.get_file()
            await tg_file.download_to_drive(file_path)
            
            # Инициализируем contact_data если его нет
            if "contact_data" not in context.user_data:
                context.user_data["contact_data"] = {"text": "", "files": []}
            
            # Добавляем файл в список
            context.user_data["contact_data"]["files"].append(file_path)
            
            # Проверяем, есть ли текст в сообщении (caption)
            text = update.message.caption or ""
            if text:
                context.user_data["contact_data"]["text"] = text
                # Если есть caption, сразу отправляем сообщение
                await send_contact_message(update, context, user)
            else:
                # Если нет caption, просим добавить текст
                await update.message.reply_text(
                    "✅ Файл получен. Теперь напишите текст сообщения:",
                    parse_mode="Markdown"
                )
                
        except Exception as e:
            logger.error(f"Ошибка загрузки контактного файла: {e}")
            await update.message.reply_text("❌ Ошибка при загрузке файла.")
        return
    
    # Обработка файлов для заявки (кадастровый номер)
    if step != "cad":
        await update.message.reply_text("⚠️ Сначала введите номер квартиры.")
        return
    
    if update.message.document:
        file = update.message.document
    elif update.message.photo:
        file = update.message.photo[-1]
    else:
        return
    
    try:
        # Скачиваем файл
        timestamp = int(datetime.now().timestamp())
        ext = pathlib.Path(file.file_name or "file").suffix or ".dat" if update.message.document else ".jpg"
        
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
        await notify_admins_about_new_app(
            context, user.id, user.full_name, user.username,
            context.user_data.get('flat', '—'), context.user_data.get('cad', '—'), file_path
        )
        
        context.user_data.clear()
        await update.message.reply_text(
            "✅ *Файл получен! Заявка отправлена на рассмотрение.*\n\n"
            "Теперь в меню появилась кнопка '📋 Статус заявки' для отслеживания.",
            parse_mode="Markdown",
            reply_markup=create_user_menu(user.id)
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
            await notify_admins_about_new_app(
                context, user.id, user.full_name, user.username,
                context.user_data['flat'], context.user_data['cad']
            )
            
            context.user_data.clear()
            await query.edit_message_text(
                "✅ *Заявка отправлена на рассмотрение!*\n\n"
                "Теперь в меню появилась кнопка '📋 Статус заявки' для отслеживания.",
                parse_mode="Markdown"
            )
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
                    # Автоматически отклоняем активную заявку
                    if target_id in apps and apps[target_id].get("status") == STATUS_TEXT["pending"]:
                        apps[target_id]["status"] = STATUS_TEXT["rejected"]
                        apps[target_id]["reject_reason"] = "⛔ Пользователь заблокирован"
                        save_json(APPS_FILE, apps)
                    
                    confirmation_text = (
                        f"⛔ *Пользователь заблокирован*\n"
                        f"👤 Имя: {apps[target_id].get('name', '—') if target_id in apps else '—'}\n"
                        f"👨‍💻 Ник: @{target_user_nick}\n"
                        f"🆔 ID: {target_id}\n\n"
                        f"📝 Активная заявка автоматически отклонена."
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
                            parse_mode="Markdown",
                            reply_markup=create_user_menu_with_new_app()
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

# ================== ЗАПУСК БОТА И HTTP СЕРВЕРА ==================
async def main_async() -> None:
    """Основная асинхронная функция запуска бота и HTTP сервера"""
    if not BOT_TOKEN:
        logger.error("❌ Токен бота не установлен!")
        return
    
    ensure_dirs()
    
    logger.info(f"🤖 Запуск Telegram бота версии {BOT_VERSION}")
    logger.info(f"🌐 HTTP порт: {HTTP_PORT}")
    
    # Запускаем HTTP сервер для UptimeRobot
    try:
        http_runner = await start_http_server(HTTP_PORT)
    except Exception as e:
        logger.error(f"❌ Ошибка запуска HTTP сервера: {e}")
        return
    
    # Создаем приложение бота
    try:
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
        
        # Инициализируем и запускаем бота с защитой от конфликтов
        await app.initialize()
        await app.start()
        
        try:
            # Даем время предыдущим процессам завершиться
            await asyncio.sleep(2)
            
            # Запускаем polling
            await app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
                poll_interval=2.0,
                timeout=15,
                bootstrap_retries=3
            )
        except telegram.error.Conflict as e:
            logger.warning(f"⚠️ Обнаружен конфликт сессий: {e}")
            logger.info("🔄 Пытаемся перезапустить через 5 секунд...")
            await asyncio.sleep(5)
            
            await app.updater.stop()
            await app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
                poll_interval=3.0,
                timeout=20
            )
        
        logger.info("✅ Бот успешно запущен и готов к работе!")
        logger.info("📡 Ожидание сообщений...")
        logger.info("🔄 UptimeRobot может мониторить по адресу: /health")
        
        # Бесконечный цикл - работаем до остановки
        stop_event = asyncio.Event()
        await stop_event.wait()
        
    except telegram.error.Conflict as e:
        logger.error(f"💥 Конфликт: другой экземпляр бота уже запущен: {e}")
        logger.info("🔄 Остановите все запущенные инстансы и перезапустите")
        return
    except Exception as e:
        logger.error(f"❌ Критическая ошибка бота: {e}")
        import traceback
        logger.error(f"Техническая информация: {traceback.format_exc()}")
    finally:
        # Останавливаем HTTP сервер
        try:
            await http_runner.cleanup()
            logger.info("🌐 HTTP сервер остановлен")
        except:
            pass
        
        # Останавливаем бота
        try:
            if 'app' in locals():
                await app.stop()
                logger.info("🤖 Бот остановлен")
        except:
            pass

def main() -> None:
    """Точка входа в приложение"""
    # Ждем, чтобы старые процессы завершились
    logger.info("⏳ Ожидание завершения предыдущих процессов...")
    time.sleep(10)
    
    try:
        logger.info("🚀 Запуск приложения...")
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("👋 Приложение остановлено пользователем (Ctrl+C)")
    except Exception as e:
        logger.error(f"💥 Фатальная ошибка: {e}")
        # Ждем перед повторной попыткой
        time.sleep(30)

if __name__ == "__main__":
    main()
