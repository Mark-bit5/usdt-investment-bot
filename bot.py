import asyncio
import os
import time
from datetime import datetime, timedelta

import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

# ======================== CONFIG ========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "")

BEP20_ADDRESS = os.getenv("BEP20_ADDRESS", "")
TRC20_ADDRESS = os.getenv("TRC20_ADDRESS", "")

USERS_DIR = os.getenv("USERS_DIR", "users")

DAILY_TASKS = {
    1: 0.00,
    10: 0.20,
    30: 0.30,
    50: 0.50,
    100: 1.00,
}

REFERRAL_COMMISSION = 0.10
CAPITAL_RECOVERY_DAYS = 50
WITHDRAW_COOLDOWN_DAYS = 10

# ======================== DATABASE ========================

DEFAULT_USER = {
    "telegram_id": 0,
    "username": "",
    "balance": 0.0,
    "deposit": 0.0,
    "deposit_date": "",
    "last_withdraw": "",
    "referral_by": "",
    "referrals": "",
    "daily_tasks_claimed": "",
    "capital_recovered": 0.0,
    "used_tx_hashes": "",
}

def ensure_users_dir():
    if not os.path.exists(USERS_DIR):
        os.makedirs(USERS_DIR)

def normalize_username(name):
    name = name.strip()
    if name.startswith("@"):
        name = name[1:]
    return name.lower()

def user_file(username):
    return os.path.join(USERS_DIR, f"{normalize_username(username)}.txt")

def load_user(username):
    ensure_users_dir()
    username = normalize_username(username)
    path = user_file(username)
    if not os.path.exists(path):
        return None
    data = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key in ("balance", "deposit", "capital_recovered"):
                    data[key] = float(value) if value else 0.0
                elif key == "telegram_id":
                    data[key] = int(value) if value else 0
                else:
                    data[key] = value
    for key, default in DEFAULT_USER.items():
        if key not in data:
            data[key] = default
    data["username"] = username
    return data

def save_user(username, data):
    ensure_users_dir()
    username = normalize_username(username)
    path = user_file(username)
    lines = []
    lines.append(f"telegram_id={int(data.get('telegram_id', 0))}")
    if data.get("username"):
        lines.append(f"username={data['username']}")
    if float(data.get("balance", 0)) != 0:
        lines.append(f"balance={float(data['balance']):.2f}")
    if float(data.get("deposit", 0)) != 0:
        lines.append(f"deposit={float(data['deposit']):.2f}")
    if data.get("deposit_date"):
        lines.append(f"deposit_date={data['deposit_date']}")
    if data.get("last_withdraw"):
        lines.append(f"last_withdraw={data['last_withdraw']}")
    if data.get("referral_by"):
        lines.append(f"referral_by={data['referral_by']}")
    if data.get("referrals"):
        lines.append(f"referrals={data['referrals']}")
    if data.get("daily_tasks_claimed"):
        lines.append(f"daily_tasks_claimed={data['daily_tasks_claimed']}")
    if float(data.get("capital_recovered", 0)) != 0:
        lines.append(f"capital_recovered={float(data['capital_recovered']):.2f}")
    if data.get("used_tx_hashes"):
        lines.append(f"used_tx_hashes={data['used_tx_hashes']}")
    with open(path, "w", encoding="utf-8") as f:
        if lines:
            f.write("\n".join(lines) + "\n")

def get_user(username):
    data = load_user(username)
    if data is None:
        data = DEFAULT_USER.copy()
        data["username"] = normalize_username(username)
        save_user(username, data)
    return data

def update_user(username, **kwargs):
    data = get_user(username)
    data.update(kwargs)
    save_user(username, data)
    return data

def get_all_usernames():
    ensure_users_dir()
    names = []
    for filename in os.listdir(USERS_DIR):
        if filename.endswith(".txt"):
            names.append(filename.replace(".txt", ""))
    return names

def get_username_by_telegram_id(tg_id):
    ensure_users_dir()
    for username in get_all_usernames():
        user = load_user(username)
        if user and user.get("telegram_id", 0) == tg_id:
            return username
    return None

def get_all_used_hashes():
    used = []
    for username in get_all_usernames():
        user = load_user(username)
        if user and user.get("used_tx_hashes"):
            used.extend(user["used_tx_hashes"].split(","))
    return used

