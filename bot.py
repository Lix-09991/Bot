import re, requests, asyncio, time, sqlite3, atexit, os, random, logging
import uuid
import hashlib
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import io
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes,
)
import telegram.error
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------- ЛОГИРОВАНИЕ ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# ---------- НАСТРОЙКИ ----------
TOKEN = "8984458345:AAHdKr28tBACCBl_QnjUWr94NKesVAgr4R4"
ADMIN_ID = 7949083738
MIN_RUB = 3000
MIN_RUB_247 = 1000
MAX_RUB = 10000
MIN_COMMISSION = 300
PAYMENT_TIMEOUT = 15 * 60
CARD_TIMEOUT = 20 * 60
MAX_PAYMENTS_PER_CARD = 2
BOT_USERNAME = "CryTRX_bot"
DB_FILE = "bot_data.db"
MAX_CANCELLED = 3
BAN_HOURS = 3
BOT_DISABLED_FILE = "bot_disabled2.flag"
# ---------- КОШЕЛЁК ----------
# Ставки начисления в кошелёк
WALLET_RATE_BASE  = 0.002    # 0.2% — базовая ставка
WALLET_RATE_SLIM  = 0.005    # 0.5% — Slim bonus (каждый 5-й обмен, не 10-й)
WALLET_RATE_EXTRA = 0.010    # 1.0% — Extra bonus (каждый 10-й обмен)
WALLET_MIN_RUB = 1000        # ниже этого порога начислений нет

# Минимальные суммы конвертации для обхода ограничений API
MIN_SWAP_LTC = 0.2
MIN_SWAP_BTC = 0.00013

WALLET_API_KEY = "1TkD4LmV.buTf7D7UOv8VrqovuyLJdLTtEEGTUXj2"
WALLET_BASE_URL = "https://b2bwallet.io"
WALLET_POLL_INTERVAL = 15
WALLET_POLL_TIMEOUT = 3600
# Адрес кошелька для пополнения USDT (TRC20)
USDT_WALLET_ADDRESS = "TEPNnjpaUqp3VCzTYB3Gy6gxHih11S715A"

# ---------- BESTMERCHANT ----------
BESTMERCHANT_BASE_URL = "https://bestmerchant.site/api"
BESTMERCHANT_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3ODJjNjE5Ni1iNWQ4LTQ0NDAtYTE0Yi01MmQ4ZGRlZGFiMzAiLCJlbWFpbCI6IkNyeXB0b1RSWDIwMjUiLCJqdGkiOiI3ZjI2OTJjNS0yMTJhLTRhNGMtYmJhOS1jYWEyNzc2NGVlM2EiLCJ2ZXJzaW9uIjoiMSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiJDcnlwdG9UUlgyMDI1IiwiaHR0cDovL3NjaGVtYXMubWljcm9zb2Z0LmNvbS93cy8yMDA4LzA2L2lkZW50aXR5L2NsYWltcy9yb2xlIjoiVXNlciIsImV4cCI6MjEwNDQ2NDEwNCwiaXNzIjoiQ29kZU1hemVBUEkiLCJhdWQiOiJodHRwczovL2Jlc3RtZXJjaGFudC5zaXRlLyJ9.o290cBSuz4jqzQslR_vMdaAueIZx1PV1N89SSAJHDQk"

# ---------- NICEPAY (Трансгран) ----------
NICEPAY_BASE_URL = "https://nicepay.io/public/api"
NICEPAY_MERCHANT_ID = "69e8c2980826c43f8384cdfb"
NICEPAY_SECRET = "zQ9wt-IY74C-AXil8-SBJVJ-84tLE"

# Доступные методы для Трансграна
NICEPAY_METHODS = {
    "sbp_rub":      "🏦 Оплата с любого банка (СБП)",
    "sberbank_rub": "🟢 Оплата со Сбербанка",
    "tinkoff_rub":  "🟡 Оплата с Т-Банка",
    "yoomoney_rub": "🟣 Оплата с ЮMoney",
}

# Минимальные суммы для конкретных методов
MIN_RUB_RU_BANKS = 10000   # Оплата на Ру банки 24/7 — от 10 000
MIN_RUB_TRANSGRAN = 1000   # Трансгран — от 1 000

# ---------- ВРЕМЕННОЕ ОТКЛЮЧЕНИЕ 24/7 ----------
BESTMERCHANT_ENABLED = True   # поставьте True, чтобы снова включить

# ---------- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ----------
orders = {}
next_order_id = 1
payment_methods = {}
next_payment_id = 1
payment_queue_index = 0
referrals = {}
referral_parent = {}
user_bonuses = {}
daily_start_time = None
payment_lock = asyncio.Lock()
order_id_lock = asyncio.Lock()
order_locks = {}
payment_methods_lock = threading.Lock()
_order_locks_mutex = threading.Lock()
google_sheet = None
app = None
main_loop = None

# ID пользователя с индивидуальным процентом (закомментирован)
# SPECIAL_USER_ID = 8789219146

# ---------- ЗАМОРОЗКА СРЕДСТВ ----------
def get_order_lock(order_id):
    with _order_locks_mutex:
        if order_id not in order_locks:
            order_locks[order_id] = asyncio.Lock()
        return order_locks[order_id]

def get_frozen_amount(crypto_type):
    total = 0.0
    for o in list(orders.values()):
        if o['crypto_type'] == crypto_type and o['status'] in ('waiting_proof', 'payment_confirmed'):
            total += o['crypto_amount'] * o.get('market_rate', 0)
    return total

def get_reserved_rub():
    return sum(o['rub_amount'] for o in list(orders.values())
               if o['status'] in ('waiting_proof', 'payment_confirmed'))

def get_reserved_usdt_rub():
    """Сумма в рублях, которая уже зарезервирована свопами подтверждённых заявок."""
    total = 0.0
    for o in list(orders.values()):
        if o['status'] == 'payment_confirmed' and o.get('swap_id'):
            total += o.get('swap_amount_rub', 0)
    return total

async def get_network_fee_rub(crypto_type):
    try:
        fee_coin = await get_network_commission(crypto_type)
        if fee_coin is None:
            return 0.0
        rate = await get_wallet_rate(crypto_type)
        if rate is None:
            rate = await get_ltc_rub() if crypto_type == 'LTC' else await get_btc_rub()
        return fee_coin * rate
    except:
        return 0.0

# ---------- КЛАВИАТУРЫ ----------
main_keyboard = ReplyKeyboardMarkup([
    ["LTC", "BTC"],
    ["Кабинет", "Контакты"],
    ["Активный обмен", "❓ FAQ"],
    ["🏠 Главное меню"]
], resize_keyboard=True)
cancel_keyboard = ReplyKeyboardMarkup([["🏠 Главное меню"]], resize_keyboard=True)
cabinet_keyboard = ReplyKeyboardMarkup([
    ["🏠 Главное меню"]
], resize_keyboard=True)

def get_cabinet_keyboard(user_id):
    """Клавиатура кабинета — единая для всех пользователей."""
    return cabinet_keyboard

(LTC_AMOUNT, LTC_WALLET, LTC_PAYMENT_METHOD, LTC_CONFIRM, LTC_PROOF, LTC_BONUS,
 BTC_AMOUNT, BTC_WALLET, BTC_PAYMENT_METHOD, BTC_CONFIRM, BTC_PROOF, BTC_BONUS,
 ATTACH_PROOF, PROMO_CODE,
 LTC_SELECT_MAIN_METHOD, BTC_SELECT_MAIN_METHOD) = range(16)
LEAVE_REVIEW_TEXT = 16
TRANSGRAN_METHOD = 17

def is_bot_disabled():
    return os.path.exists(BOT_DISABLED_FILE)
def is_ru_working_hours():
    """Возвращает True, если сейчас от 02:00 до 16:00 по московскому времени."""
    now_utc = datetime.utcnow()
    moscow_time = now_utc + timedelta(hours=3)
    return 2 <= moscow_time.hour < 16

# ---------- GOOGLE SHEETS ----------
def init_google_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('/opt/cryptotrx/credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open("CryptoTRX").sheet1

def add_successful_order_to_sheet(order_id):
    order = orders.get(order_id)
    if not order or order['status'] != 'done':
        return
    try:
        if google_sheet is None:
            logging.error("Google Sheet не инициализирован")
            return

        rub_received = order['rub_amount']
        crypto_amount = order['crypto_amount']
        market_rate = order.get('market_rate', 0)
        network_fee_rub = order.get('network_fee', 0.0)
        swap_amount_rub = order.get('swap_amount_rub', 0.0)
        reserve_rub = order.get('reserve_rub', 0)

        # Стоимость отправленной крипты по курсу
        crypto_value_rub = crypto_amount * market_rate

        # Общие затраты = стоимость свопа минус резерв (резерв остаётся на балансе)
        total_cost = swap_amount_rub - reserve_rub

        # Чистая комиссия конвертации (без резерва)
        conversion_fee = max(0, swap_amount_rub - crypto_value_rub - network_fee_rub - reserve_rub)

        profit = rub_received - total_cost

        if 3000 <= rub_received <= 6248:
            worker_percent = 0.12
        elif rub_received <= 35998:
            worker_percent = 0.10
        else:
            worker_percent = 0.08

        worker_share = rub_received * worker_percent
        my_share = calculate_my_share(order)

        google_sheet.append_row([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            order_id,
            order['crypto_type'],
            order['username'],
            rub_received,
            crypto_amount,
            total_cost,
            network_fee_rub,
            conversion_fee,
            order['wallet'],
            order.get('txid', ''),
            profit,
            worker_share,
            my_share
        ])
        logging.info(f"Заявка #{order_id} записана в Google Таблицу с детализацией затрат")
    except Exception as e:
        logging.error(f"Ошибка записи в Google Таблицу: {e}")

# ---------- КОШЕЛЁК (универсальный для всех) ----------
def get_user_order_number(user_id):
    """Счётчик завершённых личных обменов пользователя (для бонусов).
    Вызывается, когда заявка УЖЕ в статусе 'done' в БД — то есть включая её."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute(
            "SELECT COUNT(*) FROM orders WHERE user_id = ? AND status = 'done'",
            (user_id,)
        )
        return int(c.fetchone()[0] or 0)
    except Exception as e:
        logging.error(f"get_user_order_number error для {user_id}: {e}")
        return 0
    finally:
        conn.close()


def get_wallet_rate_for_order(order_number):
    """Ставка начисления в кошелёк по номеру обмена.
    Кратный 10 → Extra (1%), кратный 5 (но не 10) → Slim (0.5%),
    остальные → базовая (0.2%)."""
    if order_number <= 0:
        return WALLET_RATE_BASE
    if order_number % 10 == 0:
        return WALLET_RATE_EXTRA
    if order_number % 5 == 0:
        return WALLET_RATE_SLIM
    return WALLET_RATE_BASE


def get_wallet_bonus_type(order_number):
    """Человекочитаемый тип бонуса по номеру обмена."""
    if order_number <= 0:
        return 'base'
    if order_number % 10 == 0:
        return 'extra'
    if order_number % 5 == 0:
        return 'slim'
    return 'base'


def calculate_wallet_bonus(rub_amount, order_number):
    """Начисление в кошелёк за один обмен."""
    if rub_amount < WALLET_MIN_RUB:
        return 0.0
    rate = get_wallet_rate_for_order(order_number)
    return round(rub_amount * rate, 2)


def get_next_bonus_info(orders_count):
    """Инфо о следующем обмене и расстоянии до бонусов.
    Возвращает (next_number, next_rate_pct_str, to_slim, to_extra)."""
    next_number = orders_count + 1
    btype = get_wallet_bonus_type(next_number)

    if btype == 'extra':
        next_rate_str = '1% (Extra 🎉)'
    elif btype == 'slim':
        next_rate_str = '0.5% (Slim ✨)'
    else:
        next_rate_str = '0.2%'

    # Расстояние до следующих кратностей
    next_slim = ((orders_count // 5) + 1) * 5
    next_extra = ((orders_count // 10) + 1) * 10
    to_slim = next_slim - orders_count
    to_extra = next_extra - orders_count

    return (next_number, next_rate_str, to_slim, to_extra)


def get_wallet_available_balance(user_id):
    """Доступный баланс кошелька: начислено − списано (скидки + вывода)."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("SELECT COALESCE(SUM(earning), 0) FROM wallet_earnings WHERE user_id = ?",
                  (user_id,))
        earned = float(c.fetchone()[0] or 0)
        c.execute("SELECT COALESCE(SUM(amount), 0) FROM wallet_withdrawals WHERE user_id = ?",
                  (user_id,))
        spent = float(c.fetchone()[0] or 0)
    except Exception as e:
        logging.error(f"get_wallet_available_balance error для {user_id}: {e}")
        earned = spent = 0.0
    conn.close()
    return round(max(0.0, earned - spent), 2)


def get_wallet_total_earned(user_id):
    """Всего начислено в кошелёк за всё время."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("SELECT COALESCE(SUM(earning), 0) FROM wallet_earnings WHERE user_id = ?",
                  (user_id,))
        total = float(c.fetchone()[0] or 0)
    except Exception as e:
        logging.error(f"get_wallet_total_earned error для {user_id}: {e}")
        total = 0.0
    conn.close()
    return round(total, 2)

def get_total_discount_available(user_id):
    """Купоны + кошелёк — общий доступный баланс для скидки."""
    coupons = round(user_bonuses.get(user_id, 0), 2)
    wallet = get_wallet_available_balance(user_id)
    return round(coupons + wallet, 2)


def _insert_wallet_earning(user_id, order_id, source_user_id, kind, rub_amount, earning):
    """Идемпотентная вставка начисления в кошелёк."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute('''INSERT OR IGNORE INTO wallet_earnings
                     (user_id, order_id, source_user_id, kind, rub_amount, earning, created_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, order_id, source_user_id, kind, rub_amount, earning,
                   datetime.now().isoformat()))
        inserted = c.rowcount
        conn.commit()
        if inserted:
            logging.info(
                f"Кошелёк #{user_id}: +{earning:.2f} ₽ "
                f"(kind={kind}, заявка #{order_id}, чек {rub_amount:.2f} ₽ от #{source_user_id})"
            )
    except Exception as e:
        logging.error(f"_insert_wallet_earning error: {e}")
    finally:
        conn.close()


def award_wallet_commissions(order_id):
    """Начисляет в кошелёк за личный обмен и обмен рефералов. Идемпотентно."""
    order = orders.get(order_id)
    if not order or order['status'] != 'done':
        return

    user_id = order['user_id']
    rub_amount = order.get('rub_amount', 0) or 0
    if rub_amount <= 0:
        return

    # Номер обмена пользователя (включая текущий — он уже в статусе 'done')
    personal_order_number = get_user_order_number(user_id)
    earning = calculate_wallet_bonus(rub_amount, personal_order_number)
    if earning <= 0:
        return

    bonus_kind = get_wallet_bonus_type(personal_order_number)
    logging.info(
        f"Кошелёк #{user_id}: обмен #{personal_order_number} "
        f"({bonus_kind}), начисление {earning:.2f} ₽ за заявку #{order_id}"
    )

    # 1. Самому пользователю — за личный обмен
    _insert_wallet_earning(user_id, order_id, user_id, 'personal', rub_amount, earning)

    # 2. Рефереру — по той же ставке, что и у реферала на этом обмене
    parent_id = referral_parent.get(user_id)
    if parent_id and parent_id != user_id:
        _insert_wallet_earning(parent_id, order_id, user_id, 'referral', rub_amount, earning)

def get_markup(amount_rub):
    if amount_rub <= 4999: return 1.27
    elif amount_rub <= 29999: return 1.22
    else: return 1.17

def get_bm_commission_percent(base_rub):
    """Комиссия BestMerchant по базовой сумме (в рублях)."""
    if base_rub < 3000:
        return 50
    elif base_rub < 5000:
        return 40
    elif base_rub < 8000:
        return 35
    elif base_rub < 10000:
        return 33
    else:
        return 25

def get_nicepay_commission_percent(base_rub):
    """Комиссия NicePay по базовой сумме (в рублях).
    1000–3000 → 30%, 3000–5000 → 27%, 5000+ → 25%."""
    if base_rub < 3000:
        return 30
    elif base_rub < 5000:
        return 27
    else:
        return 25    
    
def _noop_button(text):
    """Кнопка-заголовок / инфо-строка. Ничего не делает при нажатии."""
    return InlineKeyboardButton(text, callback_data="noop")

async def safe_edit_text(query, text, **kwargs):
    """edit_message_text, который не падает на 'Message is not modified'."""
    try:
        return await query.edit_message_text(text, **kwargs)
    except telegram.error.BadRequest as e:
        if "not modified" in str(e).lower():
            return None
        raise

def calculate_my_share(order):
    """Доля владельца: чек минус доля работника минус фактическая сумма отправки.
    Бонус уже вычтен из rub_amount при использовании, отдельно вычитать не нужно."""
    rub_amount = order.get('rub_amount', 0)
    if rub_amount <= 0:
        return 0

    if 3000 <= rub_amount <= 6248:
        worker_percent = 0.12
    elif rub_amount <= 35998:
        worker_percent = 0.10
    else:
        worker_percent = 0.08
    worker_share = rub_amount * worker_percent

    swap_amount_rub = order.get('swap_amount_rub', 0) or 0
    reserve_rub = order.get('reserve_rub', 0) or 500

    if swap_amount_rub > 0:
        sent_amount = swap_amount_rub - reserve_rub
    else:
        # fallback: если свопа не было
        crypto_amount = order.get('crypto_amount', 0) or 0
        market_rate = order.get('market_rate', 0) or 0
        network_fee_rub = order.get('network_fee', 0.0) or 0.0
        sent_amount = crypto_amount * market_rate + network_fee_rub

    my_share = rub_amount - worker_share - sent_amount
    return round(my_share, 2)


def finalize_order_my_share(order_id):
    MY_SHARE_START_ID = 1092
    order = orders.get(order_id)
    if not order:
        return 0
    if order.get('payment_type') == 'bestmerchant':
        return 0
    if order_id < MY_SHARE_START_ID:
        return 0
    my_share = calculate_my_share(order)
    order['my_share'] = my_share
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE orders SET my_share = ? WHERE id = ?", (my_share, order_id))
    conn.commit()
    conn.close()
    logging.info(f"Заявка #{order_id}: доля владельца = {my_share:.2f} RUB")
    return my_share


def get_accumulated_my_share():
    MY_SHARE_START_ID = 1092
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    total = 0.0
    try:
        c.execute("""SELECT COALESCE(SUM(my_share), 0) FROM orders 
                     WHERE my_share_withdrawn = 0 AND status = 'done' 
                     AND payment_type != 'bestmerchant'
                     AND id >= ?""", (MY_SHARE_START_ID,))
        total = c.fetchone()[0] or 0
    except Exception as e:
        logging.error(f"get_accumulated_my_share error: {e}")
    conn.close()
    return round(float(total), 2)

def get_worker_commission_since_last_saturday():
    """Сумма доли работника по всем done заявкам,
    начиная с последней субботы 16:00 МСК."""
    # Приводим «сейчас» к МСК
    now_msk = datetime.utcnow() + timedelta(hours=3)
    # Понедельник=0, ..., Суббота=5, Воскресенье=6
    days_since_sat = (now_msk.weekday() - 5) % 7

    sat_msk = now_msk.replace(hour=16, minute=0, second=0, microsecond=0) - timedelta(days=days_since_sat)
    if sat_msk > now_msk:
        sat_msk -= timedelta(days=7)
    # Обратно в UTC для сравнения с finished_at
    sat_utc = sat_msk - timedelta(hours=3)

    total = 0.0
    for o in list(orders.values()):
        if o.get('status') != 'done':
            continue
        finished = o.get('finished_at')
        if not finished:
            continue
        try:
            finished_dt = datetime.fromisoformat(finished)
        except Exception:
            continue
        if finished_dt < sat_utc:
            continue

        rub = o.get('rub_amount', 0) or 0
        if 3000 <= rub <= 6248:
            worker_percent = 0.12
        elif rub <= 35998:
            worker_percent = 0.10
        else:
            worker_percent = 0.08
        total += rub * worker_percent

    return round(total, 2)

