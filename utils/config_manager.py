import json, os, sys, tempfile
from copy import deepcopy

APP_NAME = "Duplicate Photo Finder"
LATEST_VERSION = 1

DEFAULTS_COMMON = {
    "config_version": LATEST_VERSION,
    "ui": {
        #"theme": "system",              # system | light | dark
        "lang": "zh-TW",
        "overview_thumbnail": {"max_size": 240, "quality": "high"},
        "thumbnail": {"max_size": 400, "quality": "high"},
        "font_size": 12,
        "last_browser_path": "",
        "browser_view_style_key": "medium",
        "browser_sort_key": "name",
        "browser_order_asc": True,
        "show_processing_image": False,
        "show_original_groups": False
    },
    "behavior": {
        "auto_next_group": True,
        "display_same_images": True,
        "confirm_delete": True,
        "compare_file_size": True,
        "similarity_tolerance": 5,
        #"delete_to_trash": True,
        #"skip_already_decided": True,
        "locale_override_from_os": True
    },
    "performance": {"max_workers": 4, "heif_enabled": True, "raw_decode_policy": "fast"},
    "compare": {"hash": "phash", "distance_threshold": 12, "early_stop": True},
    "shortcuts": {
        "toggle_1":"1","toggle_2":"2","toggle_3":"3","toggle_all":"0",
        "next_group":"Right","prev_group":"Left","delete_selected":"Del"
    },
    "platform_overrides": {"darwin": {}, "win32": {}, "linux": {}}
}

DEFAULTS_PLATFORM = {
    "darwin": {"performance": {"max_workers": 6}},
    "win32":  {"shortcuts": {"delete_selected": "Delete"}},
    "linux":  {"behavior": {"delete_to_trash": False}}
}

def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = deepcopy(v)
    return dst

def _ensure_dir(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)

def _default_config_path():
    # Provide the configuration file path based on platform.
    if sys.platform == "darwin":  # macOS
        base_dir = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32": # windows
        base_dir = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:  # Linux / others
        base_dir = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))

    return os.path.join(base_dir, APP_NAME, "config.json")

class Config:
    # Cross-platform config manager with atomic writes.
    def __init__(self, path=None):
        self.path = path or _default_config_path()
        self._cfg = self._load()

    # ---------- lifecycle ----------
    def _load(self):
        cfg = deepcopy(DEFAULTS_COMMON)
        plat = sys.platform
        if plat in DEFAULTS_PLATFORM:
            _deep_merge(cfg, DEFAULTS_PLATFORM[plat])

        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)
                _deep_merge(cfg, user_cfg)
            except Exception:
                pass 

        po = cfg.get("platform_overrides", {}).get(plat)
        if isinstance(po, dict):
            _deep_merge(cfg, po)

        if cfg.get("config_version", 0) < LATEST_VERSION:
            cfg = self._migrate(cfg)
            self._atomic_save(cfg)
        return cfg

    def _migrate(self, cfg: dict) -> dict:
        ver = cfg.get("config_version", 0)
        while ver < LATEST_VERSION:
            if ver == 0:
                cfg.setdefault("ui", {}).setdefault("thumbnail", {"max_size": 220, "quality": "high"})
            ver += 1
        cfg["config_version"] = LATEST_VERSION
        return cfg

    def _atomic_save(self, cfg: dict):
        _ensure_dir(self.path)
        fd, tmp_path = tempfile.mkstemp(prefix="cfg_", suffix=".json", dir=os.path.dirname(self.path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            if os.path.exists(self.path):
                try:
                    os.replace(self.path, self.path + ".bak")
                except Exception:
                    pass
            os.replace(tmp_path, self.path)
        finally:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass

    # ---------- public API ----------
    def get(self, key_path: str, default=None):
        cur = self._cfg
        for part in key_path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, key_path: str, value, autosave=True):
        parts = key_path.split(".")
        cur = self._cfg
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value
        if autosave:
            self._atomic_save(self._cfg)

    def data(self):
        return deepcopy(self._cfg)

    def save(self):
        self._atomic_save(self._cfg)