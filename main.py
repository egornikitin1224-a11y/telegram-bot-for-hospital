import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command, StateFilter
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# Загружаем переменные окружения
load_dotenv()

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(id) for id in os.getenv('ADMIN_IDS', '').split(',') if id]

if not BOT_TOKEN:
    raise ValueError("Токен бота не найден! Проверьте файл .env")

# Список врачей
DOCTORS = [
    "Терапевт Иванова А.С.",
    "Хирург Петров В.И.",
    "Стоматолог Сидорова Е.М.",
    "Окулист Смирнов П.А.",
    "Невролог Козлова Н.В."
]

# Доступное время для записи
AVAILABLE_TIMES = [
    "09:00", "10:00", "11:00", "12:00",
    "14:00", "15:00", "16:00", "17:00"
]

# Процедуры для каждого врача
PROCEDURES = {
    "терапевт": ["Общий осмотр", "Консультация", "Выписка рецепта"],
    "хирург": ["Консультация", "Малая операция", "Перевязка"],
    "стоматолог": ["Лечение кариеса", "Чистка зубов", "Удаление зуба"],
    "окулист": ["Проверка зрения", "Подбор очков", "Консультация"],
    "невролог": ["Консультация", "МРТ", "ЭЭГ"]
}


# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self, filename='appointments.json'):
        self.filename = filename
        self.load_data()

    def load_data(self):
        """Загрузка данных из файла"""
        if os.path.exists(self.filename):
            with open(self.filename, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
        else:
            self.data = {
                'appointments': [],
                'users': {},
                'next_id': 1
            }
            self.save_data()

    def save_data(self):
        """Сохранение данных в файл"""
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add_user(self, user_id: int, username: str, first_name: str):
        """Добавление нового пользователя"""
        if str(user_id) not in self.data['users']:
            self.data['users'][str(user_id)] = {
                'username': username,
                'first_name': first_name,
                'registered_at': datetime.now().isoformat()
            }
            self.save_data()

    def create_appointment(self, user_id: int, patient_name: str,
                           doctor: str, procedure: str,
                           date: str, time: str) -> int:
        """Создание новой записи"""
        appointment_id = self.data['next_id']
        appointment = {
            'id': appointment_id,
            'user_id': user_id,
            'patient_name': patient_name,
            'doctor': doctor,
            'procedure': procedure,
            'date': date,
            'time': time,
            'created_at': datetime.now().isoformat(),
            'status': 'active'
        }
        self.data['appointments'].append(appointment)
        self.data['next_id'] += 1
        self.save_data()
        return appointment_id

    def get_appointments(self, user_id: Optional[int] = None) -> List[Dict]:
        """Получение записей (всех или для конкретного пользователя)"""
        if user_id:
            return [a for a in self.data['appointments']
                    if a['user_id'] == user_id and a['status'] == 'active']
        return [a for a in self.data['appointments'] if a['status'] == 'active']

    def get_appointment(self, appointment_id: int) -> Optional[Dict]:
        """Получение конкретной записи"""
        for appointment in self.data['appointments']:
            if appointment['id'] == appointment_id:
                return appointment
        return None

    def update_appointment(self, appointment_id: int, **kwargs) -> bool:
        """Обновление записи"""
        for appointment in self.data['appointments']:
            if appointment['id'] == appointment_id:
                appointment.update(kwargs)
                self.save_data()
                return True
        return False

    def delete_appointment(self, appointment_id: int) -> bool:
        """Удаление записи"""
        return self.update_appointment(appointment_id, status='deleted')

    def get_users(self) -> Dict:
        """Получение всех пользователей"""
        return self.data['users']

    def is_appointment_available(self, doctor: str, date: str, time: str) -> bool:
        """Проверка доступности времени"""
        for appointment in self.data['appointments']:
            if (appointment['status'] == 'active' and
                    appointment['doctor'] == doctor and
                    appointment['date'] == date and
                    appointment['time'] == time):
                return False
        return True


# Создаем глобальный экземпляр базы данных
db = Database()


# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard(is_admin: bool = False):
    """Основная клавиатура с инлайн кнопками (минимум 4 кнопки)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Записаться", callback_data="make_appointment"),
            InlineKeyboardButton(text="📋 Мои записи", callback_data="my_appointments")
        ],
        [
            InlineKeyboardButton(text="👨‍⚕️ Врачи", callback_data="doctors_list"),
            InlineKeyboardButton(text="ℹ️ О клинике", callback_data="about")
        ]
    ])

    # Дополнительные кнопки для администратора
    if is_admin:
        keyboard.inline_keyboard.extend([
            [
                InlineKeyboardButton(text="📊 Все записи", callback_data="all_appointments"),
                InlineKeyboardButton(text="👥 Пользователи", callback_data="users_list")
            ]
        ])

    return keyboard


def get_doctors_keyboard():
    """Клавиатура с врачами"""
    buttons = []
    for doctor in DOCTORS:
        buttons.append([InlineKeyboardButton(text=doctor, callback_data=f"select_doctor:{doctor}")])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_procedures_keyboard(doctor: str):
    """Клавиатура с процедурами для выбранного врача"""
    doctor_key = doctor.split()[0].lower()
    procedures = PROCEDURES.get(doctor_key, ["Консультация"])

    buttons = []
    for procedure in procedures:
        buttons.append([InlineKeyboardButton(text=procedure, callback_data=f"select_procedure:{procedure}")])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="select_doctor")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_dates_keyboard():
    """Клавиатура с датами на ближайшие 7 дней"""
    buttons = []
    today = datetime.now()

    for i in range(7):
        date = today + timedelta(days=i)
        date_str = date.strftime("%d.%m.%Y")
        day_name = date.strftime("%A")[:3]
        buttons.append([InlineKeyboardButton(
            text=f"{date_str} ({day_name})",
            callback_data=f"select_date:{date_str}"
        )])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="select_doctor")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_times_keyboard():
    """Клавиатура с доступным временем"""
    buttons = []
    for time in AVAILABLE_TIMES:
        buttons.append([InlineKeyboardButton(text=time, callback_data=f"select_time:{time}")])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="select_date")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_appointments_keyboard(appointments: list, is_admin: bool = False):
    """Клавиатура со списком записей"""
    buttons = []
    for apt in appointments:
        text = f"{apt['date']} {apt['time']} - {apt['doctor']}"
        if is_admin:
            callback = f"admin_view:{apt['id']}"
        else:
            callback = f"view_appointment:{apt['id']}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=callback)])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_appointment_actions_keyboard(appointment_id: int, is_admin: bool = False):
    """Клавиатура действий для конкретной записи"""
    buttons = []

    if is_admin:
        buttons = [
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_appointment:{appointment_id}")],
            [InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_appointment:{appointment_id}")],
            [InlineKeyboardButton(text="📅 В календарь", callback_data=f"add_to_calendar:{appointment_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="all_appointments")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_appointment:{appointment_id}")],
            [InlineKeyboardButton(text="📅 В календарь", callback_data=f"add_to_calendar:{appointment_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="my_appointments")]
        ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_edit_keyboard(appointment_id: int):
    """Клавиатура для редактирования записи (админ)"""
    buttons = [
        [InlineKeyboardButton(text="👤 Имя пациента", callback_data=f"edit_patient_name:{appointment_id}")],
        [InlineKeyboardButton(text="👨‍⚕️ Врача", callback_data=f"edit_doctor:{appointment_id}")],
        [InlineKeyboardButton(text="💉 Процедуру", callback_data=f"edit_procedure:{appointment_id}")],
        [InlineKeyboardButton(text="📅 Дату", callback_data=f"edit_date:{appointment_id}")],
        [InlineKeyboardButton(text="⏰ Время", callback_data=f"edit_time:{appointment_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_appointment:{appointment_id}")]
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_doctors_keyboard_for_edit():
    """Клавиатура с врачами для редактирования"""
    buttons = []
    for doctor in DOCTORS:
        buttons.append([InlineKeyboardButton(text=doctor, callback_data=f"edit_select_doctor:{doctor}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_confirmation_keyboard():
    """Клавиатура подтверждения"""
    buttons = [
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cancel_keyboard():
    """Клавиатура для отмены действия"""
    buttons = [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def format_appointment(appointment: Dict, is_admin: bool = False) -> str:
    """Форматирование информации о записи"""
    status_emoji = {
        'active': '✅',
        'deleted': '❌',
        'completed': '✔️'
    }.get(appointment['status'], '⏳')

    text = f"{status_emoji} Запись #{appointment['id']}\n\n"
    text += f"👤 Пациент: {appointment['patient_name']}\n"
    text += f"👨‍⚕️ Врач: {appointment['doctor']}\n"
    text += f"💉 Процедура: {appointment['procedure']}\n"
    text += f"📅 Дата: {appointment['date']}\n"
    text += f"⏰ Время: {appointment['time']}\n"

    if is_admin:
        text += f"🆔 ID пользователя: {appointment['user_id']}\n"
        text += f"📝 Статус: {appointment['status']}\n"
        text += f"📅 Создано: {appointment['created_at'][:16]}\n"

    return text


def generate_calendar_event(appointment: Dict) -> Optional[str]:
    """Генерация файла для календаря (.ics)"""
    try:
        date_str = f"{appointment['date']} {appointment['time']}"
        event_date = datetime.strptime(date_str, "%d.%m.%Y %H:%M")

        start_time = event_date.strftime("%Y%m%dT%H%M%S")
        end_time = event_date.replace(hour=event_date.hour + 1).strftime("%Y%m%dT%H%M%S")

        ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Clinic Bot//EN
BEGIN:VEVENT
UID:{appointment['id']}@clinicbot
DTSTART:{start_time}
DTEND:{end_time}
SUMMARY:Прием у {appointment['doctor']}
DESCRIPTION:Пациент: {appointment['patient_name']}\\nПроцедура: {appointment['procedure']}
LOCATION:Клиника «Здоровье»
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR"""

        filename = f"appointment_{appointment['id']}.ics"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(ics_content)

        return filename
    except Exception as e:
        print(f"Ошибка создания календаря: {e}")
        return None


def cleanup_temp_files():
    """Очистка временных файлов"""
    for file in os.listdir('.'):
        if file.startswith('appointment_') and file.endswith('.ics'):
            try:
                os.remove(file)
            except:
                pass


# ==================== СОСТОЯНИЯ FSM ====================
class AppointmentStates(StatesGroup):
    waiting_for_patient_name = State()
    waiting_for_doctor = State()
    waiting_for_procedure = State()
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_confirmation = State()


class EditStates(StatesGroup):
    waiting_for_new_patient_name = State()
    waiting_for_new_doctor = State()
    waiting_for_new_procedure = State()
    waiting_for_new_date = State()
    waiting_for_new_time = State()


# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ==================== ОБРАБОТЧИКИ КОМАНД ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start - приветствие пользователя по имени"""
    user = message.from_user
    db.add_user(user.id, user.username, user.first_name)

    welcome_text = (
        f"👋 Здравствуйте, {user.first_name}!\n\n"
        f"Добро пожаловать в бот клиники «Здоровье».\n"
        f"Здесь вы можете записаться на прием к врачу, "
        f"просмотреть свои записи и управлять ими."
    )

    is_admin = user.id in ADMIN_IDS
    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(is_admin)
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    help_text = (
        "📋 Доступные команды:\n\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать это сообщение\n"
        "/menu - Главное меню\n"
        "/stop - Завершить работу\n\n"
        "Также вы можете использовать инлайн-кнопки для навигации."
    )
    await message.answer(help_text)


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    """Обработчик команды /menu"""
    user = message.from_user
    is_admin = user.id in ADMIN_IDS
    await message.answer(
        "Главное меню:",
        reply_markup=get_main_keyboard(is_admin)
    )


@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    """Обработчик команды /stop"""
    await message.answer(
        "👋 До свидания! Чтобы возобновить работу, нажмите /start"
    )


# ==================== ОБРАБОТЧИКИ КОЛБЭКОВ ====================
@dp.callback_query(lambda c: c.data == 'main_menu')
async def process_callback_main_menu(callback: CallbackQuery):
    """Возврат в главное меню"""
    user = callback.from_user
    is_admin = user.id in ADMIN_IDS

    # Отправляем новое сообщение вместо редактирования
    await callback.message.answer(
        "Главное меню:",
        reply_markup=get_main_keyboard(is_admin)
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == 'make_appointment')
async def process_callback_make_appointment(callback: CallbackQuery, state: FSMContext):
    """Начало процесса записи"""
    await callback.message.edit_text(
        "👤 Введите имя и фамилию пациента:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AppointmentStates.waiting_for_patient_name)
    await callback.answer()


@dp.message(AppointmentStates.waiting_for_patient_name)
async def process_patient_name(message: Message, state: FSMContext):
    """Обработка имени пациента"""
    patient_name = message.text.strip()

    if len(patient_name) < 2 or len(patient_name) > 50:
        await message.answer(
            "❌ Пожалуйста, введите корректное имя (от 2 до 50 символов):"
        )
        return

    await state.update_data(patient_name=patient_name)

    await message.answer(
        "👨‍⚕️ Выберите врача:",
        reply_markup=get_doctors_keyboard()
    )
    await state.set_state(AppointmentStates.waiting_for_doctor)


@dp.callback_query(lambda c: c.data.startswith('select_doctor:'), StateFilter(AppointmentStates.waiting_for_doctor))
async def process_callback_select_doctor(callback: CallbackQuery, state: FSMContext):
    """Выбор врача"""
    doctor = callback.data.split(':', 1)[1]
    await state.update_data(doctor=doctor)

    await callback.message.edit_text(
        f"💉 Выберите процедуру для {doctor}:",
        reply_markup=get_procedures_keyboard(doctor)
    )
    await state.set_state(AppointmentStates.waiting_for_procedure)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('select_procedure:'),
                   StateFilter(AppointmentStates.waiting_for_procedure))
async def process_callback_select_procedure(callback: CallbackQuery, state: FSMContext):
    """Выбор процедуры"""
    procedure = callback.data.split(':', 1)[1]
    await state.update_data(procedure=procedure)

    await callback.message.edit_text(
        "📅 Выберите дату:",
        reply_markup=get_dates_keyboard()
    )
    await state.set_state(AppointmentStates.waiting_for_date)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('select_date:'), StateFilter(AppointmentStates.waiting_for_date))