# ---------- БАЗА ДАННЫХ ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Настройка для повышения устойчивости к блокировкам
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA busy_timeout=5000')
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, crypto_type TEXT,
                  rub_amount REAL, crypto_amount REAL, wallet TEXT, payment_id INTEGER,
                  payment_type TEXT, payment_details TEXT, market_rate REAL, status TEXT,
                  proof_file_id TEXT, txid TEXT, bonus_used REAL DEFAULT 0)''')
        # 👇 Поля для расчёта доли владельца
    try: c.execute('ALTER TABLE orders ADD COLUMN my_share REAL DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN my_share_withdrawn INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN notification_msg_id INTEGER DEFAULT NULL')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN notification_chat_id INTEGER DEFAULT NULL')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN created_at TEXT')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN crypto_str TEXT')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN finished_at TEXT')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN network_fee REAL DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN swap_id TEXT')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN swap_amount_rub REAL DEFAULT 0.0')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN bonus_awarded INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN bonus_refunded INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN discount_from_coupons REAL DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN discount_from_wallet REAL DEFAULT 0')
    except sqlite3.OperationalError: pass

    # 👇 Поля для BestMerchant
    try: c.execute('ALTER TABLE orders ADD COLUMN bm_order_id TEXT')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN bm_status TEXT')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN bm_requisites TEXT')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN bm_pay_method TEXT')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN bm_need_receipt INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN bm_need_contact INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN bm_receipt_url TEXT')
    except sqlite3.OperationalError: pass

    # 👇 Поля для сообщений
    try: c.execute('ALTER TABLE orders ADD COLUMN admin_message_id INTEGER DEFAULT NULL')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN user_message_id INTEGER DEFAULT NULL')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE orders ADD COLUMN user_chat_id INTEGER DEFAULT NULL')
    except sqlite3.OperationalError: pass
    

    # 👇 Старые поля (можно оставить)
    try: c.execute('ALTER TABLE orders ADD COLUMN reserve_rub REAL DEFAULT 0')
    except sqlite3.OperationalError: pass

    c.execute('''CREATE TABLE IF NOT EXISTS payment_methods
                 (id INTEGER PRIMARY KEY, fio TEXT, bank TEXT, sbp TEXT, card TEXT,
                  active INTEGER DEFAULT 1, payments INTEGER DEFAULT 0,
                  total_rub REAL DEFAULT 0, timeout_until REAL DEFAULT 0)''')
    try: c.execute('ALTER TABLE payment_methods ADD COLUMN max_payments INTEGER DEFAULT 2')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE payment_methods ADD COLUMN interval_minutes REAL DEFAULT 5')
    except sqlite3.OperationalError: pass

    # Остальные таблицы без изменений...
    c.execute('''CREATE TABLE IF NOT EXISTS referrals
                 (user_id INTEGER, parent_id INTEGER, PRIMARY KEY (user_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS bonuses
                 (user_id INTEGER PRIMARY KEY, amount REAL DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    try: c.execute('ALTER TABLE users ADD COLUMN username TEXT')
    except sqlite3.OperationalError: pass

    c.execute('''CREATE TABLE IF NOT EXISTS banned_users
                 (user_id INTEGER PRIMARY KEY, banned_until REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_cancelled
                 (user_id INTEGER, date TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (user_id, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS day_session
                 (id INTEGER PRIMARY KEY, start_time TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS promocodes
                 (code TEXT PRIMARY KEY, max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0)''')
    try: c.execute('ALTER TABLE promocodes ADD COLUMN max_uses INTEGER DEFAULT 1')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE promocodes ADD COLUMN used_count INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    c.execute('''CREATE TABLE IF NOT EXISTS promocode_usage
                 (user_id INTEGER, code TEXT, PRIMARY KEY (user_id, code))''')
    c.execute('''CREATE TABLE IF NOT EXISTS panel_subscribers
                 (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS stats_reset
                 (id INTEGER PRIMARY KEY, offset REAL DEFAULT 0)''')
        # Добавляем индексы для ускорения частых запросов
    c.execute('CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_referrals_parent ON referrals(parent_id)')
            # ---------- VIP РЕФЕРАЛЬНАЯ СИСТЕМА ----------
    c.execute('''CREATE TABLE IF NOT EXISTS vip_earnings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  vip_user_id INTEGER NOT NULL,
                  order_id INTEGER NOT NULL UNIQUE,
                  referral_user_id INTEGER NOT NULL,
                  rub_amount REAL NOT NULL,
                  earning REAL NOT NULL,
                  created_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS vip_withdrawals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  vip_user_id INTEGER NOT NULL,
                  amount REAL NOT NULL,
                  kind TEXT NOT NULL,
                  created_at TEXT NOT NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_vip_earnings_vip ON vip_earnings(vip_user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_vip_withdrawals_vip ON vip_withdrawals(vip_user_id)')

        # ---------- КОШЕЛЁК (универсальный) ----------
    c.execute('''CREATE TABLE IF NOT EXISTS wallet_earnings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  order_id INTEGER NOT NULL,
                  source_user_id INTEGER NOT NULL,
                  kind TEXT NOT NULL,
                  rub_amount REAL NOT NULL,
                  earning REAL NOT NULL,
                  created_at TEXT NOT NULL,
                  UNIQUE(user_id, order_id, kind))''')
    c.execute('''CREATE TABLE IF NOT EXISTS wallet_withdrawals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  amount REAL NOT NULL,
                  kind TEXT NOT NULL,
                  created_at TEXT NOT NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_wallet_earnings_user ON wallet_earnings(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_wallet_withdrawals_user ON wallet_withdrawals(user_id)')

    # Миграция из VIP-таблиц (один раз)
    c.execute("SELECT COUNT(*) FROM wallet_earnings")
    wallet_count = c.fetchone()[0]
    if wallet_count == 0:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vip_earnings'")
        if c.fetchone():
            try:
                c.execute('''INSERT OR IGNORE INTO wallet_earnings
                             (user_id, order_id, source_user_id, kind, rub_amount, earning, created_at)
                             SELECT vip_user_id, order_id, referral_user_id, 'referral',
                                    rub_amount, earning, created_at
                             FROM vip_earnings''')
                migrated_e = c.rowcount
                c.execute('''INSERT OR IGNORE INTO wallet_withdrawals
                             (user_id, amount, kind, created_at)
                             SELECT vip_user_id, amount, kind, created_at
                             FROM vip_withdrawals''')
                migrated_w = c.rowcount
                logging.info(f"Миграция VIP → Кошелёк: {migrated_e} начислений, {migrated_w} списаний")
            except Exception as e:
                logging.error(f"Ошибка миграции VIP → Кошелёк: {e}")

    conn.commit()   # ← коммит ПОСЛЕ всех операций
    conn.close()

def load_data():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    global orders, next_order_id
    c.execute("SELECT * FROM orders ORDER BY id")
    for row in c.fetchall():
        order = {
            'user_id': row['user_id'],
            'username': row['username'],
            'crypto_type': row['crypto_type'],
            'rub_amount': row['rub_amount'],
            'crypto_amount': row['crypto_amount'],
            'wallet': row['wallet'],
            'payment_id': row['payment_id'],
            'payment_type': row['payment_type'],
            'payment_details': row['payment_details'],
            'market_rate': row['market_rate'],
            'status': row['status'],
            'proof_file_id': row['proof_file_id'],
            'txid': row['txid'],
            'bonus_used': row['bonus_used'],
            'notification_msg_id': row['notification_msg_id'],
            'notification_chat_id': row['notification_chat_id'],
            'created_at': row['created_at'],
            'crypto_str': row['crypto_str'],
            'finished_at': row['finished_at'],
            'network_fee': row['network_fee'],
            'swap_id': row['swap_id'],
            'swap_amount_rub': row['swap_amount_rub'],
            'bonus_awarded': bool(row['bonus_awarded']),
            'reserve_rub': row['reserve_rub'],
            'bm_order_id': row['bm_order_id'],
            'bm_status': row['bm_status'],
            'bm_requisites': row['bm_requisites'],
            'bm_pay_method': row['bm_pay_method'],
            'bm_need_receipt': bool(row['bm_need_receipt']),
            'bm_need_contact': bool(row['bm_need_contact']),
            'bm_receipt_url': row['bm_receipt_url'],
            # 👇 поля сообщений
            'admin_message_id': row['admin_message_id'] if 'admin_message_id' in row.keys() else None,
            'user_message_id': row['user_message_id'] if 'user_message_id' in row.keys() else None,
            'user_chat_id': row['user_chat_id'] if 'user_chat_id' in row.keys() else None,
            # 👇 поля доли владельца
            'my_share': row['my_share'] if 'my_share' in row.keys() else 0,
            'my_share_withdrawn': bool(row['my_share_withdrawn']) if 'my_share_withdrawn' in row.keys() else False,
            'bonus_refunded': bool(row['bonus_refunded']) if 'bonus_refunded' in row.keys() else False,
            'discount_from_coupons': row['discount_from_coupons'] if 'discount_from_coupons' in row.keys() else 0,
            'discount_from_wallet': row['discount_from_wallet'] if 'discount_from_wallet' in row.keys() else 0,
        }
        orders[row['id']] = order
    if orders:
        next_order_id = max(orders.keys()) + 1

    global payment_methods, next_payment_id
    c.execute("SELECT * FROM payment_methods ORDER BY id")
    payment_methods.clear()
    for row in c.fetchall():
        payment_methods[row['id']] = {
            'fio': row['fio'],
            'bank': row['bank'],
            'sbp': row['sbp'],
            'card': row['card'],
            'active': bool(row['active']),
            'payments': row['payments'],
            'total_rub': row['total_rub'],
            'timeout_until': row['timeout_until'],
            'max_payments': row['max_payments'],
            'interval_minutes': row['interval_minutes'],
        }
    if payment_methods:
        next_payment_id = max(payment_methods.keys()) + 1

    global referrals, referral_parent
    c.execute("SELECT * FROM referrals")
    referrals.clear()
    referral_parent.clear()
    for row in c.fetchall():
        user_id, parent_id = row['user_id'], row['parent_id']
        referral_parent[user_id] = parent_id
        referrals.setdefault(parent_id, []).append(user_id)

    global user_bonuses
    c.execute("SELECT * FROM bonuses")
    user_bonuses.clear()
    for row in c.fetchall():
        user_bonuses[row['user_id']] = row['amount']

    global daily_start_time
    c.execute("SELECT start_time FROM day_session WHERE id = 1")
    row = c.fetchone()
    daily_start_time = datetime.fromisoformat(row['start_time']) if row else None

    conn.close()



def is_new_user(user_id, username=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    exists = c.fetchone()
    if not exists:
        c.execute('INSERT INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
        conn.commit(); conn.close()
        return True
    else:
        if username:
            c.execute('UPDATE users SET username = ? WHERE user_id = ?', (username, user_id))
            conn.commit()
    conn.close()
    return False

def is_banned(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT banned_until FROM banned_users WHERE user_id = ?', (user_id,))
    result = c.fetchone(); conn.close()
    return (True, result[0]) if result and time.time() < result[0] else (False, 0)

def add_cancelled(user_id):
    today = datetime.now().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT INTO user_cancelled (user_id, date, count) VALUES (?, ?, 1)
                 ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1''', (user_id, today))
    c.execute('SELECT count FROM user_cancelled WHERE user_id = ? AND date = ?', (user_id, today))
    count = c.fetchone()[0]
    conn.commit(); conn.close()
    if count >= MAX_CANCELLED:
        banned_until = time.time() + BAN_HOURS * 3600
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO banned_users VALUES (?, ?)', (user_id, banned_until))
        conn.commit(); conn.close()
        return True, banned_until
    return False, 0

def save_order(order_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    order = orders.get(order_id)
    if order:
        fields = {
            'id': order_id,
            'user_id': order.get('user_id'),
            'username': order.get('username', ''),
            'crypto_type': order.get('crypto_type', ''),
            'rub_amount': order.get('rub_amount', 0),
            'crypto_amount': order.get('crypto_amount', 0),
            'wallet': order.get('wallet', ''),
            'payment_id': order.get('payment_id'),
            'payment_type': order.get('payment_type', ''),
            'payment_details': order.get('payment_details', ''),
            'market_rate': order.get('market_rate', 0),
            'status': order.get('status', ''),
            'proof_file_id': order.get('proof_file_id'),
            'txid': order.get('txid'),
            'bonus_used': order.get('bonus_used', 0),
            'notification_msg_id': order.get('notification_msg_id'),
            'notification_chat_id': order.get('notification_chat_id'),
            'created_at': order.get('created_at'),
            'crypto_str': order.get('crypto_str'),
            'finished_at': order.get('finished_at'),
            'network_fee': order.get('network_fee', 0.0),
            'swap_id': order.get('swap_id'),
            'swap_amount_rub': order.get('swap_amount_rub', 0.0),
            'bonus_awarded': int(order.get('bonus_awarded', False)),
            'reserve_rub': order.get('reserve_rub', 0),
            'bm_order_id': order.get('bm_order_id'),
            'bm_status': order.get('bm_status'),
            'bm_requisites': order.get('bm_requisites'),
            'bm_pay_method': order.get('bm_pay_method'),
            'bm_need_receipt': 1 if order.get('bm_need_receipt') else 0,
            'bm_need_contact': 1 if order.get('bm_need_contact') else 0,
            'bm_receipt_url': order.get('bm_receipt_url'),
            # 👇 поля сообщений
            'admin_message_id': order.get('admin_message_id'),
            'user_message_id': order.get('user_message_id'),
            'user_chat_id': order.get('user_chat_id'),
            # 👇 поля доли владельца
            'my_share': order.get('my_share', 0),
            'my_share_withdrawn': 1 if order.get('my_share_withdrawn') else 0,
            'bonus_refunded': 1 if order.get('bonus_refunded') else 0,
            'discount_from_coupons': order.get('discount_from_coupons', 0),
            'discount_from_wallet': order.get('discount_from_wallet', 0),
        }
        columns = ', '.join(fields.keys())
        placeholders = ', '.join('?' * len(fields))

        # Флаги-«предохранители»: 0 → 1, но никогда 1 → 0.
        # Используем MAX, чтобы save_order из другого корутина
        # не откатил уже выставленный флаг.
        monotonic_fields = {'bonus_awarded', 'my_share_withdrawn', 'bonus_refunded'}

        update_clauses = []
        for col in fields.keys():
            if col == 'id':
                continue
            if col in monotonic_fields:
                update_clauses.append(f"{col} = MAX(orders.{col}, excluded.{col})")
            else:
                update_clauses.append(f"{col} = excluded.{col}")

        sql = (
            f"INSERT INTO orders ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {', '.join(update_clauses)}"
        )
        c.execute(sql, list(fields.values()))
        conn.commit()
    if order and order.get('status') in ('done', 'cancelled'):
        with _order_locks_mutex:
            order_locks.pop(order_id, None)
    conn.close()

def save_payment_method(payment_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    p = payment_methods.get(payment_id)
    if p:
        c.execute('''INSERT OR REPLACE INTO payment_methods (id, fio, bank, sbp, card, active,
             payments, total_rub, timeout_until, max_payments, interval_minutes)
             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
          (payment_id, p['fio'], p['bank'], p['sbp'], p['card'], int(p['active']),
           p['payments'], p['total_rub'], p['timeout_until'],
           p.get('max_payments', 2), p.get('interval_minutes', 5)))
        conn.commit()
    conn.close()

def save_referral(user_id, parent_id):
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO referrals VALUES (?,?)', (user_id, parent_id))
    conn.commit(); conn.close()

def save_bonus(user_id):
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO bonuses VALUES (?,?)', (user_id, user_bonuses.get(user_id,0)))
    conn.commit(); conn.close()

def reserve_discount_for_order(user_id, amount):
    """Списывает скидку: сначала купоны, остаток — из кошелька.
    Возвращает (from_coupons, from_wallet) или None если средств не хватает."""
    if amount <= 0:
        return (0.0, 0.0)

    coupons = round(user_bonuses.get(user_id, 0), 2)
    wallet = get_wallet_available_balance(user_id)

    if coupons + wallet < amount:
        return None

    from_coupons = min(coupons, amount)
    from_wallet = round(amount - from_coupons, 2)

    if from_coupons > 0:
        user_bonuses[user_id] = round(coupons - from_coupons, 2)
        save_bonus(user_id)

    if from_wallet > 0:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute('''INSERT INTO wallet_withdrawals
                         (user_id, amount, kind, created_at)
                         VALUES (?, ?, 'discount', ?)''',
                      (user_id, from_wallet, datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            logging.error(f"reserve_discount_for_order: {e}")
            return None
        finally:
            conn.close()

    return (from_coupons, from_wallet)


def refund_bonus_for_order(order_id):
    """Возврат скидки при отмене: купоны → купоны, кошелёк → кошелёк.
    Идемпотентно через флаг bonus_refunded."""
    order = orders.get(order_id)
    if not order:
        return
    bonus_used = order.get('bonus_used', 0) or 0
    if bonus_used <= 0:
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute('UPDATE orders SET bonus_refunded = 1 '
                  'WHERE id = ? AND bonus_refunded = 0',
                  (order_id,))
        if c.rowcount == 0:
            return
        conn.commit()
    finally:
        conn.close()

    uid = order['user_id']

    from_coupons = order.get('discount_from_coupons', 0) or 0
    from_wallet = order.get('discount_from_wallet', 0) or 0

    # Fallback для старых заявок (до апдейта): всё возвращаем купонами
    if from_coupons == 0 and from_wallet == 0:
        from_coupons = bonus_used

    if from_coupons > 0:
        user_bonuses[uid] = user_bonuses.get(uid, 0) + from_coupons
        save_bonus(uid)

    if from_wallet > 0:
        # Компенсация: возвращаем в кошелёк отрицательным списанием
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute('''INSERT INTO wallet_withdrawals
                         (user_id, amount, kind, created_at)
                         VALUES (?, ?, 'refund', ?)''',
                      (uid, -from_wallet, datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            logging.error(f"refund_bonus_for_order wallet: {e}")
        finally:
            conn.close()

    order['bonus_refunded'] = True
    logging.info(
        f"Заявка #{order_id}: возврат скидки "
        f"(купоны {from_coupons:.2f} + кошелёк {from_wallet:.2f})"
    )

def save_all_data():
    for oid in orders: save_order(oid)
    for pid in payment_methods: save_payment_method(pid)
    for uid in user_bonuses: save_bonus(uid)

def reload_payments():
    global payment_methods, next_payment_id
    with payment_methods_lock:
        conn = sqlite3.connect(DB_FILE); c = conn.cursor()
        new_methods = {}
        c.execute("SELECT * FROM payment_methods ORDER BY id")
        for row in c.fetchall():
            new_methods[row[0]] = {
                'fio': row[1], 'bank': row[2], 'sbp': row[3], 'card': row[4],
                'active': bool(row[5]), 'payments': row[6], 'total_rub': row[7],
                'timeout_until': row[8],
                'max_payments': row[9] if len(row) > 9 else 2,
                'interval_minutes': row[10] if len(row) > 10 else 5
            }
        # Атомарная замена — dict reference swap, безопасно
        payment_methods.clear()
        payment_methods.update(new_methods)
        next_payment_id = max(payment_methods.keys()) + 1 if payment_methods else 1
        conn.close()

# ---------- ВНУТРЕННИЙ HTTP API ДЛЯ ПАНЕЛИ + ВЕБХУК BestMerchant ----------
class ReloadHandler(BaseHTTPRequestHandler):

    # ---------- ВСПОМОГАТЕЛЬНОЕ ----------
    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, text):
        body = text.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                return None
            body = self.rfile.read(content_length)
            return json.loads(body)
        except Exception as e:
            logging.error(f"[HTTP] read_json_body error: {e}")
            return None

    # ---------- GET ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == '/reload':
            reload_payments()
            self._send_text(200, 'OK')

        elif path == '/set_bm_success':
            # Ручное подтверждение BM-заявки из панели (внутренний order_id)
            order_id = int(qs.get('order_id', [0])[0])
            asyncio.run_coroutine_threadsafe(set_bm_success(order_id), main_loop)
            self._send_text(200, 'OK')

        elif path == '/confirm_payment':
            order_id = int(qs.get('order_id', [0])[0])
            asyncio.run_coroutine_threadsafe(confirm_payment_by_id(order_id), main_loop)
            self._send_text(200, 'OK')

        elif path == '/cancel_order':
            order_id = int(qs.get('order_id', [0])[0])
            asyncio.run_coroutine_threadsafe(cancel_order_by_id(order_id), main_loop)
            self._send_text(200, 'OK')

        elif path == '/reset_payment':
            pid = int(qs.get('payment_id', [0])[0])
            asyncio.run_coroutine_threadsafe(_async_reset_payment_counter(pid), main_loop)
            self._send_text(200, 'OK')

        elif path == '/deactivate_payment':
            pid = int(qs.get('payment_id', [0])[0])
            asyncio.run_coroutine_threadsafe(_async_deactivate_payment(pid), main_loop)
            self._send_text(200, 'OK')

        elif path == '/subscribe':
            user_id = int(qs.get('user_id', [0])[0])
            add_subscriber(user_id)
            self._send_text(200, 'OK')

        elif path == '/unsubscribe':
            user_id = int(qs.get('user_id', [0])[0])
            remove_subscriber(user_id)
            self._send_text(200, 'OK')

        elif path == '/get_file':
            file_id = qs.get('file_id', [None])[0]
            if not file_id:
                self.send_response(400); self.end_headers(); return
            try:
                async def get_file_bytes():
                    file = await app.bot.get_file(file_id)
                    file_bytes = await file.download_as_bytearray()
                    return bytes(file_bytes)

                future = asyncio.run_coroutine_threadsafe(get_file_bytes(), main_loop)
                data = future.result(timeout=30)

                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                logging.error(f"Get file error: {e}")
                self.send_response(404); self.end_headers()

        elif path == '/balance':
            try:
                async def get_balances():
                    ltc = btc = usdt = 0
                    try:
                        ltc = await asyncio.wait_for(get_wallet_balance_rub('LTC'), timeout=3)
                    except Exception: pass
                    try:
                        btc = await asyncio.wait_for(get_wallet_balance_rub('BTC'), timeout=3)
                    except Exception: pass
                    try:
                        usdt = await asyncio.wait_for(get_coin_balance('USDT-TRC20'), timeout=3)
                    except Exception: pass

                    my_share_total = get_accumulated_my_share()
                    usdt_rub_rate = await get_wallet_rate('USDT-TRC20') or 90.0
                    my_share_usdt = my_share_total / usdt_rub_rate
                    usdt_available = max(0, (usdt or 0) - my_share_usdt)
                    return {
                        'LTC': ltc or 0,
                        'BTC': btc or 0,
                        'USDT': usdt or 0,
                        'USDT_available': usdt_available,
                        'my_share_total_rub': my_share_total,
                        'wallet_rub': (usdt or 0) * usdt_rub_rate,
                    }

                future = asyncio.run_coroutine_threadsafe(get_balances(), main_loop)
                data = future.result(timeout=10)
                self._send_json(200, data)
            except Exception as e:
                logging.error(f"Balance error: {e}")
                self.send_response(500); self.end_headers()

        elif path == '/usdt_wallet':
            self._send_json(200, {"address": USDT_WALLET_ADDRESS})

        elif path == '/nicepay_webhook':
            try:
                params = {k: v[0] for k, v in qs.items()}
                if not nicepay_verify_hash(params):
                    logging.warning(f"[NicePay webhook] invalid hash: {params}")
                    self._send_json(200, {"error": {"message": "Invalid hash"}})
                    return

                result = params.get('result')
                order_id_str = params.get('order_id')
                payment_id = params.get('payment_id')

                # --- Логирование всех полей вебхука ---
                logging.info(
                    f"[NicePay webhook] result={result} "
                    f"payment_id={payment_id} "
                    f"merchant_id={params.get('merchant_id')} "
                    f"order_id={order_id_str} "
                    f"amount={params.get('amount')} {params.get('amount_currency')} "
                    f"profit={params.get('profit')} {params.get('profit_currency')} "
                    f"method={params.get('method')} "
                    f"hash={params.get('hash')}"
                )

                try:
                    order_id = int(order_id_str)
                except (TypeError, ValueError):
                    self._send_json(200, {"error": {"message": "Bad order_id"}})
                    return

                if result == 'success':
                    asyncio.run_coroutine_threadsafe(
                        _nicepay_webhook_success(order_id, payment_id), main_loop
                    )
                    self._send_json(200, {"result": {"message": "Success"}})
                elif result == 'error':
                    asyncio.run_coroutine_threadsafe(
                        cancel_order_by_id(order_id), main_loop
                    )
                    self._send_json(200, {"error": {"message": "Error"}})
                else:
                    self._send_json(200, {"error": {"message": "Empty"}})
            except Exception as e:
                logging.exception(f"[NicePay webhook] {e}")
                self._send_json(200, {"error": {"message": str(e)}})

        else:
            self.send_response(404); self.end_headers()

    # ---------- POST ----------
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # ================================================================
        # ВЕБХУК BestMerchant — SmartOrderStatus
        # Документация: POST / , Content-Type: application/json
        # Payload: { "order_guid": "...", "user_guid": "...",
        #            "status": 0|1|2, "amount": ..., "close_time": "..." }
        # order_guid — это bm_order_id (UUID-строка), НЕ внутренний order_id.
        # ================================================================
        if path == '/bm_webhook':
            try:
                data = self._read_json_body()
                if not data:
                    logging.warning("[BM webhook] пустое тело запроса")
                    self._send_text(400, 'Bad Request')
                    return

                order_guid = data.get('order_guid')
                status = data.get('status')
                user_guid = data.get('user_guid')
                amount = data.get('amount')
                close_time = data.get('close_time')

                logging.info(
                    f"[BM webhook] order_guid={order_guid} status={status} "
                    f"user_guid={user_guid} amount={amount} close_time={close_time}"
                )

                if not order_guid:
                    logging.warning("[BM webhook] отсутствует order_guid")
                    self._send_text(400, 'No order_guid')
                    return

                # Ищем внутренний order_id по bm_order_id
                internal_order_id = None
                for oid, o in list(orders.items()):
                    if o.get('bm_order_id') == order_guid:
                        internal_order_id = oid
                        break

                if internal_order_id is None:
                    logging.warning(
                        f"[BM webhook] не найдена заявка с bm_order_id={order_guid}"
                    )
                    # Отвечаем 200, чтобы BM не считал это ошибкой
                    self._send_text(200, 'OK (unknown order)')
                    return

                # Status: 0 — ожидает оплаты, 1 — оплачена, 2 — отменена
                if status == 1:
                    asyncio.run_coroutine_threadsafe(
                        mark_bm_paid_and_confirm(internal_order_id), main_loop
                    )
                    logging.info(
                        f"[BM webhook] заявка #{internal_order_id} → подтверждение "
                        f"(bm_order_id={order_guid})"
                    )
                elif status == 2:
                    asyncio.run_coroutine_threadsafe(
                        cancel_order_by_id(internal_order_id), main_loop
                    )
                    logging.info(
                        f"[BM webhook] заявка #{internal_order_id} → отмена "
                        f"(bm_order_id={order_guid})"
                    )
                elif status == 0:
                    logging.info(
                        f"[BM webhook] заявка #{internal_order_id} ожидает оплаты"
                    )
                else:
                    logging.warning(
                        f"[BM webhook] неизвестный статус {status} "
                        f"для заявки #{internal_order_id}"
                    )

                self._send_text(200, 'OK')

            except Exception as e:
                logging.exception(f"[BM webhook] критическая ошибка: {e}")
                self.send_response(500); self.end_headers()

        # ================================================================
        # Остальные POST-эндпоинты панели
        # ================================================================
        elif path == '/add_payment':
            data = self._read_json_body()
            if not data:
                self._send_text(400, 'Bad Request'); return
            asyncio.run_coroutine_threadsafe(add_payment_from_panel(data), main_loop)
            self._send_text(200, 'OK')

        elif path == '/update_payment_limit':
            data = self._read_json_body()
            if not data:
                self._send_text(400, 'Bad Request'); return
            asyncio.run_coroutine_threadsafe(
                _async_update_payment_limit(data['payment_id'], data['max_payments']),
                main_loop
            )
            self._send_text(200, 'OK')

        elif path == '/update_payment_interval':
            data = self._read_json_body()
            if not data:
                self._send_text(400, 'Bad Request'); return
            asyncio.run_coroutine_threadsafe(
                _async_update_payment_interval(data['payment_id'], data['interval_minutes']),
                main_loop
            )
            self._send_text(200, 'OK')

        elif path == '/remove_payment':
            data = self._read_json_body()
            if not data:
                self._send_text(400, 'Bad Request'); return
            asyncio.run_coroutine_threadsafe(
                _async_remove_payment_by_id(data['payment_id']),
                main_loop
            )
            self._send_text(200, 'OK')

        else:
            self.send_response(404); self.end_headers()

    def log_message(self, format, *args):
        # Подавляем стандартный лог BaseHTTPRequestHandler
        pass

async def _do_fulfillment(bot, order_id):
    """Общая логика после подтверждения оплаты:
    резерв → комиссия → своп → поллинг → отправка крипты → поллинг вывода.
    Статус заявки УЖЕ должен быть 'payment_confirmed'."""
    order = orders.get(order_id)
    if not order:
        return

    try:
        crypto_type = order['crypto_type']
        crypto_amount = order['crypto_amount']
        market_rate = order['market_rate']
        wallet = order['wallet']

        # --- резерв ---
        reserve_rub = 500
        reserve_coin = reserve_rub / market_rate
        order['reserve_rub'] = reserve_rub
        save_order(order_id)

        # --- комиссия сети ---
        network_fee_coin = await get_network_commission(crypto_type) or 0
        network_fee_rub = network_fee_coin * market_rate
        order['network_fee'] = network_fee_rub
        save_order(order_id)

        total_coin_needed = crypto_amount + network_fee_coin + reserve_coin
        min_swap = MIN_SWAP_LTC if crypto_type == 'LTC' else MIN_SWAP_BTC
        swap_amount = max(total_coin_needed * 1.01, min_swap)

        # --- своп ---
        swap_id = await swap_usdt_to(crypto_type, swap_amount)
        if not swap_id:
            swap_amount = max(total_coin_needed * 1.1, min_swap)
            swap_id = await swap_usdt_to(crypto_type, swap_amount)
            if not swap_id:
                logging.error(f"Заявка #{order_id}: не удалось создать своп")
                save_order(order_id)
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 Заявка №{order_id} — ошибка конвертации\n"
                    f"Ожидает отправки валюты\n\n"
                    f"👤 Юзер: {order['username']} (ID: {order['user_id']})\n"
                    f"💠 Валюта: {crypto_type}\n"
                    f"🔢 Количество: {crypto_amount:.6f}\n"
                    f"📫 Кошелёк: <code>{wallet}</code>\n"
                    f"💰 Сумма RUB: {order['rub_amount']:.2f}",
                    parse_mode='HTML'
                )
                return

        order['swap_id'] = swap_id
        order['swap_amount_rub'] = swap_amount * market_rate
        save_order(order_id)

        success = await poll_swap(swap_id)
        if not success:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ Обмен {swap_id} для заявки #{order_id} не завершился успешно."
            )
            return

        # --- фактическая стоимость свопа ---
        try:
            resp = await asyncio.to_thread(
                requests.get,
                f"{WALLET_BASE_URL}/api/v1/exchange",
                headers={"X-Api-Key": WALLET_API_KEY},
                params={"id": swap_id}, timeout=15
            )
            swap_data = resp.json()
            if resp.status_code == 200 and 'from_amount' in swap_data:
                from_amount_usdt = float(swap_data['from_amount'])
                usdt_rub_rate = await get_wallet_rate('USDT-TRC20')
                if usdt_rub_rate is None:
                    usdt_rub_rate = 90.0
                order['swap_amount_rub'] = from_amount_usdt * usdt_rub_rate
                save_order(order_id)
                logging.info(
                    f"Заявка #{order_id}: фактическая стоимость свопа "
                    f"= {order['swap_amount_rub']:.2f} RUB"
                )
        except Exception as e:
            logging.error(
                f"Не удалось получить фактическую стоимость свопа "
                f"для заявки #{order_id}: {e}"
            )

        if order.get('bonus_used', 0) > 0:
            logging.info(
                f"Заявка #{order_id}: применена скидка "
                f"{order['bonus_used']:.2f} ₽ (зарезервировано ранее)"
            )

        # --- вывод крипты ---
        withdrawal_id = await send_crypto(crypto_type, wallet, crypto_amount)
        if withdrawal_id:
            asyncio.create_task(
                poll_withdrawal_status(bot, order_id, withdrawal_id)
            )
            if order.get('admin_message_id'):
                try:
                    if order.get('payment_type') in ('bestmerchant', 'nicepay'):
                        await bot.edit_message_text(
                            chat_id=ADMIN_ID,
                            message_id=order['admin_message_id'],
                            text=f"✅ Заявка #{order_id} подтверждена. "
                                 f"Ожидаем завершения вывода {withdrawal_id}...",
                            parse_mode='HTML'
                        )
                    else:
                        await bot.edit_message_caption(
                            chat_id=ADMIN_ID,
                            message_id=order['admin_message_id'],
                            caption=f"✅ Заявка #{order_id} подтверждена. "
                                    f"Ожидаем завершения вывода {withdrawal_id}..."
                        )
                except Exception as e:
                    logging.error(f"Не удалось отредактировать сообщение админа: {e}")
            notify_subscribers(order_id, f"✅ Заявка #{order_id} завершена.")
        else:
            logging.error(f"Ошибка создания вывода для заявки #{order_id}")
            save_order(order_id)
            await bot.send_message(
                ADMIN_ID,
                f"🚨 Заявка №{order_id} — ошибка создания вывода\n"
                f"Своп выполнен (swap_id: {order.get('swap_id')}), "
                f"но вывод не создан\n\n"
                f"👤 Юзер: {order['username']} (ID: {order['user_id']})\n"
                f"💠 Валюта: {crypto_type}\n"
                f"🔢 Количество: {crypto_amount:.6f}\n"
                f"📫 Кошелёк: <code>{wallet}</code>\n"
                f"💰 Сумма RUB: {order['rub_amount']:.2f}",
                parse_mode='HTML'
            )
    except Exception as e:
        logging.exception(f"Ошибка в _do_fulfillment для заявки #{order_id}: {e}")
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🚨 Непредвиденная ошибка при обработке заявки #{order_id}: {e}"
            )
        except:
            pass

async def confirm_payment_by_id(order_id):
    try:
        order = orders.get(order_id)
        if not order or order['status'] != 'waiting_proof':
            return

        # --- атомарный переход статуса ---
        lock = get_order_lock(order_id)
        async with lock:
            if order['status'] != 'waiting_proof':
                return
            order['status'] = 'payment_confirmed'
            save_order(order_id)

        # --- уведомление пользователя ---
        asyncio.ensure_future(
            send_payment_confirmed_message(app.bot, order_id, order)
        )

        # --- всё остальное ---
        await _do_fulfillment(app.bot, order_id)
    except Exception as e:
        logging.exception(f"Критическая ошибка в confirm_payment_by_id для заявки #{order_id}: {e}")
        try:
            await app.bot.send_message(
                ADMIN_ID,
                f"🚨 Непредвиденная ошибка при обработке заявки #{order_id}: {e}"
            )
        except:
            pass

async def set_bm_success(order_id):
    order = orders.get(order_id)
    if order and order['status'] == 'waiting_proof':
        order['bm_status'] = 'success'
        save_order(order_id)
        logging.info(f"Заявка #{order_id}: bm_status установлен в success вебхуком")
        # ✅ Немедленно запускаем обработку оплаты
        await confirm_payment_by_id(order_id)

async def mark_bm_paid_and_confirm(order_id):
    order = orders.get(order_id)
    if not order:
        logging.warning(f"mark_bm_paid_and_confirm: заявка #{order_id} не найдена")
        return
    if order['status'] != 'waiting_proof':
        logging.info(
            f"mark_bm_paid_and_confirm: заявка #{order_id} в статусе "
            f"{order['status']}, пропускаем"
        )
        return

    order['bm_status'] = 'success'
    save_order(order_id)

    if order.get('_bm_confirm_started'):
        return

    order['_bm_confirm_started'] = True
    logging.info(
        f"Заявка #{order_id}: BM webhook → success, запускаю confirm_payment_by_id"
    )
    await confirm_payment_by_id(order_id)

async def send_payment_confirmed_message(bot, order_id, order):
    crypto_type = order['crypto_type']
    crypto_str = order.get('crypto_str', f"{order['crypto_amount']:.6f}")
    wallet = order['wallet']
    user_id = order['user_id']
    text = (
        f"✅ <b>Заявка №{order_id} оплачена</b>\n\n"
        f"🪙 К получению: <code>{crypto_str}</code> {crypto_type}\n"
        f"👛 Кошелёк: <code>{wallet}</code>\n\n"
        f"⚡️ В ближайшее время криптовалюта будет отправлена на указанный кошелёк.\n\n"
        f"🔗 Ссылка на транзакцию поступит следующим сообщением."
    )

    # Пытаемся отредактировать исходное сообщение заявки — меняем
    # «Заявка создана» на «Заявка оплачена»
    msg_id = order.get('user_message_id')
    chat_id = order.get('user_chat_id', user_id)

    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                parse_mode='HTML',
                disable_web_page_preview=True,
                reply_markup=None
            )
            return
        except Exception as e:
            logging.warning(f"Не удалось отредактировать сообщение заявки #{order_id}: {e}")

    # Fallback: если edit не сработал — отправляем новое
    try:
        await bot.send_message(user_id, text, parse_mode='HTML', disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение о подтверждении: {e}")

async def cancel_order_by_id(order_id):
    order = orders.get(order_id)
    if not order:
        return
    if order['status'] == 'done':
        logging.warning(f"Попытка отмены завершённого заказа #{order_id}, игнорируем")
        return
    if order['status'] not in ('waiting_proof', 'payment_confirmed'):
        return

    order['status'] = 'cancelled'
    save_order(order_id)
    refund_bonus_for_order(order_id)
    add_cancelled(order['user_id'])
    pid = order.get('payment_id')
    if pid and pid in payment_methods:
        payment_methods[pid]['timeout_until'] = 0
        save_payment_method(pid)

    try:
        await app.bot.send_message(
            order['user_id'],
            "Платёж был отклонён.\nПожалуйста, обратитесь в поддержку: @TeRX_Supp",
            reply_markup=main_keyboard
        )
    except:
        pass

    user_msg_id = order.get('user_message_id')
    user_chat_id = order.get('user_chat_id', order['user_id'])
    if user_msg_id:
        try:
            await app.bot.edit_message_text(
                chat_id=user_chat_id,
                message_id=user_msg_id,
                text=f"❌ Заявка #{order_id} отменена.",
                reply_markup=None
            )
        except:
            pass

    # --- Админ-сообщение: для BM/NicePay — текст, для локальных — caption ---
    if order.get('admin_message_id'):
        if order.get('payment_type') in ('bestmerchant', 'nicepay'):
            try:
                crypto_str_display = order.get('crypto_str') or f"{order['crypto_amount']:.6f}"
                await app.bot.edit_message_text(
                    chat_id=ADMIN_ID,
                    message_id=order['admin_message_id'],
                    text=(
                        f"❌ Заявка №{order_id} отменена\n"
                        f"К оплате: {order['rub_amount']:.2f} RUB\n"
                        f"Получение: {crypto_str_display} {order['crypto_type']}"
                    ),
                    parse_mode='HTML'
                )
            except Exception as e:
                logging.error(f"Не удалось отредактировать сообщение админа об отмене: {e}")
        else:
            try:
                await app.bot.edit_message_caption(
                    chat_id=ADMIN_ID,
                    message_id=order['admin_message_id'],
                    caption=f"❌ Заявка #{order_id} отменена."
                )
            except Exception as e:
                logging.error(f"Не удалось отредактировать сообщение админа об отмене: {e}")

    notify_subscribers(order_id, f"❌ Заявка #{order_id} отклонена.")

def add_subscriber(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO panel_subscribers (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()

def remove_subscriber(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM panel_subscribers WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def update_payment_limit(pid, max_pay):
    p = payment_methods.get(pid)
    if p:
        p['max_payments'] = max_pay
        if p['payments'] < max_pay:
            p['active'] = True
        save_payment_method(pid)

def update_payment_interval(pid, interval_minutes):
    p = payment_methods.get(pid)
    if p:
        p['interval_minutes'] = float(interval_minutes)
        save_payment_method(pid)

def remove_payment_by_id(pid):
    if pid in payment_methods:
        del payment_methods[pid]
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('DELETE FROM payment_methods WHERE id = ?', (pid,))
        conn.commit()
        conn.close()

def reset_payment_counter(pid):
    p = payment_methods.get(pid)
    if p:
        p['payments'] = 0
        p['active'] = True
        p['timeout_until'] = 0
        save_payment_method(pid)

def deactivate_payment(pid):
    p = payment_methods.get(pid)
    if p:
        p['active'] = False
        p['timeout_until'] = 0
        save_payment_method(pid)

async def add_payment_from_panel(data):
    global next_payment_id, payment_methods
    pid = next_payment_id
    next_payment_id += 1
    payment_methods[pid] = {
        'fio': data['fio'],
        'bank': data['bank'],
        'sbp': data['sbp'],
        'card': data['card'],
        'active': True,
        'payments': 0,
        'total_rub': 0.0,
        'timeout_until': 0,
        'max_payments': int(data.get('max_payments', 2)),
        'interval_minutes': float(data.get('interval_minutes', 5))
    }
    save_payment_method(pid)

async def _async_reset_payment_counter(pid):
    reset_payment_counter(pid)

async def _async_deactivate_payment(pid):
    deactivate_payment(pid)

async def _async_update_payment_limit(pid, max_pay):
    update_payment_limit(pid, max_pay)

async def _async_update_payment_interval(pid, interval_minutes):
    update_payment_interval(pid, interval_minutes)

async def _async_remove_payment_by_id(pid):
    remove_payment_by_id(pid)

def notify_subscribers(order_id, text):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id FROM panel_subscribers')
    for row in c.fetchall():
        try:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(row[0], f"📌 {text}"),
                main_loop
            )
        except Exception as e:
            logging.error(f"Notify error: {e}")
    conn.close()

# ---------- АСИНХРОННЫЕ КУРСЫ ----------
async def get_usdt_rub():
    try:
        resp = await asyncio.to_thread(
            requests.get,
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "tether", "vs_currencies": "rub"},
            timeout=10
        )
        return float(resp.json()["tether"]["rub"])
    except:
        return 90.0
    
async def get_ltc_rub():
    try:
        resp = await asyncio.to_thread(
            requests.get,
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids":"litecoin","vs_currencies":"rub"}, timeout=10
        )
        return float(resp.json()["litecoin"]["rub"])
    except: return 3300.0

async def get_btc_rub():
    try:
        resp = await asyncio.to_thread(
            requests.get,
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids":"bitcoin","vs_currencies":"rub"}, timeout=10
        )
        return float(resp.json()["bitcoin"]["rub"])
    except: return 3000000.0

async def get_wallet_rate(crypto_type):
    for attempt in range(3):
        try:
            resp = await asyncio.to_thread(
                requests.get,
                f"{WALLET_BASE_URL}/api/v1/rates",
                headers={"X-Api-Key": WALLET_API_KEY},
                params={"coin": crypto_type},
                timeout=10
            )
            data = resp.json()
            if resp.status_code == 200 and 'RUB' in data:
                return float(data['RUB'])
        except Exception as e:
            logging.warning(f"Попытка {attempt+1}: ошибка получения курса {crypto_type} из кошелька: {e}")
            await asyncio.sleep(1)
    # Фолбэк на CoinGecko
    logging.warning(f"Не удалось получить курс {crypto_type} из кошелька, использую CoinGecko")
    if crypto_type == 'LTC':
        return await get_ltc_rub()
    elif crypto_type == 'BTC':
        return await get_btc_rub()
    elif crypto_type == 'USDT-TRC20':
        return await get_usdt_rub()
    else:
        return await get_ltc_rub() if crypto_type == 'LTC' else await get_btc_rub()

async def get_chart_data(crypto_type):
    try:
        coin_id = "litecoin" if crypto_type == "LTC" else "bitcoin"
        resp = await asyncio.to_thread(
            requests.get,
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
            params={"vs_currency":"rub","days":1}, timeout=10
        )
        return resp.json()["prices"]
    except: return None

def create_chart_image(prices, crypto_type):
    if not prices or len(prices) < 2: return None
    times = [datetime.fromtimestamp(p[0]/1000) for p in prices]
    values = [p[1] for p in prices]
    plt.figure(figsize=(10,5))
    plt.plot(times, values, color='#2962FF', linewidth=2)
    plt.fill_between(times, values, min(values), alpha=0.1, color='#2962FF')
    plt.title(f'Курс {crypto_type}/RUB за 24 часа', fontsize=14, fontweight='bold')
    plt.xlabel('Время'); plt.ylabel('Цена (RUB)'); plt.grid(True, alpha=0.3)
    plt.xticks(rotation=45); plt.tight_layout()
    plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:,.0f}'))
    current = values[-1]
    plt.axhline(y=current, color='red', linestyle='--', alpha=0.5, label=f'Текущая: {current:,.2f} RUB')
    plt.legend()
    buf = io.BytesIO(); plt.savefig(buf, format='png', dpi=100); buf.seek(0); plt.close()
    return buf

# ---------- ВЫБОР РЕКВИЗИТОВ ----------
async def get_next_active_payment():
    async with payment_lock:
        reload_payments()
        global payment_queue_index, payment_methods
        now = time.time()
        available = [pid for pid, p in list(payment_methods.items())
             if p['active'] and p.get('timeout_until',0) <= now
             and p.get('payments', 0) < p.get('max_payments', MAX_PAYMENTS_PER_CARD)]
        if not available: return None
        start = payment_queue_index % len(available)
        for i in range(len(available)):
            idx = (start + i) % len(available); pid = available[idx]
            payment_queue_index = (idx + 1) % len(available)
            return payment_methods[pid], pid
        return None

def record_payment(payment_id, rub_amount):
    p = payment_methods.get(payment_id)
    if p:
        p['payments'] += 1
        p['total_rub'] += rub_amount
        # timeout_until управляется отдельно (резервирование на PAYMENT_TIMEOUT)
        if p['payments'] >= p.get('max_payments', MAX_PAYMENTS_PER_CARD):
            p['active'] = False
        save_payment_method(payment_id)

def format_payment_info(p, payment_type='card'):
    if payment_type == 'sbp': return f"ФИО: {p['fio']}\nБанк: {p['bank']}\nСБП: {p['sbp']}"
    return f"ФИО: {p['fio']}\nБанк: {p['bank']}\nНомер карты: {p['card']}"

# ---------- АДМИН-КОМАНДЫ ----------
async def add_payment_method(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    args = update.message.text.replace('/add_payment','').strip()
    if not args: await update.message.reply_text("📝 <b>Формат:</b>\n<code>/add_payment ФИО; Банк; СБП; Номер карты</code>", parse_mode='HTML'); return
    parts = re.split(r'[;,]\s*', args)
    if len(parts) < 4: await update.message.reply_text("❌ Нужно 4 поля."); return
    global next_payment_id, payment_methods
    pid = next_payment_id; next_payment_id += 1
    payment_methods[pid] = {'fio': parts[0].strip(), 'bank': parts[1].strip(), 'sbp': parts[2].strip(), 'card': parts[3].strip(), 'active': True, 'payments': 0, 'total_rub': 0.0, 'timeout_until': 0, 'max_payments': 2}
    save_payment_method(pid)
    await update.message.reply_text(f"✅ Реквизиты #{pid} добавлены.")

async def payment_stats(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    if not payment_methods: await update.message.reply_text("📭 Нет реквизитов."); return
    now = time.time(); lines = ["📊 <b>Статистика реквизитов</b>\n"]
    for pid, p in list(payment_methods.items()):
        if not p['active']: status = "❌ Неактивна"
        elif p.get('timeout_until',0) > now: status = f"⏳ Таймаут ({int(p['timeout_until']-now)//60} мин)"
        else: status = "✅ Активна"
        lines.append(f"<b>ID {pid}</b> — {p['fio']}, {p['bank']}\n"
                     f"💳 {p['card']} | 📱 {p['sbp']}\n"
                     f"Платежей: {p['payments']} | Сумма: {p['total_rub']:.2f} RUB\n"
                     f"Интервал: {p.get('interval_minutes', 5)} мин\n"
                     f"Статус: {status}\n------")
    await update.message.reply_text("\n".join(lines), parse_mode='HTML')

async def activate_payment(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    try: pid = int(update.message.text.split()[1])
    except: await update.message.reply_text("Укажите ID: /activate_payment <id>"); return
    p = payment_methods.get(pid)
    if not p: await update.message.reply_text("❌ Не найдены."); return
    p['active'] = True; p['payments'] = 0; p['timeout_until'] = 0
    save_payment_method(pid)
    await update.message.reply_text(f"✅ Реквизиты #{pid} активированы.")

async def remove_payment(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    try: pid = int(update.message.text.split()[1])
    except: await update.message.reply_text("Укажите ID: /remove_payment <id>"); return
    if pid not in payment_methods: await update.message.reply_text("❌ Реквизиты с таким ID не найдены."); return
    del payment_methods[pid]
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute('DELETE FROM payment_methods WHERE id = ?', (pid,)); conn.commit(); conn.close()
    await update.message.reply_text(f"✅ Реквизиты #{pid} удалены.")

async def ban_user(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    try: parts = update.message.text.split(); user_id = int(parts[1]); hours = int(parts[2]) if len(parts)>2 else 24
    except: await update.message.reply_text("Формат: /ban <user_id> <часы>"); return
    banned_until = time.time() + hours * 3600
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO banned_users VALUES (?,?)', (user_id, banned_until)); conn.commit(); conn.close()
    await update.message.reply_text(f"🚫 Пользователь {user_id} забанен на {hours} часов.")

async def unban_user(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    try: user_id = int(update.message.text.split()[1])
    except: await update.message.reply_text("Формат: /unban <user_id>"); return
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,)); conn.commit(); conn.close()
    await update.message.reply_text(f"✅ Пользователь {user_id} разбанен.")

async def disable_bot(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    with open(BOT_DISABLED_FILE, 'w') as f: f.write('disabled')
    await update.message.reply_text("🔴 Бот отключен для пользователей.")

async def enable_bot(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    if os.path.exists(BOT_DISABLED_FILE): os.remove(BOT_DISABLED_FILE)
    await update.message.reply_text("🟢 Бот включен.")

async def start_day(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    now_iso = datetime.now().isoformat()
    c.execute('INSERT OR REPLACE INTO day_session (id, start_time) VALUES (1, ?)', (now_iso,))
    conn.commit(); conn.close()
    global daily_start_time; daily_start_time = datetime.now()
    await update.message.reply_text("📅 День начат. Статистика обнулена.")

async def end_day(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    global daily_start_time
    if not daily_start_time: await update.message.reply_text("❌ День не начат. Используйте /start_day."); return
    day_orders = []
    for o in orders.values():
        finished = o.get('finished_at')
        if finished:
            try:
                finished_dt = datetime.fromisoformat(finished)
                if finished_dt >= daily_start_time:
                    day_orders.append(o); continue
            except: pass
        created = o.get('created_at')
        if created and o['status'] != 'done':
            try:
                created_dt = datetime.fromisoformat(created)
                if created_dt >= daily_start_time:
                    day_orders.append(o)
            except: pass
    done_orders = [o for o in day_orders if o['status'] == 'done']
    cancelled_orders = [o for o in day_orders if o['status'] == 'cancelled']
    waiting = [o for o in day_orders if o['status'] in ('waiting_proof', 'payment_confirmed')]
    total_rub = sum(o['rub_amount'] for o in done_orders)
    sent_rub = sum(o['crypto_amount'] * o.get('market_rate', 0) for o in done_orders)
    profit = total_rub - sent_rub
    text = (f"📅 <b>Статистика за день</b>\nЗаявок всего: {len(day_orders)}\nВыполнено: {len(done_orders)}\nОтменено: {len(cancelled_orders)}\nВ обработке: {len(waiting)}\n\n💰 Приход: {total_rub:.2f} RUB\n📤 Отправлено: {sent_rub:.2f} RUB\n📈 Прибыль: {profit:.2f} RUB")
    await update.message.reply_text(text, parse_mode='HTML')
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute('DELETE FROM day_session WHERE id = 1')
    conn.commit(); conn.close()
    daily_start_time = None

async def status_day(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    if not daily_start_time: await update.message.reply_text("❌ День не начат."); return
    day_orders = []
    for o in orders.values():
        finished = o.get('finished_at')
        if finished:
            try:
                finished_dt = datetime.fromisoformat(finished)
                if finished_dt >= daily_start_time:
                    day_orders.append(o); continue
            except: pass
        created = o.get('created_at')
        if created and o['status'] != 'done':
            try:
                created_dt = datetime.fromisoformat(created)
                if created_dt >= daily_start_time:
                    day_orders.append(o)
            except: pass
    done_orders = [o for o in day_orders if o['status'] == 'done']
    cancelled_orders = [o for o in day_orders if o['status'] == 'cancelled']
    waiting = [o for o in day_orders if o['status'] in ('waiting_proof', 'payment_confirmed')]
    total_rub = sum(o['rub_amount'] for o in done_orders)
    sent_rub = sum(o['crypto_amount'] * o.get('market_rate', 0) for o in done_orders)
    profit = total_rub - sent_rub
    text = (f"📅 <b>Текущая статистика дня</b>\nЗаявок всего: {len(day_orders)}\nВыполнено: {len(done_orders)}\nОтменено: {len(cancelled_orders)}\nВ обработке: {len(waiting)}\n\n💰 Приход: {total_rub:.2f} RUB\n📤 Отправлено: {sent_rub:.2f} RUB\n📈 Прибыль: {profit:.2f} RUB")
    await update.message.reply_text(text, parse_mode='HTML')

async def report_command(update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    done_orders = [o for o in orders.values() if o['status'] == 'done']
    cancelled_orders = [o for o in orders.values() if o['status'] == 'cancelled']
    waiting = [o for o in orders.values() if o['status'] in ('waiting_proof', 'payment_confirmed')]
    total_rub = sum(o['rub_amount'] for o in done_orders)
    sent_rub = sum(o['crypto_amount'] * o.get('market_rate', 0) for o in done_orders)
    profit = total_rub - sent_rub
    text = (
        f"📊 <b>Статистика бота</b>\n"
        f"Всего заявок: {len(orders)}\n"
        f"Выполнено: {len(done_orders)}\n"
        f"Отменено: {len(cancelled_orders)}\n"
        f"В обработке: {len(waiting)}\n\n"
        f"💰 Приход: {total_rub:.2f} RUB\n"
        f"📤 Отправлено: {sent_rub:.2f} RUB\n"
        f"📈 Прибыль: {profit:.2f} RUB"
    )
    await update.message.reply_text(text, parse_mode='HTML')

async def show_balances(update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    usdt_balance = await get_coin_balance('USDT-TRC20')
    usdt_rub_rate = await get_wallet_rate('USDT-TRC20') or 90.0
    wallet_rub = usdt_balance * usdt_rub_rate
    my_share_total = get_accumulated_my_share()
    site_rub = wallet_rub - my_share_total
    commission = get_worker_commission_since_last_saturday()

    text = (
        f"💰 <b>Финансовый отчёт</b>\n\n"
        f"💳 Баланс на сайте: <b>{site_rub:.2f} ₽</b>\n"
        f"👛 Баланс на кошельке: <b>{wallet_rub:.2f} ₽</b>\n"
        f"📈 Сколько заработано: <b>{my_share_total:.2f} ₽</b>\n"
        f"💼 Комиссия: <b>{commission:.2f} ₽</b>\n\n"
        f"<i>Разница между кошельком и сайтом — Ваша доля.</i>"
    )
    await update.message.reply_text(text, parse_mode='HTML')


async def withdraw_my_share(update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text(
            "📝 Использование: <code>/withdraw_share АДРЕС_КОШЕЛЬКА</code>",
            parse_mode='HTML'
        )
        return
    address = args[1].strip()
    if not address:
        await update.message.reply_text("❌ Укажите адрес кошелька.")
        return

    # ---- Проверка TRC20-адреса ----
    if not re.match(r'^T[a-km-zA-HJ-NP-Z1-9]{33}$', address):
        await update.message.reply_text(
            "❌ Неверный формат адреса TRC20.\n\n"
            "⚠️ Если у вас ERC20-адрес (начинается с 0x) — он <b>не подойдёт</b>, "
            "USDT уйдёт в неправильную сеть.",
            parse_mode='HTML'
        )
        return

    # ── 1. Атомарно: снимок + пометка "выведено" в одной транзакции ──
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("""SELECT id, my_share FROM orders
                     WHERE my_share_withdrawn = 0 AND status = 'done'
                     AND payment_type != 'bestmerchant'
                     AND id >= 1092""")
        rows = c.fetchall()
        if not rows:
            conn.rollback()
            await update.message.reply_text("❌ Нет накопленной доли для вывода.")
            return

        ids_to_mark = [int(r[0]) for r in rows]
        my_share_total = sum(float(r[1] or 0) for r in rows)

        if my_share_total <= 0:
            conn.rollback()
            await update.message.reply_text("❌ Нет накопленной доли для вывода.")
            return

        placeholders = ','.join('?' * len(ids_to_mark))
        c.execute(
            f"UPDATE orders SET my_share_withdrawn = 1 WHERE id IN ({placeholders})",
            ids_to_mark
        )
        marked = c.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        logging.error(f"withdraw_my_share: ошибка транзакции: {e}")
        await update.message.reply_text("❌ Ошибка БД. Попробуйте позже.")
        return
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # ── 2. Синхронизируем память ──
    for oid in ids_to_mark:
        o = orders.get(oid)
        if o is not None:
            o['my_share_withdrawn'] = True

    # ── 3. Проверяем минимум USDT ──
    usdt_rub_rate = await get_wallet_rate('USDT-TRC20') or 90.0
    usdt_amount = my_share_total / usdt_rub_rate

    USDT_TRC20_MIN = 10.0
    if usdt_amount < USDT_TRC20_MIN:
        # Откатываем пометку — выводить нечего
        _rollback_my_share(ids_to_mark)
        await update.message.reply_text(
            f"❌ <b>Сумма слишком мала для вывода</b>\n\n"
            f"💰 Накоплено: <b>{my_share_total:.2f} ₽</b> ≈ {usdt_amount:.4f} USDT\n"
            f"📌 Минимум для TRC20: <b>{USDT_TRC20_MIN:.0f} USDT</b>",
            parse_mode='HTML'
        )
        return

    await update.message.reply_text(
        f"⏳ Вывожу <b>{usdt_amount:.4f} USDT</b> ({my_share_total:.2f} ₽) "
        f"на <code>{address}</code>...",
        parse_mode='HTML'
    )

    # ── 4. Реальная отправка ──
    withdrawal_id = await send_crypto('USDT-TRC20', address, usdt_amount)

    if not withdrawal_id:
        # Откат: снять пометку, чтобы админ мог попробовать снова
        _rollback_my_share(ids_to_mark)
        await update.message.reply_text(
            "❌ Ошибка создания вывода. Сумма возвращена в накопление. "
            "Проверьте баланс и адрес.",
            parse_mode='HTML'
        )
        return

    await update.message.reply_text(
        f"✅ Вывод создан!\n"
        f"💰 Сумма: <b>{usdt_amount:.4f} USDT</b>\n"
        f"📫 Адрес: <code>{address}</code>\n"
        f"🆔 ID вывода: <code>{withdrawal_id}</code>\n"
        f"📦 Учтено заявок: {marked}",
        parse_mode='HTML'
    )


def _rollback_my_share(ids):
    """Откатывает my_share_withdrawn=1 → 0 для перечисленных заявок."""
    if not ids:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        placeholders = ','.join('?' * len(ids))
        c.execute(
            f"UPDATE orders SET my_share_withdrawn = 0 WHERE id IN ({placeholders})",
            ids
        )
        conn.commit()
    except Exception as e:
        logging.error(f"_rollback_my_share: {e}")
    finally:
        conn.close()

    for oid in ids:
        o = orders.get(oid)
        if o is not None:
            o['my_share_withdrawn'] = False

async def info_command(update, context):
    text = (
        "📋 <b>Доступные команды</b>\n\n"
        "<b>Основные:</b>\n"
        "/start — Главное меню\n"
        "/ping — Проверка связи\n"
        "/info — Список команд\n\n"
        "<b>Администратор:</b>\n"
        "/add_payment ФИО; Банк; СБП; Карта — Добавить реквизиты\n"
        "/payment_stats — Статистика реквизитов\n"
        "/activate_payment &lt;id&gt; — Активировать реквизит\n"
        "/remove_payment &lt;id&gt; — Удалить реквизит\n"
        "/ban &lt;user_id&gt; &lt;часы&gt; — Забанить пользователя\n"
        "/unban &lt;user_id&gt; — Разбанить пользователя\n"
        "/report — Статистика обменов за всё время\n"
        "/history — История завершённых заявок\n"
        "/active — Активные заявки\n"
        "/disable — Отключить бота для пользователей\n"
        "/enable — Включить бота\n"
        "/start_day — Начать новый рабочий день\n"
        "/end_day — Завершить день и показать статистику\n"
        "/status_day — Текущая статистика дня\n"
        "/cancel_order &lt;id&gt; — Отменить активную заявку\n"
        "/addcoupon &lt;code&gt; — Добавить промокод\n"
        "/coupons — Список неиспользованных промокодов\n"
        "/broadcast &lt;текст&gt; — Рассылка всем пользователям\n"
        "/balances — Показать балансы сайта и кошелька\n"
        "/withdraw_share &lt;адрес&gt; — Вывести долю владельца\n\n"
        "💡 Кнопки в главном меню дублируют основные функции."
    )
    await update.message.reply_text(text, parse_mode='HTML')

async def admin_cancel_order(update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    try:
        order_id = int(update.message.text.split()[1])
    except:
        await update.message.reply_text("Укажите номер заявки: /cancel_order <id>")
        return

    order = orders.get(order_id)
    if not order:
        await update.message.reply_text(f"❌ Заявка #{order_id} не найдена.")
        return
    if order['status'] not in ('waiting_proof', 'payment_confirmed'):
        await update.message.reply_text(f"❌ Не в активном статусе.")
        return

    # Если это заказ Meridian — отменяем его в API

    order['status'] = 'cancelled'
    save_order(order_id)
    refund_bonus_for_order(order_id)
    add_cancelled(order['user_id'])
    pid = order.get('payment_id')
    if pid and pid in payment_methods:
        payment_methods[pid]['timeout_until'] = 0
        save_payment_method(pid)

    await update.message.reply_text(f"🚫 Заявка #{order_id} принудительно отменена.")

# ---------- ПРОМОКОДЫ ----------
async def add_coupon(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    try:
        full_code = update.message.text.split()[1].strip()
    except (IndexError, ValueError):
        await update.message.reply_text("Используйте: /addcoupon <код> или <код_число>")
        return
    if '_' in full_code:
        parts = full_code.rsplit('_', 1)
        code = parts[0]
        try:
            max_uses = int(parts[1])
            if max_uses < 1: raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Неверный формат. Используйте код_число (например, SALE50_10).")
            return
    else:
        code = full_code
        max_uses = 1
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT code FROM promocodes WHERE code = ?', (code,))
    if c.fetchone():
        await update.message.reply_text("⚠️ Такой промокод уже добавлен.")
    else:
        c.execute('INSERT INTO promocodes (code, max_uses, used_count) VALUES (?, ?, 0)', (code, max_uses))
        conn.commit()
        uses_msg = f"(можно использовать {max_uses} раз(а))" if max_uses > 1 else ""
        await update.message.reply_text(f"✅ Промокод {code} успешно добавлен в систему. {uses_msg}")
    conn.close()

async def list_coupons(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT code, max_uses, used_count FROM promocodes WHERE used_count < max_uses ORDER BY code')
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Неактивированных промокодов нет.")
    else:
        lines = ["Неактивированные промокоды:"]
        for code, max_u, used in rows:
            if max_u > 1:
                lines.append(f"{code} (использовано {used}/{max_u})")
            else:
                lines.append(code)
        await update.message.reply_text("\n".join(lines))

async def promo_start(update, context):
    if is_bot_disabled() and update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return ConversationHandler.END
    await update.message.reply_text("✏️ Введите ваш промокод:", reply_markup=cancel_keyboard)
    return PROMO_CODE

async def promo_code_entered(update, context):
    code = update.message.text.strip()
    user_id = update.message.from_user.id
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT max_uses, used_count FROM promocodes WHERE code = ?', (code,))
    row = c.fetchone()
    if not row:
        await update.message.reply_text("❌ Промокод недействителен или уже был использован.", reply_markup=main_keyboard)
        conn.close()
        return ConversationHandler.END
    max_uses, used_count = row
    if used_count >= max_uses:
        await update.message.reply_text("❌ Промокод недействителен или уже был использован.", reply_markup=main_keyboard)
        conn.close()
        return ConversationHandler.END
    c.execute('SELECT 1 FROM promocode_usage WHERE user_id = ? AND code = ?', (user_id, code))
    if c.fetchone():
        await update.message.reply_text("❌ Вы уже использовали этот промокод.", reply_markup=main_keyboard)
        conn.close()
        return ConversationHandler.END
    base_code = code.split('_')[0]
    match = re.search(r'(\d+)$', base_code)
    if not match:
        await update.message.reply_text("❌ Промокод не содержит суммы бонуса.", reply_markup=main_keyboard)
        conn.close()
        return ConversationHandler.END
    bonus_amount = int(match.group(1))
    user_bonuses[user_id] = user_bonuses.get(user_id, 0) + bonus_amount
    save_bonus(user_id)
    new_used = used_count + 1
    if new_used >= max_uses:
        c.execute('DELETE FROM promocodes WHERE code = ?', (code,))
        c.execute('DELETE FROM promocode_usage WHERE code = ?', (code,))
    else:
        c.execute('UPDATE promocodes SET used_count = ? WHERE code = ?', (new_used, code))
        c.execute('INSERT OR IGNORE INTO promocode_usage (user_id, code) VALUES (?, ?)', (user_id, code))
    conn.commit()
    conn.close()
    msg = (
        f"✅ Промокод активирован!\n"
        f"🎟 Начислено купонов: <b>{bonus_amount} RUB</b>\n"
        f"💎 Ваш баланс купонов: <b>{user_bonuses[user_id]:.0f} RUB</b>\n\n"
        f"<i>Купоны используются только для скидки на комиссию.</i>"
    )
    await update.message.reply_text(msg, parse_mode='HTML', reply_markup=main_keyboard)
    return ConversationHandler.END

async def cancel_promo(update, context):
    await update.message.reply_text("Действие отменено.", reply_markup=main_keyboard)
    return ConversationHandler.END

# ---------- БАЛАНС КОШЕЛЬКА ----------
async def get_wallet_balance_rub(crypto_type):
    for attempt in range(3):
        try:
            bal_resp = await asyncio.to_thread(
                requests.get,
                f"{WALLET_BASE_URL}/api/v1/balance",
                headers={"X-Api-Key": WALLET_API_KEY},
                params={"coin": crypto_type},
                timeout=10
            )
            bal_data = bal_resp.json()
            if bal_resp.status_code == 200 and 'balance' in bal_data:
                balance = float(bal_data['balance'])
                rate = await get_wallet_rate(crypto_type)
                if rate is None:
                    rate = await get_ltc_rub() if crypto_type == 'LTC' else await get_btc_rub()
                return balance * rate
        except Exception as e:
            logging.warning(f"Попытка {attempt+1}: ошибка получения баланса {crypto_type}: {e}")
            await asyncio.sleep(1)
    return None

# ---------- КОНВЕРТАЦИЯ USDT ----------
async def get_coin_balance(coin):
    try:
        resp = await asyncio.to_thread(requests.get,
            f"{WALLET_BASE_URL}/api/v1/balance",
            headers={"X-Api-Key": WALLET_API_KEY},
            params={"coin": coin}, timeout=10)
        data = resp.json()
        if resp.status_code == 200 and 'balance' in data:
            return float(data['balance'])
    except Exception as e:
        logging.error(f"Ошибка получения баланса {coin}: {e}")
    return 0.0

async def swap_usdt_to(coin, amount_coin):
    url = f"{WALLET_BASE_URL}/api/v1/exchange/create"
    headers = {"X-Api-Key": WALLET_API_KEY, "Content-Type": "application/json"}
    payload = {
        "from_coin": "USDT-TRC20",
        "to_coin": coin,
        "amount": f"{amount_coin:.8f}",
        "calc_action": "receive"
    }
    try:
        resp = await asyncio.to_thread(requests.post, url, json=payload, headers=headers, timeout=30)
        data = resp.json()
        if resp.status_code == 200 and "id" in data:
            logging.info(f"Обмен USDT->{coin} создан, swap_id={data['id']}, ожидаем {amount_coin:.6f} {coin}")
            return data["id"]
        else:
            msg = data.get('message', '')
            if 'Min. amount is' in msg:
                logging.warning(f"Обмен USDT->{coin} не создан: сумма {amount_coin:.6f} меньше минимальной ({msg})")
            else:
                logging.error(f"Ошибка создания обмена: {resp.status_code} {data}")
            return None
    except Exception as e:
        logging.error(f"Исключение при создании обмена: {e}")
        return None

async def poll_swap(swap_id):
    url = f"{WALLET_BASE_URL}/api/v1/exchange"
    headers = {"X-Api-Key": WALLET_API_KEY}
    params = {"id": swap_id}
    start = time.time()
    while time.time() - start < 600:
        await asyncio.sleep(10)
        try:
            resp = await asyncio.to_thread(requests.get, url, headers=headers, params=params, timeout=15)
            data = resp.json()
            if resp.status_code == 200:
                status = data.get("status")
                if status == "completed":
                    logging.info(f"Обмен {swap_id} завершён")
                    return True
                elif status in ("failed", "cancelled"):
                    logging.error(f"Обмен {swap_id} завершился со статусом {status}")
                    return False
        except Exception as e:
            logging.error(f"Ошибка опроса обмена {swap_id}: {e}")
    return False

# ---------- ПОКУПКА КРИПТОВАЛЮТ ----------
async def buy_ltc_start(update, context):
    user_id = update.message.from_user.id
    if is_bot_disabled() and user_id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return ConversationHandler.END
    banned, until = is_banned(user_id)
    if banned:
        await update.message.reply_text(f"🚫 Заблокированы на {int((until-time.time())/60)} мин.")
        return ConversationHandler.END

    crypto_type = 'LTC'
    context.user_data['crypto_type'] = crypto_type

    # Курс + балансы параллельно
    market_rate_task = asyncio.create_task(get_wallet_rate(crypto_type))
    balance_rub_task = asyncio.create_task(get_wallet_balance_rub(crypto_type))
    usdt_balance_task = asyncio.create_task(get_wallet_balance_rub('USDT-TRC20'))

    market_rate = await market_rate_task
    if market_rate is None:
        market_rate = await get_ltc_rub()
    balance_rub = await balance_rub_task
    usdt_balance_rub = await usdt_balance_task

    # Fallback, если не получилось запросить баланс
    if balance_rub is None:
        context.user_data['max_rub'] = MAX_RUB
        await update.message.reply_text(
            f"🪙 <b>Покупка LTC</b>\n\n"
            f"📊 Текущий курс: <b>{market_rate:.2f} RUB</b>\n"
            f"💰 Доступная сумма: от <b>{MIN_RUB_247} ₽</b> до <b>{MAX_RUB:,} ₽</b>\n\n"
            f"Введите сумму в рублях или криптовалюте:",
            parse_mode='HTML',
            reply_markup=cancel_keyboard
        )
        return LTC_AMOUNT

    reserve_coin = 60
    frozen = get_frozen_amount('LTC')
    available_coin = balance_rub - frozen - reserve_coin
    if available_coin < 0:
        available_coin = 0

    reserved_rub = get_reserved_rub()
    reserved_usdt_rub = get_reserved_usdt_rub()
    my_share_total = get_accumulated_my_share()

    if usdt_balance_rub is not None:
        max_from_usdt = max(0, (usdt_balance_rub - 250 - reserved_rub - reserved_usdt_rub - my_share_total) / 1.01)
        total_max = min(MAX_RUB, available_coin + max_from_usdt)
    else:
        total_max = min(MAX_RUB, available_coin)

    if total_max < MIN_RUB_247:
        await update.message.reply_text(
            "⛔️ <b>Обмен LTC временно недоступен</b>\n",
            parse_mode='HTML'
        )
        return ConversationHandler.END

    context.user_data['max_rub'] = int(total_max)

    await update.message.reply_text(
        f"🪙 <b>Покупка LTC</b>\n\n"
        f"📊 Текущий курс: <b>{market_rate:.2f} RUB</b>\n"
        f"💰 Доступная сумма: от <b>{MIN_RUB_247} ₽</b> до <b>{int(total_max):,} ₽</b>\n\n"
        f"Введите сумму в рублях или криптовалюте:",
        parse_mode='HTML',
        reply_markup=cancel_keyboard
    )
    return LTC_AMOUNT

async def buy_btc_start(update, context):
    user_id = update.message.from_user.id
    if is_bot_disabled() and user_id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return ConversationHandler.END
    banned, until = is_banned(user_id)
    if banned:
        await update.message.reply_text(f"🚫 Заблокированы на {int((until-time.time())/60)} мин.")
        return ConversationHandler.END

    crypto_type = 'BTC'
    context.user_data['crypto_type'] = crypto_type

    market_rate_task = asyncio.create_task(get_wallet_rate(crypto_type))
    balance_rub_task = asyncio.create_task(get_wallet_balance_rub(crypto_type))
    usdt_balance_task = asyncio.create_task(get_wallet_balance_rub('USDT-TRC20'))

    market_rate = await market_rate_task
    if market_rate is None:
        market_rate = await get_btc_rub()
    balance_rub = await balance_rub_task
    usdt_balance_rub = await usdt_balance_task

    if balance_rub is None:
        context.user_data['max_rub'] = MAX_RUB
        await update.message.reply_text(
            f"🪙 <b>Покупка BTC</b>\n\n"
            f"📊 Текущий курс: <b>{market_rate:.2f} RUB</b>\n"
            f"💰 Доступная сумма: от <b>{MIN_RUB_247} ₽</b> до <b>{MAX_RUB:,} ₽</b>\n\n"
            f"Введите сумму в рублях или криптовалюте:",
            parse_mode='HTML',
            reply_markup=cancel_keyboard
        )
        return BTC_AMOUNT

    reserve_coin = 200
    frozen = get_frozen_amount('BTC')
    available_coin = balance_rub - frozen - reserve_coin
    if available_coin < 0:
        available_coin = 0

    reserved_rub = get_reserved_rub()
    reserved_usdt_rub = get_reserved_usdt_rub()
    my_share_total = get_accumulated_my_share()

    if usdt_balance_rub is not None:
        max_from_usdt = max(0, (usdt_balance_rub - 250 - reserved_rub - reserved_usdt_rub - my_share_total) / 1.01)
        total_max = min(MAX_RUB, available_coin + max_from_usdt)
    else:
        total_max = min(MAX_RUB, available_coin)

    if total_max < MIN_RUB_247:
        await update.message.reply_text(
            "⛔️ <b>Обмен BTC временно недоступен</b>\n",
            parse_mode='HTML'
        )
        return ConversationHandler.END

    context.user_data['max_rub'] = int(total_max)

    await update.message.reply_text(
        f"🪙 <b>Покупка BTC</b>\n\n"
        f"📊 Текущий курс: <b>{market_rate:.2f} RUB</b>\n"
        f"💰 Доступная сумма: от <b>{MIN_RUB_247} ₽</b> до <b>{int(total_max):,} ₽</b>\n\n"
        f"Введите сумму в рублях или криптовалюте:",
        parse_mode='HTML',
        reply_markup=cancel_keyboard
    )
    return BTC_AMOUNT

async def parse_amount(update, context):
    text = update.message.text.strip()
    crypto_type = context.user_data['crypto_type']
    match = re.search(r'[\d.,]+', text)
    if not match: await update.message.reply_text("❌ Введите сумму числом."); return LTC_AMOUNT if crypto_type == 'LTC' else BTC_AMOUNT
    number_str = match.group()
    try:
        amount = float(number_str.replace(',', '.'))
        if amount <= 0: raise ValueError
    except: await update.message.reply_text("❌ Некорректное число."); return LTC_AMOUNT if crypto_type == 'LTC' else BTC_AMOUNT
    has_dot = '.' in number_str or ',' in number_str
    currency = crypto_type if has_dot else 'RUB'
    market_rate = await get_wallet_rate(crypto_type)
    if market_rate is None:
        market_rate = await get_ltc_rub() if crypto_type == 'LTC' else await get_btc_rub()
    rub_base = amount if currency == 'RUB' else amount * market_rate
    if rub_base < MIN_RUB_247:
        min_crypto = MIN_RUB_247 / market_rate
        await update.message.reply_text(f"❌ Минимальная сумма обмена: {MIN_RUB_247} RUB.\nЭто примерно {min_crypto:.4f} {crypto_type}.", parse_mode='HTML')
        return LTC_AMOUNT if crypto_type == 'LTC' else BTC_AMOUNT
    max_rub = context.user_data.get('max_rub', MAX_RUB)
    if rub_base > max_rub:
        await update.message.reply_text(
            "❌ <b>Сумма временно недоступна</b>\n\n"
            f"📌 Максимум: <b>{int(max_rub):,} ₽</b>",
            parse_mode='HTML',
            reply_markup=cancel_keyboard
        )
        return LTC_AMOUNT if crypto_type == 'LTC' else BTC_AMOUNT
    
    markup = get_markup(rub_base)
    if currency == 'RUB':
        crypto_to_get = amount / market_rate
        rub_to_pay = amount * markup
        crypto_str = f"{crypto_to_get:.6f}"
    else:
        crypto_to_get = amount
        rub_to_pay = amount * market_rate * markup
        crypto_str = number_str.replace(',', '.')
    rub_to_pay = round(rub_to_pay) + random.randint(1,3)
    commission = rub_to_pay - (crypto_to_get * market_rate)
    context.user_data.update(input_type=currency, input_amount=amount, rub_amount=rub_to_pay,
                             crypto_amount=crypto_to_get, crypto_str=crypto_str,
                             market_rate=market_rate, commission=commission, original_rub=rub_to_pay)
    
        # Вызываем выбор основного способа оплаты
    return await show_main_payment_choice(update, context)

async def create_bestmerchant_order(pay_method, amount_rub, partner_guid):
    url = f"{BESTMERCHANT_BASE_URL}/smart-orders"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BESTMERCHANT_TOKEN}"
    }
    payload = {
        "PayMethod": pay_method,
        "InTotal": amount_rub,
        "PartnerGuid": partner_guid
    }
    try:
        resp = await asyncio.to_thread(requests.post, url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            create_bestmerchant_order._last_error = ''
            return resp.json()
        else:
            # Пытаемся достать текст ошибки
            try:
                err_data = resp.json()
                err_msg = err_data.get('message') or err_data.get('error') or resp.text
            except Exception:
                err_msg = resp.text
            create_bestmerchant_order._last_error = err_msg or ''
            logging.error(f"BestMerchant create error: {resp.status_code} {err_msg}")
            return None
    except Exception as e:
        create_bestmerchant_order._last_error = str(e)
        logging.error(f"BestMerchant create exception: {e}")
        return None

    # ---------- NICEPAY API ----------
def _nicepay_post_sync(url, payload, timeout=30):
    """Синхронный POST — вызывается через asyncio.to_thread."""
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            data = {"status": "error", "data": {"message": f"HTTP {resp.status_code}: {resp.text[:200]}"}}
        return data
    except Exception as e:
        return {"status": "error", "data": {"message": str(e)}}


async def nicepay_create_payment(method, amount_rub, order_id, customer="user"):
    """Создаёт платёж NicePay через Payment Page.
    Возвращает dict {payment_id, amount, currency, link, expired} или None."""
    url = f"{NICEPAY_BASE_URL}/payment"
    payload = {
        "merchant_id": NICEPAY_MERCHANT_ID,
        "secret": NICEPAY_SECRET,
        "order_id": str(order_id),
        "customer": customer,
        "amount": int(round(amount_rub * 100)),   # копейки
        "currency": "RUB",
        "description": f"CryptoTRX order #{order_id}",
    }
    # method — необязательный; если указан, NicePay предвыберет его на странице
    if method:
        payload["method"] = method

    data = await asyncio.to_thread(_nicepay_post_sync, url, payload)

    if data.get("status") == "success":
        d = data.get("data") or {}
        logging.info(
            f"NicePay: платёж создан, payment_id={d.get('payment_id')}, "
            f"order_id={order_id}, amount={amount_rub}, link={d.get('link')}"
        )
        return d

    logging.error(f"NicePay create error: {data}")
    nicepay_create_payment._last_error = (
        (data.get("data") or {}).get("message") or data.get("status") or "unknown"
    )
    return None


async def nicepay_get_payment_info(payment_id):
    url = f"{NICEPAY_BASE_URL}/h2hPaymentInfo"
    payload = {
        "merchant_id": NICEPAY_MERCHANT_ID,
        "secret": NICEPAY_SECRET,
        "payment": payment_id,
    }
    data = await asyncio.to_thread(_nicepay_post_sync, url, payload)
    if data.get("status") == "success":
        return data.get("data")
    return None


async def nicepay_confirm_paid(payment_id):
    url = f"{NICEPAY_BASE_URL}/h2hConfirmPaid"
    payload = {
        "merchant_id": NICEPAY_MERCHANT_ID,
        "secret": NICEPAY_SECRET,
        "payment": payment_id,
    }
    data = await asyncio.to_thread(_nicepay_post_sync, url, payload)
    return data.get("status") == "success"


def nicepay_verify_hash(params):
    """Проверка подписи вебхука."""
    params = dict(params)  # копия
    received = params.pop("hash", None)
    if not received:
        return False
    items = sorted((k, str(v)) for k, v in params.items())
    joined = "{np}".join(v for _, v in items)
    joined += "{np}" + NICEPAY_SECRET
    calc = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return calc == received

async def get_bestmerchant_order(order_id):
    url = f"{BESTMERCHANT_BASE_URL}/smart-orders/{order_id}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BESTMERCHANT_TOKEN}"
    }
    try:
        resp = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        else:
            logging.error(f"BestMerchant get error: {resp.status_code} {resp.text}")
            return None
    except Exception as e:
        logging.error(f"BestMerchant get exception: {e}")
        return None

async def upload_receipt_to_bestmerchant(order_id, file_id):
    try:
        file = await app.bot.get_file(file_id)
        file_bytes = await file.download_as_bytearray()
    except Exception as e:
        logging.error(f"Ошибка получения файла чека для BestMerchant: {e}")
        return None
    url = f"{BESTMERCHANT_BASE_URL}/smart-orders/receipts/{order_id}"
    headers = {"Authorization": f"Bearer {BESTMERCHANT_TOKEN}"}
    files = {"file": ("receipt.pdf", bytes(file_bytes), "application/pdf")}
    try:
        resp = await asyncio.to_thread(requests.post, url, headers=headers, files=files, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("receiptUrl")
        else:
            logging.error(f"BestMerchant receipt upload error: {resp.status_code} {resp.text}")
            return None
    except Exception as e:
        logging.error(f"BestMerchant receipt exception: {e}")
        return None



async def show_main_payment_choice(update, context):
    if hasattr(update, 'message') and update.message:
        target = update.message
    else:
        target = update.callback_query.message

    # --- Защита от истёкших сессий ---
    crypto_type = context.user_data.get('crypto_type')
    crypto_amount = context.user_data.get('crypto_amount')
    market_rate = context.user_data.get('market_rate')

    if not crypto_type or not crypto_amount or not market_rate:
        msg = (
            "⚠️ <b>Сессия истекла</b>\n\n"
            "Начните заново: нажмите «LTC» или «BTC» в главном меню."
        )
        if hasattr(update, 'message') and update.message:
            await target.reply_text(msg, parse_mode='HTML')
        else:
            try:
                await target.edit_text(msg, parse_mode='HTML', reply_markup=None)
            except Exception:
                pass
        return ConversationHandler.END

    crypto_str = context.user_data.get('crypto_str', f"{crypto_amount:.6f}")
    base_rub = crypto_amount * market_rate

    # --- Проверка минимума ---
    if base_rub < MIN_RUB_TRANSGRAN:
        msg = "⛔️ <b>Обмен временно недоступен для данной суммы</b>\nМинимум: 1000 ₽."
        if hasattr(update, 'message') and update.message:
            await target.reply_text(msg, parse_mode='HTML')
        else:
            try:
                await target.edit_text(msg, parse_mode='HTML')
            except Exception:
                pass
        return ConversationHandler.END

    # --- Проверка максимума ---
    if base_rub > MAX_RUB:
        msg = (
            "❌ <b>Сумма временно недоступна</b>\n\n"
            f"📌 Максимум: <b>{MAX_RUB:,} ₽</b>"
        )
        if hasattr(update, 'message') and update.message:
            await target.reply_text(msg, parse_mode='HTML')
        else:
            try:
                await target.edit_text(msg, parse_mode='HTML')
            except Exception:
                pass
        return ConversationHandler.END

    # --- Комиссия и итоговая сумма ---
    commission_percent = get_nicepay_commission_percent(base_rub)
    total_rub = round(base_rub * (1 + commission_percent / 100))
    context.user_data['bestmerchant_total'] = total_rub

    # --- Сразу фиксируем намерение nicepay ---
    context.user_data['selected_payment_option'] = 'nicepay'
    context.user_data['payment_type'] = 'nicepay'

    # --- Кнопки методов оплаты ---
    buttons = []
    for code, label in NICEPAY_METHODS.items():
        buttons.append([InlineKeyboardButton(
            label, callback_data=f"nicepay_method_{code}"
        )])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    keyboard = InlineKeyboardMarkup(buttons)

    text = (
        f"💰 <b>Выбор способа оплаты</b>\n\n"
        f"🪙 Валюта: <b>{crypto_type}</b>\n"
        f"📊 Курс: <b>{market_rate:.2f} RUB</b>\n"
        f"💳 Сумма к оплате: <b>{total_rub:.0f} RUB</b>\n"
        f"🪙 Сумма к получению: <b>{crypto_str} {crypto_type}</b>\n\n"
        f"Выберите способ оплаты:"
    )

    if hasattr(update, 'message') and update.message:
        await target.reply_text(text, parse_mode='HTML', reply_markup=keyboard)
    else:
        try:
            await target.edit_text(text, parse_mode='HTML', reply_markup=keyboard)
        except Exception as e:
            logging.exception(f"show_main_payment_choice: edit_text упал: {e}")
            return ConversationHandler.END

    return LTC_SELECT_MAIN_METHOD if crypto_type == 'LTC' else BTC_SELECT_MAIN_METHOD

async def back_to_methods_callback(update, context):
    query = update.callback_query
    await query.answer()
    # Повторно показываем выбор метода оплаты
    return await show_main_payment_choice(update, context)

async def bm_retry_callback(update, context):
    """Повторная попытка создания BM-заявки с теми же данными."""
    query = update.callback_query

        # Проверяем, что данные сессии на месте
        # Обязательные поля для любой заявки
    required = ('bestmerchant_total', 'wallet',
                'crypto_amount', 'crypto_str', 'market_rate', 'crypto_type')
    missing = [k for k in required if k not in context.user_data]
    if missing:
        try:
            await query.answer("Сессия истекла, начните заново", show_alert=True)
        except Exception:
            pass
        try:
            await query.edit_message_text(
                "Начните заново: нажмите «LTC» или «BTC» в главном меню.",
                reply_markup=None
            )
        except Exception:
            pass
        return ConversationHandler.END

    # Куда возвращаться — в BM или NicePay
    payment_type = context.user_data.get('payment_type')
    if payment_type == 'nicepay':
        if 'nicepay_method' not in context.user_data:
            try:
                await query.answer("Сессия истекла", show_alert=True)
            except Exception:
                pass
            return ConversationHandler.END
        return await confirm_order_nicepay(update, context)

    if 'bm_method_chain' not in context.user_data:
        try:
            await query.answer("Сессия истекла", show_alert=True)
        except Exception:
            pass
        return ConversationHandler.END
    return await confirm_order_bestmerchant(update, context)


async def to_main_menu_callback(update, context):
    """Возврат в главное меню из инлайн-кнопки — удаляем сообщение кабинета."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    # --- Удаляем сообщение кабинета ---
    try:
        await query.message.delete()
    except Exception as e:
        # Если удалить нельзя (сообщение старше 48 ч, уже удалено и т.п.) —
        # хотя бы убираем inline-клавиатуру, чтобы не было путаницы
        logging.warning(f"to_main_menu_callback: не удалось удалить сообщение: {e}")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

    # --- Отправляем главное меню заново ---
    try:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "🏠 Главное меню CryptoTRX\n"
                "Выберите нужный раздел ниже.\n"
                "При любых непонятных ситуациях напишите /start."
            ),
            parse_mode='HTML',
            reply_markup=main_keyboard
        )
    except Exception as e:
        logging.error(f"Не удалось отправить главное меню: {e}")
    return ConversationHandler.END

async def bonus_callback(update, context):
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id; rub = context.user_data['rub_amount']; commission = context.user_data['commission']
    crypto_type = context.user_data['crypto_type']

    # Купоны + кошелёк
    available = get_total_discount_available(user_id)

    if query.data == "use_bonus":
        discount = min(available, max(0, commission - MIN_COMMISSION))
        if discount > 0:
            context.user_data['bonus_used'] = discount
            new_rub = rub - discount
            context.user_data['rub_amount'] = new_rub
            msg = f"🎁 Скидка: {discount:.0f} RUB\n💵 <b>К оплате:</b> <code>{new_rub:.0f}</code> RUB\n\n👇 Введите {crypto_type}-кошелёк:"
        else:
            context.user_data['bonus_used'] = 0
            msg = f"❌ Недостаточно средств для скидки.\n👇 Введите {crypto_type}-кошелёк:"
    else:
        context.user_data['bonus_used'] = 0
        msg = f"👇 Введите {crypto_type}-кошелёк:"
    await query.edit_message_text(msg, parse_mode='HTML')
    return LTC_WALLET if crypto_type == 'LTC' else BTC_WALLET

def is_btc_address(addr):
    if re.match(r'^1[a-km-zA-HJ-NP-Z1-9]{25,34}$', addr): return True
    if re.match(r'^3[a-km-zA-HJ-NP-Z1-9]{25,34}$', addr): return True
    if re.match(r'^bc1[a-zA-HJ-NP-Z0-9]{11,71}$', addr): return True
    if re.match(r'^bc1p[a-zA-HJ-NP-Z0-9]{11,71}$', addr): return True
    return False

def is_ltc_address(addr):
    if re.match(r'^L[a-km-zA-HJ-NP-Z1-9]{25,34}$', addr): return True
    if re.match(r'^[M3][a-km-zA-HJ-NP-Z1-9]{25,34}$', addr): return True
    if re.match(r'^ltc1[a-zA-HJ-NP-Z0-9]{11,71}$', addr): return True
    return False

async def wallet_entered(update, context):
    wallet = update.message.text.strip()
    crypto_type = context.user_data['crypto_type']

    # Валидация адреса
    if crypto_type == 'LTC':
        if is_btc_address(wallet) and not is_ltc_address(wallet):
            await update.message.reply_text("⚠️ Это BTC-кошелёк.")
            return LTC_WALLET
        if not is_ltc_address(wallet):
            await update.message.reply_text("⚠️ Неверный формат LTC-адреса.")
            return LTC_WALLET
    else:  # BTC
        if not is_btc_address(wallet):
            await update.message.reply_text("⚠️ Неверный формат BTC-адреса.")
            return BTC_WALLET

    context.user_data['wallet'] = wallet

    option = context.user_data.get('selected_payment_option')

    if option == 'ru_banks':
        # Подтверждение для РУ-метода
        payment_type = context.user_data.get('payment_type', 'card')
        pt = "Реквизиты" if payment_type == 'card' else "СБП"
        rub = context.user_data['rub_amount']
        crypto_str = context.user_data.get('crypto_str', f"{context.user_data['crypto_amount']:.6f}")
        msg_text = (
            f"📋 Подтверждение\n\n"
            f"🪙 К получению: {crypto_str} {crypto_type}\n"
            f"🔗 Кошелёк: {wallet}\n"
            f"💵 К оплате: {rub:.0f} RUB\n"
            f"💳 Метод: {pt}\n\n"
            f"⚠️ Важно!\n\n"
            f"После оплаты обязательно отправьте PDF-чек в этот чат.\n"
            f"Если вы:\n"
            f"• оплатите не ту сумму;\n"
            f"• оплатите не с того банка;\n"
            f"• просрочите оплату,\n"
            f"то заявка может быть обработана с большой задержкой, а в некоторых случаях возврат денежных средств будет невозможен.\n\n"
            f"Подтверждаете?"
        )
        await update.message.reply_text(
            msg_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_order")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ])
        )
        return LTC_CONFIRM if crypto_type == 'LTC' else BTC_CONFIRM

    elif option == 'nicepay':
        total_rub = context.user_data.get('bestmerchant_total', 0)
        method_code = context.user_data.get('nicepay_method', '')
        method_name = NICEPAY_METHODS.get(method_code, method_code)
        crypto_str = context.user_data.get('crypto_str', f"{context.user_data['crypto_amount']:.6f}")
        msg_text = (
            f"📋 <b>Подтверждение</b>\n\n"
            f"🌍 Метод: <b>{method_name}</b>\n"
            f"🪙 К получению: {crypto_str} {crypto_type}\n"
            f"🔗 Кошелёк: {wallet}\n"
            f"💵 К оплате: {total_rub:.0f} RUB\n\n"
            f"⚠️ Важно!\n\n"
            f"Если вы:\n"
            f"• оплатите не ту сумму;\n"
            f"• оплатите не с того банка;\n"
            f"• просрочите оплату,\n"
            f"то заявка может быть обработана с большой задержкой.\n\n"
            f"📄 После оплаты обязательно прикрепите чек в платёжной ссылке.\n\n"
            f"Подтверждаете?"
        )
        await update.message.reply_text(
            msg_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_order")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ])
        )
        return LTC_CONFIRM if crypto_type == 'LTC' else BTC_CONFIRM

    elif option == 'bestmerchant':
        total_rub = context.user_data.get('bestmerchant_total')
        method_desc = context.user_data.get('bm_method_label', 'Оплата 24/7')
        crypto_str = context.user_data.get('crypto_str', f"{context.user_data['crypto_amount']:.6f}")
        msg_text = (
            f"📋 Подтверждение\n\n"
            f"🏦 Метод: {method_desc}\n"
            f"🪙 К получению: {crypto_str} {crypto_type}\n"
            f"🔗 Кошелёк: {wallet}\n"
            f"💵 К оплате: {total_rub} RUB\n\n"
            f"⚠️ Важно!\n\n"
            f"• После оплаты нажмите кнопку «✅ Я оплатил».\n"
            f"Если вы:\n"
            f"• оплатите не ту сумму;\n"
            f"• оплатите не с того банка;\n"
            f"• просрочите оплату,\n"
            f"то заявка может быть обработана с большой задержкой, а в некоторых случаях возврат денежных средств будет невозможен.\n\n"
            f"Подтверждаете?"
        )
        await update.message.reply_text(
            msg_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_order")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ])
        )
        return LTC_CONFIRM if crypto_type == 'LTC' else BTC_CONFIRM

    else:
        await update.message.reply_text("❌ Способ оплаты не выбран.")
        return ConversationHandler.END

async def payment_method_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    crypto_type = context.user_data.get('crypto_type', 'LTC')

    # Заголовок / инфо-строка — ничего не делаем
    if data == "noop":
        return LTC_SELECT_MAIN_METHOD if crypto_type == 'LTC' else BTC_SELECT_MAIN_METHOD

    if data == "cancel":
        await query.edit_message_text("❌ Отменено.")
        return ConversationHandler.END

        # ===== BestMerchant =====
    if data.startswith("bm_"):
        context.user_data['selected_payment_option'] = 'bestmerchant'
        context.user_data['payment_type'] = 'bestmerchant'
        context.user_data['bonus_used'] = 0
        context.user_data['bm_partner_guid'] = f"cryptotrx_{uuid.uuid4()}"
        context.user_data['bm_creating'] = False
        context.user_data.pop('bestmerchant_paymethod', None)

        if data == "bm_ru_banks":
            # Цепочка попыток: SBP → CARD → QR
            context.user_data['bm_method_chain'] = ["SBP", "CARD", "QR"]
            context.user_data['bm_method_label'] = "Оплата на Ру банки"

        elif data == "bm_transgran":
            context.user_data['selected_payment_option'] = 'nicepay'
            context.user_data['payment_type'] = 'nicepay'

            method_buttons = []
            for code, label in NICEPAY_METHODS.items():
                method_buttons.append([InlineKeyboardButton(
                    label, callback_data=f"nicepay_method_{code}"
                )])
            method_buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

            total_rub = context.user_data.get('bestmerchant_total', 0)
            await query.edit_message_text(
                f"🌍 <b>Выберите способ оплаты</b>\n\n"
                f"💵 К оплате: <b>{total_rub:.0f} ₽</b>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(method_buttons),
            )
            return TRANSGRAN_METHOD

        # --- Старые callback'и для совместимости (если остались в истории) ---
        elif data == "bm_card":
            context.user_data['bm_method_chain'] = ["CARD"]
            context.user_data['bm_method_label'] = "Реквизиты 24/7"
        elif data == "bm_sbp":
            context.user_data['bm_method_chain'] = ["SBP"]
            context.user_data['bm_method_label'] = "СБП 24/7"
        elif data == "bm_qr":
            context.user_data['bm_method_chain'] = ["QR"]
            context.user_data['bm_method_label'] = "Оплата в приложении"
        elif data == "bm_sim":
            context.user_data['bm_method_chain'] = ["SIM"]
            context.user_data['bm_method_label'] = "Оплата SIM"
        elif data.startswith("bm_bank_"):
            context.user_data['bm_method_chain'] = [data.replace("bm_bank_", "")]
            context.user_data['bm_method_label'] = "Оплата банком"
        else:
            return ConversationHandler.END

        await query.edit_message_text(
            f"👇 Введите {crypto_type}-кошелёк:",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ])
        )
        return LTC_WALLET if crypto_type == 'LTC' else BTC_WALLET

    # ===== Старые РУ-методы (совместимость с историей) =====
    if data in ("ru_card", "ru_sbp"):
        if update.effective_user.id != ADMIN_ID and not is_ru_working_hours():
            await query.answer("⛔ РУ банки сейчас недоступны.", show_alert=True)
            return LTC_SELECT_MAIN_METHOD if crypto_type == 'LTC' else BTC_SELECT_MAIN_METHOD

        if context.user_data.get('rub_amount', 0) < 3000:
            await query.answer("РУ методы недоступны для суммы менее 3000 ₽", show_alert=True)
            return LTC_SELECT_MAIN_METHOD if crypto_type == 'LTC' else BTC_SELECT_MAIN_METHOD

        context.user_data['selected_payment_option'] = 'ru_banks'
        context.user_data['payment_type'] = 'card' if data == 'ru_card' else 'sbp'

        user_id = query.from_user.id
        bonus = get_total_discount_available(user_id)

        if bonus > 0:
            await query.edit_message_text(
                f"💰 Доступно для скидки: {bonus:.0f} RUB\nИспользовать скидку?",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Использовать", callback_data="use_bonus")],
                    [InlineKeyboardButton("❌ Без скидки", callback_data="no_bonus")]
                ])
            )
            return LTC_BONUS if crypto_type == 'LTC' else BTC_BONUS
        else:
            await query.edit_message_text(
                f"👇 Введите {crypto_type}-кошелёк:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
                ])
            )
            return LTC_WALLET if crypto_type == 'LTC' else BTC_WALLET

    return ConversationHandler.END

async def nicepay_method_callback(update, context):
    """Пользователь выбрал метод оплаты."""
    query = update.callback_query
    await query.answer()

    data = query.data
    method_code = data.replace("nicepay_method_", "")
    if method_code not in NICEPAY_METHODS:
        return ConversationHandler.END

    # --- Защита от истёкших сессий ---
    crypto_type = context.user_data.get('crypto_type')
    total_rub = context.user_data.get('bestmerchant_total')
    crypto_str = context.user_data.get('crypto_str', '')

    if not crypto_type or not total_rub or total_rub <= 0:
        try:
            await query.edit_message_text(
                "⚠️ <b>Сессия истекла</b>\n\n"
                "Начните заново: нажмите «LTC» или «BTC» в главном меню.",
                parse_mode='HTML',
                reply_markup=None
            )
        except Exception:
            pass
        return ConversationHandler.END

    # --- Фиксируем выбор ---
    context.user_data['nicepay_method'] = method_code
    context.user_data['selected_payment_option'] = 'nicepay'
    context.user_data['payment_type'] = 'nicepay'

    method_name = NICEPAY_METHODS[method_code]

    await query.edit_message_text(
        f"👛 <b>Ввод кошелька</b>\n\n"
        f"🪙 Валюта: <b>{crypto_type}</b>\n"
        f"💳 Способ: <b>{method_name}</b>\n"
        f"💰 К оплате: <b>{total_rub:.0f} RUB</b>\n"
        f"🪙 К получению: <b>{crypto_str} {crypto_type}</b>\n\n"
        f"👇 Укажите ваш {crypto_type}-кошелёк:",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ]),
    )
    return LTC_WALLET if crypto_type == 'LTC' else BTC_WALLET

async def cancel_callback(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Отменено.")
    return ConversationHandler.END

async def payment_timeout_task(order_id, bot):
    try:
        await asyncio.sleep(PAYMENT_TIMEOUT)
        order = orders.get(order_id)
        if not order:
            return
        if order.get('payment_type') in ('bestmerchant', 'nicepay'):
            return
        if order['status'] == 'waiting_proof' and not order.get('proof_file_id'):
            order['status'] = 'cancelled'
            save_order(order_id)
            refund_bonus_for_order(order_id)
            add_cancelled(order['user_id'])

            pid = order.get('payment_id')
            if pid and pid in payment_methods:
                payment_methods[pid]['timeout_until'] = 0
                save_payment_method(pid)

            # Редактируем исходное сообщение с реквизитами
            msg_id = order.get('user_message_id')
            chat_id = order.get('user_chat_id', order['user_id'])
            if msg_id:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=(
                            f"⏳ Срок заявки №{order_id} истёк.\n"
                            f"Если вы оплатили и заявка отменилась, "
                            f"напишите в поддержку: @TeRX_Supp"
                        ),
                        reply_markup=None
                    )
                except Exception as e:
                    logging.error(f"Не удалось отредактировать сообщение о таймауте: {e}")
                    # Фолбэк – отправляем новое сообщение
                    try:
                        await bot.send_message(
                            order['user_id'],
                            f"⏳ Срок заявки №{order_id} истёк.\n"
                            f"Если вы оплатили и заявка отменилась, "
                            f"напишите в поддержку: @TeRX_Supp",
                            reply_markup=main_keyboard
                        )
                    except Exception as e2:
                        logging.error(f"Не удалось уведомить пользователя об отмене: {e2}")
            else:
                try:
                    await bot.send_message(
                        order['user_id'],
                        f"⏳ Срок заявки №{order_id} истёк.\n"
                        f"Если вы оплатили и заявка отменилась, "
                        f"напишите в поддержку: @TeRX_Supp",
                        reply_markup=main_keyboard
                    )
                except Exception as e:
                    logging.error(f"Не удалось уведомить пользователя об отмене: {e}")

    except Exception as e:
        logging.error(f"Ошибка в payment_timeout_task для заявки #{order_id}: {e}")

async def nicepay_expire_task(order_id, bot, timeout_sec):
    """Ждёт до истечения ссылки оплаты; если заявка всё ещё waiting_proof — отменяет."""
    try:
        await asyncio.sleep(timeout_sec)
        order = orders.get(order_id)
        if not order or order['status'] != 'waiting_proof':
            return
        logging.info(
            f"NicePay #{order_id}: время оплаты истекло ({timeout_sec} сек), отменяем"
        )
        await cancel_order_by_id(order_id)
    except Exception as e:
        logging.error(f"Ошибка в nicepay_expire_task для заявки #{order_id}: {e}")

async def bm_payment_timeout_task(order_id, bot):
    try:
        await asyncio.sleep(PAYMENT_TIMEOUT)
        order = orders.get(order_id)
        if not order or order['status'] != 'waiting_proof':
            return

        bm_order = await get_bestmerchant_order(order.get('bm_order_id'))



        async def cancel_timeout():
            order['status'] = 'cancelled'
            save_order(order_id)
            refund_bonus_for_order(order_id)
            add_cancelled(order['user_id'])

            # Сообщение пользователю об истечении срока
            msg_id = order.get('user_message_id')
            chat_id = order.get('user_chat_id', order['user_id'])
            text_user = (
                f"⏳ Срок заявки №{order_id} истёк.\n"
                f"Если вы оплатили и заявка отменилась, "
                f"напишите в поддержку: @TeRX_Supp"
            )
            if msg_id:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=text_user,
                        reply_markup=None
                    )
                except Exception as e:
                    logging.error(f"Не удалось отредактировать сообщение о таймауте BM: {e}")
                    try:
                        await bot.send_message(order['user_id'], text_user, reply_markup=main_keyboard)
                    except Exception as e2:
                        logging.error(f"Не удалось уведомить пользователя об отмене BM: {e2}")
            else:
                try:
                    await bot.send_message(order['user_id'], text_user, reply_markup=main_keyboard)
                except Exception as e:
                    logging.error(f"Не удалось уведомить пользователя об отмене BM: {e}")

            # Редактируем сообщение администратора
            if order.get('admin_message_id'):
                crypto_str_display = order.get('crypto_str') or f"{order['crypto_amount']:.6f}"
                try:
                    await bot.edit_message_text(
                        chat_id=ADMIN_ID,
                        message_id=order['admin_message_id'],
                        text=(
                            f"❌ Заявка №{order_id} отменена\n"
                            f"К оплате: {order['rub_amount']:.2f} RUB\n"
                            f"Получение: {crypto_str_display} {order['crypto_type']}"
                        ),
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logging.error(f"Не удалось отредактировать сообщение админа при таймауте BM: {e}")

            notify_subscribers(order_id, f"❌ Заявка #{order_id} отменена по таймауту.")

        if bm_order is None:
            await cancel_timeout()
            return

        if bm_order.get('status') == 1:
            order['bm_status'] = 'success'
            save_order(order_id)
            await confirm_payment_by_id(order_id)
            return

        if bm_order.get('status') == 0:
            await cancel_timeout()
            return

        if bm_order.get('status') == 2:
            await cancel_order_by_id(order_id)
            return

    except Exception as e:
        logging.error(f"Ошибка в bm_payment_timeout_task для заявки #{order_id}: {e}")  

async def confirm_order(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        await query.edit_message_text("❌ Отменено.")
        return ConversationHandler.END

        # Если выбран BestMerchant
    if context.user_data.get('payment_type') == 'bestmerchant':
        return await confirm_order_bestmerchant(update, context)

    # Если выбран NicePay (Трансгран)
    if context.user_data.get('payment_type') == 'nicepay':
        return await confirm_order_nicepay(update, context)

    # ── 1. Сначала читаем все данные из user_data
    user = query.from_user
    crypto_type = context.user_data['crypto_type']
    rub = context.user_data['rub_amount']
    crypto_amount = context.user_data['crypto_amount']
    wallet = context.user_data['wallet']
    crypto_str = context.user_data.get('crypto_str', f"{crypto_amount:.6f}")

    # ── 2. Ищем реквизиты ДО резервирования
    info = await get_next_active_payment()
    if not info:
        await query.edit_message_text("❌ Нет доступных реквизитов.")
        return ConversationHandler.END

    payment_info, payment_id = info

        # ── 3. Резервируем скидку (после всех внешних проверок)
    bonus_used = context.user_data.get('bonus_used', 0)
    discount_from_coupons = 0.0
    discount_from_wallet = 0.0
    if bonus_used > 0:
        res = reserve_discount_for_order(user.id, bonus_used)
        if res is None:
            await query.edit_message_text(
                "❌ Недостаточно средств для скидки. Начните оформление заново.",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        discount_from_coupons, discount_from_wallet = res

    # ── 4. Резервируем реквизиты и создаём заявку
    payment_methods[payment_id]['timeout_until'] = time.time() + PAYMENT_TIMEOUT
    save_payment_method(payment_id)

    await query.edit_message_text("⏳ Создаю заявку…")

    async with order_id_lock:
        global next_order_id, orders
        order_id = next_order_id
        next_order_id += 1

    created_at = datetime.now().isoformat()
    fio, bank = payment_info['fio'], payment_info['bank']
    payment_type = context.user_data['payment_type']

    if payment_type == 'sbp':
        payment_block = f"👤 Получатель: {fio}\n🏦 Банк: {bank}\n📱 СБП: {payment_info['sbp']}"
    else:
        payment_block = f"👤 Получатель: {fio}\n🏦 Банк: {bank}\n💳 Номер карты: {payment_info['card']}"

    msg_text = (
        f"✅ Заявка #{order_id} создана!\n\n"
        f"💳 Реквизиты для оплаты\n\n"
        f"💰 Сумма: {rub:.0f} RUB\n"
        f"{payment_block}\n\n"
        f"⏳ Оплатите заявку в течение 15 минут.\n"
        f"📄 После оплаты обязательно отправьте PDF-чек в этот чат."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_order_{order_id}")],
        [InlineKeyboardButton("📎 Как отправить чек?", callback_data="proof_help")]
    ])
    msg = await query.edit_message_text(msg_text, parse_mode='HTML', reply_markup=kb)

    orders[order_id] = {
        'user_id': user.id,
        'username': user.username or user.full_name,
        'crypto_type': crypto_type,
        'rub_amount': rub,
        'crypto_amount': crypto_amount,
        'wallet': wallet,
        'payment_id': payment_id,
        'payment_type': payment_type,
        'payment_details': format_payment_info(payment_info, payment_type),
        'market_rate': context.user_data['market_rate'],
        'status': 'waiting_proof',
        'admin_message_id': None,
        'proof_file_id': None,
        'txid': None,
        'bonus_used': bonus_used,
        'user_message_id': msg.message_id,
        'user_chat_id': query.message.chat_id,
        'created_at': created_at,
        'crypto_str': crypto_str,
        'finished_at': None,
        'network_fee': 0.0,
        'bonus_awarded': False,
        'bonus_refunded': False,
        'discount_from_coupons': discount_from_coupons,
        'discount_from_wallet': discount_from_wallet,
    }
    save_order(order_id)
    context.user_data['order_id'] = order_id

    record_payment(payment_id, rub)

    asyncio.ensure_future(payment_timeout_task(order_id, context.bot))
    return LTC_PROOF if crypto_type == 'LTC' else BTC_PROOF

async def confirm_order_bestmerchant(update, context):
    query = update.callback_query

        # ===== ЗАЩИТА ОТ ДВОЙНОГО КЛИКА =====
    if context.user_data.get('bm_creating'):
        try:
            await query.answer(
                "⏳ Заявка уже создаётся.\n\n"
                "Подождите несколько секунд и проверьте чат — "
                "реквизиты придут отдельным сообщением.",
                show_alert=True
            )
        except Exception:
            pass
        return ConversationHandler.END

    context.user_data['bm_creating'] = True

    # ===== ГАРАНТИРОВАННЫЙ СБРОС ФЛАГА ЧЕРЕЗ finally =====
    try:
        # --- 1. Скрываем кнопки, сбрасываем callback ---
        try:
            await query.answer()
        except Exception:
            pass

        try:
            await query.edit_message_text("⏳ Создаю заявку…", reply_markup=None)
        except Exception as e:
            logging.debug(f"edit_message_text в confirm_order_bestmerchant: {e}")

            # --- 2. Читаем данные сессии ---
        crypto_type = context.user_data['crypto_type']
        total_rub = context.user_data['bestmerchant_total']
        crypto_amount = context.user_data['crypto_amount']
        wallet = context.user_data['wallet']
        crypto_str = context.user_data.get('crypto_str', f"{crypto_amount:.6f}")
        user = query.from_user
        bonus_used = context.user_data.get('bonus_used', 0)

        # Цепочка методов: SBP → CARD → QR (или один метод для старых callback'ов)
        pay_method_chain = context.user_data.get('bm_method_chain')
        if not pay_method_chain:
            # Fallback для старых сессий
            single = context.user_data.get('bestmerchant_paymethod')
            pay_method_chain = [single] if single else ['CARD']

        # --- 3. Пытаемся создать BM-заявку с fallback'ом по цепочке ---
        partner_guid = context.user_data.get('bm_partner_guid') or f"cryptotrx_{uuid.uuid4()}"

        bm_order = None
        used_pay_method = None
        last_error = ''

        for method in pay_method_chain:
            bm_order = await create_bestmerchant_order(method, total_rub, partner_guid)
            if bm_order:
                used_pay_method = method
                logging.info(
                    f"BestMerchant: заявка создана через {method} "
                    f"(цепочка: {pay_method_chain})"
                )
                break
            last_error = getattr(create_bestmerchant_order, '_last_error', '') or ''
            logging.warning(f"BestMerchant: {method} не сработал — {last_error}")

        if not bm_order:
            err_lower = last_error.lower()
            no_route = (
                'нет свободного маршрута' in err_lower
                or 'no route' in err_lower
                or 'no free route' in err_lower
                or 'нет доступных' in err_lower
            )

            if no_route:
                text = (
                    "⚠️ <b>Временно нет доступных реквизитов</b>\n\n"
                    "Попробуйте снова — нажмите кнопку ниже.\n"
                )
            else:
                text = (
                    "⚠️ <b>Не удалось создать заявку</b>\n\n"
                    "Нажмите «Попробовать снова». Если ошибка повторяется — "
                    "выберите другой метод оплаты."
                )

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="retry_bm_order")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="to_main_menu")],
            ])
            try:
                await query.edit_message_text(text, parse_mode='HTML', reply_markup=kb)
            except Exception as e:
                logging.error(f"Не удалось показать ошибку BM клиенту: {e}")
            # Флаг снимется в finally
            return ConversationHandler.END

        # --- 4. Разбираем ответ BM ---
        bm_order_id = bm_order.get("id")
        requisites = bm_order.get("requisites", "")
        need_receipt = bm_order.get("needReceipt", False)
        need_contact = bm_order.get("needContact", False)
        in_total = bm_order.get("inTotal", total_rub)

        # Определяем тип реквизита для метки
        req_type = bm_order.get("requisiteType")
        if req_type == 2:
            label = "💳 Номер карты"
        elif req_type == 1:
            label = "📱 Номер СБП"
        elif req_type == 4:
            label = "🔗 Платёжная ссылка"
        else:
            if requisites.startswith("http://") or requisites.startswith("https://"):
                label = "🔗 Платёжная ссылка"
            elif requisites.startswith("+"):
                label = "📱 Номер СБП"
            elif re.match(r'^\d{16,19}$', requisites):
                label = "💳 Номер карты"
            else:
                label = "🏦 Реквизиты"

        # --- 5. Присваиваем внутренний ID ---
        async with order_id_lock:
            global next_order_id, orders
            internal_order_id = next_order_id
            next_order_id += 1

        # --- 6. Сообщение №1: инструкция + кнопки ---
        msg_text = (
            f"🕓 Заявка №{internal_order_id} создана.\n\n"
            f"💰 Сумма к оплате: {in_total:.0f} RUB\n\n"
            f"Оплатите, используя реквизиты из следующего сообщения.\n\n"
            f"⚠️ После подтверждения оплаты вам придёт уведомление о поступлении платежа, "
            f"отправка криптовалюты произойдёт автоматически.\n\n"
            f"⏳ Оплатите в течение 15 минут."
        )

        kb_buttons = [
            [InlineKeyboardButton("✅ Я оплатил", callback_data=f"bm_paid_{internal_order_id}")],
            [InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_order_{internal_order_id}")],
        ]
        kb = InlineKeyboardMarkup(kb_buttons)

        msg = await query.edit_message_text(msg_text, parse_mode='HTML', reply_markup=kb)

        # --- 7. Сообщение №2: реквизиты ---
        bank_name = bm_order.get("bankName", "")
        message_text = f"{label}:\n{requisites}"
        if bank_name:
            message_text += f"\n🏦 Банк: {bank_name}"
        try:
            await context.bot.send_message(
                user.id,
                message_text,
                disable_web_page_preview=True
            )
        except Exception as e:
            logging.error(f"Не удалось отправить реквизиты клиенту {user.id}: {e}")
            # Продолжаем — заявка уже создана в BM, отменить нельзя

        # --- 8. Сохраняем заявку в БД ---
        orders[internal_order_id] = {
            'user_id': user.id,
            'username': user.username or user.full_name,
            'crypto_type': crypto_type,
            'rub_amount': in_total,
            'crypto_amount': crypto_amount,
            'wallet': wallet,
            'payment_id': None,
            'payment_type': 'bestmerchant',
            'payment_details': json.dumps({
                'bm_order_id': bm_order_id,
                'requisites': requisites,
                'pay_method': used_pay_method,
                'need_receipt': need_receipt,
                'need_contact': need_contact
            }),
            'market_rate': context.user_data['market_rate'],
            'status': 'waiting_proof',
            'admin_message_id': None,
            'proof_file_id': None,
            'txid': None,
            'bonus_used': bonus_used,
            'user_message_id': msg.message_id,
            'user_chat_id': query.message.chat_id,
            'created_at': datetime.now().isoformat(),
            'crypto_str': crypto_str,
            'finished_at': None,
            'network_fee': 0.0,
            'swap_id': None,
            'swap_amount_rub': 0.0,
            'bonus_awarded': False,
            'bm_order_id': bm_order_id,
            'bm_status': 'pending',
            'bm_requisites': requisites,
            'bm_pay_method': used_pay_method,
            'bm_need_receipt': need_receipt,
            'bm_need_contact': need_contact,
        }
        save_order(internal_order_id)
        context.user_data['order_id'] = internal_order_id

        # --- 9. Уведомляем админа ---
        try:
            admin_msg = await context.bot.send_message(
                ADMIN_ID,
                f"🆕 Заявка #{internal_order_id}\n"
                f"👤 Клиент: {user.username or user.full_name} (ID: {user.id})\n"
                f"💰 Сумма к оплате: {in_total:.2f} RUB\n"
                f"🪙 Сумма к отправке: {crypto_str} {crypto_type}",
                parse_mode='HTML'
            )
            orders[internal_order_id]['admin_message_id'] = admin_msg.message_id
            save_order(internal_order_id)
        except Exception as e:
            logging.error(f"Не удалось уведомить админа о заявке #{internal_order_id}: {e}")

        # --- 10. Таймер на 15 минут для BM-заявки ---
        asyncio.ensure_future(bm_payment_timeout_task(internal_order_id, context.bot))

        # --- 11. Успех — очищаем retry-данные ---
        context.user_data.pop('bm_partner_guid', None)
        context.user_data['bm_creating'] = False

        return LTC_PROOF if crypto_type == 'LTC' else BTC_PROOF

    finally:
        # Гарантированный сброс флага даже при exception.
        # bm_partner_guid НЕ удаляем — нужен для retry.
        context.user_data['bm_creating'] = False

async def confirm_order_nicepay(update, context):
    """Создаёт заявку через NicePay Payment Page (ссылка на оплату)."""
    query = update.callback_query
    await query.answer()

    crypto_type = context.user_data.get('crypto_type')
    total_rub = context.user_data.get('bestmerchant_total')
    crypto_amount = context.user_data.get('crypto_amount')
    wallet = context.user_data.get('wallet')
    crypto_str = context.user_data.get('crypto_str', f"{crypto_amount:.6f}")
    method_code = context.user_data.get('nicepay_method') or None
    user = query.from_user
    bonus_used = context.user_data.get('bonus_used', 0)

    try:
        await query.edit_message_text("⏳ Создаю заявку…", reply_markup=None)
    except Exception:
        pass

    # --- Резервируем скидку ---
    discount_from_coupons = 0.0
    discount_from_wallet = 0.0
    if bonus_used > 0:
        res = reserve_discount_for_order(user.id, bonus_used)
        if res is None:
            await query.edit_message_text(
                "❌ Недостаточно средств для скидки. Начните заново.",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        discount_from_coupons, discount_from_wallet = res

    # --- Внутренний ID заявки ---
    async with order_id_lock:
        global next_order_id, orders
        internal_order_id = next_order_id
        next_order_id += 1

    # --- Создаём платёж на NicePay ---
    customer = user.username or user.full_name or f"user_{user.id}"
    np_data = await nicepay_create_payment(
        method=method_code,
        amount_rub=total_rub,
        order_id=internal_order_id,
        customer=customer,
    )

    if not np_data:
        # Откат скидки
        if discount_from_coupons > 0:
            user_bonuses[user.id] = user_bonuses.get(user.id, 0) + discount_from_coupons
            save_bonus(user.id)
        if discount_from_wallet > 0:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('''INSERT INTO wallet_withdrawals
                         (user_id, amount, kind, created_at)
                         VALUES (?, ?, 'refund', ?)''',
                      (user.id, -discount_from_wallet, datetime.now().isoformat()))
            conn.commit(); conn.close()

        err = getattr(nicepay_create_payment, '_last_error', '') or ''
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Попробовать снова", callback_data="retry_bm_order")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="to_main_menu")],
        ])
        try:
            await query.edit_message_text(
                f"⚠️ <b>Не удалось создать заявку</b>\n\n{err}\n\n"
                f"Нажмите «Попробовать снова».",
                parse_mode='HTML', reply_markup=kb
            )
        except Exception:
            pass
        return ConversationHandler.END

    payment_id = np_data.get("payment_id")
    payment_link = np_data.get("link")
    expired_ts = np_data.get("expired")
    # NicePay возвращает amount в копейках
    amount_kopecks = np_data.get("amount") or int(total_rub * 100)
    amount_rub_req = amount_kopecks / 100

    # --- Время истечения ссылки ---
    if expired_ts:
        try:
            expired_dt = datetime.fromtimestamp(int(expired_ts))
            # МСК = UTC + 3
            expired_dt_msk = expired_dt + timedelta(hours=3)
            expired_str = expired_dt_msk.strftime('%H:%M')
        except Exception:
            expired_str = "—"
    else:
        expired_str = "—"

    # --- Готовим текст сообщения клиенту ---
    msg_text = (
        f"✅ <b>Заявка #{internal_order_id} создана</b>\n\n"
        f"💵 Сумма к оплате: <b>{amount_rub_req:.2f} ₽</b>\n"
        f"🪙 К получению: <b>{crypto_str} {crypto_type}</b>\n"
        f"🔗 Ваш кошелёк: <code>{wallet}</code>\n\n"
        f"👇 Нажмите кнопку ниже, чтобы перейти на страницу оплаты.\n"
        f"Там выберите удобный способ оплаты и совершите перевод.\n\n"
        f"⏳ Ссылка действительна до <b>{expired_str}</b> МСК.\n\n"
        f"⚠️ После подтверждения оплаты вам придёт уведомление о поступлении платежа, "
        f"отправка произойдёт автоматически."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Перейти к оплате", url=payment_link)],
        [InlineKeyboardButton("❌ Отменить заявку", callback_data=f"cancel_order_{internal_order_id}")],
    ])

    # ═══════════════════════════════════════════════════════════════════
    # ВАЖНО: сначала сохраняем заявку в БД.
    # Платёж на стороне NicePay уже создан, поэтому мы ОБЯЗАНЫ
    # иметь заявку в системе, иначе вебхук не найдёт её при оплате.
    # ═══════════════════════════════════════════════════════════════════
    orders[internal_order_id] = {
        'user_id': user.id,
        'username': user.username or user.full_name,
        'crypto_type': crypto_type,
        'rub_amount': amount_rub_req,
        'crypto_amount': crypto_amount,
        'wallet': wallet,
        'payment_id': None,
        'payment_type': 'nicepay',
        'payment_details': json.dumps({
            'nicepay_payment_id': payment_id,
            'method': method_code,
            'link': payment_link,
            'expired': expired_ts,
        }),
        'market_rate': context.user_data['market_rate'],
        'status': 'waiting_proof',
        'admin_message_id': None,
        'proof_file_id': None,
        'txid': None,
        'bonus_used': bonus_used,
        'user_message_id': None,          # заполним после отправки
        'user_chat_id': query.message.chat_id,
        'created_at': datetime.now().isoformat(),
        'crypto_str': crypto_str,
        'finished_at': None,
        'network_fee': 0.0,
        'swap_id': None,
        'swap_amount_rub': 0.0,
        'bonus_awarded': False,
        'bonus_refunded': False,
        'discount_from_coupons': discount_from_coupons,
        'discount_from_wallet': discount_from_wallet,
        'bm_order_id': payment_id,   # используем это поле как NicePay payment_id
        'bm_status': 'pending',
        'bm_pay_method': method_code,
    }
    save_order(internal_order_id)
    context.user_data['order_id'] = internal_order_id

    # ═══════════════════════════════════════════════════════════════════
    # Теперь пытаемся показать клиенту сообщение с кнопкой оплаты.
    # Если edit_message_text упадёт — отправим новое сообщение.
    # Если и это упадёт — уведомим админа.
    # ═══════════════════════════════════════════════════════════════════
    try:
        msg = await query.edit_message_text(msg_text, parse_mode='HTML', reply_markup=kb)
        orders[internal_order_id]['user_message_id'] = msg.message_id
        save_order(internal_order_id)
    except Exception as e:
        logging.exception(
            f"NicePay #{internal_order_id}: не удалось отредактировать сообщение "
            f"клиента через query.edit_message_text: {e}"
        )
        # Fallback 1: отправить новое сообщение клиенту
        try:
            fallback_msg = await context.bot.send_message(
                chat_id=user.id,
                text=msg_text,
                parse_mode='HTML',
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            orders[internal_order_id]['user_message_id'] = fallback_msg.message_id
            save_order(internal_order_id)
        except Exception as e2:
            logging.exception(
                f"NicePay #{internal_order_id}: fallback send_message тоже упал: {e2}"
            )
            # Fallback 2: сообщаем админу, что клиент не получил ссылку
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>NicePay #{internal_order_id}</b>: не удалось отправить клиенту ссылку на оплату.\n\n"
                    f"👤 Клиент: {user.username or user.full_name} (ID: {user.id})\n"
                    f"💰 Сумма: {amount_rub_req:.2f} ₽\n"
                    f"🪙 К отправке: {crypto_str} {crypto_type}\n"
                    f"🔗 Ссылка: {payment_link}\n\n"
                    f"Отправьте ссылку клиенту вручную или отмените заявку.",
                    parse_mode='HTML',
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════════
    # Уведомляем админа о новой заявке
    # ═══════════════════════════════════════════════════════════════════
    try:
        admin_msg = await context.bot.send_message(
            ADMIN_ID,
            f"🆕 Заявка #{internal_order_id} (NicePay Payment Page)\n"
            f"👤 Клиент: {user.username or user.full_name} (ID: {user.id})\n"
            f"💰 Сумма к оплате: {amount_rub_req:.2f} RUB\n"
            f"🏦 Метод: {method_code or 'авто'}\n"
            f"🪙 К отправке: {crypto_str} {crypto_type}\n"
            f"🆔 NicePay: {payment_id}\n"
            f"🔗 {payment_link}",
            parse_mode='HTML',
            disable_web_page_preview=True,
        )
        orders[internal_order_id]['admin_message_id'] = admin_msg.message_id
        save_order(internal_order_id)
    except Exception as e:
        logging.error(f"Не удалось уведомить админа о заявке #{internal_order_id}: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # Таймер автоотмены
    # Приоритет — время expired от NicePay; fallback — PAYMENT_TIMEOUT
    # ═══════════════════════════════════════════════════════════════════
    timeout_sec = PAYMENT_TIMEOUT
    if expired_ts:
        try:
            timeout_sec = max(60, int(expired_ts) - int(time.time()))
        except Exception:
            pass
    logging.info(
        f"NicePay #{internal_order_id}: ссылка активна {timeout_sec} сек, "
        f"по истечении заявка будет отменена автоматически"
    )
    asyncio.ensure_future(
        nicepay_expire_task(internal_order_id, context.bot, timeout_sec)
    )

    context.user_data.pop('nicepay_method', None)
    context.user_data.pop('bm_method_chain', None)
    context.user_data.pop('bestmerchant_paymethod', None)

    return LTC_PROOF if crypto_type == 'LTC' else BTC_PROOF

async def bm_paid_callback(update, context):
    query = update.callback_query
    try:
        await query.answer()
    except telegram.error.BadRequest:
        # старый callback, ничего страшного
        pass

    try:
        order_id = int(query.data.split('_')[2])
    except (IndexError, ValueError):
        return

    order = orders.get(order_id)
    if not order or order['user_id'] != query.from_user.id:
        await safe_edit_text(query, "❌ Заявка не найдена.", reply_markup=None)
        return

    # Заявка уже ушла в обработку/завершена — ничего не делаем повторно
    if order['status'] != 'waiting_proof':
        await safe_edit_text(query, "❌ Заявка уже обработана.", reply_markup=None)
        return

    # 1. Вебхук уже пометил как success
    if order.get('bm_status') == 'success':
        await safe_edit_text(
            query,
            f"✅ Заявка №{order_id} обрабатывается…",
            reply_markup=None,
        )
        # confirm_payment_by_id сам атомарен по статусу,
        # но не даём запустить его дважды параллельно
        if not order.get('_bm_confirm_started'):
            order['_bm_confirm_started'] = True
            await confirm_payment_by_id(order_id)
        return

    # 2. Флага нет — запускаем фоновую проверку (вебхук + API), но только один раз
    if not context.user_data.get(f'bm_wait_started_{order_id}'):
        context.user_data[f'bm_wait_started_{order_id}'] = True
        asyncio.create_task(wait_for_bm_success(order_id))

    await safe_edit_text(
        query,
        f"⏳ Ожидаем подтверждение оплаты для заявки №{order_id}.\n"
        f"Как только оплата поступит, заявка будет обработана автоматически.",
        parse_mode='HTML',
        reply_markup=None,
    )

async def nicepay_paid_callback(update, context):
    """Клиент нажал «Я оплатил» в NicePay-заявке."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    try:
        order_id = int(query.data.split('_')[2])
    except (IndexError, ValueError):
        return

    order = orders.get(order_id)
    if not order or order['user_id'] != query.from_user.id:
        await safe_edit_text(query, "❌ Заявка не найдена.", reply_markup=None)
        return

    if order['status'] != 'waiting_proof':
        await safe_edit_text(query, "❌ Заявка уже обработана.", reply_markup=None)
        return

    # Отправляем подтверждение в NicePay
    payment_id = order.get('bm_order_id')
    if payment_id:
        ok = await nicepay_confirm_paid(payment_id)
        if not ok:
            logging.warning(f"NicePay: h2hConfirmPaid для {payment_id} не удалось")

    crypto_str = order.get('crypto_str', f"{order['crypto_amount']:.6f}")
    crypto_type = order['crypto_type']

    await safe_edit_text(
        query,
        f"⏳ <b>Оплата в пути!</b>\n\n"
        f"Ожидайте поступления средств.\n"
        f"После подтверждения платежа мы автоматически обработаем "
        f"заявку и отправим <b>{crypto_str} {crypto_type}</b> "
        f"на указанный вами кошелёк.\n\n"
        f"🔔 Уведомление придёт в этот чат.",
        parse_mode='HTML',
        reply_markup=None,
    )


async def poll_nicepay_status(bot, order_id, payment_id, timeout_sec=3600):
    """Опрашивает статус платежа NicePay до фактического финала.
    Внутренний таймаут — только для выхода из цикла, саму заявку НЕ отменяем
    (отмена придёт от NicePay через webhook / статус 82/92/95/96)."""
    deadline = time.time() + timeout_sec
    last_logged = None

    while time.time() < deadline:
        await asyncio.sleep(10)

        order = orders.get(order_id)
        if not order or order['status'] not in ('waiting_proof', 'payment_confirmed'):
            return

        info = await nicepay_get_payment_info(payment_id)
        if not info:
            continue

        status = info.get('status')
        if status != last_logged:
            logging.info(f"NicePay #{payment_id} (order #{order_id}): status={status}")
            last_logged = status

        if status == 5:
            lock = get_order_lock(order_id)
            async with lock:
                if order['status'] != 'waiting_proof':
                    return
                order['status'] = 'payment_confirmed'
                save_order(order_id)
            await process_confirmed_order(bot, order_id)
            return

        if status in (82, 92, 95, 96):
            await cancel_order_by_id(order_id)
            return

    # ← УБРАЛИ блок «Таймаут — отменяем»
    logging.info(
        f"NicePay #{payment_id}: внутренний поллинг остановлен, "
        f"заявка #{order_id} остаётся в ожидании webhook"
    )

async def _nicepay_webhook_success(order_id, payment_id):
    """Обработка успешного вебхука NicePay."""
    order = orders.get(order_id)
    if not order or order['status'] != 'waiting_proof':
        return

    lock = get_order_lock(order_id)
    async with lock:
        if order['status'] != 'waiting_proof':
            return
        order['status'] = 'payment_confirmed'
        save_order(order_id)

    logging.info(f"NicePay webhook: заявка #{order_id} → payment_confirmed")
    await process_confirmed_order(app.bot, order_id)

async def process_confirmed_order(bot, order_id):
    """Логика после подтверждения оплаты NicePay.
    Статус заявки уже payment_confirmed."""
    order = orders.get(order_id)
    if not order:
        return
    asyncio.ensure_future(
        send_payment_confirmed_message(bot, order_id, order)
    )
    await _do_fulfillment(bot, order_id)

async def wait_for_bm_success(order_id):
    order = orders.get(order_id)
    if not order:
        return
    deadline = time.time() + PAYMENT_TIMEOUT
    while time.time() < deadline:
        if order['status'] != 'waiting_proof':
            return

        # Сначала проверяем флаг от вебхука
        if order.get('bm_status') == 'success':
            if not order.get('_bm_confirm_started'):
                order['_bm_confirm_started'] = True
                await confirm_payment_by_id(order_id)
            return

        # Если флага нет – опрашиваем API BestMerchant
        bm_order = await get_bestmerchant_order(order['bm_order_id'])
        if bm_order:
            if bm_order.get("status") == 1:
                order['bm_status'] = 'success'
                save_order(order_id)
                if not order.get('_bm_confirm_started'):
                    order['_bm_confirm_started'] = True
                    await confirm_payment_by_id(order_id)
                return
            elif bm_order.get("status") == 2:
                await cancel_order_by_id(order_id)
                return

        await asyncio.sleep(5)

    # Таймаут
    if order.get('status') == 'waiting_proof' and not order.get('proof_file_id'):
        await cancel_order_by_id(order_id)

async def proof_handler(update, context):
    order_id = context.user_data.get('order_id')
    if not order_id or order_id not in orders:
        await update.message.reply_text("❌ Заявка не найдена.")
        return ConversationHandler.END

    order = orders[order_id]
    if order['status'] != 'waiting_proof':
        await update.message.reply_text("❌ Уже обработана.")
        return ConversationHandler.END

    if order.get('proof_file_id'):
        await update.message.reply_text(
            "❌ Чек уже прикреплён.",
            reply_markup=main_keyboard
        )
        return ConversationHandler.END

    if order.get('payment_type') in ('bestmerchant', 'nicepay'):
        await update.message.reply_text(
            "📄 PDF-чек не требуется для этого метода оплаты. "
            "Ожидайте автоматическое подтверждение после оплаты."
        )
        return ConversationHandler.END
    
    if not update.message.document or update.message.document.mime_type != 'application/pdf':
        await update.message.reply_text("⚠️ Только PDF.")
        return LTC_PROOF if order['crypto_type'] == 'LTC' else BTC_PROOF

    order['proof_file_id'] = update.message.document.file_id
    save_order(order_id)

    if order.get('user_message_id'):
        rub_amount = order['rub_amount']
        crypto_type = order['crypto_type']
        crypto_str = order.get('crypto_str', f"{order['crypto_amount']:.6f}")
        new_text = (
            f"Заявка: <b>#{order_id}</b>\n"
            f"💵 Оплата: <code>{rub_amount:.0f} RUB</code>\n"
            f"🪙 К получению: <code>{crypto_str}</code> {crypto_type}\n\n"
            f"⏳ Ожидайте подтверждения — обработка занимает от 1 до 15 минут."
        )
        try:
            await context.bot.edit_message_text(
                chat_id=order.get('user_chat_id', order['user_id']),
                message_id=order['user_message_id'],
                text=new_text,
                reply_markup=None,
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Не удалось отредактировать сообщение о чеке: {e}")

    crypto_str = order.get('crypto_str', f"{order['crypto_amount']:.6f}")
    admin_text = (
    f"🆕 Заявка #{order_id}\n"
    f"👤 {order['username']} (ID: {order['user_id']})\n"
    f"🪙 {crypto_str} {order['crypto_type']}\n"
    f"Кошелёк: {order['wallet']}\n"
    f"Оплатил: {order['rub_amount']:.2f} RUB\n"
    f"Реквизиты: {order['payment_details']}"
    )
    admin_msg = await context.bot.send_document(
        ADMIN_ID,
        order['proof_file_id'],
        caption=admin_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"confirm_payment_{order_id}")],
            [InlineKeyboardButton("📝 TxID", callback_data=f"send_txid_{order_id}")]
        ])
    )
    order['admin_message_id'] = admin_msg.message_id
    save_order(order_id)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id FROM panel_subscribers')
    for row in c.fetchall():
        try:
            await context.bot.send_message(
                row[0],
                f"📌 Заявка #{order_id} ожидает подтверждения оплаты."
            )
        except:
            pass
    conn.close()

    return ConversationHandler.END

async def cancel_order(update, context):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split('_')[2])
    order = orders.get(order_id)
    if not order or query.from_user.id != order['user_id']:
        return
    if order.get('proof_file_id'):
        await query.answer("Чек уже отправлен, отмена невозможна.", show_alert=True)
        return
    if order['status'] in ('waiting_proof', 'payment_confirmed'):

        order['status'] = 'cancelled'
        save_order(order_id)
        refund_bonus_for_order(order_id)
        banned, _ = add_cancelled(order['user_id'])

        pid = order.get('payment_id')
        if pid and pid in payment_methods:
            payment_methods[pid]['timeout_until'] = 0
            save_payment_method(pid)

        # --- Обновляем сообщение админа ---
        if order.get('admin_message_id'):
            if order.get('payment_type') in ('bestmerchant', 'nicepay'):
                try:
                    crypto_str_display = order.get('crypto_str') or f"{order['crypto_amount']:.6f}"
                    await context.bot.edit_message_text(
                        chat_id=ADMIN_ID,
                        message_id=order['admin_message_id'],
                        text=(
                            f"❌ Заявка №{order_id} отменена\n"
                            f"Причина: клиент отменил заявку\n\n"
                            f"К оплате: {order['rub_amount']:.2f} RUB\n"
                            f"Получение: {crypto_str_display} {order['crypto_type']}"
                        ),
                        parse_mode='HTML',
                        reply_markup=None,
                    )
                except Exception as e:
                    logging.error(f"Не удалось отредактировать сообщение админа об отмене: {e}")
            else:
                try:
                    await context.bot.edit_message_caption(
                        chat_id=ADMIN_ID,
                        message_id=order['admin_message_id'],
                        caption=f"❌ Заявка #{order_id} отменена."
                    )
                except Exception as e:
                    logging.error(f"Не удалось отредактировать сообщение админа об отмене: {e}")

        msg_text = (
            f"🚫 Заявка #{order_id} отменена.\n⚠️ Бан на {BAN_HOURS}ч."
            if banned else f"🚫 Заявка #{order_id} отменена."
        )
        await query.edit_message_text(msg_text, parse_mode='HTML')
        return ConversationHandler.END

# ---------- ОТПРАВКА КРИПТЫ ----------
def get_explorer_url(crypto_type, txid):
    if crypto_type == 'BTC': return f"https://mempool.space/tx/{txid}"
    elif crypto_type == 'LTC': return f"https://blockchair.com/litecoin/transaction/{txid}"
    return f"https://www.blockchain.com/explorer?currency={crypto_type}&txid={txid}"

async def get_network_commission(crypto_type):
    url = f"{WALLET_BASE_URL}/api/v1/commissions"
    headers = {"X-Api-Key": WALLET_API_KEY}
    for attempt in range(3):
        try:
            resp = await asyncio.to_thread(
                requests.get, url, headers=headers, params={"coin": crypto_type}, timeout=10
            )
            data = resp.json()
            if resp.status_code == 200 and 'withdrawal' in data:
                return float(data['withdrawal'])
        except Exception as e:
            logging.warning(f"Попытка {attempt+1}: ошибка запроса комиссии {crypto_type}: {e}")
            await asyncio.sleep(1)
    logging.error(f"Не удалось получить комиссию {crypto_type} после 3 попыток")
    return None

# Точность вывода по монетам — максимум знаков после запятой
COIN_DECIMALS = {
    'BTC': 8,
    'LTC': 8,
    'USDT-TRC20': 6,
}

async def send_crypto(crypto_type, to_address, amount):
    coin = crypto_type

    # Округляем до допустимой точности монеты
    decimals = COIN_DECIMALS.get(coin, 6)
    rounded_amount = round(float(amount), decimals)
    if rounded_amount <= 0:
        logging.error(f"send_crypto: недопустимая сумма {amount} для {coin} (после округления {rounded_amount})")
        return None

    url = f"{WALLET_BASE_URL}/api/v1/withdrawal"
    headers = {"X-Api-Key": WALLET_API_KEY, "Content-Type": "application/json"}
    payload = {
        "coin": coin,
        "amount": f"{rounded_amount:.{decimals}f}",
        "address": to_address
    }
    try:
        resp = await asyncio.to_thread(requests.post, url, json=payload, headers=headers, timeout=30)
        data = resp.json()
        if resp.status_code == 200 and "id" in data:
            logging.info(f"Вывод создан: id={data['id']}, статус={data.get('status')}, "
                         f"coin={coin}, amount={rounded_amount}")
            return data["id"]
        else:
            logging.error(f"Ошибка API вывода {coin}: {resp.status_code} {data} "
                          f"(amount={rounded_amount})")
            return None
    except Exception as e:
        logging.error(f"Исключение при выводе {coin}: {e}")
        return None

async def poll_withdrawal_status(bot, order_id, withdrawal_id):
    try:
        start_time = time.time()
        url = f"{WALLET_BASE_URL}/api/v1/transaction"
        headers = {"X-Api-Key": WALLET_API_KEY}
        while time.time() - start_time < WALLET_POLL_TIMEOUT:
            await asyncio.sleep(WALLET_POLL_INTERVAL)
            try:
                resp = await asyncio.to_thread(requests.get, url, headers=headers, params={"id": withdrawal_id}, timeout=15)
                data = resp.json()
                if resp.status_code == 200:
                    status = data.get("status")
                    tx_id = data.get("tx_id")
                    if tx_id and status not in ("network_error", "refunded", "frozen"):
                        # Атомарно завершаем заказ через SQLite
                        conn = sqlite3.connect(DB_FILE)
                        c = conn.cursor()
                        c.execute('''UPDATE orders SET txid = ?, status = 'done', finished_at = ? 
                                     WHERE id = ? AND status = 'payment_confirmed' ''',
                                  (tx_id, datetime.now().isoformat(), order_id))
                        updated = c.rowcount
                        conn.commit()
                        conn.close()

                        if updated == 0:
                            # заказ уже завершён или статус изменился
                            return

                        # Обновляем локальный объект
                        order = orders.get(order_id)
                        if order:
                            order['txid'] = tx_id
                            order['status'] = 'done'
                            order['finished_at'] = datetime.now().isoformat()

                        # Записываем в Google Sheets и начисляем бонусы
                        add_successful_order_to_sheet(order_id)
                        award_wallet_commissions(order_id)
                        finalize_order_my_share(order_id)

                        # Формируем строку с количеством крипты
                        crypto_str_display = order.get('crypto_str') or f"{order['crypto_amount']:.6f}"

                        # --- Редактируем сообщение администратора ---
                        if order.get('admin_message_id'):
                            if order.get('payment_type') in ('bestmerchant', 'nicepay'):
                                # BM и NicePay — текстовые сообщения
                                try:
                                    await bot.edit_message_text(
                                        chat_id=ADMIN_ID,
                                        message_id=order['admin_message_id'],
                                        text=(
                                            f"✅ Заявка #{order_id} завершена\n"
                                            f"👤 Клиент: {order['username']} (ID: {order['user_id']})\n"
                                            f"🪙 Отправлено: {crypto_str_display} {order['crypto_type']}\n"
                                            f"📫 Кошелёк: {order['wallet']}"
                                        ),
                                        parse_mode='HTML'
                                    )
                                except Exception as e:
                                    logging.error(f"Не удалось отредактировать сообщение админа после успешного вывода: {e}")
                            else:
                                # Локальные методы — сообщение с чеком (документом)
                                try:
                                    pay_desc = "СБП" if order.get('payment_type') == 'sbp' else (
                                        "реквизиты" if order.get('payment_type') == 'card' else "24/7"
                                    )
                                    await bot.edit_message_caption(
                                        chat_id=ADMIN_ID,
                                        message_id=order['admin_message_id'],
                                        caption=(
                                            f"✅ Заявка #{order_id} успешно\n"
                                            f"Отправлено: {crypto_str_display} {order['crypto_type']}\n"
                                            f"На кошелёк: {order['wallet']}\n"
                                            f"Оплата: {order['rub_amount']:.2f} RUB ({pay_desc})"
                                        ),
                                        parse_mode='HTML'
                                    )
                                except Exception as e:
                                    logging.error(f"Не удалось отредактировать сообщение админа после успешного вывода: {e}")

                        # Отправляем пользователю информацию о транзакции
                        explorer_link = get_explorer_url(order['crypto_type'], tx_id)
                        crypto_str = order.get('crypto_str', f"{order['crypto_amount']:.6f}")
                        user_text = (
                            f"✅ {order['crypto_type']} успешно отправлены!\n\n"
                            f"Отправлено: {crypto_str} {order['crypto_type']}\n"
                            f"Кошелёк: <code>{order['wallet']}</code>\n\n"
                            f"TxID: <a href='{explorer_link}'>{tx_id}</a>\n\n"
                            f"⛓️ Средства поступят на ваш кошелёк после 2 подтверждений сети.\n\n"
                            f"🤝 Благодарим за обмен! Будем рады видеть вас снова."
                        )
                        review_kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton("⭐ Оставить отзыв", callback_data=f"leave_review_{order_id}")]
                        ])
                        try:
                            await bot.send_message(
                                order['user_id'],
                                user_text,
                                parse_mode='HTML',
                                reply_markup=review_kb,
                            )
                        except Exception as e:
                            logging.error(f"Ошибка отправки сообщения пользователю {order['user_id']}: {e}")
                        logging.info(f"Заявка #{order_id} завершена, TxID: {tx_id}")
                        return
                    if status in ("network_error", "refunded", "frozen"):
                        await bot.send_message(ADMIN_ID, f"⚠️ Вывод {withdrawal_id} для заявки #{order_id} завершился со статусом {status}")
                        return
            except Exception as e:
                logging.error(f"Ошибка опроса транзакции {withdrawal_id}: {e}")
        await bot.send_message(ADMIN_ID, f"⚠️ Не удалось получить tx_id для вывода {withdrawal_id} (заявка #{order_id}) за {WALLET_POLL_TIMEOUT} сек.")
    except Exception as e:
        logging.error(f"Критическая ошибка в poll_withdrawal_status: {e}")

async def confirm_payment(update, context):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    order_id = int(query.data.split('_')[2])
    order = orders.get(order_id)
    if not order or order['status'] != 'waiting_proof':
        return

    # Вызываем общую логику подтверждения (конвертация с резервом, отправка, уведомления)
    await confirm_payment_by_id(order_id)

    # Обновляем сообщение администратора (если оно было отправлено)
    if order.get('admin_message_id'):
        if order.get('payment_type') in ('bestmerchant', 'nicepay'):
            # BM и NicePay — текстовые сообщения
            try:
                await query.edit_message_text(
                    text=f"✅ Заявка #{order_id} подтверждена. Ожидаем завершения вывода...",
                    parse_mode='HTML'
                )
            except Exception as e:
                logging.error(f"Не удалось отредактировать сообщение админа: {e}")
        else:
            # Локальные методы — сообщение с документом (caption)
            try:
                await query.edit_message_caption(
                    caption=f"✅ Заявка #{order_id} подтверждена. Ожидаем завершения вывода..."
                )
            except Exception as e:
                logging.error(f"Не удалось отредактировать сообщение админа: {e}")

async def send_txid(update, context):
    query = update.callback_query; await query.answer()
    if query.from_user.id != ADMIN_ID: return
    order_id = int(query.data.split('_')[2])
    order = orders.get(order_id)
    if not order or order['status'] != 'payment_confirmed': return
    context.user_data['pending_txid_order'] = order_id

    text = f"📝 Введите TxID для заявки #{order_id}."
    try:
        if order.get('payment_type') in ('bestmerchant', 'nicepay'):
            await query.edit_message_text(text=text, parse_mode='HTML')
        else:
            await query.edit_message_caption(caption=text, parse_mode='HTML')
    except telegram.error.BadRequest:
        # На случай старых callback'ов / уже отредактированных сообщений
        pass

async def admin_txid_handler(update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    order_id = context.user_data.pop('pending_txid_order', None)
    if not order_id:
        return
    txid = update.message.text.strip()
    if not txid:
        return
    order = orders.get(order_id)
    if not order:
        await update.message.reply_text("❌ Заявка не найдена.")
        return
    if order['status'] == 'done':
        await update.message.reply_text("❌ Заявка уже завершена.")
        return

    order['txid'] = txid
    order['status'] = 'done'
    order['finished_at'] = datetime.now().isoformat()

    if not order.get('network_fee'):
        network_fee = await get_network_commission(order['crypto_type'])
        order['network_fee'] = (network_fee * order['market_rate']) if network_fee else 0.0

    save_order(order_id)
    await update.message.reply_text(f"✅ Заявка #{order_id} завершена.")

    add_successful_order_to_sheet(order_id)
    award_wallet_commissions(order_id)
    finalize_order_my_share(order_id)

    # Формируем строку с количеством крипты
    crypto_str_display = order.get('crypto_str') or f"{order['crypto_amount']:.6f}"

    # --- Редактируем сообщение администратора ---
    if order.get('admin_message_id'):
        if order.get('payment_type') in ('bestmerchant', 'nicepay'):
            # BM и NicePay — текстовые сообщения
            try:
                await context.bot.edit_message_text(
                    chat_id=ADMIN_ID,
                    message_id=order['admin_message_id'],
                    text=(
                        f"✅ Заявка #{order_id} завершена\n"
                        f"👤 Клиент: {order['username']} (ID: {order['user_id']})\n"
                        f"🪙 Отправлено: {crypto_str_display} {order['crypto_type']}\n"
                        f"📫 Кошелёк: {order['wallet']}"
                    ),
                    parse_mode='HTML'
                )
            except Exception as e:
                logging.error(f"Не удалось отредактировать сообщение админа: {e}")
        else:
            # Локальные методы — сообщение с чеком (документом)
            try:
                pay_desc = "СБП" if order.get('payment_type') == 'sbp' else (
                    "реквизиты" if order.get('payment_type') == 'card' else "24/7"
                )
                await context.bot.edit_message_caption(
                    chat_id=ADMIN_ID,
                    message_id=order['admin_message_id'],
                    caption=(
                        f"✅ Заявка #{order_id} успешно\n"
                        f"Отправлено: {crypto_str_display} {order['crypto_type']}\n"
                        f"На кошелёк: {order['wallet']}\n"
                        f"Оплата: {order['rub_amount']:.2f} RUB ({pay_desc})"
                    ),
                    parse_mode='HTML'
                )
            except Exception as e:
                logging.error(f"Не удалось отредактировать сообщение админа: {e}")

    explorer_link = get_explorer_url(order['crypto_type'], txid)
    crypto_str = order.get('crypto_str', f"{order['crypto_amount']:.6f}")
    user_text = (
        f"✅ {order['crypto_type']} успешно отправлены!\n\n"
        f"Отправлено: {crypto_str} {order['crypto_type']}\n"
        f"Кошелёк: <code>{order['wallet']}</code>\n\n"
        f"TxID: <a href='{explorer_link}'>{txid}</a>\n\n"
        f"⛓️ Средства поступят на ваш кошелёк после 2 подтверждений сети.\n\n"
        f"🤝 Благодарим за обмен! Будем рады видеть вас снова."
    )
    review_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Оставить отзыв", callback_data=f"leave_review_{order_id}")]
    ])
    try:
        await context.bot.send_message(
            order['user_id'],
            user_text,
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=review_kb,
        )
    except Exception as e:
        logging.error(f"Ошибка отправки сообщения пользователю {order['user_id']}: {e}")

# ---------- ЛИЧНЫЙ КАБИНЕТ ----------
def _build_cabinet(user_id):
    """Строит текст и клавиатуру личного кабинета.
    Возвращает (text, InlineKeyboardMarkup)."""
    # Успешные обмены пользователя
    user_orders = [o for o in orders.values() if o['user_id'] == user_id]
    done_orders = [o for o in user_orders if o['status'] == 'done']
    done_count = len(done_orders)
    done_sum = sum(o['rub_amount'] for o in done_orders)

    # Рефералы
    refs = referrals.get(user_id, [])
    ref_orders_done = [
        o for rid in refs
        for o in orders.values()
        if o['user_id'] == rid and o['status'] == 'done'
    ]
    ref_orders_count = len(ref_orders_done)
    ref_orders_sum = sum(o['rub_amount'] for o in ref_orders_done)

    # Кошелёк / купоны
    wallet_balance = get_wallet_available_balance(user_id)
    coupons_balance = round(user_bonuses.get(user_id, 0), 2)

    text = (
        f"👤 <b>Личный кабинет</b>\n"
        f"🆔 Ваш ID: <a href='tg://user?id={user_id}'>{user_id}</a>\n\n"
        f"✅ Успешных обменов: <b>{done_count}</b>\n"
        f"💰 Сумма успешных обменов: <b>{done_sum:.2f} RUB</b>\n\n"
        f"<b>👥 Рефералы</b>\n"
        f"• Приглашено: <b>{len(refs)}</b>\n"
        f"• Обменов: <b>{ref_orders_count}</b>\n"
        f"• Сумма обменов: <b>{ref_orders_sum:.2f} RUB</b>\n\n"
        f"💼 На кошельке: <b>{wallet_balance:.2f} RUB</b>\n"
        f"🎟 Купонов: <b>{coupons_balance:.2f} RUB</b>"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💸 Вывести", callback_data="cabinet_withdraw"),
            InlineKeyboardButton("💱 Продать", callback_data="cabinet_sell"),
        ],
        [InlineKeyboardButton("📋 Мои успешные обмены", callback_data="my_orders")],
        [
            InlineKeyboardButton("🎟 Промокоды", callback_data="cabinet_promo"),
            InlineKeyboardButton("🔗 Реф. ссылка", callback_data="copy_ref"),
        ],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="to_main_menu")],
    ])
    return text, kb

# ---------- FAQ ----------
FAQ_ITEMS = {
    'faq_time': (
        "❓ Как долго обрабатывается заявка?",
        "Заявки обычно обрабатываются за 25 минут. "
        "Однако время зачисления может зависеть от сети. "
        "В Биткоине подтверждение может занимать до часа или дольше при нагрузке."
    ),
    'faq_overpay': (
        "❓ Что делать, если я отправил больше средств, чем указано в заявке?",
        "Сообщите об этом оператору. Он поможет разобраться."
    ),
    'faq_underpay': (
        "❓ Что делать, если я отправил меньше средств?",
        "Сумма выплаты будет пересчитана исходя из реально поступивших средств "
        "и актуального курса."
    ),
    'faq_cancel': (
        "❓ Могу ли я отменить обмен после оплаты?",
        "Нет, отмена невозможна после выполнения оплаты."
    ),
    'faq_rate': (
        "❓ Как фиксируется курс?",
        "Курс фиксируется при поступлении средств на наш счет. "
        "До этого он может изменяться, если колебание >0,1%."
    ),
    'faq_requisites': (
        "❓ Как изменить реквизиты в заявке?",
        "Свяжитесь с оператором и сообщите о необходимости изменения реквизитов."
    ),
    'faq_third_party': (
        "❓ Переводы от 3х лиц, принимаете ли?",
        "Нет, переводы от третьих лиц совершать запрещено."
    ),
}


def _faq_menu_keyboard():
    """Клавиатура со списком вопросов."""
    buttons = []
    for key, (btn_text, _) in FAQ_ITEMS.items():
        buttons.append([InlineKeyboardButton(btn_text, callback_data=key)])
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="to_main_menu")])
    return InlineKeyboardMarkup(buttons)


async def faq_menu(update, context):
    """Reply-кнопка «❓ FAQ» — показывает список вопросов."""
    if is_bot_disabled() and update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return

    await update.message.reply_text(
        "❓ <b>Часто задаваемые вопросы</b>\n\nВыберите вопрос:",
        parse_mode='HTML',
        reply_markup=_faq_menu_keyboard()
    )


async def faq_answer_callback(update, context):
    """Показывает ответ на выбранный вопрос."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    key = query.data
    if key not in FAQ_ITEMS:
        return

    question, answer = FAQ_ITEMS[key]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад к вопросам", callback_data="faq_back")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="to_main_menu")],
    ])

    try:
        await query.edit_message_text(
            f"<b>{question}</b>\n\n{answer}",
            parse_mode='HTML',
            reply_markup=kb
        )
    except telegram.error.BadRequest:
        pass


async def faq_back_callback(update, context):
    """Возврат к списку вопросов."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    try:
        await query.edit_message_text(
            "❓ <b>Часто задаваемые вопросы</b>\n\nВыберите вопрос:",
            parse_mode='HTML',
            reply_markup=_faq_menu_keyboard()
        )
    except telegram.error.BadRequest:
        pass

async def personal_cabinet(update, context):
    user_id = update.message.from_user.id
    if is_bot_disabled() and user_id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return

    text, kb = _build_cabinet(user_id)
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=kb)


async def my_orders_callback(update, context):
    """Показывает последние 10 успешных обменов из личного кабинета."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    done_orders = [
        (oid, o) for oid, o in orders.items()
        if o['user_id'] == user_id and o['status'] == 'done'
    ]
    done_orders.sort(key=lambda x: x[1].get('finished_at') or '', reverse=True)
    last10 = done_orders[:10]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад в кабинет", callback_data="cabinet_back")]
    ])

    if not last10:
        try:
            await query.edit_message_text(
                "📭 У вас пока нет завершённых обменов.",
                reply_markup=kb,
            )
        except telegram.error.BadRequest:
            pass
        return

    lines = ["📋 <b>Последние успешные обмены:</b>"]
    for oid, o in last10:
        crypto_str = o.get('crypto_str') or f"{o['crypto_amount']:.6f}"
        lines.append(
            f"<b>#{oid}</b>\n"
            f"🪙 Получил: <code>{crypto_str}</code> {o['crypto_type']}\n"
            f"💵 Оплатил: <code>{o['rub_amount']:.2f}</code> RUB"
        )

    try:
        await query.edit_message_text(
            "\n\n".join(lines),
            parse_mode='HTML',
            reply_markup=kb,
        )
    except telegram.error.BadRequest as e:
        if "not modified" not in str(e).lower():
            raise

async def cabinet_back_callback(update, context):
    """Возврат из списка обменов в главный экран кабинета."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    text, kb = _build_cabinet(user_id)
    try:
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=kb)
    except telegram.error.BadRequest:
        pass

async def cabinet_withdraw_handler(update, context):
    """Reply-кнопка «💸 Вывести» в кабинете."""
    user_id = update.message.from_user.id
    if is_bot_disabled() and user_id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return
    await update.message.reply_text(
        "🚧 <b>Вывод временно недоступен</b>\n\n"
        "Функция в разработке. Попробуйте позже.",
        parse_mode='HTML',
        reply_markup=cabinet_keyboard
    )


async def cabinet_sell_handler(update, context):
    """Reply-кнопка «💱 Продать» в кабинете."""
    user_id = update.message.from_user.id
    if is_bot_disabled() and user_id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return
    await update.message.reply_text(
        "🚧 <b>Продажа временно недоступна</b>\n\n"
        "Функция в разработке. Попробуйте позже.",
        parse_mode='HTML',
        reply_markup=cabinet_keyboard
    )

async def cabinet_withdraw_callback(update, context):
    """Inline-кнопка «💸 Вывести» в кабинете."""
    query = update.callback_query
    try:
        await query.answer("🚧 Вывод временно недоступен", show_alert=True)
    except Exception:
        pass


async def cabinet_sell_callback(update, context):
    """Inline-кнопка «💱 Продать» в кабинете."""
    query = update.callback_query
    try:
        await query.answer("🚧 Продажа временно недоступна", show_alert=True)
    except Exception:
        pass

async def cabinet_promo_callback(update, context):
    """Запускает флоу ввода промокода из inline-кнопки кабинета."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    if is_bot_disabled() and query.from_user.id != ADMIN_ID:
        try:
            await query.edit_message_text("🔴 Бот временно отключен.")
        except Exception:
            pass
        return ConversationHandler.END

    await query.message.reply_text(
        "✏️ Введите ваш промокод:",
        reply_markup=cancel_keyboard,
    )
    return PROMO_CODE

async def contacts(update, context):
    if is_bot_disabled() and update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return

    text = (
    "📞 <b>Контакты</b>\n\n"
    f"🆘 <b>Поддержка:</b> {CONTACT_SUPPORT_URL}\n"
    f"📢 <b>Канал:</b> {CONTACT_CHANNEL_URL}\n"
    f"⭐ <b>Отзывы:</b> {REVIEWS_CHANNEL_URL}\n\n"
    "Если у вас возникли вопросы — напишите нам."
)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🆘 Поддержка", url=CONTACT_SUPPORT_URL)],
        [InlineKeyboardButton("📢 Канал", url=CONTACT_CHANNEL_URL)],
        [InlineKeyboardButton("⭐ Отзывы", url=REVIEWS_CHANNEL_URL)],
        [InlineKeyboardButton("✍️ Написать отзыв", callback_data="write_review")],
    ])

    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=kb,
        disable_web_page_preview=True,
    )

async def wallet_handler(update, context):
    """Кошелёк пользователя — единый для всех."""
    user_id = update.message.from_user.id

    if is_bot_disabled() and user_id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return

    try:
        available = get_wallet_available_balance(user_id)

        # Все приглашённые из referrals
        refs_count = len(referrals.get(user_id, []))

        # Количество завершённых личных обменов
        orders_count = sum(
            1 for o in orders.values()
            if o['user_id'] == user_id and o['status'] == 'done'
        )

        promo_balance = round(user_bonuses.get(user_id, 0), 2)

        # Инфо о следующем бонусе
        next_num, next_rate_str, _, _ = get_next_bonus_info(orders_count)

        text = (
            f"💼 <b>Кошелёк</b>\n\n"
            f"💰 Доступная сумма: <b>{available:.2f} ₽</b>\n"
            f"👥 Рефералов: <b>{refs_count}</b>\n"
            f"📊 Обменов: <b>{orders_count}</b>\n"
            f"📈 Заработано: <b>{available:.2f} ₽</b>\n"
            f"🎟 Промокоды: <b>{promo_balance:.2f} ₽</b>\n\n"
            f"🎁 <b>Бонусы за обмены:</b>\n"
            f"• Каждый 5-й — Slim\n"
            f"• Каждый 10-й — Extra\n"
            f"➡️ Следующий (#{next_num}): <b>{next_rate_str}</b>\n\n"
            f"<i>Промокоды используются только для скидки на комиссию.</i>"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💸 Вывод", callback_data="wallet_withdraw")],
            [InlineKeyboardButton("💱 Продажа", callback_data="wallet_sell")],
        ])
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=kb)

    except Exception as e:
        logging.error(f"Ошибка wallet_handler для {user_id}: {e}")
        await update.message.reply_text("❌ Не удалось загрузить кошелёк. Попробуйте позже.")


async def wallet_withdraw_callback(update, context):
    query = update.callback_query
    try:
        await query.answer("🚧 Функция в разработке", show_alert=True)
    except Exception:
        pass


async def wallet_sell_callback(update, context):
    query = update.callback_query
    try:
        await query.answer("🚧 Функция в разработке", show_alert=True)
    except Exception:
        pass

async def bonus_placeholder(update, context):
    user_id = update.message.from_user.id
    await update.message.reply_text(
        "🚧 Функция в разработке",
        reply_markup=get_cabinet_keyboard(user_id)
    )

async def copy_ref_link(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        f"🔗 <b>Ваша ссылка:</b>\n<code>https://t.me/{BOT_USERNAME}?start=ref{query.from_user.id}</code>\n\nНажмите чтобы скопировать.",
        parse_mode='HTML', disable_web_page_preview=True
    )

# ---------- ПОДДЕРЖКА ----------
async def support(update, context):
    if is_bot_disabled() and update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return
    await update.message.reply_text("📧 @TeRX_Supp")

# ---------- ГЛАВНОЕ МЕНЮ ----------
async def handle_main_menu(update, context):
    await update.message.reply_text(
        "🏠 Главное меню CryptoTRX\n"
        "Выберите нужный раздел ниже.\n"
        "При любых непонятных ситуациях напишите /start.",
        parse_mode='HTML',
        disable_web_page_preview=True,
        reply_markup=main_keyboard
    )
    return ConversationHandler.END

async def cancel_by_command(update, context):
    await handle_main_menu(update, context)
    return ConversationHandler.END

# ---------- СТАРТ ----------
async def start(update, context):
    user_id = update.message.from_user.id
    if is_bot_disabled() and user_id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return ConversationHandler.END
    banned, until = is_banned(user_id)
    if banned:
        await update.message.reply_text(f"🚫 Заблокированы на {int((until-time.time())/60)} мин.")
        return ConversationHandler.END
    args = context.args
    username = update.message.from_user.username
    new_user = is_new_user(user_id, username)
    if new_user and args and len(args) > 0 and args[0].startswith('ref'):
        try:
            parent_id = int(args[0][3:])
            if parent_id != user_id:
                save_referral(user_id, parent_id)
                if user_id not in referral_parent:
                    referral_parent[user_id] = parent_id
                    referrals.setdefault(parent_id, []).append(user_id)
                if user_id not in user_bonuses:
                    user_bonuses[user_id] = 0
                    save_bonus(user_id)
        except: pass
    await update.message.reply_text(
        "👋 Добро пожаловать в CryptoTRX\n"
        "Обмен ₽ на BTC и LTC за несколько минут по выгодному курсу.",
        reply_markup=main_keyboard
    )
    return ConversationHandler.END

# ---------- ПРОЧИЕ ФУНКЦИИ ----------
async def show_chart_menu(update, context):
    if is_bot_disabled() and update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🔴 Бот временно отключен.")
        return
    await update.message.reply_text("Выберите криптовалюту:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 LTC", callback_data="chart_LTC"),
            InlineKeyboardButton("📊 BTC", callback_data="chart_BTC")
        ]])
    )

async def chart_callback(update, context):
    query = update.callback_query; await query.answer()
    crypto_type = query.data.split('_')[1]
    await query.edit_message_text(f"⏳ Загружаю график {crypto_type}...")
    prices = await get_chart_data(crypto_type)
    if prices:
        buf = create_chart_image(prices, crypto_type)
        if buf:
            current = prices[-1][1]; high = max(p[1] for p in prices); low = min(p[1] for p in prices)
            change = ((current - prices[0][1]) / prices[0][1]) * 100; arrow = "▲" if change >= 0 else "▼"
            await query.message.delete()
            await context.bot.send_photo(query.message.chat_id, buf,
                caption=f"📊 <b>{crypto_type}/RUB</b>\n💰 Текущий: {current:,.2f} RUB\n📈 Макс: {high:,.2f} RUB\n📉 Мин: {low:,.2f} RUB\n📊 Изменение: {change:+.2f}% {arrow}",
                parse_mode='HTML')
        else:
            await query.edit_message_text("❌ Не удалось построить график.")
    else:
        await query.edit_message_text("❌ Нет данных.")

async def active_orders(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    active = {oid: o for oid, o in orders.items() if o['status'] in ('waiting_proof', 'payment_confirmed')}
    if not active: await update.message.reply_text("📭 Нет активных."); return
    for oid, o in sorted(active.items()):
        status = "⏳" if o['status'] == 'waiting_proof' else "✅"
        crypto_str = o.get('crypto_str', f"{o['crypto_amount']:.6f}")
        kb = []
        if o['status'] == 'waiting_proof': kb.append([InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_payment_{oid}")])
        kb.append([InlineKeyboardButton("📝 TxID", callback_data=f"send_txid_{oid}")])
        if o.get('proof_file_id'):
            await context.bot.send_document(ADMIN_ID, o['proof_file_id'],
                caption=f"<b>Заявка #{oid}</b> — {status}\n{o['username']} | {o['rub_amount']:.2f} RUB | {crypto_str} {o['crypto_type']}",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await context.bot.send_message(ADMIN_ID,
                f"<b>Заявка #{oid}</b> — {status}\n{o['username']} | {o['rub_amount']:.2f} RUB",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def history_command(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    done = {oid: o for oid, o in orders.items() if o['status'] == 'done'}
    if not done: await update.message.reply_text("📭 Пусто."); return
    for oid, o in sorted(done.items()):
        crypto_str = o.get('crypto_str', f"{o['crypto_amount']:.6f}")
        await update.message.reply_text(
            f"📋 <b>Заявка #{oid}</b>\n"
            f"👤 {o['username']} (ID: {o['user_id']})\n"
            f"{crypto_str} {o['crypto_type']} | {o['rub_amount']:.2f} RUB\n"
            f"💼 Моя доля: <b>{o.get('my_share', 0):.2f} RUB</b>\n"
            f"Кошелёк: <code>{o['wallet']}</code>\nTxID: {o['txid']}",
            parse_mode='HTML'
        )

# ---------- АКТИВНЫЙ ОБМЕН И ЧЕКИ ----------
async def active_order(update, context):
    user_id = update.effective_user.id
    active_orders_list = [(oid, o) for oid, o in orders.items() if o['user_id'] == user_id and o['status'] in ('waiting_proof', 'payment_confirmed')]
    if not active_orders_list:
        await update.message.reply_text("📭 У вас нет активных обменов.")
        return
    oid, order = active_orders_list[-1]
    crypto_str = order.get('crypto_str', f"{order['crypto_amount']:.6f}")
    if order['status'] == 'payment_confirmed':
        text = (
            f"✅ <b>Заявка #{oid} оплачена</b>\n"
            f"💵 Сумма: {order['rub_amount']:.0f} RUB\n"
            f"🪙 К получению: {crypto_str} {order['crypto_type']}\n"
            f"🔗 Кошелёк: <code>{order['wallet']}</code>\n\n"
            f"⏳ Ожидайте ссылку на транзакцию."
        )
        await update.message.reply_text(text, parse_mode='HTML')
    elif order['status'] == 'waiting_proof':
        if order.get('proof_file_id'):
            text = (
                f"📄 <b>Активный обмен #{oid}</b>\n"
                f"💵 Сумма: {order['rub_amount']:.0f} RUB\n"
                f"🪙 К получению: {crypto_str} {order['crypto_type']}\n"
                f"🔗 Кошелёк: <code>{order['wallet']}</code>\n\n"
                f"✅ Чек отправлен."
            )
            await update.message.reply_text(text, parse_mode='HTML')
        else:
            text = (
                f"📄 <b>Активный обмен #{oid}</b>\n"
                f"💵 Сумма: {order['rub_amount']:.0f} RUB\n"
                f"🪙 К получению: {crypto_str} {order['crypto_type']}\n"
                f"🔗 Кошелёк: <code>{order['wallet']}</code>\n"
                f"🏦 Реквизиты:\n<code>{order['payment_details']}</code>\n\n"
                f"⏳ Заявка действительна 15 минут."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отменить заявку", callback_data=f"cancel_active_{oid}")],
                [InlineKeyboardButton("📎 Прикрепить чек", callback_data=f"attach_proof_{oid}")]
            ])
            msg = await update.message.reply_text(text, parse_mode='HTML', reply_markup=keyboard)
            context.user_data['active_order_msg_id'] = msg.message_id
            context.user_data['active_order_chat_id'] = update.effective_chat.id
            context.user_data['active_order_id'] = oid

async def cancel_active_order(update, context):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split('_')[2])
    order = orders.get(order_id)
    if not order or order['user_id'] != query.from_user.id:
        await query.edit_message_text("❌ Заявка не найдена.")
        return

    if order['status'] != 'waiting_proof':
        await query.edit_message_text("❌ Заявку нельзя отменить.")
        return

    if order.get('proof_file_id'):
        await query.answer("Чек уже отправлен, отмена невозможна.", show_alert=True)
        return

    order['status'] = 'cancelled'
    save_order(order_id)
    refund_bonus_for_order(order_id)
    banned, _ = add_cancelled(order['user_id'])

    pid = order.get('payment_id')
    if pid and pid in payment_methods:
        payment_methods[pid]['timeout_until'] = 0
        save_payment_method(pid)

    # --- Обновляем сообщение админа ---
    if order.get('admin_message_id'):
        if order.get('payment_type') in ('bestmerchant', 'nicepay'):
            try:
                crypto_str_display = order.get('crypto_str') or f"{order['crypto_amount']:.6f}"
                await context.bot.edit_message_text(
                    chat_id=ADMIN_ID,
                    message_id=order['admin_message_id'],
                    text=(
                        f"❌ Заявка №{order_id} отменена\n"
                        f"Причина: клиент отменил заявку\n\n"
                        f"К оплате: {order['rub_amount']:.2f} RUB\n"
                        f"Получение: {crypto_str_display} {order['crypto_type']}"
                    ),
                    parse_mode='HTML',
                    reply_markup=None,
                )
            except Exception as e:
                logging.error(f"Не удалось отредактировать сообщение админа об отмене: {e}")
        else:
            try:
                await context.bot.edit_message_caption(
                    chat_id=ADMIN_ID,
                    message_id=order['admin_message_id'],
                    caption=f"❌ Заявка #{order_id} отменена."
                )
            except Exception as e:
                logging.error(f"Не удалось отредактировать сообщение админа об отмене: {e}")

    msg_text = (
        f"🚫 Заявка #{order_id} отменена.\n⚠️ Бан на {BAN_HOURS}ч."
        if banned else f"🚫 Заявка #{order_id} отменена."
    )
    await query.edit_message_text(msg_text, parse_mode='HTML')

    context.user_data.pop('active_order_msg_id', None)
    context.user_data.pop('active_order_chat_id', None)
    context.user_data.pop('active_order_id', None)

async def start_attach_proof(update, context):
    query = update.callback_query; await query.answer()
    order_id = int(query.data.split('_')[2]); order = orders.get(order_id)
    if not order or order['user_id'] != query.from_user.id:
        await query.edit_message_text("❌ Заявка не найдена."); return ConversationHandler.END
    if order['status'] != 'waiting_proof':
        await query.edit_message_text("❌ Заявка уже обработана или отменена."); return ConversationHandler.END
    if order.get('proof_file_id'):
        await query.answer("Чек уже прикреплён.", show_alert=True); return ConversationHandler.END
    context.user_data['order_id'] = order_id
    context.user_data['attach_chat_id'] = query.message.chat_id
    context.user_data['attach_msg_id'] = query.message.message_id
    await query.edit_message_text("📎 Отправьте PDF-чек в ответ на это сообщение.")
    return ATTACH_PROOF

async def attach_proof_handler(update, context):
    order_id = context.user_data.get('order_id')
    if not order_id or order_id not in orders:
        await update.message.reply_text("❌ Заявка не найдена.")
        return ConversationHandler.END

    order = orders[order_id]
    if order['status'] != 'waiting_proof':
        await update.message.reply_text("❌ Уже обработана.")
        return ConversationHandler.END

    if order.get('payment_type') in ('bestmerchant', 'nicepay'):
        await update.message.reply_text(
            "📄 PDF-чек не требуется для этого метода оплаты. "
            "Ожидайте автоматическое подтверждение после оплаты."
        )
        return ConversationHandler.END

    if order.get('proof_file_id'):
        await update.message.reply_text("❌ Чек уже прикреплён.")
        return ConversationHandler.END

    if not update.message.document or update.message.document.mime_type != 'application/pdf':
        await update.message.reply_text("⚠️ Только PDF.")
        return ATTACH_PROOF

    order['proof_file_id'] = update.message.document.file_id
    save_order(order_id)

    crypto_str = order.get('crypto_str', f"{order['crypto_amount']:.6f}")
    admin_text = (
        f"🆕 Заявка #{order_id}\n"
        f"👤 {order['username']} (ID: {order['user_id']})\n"
        f"🪙 {crypto_str} {order['crypto_type']}\n"
        f"Кошелёк: {order['wallet']}\n"
        f"Оплатил: {order['rub_amount']:.2f} RUB\n"
        f"Реквизиты: {order['payment_details']}"
    )
    admin_msg = await context.bot.send_document(
        ADMIN_ID,
        order['proof_file_id'],
        caption=admin_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"confirm_payment_{order_id}")],
            [InlineKeyboardButton("📝 TxID", callback_data=f"send_txid_{order_id}")]
        ])
    )
    order['admin_message_id'] = admin_msg.message_id
    save_order(order_id)

    attach_chat = context.user_data.get('attach_chat_id')
    attach_msg = context.user_data.get('attach_msg_id')
    if attach_chat and attach_msg:
        try:
            await context.bot.delete_message(chat_id=attach_chat, message_id=attach_msg)
        except:
            pass

    if order.get('user_message_id'):
        rub_amount = order['rub_amount']
        crypto_type = order['crypto_type']
        crypto_str = order.get('crypto_str', f"{order['crypto_amount']:.6f}")
        new_text = (
            f"Заявка: <b>#{order_id}</b>\n"
            f"💵 Оплата: <code>{rub_amount:.0f} RUB</code>\n"
            f"🪙 К получению: <code>{crypto_str}</code> {crypto_type}\n\n"
            f"⏳ Ожидайте подтверждения — обработка занимает от 1 до 15 минут."
        )
        try:
            await context.bot.edit_message_text(
                chat_id=order.get('user_chat_id', order['user_id']),
                message_id=order['user_message_id'],
                text=new_text,
                reply_markup=None,
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Не удалось отредактировать сообщение о чеке: {e}")
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ Чек получен. Ожидайте подтверждение.",
            reply_markup=main_keyboard
        )

    return ConversationHandler.END

async def cancel_attach(update, context):
    await handle_main_menu(update, context)
    return ConversationHandler.END

async def send_proof_help(update, context):
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📎 Как отправить чек?", callback_data="proof_help")]])
    msg = await update.message.reply_text("📄 Пожалуйста, отправьте PDF-чек.", reply_markup=keyboard)
    context.user_data['proof_help_msg_id'] = msg.message_id

async def proof_help_callback(update, context):
    query = update.callback_query
    text = (
        "📄 Как отправить чек:\n"
        "1. Сохраните чек как PDF.\n"
        "2. Нажмите на 📎 в поле ввода и отправьте файл.\n\n"
        "Принимаются только PDF!"
    )
    try:
        # show_alert=True показывает всплывающее окно — не засоряет чат,
        # и не создаёт дубликатов при повторных нажатиях
        await query.answer(text=text, show_alert=True)
    except telegram.error.BadRequest:
        # Устаревший callback (>5 мин) — тихо игнорируем
        pass

# ---------- РАССЫЛКА ----------
async def broadcast(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Используйте: /broadcast <текст сообщения>")
        return
    text = ' '.join(context.args)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    users = [row[0] for row in c.fetchall()]
    conn.close()
    success = 0; blocked = 0
    await update.message.reply_text(f"📢 Начинаю рассылку для {len(users)} пользователей...")
    for user_id in users:
        try:
            await context.bot.send_message(user_id, text)
            success += 1
        except telegram.error.Forbidden:
            blocked += 1
        except Exception as e:
            logging.error(f"Ошибка отправки пользователю {user_id}: {e}")
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"✅ Рассылка завершена.\n\nУспешно: {success}\nЗаблокировали бота: {blocked}")

_last_daily_report_date = None

async def send_daily_report():
    """Отправляет ежедневный отчёт админу (вызывается из потока)."""
    try:
        now_msk = datetime.utcnow() + timedelta(hours=3)
        date_str = now_msk.strftime('%d.%m.%Y')

        usdt_balance = await get_coin_balance('USDT-TRC20')
        usdt_rub_rate = await get_wallet_rate('USDT-TRC20') or 90.0
        wallet_rub = usdt_balance * usdt_rub_rate
        my_share_total = get_accumulated_my_share()
        site_rub = wallet_rub - my_share_total
        commission = get_worker_commission_since_last_saturday()

        text = (
            f"📊 <b>Ежедневный отчёт</b>\n\n"
            f"Дата: <b>{date_str}</b>\n"
            f"Баланс на сайте: <b>{site_rub:.2f} ₽</b>\n"
            f"Баланс на кошельке: <b>{wallet_rub:.2f} ₽</b>\n"
            f"Заработано: <b>{my_share_total:.2f} ₽</b>\n"
            f"Комиссия: <b>{commission:.2f} ₽</b>"
        )
        await app.bot.send_message(ADMIN_ID, text, parse_mode='HTML')
        logging.info(f"Ежедневный отчёт отправлен админу за {date_str}")
    except Exception as e:
        logging.error(f"Ошибка отправки ежедневного отчёта: {e}")


def daily_report_thread():
    """Каждый день в 16:00 МСК шлёт отчёт админу."""
    logging.info("Планировщик ежедневного отчёта запущен (поток)")
    global _last_daily_report_date
    while True:
        try:
            now_msk = datetime.utcnow() + timedelta(hours=3)
            today = now_msk.date()
            # Окно: 16:00–16:59 МСК, отправляем ровно один раз за день
            if now_msk.hour == 16 and _last_daily_report_date != today:
                _last_daily_report_date = today
                asyncio.run_coroutine_threadsafe(send_daily_report(), main_loop)
        except Exception as e:
            logging.error(f"Ошибка в daily_report_thread: {e}")
        time.sleep(30)

# ---------- ОТЗЫВЫ ----------
CONTACT_SUPPORT_URL = "https://t.me/TeRX_Supp"
CONTACT_CHANNEL_URL = "https://t.me/TeRXNews"   # ← замени на свой канал
REVIEWS_CHANNEL_URL = "https://t.me/TeRX_HISTORI"

async def show_reviews(update, context):
    """Кнопка «⭐ Отзывы» в главном меню — показывает ссылку на канал."""
    await update.message.reply_text(
        f"⭐ Отзывы наших клиентов:\n\n{REVIEWS_CHANNEL_URL}",
        reply_markup=main_keyboard,
        disable_web_page_preview=False,
    )


async def leave_review_start(update, context):
    """Пользователь нажал «Оставить отзыв» — из сообщения о заказе
    или из раздела «Контакты»."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    # Пытаемся вытащить order_id, если он есть
    parts = (query.data or "").split('_')
    order_id = None
    if len(parts) >= 3:
        try:
            order_id = int(parts[2])
        except (ValueError, IndexError):
            order_id = None

    if order_id:
        context.user_data['review_order_id'] = order_id
    else:
        context.user_data.pop('review_order_id', None)

    # Убираем inline-кнопку, чтобы не нажали повторно
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            "✍️ Напишите ваш отзыв и отправьте.\n\n"
            "Чтобы отменить — нажмите «🏠 Главное меню»."
        ),
        reply_markup=cancel_keyboard,
    )
    return LEAVE_REVIEW_TEXT


async def leave_review_text(update, context):
    """Получили текст отзыва — благодарим, шлём админу."""
    user = update.message.from_user
    order_id = context.user_data.pop('review_order_id', None)
    review_text = (update.message.text or "").strip()

    if not review_text:
        # Пустое сообщение — попросим ещё раз
        await update.message.reply_text("Пожалуйста, напишите текст отзыва.")
        return LEAVE_REVIEW_TEXT

    # Ответ пользователю
    await update.message.reply_text(
        "Спасибо за ваш отзыв!\n\n"
        "После модерации он будет опубликован в канале «Отзывы» ⭐",
        reply_markup=main_keyboard,
    )

    # Уведомляем админа: шапка + forward оригинального сообщения
    try:
        header = (
            f"⭐ <b>Новый отзыв</b>\n"
            f"👤 {user.full_name}"
        )
        if user.username:
            header += f" (@{user.username})"
        header += f"\n🆔 <code>{user.id}</code>"
        if order_id:
            header += f"\n📦 Заявка: #{order_id}"

        await context.bot.send_message(ADMIN_ID, header, parse_mode='HTML')

        # Именно forward — админ увидит автора по клику
        await context.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id,
        )
    except Exception as e:
        logging.error(f"Не удалось переслать отзыв админу: {e}")

    return ConversationHandler.END


