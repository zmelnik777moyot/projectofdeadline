
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
import os
from datetime import datetime, timedelta
from functools import lru_cache, wraps

# ---------- ЛОГИРОВАНИЕ ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# ---------- БД ----------
conn = sqlite3.connect('polz.db', check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# users
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    phone TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# reminders (оставляем как есть)
cursor.execute('''
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    reminder_text TEXT,
    reminder_time TIMESTAMP,
    sent INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    repeat_pattern TEXT,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
)
''')

# schedule_items - новая таблица расписания (по датам)
cursor.execute('''
CREATE TABLE IF NOT EXISTS schedule_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    title TEXT,
    due_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
)
''')

conn.commit()

# ---------- BOT ----------
BOT_TOKEN = "8469594997:AAGw-wNxW4e-vPYAR50ROcrfW8Y5gTRJxc8"  # <- замените на свой токен
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- ДЕКОРАТОР ПРОВЕРКИ РЕГИСТРАЦИИ ----------
def user_registered(func):
    @wraps(func)
    async def wrapper(message: types.Message, *args, **kwargs):
        user_id = message.from_user.id
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            await message.answer("⚠️ Сначала зарегистрируйтесь через /start")
            return
        return await func(message, *args, **kwargs)
    return wrapper

# ---------- STATES ----------
class ReminderForm(StatesGroup):
    waiting_for_method = State()
    waiting_for_text = State()
    waiting_for_voice = State()
    waiting_for_year = State()
    waiting_for_month = State()
    waiting_for_day = State()
    waiting_for_time = State()

class ScheduleForm(StatesGroup):
    waiting_for_date = State()
    waiting_for_title = State()
    waiting_for_time = State()
    editing_item = State()
    editing_action = State()

# ---------- PARSER (используем из старого кода) ----------
class DateParser:
    MONTHS = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12
    }

    @staticmethod
    @lru_cache(maxsize=200)
    def parse_date_from_text(text: str) -> datetime:
        text = (text or "").lower().strip()
        now = datetime.now()

        # простой парсер: "через N часов/мин", "сегодня/завтра", "dd.mm.yyyy hh:mm", "dd.mm hh:mm"
        m = re.search(r"через\s+(\d+)\s*час", text)
        if m:
            hours = int(m.group(1))
            return now + timedelta(hours=hours)

        if "через полчас" in text:
            return now + timedelta(minutes=30)

        if "сегодня" in text:
            d = now.date()
        elif "завтра" in text:
            d = (now + timedelta(days=1)).date()
        else:
            m = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text)
            if m:
                d = int(m.group(1)); mo = int(m.group(2)); y = m.group(3)
                year = int(y) if y else now.year
                try:
                    return datetime(year, mo, d, now.hour, now.minute)
                except:
                    d = now.date()
            else:
                d = now.date()

        # время
        m = re.search(r"(\d{1,2})[:.](\d{2})", text)
        if m:
            h = int(m.group(1)); mi = int(m.group(2))
        else:
            # период дня
            if "утр" in text:
                h, mi = 9, 0
            elif "вечер" in text:
                h, mi = 18, 0
            else:
                h, mi = 9, 0

        result = datetime(d.year, d.month, d.day, h % 24, mi)
        if result < now:
            result += timedelta(days=1)
        return result

    @staticmethod
    def extract_reminder_text(text: str) -> str:
        if not text:
            return "Напоминание"
        # удаляем упоминания дат/времени (простейшая версия)
        clean = re.sub(r"\b(сегодня|завтра|через|утр|день|вечер|ночь|часов?|минут|через\s+\d+)\b", "", text, flags=re.I)
        clean = re.sub(r"\d{1,2}[:.]\d{2}", "", clean)
        clean = re.sub(r"\d{1,2}[./]\d{1,2}([./]\d{2,4})?", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean.capitalize() if clean else "Напоминание"

date_parser = DateParser()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def parse_datetime_from_db(dt_value):
    if dt_value is None:
        return None
    if isinstance(dt_value, datetime):
        return dt_value
    if isinstance(dt_value, str):
        formats = [
            '%Y-%m-%d %H:%M:%S.%f',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%d.%m.%Y %H:%M:%S',
            '%d.%m.%Y %H:%M',
            '%d.%m.%Y'
        ]
        for fmt in formats:
            try:
                return datetime.strptime(dt_value, fmt)
            except ValueError:
                continue
    return None

# ---------- START / REGISTRATION / KEYBOARD ----------
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT first_name FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏰ Создать напоминание")],
            [KeyboardButton(text="📋 Мои напоминания")],
            [KeyboardButton(text="📅 Расписание")],
            [KeyboardButton(text="⚙️ Настройки")]
        ],
        resize_keyboard=True
    )

    if result:
        await message.answer(f"Привет снова, {result[0]}! 👋\nВыбери действие:", reply_markup=kb)
    else:
        kb_reg = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Отправить мой номер", request_contact=True)],
                [KeyboardButton(text="⚙️ Настройки")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer("Привет! 👋 Для регистрации, пожалуйста, отправь свой номер телефона:", reply_markup=kb_reg)

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
        await message.answer(f"✅ Спасибо, {first_name}! Ты успешно зарегистрирован.", reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⏰ Создать напоминание")],
                [KeyboardButton(text="📋 Мои напоминания")],
                [KeyboardButton(text="📅 Расписание")],
                [KeyboardButton(text="⚙️ Настройки")]
            ],
            resize_keyboard=True
        ))
    else:
        await message.answer("Ты уже зарегистрирован ✅")

