import logging
import os
from telegram import Update, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, PreCheckoutQueryHandler
import requests
import json
import asyncio
from datetime import datetime
from typing import Dict, List

# Попытка загрузить python-dotenv для поддержки .env файлов
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Если python-dotenv не установлен, используем только переменные окружения

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка токенов из переменных окружения
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CLICK_PROVIDER_TOKEN = os.getenv('CLICK_PROVIDER_TOKEN')

# Проверка наличия обязательных токенов
if not TELEGRAM_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN не найден! "
        "Создайте файл .env или установите переменную окружения TELEGRAM_BOT_TOKEN"
    )

if not CLICK_PROVIDER_TOKEN:
    logger.warning(
        "CLICK_PROVIDER_TOKEN не найден! "
        "Функция пополнения баланса будет недоступна. "
        "Установите переменную окружения CLICK_PROVIDER_TOKEN для включения платежей."
    )

COINGECKO_API = "https://api.coingecko.com/api/v3"
FEAR_GREED_API = "https://api.alternative.me/fng/"

# Хранение (в памяти)
user_alerts: Dict[int, List[Dict]] = {}  # {user_id: [{'crypto_id': str, 'target_price': float, 'direction': 'above'/'below'}]}
user_portfolio: Dict[int, Dict[str, float]] = {}  # {user_id: {'crypto_id': amount}}
user_favorites: Dict[int, List[str]] = {}  # {user_id: ['crypto_id1', 'crypto_id2']}
user_balance: Dict[int, float] = {}  # {user_id: balance_amount}

# Популярные криптовалюты
CRYPTO_IDS = {
    'bitcoin': 'btc',
    'ethereum': 'eth',
    'binancecoin': 'bnb',
    'solana': 'sol',
    'cardano': 'ada',
    'ripple': 'xrp',
    'polkadot': 'dot',
    'dogecoin': 'doge',
    'tether': 'usdt',
    'usd-coin': 'usdc'
}

def find_crypto_id(crypto_input: str) -> str:
    """Найти ID криптовалюты по названию или коду"""
    crypto_input = crypto_input.lower()
    
    # Поиск по ID или коду
    for cid, code in CRYPTO_IDS.items():
        if crypto_input == cid or crypto_input == code.lower():
            return cid
    
    
    return crypto_input

