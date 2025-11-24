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

# Нейросеть для распознавания дат из текста
class DateParser:
    MONTHS = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12
    }

    WEEKDAYS = {
        "понедельник": 0, "вторник": 1, "среда": 2,
        "четверг": 3, "пятница": 4, "суббота": 5, "воскресенье": 6
    }

    WEEKDAY_FORMS = {
        "понедельник": ["понедельник", "понедельникe", "в понедельник"],
        "вторник": ["вторник", "во вторник"],
        "среда": ["среда", "среду", "в среду"],
        "четверг": ["четверг", "в четверг"],
        "пятница": ["пятница", "пятницу", "в пятницу"],
        "суббота": ["суббота", "субботу", "в субботу"],
        "воскресенье": ["воскресенье", "в воскресенье"]
    }

    # Настройки периодов дня по умолчанию
    PERIODS = {
        "утро": (6, 12),
        "день": (12, 18),
        "вечер": (18, 0),
        "ночь": (0, 6)
    }

    def parse_date_from_text(self, text: str) -> datetime:
        text = (text or "").lower().strip()
        now = datetime.now()

        # --- Обработка относительных времен ---
        m = re.search(r"через\s+(\d+)\s*час", text)
        if m:
            hours = int(m.group(1))
            m2 = re.search(r"(\d+)\s*мин", text)
            minutes = int(m2.group(1)) if m2 else 0
            return now + timedelta(hours=hours, minutes=minutes)
        m = re.search(r"через\s+(\d+)\s*мин", text)
        if m:
            minutes = int(m.group(1))
            return now + timedelta(minutes=minutes)
        if "через полчаса" in text:
            return now + timedelta(minutes=30)
        m = re.search(r"через\s+(\d+)\s*дн", text)
        if m:
            days = int(m.group(1))
            return now + timedelta(days=days)
        m = re.search(r"через\s+(\d+)\s*нед", text)
        if m:
            weeks = int(m.group(1))
            return now + timedelta(weeks=weeks)
        if "через неделю" in text:
            return now + timedelta(weeks=1)

        # --- Обработка дней недели ---
        date = None
        for base_name, forms in self.WEEKDAY_FORMS.items():
            if any(f in text for f in forms):
                weekday = self.WEEKDAYS[base_name]
                today_wd = now.weekday()

                # "в эту/этот <день>" — ближайший в текущей неделе
                if "эту" in text or "этот" in text:
                    delta = weekday - today_wd
                    if delta < 0:
                        delta += 7
                    date = (now + timedelta(days=delta)).date()
                    break

                # "в следующую/следующий <день>" — на следующей неделе
                if "следующ" in text:
                    delta = (weekday - today_wd) % 7
                    delta = delta + 7 if delta == 0 else delta + 7
                    date = (now + timedelta(days=delta)).date()
                    break

                # просто "в <день>" — ближайший
                delta = (weekday - today_wd) % 7
                if delta == 0:
                    delta = 7
                date = (now + timedelta(days=delta)).date()
                break

        # --- Абсолютные даты (dd.mm.yyyy, dd.mm, dd month yyyy, dd month) ---
        if date is None:
            m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
            if m:
                d, mo, y = map(int, m.groups())
                try:
                    date = datetime(y, mo, d).date()
                except ValueError:
                    date = None
        if date is None:
            m = re.search(r"(\d{1,2})[./](\d{1,2})\b", text)
            if m:
                d, mo = map(int, m.groups())
                y = now.year
                try:
                    candidate = datetime(y, mo, d)
                    if candidate < now:
                        candidate = candidate.replace(year=y + 1)
                    date = candidate.date()
                except ValueError:
                    date = None
        if date is None:
            m = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", text)
            if m:
                d = int(m.group(1))
                mon = m.group(2)
                y = int(m.group(3))
                mo = self.MONTHS.get(mon)
                if mo:
                    try:
                        date = datetime(y, mo, d).date()
                    except ValueError:
                        date = None
        if date is None:
            m = re.search(r"(\d{1,2})\s+([а-яё]+)\b", text)
            if m:
                d = int(m.group(1))
                mon = m.group(2)
                mo = self.MONTHS.get(mon)
                if mo:
                    y = now.year
                    try:
                        candidate = datetime(y, mo, d)
                        if candidate < now:
                            candidate = candidate.replace(year=y + 1)
                        date = candidate.date()
                    except ValueError:
                        date = None

        if date is None:
            date = now.date()

        # --- Обработка времени ---
        hour = None
        minute = None

        # 1) Точное время 10:30
        m = re.search(r"(\d{1,2})[:.](\d{2})", text)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))

        else:
            # 2) Время с указанием периода — «в 8 утра»
            m = re.search(r"в\s*(\d{1,2})(?:\s*(утра|вечера|дня|ночи))?", text)
            if m:
                hour = int(m.group(1))
                minute = 0
                period = m.group(2)

                if period:
                    if period in ("вечера", "дня") and hour < 12:
                        hour += 12
                    if period == "ночи" and hour == 12:
                        hour = 0

            else:
                # 3) просто период — «утром», «вечером»
                m = re.search(r"(утро|день|вечер|ночь)", text)
                if m:
                    period = m.group(1)
                    start_hour, _ = self.PERIODS.get(period, (9, 18))
                    hour = start_hour
                    minute = 0

        if hour is None:
            hour = now.hour
        if minute is None:
            minute = 0

        result = datetime(date.year, date.month, date.day, hour % 24, minute)

        # Если время прошло — переносим на завтра
        if result < now:
            result += timedelta(days=1)

        return result
    
    def extract_reminder_text(self, text: str) -> str:
        if not text:
            return "Напоминание"
        clean = text.lower()
        patterns = [
            r"сегодня", r"завтра", r"послезавтра",
            r"через\s+\d+\s*(час|часа|часов|мин(ут)?|дн(ь|я|ей)|нед(я|ели)?)",
            r"через\s+полчаса",
            r"через\s+неделю",
            r"в\s+следующ(ую|ий)\s+[а-яё]+",
            r"в\s+эту\s+[а-яё]+",
            r"в\s+этот\s+[а-яё]+",
            r"в\s+[а-яё]+",
            r"\d{1,2}[./]\d{1,2}([./]\d{2,4})?",
            r"\d{1,2}[:.]\d{2}",
            r"\d{1,2}\s+[а-яё]+(\s+\d{4})?"
        ]
        for p in patterns:
            clean = re.sub(p, "", clean, flags=re.I)
        clean = re.sub(r"\bв\b", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean.capitalize() if clean else "Напоминание"

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
                [KeyboardButton(text="⏰ Создать напоминание")],
                [KeyboardButton(text="⚙️ Настройки")]
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
                [KeyboardButton(text="📱 Отправить мой номер", request_contact=True)],
                [KeyboardButton(text="⚙️ Настройки")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            "Привет! 👋 Для регистрации, пожалуйста, отправь свой номер телефона:",
            reply_markup=kb
        )

# =======================  
#     Настройки периодов  
# =======================
@dp.message(Command("settings"))
async def settings_handler(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔧 Настройка промежутков", callback_data="settings_periods")]
        ]
    )
    await message.answer("⚙️ Настройки бота:", reply_markup=keyboard)

