import telebot
import logging
from django.core.management.base import BaseCommand
from telebot.types import Message, CallbackQuery
from main.models import User, Event, Attendance, TelegramChannel
from event_bot import settings
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from main.keyboards import (
    main_menu_keyboard,
    category_keyboard,
    attendance_keyboard,
    back_to_main_menu_keyboard,
    my_events_keyboard,
    my_events_category_keyboard,
    my_event_actions_keyboard,
    private_channels_keyboard
)
from datetime import datetime, timedelta
import calendar
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from django.core.cache import cache
import threading
import time
from functools import lru_cache
from django.db.models import Q

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
bot = telebot.TeleBot(settings.TOKENBOT, parse_mode="HTML")

# Глобальный словарь для хранения состояний пользователей
user_selection = {}
# Время жизни состояния пользователя (в секундах)
STATE_LIFETIME = 3600  # 1 час
# Время жизни кэша (в секундах)
CACHE_LIFETIME = 300  # 5 минут

@lru_cache(maxsize=100)
def get_event_cache_key(event_type, category):
    """Генерация ключа кэша для мероприятий"""
    return f"events_{event_type}_{category}"

def get_cached_events(event_type, category):
    """Получение мероприятий из кэша или базы данных"""
    cache_key = get_event_cache_key(event_type, category)
    events = cache.get(cache_key)
    
    if events is None:
        events = list(Event.objects.filter(
            event_type=event_type,
            category=category,
            date_time__gte=datetime.now()
        ).order_by("date_time"))
        cache.set(cache_key, events, CACHE_LIFETIME)
        logger.info(f"Updated cache for {event_type} {category}")
    
    return events

def invalidate_event_cache(event_type=None, category=None):
    """Инвалидация кэша мероприятий"""
    if event_type and category:
        cache_key = get_event_cache_key(event_type, category)
        cache.delete(cache_key)
    else:
        # Очистка всего кэша мероприятий
        for event_type in Event.EVENT_TYPE_CHOICES:
            for category in Event.CATEGORY_CHOICES:
                cache_key = get_event_cache_key(event_type[0], category[0])
                cache.delete(cache_key)
    logger.info("Event cache invalidated")

@lru_cache(maxsize=100)
def get_user_events_cache_key(user_id, status):
    """Генерация ключа кэша для мероприятий пользователя"""
    return f"user_events_{user_id}_{status}"

def get_cached_user_events(user_id, status):
    """Получение мероприятий пользователя из кэша или базы данных"""
    cache_key = get_user_events_cache_key(user_id, status)
    events = cache.get(cache_key)
    
    if events is None:
        events = list(Event.objects.filter(
            attendance__user__telegram_id=str(user_id),
            attendance__status=status,
            date_time__gte=datetime.now()
        ).order_by("date_time"))
        cache.set(cache_key, events, CACHE_LIFETIME)
        logger.info(f"Updated user events cache for {user_id} {status}")
    
    return events

def invalidate_user_events_cache(user_id=None, status=None):
    """Инвалидация кэша мероприятий пользователя"""
    if user_id and status:
        cache_key = get_user_events_cache_key(user_id, status)
        cache.delete(cache_key)
    else:
        # Очистка всего кэша мероприятий пользователей
        for user in User.objects.all():
            for status in ['going', 'maybe']:
                cache_key = get_user_events_cache_key(user.telegram_id, status)
                cache.delete(cache_key)
    logger.info("User events cache invalidated")

def cleanup_old_states():
    """Очистка устаревших состояний пользователей"""
    current_time = time.time()
    expired_users = [
        user_id for user_id, state in user_selection.items()
        if current_time - state.get('timestamp', 0) > STATE_LIFETIME
    ]
    for user_id in expired_users:
        del user_selection[user_id]
        logger.info(f"Cleaned up state for user {user_id}")

def update_user_state(user_id, data):
    """Обновление состояния пользователя"""
    user_selection[user_id] = {
        **data,
        'timestamp': time.time()
    }
    logger.info(f"Updated user {user_id} state: {data}")

def get_user_state(user_id):
    """Получение состояния пользователя"""
    state = user_selection.get(user_id)
    if state and time.time() - state.get('timestamp', 0) > STATE_LIFETIME:
        del user_selection[user_id]
        return None
    logger.info(f"Retrieved user {user_id} state: {state}")
    return state

def start_cleanup_thread():
    """Запуск потока для периодической очистки состояний"""
    def cleanup_loop():
        while True:
            cleanup_old_states()
            time.sleep(300)  # Проверка каждые 5 минут

    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()
    return thread

