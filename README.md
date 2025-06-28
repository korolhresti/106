# Telegram AI News Bot

Цей проєкт є Telegram-ботом для агрегації новин з використанням AI для генерації резюме. Бот створений на базі `aiogram 3` та `FastAPI` і призначений для розгортання на платформі Render.

## Локальний запуск (для тестування)

1.  **Клонуйте репозиторій:**
    ```bash
    git clone <your-repo-url>
    cd <repo-folder>
    ```

2.  **Створіть та активуйте віртуальне середовище:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # для Windows: venv\Scripts\activate
    ```

3.  **Встановіть залежності:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Налаштуйте змінні середовища:**
    Створіть файл `.env` у корені проєкту та додайте до нього наступні змінні:
    ```env
    TELEGRAM_BOT_TOKEN="your_actual_telegram_bot_token"
    GEMINI_API_KEY="your_actual_gemini_api_key"
    DATABASE_URL="postgresql://user:password@localhost:5432/dbname" # Посилання на вашу локальну БД
    BOT_USERNAME="YourBotUsername" # Без @
    # WEBHOOK_URL не потрібен для локального запуску
    ```

5.  **Запустіть локальну базу даних PostgreSQL** (наприклад, через Docker).

6.  **Застосуйте схему до бази даних:**
    ```bash
    psql $DATABASE_URL -f schema.sql
    ```

7.  **Запустіть бота:**
    ```bash
    uvicorn bot:app --reload --port 8000
    ```

---

## Розгортання на Render.com (Production)

Проєкт повністю налаштований для автоматичного розгортання на Render за допомогою файлу `render.yaml`.

### Кроки для розгортання:

1.  **Створіть акаунт на [Render.com](https://render.com/).**

2.  **Завантажте ваш проєкт на GitHub/GitLab.** Переконайтеся, що файли `bot.py`, `requirements.txt`, `start.sh`, `schema.sql` та `render.yaml` знаходяться у корені репозиторію.

3.  **Створіть новий "Blueprint Instance":**
    * У вашій панелі керування Render натисніть **New +** -> **Blueprint**.
    * Підключіть ваш репозиторій GitHub/GitLab.
    * Render автоматично виявить та проаналізує ваш файл `render.yaml`. Вам буде запропоновано створити два сервіси: `telegram-ai-bot` (веб-сервіс) та `news-bot-db` (база даних).
    * Натисніть **Approve**.

4.  **Додайте секретні змінні середовища:**
    * Після створення сервісів перейдіть до налаштувань сервісу `telegram-ai-bot`.
    * Відкрийте вкладку **Environment**.
    * У розділі **Secret Files / Environment Groups** додайте ваші секретні ключі, які були позначені як `sync: false` у `render.yaml`:
        * `TELEGRAM_BOT_TOKEN`
        * `GEMINI_API_KEY`
        * `BOT_USERNAME`
    * Натисніть **Save Changes**.

5.  **Завершення розгортання:**
    * Після збереження змінних середовища, Render автоматично почне процес розгортання (Build & Deploy).
    * Ви можете спостерігати за процесом у вкладці **Events** або **Logs** вашого сервісу.
    * Скрипт `start.sh` автоматично застосує схему `schema.sql` до нової бази даних.
    * Після успішного запуску, скрипт `bot.py` автоматично встановить вебхук на URL вашого сервісу.

6.  **Готово!** Ваш бот працює і доступний в Telegram. Будь-які зміни, які ви будете завантажувати у ваш репозиторій, автоматично запускатимуть новий процес розгортання.

