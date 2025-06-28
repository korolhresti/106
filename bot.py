# bot.py - Головний файл для Telegram AI News Bot
# Об'єднує логіку FastAPI, Aiogram та взаємодію з базою даних.
# Версія: 2.0 (покращена та перевірена)

import os
import asyncio
import logging
import uuid
from datetime import datetime

import aiohttp
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.markdown import hbold, hitalic, hcode, hlink
from dotenv import load_dotenv
from fastapi import FastAPI, Request

# --- 1. КОНФІГУРАЦІЯ ТА ГЛОБАЛЬНІ ЗМІННІ ---

load_dotenv()

# Завантаження змінних середовища
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourNewsAIBot")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = os.getenv("GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent")

# Перевірка наявності головного токена
if not TELEGRAM_TOKEN:
    raise ValueError("Необхідно встановити змінну середовища TELEGRAM_BOT_TOKEN")

# Налаштування шляху для вебхука
WEBHOOK_PATH = f"/bot/{TELEGRAM_TOKEN}"
WEBHOOK_FULL_URL = f"{WEBHOOK_URL}{WEBHOOK_PATH}"

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Глобальний пул з'єднань з БД
db_pool = None

# --- 2. ДОПОМІЖНІ ФУНКЦІЇ (УТИЛІТИ) ---

async def get_db_pool():
    """Створює та повертає глобальний пул з'єднань до бази даних."""
    global db_pool
    if db_pool is None:
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL)
            logger.info("Успішно створено пул з'єднань до бази даних.")
        except Exception as e:
            logger.error(f"Не вдалося підключитися до бази даних: {e}")
            raise
    return db_pool

async def execute_query(query, *args, fetch=None):
    """
    Виконує SQL-запит з використанням пулу з'єднань.
    `fetch` може бути 'val' (одне значення), 'row' (один рядок), 'all' (всі рядки).
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as connection:
            if fetch == 'val':
                return await connection.fetchval(query, *args)
            elif fetch == 'row':
                return await connection.fetchrow(query, *args)
            elif fetch == 'all':
                return await connection.fetch(query, *args)
            else:
                return await connection.execute(query, *args)
    except (asyncpg.PostgresError, OSError) as e:
        logger.error(f"Помилка виконання SQL-запиту: {query}, args={args}. Помилка: {e}")
        return None # Повертаємо None у разі помилки

def escape_markdown(text: str) -> str:
    """Екранує спеціальні символи для MarkdownV2."""
    if not isinstance(text, str):
        text = str(text)
    special_chars = r'_*[]()~`>#+-.=|{}!'
    return "".join(f"\\{char}" if char in special_chars else char for char in text)

async def generate_summary_with_ai(text_to_summarize: str) -> str:
    """Генерує резюме тексту за допомогою Gemini AI."""
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не налаштовано.")
        return "Функція AI-резюме тимчасово недоступна."

    prompt = f"Зроби коротке, змістовне та нейтральне резюме новини одним абзацом (3-4 речення) українською мовою. Починай одразу з суті.\n\nТекст новини:\n---\n{text_to_summarize}\n---\n\nРезюме:"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}
    api_url = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers, timeout=45) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "Не вдалося згенерувати резюме.").strip()
                else:
                    error_text = await response.text()
                    logger.error(f"Помилка від Gemini API: {response.status} - {error_text}")
                    return f"Помилка сервісу AI: {response.status}"
    except asyncio.TimeoutError:
        logger.error("Таймаут при запиті до Gemini API.")
        return "Сервіс AI не відповів вчасно."
    except Exception as e:
        logger.error(f"Невідома помилка під час виклику Gemini API: {e}")
        return "Не вдалося підключитися до сервісу AI."

# --- 3. СТАНИ FSM (FINITE STATE MACHINE) ---

class Form(StatesGroup):
    add_source_link = State()
    report_news_id = State()
    report_reason = State()
    feedback_message = State()
    filter_keyword = State()
    comment_news_id = State()
    comment_content = State()
    view_comments_news_id = State()

# --- 4. КЛАВІАТУРИ ---

# Головне меню
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📰 Моя стрічка"), KeyboardButton(text="🔥 Тренди")],
        [KeyboardButton(text="🎯 Фільтри"), KeyboardButton(text="🔖 Закладки")],
        [KeyboardButton(text="📊 Мій профіль"), KeyboardButton(text="💬 Допомога")],
    ], resize_keyboard=True
)

# Меню допомоги
help_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Залишити відгук", callback_data="action_feedback")],
        [InlineKeyboardButton(text="➕ Додати джерело", callback_data="action_add_source")],
        [InlineKeyboardButton(text="✉️ Запросити друга", callback_data="action_invite")],
    ]
)

# --- 5. ІНІЦІАЛІЗАЦІЯ БОТА ТА ДИСПЕТЧЕРА ---

storage = MemoryStorage()
bot = Bot(token=TELEGRAM_TOKEN, parse_mode=ParseMode.MARKDOWN_V2)
dp = Dispatcher(storage=storage)

# --- 6. ОБРОБНИКИ КОМАНД ТА ПОВІДОМЛЕНЬ ---

@dp.message(Command("cancel"))
@dp.message(F.text.casefold() == "відміна")
async def cancel_handler(message: Message, state: FSMContext):
    """Дозволяє користувачу скасувати будь-яку поточну дію."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активних дій для скасування.")
        return

    await state.clear()
    await message.answer("Дію скасовано.", reply_markup=main_keyboard)

