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
BOT_VERSION = "1.5.1"  # Увеличил версию на +0.0.1
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

# НАЗВАНИЕ ЖК
COMPLEX = os.getenv("COMPLEX", "Жилой комплекс")

# ПРОСТОЙ СЛОВАРЬ ДОМОВ
HOUSES = {}

# Автоматически загружаем дома из переменных
i = 1
while True:
    house_address = os.getenv(f"HOUSE{i}")
    chat_link = os.getenv(f"CHAT{i}")
    
    if not house_address:  # Если нет адреса - останавливаемся
        break
    
    HOUSES[f"house{i}"] = {
        "id": f"house{i}",
        "address": house_address,
        "chat_link": chat_link or ""
    }
    
    i += 1

if not HOUSES:
    logger.warning("⚠️ Дома не настроены в переменных окружения!")

# Пути к данным
DATA_DIR = "data"
FILES_DIR = os.path.join(DATA_DIR, "files")
CONTACT_FILES_DIR = os.path.join(DATA_DIR, "contact_files")
APPS_FILE = os.path.join(DATA_DIR, "applications.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")
ARCHIVE_FILE = os.path.join(DATA_DIR, "archive.json")

# Настройки
ARCHIVE_KEEP_DAYS = 30
ACTIVE_APP_EXPIRE_DAYS = 7
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
    "📌 *По кадастровому номеру* *невозможно* узнать:\n"
    "🧾 ФИО, дату рождения, паспортные данные\n"
    "🔒 Данные *не дают* доступа к собственности\n"
    "👤 Их видит *только* администратор домового чата\n"
    "🗑 После сверки все данные *удаляются* автоматически!\n\n"
    "📋 *Кадастровый номер можно найти:*\n"
    "1. В выписке ЕГРН\n"
    "2. Договоре купли-продажи\n"
    "3. Договоре найма\n"
    "Если сомневаетесь, можете замазать все персональные данные на фото."
)

STATUS_TEXT = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
}

AUTO_HELP_KEYWORDS = ["зачем", "почему", "кадастр", "кадастров", "помощь", "справка"]

ADVICE_TEXT = (
    "━━━━━━━━━━━━━━━━\n\n"
    "💡 *Совет для будущих заявок:*\n\n"
    "Администраторам проще проверить заявки, "
    "когда указаны *Имя* и *Телеграм ник*.\n\n"
    "Такие заявки часто рассматриваются быстрее. "
    "Учтите на будущее! 👍\n\n"
    "📌 *Как добавить:*\n"
    "1. В настройках Telegram укажите Имя\n"
    "2. Установите Username (@ваш_ник)"
)

# ================== GITHUB ХРАНИЛИЩЕ ==================
import base64
import aiohttp

class GitHubStorage:
    """Простое хранилище данных в GitHub"""
    
    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN")
        self.repo = os.getenv("GITHUB_REPO")
        
        if not self.token or not self.repo:
            logger.warning("⚠️ GitHub токен или репозиторий не настроены")
            self.enabled = False
        else:
            self.enabled = True
            
        self.base_url = f"https://api.github.com/repos/{self.repo}/contents"
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }
    
    async def upload_json(self, filename: str, data: Dict) -> bool:
        """Загружает JSON данные в GitHub"""
        if not self.enabled:
            return False
            
        try:
            content = json.dumps(data, ensure_ascii=False, indent=2)
            encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/{filename}",
                    headers=self.headers
                ) as response:
                    sha = None
                    if response.status == 200:
                        existing = await response.json()
                        sha = existing.get("sha")
                
                payload = {
                    "message": f"Bot backup: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    "content": encoded,
                    "branch": "main"
                }
                
                if sha:
                    payload["sha"] = sha
                
                async with session.put(
                    f"{self.base_url}/{filename}",
                    headers=self.headers,
                    json=payload
                ) as response:
                    if response.status in [200, 201]:
                        logger.info(f"✅ JSON сохранен в GitHub: {filename}")
                        return True
                    else:
                        error = await response.text()
                        logger.error(f"❌ Ошибка сохранения JSON в GitHub: {error}")
                        return False
                        
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки в GitHub: {e}")
            return False
    
    async def download_json(self, filename: str) -> Optional[Dict]:
        """Скачивает JSON из GitHub"""
        if not self.enabled:
            return None
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/{filename}",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        content = base64.b64decode(data["content"]).decode('utf-8')
                        return json.loads(content)
                    else:
                        logger.warning(f"Файл не найден в GitHub: {filename}")
                        return None
                        
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки из GitHub: {e}")
            return None
    
    async def file_exists(self, filename: str) -> bool:
        """Проверяет существует ли файл в GitHub"""
        if not self.enabled:
            return False
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/{filename}",
                    headers=self.headers
                ) as response:
                    return response.status == 200
        except:
            return False

github_storage = GitHubStorage()

# ================== HTTP СЕРВЕР ==================
async def handle_health(request):
    return web.Response(text="🤖 Telegram Bot is running")