def get_crypto_price(crypto_id: str) -> dict:
    """Получить текущую цену криптовалюты"""
    try:
        url = f"{COINGECKO_API}/simple/price"
        params = {
            'ids': crypto_id,
            'vs_currencies': 'usd,eur,rub',
            'include_24hr_change': 'true'
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка при получении цены {crypto_id}: {e}")
        return None

def get_all_prices() -> dict:
    """Получить цены всех популярных криптовалют"""
    try:
        crypto_ids = ','.join(CRYPTO_IDS.keys())
        url = f"{COINGECKO_API}/simple/price"
        params = {
            'ids': crypto_ids,
            'vs_currencies': 'usd,eur,rub',
            'include_24hr_change': 'true'
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка при получении цен: {e}")
        return None

def get_top_cryptos(limit: int = 10) -> dict:
    """Получить топ криптовалют по капитализации"""
    try:
        url = f"{COINGECKO_API}/coins/markets"
        params = {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': limit,
            'page': 1,
            'sparkline': 'false'
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка при получении топ криптовалют: {e}")
        return None

def get_historical_data(crypto_id: str, days: int = 7) -> dict:
    """Получить исторические данные"""
    try:
        url = f"{COINGECKO_API}/coins/{crypto_id}/market_chart"
        params = {
            'vs_currency': 'usd',
            'days': days
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка при получении исторических данных: {e}")
        return None

def get_market_stats() -> dict:
    """Получить статистику рынка"""
    try:
        url = f"{COINGECKO_API}/global"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка при получении статистики рынка: {e}")
        return None

def get_fear_greed_index() -> dict:
    """Получить индекс страха и жадности"""
    try:
        response = requests.get(FEAR_GREED_API, timeout=10)
        response.raise_for_status()
        data = response.json()
        if 'data' in data and len(data['data']) > 0:
            return data['data'][0]
        return None
    except Exception as e:
        logger.error(f"Ошибка при получении Fear & Greed Index: {e}")
        return None

def search_crypto(query: str) -> list:
    """Поиск криптовалют"""
    try:
        url = f"{COINGECKO_API}/search"
        params = {'query': query}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if 'coins' in data:
            return data['coins'][:10]
        return []
    except Exception as e:
        logger.error(f"Ошибка при поиске: {e}")
        return []

def get_crypto_news(crypto_id: str = None) -> list:
    """Получить новости о криптовалютах"""
    try:
        if crypto_id:
            # Новости по конкретной криптовалюте
            url = f"{COINGECKO_API}/coins/{crypto_id}"
            params = {'localization': 'false', 'tickers': 'false', 'community_data': 'true'}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            return []
        else:
            return []
    except Exception as e:
        logger.error(f"Ошибка при получении новостей: {e}")
        return []

def calculate_exchange(from_crypto: str, to_crypto: str, amount: float = 1.0) -> str:
    """Рассчитать обмен между криптовалютами"""
    try:
        crypto_ids = f"{from_crypto},{to_crypto}"
        url = f"{COINGECKO_API}/simple/price"
        params = {
            'ids': crypto_ids,
            'vs_currencies': 'usd'
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if from_crypto not in data or to_crypto not in data:
            return "❌ Одна из криптовалют не найдена"
        
        from_price = data[from_crypto]['usd']
        to_price = data[to_crypto]['usd']
        
        # Конвертация
        result = amount * (from_price / to_price)
        
        from_name = from_crypto.capitalize()
        to_name = to_crypto.capitalize()
        
        return f"💱 Обмен:\n\n" \
               f"{amount} {from_name} = {result:.8f} {to_name}\n\n" \
               f"📊 Курсы:\n" \
               f"{from_name}: ${from_price:,.2f}\n" \
               f"{to_name}: ${to_price:,.2f}"
    except Exception as e:
        logger.error(f"Ошибка при расчете обмена: {e}")
        return "❌ Ошибка при расчете обмена"

async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая проверка алертов"""
    for user_id, alerts in user_alerts.items():
        for alert in alerts[:]:  # Копия списка
            crypto_id = alert['crypto_id']
            target_price = alert['target_price']
            direction = alert['direction']
            
            price_data = get_crypto_price(crypto_id)
            if price_data and crypto_id in price_data:
                current_price = price_data[crypto_id]['usd']
                triggered = False
                
                if direction == 'above' and current_price >= target_price:
                    triggered = True
                    message = f"🔔 Алерт сработал!\n\n{crypto_id.capitalize()} достиг ${current_price:,.2f}\n(Цель: ${target_price:,.2f})"
                elif direction == 'below' and current_price <= target_price:
                    triggered = True
                    message = f"🔔 Алерт сработал!\n\n{crypto_id.capitalize()} упал до ${current_price:,.2f}\n(Цель: ${target_price:,.2f})"
                
                if triggered:
                    try:
                        await context.bot.send_message(chat_id=user_id, text=message)
                        alerts.remove(alert)
                    except Exception as e:
                        logger.error(f"Ошибка при отправке алерта пользователю {user_id}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_message = (
        "👋 Привет! Я криптовалютный бот!\n\n"
        "📋 Основные команды:\n"
        "/rates - Курсы популярных криптовалют\n"
        "/top - Топ криптовалют по капитализации\n"
        "/price <криптовалюта> - Цена криптовалюты\n"
        "/exchange <от> <к> [количество] - Обмен\n"
        "/search <название> - Поиск криптовалюты\n"
        "/history <криптовалюта> [дни] - История цены\n"
        "/market - Статистика рынка\n"
        "/feargreed - Индекс страха и жадности\n\n"
        "💼 Портфель:\n"
        "/portfolio - Мой портфель\n"
        "/add <криптовалюта> <количество> - Добавить в портфель\n"
        "/remove <криптовалюта> - Удалить из портфеля\n\n"
        "⭐ Избранное:\n"
        "/favorites - Мои избранные\n"
        "/fav <криптовалюта> - Добавить в избранное\n"
        "/unfav <криптовалюта> - Удалить из избранного\n\n"
        "🔔 Алерты:\n"
        "/alert <криптовалюта> <цена> <above/below> - Создать алерт\n"
        "/alerts - Мои алерты\n"
        "/delalert <номер> - Удалить алерт\n\n"
        "💳 Баланс:\n"
        "/balance - Мой баланс\n"
        "/deposit <сумма> - Пополнить баланс\n\n"
        "/help - Полная справка"
    )
    await update.message.reply_text(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📖 Полная справка по командам:\n\n"
        "📊 КУРСЫ И ЦЕНЫ:\n"
        "• /rates - Курсы популярных криптовалют\n"
        "• /top [число] - Топ криптовалют (по умолчанию 10)\n"
        "• /price <криптовалюта> - Детальная информация о цене\n"
        "• /history <криптовалюта> [7/30/90] - История цены\n"
        "• /market - Общая статистика рынка\n"
        "• /feargreed - Индекс страха и жадности\n\n"
        "💱 ОБМЕН И КОНВЕРТАЦИЯ:\n"
        "• /exchange <от> <к> [количество] - Калькулятор обмена\n"
        "Пример: /exchange bitcoin ethereum 1\n\n"
        "🔍 ПОИСК:\n"
        "• /search <название> - Поиск криптовалюты\n\n"
        "💼 ПОРТФЕЛЬ:\n"
        "• /portfolio - Просмотр портфеля\n"
        "• /add <криптовалюта> <количество> - Добавить актив\n"
        "• /remove <криптовалюта> - Удалить актив\n\n"
        "⭐ ИЗБРАННОЕ:\n"
        "• /favorites - Быстрый доступ к избранным\n"
        "• /fav <криптовалюта> - Добавить в избранное\n"
        "• /unfav <криптовалюта> - Удалить из избранного\n\n"
        "🔔 АЛЕРТЫ:\n"
        "• /alert <криптовалюта> <цена> <above/below> - Создать алерт\n"
        "Пример: /alert bitcoin 50000 above\n"
        "• /alerts - Список активных алертов\n"
        "• /delalert <номер> - Удалить алерт\n\n"
        "💳 БАЛАНС:\n"
        "• /balance - Просмотр баланса\n"
        "• /deposit <сумма> - Пополнить баланс через CLICK\n"
        "Пример: /deposit 10000 (сумма в UZS)\n\n"
        "💡 Используйте названия (bitcoin) или коды (btc)"
    )
    await update.message.reply_text(help_text)

async def rates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /rates"""
    await update.message.reply_text("⏳ Получаю актуальные курсы...")
    
    prices = get_all_prices()
    if not prices:
        await update.message.reply_text("❌ Ошибка при получении курсов. Попробуйте позже.")
        return
    
    message = "📊 Курсы криптовалют:\n\n"
    
    for crypto_id, crypto_code in CRYPTO_IDS.items():
        if crypto_id in prices:
            data = prices[crypto_id]
            name = crypto_id.capitalize()
            usd = data.get('usd', 0)
            eur = data.get('eur', 0)
            rub = data.get('rub', 0)
            change_24h = data.get('usd_24h_change', 0)
            
            change_emoji = "📈" if change_24h >= 0 else "📉"
            
            message += f"💰 {name} ({crypto_code.upper()})\n"
            message += f"   USD: ${usd:,.2f}\n"
            message += f"   EUR: €{eur:,.2f}\n"
            message += f"   RUB: ₽{rub:,.2f}\n"
            message += f"   {change_emoji} 24ч: {change_24h:+.2f}%\n\n"
    
    await update.message.reply_text(message)

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /top"""
    limit = 10
    if context.args and context.args[0].isdigit():
        limit = min(int(context.args[0]), 50)
    
    await update.message.reply_text(f"⏳ Получаю топ {limit} криптовалют...")
    
    top_cryptos = get_top_cryptos(limit)
    if not top_cryptos:
        await update.message.reply_text("❌ Ошибка при получении данных.")
        return
    
    message = f"🏆 Топ {limit} криптовалют по капитализации:\n\n"
    
    for i, crypto in enumerate(top_cryptos, 1):
        name = crypto.get('name', 'N/A')
        symbol = crypto.get('symbol', '').upper()
        price = crypto.get('current_price', 0)
        market_cap = crypto.get('market_cap', 0)
        change_24h = crypto.get('price_change_percentage_24h', 0)
        rank = crypto.get('market_cap_rank', i)
        
        change_emoji = "📈" if change_24h >= 0 else "📉"
        
        message += f"{i}. {name} ({symbol})\n"
        message += f"   💵 ${price:,.2f}\n"
        message += f"   💰 Капитализация: ${market_cap:,.0f}\n"
        message += f"   📊 Ранг: #{rank}\n"
        message += f"   {change_emoji} 24ч: {change_24h:+.2f}%\n\n"
    
    await update.message.reply_text(message)

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /price"""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите криптовалюту\n"
            "Пример: /price bitcoin"
        )
        return
    
    crypto_input = context.args[0].lower()
    crypto_id = find_crypto_id(crypto_input)
    
    await update.message.reply_text(f"⏳ Получаю цену {crypto_id}...")
    
    price_data = get_crypto_price(crypto_id)
    if not price_data or crypto_id not in price_data:
        await update.message.reply_text(
            f"❌ Криптовалюта '{crypto_input}' не найдена\n"
            f"Попробуйте /search {crypto_input}"
        )
        return
    
    data = price_data[crypto_id]
    usd = data.get('usd', 0)
    eur = data.get('eur', 0)
    rub = data.get('rub', 0)
    change_24h = data.get('usd_24h_change', 0)
    
    change_emoji = "📈" if change_24h >= 0 else "📉"
    
    message = f"💰 {crypto_id.capitalize()}\n\n"
    message += f"💵 USD: ${usd:,.2f}\n"
    message += f"💶 EUR: €{eur:,.2f}\n"
    message += f"💷 RUB: ₽{rub:,.2f}\n"
    message += f"\n{change_emoji} Изменение за 24ч: {change_24h:+.2f}%"
    
    await update.message.reply_text(message)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /history"""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите криптовалюту\n"
            "Пример: /history bitcoin 7"
        )
        return
    
    crypto_input = context.args[0].lower()
    days = 7
    if len(context.args) > 1 and context.args[1].isdigit():
        days = int(context.args[1])
        if days not in [1, 7, 30, 90, 365]:
            days = 7
    
    crypto_id = find_crypto_id(crypto_input)
    
    await update.message.reply_text(f"⏳ Получаю историю {crypto_id} за {days} дней...")
    
    historical = get_historical_data(crypto_id, days)
    if not historical or 'prices' not in historical:
        await update.message.reply_text("❌ Не удалось получить исторические данные.")
        return
    
    prices = historical['prices']
    if not prices:
        await update.message.reply_text("❌ Нет данных.")
        return
    
    current_price = prices[-1][1]
    old_price = prices[0][1]
    change = ((current_price - old_price) / old_price) * 100
    
    change_emoji = "📈" if change >= 0 else "📉"
    high = max(p[1] for p in prices)
    low = min(p[1] for p in prices)
    
    message = f"📊 История {crypto_id.capitalize()} ({days} дней)\n\n"
    message += f"💵 Текущая цена: ${current_price:,.2f}\n"
    message += f"📅 Цена {days} дней назад: ${old_price:,.2f}\n"
    message += f"⬆️ Максимум: ${high:,.2f}\n"
    message += f"⬇️ Минимум: ${low:,.2f}\n"
    message += f"\n{change_emoji} Изменение: {change:+.2f}%"
    
    await update.message.reply_text(message)

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /market"""
    await update.message.reply_text("⏳ Получаю статистику рынка...")
    
    stats = get_market_stats()
    if not stats or 'data' not in stats:
        await update.message.reply_text("❌ Ошибка при получении статистики.")
        return
    
    data = stats['data']
    total_market_cap = data.get('total_market_cap', {}).get('usd', 0)
    total_volume = data.get('total_volume', {}).get('usd', 0)
    btc_dominance = data.get('market_cap_percentage', {}).get('btc', 0)
    eth_dominance = data.get('market_cap_percentage', {}).get('eth', 0)
    active_cryptos = data.get('active_cryptocurrencies', 0)
    markets = data.get('markets', 0)
    
    message = "🌍 Статистика криптовалютного рынка\n\n"
    message += f"💰 Общая капитализация: ${total_market_cap:,.0f}\n"
    message += f"📊 Объем торгов (24ч): ${total_volume:,.0f}\n"
    message += f"🪙 Активных криптовалют: {active_cryptos:,}\n"
    message += f"🏪 Бирж: {markets:,}\n\n"
    message += f"📈 Доминирование:\n"
    message += f"   Bitcoin: {btc_dominance:.2f}%\n"
    message += f"   Ethereum: {eth_dominance:.2f}%"
    
    await update.message.reply_text(message)

async def feargreed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /feargreed"""
    await update.message.reply_text("⏳ Получаю индекс страха и жадности...")
    
    fng = get_fear_greed_index()
    if not fng:
        await update.message.reply_text("❌ Ошибка при получении индекса.")
        return
    
    value = int(fng.get('value', 0))
    classification = fng.get('value_classification', 'N/A')
    
    # Эмодзи в зависимости от значения
    if value <= 25:
        emoji = "😱"
        status = "Крайний страх"
    elif value <= 45:
        emoji = "😨"
        status = "Страх"
    elif value <= 55:
        emoji = "😐"
        status = "Нейтрально"
    elif value <= 75:
        emoji = "😊"
        status = "Жадность"
    else:
        emoji = "🤩"
        status = "Крайняя жадность"
    
    message = f"{emoji} Индекс страха и жадности\n\n"
    message += f"📊 Значение: {value}/100\n"
    message += f"📈 Классификация: {classification}\n"
    message += f"💭 Статус: {status}\n\n"
    message += f"📅 Дата: {fng.get('time_until_update', 'N/A')}"
    
    await update.message.reply_text(message)

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /search"""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите название для поиска\n"
            "Пример: /search bitcoin"
        )
        return
    
    query = ' '.join(context.args)
    await update.message.reply_text(f"🔍 Ищу '{query}'...")
    
    results = search_crypto(query)
    if not results:
        await update.message.reply_text("❌ Ничего не найдено.")
        return
    
    message = f"🔍 Результаты поиска '{query}':\n\n"
    for i, coin in enumerate(results[:10], 1):
        name = coin.get('name', 'N/A')
        symbol = coin.get('symbol', '').upper()
        coin_id = coin.get('id', '')
        rank = coin.get('market_cap_rank', 'N/A')
        
        message += f"{i}. {name} ({symbol})\n"
        message += f"   ID: {coin_id}\n"
        if rank:
            message += f"   Ранг: #{rank}\n"
        message += "\n"
    
    message += "💡 Используйте ID для команд /price и /history"
    await update.message.reply_text(message)

async def exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /exchange"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Укажите криптовалюты для обмена\n"
            "Пример: /exchange bitcoin ethereum 1"
        )
        return
    
    from_crypto_input = context.args[0].lower()
    to_crypto_input = context.args[1].lower()
    amount = float(context.args[2]) if len(context.args) > 2 and context.args[2].replace('.', '').replace('-', '').isdigit() else 1.0
    
    from_crypto_id = find_crypto_id(from_crypto_input)
    to_crypto_id = find_crypto_id(to_crypto_input)
    
    await update.message.reply_text(f"⏳ Рассчитываю обмен...")
    
    result = calculate_exchange(from_crypto_id, to_crypto_id, amount)
    await update.message.reply_text(result)

# ПОРТФЕЛЬ
async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /portfolio"""
    user_id = update.effective_user.id
    
    if user_id not in user_portfolio or not user_portfolio[user_id]:
        await update.message.reply_text("💼 Ваш портфель пуст.\nИспользуйте /add для добавления активов.")
        return
    
    portfolio_data = user_portfolio[user_id]
    total_value = 0
    message = "💼 Ваш портфель:\n\n"
    
    # Получаем цены всех криптовалют в портфеле
    crypto_ids = ','.join(portfolio_data.keys())
    prices = get_crypto_price(crypto_ids)
    
    if prices:
        for crypto_id, amount in portfolio_data.items():
            if crypto_id in prices:
                price = prices[crypto_id]['usd']
                value = amount * price
                total_value += value
                message += f"💰 {crypto_id.capitalize()}\n"
                message += f"   Количество: {amount:.8f}\n"
                message += f"   Цена: ${price:,.2f}\n"
                message += f"   Стоимость: ${value:,.2f}\n\n"
    
    message += f"💵 Общая стоимость: ${total_value:,.2f}"
    await update.message.reply_text(message)

async def add_to_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /add"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Укажите криптовалюту и количество\n"
            "Пример: /add bitcoin 0.5"
        )
        return
    
    user_id = update.effective_user.id
    crypto_input = context.args[0].lower()
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Неверное количество.")
        return
    
    crypto_id = find_crypto_id(crypto_input)
    
    # Проверяем существование криптовалюты
    price_data = get_crypto_price(crypto_id)
    if not price_data or crypto_id not in price_data:
        await update.message.reply_text(f"❌ Криптовалюта '{crypto_input}' не найдена.")
        return
    
    if user_id not in user_portfolio:
        user_portfolio[user_id] = {}
    
    if crypto_id in user_portfolio[user_id]:
        user_portfolio[user_id][crypto_id] += amount
    else:
        user_portfolio[user_id][crypto_id] = amount
    
    await update.message.reply_text(
        f"✅ Добавлено {amount:.8f} {crypto_id.capitalize()} в портфель.\n"
        f"Используйте /portfolio для просмотра."
    )

async def remove_from_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /remove"""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите криптовалюту\n"
            "Пример: /remove bitcoin"
        )
        return
    
    user_id = update.effective_user.id
    crypto_input = context.args[0].lower()
    crypto_id = find_crypto_id(crypto_input)
    
    if user_id not in user_portfolio or crypto_id not in user_portfolio[user_id]:
        await update.message.reply_text(f"❌ {crypto_id.capitalize()} не найден в портфеле.")
        return
    
    del user_portfolio[user_id][crypto_id]
    if not user_portfolio[user_id]:
        del user_portfolio[user_id]
    
    await update.message.reply_text(f"✅ {crypto_id.capitalize()} удален из портфеля.")

# ИЗБРАННОЕ
async def favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /favorites"""
    user_id = update.effective_user.id
    
    if user_id not in user_favorites or not user_favorites[user_id]:
        await update.message.reply_text("⭐ У вас нет избранных криптовалют.\nИспользуйте /fav для добавления.")
        return
    
    favorites_list = user_favorites[user_id]
    message = "⭐ Ваши избранные криптовалюты:\n\n"
    
    # Получаем цены
    crypto_ids = ','.join(favorites_list)
    prices = get_crypto_price(crypto_ids)
    
    if prices:
        for crypto_id in favorites_list:
            if crypto_id in prices:
                data = prices[crypto_id]
                usd = data.get('usd', 0)
                change_24h = data.get('usd_24h_change', 0)
                change_emoji = "📈" if change_24h >= 0 else "📉"
                
                message += f"💰 {crypto_id.capitalize()}\n"
                message += f"   ${usd:,.2f} {change_emoji} {change_24h:+.2f}%\n\n"
    
    await update.message.reply_text(message)

async def add_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /fav"""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите криптовалюту\n"
            "Пример: /fav bitcoin"
        )
        return
    
    user_id = update.effective_user.id
    crypto_input = context.args[0].lower()
    crypto_id = find_crypto_id(crypto_input)
    
    # Проверяем существование
    price_data = get_crypto_price(crypto_id)
    if not price_data or crypto_id not in price_data:
        await update.message.reply_text(f"❌ Криптовалюта '{crypto_input}' не найдена.")
        return
    
    if user_id not in user_favorites:
        user_favorites[user_id] = []
    
    if crypto_id not in user_favorites[user_id]:
        user_favorites[user_id].append(crypto_id)
        await update.message.reply_text(f"✅ {crypto_id.capitalize()} добавлен в избранное.")
    else:
        await update.message.reply_text(f"ℹ️ {crypto_id.capitalize()} уже в избранном.")

