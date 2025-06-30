import asyncio
import logging
from datetime import datetime, timedelta, date
from decimal import Decimal
import json
import re
from typing import List, Optional, Dict, Any, Union

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hbold, hlink

from aiohttp import ClientSession
from gtts import gTTS # Для генерації аудіо
import asyncpg # Для роботи з PostgreSQL

# --- Конфігурація та ініціалізація (початок) ---
# Заміни ці значення на свої
API_TOKEN = "YOUR_BOT_TOKEN"
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
DATABASE_URL = "postgresql://user:password@host:port/database"
ADMIN_IDS = [123456789, 987654321] # Заміни на ID адмінів

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ініціалізація бота та диспетчера
bot = Bot(token=API_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Пул підключень до БД
db_pool = None

async def get_db_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
    return db_pool

# --- Класи даних (dataclasses) ---
class User:
    def __init__(self, id: int, username: Optional[str] = None, first_name: Optional[str] = None,
                 last_name: Optional[str] = None, created_at: Optional[datetime] = None,
                 is_admin: bool = False, last_active: Optional[datetime] = None,
                 language: str = 'uk', blocked_users: Optional[List[int]] = None):
        self.id = id
        self.username = username
        self.first_name = first_name
        self.last_name = last_name
        self.created_at = created_at if created_at else datetime.now()
        self.is_admin = is_admin
        self.last_active = last_active if last_active else datetime.now()
        self.language = language
        self.blocked_users = blocked_users if blocked_users is not None else []

class News:
    def __init__(self, id: int, title: str, content: str, source_id: Optional[int], source_url: Optional[str],
                 image_url: Optional[str], published_at: datetime, lang: str,
                 tone: Optional[str], sentiment_score: Optional[float], country_code: Optional[str],
                 media_type: Optional[str], ai_summary: Optional[str] = None,
                 ai_classified_topics: Optional[List[str]] = None, moderation_status: str = 'approved',
                 moderated_by: Optional[int] = None, moderated_at: Optional[datetime] = None,
                 expires_at: Optional[datetime] = None):
        self.id = id
        self.title = title
        self.content = content
        self.source_id = source_id
        self.source_url = source_url
        self.image_url = image_url
        self.published_at = published_at
        self.lang = lang
        self.tone = tone
        self.sentiment_score = sentiment_score
        self.country_code = country_code
        self.media_type = media_type
        self.ai_summary = ai_summary
        self.ai_classified_topics = ai_classified_topics
        self.moderation_status = moderation_status
        self.moderated_by = moderated_by
        self.moderated_at = moderated_at
        self.expires_at = expires_at if expires_at else published_at + timedelta(days=7) # Новини живуть 7 днів

class Product:
    def __init__(self, id: int, user_id: int, product_name: str, description: str, price: Decimal,
                 currency: str, image_url: Optional[str], e_point_location_text: str,
                 status: str, created_at: datetime):
        self.id = id
        self.user_id = user_id
        self.product_name = product_name
        self.description = description
        self.price = price
        self.currency = currency
        self.image_url = image_url
        self.e_point_location_text = e_point_location_text
        self.status = status
        self.created_at = created_at

# --- FSM (Finite State Machine) States ---
class AddNews(StatesGroup):
    waiting_for_news_url = State()
    waiting_for_news_lang = State()
    confirm_news = State()

class NewsBrowse(StatesGroup):
    Browse_news = State()
    news_index = State()
    news_ids = State()
    last_message_id = State()

class AIAssistant(StatesGroup):
    waiting_for_question = State()
    waiting_for_news_id_for_question = State()
    waiting_for_term_to_explain = State()
    waiting_for_fact_to_check = State()
    fact_check_news_id = State()
    waiting_for_detailed_question = State()
    detailed_question_news_id = State()
    interview_mode = State()
    interview_news_id = State()
    interview_chat_history = State()
    waiting_for_context_term = State()
    context_news_id = State()
    press_conference_mode = State()
    press_conference_news_id = State()
    press_conference_chat_history = State()
    current_speaker = State()
    waiting_for_audience_summary_type = State()
    audience_summary_news_id = State()
    waiting_for_what_if_query = State()
    what_if_news_id = State()
    waiting_for_youtube_interview_url = State() # NEW: State for YouTube interview news
    youtube_interview_news_id = State() # To store news ID if creating

class DirectMessage(StatesGroup):
    waiting_for_message_text = State()
    recipient_user_id = State()
    original_product_id = State()

class ReviewState(StatesGroup):
    waiting_for_seller_rating = State()
    waiting_for_seller_review = State()
    waiting_for_buyer_rating = State()
    waiting_for_buyer_review = State()
    review_transaction_id = State()

class SellProduct(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_price = State()
    waiting_for_currency = State()
    waiting_for_image = State()
    waiting_for_e_point = State()
    confirm_product = State()
    editing_field = State()
    editing_product_id = State()
    deleting_product_id = State()

class ProductTransaction(StatesGroup):
    awaiting_buyer_confirmation = State()
    awaiting_seller_confirmation = State()
    transaction_id = State()
    product_id = State()
    seller_id = State()
    buyer_id = State()
    waiting_for_negotiation_query = State() # NEW: State for buyer negotiation query
    negotiation_product_id = State() # NEW: To store product ID for negotiation

class SalesAssistance(StatesGroup):
    waiting_for_sales_query = State()
    sales_product_id = State()

# --- Допоміжні функції бази даних ---
async def create_tables():
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        # Створення таблиці users
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                username VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                is_admin BOOLEAN DEFAULT FALSE,
                last_active TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                language VARCHAR(10) DEFAULT 'uk',
                blocked_users BIGINT[] DEFAULT ARRAY[]::BIGINT[]
            );
        """)

        # Створення таблиці news
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_id INTEGER,
                source_url TEXT,
                image_url TEXT,
                published_at TIMESTAMP WITH TIME ZONE,
                lang VARCHAR(10) NOT NULL,
                tone VARCHAR(50),
                sentiment_score NUMERIC(5, 2),
                country_code VARCHAR(10),
                media_type VARCHAR(50),
                ai_summary TEXT,
                ai_classified_topics JSONB,
                moderation_status VARCHAR(50) DEFAULT 'pending_review',
                moderated_by BIGINT,
                moderated_at TIMESTAMP WITH TIME ZONE,
                expires_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP + INTERVAL '7 days')
            );
        """)

        # Створення таблиці products_for_sale
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS products_for_sale (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id),
                product_name VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                price NUMERIC(10, 2) NOT NULL,
                currency VARCHAR(10) NOT NULL,
                image_url TEXT,
                e_point_location_text TEXT NOT NULL,
                status VARCHAR(50) DEFAULT 'pending_review', -- pending_review, approved, sold, declined, archived
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Створення таблиці transactions
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products_for_sale(id),
                seller_id BIGINT NOT NULL REFERENCES users(id),
                buyer_id BIGINT NOT NULL REFERENCES users(id),
                status VARCHAR(50) DEFAULT 'initiated', -- initiated, buyer_confirmed, seller_confirmed, completed, cancelled
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Створення таблиці reviews
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id SERIAL PRIMARY KEY,
                transaction_id INTEGER REFERENCES transactions(id),
                reviewer_id BIGINT NOT NULL REFERENCES users(id),
                reviewed_user_id BIGINT NOT NULL REFERENCES users(id),
                rating INTEGER CHECK (rating >= 1 AND rating <= 5) NOT NULL,
                review_text TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        logger.info("Database tables checked/created successfully.")

async def get_user(user_id: int) -> Optional[User]:
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        user_record = await connection.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if user_record:
            return User(**dict(user_record))
        return None

