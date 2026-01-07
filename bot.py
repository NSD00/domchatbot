import os
import json
import logging
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
BOT_VERSION = "1.1.3"
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
        ["📦 Экспорт JSON", "🔄 Перезагрузить бота"]
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
        [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{app_id}")],
    ]
    
    if blocked:
        buttons.append([InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock:{app_id}")])
    else:
        buttons.append([InlineKeyboardButton("🚫 Заблокировать", callback_data=f"block:{app_id}")])
    
    return InlineKeyboardMarkup(buttons)

def create_reject_templates_keyboard(pending_app_id: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру с шаблонами причин отклонения"""
    buttons = []
    for template in REJECT_TEMPLATES:
        # Используем безопасный формат callback_data
        callback_data = f"reject_template_{pending_app_id}_{hash(template) % 10000}"
        buttons.append([InlineKeyboardButton(template, callback_data=callback_data)])
    buttons.append([InlineKeyboardButton("✏️ Своя причина", callback_data=f"reject_custom:{pending_app_id}")])
    return InlineKeyboardMarkup(buttons)

# ================== ОСНОВНЫЕ ОБРАБОТЧИКИ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    
    if not context.user_data.get("step"):
        context.user_data.clear()
    
    if is_blocked(user.id):
        await update.message.reply_text("🚫 Вы заблокированы в системе.")
        return
    
    cleanup_old_apps()
    
    if is_admin(user.id):
        await update.message.reply_text(
            f"👑 Административная панель\nВерсия: {BOT_VERSION}",
            reply_markup=ADMIN_MENU
        )
    else:
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\nВведите номер вашей квартиры:",
            reply_markup=USER_MENU
        )
        context.user_data["step"] = "flat"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    text = update.message.text.strip()
    text_lower = text.lower()
    
    if is_blocked(user.id):
        return
    
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
            status_msg = f"📋 Ваша заявка\n\n🏠 Квартира: {app.get('flat', '—')}\n📌 Статус: {app.get('status', '—')}"
            if app.get("reject_reason"):
                status_msg += f"\n\nПричина отклонения:\n{app['reject_reason']}"
                # Добавляем кнопку для новой заявки после отклонения
                if app.get("status") == STATUS_TEXT["rejected"]:
                    status_msg += "\n\n📝 Чтобы подать новую заявку, нажмите /start"
            await update.message.reply_text(status_msg)
        return
    
    if text == "📨 Написать админу":
        context.user_data["step"] = "contact"
        await update.message.reply_text("✉️ Напишите ваше сообщение администратору:")
        return
    
    if text == "❓ Помощь":
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return
    
    if step == "contact":
        contact_msg = (
            f"✉️ Сообщение от пользователя\n\n"
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
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user.id}")
                    ]])
                )
            except:
                pass
        
        context.user_data.clear()
        await update.message.reply_text("✅ Сообщение отправлено!", reply_markup=USER_MENU)
        return
    
    if step == "flat":
        context.user_data["flat"] = text
        context.user_data["step"] = "cad"
        await update.message.reply_text(
            "📄 Введите кадастровый номер или отправьте файл (фото/PDF):"
        )
        return
    
    if step == "cad":
        cadastre = normalize_cadastre(text)
        
        if not cadastre:
            await update.message.reply_text(
                "❌ Не удалось распознать кадастровый номер.\n"
                "Введите номер в формате: XX:XX:XXXXXXX:XXX"
            )
            return
        
        context.user_data["cad"] = cadastre
        
        confirm_text = (
            f"📋 Проверьте введенные данные:\n\n"
            f"🏠 Квартира: {context.user_data['flat']}\n"
            f"📄 Кадастр:\n"
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
        
        for uid, app in apps.items():
            blocked = is_blocked(int(uid))
            
            app_text = (
                f"👤 Имя: {app.get('name', '—')}\n"
                f"👨‍💻 Ник: @{app.get('username', '—')}\n"
                f"🆔 ID: {uid}\n"
                f"🏠 Квартира: {app.get('flat', '—')}\n"
                f"📌 Статус: {app.get('status', '—')}\n"
            )
            
            if app.get("cadastre"):
                app_text += f"\n📄 Кадастр:\n```\n{app['cadastre']}\n```\n"
            
            if blocked:
                app_text += "\n🚫 Заблокирован"
            
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
                except:
                    await context.bot.send_message(
                        user.id,
                        app_text,
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
            f"📊 Статистика заявок\n\n"
            f"📈 Всего заявок: {total}\n"
            f"⏳ На рассмотрении: {pending}\n"
            f"✅ Одобрено: {approved}\n"
            f"❌ Отклонено: {rejected}"
        )
        
        await update.message.reply_text(stats_text)
        return
    
    if text == "📦 Экспорт JSON":
        if os.path.exists(APPS_FILE):
            await context.bot.send_document(
                user.id,
                document=open(APPS_FILE, "rb"),
                filename="applications.json"
            )
        return
    
    if text == "🔄 Перезагрузить бота":
        await update.message.reply_text("🔄 Перезагружаю бота...")
        # Перезапуск через остановку и запуск
        import sys
        os.execv(sys.executable, [sys.executable] + sys.argv)

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик файлов"""
    user = update.effective_user
    
    if is_blocked(user.id):
        return
    
    if context.user_data.get("step") != "cad":
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
        safe_filename = f"{user.id}_{int(datetime.now().timestamp())}.dat"
        file_path = os.path.join(FILES_DIR, safe_filename)
        
        tg_file = await file.get_file()
        await tg_file.download_to_drive(file_path)
    except Exception as e:
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
            f"🆕 Новая заявка:\n\n"
            f"👤 Имя: {user.full_name}\n"
            f"👨‍💻 Ник: @{user.username if user.username else '—'}\n"
            f"🆔 ID: {user.id}\n"
            f"🏠 Квартира: {context.user_data.get('flat', '—')}\n"
        )
        
        if context.user_data.get("cad"):
            app_info += f"\n📄 Кадастр:\n```\n{context.user_data['cad']}\n```\n"
        
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(
                    admin_id,
                    app_info,
                    parse_mode="Markdown",
                    reply_markup=create_admin_buttons(str(user.id), False)
                )
            except:
                pass
        
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Файл получен! Заявка отправлена на рассмотрение.",
            reply_markup=USER_MENU
        )
    else:
        await update.message.reply_text("❌ Ошибка при сохранении заявки.")

async def handle_user_callback(query, context, data, user):
    """Обработка callback'ов от пользователей"""
    if data == "cad_ok":
        u = user
        apps = load_json(APPS_FILE, {})
        
        apps[str(u.id)] = {
            "user_id": u.id,
            "name": u.full_name,
            "username": u.username,
            "flat": context.user_data["flat"],
            "cadastre": context.user_data["cad"],
            "status": STATUS_TEXT["pending"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        save_json(APPS_FILE, apps)
        
        # Уведомляем администраторов
        app_info = (
            f"🆕 Новая заявка:\n\n"
            f"👤 Имя: {u.full_name}\n"
            f"👨‍💻 Ник: @{u.username if u.username else '—'}\n"
            f"🆔 ID: {u.id}\n"
            f"🏠 Квартира: {context.user_data['flat']}\n"
            f"📄 Кадастр:\n```\n{context.user_data['cad']}\n```"
        )
        
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(
                    admin_id,
                    app_info,
                    parse_mode="Markdown",
                    reply_markup=create_admin_buttons(str(u.id), False)
                )
            except:
                pass
        
        context.user_data.clear()
        await query.edit_message_text("⏳ Заявка отправлена администратору.")
        return
    
    elif data == "cad_no":
        context.user_data.pop("cad", None)
        await query.edit_message_text("Введите кадастровый номер заново:")
        return

async def handle_admin_callback(query, context, data, user):
    """Обработка callback'ов от администраторов"""
    if not is_admin(user.id):
        await query.edit_message_text("❌ У вас нет прав для этого действия.")
        return
    
    # Проверяем формат callback_data
    if not data:
        await query.edit_message_text("❌ Неверный формат команды.")
        return
    
    # Обработка шаблонов причин отклонения
    if data.startswith("reject_template_"):
        # Формат: reject_template_<app_id>_<hash>
        parts = data.split("_")
        if len(parts) >= 3:
            app_id = parts[2]
            # Находим соответствующий шаблон
            template_text = None
            for template in REJECT_TEMPLATES:
                if str(hash(template) % 10000) == parts[3]:
                    template_text = template
                    break
            
            if template_text and app_id:
                await process_rejection(context, app_id, template_text, query)
                return
    
    # Обработка обычных действий с :
    if ":" in data:
        action, target_id = data.split(":", 1)
        
        apps = load_json(APPS_FILE, {})
        blacklist = load_json(BLACKLIST_FILE, [])
        target_id_int = int(target_id)
        
        if action == "block":
            if target_id_int not in blacklist:
                blacklist.append(target_id_int)
                save_json(BLACKLIST_FILE, blacklist)
            await query.edit_message_text("🚫 Пользователь заблокирован.")
            return
        
        if action == "unblock":
            if target_id_int in blacklist:
                blacklist.remove(target_id_int)
                save_json(BLACKLIST_FILE, blacklist)
            await query.edit_message_text("🔓 Пользователь разблокирован.")
            return
        
        if action == "approve":
            if target_id in apps:
                apps[target_id]["status"] = STATUS_TEXT["approved"]
                save_json(APPS_FILE, apps)
                
                try:
                    await context.bot.send_message(
                        target_id_int,
                        "✅ Ваша заявка одобрена!"
                    )
                except:
                    pass
                
                await query.edit_message_text("✅ Заявка одобрена.")
            return
        
        if action == "reject":
            if target_id in apps:
                # Сохраняем ID заявки для отклонения
                context.chat_data["pending_reject_app"] = target_id
                # Показываем шаблоны причин
                await query.edit_message_text(
                    "📝 Выберите причину отклонения:",
                    reply_markup=create_reject_templates_keyboard(target_id)
                )
            return
        
        if action == "reply":
            context.chat_data["replying_to"] = target_id
            await query.edit_message_text("✉️ Введите ответ для пользователя:")
            return
        
        if action == "reject_custom":
            context.chat_data["rejecting_app"] = target_id
            await query.edit_message_text("✏️ Введите свою причину отклонения:")
            return
    
    await query.edit_message_text("❌ Неизвестная команда.")

async def process_rejection(context, app_id, reason, query=None):
    """Обработка отклонения заявки"""
    apps = load_json(APPS_FILE, {})
    
    if app_id in apps:
        apps[app_id]["status"] = STATUS_TEXT["rejected"]
        apps[app_id]["reject_reason"] = reason
        save_json(APPS_FILE, apps)
        
        # Уведомляем пользователя с кнопкой для новой заявки
        try:
            await context.bot.send_message(
                int(app_id),
                f"❌ Ваша заявка отклонена.\n\nПричина: {reason}\n\n📝 Чтобы подать новую заявку, нажмите /start",
                reply_markup=ReplyKeyboardMarkup([["/start"]], resize_keyboard=True)
            )
        except:
            pass
        
        if query:
            await query.edit_message_text(f"✅ Заявка отклонена.\nПричина: {reason}")
        
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
        await process_rejection(context, app_id, text)
        await update.message.reply_text(f"✅ Заявка отклонена.\nПричина: {text}")
        context.chat_data.pop("rejecting_app", None)
        return
    
    # Обработка ответа пользователю
    if "replying_to" in context.chat_data:
        target_id = context.chat_data["replying_to"]
        
        try:
            await context.bot.send_message(
                int(target_id),
                f"✉️ Сообщение от администратора:\n\n{text}"
            )
            await update.message.reply_text("✅ Ответ отправлен пользователю.")
        except:
            await update.message.reply_text("❌ Не удалось отправить сообщение.")
        
        context.chat_data.pop("replying_to", None)
        return

# ================== ЗАПУСК БОТА ==================
def main() -> None:
    """Основная функция запуска бота"""
    if not BOT_TOKEN:
        logger.error("Токен бота не установлен!")
        return
    
    ensure_dirs()
    
    # Создаем приложение с обработкой конфликтов
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    
    # Упрощенный обработчик для администраторских ответов
    async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if is_admin(user.id) and ("rejecting_app" in context.chat_data or "replying_to" in context.chat_data):
            await handle_admin_reply(update, context)
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler), group=1)
    
    # Обычные текстовые сообщения (низкий приоритет)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message), group=2)
    
    logger.info(f"Бот версии {BOT_VERSION} запускается...")
    
    # Запуск с обработкой конфликтов
    try:
        app.run_polling(
            drop_pending_updates=True,
            close_loop=False,
            allowed_updates=Update.ALL_TYPES
        )
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        import time
        time.sleep(5)
        app.run_polling(
            drop_pending_updates=True,
            close_loop=False,
            allowed_updates=Update.ALL_TYPES
        )

if __name__ == "__main__":
    main()
