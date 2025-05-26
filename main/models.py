from django.db import models

class User(models.Model):
    telegram_id = models.CharField(max_length=100, unique=True)
    username = models.CharField(max_length=100, blank=True, null=True)
    is_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.username or self.telegram_id


class TelegramChannel(models.Model):
    channel_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Event(models.Model):
    EVENT_TYPE_CHOICES = [
        ('online', 'Онлайн'),
        ('offline', 'Офлайн'),
        ('hybrid', 'Гибрид')
    ]

    CATEGORY_CHOICES = [
        ('concert', 'Концерт'),
        ('meeting', 'Встреча'),
        ('marathon', 'Марафон'),
        ('training', 'Тренинг')
    ]

    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    date_time = models.DateTimeField()
    details = models.TextField(blank=True, null=True)
    link_2gis = models.URLField(blank=True, null=True)
    # ticket_link = models.URLField(blank=True, null=True)  # Ссылка для покупки билета
    created_at = models.DateTimeField(auto_now_add=True)
    is_private = models.BooleanField(default=False)
    channel = models.ForeignKey(TelegramChannel, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return self.name


class Attendance(models.Model):
    STATUS_CHOICES = [
        ('going', 'Иду'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'event')

    def __str__(self):
        return f"{self.user.username or self.user.telegram_id} - {self.event.name} ({self.status})"