# ---------- УВЕДОМЛЕНИЯ: фоновая задача ----------
async def reminder_scheduler():
    while True:
        try:
            now = datetime.now()
            cursor.execute("""
                SELECT * FROM reminders
                WHERE reminder_time <= ? AND sent = 0
            """, (now,))
            reminders = cursor.fetchall()
            for rem in reminders:
                try:
                    await bot.send_message(rem['user_id'], f"🔔 *Напоминание!*\n\n{rem['reminder_text']}", parse_mode="Markdown")
                    cursor.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (rem['id'],))
                    conn.commit()
                except Exception as e:
                    logging.error("Ошибка отправки напоминания %s: %s", rem['id'], e)
            # Проверять каждые 30 сек
            await asyncio.sleep(30)
        except Exception as e:
            logging.error("Ошибка в reminder_scheduler: %s", e)
            await asyncio.sleep(60)

# ---------- НАПОМИНАНИЯ (сохраняем функционал) ----------
@dp.message(Command("reminder"))
@dp.message(lambda message: message.text == "⏰ Создать напоминание")
@user_registered
async def reminder_command(message: types.Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Текстом", callback_data="method_text")],
        [InlineKeyboardButton(text="🔘 Кнопочками", callback_data="method_buttons")],
        [InlineKeyboardButton(text="🎤 Голосом", callback_data="method_voice")]
    ])
    await message.answer("Выбери способ создания напоминания:", reply_markup=keyboard)
    await state.set_state(ReminderForm.waiting_for_method)

@dp.callback_query(lambda c: c.data and c.data.startswith("method_"))
async def process_method(callback: types.CallbackQuery, state: FSMContext):
    method = callback.data
    if method == "method_text":
        await callback.message.answer("📝 Напиши текст напоминания с указанием времени.\nПример: «Завтра в 10:00 позвонить маме»")
        await state.set_state(ReminderForm.waiting_for_text)
    elif method == "method_buttons":
        # Используем простую кнопку выбора даты/времени через текст — перенаправим на схему выбора даты
        await callback.message.answer("Выбери дату и время через команды. Напиши, например: 25.12.2025 14:30\nИли используй голосовой метод.")
        await state.set_state(ReminderForm.waiting_for_text)
    elif method == "method_voice":
        await callback.message.answer("🎤 Отправь голосовое сообщение с напоминанием (пример: «Напомни завтра в 14:00 о встрече»)")
        await state.set_state(ReminderForm.waiting_for_voice)
    await callback.answer()