async def create_or_update_user(tg_user: Any) -> User:
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        user = await get_user(tg_user.id)
        if user:
            # Оновлюємо тільки last_active
            await connection.execute(
                "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE id = $1",
                tg_user.id
            )
            user.last_active = datetime.now()
            return user
        else:
            is_admin = tg_user.id in ADMIN_IDS
            await connection.execute(
                """
                INSERT INTO users (id, username, first_name, last_name, is_admin, created_at, last_active)
                VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                tg_user.id, tg_user.username, tg_user.first_name, tg_user.last_name, is_admin
            )
            new_user = User(
                id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                is_admin=is_admin
            )
            logger.info(f"New user registered: {new_user.username or new_user.first_name} (ID: {new_user.id})")
            return new_user

# --- AI (Gemini) інтеграція ---
async def make_gemini_request_with_history(messages: List[Dict[str, Any]]) -> str:
    """Відправляє запит до Gemini API з історією повідомлень."""
    headers = {
        "Content-Type": "application/json",
    }
    params = {
        "key": GEMINI_API_KEY
    }
    data = {
        "contents": messages
    }

    async with ClientSession() as session:
        async with session.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
            params=params,
            headers=headers,
            json=data
        ) as response:
            if response.status == 200:
                response_json = await response.json()
                # logger.info(f"Gemini raw response: {response_json}") # Для дебагу
                if 'candidates' in response_json and response_json['candidates']:
                    first_candidate = response_json['candidates'][0]
                    if 'content' in first_candidate and 'parts' in first_candidate['content']:
                        for part in first_candidate['content']['parts']:
                            if 'text' in part:
                                return part['text']
                logger.warning(f"Gemini response missing expected parts: {response_json}")
                return "Не вдалося отримати відповідь від AI."
            else:
                error_text = await response.text()
                logger.error(f"Gemini API error: {response.status} - {error_text}")
                return f"Помилка AI: {response.status}. Спробуйте пізніше."

async def ai_summarize_news(title: str, content: str) -> Optional[str]:
    """Генерує резюме новини за допомогою AI."""
    prompt = (
        f"Зроби коротке резюме цієї новини (до 150 слів). Українською мовою.\n\nЗаголовок: {title}\n\nЗміст: {content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_translate_news(text: str, target_lang: str) -> Optional[str]:
    """Перекладає текст новини за допомогою AI."""
    prompt = (
        f"Переклади наступний текст на {target_lang}. Збережи стилістику та сенс оригіналу. "
        f"Текст:\n{text}"
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_answer_news_question(news_item: News, question: str, chat_history: List[Dict[str, Any]]) -> Optional[str]:
    """Відповідає на питання про новину за допомогою AI, враховуючи історію чату."""
    history_for_gemini = chat_history + [
        {"role": "user", "parts": [{"text": f"Новина: {news_item.title}\n{news_item.content[:2000]}...\n\nМій запит: {question}"}]}
    ]
    return await make_gemini_request_with_history(history_for_gemini)

async def ai_explain_term(term: str, news_content: str) -> Optional[str]:
    """Пояснює термін з контексту новини за допомогою AI."""
    prompt = (
        f"Поясни термін '{term}' у контексті наступної новини. "
        f"Дай коротке та зрозуміле пояснення (до 100 слів) українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_fact_check(fact_to_check: str, news_content: str) -> Optional[str]:
    """Перевіряє факт за допомогою AI."""
    prompt = (
        f"Перевір наступний факт: '{fact_to_check}'. "
        f"Використай надану новину як контекст, але також вкажи, чи є цей факт загальновідомим або чи потребує він додаткової перевірки. "
        f"Надай коротку відповідь (до 150 слів), вказуючи джерела інформації, якщо це можливо (імітуй, якщо їх немає). "
        f"Відповідь має бути об'єктивною та лише українською.\n\nКонтекст новини: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_extract_entities(news_content: str) -> Optional[str]:
    """Витягує ключові особи/сутності з новини за допомогою AI."""
    prompt = (
        f"Виділи ключові особи, організації та сутності, згадані в наступній новині. "
        f"Перерахуй їх списком (до 10 елементів) з коротким поясненням їх ролі у новині. Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_classify_topics(news_content: str) -> Optional[List[str]]:
    """Класифікує новину за темами за допомогою AI."""
    prompt = (
        f"Класифікуй наступну новину за 3-5 основними темами/категоріями. "
        f"Перерахуй теми через кому, без зайвих пояснень, українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    response = await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])
    if response:
        return [t.strip() for t in response.split(',') if t.strip()]
    return None

async def ai_compare_news(main_news_content: str, other_news_content: str) -> Optional[str]:
    """Порівнює новину з іншою статтею за допомогою AI."""
    prompt = (
        f"Порівняй наступні дві новини. "
        f"Виділи спільні риси, відмінності та різні кути висвітлення, якщо такі є. "
        f"Відповідь до 200 слів, українською мовою.\n\nНовина 1: {main_news_content[:1000]}...\n\nНовина 2: {other_news_content[:1000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_event_chain(news_content: str) -> Optional[str]:
    """Створює хроніку подій з новини за допомогою AI."""
    prompt = (
        f"Склади хронологічну послідовність ключових подій, згаданих у наступній новині. "
        f"Представ у вигляді маркованого списку. Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_predict_events(news_content: str) -> Optional[str]:
    """Прогнозує події на основі новини за допомогою AI."""
    prompt = (
        f"На основі інформації з наступної новини, спрогнозуй 1-3 можливих майбутніх події або розвитку ситуації. "
        f"Обґрунтуй свої припущення. Відповідь до 200 слів, українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_causality_analysis(news_content: str) -> Optional[str]:
    """Аналізує причини та наслідки подій у новині за допомогою AI."""
    prompt = (
        f"Проаналізуй наступну новину та виділи ключові причини та потенційні наслідки подій, що в ній описуються. "
        f"Представ у структурованому вигляді: 'Причини:' та 'Наслідки:'. Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_generate_knowledge_map(news_content: str) -> Optional[str]:
    """Генерує карту знань (основні концепції та зв'язки) з новини за допомогою AI."""
    prompt = (
        f"Створи 'карту знань' для наступної новини. "
        f"Виділи основні концепції, терміни, події та зв'язки між ними. "
        f"Представ це у текстовому вигляді (наприклад, 'Концепція А -> пов'язана з -> Концепцією Б'). До 250 слів. Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_get_polar_opinions(news_content: str) -> Optional[str]:
    """Генерує полярні думки з новини за допомогою AI."""
    prompt = (
        f"На основі наступної новини, згенеруй дві протилежні точки зору або полярні думки щодо описаної ситуації. "
        f"Кожна точка зору повинна бути стислою (до 100 слів). Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_cross_reference_sources(news_content: str) -> Optional[str]:
    """Імітує перехресну перевірку з іншими джерелами за допомогою AI."""
    prompt = (
        f"На основі наступної новини, імітуй перехресну перевірку інформації з 'іншими джерелами'. "
        f"Зазнач, яку додаткову інформацію могли б надати інші джерела (наприклад, офіційні заяви, думки експертів, альтернативні ЗМІ). "
        f"Відповідь до 200 слів, українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_suggest_topics_keywords(news_content: str) -> Optional[str]:
    """Пропонує пов'язані теми/ключові слова з новини за допомогою AI."""
    prompt = (
        f"На основі наступної новини, запропонуй 5-7 пов'язаних тем або ключових слів, які можуть бути цікавими для подальшого вивчення. "
        f"Перерахуй їх через кому. Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_quiz_from_news(news_title: str, news_content: str) -> Optional[str]:
    """Генерує тест по новині за допомогою AI."""
    prompt = (
        f"Створи короткий тест (3-5 запитань з варіантами відповідей A, B, C або D) на основі наступної новини. "
        f"Надай також правильні відповіді окремо. Українською мовою.\n\nЗаголовок: {news_title}\n\nЗміст: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_enrich_context(news_content: str) -> Optional[str]:
    """Розширює контекст новини за допомогою AI."""
    prompt = (
        f"Розшир контекст наступної новини. "
        f"Надай додаткову інформацію, яка допоможе краще зрозуміти її значення або передісторію. "
        f"До 250 слів. Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_generate_analogies(news_content: str) -> Optional[str]:
    """Генерує аналогії/приклади для новини за допомогою AI."""
    prompt = (
        f"Створи 1-2 прості аналогії або приклади, які допоможуть краще пояснити суть наступної новини. "
        f"Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_get_historical_context(news_content: str) -> Optional[str]:
    """Надає історичну довідку для новини за допомогою AI."""
    prompt = (
        f"Надай коротку історичну довідку або контекст, пов'язаний з основними темами чи подіями наступної новини. "
        f"До 200 слів. Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_generate_discussion_prompts(news_content: str) -> Optional[str]:
    """Генерує питання для обговорення новини за допомогою AI."""
    prompt = (
        f"Створи 3-5 питань для обговорення на основі наступної новини. "
        f"Питання мають бути відкритими та заохочувати дискусію. Українською мовою.\n\nНовина: {news_content[:2000]}..."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_assist_buyer_negotiation(product_name: str, product_description: str, product_price: Decimal, product_currency: str, buyer_query: str) -> Optional[str]:
    """Використовує Gemini AI для надання поради покупцю щодо переговорів за товар."""
    prompt = (
        f"Я розглядаю товар '{product_name}' за ціною {product_price} {product_currency}. "
        f"Опис товару: '{product_description[:500]}...'. "
        f"Мій запит: '{buyer_query}'. "
        "Будь ласка, проаналізуй цю інформацію і запропонуй стратегію переговорів, "
        "можливу справедливу ціну або аргументи для зниження ціни. "
        "Враховуй, що це AI-асистент для покупця. Відповідь має бути лише українською, до 200 слів."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_assist_seller_sales_pitch(product_name: str, product_description: str, product_price: Decimal, product_currency: str, seller_query: str) -> Optional[str]:
    """Використовує Gemini AI для надання поради продавцю щодо покращення продажів."""
    prompt = (
        f"Я продаю товар '{product_name}' за ціною {product_price} {product_currency}. "
        f"Опис товару: '{product_description[:500]}...'. "
        f"Мій запит як продавця: '{seller_query}'. "
        "Будь ласка, запропонуй, як я можу покращити опис для продажу, "
        "виділити ключові переваги, або створити короткий, привабливий 'продаючий' текст. "
        "Розглянь це як допомогу маркетолога. Відповідь має бути лише українською, до 300 слів."
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_analyze_sentiment_trend(news_item: News, related_news_items: List[News]) -> Optional[str]:
    """Використовує Gemini AI для аналізу та узагальнення тренду настроїв щодо теми/сутності."""
    prompt_parts = [
        "Проаналізуй наступні новини та визнач, як змінювався настрій (позитивний, негативний, нейтральний) "
        "щодо основної теми або ключових сутностей, згаданих у них, з часом. "
        "Сформулюй висновок про загальний тренд настроїв, вказуючи, якщо настрій змінювався, чому це могло статися. "
        "Відповідь має бути об'єктивною, стислою (до 250 слів) та лише українською. "
        "Зосередься на динаміці зміни настроїв."
        "\n\n--- Основна Новина ---"
        f"\nЗаголовок: {news_item.title}"
        f"\nЗміст: {news_item.content[:1000]}..."
    ]
    if news_item.ai_summary:
        prompt_parts.append(f"AI-резюме: {news_item.ai_summary}")
    if related_news_items:
        prompt_parts.append("\n\n--- Пов'язані Новини (для аналізу тренду) ---")
        sorted_related_news = sorted(related_news_items, key=lambda n: n.published_at)
        for i, rn in enumerate(sorted_related_news):
            prompt_parts.append(f"\n- Новина {i+1} ({rn.published_at.strftime('%d.%m.%Y')}): {rn.title}")
            prompt_parts.append(f"  Зміст: {rn.content[:500]}...")
            if rn.ai_summary:
                prompt_parts.append(f"  Резюме: {rn.ai_summary}")
    prompt = "\n".join(prompt_parts)
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_detect_bias_in_news(news_title: str, news_content: str, ai_summary: Optional[str]) -> Optional[str]:
    """Використовує Gemini AI для аналізу новинної статті на предмет потенційних упереджень."""
    prompt = (
        "Проаналізуй наступну новину на наявність можливих упереджень. "
        "Зверни увагу на вибір слів, тон, акцент на певних аспектах, замовчування фактів, "
        "джерела, на які посилаються, та загальний кут висвітлення. "
        "Виділи 1-3 потенційні упередження, якщо вони присутні, та поясни їх. "
        "Якщо упереджень не виявлено, так і зазнач. "
        "Відповідь має бути об'єктивною, стислою (до 250 слів) та лише українською."
        "\n\n--- Новина ---"
        f"\nЗаголовок: {news_title}"
        f"\nЗміст: {news_content[:2000]}..."
    )
    if ai_summary:
        prompt += f"\nAI-резюме: {ai_summary}"
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_summarize_for_audience(news_title: str, news_content: str, ai_summary: Optional[str], audience_type: str) -> Optional[str]:
    """Використовує Gemini AI для узагальнення новинної статті для конкретної аудиторії."""
    prompt = (
        f"Узагальни наступну новину для аудиторії: '{audience_type}'. "
        "Адаптуй мову, складність та акценти до цієї аудиторії. "
        "Зроби резюме стислим, до 200 слів. "
        "Відповідь має бути лише українською."
        "\n\n--- Новина ---"
        f"\nЗаголовок: {news_title}"
        f"\nЗміст: {news_content[:2000]}..."
    )
    if ai_summary:
        prompt += f"\nAI-резюме: {ai_summary}"
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_find_historical_analogues(news_title: str, news_content: str, ai_summary: Optional[str]) -> Optional[str]:
    """Використовує Gemini AI для пошуку та узагальнення історичних подій або ситуацій, схожих на поточну новину."""
    prompt = (
        "Проаналізуй наступну новину. Знайди 1-3 історичні події, ситуації або прецеденти, "
        "які мають значну схожість з основними аспектами цієї новини (тема, причини, наслідки, учасники тощо). "
        "Коротко опиши кожну аналогію та поясни, в чому полягає її схожість з поточною новиною. "
        "Відповідь має бути об'єктивною, стислою (до 300 слів) та лише українською. "
        "Якщо прямих аналогій немає, зазнач це або надай загальний історичний контекст для схожих явищ."
        "\n\n--- Новина ---"
        f"\nЗаголовок: {news_title}"
        f"\nЗміст: {news_content[:2000]}..."
    )
    if ai_summary:
        prompt += f"\nAI-резюме: {ai_summary}"
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_analyze_impact(news_title: str, news_content: str, ai_summary: Optional[str]) -> Optional[str]:
    """Використовує Gemini AI для аналізу потенційного впливу новинної події."""
    prompt = (
        "Проаналізуй наступну новину та оціни її потенційний вплив. "
        "Розглянь короткострокові та довгострокові наслідки. "
        "Проаналізуй вплив на різні сфери, такі як: економіка, суспільство, політика, технології, екологія (якщо релевантно). "
        "Сформулюй висновки у структурованому вигляді. "
        "Відповідь має бути об'єктивною, стислою (до 300 слів) та лише українською."
        "\n\n--- Новина ---"
        f"\nЗаголовок: {news_title}"
        f"\nЗміст: {news_content[:2000]}..."
    )
    if ai_summary:
        prompt += f"\nAI-резюме: {ai_summary}"
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

async def ai_generate_what_if_scenario(news_title: str, news_content: str, ai_summary: Optional[str], what_if_question: str) -> Optional[str]:
    """Використовує Gemini AI для генерації гіпотетичного сценарію "що якби..." на основі новини та запитання користувача."""
    prompt = (
        "На основі наступної новини, згенеруй гіпотетичний сценарій, "
        f"відповідаючи на запитання 'Що якби...': '{what_if_question}'. "
        "Розглянь потенційні наслідки та розвиток подій у цьому гіпотетичному контексті. "
        "Сценарій має бути логічним, послідовним, але обмежуватися кількома реченнями (до 200 слів). "
        "Відповідь має бути лише українською."
        "\n\n--- Новина ---"
        f"\nЗаголовок: {news_title}"
        f"\nЗміст: {news_content[:2000]}..."
    )
    if ai_summary:
        prompt += f"\nAI-резюме: {ai_summary}"
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])

# NEW AI FUNCTION: Generate news from YouTube Interview
async def ai_generate_news_from_youtube_interview(youtube_content_summary: str) -> Optional[str]:
    """
    Генерує новину на основі наданого "змісту YouTube-інтерв'ю".
    Імітує аналіз інтерв'ю та виділення ключових новинних тез.
    """
    prompt = (
        "На основі наступного змісту YouTube-інтерв'ю, створи коротку новинну статтю. "
        "Виділи 1-3 ключові тези або заяви з інтерв'ю, які могли б стати основою для новини. "
        "Новина має бути об'єктивною, стислою (до 300 слів) та лише українською. "
        "Оформи її як звичайну новинну статтю з заголовком."
        "\n\n--- Зміст YouTube-інтерв'ю ---"
        f"\n{youtube_content_summary}"
    )
    return await make_gemini_request_with_history([{"role": "user", "parts": [{"text": prompt}]}])


# --- Inline Keyboards ---
def get_main_menu_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📰 Мої новини", callback_data="my_news"))
    keyboard.add(InlineKeyboardButton(text="➕ Додати новину", callback_data="add_news"))
    keyboard.add(InlineKeyboardButton(text="🛍️ Маркетплейс", callback_data="marketplace_menu"))
    keyboard.add(InlineKeyboardButton(text="⚙️ Налаштування", callback_data="settings_menu"))
    keyboard.add(InlineKeyboardButton(text="❓ Допомога", callback_data="help_menu"))
    keyboard.add(InlineKeyboardButton(text="🧠 AI-функції (Новини)", callback_data="ai_news_functions_menu")) # Нове меню для AI-новин
    keyboard.adjust(2)
    return keyboard.as_markup()

def get_ai_news_functions_menu():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🗞️ Новина з YouTube-інтерв'ю", callback_data="news_from_youtube_interview")) # NEW BUTTON
    # ... інші загальні AI-функції, якщо такі будуть
    keyboard.add(InlineKeyboardButton(text="⬅️ Назад до головного", callback_data="main_menu"))
    keyboard.adjust(1)
    return keyboard.as_markup()


def get_news_keyboard(news_id: int):
    """Повертає інлайн-клавіатуру для взаємодії з новиною."""
    buttons = [
        [
            InlineKeyboardButton(text="👍", callback_data=f"act_like_{news_id}"),
            InlineKeyboardButton(text="👎", callback_data=f"act_dislike_{news_id}"),
            InlineKeyboardButton(text="🔖 Зберегти", callback_data=f"act_save_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="💬 Коментувати", callback_data=f"act_comment_{news_id}"),
            InlineKeyboardButton(text="➡️ Далі", callback_data=f"act_next_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ Блокувати джерело", callback_data=f"act_block_source_{news_id}"),
            InlineKeyboardButton(text="⛔️ Блокувати ключове слово", callback_data=f"act_block_keyword_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="📝 AI-резюме", callback_data=f"ai_summary_{news_id}"),
            InlineKeyboardButton(text="🌐 Перекласти", callback_data=f"translate_{news_id}"),
            InlineKeyboardButton(text="❓ Запитати AI", callback_data=f"ask_news_ai_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="🔊 Аудіо-резюме", callback_data=f"audio_summary_{news_id}"),
            InlineKeyboardButton(text="🧑‍🤝‍🧑 Ключові особи/сутності", callback_data=f"extract_entities_{news_id}"),
            InlineKeyboardButton(text="❓ Пояснити термін", callback_data=f"explain_term_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="🏷️ Класифікувати за темами", callback_data=f"classify_topics_{news_id}"),
            InlineKeyboardButton(text="🔄 Порівняти / Контекст", callback_data=f"compare_news_{news_id}"),
            InlineKeyboardButton(text="🗓️ Хроніка подій", callback_data=f"event_chain_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="🔮 Прогноз подій", callback_data=f"predict_events_{news_id}"),
            InlineKeyboardButton(text="➕ Календар", callback_data=f"add_to_calendar_{news_id}"),
            InlineKeyboardButton(text="🔍 Причини/Наслідки", callback_data=f"causality_analysis_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="🗺️ Карта знань", callback_data=f"knowledge_map_{news_id}"),
            InlineKeyboardButton(text="⚖️ Полярні думки", callback_data=f"polar_opinions_{news_id}"),
            InlineKeyboardButton(text="✅ Перевірити факт", callback_data=f"fact_check_news_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="❓ Деталі", callback_data=f"ask_details_{news_id}"),
            InlineKeyboardButton(text="📚 Інші джерела", callback_data=f"cross_reference_{news_id}"),
            InlineKeyboardButton(text="🔍 Пов'язані теми/слова", callback_data=f"suggest_topics_keywords_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="❓ Тест по новині", callback_data=f"quiz_from_news_{news_id}"),
            InlineKeyboardButton(text="🗣️ Інтерв'ю з AI", callback_data=f"interview_ai_{news_id}"),
            InlineKeyboardButton(text="🌐 Розширити контекст", callback_data=f"enrich_context_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="💡 Аналогії/Приклади", callback_data=f"analogies_from_news_{news_id}"),
            InlineKeyboardButton(text="📜 Історична довідка", callback_data=f"historical_context_{news_id}"),
            InlineKeyboardButton(text="❓ Питання для обговорення", callback_data=f"discussion_prompts_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="🎤 Прес-конференція", callback_data=f"press_conference_{news_id}"),
            InlineKeyboardButton(text="📊 Аналіз тренду настроїв", callback_data=f"sentiment_trend_analysis_{news_id}"),
            InlineKeyboardButton(text="🔍 Виявлення упередженості", callback_data=f"bias_detection_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="📝 Резюме для аудиторії", callback_data=f"audience_summary_{news_id}"),
            InlineKeyboardButton(text="📜 Історичні аналоги", callback_data=f"historical_analogues_{news_id}"),
            InlineKeyboardButton(text="💥 Аналіз впливу", callback_data=f"impact_analysis_{news_id}"),
        ],
        [
            InlineKeyboardButton(text="🤔 Сценарії 'Що якби...'", callback_data=f"what_if_scenario_{news_id}"),
            InlineKeyboardButton(text="➡️ Поділитися", callback_data=f"share_news_{news_id}"),
            InlineKeyboardButton(text="⚠️ Поскаржитись", callback_data=f"report_news_menu_{news_id}"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_marketplace_menu_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="➕ Продати товар", callback_data="add_product_for_sale"))
    keyboard.add(InlineKeyboardButton(text="🛒 Допоможи купити", callback_data="buy_product_menu"))
    keyboard.add(InlineKeyboardButton(text="📦 Мої товари (продаж)", callback_data="my_products"))
    keyboard.add(InlineKeyboardButton(text="💰 Мої угоди", callback_data="my_transactions"))
    keyboard.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu"))
    keyboard.adjust(2)
    return keyboard.as_markup()

def get_buy_product_menu_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="👁️ Переглянути всі товари", callback_data="browse_all_products"))
    keyboard.add(InlineKeyboardButton(text="🔍 Знайти/Відфільтрувати", callback_data="filter_products"))
    keyboard.add(InlineKeyboardButton(text="⬅️ Назад до маркетплейсу", callback_data="marketplace_menu"))
    keyboard.adjust(1)
    return keyboard.as_markup()

async def send_product_to_user(chat_id: int, product_id: int, current_index: int, total_count: int):
    """Надсилає інформацію про товар користувачеві з кнопками навігації та дії."""
    conn = None
    try:
        conn = await get_db_pool()
        product_record = await conn.fetchrow(
            """
            SELECT p.id, p.product_name, p.description, p.price, p.currency, p.image_url, p.e_point_location_text, p.status, u.username, u.first_name
            FROM products_for_sale p
            JOIN users u ON p.user_id = u.id
            WHERE p.id = $1 AND p.status = 'approved'
            """, product_id
        )
        if not product_record:
            await bot.send_message(chat_id, "На жаль, цей товар не знайдено або він більше не доступний.")
            return

        product = Product(**dict(product_record))
        seller_username = product_record['username'] if product_record['username'] else product_record['first_name']

        message_text = (
            f"✨ <b>{product.product_name}</b>\n\n"
            f"<b>Опис:</b>\n{product.description}\n\n"
            f"<b>Ціна:</b> {product.price} {product.currency}\n"
            f"<b>Місце зустрічі:</b> {product.e_point_location_text}\n"
            f"<b>Продавець:</b> @{seller_username}\n\n"
            f"<i>Товар {current_index + 1} з {total_count}</i>"
        )

        keyboard_buttons = []
        nav_buttons = []
        if current_index > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Попередній", callback_data="prev_product"))
        if current_index < total_count - 1:
            nav_buttons.append(InlineKeyboardButton(text="Наступний ➡️", callback_data="next_product"))
        if nav_buttons:
            keyboard_buttons.append(nav_buttons)

        action_buttons = [
            InlineKeyboardButton(text="✉️ Написати продавцю", callback_data=f"contact_seller_{product.id}"),
            InlineKeyboardButton(text="🛒 Купити", callback_data=f"buy_product_{product.id}"),
        ]
        keyboard_buttons.append(action_buttons)
        
        ai_negotiation_button = [
            InlineKeyboardButton(text="🧠 AI-аналіз ціни / Пропозиція", callback_data=f"ai_negotiate_product_{product.id}")
        ]
        keyboard_buttons.append(ai_negotiation_button)

        keyboard_buttons.append([InlineKeyboardButton(text="❌ Завершити перегляд", callback_data="stop_browse_products")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        if product.image_url:
            try:
                await bot.send_photo(
                    chat_id, photo=product.image_url, caption=message_text,
                    parse_mode=ParseMode.HTML, reply_markup=reply_markup, disable_notification=True
                )
            except Exception as e:
                logger.warning(f"Не вдалося надіслати фото товару {product.id}: {e}. Надсилаю без фото.")
                await bot.send_message(
                    chat_id, message_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup, disable_web_page_preview=True
                )
        else:
            await bot.send_message(
                chat_id, message_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup, disable_web_page_preview=True
            )

    except Exception as e:
        logger.error(f"Помилка при надсиланні товару {product_id} користувачу {chat_id}: {e}")
        await bot.send_message(chat_id, "❌ Сталася помилка при завантаженні товару. Спробуйте пізніше.")
    finally:
        if conn:
            await db_pool.release(conn)

# --- Обробники команд ---
@router.message(CommandStart())
async def command_start_handler(message: Message, state: FSMContext) -> None:
    """Обробляє команду /start."""
    await state.clear()
    await create_or_update_user(message.from_user)
    await message.answer(f"Привіт, {hbold(message.from_user.full_name)}! 👋\n\n"
                         "Я ваш особистий новинний помічник та асистент з купівлі-продажу. "
                         "Я можу допомогти вам бути в курсі подій, аналізувати новини та зручно купувати/продавати товари.\n\n"
                         "Оберіть дію:", reply_markup=get_main_menu_keyboard())

@router.message(Command("menu"))
async def command_menu_handler(message: Message, state: FSMContext):
    """Показує головне меню."""
    await state.clear()
    await message.answer("Оберіть дію:", reply_markup=get_main_menu_keyboard())

@router.message(Command("cancel"))
@router.message(StateFilter(
    AddNews.waiting_for_news_url, AddNews.waiting_for_news_lang, AddNews.confirm_news,
    SellProduct.waiting_for_name, SellProduct.waiting_for_description, SellProduct.waiting_for_price,
    SellProduct.waiting_for_currency, SellProduct.waiting_for_image, SellProduct.waiting_for_e_point,
    SellProduct.confirm_product, SellProduct.editing_field, SellProduct.deleting_product_id,
    ProductTransaction.awaiting_buyer_confirmation, ProductTransaction.awaiting_seller_confirmation,
    DirectMessage.waiting_for_message_text,
    ReviewState.waiting_for_seller_rating, ReviewState.waiting_for_seller_review,
    ReviewState.waiting_for_buyer_rating, ReviewState.waiting_for_buyer_review,
    AIAssistant.waiting_for_question, AIAssistant.waiting_for_news_id_for_question,
    AIAssistant.waiting_for_term_to_explain, AIAssistant.waiting_for_fact_to_check,
    AIAssistant.waiting_for_detailed_question, AIAssistant.interview_mode,
    AIAssistant.press_conference_mode, AIAssistant.waiting_for_audience_summary_type,
    AIAssistant.waiting_for_what_if_query,
    AIAssistant.waiting_for_youtube_interview_url, # NEW: Cancel for YouTube news
    ProductTransaction.waiting_for_negotiation_query, # NEW: Cancel for negotiation
    SalesAssistance.waiting_for_sales_query
))
async def cmd_cancel(message: Message, state: FSMContext):
    """Дозволяє скасувати поточну дію."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активних дій для скасування.")
        return
    await state.clear()
    await message.answer("Дію скасовано. Оберіть наступну дію:", reply_markup=get_main_menu_keyboard())

@router.message(Command("myprofile"))
async def handle_my_profile_command(message: Message):
    """Показує профіль користувача, включаючи відгуки та рейтинг."""
    user_id = message.from_user.id
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        user_record = await connection.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not user_record:
            await message.answer("Ваш профіль не знайдено. Спробуйте /start.")
            return

        username = user_record['username'] if user_record['username'] else user_record['first_name']
        is_admin_str = "Так" if user_record['is_admin'] else "Ні"
        created_at_str = user_record['created_at'].strftime("%d.%m.%Y %H:%M")

        # Отримати середній рейтинг
        avg_rating_record = await connection.fetchrow(
            "SELECT AVG(rating) AS avg_rating FROM reviews WHERE reviewed_user_id = $1", user_id
        )
        avg_rating = round(avg_rating_record['avg_rating'], 2) if avg_rating_record['avg_rating'] else "немає"

        # Отримати останні 3 відгуки
        recent_reviews = await connection.fetch(
            """
            SELECT r.rating, r.review_text, u.username, u.first_name
            FROM reviews r
            JOIN users u ON r.reviewer_id = u.id
            WHERE r.reviewed_user_id = $1
            ORDER BY r.created_at DESC
            LIMIT 3
            """, user_id
        )

        profile_text = (
            f"👤 <b>Ваш Профіль:</b>\n"
            f"Ім'я: {username}\n"
            f"ID: <code>{user_id}</code>\n"
            f"Зареєстрований: {created_at_str}\n"
            f"Адмін: {is_admin_str}\n"
            f"Середній рейтинг: ⭐ <b>{avg_rating}</b>\n\n"
        )

        if recent_reviews:
            profile_text += "<b>Останні відгуки:</b>\n"
            for review in recent_reviews:
                reviewer_name = review['username'] if review['username'] else review['first_name']
                review_text = review['review_text'] if review['review_text'] else "Без тексту"
                profile_text += f"  • Від @{reviewer_name}: {review['rating']}⭐ - \"{review_text}\"\n"
        else:
            profile_text += "Поки що немає відгуків.\n"

        await message.answer(profile_text)

# --- Обробники CallbackQuery (основне меню) ---
@router.callback_query(F.data == "main_menu")
async def process_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Оберіть дію:", reply_markup=get_main_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data == "marketplace_menu")
async def process_marketplace_menu(callback: CallbackQuery):
    await callback.message.edit_text("🛍️ *Маркетплейс:*\nОберіть дію:", reply_markup=get_marketplace_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@router.callback_query(F.data == "buy_product_menu")
async def process_buy_product_menu(callback: CallbackQuery):
    await callback.message.edit_text("🛒 *Купити товар:*\nОберіть дію:", reply_markup=get_buy_product_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@router.callback_query(F.data == "ai_news_functions_menu")
async def process_ai_news_functions_menu(callback: CallbackQuery):
    await callback.message.edit_text("🧠 *AI-функції для новин:*\nОберіть бажану функцію:", reply_markup=get_ai_news_functions_menu(), parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

# --- Нова функція: Новини з YouTube-інтерв'ю ---
@router.callback_query(F.data == "news_from_youtube_interview")
async def handle_news_from_youtube_interview(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AIAssistant.waiting_for_youtube_interview_url)
    await callback.message.edit_text(
        "🗞️ Щоб створити новину з YouTube-інтерв'ю, будь ласка, надішліть мені посилання на відео."
        "\n\n*Приклад:* `https://www.youtube.com/watch?v=dQw4w9WgXcQ`"
        "\n\n*(AI імітуватиме аналіз змісту, оскільки прямого доступу до транскриптів немає)*",
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@router.message(AIAssistant.waiting_for_youtube_interview_url, F.text.regexp(r"(https?://)?(www\.)?(youtube|youtu|m\.youtube)\.(com|be)/(watch\?v=|embed/|v/|)([\w-]{11})(?:\S+)?"))
async def process_youtube_interview_url(message: Message, state: FSMContext):
    youtube_url = message.text
    await message.answer("⏳ Аналізую YouTube-інтерв'ю та генерую новину... Це може зайняти до хвилини.")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # Імітація вилучення змісту з YouTube-інтерв'ю.
    # В реальному проекті тут була б інтеграція з YouTube Data API або сервісом транскрипції.
    # Для демонстрації, AI "уявляє" зміст за посиланням.
    mock_content_prompt = (
        f"Уяви, що ти переглянув/переглянула YouTube-інтерв'ю за посиланням: {youtube_url}. "
        "Сформуй короткий уявний зміст цього інтерв'ю, щоб я міг створити новину. "
        "Включи гіпотетичні основні теми та кілька ключових цитат або заяв від учасників. "
        "Зміст має бути достатньо реалістичним, щоб з нього можна було згенерувати новину. "
        "Відповідь до 500 слів, тільки зміст, без вступу чи висновків. Українською мовою."
    )
    
    simulated_content = await make_gemini_request_with_history([{"role": "user", "parts": [{"text": mock_content_prompt}]}])

    if not simulated_content or "Не вдалося отримати відповідь від AI." in simulated_content:
        await message.answer("❌ Не вдалося отримати зміст інтерв'ю для аналізу. Спробуйте інше посилання або пізніше.")
        await state.clear()
        return

    # Тепер використовуємо згенерований зміст для створення новини
    generated_news_text = await ai_generate_news_from_youtube_interview(simulated_content)

    if generated_news_text and "Не вдалося отримати відповідь від AI." not in generated_news_text:
        await message.answer(f"✅ **Ваша новина з YouTube-інтерв'ю:**\n\n{generated_news_text}", parse_mode=ParseMode.MARKDOWN)
    else:
        await message.answer("❌ Не вдалося створити новину з наданого інтерв'ю. Спробуйте пізніше.")

    await state.clear()
    await message.answer("Оберіть наступну дію:", reply_markup=get_main_menu_keyboard())

@router.message(AIAssistant.waiting_for_youtube_interview_url)
async def process_youtube_interview_url_invalid(message: Message):
    await message.answer("Будь ласка, надішліть дійсне посилання на YouTube-відео, або введіть /cancel для скасування.")


# --- Обробники CallbackQuery (AI-функції для новин) ---
# Це лише приклади, потрібно додати аналогічні обробники для всіх кнопок AI з get_news_keyboard
@router.callback_query(F.data.startswith("ai_summary_"))
async def handle_ai_summary_callback(callback: CallbackQuery):
    news_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        news_item = await connection.fetchrow("SELECT title, content FROM news WHERE id = $1", news_id)
        if not news_item:
            await callback.message.answer("❌ Новину не знайдено.")
            await callback.answer()
            return
        
        await callback.message.answer("⏳ Генерую резюме за допомогою AI...")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
        
        summary = await ai_summarize_news(news_item['title'], news_item['content'])
        
        if summary:
            await connection.execute("UPDATE news SET ai_summary = $1 WHERE id = $2", summary, news_id)
            await callback.message.answer(f"📝 <b>AI-резюме новини (ID: {news_id}):</b>\n\n{summary}")
        else:
            await callback.message.answer("❌ Не вдалося згенерувати резюме.")
    await callback.answer()

@router.callback_query(F.data.startswith("translate_"))
async def handle_translate_callback(callback: CallbackQuery):
    news_id = int(callback.data.split('_')[1])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        news_item = await connection.fetchrow("SELECT title, content, lang FROM news WHERE id = $1", news_id)
        if not news_item:
            await callback.message.answer("❌ Новину не знайдено.")
            await callback.answer()
            return

        target_lang = 'en' if news_item['lang'] == 'uk' else 'uk'
        await callback.message.answer(f"⏳ Перекладаю новину на {target_lang.upper()} за допомогою AI...")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

        translated_title = await ai_translate_news(news_item['title'], target_lang)
        translated_content = await ai_translate_news(news_item['content'], target_lang)

        if translated_title and translated_content:
            await callback.message.answer(
                f"🌐 <b>Переклад новини (ID: {news_id}) на {target_lang.upper()}:</b>\n\n"
                f"<b>{translated_title}</b>\n\n{translated_content}"
            )
        else:
            await callback.message.answer("❌ Не вдалося перекласти новину.")
    await callback.answer()

@router.callback_query(F.data.startswith("ask_news_ai_"))
async def handle_ask_news_ai_callback(callback: CallbackQuery, state: FSMContext):
    news_id = int(callback.data.split('_')[3])
    await state.update_data(waiting_for_news_id_for_question=news_id)
    await state.set_state(AIAssistant.waiting_for_question)
    await callback.message.answer("❓ Задайте ваше питання про новину.")
    await callback.answer()

@router.message(AIAssistant.waiting_for_question, F.text)
async def process_news_question(message: Message, state: FSMContext):
    data = await state.get_data()
    news_id = data.get('waiting_for_news_id_for_question')
    question = message.text

    if not news_id:
        await message.answer("Вибачте, контекст новини втрачено. Спробуйте ще раз через команду /mynews.")
        await state.clear()
        return

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        news_item_data = await connection.fetchrow("SELECT title, content, lang FROM news WHERE id = $1", news_id)
        if not news_item_data:
            await message.answer("Новину не знайдено.")
            await state.clear()
            return

        news_item = News(id=news_id, title=news_item_data['title'], content=news_item_data['content'], lang=news_item_data['lang'],
                         source_id=None, source_url=None, image_url=None, published_at=datetime.now(),
                         tone=None, sentiment_score=None, country_code=None, media_type=None) # Заповнюємо мінімально необхідні поля

        chat_history = data.get('ask_news_ai_history', [])
        chat_history.append({"role": "user", "parts": [{"text": question}]})

        await message.answer("⏳ Обробляю ваше питання за допомогою AI...")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        ai_response = await ai_answer_news_question(news_item, question, chat_history)

        if ai_response:
            await message.answer(f"<b>AI відповідає:</b>\n\n{ai_response}")
            chat_history.append({"role": "model", "parts": [{"text": ai_response}]})
            await state.update_data(ask_news_ai_history=chat_history)
        else:
            await message.answer("❌ Не вдалося відповісти на ваше питання.")
    
    # Залишаємося у стані, щоб можна було продовжити діалог
    await message.answer("Продовжуйте ставити питання або введіть /cancel для завершення діалогу.")


@router.callback_query(F.data.startswith("extract_entities_"))
async def handle_extract_entities_callback(callback: CallbackQuery):
    news_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        news_item = await connection.fetchrow("SELECT content FROM news WHERE id = $1", news_id)
        if not news_item:
            await callback.message.answer("❌ Новину не знайдено.")
            await callback.answer()
            return
        
        await callback.message.answer("⏳ Витягую ключові сутності за допомогою AI...")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
        
        entities = await ai_extract_entities(news_item['content'])
        
        if entities:
            await callback.message.answer(f"🧑‍🤝‍🧑 <b>Ключові особи/сутності в новині (ID: {news_id}):</b>\n\n{entities}")
        else:
            await callback.message.answer("❌ Не вдалося витягнути сутності.")
    await callback.answer()

@router.callback_query(F.data.startswith("classify_topics_"))
async def handle_classify_topics_callback(callback: CallbackQuery):
    news_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        news_item_record = await connection.fetchrow("SELECT content, ai_classified_topics FROM news WHERE id = $1", news_id)
        if not news_item_record:
            await callback.message.answer("❌ Новину не знайдено.")
            await callback.answer()
            return

        topics = news_item_record['ai_classified_topics']
        if not topics: # Якщо ще не класифіковано
            await callback.message.answer("⏳ Класифікую новину за темами за допомогою AI...")
            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
            topics = await ai_classify_topics(news_item_record['content'])
            if topics:
                await connection.execute("UPDATE news SET ai_classified_topics = $1 WHERE id = $2", json.dumps(topics), news_id)
            else:
                topics = ["Не вдалося визначити теми."]

        if topics:
            topics_str = ", ".join(topics)
            await callback.message.answer(f"🏷️ <b>Класифікація за темами для новини (ID: {news_id}):</b>\n\n{topics_str}")
        else:
            await callback.message.answer("❌ Не вдалося класифікувати новину за темами.")
    await callback.answer()

@router.callback_query(F.data.startswith("explain_term_"))
async def handle_explain_term_callback(callback: CallbackQuery, state: FSMContext):
    news_id = int(callback.data.split('_')[2])
    await state.update_data(waiting_for_news_id_for_question=news_id) # Reuse state variable for news_id context
    await state.set_state(AIAssistant.waiting_for_term_to_explain)
    await callback.message.answer("❓ Введіть термін, який ви хочете, щоб AI пояснив у контексті цієї новини.")
    await callback.answer()

@router.message(AIAssistant.waiting_for_term_to_explain, F.text)
async def process_explain_term_query(message: Message, state: FSMContext):
    data = await state.get_data()
    news_id = data.get('waiting_for_news_id_for_question') # Reused state variable
    term = message.text.strip()

    if not news_id:
        await message.answer("Вибачте, контекст новини втрачено. Спробуйте ще раз через команду /mynews.")
        await state.clear()
        return

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        news_item = await connection.fetchrow("SELECT content FROM news WHERE id = $1", news_id)
        if not news_item:
            await message.answer("Новину не знайдено.")
            await state.clear()
            return

        await message.answer(f"⏳ Пояснюю термін '{term}' за допомогою AI...")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        explanation = await ai_explain_term(term, news_item['content'])

        if explanation:
            await message.answer(f"❓ <b>Пояснення терміну '{term}' (Новина ID: {news_id}):</b>\n\n{explanation}")
        else:
            await message.answer("❌ Не вдалося пояснити термін.")
    await state.clear()

@router.callback_query(F.data.startswith("fact_check_news_"))
async def handle_fact_check_news_callback(callback: CallbackQuery, state: FSMContext):
    news_id = int(callback.data.split('_')[3])
    await state.update_data(fact_check_news_id=news_id)
    await state.set_state(AIAssistant.waiting_for_fact_to_check)
    await callback.message.answer("✅ Введіть факт, який ви хочете перевірити в контексті цієї новини.")
    await callback.answer()

@router.message(AIAssistant.waiting_for_fact_to_check, F.text)
async def process_fact_to_check(message: Message, state: FSMContext):
    data = await state.get_data()
    news_id = data.get('fact_check_news_id')
    fact_to_check = message.text.strip()

    if not news_id:
        await message.answer("Вибачте, контекст новини втрачено. Спробуйте ще раз.")
        await state.clear()
        return

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        news_item = await connection.fetchrow("SELECT content FROM news WHERE id = $1", news_id)
        if not news_item:
            await message.answer("Новину не знайдено.")
            await state.clear()
            return

        await message.answer(f"⏳ Перевіряю факт: '{fact_to_check}' за допомогою AI...")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        fact_check_result = await ai_fact_check(fact_to_check, news_item['content'])

        if fact_check_result:
            await message.answer(f"✅ <b>Перевірка факту для новини (ID: {news_id}):</b>\n\n{fact_check_result}")
        else:
            await message.answer("❌ Не вдалося перевірити факт.")
    await state.clear()

@router.callback_query(F.data.startswith("sentiment_trend_analysis_"))
async def handle_sentiment_trend_analysis_callback(callback: CallbackQuery):
    news_id = int(callback.data.split('_')[3])
    conn = await get_db_pool()
    try:
        conn = await get_db_pool()
        main_news_record = await conn.fetchrow(
            "SELECT id, title, content, ai_summary, ai_classified_topics, lang, published_at FROM news WHERE id = $1", news_id
        )

        if not main_news_record:
            await callback.message.answer("❌ Новину для аналізу не знайдено.")
            await callback.answer()
            return

        main_news_obj = News(
            id=main_news_record['id'],
            title=main_news_record['title'],
            content=main_news_record['content'],
            lang=main_news_record['lang'],
            published_at=main_news_record['published_at'],
            source_id=None, source_url=None, image_url=None, tone=None, sentiment_score=None, country_code=None, media_type=None,
            ai_summary=main_news_record['ai_summary'],
            ai_classified_topics=main_news_record['ai_classified_topics']
        )

        await callback.message.answer("⏳ Аналізую тренд настроїв за допомогою AI... Це може зайняти трохи часу.")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

        related_news_items = []
        if main_news_obj.ai_classified_topics:
            topic_conditions = [f"ai_classified_topics @> '[\"{t}\"]'::jsonb" for t in main_news_obj.ai_classified_topics]
            related_news_records = await conn.fetch(
                f"""
                SELECT id, title, content, ai_summary, lang, published_at
                FROM news
                WHERE id != $1
                AND moderation_status = 'approved'
                AND expires_at > NOW()
                AND published_at >= NOW() - INTERVAL '30 days'
                AND ({' OR '.join(topic_conditions)})
                ORDER BY published_at ASC
                LIMIT 5
                """, news_id
            )
            related_news_items = [
                News(
                    id=r['id'], title=r['title'], content=r['content'], lang=r['lang'],
                    published_at=r['published_at'], source_id=None, source_url=None, image_url=None,
                    ai_summary=r['ai_summary'], ai_classified_topics=None, tone=None, sentiment_score=None, country_code=None, media_type=None
                ) for r in related_news_records
            ]

        ai_sentiment_trend = await ai_analyze_sentiment_trend(main_news_obj, related_news_items)

        response_message = f"📊 <b>Аналіз тренду настроїв для новини (ID: {news_id}):</b>\n\n{ai_sentiment_trend}"
        
        await callback.message.answer(response_message, parse_mode=ParseMode.HTML)
        logger.info(f"Користувач {callback.from_user.id} запросив аналіз тренду настроїв для новини {news_id}.")

    except Exception as e:
        logger.error(f"Помилка при обробці запиту аналізу тренду настроїв для новини {news_id} користувача {callback.from_user.id}: {e}")
        await callback.message.answer("❌ Сталася помилка при аналізі тренду настроїв. Спробуйте пізніше.")
    finally:
        if conn:
            await db_pool.release(conn)
        await callback.answer()

@router.callback_query(F.data.startswith("bias_detection_"))
async def handle_bias_detection_callback(callback: CallbackQuery):
    news_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    try:
        conn = await get_db_pool()
        news_item = await conn.fetchrow(
            "SELECT title, content, ai_summary FROM news WHERE id = $1", news_id
        )

        if not news_item:
            await callback.message.answer("❌ Новину для аналізу не знайдено.")
            await callback.answer()
            return

        await callback.message.answer("⏳ Аналізую новину на наявність упереджень за допомогою AI... Це може зайняти трохи часу.")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

        ai_bias_analysis = await ai_detect_bias_in_news(
            news_item['title'],
            news_item['content'],
            news_item['ai_summary']
        )

        response_message = f"🔍 <b>Аналіз на упередженість для новини (ID: {news_id}):</b>\n\n{ai_bias_analysis}"
        
        await callback.message.answer(response_message, parse_mode=ParseMode.HTML)
        logger.info(f"Користувач {callback.from_user.id} запросив аналіз на упередженість для новини {news_id}.")

    except Exception as e:
        logger.error(f"Error handling bias detection request for news {news_id} by user {callback.from_user.id}: {e}")
        await callback.message.answer("❌ Сталася помилка при аналізі на упередженість. Спробуйте пізніше.")
    finally:
        if conn:
            await db_pool.release(conn)
        await callback.answer()

@router.callback_query(F.data.startswith("audience_summary_"))
async def handle_audience_summary_callback(callback: CallbackQuery, state: FSMContext):
    news_id = int(callback.data.split('_')[2])
    await state.update_data(audience_summary_news_id=news_id)
    await state.set_state(AIAssistant.waiting_for_audience_summary_type)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🧒 Дитина (простою мовою)", callback_data="audience_type_child"),
                InlineKeyboardButton(text="🎓 Експерт (глибокий аналіз)", callback_data="audience_type_expert"),
            ],
            [
                InlineKeyboardButton(text="🏛️ Політик (політичний аспект)", callback_data="audience_type_politician"),
                InlineKeyboardButton(text="🧑‍💻 Технолог (технічний аспект)", callback_data="audience_type_technologist"),
            ],
            [
                InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_audience_summary")
            ]
        ]
    )
    await callback.message.edit_text(
        "📝 Для якої аудиторії ви хочете отримати резюме цієї новини?",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@router.callback_query(AIAssistant.waiting_for_audience_summary_type, F.data.startswith("audience_type_"))
async def process_audience_type_selection(callback: CallbackQuery, state: FSMContext):
    audience_type_key = callback.data.split('_')[2]
    audience_map = {
        'child': 'дитини (простою мовою)',
        'expert': 'експерта (з глибоким аналізом)',
        'politician': 'політика (з акцентом на політичний аспект)',
        'technologist': 'технолога (з акцентом на технічний аспект)',
    }
    selected_audience = audience_map.get(audience_type_key, 'загальної аудиторії')

    data = await state.get_data()
    news_id = data.get('audience_summary_news_id')

    if not news_id:
        await callback.message.answer("Вибачте, контекст новини втрачено. Будь ласка, спробуйте знову.")
        await state.clear()
        await callback.answer()
        return
    
    await callback.message.edit_text(f"⏳ Генерую резюме для аудиторії: <b>{selected_audience}</b>...", parse_mode=ParseMode.HTML)
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

    conn = None
    try:
        conn = await get_db_pool()
        news_item = await conn.fetchrow(
            "SELECT title, content, ai_summary FROM news WHERE id = $1", news_id
        )

        if not news_item:
            await callback.message.answer("❌ Новину для резюме не знайдено.")
            await state.clear()
            await callback.answer()
            return

        ai_summary_for_audience = await ai_summarize_for_audience(
            news_item['title'],
            news_item['content'],
            news_item['ai_summary'],
            selected_audience
        )

        response_message = (
            f"📝 <b>Резюме для аудиторії: {selected_audience} (Новина ID: {news_id}):</b>\n\n"
            f"{ai_summary_for_audience}"
        )
        
        await callback.message.answer(response_message, parse_mode=ParseMode.HTML)
        logger.info(f"Користувач {callback.from_user.id} запросив резюме для аудиторії '{selected_audience}' для новини {news_id}.")

    except Exception as e:
        logger.error(f"Error handling audience summary request for news {news_id} by user {callback.from_user.id}: {e}")
        await callback.message.answer("❌ Сталася помилка при генерації резюме. Спробуйте пізніше.")
    finally:
        if conn:
            await db_pool.release(conn)
        await state.clear()
    await callback.answer()

@router.callback_query(AIAssistant.waiting_for_audience_summary_type, F.data == "cancel_audience_summary")
async def cancel_audience_summary_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("✅ Генерацію резюме для аудиторії скасовано.")
    await state.clear()
    await callback.answer()

@router.callback_query(F.data.startswith("historical_analogues_"))
async def handle_historical_analogues_callback(callback: CallbackQuery):
    news_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    try:
        conn = await get_db_pool()
        news_item = await conn.fetchrow(
            "SELECT title, content, ai_summary FROM news WHERE id = $1", news_id
        )

        if not news_item:
            await callback.message.answer("❌ Новину для аналізу не знайдено.")
            await callback.answer()
            return

        await callback.message.answer("⏳ Шукаю історичні аналоги за допомогою AI... Це може зайняти трохи часу.")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

        ai_historical_analogues = await ai_find_historical_analogues(
            news_item['title'],
            news_item['content'],
            news_item['ai_summary']
        )

        response_message = f"📜 <b>Історичні аналоги для новини (ID: {news_id}):</b>\n\n{ai_historical_analogues}"
        
        await callback.message.answer(response_message, parse_mode=ParseMode.HTML)
        logger.info(f"Користувач {callback.from_user.id} запросив історичні аналоги для новини {news_id}.")

    except Exception as e:
        logger.error(f"Error handling historical analogues request for news {news_id} by user {callback.from_user.id}: {e}")
        await callback.message.answer("❌ Сталася помилка при пошуку історичних аналогів. Спробуйте пізніше.")
    finally:
        if conn:
            await db_pool.release(conn)
        await callback.answer()

@router.callback_query(F.data.startswith("impact_analysis_"))
async def handle_impact_analysis_callback(callback: CallbackQuery):
    news_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    try:
        conn = await get_db_pool()
        news_item = await conn.fetchrow(
            "SELECT title, content, ai_summary FROM news WHERE id = $1", news_id
        )

        if not news_item:
            await callback.message.answer("❌ Новину для аналізу впливу не знайдено.")
            await callback.answer()
            return

        await callback.message.answer("⏳ Аналізую потенційний вплив новини за допомогою AI... Це може зайняти трохи часу.")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

        ai_impact_analysis = await ai_analyze_impact(
            news_item['title'],
            news_item['content'],
            news_item['ai_summary']
        )

        response_message = f"💥 <b>Аналіз впливу новини (ID: {news_id}):</b>\n\n{ai_impact_analysis}"
        
        await callback.message.answer(response_message, parse_mode=ParseMode.HTML)
        logger.info(f"Користувач {callback.from_user.id} запросив аналіз впливу для новини {news_id}.")

    except Exception as e:
        logger.error(f"Помилка при обробці запиту аналізу впливу для новини {news_id} користувача {callback.from_user.id}: {e}")
        await callback.message.answer("❌ Сталася помилка при аналізі впливу. Спробуйте пізніше.")
    finally:
        if conn:
            await db_pool.release(conn)
        await callback.answer()

@router.callback_query(F.data.startswith("what_if_scenario_"))
async def handle_what_if_scenario_callback(callback: CallbackQuery, state: FSMContext):
    news_id = int(callback.data.split('_')[3])
    
    await state.update_data(what_if_news_id=news_id)
    await state.set_state(AIAssistant.waiting_for_what_if_query)
    
    await callback.message.edit_reply_markup(reply_markup=None) # Прибираємо клавіатуру під новиною
    await callback.message.answer(
        f"🤔 Введіть ваше питання у форматі 'Що якби...' для новини (ID: {news_id}). "
        "Наприклад: 'Що якби зустріч завершилася без угоди?', 'Що якби новий закон не був прийнятий?'"
    )
    await callback.answer()

@router.message(AIAssistant.waiting_for_what_if_query, F.text)
async def process_what_if_query(message: Message, state: FSMContext):
    user_id = message.from_user.id
    what_if_question = message.text.strip()

    if not what_if_question:
        await message.answer("Будь ласка, введіть ваше питання у форматі 'Що якби...'.")
        return

    data = await state.get_data()
    news_id_for_context = data.get('what_if_news_id')

    if not news_id_for_context:
        await message.answer("Вибачте, контекст новини втрачено. Будь ласка, спробуйте ще раз через команду /mynews.")
        await state.clear()
        return

    await message.answer("⏳ Генерую сценарій 'Що якби...' за допомогою AI... Це може зайняти трохи часу.")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    conn = None
    try:
        conn = await get_db_pool()
        news_item = await conn.fetchrow(
            "SELECT title, content, ai_summary FROM news WHERE id = $1", news_id_for_context
        )

        if not news_item:
            await message.answer("❌ Новину, до якої ви хотіли згенерувати сценарій, не знайдено. Будь ласка, спробуйте з іншою новиною.")
            await state.clear()
            return

        ai_what_if_scenario = await ai_generate_what_if_scenario(
            news_item['title'],
            news_item['content'],
            news_item['ai_summary'],
            what_if_question
        )

        response_message = f"🤔 <b>Сценарій 'Що якби...' для новини (ID: {news_id_for_context}):</b>\n\n{ai_what_if_scenario}"
        
        await message.answer(response_message, parse_mode=ParseMode.HTML)
        logger.info(f"Користувач {user_id} запросив сценарій 'що якби': '{what_if_question}' для новини {news_id_for_context}.")

    except Exception as e:
        logger.error(f"Error processing 'what-if' query for news {news_id_for_context} by user {user_id}: {e}")
        await message.answer("❌ Сталася помилка при генерації сценарію. Спробуйте пізніше.")
    finally:
        if conn:
            await db_pool.release(conn)
        await state.clear()

# --- Placeholder handlers for other AI functions (add as needed) ---
@router.callback_query(F.data.startswith("audio_summary_"))
async def handle_audio_summary_callback(callback: CallbackQuery):
    await callback.message.answer("🔊 Функція аудіо-резюме знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("compare_news_"))
async def handle_compare_news_callback(callback: CallbackQuery):
    await callback.message.answer("🔄 Функція порівняння новин знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("event_chain_"))
async def handle_event_chain_callback(callback: CallbackQuery):
    await callback.message.answer("🗓️ Функція хроніки подій знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("predict_events_"))
async def handle_predict_events_callback(callback: CallbackQuery):
    await callback.message.answer("🔮 Функція прогнозу подій знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("add_to_calendar_"))
async def handle_add_to_calendar_callback(callback: CallbackQuery):
    await callback.message.answer("➕ Функція додавання в календар знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("causality_analysis_"))
async def handle_causality_analysis_callback(callback: CallbackQuery):
    await callback.message.answer("🔍 Функція причин/наслідків знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("knowledge_map_"))
async def handle_knowledge_map_callback(callback: CallbackQuery):
    await callback.message.answer("🗺️ Функція карти знань знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("polar_opinions_"))
async def handle_polar_opinions_callback(callback: CallbackQuery):
    await callback.message.answer("⚖️ Функція полярних думок знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("ask_details_"))
async def handle_ask_details_callback(callback: CallbackQuery):
    await callback.message.answer("❓ Функція деталей знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("cross_reference_"))
async def handle_cross_reference_callback(callback: CallbackQuery):
    await callback.message.answer("📚 Функція інших джерел знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("suggest_topics_keywords_"))
async def handle_suggest_topics_keywords_callback(callback: CallbackQuery):
    await callback.message.answer("🔍 Функція пов'язаних тем знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("quiz_from_news_"))
async def handle_quiz_from_news_callback(callback: CallbackQuery):
    await callback.message.answer("❓ Функція тесту по новині знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("interview_ai_"))
async def handle_interview_ai_callback(callback: CallbackQuery):
    await callback.message.answer("🗣️ Функція інтерв'ю з AI знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("enrich_context_"))
async def handle_enrich_context_callback(callback: CallbackQuery):
    await callback.message.answer("🌐 Функція розширення контексту знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("analogies_from_news_"))
async def handle_analogies_from_news_callback(callback: CallbackQuery):
    await callback.message.answer("💡 Функція аналогій/прикладів знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("historical_context_"))
async def handle_historical_context_callback(callback: CallbackQuery):
    await callback.message.answer("📜 Функція історичної довідки знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("discussion_prompts_"))
async def handle_discussion_prompts_callback(callback: CallbackQuery):
    await callback.message.answer("❓ Функція питань для обговорення знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("press_conference_"))
async def handle_press_conference_callback(callback: CallbackQuery):
    await callback.message.answer("🎤 Функція прес-конференції знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("share_news_"))
async def handle_share_news_callback(callback: CallbackQuery):
    await callback.message.answer("➡️ Функція поділитися новиною знаходиться в розробці.")
    await callback.answer()

@router.callback_query(F.data.startswith("report_news_menu_"))
async def handle_report_news_menu_callback(callback: CallbackQuery):
    await callback.message.answer("⚠️ Функція поскаржитись на новину знаходиться в розробці.")
    await callback.answer()

# --- My News / News Browse ---
@router.callback_query(F.data == "my_news")
async def handle_my_news_command(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        news_records = await connection.fetch(
            "SELECT id FROM news WHERE moderation_status = 'approved' AND expires_at > NOW() ORDER BY published_at DESC"
        )
        if not news_records:
            await callback.message.answer("Наразі немає доступних новин. Спробуйте додати нову новину або зайдіть пізніше.")
            await callback.answer()
            return

        news_ids = [r['id'] for r in news_records]
        await state.update_data(news_ids=news_ids, news_index=0)
        await state.set_state(NewsBrowse.Browse_news)

        current_news_id = news_ids[0]
        await callback.message.edit_text("Завантажую новину...")
        await send_news_to_user(callback.message.chat.id, current_news_id, 0, len(news_ids))
    await callback.answer()

async def send_news_to_user(chat_id: int, news_id: int, current_index: int, total_count: int):
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        news_record = await connection.fetchrow(
            "SELECT id, title, content, source_url, image_url, published_at, lang, ai_summary FROM news WHERE id = $1", news_id
        )
        if not news_record:
            await bot.send_message(chat_id, "Новина не знайдена.")
            return

        news_obj = News(
            id=news_record['id'],
            title=news_record['title'],
            content=news_record['content'],
            source_url=news_record['source_url'],
            image_url=news_record['image_url'],
            published_at=news_record['published_at'],
            lang=news_record['lang'],
            ai_summary=news_record['ai_summary'],
            source_id=None, tone=None, sentiment_score=None, country_code=None, media_type=None
        )

        message_text = (
            f"<b>{news_obj.title}</b>\n\n"
            f"{news_obj.content[:1000]}...\n\n" # Обрізаємо великий текст
            f"<i>Опубліковано: {news_obj.published_at.strftime('%d.%m.%Y %H:%M')}</i>\n"
            f"<i>Новина {current_index + 1} з {total_count}</i>"
        )
        if news_obj.source_url:
            message_text += f"\n\n🔗 {hlink('Читати джерело', news_obj.source_url)}"

        reply_markup = get_news_keyboard(news_obj.id)

        if news_obj.image_url:
            try:
                msg = await bot.send_photo(
                    chat_id, photo=news_obj.image_url, caption=message_text,
                    parse_mode=ParseMode.HTML, reply_markup=reply_markup, disable_notification=True
                )
            except Exception as e:
                logger.warning(f"Failed to send photo for news {news_id}: {e}. Sending without photo.")
                msg = await bot.send_message(
                    chat_id, message_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup, disable_web_page_preview=True
                )
        else:
            msg = await bot.send_message(
                chat_id, message_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup, disable_web_page_preview=True
            )
        await dp.fsm.get_context(chat_id, chat_id).update_data(last_message_id=msg.message_id)

@router.callback_query(NewsBrowse.Browse_news, F.data == "next_news")
async def process_next_news(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    news_ids = data.get('news_ids', [])
    current_index = data.get('news_index', 0)

    if current_index < len(news_ids) - 1:
        new_index = current_index + 1
        await state.update_data(news_index=new_index)
        await callback.message.delete()
        await send_news_to_user(callback.message.chat.id, news_ids[new_index], new_index, len(news_ids))
    else:
        await callback.answer("Це остання новина.", show_alert=True)
    await callback.answer()

@router.callback_query(NewsBrowse.Browse_news, F.data == "prev_news")
async def process_prev_news(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    news_ids = data.get('news_ids', [])
    current_index = data.get('news_index', 0)

    if current_index > 0:
        new_index = current_index - 1
        await state.update_data(news_index=new_index)
        await callback.message.delete()
        await send_news_to_user(callback.message.chat.id, news_ids[new_index], new_index, len(news_ids))
    else:
        await callback.answer("Це перша новина.", show_alert=True)
    await callback.answer()


# --- Add News ---
@router.callback_query(F.data == "add_news")
async def add_news_command(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddNews.waiting_for_news_url)
    await callback.message.answer("Будь ласка, надішліть посилання на новинну статтю.")
    await callback.answer()

@router.message(AddNews.waiting_for_news_url, F.text.regexp(r"https?://[^\s]+"))
async def process_news_url(message: Message, state: FSMContext):
    # Dummy logic to simulate news parsing
    news_url = message.text
    # In a real scenario, you'd parse the URL to get title, content, image, etc.
    # For now, we'll use placeholder or mock data.
    mock_title = f"Новина з {news_url.split('/')[2]}"
    mock_content = f"Це уявний зміст новинної статті за посиланням: {news_url}. Вона розповідає про важливі події у світі, вплив технологій на суспільство та нові відкриття у науці. Деталі залишаються за кадром, оскільки це лише симуляція парсингу реальної новини. Більше інформації можна знайти за посиланням."
    mock_image_url = "https://via.placeholder.com/600x400?text=News+Image" # Placeholder image

    await state.update_data(news_url=news_url, news_title=mock_title, news_content=mock_content, news_image_url=mock_image_url)
    await state.set_state(AddNews.waiting_for_news_lang)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Українська 🇺🇦", callback_data="lang_uk")],
        [InlineKeyboardButton(text="Англійська 🇬🇧", callback_data="lang_en")]
    ])
    await message.answer("Якою мовою написана новина?", reply_markup=keyboard)

@router.message(AddNews.waiting_for_news_url)
async def process_news_url_invalid(message: Message):
    await message.answer("Будь ласка, надішліть дійсне посилання на статтю.")

@router.callback_query(AddNews.waiting_for_news_lang, F.data.startswith("lang_"))
async def process_news_lang(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split('_')[1]
    await state.update_data(news_lang=lang)
    data = await state.get_data()
    
    title = data['news_title']
    content = data['news_content']
    image_url = data['news_image_url']
    news_url = data['news_url']

    message_text = (
        f"<b>Перевірте деталі новини:</b>\n\n"
        f"<b>Заголовок:</b> {title}\n"
        f"<b>Зміст:</b> {content[:500]}...\n" # Limit content for preview
        f"<b>Мова:</b> {lang.upper()}\n"
        f"<b>Посилання:</b> {hlink('Відкрити', news_url)}\n\n"
        f"Все вірно?"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Так, опублікувати", callback_data="confirm_publish_news"),
            InlineKeyboardButton(text="✏️ Редагувати", callback_data="edit_news_details")
        ],
        [
            InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_add_news")
        ]
    ])

    if image_url:
        try:
            await callback.message.answer_photo(photo=image_url, caption=message_text, reply_markup=keyboard)
        except Exception:
            await callback.message.answer(message_text, reply_markup=keyboard)
    else:
        await callback.message.answer(message_text, reply_markup=keyboard)
    
    await state.set_state(AddNews.confirm_news)
    await callback.answer()

@router.callback_query(AddNews.confirm_news, F.data == "confirm_publish_news")
async def confirm_publish_news(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    
    title = data['news_title']
    content = data['news_content']
    source_url = data['news_url']
    image_url = data['news_image_url']
    lang = data['news_lang']

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        # Save news to DB with 'pending_review' status
        news_id = await connection.fetchval(
            """
            INSERT INTO news (title, content, source_url, image_url, published_at, lang, moderation_status)
            VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, $5, 'pending_review')
            RETURNING id
            """,
            title, content, source_url, image_url, lang
        )
        logger.info(f"News {news_id} added by user {user_id} and set to pending_review.")
        await callback.message.edit_text("✅ Новину додано і відправлено на модерацію. Дякуємо!")
        # Optionally notify admins
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, f"🔔 Нова новина #{news_id} додана користувачем {user_id} (@{callback.from_user.username or callback.from_user.first_name}) і очікує модерації.")
    await state.clear()
    await callback.answer()

@router.callback_query(AddNews.confirm_news, F.data == "cancel_add_news")
async def cancel_add_news(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Додавання новини скасовано.")
    await callback.answer()

# --- Placeholder for Marketplace (Product Selling/Buying) Handlers ---
@router.callback_query(F.data == "add_product_for_sale")
async def add_product_for_sale_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SellProduct.waiting_for_name)
    await callback.message.edit_text("Будь ласка, введіть назву товару:")
    await callback.answer()

@router.message(SellProduct.waiting_for_name)
async def process_product_name(message: Message, state: FSMContext):
    await state.update_data(product_name=message.text)
    await state.set_state(SellProduct.waiting_for_description)
    await message.answer("Введіть опис товару:")

@router.message(SellProduct.waiting_for_description)
async def process_product_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(SellProduct.waiting_for_price)
    await message.answer("Введіть ціну товару (наприклад, 100.50):")

@router.message(SellProduct.waiting_for_price)
async def process_product_price(message: Message, state: FSMContext):
    try:
        price = Decimal(message.text.replace(',', '.'))
        if price <= 0:
            raise ValueError
        await state.update_data(price=price)
        await state.set_state(SellProduct.waiting_for_currency)
        await message.answer("Введіть валюту (наприклад, UAH, USD):")
    except ValueError:
        await message.answer("Будь ласка, введіть коректну ціну (число, більше 0).")

@router.message(SellProduct.waiting_for_currency)
async def process_product_currency(message: Message, state: FSMContext):
    currency = message.text.upper()
    if not re.match(r'^[A-Z]{3}$', currency):
        await message.answer("Будь ласка, введіть коректний код валюти (3 літери, наприклад, UAH, USD).")
        return
    await state.update_data(currency=currency)
    await state.set_state(SellProduct.waiting_for_image)
    await message.answer("Надішліть фото товару або введіть 'без фото':")

@router.message(SellProduct.waiting_for_image)
async def process_product_image(message: Message, state: FSMContext):
    image_url = None
    if message.photo:
        image_url = message.photo[-1].file_id # Get largest photo
        # In a real app, you'd save this file_id or download and store the photo URL
    elif message.text and message.text.lower() == 'без фото':
        pass # image_url remains None
    else:
        await message.answer("Будь ласка, надішліть фото або напишіть 'без фото'.")
        return

    await state.update_data(image_url=image_url)
    await state.set_state(SellProduct.waiting_for_e_point)
    await message.answer("Введіть бажане місце зустрічі для передачі товару (наприклад, 'метро Хрещатик', 'ТЦ Глобус'):")

@router.message(SellProduct.waiting_for_e_point)
async def process_e_point(message: Message, state: FSMContext):
    await state.update_data(e_point_location_text=message.text)
    data = await state.get_data()

    product_name = data.get('product_name')
    description = data.get('description')
    price = data.get('price')
    currency = data.get('currency')
    image_url = data.get('image_url')
    e_point_location_text = data.get('e_point_location_text')

    confirm_text = (
        f"✨ <b>Перевірте деталі товару:</b>\n\n"
        f"<b>Назва:</b> {product_name}\n"
        f"<b>Опис:</b> {description}\n"
        f"<b>Ціна:</b> {price} {currency}\n"
        f"<b>Місце зустрічі:</b> {e_point_location_text}\n"
        f"<b>Фото:</b> {'Присутнє' if image_url else 'Відсутнє'}\n\n"
        f"Все вірно?"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Так, додати", callback_data="confirm_add_product"),
            InlineKeyboardButton(text="✏️ Редагувати", callback_data="edit_product_creation")
        ],
        [
            InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_add_product")
        ]
    ])

    if image_url:
        try:
            await message.answer_photo(photo=image_url, caption=confirm_text, reply_markup=keyboard)
        except Exception:
            await message.answer(confirm_text, reply_markup=keyboard)
    else:
        await message.answer(confirm_text, reply_markup=keyboard)
    
    await state.set_state(SellProduct.confirm_product)

@router.callback_query(SellProduct.confirm_product, F.data == "confirm_add_product")
async def confirm_add_product_to_db(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    
    product_name = data.get('product_name')
    description = data.get('description')
    price = data.get('price')
    currency = data.get('currency')
    image_url = data.get('image_url')
    e_point_location_text = data.get('e_point_location_text')

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        product_id = await connection.fetchval(
            """
            INSERT INTO products_for_sale (user_id, product_name, description, price, currency, image_url, e_point_location_text, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending_review')
            RETURNING id
            """,
            user_id, product_name, description, price, currency, image_url, e_point_location_text
        )
        logger.info(f"Product {product_id} added by user {user_id} for review.")
        await callback.message.edit_text("✅ Товар додано і відправлено на модерацію. Дякуємо!")
        # Optionally notify admins
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, f"🔔 Новий товар #{product_id} доданий користувачем {user_id} (@{callback.from_user.username or callback.from_user.first_name}) і очікує модерації.")
    await state.clear()
    await callback.answer()

@router.callback_query(SellProduct.confirm_product, F.data == "cancel_add_product")
async def cancel_add_product_to_db(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Додавання товару скасовано.")
    await callback.answer()

@router.callback_query(SellProduct.confirm_product, F.data == "edit_product_creation")
async def edit_product_creation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SellProduct.editing_field)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назва", callback_data="edit_field_product_name")],
        [InlineKeyboardButton(text="Опис", callback_data="edit_field_description")],
        [InlineKeyboardButton(text="Ціна", callback_data="edit_field_price")],
        [InlineKeyboardButton(text="Валюта", callback_data="edit_field_currency")],
        [InlineKeyboardButton(text="Фото", callback_data="edit_field_image_url")],
        [InlineKeyboardButton(text="Місце зустрічі", callback_data="edit_field_e_point_location_text")],
        [InlineKeyboardButton(text="Завершити редагування", callback_data="finish_editing_product_creation")]
    ])
    await callback.message.edit_text("Оберіть поле для редагування:", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(SellProduct.editing_field, F.data.startswith("edit_field_"))
async def start_editing_field_creation(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split('_', 2)[2] # e.g., 'product_name'
    await state.update_data(current_edit_field=field)
    await callback.message.answer(f"Введіть нове значення для '{field}':")
    await callback.answer()

@router.message(SellProduct.editing_field, F.text | F.photo)
async def process_editing_field_creation(message: Message, state: FSMContext):
    data = await state.get_data()
    field_to_edit = data.get('current_edit_field')

    if field_to_edit == 'image_url':
        if message.photo:
            new_value = message.photo[-1].file_id
        elif message.text and message.text.lower() == 'без фото':
            new_value = None
        else:
            await message.answer("Будь ласка, надішліть фото або напишіть 'без фото'.")
            return
    elif field_to_edit == 'price':
        try:
            new_value = Decimal(message.text.replace(',', '.'))
            if new_value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Будь ласка, введіть коректну ціну (число, більше 0).")
            return
    elif field_to_edit == 'currency':
        new_value = message.text.upper()
        if not re.match(r'^[A-Z]{3}$', new_value):
            await message.answer("Будь ласка, введіть коректний код валюти (3 літери, наприклад, UAH, USD).")
            return
    else:
        new_value = message.text

    await state.update_data(**{field_to_edit: new_value})
    
    # Return to editing menu
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назва", callback_data="edit_field_product_name")],
        [InlineKeyboardButton(text="Опис", callback_data="edit_field_description")],
        [InlineKeyboardButton(text="Ціна", callback_data="edit_field_price")],
        [InlineKeyboardButton(text="Валюта", callback_data="edit_field_currency")],
        [InlineKeyboardButton(text="Фото", callback_data="edit_field_image_url")],
        [InlineKeyboardButton(text="Місце зустрічі", callback_data="edit_field_e_point_location_text")],
        [InlineKeyboardButton(text="Завершити редагування", callback_data="finish_editing_product_creation")]
    ])
    await message.answer(f"Поле '{field_to_edit}' оновлено. Оберіть наступне поле або завершіть редагування:", reply_markup=keyboard)
    await state.set_state(SellProduct.editing_field) # Stay in editing state

@router.callback_query(SellProduct.editing_field, F.data == "finish_editing_product_creation")
async def finish_editing_product_creation(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    product_name = data.get('product_name')
    description = data.get('description')
    price = data.get('price')
    currency = data.get('currency')
    image_url = data.get('image_url')
    e_point_location_text = data.get('e_point_location_text')

    confirm_text = (
        f"✨ <b>Перевірте оновлені деталі товару:</b>\n\n"
        f"<b>Назва:</b> {product_name}\n"
        f"<b>Опис:</b> {description}\n"
        f"<b>Ціна:</b> {price} {currency}\n"
        f"<b>Місце зустрічі:</b> {e_point_location_text}\n"
        f"<b>Фото:</b> {'Присутнє' if image_url else 'Відсутнє'}\n\n"
        f"Все вірно?"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Так, додати", callback_data="confirm_add_product"),
            InlineKeyboardButton(text="✏️ Редагувати ще", callback_data="edit_product_creation")
        ],
        [
            InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_add_product")
        ]
    ])

    if image_url:
        try:
            await callback.message.answer_photo(photo=image_url, caption=confirm_text, reply_markup=keyboard)
        except Exception:
            await callback.message.answer(confirm_text, reply_markup=keyboard)
    else:
        await callback.message.answer(confirm_text, reply_markup=keyboard)
    
    await state.set_state(SellProduct.confirm_product)
    await callback.answer()

@router.callback_query(F.data == "browse_all_products")
async def browse_all_products(callback: CallbackQuery, state: FSMContext):
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        product_records = await connection.fetch(
            "SELECT id FROM products_for_sale WHERE status = 'approved' ORDER BY created_at DESC"
        )
        if not product_records:
            await callback.message.answer("Наразі немає доступних товарів для покупки.")
            await callback.answer()
            return

        product_ids = [r['id'] for r in product_records]
        await state.update_data(product_ids=product_ids, product_index=0)
        await state.set_state(NewsBrowse.Browse_news) # Re-use NewsBrowse state for generic Browse

        current_product_id = product_ids[0]
        await callback.message.edit_text("Завантажую товар...") # Edit the previous message
        await send_product_to_user(callback.message.chat.id, current_product_id, 0, len(product_ids))
    await callback.answer()

@router.callback_query(F.data == "prev_product")
async def process_prev_product(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    product_ids = data.get('product_ids', [])
    current_index = data.get('product_index', 0)

    if current_index > 0:
        new_index = current_index - 1
        await state.update_data(product_index=new_index)
        await callback.message.delete() # Delete previous product message
        await send_product_to_user(callback.message.chat.id, product_ids[new_index], new_index, len(product_ids))
    else:
        await callback.answer("Це перший товар.", show_alert=True)
    await callback.answer()

@router.callback_query(F.data == "next_product")
async def process_next_product(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    product_ids = data.get('product_ids', [])
    current_index = data.get('product_index', 0)

    if current_index < len(product_ids) - 1:
        new_index = current_index + 1
        await state.update_data(product_index=new_index)
        await callback.message.delete() # Delete previous product message
        await send_product_to_user(callback.message.chat.id, product_ids[new_index], new_index, len(product_ids))
    else:
        await callback.answer("Це останній товар.", show_alert=True)
    await callback.answer()

@router.callback_query(F.data == "stop_browse_products")
async def stop_browse_products(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete() # Delete product message
    await callback.message.answer("Завершено перегляд товарів.", reply_markup=get_buy_product_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("contact_seller_"))
async def contact_seller_callback(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        product_record = await connection.fetchrow("SELECT user_id, product_name FROM products_for_sale WHERE id = $1", product_id)
        if not product_record:
            await callback.message.answer("Товар не знайдено.")
            await callback.answer()
            return
        
        seller_id = product_record['user_id']
        if seller_id == callback.from_user.id:
            await callback.message.answer("Ви не можете написати собі.")
            await callback.answer()
            return

        await state.update_data(recipient_user_id=seller_id, original_product_id=product_id)
        await state.set_state(DirectMessage.waiting_for_message_text)
        await callback.message.answer(f"Напишіть повідомлення продавцю товару '{product_record['product_name']}':")
    await callback.answer()

@router.message(DirectMessage.waiting_for_message_text)
async def send_dm_to_user(message: Message, state: FSMContext):
    data = await state.get_data()
    recipient_id = data.get('recipient_user_id')
    original_product_id = data.get('original_product_id')
    sender_id = message.from_user.id

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        # Check if recipient has blocked sender
        recipient_user_obj = await get_user(recipient_id)
        if recipient_user_obj and sender_id in recipient_user_obj.blocked_users:
            await message.answer("Ваше повідомлення не може бути доставлене, оскільки користувач заблокував вас.")
            await state.clear()
            return

    sender_info = f"Від: @{message.from_user.username or message.from_user.first_name} (ID: {message.from_user.id})"
    product_link = f"До товару: {hlink(f'#{original_product_id}', f'https://t.me/{bot.me.username}?start=product_{original_product_id}')}"

    try:
        await bot.send_message(
            recipient_id,
            f"<b>Нове повідомлення в чаті маркетплейсу!</b>\n\n"
            f"{sender_info}\n"
            f"{product_link}\n\n"
            f"<b>Повідомлення:</b>\n{message.text}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✉️ Відповісти", callback_data=f"reply_to_dm_{sender_id}_{original_product_id}")]
            ])
        )
        await message.answer("✅ Ваше повідомлення надіслано.")
    except Exception as e:
        logger.error(f"Failed to send DM from {sender_id} to {recipient_id}: {e}")
        await message.answer("❌ Не вдалося надіслати повідомлення.")
    finally:
        await state.clear()

@router.callback_query(F.data.startswith("reply_to_dm_"))
async def reply_to_dm_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split('_')
    sender_id = int(parts[3])
    product_id = int(parts[4])
    
    await state.update_data(recipient_user_id=sender_id, original_product_id=product_id)
    await state.set_state(DirectMessage.waiting_for_message_text)
    await callback.message.answer(f"Напишіть повідомлення у відповідь користувачу ID:{sender_id} щодо товару #{product_id}:")
    await callback.answer()

@router.callback_query(F.data.startswith("buy_product_"))
async def handle_buy_product_callback(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split('_')[2])
    buyer_id = callback.from_user.id

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        product_record = await connection.fetchrow("SELECT user_id, product_name, status FROM products_for_sale WHERE id = $1", product_id)
        if not product_record:
            await callback.message.answer("Товар не знайдено або він не доступний.")
            await callback.answer()
            return
        
        seller_id = product_record['user_id']
        product_name = product_record['product_name']

        if seller_id == buyer_id:
            await callback.message.answer("Ви не можете купити власний товар.")
            await callback.answer()
            return

        if product_record['status'] != 'approved':
            await callback.message.answer(f"На жаль, товар '{product_name}' не доступний для покупки (статус: {product_record['status']}).")
            await callback.answer()
            return

        # Check for existing pending transaction for this product and buyer
        existing_transaction = await connection.fetchrow(
            "SELECT id FROM transactions WHERE product_id = $1 AND buyer_id = $2 AND status IN ('initiated', 'buyer_confirmed', 'seller_confirmed')",
            product_id, buyer_id
        )
        if existing_transaction:
            await callback.message.answer("У вас вже є активна угода щодо цього товару. Перейдіть до Мої угоди, щоб керувати нею.")
            await callback.answer()
            return

        transaction_id = await connection.fetchval(
            """
            INSERT INTO transactions (product_id, seller_id, buyer_id, status)
            VALUES ($1, $2, $3, 'initiated')
            RETURNING id
            """,
            product_id, seller_id, buyer_id
        )

        await state.update_data(
            transaction_id=transaction_id,
            product_id=product_id,
            seller_id=seller_id,
            buyer_id=buyer_id
        )
        await state.set_state(ProductTransaction.awaiting_buyer_confirmation)

        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Так, підтверджую покупку", callback_data=f"confirm_purchase_{transaction_id}")],
            [InlineKeyboardButton(text="❌ Скасувати", callback_data=f"cancel_transaction_{transaction_id}")]
        ])
        await callback.message.answer(
            f"Ви ініціюєте покупку товару '{product_name}'. "
            f"Продавець отримає сповіщення. Будь ласка, підтвердіть вашу згоду:\n\n"
            f"<b>Номер угоди: {transaction_id}</b>", reply_markup=confirm_keyboard
        )
        await callback.answer()

@router.callback_query(ProductTransaction.awaiting_buyer_confirmation, F.data.startswith("confirm_purchase_"))
async def buyer_confirms_purchase(callback: CallbackQuery, state: FSMContext):
    transaction_id = int(callback.data.split('_')[2])
    data = await state.get_data()
    stored_transaction_id = data.get('transaction_id')

    if transaction_id != stored_transaction_id:
        await callback.message.answer("Помилка: невідповідність угоди. Спробуйте ще раз.")
        await callback.answer()
        return

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        transaction = await connection.fetchrow("SELECT * FROM transactions WHERE id = $1", transaction_id)
        if not transaction or transaction['status'] != 'initiated':
            await callback.message.answer("Угода не знайдена або вже не активна.")
            await callback.answer()
            return
        
        product = await connection.fetchrow("SELECT product_name FROM products_for_sale WHERE id = $1", transaction['product_id'])
        product_name = product['product_name'] if product else "невідомий товар"

        await connection.execute(
            "UPDATE transactions SET status = 'buyer_confirmed', updated_at = CURRENT_TIMESTAMP WHERE id = $1",
            transaction_id
        )
        await callback.message.edit_text(f"✅ Ви підтвердили свою готовність купити товар '{product_name}'. "
                                       f"Продавець отримав запит і має підтвердити свою згоду на продаж.\n"
                                       f"Номер угоди: {transaction_id}")
        
        seller_id = transaction['seller_id']
        buyer_username = callback.from_user.username or callback.from_user.first_name
        seller_confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Підтвердити продаж", callback_data=f"confirm_sell_{transaction_id}")],
            [InlineKeyboardButton(text="❌ Відхилити", callback_data=f"decline_transaction_{transaction_id}")]
        ])
        await bot.send_message(
            seller_id,
            f"🔔 Угода #{transaction_id} по вашому товару '{product_name}' була ініційована та підтверджена покупцем @{buyer_username} (ID: {callback.from_user.id}).\n"
            f"Будь ласка, підтвердіть або відхиліть продаж:",
            reply_markup=seller_confirm_keyboard
        )
    await state.clear()
    await callback.answer()

@router.callback_query(ProductTransaction.awaiting_buyer_confirmation, F.data.startswith("cancel_transaction_"))
async def buyer_cancels_purchase(callback: CallbackQuery, state: FSMContext):
    transaction_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        transaction = await connection.fetchrow("SELECT * FROM transactions WHERE id = $1", transaction_id)
        if not transaction:
            await callback.message.answer("Угода не знайдена або вже не активна.")
            await callback.answer()
            return
        
        await connection.execute(
            "UPDATE transactions SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = $1",
            transaction_id
        )
        product_name = (await connection.fetchrow("SELECT product_name FROM products_for_sale WHERE id = $1", transaction['product_id']))['product_name']
        await callback.message.edit_text(f"❌ Ви скасували угоду по товару '{product_name}' (Номер угоди: {transaction_id}).")
        
        # Notify seller
        seller_id = transaction['seller_id']
        await bot.send_message(seller_id, f"ℹ️ Покупець скасував угоду #{transaction_id} по вашому товару '{product_name}'.")
    await state.clear()
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_sell_"))
async def seller_confirms_sell(callback: CallbackQuery, state: FSMContext):
    transaction_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        transaction = await connection.fetchrow("SELECT * FROM transactions WHERE id = $1", transaction_id)
        if not transaction or transaction['status'] != 'buyer_confirmed' or transaction['seller_id'] != callback.from_user.id:
            await callback.message.answer("Угода не знайдена, вже не активна або ви не є її продавцем.")
            await callback.answer()
            return
        
        product_name = (await connection.fetchrow("SELECT product_name FROM products_for_sale WHERE id = $1", transaction['product_id']))['product_name']

        await connection.execute(
            "UPDATE transactions SET status = 'seller_confirmed', updated_at = CURRENT_TIMESTAMP WHERE id = $1",
            transaction_id
        )
        await connection.execute(
            "UPDATE products_for_sale SET status = 'sold' WHERE id = $1",
            transaction['product_id']
        )
        await callback.message.edit_text(f"✅ Ви підтвердили продаж товару '{product_name}'. "
                                       f"Тепер ви можете зв'язатися з покупцем для організації зустрічі.\n"
                                       f"Номер угоди: {transaction_id}")
        
        buyer_id = transaction['buyer_id']
        seller_username = callback.from_user.username or callback.from_user.first_name

        buyer_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Написати продавцю", callback_data=f"contact_seller_{transaction['product_id']}")]
        ])
        await bot.send_message(
            buyer_id,
            f"🎉 Продавець @{seller_username} (ID: {callback.from_user.id}) підтвердив продаж товару '{product_name}'! "
            f"Угода #{transaction_id} готова до завершення.\n"
            f"Ви можете зв'язатися з продавцем для організації зустрічі.",
            reply_markup=buyer_keyboard
        )

        # Prompt for review
        await bot.send_message(
            buyer_id,
            f"Будь ласка, залиште відгук про продавця @{seller_username} після завершення угоди:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Залишити відгук", callback_data=f"leave_review_seller_{transaction_id}")]
            ])
        )
        await callback.answer()

@router.callback_query(F.data.startswith("decline_transaction_"))
async def seller_declines_sell(callback: CallbackQuery, state: FSMContext):
    transaction_id = int(callback.data.split('_')[2])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        transaction = await connection.fetchrow("SELECT * FROM transactions WHERE id = $1", transaction_id)
        if not transaction or transaction['seller_id'] != callback.from_user.id:
            await callback.message.answer("Угода не знайдена, або ви не є її продавцем.")
            await callback.answer()
            return
        
        product_name = (await connection.fetchrow("SELECT product_name FROM products_for_sale WHERE id = $1", transaction['product_id']))['product_name']
        
        await connection.execute(
            "UPDATE transactions SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = $1",
            transaction_id
        )
        await callback.message.edit_text(f"❌ Ви відхилили угоду по товару '{product_name}' (Номер угоди: {transaction_id}).")
        
        # Notify buyer
        buyer_id = transaction['buyer_id']
        await bot.send_message(buyer_id, f"ℹ️ Продавець відхилив угоду #{transaction_id} по товару '{product_name}'.")
    await state.clear()
    await callback.answer()

@router.callback_query(F.data.startswith("my_products"))
async def handle_my_products_command(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        my_products = await connection.fetch(
            "SELECT id, product_name, status FROM products_for_sale WHERE user_id = $1 ORDER BY created_at DESC", user_id
        )

        if not my_products:
            await callback.message.answer("У вас ще немає товарів для продажу.")
            await callback.answer()
            return

        for product in my_products:
            response_text = (
                f"📦 <b>Товар #{product['id']}</b>: {product['product_name']}\n"
                f"Статус: <i>{product['status']}</i>\n"
            )

            keyboard_buttons = []
            if product['status'] == 'approved' or product['status'] == 'pending_review':
                keyboard_buttons.append(InlineKeyboardButton(text="🧠 AI-допомога з продажу", callback_data=f"ai_sales_assist_{product['id']}"))
                keyboard_buttons.append(InlineKeyboardButton(text="💰 Позначити як продано", callback_data=f"mark_product_sold_{product['id']}"))
            
            keyboard_buttons.append(InlineKeyboardButton(text="✏️ Редагувати", callback_data=f"edit_product_{product['id']}"))
            keyboard_buttons.append(InlineKeyboardButton(text="🗑️ Видалити", callback_data=f"delete_product_confirm_{product['id']}"))
            
            if keyboard_buttons:
                row1 = keyboard_buttons[:2]
                row2 = keyboard_buttons[2:]
                final_keyboard = InlineKeyboardMarkup(inline_keyboard=[row1, row2])
                await callback.message.answer(response_text, parse_mode=ParseMode.HTML, reply_markup=final_keyboard, disable_web_page_preview=True)
            else:
                await callback.message.answer(response_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            await asyncio.sleep(0.3) # To avoid flood limits
        
        await callback.message.answer("⬆️ Це ваші товари для продажу.")
    await callback.answer()

@router.callback_query(F.data.startswith("ai_sales_assist_"))
async def handle_ai_sales_assist_callback(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split('_')[3])
    conn = await get_db_pool()
    try:
        conn = await get_db_pool()
        product_record = await conn.fetchrow(
            "SELECT product_name, description, price, currency FROM products_for_sale WHERE id = $1", product_id
        )

        if not product_record:
            await callback.message.answer("❌ Товар не знайдено.")
            await callback.answer()
            return

        await state.update_data(
            sales_product_id=product_id,
            sales_product_name=product_record['product_name'],
            sales_product_description=product_record['description'],
            sales_product_price=product_record['price'],
            sales_product_currency=product_record['currency']
        )
        await state.set_state(SalesAssistance.waiting_for_sales_query)

        await callback.message.edit_text(
            "📈 <b>AI-асистент з продажу:</b>\n\n"
            f"Напишіть, яка допомога вам потрібна щодо товару '<b>{product_record['product_name']}</b>' ({product_record['price']} {product_record['currency']}). "
            "Наприклад: 'Як покращити опис?', 'Які ключові переваги виділити?', 'Склади короткий слоган'."
            "\n\nЩоб вийти, введіть /cancel."
            , parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logger.error(f"Помилка при ініціалізації AI-допомоги з продажу для товару {product_id} користувача {callback.from_user.id}: {e}")
        await callback.message.answer("❌ Сталася помилка при запуску AI-асистента. Спробуйте пізніше.")
    finally:
        if conn:
            await db_pool.release(conn)
    await callback.answer()

@router.message(SalesAssistance.waiting_for_sales_query, F.text)
async def process_seller_sales_query(message: Message, state: FSMContext):
    user_id = message.from_user.id
    seller_query = message.text.strip()

    if seller_query.lower() == "/cancel":
        await message.answer("✅ AI-асистент з продажу завершено.")
        await state.clear()
        return

    if not seller_query:
        await message.answer("Будь ласка, введіть ваш запит для AI-асистента.")
        return

    data = await state.get_data()
    product_id = data.get('sales_product_id')
    product_name = data.get('sales_product_name')
    product_description = data.get('sales_product_description')
    product_price = data.get('sales_product_price')
    product_currency = data.get('sales_product_currency')

    if not product_id:
        await message.answer("Вибачте, контекст товару втрачено. Будь ласка, почніть AI-допомогу з продажу знову.")
        await state.clear()
        return

    await message.answer("⏳ AI-асистент генерує рекомендації...")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    try:
        ai_sales_advice = await ai_assist_seller_sales_pitch(
            product_name, product_description, product_price, product_currency, seller_query
        )

        if ai_sales_advice:
            await message.answer(f"📈 <b>Порада AI для продажу '{product_name}':</b>\n\n{ai_sales_advice}", parse_mode=ParseMode.HTML)
            logger.info(f"Користувач {user_id} отримав пораду AI для продажу товару {product_id}.")
        else:
            await message.answer("❌ На жаль, AI не зміг надати пораду з продажу. Спробуйте інше запитання.")
            logger.warning(f"AI не зміг надати пораду з продажу для товару {product_id}.")

    except Exception as e:
        logger.error(f"Помилка при обробці запиту продавця щодо продажів для користувача {user_id}: {e}")
        await message.answer("❌ Сталася помилка при отриманні поради від AI. Спробуйте пізніше.")
    finally:
        await state.clear()

@router.message(Command("cancel"), SalesAssistance.waiting_for_sales_query)
async def cancel_sales_assistance(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ AI-асистент з продажу завершено.")

@router.callback_query(F.data.startswith("mark_product_sold_"))
async def mark_product_sold(callback: CallbackQuery):
    product_id = int(callback.data.split('_')[3])
    user_id = callback.from_user.id
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        product = await connection.fetchrow("SELECT user_id, product_name, status FROM products_for_sale WHERE id = $1", product_id)
        if not product or product['user_id'] != user_id:
            await callback.message.answer("Ви не можете позначити цей товар як проданий.")
            await callback.answer()
            return
        
        if product['status'] == 'sold':
            await callback.message.answer(f"Товар '{product['product_name']}' вже позначено як проданий.")
            await callback.answer()
            return

        await connection.execute("UPDATE products_for_sale SET status = 'sold' WHERE id = $1", product_id)
        await callback.message.edit_text(f"✅ Товар '{product['product_name']}' позначено як проданий!")
        logger.info(f"Product {product_id} marked as sold by user {user_id}.")
    await callback.answer()

@router.callback_query(F.data.startswith("edit_product_"))
async def edit_product_existing(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split('_')[2])
    await state.update_data(editing_product_id=product_id)
    await state.set_state(SellProduct.editing_field)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назва", callback_data="edit_field_product_name")],
        [InlineKeyboardButton(text="Опис", callback_data="edit_field_description")],
        [InlineKeyboardButton(text="Ціна", callback_data="edit_field_price")],
        [InlineKeyboardButton(text="Валюта", callback_data="edit_field_currency")],
        [InlineKeyboardButton(text="Фото", callback_data="edit_field_image_url")],
        [InlineKeyboardButton(text="Місце зустрічі", callback_data="edit_field_e_point_location_text")],
        [InlineKeyboardButton(text="Завершити редагування", callback_data="finish_editing_existing_product")]
    ])
    await callback.message.edit_text(f"Оберіть поле для редагування товару #{product_id}:", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(SellProduct.editing_field, F.data == "finish_editing_existing_product")
async def finish_editing_existing_product(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    product_id = data.get('editing_product_id')
    user_id = callback.from_user.id

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        # Fetch current product details to apply updates
        current_product = await connection.fetchrow("SELECT * FROM products_for_sale WHERE id = $1 AND user_id = $2", product_id, user_id)
        if not current_product:
            await callback.message.answer("Товар не знайдено або він вам не належить.")
            await state.clear()
            await callback.answer()
            return
        
        # Apply updates from FSM data
        update_fields = {}
        for key, value in data.items():
            if key in ['product_name', 'description', 'price', 'currency', 'image_url', 'e_point_location_text']:
                if value is not None and value != current_product[key]: # Only update if value changed
                    update_fields[key] = value

        if update_fields:
            set_clauses = [f"{k} = ${i+1}" for i, k in enumerate(update_fields.keys())]
            query = f"UPDATE products_for_sale SET {', '.join(set_clauses)} WHERE id = ${len(update_fields)+1}"
            values = list(update_fields.values()) + [product_id]
            await connection.execute(query, *values)
            await callback.message.answer(f"✅ Товар #{product_id} оновлено! Відправлено на перевірку.")
            await connection.execute("UPDATE products_for_sale SET status = 'pending_review' WHERE id = $1", product_id)
            logger.info(f"Product {product_id} updated by user {user_id} and set to pending_review.")
            for admin_id in ADMIN_IDS:
                await bot.send_message(admin_id, f"🔔 Товар #{product_id} оновлено користувачем {user_id} (@{callback.from_user.username or callback.from_user.first_name}) і відправлено на повторну модерацію.")
        else:
            await callback.message.answer("Немає змін для оновлення товару.")
        
    await state.clear()
    await callback.answer()

@router.message(SellProduct.editing_field, F.text | F.photo)
async def process_editing_field_existing(message: Message, state: FSMContext):
    data = await state.get_data()
    field_to_edit = data.get('current_edit_field')
    product_id = data.get('editing_product_id')

    if not product_id:
        await message.answer("Вибачте, контекст товару втрачено. Будь ласка, спробуйте ще раз.")
        await state.clear()
        return

    if field_to_edit == 'image_url':
        if message.photo:
            new_value = message.photo[-1].file_id
        elif message.text and message.text.lower() == 'без фото':
            new_value = None
        else:
            await message.answer("Будь ласка, надішліть фото або напишіть 'без фото'.")
            return
    elif field_to_edit == 'price':
        try:
            new_value = Decimal(message.text.replace(',', '.'))
            if new_value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Будь ласка, введіть коректну ціну (число, більше 0).")
            return
    elif field_to_edit == 'currency':
        new_value = message.text.upper()
        if not re.match(r'^[A-Z]{3}$', new_value):
            await message.answer("Будь ласка, введіть коректний код валюти (3 літери, наприклад, UAH, USD).")
            return
    else:
        new_value = message.text

    await state.update_data(**{field_to_edit: new_value})
    
    # Return to editing menu for existing product
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назва", callback_data="edit_field_product_name")],
        [InlineKeyboardButton(text="Опис", callback_data="edit_field_description")],
        [InlineKeyboardButton(text="Ціна", callback_data="edit_field_price")],
        [InlineKeyboardButton(text="Валюта", callback_data="edit_field_currency")],
        [InlineKeyboardButton(text="Фото", callback_data="edit_field_image_url")],
        [InlineKeyboardButton(text="Місце зустрічі", callback_data="edit_field_e_point_location_text")],
        [InlineKeyboardButton(text="Завершити редагування", callback_data="finish_editing_existing_product")]
    ])
    await message.answer(f"Поле '{field_to_edit}' оновлено. Оберіть наступне поле або завершіть редагування:", reply_markup=keyboard)
    await state.set_state(SellProduct.editing_field) # Stay in editing state

@router.callback_query(F.data.startswith("delete_product_confirm_"))
async def delete_product_confirm(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split('_')[3])
    await state.update_data(deleting_product_id=product_id)
    await state.set_state(SellProduct.deleting_product_id) # Use this state to confirm deletion

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Так, видалити", callback_data=f"delete_product_confirmed_{product_id}"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_delete_product")
        ]
    ])
    await callback.message.answer(f"Ви впевнені, що хочете видалити товар #{product_id}? Цю дію не можна буде скасувати.", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(SellProduct.deleting_product_id, F.data.startswith("delete_product_confirmed_"))
async def delete_product_confirmed(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split('_')[3])
    user_id = callback.from_user.id
    data = await state.get_data()
    stored_product_id = data.get('deleting_product_id')

    if product_id != stored_product_id:
        await callback.message.answer("Помилка: невідповідність товару. Спробуйте ще раз.")
        await state.clear()
        await callback.answer()
        return

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        product = await connection.fetchrow("SELECT user_id, product_name FROM products_for_sale WHERE id = $1", product_id)
        if not product or product['user_id'] != user_id:
            await callback.message.answer("Ви не можете видалити цей товар.")
            await state.clear()
            await callback.answer()
            return
        
        await connection.execute("DELETE FROM products_for_sale WHERE id = $1", product_id)
        await callback.message.edit_text(f"🗑️ Товар '{product['product_name']}' (ID: {product_id}) успішно видалено.")
        logger.info(f"Product {product_id} deleted by user {user_id}.")
    await state.clear()
    await callback.answer()

@router.callback_query(SellProduct.deleting_product_id, F.data == "cancel_delete_product")
async def cancel_delete_product(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Видалення товару скасовано.")
    await callback.answer()

@router.callback_query(F.data.startswith("my_transactions"))
async def handle_my_transactions_command(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        transactions = await connection.fetch(
            """
            SELECT t.id, t.product_id, t.seller_id, t.buyer_id, t.status, t.created_at,
                   p.product_name, p.price, p.currency,
                   s.username AS seller_username, s.first_name AS seller_first_name,
                   b.username AS buyer_username, b.first_name AS buyer_first_name
            FROM transactions t
            JOIN products_for_sale p ON t.product_id = p.id
            JOIN users s ON t.seller_id = s.id
            JOIN users b ON t.buyer_id = b.id
            WHERE t.seller_id = $1 OR t.buyer_id = $1
            ORDER BY t.created_at DESC
            """, user_id
        )

        if not transactions:
            await callback.message.answer("У вас ще немає активних або завершених угод.")
            await callback.answer()
            return

        for transaction in transactions:
            is_seller = (transaction['seller_id'] == user_id)
            other_party_id = transaction['buyer_id'] if is_seller else transaction['seller_id']
            other_party_username = transaction['buyer_username'] if is_seller else transaction['seller_username']
            other_party_name = transaction['buyer_first_name'] if is_seller else transaction['seller_first_name']
            
            status_map = {
                'initiated': 'Ініційовано',
                'buyer_confirmed': 'Покупець підтвердив',
                'seller_confirmed': 'Продавець підтвердив',
                'completed': 'Завершено',
                'cancelled': 'Скасовано'
            }
            status_text = status_map.get(transaction['status'], transaction['status'])

            response_text = (
                f"🤝 <b>Угода #{transaction['id']}</b>\n"
                f"Товар: {transaction['product_name']} ({transaction['price']} {transaction['currency']})\n"
                f"Ваша роль: {'Продавець' if is_seller else 'Покупець'}\n"
                f"{'Покупець' if is_seller else 'Продавець'}: @{other_party_username or other_party_name} (ID: {other_party_id})\n"
                f"Статус: <i>{status_text}</i>\n"
                f"Створено: {transaction['created_at'].strftime('%d.%m.%Y %H:%M')}\n"
            )

            keyboard_buttons = []
            if transaction['status'] == 'buyer_confirmed' and is_seller:
                keyboard_buttons.append(InlineKeyboardButton(text="✅ Підтвердити продаж", callback_data=f"confirm_sell_{transaction['id']}"))
                keyboard_buttons.append(InlineKeyboardButton(text="❌ Відхилити", callback_data=f"decline_transaction_{transaction['id']}"))
            elif transaction['status'] == 'initiated' and not is_seller:
                keyboard_buttons.append(InlineKeyboardButton(text="✅ Підтвердити покупку", callback_data=f"confirm_purchase_{transaction['id']}"))
                keyboard_buttons.append(InlineKeyboardButton(text="❌ Скасувати", callback_data=f"cancel_transaction_{transaction['id']}"))
            
            if transaction['status'] == 'seller_confirmed': # Both can contact after seller confirms
                keyboard_buttons.append(InlineKeyboardButton(text="✉️ Написати іншому учаснику", callback_data=f"contact_other_party_{other_party_id}_{transaction['product_id']}"))
                keyboard_buttons.append(InlineKeyboardButton(text="✅ Завершити угоду", callback_data=f"complete_transaction_{transaction['id']}"))
            
            if transaction['status'] == 'completed' and not is_seller: # Buyer can review seller
                 # Check if buyer already reviewed seller for this transaction
                review_exists = await connection.fetchrow(
                    "SELECT id FROM reviews WHERE transaction_id = $1 AND reviewer_id = $2 AND reviewed_user_id = $3",
                    transaction['id'], user_id, transaction['seller_id']
                )
                if not review_exists:
                    keyboard_buttons.append(InlineKeyboardButton(text="✍️ Залишити відгук про продавця", callback_data=f"leave_review_seller_{transaction['id']}"))
            elif transaction['status'] == 'completed' and is_seller: # Seller can review buyer
                # Check if seller already reviewed buyer for this transaction
                review_exists = await connection.fetchrow(
                    "SELECT id FROM reviews WHERE transaction_id = $1 AND reviewer_id = $2 AND reviewed_user_id = $3",
                    transaction['id'], user_id, transaction['buyer_id']
                )
                if not review_exists:
                    keyboard_buttons.append(InlineKeyboardButton(text="✍️ Залишити відгук про покупця", callback_data=f"leave_review_buyer_{transaction['id']}"))

            if keyboard_buttons:
                # Adjust layout for 2 buttons per row
                rows = [keyboard_buttons[i:i + 2] for i in range(0, len(keyboard_buttons), 2)]
                final_keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
                await callback.message.answer(response_text, parse_mode=ParseMode.HTML, reply_markup=final_keyboard, disable_web_page_preview=True)
            else:
                await callback.message.answer(response_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            await asyncio.sleep(0.3) # To avoid flood limits
        
        await callback.message.answer("⬆️ Це ваші угоди.")
    await callback.answer()

@router.callback_query(F.data.startswith("contact_other_party_"))
async def contact_other_party_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split('_')
    recipient_id = int(parts[3])
    product_id = int(parts[4])
    
    await state.update_data(recipient_user_id=recipient_id, original_product_id=product_id)
    await state.set_state(DirectMessage.waiting_for_message_text)
    await callback.message.answer(f"Напишіть повідомлення користувачу ID:{recipient_id} щодо товару #{product_id}:")
    await callback.answer()

@router.callback_query(F.data.startswith("complete_transaction_"))
async def complete_transaction_callback(callback: CallbackQuery, state: FSMContext):
    transaction_id = int(callback.data.split('_')[2])
    user_id = callback.from_user.id
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        transaction = await connection.fetchrow("SELECT * FROM transactions WHERE id = $1", transaction_id)
        if not transaction or transaction['status'] != 'seller_confirmed':
            await callback.message.answer("Угода не може бути завершена. Перевірте її статус.")
            await callback.answer()
            return
        
        # Only seller or buyer can mark as complete if seller confirmed
        if not (transaction['seller_id'] == user_id or transaction['buyer_id'] == user_id):
            await callback.message.answer("Ви не є учасником цієї угоди.")
            await callback.answer()
            return

        await connection.execute(
            "UPDATE transactions SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE id = $1",
            transaction_id
        )
        product_name = (await connection.fetchrow("SELECT product_name FROM products_for_sale WHERE id = $1", transaction['product_id']))['product_name']
        await callback.message.edit_text(f"✅ Угода #{transaction_id} по товару '{product_name}' успішно завершена!")

        # Notify both parties to leave a review
        seller_id = transaction['seller_id']
        buyer_id = transaction['buyer_id']

        # Notify seller to review buyer
        await bot.send_message(
            seller_id,
            f"Угода #{transaction_id} по товару '{product_name}' завершена. Будь ласка, залиште відгук про покупця:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Залишити відгук", callback_data=f"leave_review_buyer_{transaction_id}")]
            ])
        )
        # Notify buyer to review seller (if not already done by previous prompt)
        buyer_review_exists = await connection.fetchrow(
            "SELECT id FROM reviews WHERE transaction_id = $1 AND reviewer_id = $2 AND reviewed_user_id = $3",
            transaction_id, buyer_id, seller_id
        )
        if not buyer_review_exists:
            await bot.send_message(
                buyer_id,
                f"Угода #{transaction_id} по товару '{product_name}' завершена. Будь лажалуйста, залиште відгук про продавця:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✍️ Залишити відгук", callback_data=f"leave_review_seller_{transaction_id}")]
                ])
            )
    await state.clear()
    await callback.answer()

@router.callback_query(F.data.startswith("leave_review_seller_"))
async def leave_review_seller_callback(callback: CallbackQuery, state: FSMContext):
    transaction_id = int(callback.data.split('_')[3])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        transaction = await connection.fetchrow("SELECT seller_id FROM transactions WHERE id = $1", transaction_id)
        if not transaction:
            await callback.message.answer("Угода не знайдена.")
            await callback.answer()
            return
        
        reviewed_user_id = transaction['seller_id']
        reviewer_id = callback.from_user.id

        # Check if already reviewed
        review_exists = await connection.fetchrow(
            "SELECT id FROM reviews WHERE transaction_id = $1 AND reviewer_id = $2 AND reviewed_user_id = $3",
            transaction_id, reviewer_id, reviewed_user_id
        )
        if review_exists:
            await callback.message.answer("Ви вже залишили відгук про цього продавця для цієї угоди.")
            await callback.answer()
            return

        await state.update_data(review_transaction_id=transaction_id, reviewed_user_id=reviewed_user_id, reviewer_id=reviewer_id)
        await state.set_state(ReviewState.waiting_for_seller_rating)
        await callback.message.answer("Будь ласка, оцініть продавця від 1 до 5 зірок (наприклад, 5):")
    await callback.answer()

@router.message(ReviewState.waiting_for_seller_rating)
async def process_seller_rating(message: Message, state: FSMContext):
    try:
        rating = int(message.text)
        if not (1 <= rating <= 5):
            raise ValueError
        await state.update_data(rating=rating)
        await state.set_state(ReviewState.waiting_for_seller_review)
        await message.answer("Будь ласка, напишіть короткий відгук про продавця (необов'язково):")
    except ValueError:
        await message.answer("Будь ласка, введіть число від 1 до 5.")

@router.message(ReviewState.waiting_for_seller_review)
async def process_seller_review_text(message: Message, state: FSMContext):
    review_text = message.text
    data = await state.get_data()
    transaction_id = data.get('review_transaction_id')
    reviewer_id = data.get('reviewer_id')
    reviewed_user_id = data.get('reviewed_user_id')
    rating = data.get('rating')

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO reviews (transaction_id, reviewer_id, reviewed_user_id, rating, review_text)
            VALUES ($1, $2, $3, $4, $5)
            """,
            transaction_id, reviewer_id, reviewed_user_id, rating, review_text if review_text != "без тексту" else None
        )
        await message.answer("✅ Ваш відгук про продавця успішно додано!")
        logger.info(f"Review for seller {reviewed_user_id} by buyer {reviewer_id} for transaction {transaction_id} added.")
    await state.clear()

@router.callback_query(F.data.startswith("leave_review_buyer_"))
async def leave_review_buyer_callback(callback: CallbackQuery, state: FSMContext):
    transaction_id = int(callback.data.split('_')[3])
    conn = await get_db_pool()
    async with conn.acquire() as connection:
        transaction = await connection.fetchrow("SELECT buyer_id FROM transactions WHERE id = $1", transaction_id)
        if not transaction:
            await callback.message.answer("Угода не знайдена.")
            await callback.answer()
            return
        
        reviewed_user_id = transaction['buyer_id']
        reviewer_id = callback.from_user.id

        # Check if already reviewed
        review_exists = await connection.fetchrow(
            "SELECT id FROM reviews WHERE transaction_id = $1 AND reviewer_id = $2 AND reviewed_user_id = $3",
            transaction_id, reviewer_id, reviewed_user_id
        )
        if review_exists:
            await callback.message.answer("Ви вже залишили відгук про цього покупця для цієї угоди.")
            await callback.answer()
            return

        await state.update_data(review_transaction_id=transaction_id, reviewed_user_id=reviewed_user_id, reviewer_id=reviewer_id)
        await state.set_state(ReviewState.waiting_for_buyer_rating)
        await callback.message.answer("Будь ласка, оцініть покупця від 1 до 5 зірок (наприклад, 5):")
    await callback.answer()

@router.message(ReviewState.waiting_for_buyer_rating)
async def process_buyer_rating(message: Message, state: FSMContext):
    try:
        rating = int(message.text)
        if not (1 <= rating <= 5):
            raise ValueError
        await state.update_data(rating=rating)
        await state.set_state(ReviewState.waiting_for_buyer_review)
        await message.answer("Будь ласка, напишіть короткий відгук про покупця (необов'язково):")
    except ValueError:
        await message.answer("Будь ласка, введіть число від 1 до 5.")

@router.message(ReviewState.waiting_for_buyer_review)
async def process_buyer_review_text(message: Message, state: FSMContext):
    review_text = message.text
    data = await state.get_data()
    transaction_id = data.get('review_transaction_id')
    reviewer_id = data.get('reviewer_id')
    reviewed_user_id = data.get('reviewed_user_id')
    rating = data.get('rating')

    conn = await get_db_pool()
    async with conn.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO reviews (transaction_id, reviewer_id, reviewed_user_id, rating, review_text)
            VALUES ($1, $2, $3, $4, $5)
            """,
            transaction_id, reviewer_id, reviewed_user_id, rating, review_text if review_text != "без тексту" else None
        )
        await message.answer("✅ Ваш відгук про покупця успішно додано!")
        logger.info(f"Review for buyer {reviewed_user_id} by seller {reviewer_id} for transaction {transaction_id} added.")
    await state.clear()

@router.callback_query(F.data.startswith("ai_negotiate_product_"))
async def handle_ai_negotiate_product_callback(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split('_')[3])
    conn = await get_db_pool()
    try:
        conn = await get_db_pool()
        product_record = await conn.fetchrow(
            "SELECT product_name, description, price, currency FROM products_for_sale WHERE id = $1", product_id
        )

        if not product_record:
            await callback.message.answer("❌ Товар не знайдено.")
            await callback.answer()
            return

        await state.update_data(
            negotiation_product_id=product_id,
            negotiation_product_name=product_record['product_name'],
            negotiation_product_description=product_record['description'],
            negotiation_product_price=product_record['price'],
            negotiation_product_currency=product_record['currency']
        )
        await state.set_state(ProductTransaction.waiting_for_negotiation_query)

        await callback.message.edit_text(
            "🧠 <b>AI-асистент для переговорів:</b>\n\n"
            f"Я можу допомогти вам сформулювати пропозицію або проаналізувати ціну товару '<b>{product_record['product_name']}</b>' ({product_record['price']} {product_record['currency']}). "
            "Напишіть, що ви хотіли б дізнатися або запропонувати. "
            "Наприклад: 'Яка справедлива ціна?', 'Як краще запропонувати знижку?', 'Які аргументи використати для торгу?'"
            "\n\nЩоб вийти, введіть /cancel."
            , parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logger.error(f"Помилка при ініціалізації AI-асистента для переговорів для товару {product_id} користувача {callback.from_user.id}: {e}")
        await callback.message.answer("❌ Сталася помилка при запуску AI-асистента. Спробуйте пізніше.")
    finally:
        if conn:
            await db_pool.release(conn)
    await callback.answer()

@router.message(ProductTransaction.waiting_for_negotiation_query, F.text)
async def process_buyer_negotiation_query(message: Message, state: FSMContext):
    user_id = message.from_user.id
    buyer_query = message.text.strip()

    if buyer_query.lower() == "/cancel":
        await message.answer("✅ AI-асистент для переговорів завершено.")
        await state.clear()
        return

    if not buyer_query:
        await message.answer("Будь ласка, введіть ваш запит для AI-асистента.")
        return

    data = await state.get_data()
    product_id = data.get('negotiation_product_id')
    product_name = data.get('negotiation_product_name')
    product_description = data.get('negotiation_product_description')
    product_price = data.get('negotiation_product_price')
    product_currency = data.get('negotiation_product_currency')

    if not product_id:
        await message.answer("Вибачте, контекст товару втрачено. Будь ласка, почніть AI-допомогу з переговорів знову.")
        await state.clear()
        return

    await message.answer("⏳ AI-асистент генерує рекомендації...")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    try:
        ai_negotiation_advice = await ai_assist_buyer_negotiation(
            product_name, product_description, product_price, product_currency, buyer_query
        )

        if ai_negotiation_advice:
            await message.answer(f"🧠 <b>Порада AI для переговорів щодо '{product_name}':</b>\n\n{ai_negotiation_advice}", parse_mode=ParseMode.HTML)
            logger.info(f"Користувач {user_id} отримав пораду AI для переговорів щодо товару {product_id}.")
        else:
            await message.answer("❌ На жаль, AI не зміг надати пораду для переговорів. Спробуйте інше запитання.")
            logger.warning(f"AI не зміг надати пораду для переговорів щодо товару {product_id}.")

    except Exception as e:
        logger.error(f"Помилка при обробці запиту покупця щодо переговорів для користувача {user_id}: {e}")
        await message.answer("❌ Сталася помилка при отриманні поради від AI. Спробуйте пізніше.")
    finally:
        await state.clear()

@router.message(Command("cancel"), ProductTransaction.waiting_for_negotiation_query)
async def cancel_negotiation_assistance(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ AI-асистент для переговорів завершено.")

async def main() -> None:
    await create_tables()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