async def handle_stats(request):
    try:
        apps = load_json(APPS_FILE, {})
        archive = load_json(ARCHIVE_FILE, {})
        blacklist = load_json(BLACKLIST_FILE, [])
        
        total_active = len(apps)
        total_archive = len(archive)
        pending = sum(1 for a in apps.values() if a.get("status") == STATUS_TEXT["pending"])
        approved_archive = sum(1 for a in archive.values() if a.get("status") == STATUS_TEXT["approved"])
        rejected_archive = sum(1 for a in archive.values() if a.get("status") == STATUS_TEXT["rejected"])
        
        stats = {
            "status": "running",
            "version": BOT_VERSION,
            "active_applications": {
                "total": total_active,
                "pending": pending,
            },
            "archive": {
                "total": total_archive,
                "approved": approved_archive,
                "rejected": rejected_archive
            },
            "blacklist": len(blacklist),
            "houses": len(HOUSES),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return web.json_response(stats)
    except Exception as e:
        logger.error(f"Error in stats endpoint: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def start_http_server(port: int = 8080):
    app = web.Application()
    
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
    return runner

# ================== УТИЛИТЫ ==================
def ensure_dirs() -> None:
    for directory in [DATA_DIR, FILES_DIR, CONTACT_FILES_DIR]:
        os.makedirs(directory, exist_ok=True)

def load_json(path: str, default) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Ошибка загрузки {path}: {e}")
        return default

def save_json(path: str, data: Any) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except (IOError, TypeError) as e:
        logger.error(f"Ошибка сохранения {path}: {e}")
        return False

def save_json_with_backup(path: str, data: Any) -> bool:
    if not save_json(path, data):
        return False
    
    filename = os.path.basename(path)
    
    if "applications" in filename:
        gh_filename = "applications.json"
    elif "blacklist" in filename:
        gh_filename = "blacklist.json"
    elif "archive" in filename:
        gh_filename = "archive.json"
    else:
        gh_filename = filename
    
    asyncio.create_task(
        github_storage.upload_json(gh_filename, data)
    )
    
    return True

def save_file_locally(file_data: bytes, user_id: int, file_type: str, extension: str = ".jpg") -> str:
    timestamp = int(datetime.now().timestamp())
    
    if file_type == "application":
        filename = f"{user_id}_{timestamp}{extension}"
        local_path = os.path.join(FILES_DIR, filename)
    else:
        filename = f"contact_{user_id}_{timestamp}{extension}"
        local_path = os.path.join(CONTACT_FILES_DIR, filename)
    
    with open(local_path, "wb") as f:
        f.write(file_data)
    
    return local_path

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def is_blocked(user_id: int) -> bool:
    return user_id in load_json(BLACKLIST_FILE, [])

def has_empty_name(user) -> bool:
    if not user.full_name:
        return True
    
    name = user.full_name.strip()
    if not name:
        return True
    
    if len(name) < 2:
        return True
    
    letters_only = ''.join(c for c in name if c.isalpha())
    if len(letters_only) < 1:
        return True
    
    return False

def has_empty_username(user) -> bool:
    return not user.username or not user.username.strip()

def should_show_advice(user) -> bool:
    return has_empty_name(user) or has_empty_username(user)

def has_empty_name_from_data(name: str) -> bool:
    if not name:
        return True
    
    name_str = name.strip()
    if len(name_str) < 2:
        return True
    
    has_letters = any(c.isalpha() for c in name_str)
    return not has_letters

def validate_flat_number(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    
    if len(text) > 4:
        return False
    
    pattern = r'^\d+[a-zA-Zа-яА-ЯёЁ]?$'
    return bool(re.match(pattern, text))

def normalize_cadastre(text: str) -> Optional[str]:
    digits = ''.join(c for c in text if c.isdigit())
    
    if len(digits) < 12 or len(digits) > 20:
        return None
    
    try:
        return f"{digits[:2]}:{digits[2:4]}:{digits[4:-3]}:{digits[-3:]}"
    except IndexError:
        return None

def move_to_archive(app_id: str, app_data: Dict) -> None:
    archive = load_json(ARCHIVE_FILE, {})
    archive[app_id] = app_data
    
    apps = load_json(APPS_FILE, {})
    if app_id in apps:
        del apps[app_id]
        save_json_with_backup(APPS_FILE, apps)
    
    save_json_with_backup(ARCHIVE_FILE, archive)

def cleanup_archive() -> int:
    archive = load_json(ARCHIVE_FILE, {})
    now = datetime.now(timezone.utc)
    removed_count = 0
    
    for app_id, data in list(archive.items()):
        try:
            created_str = data.get("created_at")
            if not created_str:
                continue
                
            created = datetime.fromisoformat(created_str)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            
            if now - created > timedelta(days=ARCHIVE_KEEP_DAYS):
                file_path = data.get("file")
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                
                contact_files = data.get("contact_files", [])
                for contact_file in contact_files:
                    if os.path.exists(contact_file):
                        try:
                            os.remove(contact_file)
                        except OSError:
                        pass
                
                del archive[app_id]
                removed_count += 1
                
        except (KeyError, ValueError, AttributeError) as e:
            logger.error(f"Ошибка при очистке архива {app_id}: {e}")
            if app_id in archive:
                del archive[app_id]
                removed_count += 1
    
    if removed_count > 0:
        save_json_with_backup(ARCHIVE_FILE, archive)
    
    return removed_count

def cleanup_expired_applications() -> int:
    apps = load_json(APPS_FILE, {})
    now = datetime.now(timezone.utc)
    expired_count = 0
    
    for app_id, data in list(apps.items()):
        try:
            if data.get("status") != STATUS_TEXT["pending"]:
                continue
                
            created_str = data.get("created_at")
            if not created_str:
                continue
                
            created = datetime.fromisoformat(created_str)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            
            if now - created > timedelta(days=ACTIVE_APP_EXPIRE_DAYS):
                data["status"] = STATUS_TEXT["rejected"]
                data["reject_reason"] = "⏳ Время рассмотрения истекло."
                
                move_to_archive(app_id, data)
                expired_count += 1
                
                logger.info(f"✅ Заявка {app_id} просрочена и перенесена в архив")
                
        except (KeyError, ValueError, AttributeError) as e:
            logger.error(f"Ошибка при очистке просроченной заявки {app_id}: {e}")
    
    return expired_count

async def notify_expired_applications(context: ContextTypes.DEFAULT_TYPE) -> None:
    archive = load_json(ARCHIVE_FILE, {})
    
    for app_id, data in archive.items():
        if data.get("reject_reason") == "⏳ Время рассмотрения истекло.":
            try:
                user_id = int(app_id)
                
                house_address = "-"
                house_id = data.get("house_id")
                if house_id and house_id in HOUSES:
                    house_address = HOUSES[house_id]["address"]
                
                user_name = data.get('name', '')
                name_display = f", {user_name}" if user_name else ""
                
                await context.bot.send_message(
                    user_id,
                    f"❌ *Ваша заявка отклонена {COMPLEX}:*\n\n"
                    f"*Причина:* Время рассмотрения заявки истекло\n"
                    f"📝 Вы можете подать новую заявку, если это ещё актуально.",
                    parse_mode="Markdown",
                    reply_markup=create_user_menu_with_new_app()
                )
                
                logger.info(f"✅ Уведомление отправлено пользователю {app_id}{name_display}")
                
            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления пользователю {app_id}: {e}")

def cleanup_data() -> int:
    total_removed = 0
    
    archive_removed = cleanup_archive()
    total_removed += archive_removed
    
    expired_removed = cleanup_expired_applications()
    total_removed += expired_removed
    
    apps = load_json(APPS_FILE, {})
    now = datetime.now(timezone.utc)
    files_cleaned = 0
    
    for uid, data in list(apps.items()):
        try:
            created_str = data.get("created_at")
            if not created_str:
                continue
                
            created = datetime.fromisoformat(created_str)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            
            if now - created > timedelta(days=90):
                file_path = data.get("file")
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        files_cleaned += 1
                    except OSError:
                        pass
                
                contact_files = data.get("contact_files", [])
                for contact_file in contact_files:
                    if os.path.exists(contact_file):
                        try:
                            os.remove(contact_file)
                            files_cleaned += 1
                        except OSError:
                            pass
                
        except (KeyError, ValueError, AttributeError) as e:
            logger.error(f"Ошибка при очистке файлов заявки {uid}: {e}")
    
    total_removed += files_cleaned
    
    if total_removed > 0:
        logger.info(f"🧹 Очищено: {archive_removed} архивных, {expired_removed} просроченных, {files_cleaned} файлов")
    
    return total_removed

async def scheduled_cleanup(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔄 Запуск ежедневной очистки данных...")
    
    cleaned_count = cleanup_data()
    
    await notify_expired_applications(context)
    
    if cleaned_count > 0:
        logger.info(f"✅ Ежедневная очистка завершена. Обработано: {cleaned_count}")
    else:
        logger.info("✅ Ежедневная очистка завершена. Данных для очистки не найдено.")

async def load_data_from_github():
    logger.info("🔄 Загрузка данных из GitHub...")
    
    apps_data = await github_storage.download_json("applications.json")
    blacklist_data = await github_storage.download_json("blacklist.json")
    archive_data = await github_storage.download_json("archive.json")
    
    if apps_data:
        save_json(APPS_FILE, apps_data)
        logger.info(f"✅ Загружено {len(apps_data)} активных заявок из GitHub")
    else:
        logger.info("ℹ️ Активные заявки не найдены в GitHub, начинаем с чистого листа")
    
    if blacklist_data:
        save_json(BLACKLIST_FILE, blacklist_data)
        logger.info(f"✅ Загружен черный список ({len(blacklist_data)} пользователей) из GitHub")
    
    if archive_data:
        save_json(ARCHIVE_FILE, archive_data)
        logger.info(f"✅ Загружено {len(archive_data)} архивных заявок из GitHub")
    
    has_files = await github_storage.file_exists("files/")
    if has_files:
        logger.info("ℹ️ Файлы найдены в GitHub (будут загружаться по мере необходимости)")
    
    return apps_data is not None or blacklist_data is not None or archive_data is not None

# ================== КЛАВИАТУРЫ ==================
def create_user_menu(user_id: Optional[int] = None) -> ReplyKeyboardMarkup:
    apps = load_json(APPS_FILE, {})
    has_active_app = user_id and str(user_id) in apps
    
    if has_active_app:
        keyboard_buttons = [
            ["📋 Статус заявки"],
            ["❓ Помощь", "📨 Написать админу"]
        ]
    else:
        keyboard_buttons = [
            ["📝 Подать заявку"],
            ["❓ Помощь", "📨 Написать админу"]
        ]
    
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)

def create_user_menu_with_new_app() -> ReplyKeyboardMarkup:
    keyboard_buttons = [
        ["📝 Подать новую заявку"],
        ["❓ Помощь", "📨 Написать админу"]
    ]
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)

def create_user_menu_after_app_submission() -> ReplyKeyboardMarkup:
    keyboard_buttons = [
        ["📋 Статус заявки"],
        ["❓ Помощь", "📨 Написать админу"]
    ]
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)

def create_user_menu_during_entry() -> ReplyKeyboardMarkup:
    keyboard_buttons = [
        ["❌ Отмена"],
        ["❓ Помощь", "📨 Написать админу"]
    ]
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Список заявок", "📊 Статистика"],
        ["📁 Архив", "⛔ Черный список"],
        ["📦 Экспорт JSON"]
    ],
    resize_keyboard=True
)

def create_cad_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Всё верно", callback_data="cad_ok"),
            InlineKeyboardButton("❌ Исправить", callback_data="cad_no")
        ]
    ])

def create_admin_buttons(app_id: str, blocked: bool = False, status: str = None) -> InlineKeyboardMarkup:
    buttons = []
    
    if status == STATUS_TEXT["pending"]:
        buttons.append([
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{app_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{app_id}")
        ])
        buttons.append([InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{app_id}")])
    
    if blocked:
        buttons.append([InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock:{app_id}")])
    else:
        buttons.append([InlineKeyboardButton("⛔ Заблокировать", callback_data=f"block:{app_id}")])
    
    if not buttons and status:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(f"📋 {status}", callback_data="no_action")
        ]])
    
    return InlineKeyboardMarkup(buttons) if buttons else None

def create_reject_templates_keyboard(app_id: str) -> InlineKeyboardMarkup:
    buttons = []
    for template in REJECT_TEMPLATES:
        callback_data = f"reject_template:{app_id}:{hash(template) % 10000}"
        buttons.append([InlineKeyboardButton(template, callback_data=callback_data)])
    buttons.append([InlineKeyboardButton("✏️ Своя причина", callback_data=f"reject_custom:{app_id}")])
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"cancel:{app_id}")])
    return InlineKeyboardMarkup(buttons)

def create_reply_templates_keyboard(target_user_id: str) -> InlineKeyboardMarkup:
    buttons = []
    for template in REPLY_TEMPLATES:
        callback_data = f"reply_template:{target_user_id}:{hash(template) % 10000}"
        buttons.append([InlineKeyboardButton(template, callback_data=callback_data)])
    buttons.append([InlineKeyboardButton("✏️ Свой ответ", callback_data=f"reply_custom:{target_user_id}")])
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"cancel_reply:{target_user_id}")])
    return InlineKeyboardMarkup(buttons)

