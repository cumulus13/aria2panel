from django import forms

from . import conf
from .models import Aria2Server


class BootstrapMixin:
    def _style(self):
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs["class"] = "form-check-input"
            elif isinstance(w, forms.Select):
                w.attrs["class"] = "form-select"
            else:
                w.attrs["class"] = "form-control"


class ServerForm(BootstrapMixin, forms.ModelForm):
    secret = forms.CharField(
        label="RPC secret",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Leave empty to keep the stored secret. Stored encrypted.",
    )
    clear_secret = forms.BooleanField(label="Remove stored secret", required=False)

    class Meta:
        model = Aria2Server
        fields = [
            "name", "rpc_url", "verify_tls", "enabled", "is_default",
            "scheduler_enabled", "max_downloads", "enforce_in_aria2", "default_download_limit", "default_upload_limit",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(["name", "rpc_url", "secret", "clear_secret"])
        self._style()

    def save(self, commit=True):
        server = super().save(commit=False)
        if self.cleaned_data.get("clear_secret"):
            server.secret = ""
        elif self.cleaned_data.get("secret"):
            server.secret = self.cleaned_data["secret"]
        if commit:
            server.save()
        return server


class SettingsForm(BootstrapMixin, forms.Form):
    """Built from conf.DEFINITIONS so a new setting only needs an entry there."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.keys = {}
        for item in conf.describe():
            key, opts = item["key"], item["options"]
            name = key.replace(".", "__")
            label = key
            kw = {"label": label, "help_text": item["help"], "required": False, "initial": item["value"]}
            typ = item["type"]
            if "choices" in opts:
                field = forms.ChoiceField(choices=[(c, c) for c in opts["choices"]], **{**kw, "required": True})
            elif typ == "bool":
                field = forms.BooleanField(**kw)
            elif typ == "int":
                field = forms.IntegerField(min_value=opts.get("min"), max_value=opts.get("max"), **{**kw, "required": True})
            elif typ == "float":
                field = forms.FloatField(min_value=opts.get("min"), max_value=opts.get("max"), **{**kw, "required": True})
            elif typ == "list":
                field = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), **{**kw, "initial": "\n".join(item["value"])})
            else:
                field = forms.CharField(**{**kw, "required": True})
            self.fields[name] = field
            self.keys[name] = key
        self._style()

    def save(self):
        for name, key in self.keys.items():
            conf.set(key, self.cleaned_data[name])