async def process_callback_select_date(callback: CallbackQuery, state: FSMContext):
    """Выбор даты"""
    date = callback.data.split(':', 1)[1]
    await state.update_data(date=date)

    await callback.message.edit_text(
        "⏰ Выберите время:",
        reply_markup=get_times_keyboard()
    )
    await state.set_state(AppointmentStates.waiting_for_time)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('select_time:'), StateFilter(AppointmentStates.waiting_for_time))
async def process_callback_select_time(callback: CallbackQuery, state: FSMContext):
    """Выбор времени"""
    time = callback.data.split(':', 1)[1]
    data = await state.get_data()

    # Проверка доступности времени
    if not db.is_appointment_available(data['doctor'], data['date'], time):
        await callback.message.edit_text(
            "❌ Это время уже занято. Пожалуйста, выберите другое время:",
            reply_markup=get_times_keyboard()
        )
        await callback.answer()
        return

    await state.update_data(time=time)

    # Показываем подтверждение
    appointment_info = (
        f"📋 Проверьте данные записи:\n\n"
        f"👤 Пациент: {data['patient_name']}\n"
        f"👨‍⚕️ Врач: {data['doctor']}\n"
        f"💉 Процедура: {data['procedure']}\n"
        f"📅 Дата: {data['date']}\n"
        f"⏰ Время: {time}\n\n"
        f"Всё верно?"
    )

    await callback.message.edit_text(
        appointment_info,
        reply_markup=get_confirmation_keyboard()
    )
    await state.set_state(AppointmentStates.waiting_for_confirmation)
    await callback.answer()