@dp.message(ReminderForm.waiting_for_text)
async def process_text_reminder(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    reminder_time = date_parser.parse_date_from_text(text)
    reminder_text = date_parser.extract_reminder_text(text)
    if reminder_time:
        cursor.execute("INSERT INTO reminders (user_id, reminder_text, reminder_time) VALUES (?, ?, ?)",
                       (user_id, reminder_text, reminder_time))
        conn.commit()
        await message.answer(f"✅ Напоминание создано!\n📋 Что: {reminder_text}\n⏰ Когда: {reminder_time.strftime('%d.%m.%Y в %H:%M')}")
    else:
        await message.answer("❌ Не удалось распознать дату/время. Попробуй формат: 25.12.2025 14:30 или «завтра в 10:00 ...»")
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
        file = await bot.get_file(message.voice.file_id)
        await bot.download_file(file.file_path, destination=ogg_path)
        # Проверка ffmpeg
        from pydub.utils import which
        if not which("ffmpeg"):
            await message.answer("⚠️ ffmpeg не найден. Установи ffmpeg и добавь в PATH.")
            return
        audio = AudioSegment.from_file(ogg_path, format="ogg", codec="opus")
        audio.export(wav_path, format="wav")
        await message.answer("🎤 Распознаю речь...")
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            try:
                recognized_text = recognizer.recognize_google(audio_data, language="ru-RU")
            except sr.UnknownValueError:
                await message.answer("❌ Не удалось распознать речь. Попробуй снова или напиши текстом.")
                await state.set_state(ReminderForm.waiting_for_text)
                return
            except sr.RequestError as e:
                await message.answer(f"⚠️ Ошибка сервиса распознавания: {e}")
                return
        await message.answer(f"🎤 Распознано: `{recognized_text}`", parse_mode="Markdown")
        reminder_time = date_parser.parse_date_from_text(recognized_text)
        reminder_text = date_parser.extract_reminder_text(recognized_text)
        if reminder_time:
            cursor.execute("INSERT INTO reminders (user_id, reminder_text, reminder_time) VALUES (?, ?, ?)",
                           (message.from_user.id, reminder_text, reminder_time))
            conn.commit()
            await message.answer(f"✅ Напоминание создано из голоса!\n📋 {reminder_text}\n⏰ {reminder_time.strftime('%d.%m.%Y в %H:%M')}", parse_mode="Markdown")
            await state.clear()
        else:
            await message.answer("❌ Не удалось распознать дату/время в голосе. Введи текстом.")
            await state.set_state(ReminderForm.waiting_for_text)
    except Exception as e:
        logging.error("Voice proc error: %s", e)
        await message.answer(f"❌ Ошибка при обработке голосового сообщения: {e}")
        await state.set_state(ReminderForm.waiting_for_text)
    finally:
        for p in (ogg_path, wav_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except:
                pass

# Список напоминаний
@dp.message(Command("my_reminders"))
@dp.message(lambda message: message.text == "📋 Мои напоминания")
@user_registered
async def list_reminders(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT id, reminder_text, reminder_time FROM reminders WHERE user_id = ? AND sent = 0 ORDER BY reminder_time", (user_id,))
    reminders = cursor.fetchall()
    if not reminders:
        await message.answer("📭 У вас нет активных напоминаний")
        return
    text = "📋 Ваши активные напоминания:\n\n"
    for rem in reminders:
        rt = parse_datetime_from_db(rem['reminder_time'])
        time_str = rt.strftime('%d.%m.%Y %H:%M') if rt else "неизвестно"
        text += f"• {rem['reminder_text']} — {time_str}\n"
    await message.answer(text)

# ---------- НОВОЕ: РАСПИСАНИЕ (по датам) ----------
# Меню /schedule показывает меню A (как обсуждали)
@dp.message(Command("schedule"))
@dp.message(lambda message: message.text == "📅 Расписание")
@user_registered
async def schedule_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👀 Показать задачи", callback_data="sched_show")],
        [InlineKeyboardButton(text="🕒 Сегодня", callback_data="sched_today"),
         InlineKeyboardButton(text="🌅 Завтра", callback_data="sched_tomorrow"),
         InlineKeyboardButton(text="🗓 Выбрать дату", callback_data="sched_pick_date")],
        [InlineKeyboardButton(text="➕ Добавить задачу", callback_data="sched_add")],
        [InlineKeyboardButton(text="✏️ Редактировать/Удалить", callback_data="sched_edit")]
    ])
    await message.answer("📅 Управление расписанием:", reply_markup=keyboard)

# Helpers: format schedule items
def format_schedule_rows(rows):
    if not rows:
        return "📭 Нет задач."
    text = ""
    for r in rows:
        dt = parse_datetime_from_db(r['due_at'])
        dt_str = dt.strftime('%d.%m.%Y %H:%M') if dt else "неизвестно"
        text += f"• [{r['id']}] {dt_str} — {r['title']}\n"
    return text

# Показать все задачи (вперед)
@dp.callback_query(lambda c: c.data == "sched_show")
async def sched_show_all(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    cursor.execute("SELECT id, title, due_at FROM schedule_items WHERE user_id = ? ORDER BY due_at LIMIT 200", (user_id,))
    rows = cursor.fetchall()
    await callback.message.edit_text("📋 Ваши задачи:\n\n" + format_schedule_rows(rows), reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="sched_back")]]
    ))
    await callback.answer()