def add_used_hash(username, tx_hash):
    data = get_user(username)
    existing = data.get("used_tx_hashes", "")
    if existing:
        data["used_tx_hashes"] = existing + "," + tx_hash
    else:
        data["used_tx_hashes"] = tx_hash
    save_user(username, data)

def get_referrals(username):
    data = get_user(username)
    refs = data.get("referrals", "")
    if refs:
        return [x for x in refs.split(",") if x]
    return []

def add_referral(username, ref_username):
    data = get_user(username)
    refs = data.get("referrals", "")
    if refs:
        if ref_username not in refs.split(","):
            data["referrals"] = refs + "," + ref_username
    else:
        data["referrals"] = ref_username
    save_user(username, data)

def get_daily_tasks(username):
    data = get_user(username)
    tasks = data.get("daily_tasks_claimed", "")
    if tasks:
        return tasks.split(",")
    return []

def add_daily_task(username, date_str):
    data = get_user(username)
    tasks = data.get("daily_tasks_claimed", "")
    if tasks:
        data["daily_tasks_claimed"] = tasks + "," + date_str
    else:
        data["daily_tasks_claimed"] = date_str
    save_user(username, data)

# ======================== DEPOSIT CHECK ========================

def check_bsc_deposit(address, min_amount=0):
    url = "https://api.bscscan.com/api"
    params = {
        "module": "account",
        "action": "tokentx",
        "address": address,
        "contractaddr": "0x55d398326f99059fF775485246999027B3197955",
        "page": 1,
        "offset": 10,
        "sort": "desc",
        "apikey": BSCSCAN_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get("status") == "1" and data.get("result"):
            deposits = []
            for tx in data["result"]:
                if tx["to"].lower() == address.lower():
                    amount = int(tx["value"]) / (10 ** int(tx["tokenDecimal"]))
                    if amount >= min_amount:
                        deposits.append({
                            "hash": tx["hash"],
                            "amount": amount,
                            "time": int(tx["timeStamp"]),
                        })
            return deposits
    except Exception:
        pass
    return []

def check_trc_deposit(address, min_amount=0):
    url = "https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    params = {
        "limit": 10,
        "order_by": "block_timestamp,desc",
        "only_to": True,
    }
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        if data.get("data"):
            deposits = []
            for tx in data["data"]:
                contract = tx.get("token_info", {}).get("address", "")
                if contract == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t":
                    tx_hash = tx["transaction_id"]
                    amount = int(tx.get("value", 0)) / (10 ** tx.get("token_info", {}).get("decimals", 6))
                    if tx["to"] == address and amount >= min_amount:
                        deposits.append({
                            "hash": tx_hash,
                            "amount": amount,
                            "time": int(tx.get("block_timestamp", 0) / 1000),
                        })
            return deposits
    except Exception:
        pass
    return []

# ======================== BOT SETUP ========================

class RegisterForm(StatesGroup):
    waiting_for_username = State()

class DepositForm(StatesGroup):
    waiting_for_network = State()
    waiting_for_hash = State()
    waiting_for_amount = State()


bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 رصيدي"), KeyboardButton(text="📥 الإيداع")],
            [KeyboardButton(text="📤 السحب"), KeyboardButton(text="📋 المهام اليومية")],
            [KeyboardButton(text="🔗 رابط الإحالة"), KeyboardButton(text="📊 تفاصيل حسابي")],
        ],
        resize_keyboard=True,
    )

async def show_main_menu(message: types.Message):
    await message.answer(
        "مرحباً بك في بوت الاستثمار الرقمي! 💎\n\n"
        "اختر من القائمة:",
        reply_markup=main_keyboard(),
    )

def my_username(message: types.Message):
    username = get_username_by_telegram_id(message.from_user.id)
    if not username:
        return None
    return username

# ======================== HANDLERS ========================

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    tg_id = message.from_user.id
    username = get_username_by_telegram_id(tg_id)

    args = message.text.split()
    ref_tg_id = None
    if len(args) > 1:
        try:
            ref_tg_id = int(args[1])
        except ValueError:
            pass

    if username is not None:
        await state.clear()
        await show_main_menu(message)
        return

    await state.update_data(pending_ref=ref_tg_id)
    await state.set_state(RegisterForm.waiting_for_username)
    await message.answer(
        "👤 أرسل اسم المستخدم الخاص بك (@username) \n"
        "مثال: <code>@mark_as</code>\n\n"
        "⚠️ هذا الاسم سيكون معرفك الدائم في البوت."
    )