async def remove_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /unfav"""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите криптовалюту\n"
            "Пример: /unfav bitcoin"
        )
        return
    
    user_id = update.effective_user.id
    crypto_input = context.args[0].lower()
    crypto_id = find_crypto_id(crypto_input)
    
    if user_id not in user_favorites or crypto_id not in user_favorites[user_id]:
        await update.message.reply_text(f"❌ {crypto_id.capitalize()} не найден в избранном.")
        return
    
    user_favorites[user_id].remove(crypto_id)
    if not user_favorites[user_id]:
        del user_favorites[user_id]
    
    await update.message.reply_text(f"✅ {crypto_id.capitalize()} удален из избранного.")

# АЛЕРТЫ
async def create_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /alert"""
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ Формат: /alert <криптовалюта> <цена> <above/below>\n"
            "Пример: /alert bitcoin 50000 above"
        )
        return
    
    user_id = update.effective_user.id
    crypto_input = context.args[0].lower()
    try:
        target_price = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Неверная цена.")
        return
    
    direction = context.args[2].lower()
    if direction not in ['above', 'below']:
        await update.message.reply_text("❌ Направление должно быть 'above' или 'below'.")
        return
    
    crypto_id = find_crypto_id(crypto_input)
    
    # Проверяем существование
    price_data = get_crypto_price(crypto_id)
    if not price_data or crypto_id not in price_data:
        await update.message.reply_text(f"❌ Криптовалюта '{crypto_input}' не найдена.")
        return
    
    if user_id not in user_alerts:
        user_alerts[user_id] = []
    
    current_price = price_data[crypto_id]['usd']
    user_alerts[user_id].append({
        'crypto_id': crypto_id,
        'target_price': target_price,
        'direction': direction
    })
    
    direction_text = "выше" if direction == 'above' else "ниже"
    await update.message.reply_text(
        f"✅ Алерт создан!\n\n"
        f"Криптовалюта: {crypto_id.capitalize()}\n"
        f"Текущая цена: ${current_price:,.2f}\n"
        f"Уведомление при цене {direction_text} ${target_price:,.2f}"
    )

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /alerts"""
    user_id = update.effective_user.id
    
    if user_id not in user_alerts or not user_alerts[user_id]:
        await update.message.reply_text("🔔 У вас нет активных алертов.\nИспользуйте /alert для создания.")
        return
    
    alerts = user_alerts[user_id]
    message = f"🔔 Ваши алерты ({len(alerts)}):\n\n"
    
    # Получаем текущие цены
    crypto_ids = list(set(alert['crypto_id'] for alert in alerts))
    prices = get_crypto_price(','.join(crypto_ids))
    
    for i, alert in enumerate(alerts, 1):
        crypto_id = alert['crypto_id']
        target_price = alert['target_price']
        direction = alert['direction']
        direction_text = "выше" if direction == 'above' else "ниже"
        
        current_price = "N/A"
        if prices and crypto_id in prices:
            current_price = f"${prices[crypto_id]['usd']:,.2f}"
        
        message += f"{i}. {crypto_id.capitalize()}\n"
        message += f"   Текущая: {current_price}\n"
        message += f"   Алерт: {direction_text} ${target_price:,.2f}\n\n"
    
    message += "Используйте /delalert <номер> для удаления."
    await update.message.reply_text(message)

async def delete_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /delalert"""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "❌ Укажите номер алерта\n"
            "Пример: /delalert 1"
        )
        return
    
    user_id = update.effective_user.id
    alert_num = int(context.args[0]) - 1
    
    if user_id not in user_alerts or alert_num < 0 or alert_num >= len(user_alerts[user_id]):
        await update.message.reply_text("❌ Алерт не найден.")
        return
    
    removed_alert = user_alerts[user_id].pop(alert_num)
    if not user_alerts[user_id]:
        del user_alerts[user_id]
    
    await update.message.reply_text(
        f"✅ Алерт для {removed_alert['crypto_id'].capitalize()} удален."
    )