@dp.callback_query(lambda c: c.data == 'confirm', StateFilter(AppointmentStates.waiting_for_confirmation))
async def process_callback_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение записи"""
    data = await state.get_data()
    user = callback.from_user

    # Создаем запись в базе данных
    appointment_id = db.create_appointment(
        user_id=user.id,
        patient_name=data['patient_name'],
        doctor=data['doctor'],
        procedure=data['procedure'],
        date=data['date'],
        time=data['time']
    )

    success_text = (
        f"✅ Запись успешно создана!\n\n"
        f"Номер записи: #{appointment_id}\n"
        f"👤 Пациент: {data['patient_name']}\n"
        f"👨‍⚕️ Врач: {data['doctor']}\n"
        f"💉 Процедура: {data['procedure']}\n"
        f"📅 Дата: {data['date']}\n"
        f"⏰ Время: {data['time']}"
    )

    await callback.message.edit_text(success_text)

    # Возвращаемся в главное меню
    is_admin = user.id in ADMIN_IDS
    await callback.message.answer(
        "Главное меню:",
        reply_markup=get_main_keyboard(is_admin)
    )

    await state.clear()
    await callback.answer()


@dp.callback_query(lambda c: c.data == 'cancel')
async def process_callback_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена действия"""
    user = callback.from_user
    is_admin = user.id in ADMIN_IDS

    await state.clear()
    await callback.message.edit_text(
        "❌ Действие отменено.\n\nГлавное меню:",
        reply_markup=get_main_keyboard(is_admin)
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == 'my_appointments')
async def process_callback_my_appointments(callback: CallbackQuery):
    """Просмотр записей пользователя"""
    user = callback.from_user
    appointments = db.get_appointments(user.id)

    if not appointments:
        # Если записей нет, отправляем новое сообщение
        await callback.message.answer(
            "📭 У вас пока нет записей.\n\n"
            "Чтобы создать новую запись, нажмите «Записаться».",
            reply_markup=get_main_keyboard(user.id in ADMIN_IDS)
        )
    else:
        # Если записи есть, отправляем новое сообщение со списком
        await callback.message.answer(
            "📋 Ваши записи:",
            reply_markup=get_appointments_keyboard(appointments)
        )

    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('view_appointment:'))