@dp.message(RegisterForm.waiting_for_username)
async def process_username(message: types.Message, state: FSMContext):
    raw = message.text.strip()
    username = normalize_username(raw)

    if not username or len(username) < 4:
        await message.answer("❌ اسم المستخدم غير صحيح.\nأرسله على الشكل @username")
        return

    tg_id = message.from_user.id

    existing = load_user(username)
    if existing is not None:
        other_id = existing.get("telegram_id", 0)
        if other_id and other_id != tg_id:
            await message.answer("❌ اسم المستخدم هذا مسجل من قبل.\nاختر اسماً آخر.")
            return

    data = DEFAULT_USER.copy()
    data["username"] = username
    data["telegram_id"] = tg_id

    state_data = await state.get_data()
    ref_tg_id = state_data.get("pending_ref")
    if ref_tg_id and ref_tg_id != tg_id:
        ref_username = get_username_by_telegram_id(ref_tg_id)
        if ref_username and ref_username != username:
            data["referral_by"] = ref_username
            add_referral(ref_username, username)

    save_user(username, data)
    await state.clear()
    await show_main_menu(message)


@dp.message(F.text == "💰 رصيدي")
async def show_balance(message: types.Message):
    username = my_username(message)
    if not username:
        await message.answer("اضغط /start أولاً لتسجيل اسمك")
        return
    user = get_user(username)
    await message.answer(
        f"💰 رصيدك الحالي: {user['balance']:.2f} USDT\n"
        f"📥 مبلغ الإيداع: {user['deposit']:.2f} USDT\n"
        f"🔄 تم استرداد: {user['capital_recovered']:.2f} USDT"
    )


@dp.message(F.text == "📊 تفاصيل حسابي")
async def show_details(message: types.Message):
    username = my_username(message)
    if not username:
        await message.answer("اضغط /start أولاً لتسجيل اسمك")
        return
    user = get_user(username)
    days_left = 0
    if user["deposit_date"]:
        dep_date = datetime.fromisoformat(user["deposit_date"])
        elapsed = (datetime.now() - dep_date).days
        days_left = max(0, CAPITAL_RECOVERY_DAYS - elapsed)

    daily = DAILY_TASKS.get(int(user["deposit"]), 0)
    referrals = get_referrals(username)
    await message.answer(
        f"📊 تفاصيل حسابك:\n\n"
        f"💰 الرصيد: {user['balance']:.2f} USDT\n"
        f"📥 الإيداع: {user['deposit']:.2f} USDT\n"
        f"📈 المهمة اليومية: {daily:.2f} USDT\n"
        f"📅 أيام الاسترداد المتبقية: {days_left} يوم\n"
        f"🔄 تم استرداد رأس المال: {user['capital_recovered']:.2f} USDT\n"
        f"👥 عدد الإحالات: {len(referrals)}"
    )


@dp.message(F.text == "📥 الإيداع")
async def deposit_menu(message: types.Message, state: FSMContext):
    username = my_username(message)
    if not username:
        await message.answer("اضغط /start أولاً لتسجيل اسمك")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="BEP-20 (BSC)", callback_data="deposit_bep20")],
        [InlineKeyboardButton(text="TRC-20 (TRON)", callback_data="deposit_trc20")],
    ])
    await state.set_state(DepositForm.waiting_for_network)
    await message.answer("اختر شبكة الإيداع:", reply_markup=kb)