# Сегодня
@dp.callback_query(lambda c: c.data == "sched_today")
async def sched_today(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    today = datetime.now().date()
    start = datetime.combine(today, datetime.min.time())
    end = datetime.combine(today, datetime.max.time())
    cursor.execute("SELECT id, title, due_at FROM schedule_items WHERE user_id = ? AND due_at BETWEEN ? AND ? ORDER BY due_at", (user_id, start, end))
    rows = cursor.fetchall()
    await callback.message.edit_text(f"📅 Задачи на сегодня ({today.strftime('%d.%m.%Y')}):\n\n" + format_schedule_rows(rows),
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                         [InlineKeyboardButton(text="⬅️ В меню", callback_data="sched_back"),
                                          InlineKeyboardButton(text="➕ Добавить", callback_data="sched_add")]
                                     ]))
    await callback.answer()

# Завтра
@dp.callback_query(lambda c: c.data == "sched_tomorrow")
async def sched_tomorrow(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    tomorrow = (datetime.now().date() + timedelta(days=1))
    start = datetime.combine(tomorrow, datetime.min.time())
    end = datetime.combine(tomorrow, datetime.max.time())
    cursor.execute("SELECT id, title, due_at FROM schedule_items WHERE user_id = ? AND due_at BETWEEN ? AND ? ORDER BY due_at", (user_id, start, end))
    rows = cursor.fetchall()
    await callback.message.edit_text(f"📅 Задачи на завтра ({tomorrow.strftime('%d.%m.%Y')}):\n\n" + format_schedule_rows(rows),
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                         [InlineKeyboardButton(text="⬅️ В меню", callback_data="sched_back"),
                                          InlineKeyboardButton(text="➕ Добавить", callback_data="sched_add")]
                                     ]))
    await callback.answer()

# Выбрать дату — переводим в state: просим дату в формате DD.MM.YYYY
@dp.callback_query(lambda c: c.data == "sched_pick_date")
async def sched_pick_date(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🗓 Введи дату в формате DD.MM.YYYY (например, 25.12.2025):")
    await state.set_state(ScheduleForm.waiting_for_date)
    await callback.answer()

@dp.message(ScheduleForm.waiting_for_date)
@user_registered
async def sched_date_input(message: types.Message, state: FSMContext):
    txt = message.text.strip()
    try:
        d = datetime.strptime(txt, '%d.%m.%Y').date()
    except:
        await message.answer("❌ Неверный формат даты. Используй DD.MM.YYYY")
        return
    start = datetime.combine(d, datetime.min.time()); end = datetime.combine(d, datetime.max.time())
    user_id = message.from_user.id
    cursor.execute("SELECT id, title, due_at FROM schedule_items WHERE user_id = ? AND due_at BETWEEN ? AND ? ORDER BY due_at", (user_id, start, end))
    rows = cursor.fetchall()
    await message.answer(f"📅 Задачи на {d.strftime('%d.%m.%Y')}:\n\n" + format_schedule_rows(rows),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [InlineKeyboardButton(text="⬅️ В меню", callback_data="sched_back"),
                              InlineKeyboardButton(text="➕ Добавить", callback_data="sched_add")]
                         ]))
    await state.clear()

# Добавить задачу (пошагово: дата -> текст -> время)
@dp.callback_query(lambda c: c.data == "sched_add")
async def sched_add_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("➕ Создание задачи.\nВведи дату в формате DD.MM.YYYY (например, 25.12.2025):")
    await state.set_state(ScheduleForm.waiting_for_date)
    await callback.answer()