def handle_error(chat_id, error_message, original_message=None):
    """Обработка ошибок и отправка сообщения пользователю"""
    logger.error(f"Error in chat {chat_id}: {error_message}")
    if original_message:
        logger.error(f"Original message: {original_message}")
    bot.send_message(chat_id, "Произошла ошибка. Пожалуйста, попробуйте позже или обратитесь к администратору.")
    bot.send_message(chat_id, "Выбери тип мероприятия:", reply_markup=main_menu_keyboard())

def safe_delete_last_message(chat_id, user_id):
    state = get_user_state(user_id)
    if state and state.get('last_message_id'):
        try:
            bot.delete_message(chat_id, state['last_message_id'])
        except Exception:
            pass

def send_and_store_message(chat_id, user_id, *args, keep_message=False, **kwargs):
    if not keep_message:
        safe_delete_last_message(chat_id, user_id)
    msg = bot.send_message(chat_id, *args, **kwargs)
    if not keep_message:
        state = get_user_state(user_id) or {}
        state['last_message_id'] = msg.message_id
        update_user_state(user_id, state)
    return msg

@bot.message_handler(commands=["start"])
def start(message: Message):
    try:
        telegram_id = str(message.from_user.id)
        username = message.from_user.username
        with transaction.atomic():
            user, created = User.objects.get_or_create(
                telegram_id=telegram_id,
                defaults={"username": username}
            )
        text = f"Привет, {username or 'пользователь'}! 🎉 Ты зарегистрирован в системе." if created else \
               f"С возвращением, {username or 'пользователь'}! 🔥"
        send_and_store_message(message.chat.id, message.from_user.id, text, keep_message=True)
        send_and_store_message(message.chat.id, message.from_user.id, "Выбери тип мероприятия:", reply_markup=main_menu_keyboard())
        logger.info(f"User {telegram_id} started the bot")
    except Exception as e:
        handle_error(message.chat.id, str(e), message.text)

@bot.callback_query_handler(func=lambda call: call.data == "back_main")
def back_to_main(call: CallbackQuery):
    try:
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        # Очистить список мероприятий из состояния
        state = get_user_state(call.from_user.id) or {}
        state.pop('events', None)
        update_user_state(call.from_user.id, state)
        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            "Выберите тип мероприятия:",
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("event_type_"))
def select_event_type(call: CallbackQuery):
    try:
        # Delete the current message
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        event_type = call.data.split("_")[2]
        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            f"Выберите категорию для {event_type} мероприятий:",
            reply_markup=category_keyboard(event_type)
        )
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("category_"))
def select_category(call: CallbackQuery):
    try:
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        event_type = call.data.split("_")[1]
        category = call.data.split("_")[2]
        user = User.objects.get(telegram_id=str(call.from_user.id))
        # Исключаем мероприятия, на которые пользователь уже записан
        attending_events = set(Event.objects.filter(
            attendance__user=user,
            attendance__status="going"
        ).values_list('id', flat=True))
        events = list(Event.objects.filter(
            event_type=event_type,
            category=category,
            date_time__gte=datetime.now()
        ).exclude(id__in=attending_events).order_by("date_time"))
        if not events:
            send_and_store_message(
                call.message.chat.id,
                call.from_user.id,
                "На данный момент нет доступных мероприятий в этой категории.",
                reply_markup=back_to_main_menu_keyboard()
            )
            return
        message = f"Доступные {category} мероприятия ({event_type}):\n\n"
        for i, event in enumerate(events, 1):
            message += f"{i}. {event.name}\n"
            message += f"   📅 {event.date_time.strftime('%d.%m.%Y %H:%M')}\n"
            message += f"   📍 {event.location}\n"
            if event.address:
                message += f"   🏠 {event.address}\n"
            if event.link_2gis:
                message += f"   🗺️ {event.link_2gis}\n"
            message += "\n"
        # Save events in state
        state = get_user_state(call.from_user.id) or {}
        state["events"] = events
        update_user_state(call.from_user.id, state)
        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            message,
            reply_markup=back_to_main_menu_keyboard()
        )
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("going_"))
def mark_attendance(call: CallbackQuery):
    try:
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        event_id = call.data.replace("going_", "")
        with transaction.atomic():
            user = User.objects.get(telegram_id=str(call.from_user.id))
            event = Event.objects.get(id=int(event_id))
            attendance, created = Attendance.objects.update_or_create(
                user=user,
                event=event,
                defaults={"status": "going"}
            )
        invalidate_user_events_cache(user.telegram_id, "going")
        send_and_store_message(call.message.chat.id, call.from_user.id, "✅ Ты отметил своё участие.", keep_message=True)
        send_and_store_message(call.message.chat.id, call.from_user.id, "Выбери тип мероприятия:", reply_markup=main_menu_keyboard())
        logger.info(f"User {call.from_user.id} marked attendance for event {event_id}")
    except ObjectDoesNotExist:
        handle_error(call.message.chat.id, "Мероприятие или пользователь не найдены", call.data)
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.data)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_status_"))
def edit_status(call: CallbackQuery):
    try:
        # Delete the current message
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        
        _, _, action, event_id = call.data.split("_", 3)
        user = User.objects.get(telegram_id=str(call.from_user.id))
        
        with transaction.atomic():
            try:
                attendance = Attendance.objects.get(user=user, event__id=event_id)
            except Attendance.DoesNotExist:
                send_and_store_message(call.message.chat.id, call.from_user.id, "Участие не найдено.")
                return

            if action == "going":
                old_status = attendance.status
                attendance.status = "going"
                attendance.save()
                
                # Инвалидация кэша
                invalidate_user_events_cache(user.telegram_id, "going")
                invalidate_user_events_cache(user.telegram_id, old_status)
                
                send_and_store_message(call.message.chat.id, call.from_user.id, "✅ Статус обновлён на 'Иду'.", 
                               reply_markup=main_menu_keyboard())
            elif action == "delete":
                old_status = attendance.status
                attendance.delete()
                
                # Инвалидация кэша
                invalidate_user_events_cache(user.telegram_id, old_status)
                
                send_and_store_message(call.message.chat.id, call.from_user.id, "🗑 Участие удалено.", reply_markup=main_menu_keyboard())
            else:
                send_and_store_message(call.message.chat.id, call.from_user.id, "Неизвестное действие.")
        
        logger.info(f"User {call.from_user.id} {action}ed attendance for event {event_id}")
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.data)

