from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from main.models import Event

def main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🌐 Онлайн", callback_data="event_type_online"),
        InlineKeyboardButton("🏙 Оффлайн", callback_data="event_type_offline"),
        InlineKeyboardButton("🔀 Гибрид", callback_data="event_type_hybrid")
    )
    markup.row(
        InlineKeyboardButton("📋 Мои мероприятия", callback_data="my_events"),
        InlineKeyboardButton("🔒 Приватные", callback_data="private_events")
    )
    return markup

def category_keyboard(event_type):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎶 Концерты", callback_data=f"category_{event_type}_concert"),
        InlineKeyboardButton("💬 Встречи", callback_data=f"category_{event_type}_meeting")
    )
    markup.row(
        InlineKeyboardButton("🏃 Марафоны", callback_data=f"category_{event_type}_marathon"),
        InlineKeyboardButton("📚 Тренинги", callback_data=f"category_{event_type}_training")
    )
    return markup

def attendance_keyboard(event_id):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Иду", callback_data=f"going_{event_id}")
    )
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return markup

def back_to_main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return markup

def my_events_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📋 Мои мероприятия", callback_data="my_events"))
    return markup

def my_events_category_keyboard(categories):
    markup = InlineKeyboardMarkup()
    for category in categories:
        display = dict(Event.CATEGORY_CHOICES).get(category, category)
        markup.add(InlineKeyboardButton(f"{display}", callback_data=f"my_cat_{category}"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return markup

def my_event_actions_keyboard(event_id):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎫 Купить билет", callback_data=f"buy_ticket_{event_id}"),
        InlineKeyboardButton("❌ Отменить участие", callback_data=f"cancel_attendance_{event_id}")
    )
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="my_events"))
    return markup

def private_channels_keyboard(channels):
    markup = InlineKeyboardMarkup()
    for channel in channels:
        markup.add(InlineKeyboardButton(f"{channel.name}", callback_data=f"private_channel_{channel.id}"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return markup