@dp.message(CommandStart())
async def handle_start(message: Message, state: FSMContext):
    """Обробник команди /start."""
    await state.clear() # Очищуємо стан на випадок, якщо користувач щось робив
    user = message.from_user
    inviter_id = None
    
    # Обробка реферального посилання
    try:
        payload = message.text.split(maxsplit=1)[1]
        inviter_user_id_record = await execute_query("SELECT user_id FROM invites WHERE invite_code = $1 AND accepted_at IS NULL", payload, fetch='row')
        if inviter_user_id_record:
            inviter_id = inviter_user_id_record['user_id']
    except IndexError:
        pass

    # Реєстрація/оновлення користувача
    user_db_id = await execute_query(
        """INSERT INTO users (telegram_id, username, first_name, language_code, inviter_id, last_active)
           VALUES ($1, $2, $3, $4, $5, NOW())
           ON CONFLICT (telegram_id) DO UPDATE SET
               username = EXCLUDED.username, first_name = EXCLUDED.first_name, last_active = NOW()
           RETURNING id;""",
        user.id, user.username, user.first_name, user.language_code, inviter_id, fetch='val'
    )

    # Створення статистики
    if user_db_id:
        await execute_query("INSERT INTO user_stats (user_id) VALUES ($1) ON CONFLICT DO NOTHING;", user_db_id)
        if inviter_id:
            await execute_query("UPDATE invites SET accepted_at = NOW(), invited_user_id = $1 WHERE invite_code = $2", user_db_id, payload)
            # Тут можна додати логіку нарахування бонусів
            
    welcome_text = f"Привіт, {hbold(escape_markdown(user.first_name))} 👋\n\nЯ — ваш персональний AI\\-агрегатор новин.\nСкористайтесь кнопками нижче, щоб почати."
    await message.answer(welcome_text, reply_markup=main_keyboard)

