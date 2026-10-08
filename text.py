# texts.py
"""Все тексты и подписи кнопок бота. Меняй только здесь."""

# ─────────────────────────── КНОПКИ ГЛАВНОГО МЕНЮ ───────────────────────────
BTN_LTC = "💎 LTC"
BTN_BTC = "₿ BTC"
BTN_CABINET = "👤 Кабинет"
BTN_CONTACTS = "📞 Контакты"
BTN_ACTIVE = "⏳ Активный обмен"
BTN_FAQ = "❓ FAQ"
BTN_MAIN_MENU = "🏠 Главное меню"
BTN_WALLET = "💼 Кошелёк"

# ─────────────────────────── ОБЩИЕ КНОПКИ ───────────────────────────
BTN_CONFIRM = "✅ Подтвердить"
BTN_CANCEL = "❌ Отмена"
BTN_CANCEL_ORDER = "❌ Отменить заявку"
BTN_I_PAID = "💳 Я оплатил"
BTN_ATTACH_PROOF = "📎 Прикрепить чек"
BTN_PROOF_HELP = "📎 Как отправить чек?"
BTN_BACK_TO_METHODS = "⬅️ Назад"
BTN_TO_MAIN = "🏠 Главное меню"
BTN_RETRY = "🔄 Попробовать снова"
BTN_SEND_REQUISITES = "📤 Отправить реквизиты"
BTN_CONFIRM_PAYMENT = "✅ Подтвердить оплату"
BTN_REJECT_PAYMENT = "❌ Отклонить оплату"
BTN_TXID = "📝 Ввести TxID"
BTN_LEAVE_REVIEW = "⭐ Оставить отзыв"

# ─────────────────────────── СТАРТ / МЕНЮ ───────────────────────────
START = (
    "👋 <b>Добро пожаловать в CryptoTRX</b>\n\n"
    "Обмен ₽ на BTC и LTC за несколько минут по выгодному курсу.\n"
    "Выберите нужный раздел ниже."
)

MAIN_MENU = (
    "🏠 <b>Главное меню CryptoTRX</b>\n"
    "Выберите нужный раздел ниже.\n"
    "При любых непонятных ситуациях напишите /start."
)

# ─────────────────────────── СОЗДАНИЕ ЗАЯВКИ ───────────────────────────
ORDER_CREATED_AWAITING_REQUISITES = (
    "✅ <b>Заявка №{order_id} создана</b>\n\n"
    "💠 Валюта: <b>{crypto_type}</b>\n"
    "🪙 К получению: <code>{crypto_str}</code>\n"
    "🔗 Кошелёк: <code>{wallet}</code>\n"
    "💵 К оплате: <b>{rub_amount:.0f} RUB</b>\n\n"
    "⏳ Ожидайте реквизиты для оплаты.\n"
    "Администратор отправит их в течение пары минут."
)

REQUISITES_RECEIVED = (
    "💳 <b>Реквизиты для оплаты заявки №{order_id}</b>\n\n"
    "{requisites}\n\n"
    "💵 Сумма к оплате: <b>{rub_amount:.0f} RUB</b>\n\n"
    "⚠️ После оплаты обязательно нажмите кнопку <b>«{btn_i_paid}»</b> ниже.\n"
    "Без нажатия кнопки заявка не будет обработана."
)

I_PAID_AWAITING = (
    "⏳ <b>Ожидайте подтверждения оплаты</b>\n\n"
    "Проверка оплаты в среднем занимает <b>5–10 минут</b>.\n"
    "Пожалуйста, не производите повторную оплату.\n\n"
    "Статус заявки: <b>Ожидает подтверждения оплаты</b>"
)

# ─────────────────────────── УВЕДОМЛЕНИЯ АДМИНУ ───────────────────────────
ADMIN_NEW_ORDER = (
    "🆕 <b>Новая заявка #{order_id}</b>\n"
    "👤 Пользователь: {username}\n"
    "🆔 ID: <code>{user_id}</code>\n"
    "💰 Валюта: <b>{crypto_type}</b>\n"
    "💵 К получению: <code>{crypto_str}</code> {crypto_type}\n"
    "💳 К оплате: <b>{rub_amount:.0f} RUB</b>\n"
    "📥 Кошелёк: <code>{wallet}</code>\n"
    "🕐 Время: {created_at}\n"
    "Статус: <b>Ожидает реквизиты</b>"
)

ADMIN_USER_PAID = (
    "💳 <b>Пользователь сообщил об оплате</b>\n\n"
    "Заявка: <b>#{order_id}</b>\n"
    "Пользователь: {username} (<code>{user_id}</code>)\n"
    "Сумма: <b>{rub_amount:.0f} RUB</b>\n"
    "Статус: <b>Ожидает проверки оплаты</b>"
)

ADMIN_REQUISITES_SENT = (
    "📤 Реквизиты отправлены клиенту по заявке #{order_id}."
)

ADMIN_ENTER_REQUISITES = (
    "✏️ <b>Введите текст с реквизитами</b>\n\n"
    "Заявка #{order_id}\n"
    "Клиент: {username} (<code>{user_id}</code>)\n"
    "Сумма: <b>{rub_amount:.0f} RUB</b>\n\n"
    "Отправьте одним сообщением. Поддерживается HTML."
)

# ─────────────────────────── ОШИБКИ / ВАЛИДАЦИЯ ───────────────────────────
ERR_BOT_DISABLED = "🔴 Бот временно отключён."
ERR_BANNED = "🚫 Вы заблокированы на {minutes} мин."
ERR_INVALID_AMOUNT = "❌ Введите сумму числом."
ERR_MIN_AMOUNT = "❌ Минимальная сумма обмена: {min_rub} RUB."
ERR_MAX_AMOUNT = "❌ <b>Сумма временно недоступна</b>\n📌 Максимум: <b>{max_rub:,} ₽</b>"
ERR_INVALID_WALLET = "⚠️ Неверный формат {crypto_type}-адреса."
ERR_ONLY_PDF = "⚠️ Принимается только PDF-файл."
ERR_SESSION_EXPIRED = (
    "⚠️ <b>Сессия истекла</b>\n\n"
    "Начните заново: нажмите кнопку «{btn_ltc}» или «{btn_btc}» в главном меню."
)

# ─────────────────────────── СТАТУСЫ (enum-строки для БД) ───────────────────────────
class Status:
    CREATED = "created"                     # Создана
    AWAITING_REQUISITES = "awaiting_req"    # Ожидает реквизиты
    REQUISITES_SENT = "req_sent"            # Реквизиты отправлены
    AWAITING_PAYMENT_CHECK = "await_check"  # Ожидает подтверждения оплаты
    PAYMENT_CONFIRMED = "payment_confirmed" # Оплата подтверждена
    PAYMENT_REJECTED = "payment_rejected"   # Оплата не подтверждена
    DONE = "done"                           # Завершена
    CANCELLED = "cancelled"                 # Отменена