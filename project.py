from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import logging
import sqlite3
import re
import speech_recognition as sr
from pydub import AudioSegment
import io
from datetime import datetime, timedelta
import os
import aiogram 
# Включаем логирование, чтобы не пропустить важные сообщения
conn = sqlite3.connect('polz.db') 
cursor = conn.cursor()

# Создаем таблицы если их нет
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    phone TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    day TEXT,
    text TEXT,
    reminder_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    reminder_text TEXT,
    reminder_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
)
''')

conn.commit()

logging.basicConfig(level=logging.INFO)
# Объект бота
bot = Bot(token="8469594997:AAGw-wNxW4e-vPYAR50ROcrfW8Y5gTRJxc8")
# Диспетчер
dp = Dispatcher()

class ScheduleForm(StatesGroup):
    waiting_for_day = State()
    waiting_for_text = State()

class ReminderForm(StatesGroup):
    waiting_for_method = State()
    waiting_for_text = State()
    waiting_for_voice = State()
    waiting_for_year = State()
    waiting_for_month = State()
    waiting_for_day = State()
    waiting_for_time = State()

# Нейросеть для распознавания дат из текста (упрощенная версия)
class DateParser:
    def parse_date_from_text(self, text):
        """
        Парсит дату и время из текста
        Возвращает datetime объект или None если не удалось распознать
        """
        text = text.lower()
        
        # Текущая дата для отсчета
        now = datetime.now()
        
        # Распознавание относительных дат
        if 'сегодня' in text:
            date = now.date()
        elif 'завтра' in text:
            date = now.date() + timedelta(days=1)
        elif 'послезавтра' in text:
            date = now.date() + timedelta(days=2)
        elif 'через' in text and 'день' in text:
            days_match = re.search(r'через\s+(\d+)\s+день', text)
            if days_match:
                days = int(days_match.group(1))
                date = now.date() + timedelta(days=days)
            else:
                date = now.date() + timedelta(days=1)
        else:
            date = now.date()
        
        # Распознавание времени
        time_match = re.search(r'(\d{1,2})[:\s]?(\d{2})?\s*(утра|вечера|ночи|дня|am|pm)?', text)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2)) if time_match.group(2) else 0
            period = time_match.group(3)
            
            # Корректировка времени в зависимости от периода
            if period in ['вечера', 'ночи', 'pm'] and hour < 12:
                hour += 12
            elif period in ['утра', 'дня', 'am'] and hour == 12:
                hour = 0
        else:
            # Время по умолчанию - текущее + 1 час
            hour = now.hour + 1
            minute = now.minute
        
        # Создаем datetime объект
        try:
            reminder_time = datetime(date.year, date.month, date.day, hour % 24, minute)
            # Если время уже прошло сегодня, переносим на завтра
            if reminder_time < now:
                reminder_time += timedelta(days=1)
            return reminder_time
        except ValueError:
            return None
    
    def extract_reminder_text(self, text):
        """Извлекает текст напоминания, убирая временные указания"""
        # Удаляем временные выражения
        patterns = [
            r'сегодня', r'завтра', r'послезавтра', r'через\s+\d+\s+день',
            r'в\s+\d{1,2}[:\s]?\d{0,2}\s*(утра|вечера|ночи|дня|am|pm)?',
            r'\d{1,2}[:\s]?\d{0,2}\s*(утра|вечера|ночи|дня|am|pm)?'
        ]
        
        clean_text = text
        for pattern in patterns:
            clean_text = re.sub(pattern, '', clean_text, flags=re.IGNORECASE)
        
        # Убираем лишние пробелы
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        
        return clean_text if clean_text else "Напоминание"

# Инициализация парсера дат
date_parser = DateParser()

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT first_name FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if result:
        first_name = result[0]
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📅 Составить расписание")],
                [KeyboardButton(text="⏰ Создать напоминание")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            f"Привет снова, {first_name}! 👋\nВыбери действие:",
            reply_markup=kb
        )
    else:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            "Привет! 👋 Для регистрации, пожалуйста, отправь свой номер телефона:",
            reply_markup=kb
        )

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
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="📅 Составить расписание")],
                    [KeyboardButton(text="⏰ Создать напоминание")]
                ],
                resize_keyboard=True
            )
        )
    else:
        await message.answer(
            "Ты уже зарегистрирован ✅",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="📅 Составить расписание")],
                    [KeyboardButton(text="⏰ Создать напоминание")]
                ],
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

@dp.message(ScheduleForm.waiting_for_day)
async def schedule_day(message: types.Message, state: FSMContext):
    await state.update_data(day=message.text)
    await message.answer("✏️ Отлично! Теперь отправь текст расписания (например, «Учёба с 9:00 до 14:00»).")
    await state.set_state(ScheduleForm.waiting_for_text)

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

# Новые функции для напоминаний
@dp.message(Command("reminder"))
@dp.message(lambda message: message.text == "⏰ Создать напоминание")
async def reminder_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        await message.answer("⚠️ Сначала зарегистрируйся через /start, чтобы создать напоминание.")
        return

    # Создаем инлайн-кнопки для выбора метода
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Текстом", callback_data="method_text")],
            [InlineKeyboardButton(text="🔘 Кнопочками", callback_data="method_buttons")],
            [InlineKeyboardButton(text="🎤 Голосом", callback_data="method_voice")]
        ]
    )
    
    await message.answer(
        "Выбери способ создания напоминания:",
        reply_markup=keyboard
    )
    await state.set_state(ReminderForm.waiting_for_method)

@dp.callback_query(ReminderForm.waiting_for_method)
async def process_method(callback: types.CallbackQuery, state: FSMContext):
    method = callback.data
    
    if method == "method_text":
        await callback.message.answer(
            "📝 Напиши текст напоминания с указанием времени.\n\n"
            "Примеры:\n"
            "• «Завтра в 10:00 позвонить маме»\n"
            "• «Через 2 дня в 15:30 встреча у врача»\n"
            "• «Сегодня вечером в 19:00 купить продукты»"
        )
        await state.set_state(ReminderForm.waiting_for_text)
    
    elif method == "method_buttons":
        # Начинаем процесс выбора даты через кнопки
        await select_year(callback.message, state)
    
    elif method == "method_voice":
        await callback.message.answer(
            "🎤 Запиши голосовое сообщение с напоминанием.\n\n"
            "Пример: «Напомни завтра в 14:00 о встрече с коллегами»"
        )
        await state.set_state(ReminderForm.waiting_for_voice)
    
    await callback.answer()

# Обработка текстового напоминания
@dp.message(ReminderForm.waiting_for_text)
async def process_text_reminder(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    
    # Используем нейросеть для парсинга даты
    reminder_time = date_parser.parse_date_from_text(text)
    reminder_text = date_parser.extract_reminder_text(text)
    
    if reminder_time:
        # Сохраняем напоминание в базу
        cursor.execute(
            "INSERT INTO reminders (user_id, reminder_text, reminder_time) VALUES (?, ?, ?)",
            (user_id, reminder_text, reminder_time)
        )
        conn.commit()
        
        await message.answer(
            f"✅ Напоминание создано!\n"
            f"📋 *Что:* {reminder_text}\n"
            f"⏰ *Когда:* {reminder_time.strftime('%d.%m.%Y в %H:%M')}",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "❌ Не удалось распознать дату и время в тексте.\n"
            "Попробуй еще раз, например: «Завтра в 10:00 позвонить маме»"
        )
    
    await state.clear()

# Обработка голосового напоминания - исправленная версия
@dp.message(ReminderForm.waiting_for_voice)
# Обработка голосового напоминания - сложная версия с полным распознаванием
@dp.message(ReminderForm.waiting_for_voice)
# Обработка голосового напоминания - сложная версия с полным распознаванием
@dp.message(ReminderForm.waiting_for_voice)
@dp.message(ReminderForm.waiting_for_voice)
@dp.message(ReminderForm.waiting_for_voice)
async def process_voice_reminder(message: types.Message, state: FSMContext):
    if not message.voice:
        await message.answer("❌ Пожалуйста, отправь голосовое сообщение.")
        return

    ogg_path = "voice.ogg"
    wav_path = "voice.wav"

    try:
        await message.answer("🔊 Скачиваю голосовое сообщение...")

        # Скачиваем файл из Telegram
        file = await bot.get_file(message.voice.file_id)
        await bot.download_file(file.file_path, destination=ogg_path)

        # Проверяем, что файл реально скачался
        if not os.path.exists(ogg_path):
            await message.answer("❌ Ошибка: файл голосового сообщения не найден после скачивания.")
            return

        await message.answer("🔄 Конвертирую аудио...")

        # Проверяем наличие ffmpeg
        from pydub.utils import which
        if not which("ffmpeg"):
            await message.answer("⚠️ Ошибка: ffmpeg не установлен или не добавлен в PATH.\n"
                                 "1️⃣ Скачай с сайта: https://www.gyan.dev/ffmpeg/builds/\n"
                                 "2️⃣ Добавь в PATH, например C:\\ffmpeg\\bin")
            return

        # Конвертация ogg → wav
        try:
            audio = AudioSegment.from_file(ogg_path, format="ogg", codec="opus")
            audio.export(wav_path, format="wav")
        except Exception as e:
            await message.answer(f"❌ Ошибка при конвертации аудио: {e}")
            return

        await message.answer("🎤 Распознаю речь...")

        # Распознавание речи
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            try:
                recognized_text = recognizer.recognize_google(audio_data, language="ru-RU")
            except sr.UnknownValueError:
                await message.answer("❌ Не удалось распознать речь. Попробуй сказать четче или напиши текстом.")
                await state.set_state(ReminderForm.waiting_for_text)
                return
            except sr.RequestError as e:
                await message.answer(f"⚠️ Ошибка при подключении к Google Speech API: {e}")
                return

        await message.answer(f"🎤 Распознанный текст:\n\n`{recognized_text}`", parse_mode="Markdown")

        # Извлекаем дату и текст
        reminder_time = date_parser.parse_date_from_text(recognized_text)
        reminder_text = date_parser.extract_reminder_text(recognized_text)

        if reminder_time:
            user_id = message.from_user.id
            cursor.execute(
                "INSERT INTO reminders (user_id, reminder_text, reminder_time) VALUES (?, ?, ?)",
                (user_id, reminder_text, reminder_time)
            )
            conn.commit()

            await message.answer(
                f"✅ *Напоминание создано из голосового сообщения!*\n\n"
                f"📋 *Что:* {reminder_text}\n"
                f"⏰ *Когда:* {reminder_time.strftime('%d.%m.%Y в %H:%M')}",
                parse_mode="Markdown"
            )
            await state.clear()
        else:
            await message.answer(
                "❌ Не удалось распознать дату и время.\nПопробуй сказать четче или введи текст вручную."
            )
            await state.set_state(ReminderForm.waiting_for_text)

    except Exception as e:
        import traceback
        logging.error("Voice processing error: %s", traceback.format_exc())
        await message.answer(f"❌ Ошибка при обработке голосового сообщения: {e}")
        await state.set_state(ReminderForm.waiting_for_text)

    finally:
        # Удаляем временные файлы
        for path in (ogg_path, wav_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


# Функции для выбора даты через кнопки
async def select_year(message: types.Message, state: FSMContext):
    current_year = datetime.now().year
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(year), callback_data=f"year_{year}") 
             for year in range(current_year, current_year + 3)],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
        ]
    )
    await message.answer("Выбери год:", reply_markup=keyboard)
    await state.set_state(ReminderForm.waiting_for_year)

async def select_month(message: types.Message, state: FSMContext, year: int):
    months = [
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]
    
    keyboard_buttons = []
    for i in range(0, 12, 3):
        row = [
            InlineKeyboardButton(text=months[j], callback_data=f"month_{j+1}") 
            for j in range(i, min(i+3, 12))
        ]
        keyboard_buttons.append(row)
    
    keyboard_buttons.append([InlineKeyboardButton(text="Назад", callback_data="back_to_year")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer(f"Выбери месяц для {year} года:", reply_markup=keyboard)
    await state.set_state(ReminderForm.waiting_for_month)

async def select_day(message: types.Message, state: FSMContext, year: int, month: int):
    # Определяем количество дней в месяце
    if month == 12:
        next_month = 1
        next_year = year + 1
    else:
        next_month = month + 1
        next_year = year
    
    last_day = (datetime(next_year, next_month, 1) - timedelta(days=1)).day
    
    # Создаем кнопки с днями
    keyboard_buttons = []
    row = []
    for day in range(1, last_day + 1):
        row.append(InlineKeyboardButton(text=str(day), callback_data=f"day_{day}"))
        if len(row) == 7:
            keyboard_buttons.append(row)
            row = []
    if row:
        keyboard_buttons.append(row)
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="Назад", callback_data="back_to_month")
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    month_names = [
        "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня",
        "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"
    ]
    await message.answer(f"Выбери день {month_names[month-1]}:", reply_markup=keyboard)
    await state.set_state(ReminderForm.waiting_for_day)

async def select_time(message: types.Message, state: FSMContext):
    keyboard_buttons = []
    for hour in range(0, 24, 4):
        row = []
        for h in range(hour, min(hour + 4, 24)):
            for minute in ['00', '30']:
                time_str = f"{h:02d}:{minute}"
                row.append(InlineKeyboardButton(text=time_str, callback_data=f"time_{time_str}"))
        keyboard_buttons.append(row)
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="Назад", callback_data="back_to_day")
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer("Выбери время:", reply_markup=keyboard)
    await state.set_state(ReminderForm.waiting_for_time)

# Обработчики callback для выбора даты
@dp.callback_query(ReminderForm.waiting_for_year)
async def process_year(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "cancel":
        await callback.message.answer("❌ Создание напоминания отменено")
        await state.clear()
        return
    
    year = int(callback.data.split('_')[1])
    await state.update_data(year=year)
    await select_month(callback.message, state, year)
    await callback.answer()

@dp.callback_query(ReminderForm.waiting_for_month)
async def process_month(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "back_to_year":
        await select_year(callback.message, state)
        return
    
    month = int(callback.data.split('_')[1])
    data = await state.get_data()
    year = data['year']
    await state.update_data(month=month)
    await select_day(callback.message, state, year, month)
    await callback.answer()

@dp.callback_query(ReminderForm.waiting_for_day)
async def process_day(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "back_to_month":
        data = await state.get_data()
        await select_month(callback.message, state, data['year'])
        return
    
    day = int(callback.data.split('_')[1])
    await state.update_data(day=day)
    await select_time(callback.message, state)
    await callback.answer()

@dp.callback_query(ReminderForm.waiting_for_time)
async def process_time(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "back_to_day":
        data = await state.get_data()
        await select_day(callback.message, state, data['year'], data['month'])
        return
    
    time_str = callback.data.split('_')[1]
    data = await state.get_data()
    
    # Собираем полную дату
    year = data['year']
    month = data['month']
    day = data['day']
    hour, minute = map(int, time_str.split(':'))
    
    reminder_time = datetime(year, month, day, hour, minute)
    
    # Проверяем, что дата не в прошлом
    if reminder_time < datetime.now():
        await callback.message.answer("❌ Нельзя установить напоминание на прошедшее время!")
        await select_time(callback.message, state)
        return
    
    await state.update_data(reminder_time=reminder_time)
    
    # Запрашиваем текст напоминания
    await callback.message.answer("📝 Теперь введи текст напоминания:")
    await state.set_state(ReminderForm.waiting_for_text)
    await callback.answer()

# Запуск бота
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
