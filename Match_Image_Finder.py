import sys, os, json, time, html, platform, queue, hashlib, rawpy, io
from tkinter import constants
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
from PyQt5.QtCore import Qt, QTimer, QSettings, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAction, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QScrollArea, QCheckBox, QSizePolicy,
    QMessageBox, QProgressBar, QSlider, QDialog, QDialogButtonBox, QShortcut,
    QLineEdit
)
from PyQt5.QtGui import QPixmap, QImage, QIcon, QFont, QKeySequence, QPainter, QColor
from PIL import Image, ImageOps, ImageFile
from PIL.Image import Resampling
import numpy as np
from pillow_heif import register_heif_opener
from build_info import VERSION, BUILD_TIME
from datetime import datetime, timedelta
from utils.config_manager import Config
from utils.settings_dialog import SettingsDialog
from utils.i18n import I18n, UiTextBinder
from utils.common import resource_path
from utils.constraints_store import ConstraintsStore









ImageFile.LOAD_TRUNCATED_IMAGES = True
EXCEPTIONS_FILE = ".exceptions.json"
FILELIST_FILE = ".filelist.json"
PROGRESS_FILE = ".progress.json"

register_heif_opener()

# Configure worker number
MAX_WORKERS = 4

# Supported image format
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".raf", ".orf", ".dng", ".rw2", ".heic")
RAW_EXTS = {".nef", ".nrw", ".cr2", ".cr3", ".arw", ".raf", ".rw2", ".orf", ".dng"}

# Try to use imagehash; if it fails, use the backup method
try:
    import imagehash
    IMAGEHASH_AVAILABLE = True
except ImportError:
    IMAGEHASH_AVAILABLE = False

def excepthook(exc_type, exc_value, exc_tb):
    print("[Error] Uncaught exception:", exc_type, exc_value)
    traceback.print_tb(exc_tb)

sys.excepthook = excepthook

def phash(path):
    try:
        img = Image.open(path)
        if IMAGEHASH_AVAILABLE:
            return int(str(imagehash.phash(img)), 16)  # Transform to int
        else:
            # Alternate method: Using simple hash method
            img = img.convert('RGB')
            img = img.resize((64, 64))
            pixels = list(img.getdata())
            
            # Calculate average color for hash
            avg_r = sum(p[0] for p in pixels) // len(pixels)
            avg_g = sum(p[1] for p in pixels) // len(pixels)
            avg_b = sum(p[2] for p in pixels) // len(pixels)
            return hash((avg_r, avg_g, avg_b)) & 0xFFFFFFFF
    except Exception as e:
        print(f"[Error] hashing {path}: {e}")
        # Return default value instead of error
        return 0

def compute_hash(path):
    return phash(path)

def build_highlight_html(common_prefix, rel_path):
    return (
        f"<span style='color:gray'>{html.escape(common_prefix)}</span>"
        f"<span style='color:red; font-weight:bold; font-size:116%'>{html.escape(rel_path)}</span>"
    )

def gen_group_sort_key(grp):
    folders = sorted(
        os.path.dirname(p).replace("\\", "/").lower()
        for p in grp
    )
    return "|".join(folders)

def image_pil_to_qimage(pil_img):
    if pil_img.mode not in ("RGB", "RGBA"):
        pil_img = pil_img.convert("RGBA")
    else:
        # Use RGBA，prevent some platform RGB888 stride
        if pil_img.mode == "RGB":
            pil_img = pil_img.convert("RGBA")

    data = pil_img.tobytes("raw", "RGBA")
    w, h = pil_img.size
    bytes_per_line = 4 * w

    qimg = QImage(data, w, h, bytes_per_line, QImage.Format_RGBA8888)
    # Prevent QImage point to invalid memory after free original data
    qimg = qimg.copy()
    return qimg

def image_load_for_thumb(path, want_min_edge=1400):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in RAW_EXTS:
            with rawpy.imread(path) as raw:
                try:
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        img = Image.open(io.BytesIO(thumb.data))
                        img.load()
                    else:
                        img = Image.fromarray(thumb.data)
                except rawpy.LibRawNoThumbnailError as e:
                    rgb = raw.postprocess(
                        use_camera_wb=True,
                        no_auto_bright=True,
                        half_size=True,
                        gamma=(1, 1)
                    )
                    img = Image.fromarray(rgb)
        else:
            img = Image.open(path)
    except Exception as e:
        img = Image.open(path)

    img = ImageOps.exif_transpose(img)

    w, h = img.size
    scale = min(want_min_edge / max(w, h), 1.0)
    if scale < 1.0:
        img = img.resize((max(1, int(w*scale)), max(1, int(h*scale))), Resampling.LANCZOS)

    return img

def math_clamp(x, min_val, max_val):
    return max(min_val, min(x, max_val))

class ImageDialog(QDialog):
    def __init__(self, image_path):
        super().__init__()
        self.setWindowTitle(os.path.basename(image_path))
        self.resize(1000, 800)

        self.image_path = image_path
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.image_label)
        layout.addWidget(scroll)

        self.update_scaled_pixmap()

        info = QLabel(image_path)
        info.setWordWrap(True)
        layout.addWidget(info)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scaled_pixmap()

    def update_scaled_pixmap(self):
        try:
            img = ImageOps.exif_transpose(Image.open(self.image_path))  # Fix rotation issue
            img.thumbnail((self.width() - 40, self.height() - 120))     # Fit windows size
            qimg = image_pil_to_qimage(img)
            pixmap = QPixmap.fromImage(qimg)
        except Exception as e:
            print(f"[Error] Failed to display large image] {self.image_path}: {e}")
            pixmap = QPixmap()  # Avoid flash when no image

        self.image_label.setPixmap(pixmap)

