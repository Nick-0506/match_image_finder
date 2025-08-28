import sys, os, json, time, html, platform, rawpy, io
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial
from PyQt5.QtCore import Qt, QTimer, QSettings, QPropertyAnimation, QRect
from PyQt5.QtWidgets import (
    QAction, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QScrollArea, QCheckBox, QSizePolicy,
    QMessageBox, QProgressBar, QSlider, QDialog, QDialogButtonBox, QShortcut,
    QLineEdit, QGridLayout
)
from PyQt5.QtGui import QPixmap, QImage, QIcon, QKeySequence, QPainter, QColor
from PIL import Image, ImageOps, ImageFile
from PIL.Image import Resampling
from pillow_heif import register_heif_opener
from build_info import VERSION, BUILD_TIME
from datetime import datetime, timedelta
from utils.config_manager import Config
from utils.settings_dialog import SettingsDialog
from utils.i18n import I18n, UiTextBinder
from utils.common import resource_path
from utils.constraints_store import ConstraintsStore
from collections import OrderedDict

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
        self.action = "init"

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

        self.first_btn = QPushButton()
        self.first_btn.clicked.connect(partial(self.button_handler,"first"))
        self.first_btn.setEnabled(False)

        self.prev_folder_btn = QPushButton()
        self.prev_folder_btn.clicked.connect(partial(self.button_handler,"pre_folder"))
        self.prev_folder_btn.setEnabled(False)

        self.prev_btn = QPushButton()
        self.prev_btn.clicked.connect(partial(self.button_handler,"pre_group"))
        self.prev_btn.setEnabled(False)

        self.next_btn = QPushButton()
        self.next_btn.clicked.connect(partial(self.button_handler,"next_group"))
        self.next_btn.setEnabled(False)

        self.next_folder_btn = QPushButton()
        self.next_folder_btn.clicked.connect(partial(self.button_handler,"next_folder"))
        self.next_folder_btn.setEnabled(False)

        self.last_btn = QPushButton()
        self.last_btn.clicked.connect(partial(self.button_handler,"last"))
        self.last_btn.setEnabled(False)

        self.auto_next_cb = QCheckBox()
        self.auto_next_cb.setChecked(False)     

        self.delete_btn = QPushButton()
        self.delete_btn.clicked.connect(self.btn_action_delete_unchecked)
        self.delete_btn.setEnabled(False)

        self.merge_btn    = QPushButton(self.i18n.t("btn.merge"))
        self.ignore_btn   = QPushButton(self.i18n.t("btn.ignore"))
        self.separate_btn = QPushButton(self.i18n.t("btn.separate"))
        self.unmarked_btn = QPushButton(self.i18n.t("btn.unmarked"))
        
        self.delete_btn.clicked.connect(self.btn_action_delete_unchecked)
        self.ignore_btn.clicked.connect(self.btn_action_ignore_group)
        self.separate_btn.clicked.connect(self.btn_action_separate_selected)
        self.merge_btn.clicked.connect(self.btn_action_merge_selected)
        self.unmarked_btn.clicked.connect(self.btn_action_unmarked_selected)

        self.show_group_back_btn = QPushButton("⏏")
        self.show_group_back_btn.setFixedWidth(36)
        self.show_group_back_btn.clicked.connect(self.show_overview)

        self.display_img_dynamic_cb = QCheckBox()
        self.display_img_dynamic_cb.setChecked(False)
        self.display_img_dynamic_cb.clicked.connect(self.checkbox_handler)

        self.exclude_input = QLineEdit()
        self.exclude_input.setFixedWidth(250)
        self.exclude_input.setEnabled(False)
        self.exclude_input.editingFinished.connect(self.clear_exclude_focus)
        self.exclude_input.setFocusPolicy(Qt.ClickFocus)

        for w in (self.open_btn, self.path_str, self.exclude_str, self.exclude_input):
            ctl_top.addWidget(w)

        ctl_top.addStretch()

        for w in (self.auto_next_cb, self.display_img_dynamic_cb):
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
        layout.addWidget(self.status, alignment=Qt.AlignLeft | Qt.AlignTop)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)

        # Create here for bind i18n
        self.thumb_size_lbl = QLabel(self.i18n.t('label.thumb_size') if hasattr(self,'i18n') else 'Thumb')

        # ---------- 3) i18n binding ----------
        self.i18n_binder.bind(self.open_btn, "setText", "btn.open")
        self.i18n_binder.bind(self.exclude_str, "setText", "label.exclude_str")
        self.i18n_binder.bind(self.scan_btn, "setText", "btn.scan")
        self.i18n_binder.bind(self.pause_btn, "setText", "btn.pause")
        self.i18n_binder.bind(self.continue_btn, "setText", "btn.continue")
        self.i18n_binder.bind(self.exit_btn, "setText", "btn.exit")

        self.i18n_binder.bind(self.first_btn, "setText", "btn.first")
        self.i18n_binder.bind(self.prev_folder_btn, "setText", "btn.prev_folder")
        self.i18n_binder.bind(self.prev_btn, "setText", "btn.prev")
        self.i18n_binder.bind(self.next_btn, "setText", "btn.next")
        self.i18n_binder.bind(self.next_folder_btn, "setText", "btn.next_folder")
        self.i18n_binder.bind(self.last_btn, "setText", "btn.last")

        self.i18n_binder.bind(self.delete_btn, "setText", "btn.delete")
        self.i18n_binder.bind(self.merge_btn, "setText", "btn.merge")
        self.i18n_binder.bind(self.ignore_btn, "setText", "btn.ignore")
        self.i18n_binder.bind(self.separate_btn, "setText", "btn.separate")
        self.i18n_binder.bind(self.unmarked_btn, "setText", "btn.unmarked")
        
        self.i18n_binder.bind(self.auto_next_cb, "setText", "cb.auto_next")
        
        # placeholder / status line
        self.exclude_input.setPlaceholderText(self.i18n.t("input.exclude_placeholder"))
        self.i18n.changed.connect(lambda: self.exclude_input.setPlaceholderText(self.i18n.t("input.exclude_placeholder")))
        self.i18n.changed.connect(self.refresh_status_text)
        self.i18n_binder.bind(self.status, "setText", "status.please_select_folder")
        self.i18n_binder.bind(self.thumb_size_lbl, "setText", "label.thumb_size")

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
        self.compare_index = 0
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
        self.show_original_group = False
        self.show_processing_image = False
        self.visited = set()
        # Fixed issue: APP will not compare if only progress file is not exist.
        # Root cause: `progress_compare_file_size` and 'progress_similarity_tolerance' are not defined,
        #             causing the error.
        # Solution: Init these variables.
        self.progress_compare_file_size = 0
        self.progress_similarity_tolerance = 0
        
        # Restore configuration theme / language
        self.cfg = Config()
        self.apply_theme(self.cfg.get("ui.theme","system"))
        self.apply_language(self.cfg.get("ui.lang","zh-TW"))
        self.current_group_thumb_size = int(self.cfg.get("ui.thumbnail.max_size", 220))
        self.confirm_delete = (bool(self.cfg.get("behavior.confirm_delete", True)))
        self.compare_file_size = (bool(self.cfg.get("behavior.compare_file_size", True)))
        self.similarity_tolerance = int(self.cfg.get("behavior.similarity_tolerance", 5))

        # Group overview
        self.overview_cols = 4
        self.overview_rows = 3
        self.overview_page = 0
        self.group_preview_cache = OrderedDict()
        self.group_preview_cache_limit = 1024
        self.current_overview_thumb_size = 240   # overview thumb size 
        self.view_groups_update = True

        # Restore font size
        self.fontsize = int(self.cfg.get("ui.font_size", 12))
        self.apply_app_font(self.fontsize)

        self._register_shortcuts()
        self.button_controller()
        self.checkbox_controller()

        self._ui_mode = 'normal'       # 'normal' or 'processing'
        self.normal_host = None
        self.processing_host = None
        self.normal_body_layout = None
        self.processing_body_layout = None

        # Set init UI
        self._set_mode('processing')
        self._set_body_processing(QWidget())
        QTimer.singleShot(0, lambda: self.setFocus())  

    def _register_shortcuts(self):
        self._shortcuts = []

        def add(seq, handler):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)         # Active for APP
            sc.activated.connect(handler)
            self._shortcuts.append(sc)

        # File / Process control
        add("O", self.btn_action_select_folder)           # 0 Select folder
        add("S", self.btn_action_scan)                    # 1 Start scan
        add("P", self.btn_action_pause_processing)        # 2 Pause
        add("C", self.btn_action_continue_processing)     # 3 Continue
        add("Q", self.btn_action_exit_and_save)           # 4 Exit

        # Explorer
        add("F", partial(self.button_handler,"first"))             # 5 First group
        add("Home", partial(self.button_handler,"first"))          # 6 First group
        add("Left", partial(self.button_handler,"pre_group"))      # 7 Previous group
        add("Right", partial(self.button_handler,"next_group"))    # 8 Next group or compare
        add("Up", partial(self.button_handler,"pre_folder"))       # 9 Previous folder
        add("Down", partial(self.button_handler,"next_folder"))    # 10 Next folder
        add("L", partial(self.button_handler,"last"))              # 11 Last group
        add("End", partial(self.button_handler,"last"))            # 12 Last group

        # Delete unselected files
        add("Backspace", self.btn_action_delete_unchecked)   # 13 Delete unchecked files
        add("Delete", self.btn_action_delete_unchecked)      # 14 Delete unchecked files

        # 0~9 mapping checkbox
        for i in range(0, 10):
            sc = QShortcut(QKeySequence(str(i)), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(lambda i=i: self.toggle_checkbox(i - 1))
            self._shortcuts.append(sc)        # 15~24 File number and all
        
        # Show groups edit 
        add("Ctrl+S",self.btn_action_merge_selected)        # 25 Merge selected
        add("Ctrl+D",self.btn_action_separate_selected)     # 26 Separate selected
        add("Ctrl+I",self.btn_action_ignore_group)          # 27 Ignore selected
        add("Ctrl+U",self.btn_action_unmarked_selected)     # 28 Clear Mark
        add("B",self.show_overview)                         # 29 Back to Overview

    def clear_exclude_focus(self):
        self.setFocus()
        
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
            self.current_group_thumb_size = int(self.cfg.get("ui.thumbnail.max_size"))
            self.reload_thumbnails_for_current_group()
        if "behavior.confirm_delete" in changed_keys:
            self.confirm_delete = int(self.cfg.get("behavior.confirm_delete"))
        if "behavior.compare_file_size" in changed_keys or "behavior.similarity_tolerance" in changed_keys:
            self.compare_file_size = int(self.cfg.get("behavior.compare_file_size"))
            self.similarity_tolerance = self.cfg.get("behavior.similarity_tolerance")
            if self.stage=="done" or self.stage=="comparing":
                self.compare_index = 0
                self.groups = []
                self.duplicate_size = 0
                self.run_comparing()
    
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
        self.view_groups_update = True
        if self.stage == "comparing":
            self.scan_duplicates()
        else:
            self.show_group_detail()

    def btn_action_unmarked_selected(self):
        if not self.view_groups or self.current >= len(self.view_groups):
            return

        grp = self.view_groups[self.current]
        self.constraints.clear_constraints_for_group(grp)
        self.constraints.save_constraints()
        self.view_groups_update = True
        self.show_group_detail()

    def btn_action_separate_selected(self):
        sel_path = self.get_selected_paths()
        for idx_o in range(0,len(sel_path)):
            for idx_i in range(0,len(self.view_groups[self.current])):
                if sel_path[idx_o]!=self.view_groups[self.current][idx_i]:
                    self.constraints.add_cannot_link(sel_path[idx_o], self.view_groups[self.current][idx_i])
        self.constraints.save_constraints()

        if not self.forward:
            self.current -= 1

        if self.current>=len(self.view_groups):
            self.current = len(self.view_groups)-1
        if self.current < 0:
            self.current = 0
        
        self.view_groups_update = True
        if self.stage == "comparing":
            self.scan_duplicates()
        else:
            self.show_group_detail()

    def btn_action_ignore_group(self):
        igr = self.view_groups[self.current]
        self.constraints.add_ignore_files(igr)
        self.constraints.save_constraints()

        if not self.forward:
            self.current -= 1

        if self.current>=len(self.view_groups):
            self.current = len(self.view_groups)-1
        if self.current < 0:
            self.current = 0

        self.view_groups_update = True
        if self.stage == "comparing":
            self.scan_duplicates()
        else:
            self.show_group_detail()
    
    # -------- Implement Hot-apply --------
    def apply_theme(self, theme: str):
        # If support QSS / dark-light
        if theme == "dark":
            QApplication.setStyle("Fusion")
            # TODO: Load dark.qss
        elif theme == "light":
            QApplication.setStyle("Fusion")
            # TODO：Load light.qss
        else:
            # system
            QApplication.setStyle(None)

    def apply_language(self, lang_code: str):
        # "auto" using system language
        self.i18n.set_locale(lang_code)
        self.i18n_binder.retranslate()
        self.refresh_status_text()
        self.checkbox_controller()
        self.exclude_input.setPlaceholderText(self.i18n.t("input.exclude_placeholder"))
        # Save to QSettings
        self.settings.setValue("locale", lang_code)

    def retranslate_ui_texts(self):
        if self.action == "show_group":
            self.show_group_detail_advance()
        if self.action == "show_overview":
            self.show_overview()

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
        if self.action == "show_overview":
            self.show_overview()

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
                full_path = "\\\\" + full_path[2:]
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

        self._set_mode('processing')
        self._set_body_processing(QWidget())
        
        self.progress.setVisible(False)
        self.path_str.setText(self.folder)
        
        self.button_controller()

        self.phashes = {}
        self.groups = []
        self.image_paths = []
        self.current = 0
        self.overview_page = 0
        self.progress_file = os.path.join(self.folder, f"{PROGRESS_FILE}")
        self.filelist_file = os.path.join(self.folder, f"{FILELIST_FILE}")
        self.exceptions_file = os.path.join(self.folder,f"{EXCEPTIONS_FILE}")
        self.compare_index = 0
        self.visited = set()
        self.constraints = ConstraintsStore(scan_folder=self.folder)
        self.status.setText(self.i18n.t("status.press_scan_button"))
        self.view_groups_update = True
        self.load_filelist()
        self.load_exceptions()
        self.load_progress()
        self.checkbox_controller()

    def btn_action_scan(self):
        self.action = "collecting"
        #self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
        QApplication.processEvents()

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
                # Settings of "compare file size" or "similarity tolerance" is different.
                # Force to collecting stage
                if self.progress_compare_file_size!=self.compare_file_size or self.progress_similarity_tolerance!=self.similarity_tolerance:
                    self.stage = "collecting"
                    self.compare_index = 0
                    self.groups = []
                    self.duplicate_size = 0
                    self.current = 0
                
                self.scan_duplicates()
                return
        
        # Scan folder
        original_stage = self.stage
        self.stage = "collecting"
        
        self.button_controller()
        self.checkbox_controller()

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
            completed = 0;
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
                self.compare_index = 0
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
        self.button_controller()
        self._set_mode('processing')
        self._set_body_processing(QWidget())
        self.progress.setMaximum(len(self.image_paths))
        self.progress.setValue(len(self.phashes))
        self.progress.setVisible(True)
        
        self.checkbox_controller()
        
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
                time.sleep(0.1)
                if self.paused:
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
                    rel_path = os.path.relpath(p, self.folder).replace("\\","/").lower()
                    try:
                        h = f.result()
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
                        if self.show_processing_image:
                            self.show_current_processing_image(f"{self.i18n.t('msg.hashing')}",rel_path)
                        else:
                            self._set_mode('processing')
                            self._set_body_processing(QWidget())
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
        self.button_controller()
        self.checkbox_controller()
        self.run_pairwise_comparing()
    
    def run_pairwise_comparing(self):
        self._set_mode('processing')
        self._set_body_processing(QWidget())
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
        self.progress.setValue(self.compare_index)

        MAX_LOOKAHEAD = math_clamp(8*(self.similarity_tolerance+1) ** 2, 64, 384)
        completed = 0
        
        t_report = int(self.similarity_tolerance)     # UI threshold
        delta    = min(3, t_report // 2)              # t/2，max 3
        t_link   = t_report + delta                   # edge
        
        for i, (p1, h1) in enumerate(items[self.compare_index:], start=self.compare_index):
            time.sleep(0.1)
            completed += 1            
            self.compare_index = i
            self.progress.setValue(self.compare_index)
            if p1 in self.visited:
                continue
            elapsed = time.time() - start_compare
            eta = (elapsed / (completed)) * (total - (i+1))
            eta_str = time.strftime('%H:%M:%S', time.gmtime(eta))
            self.status.setText(self.i18n.t("status.compare_eta", eta=eta_str, cur = i+1, total = total, remaining = self.compare_index, groups = len(new_grps), cur_file = os.path.basename(p1)))
            if self.paused:
                self.status.setText(self.i18n.t("status.comparison_pause"))
                self.groups = new_grps
                self.save_progress(stage="comparing", extra={"compare_index": self.compare_index})
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
                    self.status.setText(self.i18n.t("status.comparison_pause"))
                    self.groups = new_grps
                    self.save_progress(stage="comparing", extra={"compare_index": self.compare_index})
                    return
                if self.pertimes_processevent(0.5):
                    if self.show_processing_image:
                        self.show_comparing_pair(p1,p2)
                    else:
                        self._set_mode('processing')
                        self._set_body_processing(QWidget())
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

                if self.compare_index < len(items):
                    if self.auto_next_cb.isChecked():
                        continue
                    else:
                        self.save_progress(stage="comparing", extra={"compare_index": self.compare_index})
                        self.view_groups_update = True
                        self.show_group_detail()
                        return

        self.sort_group(new_grps)
        self.compare_index = len(self.phashes)
        self.progress.setValue(total)
        QApplication.processEvents()
        self.stage = "done"
        self.visited = set()
        self.save_progress(stage="done")
        self.view_groups_update = True        
        self.show_overview()

    def scan_duplicates(self):
        #Resume stage
        if self.stage == "done":
            self.compare_index = len(self.phashes)
            self.show_overview()
            return
        elif self.stage == "comparing":
            self.run_comparing()
            return
        else:
            self.status.setText(self.i18n.t("status.resuming_hashing"))
            self.run_hashing()
            return                        

    def show_current_processing_image(self, label, path):
        full_path = self.get_full_path(path)
        try:
            cont = QWidget()
            v = QVBoxLayout(cont)
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

            #self.scroll.setWidget(cont)
            self._set_mode('processing')
            self._set_body_processing(cont)
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

            #self.scroll.setWidget(cont)
            self._set_mode('processing')
            self._set_body_processing(cont)
        except Exception as e:
            print(f"[Error] Failed to show comparing images: {e}")
    
    def show_overview(self):
        self.show_overview_g1b1()

    def show_overview_g1b1(self):
        # 1.Prepare data
        self.action = "show_overview"
        if self.view_groups_update:
            # Update view_groups if need
            if self.show_original_group:
                self.view_groups = self.groups
            else:
                self.view_groups, self.view_summary = self.constraints.apply_to_all_groups(self.groups)
            self.view_groups_update = False
            self.duplicate_size = self.count_duplicate_size(self.view_groups)

        # 2.Build UI host (head and body) and set button, checkbox
        self._set_mode('normal')
        self.checkbox_controller()
        self.button_controller()
        self.show_group_back_btn.setVisible(False)  # Overview don't support back to overview

        # 2.Count page, prevent -1
        cols = self.overview_cols
        rows = self.overview_rows
        per_page = max(1, cols * rows)
        total_groups = len(self.view_groups)
        max_page = (total_groups + per_page - 1) // per_page
        if max_page == 0:
            self.overview_page = 0
        else:
            self.overview_page = max(0, min(self.overview_page, max_page - 1))
        start = self.overview_page * per_page
        end = min(start + per_page, total_groups)

        # 3. Build images area
        cont = QWidget()
        v = QVBoxLayout(cont)
        v.setSpacing(8)
        v.setContentsMargins(6, 6, 6, 6)

        self.group_info.setText(
            self.i18n.t("label.groups_overview", total=max_page, page=(self.overview_page + 1 if max_page else 0))
        )

        grid = QGridLayout()
        grid.setSpacing(8)
        v.addLayout(grid)

        if not self.view_groups:
            tip = QLabel(self.i18n.t("label.no_groups"))
            tip.setWordWrap(True)
            tip.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            v.addWidget(tip)
            self._set_body_normal(cont)
            self.refresh_status_text()
            self._set_slider_mode("show_overview")
            self.show_group_back_btn.setVisible(False)
            return

        # Prepare cache
        self._ovw_labels = []    # Image QLabel
        self._ovw_qimages = []   # Original QImage
        self._ovw_tiles   = []   # dict：widget/img/count/gi

        edge = int(max(120, min(320, getattr(self, "current_overview_thumb_size", 240))))
        indices = list(range(start, end))

        # Add body 
        self._set_body_normal(cont)
        self.refresh_status_text()
        self._set_slider_mode("show_overview")

        # Build shell (clickable, images counter), show image later
        for i, gi in enumerate(indices):
            r, c = divmod(i, cols)
            tile_w, img_lbl, count_lbl = self._make_overview_tile_shell(gi, edge)
            grid.addWidget(tile_w, r, c)
            self._ovw_labels.append(img_lbl)
            self._ovw_qimages.append(None)
            self._ovw_tiles.append({
                "widget": tile_w,
                "img": img_lbl,
                "count": count_lbl,
                "group_index": gi,
            })

        # Show images one by one
        self._ovw_build_gen = getattr(self, "_ovw_build_gen", 0) + 1
        gen = self._ovw_build_gen

        def _fill_one(i=0):
            if gen != self._ovw_build_gen:
                return  # Cancel previous not finished loop
            if i >= len(indices):
                # If all images are present, put high quality images
                cur = int(getattr(self,"current_overview_thumb_size",240))
                self._resize_overview_thumbs(cur, Qt.SmoothTransformation)
                return

            gi = indices[i]
            self._load_overview_tile_image(i, gi, edge)      # Load images

            # Next
            QTimer.singleShot(10, lambda: _fill_one(i + 1))
        
        self._resize_overview_thumbs(edge, Qt.FastTransformation)
        _fill_one(0)

        self.show_group_back_btn.setVisible(False)
    
    def _make_overview_tile_shell(self, gi: int, edge: int):
        # Build a shell and image counter first
        cont = QWidget()
        vbox = QVBoxLayout(cont)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        # Image shell
        img_lbl = QLabel()
        img_lbl.setMinimumSize(edge, edge)
        img_lbl.setMaximumSize(edge, edge)
        img_lbl.setAlignment(Qt.AlignCenter)
        img_lbl.setStyleSheet("background:#2e2e2e; border:1px solid #ddd;")
        img_lbl.setText(self.i18n.t("label.loading", default="Loading…"))
        img_lbl.setCursor(Qt.PointingHandCursor)

        # Image counter
        count = len(self.view_groups[gi]) if 0 <= gi < len(self.view_groups) else 0
        count_lbl = QLabel(self.i18n.t("label.group_tile", count=count))
        count_lbl.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        count_lbl.setMinimumWidth(edge)
        count_lbl.setMaximumWidth(edge)
        count_lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        count_lbl.setCursor(Qt.PointingHandCursor)

        fm = count_lbl.fontMetrics()
        count_lbl.setFixedHeight(int(fm.height() * 1.4))

        vbox.addWidget(img_lbl)
        vbox.addWidget(count_lbl)

        # Click image to show group detail
        def _go_detail(_ev=None, _gi=gi):
            self.open_group(_gi)

        for w in (img_lbl, count_lbl, cont):
            w.mousePressEvent = _go_detail

        return cont, img_lbl, count_lbl

    def _load_overview_tile_image(self, slot_index: int, group_index: int, edge: int):
        # Use group_index of groups to slot_index img_lbl
        edge = int(getattr(self, "current_overview_thumb_size", edge))

        if not (0 <= slot_index < len(getattr(self, "_ovw_tiles", []))):
            return
        tile = self._ovw_tiles[slot_index]
        img_lbl = tile.get("img")
        gi_in_tile = tile.get("group_index")

        if img_lbl is None or gi_in_tile != group_index:
            return

        if not (0 <= group_index < len(self.view_groups)):
            return
        members = self.view_groups[group_index]
        if not members:
            return
        rep_rel = members[0]
        full_path = self.get_full_path(rep_rel)

        # Cache
        cache_key = full_path
        qimg = None

        try:
            # 1) Lookup cache
            cached = self.group_preview_cache.get(cache_key)
            if isinstance(cached, QImage) and not cached.isNull():
                qimg = cached
                # Hit: Move to end 
                self.group_preview_cache.move_to_end(cache_key, last=True)
            else:
                # Not Hit: Get thumb
                if hasattr(self, "_load_preview_qimage"):
                    qimg = self._load_preview_qimage(full_path, max(edge * 2, 240))
                else:
                    from PIL import ImageOps
                    im = image_load_for_thumb(full_path, want_min_edge=max(edge * 2, 240))
                    im = ImageOps.exif_transpose(im)
                    if im.mode != "RGBA":
                        im = im.convert("RGBA")
                    qimg = QImage(im.tobytes("raw", "RGBA"), im.size[0], im.size[1], QImage.Format_RGBA8888)

                # Add to cache
                if isinstance(qimg, QImage) and not qimg.isNull():
                    self.group_preview_cache[cache_key] = qimg
                    # Drop oldest cache if cache full
                    while len(self.group_preview_cache) > getattr(self, "group_preview_cache_limit", 256):
                        try:
                            self.group_preview_cache.popitem(last=False)
                        except Exception:
                            break

            self._ovw_qimages[slot_index] = qimg if (isinstance(qimg, QImage) and not qimg.isNull()) else None

            if isinstance(qimg, QImage) and not qimg.isNull():
                pm = QPixmap.fromImage(qimg).scaled(edge, edge, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                img_lbl.setStyleSheet("")   # Remove placeholder
                img_lbl.setText("")         # Remove "Loading…"
                img_lbl.setPixmap(pm)
            else:
                # Display error background color, if read image fail
                img_lbl.setStyleSheet("background:#fff0f0; border:1px solid #e0b4b4;")
                img_lbl.setText(self.i18n.t("err.fail_to_load_images_short", default="Load failed"))

        except Exception:
            img_lbl.setStyleSheet("background:#fff0f0; border:1px solid #e0b4b4;")
            img_lbl.setText(self.i18n.t("err.fail_to_load_images_short", default="Load failed"))
    
    def _resize_overview_thumbs(self, edge: int, quality):
        if not hasattr(self, "_ovw_labels"):
            return
        for i, (lbl, qimg) in enumerate(zip(self._ovw_labels, self._ovw_qimages)):
            if lbl is None:
                continue
            
            if qimg is not None:
                # If cached: re-scaled pixmap
                pm = QPixmap.fromImage(qimg).scaled(
                    edge, edge, Qt.KeepAspectRatio, quality
                )
                lbl.setPixmap(pm)
                lbl.setText("")
            else:
                # If not cached: display loading or fail
                lbl.setPixmap(QPixmap())  # Clear old image
                lbl.setText(self.i18n.t("label.loading", default="Loading…"))
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet("background:#2e2e2e; border:1px solid #ddd;")

            # Resize label with edge to prevent cut image
            lbl.setMinimumSize(edge, edge)
            lbl.setMaximumSize(edge, edge)
            lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

            # 🔸 Sync image counter label width with edge
            if hasattr(self, "_ovw_tiles") and i < len(self._ovw_tiles):
                count_lbl = self._ovw_tiles[i].get("count")
                if count_lbl:
                    count_lbl.setMinimumWidth(edge)
                    count_lbl.setMaximumWidth(edge)

                tw = self._ovw_tiles[i].get("widget")
                if tw:
                    tw.setMinimumWidth(edge)

    def btn_action_overview_first_page(self):
        self.overview_page = 0
        self.show_overview()

    def btn_action_overview_prev_page(self):
        if self.overview_page > 0:
            self.overview_page -= 1
            self.show_overview()

    def btn_action_overview_next_page(self):
        cols = self.overview_cols
        rows = self.overview_rows
        per_page = cols * rows
        max_page = (max(len(self.view_groups) - 1, 0)) // per_page
        if self.overview_page < max_page:
            self.overview_page += 1
            self.show_overview()

    def btn_action_overview_last_page(self):
        cols = self.overview_cols
        rows = self.overview_rows
        per_page = cols * rows
        max_page = (max(len(self.view_groups) - 1, 0)) // per_page
        self.overview_page = max_page
        self.show_overview()

    def show_group_detail(self):
        if self.view_groups_update:
            if self.show_original_group or self.stage != "done":
                self.view_groups = self.groups
            else:
                self.view_groups, self.view_summary = self.constraints.apply_to_all_groups(self.groups)
            self.view_groups_update = False
            self.duplicate_size = self.count_duplicate_size(self.view_groups)

        self.show_group_detail_advance()

    def _set_mode(self, mode: str):
        self._group_host_build()

        if mode == 'processing':
            self._ui_mode = 'processing'
            self.normal_host.hide()
            self.processing_host.show()
        elif mode == 'normal':
            self._ui_mode = 'normal'
            self.processing_host.hide()
            self.normal_host.show()
        else:
            print("[Error] mode is not defined")

    def _group_host_build(self):
        # Make sure build once only
        if getattr(self, "_dual_host_ready", False):
            return

        cw = self.centralWidget()
        if cw is None or not isinstance(cw, QWidget):
            holder = QWidget()
            root = QVBoxLayout(holder)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)
            self.setCentralWidget(holder)
        else:
            holder = cw
            root = holder.layout()
            if root is None:
                root = QVBoxLayout(holder)
                root.setContentsMargins(0, 0, 0, 0)
                root.setSpacing(0)

        # ---------- normal_host（head + body） ----------
        self.normal_host = QWidget()
        normal_layout = QVBoxLayout(self.normal_host)
        normal_layout.setContentsMargins(6, 6, 6, 6)
        normal_layout.setSpacing(8)

        # head
        self.group_host_head = QWidget()
        a_box = QVBoxLayout(self.group_host_head)
        a_box.setContentsMargins(0, 0, 0, 0)
        a_box.setSpacing(6)

        # row1: back、information、slider
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)

        row1.addWidget(self.show_group_back_btn)

        self.group_info = QLabel("")
        row1.addWidget(self.group_info)

        self.thumb_size_lbl = getattr(self, "thumb_size_lbl", QLabel("Thumb"))
        row1.addWidget(self.thumb_size_lbl)

        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(120, 320)
        self.size_slider.setSingleStep(4)
        self.size_slider.setPageStep(32)
        self.size_slider.setMinimumWidth(300)
        row1.addWidget(self.size_slider)

        self.size_val_lbl = QLabel("")
        row1.addWidget(self.size_val_lbl)

        # row2: Buttons (Delete/Mark Same/Mark Different/Ignore/Clear Mark）
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        for b in (self.delete_btn, self.merge_btn, self.separate_btn, self.ignore_btn, self.unmarked_btn):
            row2.addWidget(b)
        row2.addStretch(1)

        a_box.addLayout(row1)
        a_box.addLayout(row2)
        
        normal_layout.addWidget(self.group_host_head)

        # body
        normal_body_holder = QWidget()
        self.normal_body_layout = QVBoxLayout(normal_body_holder)
        self.normal_body_layout.setContentsMargins(0, 0, 0, 0)
        self.normal_body_layout.setSpacing(0)
        normal_layout.addWidget(normal_body_holder, 1)

        # ---------- processing_host（only body） ----------
        self.processing_host = QWidget()
        proc_layout = QVBoxLayout(self.processing_host)
        proc_layout.setContentsMargins(6, 6, 6, 6)
        proc_layout.setSpacing(8)

        processing_body_holder = QWidget()
        self.processing_body_layout = QVBoxLayout(processing_body_holder)
        self.processing_body_layout.setContentsMargins(0, 0, 0, 0)
        self.processing_body_layout.setSpacing(0)
        proc_layout.addWidget(processing_body_holder, 1)

        # Add normal and processing to root
        root.addWidget(self.normal_host, 1)
        root.addWidget(self.processing_host, 1)

        # Show normal currently
        self.normal_host.show()
        self.processing_host.hide()

        self._dual_host_ready = True

    def _apply_detail_resize_once(self, val: int, quality):
        # Resize show group detail here, don't repeat resize in handler
        self._resize_thumbs(val, quality)

    def _set_slider_mode(self, mode: str):
        if mode == "show_overview":
            try:
                self.size_slider.valueChanged.disconnect()
            except TypeError:
                pass

            self.size_slider.blockSignals(True)
            self.size_slider.setRange(120, 320)
            self.current_overview_thumb_size = int(getattr(self, "current_overview_thumb_size", 240))
            self.size_slider.setValue(self.current_overview_thumb_size)

            if hasattr(self, "size_val_lbl"):
                self.size_val_lbl.setText(str(self.current_overview_thumb_size))
            self.size_slider.blockSignals(False)

            if not hasattr(self, "_overview_resize_timer"):
                self._overview_resize_timer = QTimer(self)
                self._overview_resize_timer.setSingleShot(True)
                self._overview_resize_timer.setInterval(120)

            def on_overview_changed(x):
                x = max(120, min(320, int(x)))
                self.current_overview_thumb_size = x
                if hasattr(self, "size_val_lbl"):
                    self.size_val_lbl.setText(str(x))

                self._resize_overview_thumbs(x, Qt.FastTransformation)

                self._overview_resize_timer.stop()
                try:
                    self._overview_resize_timer.timeout.disconnect()
                except TypeError:
                    pass

                def _do_smooth():
                    if getattr(self, "action", "") == "show_overview":
                        self._resize_overview_thumbs(self.current_overview_thumb_size, Qt.SmoothTransformation)

                self._overview_resize_timer.timeout.connect(_do_smooth)
                self._overview_resize_timer.start()

            self.size_slider.valueChanged.connect(on_overview_changed)

        else:
            try:
                self.size_slider.valueChanged.disconnect()
            except TypeError:
                pass
            self.size_slider.blockSignals(True)
            self.size_slider.setRange(400, 1000)
            self.current_group_thumb_size = int(getattr(self, "current_group_thumb_size", 400))
            self.size_slider.setValue(self.current_group_thumb_size)
            if hasattr(self, "size_val_lbl"):
                self.size_val_lbl.setText(str(self.current_group_thumb_size))
            self.size_slider.blockSignals(False)

            if not hasattr(self, "_thumb_resize_timer"):
                self._thumb_resize_timer = QTimer(self)
                self._thumb_resize_timer.setSingleShot(True)
                self._thumb_resize_timer.setInterval(120)

            def on_detail_changed(x):
                x = max(400, min(1000, int(x)))
                self.current_group_thumb_size = x
                if hasattr(self, "size_val_lbl"):
                    self.size_val_lbl.setText(str(x))

                self._apply_detail_resize_once(x, Qt.FastTransformation)

                self._thumb_resize_timer.stop()
                try:
                    self._thumb_resize_timer.timeout.disconnect()
                except TypeError:
                    pass
                self._thumb_resize_timer.timeout.connect(
                    lambda: self._apply_detail_resize_once(self.current_group_thumb_size, Qt.SmoothTransformation)
                )
                self._thumb_resize_timer.start()

            self.size_slider.valueChanged.connect(on_detail_changed)

    def _set_body_normal(self, w: QWidget):
        # 放到 normal body
        lay = self.normal_body_layout
        while lay.count():
            it = lay.takeAt(0)
            ww = it.widget()
            if ww:
                ww.setParent(None)
        lay.addWidget(w)

    def _set_body_processing(self, w: QWidget):
        # 放到 processing body
        lay = self.processing_body_layout
        while lay.count():
            it = lay.takeAt(0)
            ww = it.widget()
            if ww:
                ww.setParent(None)
        lay.addWidget(w)

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

    def _load_preview_qimage(self, full_path: str, target_edge: int) -> QImage:
        try:
            im = image_load_for_thumb(full_path, want_min_edge=target_edge)
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            qimg = QImage(
                im.tobytes("raw", "RGBA"),
                im.size[0], im.size[1],
                QImage.Format_RGBA8888
            )
            return qimg
        except Exception as e:
            print(f"[Error] Overview preview load failed {full_path}: {e}")
            return QImage()
    
    def _groups_info_update(self, grp):
        # Clear cache
        self.group_checkboxes = []
        self._thumb_labels = []
        self._thumb_qimages = []
        self._thumb_styles = []
        is_marked = False
        
        relation = self.query_group_constraints(grp)

        cont = QWidget()
        v = QHBoxLayout(cont)
        v.setSpacing(8)
        v.setContentsMargins(0, 0, 0, 0)
        
        group_full_paths = [self.get_full_path(p) for p in grp]
        common_prefix = os.path.commonpath(group_full_paths).replace("\\", "/").lower()
        if len(common_prefix) > 0 and not common_prefix.endswith("/"):
            common_prefix += "/"
        
        for idx, p in enumerate(grp, start=1):
            hb = QVBoxLayout()
            hb.setSpacing(6)
            hb.setContentsMargins(0, 0, 0, 8)

            full_path = self.get_full_path(p).replace("\\", "/").lower()
            # Thumb（Left）
            try:
                # Load image (PIL）and rotation and zoom in/out
                base_size = max(self.current_group_thumb_size, 1400)
                img = image_load_for_thumb(full_path, want_min_edge=max(self.current_group_thumb_size, 1400))

                # If image in ignore list, transform to gray
                if relation=="ignored":
                    try:
                        img = ImageOps.grayscale(img)
                    except Exception:
                        img = img.convert("L")

                # Build QImage / QPixmap ----------------
                qimg = image_pil_to_qimage(img)
                pm   = QPixmap.fromImage(qimg)
                target_w = min(self.current_group_thumb_size, pm.width())
                target_h = min(self.current_group_thumb_size, pm.height())
                pixmap = pm.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

                # Dark for can't-link
                style = 'normal'

                if relation=="different":
                    style = 'dark'
                    painter = QPainter(pixmap)
                    painter.fillRect(pixmap.rect(), QColor(0, 0, 0, 110))  # Range 80~150 
                    painter.end()
                
                # Display
                thumb_lbl = QLabel()
                thumb_lbl.setAlignment(Qt.AlignCenter)
                thumb_lbl.setPixmap(pixmap)
                thumb_lbl.mousePressEvent = lambda e, fp=full_path: self.show_image_dialog(fp)
                thumb_lbl.setCursor(Qt.PointingHandCursor)
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
                if relation=="same":
                    cb = QCheckBox(f"{self.i18n.t('msg.must')}")
                elif relation=="different":
                    cb = QCheckBox(f"{self.i18n.t('msg.separate')}")
                elif relation=="ignored":
                    cb = QCheckBox(f"{self.i18n.t('msg.ignore')}")
                elif relation=="mix":
                    cb = QCheckBox(f"{self.i18n.t('msg.mix')}")
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
            btn.clicked.connect(lambda _, fp=full_path: self.open_in_explorer(fp))
            v_info.addWidget(btn)
            v_info.addStretch(1)

            hb.addLayout(v_info)
            v.addLayout(hb)

        if relation!="none":
            self.unmarked_btn.setEnabled(True)
        else:
            self.unmarked_btn.setEnabled(False)
        v.addStretch(1)
        self.scroll.setWidget(cont)
    
    def open_group(self, index: int):
        self.current = index
        self.show_group_detail()

    def show_group_detail_advance(self):
        self.action = "show_group"
        self._set_mode('normal')
        if hasattr(self, "_overview_resize_timer") and self._overview_resize_timer.isActive():
            self._overview_resize_timer.stop()
            try:
                self._overview_resize_timer.timeout.disconnect()
            except TypeError:
                pass

        show_groups = getattr(self, "view_groups", self.groups) or []
        if not show_groups:
            self.group_info.setText(self.i18n.t("label.group_empty"))
            self._set_body_normal(QWidget())
            self._set_slider_mode("detail")
            return

        if self.current >= len(show_groups):
            self.current = len(show_groups) - 1
        if self.current < 0:
            self.current = 0

        cols = max(1, int(self.overview_cols))
        rows = max(1, int(self.overview_rows))
        per_page = cols * rows
        self.overview_page = self.current // max(1, per_page)

        grp = show_groups[self.current]

        if self.compare_index >= len(self.phashes):
            label_text = self.i18n.t("label.group_progress",
                                    current=self.current + 1,
                                    total=len(show_groups),
                                    images=len(grp))
        else:
            label_text = self.i18n.t("label.group_found",
                                    current=self.current + 1,
                                    images=len(grp))
        self.group_info.setText(label_text)
        self.show_group_back_btn.setVisible(True)
        self.button_controller()
        
        vp = self.scroll.viewport() if hasattr(self, "scroll") else None
        if vp: vp.setUpdatesEnabled(False)
        try:
            self._groups_info_update(grp)
        finally:
            if vp: vp.setUpdatesEnabled(True)

        # Add show group detail to host body
        self._set_body_normal(self.scroll)

        self._set_slider_mode("detail")

    def show_image_dialog(self, image_path):
        dialog = ImageDialog(image_path)
        dialog.setModal(False)
        dialog.show()
        self.dialogs.append(dialog)
    
    def button_controller(self):
        if self.action=="init":
            self.open_btn.setEnabled(True)
            self._shortcuts[0].setEnabled(True)

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

            self.show_group_back_btn.setEnabled(False)
            self._shortcuts[29].setEnabled(False)
            return

        if self.action=="select_folder":
            self.open_btn.setEnabled(True)
            self._shortcuts[0].setEnabled(True)

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

            self.show_group_back_btn.setEnabled(False)
            self._shortcuts[29].setEnabled(False)
            return

        if self.action=="collecting":
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

            self.show_group_back_btn.setEnabled(False)
            self._shortcuts[29].setEnabled(False)
            return

        if self.action=="scan" or self.action=="hashing" or self.action=="comparing":
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

            self.show_group_back_btn.setEnabled(False)
            self._shortcuts[29].setEnabled(False)
            return

        if self.action=="pause":
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

            self.show_group_back_btn.setEnabled(False)
            self._shortcuts[29].setEnabled(False)
            return

        if self.action=="continue":
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

            self.show_group_back_btn.setEnabled(False)
            self._shortcuts[29].setEnabled(False)
            return

        if self.action=="resuming":
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

            self.show_group_back_btn.setEnabled(False)
            self._shortcuts[29].setEnabled(False)
            return

        if self.action=="show_group":
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

            self.first_btn.setText(self.i18n.t("btn.first"))
            self.prev_btn.setText(self.i18n.t("btn.prev"))
            self.prev_folder_btn.show()
            self.prev_folder_btn.setText(self.i18n.t("btn.prev_folder"))
            self.next_btn.setText(self.i18n.t("btn.next"))
            self.next_folder_btn.show()
            self.next_folder_btn.setText(self.i18n.t("btn.next_folder"))
            self.last_btn.setText(self.i18n.t("btn.last"))

            self.delete_btn.show()
            self.merge_btn.show()
            self.separate_btn.show()
            self.ignore_btn.show()
            self.unmarked_btn.show()

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

            self.show_group_back_btn.setEnabled(self.stage=="done")
            self._shortcuts[29].setEnabled(self.stage=="done")
            return
        if self.action=="show_overview":
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
            
            self.first_btn.setText(self.i18n.t("btn.first_page"))
            self.prev_btn.setText(self.i18n.t("btn.prev_page"))
            self.prev_folder_btn.hide()
            self.next_btn.setText(self.i18n.t("btn.next_page"))
            self.next_folder_btn.hide()
            self.last_btn.setText(self.i18n.t("btn.last_page"))

            self.delete_btn.hide()
            self.merge_btn.hide()
            self.separate_btn.hide()
            self.ignore_btn.hide()
            self.unmarked_btn.hide()

            cols = self.overview_cols
            rows = self.overview_rows
            per_page = cols * rows
            max_page = (max(len(self.view_groups) - 1, 0)) // per_page

            self.first_btn.setEnabled(len(self.view_groups)>1 and self.overview_page>0)
            self._shortcuts[5].setEnabled(len(self.view_groups)>1 and self.overview_page>0)
            self._shortcuts[6].setEnabled(len(self.view_groups)>1 and self.overview_page>0)

            self.prev_btn.setEnabled(len(self.view_groups)>1 and self.overview_page>0)
            self._shortcuts[7].setEnabled(len(self.view_groups)>1 and self.overview_page>0)

            self.prev_folder_btn.setEnabled(False)
            self._shortcuts[9].setEnabled(False)
            
            self.next_btn.setEnabled(self.stage!="done" or (len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done"))
            self._shortcuts[8].setEnabled(self.stage!="done" or (len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done"))
            
            self.next_folder_btn.setEnabled(False)
            self._shortcuts[10].setEnabled(False)

            self.last_btn.setEnabled(len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done")
            self._shortcuts[11].setEnabled(len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done")
            self._shortcuts[12].setEnabled(len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done")

            self.show_group_back_btn.setEnabled(False)
            self._shortcuts[29].setEnabled(False)
            return

    def button_handler(self, func):
        if self.action == "show_overview":
            match func:
                case "first":
                    self.btn_action_overview_first_page()
                case "pre_folder":
                    self.show_overview()
                case "pre_group":
                    self.btn_action_overview_prev_page()
                case "next_group":
                    self.btn_action_overview_next_page()
                case "next_folder":
                    self.show_overview()
                case "last":
                    self.btn_action_overview_last_page()
                case _:
                    print("[Error] Undefined function for show_overview")
        elif self.action == "show_group":
            match func:
                case "first":
                    self.btn_action_first_group()
                case "pre_folder":
                    self.btn_action_prev_compare_folder()
                case "pre_group":
                    self.btn_action_prev_group()
                case "next_group":
                    self.btn_action_next_group_or_compare()
                case "next_folder":
                    self.btn_action_next_compare_folder()
                case "last":
                    self.btn_action_last_group()
                case _:
                    print("[Error] Undefined function for show_group")
        else:
            print("[Error] Undefined action")
    
    def checkbox_controller(self):
        if self.action=="show_group" or self.action=="show_overview":
            self.display_img_dynamic_cb.setText(self.i18n.t("cb.display_original_groups"))
            self.display_img_dynamic_cb.setChecked(self.show_original_group)  
        else:
            self.display_img_dynamic_cb.setText(self.i18n.t("cb.display_img"))
            self.display_img_dynamic_cb.setChecked(self.show_processing_image)

    def checkbox_handler(self):
        if self.stage=="done":
            self.show_original_group = not self.show_original_group
            self.view_groups_update = True
            if self.action == "show_group":
                self.show_group_detail()
            elif self.action == "show_overview":
                self.show_overview()
        else:
            self.show_processing_image = not self.show_processing_image

    def btn_bounce_start(self, btn):
        r = btn.geometry()
        anim = QPropertyAnimation(btn, b"geometry", self)
        anim.setDuration(1000)
        anim.setStartValue(r)
        anim.setKeyValueAt(0.5, QRect(r.x()-5, r.y()-5, r.width()+10, r.height()+10))
        anim.setEndValue(r)
        anim.setLoopCount(-1)
        anim.start()
        btn._bounce_anim = anim

    def btn_bounce_stop(self, btn: QPushButton):
        if hasattr(btn, "_bounce_anim"):
            btn._bounce_anim.stop()
            del btn._bounce_anim
            r = btn.geometry()
            btn.setGeometry(r.x()+5, r.y()+5, r.width()-10, r.height()-10)

    def btn_action_continue_processing(self):
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

            if not self.forward:
                self.current -= 1

            if new_groups and self.current >= len(new_groups):
                self.current = len(new_groups) - 1      # Over groups index
            elif not new_groups or self.current<0:
                self.current = 0                        # Groups is empty

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

        self.view_groups_update = True
        if self.stage == "comparing":
            self.scan_duplicates()
        else:
            self.show_group_detail()

    def toast(self, text: str):
        QMessageBox.information(self, self.i18n.t("msg.info", default="Info"), text)

    def btn_action_first_group(self):        
        if self.current > 0:
            self.current = 0
        self.forward = True
        self.show_group_detail()

    def btn_action_prev_group(self):
        if self.current > 0:
            self.current -= 1
        self.forward = False
        self.show_group_detail()
    
    def btn_action_prev_compare_folder(self):
        if self.current > 0:
            curkey = os.path.dirname(self.view_groups[self.current][0])
            for i in range(1, self.current+1): 
                prekey = os.path.dirname(self.view_groups[self.current-i][0])             
                if prekey != curkey:
                    self.current = self.current-i
                    break
        self.forward = False
        self.show_group_detail()

    def btn_action_next_compare_folder(self):
        if self.current < len(self.view_groups)-1:
            curkey = os.path.dirname(self.view_groups[self.current][0])
            for i in range(1, len(self.view_groups)-self.current):
                nextkey = os.path.dirname(self.view_groups[self.current+i][0])
                if nextkey != curkey:
                    self.current = self.current+i
                    break
        self.forward = True
        self.show_group_detail()
    
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
                    self.overview_page = data.get("overview_page",0)
                    self.show_processing_image = data.get("show_processing_image", False)
                    self.auto_next_cb.setChecked(data.get("auto_next_group", False))
                    self.show_original_group = data.get("show_original_groups",False)

                    self.show_processing_image = data.get("show_processing_image", False)
                    self.auto_next_group = data.get("auto_next_group", False)
                    self.show_original_group = data.get("show_original_groups",False)
                    
                    self.progress_compare_file_size = data.get("compare_file_size", True)
                    self.progress_similarity_tolerance = data.get("similarity_tolerance", 5)
                    self.duplicate_size = data.get("duplicate_size", 0)
                    self.visited = set(data.get("visited",[]) )
                    self.groups = data.get("groups",[])
                    self.phashes = data.get("phashes",{})                    
                    self.compare_index = data.get("compare_index",0)
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
       
        data = {
            "hash_format": "v2",
            "stage": self.stage,
            "file_counter": len(self.phashes),
            "current": self.current,
            "compare_index": self.compare_index,
            "overview_page": self.overview_page,
            "auto_next_group":self.auto_next_cb.isChecked(),
            "show_processing_image":self.show_processing_image,
            "show_original_groups":self.show_original_group,
            "compare_file_size": self.compare_file_size,
            "similarity_tolerance": self.similarity_tolerance,
            "duplicate_size":self.duplicate_size,
            "visited": list(self.visited),
            "groups": self.groups,
            "phashes": sorted_hashes
        }
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
            print(f"[Message] Exception file does not exist") 
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
        self.action = "pause"
        self.button_controller()

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
            self.show_group_detail()
        else:
            if self.stage != "done":
                self.compare_index += 1
                self.scan_duplicates()
    
    def btn_action_last_group(self):
        if getattr(self, "stage", None) != "done":
            return
        self.forward = False
        if len(self.view_groups)>0:
            self.current = len(self.view_groups)-1
        self.show_group_detail()

    def relation_by_constraints(self, a: str, b: str) -> str:
        if not hasattr(self, "constraints") or not self.constraints:
            return "none"

        try:
            # must_pairs, cannot_pairs 是 List[Tuple[str, str]]
            if (a, b) in self.constraints.must_pairs or (b, a) in self.constraints.must_pairs:
                return "same"

            if (a, b) in self.constraints.cannot_pairs or (b, a) in self.constraints.cannot_pairs:
                return "different"

            # ignored_files 是 Set[str] 或 List[str]
            if a in self.constraints.ignored_files or b in self.constraints.ignored_files:
                return "ignored"
        except Exception:
            return "none"

        return "none"
    
    def query_group_constraints(self, grp: list) -> list:
        rel = None
        n = len(grp)
        for i in range(n):
            for j in range(i + 1, n):
                if rel == None:
                    rel = self.relation_by_constraints(grp[i], grp[j])
                else:
                    if rel == self.relation_by_constraints(grp[i], grp[j]):
                        continue
                    else:
                        return "mix"
        return rel

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
