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
    ApplicationBuilder,  # ИЗМЕНЕНО: ApplicationBuilder вместо Application
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
BOT_VERSION = "1.1.8"  # Обновлена версия
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
    # Используем более гибкую проверку
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
            logger.error(f"Error cleaning up app {uid}: {e}")
            if uid in apps:
                del apps[uid]
                removed_count += 1
    
    if removed_count > 0:
        save_json(APPS_FILE, apps)
    
    return removed_count

# ================== ТЕКСТОВЫЕ КОНСТАНТЫ ==================
HELP_TEXT = (
    "❓ *Зачем нужен кадастровый номер?*\n\n"
    "Кадастровый номер нужен для подтверждения\n"
    "проживания Вас в доме.\n\n"
    "📌 По кадастровому номеру *невозможно* узнать:\n"
    "🧾 ФИО, дату рождения, паспортные данные\n"
    "🔒 Данные *не дают* доступа к собственности\n"
    "👤 Их видит *только* администратор дома\n"
    "🗑 После сверки все данные *удаляются* автоматически!\n\n"
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
# ... (остальные функции клавиатур остаются без изменений)

# ================== ОСНОВНЫЕ ОБРАБОТЧИКИ ==================
# ... (основные обработчики без изменений, кроме исправленных мест)

# ================== ЗАПУСК БОТА ==================
async def main_async() -> None:
    """Основная асинхронная функция запуска бота"""
    if not BOT_TOKEN:
        logger.error("Токен бота не установлен!")
        return
    
    ensure_dirs()
    
    # Создаем приложение с использованием ApplicationBuilder
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
    
    # Запуск с обработкой ошибок
    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        
        # Бесконечный цикл обработки
        await asyncio.Event().wait()
        
    except Exception as e:
        logger.error(f"Критическая ошибка бота: {e}")
    finally:
        if app:
            await app.stop()

def main() -> None:
    """Точка входа"""
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