@bot.callback_query_handler(func=lambda call: call.data == "maybe_events")
def show_maybe_categories(call: CallbackQuery):
    try:
        # Delete the current message
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        
        user = User.objects.get(telegram_id=str(call.from_user.id))
        events = get_cached_user_events(call.from_user.id, "maybe")

        if not events:
            send_and_store_message(call.message.chat.id, call.from_user.id, "У тебя нет неопределённых мероприятий.", reply_markup=back_to_main_menu_keyboard())
            return

        # Группируем мероприятия по категориям
        categories = {}
        for event in events:
            if event.category not in categories:
                categories[event.category] = []
            categories[event.category].append(event)

        # Создаем клавиатуру с категориями
        markup = InlineKeyboardMarkup()
        for category in categories.keys():
            display = dict(Event.CATEGORY_CHOICES).get(category, category)
            markup.add(InlineKeyboardButton(f"{display}", callback_data=f"maybe_cat_{category}"))
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
        
        send_and_store_message(call.message.chat.id, call.from_user.id, "Выбери категорию мероприятия:", reply_markup=markup)
        logger.info(f"User {call.from_user.id} viewed maybe events categories")
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.data)

@bot.callback_query_handler(func=lambda call: call.data.startswith("maybe_cat_"))
def maybe_category_events(call: CallbackQuery):
    try:
        # Delete the current message
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        
        category = call.data.replace("maybe_cat_", "")
        events = get_cached_user_events(call.from_user.id, "maybe")
        
        if not events:
            send_and_store_message(
                call.message.chat.id,
                call.from_user.id,
                "У вас нет мероприятий в этой категории.",
                reply_markup=back_to_main_menu_keyboard()
            )
            return

        message = f"Ваши мероприятия в категории {category}:\n\n"
        category_events = [event for event in events if event.category == category]
        
        if not category_events:
            send_and_store_message(
                call.message.chat.id,
                call.from_user.id,
                "У вас нет мероприятий в этой категории.",
                reply_markup=back_to_main_menu_keyboard()
            )
            return
            
        for i, event in enumerate(category_events, 1):
            message += f"{i}. {event.name}\n"
            message += f"   📅 {event.date_time.strftime('%d.%m.%Y %H:%M')}\n"
            message += f"   📍 {event.location}\n"
            if event.address:
                message += f"   🏠 {event.address}\n"
            if event.link_2gis:
                message += f"   🗺️ {event.link_2gis}\n"
            message += "\n"

        # Save events in state
        state = get_user_state(call.from_user.id) or {}
        state["events"] = category_events
        update_user_state(call.from_user.id, state)

        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            message,
            reply_markup=back_to_main_menu_keyboard()
        )
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.data)

