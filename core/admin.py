from django.contrib import admin

from .models import Aria2Server, ApiToken, DownloadEvent, QueueItem, Setting, SpeedRule


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


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "prefix", "read_only", "created_at", "last_used_at", "expires_at", "revoked_at")
    list_filter = ("read_only",)
    readonly_fields = ("prefix", "token_hash", "created_at", "last_used_at")


@admin.register(DownloadEvent)
class DownloadEventAdmin(admin.ModelAdmin):
    list_display = ("ts", "server", "kind", "name", "gid")
    list_filter = ("kind", "server")
