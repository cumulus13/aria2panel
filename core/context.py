from . import conf

ASSET_VERSION = "1"


def panel(request):
    return {
        "site_name": conf.get("app.site_name"),
        "default_theme": conf.get("ui.default_theme"),
        "asset_version": ASSET_VERSION,
    }