@dp.message(ScheduleForm.waiting_for_date)
@user_registered
async def sched_add_date(message: types.Message, state: FSMContext):
    txt = message.text.strip()
    try:
        d = datetime.strptime(txt, '%d.%m.%Y').date()
    except:
        await message.answer("❌ Неверный формат даты. Попробуй DD.MM.YYYY")
        return
    await state.update_data(sched_date=str(d))
    await message.answer("📝 Теперь введи текст задачи (коротко):")
    await state.set_state(ScheduleForm.waiting_for_title)

@dp.message(ScheduleForm.waiting_for_title)
async def sched_add_title(message: types.Message, state: FSMContext):
    title = message.text.strip()
    if not title:
        await message.answer("❌ Текст задачи пустой. Введи текст.")
        return
    await state.update_data(sched_title=title)
    await message.answer("⏰ Укажи время в формате ЧЧ:ММ (например, 14:30):")
    await state.set_state(ScheduleForm.waiting_for_time)

@dp.message(ScheduleForm.waiting_for_time)
async def sched_add_time(message: types.Message, state: FSMContext):
    t = message.text.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})$', t)
    if not m:
        await message.answer("❌ Неверный формат времени. Используй ЧЧ:ММ")
        return
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        await message.answer("❌ Неверное время.")
        return
    data = await state.get_data()
    d = datetime.strptime(data['sched_date'], '%Y-%m-%d').date()
    due_at = datetime(d.year, d.month, d.day, h, mi)
    if due_at < datetime.now():
        await message.answer("❌ Нельзя создавать задачу в прошлом.")
        return
    user_id = message.from_user.id
    title = data['sched_title']
    cursor.execute("INSERT INTO schedule_items (user_id, title, due_at) VALUES (?, ?, ?)", (user_id, title, due_at))
    conn.commit()
    await message.answer(f"✅ Задача добавлена:\n{due_at.strftime('%d.%m.%Y %H:%M')} — {title}", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="sched_back")]]
    ))
    await state.clear()