@bot.message_handler(func=lambda message: message.text.isdigit())
def handle_event_number(message: Message):
    try:
        user_id = message.from_user.id
        number = int(message.text)
        state = get_user_state(user_id)
        
        if not state:
            send_and_store_message(message.chat.id, message.from_user.id, "Произошла ошибка. Пожалуйста, начните сначала.", reply_markup=main_menu_keyboard())
            return

        events = None
        if "events" in state:
            events = state["events"]
        
        if not events:
            send_and_store_message(message.chat.id, message.from_user.id, "Пожалуйста, выберите мероприятие из списка.", reply_markup=back_to_main_menu_keyboard())
            return

        if number < 1 or number > len(events):
            send_and_store_message(message.chat.id, message.from_user.id, f"Пожалуйста, выберите номер от 1 до {len(events)}.")
            return

        event = events[number - 1]

        # Формируем текст с информацией о мероприятии
        text = f"<b>{event.name}</b>\n"
        text += f"📍 {event.location}, {event.address}\n"
        text += f"📅 {event.date_time.strftime('%d.%m.%Y %H:%M')}\n"
        if event.details:
            text += f"📝 {event.details}\n"
        if event.link_2gis:
            text += f"🔗 <a href='{event.link_2gis}'>Ссылка на 2ГИС</a>"

        # Проверяем, является ли пользователь участником мероприятия
        try:
            attendance = Attendance.objects.get(user__telegram_id=str(user_id), event=event)
            markup = my_event_actions_keyboard(event.id)
        except Attendance.DoesNotExist:
            markup = attendance_keyboard(event.id)

        send_and_store_message(message.chat.id, message.from_user.id, text, reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        handle_error(message.chat.id, str(e), message.text)

@bot.message_handler(func=lambda message: True)
def fallback_handler(message: Message):
    send_and_store_message(message.chat.id, message.from_user.id, "⛔️ Неизвестная команда. Пожалуйста, выбери действие с клавиатуры.")
    send_and_store_message(message.chat.id, message.from_user.id, "Выбери тип мероприятия:", reply_markup=main_menu_keyboard())

@bot.callback_query_handler(func=lambda call: call.data == "my_events")
def show_my_events_categories(call: CallbackQuery):
    try:
        # Delete the current message
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        
        # Get user's events
        events = get_cached_user_events(call.from_user.id, "going")
        
        if not events:
            send_and_store_message(
                call.message.chat.id,
                call.from_user.id,
                "У вас пока нет мероприятий, на которые вы идёте.",
                reply_markup=back_to_main_menu_keyboard()
            )
            return
            
        # Group events by category
        categories = {}
        for event in events:
            if event.category not in categories:
                categories[event.category] = []
            categories[event.category].append(event)
            
        # Create keyboard with categories
        markup = InlineKeyboardMarkup()
        for category in categories.keys():
            display = dict(Event.CATEGORY_CHOICES).get(category, category)
            markup.add(InlineKeyboardButton(display, callback_data=f"my_cat_{category}"))
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
        
        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            "Выберите категорию ваших мероприятий:",
            reply_markup=markup
        )
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("my_cat_"))
def show_my_category_events(call: CallbackQuery):
    try:
        # Delete the current message
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        
        category = call.data.split("_")[2]
        events = get_cached_user_events(call.from_user.id, "going")
        
        if not events:
            send_and_store_message(
                call.message.chat.id,
                call.from_user.id,
                "У вас нет мероприятий в этой категории.",
                reply_markup=back_to_main_menu_keyboard()
            )
            return

        category_events = [event for event in events if event.category == category]
        
        if not category_events:
            send_and_store_message(
                call.message.chat.id,
                call.from_user.id,
                "У вас нет мероприятий в этой категории.",
                reply_markup=back_to_main_menu_keyboard()
            )
            return

        message = f"Ваши мероприятия в категории {dict(Event.CATEGORY_CHOICES).get(category, category)}:\n\n"
        for i, event in enumerate(category_events, 1):
            message += f"{i}. {event.name}\n"
            message += f"   📅 {event.date_time.strftime('%d.%m.%Y %H:%M')}\n"
            message += f"   📍 {event.location}\n"
            if event.address:
                message += f"   🏠 {event.address}\n"
            if event.link_2gis:
                message += f"   🗺️ {event.link_2gis}\n"
            message += "\n"

        # Save events in state
        state = get_user_state(call.from_user.id) or {}
        state["events"] = category_events
        update_user_state(call.from_user.id, state)

        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            message,
            reply_markup=back_to_main_menu_keyboard()
        )
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.message)

