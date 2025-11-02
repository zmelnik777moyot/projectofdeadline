from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import logging
import sqlite3

# Включаем логирование, чтобы не пропустить важные сообщения
conn = sqlite3.connect('polz.db') 
cursor = conn.cursor() 
logging.basicConfig(level=logging.INFO)
# Объект бота
bot = Bot(token="8469594997:AAGw-wNxW4e-vPYAR50ROcrfW8Y5gTRJxc8")
# Диспетчер
dp = Dispatcher()

class ScheduleForm(StatesGroup):
    waiting_for_day = State()
    waiting_for_text = State()


@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT first_name FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if result:
        # Пользователь найден
        first_name = result[0]
        kb = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="📅 Составить расписание")],
            ],
            resize_keyboard=True
        )
        await message.answer(
            f"Привет снова, {first_name}! 👋\nГотов составить новое расписание?",
            reply_markup=kb
        )
    else:
        # Новый пользователь — просим номер
        kb = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            "Привет! 👋 Для регистрации, пожалуйста, отправь свой номер телефона:",
            reply_markup=kb
        )


# --- Обработка контакта ---
@dp.message(lambda message: message.contact is not None)
async def contact_handler(message: types.Message):
    contact = message.contact
    user_id = message.from_user.id
    phone = contact.phone_number
    first_name = contact.first_name or message.from_user.first_name

    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO users (user_id, first_name, phone) VALUES (?, ?, ?)",
                       (user_id, first_name, phone))
        conn.commit()
        await message.answer(
            f"✅ Спасибо, {first_name}! Ты успешно зарегистрирован.\nТеперь можешь добавить своё расписание 📅",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="📅 Составить расписание")]],
                resize_keyboard=True
            )
        )
    else:
        await message.answer(
            "Ты уже зарегистрирован ✅",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="📅 Составить расписание")]],
                resize_keyboard=True
            )
        )

@dp.message(Command("schedule"))
async def schedule_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        await message.answer("⚠️ Сначала зарегистрируйся через /start, чтобы добавить расписание.")
        return

    await message.answer("📅 На какой день недели хочешь добавить расписание?")
    await state.set_state(ScheduleForm.waiting_for_day)


# --- Этап 1: ввод дня ---
@dp.message(ScheduleForm.waiting_for_day)
async def schedule_day(message: types.Message, state: FSMContext):
    await state.update_data(day=message.text)
    await message.answer("✏️ Отлично! Теперь отправь текст расписания (например, «Учёба с 9:00 до 14:00»).")
    await state.set_state(ScheduleForm.waiting_for_text)


# --- Этап 2: ввод текста расписания ---
@dp.message(ScheduleForm.waiting_for_text)
async def schedule_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    day = data["day"]
    text = message.text
    user_id = message.from_user.id

    cursor.execute("INSERT INTO schedule (user_id, day, text) VALUES (?, ?, ?)", (user_id, day, text))
    conn.commit()

    await message.answer(f"✅ Расписание на *{day}* добавлено:\n_{text}_", parse_mode="Markdown")
    await state.clear()

# --- Запуск бота ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