@dp.callback_query(F.data == "deposit_bep20", DepositForm.waiting_for_network)
async def deposit_bep20(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(network="bsc")
    await state.set_state(DepositForm.waiting_for_hash)
    await callback.message.answer(
        f"📥 عنوان الإيداع (BEP-20 / USDT-BSC):\n\n"
        f"<code>{BEP20_ADDRESS}</code>\n\n"
        f"⚠️ بعد التحويل، أرسل رقم العملية (Transaction Hash)\n"
        f"يكون على شكل:\n"
        f"<code>0x1234567890abcdef...</code>",
    )
    await callback.answer()


@dp.callback_query(F.data == "deposit_trc20", DepositForm.waiting_for_network)
async def deposit_trc20(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(network="tron")
    await state.set_state(DepositForm.waiting_for_hash)
    await callback.message.answer(
        f"📥 عنوان الإيداع (TRC-20 / USDT-TRON):\n\n"
        f"<code>{TRC20_ADDRESS}</code>\n\n"
        f"⚠️ بعد التحويل، أرسل رقم العملية (Transaction Hash)\n"
        f"يكون على شكل:\n"
        f"<code>abcdef1234567890...</code>",
    )
    await callback.answer()


@dp.message(DepositForm.waiting_for_hash)
async def process_hash(message: types.Message, state: FSMContext):
    tx_hash = message.text.strip()
    if len(tx_hash) < 10:
        await message.answer("❌ رقم العملية غير صحيح. أعد الإرسال.")
        return
    await state.update_data(tx_hash=tx_hash)
    await state.set_state(DepositForm.waiting_for_amount)
    await message.answer("💰 أرسل مبلغ الإيداع بال USDT (مثال: 10)")


@dp.message(DepositForm.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    username = my_username(message)
    if not username:
        await message.answer("اضغط /start أولاً لتسجيل اسمك")
        await state.clear()
        return

    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("❌ مبلغ غير صحيح. أعد الإرسال.")
        return

    if amount not in DAILY_TASKS:
        await message.answer(
            "❌ المبلغ غير مدعوم.\n"
            "القيم المتاحة: 1, 10, 30, 50, 100 USDT"
        )
        return

    state_data = await state.get_data()
    network = state_data.get("network")
    tx_hash = state_data.get("tx_hash")

    used_hashes = get_all_used_hashes()

    await message.answer("🔍 جارٍ التحقق من الإيداع... يرجى الانتظار.")

    if tx_hash in used_hashes:
        await message.answer("❌ هذه العملية تم استخدامها من قبل.")
        await state.clear()
        return

    confirmed = False

    if network == "bsc":
        deposits = check_bsc_deposit(BEP20_ADDRESS, amount)
        for dep in deposits:
            if dep["hash"].lower() == tx_hash.lower() and dep["amount"] == amount:
                confirmed = True
                break
    elif network == "tron":
        deposits = check_trc_deposit(TRC20_ADDRESS, amount)
        for dep in deposits:
            if dep["hash"].lower() == tx_hash.lower() and dep["amount"] == amount:
                confirmed = True
                break

    if confirmed:
        update_user(
            username,
            deposit=amount,
            deposit_date=datetime.now().isoformat(),
            capital_recovered=0.0,
        )
        add_used_hash(username, tx_hash)

        user = get_user(username)
        if user.get("referral_by"):
            ref_name = user["referral_by"]
            ref_balance = get_user(ref_name)["balance"]
            commission = amount * REFERRAL_COMMISSION
            update_user(ref_name, balance=ref_balance + commission)

        daily = DAILY_TASKS.get(int(amount), 0)
        await message.answer(
            f"✅ تم تأكيد الإيداع: {amount:.2f} USDT\n"
            f"🔗 العملية: <code>{tx_hash}</code>\n\n"
            f"📈 المهمة اليومية: {daily:.2f} USDT\n"
            f"📅 مدة الاسترداد: {CAPITAL_RECOVERY_DAYS} يوم"
        )
    else:
        await message.answer(
            "❌ لم يتم العثور على هذه العملية أو المبلغ غير متطابق.\n"
            "تأكد من رقم العملية والمبلغ وأعد المحاولة."
        )

    await state.clear()


@dp.message(F.text == "📤 السحب")
async def withdraw(message: types.Message):
    username = my_username(message)
    if not username:
        await message.answer("اضغط /start أولاً لتسجيل اسمك")
        return
    user = get_user(username)

    if user["balance"] < 1:
        await message.answer("❌ رصيدك غير كافٍ للسحب. الحد الأدنى: 1 USDT")
        return

    if user["last_withdraw"]:
        last = datetime.fromisoformat(user["last_withdraw"])
        diff = (datetime.now() - last).days
        if diff < WITHDRAW_COOLDOWN_DAYS:
            remaining = WITHDRAW_COOLDOWN_DAYS - diff
            await message.answer(f"❌ يمكنك السحب بعد {remaining} يوم(أيام).")
            return

    await message.answer(
        f"📤 طلب سحب: {user['balance']:.2f} USDT\n\n"
        f"سيتم التحويل إلى عنوانك المسجل.\n"
        f"أرسل 'تأكيد السحب' للتأكيد."
    )


@dp.message(F.text == "تأكيد السحب")
async def confirm_withdraw(message: types.Message):
    username = my_username(message)
    if not username:
        await message.answer("اضغط /start أولاً لتسجيل اسمك")
        return
    user = get_user(username)

    if user["balance"] < 1:
        await message.answer("❌ رصيدك غير كافٍ.")
        return

    if user["last_withdraw"]:
        last = datetime.fromisoformat(user["last_withdraw"])
        diff = (datetime.now() - last).days
        if diff < WITHDRAW_COOLDOWN_DAYS:
            await message.answer("❌ فترة الانتظار لم تنتهِ بعد.")
            return

    amount = user["balance"]
    update_user(username, balance=0.0, last_withdraw=datetime.now().isoformat())

    await message.answer(
        f"✅ تم تسجيل طلب السحب: {amount:.2f} USDT\n"
        f"سيتم التحويل خلال 24 ساعة."
    )


@dp.message(F.text == "🔗 رابط الإحالة")
async def referral_link(message: types.Message):
    username = my_username(message)
    if not username:
        await message.answer("اضغط /start أولاً لتسجيل اسمك")
        return
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    referrals = get_referrals(username)
    await message.answer(
        f"🔗 رابط الإحالة الخاص بك:\n\n{link}\n\n"
        f"👥 عدد إحالاتك: {len(referrals)}\n"
        f"💎 عمولة الإحالة: 10%"
    )


@dp.message(F.text == "📋 المهام اليومية")
async def daily_tasks(message: types.Message):
    username = my_username(message)
    if not username:
        await message.answer("اضغط /start أولاً لتسجيل اسمك")
        return
    user = get_user(username)

    if user["deposit"] == 0:
        await message.answer("❌ يجب عليك الإيداع أولاً لتفعيل المهام اليومية.")
        return

    dep_amount = int(user["deposit"])
    daily_reward = DAILY_TASKS.get(dep_amount, 0)

    today = datetime.now().strftime("%Y-%m-%d")
    claimed = get_daily_tasks(username)
    if today in claimed:
        await message.answer("✅ لقد قمت بمهمتك اليومية بالفعل اليوم.\nعد غدً.")
        return

    add_daily_task(username, today)
    new_balance = user["balance"] + daily_reward
    update_user(username, balance=new_balance)

    await message.answer(
        f"✅ تم تنفيذ المهمة اليومية!\n"
        f"💰 المكافأة: {daily_reward:.2f} USDT\n"
        f"💎 رصيدك الحالي: {new_balance:.2f} USDT"
    )


# ======================== DAILY CAPITAL RECOVERY ========================

async def capital_recovery_task():
    while True:
        now = datetime.now()
        for username in get_all_usernames():
            user = load_user(username)
            if not user:
                continue
            tg_id = user.get("telegram_id", 0)
            if tg_id and user["deposit"] > 0 and user["deposit_date"]:
                dep_date = datetime.fromisoformat(user["deposit_date"])
                days_passed = (now - dep_date).days
                expected_recovered = min(
                    days_passed * (user["deposit"] / CAPITAL_RECOVERY_DAYS),
                    user["deposit"],
                )
                diff = expected_recovered - user["capital_recovered"]
                if diff > 0:
                    new_recovered = round(expected_recovered, 2)
                    new_balance = round(user["balance"] + diff, 2)
                    update_user(username, capital_recovered=new_recovered, balance=new_balance)
                    try:
                        await bot.send_message(
                            tg_id,
                            f"🔄 استرداد رأس المال اليومي: +{diff:.2f} USDT\n"
                            f"💎 رصيدك الحالي: {new_balance:.2f} USDT",
                        )
                    except Exception:
                        pass
        await asyncio.sleep(86400)


# ======================== MAIN ========================

def run_health_server():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    port = int(os.getenv("PORT", "7860"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


async def main():
    asyncio.create_task(capital_recovery_task())
    thread = asyncio.to_thread(run_health_server)
    asyncio.create_task(thread)
    print("Bot is running...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())