# @bot.callback_query_handler(func=lambda call: call.data.startswith("buy_ticket_"))
# def handle_buy_ticket(call: CallbackQuery):
#     try:
#         event_id = call.data.replace("buy_ticket_", "")
#         event = Event.objects.get(id=event_id)
#         
#         if event.ticket_link:
#             send_and_store_message(
#                 call.message.chat.id,
#                 call.from_user.id,
#                 f"🎫 Ссылка для покупки билета: {event.ticket_link}",
#                 reply_markup=back_to_main_menu_keyboard()
#             )
#         else:
#             send_and_store_message(
#                 call.message.chat.id,
#                 call.from_user.id,
#                 "К сожалению, ссылка на покупку билета пока недоступна.",
#                 reply_markup=back_to_main_menu_keyboard()
#             )
#     except Exception as e:
#         logger.error(f"Error in handle_buy_ticket: {str(e)}")
#         send_and_store_message(
#             call.message.chat.id,
#             call.from_user.id,
#             "Произошла ошибка. Пожалуйста, попробуйте позже.",
#             reply_markup=back_to_main_menu_keyboard()
#         )

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_attendance_"))
def handle_cancel_attendance(call: CallbackQuery):
    try:
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        event_id = call.data.replace("cancel_attendance_", "")
        user = User.objects.get(telegram_id=str(call.from_user.id))
        event = Event.objects.get(id=event_id)
        Attendance.objects.filter(user=user, event=event).delete()
        invalidate_user_events_cache(user.telegram_id, "going")
        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            "❌ Ты отменил своё участие в мероприятии.",
            keep_message=True
        )
        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            "Выбери тип мероприятия:",
            reply_markup=main_menu_keyboard()
        )
        logger.info(f"User {call.from_user.id} cancelled attendance for event {event_id}")
    except Exception as e:
        logger.error(f"Error in handle_cancel_attendance: {str(e)}")
        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            "Произошла ошибка при отмене участия. Пожалуйста, попробуйте позже.",
            reply_markup=back_to_main_menu_keyboard()
        )

@bot.callback_query_handler(func=lambda call: call.data == "private_events")
def show_private_channels(call: CallbackQuery):
    try:
        # Delete the current message
        safe_delete_last_message(call.message.chat.id, call.from_user.id)
        channels = TelegramChannel.objects.all()
        if not channels:
            send_and_store_message(
                call.message.chat.id,
                call.from_user.id,
                "Нет доступных приватных каналов.",
                reply_markup=back_to_main_menu_keyboard()
            )
            return

        send_and_store_message(
            call.message.chat.id,
            call.from_user.id,
            "Выберите приватный канал:",
            reply_markup=private_channels_keyboard(channels)
        )
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("private_channel_"))
def show_private_channel_events(call: CallbackQuery):
    try:
        channel_id = call.data.replace("private_channel_", "")
        channel = TelegramChannel.objects.get(id=channel_id)
        
        # Get user's attending events
        user = User.objects.get(telegram_id=str(call.from_user.id))
        attending_events = set(Event.objects.filter(
            attendance__user=user,
            attendance__status="going"
        ).values_list('id', flat=True))
        
        # Get events for this channel, excluding those user is already attending
        events = Event.objects.filter(
            channel=channel,
            is_private=True,
            date_time__gte=datetime.now()
        ).exclude(id__in=attending_events).order_by("date_time")
        
        if not events:
            send_and_store_message(call.message.chat.id, call.from_user.id, f"В канале {channel.name} пока нет доступных мероприятий.", reply_markup=back_to_main_menu_keyboard())
            return
            
        # Группируем мероприятия по типам
        event_types = {}
        for event in events:
            if event.event_type not in event_types:
                event_types[event.event_type] = []
            event_types[event.event_type].append(event)
        
        # Создаем клавиатуру с типами мероприятий
        markup = InlineKeyboardMarkup()
        for event_type in event_types.keys():
            display = dict(Event.EVENT_TYPE_CHOICES).get(event_type, event_type)
            markup.add(InlineKeyboardButton(f"{display}", callback_data=f"private_type_{channel_id}_{event_type}"))
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
        
        # Save channel_id in state
        state = get_user_state(call.from_user.id) or {}
        state["private_channel_id"] = channel_id
        state["private_events"] = {et: list(evs) for et, evs in event_types.items()}
        update_user_state(call.from_user.id, state)
        
        send_and_store_message(call.message.chat.id, call.from_user.id, f"Выбери тип мероприятия в канале {channel.name}:", reply_markup=markup)
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.data)

