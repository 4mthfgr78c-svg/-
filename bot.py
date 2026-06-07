import asyncio
import sqlite3
import json
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
COURIER_PASSWORD = os.getenv('COURIER_PASSWORD')

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ============ БАЗА ДАННЫХ ============
def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            role TEXT DEFAULT 'user',
            balance INTEGER DEFAULT 0,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_tg_id INTEGER,
            items TEXT,
            total_price INTEGER,
            status TEXT DEFAULT 'new',
            courier_tg_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_at TIMESTAMP,
            cooking_started_at TIMESTAMP,
            ready_at TIMESTAMP,
            assigned_at TIMESTAMP,
            delivered_at TIMESTAMP,
            payment_url TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS courier_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            courier_tg_id INTEGER,
            date DATE DEFAULT CURRENT_DATE,
            orders_delivered INTEGER DEFAULT 0,
            total_delivery_time INTEGER DEFAULT 0
        )
    ''')
    
    conn.commit()
    conn.close()
    print("База данных готова")

init_db()

# ============ СОСТОЯНИЯ FSM ============
class AdminStates(StatesGroup):
    waiting_payment_link = State()
    waiting_admin_password = State()

class CourierStates(StatesGroup):
    waiting_courier_password = State()

class CartStates(StatesGroup):
    adding_to_cart = State()

# ============ ТОВАРЫ ============
products = {
    'classic': {'name': 'Классическая шаурма', 'price': 350, 'emoji': '🌯'},
    'signature': {'name': 'Фирменная шаурма', 'price': 500, 'emoji': '🔥'},
    'cola': {'name': 'Добрая кола', 'price': 100, 'emoji': '🥤'}
}

toppings = {
    'cheese': {'name': 'Сыр', 'price': 50},
    'jalapeno': {'name': 'Халапеньо', 'price': 50},
    'carrot': {'name': 'Морковь по-корейски', 'price': 50},
    'kimchi': {'name': 'Кимчи', 'price': 50}
}

# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============
def get_user(tg_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute('INSERT INTO users (tg_id, username) VALUES (?, ?)', (tg_id, f"user_{tg_id}"))
        conn.commit()
        cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
        user = cursor.fetchone()
    conn.close()
    return user

def set_user_role(tg_id, role):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET role = ? WHERE tg_id = ?', (role, tg_id))
    conn.commit()
    conn.close()

def get_status_emoji(status):
    emojis = {
        'new': '🆕',
        'waiting_payment': '💳',
        'paid': '✅',
        'cooking': '👨‍🍳',
        'ready': '📦',
        'delivering': '🚚',
        'delivered': '🏁',
        'cancelled': '❌'
    }
    return emojis.get(status, '❓')

def generate_order_id():
    import random
    import time
    return f"{int(time.time())}{random.randint(1000, 9999)}"

# ============ КЛАВИАТУРЫ ============
async def show_main_menu(message: types.Message):
    user = get_user(message.from_user.id)
    role = user[2]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    if role == 'user':
        keyboard.inline_keyboard = [
            [InlineKeyboardButton(text='🍽 Меню', callback_data='menu')],
            [InlineKeyboardButton(text='🚴 Стать курьером', callback_data='become_courier')],
            [InlineKeyboardButton(text='📦 Мой заказ', callback_data='my_order')],
            [InlineKeyboardButton(text='🔐 Войти как админ', callback_data='admin_login')]
        ]
    elif role == 'courier':
        keyboard.inline_keyboard = [
            [InlineKeyboardButton(text='📋 Доступные заказы', callback_data='available_orders')],
            [InlineKeyboardButton(text='💰 Мой баланс', callback_data='balance')],
            [InlineKeyboardButton(text='⭐ Топ курьеров', callback_data='top_couriers')],
            [InlineKeyboardButton(text='🔓 Выйти', callback_data='logout_courier')]
        ]
    elif role == 'admin':
        keyboard.inline_keyboard = [
            [InlineKeyboardButton(text='🆕 Новые заказы', callback_data='admin_new_orders')],
            [InlineKeyboardButton(text='💳 Ожидают оплаты', callback_data='admin_waiting_payment')],
            [InlineKeyboardButton(text='✅ Оплаченные', callback_data='admin_paid_orders')],
            [InlineKeyboardButton(text='👨‍🍳 В готовке', callback_data='admin_cooking_orders')],
            [InlineKeyboardButton(text='📦 Готовые', callback_data='admin_ready_orders')],
            [InlineKeyboardButton(text='🚚 В доставке', callback_data='admin_delivering_orders')],
            [InlineKeyboardButton(text='💰 Расчёт курьеров', callback_data='admin_calc_couriers')],
            [InlineKeyboardButton(text='🚪 Выйти', callback_data='admin_logout')]
        ]
    
    await message.answer('🥙 ДаркКитчен\nВыберите действие:', reply_markup=keyboard)

# ============ КОРЗИНА (в памяти, для простоты) ============
user_carts = {}

# ============ ОБРАБОТЧИКИ ============
@dp.message(Command('start'))
async def cmd_start(message: types.Message):
    user_carts[message.from_user.id] = []
    await show_main_menu(message)

@dp.callback_query(F.data == 'menu')
async def cmd_menu(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{products['classic']['emoji']} {products['classic']['name']} - {products['classic']['price']}₽", callback_data='add_classic')],
        [InlineKeyboardButton(text=f"{products['signature']['emoji']} {products['signature']['name']} - {products['signature']['price']}₽", callback_data='add_signature')],
        [InlineKeyboardButton(text='➕ Топпинг (+50₽)', callback_data='add_topping')],
        [InlineKeyboardButton(text=f"{products['cola']['emoji']} {products['cola']['name']} - {products['cola']['price']}₽", callback_data='add_cola')],
        [InlineKeyboardButton(text='🛒 Корзина', callback_data='view_cart'), InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')]
    ])
    await callback.message.edit_text('🍽 Меню:', reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == 'add_topping')
async def cmd_toppings(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🧀 Сыр - 50₽', callback_data='add_topping_cheese')],
        [InlineKeyboardButton(text='🌶 Халапеньо - 50₽', callback_data='add_topping_jalapeno')],
        [InlineKeyboardButton(text='🥕 Морковь по-корейски - 50₽', callback_data='add_topping_carrot')],
        [InlineKeyboardButton(text='🥬 Кимчи - 50₽', callback_data='add_topping_kimchi')],
        [InlineKeyboardButton(text='🔙 Назад', callback_data='menu')]
    ])
    await callback.message.edit_text('➕ Выберите топпинг:', reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith('add_'))
async def add_to_cart(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_carts:
        user_carts[user_id] = []
    
    if callback.data == 'add_classic':
        user_carts[user_id].append(products['classic'])
        await callback.answer(f"✅ Добавлено: {products['classic']['name']}")
    elif callback.data == 'add_signature':
        user_carts[user_id].append(products['signature'])
        await callback.answer(f"✅ Добавлено: {products['signature']['name']}")
    elif callback.data == 'add_cola':
        user_carts[user_id].append(products['cola'])
        await callback.answer(f"✅ Добавлено: {products['cola']['name']}")
    elif callback.data == 'add_topping_cheese':
        user_carts[user_id].append(toppings['cheese'])
        await callback.answer("✅ Добавлено: Сыр (+50₽)")
    elif callback.data == 'add_topping_jalapeno':
        user_carts[user_id].append(toppings['jalapeno'])
        await callback.answer("✅ Добавлено: Халапеньо (+50₽)")
    elif callback.data == 'add_topping_carrot':
        user_carts[user_id].append(toppings['carrot'])
        await callback.answer("✅ Добавлено: Морковь по-корейски (+50₽)")
    elif callback.data == 'add_topping_kimchi':
        user_carts[user_id].append(toppings['kimchi'])
        await callback.answer("✅ Добавлено: Кимчи (+50₽)")

@dp.callback_query(F.data == 'view_cart')
async def view_cart(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    cart = user_carts.get(user_id, [])
    
    if not cart:
        await callback.message.edit_text('🛒 Корзина пуста')
        await callback.answer()
        return
    
    total = sum(item['price'] for item in cart)
    items_list = '\n'.join([f"{i+1}. {item['name']} - {item['price']}₽" for i, item in enumerate(cart)])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Оформить заказ', callback_data='checkout')],
        [InlineKeyboardButton(text='🗑 Очистить', callback_data='clear_cart'), InlineKeyboardButton(text='🔙 Назад', callback_data='menu')]
    ])
    
    await callback.message.edit_text(f"🛒 Ваша корзина:\n{items_list}\n\n💰 Итого: {total}₽", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == 'clear_cart')
async def clear_cart(callback: types.CallbackQuery):
    user_carts[callback.from_user.id] = []
    await callback.answer('Корзина очищена')
    await cmd_menu(callback)

@dp.callback_query(F.data == 'checkout')
async def checkout(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    cart = user_carts.get(user_id, [])
    
    if not cart:
        await callback.answer('Корзина пуста')
        return
    
    order_id = generate_order_id()
    total = sum(item['price'] for item in cart)
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO orders (id, user_tg_id, items, total_price, status) VALUES (?, ?, ?, ?, ?)',
        (order_id, user_id, json.dumps(cart), total, 'new')
    )
    conn.commit()
    conn.close()
    
    # Уведомляем админов
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT tg_id FROM users WHERE role = "admin"')
    admins = cursor.fetchall()
    conn.close()
    
    items_text = ', '.join([item['name'] for item in cart])
    
    for admin in admins:
        await bot.send_message(admin[0], 
            f"🆕 <b>НОВЫЙ ЗАКАЗ #{order_id[:8]}</b>\n"
            f"👤 Клиент: @{callback.from_user.username or callback.from_user.id}\n"
            f"💰 Сумма: {total}₽\n"
            f"🍽 Состав: {items_text}",
            parse_mode='HTML'
        )
    
    user_carts[user_id] = []
    await callback.message.edit_text(f"✅ Заказ #{order_id[:8]} создан!\nСумма: {total}₽\nОжидайте ссылку на оплату от администратора.")
    await callback.answer()

@dp.callback_query(F.data == 'back_to_menu')
async def back_to_menu(callback: types.CallbackQuery):
    await show_main_menu(callback.message)
    await callback.answer()

@dp.callback_query(F.data == 'become_courier')
async def become_courier(callback: types.CallbackQuery, state: FSMContext):
    user = get_user(callback.from_user.id)
    if user[2] == 'courier':
        await callback.answer('Вы уже курьер!')
        return
    
    await state.set_state(CourierStates.waiting_courier_password)
    await callback.message.answer('🔑 Введите пароль для регистрации курьера:')
    await callback.answer()

@dp.callback_query(F.data == 'admin_login')
async def admin_login(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_admin_password)
    await callback.message.answer('🔐 Введите пароль администратора:')
    await callback.answer()

@dp.message(AdminStates.waiting_admin_password)
async def process_admin_password(message: types.Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        set_user_role(message.from_user.id, 'admin')
        await state.clear()
        await message.answer('✅ Добро пожаловать в админ-панель!')
        await show_main_menu(message)
    else:
        await state.clear()
        await message.answer('❌ Неверный пароль!')
        await show_main_menu(message)

@dp.message(CourierStates.waiting_courier_password)
async def process_courier_password(message: types.Message, state: FSMContext):
    if message.text == COURIER_PASSWORD:
        set_user_role(message.from_user.id, 'courier')
        await state.clear()
        await message.answer('✅ Поздравляем! Вы теперь курьер! 🚴')
        await show_main_menu(message)
    else:
        await state.clear()
        await message.answer('❌ Неверный пароль!')
        await show_main_menu(message)

@dp.callback_query(F.data == 'logout_courier')
async def logout_courier(callback: types.CallbackQuery):
    set_user_role(callback.from_user.id, 'user')
    await callback.message.answer('Вы вышли из аккаунта курьера')
    await show_main_menu(callback.message)
    await callback.answer()

@dp.callback_query(F.data == 'admin_logout')
async def admin_logout(callback: types.CallbackQuery):
    set_user_role(callback.from_user.id, 'user')
    await callback.message.answer('Вы вышли из админки')
    await show_main_menu(callback.message)
    await callback.answer()

@dp.callback_query(F.data == 'my_order')
async def my_order(callback: types.CallbackQuery):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM orders WHERE user_tg_id = ? AND status NOT IN ("delivered", "cancelled") ORDER BY created_at DESC LIMIT 1',
        (callback.from_user.id,)
    )
    order = cursor.fetchone()
    conn.close()
    
    if not order:
        await callback.message.answer('📭 У вас нет активных заказов')
        await callback.answer()
        return
    
    items = json.loads(order[3])
    status = get_status_emoji(order[5])
    
    await callback.message.answer(
        f"📦 Заказ #{order[0][:8]}\n"
        f"Статус: {status}\n"
        f"💰 Сумма: {order[4]}₽\n"
        f"🍽 Состав: {', '.join([i['name'] for i in items])}"
    )
    await callback.answer()

@dp.callback_query(F.data == 'balance')
async def show_balance(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    await callback.message.answer(f"💰 Ваш баланс: {user[3]}₽")
    await callback.answer()

# ============ АДМИН-ФУНКЦИИ ============
async def show_orders_by_status(callback: types.CallbackQuery, status, title):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM orders WHERE status = ? ORDER BY created_at ASC', (status,))
    orders = cursor.fetchall()
    conn.close()
    
    if not orders:
        await callback.message.answer(f"📭 Нет заказов со статусом \"{title}\"")
        await callback.answer()
        return
    
    for order in orders:
        items = json.loads(order[3])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        if status == 'new':
            keyboard.inline_keyboard = [
                [InlineKeyboardButton(text='💳 Отправить ссылку', callback_data=f'admin_send_link_{order[0]}')],
                [InlineKeyboardButton(text='❌ Отказать', callback_data=f'admin_cancel_{order[0]}')]
            ]
        elif status == 'waiting_payment':
            keyboard.inline_keyboard = [
                [InlineKeyboardButton(text='✅ Подтвердить оплату', callback_data=f'admin_confirm_payment_{order[0]}')],
                [InlineKeyboardButton(text='🔄 Другую ссылку', callback_data=f'admin_send_link_{order[0]}')]
            ]
        elif status == 'paid':
            keyboard.inline_keyboard = [
                [InlineKeyboardButton(text='👨‍🍳 Начать готовку', callback_data=f'admin_start_cooking_{order[0]}')]
            ]
        elif status == 'cooking':
            keyboard.inline_keyboard = [
                [InlineKeyboardButton(text='✅ Готов к выдаче', callback_data=f'admin_mark_ready_{order[0]}')]
            ]
        elif status == 'ready':
            keyboard.inline_keyboard = [
                [InlineKeyboardButton(text='🚚 Назначить курьера', callback_data=f'admin_assign_courier_{order[0]}')]
            ]
        elif status == 'delivering':
            keyboard.inline_keyboard = [
                [InlineKeyboardButton(text='✅ Подтвердить доставку', callback_data=f'admin_confirm_delivery_{order[0]}')]
            ]
        
        await callback.message.answer(
            f"📦 <b>Заказ #{order[0][:8]}</b>\n"
            f"👤 Клиент: {order[1]}\n"
            f"💰 Сумма: {order[4]}₽\n"
            f"🍽 Состав: {', '.join([i['name'] for i in items])}\n"
            f"📅 Создан: {order[6]}\n"
            f"🏷 Статус: {get_status_emoji(order[5])}",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    
    await callback.answer()

@dp.callback_query(F.data == 'admin_new_orders')
async def admin_new_orders(callback: types.CallbackQuery):
    await show_orders_by_status(callback, 'new', 'Новые')

@dp.callback_query(F.data == 'admin_waiting_payment')
async def admin_waiting_payment(callback: types.CallbackQuery):
    await show_orders_by_status(callback, 'waiting_payment', 'Ожидают оплаты')

@dp.callback_query(F.data == 'admin_paid_orders')
async def admin_paid_orders(callback: types.CallbackQuery):
    await show_orders_by_status(callback, 'paid', 'Оплаченные')

@dp.callback_query(F.data == 'admin_cooking_orders')
async def admin_cooking_orders(callback: types.CallbackQuery):
    await show_orders_by_status(callback, 'cooking', 'В готовке')

@dp.callback_query(F.data == 'admin_ready_orders')
async def admin_ready_orders(callback: types.CallbackQuery):
    await show_orders_by_status(callback, 'ready', 'Готовые')

@dp.callback_query(F.data == 'admin_delivering_orders')
async def admin_delivering_orders(callback: types.CallbackQuery):
    await show_orders_by_status(callback, 'delivering', 'В доставке')

@dp.callback_query(F.data.startswith('admin_send_link_'))
async def admin_send_link(callback: types.CallbackQuery, state: FSMContext):
    order_id = callback.data.replace('admin_send_link_', '')
    await state.update_data(order_id=order_id)
    await state.set_state(AdminStates.waiting_payment_link)
    await callback.message.answer('📎 Вставьте ссылку на оплату с ЮMoney:')
    await callback.answer()

@dp.message(AdminStates.waiting_payment_link)
async def process_payment_link(message: types.Message, state: FSMContext):
    payment_url = message.text
    data = await state.get_data()
    order_id = data.get('order_id')
    
    if 'yoomoney.ru' not in payment_url:
        await message.answer('❌ Это не похоже на ссылку ЮMoney. Попробуйте ещё раз')
        return
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
    order = cursor.fetchone()
    
    cursor.execute('UPDATE orders SET payment_url = ?, status = "waiting_payment" WHERE id = ?', (payment_url, order_id))
    conn.commit()
    conn.close()
    
    await bot.send_message(order[1],
        f"💳 Заказ #{order_id[:8]} на сумму {order[4]}₽\n\n"
        f"Оплатите по ссылке:\n{payment_url}\n\n"
        f"После оплаты нажмите кнопку 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='✅ Оплатил', callback_data=f'user_paid_{order_id}')]
        ])
    )
    
    await message.answer(f"✅ Ссылка отправлена клиенту!")
    await state.clear()

@dp.callback_query(F.data.startswith('admin_confirm_payment_'))
async def admin_confirm_payment(callback: types.CallbackQuery):
    order_id = callback.data.replace('admin_confirm_payment_', '')
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = "paid", paid_at = CURRENT_TIMESTAMP WHERE id = ?', (order_id,))
    conn.commit()
    conn.close()
    await callback.message.answer('✅ Оплата подтверждена')
    await callback.answer()

@dp.callback_query(F.data.startswith('admin_start_cooking_'))
async def admin_start_cooking(callback: types.CallbackQuery):
    order_id = callback.data.replace('admin_start_cooking_', '')
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = "cooking", cooking_started_at = CURRENT_TIMESTAMP WHERE id = ?', (order_id,))
    conn.commit()
    conn.close()
    await callback.message.answer('👨‍🍳 Готовка начата')
    await callback.answer()

@dp.callback_query(F.data.startswith('admin_mark_ready_'))
async def admin_mark_ready(callback: types.CallbackQuery):
    order_id = callback.data.replace('admin_mark_ready_', '')
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = "ready", ready_at = CURRENT_TIMESTAMP WHERE id = ?', (order_id,))
    conn.commit()
    conn.close()
    await callback.message.answer('✅ Заказ готов к выдаче')
    await callback.answer()

@dp.callback_query(F.data.startswith('admin_assign_courier_'))
async def admin_assign_courier(callback: types.CallbackQuery):
    order_id = callback.data.replace('admin_assign_courier_', '')
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT tg_id, username FROM users WHERE role = "courier"')
    couriers = cursor.fetchall()
    conn.close()
    
    if not couriers:
        await callback.message.answer('❌ Нет зарегистрированных курьеров')
        await callback.answer()
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for courier in couriers:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"🚴 {courier[1] or courier[0]}", callback_data=f'assign_courier_{order_id}_{courier[0]}')
        ])
    
    await callback.message.answer('Выберите курьера:', reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith('assign_courier_'))
async def assign_courier(callback: types.CallbackQuery):
    parts = callback.data.split('_')
    order_id = parts[2]
    courier_id = int(parts[3])
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = "delivering", courier_tg_id = ?, assigned_at = CURRENT_TIMESTAMP WHERE id = ?', 
                   (courier_id, order_id))
    cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
    order = cursor.fetchone()
    conn.commit()
    conn.close()
    
    await bot.send_message(courier_id,
        f"🚚 Вам назначен заказ #{order_id[:8]}!\n"
        f"💰 {order[4]}₽\n"
        f"⏱ Доставьте за 30 минут",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='✅ Доставил', callback_data=f'courier_delivered_{order_id}')]
        ])
    )
    
    await callback.message.answer(f"✅ Курьер назначен")
    await callback.answer()

@dp.callback_query(F.data.startswith('admin_confirm_delivery_'))
async def admin_confirm_delivery(callback: types.CallbackQuery):
    order_id = callback.data.replace('admin_confirm_delivery_', '')
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = "delivered", delivered_at = CURRENT_TIMESTAMP WHERE id = ?', (order_id,))
    conn.commit()
    conn.close()
    await callback.message.answer('✅ Доставка подтверждена')
    await callback.answer()

@dp.callback_query(F.data.startswith('admin_cancel_'))
async def admin_cancel(callback: types.CallbackQuery):
    order_id = callback.data.replace('admin_cancel_', '')
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = "cancelled" WHERE id = ?', (order_id,))
    conn.commit()
    conn.close()
    await callback.message.answer(f'❌ Заказ отменён')
    await callback.answer()

@dp.callback_query(F.data.startswith('user_paid_'))
async def user_paid(callback: types.CallbackQuery):
    order_id = callback.data.replace('user_paid_', '')
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = "paid", paid_at = CURRENT_TIMESTAMP WHERE id = ?', (order_id,))
    conn.commit()
    
    cursor.execute('SELECT tg_id FROM users WHERE role = "admin"')
    admins = cursor.fetchall()
    conn.close()
    
    await callback.message.answer('✅ Спасибо! Администратор получит уведомление.')
    
    for admin in admins:
        await bot.send_message(admin[0], f"💰 Клиент оплатил заказ #{order_id[:8]}")
    
    await callback.answer()

@dp.callback_query(F.data.startswith('courier_delivered_'))
async def courier_delivered(callback: types.CallbackQuery):
    order_id = callback.data.replace('courier_delivered_', '')
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
    order = cursor.fetchone()
    
    # Проверяем время доставки
    assigned_at = datetime.fromisoformat(order[11])
    delivered_at = datetime.now()
    minutes_diff = (delivered_at - assigned_at).total_seconds() / 60
    
    delivery_fee = 100
    bonus_message = ''
    
    if minutes_diff > 30:
        delivery_fee = 0
        bonus_message = '\n\n⚠️ Превышен лимит времени! Оплата не начислена.'
    
    cursor.execute('UPDATE users SET balance = balance + ? WHERE tg_id = ?', (delivery_fee, order[7]))
    cursor.execute('UPDATE orders SET status = "delivered", delivered_at = CURRENT_TIMESTAMP WHERE id = ?', (order_id,))
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"✅ Доставка завершена!{bonus_message}\n💰 Начислено: {delivery_fee}₽")
    await callback.answer()

@dp.callback_query(F.data == 'available_orders')
async def available_orders(callback: types.CallbackQuery):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM orders WHERE status = "ready" AND courier_tg_id IS NULL ORDER BY created_at ASC')
    orders = cursor.fetchall()
    conn.close()
    
    if not orders:
        await callback.message.answer('📭 Нет доступных заказов')
        await callback.answer()
        return
    
    for order in orders:
        items = json.loads(order[3])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🚚 Взять заказ', callback_data=f'take_order_{order[0]}')]
        ])
        
        await callback.message.answer(
            f"📦 Заказ #{order[0][:8]}\n"
            f"💰 Сумма: {order[4]}₽\n"
            f"🍽 Состав: {', '.join([i['name'] for i in items])}\n"
            f"⏱ Доставить за 30 минут",
            reply_markup=keyboard
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith('take_order_'))
async def take_order(callback: types.CallbackQuery):
    order_id = callback.data.replace('take_order_', '')
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM orders WHERE id = ? AND status = "ready" AND courier_tg_id IS NULL', (order_id,))
    order = cursor.fetchone()
    
    if not order:
        await callback.message.answer('❌ Заказ уже кто-то взял')
        await callback.answer()
        return
    
    cursor.execute('UPDATE orders SET status = "delivering", courier_tg_id = ?, assigned_at = CURRENT_TIMESTAMP WHERE id = ?',
                   (callback.from_user.id, order_id))
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"✅ Заказ #{order_id[:8]} взят!\n⏱ Доставьте за 30 минут",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='✅ Доставил', callback_data=f'courier_delivered_{order_id}')]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == 'top_couriers')
async def top_couriers(callback: types.CallbackQuery):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.username, cs.orders_delivered, cs.total_delivery_time 
        FROM courier_stats cs 
        JOIN users u ON u.tg_id = cs.courier_tg_id 
        WHERE cs.date = CURRENT_DATE 
        ORDER BY cs.orders_delivered DESC 
        LIMIT 5
    ''')
    top = cursor.fetchall()
    conn.close()
    
    if not top:
        await callback.message.answer('📊 Сегодня ещё нет доставок')
        await callback.answer()
        return
    
    message = '🏆 Топ курьеров сегодня:\n\n'
    for i, row in enumerate(top):
        avg_time = round(row[2] / row[1]) if row[1] > 0 else 0
        message += f"{i+1}. {row[0] or 'Курьер'} — {row[1]} доставок, среднее {avg_time} мин\n"
    
    await callback.message.answer(message)
    await callback.answer()

@dp.callback_query(F.data == '