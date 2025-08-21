import json
import os
from PyQt5.QtCore import QObject, pyqtSignal, QLocale, QFileSystemWatcher

class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"

class I18n(QObject):
    changed = pyqtSignal()

    def __init__(self, i18n_dir="i18n", code="auto", fallback="en-US", debug_missing=True):
        super().__init__()
        self.i18n_dir = i18n_dir
        self.fallback = fallback
        self.debug_missing = debug_missing
        self._data = {}
        self._available = []
        self._watcher = QFileSystemWatcher()

        if os.path.isdir(i18n_dir):
            self._watcher.addPath(i18n_dir)
        self._watcher.directoryChanged.connect(self._refresh_available)

        self._refresh_available()
        self.set_locale(code)

    def _norm_code(self, c):
        return c.replace("_", "-")

    def _sys_code(self):
        return self._norm_code(QLocale.system().name())

    def _load_file(self, code):
        path = os.path.join(self.i18n_dir, f"{code}.json")
        if not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _refresh_available(self):
        self._available = []
        if not os.path.isdir(self.i18n_dir):
            return
        for fn in os.listdir(self.i18n_dir):
            if fn.endswith(".json"):
                try:
                    data = self._load_file(fn[:-5])
                    meta = data.get("meta", {})
                    code = meta.get("code") or fn[:-5]
                    name = meta.get("name") or code
                    self._available.append((code, name))
                except Exception:
                    pass
        self._available = sorted(set(self._available), key=lambda x: x[0])

    def available_locales(self):
        return list(self._available)

    def set_locale(self, code):
        code = self._sys_code() if code == "auto" else self._norm_code(code)
        base = self._load_file(self.fallback)
        tgt = self._load_file(code)
        merged = {k: v for k, v in base.items() if k != "meta"}
        merged.update({k: v for k, v in tgt.items() if k != "meta"})
        self._data = merged
        self.changed.emit()

    def t(self, key, **kwargs):
        if "count" in kwargs:
            count = kwargs["count"]
            base_key = key + (".one" if count == 1 else ".other")
            if base_key in self._data:
                return self._data[base_key].format_map(_SafeDict(kwargs))
        text = self._data.get(key)
        if text is None:
            return key if self.debug_missing else ""
        return text.format_map(_SafeDict(kwargs))

class UiTextBinder(QObject):
    def __init__(self, i18n: I18n):
        super().__init__()
        self.i18n = i18n
        self.bindings = []
        self.i18n.changed.connect(self.retranslate)

    def bind(self, obj, method_name, key, kwargs=None):
        method = getattr(obj, method_name)
        self.bindings.append((method, key, kwargs or {}))
        method(self.i18n.t(key, **(kwargs or {})))

    def retranslate(self):
        for method, key, kwargs in self.bindings:
            method(self.i18n.t(key, **kwargs))