import hashlib
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone

from . import crypto

SPEED_VALIDATOR = RegexValidator(r"^\d+[KkMm]?$", "Use a number with an optional K or M suffix, e.g. 500K, 2M, 0 = unlimited.")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def default_days():
    return list(range(7))


def validate_rpc_url(value):
    if not re.match(r"^https?://[^\s/]+(/\S*)?$", value):
        raise ValidationError("Enter an http:// or https:// URL, e.g. http://aria2:6800/jsonrpc")


class Setting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.key


class Aria2Server(models.Model):
    name = models.CharField(max_length=100, unique=True)
    rpc_url = models.CharField(max_length=300, default="http://127.0.0.1:6800/jsonrpc", validators=[validate_rpc_url])
    secret_enc = models.TextField(blank=True, default="", editable=False)
    verify_tls = models.BooleanField("Verify TLS certificate", default=True)
    enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    scheduler_enabled = models.BooleanField("Scheduler enabled", default=True)
    max_downloads = models.PositiveIntegerField(
        "Max simultaneous downloads", default=3, validators=[MinValueValidator(1), MaxValueValidator(500)]
    )
    enforce_in_aria2 = models.BooleanField(
        "Also enforce the limit inside aria2", default=True,
        help_text="Keeps aria2's own max-concurrent-downloads in step with the scheduler, so resuming a paused download "
                  "above the limit waits for a free slot instead of starting. Restored when the scheduler is switched off.",
    )
    default_download_limit = models.CharField("Default download limit", max_length=20, default="0", validators=[SPEED_VALIDATOR])
    default_upload_limit = models.CharField("Default upload limit", max_length=20, default="0", validators=[SPEED_VALIDATOR])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return self.name

    @property
    def secret(self):
        return crypto.decrypt(self.secret_enc)

    @secret.setter
    def secret(self, value):
        self.secret_enc = crypto.encrypt(value)

    @property
    def has_secret(self):
        return bool(self.secret_enc)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.is_default:
                Aria2Server.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)

    def client(self):
        from . import conf
        from .aria2 import Aria2Client

        return Aria2Client(self.rpc_url, self.secret, timeout=conf.get("rpc.timeout_seconds"), verify=self.verify_tls)


class QueueItem(models.Model):
    class Kind(models.TextChoices):
        URI = "uri", "Links"
        TORRENT = "torrent", "Torrent"
        METALINK = "metalink", "Metalink"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        DISPATCHED = "dispatched", "Dispatched"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    server = models.ForeignKey(Aria2Server, on_delete=models.CASCADE, related_name="queue")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.URI)
    name = models.CharField(max_length=500, blank=True)
    uris = models.JSONField(default=list, blank=True)
    payload = models.TextField(blank=True, default="")  # base64 torrent / metalink
    options = models.JSONField(default=dict, blank=True)
    position = models.BigIntegerField(default=0)
    not_before = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    gid = models.CharField(max_length=200, blank=True, default="")
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        indexes = [models.Index(fields=["server", "status", "position"])]

    def __str__(self):
        return "%s (%s)" % (self.name or self.kind, self.status)

    def save(self, *args, **kwargs):
        if self._state.adding and not self.position:
            top = QueueItem.objects.filter(server=self.server).aggregate(m=Max("position"))["m"] or 0
            self.position = top + 1
        super().save(*args, **kwargs)


class SpeedSample(models.Model):
    """Global transfer speed sampled by the scheduler daemon, feeds the dashboard graph so it
    stays continuous across pages, reloads and devices."""

    server = models.ForeignKey(Aria2Server, on_delete=models.CASCADE, related_name="speed_samples")
    ts = models.DateTimeField(default=timezone.now)
    download = models.BigIntegerField(default=0)
    upload = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["ts"]
        indexes = [models.Index(fields=["server", "ts"])]


class ApiToken(models.Model):
    """Bearer token for API clients (mobile app, scripts). Only a SHA-256 hash is stored,
    the plain token is shown once when it is created."""

    PREFIX = "a2p_"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=16, editable=False)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    read_only = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return "%s (%s…)" % (self.name, self.prefix)

    @staticmethod
    def digest(plain):
        return hashlib.sha256(plain.encode()).hexdigest()

    @classmethod
    def issue(cls, user, name, read_only=False, expires_at=None):
        plain = cls.PREFIX + secrets.token_urlsafe(32)
        token = cls.objects.create(
            user=user, name=(name or "token")[:100], prefix=plain[:10], token_hash=cls.digest(plain),
            read_only=read_only, expires_at=expires_at,
        )
        return token, plain

    @property
    def is_valid(self):
        if self.revoked_at is not None or not self.user.is_active:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    @classmethod
    def authenticate(cls, plain):
        if not plain or not plain.startswith(cls.PREFIX):
            return None
        token = cls.objects.select_related("user").filter(token_hash=cls.digest(plain)).first()
        if token is None or not token.is_valid:
            return None
        now = timezone.now()
        if token.last_used_at is None or now - token.last_used_at > timedelta(minutes=1):
            cls.objects.filter(pk=token.pk).update(last_used_at=now)
        return token


class DownloadEvent(models.Model):
    """Things that happened to downloads, recorded by the scheduler daemon. Clients replay
    them after a reconnect (SSE Last-Event-ID) so a notification is not lost while offline."""

    class Kind(models.TextChoices):
        ADDED = "added", "Added"
        COMPLETED = "completed", "Completed"
        ERROR = "error", "Error"
        REMOVED = "removed", "Removed"

    server = models.ForeignKey(Aria2Server, on_delete=models.CASCADE, related_name="events")
    ts = models.DateTimeField(default=timezone.now)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    gid = models.CharField(max_length=32)
    name = models.CharField(max_length=500, blank=True)
    total = models.BigIntegerField(default=0)
    error_code = models.CharField(max_length=10, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["server", "id"])]

    def as_dict(self):
        return {
            "id": self.pk, "kind": self.kind, "gid": self.gid, "name": self.name, "total": self.total,
            "errorCode": self.error_code, "errorMessage": self.error_message, "ts": int(self.ts.timestamp() * 1000),
        }


class SpeedRule(models.Model):
    """Bandwidth limits applied while the rule's time window is active."""

    server = models.ForeignKey(Aria2Server, on_delete=models.CASCADE, related_name="speed_rules")
    name = models.CharField(max_length=100)
    days = models.JSONField(default=default_days)  # 0 = Monday
    start_time = models.TimeField()
    end_time = models.TimeField()
    download_limit = models.CharField(max_length=20, default="0", validators=[SPEED_VALIDATOR])
    upload_limit = models.CharField(max_length=20, default="0", validators=[SPEED_VALIDATOR])
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["start_time", "id"]

    def __str__(self):
        return self.name

    def matches(self, local_dt):
        """local_dt: aware datetime in the server time zone. Windows may span midnight."""
        t = local_dt.time()
        wd = local_dt.weekday()
        if self.start_time == self.end_time:
            return wd in self.days  # whole day
        if self.start_time < self.end_time:
            return self.start_time <= t < self.end_time and wd in self.days
        # overnight: the window belongs to the day it starts on
        if t >= self.start_time:
            return wd in self.days
        if t < self.end_time:
            return ((wd - 1) % 7) in self.days
        return False