# ================== ОБНОВЛЕННЫЕ ФУНКЦИИ СООБЩЕНИЙ ==================
async def send_app_message(user_id: int, context: ContextTypes.DEFAULT_TYPE, 
                          text: str, keyboard=None) -> int:
    """Отправляет или редактирует сообщение заявки"""
    user_data = context.user_data
    
    # Получаем ID предыдущего сообщения
    last_msg_id = user_data.get("last_app_message_id")
    
    try:
        if last_msg_id:
            # Редактируем существующее сообщение
            message = await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=last_msg_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            # Первое сообщение - отправляем новое
            message = await context.bot.send_message(
                user_id,
                text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        
        # Сохраняем ID для следующего редактирования
        user_data["last_app_message_id"] = message.message_id
        return message.message_id
        
    except telegram.error.BadRequest as e:
        # Если не удалось отредактировать (например, сообщение удалено)
        logger.error(f"Ошибка редактирования: {e}")
        message = await context.bot.send_message(
            user_id,
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        user_data["last_app_message_id"] = message.message_id
        return message.message_id
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        return None

async def send_contact_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user) -> None:
    contact_data = context.user_data.get("contact_data", {})
    text = contact_data.get("text", "")
    files = contact_data.get("files", [])
    
    if not text and not files:
        await update.message.reply_text(
            "❌ Сообщение пустое. Напишите текст или прикрепите файл.",
            parse_mode="Markdown"
        )
        return
    
    user_name = user.full_name if user.full_name else "-"
    username_display = f"@{user.username}" if user.username else "-"
    
    full_contact_msg = (
        f"✉️ *Сообщение от пользователя:*\n\n"
        f"👤 Имя: {user_name}\n"
        f"👨‍💻 Ник: {username_display}\n"
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
    
    for file_path in files:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass
    
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

# ================== ОСНОВНЫЕ ОБРАБОТЧИКИ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    
    # Очищаем предыдущие данные
    context.user_data.clear()
    
    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text(
            "🚫 Вы заблокированы и не можете пользоваться ботом. "
            "Если Вы считаете, что заблокированы по ошибке, "
            "попросите соседа написать администратору домового чата."
        )
        
        apps = load_json(APPS_FILE, {})
        user_app = apps.get(str(user.id))
        if user_app and user_app.get("status") == STATUS_TEXT["pending"]:
            user_app["status"] = STATUS_TEXT["rejected"]
            user_app["reject_reason"] = "⛔ Пользователь заблокирован"
            move_to_archive(str(user.id), user_app)
        
        return
    
    # Проверяем и чистим данные при каждом /start
    cleanup_data()
    
    args = context.args
    
    if args and len(args) > 0:
        house_param = args[0]
        
        if house_param in HOUSES:
            context.user_data["house_id"] = house_param
            
            if is_admin(user.id):
                update_info = (
                    f"👑 *Административная панель*\n"
                    f"🔄 Версия: `{BOT_VERSION}`\n"
                    f"🏘️ Домов настроено: {len(HOUSES)}"
                )
                
                await update.message.reply_text(
                    update_info,
                    parse_mode="Markdown",
                    reply_markup=ADMIN_MENU
                )
            else:
                house = HOUSES[house_param]
                
                welcome_text = (
                    f"👋 *Добро пожаловать в бот {COMPLEX}!*\n\n"
                    f"🏠 Ваш дом: {house['address']}\n\n"
                    f"📝 *Для подачи заявки в домовой чат:*\n"
                    f"1. Укажите номер квартиры\n"
                    f"2. Предоставьте кадастровый номер\n\n"
                    f"⏱️ *Рассмотрение:* 1-3 дня\n"
                    f"✅ *После одобрения:* получите ссылку на чат"
                )
                
                await update.message.reply_text(
                    welcome_text,
                    parse_mode="Markdown",
                    reply_markup=create_user_menu()
                )
                
                await asyncio.sleep(1)
                
                context.user_data["step"] = "flat"
                
                await send_app_message(
                    user.id, context,
                    f"📝 *Заявка {COMPLEX}:*\n"
                    f"🏠 Адрес: {house['address']}\n\n"
                    f"Введите номер вашей квартиры:",
                    create_user_menu_during_entry()
                )
            
            return
    
    if is_admin(user.id):
        update_info = (
            f"👑 *Административная панель*\n"
            f"🔄 Версия: `{BOT_VERSION}`\n"
            f"🏘️ Домов настроено: {len(HOUSES)}"
        )
        
        await update.message.reply_text(
            update_info,
            parse_mode="Markdown",
            reply_markup=ADMIN_MENU
        )
    else:
        welcome_text = (
            f"👋 *Добро пожаловать в бот {COMPLEX}!*\n\n"
            f"📝 *Для подачи заявки в домовой чат:*\n"
            f"1. Выберите ваш дом\n"
            f"2. Укажите номер квартиры\n"
            f"3. Предоставьте кадастровый номер\n\n"
            f"⏱️ *Рассмотрение:* 1-3 дня\n"
            f"✅ *После одобрения:* получите ссылку на чат"
        )
        
        await update.message.reply_text(
            welcome_text,
            parse_mode="Markdown",
            reply_markup=create_user_menu()
        )
        
        if len(HOUSES) > 1:
            await asyncio.sleep(1)
            
            houses_text = (
                f"📝 *Заявка {COMPLEX}:*\n\n"
                f"🏠 *Выберите ваш адрес:*\n\n"
            )
            
            for idx, (house_id, house) in enumerate(HOUSES.items(), 1):
                houses_text += f"{idx}. {house['address']}\n"
            
            houses_text += f"\nНапишите номер (1-{len(HOUSES)}):"
            
            context.user_data["step"] = "select_house"
            
            await send_app_message(
                user.id, context,
                houses_text,
                create_user_menu()
            )
        else:
            await asyncio.sleep(1)
            
            house_id = list(HOUSES.keys())[0]
            context.user_data["house_id"] = house_id
            context.user_data["step"] = "flat"
            
            house = HOUSES[house_id]
            await send_app_message(
                user.id, context,
                f"📝 *Заявка {COMPLEX}:*\n"
                f"🏠 Адрес: {house['address']}\n\n"
                f"Введите номер вашей квартиры:",
                create_user_menu_during_entry()
            )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    
    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text(
            "🚫 Вы заблокированы и не можете пользоваться ботом. "
            "Если Вы считаете, что заблокированы по ошибке, "
            "попросите соседа написать администратору домового чата."
        )
        
        apps = load_json(APPS_FILE, {})
        user_app = apps.get(str(user.id))
        if user_app and user_app.get("status") == STATUS_TEXT["pending"]:
            user_app["status"] = STATUS_TEXT["rejected"]
            user_app["reject_reason"] = "⛔ Пользователь заблокирован"
            move_to_archive(str(user.id), user_app)
        
        return
    
    step = context.user_data.get("step")
    
    if step == "contact":
        if update.message.document:
            file = update.message.document
        elif update.message.photo:
            file = update.message.photo[-1]
        else:
            return
        
        try:
            timestamp = int(datetime.now().timestamp())
            ext = pathlib.Path(file.file_name or "file").suffix or ".dat" if update.message.document else ".jpg"
            
            tg_file = await file.get_file()
            file_data = await tg_file.download_as_bytearray()
            
            file_path = save_file_locally(
                bytes(file_data),
                user.id,
                "contact",
                ext
            )
            
            if "contact_data" not in context.user_data:
                context.user_data["contact_data"] = {"text": "", "files": []}
            
            context.user_data["contact_data"]["files"].append(file_path)
            
            text = update.message.caption or ""
            if text:
                if len(text.strip()) == 1:
                    context.user_data.clear()
                    try:
                        os.remove(file_path)
                    except:
                        pass
                    await update.message.reply_text(
                        "❌ *Отправка сообщения отменена.*",
                        parse_mode="Markdown",
                        reply_markup=create_user_menu(user.id)
                    )
                    return
                    
                context.user_data["contact_data"]["text"] = text
                await send_contact_message(update, context, user)
            else:
                await update.message.reply_text(
                    "✅ Файл получен. Теперь напишите текст сообщения:",
                    parse_mode="Markdown",
                    reply_markup=create_user_menu_during_entry()
                )
                
        except Exception as e:
            logger.error(f"Ошибка загрузки контактного файла: {e}")
            await update.message.reply_text("❌ Ошибка при загрузке файла.")
        return
    
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
        timestamp = int(datetime.now().timestamp())
        ext = pathlib.Path(file.file_name or "file").suffix or ".dat" if update.message.document else ".jpg"
        
        tg_file = await file.get_file()
        file_data = await tg_file.download_as_bytearray()
        
        file_path = save_file_locally(
            bytes(file_data),
            user.id,
            "application",
            ext
        )
        
    except Exception as e:
        logger.error(f"Ошибка загрузки файла: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке файла.")
        return
    
    apps = load_json(APPS_FILE, {})
    
    apps[str(user.id)] = {
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username,
        "house_id": context.user_data.get("house_id", ""),
        "flat": context.user_data.get("flat", ""),
        "cadastre": context.user_data.get("cad", ""),
        "file": file_path,
        "status": STATUS_TEXT["pending"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    if save_json_with_backup(APPS_FILE, apps):
        house_id = context.user_data.get("house_id")
        house_address = HOUSES[house_id]["address"] if house_id in HOUSES else "-"
        
        await notify_admins_about_new_app(
            context, user.id, user.full_name, user.username,
            context.user_data.get('flat', '-'), context.user_data.get('cad', '-'), file_path
        )
        
        confirmation_text = (
            f"✅ *Заявка отправлена на рассмотрение!*\n\n"
            f"📋 *Ваша заявка {COMPLEX}:*\n"
            f"🏠 Адрес: {house_address}, кв. {context.user_data.get('flat', '-')}\n"
            f"📄 Кадастровый номер: {context.user_data.get('cad', '-')}\n\n"
            f"⏳ *Статус:* На рассмотрении\n"
            f"📅 *Срок рассмотрения:* 1-3 дня\n\n"
            f"📝 Используйте кнопку «Статус заявки» для отслеживания."
        )
        
        await update.message.reply_text(
            confirmation_text,
            parse_mode="Markdown",
            reply_markup=create_user_menu_after_app_submission()
        )
        
        if should_show_advice(user):
            advice_message = (
                "━━━━━━━━━━━━━━━━\n\n"
                "💡 *Совет для будущих заявки:*\n\n"
                "Администраторам проще проверить заявки, "
                "когда указаны *Имя* и *Телеграм ник*.\n\n"
                "Такие заявки часто рассматриваются быстрее. "
                "Учтите на будущее! 👍\n\n"
                "📌 *Как добавить:*\n"
                "1. В настройках Telegram укажите Имя\n"
                "2. Установите Username (@ваш_ник)"
            )
            await context.bot.send_message(user.id, advice_message, parse_mode="Markdown")
        
        # Очищаем данные после успешной отправки
        context.user_data.clear()
    else:
        await update.message.reply_text("❌ Ошибка при сохранении заявки.")

async def handle_user_callback(query, context, data, user):
    """Обработка callback-ов от пользователя"""
    if data == "cad_ok":
        apps = load_json(APPS_FILE, {})
        
        apps[str(user.id)] = {
            "user_id": user.id,
            "name": user.full_name,
            "username": user.username,
            "house_id": context.user_data["house_id"],
            "flat": context.user_data["flat"],
            "cadastre": context.user_data["cad"],
            "status": STATUS_TEXT["pending"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        if save_json_with_backup(APPS_FILE, apps):
            house_id = context.user_data["house_id"]
            house_address = HOUSES[house_id]["address"] if house_id in HOUSES else "-"
            
            await notify_admins_about_new_app(
                context, user.id, user.full_name, user.username,
                context.user_data['flat'], context.user_data['cad']
            )
            
            try:
                # Пытаемся отредактировать старое сообщение
                await query.edit_message_text(
                    f"✅ *Заявка отправлена на рассмотрение!*",
                    parse_mode="Markdown"
                )
            except telegram.error.BadRequest:
                # Если не получилось отредактировать — ничего страшного
                pass
            
            # Отправляем полное подтверждение новым сообщением
            confirmation_text = (
                f"📋 *Ваша заявка {COMPLEX}:*\n"
                f"🏠 Адрес: {house_address}, кв. {context.user_data['flat']}\n"
                f"📄 Кадастровый номер: {context.user_data['cad']}\n\n"
                f"⏳ *Статус:* На рассмотрении\n"
                f"📅 *Срок рассмотрения:* 1-3 дня\n\n"
                f"Используйте кнопку «📋 Статус заявки» для отслеживания."
            )
            
            await context.bot.send_message(
                user.id,
                confirmation_text,
                parse_mode="Markdown",
                reply_markup=create_user_menu_after_app_submission()
            )
            
            if should_show_advice(user):
                await context.bot.send_message(user.id, ADVICE_TEXT, parse_mode="Markdown")
            
            context.user_data.clear()
        else:
            await query.edit_message_text("❌ Ошибка при сохранении заявки.")
        return
    
    elif data == "cad_no":
        context.user_data.pop("cad", None)
        context.user_data["step"] = "cad"
        
        house_id = context.user_data.get("house_id")
        house_address = HOUSES[house_id]["address"] if house_id in HOUSES else "-"
        flat_number = context.user_data['flat']
        
        try:
            await query.edit_message_text(
                f"📝 *Заявка {COMPLEX}:*\n"
                f"🏠 Адрес: {house_address}, кв. {flat_number}\n\n"
                "Введите кадастровый номер или отправьте файл документа с номером (фото/PDF):",
                parse_mode="Markdown",
                reply_markup=None
            )
        except telegram.error.BadRequest:
            # Если не получилось отредактировать, отправляем новое сообщение
            await send_app_message(
                user.id, context,
                f"📝 *Заявка {COMPLEX}:*\n"
                f"🏠 Адрес: {house_address}, кв. {flat_number}\n\n"
                "Введите кадастровый номер или отправьте файл документа с номером (фото/PDF):",
                create_user_menu_during_entry()
            )
        return

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
async def notify_admins_about_new_app(context, user_id: int, user_name: str, username: str, 
                                     flat: str, cadastre: str, file_path: Optional[str] = None) -> None:
    apps = load_json(APPS_FILE, {})
    user_app = apps.get(str(user_id))
    house_id = user_app.get("house_id") if user_app else None
    house_address = "-"
    
    if house_id and house_id in HOUSES:
        house_address = HOUSES[house_id]["address"]
    
    display_name = user_name if user_name else "-"
    username_display = f"@{username}" if username else "-"
    
    app_info = (
        f"🆕 *Новая заявка {COMPLEX}:*\n\n"
        f"🏠 Адрес: {house_address}, кв. {flat}\n\n"
        f"👤 Имя: {display_name}\n"
        f"👨‍💻 Ник: {username_display}\n"
        f"🆔 ID: {user_id}\n"
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
                            reply_markup=create_admin_buttons(str(user_id), False, STATUS_TEXT["pending"])
                        )
                else:
                    with open(file_path, "rb") as doc_file:
                        await context.bot.send_document(
                            admin_id,
                            document=doc_file,
                            caption=app_info,
                            parse_mode="Markdown",
                            reply_markup=create_admin_buttons(str(user_id), False, STATUS_TEXT["pending"])
                        )
            else:
                await context.bot.send_message(
                    admin_id,
                    app_info,
                    parse_mode="Markdown",
                    reply_markup=create_admin_buttons(str(user_id), False, STATUS_TEXT["pending"])
                )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
            try:
                await context.bot.send_message(
                    admin_id,
                    app_info + f"\n📎 Файл не отправлен: {e}",
                    parse_mode="Markdown",
                    reply_markup=create_admin_buttons(str(user_id), False, STATUS_TEXT["pending"])
                )
            except:
                pass

async def send_simple_invite(context, user_id: int, user_data: Dict) -> bool:
    try:
        house_id = user_data.get("house_id")
        if not house_id or house_id not in HOUSES:
            await context.bot.send_message(
                user_id,
                "✅ *Заявка одобрена!*\n\n"
                "⚠️ Ошибка. Администратор свяжется с вами.",
                parse_mode="Markdown"
            )
            return False
        
        house = HOUSES[house_id]
        
        if not house.get("chat_link"):
            await context.bot.send_message(
                user_id,
                f"✅ *Заявка одобрена {COMPLEX}:*\n\n"
                f"🏠 Адрес: {house['address']}\n\n"
                "⚠️ Ссылка не настроена. Админ свяжется.",
                parse_mode="Markdown"
            )
            return False
        
        user_name = user_data.get('name', '-')
        username = user_data.get('username')
        nick_display = f"@{username}" if username else "-"
        
        message = (
            f"✅ *Заявка одобрена {COMPLEX}:*\n\n"
            f"🏠 Адрес: {house['address']}, кв. {user_data.get('flat', '')}\n"
            f"🔗 *Ссылка на чат дома:*\n"
            f"{house['chat_link']}\n\n"
            f"1. Нажмите на ссылку.\n"
            f"2. Нажмите \"Вступить\".\n"
            f"3. Ждите одобрения админа.\n\n"
            f"⚠️ Никому не передавайте ссылку!"
        )
        
        await context.bot.send_message(
            user_id,
            message,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        
        flat_display = user_data.get('flat', '-')
        if flat_display != '-':
            flat_display = f"кв. {flat_display}"
        
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"📨 Отправлена ссылка:\n"
                    f"🏘️ {COMPLEX}\n"
                    f"🏠 Адрес: {house['address']}, {flat_display}\n"
                    f"👤 Имя: {user_name}\n"
                    f"👨‍💻 Ник: {nick_display}\n"
                    f"🆔 {user_id}",
                    parse_mode="Markdown"
                )
            except:
                pass
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        return False

# ================== ДОПОЛНИТЕЛЬНЫЕ ОБРАБОТЧИКИ ==================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    
    if not is_admin(user.id) and is_blocked(user.id):
        await update.message.reply_text(
            "🚫 Вы заблокированы и не можете пользоваться ботом. "
            "Если Вы считаете, что заблокированы по ошибке, "
            "попросите соседа написать администратору домового чата."
        )
        
        apps = load_json(APPS_FILE, {})
        user_app = apps.get(str(user.id))
        if user_app and user_app.get("status") == STATUS_TEXT["pending"]:
            user_app["status"] = STATUS_TEXT["rejected"]
            user_app["reject_reason"] = "⛔ Пользователь заблокирован"
            move_to_archive(str(user.id), user_app)
        
        return
    
    text = update.message.text.strip()
    text_lower = text.lower()
    
    if text == "❓ Помощь" or any(keyword in text_lower for keyword in AUTO_HELP_KEYWORDS):
        await show_context_help(update, context)
        return
    
    if not is_admin(user.id):
        await handle_user_message(update, context, text, text_lower)
        return
    
    await handle_admin_message(update, context, text)

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                             text: str, text_lower: str) -> None:
    user = update.effective_user
    step = context.user_data.get("step")
    
    apps = load_json(APPS_FILE, {})
    user_app = apps.get(str(user.id))
    
    if text == "📋 Статус заявки":
        if not user_app:
            await update.message.reply_text(
                "📭 У вас нет активных заявок.",
                reply_markup=create_user_menu(user.id)
            )
            return
        
        house_id = user_app.get("house_id")
        house_address = "-"
        if house_id and house_id in HOUSES:
            house_address = HOUSES[house_id]["address"]
        
        status_msg = (
            f"📋 *Статус заявки:*\n\n"
            f"📝 *Заявка {COMPLEX}:*\n"
            f"🏠 Адрес: {house_address}, кв. {user_app.get('flat', '-')}\n"
            f"📄 Кадастровый номер: {user_app.get('cadastre', '-')}\n"
            f"📌 Статус: {user_app.get('status', '-')}"
        )
        
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
            "✉️ *Напишите ваше сообщение администратору:*\n\n"
            "ℹ️ Чтобы отменить отправку, напишите любое сообщение в один символ.",
            parse_mode="Markdown"
        )
        return
    
    if len(text) == 1 and context.user_data.get("step") == "contact":
        context.user_data.clear()
        contact_data = context.user_data.get("contact_data", {})
        if contact_data:
            for file_path in contact_data.get("files", []):
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except:
                    pass
        
        await update.message.reply_text(
            "❌ *Отправка сообщения отменена.*",
            parse_mode="Markdown",
            reply_markup=create_user_menu(user.id)
        )
        return
    
    if text == "❌ Отмена":
        context.user_data.clear()
        await update.message.reply_text(
            "❌ *Ввод данных отменен.*",
            parse_mode="Markdown",
            reply_markup=create_user_menu(user.id)
        )
        return
    
    if text == "📝 Подать заявку" or text == "📝 Подать новую заявку":
        context.user_data.clear()
        
        if not HOUSES:
            await update.message.reply_text(
                "⚠️ *Система временно недоступна.*\n"
                "Обратитесь к администратору.",
                parse_mode="Markdown"
            )
            return
        
        welcome_text = (
            f"👋 *Начинаем оформление заявки {COMPLEX}:*\n\n"
            f"📝 *Вам потребуется:*\n"
            f"1. Выберите ваш дом (если их несколько)\n"
            f"2. Укажите номер квартиры\n"
            f"3. Предоставьте кадастровый номер\n\n"
            f"⏱️ *Срок рассмотрения:* 1-3 дня"
        )
        
        await update.message.reply_text(
            welcome_text,
            parse_mode="Markdown",
            reply_markup=create_user_menu()
        )
        
        await asyncio.sleep(1)
        
        if len(HOUSES) == 1:
            house_id = list(HOUSES.keys())[0]
            context.user_data["house_id"] = house_id
            context.user_data["step"] = "flat"
            
            house = HOUSES[house_id]
            await send_app_message(
                user.id, context,
                f"📝 *Заявка {COMPLEX}:*\n"
                f"🏠 Адрес: {house['address']}\n\n"
                f"Введите номер вашей квартиры:",
                create_user_menu_during_entry()
            )
            return
        
        context.user_data["step"] = "select_house"
        
        houses_text = (
            f"📝 *Заявка {COMPLEX}:*\n\n"
            f"🏠 *Выберите ваш адрес:*\n\n"
        )
        
        for idx, (house_id, house) in enumerate(HOUSES.items(), 1):
            houses_text += f"{idx}. {house['address']}\n"
        
        houses_text += f"\nНапишите номер (1-{len(HOUSES)}):"
        
        await send_app_message(
            user.id, context,
            houses_text,
            create_user_menu()
        )
        return
    
    if step == "select_house":
        try:
            choice = int(text)
            if 1 <= choice <= len(HOUSES):
                house_id = list(HOUSES.keys())[choice-1]
                context.user_data["house_id"] = house_id
                context.user_data["step"] = "flat"
                
                house = HOUSES[house_id]
                await send_app_message(
                    user.id, context,
                    f"📝 *Заявка {COMPLEX}:*\n"
                    f"🏠 Адрес: {house['address']}\n\n"
                    f"Введите номер квартиры:",
                    create_user_menu_during_entry()
                )
            else:
                await update.message.reply_text(
                    f"❌ Введите число от 1 до {len(HOUSES)}",
                    reply_markup=create_user_menu()
                )
        except ValueError:
            await update.message.reply_text(
                "❌ Введите цифру",
                reply_markup=create_user_menu()
            )
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
                parse_mode="Markdown",
                reply_markup=create_user_menu_during_entry()
            )
            return
        
        context.user_data["flat"] = text.strip()
        context.user_data["step"] = "cad"
        
        house_id = context.user_data.get("house_id")
        house_address = HOUSES[house_id]["address"] if house_id in HOUSES else "-"
        
        await send_app_message(
            user.id, context,
            f"📝 *Заявка {COMPLEX}:*\n"
            f"🏠 Адрес: {house_address}, кв. {text.strip()}\n\n"
            "Введите кадастровый номер или отправьте файл документа с номером (фото/PDF):",
            create_user_menu_during_entry()
        )
        return
    
    if step == "cad":
        cadastre = normalize_cadastre(text)
        
        if not cadastre:
            await update.message.reply_text(
                "❌ *Не удалось распознать кадастровый номер.*\n\n"
                "Введите номер в формате:\n"
                "XX:XX:XXXXXXX:XXX\n\n"
                "📌 *Можно:*\n"
                "• Использовать пробелы вместо двоеточий\n"
                "• Написать слитно (только цифры)\n"
                "• Отправить файл документа с номером (фото/PDF)",
                parse_mode="Markdown",
                reply_markup=create_user_menu_during_entry()
            )
            return
        
        context.user_data["cad"] = cadastre
        
        house_id = context.user_data.get("house_id")
        house_address = HOUSES[house_id]["address"] if house_id in HOUSES else "-"
        flat_number = context.user_data['flat']
        
        confirm_text = (
            f"📋 *Проверьте введенные данные:*\n\n"
            f"📝 *Заявка {COMPLEX}:*\n"
            f"🏠 Адрес: {house_address}, кв. {flat_number}\n"
            f"📄 Кадастровый номер: {cadastre}\n\n"
            f"Всё верно?"
        )
        
        await send_app_message(
            user.id, context,
            confirm_text,
            create_cad_confirm_keyboard()
        )
        return

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                              text: str) -> None:
    user = update.effective_user
    apps = load_json(APPS_FILE, {})
    
    if text == "📋 Список заявок":
        if not apps:
            await update.message.reply_text("📭 Нет активных заявок.")
            return
        
        pending_apps = {k: v for k, v in apps.items() 
                       if v.get("status") == STATUS_TEXT["pending"]}
        
        if not pending_apps:
            await update.message.reply_text("✅ Все заявки обработаны.")
            return
        
        for uid, app in pending_apps.items():
            blocked = is_blocked(int(uid))
            
            house_address = "-"
            house_id = app.get("house_id")
            if house_id and house_id in HOUSES:
                house_address = HOUSES[house_id]['address']
            
            user_name = app.get('name', '-')
            username = app.get('username')
            nick_display = f"@{username}" if username else "-"
            
            app_text = (
                f"📝 *Заявка {COMPLEX}:*\n"
                f"🏠 Адрес: {house_address}, кв. {app.get('flat', '-')}\n\n"
                f"👤 Имя: {user_name}\n"
                f"👨‍💻 Ник: {nick_display}\n"
                f"🆔 ID: {uid}\n"
            )
            
            if app.get("cadastre"):
                app_text += f"📄 Кадастр: `{app['cadastre']}`\n\n"
            else:
                app_text += "\n"
            
            app_text += f"📌 Статус: {app.get('status', '-')}"
            
            if blocked:
                app_text += "\n\n⛔ *Заблокирован*"
            
            file_exists = False
            if app.get("file"):
                file_path = app["file"]
                if os.path.exists(file_path):
                    file_exists = True
                else:
                    app_text += "\n\n📎 Файл отсутствует"
            
            keyboard = create_admin_buttons(uid, blocked, app.get("status"))
            
            if file_exists:
                try:
                    file_path = app["file"]
                    ext = pathlib.Path(file_path).suffix.lower()
                    if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                        with open(file_path, "rb") as f:
                            await context.bot.send_photo(
                                user.id,
                                photo=f,
                                caption=app_text,
                                parse_mode="Markdown",
                                reply_markup=keyboard
                            )
                    else:
                        with open(file_path, "rb") as f:
                            await context.bot.send_document(
                                user.id,
                                document=f,
                                caption=app_text,
                                parse_mode="Markdown",
                                reply_markup=keyboard
                            )
                except Exception as e:
                    logger.error(f"Ошибка отправки файла: {e}")
                    app_text += f"\n\n⚠️ Ошибка загрузки файла: {e}"
                    await context.bot.send_message(
                        user.id,
                        app_text,
                        parse_mode="Markdown",
                        reply_markup=keyboard
                    )
            else:
                await context.bot.send_message(
                    user.id,
                    app_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
        return
    
    if text == "📊 Статистика":
        total = len(apps)
        pending = sum(1 for a in apps.values() if a.get("status") == STATUS_TEXT["pending"])
        
        archive = load_json(ARCHIVE_FILE, {})
        total_archive = len(archive)
        approved_archive = sum(1 for a in archive.values() if a.get("status") == STATUS_TEXT["approved"])
        rejected_archive = sum(1 for a in archive.values() if a.get("status") == STATUS_TEXT["rejected"])
        
        blacklist = len(load_json(BLACKLIST_FILE, []))
        
        stats_text = (
            f"📊 *Статистика {COMPLEX}:*\n\n"
            f"📈 Активных заявок: *{total}*\n"
            f"⏳ На рассмотрении: *{pending}*\n\n"
            f"📁 Архив заявок: *{total_archive}*\n"
            f"✅ Одобрено в архиве: *{approved_archive}*\n"
            f"❌ Отклонено в архиве: *{rejected_archive}*\n\n"
            f"⛔ Заблокировано: *{blacklist}*\n"
            f"🏠 Домов настроено: *{len(HOUSES)}*"
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
    
    if text == "📁 Архив":
        await archive_command(update, context)
        return
    
    if text == "⛔ Черный список":
        await blacklist_command(update, context)
        return

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data in ["cad_ok", "cad_no"]:
        await handle_user_callback(query, context, data, user)
    elif data.startswith("archive_"):
        await handle_archive_callback(query, context, data, user)
    elif data.startswith("bl_"):
        await handle_blacklist_callback(query, context, data, user)
    else:
        await handle_admin_callback(query, context, data, user)

async def handle_admin_callback(query, context, data, user):
    if not is_admin(user.id):
        await query.edit_message_text("❌ У вас нет прав для этого действия.")
        return
    
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
    
    if ":" in data:
        parts = data.split(":", 2)
        action = parts[0]
        
        if len(parts) < 2:
            await query.edit_message_text("❌ Неверный формат команды.")
            return
        
        target_id = parts[1]
        
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
        
        target_user_info = ""
        target_user_nick = ""
        house_id = ""
        if target_id in apps:
            target_user_info = f" ({apps[target_id].get('name', 'ID: ' + target_id)})"
            target_user_nick = apps[target_id].get('username', '-')
            house_id = apps[target_id].get('house_id', '')
        
        if action == "block":
            if target_id_int not in blacklist:
                blacklist.append(target_id_int)
                if save_json_with_backup(BLACKLIST_FILE, blacklist):
                    try:
                        await context.bot.send_message(
                            target_id_int,
                            "🚫 *Вы заблокированы в боте.*\n\n"
                            "Если Вы считаете, что заблокированы по ошибке, "
                            "попросите соседа написать администратору домового чата.",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления о блокировке пользователю {target_id}: {e}")
                    
                    if target_id in apps and apps[target_id].get("status") == STATUS_TEXT["pending"]:
                        apps[target_id]["status"] = STATUS_TEXT["rejected"]
                        apps[target_id]["reject_reason"] = "⛔ Пользователь заблокирован"
                        move_to_archive(target_id, apps[target_id])
                    
                    house_address = "-"
                    if house_id and house_id in HOUSES:
                        house_address = HOUSES[house_id]['address']
                    
                    user_name = apps[target_id].get('name', '-') if target_id in apps else '-'
                    username_display = f"@{target_user_nick}" if target_user_nick and target_user_nick != '-' else "-"
                    
                    confirmation_text = (
                        f"⛔ *Пользователь заблокирован {COMPLEX}:*\n"
                        f"🏠 Адрес: {house_address}, кв. {apps[target_id].get('flat', '-') if target_id in apps else '-'}\n"
                        f"👤 Имя: {user_name}\n"
                        f"👨‍💻 Ник: {username_display}\n"
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
                if save_json_with_backup(BLACKLIST_FILE, blacklist):
                    try:
                        await context.bot.send_message(
                            target_id_int,
                            "✅ *Вы разблокированы в боте.*\n\n"
                            "Теперь вы можете пользоваться ботом.",
                            parse_mode="Markdown",
                            reply_markup=create_user_menu(target_id_int)
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления о разблокировке пользователю {target_id}: {e}")
                    
                    house_address = "-"
                    if house_id and house_id in HOUSES:
                        house_address = HOUSES[house_id]['address']
                    
                    user_name = apps[target_id].get('name', '-') if target_id in apps else '-'
                    username_display = f"@{target_user_nick}" if target_user_nick and target_user_nick != '-' else "-"
                    
                    confirmation_text = (
                        f"✅ *Пользователь разблокирован {COMPLEX}:*\n"
                        f"🏠 Адрес: {house_address}, кв. {apps[target_id].get('flat', '-') if target_id in apps else '-'}\n"
                        f"👤 Имя: {user_name}\n"
                        f"👨‍💻 Ник: {username_display}\n"
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
                
                save_json_with_backup(APPS_FILE, apps)
                
                success = await send_simple_invite(
                    context, 
                    target_id_int,
                    apps[target_id]
                )
                
                move_to_archive(target_id, apps[target_id])
                
                if success:
                    await query.edit_message_text(
                        f"✅ Заявка одобрена, ссылка отправлена и заявка перенесена в архив.",
                        parse_mode="Markdown"
                    )
                else:
                    await query.edit_message_text(
                        f"✅ Заявка одобрена, но ошибка отправки ссылки. Заявка перенесена в архив.",
                        parse_mode="Markdown"
                    )
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

async def process_rejection(context, app_id, reason, query=None) -> bool:
    apps = load_json(APPS_FILE, {})
    
    if app_id in apps:
        apps[app_id]["status"] = STATUS_TEXT["rejected"]
        apps[app_id]["reject_reason"] = reason
        
        move_to_archive(app_id, apps[app_id])
        
        try:
            await context.bot.send_message(
                int(app_id),
                f"❌ *Ваша заявка отклонена {COMPLEX}:*\n\n*Причина:* {reason}",
                parse_mode="Markdown",
                reply_markup=create_user_menu_with_new_app()
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об отклонении пользователю {app_id}: {e}")
        
        if query:
            try:
                await query.edit_message_text(f"✅ *Заявка отклонена и перенесена в архив {COMPLEX}:*\nПричина: {reason}", parse_mode="Markdown")
            except:
                await context.bot.send_message(
                    query.from_user.id,
                    f"✅ *Заявка отклонена и перенесена в архив {COMPLEX}:*\nПричина: {reason}",
                    parse_mode="Markdown"
                )
        
        context.chat_data.pop("pending_reject_app", None)
        return True
    
    return False

async def handle_archive_callback(query, context, data, user):
    if not is_admin(user.id):
        return
    
    parts = data.split(":")
    action = parts[0]
    
    if action == "archive_recent":
        archive = load_json(ARCHIVE_FILE, {})
        sorted_apps = sorted(
            archive.items(),
            key=lambda x: x[1].get("created_at", ""),
            reverse=True
        )[:10]
        
        if not sorted_apps:
            await query.edit_message_text("📭 В архиве нет заявок.")
            return
        
        await query.edit_message_text(f"📅 *Последние 10 заявок:*", parse_mode="Markdown")
        await show_archive_apps(context, user.id, sorted_apps, "recent")
        return
    
    elif action == "archive_approved":
        archive = load_json(ARCHIVE_FILE, {})
        approved_apps = [(k, v) for k, v in archive.items() 
                        if v.get("status") == STATUS_TEXT["approved"]]
        
        if not approved_apps:
            await query.edit_message_text("✅ Нет одобренных заявок в архиве.")
            return
        
        await query.edit_message_text(f"✅ *Одобренные заявки ({len(approved_apps)}):*", parse_mode="Markdown")
        await show_archive_apps(context, user.id, approved_apps, "approved")
        return
    
    elif action == "archive_rejected":
        archive = load_json(ARCHIVE_FILE, {})
        rejected_apps = [(k, v) for k, v in archive.items() 
                        if v.get("status") == STATUS_TEXT["rejected"]]
        
        if not rejected_apps:
            await query.edit_message_text("❌ Нет отклоненных заявок в архиве.")
            return
        
        await query.edit_message_text(f"❌ *Отклоненные заявки ({len(rejected_apps)}):*", parse_mode="Markdown")
        await show_archive_apps(context, user.id, rejected_apps, "rejected")
        return
    
    elif action == "archive_search":
        context.chat_data["archive_action"] = "search"
        await query.edit_message_text(
            "🔍 *Поиск в архиве*\n\n"
            "Введите ID пользователя для поиска:",
            parse_mode="Markdown"
        )
        return
    
    elif action == "archive_msg":
        if len(parts) >= 2:
            target_id = parts[1]
            context.chat_data["archive_replying_to"] = target_id
            await query.edit_message_text(
                f"✉️ *Написать пользователю {target_id}*\n\n"
                f"Введите сообщение:",
                parse_mode="Markdown"
            )
        return
    
    elif action == "archive_detail":
        if len(parts) >= 2:
            app_id = parts[1]
            archive = load_json(ARCHIVE_FILE, {})
            app = archive.get(app_id)
            
            if not app:
                await query.edit_message_text("Заявка не найдена в архиве.")
                return
            
            house_address = "-"
            house_id = app.get("house_id")
            if house_id and house_id in HOUSES:
                house_address = HOUSES[house_id]['address']
            
            user_name = app.get('name', '-')
            username = app.get('username')
            nick_display = f"@{username}" if username else "-"
            
            created = app.get('created_at', '-')
            if created != '-':
                try:
                    dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    created = dt.strftime("%d.%m.%Y %H:%M:%S")
                except:
                    pass
            
            detail_text = (
                f"📋 *Подробности заявки {COMPLEX}:*\n\n"
                f"🏠 Адрес: {house_address}\n"
                f"🏢 Квартира: {app.get('flat', '-')}\n\n"
                f"👤 Имя: {user_name}\n"
                f"👨‍💻 Ник: {nick_display}\n"
                f"🆔 ID: {app_id}\n"
                f"📄 Кадастр: `{app.get('cadastre', '-')}`\n\n"
                f"📌 Статус: {app.get('status', '-')}\n"
                f"📅 Дата подачи: {created}\n"
            )
            
            if app.get("reject_reason"):
                detail_text += f"\n*Причина отклонения:*\n{app['reject_reason']}\n"
            
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✉️ Написать", callback_data=f"archive_msg:{app_id}"),
                    InlineKeyboardButton("⬅️ Назад", callback_data="archive_back")
                ]
            ])
            
            try:
                await query.edit_message_text(detail_text, parse_mode="Markdown", reply_markup=keyboard)
            except:
                await context.bot.send_message(
                    user.id,
                    detail_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
        return
    
    elif action == "archive_prev" or action == "archive_next":
        if len(parts) >= 3:
            start_index = int(parts[1])
            title = parts[2]
            
            archive = load_json(ARCHIVE_FILE, {})
            
            if title == "approved":
                apps_list = [(k, v) for k, v in archive.items() 
                           if v.get("status") == STATUS_TEXT["approved"]]
            elif title == "rejected":
                apps_list = [(k, v) for k, v in archive.items() 
                           if v.get("status") == STATUS_TEXT["rejected"]]
            else:
                apps_list = sorted(
                    archive.items(),
                    key=lambda x: x[1].get("created_at", ""),
                    reverse=True
                )[:10]
            
            await show_archive_apps(context, user.id, apps_list, title, start_index)
        return
    
    elif action == "archive_back":
        await archive_command(update, context)
        return

async def show_archive_apps(context, user_id: int, apps_list: List[Tuple[str, Dict]], 
                          title: str, start_index: int = 0, page_size: int = 5) -> None:
    end_index = min(start_index + page_size, len(apps_list))
    
    for i in range(start_index, end_index):
        app_id, app = apps_list[i]
        
        house_address = "-"
        house_id = app.get("house_id")
        if house_id and house_id in HOUSES:
            house_address = HOUSES[house_id]['address']
        
        user_name = app.get('name', '-')
        username = app.get('username')
        nick_display = f"@{username}" if username else "-"
        
        created = ""
        if app.get("created_at"):
            try:
                dt = datetime.fromisoformat(app['created_at'].replace('Z', '+00:00'))
                created = dt.strftime("%d.%m.%Y %H:%M")
            except:
                created = app['created_at'][:10]
        
        app_text = (
            f"📁 *Архивная заявка {COMPLEX} ({i+1}/{len(apps_list)}):*\n"
            f"🏠 Адрес: {house_address}, кв. {app.get('flat', '-')}\n\n"
            f"👤 Имя: {user_name}\n"
            f"👨‍💻 Ник: {nick_display}\n"
            f"🆔 ID: {app_id}\n"
        )
        
        if app.get("cadastre"):
            app_text += f"📄 Кадастр: `{app['cadastre']}`\n\n"
        else:
            app_text += "\n"
        
        app_text += f"📌 Статус: {app.get('status', '-')}\n"
        app_text += f"📅 Дата: {created}\n"
        
        if has_empty_name_from_data(user_name) or not username:
            app_text += "⚠️ *Имя/ник отсутствуют*\n\n"
        
        if app.get("reject_reason") and app.get("status") == STATUS_TEXT["rejected"]:
            app_text += f"\n*Причина отклонения:*\n{app['reject_reason']}\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✉️ Написать", callback_data=f"archive_msg:{app_id}")]
        ])
        
        file_exists = False
        if app.get("file") and os.path.exists(app["file"]):
            file_exists = True
        
        try:
            if file_exists:
                file_path = app["file"]
                ext = pathlib.Path(file_path).suffix.lower()
                if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                    with open(file_path, "rb") as f:
                        await context.bot.send_photo(
                            user_id,
                            photo=f,
                            caption=app_text,
                            parse_mode="Markdown",
                            reply_markup=keyboard
                        )
                else:
                    with open(file_path, "rb") as f:
                        await context.bot.send_document(
                            user_id,
                            document=f,
                            caption=app_text,
                            parse_mode="Markdown",
                            reply_markup=keyboard
                        )
            else:
                await context.bot.send_message(
                    user_id,
                    app_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
        except Exception as e:
            logger.error(f"Ошибка отправки архивной заявки: {e}")
            app_text += f"\n⚠️ Ошибка загрузки файла: {e}"
            await context.bot.send_message(
                user_id,
                app_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        
        await asyncio.sleep(0.5)
    
    if len(apps_list) > page_size:
        nav_buttons = []
        if start_index > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"archive_prev:{start_index-page_size}:{title}"))
        if end_index < len(apps_list):
            nav_buttons.append(InlineKeyboardButton("Далее ➡️", callback_data=f"archive_next:{end_index}:{title}"))
        
        if nav_buttons:
            await context.bot.send_message(
                user_id,
                f"📄 Страница {start_index//page_size + 1}/{(len(apps_list)-1)//page_size + 1}",
                reply_markup=InlineKeyboardMarkup([nav_buttons])
            )

async def archive_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    
    cleanup_archive()
    
    archive = load_json(ARCHIVE_FILE, {})
    
    if not archive:
        await update.message.reply_text("📁 Архив пуст.")
        return
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Последние 10", callback_data="archive_recent"),
            InlineKeyboardButton("🔍 Поиск по ID", callback_data="archive_search")
        ],
        [
            InlineKeyboardButton("✅ Одобренные", callback_data="archive_approved"),
            InlineKeyboardButton("❌ Отклоненные", callback_data="archive_rejected")
        ]
    ])
    
    total = len(archive)
    approved = sum(1 for a in archive.values() if a.get("status") == STATUS_TEXT["approved"])
    rejected = sum(1 for a in archive.values() if a.get("status") == STATUS_TEXT["rejected"])
    
    text = (
        f"📁 *Архив заявок {COMPLEX}:*\n\n"
        f"📊 Статистика:\n"
        f"• Всего: {total} заявок\n"
        f"• Одобрено: {approved}\n"
        f"• Отклонено: {rejected}\n"
        f"• Хранятся: {ARCHIVE_KEEP_DAYS} дней\n\n"
        f"Выберите действие:"
    )
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def blacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    
    blacklist = load_json(BLACKLIST_FILE, [])
    apps = load_json(APPS_FILE, {})
    archive = load_json(ARCHIVE_FILE, {})
    
    if not blacklist:
        await update.message.reply_text("📭 Черный список пуст.")
        return
    
    text = f"⛔ *Черный список {COMPLEX}:*\n\n"
    
    for i, user_id in enumerate(blacklist, 1):
        user_info = f"🆔 `{user_id}`"
        
        if str(user_id) in apps:
            app = apps[str(user_id)]
            name = app.get('name', '-')
            username = f" @{app.get('username')}" if app.get('username') else ""
            user_info = f"🆔 `{user_id}` 👤 {name}{username}"
        
        elif str(user_id) in archive:
            app = archive[str(user_id)]
            name = app.get('name', '-')
            username = f" @{app.get('username')}" if app.get('username') else ""
            user_info = f"🆔 `{user_id}` 👤 {name}{username} 📁 (в архиве)"
        
        text += f"{i}. {user_info}\n"
    
    text += f"\n📊 Всего: {len(blacklist)} пользователей"
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Добавить по ID", callback_data="bl_add"),
            InlineKeyboardButton("🗑 Удалить по ID", callback_data="bl_remove")
        ],
        [InlineKeyboardButton("🔄 Обновить", callback_data="bl_refresh")]
    ])
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def handle_blacklist_callback(query, context, data, user):
    if not is_admin(user.id):
        return
    
    if data == "bl_add":
        context.chat_data["blacklist_action"] = "add"
        await query.edit_message_text(
            "➕ *Добавление в черный список*\n\n"
            "Введите ID пользователя для добавления:\n"
            "ℹ️ *Формат:* только цифры\n"
            "❌ *Для отмены:* введите любой нецифровой символ или 0",
            parse_mode="Markdown"
        )
        return
    
    if data == "bl_remove":
        context.chat_data["blacklist_action"] = "remove"
        await query.edit_message_text(
            "🗑 *Удаление из черного списка*\n\n"
            "Введите ID пользователя для удаления:\n"
            "ℹ️ *Формат:* только цифры\n"
            "❌ *Для отмены:* введите любой нецифровой символ или 0",
            parse_mode="Markdown"
        )
        return
    
    if data == "bl_refresh":
        await blacklist_command(update, context)
        return

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = update.message.text.strip()
    
    if not is_admin(user.id):
        return
    
    if "rejecting_app" in context.chat_data:
        app_id = context.chat_data["rejecting_app"]
        if await process_rejection(context, app_id, text):
            await update.message.reply_text(f"✅ *Заявка отклонена и перенесена в архив {COMPLEX}:*\nПричина: {text}", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Ошибка при отклонении заявки.")
        context.chat_data.pop("rejecting_app", None)
        return
    
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
    
    if "blacklist_action" in context.chat_data:
        action = context.chat_data["blacklist_action"]
        
        if not text.isdigit() or text == "0":
            await update.message.reply_text(
                "❌ *Действие отменено.*\n"
                "Черный список не изменен.",
                parse_mode="Markdown",
                reply_markup=ADMIN_MENU
            )
            context.chat_data.pop("blacklist_action", None)
            return
        
        try:
            target_id = int(text)
            
            if target_id <= 0:
                await update.message.reply_text("❌ ID должен быть положительным числом.")
                return
                
            if target_id < 100000:
                await update.message.reply_text("⚠️ ID слишком маленький. Убедитесь в правильности.")
                return
                
        except ValueError:
            await update.message.reply_text("❌ Неверный формат ID. Введите только цифры.")
            return
        
        blacklist = load_json(BLACKLIST_FILE, [])
        
        if action == "add":
            if target_id in blacklist:
                await update.message.reply_text(f"⚠️ Пользователь `{target_id}` уже в черном списке.", parse_mode="Markdown")
            else:
                blacklist.append(target_id)
                if save_json_with_backup(BLACKLIST_FILE, blacklist):
                    
                    try:
                        await context.bot.send_message(
                            target_id,
                            "🚫 *Вы заблокированы в боте.*\n\n"
                            "Если Вы считаете, что заблокированы по ошибке, "
                            "попросите соседа написать администратору домового чата.",
                            parse_mode="Markdown"
                        )
                    except:
                        pass
                    
                    apps = load_json(APPS_FILE, {})
                    if str(target_id) in apps and apps[str(target_id)].get("status") == STATUS_TEXT["pending"]:
                        apps[str(target_id)]["status"] = STATUS_TEXT["rejected"]
                        apps[str(target_id)]["reject_reason"] = "⛔ Пользователь заблокирован"
                        move_to_archive(str(target_id), apps[str(target_id)])
                    
                    await update.message.reply_text(f"✅ Пользователь `{target_id}` добавлен в черный список.", parse_mode="Markdown")
                else:
                    await update.message.reply_text("❌ Ошибка при сохранении черного списка.")
        
        elif action == "remove":
            if target_id in blacklist:
                blacklist.remove(target_id)
                if save_json_with_backup(BLACKLIST_FILE, blacklist):
                    
                    try:
                        await context.bot.send_message(
                            target_id,
                            "✅ *Вы разблокированы в боте.*\n\n"
                            "Теперь вы можете пользоваться ботом.",
                            parse_mode="Markdown",
                            reply_markup=create_user_menu(target_id)
                        )
                    except:
                        pass
                    
                    await update.message.reply_text(f"✅ Пользователь `{target_id}` удален из черного списка.", parse_mode="Markdown")
                else:
                    await update.message.reply_text("❌ Ошибка при сохранении черного списка.")
            else:
                await update.message.reply_text(f"ℹ️ Пользователь `{target_id}` не найден в черном списке.", parse_mode="Markdown")
        
        context.chat_data.pop("blacklist_action", None)
        return
    
    if "archive_action" in context.chat_data:
        action = context.chat_data["archive_action"]
        
        if action == "search":
            archive = load_json(ARCHIVE_FILE, {})
            
            if text in archive:
                apps_list = [(text, archive[text])]
                await update.message.reply_text(f"🔍 *Найдена заявка {COMPLEX}:*", parse_mode="Markdown")
                await show_archive_apps(context, user.id, apps_list, "search")
            else:
                await update.message.reply_text(f"❌ Заявка с ID `{text}` не найдена в архиве.", parse_mode="Markdown")
            
            context.chat_data.pop("archive_action", None)
            return
    
    if "archive_replying_to" in context.chat_data:
        target_id = context.chat_data["archive_replying_to"]
        
        try:
            await context.bot.send_message(
                int(target_id),
                f"✉️ *Сообщение от администратора:*\n\n{text}",
                parse_mode="Markdown"
            )
            await update.message.reply_text(f"✅ *Сообщение отправлено пользователю `{target_id}`.*", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось отправить сообщение: {e}")
        
        context.chat_data.pop("archive_replying_to", None)
        return

async def show_context_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            "Введите кадастровый номер:\n\n"
            "📌 *Как вводить:*\n"
            "• Формат: XX:XX:XXXXXXX:XXX\n\n"
            "📌 *Можно:*\n"
            "• Использовать пробелы вместо двоеточий\n"
            "• Написать слитно (только цифры)\n"
            "• Отправить файл документа с номером (фото/PDF)",
            parse_mode="Markdown"
        )
    elif step == "contact":
        await update.message.reply_text(
            "Напишите сообщение или прикрепите файл:\n\n"
            "📌 *Как отправить:*\n"
            "Избегайте слов: зачем, почему, помощь, справка, кадастр.\n"
            "Иначе бот будет выводить справочную информацию.\n"
            "ℹ️ Чтобы отменить отправку, напишите любое сообщение в один символ.",
            parse_mode="Markdown"
        )

# ================== ЗАПУСК БОТА И HTTP СЕРВЕРА ==================
async def main_async() -> None:
    if not BOT_TOKEN:
        logger.error("❌ Токен бота не установлен!")
        return
    
    ensure_dirs()
    
    logger.info(f"🤖 Запуск Telegram бота версии {BOT_VERSION}")
    
    await load_data_from_github()
    
    initial_cleanup = cleanup_data()
    if initial_cleanup > 0:
        logger.info(f"🧹 Первоначальная очистка: {initial_cleanup} записей")
    
    logger.info(f"🏘️ Название ЖК: {COMPLEX}")
    logger.info(f"🏠 Домов настроено: {len(HOUSES)}")
    logger.info(f"🌐 HTTP порт: {HTTP_PORT}")
    
    if github_storage.enabled:
        logger.info("✅ GitHub backup включен")
    else:
        logger.warning("⚠️ GitHub backup отключен (проверьте GITHUB_TOKEN и GITHUB_REPO)")
    
    try:
        http_runner = await start_http_server(HTTP_PORT)
    except Exception as e:
        logger.error(f"❌ Ошибка запуска HTTP сервера: {e}")
        return
    
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("archive", archive_command))
        app.add_handler(CommandHandler("blacklist", blacklist_command))
        
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
        
        async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            text = update.message.text.strip()
            
            if len(text) == 1 and context.user_data.get("step") == "contact":
                context.user_data.clear()
                await update.message.reply_text(
                    "❌ *Отправка сообщения отменена.*",
                    parse_mode="Markdown",
                    reply_markup=create_user_menu(user.id)
                )
                return
            
            if is_admin(user.id) and ("rejecting_app" in context.chat_data or "replying_to_custom" in context.chat_data 
                                    or "blacklist_action" in context.chat_data or "archive_action" in context.chat_data
                                    or "archive_replying_to" in context.chat_data):
                await handle_admin_reply(update, context)
        
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler), group=1)
        
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message), group=2)
        
        # Запускаем фоновую задачу для ежедневной очистки (если доступно)
        if hasattr(app, 'job_queue') and app.job_queue is not None:
            app.job_queue.run_repeating(
                scheduled_cleanup,
                interval=86400,  # 24 часа в секундах
                first=10,       # Первый запуск через 10 секунд
                name="daily_cleanup"
            )
            logger.info("✅ Фоновая задача ежедневной очистки запущена")
        else:
            logger.warning("⚠️ JobQueue не доступен. Очистка будет выполняться только при вызове /start")
        
        await app.initialize()
        await app.start()
        
        try:
            await asyncio.sleep(2)
            
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
        
        if hasattr(app, 'job_queue') and app.job_queue is not None:
            logger.info("🧹 Ежедневная очистка запланирована (каждые 24 часа)")
        else:
            logger.info("ℹ️ Очистка выполняется при каждом вызове /start")
        
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
        try:
            await http_runner.cleanup()
            logger.info("🌐 HTTP сервер остановлен")
        except:
            pass
        
        try:
            if 'app' in locals():
                await app.stop()
                logger.info("🤖 Бот остановлен")
        except:
            pass

def main() -> None:
    logger.info("⏳ Ожидание завершения предыдущих процессов...")
    time.sleep(10)
    
    try:
        logger.info("🚀 Запуск приложения...")
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("👋 Приложение остановлено пользователем (Ctrl+C)")
    except Exception as e:
        logger.error(f"💥 Фатальная ошибка: {e}")
        time.sleep(30)

if __name__ == "__main__":
    main()