@bot.callback_query_handler(func=lambda call: call.data.startswith("private_type_"))
def show_private_type_categories(call: CallbackQuery):
    try:
        _, _, channel_id, event_type = call.data.split("_")
        channel = TelegramChannel.objects.get(id=channel_id)
        
        # Get state
        state = get_user_state(call.from_user.id)
        if not state or "private_events" not in state:
            send_and_store_message(call.message.chat.id, call.from_user.id, "Произошла ошибка. Пожалуйста, начните сначала.", reply_markup=main_menu_keyboard())
            return
            
        # Get events for this type
        events = state["private_events"].get(event_type, [])
        
        if not events:
            send_and_store_message(call.message.chat.id, call.from_user.id, f"В канале {channel.name} нет мероприятий типа {dict(Event.EVENT_TYPE_CHOICES).get(event_type, event_type)}.", reply_markup=back_to_main_menu_keyboard())
            return
            
        # Группируем мероприятия по категориям
        categories = {}
        for event in events:
            if event.category not in categories:
                categories[event.category] = []
            categories[event.category].append(event)
        
        # Создаем клавиатуру с категориями
        markup = InlineKeyboardMarkup()
        for category in categories.keys():
            display = dict(Event.CATEGORY_CHOICES).get(category, category)
            markup.add(InlineKeyboardButton(f"{display}", callback_data=f"private_cat_{channel_id}_{event_type}_{category}"))
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data=f"private_channel_{channel_id}"))
        
        # Update state
        state["private_type"] = event_type
        state["private_events_by_category"] = {cat: list(evs) for cat, evs in categories.items()}
        update_user_state(call.from_user.id, state)
        
        send_and_store_message(call.message.chat.id, call.from_user.id, f"Выбери категорию мероприятий:", reply_markup=markup)
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.data)

@bot.callback_query_handler(func=lambda call: call.data.startswith("private_cat_"))
def show_private_category_events(call: CallbackQuery):
    try:
        _, _, channel_id, event_type, category = call.data.split("_")
        channel = TelegramChannel.objects.get(id=channel_id)
        
        # Get state
        state = get_user_state(call.from_user.id)
        if not state or "private_events_by_category" not in state:
            send_and_store_message(call.message.chat.id, call.from_user.id, "Произошла ошибка. Пожалуйста, начните сначала.", reply_markup=main_menu_keyboard())
            return
            
        # Get events for this category
        events = state["private_events_by_category"].get(category, [])
        
        if not events:
            send_and_store_message(call.message.chat.id, call.from_user.id, f"В канале {channel.name} нет мероприятий категории {dict(Event.CATEGORY_CHOICES).get(category, category)}.", reply_markup=back_to_main_menu_keyboard())
            return
            
        # Save events in state
        state["events"] = events
        state["is_private"] = True
        update_user_state(call.from_user.id, state)
        
        # Format events list
        text = f"Мероприятия канала {channel.name} ({dict(Event.EVENT_TYPE_CHOICES).get(event_type, event_type)}, {dict(Event.CATEGORY_CHOICES).get(category, category)}):\n\n"
        for i, event in enumerate(events, 1):
            weekday = calendar.day_name[event.date_time.weekday()]
            ru_day = {'Saturday': 'Сб', 'Sunday': 'Вс'}.get(weekday, '')
            date_str = event.date_time.strftime('%d.%m (%H:%M)')
            date_str += f" <b>{ru_day}</b>" if ru_day else ''
            text += f"{i}. {date_str} - {event.name}\n"
        text += "\nНапиши номер мероприятия, чтобы получить подробности."
        
        send_and_store_message(call.message.chat.id, call.from_user.id, text, reply_markup=back_to_main_menu_keyboard())
    except Exception as e:
        handle_error(call.message.chat.id, str(e), call.data)

def RunBot():
    try:
        logger.info("Запуск бота!")
        cleanup_thread = start_cleanup_thread()
        bot.polling(none_stop=True, interval=0)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise e
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную!")
    finally:
        logger.info("Завершение работы бота!")