# БАЛАНС И ОПЛАТА
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /balance"""
    user_id = update.effective_user.id
    balance_amount = user_balance.get(user_id, 0.0)
    
    message = f"💳 Ваш баланс\n\n"
    message += f"💰 Сумма: {balance_amount:,.2f} UZS\n\n"
    message += f"💡 Используйте /deposit для пополнения баланса"
    
    await update.message.reply_text(message)

async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /deposit - создание инвойса для пополнения"""
    if not CLICK_PROVIDER_TOKEN:
        await update.message.reply_text(
            "❌ Платежи недоступны. CLICK_PROVIDER_TOKEN не настроен."
        )
        return
    
    if not context.args or not context.args[0].replace('.', '').isdigit():
        await update.message.reply_text(
            "❌ Укажите сумму для пополнения\n"
            "Пример: /deposit 10000"
        )
        return
    
    try:
        amount = float(context.args[0])
        if amount <= 0:
            await update.message.reply_text("❌ Сумма должна быть больше 0")
            return
        
        # Минимальная сумма для CLICK обычно 1000 UZS
        if amount < 1000:
            await update.message.reply_text("❌ Минимальная сумма пополнения: 1000 UZS")
            return
        
        # Максимальная сумма (можно установить лимит)
        if amount > 10000000:
            await update.message.reply_text("❌ Максимальная сумма пополнения: 10,000,000 UZS")
            return
        
        user_id = update.effective_user.id
        invoice_payload = f"deposit_{user_id}_{int(datetime.now().timestamp())}"
        
        amount_in_tiyin = int(amount * 100)  # Конвертируем в тийины
        
        # Создаем инвойс
        prices = [LabeledPrice(label="Пополнение баланса", amount=amount_in_tiyin)]
        
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title="Пополнение баланса",
            description=f"Пополнение баланса на сумму {amount:,.2f} UZS",
            payload=invoice_payload,
            provider_token=CLICK_PROVIDER_TOKEN,
            currency="UZS",
            prices=prices,
            start_parameter=invoice_payload
        )
        
        logger.info(f"Инвойс создан для пользователя {user_id}, сумма: {amount} UZS")
        
    except ValueError:
        await update.message.reply_text("❌ Неверный формат суммы")
    except Exception as e:
        logger.error(f"Ошибка при создании инвойса: {e}")
        await update.message.reply_text("❌ Ошибка при создании счета. Попробуйте позже.")

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик pre_checkout_query - проверка перед оплатой"""
    query = update.pre_checkout_query
    user_id = query.from_user.id
    
    if not query.invoice_payload.startswith("deposit_"):
        await query.answer(ok=False, error_message="Неверный тип платежа")
        return
    
    try:
        await query.answer(ok=True)
        logger.info(f"Pre-checkout подтвержден для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при подтверждении pre-checkout: {e}")
        await query.answer(ok=False, error_message="Ошибка при обработке запроса")

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик успешной оплаты"""
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    
    if not payment:
        return
    
    #сумму из платежа
    amount = payment.total_amount / 100.0 
    
    if user_id not in user_balance:
        user_balance[user_id] = 0.0
    
    user_balance[user_id] += amount
    
    message = (
        f"✅ Платеж успешно обработан!\n\n"
        f"💰 Пополнено: {amount:,.2f} UZS\n"
        f"💳 Новый баланс: {user_balance[user_id]:,.2f} UZS\n\n"
        f"Спасибо за пополнение!"
    )
    
    await update.message.reply_text(message)
    logger.info(f"Баланс пользователя {user_id} пополнен на {amount} UZS. Новый баланс: {user_balance[user_id]} UZS")

def main():
    """Запуск бота"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("rates", rates))
    application.add_handler(CommandHandler("top", top))
    application.add_handler(CommandHandler("price", price))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("market", market))
    application.add_handler(CommandHandler("feargreed", feargreed))
    application.add_handler(CommandHandler("search", search))
    application.add_handler(CommandHandler("exchange", exchange))
    
    # Портфель
    application.add_handler(CommandHandler("portfolio", portfolio))
    application.add_handler(CommandHandler("add", add_to_portfolio))
    application.add_handler(CommandHandler("remove", remove_from_portfolio))
    
    # Избранное
    application.add_handler(CommandHandler("favorites", favorites))
    application.add_handler(CommandHandler("fav", add_favorite))
    application.add_handler(CommandHandler("unfav", remove_favorite))
    
    # Алерты
    application.add_handler(CommandHandler("alert", create_alert))
    application.add_handler(CommandHandler("alerts", list_alerts))
    application.add_handler(CommandHandler("delalert", delete_alert))
    
    # Баланс и оплата
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("deposit", deposit))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    
    # Периодическая проверка алертов (каждые 60 секунд)
    job_queue = application.job_queue
    job_queue.run_repeating(check_alerts, interval=60.0, first=10.0)
    
    # Запускаем бота
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