# Редактирование/удаление: показываем список задач с кнопками
@dp.callback_query(lambda c: c.data == "sched_edit")
async def sched_edit_start(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    cursor.execute("SELECT id, title, due_at FROM schedule_items WHERE user_id = ? ORDER BY due_at LIMIT 100", (user_id,))
    rows = cursor.fetchall()
    if not rows:
        await callback.message.edit_text("📭 Нет задач для редактирования.", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="sched_back")]]
        ))
        await callback.answer()
        return
    kb = InlineKeyboardMarkup()
    for r in rows:
        dt = parse_datetime_from_db(r['due_at'])
        lab = f"{dt.strftime('%d.%m.%Y %H:%M')} — {r['title'][:30]}"
        kb.add(InlineKeyboardButton(text=lab, callback_data=f"sched_edit_item_{r['id']}"))
    kb.add(InlineKeyboardButton(text="⬅️ В меню", callback_data="sched_back"))
    await callback.message.edit_text("✏️ Выбери задачу для редактирования:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("sched_edit_item_"))
async def sched_edit_item(callback: types.CallbackQuery, state: FSMContext):
    item_id = int(callback.data.split("_")[-1])
    cursor.execute("SELECT id, title, due_at FROM schedule_items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    if not item:
        await callback.answer("❌ Задача не найдена")
        return
    dt = parse_datetime_from_db(item['due_at'])
    text = f"✏️ Задача #{item['id']}\n{dt.strftime('%d.%m.%Y %H:%M')} — {item['title']}\n\nВыбери действие:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"sched_action_change_text_{item_id}")],
        [InlineKeyboardButton(text="📅 Изменить дату/время", callback_data=f"sched_action_change_dt_{item_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"sched_action_delete_{item_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sched_edit")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

# Обработчики действий редактирования
@dp.callback_query(lambda c: c.data and c.data.startswith("sched_action_change_text_"))
async def sched_change_text_start(callback: types.CallbackQuery, state: FSMContext):
    item_id = int(callback.data.split("_")[-1])
    await state.update_data(edit_item_id=item_id)
    await callback.message.edit_text("📝 Введи новый текст для задачи:")
    await state.set_state(ScheduleForm.editing_action)
    await callback.answer()

@dp.message(ScheduleForm.editing_action)
async def sched_change_text_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    item_id = data.get('edit_item_id')
    if not item_id:
        await message.answer("❌ Ошибка. Попробуй снова.")
        await state.clear()
        return
    new_text = message.text.strip()
    cursor.execute("UPDATE schedule_items SET title = ? WHERE id = ?", (new_text, item_id))
    conn.commit()
    await message.answer("✅ Текст задачи обновлён.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню расписания", callback_data="sched_back")]
    ]))
    await state.clear()

@dp.callback_query(lambda c: c.data and c.data.startswith("sched_action_change_dt_"))
async def sched_change_dt_start(callback: types.CallbackQuery, state: FSMContext):
    item_id = int(callback.data.split("_")[-1])
    await state.update_data(edit_item_id=item_id)
    await callback.message.edit_text("📅 Введи новую дату DD.MM.YYYY:")
    await state.set_state(ScheduleForm.waiting_for_date)
    await callback.answer()

# Reuse waiting_for_date & waiting_for_time to change dt: after date -> ask time -> save
@dp.message(ScheduleForm.waiting_for_date)
async def sched_change_dt_date(message: types.Message, state: FSMContext):
    data = await state.get_data()
    # If editing flow (edit_item_id present) -> change date then time
    if data.get('edit_item_id'):
        txt = message.text.strip()
        try:
            d = datetime.strptime(txt, '%d.%m.%Y').date()
        except:
            await message.answer("❌ Неверный формат даты. Используй DD.MM.YYYY")
            return
        await state.update_data(edit_new_date=str(d))
        await message.answer("⏰ Теперь введи время ЧЧ:ММ:")
        await state.set_state(ScheduleForm.waiting_for_time)
        return
    # Otherwise it's part of adding flow; handled earlier
    await message.answer("Неожиданный ввод. Если ты создаешь задачу — начни снова.")
    await state.clear()

@dp.message(ScheduleForm.waiting_for_time)
async def sched_change_dt_time_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if data.get('edit_item_id') and data.get('edit_new_date'):
        t = message.text.strip()
        m = re.match(r'^(\d{1,2}):(\d{2})$', t)
        if not m:
            await message.answer("❌ Неверный формат времени. Используй ЧЧ:ММ")
            return
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h < 24 and 0 <= mi < 60):
            await message.answer("❌ Неверное время.")
            return
        d = datetime.strptime(data['edit_new_date'], '%Y-%m-%d').date()
        new_dt = datetime(d.year, d.month, d.day, h, mi)
        if new_dt < datetime.now():
            await message.answer("❌ Нельзя установить в прошлое.")
            return
        cursor.execute("UPDATE schedule_items SET due_at = ? WHERE id = ?", (new_dt, data['edit_item_id']))
        conn.commit()
        await message.answer("✅ Дата/время задачи обновлены.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В меню расписания", callback_data="sched_back")]
        ]))
        await state.clear()
        return
    # If we're here, it's likely the add flow handled earlier
    await message.answer("❌ Неверный контекст. Начни действие снова.")
    await state.clear()

@dp.callback_query(lambda c: c.data and c.data.startswith("sched_action_delete_"))
async def sched_delete(callback: types.CallbackQuery):
    item_id = int(callback.data.split("_")[-1])
    cursor.execute("SELECT title FROM schedule_items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    if not item:
        await callback.answer("❌ Задача не найдена")
        return
    cursor.execute("DELETE FROM schedule_items WHERE id = ?", (item_id,))
    conn.commit()
    await callback.message.edit_text(f"✅ Задача удалена: {item['title']}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="sched_back")]
    ]))
    await callback.answer()

# Навигация назад
@dp.callback_query(lambda c: c.data == "sched_back")
async def sched_back(callback: types.CallbackQuery):
    await schedule_command(callback.message)
    await callback.answer()

# ---------- Удаляем CSV-экспорт: не регистрируем обработчики экспорта ----------
# (в предыдущей версии были функции export_history_callback и кнопки — теперь их нет)

# ---------- Settings (минимально) ----------
@dp.message(Command("settings"))
async def settings_handler(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔧 Параметры", callback_data="settings_params")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back")]
    ])
    await message.answer("⚙️ Настройки:", reply_markup=keyboard)

# ---------- Запуск бота ----------
async def main():
    # Запускаем фоновую задачу для напоминаний
    asyncio.create_task(reminder_scheduler())
    logging.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

    asyncio.run(main())
