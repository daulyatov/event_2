from django.contrib import admin
from .models import User, Event, Attendance, TelegramChannel
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from main.bot_handlers import invalidate_event_cache
import csv
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import path
from django.contrib import messages
from django.utils import timezone
from datetime import datetime


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('telegram_id', 'username', 'is_admin', 'created_at')
    search_fields = ('telegram_id', 'username')
    list_filter = ('is_admin', 'created_at')


@receiver(post_save, sender=Event)
def invalidate_event_cache_on_save(sender, instance, **kwargs):
    invalidate_event_cache(instance.event_type, instance.category)

@receiver(post_delete, sender=Event)
def invalidate_event_cache_on_delete(sender, instance, **kwargs):
    invalidate_event_cache(instance.event_type, instance.category)

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('name', 'event_type', 'category', 'date_time', 'location', 'is_private', 'channel')
    search_fields = ('name', 'location', 'address')
    list_filter = ('event_type', 'category', 'is_private', 'channel')
    date_hierarchy = 'date_time'
    change_list_template = 'admin/events_change_list.html'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import-csv/', self.import_csv, name='import-csv'),
        ]
        return custom_urls + urls

    def import_csv(self, request):
        if request.method == "POST":
            csv_file = request.FILES["csv_file"]
            if not csv_file.name.endswith('.csv'):
                messages.error(request, "File is not CSV type")
                return HttpResponseRedirect("..")
            
            try:
                csv_data = csv.reader(csv_file.read().decode('utf-8').splitlines())
                next(csv_data)  # Skip header row
                
                for row in csv_data:
                    if len(row) >= 9:  # Ensure we have all required fields
                        try:
                            date_time = datetime.strptime(row[5], '%Y-%m-%d %H:%M:%S')
                            is_private = row[8].lower() == 'true' if len(row) > 8 else False
                            
                            # Get channel if event is private and channel name is provided
                            channel = None
                            if is_private and len(row) > 9 and row[9].strip():
                                try:
                                    channel = TelegramChannel.objects.get(name=row[9].strip())
                                except TelegramChannel.DoesNotExist:
                                    messages.warning(request, f"Channel '{row[9]}' not found for event '{row[0]}'. Event will be created without channel.")
                            
                            Event.objects.create(
                                name=row[0],
                                location=row[1],
                                address=row[2],
                                event_type=row[3],
                                category=row[4],
                                date_time=date_time,
                                details=row[6] if len(row) > 6 else None,
                                link_2gis=row[7] if len(row) > 7 else None,
                                is_private=is_private,
                                channel=channel
                            )
                        except Exception as e:
                            messages.error(request, f"Error importing row: {row}. Error: {str(e)}")
                            continue
                
                messages.success(request, "Events imported successfully")
            except Exception as e:
                messages.error(request, f"Error processing CSV file: {str(e)}")
            
            return HttpResponseRedirect("..")
        
        return render(request, "admin/csv_import.html")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'status', 'created_at')
    search_fields = ('user__telegram_id', 'user__username', 'event__name')
    list_filter = ('status', 'created_at')


@admin.register(TelegramChannel)
class TelegramChannelAdmin(admin.ModelAdmin):
    list_display = ('name', 'channel_id', 'created_at')
    search_fields = ('name', 'channel_id')