# Обработчик текстовой кнопки "⚙️ Настройки" в ReplyKeyboard
@dp.message(lambda m: m.text == "⚙️ Настройки")
async def open_settings(message: types.Message):
    # вызываем ту же функцию, что и команда /settings
    await settings_handler(message)

# Исправленный callback для кнопки внутри настроек
@dp.callback_query(lambda c: c.data == "settings_periods")
async def set_periods_callback(callback: types.CallbackQuery):
    text = (
        "🕒 Введи новые диапазоны периодов дня.\n"
        "Формат: `период начало-конец`\n\n"
        "Пример:\n"
        "утро 06-12\n"
        "день 12-18\n"
        "вечер 18-00\n"
        "ночь 00-06"
    )
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

# Обработка вводимых настроек пользователем
@dp.message(lambda m: m.text and re.search(r"^(утро|день|вечер|ночь)\s+\d{1,2}-\d{1,2}", m.text.lower()))
async def update_periods(message: types.Message):
    lines = message.text.lower().splitlines()

    for line in lines:
        m = re.match(r"(утро|день|вечер|ночь)\s+(\d{1,2})-(\d{1,2})", line)
        if m:
            period, start, end = m.groups()
            date_parser.PERIODS[period] = (int(start), int(end))

    await message.answer("✅ Периоды дня обновлены!")

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
                    [KeyboardButton(text="⏰ Создать напоминание")],
                    [KeyboardButton(text="⚙️ Настройки")]
                ],
                resize_keyboard=True
            )
        )
    else:
        await message.answer(
            "Ты уже зарегистрирован ✅",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="⏰ Создать напоминание")],
                    [KeyboardButton(text="⚙️ Настройки")]
                ],
                resize_keyboard=True
            )
        )


    # cursor.execute("INSERT INTO schedule (user_id, day, text) VALUES (?, ?, ?)", (user_id, day, text))
    # conn.commit() внимание   



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