class MatchImageFinder(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Match Image Finder v{VERSION}")
        # ---------- 1) Init i18n (Should be set before any binding) ----------
        self.settings = QSettings("YourOrg", "MatchImageFinder")
        locale = self.settings.value("locale", "auto")
        i18n_dir = resource_path("i18n")
        self.i18n = I18n(i18n_dir=i18n_dir, code=locale, fallback="en-US")
        self.i18n_binder = UiTextBinder(self.i18n)

        # ---------- 2) Build UI (Don't set text here; Set text by i18n) ----------
        self.resize(1000, 700)
        main = QWidget()
        self.setCentralWidget(main)
        layout = QVBoxLayout(main)

        ctl_top = QHBoxLayout()
        ctl_mid = QHBoxLayout()
        ctl_bottom = QHBoxLayout()
        self.fontsize = 12

        self.open_btn = QPushButton()
        self.open_btn.clicked.connect(self.btn_action_select_folder)
        self.open_btn.setEnabled(True)

        self.path_str = QLabel()
        self.exclude_str = QLabel()

        self.scan_btn = QPushButton()
        self.scan_btn.clicked.connect(self.btn_action_scan)
        self.scan_btn.setEnabled(False)

        self.pause_btn = QPushButton()
        self.pause_btn.clicked.connect(self.btn_action_pause_processing)
        self.pause_btn.setEnabled(False)
        
        self.continue_btn = QPushButton()
        self.continue_btn.clicked.connect(self.btn_action_continue_processing)
        self.continue_btn.setEnabled(False)
        
        self.exit_btn = QPushButton()
        self.exit_btn.clicked.connect(self.btn_action_exit_and_save)

        self.delete_btn = QPushButton()
        self.delete_btn.clicked.connect(self.btn_action_delete_unchecked)
        self.delete_btn.setEnabled(False)

        self.first_btn = QPushButton()
        self.first_btn.clicked.connect(self.btn_action_first_group)
        self.first_btn.setEnabled(False)

        self.prev_folder_btn = QPushButton()
        self.prev_folder_btn.clicked.connect(self.btn_action_prev_compare_folder)
        self.prev_folder_btn.setEnabled(False)

        self.prev_btn = QPushButton()
        self.prev_btn.clicked.connect(self.btn_action_prev_group)
        self.prev_btn.setEnabled(False)

        self.next_btn = QPushButton()
        self.next_btn.clicked.connect(self.btn_action_next_group_or_compare)
        self.next_btn.setEnabled(False)

        self.next_folder_btn = QPushButton()
        self.next_folder_btn.clicked.connect(self.btn_action_next_compare_folder)
        self.next_folder_btn.setEnabled(False)

        self.last_btn = QPushButton()
        self.last_btn.clicked.connect(self.btn_action_last_group)
        self.last_btn.setEnabled(False)

        self.auto_next_cb = QCheckBox()
        self.auto_next_cb.setChecked(False)     

        self.display_img_cb = QCheckBox()
        self.display_img_cb.setChecked(False)

        self.display_original_groups_cb = QCheckBox(self.i18n.t("cb.display_original_groups"))
        self.display_original_groups_cb.setChecked(False)

        self.exclude_input = QLineEdit()
        self.exclude_input.setFixedWidth(250)
        self.exclude_input.setEnabled(False)

        for w in (self.open_btn, self.path_str, self.exclude_str, self.exclude_input):
            ctl_top.addWidget(w)

        ctl_top.addStretch()

        for w in (self.auto_next_cb, self.display_img_cb):
            ctl_top.addWidget(w)
        for w in (self.scan_btn, self.pause_btn, self.continue_btn, self.exit_btn):
            ctl_mid.addWidget(w)
        for w in (self.first_btn, self.prev_folder_btn, self.prev_btn,
                self.next_btn, self.next_folder_btn, self.last_btn):
            ctl_bottom.addWidget(w)

        layout.addLayout(ctl_top)
        layout.addLayout(ctl_mid)
        layout.addLayout(ctl_bottom)

        self.status = QLabel()
        layout.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll)

        # ---------- 3) i18n binding ----------
        self.i18n_binder.bind(self.open_btn, "setText", "btn.open")
        self.i18n_binder.bind(self.exclude_str, "setText", "label.exclude_str")
        self.i18n_binder.bind(self.scan_btn, "setText", "btn.scan")
        self.i18n_binder.bind(self.pause_btn, "setText", "btn.pause")
        self.i18n_binder.bind(self.continue_btn, "setText", "btn.continue")
        self.i18n_binder.bind(self.exit_btn, "setText", "btn.exit")
        self.i18n_binder.bind(self.delete_btn, "setText", "btn.delete")
        self.i18n_binder.bind(self.first_btn, "setText", "btn.first")
        self.i18n_binder.bind(self.prev_folder_btn, "setText", "btn.prev_folder")
        self.i18n_binder.bind(self.prev_btn, "setText", "btn.prev")
        self.i18n_binder.bind(self.next_btn, "setText", "btn.next")
        self.i18n_binder.bind(self.next_folder_btn, "setText", "btn.next_folder")
        self.i18n_binder.bind(self.last_btn, "setText", "btn.last")
        self.i18n_binder.bind(self.auto_next_cb, "setText", "cb.auto_next")
        self.i18n_binder.bind(self.display_img_cb, "setText", "cb.display_img")
        self.i18n_binder.bind(self.display_original_groups_cb, "setText", "cb.display_original_groups")
        
        # placeholder / status line
        self.exclude_input.setPlaceholderText(self.i18n.t("input.exclude_placeholder"))
        self.i18n.changed.connect(lambda: self.exclude_input.setPlaceholderText(self.i18n.t("input.exclude_placeholder")))
        self.i18n.changed.connect(self.refresh_status_text)
        self.i18n_binder.bind(self.status, "setText", "status.please_select_folder")

        # ---------- 4) Menu（Using i18n too) ----------
        menubar = self.menuBar()

        # Create reusable actions, not tied to a specific OS
        # About
        about_action = QAction(self)
        about_action.setMenuRole(QAction.NoRole)
        about_action.triggered.connect(self.show_about_gpg)
        self.i18n_binder.bind(about_action, "setText", "menu.help.about")

        # Preferences
        prefs_action = QAction(self)
        prefs_action.setMenuRole(QAction.NoRole)
        prefs_action.setShortcut(QKeySequence("Ctrl+,"))
        prefs_action.triggered.connect(self.open_settings)
        self.i18n_binder.bind(prefs_action, "setText", "menu.edit.settings")

        # Quit
        quit_action = QAction(self)
        quit_action.setMenuRole(QAction.NoRole)
        quit_action.setShortcut(QKeySequence.Quit)   # Cmd+Q / Ctrl+Q
        quit_action.triggered.connect(self.btn_action_exit_and_save)
        self.addAction(quit_action)
        self.i18n_binder.bind(quit_action, "setText", "menu.app.quit")

        # Menus 
        edit_menu = menubar.addMenu(self.i18n.t("menu.edit"))
        self.i18n_binder.bind(edit_menu, "setTitle", "menu.edit")
        edit_menu.addAction(prefs_action)

        help_menu = menubar.addMenu(self.i18n.t("menu.help"))
        self.i18n_binder.bind(help_menu, "setTitle", "menu.help")
        help_menu.addAction(about_action)

        quit_menu = menubar.addMenu(self.i18n.t("menu.quit"))
        self.i18n_binder.bind(quit_menu, "setTitle", "menu.quit")
        quit_menu.addAction(quit_action)

        # ---------- 5) Keep original process ----------
        self.folder = None
        self.stage = "init"
        self.image_paths = []
        self.phashes = {}
        self.groups = []
        self.current = 0
        self.dialogs = []
        self.paused = False
        self.exit = False
        self.previous_file_counter = 0
        self.duplicate_size = 0
        self.hash_format = "v2"
        self.remaining_compare_index = 0
        self.forward = True
        self.progress_file = None
        self.filelist_file = None
        self.exceptions_file = None
        self.last_ui_update = 0
        self.last_scan_time = None
        self.lock_file = None
        self.lock_data = None
        self.exception_file_version = 1
        self.not_duplicate_pairs = []
        self.exception_groups = []
        self.lock_timer = QTimer()
        self.lock_timer.timeout.connect(self.lock_update)
        self.lock_timer.start(30 * 60 * 1000)
        self.view_groups = []
        self.view_summary = []
        self.visited = set()
        self.action = "init"

        # Restore configuration theme / language
        self.cfg = Config()
        self.apply_theme(self.cfg.get("ui.theme","system"))
        self.apply_language(self.cfg.get("ui.lang","zh-TW"))
        self.current_thumb_size = int(self.cfg.get("ui.thumbnail.max_size", 220))
        self.confirm_delete = (bool(self.cfg.get("behavior.confirm_delete", True)))
        self.compare_file_size = (bool(self.cfg.get("behavior.compare_file_size", True)))
        self.similarity_tolerance = int(self.cfg.get("behavior.similarity_tolerance", 5))

        # Restore font size
        self.fontsize = int(self.cfg.get("ui.font_size", 12))
        self.apply_app_font(self.fontsize)

        self._register_shortcuts()
        self.button_controller("init")
        QTimer.singleShot(0, lambda: self.setFocus())  

    def _register_shortcuts(self):
        self._shortcuts = []

        def add(seq, handler):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)  # 整個 app 都有效（視窗在前景）
            sc.activated.connect(handler)
            self._shortcuts.append(sc)

        # File / Process control
        add("O", self.btn_action_select_folder)           # 0 Select folder
        add("S", self.btn_action_scan)                    # 1 Start scan
        add("P", self.btn_action_pause_processing)        # 2 Pause
        add("C", self.btn_action_continue_processing)     # 3 Continue
        add("Q", self.btn_action_exit_and_save)           # 4 Exit

        # Explorer
        add("F", self.btn_action_first_group)             # 5 First group
        add("Home", self.btn_action_first_group)          # 6 First group
        add("Left", self.btn_action_prev_group)           # 7 Previous group
        add("Right", self.btn_action_next_group_or_compare)  # 8 Next group or compare
        add("Up", self.btn_action_prev_compare_folder)    # 9 Previous folder
        add("Down", self.btn_action_next_compare_folder)  # 10 Next folder
        add("L", self.btn_action_last_group)              # 11 Last group
        add("End", self.btn_action_last_group)            # 12 Last group

        # Delete unselected files
        add("Backspace", self.btn_action_delete_unchecked)   # 13 Delete unchecked files
        add("Delete", self.btn_action_delete_unchecked)      # 14 Delete unchecked files

        # 0~9 mapping checkbox
        for i in range(0, 10):
            sc = QShortcut(QKeySequence(str(i)), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(lambda i=i: self.toggle_checkbox(i - 1))
            self._shortcuts.append(sc)        # 15~24 File number and all
        
        # Show groups action
        add("Ctrl+S",self.btn_action_merge_selected)
        add("Ctrl+D",self.btn_action_separate_selected)
        add("Ctrl+I",self.btn_action_ignore_group)
        add("Ctrl+U",self.btn_action_unmarked_selected)
        

    def apply_app_font(self, size: int):
        f = QApplication.font()
        f.setPointSize(int(size))
        QApplication.setFont(f)

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self.i18n, self.i18n_binder, parent=self)
        dlg.settings_applied.connect(self.on_settings_applied)
        dlg.exec_()
    
    def on_settings_applied(self, changed_keys: list):
        # Have to press hot-apply
        if "ui.font_size" in changed_keys:
            self.fontsize = int(self.cfg.get("ui.font_size", 12))
            self.apply_app_font(self.fontsize)
        #if "ui.theme" in changed_keys:
        #    self.apply_theme(self.cfg.get("ui.theme"))
        if "ui.lang" in changed_keys:
            self.apply_language(self.cfg.get("ui.lang"))
            self.retranslate_ui_texts()
        if "ui.thumbnail.max_size" in changed_keys:
            self.current_thumb_size = int(self.cfg.get("ui.thumbnail.max_size"))
            self.reload_thumbnails_for_current_group()
        if "behavior.confirm_delete" in changed_keys:
            self.confirm_delete = int(self.cfg.get("behavior.confirm_delete"))
        if "behavior.compare_file_size" in changed_keys or "behavior.similarity_tolerance" in changed_keys:
            self.compare_file_size = int(self.cfg.get("behavior.compare_file_size"))
            self.similarity_tolerance = self.cfg.get("behavior.similarity_tolerance")
            if self.stage=="done" or self.stage=="comparing":
                self.remaining_compare_index = 0
                self.groups = []
                self.duplicate_size = 0
                self.run_comparing()
        #if "behavior.exclude_dirs" in changed_keys:
        #    self.reload_exclude_dirs_from_config()
    
    def count_duplicate_size(self, groups):
        # Summary file size from second to end
        return sum(
            self.phashes[p]["size"] for group in groups for p in group[1:]
            if p in self.phashes and isinstance(self.phashes[p], dict) and "size" in self.phashes[p]
        ) / (1024 * 1024)  # MB

    def get_selected_paths(self) -> list:
        return [cb.path for cb in self.scroll.findChildren(QCheckBox) if cb.isChecked()]

    def btn_action_merge_selected(self):
        sel_path = self.get_selected_paths()
        if len(sel_path) < 2:
            self.status.setText(self.i18n.t("hint.select_two_or_more", default="Select 2+ photos."))
            return
        self.constraints.add_must_link(sel_path)
        for idx_o in range(0,len(sel_path)):
            for idx_i in range(0,len(self.view_groups[self.current])):
                if self.view_groups[self.current][idx_i] not in sel_path and sel_path[idx_o]!=self.view_groups[self.current][idx_i]:
                    self.constraints.add_cannot_link(sel_path[idx_o], self.view_groups[self.current][idx_i])
        self.constraints.save_constraints()
        self.show_group()

    def btn_action_unmarked_selected(self):
        if not self.view_groups or self.current >= len(self.view_groups):
            return

        grp = self.view_groups[self.current]
        self.constraints.clear_constraints_for_group(grp)
        self.constraints.save_constraints()

        self.show_group()

    def btn_action_separate_selected(self):
        sel_path = self.get_selected_paths()
        for idx_o in range(0,len(sel_path)):
            for idx_i in range(0,len(self.view_groups[self.current])):
                if sel_path[idx_o]!=self.view_groups[self.current][idx_i]:
                    self.constraints.add_cannot_link(sel_path[idx_o], self.view_groups[self.current][idx_i])
        self.constraints.save_constraints()

        if self.current>=len(self.view_groups):
            self.current = len(self.view_groups)-1
        self.show_group()

    def btn_action_ignore_group(self):
        igr = self.view_groups[self.current]
        self.constraints.add_ignore_files(igr)
        self.constraints.save_constraints()
        if self.current>=len(self.view_groups):
            self.current = len(self.view_groups)-1
        self.show_group()
    
    # -------- Implement Hot-apply --------
    def apply_theme(self, theme: str):
        # If support QSS / dark-light
        if theme == "dark":
            QApplication.setStyle("Fusion")
            # 可選：載入 dark.qss
        elif theme == "light":
            QApplication.setStyle("Fusion")
            # 可選：載入 light.qss
        else:
            # system
            QApplication.setStyle(None)

    def apply_language(self, lang_code: str):
        # "auto" using system language
        self.i18n.set_locale(lang_code)
        self.i18n_binder.retranslate()
        self.refresh_status_text()
        self.exclude_input.setPlaceholderText(self.i18n.t("input.exclude_placeholder"))
        # Save to QSettings
        self.settings.setValue("locale", lang_code)

    def retranslate_ui_texts(self):
        if self.action=="show_group":
            self._group_host_ready = False
            self.show_group()

    def refresh_status_text(self):
        # Show i18n context based on self.stage / self.current / self.groups
        if self.stage == "done":
            if self.duplicate_size >= 1024:
                size_str = f"{self.duplicate_size / 1024:,.2f} GB"
            else:
                size_str = f"{self.duplicate_size:,.2f} MB"
            self.status.setText(
                self.i18n.t("status.done_summary",
                            groups=len(self.groups),
                            view=len(self.view_groups),
                            size=size_str,
                            images=len(self.phashes))
            )
        elif self.stage == "hashing":
            if self.paused:
                self.status.setText(self.i18n.t("status.hashing_pause"))

        elif self.stage == "comparing":
            if self.paused:
                self.status.setText(self.i18n.t("status.comparison_pause"))

        else:
            # init
            self.status.setText(self.i18n.t("status.please_select_folder"))

    def reload_thumbnails_for_current_group(self):
        if self.action=="show_group":
            self.show_group()

    def reload_exclude_dirs_from_config(self):
        pass

    def closeEvent(self, event):
        self.btn_action_exit_and_save()
        event.accept()

    def pertimes_processevent(self,times):
        now = time.time()
        if now-self.last_ui_update > times:
            self.last_ui_update = now
            return True
        else:
            return False

    def sort_group(self, groups):    
        group_keys = [(grp, gen_group_sort_key(grp)) for grp in groups]
        group_keys.sort(key=lambda x: x[1])
        self.groups = [grp for grp, _ in group_keys]
    
    def lock_by_self(self):
        if self.folder is None:
            return False

        self.lock_file = os.path.join(self.folder, ".duplicate.lock")

        # Check if the existing lock file belongs to current process on this machine.
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, "r", encoding="utf-8") as f:
                    lock_data = json.load(f)
                lock_pid = lock_data.get("pid", 0)
                lock_machine = lock_data.get("machine", 0)

                if (lock_pid == os.getpid() and lock_machine == platform.node()):
                    return True
                else:
                    return False
            except Exception as e:
                print(f"[Error] Failed to read lock file: {e}")
                return False
        return False

    def lock_check_and_create(self):
        self.lock_file = os.path.join(self.folder, ".duplicate.lock")

        # Check for an active lock file. If not expired or belong to another process, block execution. 
        # Otherwise, create a new lock.
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, "r", encoding="utf-8") as f:
                    lock_data = json.load(f)
                lock_pid = lock_data.get("pid", 0)
                lock_machine = lock_data.get("machine", 0)
                updated_str = lock_data.get("updated", lock_data.get("created"))
                updated_time = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S")

                if datetime.now() - updated_time < timedelta(minutes=30) and (lock_pid != os.getpid() or lock_machine != platform.node()):
                    box = QMessageBox(self)
                    box.setIcon(QMessageBox.Warning)
                    box.setWindowTitle(self.i18n.t("lock.title"))
                    box.setText(f"{self.i18n.t('lock.folderlock', machine=lock_data.get('machine'),updt=updated_str)}")
                    box.setStandardButtons(QMessageBox.Ok)
                    box.button(QMessageBox.Ok).setText(self.i18n.t("btn.ok"))
                    box.exec_()
                    return False
            except Exception as e:
                print(f"[Error] Failed to read lock file: {e}")
                # If the lock file is damaged, allow overwrite
                pass

        # Create new lock file
        try:
            self.lock_file = os.path.join(self.folder, ".duplicate.lock")
            self.lock_data = {
                "machine": platform.node(),
                "pid": os.getpid(),
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(self.lock_file, "w", encoding="utf-8") as f:
                json.dump(self.lock_data, f)
            print(f"[Message] Lock file created at {self.lock_file}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "Lock Error", f"Failed to create lock file:\n{e}")
            return False

    def lock_update(self):
        if self.lock_by_self():
            try:
                self.lock_data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(self.lock_file, "w", encoding="utf-8") as f:
                    json.dump(self.lock_data, f)
                print(f"[Message] Heartbeat updated: {self.lock_data['updated']}")
            except Exception as e:
                print(f"[Error] Failed to update lock file: {e}")

    def lock_cleanup(self):
        if self.lock_by_self():
            try:
                if hasattr(self, "lock_file") and os.path.exists(self.lock_file):
                    os.remove(self.lock_file)
                    print(f"[Message] Lock file removed: {self.lock_file}")
            except Exception as e:
                print(f"[Error] Failed to remove lock file: {e}")

    def ask_question_modal(self, title, text, default):
        try:
            box = QMessageBox(self)
            box.setWindowTitle(title)
            box.setText(text)
            box.setIcon(QMessageBox.Question)

            yes_btn = box.addButton(self.i18n.t("dlg.btn.yes"), QMessageBox.YesRole)
            no_btn  = box.addButton(self.i18n.t("dlg.btn.no"),  QMessageBox.NoRole)
            if default:
                box.setDefaultButton(yes_btn)
            else:
                box.setDefaultButton(no_btn)

            box.setWindowModality(Qt.ApplicationModal)
            box.exec_()

            clicked = box.clickedButton()
            if clicked == yes_btn:
                return QMessageBox.Yes
            else:
                return QMessageBox.No
        except Exception:
            return QMessageBox.No

    def get_full_path(self, rel_path):
        full_path = os.path.join(self.folder, rel_path)

        # Normalize path (remove redundant .\.）
        full_path = os.path.normpath(full_path)

        if os.name == 'nt':  # Windows
            # Handle POSIX network path //server/share
            if full_path.startswith("//"):
                # Transfer prefix // to \\
                full_path = "\\" + full_path[2:]
            # Other / change to \
            full_path = full_path.replace("/", "\\")
        else:
            # In macOS / Linux using POSIX
            full_path = full_path.replace("\\", "/")

        return full_path

    def toggle_checkbox(self, index):
        if not hasattr(self, "group_checkboxes"):
            return

        if index == -1:
            # 🔁 Invert all checkbox
            for cb in self.group_checkboxes:
                if cb.isEnabled():
                    cb.setChecked(not cb.isChecked())
        elif 0 <= index < len(self.group_checkboxes):
            cb = self.group_checkboxes[index]
            cb.setChecked(not cb.isChecked())

    def btn_action_select_folder(self):
        self.action = "select_folder"

        # Save filelist, exceptions, progress files before change folder
        if self.folder:
            self.save_filelist()
            self.save_exceptions()
            self.save_progress()
        
        selected_folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if not selected_folder:
            return

        # Change folder or 
        if hasattr(self,"folder") and self.folder and self.folder != selected_folder:
            self.lock_cleanup()

        self.folder = selected_folder

        if self.lock_check_and_create()==False:
            return
        
        self.scroll.setWidget(QWidget())  # Clear message in scroll
        self.progress.setVisible(False)
        self.path_str.setText(self.folder)
        self.button_controller("select folder")
        self.phashes = {}
        self.groups = []
        self.image_paths = []
        self.current = 0
        self.progress_file = os.path.join(self.folder, f"{PROGRESS_FILE}")
        self.filelist_file = os.path.join(self.folder, f"{FILELIST_FILE}")
        self.exceptions_file = os.path.join(self.folder,f"{EXCEPTIONS_FILE}")

        self.constraints = ConstraintsStore(scan_folder=self.folder)
        self.status.setText(self.i18n.t("status.press_scan_button"))
        self.load_filelist()
        self.load_exceptions()
        self.load_progress()

    def btn_action_scan(self):
        self.action = "collecting_filelist"
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
        QApplication.processEvents()
        self.scroll.setWidget(QWidget())  # Clear message in scroll
        # If file list is exist, asking for re-scan folder
        if os.path.exists(self.filelist_file) and self.stage != "collecting":
            title = self.i18n.t("dlg.filelist.title")
            body = self.i18n.t(
                "dlg.filelist.body",
                last_scan_time=self.last_scan_time or self.i18n.t("common.unknown")
            )
            reply = self.ask_question_modal(title, body, False)

            if reply == QMessageBox.No:
                self.status.setText(
                    self.i18n.t("status.loaded_from_filelist", count=len(self.image_paths))
                )
                if self.progress_compare_file_size!=self.compare_file_size or self.progress_similarity_tolerance!=self.similarity_tolerance:
                    self.stage = "collecting"
                    self.remaining_compare_index = 0
                    self.groups = []
                    self.duplicate_size = 0
                    self.current = 0
                
                self.scan_duplicates()
                return
        
        # Scan folder
        original_stage = self.stage
        self.stage = "collecting"
        self.button_controller("collecting")        
        new_image_paths = []
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        exclude_dirs = {d.strip().lower() for d in self.exclude_input.text().split(",") if d.strip()}
        for root, dirs, files in os.walk(self.folder):
            dirs[:] = [d for d in dirs if not any(ex in d.lower() for ex in exclude_dirs)]
            for f in files:
                if self.pertimes_processevent(0.1):
                    QApplication.processEvents()
                full_path = os.path.join(root,f)
                rel_path = os.path.relpath(full_path, self.folder).replace("\\","/").lower()
                if os.path.splitext(f.lower())[1] in EXTS:
                    if(os.path.getsize(full_path)>50000):
                        new_image_paths.append(rel_path)
                        self.status.setText(self.i18n.t("status.found_new_images",new_image=len(new_image_paths),root=self.folder))
                    if self.exit == True:
                        return

        self.progress.setVisible(False)

        # Open progress file and compare with result of scan folder
        self.image_paths = new_image_paths
        image_paths_set = set(self.image_paths)
        if len(self.phashes)>0:
            # Remove entries from hashes, if files are not exist in file list
            removed = [path for path in self.phashes if path not in image_paths_set]
            for path in removed:
                del self.phashes[path]

            # Remove entries from groups, if files are not exist in file list
            self.duplicate_size = 0
            if self.groups:
                new_groups = []
                for group in self.groups:
                    filtered = [p for p in group if p in self.phashes]
                    if len(filtered) > 1:
                        new_groups.append(filtered)
                self.sort_group(new_groups)
                
                self.duplicate_size = sum(
                    self.phashes[p]["size"]
                    for group in self.groups
                    for p in group[1:]
                    if p in self.phashes and "size" in self.phashes[p]
                ) / 1024 / 1024
            # Convert PROGRESS file v1 to v2
            if self.hash_format=="v1":
                self.previous_file_counter = len(self.phashes)
                new_hashes = {}
                for path, h in self.phashes.items():
                    if isinstance(h, int):
                        try:
                            mtime = os.path.getmtime(path)
                            size = os.path.getsize(path)
                            new_hashes[path] = {
                                "hash": h,
                                "mtime": mtime,
                                "size": size
                            }
                        except:
                            continue
                    elif isinstance(h,dict):
                        new_hashes[path] = h

                self.phashes = new_hashes
                self.hash_format = "v2"                    
            elif self.hash_format=="v2":
                # If PROGRESS file is v2, compare entry of date is last and size is same in hashes
                completed = 0;
                if len(self.phashes)>0:
                    self.progress.setVisible(True)
                    self.progress.setMaximum(len(self.phashes))
                    self.progress.setValue(completed)
                else:
                    self.progress.setMaximum(100)
                    self.progress.setValue(100)
                
                for path in list(self.phashes.keys()):
                    if self.pertimes_processevent(0.3):
                        QApplication.processEvents()
                    completed += 1
                    self.progress.setValue(completed)
                    self.status.setText(self.i18n.t("status.checked",completed=completed,total=len(self.phashes),path=path))
                    if self.exit == True:
                        return
                    h = self.phashes[path]
                    if not isinstance(h,dict) or "hash" not in h:
                        continue
                    try:
                        full_path = self.get_full_path(path)
                        current_mtime = os.path.getmtime(full_path)
                        current_size = os.path.getsize(full_path)
                        if h.get("mtime") != current_mtime or \
                            h.get("size") != current_size:
                            del self.phashes[path]
                    except:
                        print(f"[Error] {__file__} except error del hashes {path}")
                        del self.phashes[path]
            
            # There are some entries in Hashes are removed or out of date, these entry should re-hashing
            if self.previous_file_counter!=len(self.phashes) or self.previous_file_counter!=len(self.image_paths) or self.progress_compare_file_size!=self.compare_file_size or \
                self.progress_similarity_tolerance!=self.similarity_tolerance:
                self.status.setText(self.i18n.t("status.checked_to_hash", completed=completed))
                self.remaining_compare_index = 0
                self.groups = []
                self.duplicate_size = 0
                self.current = 0
            else:
                self.stage = original_stage
                self.status.setText(self.i18n.t("status.checked_uptodate",completed=completed))

        self.previous_file_counter = len(self.image_paths)
        self.save_progress(self.stage)
        self.last_scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Save
        self.save_filelist()
        self.save_exceptions()
        self.scan_duplicates()

    def run_hashing(self):
        self.action = "hashing"
        self.stage = "hashing"
        self.paused = False
        self.progress.setMaximum(len(self.image_paths))
        self.progress.setValue(len(self.phashes))
        self.progress.setVisible(True)
        self.button_controller("scan")
        
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
        QApplication.processEvents()        

        n = len(self.image_paths)
        remaining_hash_index = len(self.phashes)
        self.progress.setMaximum(n)
        self.progress.setValue(remaining_hash_index)
        self.progress.setVisible(True)
        
        BATCH = 10
        start_time = time.time()

        # Hashing stage,using multi process
        completed = 0;
        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as exe:
            for i in range(0, len(self.image_paths), BATCH):
                if self.paused:
                    self.button_controller("pause")
                    self.status.setText(self.i18n.t("status.hashing_pause"))
                    self.save_progress(stage="hashing")
                    return

                batch = [
                    self.get_full_path(p)
                    for p in self.image_paths[i:i+BATCH]
                    if p not in self.phashes
                ]

                futs = {exe.submit(compute_hash, p): p for p in batch}

                for f in as_completed(futs):
                    if self.paused:
                        self.status.setText(self.i18n.t("status.hashing_pause"))
                        self.save_progress(stage="hashing")
                        return
                    p = futs[f]
                    try:
                        h = f.result()
                        rel_path = os.path.relpath(p, self.folder).replace("\\","/").lower()
                        self.phashes[rel_path] = {
                            "hash": h,
                            "mtime": os.path.getmtime(p),
                            "size": os.path.getsize(p)
                        }
                    except Exception as e:
                        err_msg = str(e)
                        self.phashes[rel_path] = {"error": err_msg}
                        print(f"[Error] Hash: {p} - {err_msg}")
                    if self.pertimes_processevent(0.5):
                        if self.display_img_cb.isChecked():
                            self.show_current_processing_image(f"{self.i18n.t('msg.hashing')}",p)
                        else:
                            self.scroll.setWidget(QWidget())  # Clear data in scroll
                        QApplication.processEvents()

                    remaining_hash_index += 1
                    completed = completed + 1;
                    self.progress.setValue(remaining_hash_index)
                    elapsed = time.time() - start_time
                    eta = max(0,(elapsed / completed) * (n - remaining_hash_index))
                    eta_str = time.strftime('%H:%M:%S', time.gmtime(eta))
                    self.status.setText(self.i18n.t("status.hashing_eta", eta = eta_str, remaining = remaining_hash_index, total=n, path=os.path.basename(p)))

        QApplication.processEvents()
        self.save_progress(self.stage)
        self.run_comparing()

    def run_comparing(self):
        self.action = "comparing"
        self.stage = "comparing"
        self.paused = False
        self.progress.setMaximum(len(self.image_paths))
        self.progress.setValue(len(self.phashes))
        self.progress.setVisible(True)
        self.button_controller("scan")
        self.run_pairwise_comparing()
    
    def run_pairwise_comparing(self):
        items = [
            (p, h["hash"])
            for p, h in self.phashes.items()
            if isinstance(h, dict) and "hash" in h and "error" not in h
        ]
        items.sort(key=lambda x:x[1])
        
        self.status.setText(self.i18n.t("status.comparing"))

        new_grps = self.groups[:] if self.groups else []
        total = len(items)
        start_compare = time.time()

        self.progress.setMaximum(total)
        self.progress.setValue(self.remaining_compare_index)

        MAX_LOOKAHEAD = math_clamp(8*(self.similarity_tolerance+1) ** 2, 64, 384)
        completed = 0
        
        t_report = int(self.similarity_tolerance)     # UI threshold
        delta    = min(3, t_report // 2)              # t/2，max 3
        t_link   = t_report + delta                   # edge
        
        for i, (p1, h1) in enumerate(items[self.remaining_compare_index:], start=self.remaining_compare_index):
            completed += 1            
            self.remaining_compare_index = i
            self.progress.setValue(self.remaining_compare_index)
            if p1 in self.visited:
                continue
            elapsed = time.time() - start_compare
            eta = (elapsed / (completed)) * (total - (i+1))
            eta_str = time.strftime('%H:%M:%S', time.gmtime(eta))
            self.status.setText(self.i18n.t("status.compare_eta", eta=eta_str, cur = i+1, total = total, remaining = self.remaining_compare_index, groups = len(new_grps), cur_file = os.path.basename(p1)))
            if self.paused:
                self.button_controller("pause")
                self.status.setText(self.i18n.t("status.comparison_pause"))
                self.groups = new_grps
                self.save_progress(stage="comparing", extra={"compare_index": self.remaining_compare_index})
                return

            new_grp = [p1]
            # Fixed issue: An image was added into multiple groups
            # Root cause: `visited` was defined as a local variable; after function return it was cleared,
            #             causing the same images to be re-compared and re-added into groups.
            # Solution: Use a persistent `self.visited` set at instance level and serialize it
            #           into the progress file so that state is preserved across resumes.
            self.visited.add(p1)

            size1 = self.phashes[p1]["size"]

            for j in range(i+1, min(i + 1 + MAX_LOOKAHEAD, len(items))):
                p2, h2 = items[j]
                if self.paused:
                    self.button_controller("pause")
                    self.status.setText(self.i18n.t("status.comparison_pause"))
                    self.groups = new_grps
                    self.save_progress(stage="comparing", extra={"compare_index": self.remaining_compare_index})
                    return
                if self.pertimes_processevent(0.5):
                    if self.display_img_cb.isChecked():
                        self.show_comparing_pair(p1,p2)
                    else:
                        self.scroll.setWidget(QWidget())  # Clear data in scroll
                    QApplication.processEvents()
                if p2 not in self.visited and (h1 ^ h2).bit_count() <= t_link:
                    if size1 != self.phashes[p2]["size"] and self.compare_file_size:
                        continue
                    
                    new_grp.append(p2)
                    self.duplicate_size += self.phashes[p2]["size"]/(1024*1024)
                    # Fixed issue: An image was added into multiple groups
                    # Root cause: `visited` was defined as a local variable; after function return it was cleared,
                    #             causing the same images to be re-compared and re-added into groups.
                    # Solution: Use a persistent `self.visited` set at instance level and serialize it
                    #           into the progress file so that state is preserved across resumes.
                    self.visited.add(p2)

            if len(new_grp) > 1:
                new_grps.append(new_grp)
                self.current = len(new_grps) - 1
                self.groups = new_grps
                self.status.setText(self.i18n.t("status.comparing_found", group=len(new_grps)))

                if self.remaining_compare_index < len(items):
                    if self.auto_next_cb.isChecked():
                        continue
                    else:
                        self.save_progress(stage="comparing", extra={"compare_index": self.remaining_compare_index})
                        self.show_group()
                        return

        self.sort_group(new_grps)
        self.remaining_compare_index = len(self.phashes)
        self.progress.setValue(total)
        QApplication.processEvents()
        self.stage = "done"
        self.visited = set()
        self.save_progress(stage="done")
        
        self.show_group()

    def scan_duplicates(self):
        self.scroll.setWidget(QWidget())
        
        #Resume stage
        if self.stage == "done":
            self.remaining_compare_index = len(self.phashes)
            self.show_group()
            return
        elif self.stage == "comparing":
            self.button_controller("resuming")
            self.run_comparing()
            return
        else:
            self.status.setText(self.i18n.t("status.resuming_hashing"))
            self.button_controller("resuming")
            self.run_hashing()
            return                        

    def show_current_processing_image(self, label, path):
        try:
            cont = QWidget()
            v = QVBoxLayout(cont)
            full_path = self.get_full_path(path)
            img = Image.open(full_path)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((420, 420))

            qimg = image_pil_to_qimage(img)
            pixmap = QPixmap.fromImage(qimg)

            lbl = QLabel()
            lbl.setPixmap(pixmap)
            lbl.setAlignment(Qt.AlignCenter)
            v.addWidget(lbl)

            title = QLabel(f"{label}: {os.path.basename(full_path)}")
            title.setAlignment(Qt.AlignHCenter)
            v.addWidget(title)

            self.scroll.setWidget(cont)
        except Exception as e:
            print(f"[Error] Failed to show processing image: {full_path} - {e}")
    
    def show_comparing_pair(self, p1, p2):
        try:
            cont = QWidget()
            hbox = QHBoxLayout(cont)

            for path in [p1, p2]:
                vbox = QVBoxLayout()
                full_path = self.get_full_path(path)
                img = Image.open(full_path)
                img.thumbnail((300, 300))
                qimg = image_pil_to_qimage(img)
                pixmap = QPixmap.fromImage(qimg)
                scaled_pixmap = pixmap.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)

                img_label = QLabel()
                img_label.setPixmap(scaled_pixmap)
                img_label.setAlignment(Qt.AlignHCenter)
                vbox.addWidget(img_label)

                label = QLabel(os.path.basename(full_path))
                label.setAlignment(Qt.AlignHCenter)
                vbox.addWidget(label)

                hbox.addLayout(vbox)

            self.scroll.setWidget(cont)
        except Exception as e:
            print(f"[Error] Failed to show comparing images: {e}")
    
    def show_group(self):
        if self.display_original_groups_cb.isChecked() or self.stage == "comparing":
            self.view_groups = self.groups
        else:
            self.view_groups, self.view_summary = self.constraints.apply_to_all_groups(self.groups)
        self.duplicate_size = self.count_duplicate_size(self.view_groups)
        self.button_controller("show group")
        #self.show_group_basic()
        self.show_group_advance()

    def _group_host_build(self):
        if getattr(self, "_group_host_ready", False):
            return

        cw = self.centralWidget()
        if cw is not None and isinstance(cw.layout(), QVBoxLayout):
            root = cw.layout()
        else:
            old = cw
            holder = QWidget()
            root = QVBoxLayout(holder)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)
            if old is not None:
                old.setParent(holder)
                root.addWidget(old)
            self.setCentralWidget(holder)

        anchor_idx = -1
        for i in range(root.count()):
            it = root.itemAt(i)
            w = it.widget()
            if w is self.progress:
                anchor_idx = i
                break

        if anchor_idx == -1:
            for i in range(root.count()):
                it = root.itemAt(i)
                w = it.widget()
                if w is self.scroll:
                    anchor_idx = i - 1
                    break

        if anchor_idx != -1:
            for j in range(root.count() - 1, anchor_idx, -1):
                it = root.takeAt(j)
                w = it.widget()
                if w is not None:
                    w.setParent(None)

        # Build group host and group info
        self.group_host = QWidget()
        self.group_host.setObjectName("group_host")
        self.group_host_layout = QVBoxLayout(self.group_host)
        self.group_host_layout.setContentsMargins(6, 6, 6, 6)
        self.group_host_layout.setSpacing(8)

        # Group Host
        self.a_bar = QWidget()
        a_box = QVBoxLayout(self.a_bar)
        a_box.setContentsMargins(0, 0, 0, 0)
        a_box.setSpacing(6)

        # First row
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)

        self.group_info = QLabel("")
        row1.addWidget(self.group_info)

        self.current_thumb_size = max(400, min(1000, int(getattr(self, 'current_thumb_size', 400))))
        row1.addWidget(QLabel(self.i18n.t('label.thumb_size')))
        self.thumb_slider = QSlider(Qt.Horizontal)
        self.thumb_slider.setRange(400, 1000)
        self.thumb_slider.setSingleStep(4)
        self.thumb_slider.setPageStep(32)
        self.thumb_slider.setMinimumWidth(300)
        self.thumb_slider.setValue(self.current_thumb_size)
        row1.addWidget(self.thumb_slider)
        self.thumb_val_lbl = QLabel(f"{self.current_thumb_size}")
        row1.addWidget(self.thumb_val_lbl)

        row1.addStretch(1)

        row1.addWidget(self.display_original_groups_cb)

        # Second row
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)

        self.btn_delete   = QPushButton(self.i18n.t("btn.delete"))
        self.btn_merge    = QPushButton(self.i18n.t("btn.merge"))
        self.btn_ignore   = QPushButton(self.i18n.t("btn.ignore"))
        self.btn_separate = QPushButton(self.i18n.t("btn.separate"))
        self.btn_unmarked   = QPushButton(self.i18n.t("btn.unmarked"))
        for b in (self.btn_delete, self.btn_merge, self.btn_separate, self.btn_ignore, self.btn_unmarked):
            row2.addWidget(b)
        row2.addStretch(1)

        a_box.addLayout(row1)
        a_box.addLayout(row2)

        self.a_bar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # Add to group host
        self.group_host_layout.addWidget(self.a_bar)

        # Group Info
        if self.scroll.parent() is not None:
            self.scroll.setParent(None)
        self.group_host_layout.addWidget(self.scroll, 1)

        self.a_bar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.group_host.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        root.addWidget(self.group_host, 1)

        self.btn_delete.clicked.connect(self.btn_action_delete_unchecked)
        self.btn_ignore.clicked.connect(self.btn_action_ignore_group)
        self.btn_separate.clicked.connect(self.btn_action_separate_selected)
        self.btn_merge.clicked.connect(self.btn_action_merge_selected)
        self.btn_unmarked.clicked.connect(self.btn_action_unmarked_selected)

        if not hasattr(self, "_thumb_resize_debouncer"):
            self._thumb_resize_debouncer = QTimer(self)
            self._thumb_resize_debouncer.setSingleShot(True)
            self._thumb_resize_debouncer.setInterval(120)

        def _apply_resize(val, quality):
            self._resize_thumbs(val, quality)

        def on_thumb_drag(val: int):
            val = max(400, min(1000, int(val)))
            self.current_thumb_size = val
            self.thumb_val_lbl.setText(f"{val}")
            _apply_resize(val, Qt.FastTransformation)
            self._thumb_resize_debouncer.stop()
            if self._thumb_resize_debouncer.receivers(self._thumb_resize_debouncer.timeout):
                self._thumb_resize_debouncer.timeout.disconnect()
            self._thumb_resize_debouncer.timeout.connect(
                lambda: _apply_resize(self.current_thumb_size, Qt.SmoothTransformation)
            )
            self._thumb_resize_debouncer.start()

        def on_thumb_release():
            self._thumb_resize_debouncer.stop()
            _apply_resize(self.current_thumb_size, Qt.SmoothTransformation)

        self.thumb_slider.valueChanged.connect(on_thumb_drag)
        self.thumb_slider.sliderReleased.connect(on_thumb_release)
        self.display_original_groups_cb.toggled.connect(self.show_group)

        self._group_host_ready = True

    def _resize_thumbs(self, size: int, quality=Qt.SmoothTransformation):
        if not hasattr(self, "_thumb_labels"):
            return
        vp = self.scroll.viewport() if hasattr(self, "scroll") else None
        if vp: vp.setUpdatesEnabled(False)

        styles = getattr(self, "_thumb_styles", [])
        for i, (lbl, qimg) in enumerate(zip(self._thumb_labels, self._thumb_qimages)):
            if lbl is None or qimg is None:
                continue
            pm = QPixmap.fromImage(qimg).scaled(size, size, Qt.KeepAspectRatio, quality)

            st = styles[i] if i < len(styles) else 'normal'
            if st == 'dark':
                painter = QPainter(pm)
                painter.fillRect(pm.rect(), QColor(0, 0, 0, 110))
                painter.end()

            lbl.setPixmap(pm)

        if vp: vp.setUpdatesEnabled(True)

    def _groups_info_update(self, grp):

        # Clear cache
        self.group_checkboxes = []
        self._thumb_labels = []
        self._thumb_qimages = []
        self._thumb_styles = []
        is_marked = False

        cont = QWidget()
        v = QVBoxLayout(cont)
        v.setSpacing(8)
        v.setContentsMargins(0, 0, 0, 0)
        
        group_full_paths = [self.get_full_path(p) for p in grp]
        common_prefix = os.path.commonpath(group_full_paths).replace("\\", "/").lower()
        if len(common_prefix) > 0 and not common_prefix.endswith("/"):
            common_prefix += "/"
        
        for idx, p in enumerate(grp, start=1):
            hb = QHBoxLayout()
            hb.setSpacing(6)
            hb.setContentsMargins(0, 0, 0, 8)
            is_ignored = False
            is_cannot = False
            is_must = False

            full_path = self.get_full_path(p).replace("\\", "/").lower()
            # Thumb（Left）
            try:
                # Load image (PIL）and rotation and zoom in/out
                base_size = max(self.current_thumb_size, 1400)
                img = image_load_for_thumb(full_path, want_min_edge=max(self.current_thumb_size, 1400))

                # If image in ignore list, transform to gray
                if hasattr(self, "constraints") and self.constraints:
                    try:
                        is_ignored = bool(self.constraints.is_file_ignored(p))
                    except Exception:
                        is_ignored = False

                if is_ignored:
                    is_marked = True
                    try:
                        img = ImageOps.grayscale(img)
                    except Exception:
                        img = img.convert("L")
                
                # If image in can't link group, transform to dark later
                if hasattr(self, "constraints") and self.constraints:
                    try:
                        is_cannot = any(p == a or p == b for (a, b) in self.constraints.cannot_pairs)
                    except Exception:
                        is_cannot = False
                
                # If image in must link group    
                if hasattr(self, "constraints") and self.constraints:
                    try:
                        is_must = any(p == a or p == b for (a, b) in self.constraints.must_pairs)
                    except Exception:
                        is_must = False

                # Build QImage / QPixmap ----------------
                qimg = image_pil_to_qimage(img)
                pm   = QPixmap.fromImage(qimg)
                target_w = min(self.current_thumb_size, pm.width())
                target_h = min(self.current_thumb_size, pm.height())
                pixmap = pm.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

                # Dark for can't-link
                style = 'normal'
                if is_must:
                    is_marked = True
                
                if is_cannot:
                    is_marked = True
                    style = 'dark'
                    painter = QPainter(pixmap)
                    painter.fillRect(pixmap.rect(), QColor(0, 0, 0, 110))  # Range 80~150 
                    painter.end()

                # Display
                thumb_lbl = QLabel()
                thumb_lbl.setAlignment(Qt.AlignCenter)
                thumb_lbl.setPixmap(pixmap)
                thumb_lbl.mousePressEvent = lambda e, fp=full_path: self.show_image_dialog(fp)
                hb.addWidget(thumb_lbl)

                # Cachs for slider
                self._thumb_labels.append(thumb_lbl)
                self._thumb_qimages.append(qimg)
                self._thumb_styles.append(style)
            except Exception as e:
                print(f"[Error] Failed to load image: {full_path} - {e}")
                err_msg = self.i18n.t("err.fail_to_load_images", path=full_path, str=str(e))
                if os.path.exists(full_path):
                    size = os.path.getsize(full_path) / 1024 / 1024
                    err_msg += f"\n{self.i18n.t('msg.filesize')}: {size:.2f} MB"

                thumb_lbl = QLabel(err_msg)
                thumb_lbl.setWordWrap(True)
                thumb_lbl.setFixedWidth(500)
                thumb_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                hb.addWidget(thumb_lbl)

                self._thumb_labels.append(None)
                self._thumb_qimages.append(None)

            # File information (Right)
            v_info = QVBoxLayout()
            v_info.addStretch(1)
            v_info.setContentsMargins(0, 0, 0, 0)

            # Path
            rel_path = os.path.dirname(os.path.relpath(full_path, common_prefix).replace("\\", "/").lower())
            if len(rel_path) > 0 and not rel_path.endswith("/"):
                rel_path += "/"

            file_name = ""
            file_size = ""
            if os.path.exists(full_path):
                # Keep
                if is_must:
                    cb = QCheckBox(f"{self.i18n.t('msg.must')}")
                elif is_cannot:
                    cb = QCheckBox(f"{self.i18n.t('msg.separate')}")
                elif is_ignored:
                    cb = QCheckBox(f"{self.i18n.t('msg.ignore')}")
                else:
                    cb = QCheckBox(f"{self.i18n.t('msg.keepfile')}")
                cb.setChecked(True)
                cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                cb.path = p
                self.group_checkboxes.append(cb)
                v_info.addWidget(cb)
                file_name = f"{idx}. {self.i18n.t('msg.filename')}: " + os.path.basename(full_path)
                file_size_b = os.path.getsize(full_path)
                file_size = f"{(file_size_b / 1000):,.2f} KB" if file_size_b < 1000 * 1000 else f"{(file_size_b / (1000 * 1000)):,.2f} MB"

            info_label = QLabel()
            info_label.setTextFormat(Qt.RichText)
            info_label.setWordWrap(True)
            info_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            info_label.setText(
                f"{file_name}<br>"
                f"{build_highlight_html(common_prefix, rel_path)}<br>"
                f"{self.i18n.t('msg.filesize')}: {file_size}<br>"
            )
            v_info.addWidget(info_label)

            # Open in folder
            btn = QPushButton(self.i18n.t("btn.show_in_finder"))
            btn.setFixedHeight(22)
            btn.clicked.connect(lambda _, fp=full_path: self.open_in_explorer(fp))
            v_info.addWidget(btn)
            v_info.addStretch(1)

            hb.addLayout(v_info)
            v.addLayout(hb)

        if is_marked:
            self.btn_unmarked.setEnabled(True)
        else:
            self.btn_unmarked.setEnabled(False)
        v.addStretch(1)
        self.scroll.setWidget(cont)

    def show_group_advance(self):
        self.action = "show_group"
        self._group_host_build()

        show_groups = getattr(self, "view_groups", self.groups)

        # Empty group
        if not show_groups:
            self.group_info.setText(self.i18n.t("label.group_empty"))
            self.scroll.setWidget(QWidget())
            return

        # Adjust current index
        if self.current >= len(show_groups):
            self.current = len(show_groups) - 1

        grp = show_groups[self.current]
        
        # Update group host lable
        if self.remaining_compare_index >= len(self.phashes):
            label_text = self.i18n.t("label.group_progress",
                                    current=self.current + 1,
                                    total=len(show_groups),
                                    images=len(grp))
        else:
            label_text = self.i18n.t("label.group_found",
                                    current=self.current + 1,
                                    images=len(grp))
        self.group_info.setText(label_text)

        # Refresh groups image
        self._groups_info_update(grp)

    def show_group_basic(self):
        self.action = "show_group"
        cont = QWidget()
        v = QVBoxLayout(cont)
        v.setSpacing(8)
        v.setContentsMargins(6, 6, 6, 6)
        self.group_checkboxes = []  # Saving checkboxes of current group

        # Prepare thumb cache for slider
        self._thumb_labels = []
        self._thumb_qimages = []

        show_groups = self.view_groups

        if not show_groups or len(show_groups)==0:
            self.scroll.setWidget(cont)
            return

        if self.current>=len(show_groups):
            self.current = len(show_groups)-1

        grp = show_groups[self.current]
        if self.remaining_compare_index >= len(self.phashes):
            label_text = self.i18n.t(
                "label.group_progress",
                current=self.current + 1,
                total=len(show_groups),
                images=len(grp)
            )
        else:
            label_text = self.i18n.t(
                "label.group_found",
                current=self.current + 1,
                images=len(grp)
            )

        cur_info = QLabel(label_text)
        cur_info.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        v.addWidget(cur_info)

        # Thumb size bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)
        top_bar.setContentsMargins(0, 0, 0, 4)


        size_lbl = QLabel(self.i18n.t('label.thumb_size') if hasattr(self, 'i18n') else 'Thumb')
        size_lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        top_bar.addWidget(size_lbl)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(400, 1000)
        slider.setSingleStep(4)
        slider.setPageStep(32)
        slider.setMinimumWidth(300)

        cur = getattr(self, 'current_thumb_size', 400)
        cur = max(400, min(1000, int(cur)))
        self.current_thumb_size = cur
        slider.blockSignals(True)
        slider.setValue(cur)
        slider.blockSignals(False)

        val_lbl = QLabel(f"{cur}")
        val_lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # The function only resizes (zoom in/out); don't re-create
        def _resize_thumbs(size: int, quality=Qt.SmoothTransformation):
            if not hasattr(self, "_thumb_labels"):
                return
            vp = self.scroll.viewport() if hasattr(self, "scroll") else None
            if vp: vp.setUpdatesEnabled(False)
            for lbl, qimg in zip(self._thumb_labels, self._thumb_qimages):
                if lbl is None or qimg is None:
                    continue
                pm = QPixmap.fromImage(qimg).scaled(size, size, Qt.KeepAspectRatio, quality)
                lbl.setPixmap(pm)
            if vp: vp.setUpdatesEnabled(True)

        # Avoid flicker and lag
        if not hasattr(self, "_thumb_resize_debouncer"):
            self._thumb_resize_debouncer = QTimer(self)
            self._thumb_resize_debouncer.setSingleShot(True)
            self._thumb_resize_debouncer.setInterval(120)

        def on_thumb_drag(val: int):
            val = max(400, min(1000, int(val)))
            self.current_thumb_size = val
            val_lbl.setText(f"{val}")
            _resize_thumbs(val, Qt.FastTransformation)
            # Wait 120ms then increase quality
            self._thumb_resize_debouncer.stop()
            self._thumb_resize_debouncer.timeout.disconnect() if self._thumb_resize_debouncer.receivers(self._thumb_resize_debouncer.timeout) else None
            self._thumb_resize_debouncer.timeout.connect(lambda: _resize_thumbs(self.current_thumb_size, Qt.SmoothTransformation))
            self._thumb_resize_debouncer.start()

        def on_thumb_release():
            self._thumb_resize_debouncer.stop()
            _resize_thumbs(self.current_thumb_size, Qt.SmoothTransformation)

        slider.valueChanged.connect(on_thumb_drag)
        slider.sliderReleased.connect(on_thumb_release)

        top_bar.addWidget(slider)
        top_bar.addWidget(val_lbl)
        top_bar.addStretch(1)
        v.addLayout(top_bar)
        # Thumb size bar

        group_full_paths = [self.get_full_path(p) for p in grp]
        common_prefix = os.path.commonpath(group_full_paths).replace("\\", "/").lower()
        if len(common_prefix) > 0 and not common_prefix.endswith("/"):
            common_prefix += "/"

        # Pause repaint when create
        vp = self.scroll.viewport()
        if vp: vp.setUpdatesEnabled(False)

        for idx, p in enumerate(grp, start=1):
            hb = QHBoxLayout()
            hb.setSpacing(6)
            hb.setContentsMargins(0, 0, 0, 8)

            full_path = self.get_full_path(p).replace("\\", "/").lower()

            # Thumb（Left）
            try:
                # Preload large image for slider
                base_size = max(self.current_thumb_size, 1400)

                img = image_load_for_thumb(full_path, want_min_edge=max(self.current_thumb_size, 1400))
                w0, h0 = img.size                      # PIL image size
                qimg = image_pil_to_qimage(img)
                pm   = QPixmap.fromImage(qimg)
                target_w = min(self.current_thumb_size, pm.width())
                target_h = min(self.current_thumb_size, pm.height())
                pixmap = pm.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                w1, h1 = pixmap.width(), pixmap.height()   # Display image size

                pixmap = QPixmap.fromImage(qimg).scaled(
                    self.current_thumb_size, self.current_thumb_size,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )

                thumb_lbl = QLabel()
                thumb_lbl.setAlignment(Qt.AlignCenter)
                thumb_lbl.setPixmap(pixmap)
                thumb_lbl.mousePressEvent = lambda e, fp=full_path: self.show_image_dialog(fp)
                hb.addWidget(thumb_lbl)

                # Cache for slider resize（Keep high quality QImage）
                self._thumb_labels.append(thumb_lbl)
                self._thumb_qimages.append(qimg)
            except Exception as e:
                print(f"[Error] Failed to load image: {full_path} - {e}")
                err_msg = self.i18n.t("err.fail_to_load_images", path=full_path, str=str(e))
                if os.path.exists(full_path):
                    size = os.path.getsize(full_path) / 1024 / 1024
                    err_msg += f"\n{self.i18n.t('msg.filesize')}: {size:.2f} MB"

                thumb_lbl = QLabel(err_msg)
                thumb_lbl.setWordWrap(True)
                thumb_lbl.setFixedWidth(500)
                thumb_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                hb.addWidget(thumb_lbl)

                self._thumb_labels.append(None)
                self._thumb_qimages.append(None)

            # File information (Right)
            v_info = QVBoxLayout()
            v_info.addStretch(1)
            v_info.setContentsMargins(0, 0, 0, 0)

            # Path
            rel_path = os.path.dirname(os.path.relpath(full_path, common_prefix).replace("\\", "/").lower())
            if len(rel_path) > 0 and not rel_path.endswith("/"):
                rel_path += "/"

            file_name = ""
            file_size = ""
            if os.path.exists(full_path):
                # Keep
                cb = QCheckBox(f"{self.i18n.t('msg.keepfile')}")
                cb.setChecked(True)
                cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                cb.path = p
                self.group_checkboxes.append(cb)
                v_info.addWidget(cb)
                file_name = f"{idx}. {self.i18n.t('msg.filename')}: " + os.path.basename(full_path)
                file_size_b = os.path.getsize(full_path)
                file_size = f"{(file_size_b / 1000):,.2f} KB" if file_size_b < 1000 * 1000 else f"{(file_size_b / (1000 * 1000)):,.2f} MB"

            info_label = QLabel()
            info_label.setTextFormat(Qt.RichText)
            info_label.setWordWrap(True)
            info_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            info_label.setText(
                f"{file_name}<br>"
                f"{build_highlight_html(common_prefix, rel_path)}<br>"
                f"{self.i18n.t('msg.filesize')}: {file_size}<br>"
            )
            v_info.addWidget(info_label)

            # Open in folder
            btn = QPushButton(self.i18n.t("btn.show_in_finder"))
            btn.setFixedHeight(22)
            btn.clicked.connect(lambda _, fp=full_path: self.open_in_explorer(fp))
            v_info.addWidget(btn)
            v_info.addStretch(1)

            hb.addLayout(v_info)
            v.addLayout(hb)

        if vp: vp.setUpdatesEnabled(True)
        self.scroll.setWidget(cont)
    
    def show_image_dialog(self, image_path):
        dialog = ImageDialog(image_path)
        dialog.setModal(False)
        dialog.show()
        self.dialogs.append(dialog)
    
    def button_controller(self, action):
        if action=="init":
            self.open_btn.setEnabled(self.stage!="searching")
            self._shortcuts[0].setEnabled(self.stage!="searching")

            self.exclude_input.setEnabled(True)
            self.exclude_input.setReadOnly(False)

            self.scan_btn.setEnabled(False)
            self._shortcuts[1].setEnabled(False)

            self.pause_btn.setEnabled(False)
            self._shortcuts[2].setEnabled(False)

            self.continue_btn.setEnabled(False)
            self._shortcuts[3].setEnabled(False)

            self.exit_btn.setEnabled(True)
            self._shortcuts[4].setEnabled(True)
            
            self.delete_btn.setEnabled(False)
            self._shortcuts[13].setEnabled(False)
            self._shortcuts[14].setEnabled(False)

            self.first_btn.setEnabled(False)
            self._shortcuts[5].setEnabled(False)
            self._shortcuts[6].setEnabled(False)

            self.prev_btn.setEnabled(False)
            self._shortcuts[7].setEnabled(False)

            self.next_btn.setEnabled(False)
            self._shortcuts[8].setEnabled(False)

            self.prev_folder_btn.setEnabled(False)
            self._shortcuts[9].setEnabled(False)

            self.next_folder_btn.setEnabled(False)
            self._shortcuts[10].setEnabled(False)

            self.last_btn.setEnabled(False)
            self._shortcuts[11].setEnabled(False)
            self._shortcuts[12].setEnabled(False)
            return

        if action=="select folder":
            self.open_btn.setEnabled(self.stage!="searching")
            self._shortcuts[0].setEnabled(self.stage!="searching")

            self.exclude_input.setEnabled(True)
            self.exclude_input.setReadOnly(False)

            self.scan_btn.setEnabled(True)
            self._shortcuts[1].setEnabled(True)

            self.pause_btn.setEnabled(False)
            self._shortcuts[2].setEnabled(False)

            self.continue_btn.setEnabled(False)
            self._shortcuts[3].setEnabled(False)

            self.exit_btn.setEnabled(True)
            self._shortcuts[4].setEnabled(True)
            
            self.delete_btn.setEnabled(False)
            self._shortcuts[13].setEnabled(False)
            self._shortcuts[14].setEnabled(False)

            self.first_btn.setEnabled(False)
            self._shortcuts[5].setEnabled(False)
            self._shortcuts[6].setEnabled(False)

            self.prev_btn.setEnabled(False)
            self._shortcuts[7].setEnabled(False)

            self.next_btn.setEnabled(False)
            self._shortcuts[8].setEnabled(False)

            self.prev_folder_btn.setEnabled(False)
            self._shortcuts[9].setEnabled(False)

            self.next_folder_btn.setEnabled(False)
            self._shortcuts[10].setEnabled(False)

            self.last_btn.setEnabled(False)
            self._shortcuts[11].setEnabled(False)
            self._shortcuts[12].setEnabled(False)
            return

        if action=="collecting":
            self.open_btn.setEnabled(False)
            self._shortcuts[0].setEnabled(False)

            self.exclude_input.setReadOnly(True)
            self.scan_btn.setEnabled(self.stage=="done")
            self._shortcuts[1].setEnabled(self.stage=="done")

            self.pause_btn.setEnabled(False)
            self._shortcuts[2].setEnabled(False)

            self.continue_btn.setEnabled(False)
            self._shortcuts[3].setEnabled(False)

            self.exit_btn.setEnabled(True)
            self._shortcuts[4].setEnabled(True)

            self.delete_btn.setEnabled(False)
            self._shortcuts[13].setEnabled(False)
            self._shortcuts[14].setEnabled(False)

            self.first_btn.setEnabled(False)
            self._shortcuts[5].setEnabled(False)
            self._shortcuts[6].setEnabled(False)

            self.prev_btn.setEnabled(False)
            self._shortcuts[7].setEnabled(False)

            self.next_btn.setEnabled(False)
            self._shortcuts[8].setEnabled(False)

            self.prev_folder_btn.setEnabled(False)
            self._shortcuts[9].setEnabled(False)

            self.next_folder_btn.setEnabled(False)
            self._shortcuts[10].setEnabled(False)

            self.last_btn.setEnabled(False)
            self._shortcuts[11].setEnabled(False)
            self._shortcuts[12].setEnabled(False)
            return

        if action=="scan":
            self.open_btn.setEnabled(False)
            self._shortcuts[0].setEnabled(False)

            self.exclude_input.setReadOnly(False)
            self.scan_btn.setEnabled(self.stage=="done")
            self._shortcuts[1].setEnabled(self.stage=="done")

            self.pause_btn.setEnabled(True)
            self._shortcuts[2].setEnabled(True)

            self.continue_btn.setEnabled(False)
            self._shortcuts[3].setEnabled(False)

            self.exit_btn.setEnabled(False)
            self._shortcuts[4].setEnabled(False)

            self.delete_btn.setEnabled(False)
            self._shortcuts[13].setEnabled(False)
            self._shortcuts[14].setEnabled(False)

            self.first_btn.setEnabled(False)
            self._shortcuts[5].setEnabled(False)
            self._shortcuts[6].setEnabled(False)

            self.prev_btn.setEnabled(False)
            self._shortcuts[7].setEnabled(False)

            self.next_btn.setEnabled(False)
            self._shortcuts[8].setEnabled(False)

            self.prev_folder_btn.setEnabled(False)
            self._shortcuts[9].setEnabled(False)

            self.next_folder_btn.setEnabled(False)
            self._shortcuts[10].setEnabled(False)

            self.last_btn.setEnabled(False)
            self._shortcuts[11].setEnabled(False)
            self._shortcuts[12].setEnabled(False)
            return

        if action=="pause":
            self.open_btn.setEnabled(True)
            self._shortcuts[0].setEnabled(True)

            self.scan_btn.setEnabled(self.stage=="done")
            self._shortcuts[1].setEnabled(self.stage=="done")

            self.exclude_input.setReadOnly(False)
            self.pause_btn.setEnabled(False)
            self._shortcuts[2].setEnabled(False)

            self.continue_btn.setEnabled(True)
            self._shortcuts[3].setEnabled(True)

            self.exit_btn.setEnabled(True)
            self._shortcuts[4].setEnabled(True)
            self.status.setText(self.i18n.t("status.paused"))
            return

        if action=="continue":
            self.open_btn.setEnabled(False)
            self._shortcuts[0].setEnabled(False)

            self.scan_btn.setEnabled(self.stage=="done")
            self._shortcuts[1].setEnabled(self.stage=="done")

            self.exclude_input.setReadOnly(False)
            self.pause_btn.setEnabled(True)
            self._shortcuts[2].setEnabled(True)

            self.continue_btn.setEnabled(False)
            self._shortcuts[3].setEnabled(False)

            self.exit_btn.setEnabled(False)
            self._shortcuts[4].setEnabled(False)
            return

        if action=="resuming":
            self.open_btn.setEnabled(False)
            self._shortcuts[0].setEnabled(False)

            self.exclude_input.setReadOnly(False)
            self.scan_btn.setEnabled(self.stage=="done")
            self._shortcuts[1].setEnabled(self.stage=="done")

            if self.stage=="hashing":
                self.status.setText(self.i18n.t("status.resuming_hashing"))
            if self.stage=="comparing":
                self.status.setText(self.i18n.t("status.resuming_comparison"))
            if self.stage=="done":
                self.status.setText(self.i18n.t("status.restored", groups=len(self.view_groups), total = len(self.phashes)))
            self.exit_btn.setEnabled(False)
            self._shortcuts[4].setEnabled(False)
            return

        if action=="show group":
            if (self.stage=="done"):
                if self.duplicate_size >= 1024:
                    size_str = f"{self.duplicate_size / 1024:,.2f} GB"
                else:
                    size_str = f"{self.duplicate_size:,.2f} MB"
                self.status.setText(self.i18n.t("status.done_summary",groups=len(self.groups),view=len(self.view_groups),size=size_str,images=len(self.phashes)))
            elif (self.current<=0 and len(self.view_groups)>0):
                self.status.setText(self.i18n.t("status.first_groups"))
            elif (self.current>=len(self.view_groups)-1) and self.stage=="done" and len(self.view_groups)>0:
                self.status.setText(self.i18n.t("status.last_groups"))
            self.open_btn.setEnabled(True)
            self._shortcuts[0].setEnabled(True)

            self.scan_btn.setEnabled(self.stage=="done")
            self._shortcuts[1].setEnabled(self.stage=="done")

            self.exclude_input.setReadOnly(False)
            self.pause_btn.setEnabled(False)
            self._shortcuts[2].setEnabled(False)

            self.continue_btn.setEnabled(False)
            self._shortcuts[3].setEnabled(False)

            self.exit_btn.setEnabled(True)
            self._shortcuts[4].setEnabled(True)

            self.delete_btn.setEnabled(len(self.view_groups)>0)
            self._shortcuts[13].setEnabled(len(self.view_groups)>0)
            self._shortcuts[14].setEnabled(len(self.view_groups)>0)

            self.first_btn.setEnabled(len(self.view_groups)>1 and self.current>0)
            self._shortcuts[5].setEnabled(len(self.view_groups)>1 and self.current>0)
            self._shortcuts[6].setEnabled(len(self.view_groups)>1 and self.current>0)

            self.prev_btn.setEnabled(len(self.view_groups)>1 and self.current>0)
            self._shortcuts[7].setEnabled(len(self.view_groups)>1 and self.current>0)

            self.prev_folder_btn.setEnabled(len(self.view_groups)>1 and self.current>0 and self.stage=="done")
            self._shortcuts[9].setEnabled(len(self.view_groups)>1 and self.current>0 and self.stage=="done")
            
            self.next_btn.setEnabled(self.stage!="done" or (len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done"))
            self._shortcuts[8].setEnabled(self.stage!="done" or (len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done"))
            
            self.next_folder_btn.setEnabled(len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")
            self._shortcuts[10].setEnabled(len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")

            self.last_btn.setEnabled(len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")
            self._shortcuts[11].setEnabled(len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")
            self._shortcuts[12].setEnabled(len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")
            return

    def btn_action_continue_processing(self):
        self.button_controller("continue")
        self.paused = False
        self.scan_duplicates()

    def btn_action_delete_unchecked(self):
        # 1 Collect delete file list
        checkboxes = getattr(self, "group_checkboxes", None) or self.scroll.findChildren(QCheckBox)
        to_remove = [cb.path for cb in checkboxes if not cb.isChecked()]

        if not to_remove:
            return

        # 2 Confirm dialog
        if self.confirm_delete:
            title = self.i18n.t("dlg.delete_files.title")
            body = self.i18n.t("dlg.delete_files.body", cnt=len(to_remove))
            reply = self.ask_question_modal(title, body, True)
            if reply == QMessageBox.No:
                return

        # 3 Delete（Record success delete）
        actually_deleted = []
        failed = []
        for rel in to_remove:
            full_path = self.get_full_path(rel)
            try:
                os.remove(full_path)
                actually_deleted.append(rel)
            except Exception as e:
                print(f"[Delete failed] {full_path}: {e}")
                failed.append(rel)

        if not actually_deleted:
            if failed:
                self.toast(self.i18n.t("toast.delete_failed_some", cnt=len(failed)))
            return

        deleted_set = set(actually_deleted)

        # 4 Syn
        # image_paths
        if hasattr(self, "image_paths") and self.image_paths:
            self.image_paths = [p for p in self.image_paths if p not in deleted_set]

        # previous_file_counter
        if hasattr(self, "previous_file_counter"):
            self.previous_file_counter = max(0, self.previous_file_counter - len(actually_deleted))

        # 5 Save latest FILELIST_FILE
        self.save_filelist()

        # 6 Update phashes
        if hasattr(self, "phashes") and self.phashes:
            self.phashes = {p: h for p, h in self.phashes.items() if p not in deleted_set}

        # 7 Update groups
        if hasattr(self, "groups") and self.groups:
            old_len = len(self.groups)
            new_groups = []
            for group in self.groups:
                filtered = [p for p in group if p not in deleted_set]
                if len(filtered) > 1:
                    new_groups.append(filtered)

            if new_groups and self.current >= len(new_groups):
                self.current = len(new_groups) - 1
            elif not new_groups:
                self.current = 0

            if old_len > len(new_groups) and self.current > 0 and self.current >= len(new_groups):
                self.current -= 1

            self.groups = new_groups

        # 8 Clear constraints（must/cannot/ignored_files）
        removed_pairs = 0
        try:
            removed_pairs = self.constraints.remove_paths(actually_deleted)
        except Exception as e:
            print(f"[Constraints prune error] {e}")
        else:
            if removed_pairs > 0:
                self.constraints.save_constraints()

        # 9 Save progress
        self.save_progress(self.stage)

        # 10 Failure feedback
        if failed:
            self.toast(self.i18n.t("toast.delete_failed_some", cnt=len(failed)))

        if self.stage == "comparing":
            self.button_controller("scan")
            self.scan_duplicates()
        else:
            self.show_group()

    def btn_action_first_group(self):        
        if self.current > 0:
            self.current = 0
        self.forward = True
        self.show_group()

    def btn_action_prev_group(self):
        if self.current > 0:
            self.current -= 1
        self.forward = False
        self.show_group()
    
    def btn_action_prev_compare_folder(self):
        if self.current > 0:
            curkey = os.path.dirname(self.view_groups[self.current][0])
            for i in range(1, self.current+1): 
                prekey = os.path.dirname(self.view_groups[self.current-i][0])             
                if prekey != curkey:
                    self.current = self.current-i
                    break
        self.forward = False
        self.show_group()

    def btn_action_next_compare_folder(self):
        if self.current < len(self.view_groups)-1:
            curkey = os.path.dirname(self.view_groups[self.current][0])
            for i in range(1, len(self.view_groups)-self.current):
                nextkey = os.path.dirname(self.view_groups[self.current+i][0])
                if nextkey != curkey:
                    self.current = self.current+i
                    break
        self.forward = True
        self.show_group()
    
    def open_in_explorer(self, path):
        try:
            if sys.platform.startswith('darwin'):  # macOS
                os.system(f'open -R "{path}"')
            elif os.name == 'nt':  # Windows
                os.startfile(os.path.dirname(path))
            elif os.name == 'posix':  # Linux
                os.system(f'xdg-open "{os.path.dirname(path)}"')
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Can't open Explorer: {e}")

    def load_progress(self):
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r', encoding="utf-8") as f:
                    data = json.load(f)
                    self.hash_format = data.get("hash_format","v1")
                    self.stage = data.get("stage","init")
                    self.previous_file_counter = data.get("file_counter",0)
                    self.current = data.get("current",0)
                    self.display_img_cb.setChecked(data.get("show_processing_image", False))
                    self.auto_next_cb.setChecked(data.get("auto_next_group", False))
                    self.display_original_groups_cb.setChecked(data.get("show_original_groups",False))
                    self.progress_compare_file_size = data.get("compare_file_size", True)
                    self.progress_similarity_tolerance = data.get("similarity_tolerance", 5)
                    self.duplicate_size = data.get("duplicate_size", 0)
                    self.visited = set(data.get("visited",[]) )
                    self.groups = data.get("groups",[])
                    self.phashes = data.get("phashes",{})                    
                    self.remaining_compare_index = data.get("compare_index",0)
                    return True
            except Exception as e:
                print(f"[Error] Read Progress file: {e}")
                return False
        else:
            print(f"[Message] Progress file does not exist") 
            return False

    def save_progress(self, stage="done", extra=None):
        sorted_hashes = {
            k: self.phashes[k]
            for k in sorted(self.phashes, key=lambda k: self.phashes[k].get("hash", 0))
        }

        if self.progress_file is None:
            return
        
        if self.stage == "comparing":
            if not extra:
                extra = {}
            extra["compare_index"] = getattr(self, "remaining_compare_index", 0)
        data = {
            "hash_format": "v2",
            "stage": self.stage,
            "file_counter": len(self.phashes),
            "current": self.current,
            "auto_next_group":self.auto_next_cb.isChecked(),
            "show_processing_image":self.display_img_cb.isChecked(),
            "show_original_groups":self.display_original_groups_cb.isChecked(),
            "compare_file_size": self.compare_file_size,
            "similarity_tolerance": self.similarity_tolerance,
            "duplicate_size":self.duplicate_size,
            "visited": list(self.visited),
            "groups": self.groups,
            "phashes": sorted_hashes
        }
        if extra:
            data.update(extra)
        try:
            with open(self.progress_file, 'w', encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Error] saving progress: {e}")

    def load_exceptions(self):
        if os.path.exists(self.exceptions_file):
            try:
                with open(self.exceptions_file, 'r', encoding="utf-8") as f:
                    data = json.load(f)
                    self.exception_file_version = data.get("version","1")
                    self.exception_file_updated = data.get("updated","")
                    self.not_duplicate_pairs = data.get("not_duplicate_pairs",[])
                    self.exception_groups = data.get("exception_groups",[])
                    self.exclude_input.setText(data.get("exclude_folder",""))
                    return True
            except Exception as e:
                print(f"[Error] Read exception file: {e}")
                return False
        else:
            print(f"[Message] exception file does not exist") 
            return False

    def save_exceptions(self):
        data = {
            "version": self.exception_file_version,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "exclude_folder": self.exclude_input.text(),
            "not_duplicate_pairs": self.not_duplicate_pairs,
            "exception_groups": self.exception_groups,
        }

        if self.exceptions_file is None:
            return
        try:
            with open(self.exceptions_file, 'w', encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Error] saving exception: {e}")

    def load_filelist(self):
        if os.path.exists(self.filelist_file):
            try:
                with open(self.filelist_file, 'r', encoding="utf-8") as f:
                    filelist_data = json.load(f)
                    self.image_paths = filelist_data["image_paths"]
                    self.last_scan_time = filelist_data.get("last_scan_time","None")
                    return True        
            except Exception as e:
                print(f"[Error] Read filelist file: {e}")
                return False

    def save_filelist(self):
        try:
            with open(self.filelist_file, 'w', encoding="utf-8") as f:
                json.dump({
                    #"exclude_folder": self.exclude_input.text(),
                    "last_scan_time": self.last_scan_time,
                    "image_paths": self.image_paths
                    }, f, indent=2)
        except Exception as e:
            print(f"[Error] Write Filelist file: {e}")
    
    def btn_action_pause_processing(self):
        self.paused = True
        self.button_controller("pause")

    def btn_action_exit_and_save(self):
        self.action = "exit_and_save"
        self.exit = True
        self.save_exceptions()
        self.save_progress(stage=self.stage)
        if hasattr(self, 'display_thread'):
            self.display_thread.stop()
            self.display_thread.wait()
        self.lock_cleanup()
        QApplication.instance().quit()

    def btn_action_next_group_or_compare(self):
        self.forward = True
        if self.current < len(self.view_groups) - 1:
            self.current += 1
            self.show_group()
        else:
            self.button_controller("show group")
            if self.stage != "done":
                self.remaining_compare_index += 1
                self.scan_duplicates()
    
    def btn_action_last_group(self):
        if getattr(self, "stage", None) != "done":
            return
        self.forward = False
        if len(self.view_groups)>0:
            self.current = len(self.view_groups)-1
        self.show_group()

    def show_about(self):
        QMessageBox.information(
            self,
            self.i18n.t("dlg.about.title"),
            (
                f"{self.i18n.t('app.name')}\n\n"
                f"{self.i18n.t('app.version')}: {VERSION}\n"
                f"{self.i18n.t('app.build_time')}: {BUILD_TIME}\n\n"
                f"{self.i18n.t('app.developed_by')} Nick {self.i18n.t('app.since')} July 2025.\n"
                f"{self.i18n.t('app.description')}"
            )
        )

    def show_about_gpg(self):
        from utils.verify_build_signature import verify_build_signature
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QMessageBox

        dlg = QDialog(self)
        dlg.setWindowTitle(self.i18n.t("dlg.about.title"))
        layout = QVBoxLayout(dlg)

        version_str = f"{self.i18n.t('app.name')}\n{self.i18n.t('app.version')}: {VERSION}\n{self.i18n.t('app.build_time')}: {BUILD_TIME}\n\n{self.i18n.t('app.developed_by')} Nick Lin {self.i18n.t('app.since')} July 2025.\n{self.i18n.t('app.description')}"
        layout.addWidget(QLabel(version_str))

        # Add verify button
        btn = QPushButton(self.i18n.t("app.verify_app"))
        layout.addWidget(btn)

        def on_verify():
            result = verify_build_signature(VERSION, t=self.i18n.t)

            if result["status"] == 1:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Information)
                box.setWindowTitle(self.i18n.t("verify.title_verification"))
                box.setText(f"{result['message']}\n\n{self.i18n.t('verify.signature')}{result['signed_by']}\n{self.i18n.t('verify.email')}{result['email']}\n{self.i18n.t('verify.signed_on')}{result['signature_date']}\n{self.i18n.t('verify.sha256')}{result['sha256'][:12]}...")
                box.setStandardButtons(QMessageBox.Ok)
                box.button(QMessageBox.Ok).setText(self.i18n.t("btn.ok"))
                box.exec_()
            elif result["status"] == 2:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Information)
                box.setWindowTitle(self.i18n.t("verify.title_skipped"))
                box.setText(f"{result['message']}")
                box.setStandardButtons(QMessageBox.Ok)
                box.button(QMessageBox.Ok).setText(self.i18n.t("btn.ok"))
                box.exec_()
            else:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Information)
                box.setWindowTitle(self.i18n.t("verify.title_failed"))
                box.setText(f"{result['message']}\n\n{self.i18n.t('verify.not_official')}")
                box.setStandardButtons(QMessageBox.Ok)
                box.button(QMessageBox.Ok).setText(self.i18n.t("btn.ok"))
                box.exec_()
                            

        btn.clicked.connect(on_verify)

        dlg.exec_()

def main():
    try:
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    u"nick.lin.match_image_finder"
                )
            except Exception:
                pass

        #QApplication.setAttribute(Qt.AA_DontUseNativeMenuBar, True)  # Disable macOS menu
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

        app = QApplication(sys.argv)
        app.setApplicationName("Match Image Finder")
        app.setApplicationVersion(VERSION)

        # Set APP icon based on platform
        if sys.platform == "win32":
            icon_rel = "assets/app.ico"
        elif sys.platform == "darwin":
            icon_rel = "assets/app.icns"
        else:
            icon_rel = "assets/app.png"  # Linux

        try:
            icon_path = resource_path(icon_rel)
            app.setWindowIcon(QIcon(icon_path))  # app icon
        except Exception:
            pass

        win = MatchImageFinder()
        try:
            # Window icon
            win.setWindowIcon(QIcon(resource_path(icon_rel)))
        except Exception:
            pass

        win.show()
        sys.exit(app.exec_())

    except Exception as e:
        print(f"[Error] starting application: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    import multiprocessing
    
    multiprocessing.freeze_support()  # Prevent macOS open many Apps
    main()
