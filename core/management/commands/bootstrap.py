import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.models import Aria2Server


class Command(BaseCommand):
    help = (
        "Idempotent first-run setup from the environment: creates the admin user "
        "(DJANGO_SUPERUSER_USERNAME/PASSWORD/EMAIL) and a default aria2 server (ARIA2_RPC_URL/ARIA2_RPC_SECRET)."
    )

    def handle(self, *args, **options):
        User = get_user_model()
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        if username and password and not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser(username, os.environ.get("DJANGO_SUPERUSER_EMAIL", ""), password)
            self.stdout.write("created admin user %s" % username)
        url = os.environ.get("ARIA2_RPC_URL")
        if url and not Aria2Server.objects.exists():
            server = Aria2Server(name=os.environ.get("ARIA2_NAME", "default"), rpc_url=url, is_default=True)
            server.secret = os.environ.get("ARIA2_RPC_SECRET", "")
            server.save()
            self.stdout.write("created aria2 server %s" % server.name)