async def process_callback_view_appointment(callback: CallbackQuery):
    """Просмотр конкретной записи"""
    appointment_id = int(callback.data.split(':')[1])
    appointment = db.get_appointment(appointment_id)
    user = callback.from_user

    if not appointment:
        await callback.message.edit_text(
            "❌ Запись не найдена.",
            reply_markup=get_main_keyboard(user.id in ADMIN_IDS)
        )
        await callback.answer()
        return

    text = format_appointment(appointment)
    is_admin = user.id in ADMIN_IDS

    await callback.message.edit_text(
        text,
        reply_markup=get_appointment_actions_keyboard(appointment_id, is_admin)
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('cancel_appointment:'))
async def process_callback_cancel_appointment(callback: CallbackQuery):
    """Отмена записи пользователем"""
    appointment_id = int(callback.data.split(':')[1])

    if db.delete_appointment(appointment_id):
        await callback.message.edit_text(
            "✅ Запись успешно отменена.",
            reply_markup=get_main_keyboard(callback.from_user.id in ADMIN_IDS)
        )
    else:
        await callback.message.edit_text(
            "❌ Не удалось отменить запись.",
            reply_markup=get_main_keyboard(callback.from_user.id in ADMIN_IDS)
        )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('add_to_calendar:'))
async def process_callback_add_to_calendar(callback: CallbackQuery):
    """Добавление записи в календарь"""
    appointment_id = int(callback.data.split(':')[1])
    appointment = db.get_appointment(appointment_id)

    if not appointment:
        await callback.answer("❌ Запись не найдена")
        return

    # Генерируем файл для календаря
    calendar_file = generate_calendar_event(appointment)

    if calendar_file:
        with open(calendar_file, 'rb') as f:
            await callback.message.answer_document(
                types.FSInputFile(calendar_file),
                caption="📅 Файл для добавления в календарь"
            )

    await callback.answer("✅ Файл для календаря создан")


