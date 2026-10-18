from django.contrib import admin

from .models import Aria2Server, QueueItem, Setting, SpeedRule


@admin.register(Aria2Server)
class Aria2ServerAdmin(admin.ModelAdmin):
    list_display = ("name", "rpc_url", "enabled", "is_default", "scheduler_enabled", "max_downloads")
    exclude = ("secret_enc",)


@admin.register(QueueItem)
class QueueItemAdmin(admin.ModelAdmin):
    list_display = ("name", "server", "kind", "status", "position", "not_before", "created_at")
    list_filter = ("status", "kind", "server")
    search_fields = ("name", "gid")


@admin.register(SpeedRule)
class SpeedRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "server", "start_time", "end_time", "download_limit", "upload_limit", "enabled")


@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "updated_at")