async def cancel_review(update, context):
    """Отмена ввода отзыва."""
    context.user_data.pop('review_order_id', None)
    await handle_main_menu(update, context)
    return ConversationHandler.END

# ---------- СБОРКА ----------
def main():
    logging.info("Запуск бота...")
    init_db(); load_data()
    global google_sheet, main_loop
    try:
        google_sheet = init_google_sheet()
        logging.info("Google Sheets инициализирован")
    except Exception as e:
        logging.error(f"Не удалось инициализировать Google Sheets: {e}")
        google_sheet = None

    global app
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler('ping', lambda u,c: u.message.reply_text("pong")))
    app.add_handler(CommandHandler('info', info_command))
    app.add_handler(CommandHandler('add_payment', add_payment_method))
    app.add_handler(CommandHandler('payment_stats', payment_stats))
    app.add_handler(CommandHandler('activate_payment', activate_payment))
    app.add_handler(CommandHandler('remove_payment', remove_payment))
    app.add_handler(CommandHandler('ban', ban_user))
    app.add_handler(CommandHandler('unban', unban_user))
    app.add_handler(CommandHandler('disable', disable_bot))
    app.add_handler(CommandHandler('enable', enable_bot))
    app.add_handler(CommandHandler('start_day', start_day))
    app.add_handler(CommandHandler('end_day', end_day))
    app.add_handler(CommandHandler('status_day', status_day))
    app.add_handler(CommandHandler('report', report_command))
    app.add_handler(CommandHandler('history', history_command))
    app.add_handler(CommandHandler('active', active_orders))
    app.add_handler(CommandHandler('start', start), group=1)
    app.add_handler(CommandHandler('cancel_order', admin_cancel_order))
    app.add_handler(CommandHandler('addcoupon', add_coupon))
    app.add_handler(CommandHandler('coupons', list_coupons))
    app.add_handler(CommandHandler('broadcast', broadcast))
    app.add_handler(CommandHandler('balances', show_balances))
    app.add_handler(CommandHandler('withdraw_share', withdraw_my_share))

    # --- Функции перезапуска покупки из любого состояния ---
    async def restart_ltc(update, context):
        context.user_data.clear()
        return await buy_ltc_start(update, context)

    async def restart_btc(update, context):
        context.user_data.clear()
        return await buy_btc_start(update, context)

    ltc_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Text("LTC"), buy_ltc_start)],
    states={
        LTC_AMOUNT: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND, parse_amount),
            MessageHandler(filters.COMMAND, cancel_by_command)
        ],
        LTC_BONUS: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            CallbackQueryHandler(bonus_callback, pattern='^(use_bonus|no_bonus)$')
        ],
        LTC_WALLET: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_entered),
            CallbackQueryHandler(cancel_callback, pattern='^cancel$'),
        ],
        LTC_CONFIRM: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            CallbackQueryHandler(confirm_order, pattern='^(confirm_order|cancel)$')
        ],
        LTC_PROOF: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            MessageHandler(filters.Document.ALL, proof_handler),
            MessageHandler(filters.ALL, send_proof_help),
            CallbackQueryHandler(proof_help_callback, pattern='^proof_help$'),
            CallbackQueryHandler(cancel_order, pattern='^cancel_order_'),
        ],
        LTC_SELECT_MAIN_METHOD: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
            MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            CallbackQueryHandler(payment_method_callback, pattern='^(ru_card|ru_sbp|bm_ru_banks|bm_transgran|noop|cancel)$'),
            CallbackQueryHandler(nicepay_method_callback, pattern='^nicepay_method_'),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu),
        ],
        TRANSGRAN_METHOD: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
            MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            CallbackQueryHandler(nicepay_method_callback, pattern='^nicepay_method_'),
            CallbackQueryHandler(cancel_callback, pattern='^cancel$'),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
        ],
    },
    fallbacks=[MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu)],
    allow_reentry=True,      
)
    btc_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Text("BTC"), buy_btc_start)],
    states={
        BTC_AMOUNT: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND, parse_amount),
            MessageHandler(filters.COMMAND, cancel_by_command)
        ],
        BTC_BONUS: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            CallbackQueryHandler(bonus_callback, pattern='^(use_bonus|no_bonus)$')
        ],
        BTC_WALLET: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_entered),
            CallbackQueryHandler(cancel_callback, pattern='^cancel$'),
        ],
        BTC_CONFIRM: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            CallbackQueryHandler(confirm_order, pattern='^(confirm_order|cancel)$')
        ],
        BTC_PROOF: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            MessageHandler(filters.Document.ALL, proof_handler),
            MessageHandler(filters.ALL, send_proof_help),
            CallbackQueryHandler(proof_help_callback, pattern='^proof_help$'),
            CallbackQueryHandler(cancel_order, pattern='^cancel_order_'),
        ],
        BTC_SELECT_MAIN_METHOD: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            CallbackQueryHandler(payment_method_callback, pattern='^(ru_card|ru_sbp|bm_ru_banks|bm_transgran|noop|cancel)$'),
            CallbackQueryHandler(nicepay_method_callback, pattern='^nicepay_method_'),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu),
        ],
        TRANSGRAN_METHOD: [
            MessageHandler(filters.Text("LTC"), restart_ltc),
                MessageHandler(filters.Text("BTC"), restart_btc),
            MessageHandler(filters.COMMAND, cancel_by_command),
            CallbackQueryHandler(nicepay_method_callback, pattern='^nicepay_method_'),
            CallbackQueryHandler(cancel_callback, pattern='^cancel$'),
            MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu),
        ],
    },
    fallbacks=[MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu)],
    allow_reentry=True,          # ✅ добавлено
)
    attach_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_attach_proof, pattern='^attach_proof_')],
    states={
        ATTACH_PROOF: [
            MessageHandler(filters.COMMAND, cancel_attach),
            MessageHandler(filters.Document.ALL, attach_proof_handler)
        ]
    },
    fallbacks=[MessageHandler(filters.Text("🏠 Главное меню"), cancel_attach)],
    allow_reentry=True,
)
    promo_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Text("🎟 Промокод"), promo_start),
            MessageHandler(filters.Text("🎟 Купоны"), promo_start),
            CallbackQueryHandler(cabinet_promo_callback, pattern='^cabinet_promo$'),
        ],
        states={
            PROMO_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, promo_code_entered),
                         MessageHandler(filters.COMMAND, cancel_promo),
                         MessageHandler(filters.Text("🏠 Главное меню"), cancel_promo)]
        },
        fallbacks=[MessageHandler(filters.Text("🏠 Главное меню"), cancel_promo)],
        allow_reentry=True
    )

    # ← ВСТАВИТЬ ЗДЕСЬ
    review_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(leave_review_start, pattern='^leave_review_'),
            CallbackQueryHandler(leave_review_start, pattern='^write_review$'),
        ],
        states={
            LEAVE_REVIEW_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, leave_review_text),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Text("🏠 Главное меню"), cancel_review),
            CommandHandler('cancel', cancel_review),
            CommandHandler('start', cancel_review),
        ],
        allow_reentry=True,
    )

    app.add_handler(ltc_conv)
    app.add_handler(btc_conv)
    app.add_handler(attach_conv)
    app.add_handler(promo_conv)
    app.add_handler(review_conv)

    app.add_handler(MessageHandler(filters.Text("Кабинет"), personal_cabinet))
    app.add_handler(MessageHandler(filters.Text("Контакты"), contacts))
    app.add_handler(MessageHandler(filters.Text("Активный обмен"), active_order))
    app.add_handler(MessageHandler(filters.Text("🏠 Главное меню"), handle_main_menu))
    app.add_handler(MessageHandler(filters.Text("❓ FAQ"), faq_menu))
    app.add_handler(CallbackQueryHandler(my_orders_callback, pattern='^my_orders$'))
    app.add_handler(CallbackQueryHandler(cabinet_back_callback, pattern='^cabinet_back$'))
    app.add_handler(CallbackQueryHandler(cabinet_withdraw_callback, pattern='^cabinet_withdraw$'))
    app.add_handler(CallbackQueryHandler(cabinet_sell_callback, pattern='^cabinet_sell$'))
    app.add_handler(CallbackQueryHandler(bm_paid_callback, pattern='^bm_paid_'))
    app.add_handler(CallbackQueryHandler(nicepay_paid_callback, pattern='^nicepay_paid_'))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern='^cancel_order_'))
    app.add_handler(CallbackQueryHandler(cancel_active_order, pattern='^cancel_active_'))
    app.add_handler(CallbackQueryHandler(bm_retry_callback, pattern='^retry_bm_order$'))
    app.add_handler(CallbackQueryHandler(to_main_menu_callback, pattern='^to_main_menu$'))
    app.add_handler(CallbackQueryHandler(confirm_payment, pattern='^confirm_payment_'))
    app.add_handler(CallbackQueryHandler(send_txid, pattern='^send_txid_'))
    app.add_handler(CallbackQueryHandler(chart_callback, pattern='^chart_'))
    app.add_handler(CallbackQueryHandler(copy_ref_link, pattern='^copy_ref$'))
    app.add_handler(CallbackQueryHandler(wallet_withdraw_callback, pattern='^wallet_withdraw$'))
    app.add_handler(CallbackQueryHandler(wallet_sell_callback, pattern='^wallet_sell$'))
    app.add_handler(CallbackQueryHandler(back_to_methods_callback, pattern='^back_to_methods$'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_txid_handler))
    app.add_handler(CallbackQueryHandler(faq_back_callback, pattern='^faq_back$'))
    app.add_handler(CallbackQueryHandler(
        faq_answer_callback,
        pattern='^faq_(time|overpay|underpay|cancel|rate|requisites|third_party)$'
    ))


    async def error_handler(update, context):
        try: raise context.error
        except telegram.error.Forbidden: logging.warning(f"Forbidden: {context.error}")
        except Exception as e: logging.error(f"Unhandled error: {e}")
    app.add_error_handler(error_handler)

        # Создаём и устанавливаем event loop в главном потоке
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    global main_loop
    main_loop = loop

    # Запускаем HTTP-сервер панели в фоновом потоке
    httpd = HTTPServer(('localhost', 5002), ReloadHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    logging.info("HTTP-сервер синхронизации запущен на порту 5002")

    # Запускаем планировщик ежедневного отчёта в 16:00 МСК
    report_thread = threading.Thread(target=daily_report_thread, daemon=True)
    report_thread.start()
    logging.info("Планировщик ежедневного отчёта запущен")

    # Запускаем бота: initialize → start → start_polling на нашем loop
    async def run_bot():
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        # держим loop живым до Ctrl+C / stop-сигнала
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    try:
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()

if __name__ == "__main__":
    main()