@dp.callback_query(lambda c: c.data == 'doctors_list')
async def process_callback_doctors_list(callback: CallbackQuery):
    """Список врачей"""
    text = "👨‍⚕️ Наши врачи:\n\n"
    for doctor in DOCTORS:
        text += f"• {doctor}\n"

    # Отправляем новым сообщением, чтобы избежать ошибки "message not modified"
    await callback.message.answer(
        text,
        reply_markup=get_main_keyboard(callback.from_user.id in ADMIN_IDS)
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == 'about')
async def process_callback_about(callback: CallbackQuery):
    """Информация о клинике"""
    text = (
        "🏥 Клиника «Здоровье»\n\n"
        "📍 Адрес: г. Москва, ул. Медицинская, д. 10\n"
        "📞 Телефон: +7 (495) 123-45-67\n"
        "🕒 Режим работы: Пн-Пт 8:00-20:00, Сб 9:00-18:00\n\n"
        "Мы заботимся о вашем здоровье!"
    )

    # Отправляем новым сообщением
    await callback.message.answer(
        text,
        reply_markup=get_main_keyboard(callback.from_user.id in ADMIN_IDS)
    )
    await callback.answer()


# ==================== АДМИНСКИЕ ОБРАБОТЧИКИ ====================
@dp.callback_query(lambda c: c.data == 'all_appointments')
async def process_callback_all_appointments(callback: CallbackQuery):
    """Просмотр всех записей (для админа)"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    appointments = db.get_appointments()

    if not appointments:
        await callback.message.answer(
            "📭 Нет записей.",
            reply_markup=get_main_keyboard(True)
        )
        await callback.answer()
        return

    await callback.message.answer(
        "📋 Все записи:",
        reply_markup=get_appointments_keyboard(appointments, is_admin=True)
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('admin_view:'))
async def process_callback_admin_view(callback: CallbackQuery):
    """Просмотр записи админом"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    appointment_id = int(callback.data.split(':')[1])
    appointment = db.get_appointment(appointment_id)

    if not appointment:
        await callback.message.edit_text(
            "❌ Запись не найдена.",
            reply_markup=get_main_keyboard(True)
        )
        await callback.answer()
        return

    text = format_appointment(appointment, is_admin=True)

    await callback.message.edit_text(
        text,
        reply_markup=get_appointment_actions_keyboard(appointment_id, is_admin=True)
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('delete_appointment:'))
async def process_callback_delete_appointment(callback: CallbackQuery):
    """Удаление записи админом"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    appointment_id = int(callback.data.split(':')[1])

    if db.delete_appointment(appointment_id):
        await callback.message.edit_text(
            "✅ Запись успешно удалена.",
            reply_markup=get_main_keyboard(True)
        )
    else:
        await callback.message.edit_text(
            "❌ Не удалось удалить запись.",
            reply_markup=get_main_keyboard(True)
        )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('edit_appointment:'))
async def process_callback_edit_appointment(callback: CallbackQuery):
    """Начало редактирования записи"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    appointment_id = int(callback.data.split(':')[1])

    await callback.message.edit_text(
        "✏️ Что вы хотите отредактировать?",
        reply_markup=get_admin_edit_keyboard(appointment_id)
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == 'users_list')
async def process_callback_users_list(callback: CallbackQuery):
    """Список пользователей для админа"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    users = db.get_users()

    if not users:
        await callback.message.answer(
            "👥 Нет зарегистрированных пользователей.",
            reply_markup=get_main_keyboard(True)
        )
        await callback.answer()
        return

    text = "👥 Список пользователей:\n\n"
    for user_id, user_data in users.items():
        text += f"ID: {user_id}\n"
        text += f"Имя: {user_data['first_name']}\n"
        if user_data['username']:
            text += f"Username: @{user_data['username']}\n"
        text += f"Регистрация: {user_data['registered_at'][:10]}\n"
        text += "-" * 20 + "\n"

    await callback.message.answer(text)
    await callback.answer()


# ==================== РЕДАКТИРОВАНИЕ ЗАПИСЕЙ (АДМИН) ====================
@dp.callback_query(lambda c: c.data.startswith('edit_patient_name:'))
async def process_callback_edit_patient_name(callback: CallbackQuery, state: FSMContext):
    """Редактирование имени пациента"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    appointment_id = int(callback.data.split(':')[1])
    await state.update_data(edit_appointment_id=appointment_id)

    await callback.message.edit_text(
        "👤 Введите новое имя пациента:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(EditStates.waiting_for_new_patient_name)
    await callback.answer()


@dp.message(EditStates.waiting_for_new_patient_name)
async def process_new_patient_name(message: Message, state: FSMContext):
    """Обработка нового имени пациента"""
    data = await state.get_data()
    appointment_id = data.get('edit_appointment_id')

    new_name = message.text.strip()
    if len(new_name) < 2 or len(new_name) > 50:
        await message.answer("❌ Некорректное имя. Попробуйте снова:")
        return

    if db.update_appointment(appointment_id, patient_name=new_name):
        await message.answer("✅ Имя пациента успешно обновлено!")
    else:
        await message.answer("❌ Не удалось обновить имя.")

    await state.clear()


# ==================== РЕДАКТИРОВАНИЕ ВРАЧА (АДМИН) ====================
@dp.callback_query(lambda c: c.data.startswith('edit_doctor:'))
async def process_callback_edit_doctor(callback: CallbackQuery, state: FSMContext):
    """Редактирование врача"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    appointment_id = int(callback.data.split(':')[1])
    await state.update_data(edit_appointment_id=appointment_id)

    # Показываем список врачей для выбора
    await callback.message.edit_text(
        "👨‍⚕️ Выберите нового врача:",
        reply_markup=get_doctors_keyboard_for_edit()
    )
    await state.set_state(EditStates.waiting_for_new_doctor)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('edit_select_doctor:'), StateFilter(EditStates.waiting_for_new_doctor))
async def process_callback_select_new_doctor(callback: CallbackQuery, state: FSMContext):
    """Выбор нового врача"""
    doctor = callback.data.split(':', 1)[1]
    data = await state.get_data()
    appointment_id = data.get('edit_appointment_id')

    if db.update_appointment(appointment_id, doctor=doctor):
        await callback.message.edit_text("✅ Врач успешно обновлен!")
    else:
        await callback.message.edit_text("❌ Не удалось обновить врача.")

    await state.clear()
    await callback.answer()


# ==================== РЕДАКТИРОВАНИЕ ПРОЦЕДУРЫ (АДМИН) ====================
@dp.callback_query(lambda c: c.data.startswith('edit_procedure:'))
async def process_callback_edit_procedure(callback: CallbackQuery, state: FSMContext):
    """Редактирование процедуры"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    appointment_id = int(callback.data.split(':')[1])
    appointment = db.get_appointment(appointment_id)

    if not appointment:
        await callback.message.edit_text("❌ Запись не найдена")
        await callback.answer()
        return

    await state.update_data(edit_appointment_id=appointment_id)

    # Показываем список процедур для текущего врача
    doctor_key = appointment['doctor'].split()[0].lower()
    procedures = PROCEDURES.get(doctor_key, ["Консультация"])

    buttons = []
    for procedure in procedures:
        buttons.append([InlineKeyboardButton(
            text=procedure,
            callback_data=f"edit_select_procedure:{procedure}"
        )])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_appointment:{appointment_id}")])

    await callback.message.edit_text(
        f"💉 Выберите новую процедуру для {appointment['doctor']}:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(EditStates.waiting_for_new_procedure)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('edit_select_procedure:'),
                   StateFilter(EditStates.waiting_for_new_procedure))
async def process_callback_select_new_procedure(callback: CallbackQuery, state: FSMContext):
    """Выбор новой процедуры"""
    procedure = callback.data.split(':', 1)[1]
    data = await state.get_data()
    appointment_id = data.get('edit_appointment_id')

    if db.update_appointment(appointment_id, procedure=procedure):
        await callback.message.edit_text("✅ Процедура успешно обновлена!")
    else:
        await callback.message.edit_text("❌ Не удалось обновить процедуру.")

    await state.clear()
    await callback.answer()


# ==================== РЕДАКТИРОВАНИЕ ДАТЫ (АДМИН) ====================
@dp.callback_query(lambda c: c.data.startswith('edit_date:'))
async def process_callback_edit_date(callback: CallbackQuery, state: FSMContext):
    """Редактирование даты"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    appointment_id = int(callback.data.split(':')[1])
    await state.update_data(edit_appointment_id=appointment_id)

    # Показываем календарь для выбора даты
    buttons = []
    today = datetime.now()

    for i in range(14):  # Показываем 14 дней для выбора
        date = today + timedelta(days=i)
        date_str = date.strftime("%d.%m.%Y")
        day_name = date.strftime("%A")[:3]
        buttons.append([InlineKeyboardButton(
            text=f"{date_str} ({day_name})",
            callback_data=f"edit_select_date:{date_str}"
        )])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_appointment:{appointment_id}")])

    await callback.message.edit_text(
        "📅 Выберите новую дату:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(EditStates.waiting_for_new_date)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('edit_select_date:'), StateFilter(EditStates.waiting_for_new_date))
async def process_callback_select_new_date(callback: CallbackQuery, state: FSMContext):
    """Выбор новой даты"""
    date = callback.data.split(':', 1)[1]
    data = await state.get_data()
    appointment_id = data.get('edit_appointment_id')

    if db.update_appointment(appointment_id, date=date):
        await callback.message.edit_text("✅ Дата успешно обновлена!")
    else:
        await callback.message.edit_text("❌ Не удалось обновить дату.")

    await state.clear()
    await callback.answer()


# ==================== РЕДАКТИРОВАНИЕ ВРЕМЕНИ (АДМИН) ====================
@dp.callback_query(lambda c: c.data.startswith('edit_time:'))
async def process_callback_edit_time(callback: CallbackQuery, state: FSMContext):
    """Редактирование времени"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещен")
        return

    appointment_id = int(callback.data.split(':')[1])
    await state.update_data(edit_appointment_id=appointment_id)

    # Показываем доступное время
    buttons = []
    for time in AVAILABLE_TIMES:
        buttons.append([InlineKeyboardButton(
            text=time,
            callback_data=f"edit_select_time:{time}"
        )])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_appointment:{appointment_id}")])

    await callback.message.edit_text(
        "⏰ Выберите новое время:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(EditStates.waiting_for_new_time)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith('edit_select_time:'), StateFilter(EditStates.waiting_for_new_time))
async def process_callback_select_new_time(callback: CallbackQuery, state: FSMContext):
    """Выбор нового времени"""
    time = callback.data.split(':', 1)[1]
    data = await state.get_data()
    appointment_id = data.get('edit_appointment_id')

    if db.update_appointment(appointment_id, time=time):
        await callback.message.edit_text("✅ Время успешно обновлено!")
    else:
        await callback.message.edit_text("❌ Не удалось обновить время.")

    await state.clear()
    await callback.answer()


# ==================== ЗАПУСК БОТА ====================
async def on_startup():
    """Действия при запуске бота"""
    logger.info("Бот запущен")
    cleanup_temp_files()

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, "✅ Бот клиники успешно запущен!")
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение админу {admin_id}: {e}")


async def on_shutdown():
    """Действия при остановке бота"""
    logger.info("Бот остановлен")
    cleanup_temp_files()
    await bot.session.close()


async def main():
    """Главная функция"""
    await on_startup()
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")