# Обробники кнопок головного меню
@dp.message(F.text == "📰 Моя стрічка")
async def handle_show_news(message: Message):
    # Ця функція показує одну новину
    user_db_id = await execute_query("SELECT id FROM users WHERE telegram_id = $1", message.from_user.id, fetch='val')
    if not user_db_id:
        return await message.answer("Будь ласка, натисніть /start, щоб зареєструватися.")

    # Отримуємо фільтри
    keywords = await execute_query("SELECT keywords FROM filters WHERE user_id = $1", user_db_id, fetch='val') or []
    
    news_item = await execute_query(
        """SELECT id, title, content, link, source, file_id, media_type FROM news
           WHERE moderation_status = 'approved' AND expires_at > NOW()
           AND id NOT IN (SELECT news_id FROM user_news_views WHERE user_id = $1)
           AND (COALESCE(array_length($2::text[], 1), 0) = 0 OR content ILIKE ANY(ARRAY(SELECT '%' || k || '%' FROM unnest($2) AS k)))
           ORDER BY published_at DESC LIMIT 1;""",
        user_db_id, keywords, fetch='row'
    )

    if not news_item:
        return await message.answer("✅ Нових новин за вашими фільтрами немає. Спробуйте змінити фільтри або перевірте пізніше.")

    # Логування та відправка
    await execute_query("INSERT INTO user_news_views (user_id, news_id, viewed) VALUES ($1, $2, TRUE) ON CONFLICT DO NOTHING;", user_db_id, news_item['id'])
    await execute_query("UPDATE user_stats SET viewed = viewed + 1, last_active = NOW() WHERE user_id = $1;", user_db_id)

    title = escape_markdown(news_item['title'])
    content_short = escape_markdown((news_item['content'] or '')[:700] + '...')
    text = f"{hbold(title)}\n\n{content_short}\n\n{hitalic(f"Джерело: {escape_markdown(news_item['source'])}")}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👍", callback_data=f"act_like_{news_item['id']}"),
            InlineKeyboardButton(text="👎", callback_data=f"act_dislike_{news_item['id']}"),
            InlineKeyboardButton(text="🔖", callback_data=f"act_save_{news_item['id']}"),
            InlineKeyboardButton(text="➡️", callback_data="act_skip_news"),
        ],
        [InlineKeyboardButton(text="📝 Коротко (AI)", callback_data=f"ai_summary_{news_item['id']}")],
        [InlineKeyboardButton(text="💬 Коментарі", callback_data=f"comm_view_{news_item['id']}"),
         InlineKeyboardButton(text="❗ Поскаржитись", callback_data=f"rep_news_{news_item['id']}")]
    ] + ([[InlineKeyboardButton(text="🌐 Читати повністю", url=news_item['link'])]] if news_item['link'] else []))
    
    try:
        if news_item.get('file_id') and news_item.get('media_type') == 'photo':
            await message.answer_photo(photo=news_item['file_id'], caption=text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Помилка відправки новини {news_item['id']}: {e}")
        await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


@dp.callback_query(F.data.startswith("act_"))
async def handle_news_actions(callback: CallbackQuery, state: FSMContext):
    """Обробка дій з новиною: лайк, дизлайк, зберегти, пропустити."""
    action_data = callback.data.split('_')
    action = action_data[1]
    
    user_db_id = await execute_query("SELECT id FROM users WHERE telegram_id = $1", callback.from_user.id, fetch='val')
    if not user_db_id: return await callback.answer("Будь ласка, натисніть /start.", show_alert=True)
    
    if action == "skip":
        await callback.message.delete()
        return await handle_show_news(callback.message) # Показати наступну
    
    news_id = int(action_data[2])
    if action == "like":
        await execute_query("UPDATE user_stats SET liked_count = liked_count + 1 WHERE user_id = $1", user_db_id)
        await callback.answer("❤️ Вподобано!")
    elif action == "dislike":
        await execute_query("UPDATE user_stats SET disliked_count = disliked_count + 1 WHERE user_id = $1", user_db_id)
        await callback.answer("💔 Не сподобалось.")
    elif action == "save":
        await execute_query("INSERT INTO bookmarks (user_id, news_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", user_db_id, news_id)
        await execute_query("UPDATE user_stats SET saved = saved + 1 WHERE user_id = $1", user_db_id)
        await callback.answer("🔖 Збережено в закладки!")

@dp.callback_query(F.data.startswith("ai_summary_"))
async def handle_ai_summary_callback(callback: CallbackQuery):
    """Генерує та надсилає AI-резюме новини."""
    news_id = int(callback.data.split('_')[2])
    
    await callback.answer("⏳ Генерую коротке резюме...", show_alert=False)
    summary = await execute_query("SELECT summary FROM summaries WHERE news_id = $1", news_id, fetch='val')
    
    if not summary:
        content = await execute_query("SELECT content FROM news WHERE id = $1", news_id, fetch='val')
        if not content: return await callback.message.answer("❌ Не вдалося знайти текст для цієї новини.")
        
        summary = await generate_summary_with_ai(content)
        if "Помилка" not in summary and "недоступна" not in summary:
            await execute_query("INSERT INTO summaries (news_id, summary) VALUES ($1, $2) ON CONFLICT DO NOTHING", news_id, summary)

    await callback.message.answer(f"📝 *AI\\-Резюме:*\n\n{escape_markdown(summary)}")

@dp.message(F.text == "🔥 Тренди")
async def handle_trending_news(message: Message):
    """Показує 5 найпопулярніших новин за останні 24 години."""
    trending_news = await execute_query(
        """SELECT n.id, n.title, COUNT(v.id) as views
           FROM news n JOIN user_news_views v ON n.id = v.news_id
           WHERE v.first_viewed_at > NOW() - INTERVAL '24 hours'
           GROUP BY n.id, n.title ORDER BY views DESC LIMIT 5;""",
        fetch='all'
    )
    if not trending_news:
        return await message.answer("Наразі немає трендових новин.")
        
    text = hbold("🔥 Найпопулярніше за добу:\n\n")
    text += "\n".join(f"▫️ {hlink(escape_markdown(item['title']), f'https://t.me/{BOT_USERNAME}?start=news_{item["id"]}')} ({item["views"]} переглядів)" for item in trending_news)
    await message.answer(text, disable_web_page_preview=True)

@dp.message(F.text == "🔖 Закладки")
async def handle_bookmarks(message: Message):
    """Показує збережені новини користувача."""
    user_db_id = await execute_query("SELECT id FROM users WHERE telegram_id = $1", message.from_user.id, fetch='val')
    bookmarks = await execute_query(
        "SELECT n.id, n.title FROM news n JOIN bookmarks b ON n.id = b.news_id WHERE b.user_id = $1 ORDER BY b.created_at DESC LIMIT 20;",
        user_db_id, fetch='all'
    )
    if not bookmarks:
        return await message.answer("У вас ще немає збережених новин.")
    
    text = hbold("🔖 Ваші закладки:\n\n")
    text += "\n".join(f"▫️ {hlink(escape_markdown(item['title']), f'https://t.me/{BOT_USERNAME}?start=news_{item["id"]}')}" for item in bookmarks)
    await message.answer(text, disable_web_page_preview=True)

@dp.message(F.text == "📊 Мій профіль")
async def handle_my_profile(message: Message):
    """Показує статистику та профіль користувача."""
    stats = await execute_query(
        """SELECT u.first_name, s.viewed, s.liked_count, s.saved, u.level, u.badges
           FROM user_stats s JOIN users u ON s.user_id = u.id
           WHERE u.telegram_id = $1;""",
        message.from_user.id, fetch='row'
    )
    if not stats: return await message.answer("Не вдалося завантажити профіль. Спробуйте /start")
    
    text = (
        f"👤 {hbold(escape_markdown(stats['first_name']))}\n\n"
        f"▫️ Рівень: {hcode(stats['level'])}\n"
        f"▫️ Переглянуто новин: {hcode(stats['viewed'])}\n"
        f"▫️ Лайків поставлено: {hcode(stats['liked_count'])}\n"
        f"▫️ Збережено в закладки: {hcode(stats['saved'])}\n"
        f"▫️ Нагороди: {hcode(escape_markdown(', '.join(stats['badges'])) if stats['badges'] else 'Немає')}"
    )
    await message.answer(text)

@dp.message(F.text == "💬 Допомога")
async def handle_help_menu(message: Message):
    await message.answer("Оберіть дію:", reply_markup=help_keyboard)
    
@dp.callback_query(F.data.startswith("action_"))
async def handle_help_actions(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split('_')[1]
    await callback.answer()

    if action == "feedback":
        await state.set_state(Form.feedback_message)
        await callback.message.answer("Будь ласка, напишіть ваш відгук або пропозицію. Ми цінуємо вашу думку!")
    elif action == "add_source":
        await state.set_state(Form.add_source_link)
        await callback.message.answer("Надішліть посилання на джерело (Telegram-канал, RSS-стрічка або сайт).")
    elif action == "invite":
        user_db_id = await execute_query("SELECT id FROM users WHERE telegram_id = $1", callback.from_user.id, fetch='val')
        invite_code = str(uuid.uuid4())[:8]
        await execute_query("INSERT INTO invites (user_id, invite_code) VALUES ($1, $2)", user_db_id, invite_code)
        invite_link = f"https://t.me/{BOT_USERNAME}?start={invite_code}"
        await callback.message.answer(f"✉️ Запросіть друзів та отримуйте бонуси\\!\n\nВаше унікальне посилання:\n{hcode(invite_link)}")


@dp.message(Form.feedback_message)
async def process_feedback(message: Message, state: FSMContext):
    user_db_id = await execute_query("SELECT id FROM users WHERE telegram_id = $1", message.from_user.id, fetch='val')
    await execute_query("INSERT INTO feedback (user_id, message) VALUES ($1, $2)", user_db_id, message.text)
    await state.clear()
    await message.answer("✅ Дякуємо за ваш відгук!")


@dp.message(Form.add_source_link)
async def process_add_source(message: Message, state: FSMContext):
    user_db_id = await execute_query("SELECT id FROM users WHERE telegram_id = $1", message.from_user.id, fetch='val')
    link = message.text
    source_type = "website"
    if "t.me" in link: source_type = "telegram"
    elif "rss" in link or ".xml" in link: source_type = "rss"
        
    await execute_query("INSERT INTO sources (link, type, added_by_user_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", link, source_type, user_db_id)
    await state.clear()
    await message.answer("✅ Дякуємо! Джерело додано і буде перевірено модераторами.")


# --- 7. FastAPI WEBHOOK ---

app = FastAPI(docs_url=None, redoc_url=None)

@app.on_event("startup")
async def on_startup():
    """Дії при старті додатку: ініціалізація БД та встановлення вебхука."""
    await get_db_pool()
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_FULL_URL:
        await bot.set_webhook(url=WEBHOOK_FULL_URL, allowed_updates=dp.resolve_used_update_types())
        logger.info(f"Встановлено вебхук: {WEBHOOK_FULL_URL}")

@app.on_event("shutdown")
async def on_shutdown():
    """Дії при зупинці додатку: закриття з'єднань."""
    logger.info("Завершення роботи...")
    if db_pool:
        await db_pool.close()
        logger.info("Пул з'єднань до БД закрито.")
    await bot.session.close()

@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    """Ендпоінт, що приймає оновлення від Telegram."""
    update = types.Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot=bot, update=update)
    return {"status": "ok"}

@app.get("/")
def healthcheck():
    """Перевірка працездатності сервісу."""
    return {"status": "ok", "version": "2.0"}

