import sys, os, json, time, html, platform, rawpy, io, shutil, uuid
import traceback
import ctypes
import multiprocessing
from numpy import number
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial
from PyQt5.QtCore import Qt, QTimer, QSettings, QPropertyAnimation, QRect, QSize, pyqtSignal, QEvent
from PyQt5.QtWidgets import (
    QAction, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QScrollArea, QCheckBox, QSizePolicy,
    QMessageBox, QProgressBar, QSlider, QDialog, QDialogButtonBox, QShortcut,
    QLineEdit, QListWidget, QListWidgetItem, QListView, QAbstractItemView,
    QComboBox, QToolButton, QMenu, QInputDialog, QStyle
)
from PyQt5.QtGui import QPixmap, QImage, QIcon, QKeySequence, QPainter, QColor, QDrag, QImageReader, QImage
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
from utils.verify_build_signature import verify_build_signature
from typing import List, Dict, Tuple
import sip

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
try:
    import winreg
except Exception:
    winreg = None

VIRTUAL_ROOT = "::MY_COMPUTER::"  # Windows virtual root
IS_WIN = (sys.platform.startswith("win"))

def _system_excepthook(exc_type, exc_value, exc_tb):
    print("[Error] Uncaught exception:", exc_type, exc_value)
    traceback.print_tb(exc_tb)

sys.excepthook = _system_excepthook

# Hash image
def _alg_hashing_phash(abs_path):
    try:
        img = Image.open(abs_path)
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
        print(f"[Error] hashing {abs_path}: {e}")
        # Return default value instead of error
        return 0

# Based on operation, src path, dst path to eveluate actions on db of each roots.
def _plan_fs_sync_operations(old_abs: str | None, new_abs: str | None, op: str):
    def _qualify(p: str) -> bool:
        try:
            ext = os.path.splitext(p)[1].lower()
            return ext in EXTS and os.path.getsize(p) > 50_000
        except Exception:
            return False

    old_rels, old_roots = ([], [])
    new_rels, new_roots = ([], [])

    if old_abs:
        old_rels, old_roots = _path_abs_to_rels_and_roots(old_abs)  # rels[i] 對 roots[i]
    if new_abs:
        new_rels, new_roots = _path_abs_to_rels_and_roots(new_abs)

    old_map = {r: rel for r, rel in zip(old_roots, old_rels)}
    new_map = {r: rel for r, rel in zip(new_roots, new_rels)}

    roots_all = set(old_map) | set(new_map)
    actions = []

    new_ok = True
    if op in ("add", "copy", "move") and new_abs:
        new_ok = _qualify(new_abs)

    for root in sorted(roots_all, key=lambda p: len(p), reverse=True):
        orel = old_map.get(root)
        nrel = new_map.get(root)

        if op == "delete":
            if orel:
                actions.append({"root": root, "act": "delete", "old_rel": orel, "new_rel": None})
            continue

        if op == "copy":
            if nrel and new_ok:
                actions.append({"root": root, "act": "add", "old_rel": None, "new_rel": nrel})
            continue

        if op == "add":
            if nrel and new_ok:
                actions.append({"root": root, "act": "add", "old_rel": None, "new_rel": nrel})
            continue

        if op == "move":
            if orel and nrel:
                if orel != nrel and new_ok:
                    actions.append({"root": root, "act": "replace", "old_rel": orel, "new_rel": nrel})
            elif orel and not nrel:
                actions.append({"root": root, "act": "delete", "old_rel": orel, "new_rel": None})
            elif nrel and not orel:
                if new_ok:
                    actions.append({"root": root, "act": "add", "old_rel": None, "new_rel": nrel})
            continue

    return actions

# To build an icon to cover temp image of files.
def _browser_build_icon_from_qimage(qimg: QImage, edge: int) -> QIcon:
    if not isinstance(qimg, QImage) or qimg.isNull():
        return QIcon()

    canvas = QImage(edge, edge, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)

    pm = QPixmap.fromImage(qimg).scaled(
        edge, edge, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )

    painter = QPainter(canvas)
    x = (edge - pm.width()) // 2
    y = (edge - pm.height()) // 2
    painter.drawPixmap(x, y, pm)
    painter.end()

    return QIcon(QPixmap.fromImage(canvas))

# Force enable drag on browser mode.
def _browser_drag_force_enable(lw: QListWidget):
    lw.setDragEnabled(True)
    lw.setDropIndicatorShown(True)
    lw.setAcceptDrops(True)
    lw.setDragDropMode(QAbstractItemView.DragDrop)
    lw.setDefaultDropAction(Qt.MoveAction)

    vp = lw.viewport()
    if vp is not None:
        vp.setAcceptDrops(True)

# Load from Pillow first then transform to QImage to display on Qt
def _browser_fast_load_thumb_qimage(path: str, want_edge: int) -> QImage:
    try:
        im = Image.open(path)
        im = ImageOps.exif_transpose(im)
        if want_edge and want_edge > 0:
            im.thumbnail((want_edge, want_edge), Image.LANCZOS)
        # Transform to QImage
        qimg = _image_pil_to_qimage(im)
        #qimg = ImageQt.ImageQt(im)
        if not qimg.isNull():
            return qimg
    except Exception as e:
        print(f"[dbg] Pillow load fail for {path}: {e}")

    try:
        r = QImageReader(path)
        r.setAutoTransform(True)
        if want_edge and want_edge > 0:
            r.setScaledSize(QSize(want_edge, want_edge))
        qimg = r.read()
        if not qimg.isNull():
            return qimg
        else:
            print(f"[dbg] Qt read fail: {path} | {r.errorString()}")
    except Exception as e:
        print(f"[dbg] Qt path exception: {e}")

    return QImage()

def _browser_choose_icon_path(kind: str, edge: int) -> str:
        base = "icons"  # icon's path
        sizes = [96, 128, 196, 361]
        # Find nearest size
        best = min(sizes, key=lambda s: abs(s-edge))
        fname = f"{kind}_icon_{best}.png" if kind != "arrow" else \
            f"arrow_plain_{best}.png"
        p = _resource_path(os.path.join("icons", fname))
        if os.path.exists(p):
            return p

# Check if it in virtual root
def _virtual_root_is_virtual_root(path: str) -> bool:
    return IS_WIN and (path == VIRTUAL_ROOT)

def normalize_dir(p: str) -> str:
    if IS_WIN:
        # Transform c: to c:\
        if len(p) == 2 and p[1] == ":":
            p = p + "\\"
    return os.path.abspath(p)

# Return drive type (refer to GetDriveType)
def _virtual_root_list_logical_drives() -> List[Tuple[str, int]]:
    buf = ctypes.create_unicode_buffer(254)
    ctypes.windll.kernel32.GetLogicalDriveStringsW(ctypes.sizeof(buf)//2, buf)
    drives = buf.value.split("\x00")
    res = []
    for d in drives:
        if not d:
            continue
        dtype = ctypes.windll.kernel32.GetDriveTypeW(d)
        res.append((d, dtype))
    return res

def _virtual_root_is_unc_share_root(path: str) -> bool:
    if not IS_WIN or not path:
        return False
    p = path.rstrip("\\/")
    if p.startswith("\\\\"):
        parts = p.split("\\")
        return len(parts) == 4 and all(parts[2:4])
    return False

def _virtual_root_network_letters() -> List[str]:
    letters = []
    if not winreg:
        return letters
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Network") as key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                    if len(name) == 1 and name.isalpha():
                        letters.append(f"{name.upper()}:\\")
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return letters

# Check if it is in drive root
def _virtual_root_is_drive_root(path: str) -> bool:
    if not IS_WIN:
        return False
    path = normalize_dir(path)
    return len(path) == 3 and path[1] == ":" and (path.endswith("\\") or path.endswith("/"))

# Apply new font size after configuration is changed.
def _cfg_ui_apply_app_font_size(size: int):
    f = QApplication.font()
    f.setPointSize(int(size))
    QApplication.setFont(f)

# Apply new theme after configuration is changed.
def _cfg_ui_apply_theme(theme: str):
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

def _alg_hashing_api(path):
    return _alg_hashing_phash(path)

# If abs_path is in progress file of some folders, return there rel_paths and roots abs_path
def _path_abs_to_rels_and_roots(abs_path: str) -> tuple[list[str], list[str]]:
    try:
        ap = os.path.abspath(abs_path)
        roots = []
        rels = []
        cur = ap
        while True:
            root = _path_find_progress_root(cur)
            if not root:
                break
            if root not in roots:
                roots.append(root)
                rel = os.path.relpath(ap, root).replace("\\", "/").lower()
                rels.append(rel)
            parent = os.path.dirname(root)
            if parent == root:
                break
            cur = parent

        return rels, roots
    except Exception:
        return [], []

# Build highlight path
def _path_build_highlight_html(common_prefix, rel_path):
    return (
        f"<span style='color:gray'>{html.escape(common_prefix)}</span>"
        f"<span style='color:red; font-weight:bold; font-size:116%'>{html.escape(rel_path)}</span>"
    )

# Find folder which has progress file from start_path
def _path_find_progress_root(start_path: str) -> str | None:
    try:
        d = os.path.abspath(start_path)
        if os.path.isfile(d):
            d = os.path.dirname(d)

        while True:
            pf = os.path.join(d, PROGRESS_FILE)
            if os.path.exists(pf):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return None
    except Exception:
        return None

# Gen an unique folder name
def _path_gen_unique_dir(parent_dir: str, base: str) -> str:
    cand = os.path.join(parent_dir, base)
    i = 1
    while os.path.exists(cand):
        cand = os.path.join(parent_dir, f"{base} ({i})")
        i += 1
    return cand

# Gen an unique file name
def _path_gen_unique_name(dest_dir, base, ext):
    cand = os.path.join(dest_dir, base + ext)
    i = 1
    while os.path.exists(cand):
        cand = os.path.join(dest_dir, f"{base} ({i}){ext}")
        i += 1
    return cand

# Check if child is child of parent
def _path_is_child_folder(parent: str, child: str) -> bool:
        try:
            parent = os.path.abspath(parent)
            child  = os.path.abspath(child)
            return os.path.commonpath([parent, child]) == parent
        except Exception:
            return False

# Gen a sort key for group
def _group_gen_sort_key(grp):
    folders = sorted(
        os.path.dirname(p).replace("\\", "/").lower()
        for p in grp
    )
    return "|".join(folders)

# Transform pil image to qimage
def _image_pil_to_qimage(pil_img):
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

# Collect all files in abs_dir
def _path_collect_files(abs_dir: str):        
        results = []
        try:
            for root, dirs, files in os.walk(abs_dir):
                for f in files:
                    results.append(os.path.join(root, f))
                for d in dirs:
                    results.append(os.path.join(root, d))
        except Exception as e:
            print(f"[walk error] {abs_dir}: {e}")
        return results

# Load image for thumbnail
def _image_load_for_thumb(path, want_min_edge=1400):
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

# Return value in range
def _math_clamp(x, min_val, max_val):
    return max(min_val, min(x, max_val))

def _resource_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def _resource_path(rel_path: str) -> str:
    candidates = [
        os.path.join(_resource_base_dir(), rel_path),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), rel_path),
        os.path.abspath(rel_path),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

# Class for Browser
class BrowserListWidget(QListWidget):
    operationRequested = pyqtSignal(str, list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setMovement(QListView.Static)
        self._hover_row = -1
    
    # ---- drag in / move ----
    def dragEnterEvent(self, event):
        if event.source() is self or event.mimeData().hasUrls() or event.mimeData().hasFormat('application/x-qabstractitemmodeldatalist'):
            event.setDropAction(Qt.MoveAction)
            self._drag_src_paths = []
            for ix in self.selectedIndexes():
                p = self.item(ix.row()).data(Qt.UserRole)
                if p:
                    self._drag_src_paths.append(p)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        idx = self.indexAt(event.pos())
        row = idx.row() if idx.isValid() else -1

        dest_is_dir = False
        if idx.isValid():
            it = self.item(row)
            p = it.data(Qt.UserRole)
            dest_is_dir = (p and os.path.isdir(p))

        # Highlight destination folder
        if dest_is_dir:
            if self._hover_row != row:
                if self._hover_row != -1:
                    old = self.item(self._hover_row)
                    if old:
                        old.setSelected(False)
                self._hover_row = row
                it.setSelected(True)
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            if self._hover_row != -1:
                old = self.item(self._hover_row)
                if old:
                    old.setSelected(False)
                self._hover_row = -1
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        if self._hover_row != -1:
            it = self.item(self._hover_row)
            if it:
                it.setSelected(False)
            self._hover_row = -1
        event.accept()

    # ---- drop ----
    def dropEvent(self, event):
        idx = self.indexAt(event.pos())
        if not idx.isValid():
            return super().dropEvent(event)

        it = self.item(idx.row())
        dest_path = it.data(Qt.UserRole)

        if not dest_path or not os.path.isdir(dest_path):
            return super().dropEvent(event)

        # Src list
        src_abs = []
        if event.source() is self:
            for p in self._drag_src_paths:
                if not p:
                    continue
                # Except dst
                if os.path.abspath(p) == os.path.abspath(dest_path):
                    continue
                if os.path.isfile(p) or os.path.isdir(p):
                    src_abs.append(p)
        else:
            md = event.mimeData()
            if md.hasUrls():
                for url in md.urls():
                    if url.isLocalFile():
                        lp = url.toLocalFile()
                        if os.path.isfile(lp) or os.path.isdir(lp):
                            # Except dst
                            if os.path.abspath(lp) == os.path.abspath(dest_path):
                                continue
                            src_abs.append(lp)

        if not src_abs:
            return super().dropEvent(event)

        # Ctrl=copy、Shift=move
        mods = event.keyboardModifiers()
        if mods & Qt.ControlModifier:
            op = "copy"
        else:
            op = "move"
        self.operationRequested.emit(op, src_abs, dest_path)

        # Clear highlight
        if self._hover_row != -1:
            it = self.item(self._hover_row)
            if it:
                it.setSelected(False)
            self._hover_row = -1
        
        event.setDropAction(Qt.MoveAction)
        event.accept()

# Draggable class for images in group
class DraggableListWidget(QListWidget):
    reordered = pyqtSignal(object)  # emit(self) after drop & reorder

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)             
        self.setDragDropMode(QAbstractItemView.DragDrop)  # Use my own drop DragDrop
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setResizeMode(QListWidget.Adjust)
        self.setSpacing(8)
        self.setMovement(QListWidget.Snap)            
        self.setViewMode(QListWidget.IconMode)

        self._highlight_index = None
        self._drag_rows = []  # Be dragged row

    def startDrag(self, supported_actions):
        idxs = self.selectedIndexes()
        if not idxs:
            return

        # Record dragged rows
        self._drag_rows = sorted([ix.row() for ix in idxs])

        vr = self.visualRect(idxs[0])
        if not vr.isNull() and vr.width() > 0 and vr.height() > 0:
            drag_pm = self.viewport().grab(vr)
        else:
            drag_pm = QPixmap(64, 64); drag_pm.fill(Qt.transparent)

        translucent = QPixmap(drag_pm.size())
        translucent.fill(Qt.transparent)
        p = QPainter(translucent)
        p.setOpacity(0.5)
        p.drawPixmap(0, 0, drag_pm)
        p.end()

        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData(idxs))
        drag.setPixmap(translucent)
        drag.setHotSpot(translucent.rect().center())
        drag.exec_(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        pos = event.pos()
        idx = self.indexAt(pos)

        if idx.isValid():
            it = self.item(idx.row())
            rect = self.visualItemRect(it)
            # If cursor in the image right half, insert after it.
            insert_row = idx.row() + (1 if pos.x() > rect.center().x() else 0)
        else:
            # If not hit any item, insert at the last.
            insert_row = self.count()

        self._insert_row = insert_row       # For dropEvent
        self._highlight_index = insert_row 
        self.viewport().update()

        if event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.source() is not self:
            return super().dropEvent(event)

        before = [self.item(i).data(Qt.UserRole) for i in range(self.count())]

        # Insert point
        if hasattr(self, "_insert_row"):
            drop_row = int(self._insert_row)
        else:
            idx = self.indexAt(event.pos())
            if idx.isValid():
                it = self.item(idx.row())
                rect = self.visualItemRect(it)
                drop_row = idx.row() + (1 if event.pos().x() > rect.center().x() else 0)
            else:
                drop_row = self.count()

        selected_rows = list(self._drag_rows) if self._drag_rows else [self.currentRow()]
        up = drop_row < min(selected_rows)
        rows_iter = selected_rows if up else reversed(selected_rows)

        taken = []
        for r in rows_iter:
            it = self.takeItem(r)
            w  = self.itemWidget(it)
            if w:
                self.removeItemWidget(it)
            taken.append((it, w))

        shift = sum(1 for r in selected_rows if r < drop_row)
        drop_row -= shift

        insert_at = drop_row
        for it, w in (taken if up else reversed(taken)):
            self.insertItem(insert_at, it)
            if w:
                self.setItemWidget(it, w)
            insert_at += 1

        # Clear highlight
        self._highlight_index = None
        if hasattr(self, "_insert_row"):
            del self._insert_row
        self.viewport().update()

        after = [self.item(i).data(Qt.UserRole) for i in range(self.count())]

        # Trigger to _group_drag_apply_new_order_from_list
        self.reordered.emit(self)
        event.acceptProposedAction()
        self._drag_rows = []

    # Paint highlight area
    def paintEvent(self, e):
        super().paintEvent(e)
        if self._highlight_index is not None:
            it = self.item(self._highlight_index)
            if it:
                rect = self.visualItemRect(it)
                painter = QPainter(self.viewport())
                painter.setPen(QColor(0, 150, 255, 180))
                painter.setBrush(QColor(0, 150, 255, 60))
                painter.drawRect(rect.adjusted(2, 2, -2, -2))
                painter.end()

# Class for show image
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

        self.image_label.mouseDoubleClickEvent = self.close_on_double_click

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scaled_pixmap()

    def update_scaled_pixmap(self):
        try:
            img = ImageOps.exif_transpose(Image.open(self.image_path))  # Fix rotation issue
            img.thumbnail((self.width() - 40, self.height() - 120))     # Fit windows size
            qimg = _image_pil_to_qimage(img)
            pixmap = QPixmap.fromImage(qimg)
        except Exception as e:
            print(f"[Error] Failed to display large image] {self.image_path}: {e}")
            pixmap = QPixmap()  # Avoid flash when no image

        self.image_label.setPixmap(pixmap)
    
    def close_on_double_click(self, event):
        # Close windows when double click
        if event.button() == Qt.LeftButton:  # Left button
            self.close()

# Main class
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

        self.path_lbl = QLabel()
        self.exclude_lbl = QLabel()

        self.scan_btn = QPushButton()
        self.scan_btn.clicked.connect(self._btn_action_scan)
        self.scan_btn.setEnabled(False)

        self.pause_btn = QPushButton()
        self.pause_btn.clicked.connect(self._btn_action_pause_processing)
        self.pause_btn.setEnabled(False)
        
        self.continue_btn = QPushButton()
        self.continue_btn.clicked.connect(self._btn_action_continue_processing)
        self.continue_btn.setEnabled(False)
        
        self.exit_btn = QPushButton()
        self.exit_btn.clicked.connect(self._btn_action_exit_and_save)

        self.first_btn = QPushButton()
        self.first_btn.clicked.connect(partial(self._btn_handler_navi,"first"))
        self.first_btn.setEnabled(False)

        self.prev_folder_btn = QPushButton()
        self.prev_folder_btn.clicked.connect(partial(self._btn_handler_navi,"pre_folder"))
        self.prev_folder_btn.setEnabled(False)

        self.prev_btn = QPushButton()
        self.prev_btn.clicked.connect(partial(self._btn_handler_navi,"pre_group"))
        self.prev_btn.setEnabled(False)

        self.next_btn = QPushButton()
        self.next_btn.clicked.connect(partial(self._btn_handler_navi,"next_group"))
        self.next_btn.setEnabled(False)

        self.next_folder_btn = QPushButton()
        self.next_folder_btn.clicked.connect(partial(self._btn_handler_navi,"next_folder"))
        self.next_folder_btn.setEnabled(False)

        self.last_btn = QPushButton()
        self.last_btn.clicked.connect(partial(self._btn_handler_navi,"last"))
        self.last_btn.setEnabled(False)

        self.auto_next_cb = QCheckBox()
        self.auto_next_cb.setChecked(False)     
        self.auto_next_cb.clicked.connect(partial(self._chkbox_handler,"auto_next"))

        self.delete_btn = QPushButton()
        self.delete_btn.setEnabled(False)

        self.merge_btn    = QPushButton(self.i18n.t("btn.merge"))
        self.ignore_btn   = QPushButton(self.i18n.t("btn.ignore"))
        self.separate_btn = QPushButton(self.i18n.t("btn.separate"))
        self.unmarked_btn = QPushButton(self.i18n.t("btn.unmarked"))
        
        self.delete_btn.clicked.connect(self._btn_action_delete_unchecked)
        self.ignore_btn.clicked.connect(self._btn_action_mark_images_ignore)
        self.separate_btn.clicked.connect(self._btn_action_mark_images_separate)
        self.merge_btn.clicked.connect(self._btn_action_mark_images_same)
        self.unmarked_btn.clicked.connect(self._btn_action_unmarked_images)

        self.show_browser_back_btn = QPushButton("⏏")
        self.show_browser_back_btn.setFixedWidth(36)
        self.show_browser_back_btn.clicked.connect(lambda: self._btn_handler_show_back())

        self.show_group_back_btn = QPushButton("⏏")
        self.show_group_back_btn.setFixedWidth(36)
        self.show_group_back_btn.clicked.connect(lambda: self._btn_handler_show_back())

        self.display_img_dynamic_cb = QCheckBox()
        self.display_img_dynamic_cb.setChecked(False)
        self.display_img_dynamic_cb.clicked.connect(partial(self._chkbox_handler,"img_dynamic"))

        self.exclude_input = QLineEdit()
        self.exclude_input.setFixedWidth(250)
        self.exclude_input.setEnabled(False)
        self.exclude_input.editingFinished.connect(self._input_focus_clear_exclude)
        self.exclude_input.setFocusPolicy(Qt.ClickFocus)

        self.browser_path_prefix = QLabel(self.i18n.t("label.selected_folder", default="Path:"))
        self.browser_up_btn = QPushButton(self.i18n.t("btn.browser_up", default="⬆ Up"))
        self.browser_path_label = QLabel("")
        self.browser_view_style_combo = QComboBox()
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.list", default="List View"), "list")
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.small", default="Small Icons"), "small")
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.medium", default="Medium Icons"), "medium")
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.large", default="Large Icons"), "large")
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.huge", default="Huge Icons"), "huge")
        self.browser_view_style_lbl = QLabel(self.i18n.t("label.browser_view_style", default="View Style:"))

        self.browser_sort_combo = QComboBox()
        self.browser_sort_combo.addItem(self.i18n.t("browser_sort.name",  default="Name"), "name")
        self.browser_sort_combo.addItem(self.i18n.t("browser_sort.mtime", default="Modified Time"), "mtime")
        self.browser_sort_combo.addItem(self.i18n.t("browser_sort.type",  default="Type"), "type")
        self.browser_sort_lbl = QLabel(self.i18n.t("label.browser_sort", default="Sort:"))
        self.browser_order_btn = QToolButton()
        self.status = QLabel()

        ctl_top.addWidget(self.status, alignment=Qt.AlignLeft | Qt.AlignTop)
        ctl_top.addStretch()
        for w in (self.auto_next_cb, self.display_img_dynamic_cb):
            ctl_top.addWidget(w)
        layout.addLayout(ctl_top)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)

        # Create here for bind i18n
        self.thumb_size_lbl = QLabel(self.i18n.t('label.thumb_size') if hasattr(self,'i18n') else 'Thumb')

        # ---------- 3) i18n binding ----------
        self.i18n_binder.bind(self.exclude_lbl, "setText", "label.exclude_folder")
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
        self.i18n.changed.connect(self._status_refresh_text)
        self.i18n_binder.bind(self.status, "setText", "status.please_select_folder")
        self.i18n_binder.bind(self.thumb_size_lbl, "setText", "label.thumb_size")

        # Browser
        self.i18n_binder.bind(self.browser_path_prefix,"setText","label.selected_folder")
        self.i18n_binder.bind(self.browser_up_btn,"setText","btn.browser_up")
        
        self.i18n_binder.bind(self.browser_view_style_lbl,"setText","label.browser_view_style")
        self.i18n_binder.bind(self.browser_view_style_combo,("setItemText",0),"browser_view_style.list")
        self.i18n_binder.bind(self.browser_view_style_combo,("setItemText",1),"browser_view_style.small")
        self.i18n_binder.bind(self.browser_view_style_combo,("setItemText",2),"browser_view_style.medium")
        self.i18n_binder.bind(self.browser_view_style_combo,("setItemText",3),"browser_view_style.large")
        self.i18n_binder.bind(self.browser_view_style_combo,("setItemText",4),"browser_view_style.huge")

        self.i18n_binder.bind(self.browser_sort_lbl,"setText","label.browser_sort")
        self.i18n_binder.bind(self.browser_sort_combo,("setItemText",0),"browser_sort.name")
        self.i18n_binder.bind(self.browser_sort_combo,("setItemText",1),"browser_sort.mtime")
        self.i18n_binder.bind(self.browser_sort_combo,("setItemText",2),"browser_sort.type")

        # ---------- 4) Menu（Using i18n too) ----------
        menubar = self.menuBar()

        # Create reusable actions, not tied to a specific OS
        # About
        about_action = QAction(self)
        about_action.setMenuRole(QAction.NoRole)
        about_action.triggered.connect(self._about_show_information_and_gpg)
        self.i18n_binder.bind(about_action, "setText", "menu.help.about")

        # Preferences
        prefs_action = QAction(self)
        prefs_action.setMenuRole(QAction.NoRole)
        prefs_action.setShortcut(QKeySequence("Ctrl+,"))
        prefs_action.triggered.connect(self._cfg_open_preferences)
        self.i18n_binder.bind(prefs_action, "setText", "menu.edit.settings")

        # Quit
        quit_action = QAction(self)
        quit_action.setMenuRole(QAction.NoRole)
        quit_action.setShortcut(QKeySequence.Quit)   # Cmd+Q / Ctrl+Q
        quit_action.triggered.connect(self._btn_action_exit_and_save)
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
        self.work_folder = None
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
        self.exceptions_file = None
        self.last_ui_update = 0
        self.last_scan_time = None
        self.lock_file = None
        self.lock_data = None
        self.exception_file_version = 1
        self.not_duplicate_pairs = []
        self.exception_groups = []
        self.lock_timer = QTimer()
        self.lock_timer.timeout.connect(lambda: self._db_lock_update(self.work_folder))
        self.lock_timer.start(30 * 60 * 1000)
        self.view_groups = []
        self.view_summary = []
        self.display_same_images = True
        self.show_original_groups = False
        self.show_processing_image = False
        self.visited = set()
        self.related_files_mode = False
        # Fixed issue: APP will not compare if only progress file is not exist.
        # Root cause: `progress_compare_file_size` and 'progress_similarity_tolerance' are not defined,
        #             causing the error.
        # Solution: Init these variables.
        self.progress_compare_file_size = 0
        self.progress_similarity_tolerance = 0
        
        self.exception_folder = None
        self.last_group_index = 0
        # Restore configuration theme / language
        self.cfg = Config()
        _cfg_ui_apply_theme(self.cfg.get("ui.theme","system"))
        self._cfg_ui_apply_language(self.cfg.get("ui.lang","zh-TW"))
        self.show_processing_image = self.cfg.get("ui.show_processing_image",False)
        self.show_original_groups = self.cfg.get("ui.show_original_groups", False)
        self.display_same_images = self.cfg.get("behavior.display_same_images",True)
        self.current_overview_thumb_size = int(self.cfg.get("ui.overview_thumbnail.max_size", 240))
        self.current_group_thumb_size = int(self.cfg.get("ui.thumbnail.max_size", 400))
        self.confirm_delete = (bool(self.cfg.get("behavior.confirm_delete", True)))
        self.auto_next_cb.setChecked(self.cfg.get("behavior.auto_next_group", True))
        self.compare_file_size = (bool(self.cfg.get("behavior.compare_file_size", True)))
        self.similarity_tolerance = int(self.cfg.get("behavior.similarity_tolerance", 5))
        self._browser_view_style_key = self.cfg.get("ui.browser_view_style_key", "medium") # list | small | medium | large
        self._browser_sort_key = (self.cfg.get("ui.browser_sort_key", "name"))
        self._browser_sort_asc = (bool(self.cfg.get("ui.browser_order_asc", True)))
        self.browser_folder = self.cfg.get("ui.last_browser_path", os.path.expanduser("~"))
        # Group overview
        self.overview_cols = 4
        self.overview_rows = 3
        self.overview_page = 0
        self.group_preview_cache = OrderedDict()
        self.group_preview_cache_limit = 1024
        self.view_groups_update = True
        
        # For record browser view scroll position state cache: { abs_path: {"scroll": int, "selected": abs_path_or_None}}
        self._browser_view_state = {}
        
        # Restore font size
        self.fontsize = int(self.cfg.get("ui.font_size", 12))
        _cfg_ui_apply_app_font_size(self.fontsize)

        self._register_shortcuts()
        self._btn_controller()
        self._chkbox_controller()

        self.normal_host = None
        self.processing_host = None
        self.normal_body_layout = None
        self.processing_body_layout = None

        # Set init UI
        self._browser_nav_lock = False
        idx = self.browser_sort_combo.findData(self._browser_sort_key)
        self.browser_sort_combo.setCurrentIndex(idx)
        idx = self.browser_view_style_combo.findData(self._browser_view_style_key)
        self.browser_view_style_combo.setCurrentIndex(idx)
        self.browser_order_btn.setText("a->z" if self._browser_sort_asc else "z->a")
        
        # Init cache
        self._browser_thumb_cache = OrderedDict()
        self._browser_thumb_cache_limit = 512
        
        # Default show browser
        self._browser_show(self.browser_folder)
        QTimer.singleShot(0, lambda: self.setFocus())
    
    # Indtall event for overview
    def _overview_install_events(self, listw: QListWidget):
        # EventFilter is for viewport QListWidgetItem only, group detail is QWidget
        vp = listw.viewport()
        self._ovw_listw = listw
        self._ovw_vp = vp

        listw.setMouseTracking(True)
        vp.setMouseTracking(True)

        # Remove old EventFiler then install new EventFilter
        try:
            vp.removeEventFilter(self)
        except Exception:
            pass
        vp.installEventFilter(self)

    # Remove event of overview
    def _overview_remove_events(self):
        vp = getattr(self, "_ovw_vp", None)
        if vp:
            try:
                vp.removeEventFilter(self)
            except Exception:
                pass
        self._ovw_vp = None
        self._ovw_listw = None

    # This function changes cursor when mouse move on the thumbnail
    # Active on overview page and obj is current viewport
    def eventFilter(self, obj, ev):    
        if getattr(self, "action", "") != "show_overview":
            return super().eventFilter(obj, ev)

        vp = getattr(self, "_ovw_vp", None)
        listw = getattr(self, "_ovw_listw", None)
        if obj is not vp or listw is None:
            return super().eventFilter(obj, ev)

        et = ev.type()
        if et == QEvent.MouseMove:
            pos = ev.pos()
            it = listw.itemAt(pos)
            if it:
                vp.setCursor(Qt.PointingHandCursor)
            else:
                vp.setCursor(Qt.ArrowCursor)
            return False  # Pass to Qt

        if et == QEvent.Leave:
            vp.setCursor(Qt.ArrowCursor)
            return False

        if et == QEvent.MouseButtonDblClick and ev.button() == Qt.LeftButton:
            pos = ev.pos()
            it = listw.itemAt(pos)
            if it is not None:
                gi = it.data(Qt.UserRole)
                if gi is not None:
                    # Postpone to next event to prevent listw is destoried when change page
                    QTimer.singleShot(0, lambda gi=gi: self._group_show_api(gi))
                    return True  # Process click
        return super().eventFilter(obj, ev)

    # Rebuild group list after drag and drop
    def _group_rebuild_list(self, listw: QListWidget, order_paths: list, relation: str):
        listw.blockSignals(True)
        listw.clear()

        # Reset slider cache
        self._thumb_labels = []
        self._thumb_qimages = []
        self._thumb_styles = []

        group_abs_paths = [self._path_get_abs_path(p) for p in order_paths]
        common_prefix = os.path.commonpath(group_abs_paths).replace("\\", "/").lower()
        if len(common_prefix) > 0 and not common_prefix.endswith("/"):
            common_prefix += "/"

        for idx, p in enumerate(order_paths, start=1):
            abs_path = self._path_get_abs_path(p).replace("\\", "/").lower()

            try:
                base_size = max(self.current_group_thumb_size, 1400)
                img = _image_load_for_thumb(abs_path, want_min_edge=base_size)
                if relation == "ignored":
                    try:
                        img = ImageOps.grayscale(img)
                    except Exception:
                        img = img.convert("L")

                qimg = _image_pil_to_qimage(img)
                pm = QPixmap.fromImage(qimg)
                target = min(self.current_group_thumb_size, max(pm.width(), pm.height()))
                pm = pm.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)

                style = "normal"
                if relation == "different":
                    style = "dark"
                    painter = QPainter(pm)
                    painter.fillRect(pm.rect(), QColor(0, 0, 0, 110))
                    painter.end()

                # cell widget
                cell = QWidget()
                cell_v = QVBoxLayout(cell)
                cell_v.setContentsMargins(1, 1, 1, 1)
                cell_v.setSpacing(6)

                thumb_lbl = QLabel()
                thumb_lbl.setAlignment(Qt.AlignCenter)
                thumb_lbl.setPixmap(pm)
                thumb_lbl.setCursor(Qt.PointingHandCursor)
                thumb_lbl.mouseDoubleClickEvent = lambda e, fp=abs_path: self._group_show_image(fp)
                cell_v.addWidget(thumb_lbl)

                if relation == "same":
                    cb_text = self.i18n.t("msg.must")
                elif relation == "different":
                    cb_text = self.i18n.t("msg.separate")
                elif relation == "ignored":
                    cb_text = self.i18n.t("msg.ignore")
                elif relation == "mix":
                    cb_text = self.i18n.t("msg.mix")
                else:
                    cb_text = self.i18n.t("msg.keepfile")

                cb = QCheckBox(cb_text)
                cb.setChecked(True)
                cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                cb.path = p
                self.group_checkboxes.append(cb)
                cell_v.addWidget(cb)

                rel_path = os.path.dirname(os.path.relpath(abs_path, common_prefix).replace("\\", "/").lower())
                if len(rel_path) > 0 and not rel_path.endswith("/"):
                    rel_path += "/"
                size_str = ""
                if os.path.exists(abs_path):
                    file_size_b = os.path.getsize(abs_path)
                    size_str = f"{(file_size_b / 1000):,.2f} KB" if file_size_b < 1000*1000 else f"{(file_size_b / (1000*1000)):,.2f} MB"

                info_label = QLabel()
                info_label.setTextFormat(Qt.RichText)
                info_label.setWordWrap(True)
                info_label.setText(
                    f"{idx}. {self.i18n.t('msg.filename')}: {os.path.basename(abs_path)}<br>"
                    f"{_path_build_highlight_html(common_prefix, rel_path)}<br>"
                    f"{self.i18n.t('msg.filesize')}: {size_str}<br>"
                )
                cell_v.addWidget(info_label)

                btn = QPushButton(self.i18n.t("btn.show_in_finder"))
                btn.clicked.connect(lambda _, fp=abs_path: self._system_open_in_explorer(fp))
                cell_v.addWidget(btn)
                cell_v.addStretch(1)

                # Cache for slider
                self._thumb_labels.append(thumb_lbl)
                self._thumb_qimages.append(qimg)
                self._thumb_styles.append(style)

                # Put QListWidget
                item = QListWidgetItem()
                item.setSizeHint(cell.sizeHint())
                # Save path to UserRole
                item.setData(Qt.UserRole, p)
                item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable
                            | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)

                listw.addItem(item)
                listw.setItemWidget(item, cell)

            except Exception as e:
                err = QLabel(self.i18n.t("err.fail_to_load_images", path=abs_path, str=str(e)))
                err.setWordWrap(True)
                err.setFixedWidth(480)
                err_w = QWidget()
                err_l = QVBoxLayout(err_w)
                err_l.setContentsMargins(1, 1, 1, 1)
                err_l.addWidget(err)

                item = QListWidgetItem()
                item.setSizeHint(err_w.sizeHint())
                item.setData(Qt.UserRole, p)
                item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable
                            | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
                listw.addItem(item)
                listw.setItemWidget(item, err_w)

                self._thumb_labels.append(None)
                self._thumb_qimages.append(None)
                self._thumb_styles.append("normal")

        listw.blockSignals(False)

    # Tigger API when drop image
    def _group_drag_apply_new_order_from_list(self, listw: QListWidget):
        new_order = [listw.item(i).data(Qt.UserRole) for i in range(listw.count())]

        # Update images order in group
        if self.view_groups and 0 <= self.current < len(self.view_groups):
            self.view_groups[self.current] = new_order
        elif self.groups and 0 <= self.current < len(self.groups):
            self.groups[self.current] = new_order

        relation = self._constraints_query_groups_relation(new_order)
        self._group_rebuild_list(listw, new_order, relation)

    # Register supported shortcuts
    def _register_shortcuts(self):
        self._shortcuts = {}

        def add(name, seq, handler):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(handler)
            self._shortcuts[name] = sc

        # File / Process control
        add("sc_show_back", "B", self._btn_handler_show_back)
        add("sc_scan", "S", self._btn_action_scan)
        add("sc_pause", "P", self._btn_action_pause_processing)
        add("sc_continue", "C", self._btn_action_continue_processing)
        add("sc_exit", "Q", self._btn_action_exit_and_save)

        # Explorer
        add("sc_first_f", "F", partial(self._btn_handler_navi,"first"))
        add("sc_first_home", "Home", partial(self._btn_handler_navi,"first"))
        add("sc_pre_group", "Left", partial(self._btn_handler_navi,"pre_group"))
        add("sc_next_group", "Right", partial(self._btn_handler_navi,"next_group"))
        add("sc_pre_folder", "Up", partial(self._btn_handler_navi,"pre_folder"))
        add("sc_next_folder", "Down", partial(self._btn_handler_navi,"next_folder"))
        add("sc_last_l", "L", partial(self._btn_handler_navi,"last"))
        add("sc_last_end", "End", partial(self._btn_handler_navi,"last"))

        # Delete unselected files
        add("sc_delete_backspace", "Backspace", self._btn_action_delete_unchecked)
        add("sc_delete_del", "Delete", self._btn_action_delete_unchecked)

        # 0~9 mapping checkbox
        for i in range(0, 10):
            add(f"sc_num{i}", str(i), lambda i=i: self._chkbox_toggle(i - 1))
        
        # Show groups edit 
        add("sc_mark_same", "Ctrl+S",self._btn_action_mark_images_same)
        add("sc_mark_diff", "Ctrl+D",self._btn_action_mark_images_separate)
        add("sc_mark_ignore", "Ctrl+I",self._btn_action_mark_images_ignore)
        add("sc_mark_clear", "Ctrl+U",self._btn_action_unmarked_images)
        
        # Browser Rename (F2)
        add("sc_rename", "F2",self._file_rename_current)
        add("sc_refresh","F5",lambda cur=self.browser_folder: self._browser_show(cur))

    def _file_rename_current(self):
        lw = getattr(self, "_browser_listw_ref", None)
        if not lw or self.action != "show_browser":
            return
        it = lw.currentItem()
        if not it:
            return
        pos = lw.visualItemRect(it).center()
        self._browser_action_context_menu(pos)  # 直接叫出同一個右鍵流程

    # Make focus not in input area
    def _input_focus_clear_exclude(self):
        self.setFocus()
    
    # Open perferences dialog
    def _cfg_open_preferences(self):
        dlg = SettingsDialog(self.cfg, self.i18n, self.i18n_binder, parent=self)
        dlg.settings_applied.connect(self._cfg_apply_settings)
        dlg.exec_()
    
    # Apply setting
    def _cfg_apply_settings(self, changed_keys: list):
        # Have to press hot-apply
        if "ui.font_size" in changed_keys:
            self.fontsize = int(self.cfg.get("ui.font_size", 12))
            _cfg_ui_apply_app_font_size(self.fontsize)
        #if "ui.theme" in changed_keys:
        #    _cfg_ui_apply_theme(self.cfg.get("ui.theme"))
        if "ui.lang" in changed_keys:
            self._cfg_ui_apply_language(self.cfg.get("ui.lang"))
            self._cfg_ui_retranslate_texts()
        if "ui.browser_view_style_key" in changed_keys:
            self._browser_view_style_key = self.cfg.get("ui.browser_view_style_key","medium")
            idx = self.browser_view_style_combo.findData(self._browser_view_style_key)
            self.browser_view_style_combo.setCurrentIndex(idx)
            if self.action == "show_browser":
                self._browser_show(self.browser_folder)
        if "ui.browser_sort_key" in changed_keys:
            self._browser_sort_key = self.cfg.get("ui.browser_sort_key","name")
            idx = self.browser_sort_combo.findData(self._browser_sort_key)
            self.browser_sort_combo.setCurrentIndex(idx)
            if self.action == "show_browser":
                self._browser_show(self.browser_folder)
        if "ui.browser_order_asc" in changed_keys:
            self.browser_order_btn.setText("a->z" if self.cfg.get("ui.browser_order_asc", True) else "z->a")
            self._browser_sort_asc = bool(self.cfg.get("ui.browser_order_asc", True))
            if self.action == "show_browser":
                self._browser_show(self.browser_folder)
        if "ui.overview_thumbnail.max_size" in changed_keys:
            self.current_overview_thumb_size = int(self.cfg.get("ui.overview_thumbnail.max_size"))
            self._overview_reload_thumbnails()
        if "ui.show_processing_image" in changed_keys:
            self.show_processing_image = self.cfg.get("ui.show_processing_image", False)
            self._chkbox_controller()
        if "ui.show_original_groups" in changed_keys:
            self.show_original_groups = self.cfg.get("ui.show_original_groups", False)
            self._chkbox_controller()
            self.view_groups_update = True
            if self.action == "show_group":
                self._group_show_api()
            elif self.action == "show_overview":
                self._overview_show_api()
        if "ui.thumbnail.max_size" in changed_keys:
            self.current_group_thumb_size = int(self.cfg.get("ui.thumbnail.max_size"))
            self._group_reload_thumbnails()
        if "behavior.auto_next_group" in changed_keys:
            self.auto_next_cb.setChecked(self.cfg.get("behavior.auto_next_group", True))
        if "behavior.display_same_images" in changed_keys:
            self.display_same_images = self.cfg.get("behavior.display_same_images",True)
        if "behavior.confirm_delete" in changed_keys:
            self.confirm_delete = int(self.cfg.get("behavior.confirm_delete"))
        if "behavior.compare_file_size" in changed_keys or "behavior.similarity_tolerance" in changed_keys:
            self.compare_file_size = int(self.cfg.get("behavior.compare_file_size"))
            self.similarity_tolerance = self.cfg.get("behavior.similarity_tolerance")
            if (self.action == "show_group" or self.action == "show_overview") and (self.stage=="done" or self.stage=="comparing"):
                self.compare_index = 0
                self.groups = []
                self.duplicate_size = 0
                self._alg_comparing_api()
    
    # List drives in virtual root
    def _virtual_root_list_items(self, app: QApplication, edge: int) -> List[Dict]:
        items = []

        try:
            for drive, dtype in _virtual_root_list_logical_drives():
                if dtype in (2, 3, 4, 5):  # Removable/Fixed/Remote/CDROM
                    if dtype == 2:
                        kind = "drive_removable"
                    elif dtype == 3:
                        kind = "drive_fixed"
                    elif dtype == 4:
                        kind = "drive_net"
                    else:
                        kind = "drive_net"
                    items.append({
                        "label": drive,
                        "abs_path": drive,
                        "is_dir": True,
                        "icon": QIcon(_browser_choose_icon_path(kind, edge)),
                    })
        except Exception:
            pass
        
        # Add an option: connect to nextwork share drive
        items.append({
            "label": self.i18n.t("virtual_root.conn_to_network_share"), #Connect to network share…",
            "abs_path": "::CONNECT_UNC::",
            "is_dir": False,
            "icon": QIcon(_browser_choose_icon_path("connect_net", edge)),
        })
        return items

    # Count duplicate image size
    def _wokr_folder_count_duplicate_size(self, groups):
        # Summary file size from second to end
        return sum(
            self.phashes[p]["size"] for group in groups for p in group[1:]
            if p in self.phashes and isinstance(self.phashes[p], dict) and "size" in self.phashes[p]
        ) / (1024 * 1024)  # MB

    # Get paths of selected images
    def _path_get_selected_paths(self) -> list:
        return [cb.path for cb in self.scroll.findChildren(QCheckBox) if cb.isChecked()]

    # Display files and sub folders in the start_dir
    def _browser_show(self, start_dir: str = None):
        self.browser_folder = start_dir
        browser_folder_str = self.browser_folder if self.browser_folder != VIRTUAL_ROOT else self.i18n.t("virtual_root.root")
        self.browser_path_label.setText(browser_folder_str)
        
        self.related_files_mode = False

        if self.action != "show_browser":
            self._host_set_head('show_browser')
        
        try:
            self.cfg.set("ui.last_browser_path", self.browser_folder)  # autosave=True 預設會立即寫檔
        except Exception as e:
            print(f"[cfg] save last_browser_path failed: {e}")

        if self._db_load_filelist(self.browser_folder):
            self._db_load_exceptions(self.browser_folder)
            self._db_load_progress(self.browser_folder)
            self.exclude_input.setText(self.exception_folder)
        else:
            self._work_folder_clear_variable()
            self.exclude_input.setText("")

        self.action = "show_browser"
        self._chkbox_controller()
        self._btn_controller()
        self._status_refresh_text()

        # Build body
        cur_dir = self.browser_folder or (self.work_folder if self.work_folder else os.path.expanduser("~"))
        if not cur_dir or not os.path.isdir(cur_dir):
            cur_dir = os.path.expanduser("~")
        self._browser_build_body(cur_dir)

    # Build browser body
    def _browser_build_body(self, current_dir: str):
        cont = QWidget()
        v = QVBoxLayout(cont)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(1)
        
        # Use list support drag and drop
        listw = BrowserListWidget(self)
        listw.setViewMode(QListView.IconMode)
        listw.setSelectionMode(QAbstractItemView.ExtendedSelection)
        listw.setResizeMode(QListView.Adjust)
        listw.setMovement(QListView.Static)
        listw.setSpacing(1)
        listw.setIconSize(QSize(128,128))
        listw.setGridSize(QSize(196,196))
        listw.setWordWrap(True)
        listw.setDragDropMode(QAbstractItemView.DragDrop)
        listw.current_dir = current_dir
        
        # Click enter 
        listw.itemActivated.connect(self._browser_action_click)

        # Drag folder
        listw.operationRequested.connect(self._browser_action_move_copy_request)

        # Right click menu
        listw.setContextMenuPolicy(Qt.CustomContextMenu)
        listw.customContextMenuRequested.connect(self._browser_action_context_menu)

        v.addWidget(listw, 1)
        self._browser_listw_ref = listw  # For sort
        self._browser_build_list(listw, current_dir)

        self._host_set_body_normal(cont)

    # Change browser view style: Icon mode or thumbnial mode
    def _browser_apply_view_style(self, lw: QListWidget):
        key = getattr(self, "_browser_view_style_key", "large")  # 'list' | 'small' | 'medium' | 'large' | 'huge'

        if key == "list":
            lw.setViewMode(QListView.ListMode)
            lw.setWrapping(False)
            lw.setWordWrap(False)
            lw.setSpacing(1)
            lw.setIconSize(QSize(24, 24))
            lw.setGridSize(QSize())
            lw.setResizeMode(QListView.Adjust)
            lw.setUniformItemSizes(True)
        else:
            lw.setViewMode(QListView.IconMode)
            lw.setWrapping(True)
            lw.setWordWrap(True)
            lw.setResizeMode(QListView.Adjust)
            lw.setUniformItemSizes(False)

            if key == "small":
                icon = 96
            elif key == "medium":
                icon = 128
            elif key == "large":
                icon = 196
            elif key == "huge":
                icon = 361

            text_h = 40                       # Reserved high for filename
            cell   = icon + 10                # Padding around the icon
            lw.setIconSize(QSize(icon, icon)) 
            lw.setGridSize(QSize(cell, cell + text_h)) 
            lw.setSpacing(1)
        _browser_drag_force_enable(lw)

    # Fill files and folders to browser body
    def _browser_build_list(self, lw: QListWidget, current_dir: str):
        self.action = "show_browser"
        self._btn_controller()
        try:
            lw.blockSignals(True)
            lw.clear()
            self._browser_listw_ref = lw

            self._browser_apply_view_style(lw)
            edge = lw.iconSize().width()
            icon_dir  = QIcon(_browser_choose_icon_path("folder", edge))
            icon_file = QIcon(_browser_choose_icon_path("file", edge))
            icon_up   = QIcon(_browser_choose_icon_path("arrow", edge))

            # First row, back to parent
            if _virtual_root_is_virtual_root(self.browser_folder):
                items = self._virtual_root_list_items(QApplication.instance(), edge)
                for it in items:
                    qit = QListWidgetItem(it["label"])
                    if it.get("icon"):
                        qit.setIcon(it["icon"])
                    qit.setData(Qt.UserRole, it["abs_path"])
                    lw.addItem(qit)
                #self._browser_update_breadcrumb(["My Computer"])
                return

            # Add ".." in other folder
            parent_dir = os.path.dirname(current_dir.rstrip(os.sep)) or current_dir
            if parent_dir and os.path.abspath(parent_dir) != os.path.abspath(current_dir):
                it = QListWidgetItem("..")
                it.setData(Qt.UserRole, parent_dir)
                it.setIcon(icon_up if not icon_up.isNull() else icon_dir)
                lw.addItem(it)

            if _virtual_root_is_virtual_root(self.browser_folder):
                items = self._virtual_root_list_items(QApplication.instance(), edge)
                for it in items:
                    qit = QListWidgetItem(it["label"])
                    if it.get("icon"):
                        qit.setIcon(it["icon"])
                    qit.setData(Qt.UserRole, it["abs_path"])
                    lw.addItem(qit)

                return
            
            # Get folder
            try:
                entries = os.listdir(current_dir)
            except Exception as e:
                entries = []
                print(f"[Error] listdir {current_dir}: {e}")

            # Filter hidden files and folders
            entries = [e for e in entries if not e.startswith(".")]

            # Collect item
            items = []
            for name in entries:
                p = os.path.join(current_dir, name)
                try:
                    stat = os.stat(p)
                    mtime = stat.st_mtime
                except Exception:
                    mtime = 0

                if os.path.isdir(p):
                    items.append({"type": "dir", "name": name, "path": p, "mtime": mtime, "ext": ""})
                elif os.path.isfile(p):
                    ext = os.path.splitext(name)[1].lower()
                    items.append({"type": "file", "name": name, "path": p, "mtime": mtime, "ext": ext})
            # Sort
            key = self._browser_sort_key
            asc = self._browser_sort_asc
            def sort_key(rec):
                if key == "mtime":
                    return rec["mtime"]
                if key == "type":
                    return (0 if rec["type"]=="dir" else 1, rec["ext"], rec["name"].lower())
                return (0 if rec["type"]=="dir" else 1, rec["name"].lower())
            items.sort(key=sort_key, reverse=not asc)

            # Build QListWidgetItem（Set system icon / text）
            pending = []   # [(row_index, abs_path)]
            edge = lw.gridSize().width()

            for rec in items:
                name  = rec["name"]
                p     = rec["path"]
                mtime = rec["mtime"]
                it = QListWidgetItem()
                it.setData(Qt.UserRole, p)
                it.setToolTip(p)

                # File/Folder icon
                if rec["type"] == "dir":
                    it.setIcon(icon_dir)
                else:
                    it.setIcon(icon_file)
                
                # Text
                if self._browser_view_style_key == "list":
                    # Get mtime
                    mtime_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                    # file name and mtime
                    it.setText(f"{name}\n{mtime_str}")
                    # Adjust text high
                    fm = lw.fontMetrics()
                    line_h = fm.height()
                    it.setSizeHint(QSize(0, line_h * 2 + 10))
                else:
                    # icon mode only display file name
                    it.setText(name)

                it.setFlags(it.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable
                            | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
                lw.addItem(it)
                # icon mode use lazy thumbnial
                if self._browser_view_style_key != "list" and rec["type"] == "file" and rec["ext"] in EXTS:
                    qimg = self._browser_get_cache(p)
                    if isinstance(qimg, QImage) and not qimg.isNull():
                        it.setIcon(_browser_build_icon_from_qimage(qimg, edge))
                    else:
                        pending.append((lw.count()-1, p))

            # Lazy load thumbnail
            if pending:
                gen_id = getattr(self, "_browser_lazy_gen", 0) + 1
                self._browser_lazy_gen = gen_id

                def _step(i=0):
                    # Return if change folder
                    if gen_id != getattr(self, "_browser_lazy_gen", 0):
                        return

                    # Check life cycle
                    lw_alive = getattr(self, "_browser_listw_ref", None)
                    if lw_alive is None or sip.isdeleted(lw_alive):
                        return

                    if i >= len(pending):
                        return

                    row, abs_path = pending[i]

                    try:
                        if 0 <= row < lw_alive.count():
                            qimg = self._browser_get_cache(abs_path)
                            if not (isinstance(qimg, QImage) and not qimg.isNull()):
                                edge_local = lw_alive.gridSize().width()
                                qimg = _browser_fast_load_thumb_qimage(abs_path, want_edge=edge_local)
                                if isinstance(qimg, QImage) and not qimg.isNull():
                                    self._browser_put_cache(abs_path, qimg)

                            if isinstance(qimg, QImage) and not qimg.isNull():
                                it = lw_alive.item(row)
                                if it is not None and not sip.isdeleted(lw_alive):
                                    edge_local = lw_alive.gridSize().width()
                                    it.setIcon(_browser_build_icon_from_qimage(qimg, edge_local))
                    except RuntimeError:
                        return

                    QTimer.singleShot(1, lambda: _step(i + 1))

                QTimer.singleShot(0, lambda: _step(0))
        finally:
            lw.blockSignals(False)
            curdir_abs = os.path.abspath(current_dir)
            
            # Restore browser view scroll position
            try:
                state = self._browser_view_state.get(curdir_abs)
                if state:
                    # Try to find item whose UserRole == recorded selected path
                    sel_path = state.get("selected")
                    if sel_path:
                        found_item = None
                        for i in range(lw.count()):
                            it = lw.item(i)
                            if it and it.data(Qt.UserRole) and os.path.abspath(it.data(Qt.UserRole)) == os.path.abspath(sel_path):
                                found_item = it
                                break
                        if found_item:
                            lw.setCurrentItem(found_item)
                            # ensure item visible
                            lw.scrollToItem(found_item, QAbstractItemView.PositionAtCenter)
                            # Restore scrollbar if no selection or additionally
                            try:
                                sb = lw.verticalScrollBar()
                                if sb and "scroll" in state:
                                    sb.setValue(int(state["scroll"]))
                            except Exception:
                                pass
            except Exception:
                pass
        
    # Save browser thumbnail cache
    def _browser_put_cache(self, key: str, qimg: QImage):
        if not isinstance(qimg, QImage) or qimg.isNull():
            return
        cache = self._browser_thumb_cache
        cache[key] = qimg
        cache.move_to_end(key)
        while len(cache) > getattr(self, "_browser_thumb_cache_limit", 512):
            cache.popitem(last=False)

    # Get browser thumbnail cache
    def _browser_get_cache(self, key: str):
        cache = getattr(self, "_browser_thumb_cache", None)
        if not cache:
            return None
        qimg = cache.get(key)
        if isinstance(qimg, QImage) and not qimg.isNull():
            cache.move_to_end(key)
            return qimg
        return None
    
    # Process move or copy files/folders
    def _browser_action_move_copy_request(self, op_hint: str, src_abs_list: list, dest_dir_abs: str):
        self.action = "file_operation"
        self._btn_controller()
        if not src_abs_list or not os.path.isdir(dest_dir_abs):
            return
        
        # When drag/drop files/folders, remove browser view record
        try:
            del self._browser_view_state[self.browser_folder]
        except Exception:
            pass

        # If operation is not set, popup question
        if op_hint not in ("move", "copy"):
            title = self.i18n.t("dlg.browser_move_copy.title", default="Move or Copy?")
            msg   = self.i18n.t("dlg.browser_move_copy.body",  default="Do you want to move or copy the selected item(s)?")
            box = QMessageBox(self)
            box.setWindowTitle(title)
            box.setText(msg)
            move_btn   = box.addButton(self.i18n.t("btn.browser_move", default="Move"), QMessageBox.YesRole)
            copy_btn   = box.addButton(self.i18n.t("btn.browser_copy", default="Copy"), QMessageBox.NoRole)
            cancel_btn = box.addButton(self.i18n.t("btn.browser_cancel", default="Cancel"), QMessageBox.RejectRole)
            box.exec_()
            clicked = box.clickedButton()
            if clicked == move_btn:
                op_hint = "move"
            elif clicked == copy_btn:
                op_hint = "copy"
            else:
                return
        changed = False
        ops = []
        for src_abs in src_abs_list:
            if self._system_pertimes_processevent(0.5):
                QApplication.processEvents()
            if not os.path.exists(src_abs):
                continue

            # Dst can't be sub dir or its self of src folder
            try:
                src_abs_norm  = os.path.abspath(src_abs)
                dest_dir_norm = os.path.abspath(dest_dir_abs)
                final_dst_norm = os.path.join(dest_dir_norm, os.path.basename(src_abs_norm))
                if os.path.commonpath([src_abs_norm, final_dst_norm]) == src_abs_norm:
                    self._popup_information(self.i18n.t("err.fail_to_op_files", default="File operation failed: ") +
                            f"Cannot {op_hint} a directory '{src_abs}' into its descendant '{final_dst_norm}'.")
                    continue
            except Exception:
                pass

            if os.path.isdir(src_abs):
                # Package if move/copy src is folder
                try:
                    self._browser_move_copy_folder(src_abs, dest_dir_abs, op_hint)  # op_hint: "move"/"copy"
                    changed = True
                except Exception as e:
                    self._popup_information(self.i18n.t("err.fail_to_op_files", default="File operation failed: ") + str(e))
                continue

            # Gen dst and process if there is same file name in dst
            base = os.path.basename(src_abs)
            dst_abs = os.path.join(dest_dir_abs, base)
            if os.path.exists(dst_abs):
                base_noext, ext = os.path.splitext(base)
                dst_abs = _path_gen_unique_name(dest_dir_abs, base_noext, ext)

            try:
                if op_hint == "copy":
                    shutil.copy2(src_abs, dst_abs)
                    self.status.setText(self.i18n.t("status.coping_file",src = src_abs, dst = dst_abs))
                else:
                    shutil.move(src_abs, dst_abs)
                    self.status.setText(self.i18n.t("status.moving_file",src = src_abs, dst = dst_abs))
                ops.append((src_abs, dst_abs,op_hint))
                changed = True
            except Exception as e:
                self._popup_information(self.i18n.t("err.fail_to_op_files", default="File operation failed: ") + str(e))

        if changed:
            lw = getattr(self, "_browser_listw_ref", None)
            curdir = getattr(self, "browser_folder", None)
            if ops:
                self._browser_sync_batch(ops)
            self._status_refresh_text()    
            if lw and curdir:
                self._browser_build_list(lw, curdir)

    # Right click of browser
    def _browser_action_context_menu(self, pos):
        lw = getattr(self, "_browser_listw_ref", None)
        if not lw:
            return

        item_under_cursor = lw.itemAt(pos)

        # When right click in empty area
        if item_under_cursor and not item_under_cursor.isSelected():
            lw.clearSelection()
            item_under_cursor.setSelected(True)

        # Select area one or many
        sel_items = lw.selectedItems()
        sel_paths = [it.data(Qt.UserRole) for it in sel_items if it and it.data(Qt.UserRole)]
        curdir = getattr(self, "browser_folder", None) or os.path.expanduser("~")

        m = QMenu(lw)

        # Make new folder
        act_new_folder = m.addAction(self.i18n.t("btn.browser_new_folder", default="New Folder…"))

        # Make new sub folder, if cursor on a folder
        act_new_sub = None
        if item_under_cursor:
            p_hover = item_under_cursor.data(Qt.UserRole)
            if p_hover and os.path.isdir(p_hover):
                act_new_sub = m.addAction(self.i18n.t("btn.browser_new_subfolder", default="New subfolder here…"))

        # When select files/folders display items
        act_rename = act_move = act_copy = act_delete = None
        if sel_paths:
            m.addSeparator()
            act_rename = m.addAction(self.i18n.t("btn.browser_rename", default="Rename…"))
            act_move   = m.addAction(self.i18n.t("btn.browser_move_to", default="Move to…"))
            act_copy   = m.addAction(self.i18n.t("btn.browser_copy_to", default="Copy to…"))
            act_delete = m.addAction(self.i18n.t("btn.browser_delete",  default="Delete"))

            # If select many files/folders, disable rename
            if len(sel_paths) != 1:
                act_rename.setEnabled(False)

        picked = m.exec_(lw.mapToGlobal(pos))
        if not picked:
            return

        # Create new folder
        if picked is act_new_folder:
            self._browser_create_new_folder(curdir, self.i18n.t("btn.browser_new_folder", default="New Folder…"))
            if lw and curdir:
                self._browser_build_list(lw, curdir)
            return

        # Create new sub folder
        if act_new_sub and picked is act_new_sub:
            self._browser_create_new_folder(p_hover, self.i18n.t("btn.browser_new_subfolder", default="New subfolder here…"))
            if lw:
                self._browser_build_list(lw, getattr(self, "browser_folder", curdir))
            return

        # Rename
        if act_rename and picked is act_rename and len(sel_paths) == 1:
            old_abs = sel_paths[0]
            old_name = os.path.basename(old_abs)
            new_name, ok = self._popup_input(
                self.i18n.t("btn.browser_rename", default="Rename"),
                self.i18n.t("label.browser_new_name", default="New name:"),
                old_name)
            ops = []
            if ok and new_name and new_name != old_name:
                new_abs = os.path.join(os.path.dirname(old_abs), new_name)
                if os.path.exists(new_abs):
                    self._popup_information(self.i18n.t("err.fail_to_file_exists", default="Target name already exists."))
                else:
                    pre_len = len(old_abs)
                    if os.path.isdir(old_abs):
                        old_files = _path_collect_files(old_abs)
                        for org_abs in old_files:
                            suffix = org_abs[pre_len:]
                            rnew_abs = new_abs + suffix
                            ext = os.path.splitext(org_abs)[1].lower()
                            if ext in EXTS:
                                ops.append((org_abs,rnew_abs,"move"))
                    else:
                        ops.append((old_abs,new_abs,"move"))
                    os.rename(old_abs, new_abs)
                    if ops:
                        self._browser_sync_batch(ops)
            self._status_refresh_text()
            if lw and curdir:
                self._browser_build_list(lw, curdir)
            return

        # Move or Copy
        if (act_move and picked is act_move) or (act_copy and picked is act_copy):
            dest = QFileDialog.getExistingDirectory(
                self,
                self.i18n.t("btn.browser_select_folder", default="Select Folder"),
                os.path.dirname(sel_paths[0]) if sel_paths else curdir
            )
            if not dest:
                return

            op = "move" if picked is act_move else "copy"

            self._browser_action_move_copy_request(op, sel_paths, dest)
            
            return

        # Delete
        if act_delete and picked is act_delete:
            do_delete = True
            try:
                if getattr(self, "confirm_delete", True):
                    title = self.i18n.t("dlg.browser_delete_files.title", default="Delete")
                    body  = self.i18n.t("dlg.browser_delete_files.body", default="Delete selected item(s)?", cnt=len(sel_paths))
                    do_delete = (self._popup_question(title, body, True) == QMessageBox.Yes)
            except Exception:
                pass
            if not do_delete:
                return

            ops = []
            for p in sel_paths:
                try:
                    if os.path.isfile(p):
                        self.status.setText(self.i18n.t("status.deleting_file",src = p))
                        os.remove(p)
                        ops.append((p,None,"delete"))
                    elif os.path.isdir(p):
                        self.status.setText(self.i18n.t("status.deleting_folder",src = p))
                        old_files = _path_collect_files(p)
                        for old_abs in old_files:
                            ext = os.path.splitext(old_abs)[1].lower()
                            if ext in EXTS:
                                ops.append((old_abs,None,"delete"))
                        shutil.rmtree(p)                    
                except Exception as e:
                    self._popup_information(self.i18n.t("err.fail_to_op_files", default="File operation failed: ") + str(e))
            if ops:
                self._browser_sync_batch(ops)
            self._status_refresh_text()
            if lw and curdir:
                self._browser_build_list(lw, curdir)
            return

    # Browser back to parent folder    
    def _btn_action_browser_to_parent(self, current_dir: str):
        cur = getattr(self, "browser_folder", None)
        if not cur:
            return

        if IS_WIN:
            # Drive root to virtual root
            if _virtual_root_is_drive_root(cur):
                self._browser_show(VIRTUAL_ROOT)
                return
            # UNC share root to virtual root
            if _virtual_root_is_unc_share_root(cur):
                self._browser_show(VIRTUAL_ROOT)
                return
            # Don't change if in virtual root already
            if _virtual_root_is_virtual_root(cur):
                return
        parent_dir = os.path.dirname(current_dir.rstrip(os.sep)) or current_dir
        if os.path.abspath(parent_dir) != os.path.abspath(current_dir):
            self.action = "select_folder"
            self._btn_controller()
            self.paused = False
            self.progress.setVisible(False)
            self._browser_show(parent_dir)

    # Handle mouse click in browser
    def _browser_action_click(self, item: QListWidgetItem):
        self.action = "select_folder"
        self._btn_controller()
        self.paused = False
        self.progress.setVisible(False)

        abs_path = item.data(Qt.UserRole)
        if self.stage == "collecting":
            self.paused = True
        if not abs_path:
            return

        # Process Windows virtual root
        # 1) Connect to nextwork share drive
        if IS_WIN and abs_path == "::CONNECT_UNC::":
            text, ok = self._popup_input(
                self.i18n.t("dialog.connect_share_title", default="連接到共用資料夾"),
                self.i18n.t("dialog.connect_share_label", default="輸入路徑（例如：\\\\server\\share）：")
            )
            if ok and text:
                unc = text.strip()
                if unc.startswith("\\\\"):
                    # Record current scroll potision
                    lw = getattr(self, "_browser_listw_ref", None)
                    sb_val = lw.verticalScrollBar().value() if (lw and lw.verticalScrollBar()) else 0
                    self._browser_view_state[self.browser_folder] = {"scroll": int(sb_val), "selected": unc}
                    QTimer.singleShot(0, lambda path=unc: self._browser_show(path))
                else:
                    self.toast("Invalid UNC path. Example: \\\\server\\share")
            return

        # 2) In virtual root
        if IS_WIN and _virtual_root_is_virtual_root(getattr(self, "browser_folder", "")):
            lw = getattr(self, "_browser_listw_ref", None)
            sb_val = lw.verticalScrollBar().value() if (lw and lw.verticalScrollBar()) else 0
            self._browser_view_state[self.browser_folder] = {"scroll": int(sb_val), "selected": abs_path}
            QTimer.singleShot(0, lambda path=abs_path: self._browser_show(path))
            return

        # Record browser scroll position
        rels, roots = _path_abs_to_rels_and_roots(abs_path)
        lw = getattr(self, "_browser_listw_ref", None)
        sb_val = lw.verticalScrollBar().value() if (lw and lw.verticalScrollBar()) else 0
        self._browser_view_state[self.browser_folder] = {"scroll": int(sb_val), "selected": abs_path}

        # Double click to enter folder
        if os.path.isdir(abs_path):
            QTimer.singleShot(0, lambda path=abs_path: self._browser_show(path))
            return

        # Double click file
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in EXTS:
            return
        
        if rels and roots and self.display_same_images:
            for ridx in range(len(rels)-1, -1, -1):
                self.work_folder = roots[ridx]
                self._db_load_progress(self.work_folder)
                if self.stage != "done":
                    self._db_unlock(self.work_folder)
                    self._work_folder_clear_variable()
                    continue
                self.constraints = ConstraintsStore(self.work_folder)
                self.view_groups, self.view_summary = self.constraints.apply_to_all_groups(self.groups)
                idx = next((i for i, grp in enumerate(self.view_groups) if rels[ridx] in grp), None)
                if idx is not None:
                    self.related_files_mode = True
                    self._group_show_detail(idx)
                    return
                else:
                    self._db_unlock(self.work_folder)
                    self._work_folder_clear_variable()
                    QTimer.singleShot(0, lambda path=abs_path: self._group_show_image(path))
                    return
            QTimer.singleShot(0, lambda path=abs_path: self._group_show_image(path))
        else:
            QTimer.singleShot(0, lambda path=abs_path: self._group_show_image(path))

    # Move or copy files/folders to dst and sync to filelist/progress/exclude
    def _browser_move_copy_folder(self, src_dir_abs: str, dest_dir_abs: str, op: str):
        import threading
        if _path_is_child_folder(src_dir_abs, os.path.join(dest_dir_abs, os.path.basename(src_dir_abs))):
            raise RuntimeError(f"Cannot {op} a directory '{src_dir_abs}' into its descendant '{dest_dir_abs}'.")

        if not (os.path.isdir(src_dir_abs) and os.path.isdir(dest_dir_abs)):
            self._popup_information(self.i18n.t("err.fail_to_invalid_folder", default="Invalid folder"))
            return
        
        base = os.path.basename(src_dir_abs.rstrip(os.sep))
        # Gen new position, if collition rename (1)
        if op == "copy":
            new_dir_abs = _path_gen_unique_dir(dest_dir_abs, base)
        else:
            new_dir_abs = _path_gen_unique_dir(dest_dir_abs, base) if os.path.exists(os.path.join(dest_dir_abs, base)) \
                        else os.path.join(dest_dir_abs, base)

        old_files = _path_collect_files(src_dir_abs)
        # Sync to filelist/progress/except
        ops = []
        dirs = []
        pre_len = len(src_dir_abs)
        self.progress.setVisible(True)
        self.progress.setMaximum(len(old_files)+1)  # Include src folder itself
        i = 1
        self.progress.setValue(i)
        
        # make new folder
        os.makedirs(new_dir_abs, exist_ok=True)
        dirs.append(src_dir_abs)

        for old_abs in old_files:
            suffix = old_abs[pre_len:]
            new_abs = new_dir_abs + suffix
            try:
                if os.path.isdir(old_abs):
                    # Make sub folder
                    os.makedirs(new_abs, exist_ok=True)
                    dirs.append(old_abs)
                else:
                    if op == "copy":
                        shutil.copy2(old_abs,new_abs)
                        self.status.setText(self.i18n.t("status.coping_file",src = old_abs, dst = new_abs))
                        QApplication.processEvents()
                    else:
                        shutil.move(old_abs,new_abs)
                        self.status.setText(self.i18n.t("status.moving_file",src = old_abs, dst = new_abs))
                        QApplication.processEvents()                    
                    ext = os.path.splitext(old_abs)[1].lower()
                    if ext in EXTS:
                        ops.append((old_abs, new_abs, op))
            except Exception as e:
                self._popup_information(self.i18n.t("err.fail_to_op_files", default="File operation failed: ") + str(e))
                return

            i = i + 1
            self.progress.setValue(i)
    
        if op == "move":
            for path in sorted(dirs, key=lambda p: len(p), reverse=True):
                os.rmdir(path)
        
        self._browser_sync_batch(ops)
        self._status_refresh_text()

    # Create new folder
    def _browser_create_new_folder(self, parent_dir: str, title: str):
        if not os.path.isdir(parent_dir):
            self._popup_information(self.i18n.t("err.fail_to_invalid_folder", default="Invalid folder"))
            return
        suggested = self.i18n.t("default.browser_new_folder_name", default="New Folder")
        name, ok = self._popup_input(title, self.i18n.t("label.browser_new_name"), suggested)
        if not ok:
            return
        name = name.strip() or suggested
        new_dir = os.path.join(parent_dir, name)
        if os.path.exists(new_dir):
            new_dir = _path_gen_unique_dir(parent_dir, name)
        try:
            os.makedirs(new_dir, exist_ok=False)
        except Exception as e:
            self._popup_information(self.i18n.t("err.fail_to_op_files", default="File operation failed: ") + str(e))
            return

        # Rebuild browser list
        lw = getattr(self, "_browser_listw_ref", None)
        curdir = getattr(self, "browser_folder", None)
        if lw and curdir:
            self._browser_build_list(lw, curdir)
            # Select new folder
            for i in range(lw.count()):
                it = lw.item(i)
                if it and it.data(Qt.UserRole) == new_dir:
                    lw.setCurrentItem(it)
                    lw.scrollToItem(it)
                    break

    # Update cache to new name
    def _browser_cache_rename_key(self, old_abs, new_abs, is_copy=False):
        cache = getattr(self, "_browser_thumb_cache", None)
        if not cache:
            return
        if old_abs in cache:
            if is_copy:
                cache[new_abs] = cache[old_abs]
                cache.move_to_end(new_abs)
            else:
                cache[new_abs] = cache.pop(old_abs)
                cache.move_to_end(new_abs)

    # Update constraints
    def _browser_constraints_rename(self, old_rel: str, new_rel: str):
        try:
            changed = False
            if hasattr(self.constraints, "must_pairs"):
                mp = []
                for a, b in self.constraints.must_pairs:
                    na = new_rel if a == old_rel else a
                    nb = new_rel if b == old_rel else b
                    mp.append((na, nb))
                self.constraints.must_pairs = mp
                changed = True
            if hasattr(self.constraints, "cannot_pairs"):
                cp = []
                for a, b in self.constraints.cannot_pairs:
                    na = new_rel if a == old_rel else a
                    nb = new_rel if b == old_rel else b
                    cp.append((na, nb))
                self.constraints.cannot_pairs = cp
                changed = True
            if hasattr(self.constraints, "ignored_files"):
                ig = set(self.constraints.ignored_files) if not isinstance(self.constraints.ignored_files, set) else self.constraints.ignored_files
                if old_rel in ig:
                    ig.discard(old_rel)
                    if new_rel:
                        ig.add(new_rel)
                    self.constraints.ignored_files = ig
                    changed = True
            if changed:
                self.constraints.save_constraints()
        except Exception as e:
            print(f"[Constraints rename error] {e}")

    # Update groups data
    def _group_replace_path(self, old_rel: str, new_rel: str | None):
        if not hasattr(self, "groups") or not self.groups:
            return
        new_groups = []
        for grp in self.groups:
            changed = False
            repl = []
            for p in grp:
                if p == old_rel:
                    if new_rel:
                        repl.append(new_rel)
                    changed = True
                else:
                    repl.append(p)

            if len(repl) > 1:
                new_groups.append(repl)
            elif not changed:
                new_groups.append(repl)
        self.groups = new_groups

    # Update db with add/delete/replace within a root
    def _db_update(self, root: str, batch_ops: list):
        if not self._db_lock_check_and_create(root):
            return
        try:
            # Load db
            self._db_load_filelist(root)
            self._db_load_progress(root)
            self.constraints = ConstraintsStore(scan_folder=root)

            for a in batch_ops:
                act   = a.get("act")
                orel  = a.get("old_rel")
                nrel  = a.get("new_rel")
                nabs  = a.get("new_abs")

                if act == "add" and nrel:
                    if isinstance(self.image_paths, list) and nrel not in self.image_paths:
                        self.image_paths.append(nrel)
                    # New file, keep hashing empty
                    self.stage = "hashing"
                    self.compare_index = 0
                    self.groups = []
                    self.visited = set()

                elif act == "delete" and orel:
                    if orel in self.image_paths:
                        self.image_paths.remove(orel)
                    if orel in self.phashes:
                        del self.phashes[orel]
                        self.compare_index -= 1
                    # groups / constraints
                    self._group_replace_path(orel, None)
                    try:
                        self.constraints.remove_paths([orel])
                    except Exception:
                        pass

                elif act == "replace" and orel and nrel:
                    # filelist
                    self.image_paths = [nrel if p == orel else p for p in self.image_paths]
                    # progress use same mtime/hash when move or rename files
                    try:
                        st = os.stat(nabs) if nabs else None
                    except Exception:
                        st = None
                    if orel in self.phashes:
                        h = self.phashes.pop(orel)
                        if isinstance(h, dict):
                            if st:
                                h["mtime"] = st.st_mtime
                                h["size"]  = st.st_size
                            self.phashes[nrel] = h
                        else:
                            self.phashes[nrel] = {
                                "hash": h,
                                "mtime": (st.st_mtime if st else self.phashes.get(orel, {}).get("mtime", 0)),
                                "size":  (st.st_size  if st else self.phashes.get(orel, {}).get("size",  0)),
                            }
                    # groups / constraints
                    self._group_replace_path(orel, nrel)
                    self._browser_constraints_rename(orel, nrel)

            # Update counter
            self.previous_file_counter = len(self.image_paths)
            self.view_groups_update = True
            if self.stage == "done":
                self.duplicate_size = self._wokr_folder_count_duplicate_size(self.groups)

            # Save db
            self._db_save_filelist(root)
            self._db_save_progress(root)
            try:
                self.constraints.save_constraints()
            except Exception:
                pass
        finally:
            self._db_unlock(root)

    # Sync db in each root
    def _browser_sync_batch(self, ops: list[tuple[str|None, str|None, str]]):
        # backup current folder
        if self.work_folder:
            cur_folder = self.work_folder 
        else:
            cur_folder = self.browser_folder
        
        # Process rename files thumbnail cache
        all_actions = []
        for old_abs, new_abs, op in ops:
            if op in ("move", "copy") and old_abs and new_abs:
                self._browser_cache_rename_key(old_abs, new_abs, is_copy=(op == "copy"))
            all_actions.extend(_plan_fs_sync_operations(old_abs, new_abs, op))

        if not all_actions:
            return

        # Based on root
        grouped = {}
        for a in all_actions:
            root = a["root"]
            orel = a["old_rel"]
            nrel = a["new_rel"]
            nabs = os.path.join(root, nrel) if nrel else None
            grouped.setdefault(root, []).append({
                "act": a["act"],
                "old_rel": orel,
                "new_rel": nrel,
                "new_abs": nabs,
            })

        # For each root update db
        for root, batch_ops in grouped.items():
            try:
                self.status.setText(self.i18n.t("status.updating_db",db = root))
                self._db_update(root, batch_ops)
            except Exception as e:
                print(f"[Warn] batch exec failed on {root}: {e}")
        
        # Restore self data
        self._work_folder_clear_variable()
        self._db_load_filelist(cur_folder)
        self._db_load_progress(cur_folder)
        self._db_load_exceptions(cur_folder)
        self.constraints = ConstraintsStore(scan_folder = cur_folder)

    # Mark selected images same
    def _btn_action_mark_images_same(self):
        sel_path = self._path_get_selected_paths()
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
            self._alg_handler()
        else:
            self._group_show_api()

    # Clear mark on selected images
    def _btn_action_unmarked_images(self):
        if not self.view_groups or self.current >= len(self.view_groups):
            return

        grp = self.view_groups[self.current]
        self.constraints.clear_constraints_for_group(grp)
        self.constraints.save_constraints()
        self.view_groups_update = True
        self._group_show_api()

    # Mark selected images different
    def _btn_action_mark_images_separate(self):
        sel_path = self._path_get_selected_paths()
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
            self._alg_handler()
        else:
            self._group_show_api()

    # Mark selected images ignore
    def _btn_action_mark_images_ignore(self):
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
            self._alg_handler()
        else:
            self._group_show_api()
    
    # Apply language configuration for bind static text
    def _cfg_ui_apply_language(self, lang_code: str):
        # "auto" using system language
        self.i18n.set_locale(lang_code)
        self.i18n_binder.retranslate()
        self._status_refresh_text()
        self._chkbox_controller()
        self.exclude_input.setPlaceholderText(self.i18n.t("input.exclude_placeholder"))
        # Save to QSettings
        self.settings.setValue("locale", lang_code)

    # Apply language configuration for dynamic information
    def _cfg_ui_retranslate_texts(self):
        if self.action == "show_group":
            self._group_show_detail()
        if self.action == "show_overview":
            self._overview_show_api()
        if self.action == "show_browser":
            self._browser_show(self.browser_folder)

    # Apply language configuration for status text
    def _status_refresh_text(self):
        # Show i18n context based on self.stage / self.current / self.groups
        if self.stage == "done":
            if self.duplicate_size >= 1024:
                size_str = f"{self.duplicate_size / 1024:,.2f} GB"
            else:
                size_str = f"{self.duplicate_size:,.2f} MB"
            if self.action == "show_browser":
                self.status.setText(
                    self.i18n.t("status.browser_done_summary",
                                groups=len(self.groups),
                                images=len(self.phashes))
                )
            else:
                self.status.setText(
                    self.i18n.t("status.done_summary",
                                groups=len(self.groups),
                                view=len(self.view_groups),
                                size=size_str,
                                images=len(self.phashes))
                )
        elif self.stage == "hashing":
            if self.paused or self.action == "show_browser":
                self.status.setText(self.i18n.t("status.hashing_pause"))
            elif self.action == "file_operation":
                self.status.setText(self.i18n.t("status.hashing_new_files"))

        elif self.stage == "comparing":
            if self.paused or self.action == "show_browser":
                self.status.setText(self.i18n.t("status.comparison_pause"))
            elif self.action == "file_operation":
                self.status.setText(self.i18n.t("status.comparison_new_files"))
        else:
            # init
            self.status.setText(self.i18n.t("status.press_scan_button"))

    # Reload group thumbnail
    def _group_reload_thumbnails(self):
        if self.action == "show_group":
            self._group_show_api()

    # Reload overview thumbnail
    def _overview_reload_thumbnails(self):
        if self.action == "show_overview":
            self._overview_show_api()

    # Handle APP close
    def closeEvent(self, event):
        self._btn_action_exit_and_save()
        event.accept()

    # Check if timer is expired
    def _system_pertimes_processevent(self,times):
        now = time.time()
        if now-self.last_ui_update > times:
            self.last_ui_update = now
            return True
        else:
            return False

    # Sort groups and copy to self.groups
    def _group_sort_then_copy(self, groups):
        group_keys = []
        for grp in groups:
            # Sort images in group by character order
            grp_sorted = sorted(grp, key=lambda p: os.path.basename(p).lower())
            # Sort groups by path
            group_keys.append((grp_sorted, _group_gen_sort_key(grp_sorted)))

        group_keys.sort(key=lambda x: x[1])
        self.groups = [grp for grp, _ in group_keys]

    # Check if db is locked by self
    def _db_is_lock_by_self(self, root):
        if root is None:
            return False

        self.lock_file = os.path.join(root, ".duplicate.lock")

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

    # Check if db is locked by self and create lock
    def _db_lock_check_and_create(self, root, alert = True):
        self.lock_file = os.path.join(root, ".duplicate.lock")

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
                    if alert:
                        box = QMessageBox(self)
                        box.setIcon(QMessageBox.Warning)
                        box.setWindowTitle(self.i18n.t("lock.title"))
                        box.setText(f"{self.i18n.t('lock.folderlock', folder = root, machine=lock_data.get('machine'),updt=updated_str)}")
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
            self.lock_file = os.path.join(root, ".duplicate.lock")
            self.lock_data = {
                "machine": platform.node(),
                "pid": os.getpid(),
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(self.lock_file, "w", encoding="utf-8") as f:
                json.dump(self.lock_data, f)
            return True
        except Exception as e:
            QMessageBox.critical(self, "Lock Error", f"Failed to create lock file:\n{e}")
            return False

    # Update db lock
    def _db_lock_update(self, root):
        if self._db_is_lock_by_self(root):
            try:
                self.lock_data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(self.lock_file, "w", encoding="utf-8") as f:
                    json.dump(self.lock_data, f)
            except Exception as e:
                print(f"[Error] Failed to update lock file: {e}")

    # Unlock db
    def _db_unlock(self, root):
        if self._db_is_lock_by_self(root):
            try:
                if hasattr(self, "lock_file") and os.path.exists(self.lock_file):
                    os.remove(self.lock_file)
            except Exception as e:
                print(f"[Error] Failed to remove lock file: {e}")

    # Popup question dialog
    def _popup_question(self, title, text, default):
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

    # Popup input dialog
    def _popup_input(self, title, label, default_text=""):
        try:
            dlg = QInputDialog(self)
            dlg.setWindowTitle(title)
            dlg.setLabelText(label)
            dlg.setTextValue(default_text)

            dlg.setOkButtonText(self.i18n.t("btn.ok", default="確定"))
            dlg.setCancelButtonText(self.i18n.t("btn.cancel", default="取消"))
            
            if dlg.exec_() == QInputDialog.Accepted:
                return dlg.textValue(), True
            else:
                return "", False
        except Exception as e:
            print(f"[Error] popup_input_modal failed: {e}")
            return "", False

    # Get absolutely path
    def _path_get_abs_path(self, rel_path):
        abs_path = os.path.join(self.work_folder, rel_path)

        # Normalize path (remove redundant .\.）
        abs_path = os.path.normpath(abs_path)

        if os.name == 'nt':  # Windows
            # Handle POSIX network path //server/share
            if abs_path.startswith("//"):
                # Transfer prefix // to \\
                abs_path = "\\\\" + abs_path[2:]
            # Other / change to \
            abs_path = abs_path.replace("/", "\\")
        else:
            # In macOS / Linux using POSIX
            abs_path = abs_path.replace("\\", "/")

        return abs_path

    # Toggle checkbox
    def _chkbox_toggle(self, index):
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

    # Clear work folder variable
    def _work_folder_clear_variable(self):
        self.stage = None
        self.work_folder = None
        self.phashes = {}
        self.groups = []
        self.image_paths = []
        self.overview_page = 0
        self.progress_file = None
        self.exceptions_file = None
        self.compare_index = 0
        self.visited = set()
        self.constraints = None
        self.view_groups_update = False
        self.exception_folder = None

    def _btn_action_scan(self):        
        QApplication.processEvents()

        #self._work_folder_clear_variable()
        
        # Update root
        self.work_folder = self.browser_folder

        # Lock folder
        if self._db_lock_check_and_create(self.work_folder)==False:
            self.work_folder = None
            return
        
        # reset states
        self.action = "collecting"
        self.constraints = ConstraintsStore(scan_folder=self.work_folder)
        self.view_groups_update = True
        self.current = self.last_group_index

        # If file list is exist, asking for re-scan folder
        if self._db_load_filelist(self.work_folder):
            if self.stage == "done":
                title = self.i18n.t("dlg.filelist.title")
                body = self.i18n.t(
                    "dlg.filelist.body",
                    last_scan_time=self.last_scan_time or self.i18n.t("common.unknown")
                )
                reply = True if self._popup_question(title, body, False)==QMessageBox.Yes else False
            elif self.stage == "hashing" or self.stage == "comparing":
                reply = False
            else:
                reply = True

            if reply == False:
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
                
                self._alg_handler()
                return
        
        # Scan folder
        original_stage = self.stage
        self.stage = "collecting"
        
        # Change to processing UI
        self._host_set_body_normal(QWidget())
        self._host_set_head('show_browser')
        self.path_lbl.setText(self.work_folder)

        self._btn_controller()
        self._chkbox_controller()

        new_image_paths = []
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        exclude_dirs = {d.strip().lower() for d in self.exclude_input.text().split(",") if d.strip()}
        for root, dirs, files in os.walk(self.work_folder):
            dirs[:] = [d for d in dirs if not any(ex in d.lower() for ex in exclude_dirs)]
            for f in files:
                if self._system_pertimes_processevent(0.1):
                    QApplication.processEvents()
                if self.paused:
                    self._db_unlock(self.work_folder)
                    self.progress.setVisible(False)
                    self.work_folder = None
                    self.paused = False
                    self._browser_show(self.browser_folder)
                    return
                abs_path = os.path.join(root,f)
                rel_path = os.path.relpath(abs_path, self.work_folder).replace("\\","/").lower()
                if os.path.splitext(f.lower())[1] in EXTS:
                    if(os.path.getsize(abs_path)>50000):
                        new_image_paths.append(rel_path)
                        self.status.setText(self.i18n.t("status.found_new_images",new_image=len(new_image_paths),root=self.work_folder))
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
                self._group_sort_then_copy(new_groups)
                
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
                    if self._system_pertimes_processevent(0.3):
                        QApplication.processEvents()
                    if self.paused:
                        self._db_unlock(self.work_folder)
                        self.progress.setVisible(False)
                        self.work_folder = None
                        self.paused = False
                        self._browser_show(self.browser_folder)
                        return
                    completed += 1
                    self.progress.setValue(completed)
                    self.status.setText(self.i18n.t("status.checked",completed=completed,total=len(self.phashes),path=path))
                    if self.exit == True:
                        return
                    h = self.phashes[path]
                    if not isinstance(h,dict) or "hash" not in h:
                        continue
                    try:
                        abs_path = self._path_get_abs_path(path)
                        current_mtime = os.path.getmtime(abs_path)
                        current_size = os.path.getsize(abs_path)
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
        self.last_scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Save
        self._db_save_filelist(self.work_folder)
        self._db_save_progress(self.work_folder, self.stage)
        self._db_save_exceptions(self.work_folder)
        self._alg_handler()

    def _alg_hashing(self):
        self.action = "hashing"
        self.stage = "hashing"
        self.paused = False
        self._btn_controller()
        self._host_set_head('show_browser')
        self._host_set_body_normal(QWidget())
       
        self._chkbox_controller()
        
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
        QApplication.processEvents()        

        n = len(self.image_paths)
        remaining_hash_index = len(self.phashes)
        if n:
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
                    self.status.setText(self.i18n.t("status.hashing_pause"))
                    self._db_save_progress(self.work_folder, stage="hashing")
                    self.constraints.save_constraints()
                    self._db_unlock(self.work_folder)
                    self._work_folder_clear_variable()
                    self._browser_show(self.browser_folder)
                    return

                batch = [
                    self._path_get_abs_path(p)
                    for p in self.image_paths[i:i+BATCH]
                    if p not in self.phashes
                ]

                futs = {exe.submit(_alg_hashing_api, p): p for p in batch}

                for f in as_completed(futs):
                    if self.paused:
                        self.status.setText(self.i18n.t("status.hashing_pause"))
                        self._db_save_progress(self.work_folder, stage="hashing")
                        self.constraints.save_constraints()
                        self._db_unlock(self.work_folder)
                        self._work_folder_clear_variable()
                        self._browser_show(self.browser_folder)
                        return
                    p = futs[f]
                    rel_path = os.path.relpath(p, self.work_folder).replace("\\","/").lower()
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
                    if self._system_pertimes_processevent(0.5):
                        if self.display_img_dynamic_cb.isChecked():
                            self._alg_hashing_show_current_image(f"{self.i18n.t('msg.hashing')}",rel_path)
                        else:
                            self._host_set_head('show_browser')
                            self._host_set_body_normal(QWidget())
                        QApplication.processEvents()

                    remaining_hash_index += 1
                    completed = completed + 1;
                    self.progress.setValue(remaining_hash_index)
                    elapsed = time.time() - start_time
                    eta = max(0,(elapsed / completed) * (n - remaining_hash_index))
                    eta_str = time.strftime('%H:%M:%S', time.gmtime(eta))
                    self.status.setText(self.i18n.t("status.hashing_eta", eta = eta_str, remaining = remaining_hash_index, total=n, path=os.path.basename(p)))

        QApplication.processEvents()
        self._db_save_progress(self.work_folder, self.stage)
        self._alg_comparing_api()

    def _alg_comparing_api(self):
        self.action = "comparing"
        self.stage = "comparing"
        self.paused = False
        self._btn_controller()
        self._chkbox_controller()
        self._host_set_head('show_browser')
        self._host_set_body_normal(QWidget())
        self._alg_comparing_pairwise()
    
    def _alg_comparing_pairwise(self):
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
        if total:
            self.progress.setVisible(True)
            self.progress.setMaximum(total)
            self.progress.setValue(self.compare_index)

        MAX_LOOKAHEAD = _math_clamp(8*(self.similarity_tolerance+1) ** 2, 64, 384)
        completed = 0
        
        t_report = int(self.similarity_tolerance)     # UI threshold
        delta    = min(3, t_report // 2)              # t/2，max 3
        t_link   = t_report + delta                   # edge
        
        for i, (p1, h1) in enumerate(items[self.compare_index:], start=self.compare_index):
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
                self._db_save_progress(self.work_folder, stage="comparing", extra={"compare_index": self.compare_index})
                self._db_unlock(self.work_folder)
                self._work_folder_clear_variable()
                self._browser_show(self.browser_folder)
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
                    self.visited.remove(p1)
                    self._db_save_progress(self.work_folder, stage="comparing", extra={"compare_index": self.compare_index})
                    self.constraints.save_constraints()
                    self._db_unlock(self.work_folder)
                    self._work_folder_clear_variable()
                    self._browser_show(self.browser_folder)
                    return
                if self._system_pertimes_processevent(0.5):
                    if self.display_img_dynamic_cb.isChecked():
                        self._alg_comparing_show_pair_images(p1,p2)
                    else:
                        self._host_set_head('show_browser')
                        self._host_set_body_normal(QWidget())
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
                        self._db_save_progress(self.work_folder, stage="comparing", extra={"compare_index": self.compare_index})
                        self.view_groups_update = True
                        self.paused = True
                        self._group_show_api()
                        return

        self._group_sort_then_copy(new_grps)
        self.compare_index = len(self.phashes)
        self.progress.setValue(total)
        QApplication.processEvents()
        self.stage = "done"
        self.visited = set()
        self._db_save_progress(self.work_folder, stage="done")
        self.view_groups_update = True        
        self._overview_show_api()

    def _alg_handler(self):
        #Resume stage
        if self.stage == "done":
            self.compare_index = len(self.phashes)
            self._overview_show_api()
            return
        elif self.stage == "comparing":
            self._alg_comparing_api()
            return
        else:
            self.status.setText(self.i18n.t("status.resuming_hashing"))
            self._alg_hashing()
            return                        

    def _alg_hashing_show_current_image(self, label, path):
        abs_path = self._path_get_abs_path(path)
        try:
            cont = QWidget()
            v = QVBoxLayout(cont)
            img = Image.open(abs_path)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((420, 420))

            qimg = _image_pil_to_qimage(img)
            pixmap = QPixmap.fromImage(qimg)

            lbl = QLabel()
            lbl.setPixmap(pixmap)
            lbl.setAlignment(Qt.AlignCenter)
            v.addWidget(lbl)

            title = QLabel(f"{label}: {os.path.basename(abs_path)}")
            title.setAlignment(Qt.AlignHCenter)
            v.addWidget(title)

            self._host_set_head('show_browser')
            self._host_set_body_normal(cont)
        except Exception as e:
            print(f"[Error] Failed to show processing image: {abs_path} - {e}")
    
    def _alg_comparing_show_pair_images(self, p1, p2):
        try:
            cont = QWidget()
            hbox = QHBoxLayout(cont)

            for path in [p1, p2]:
                vbox = QVBoxLayout()
                abs_path = self._path_get_abs_path(path)
                img = Image.open(abs_path)
                img.thumbnail((300, 300))
                qimg = _image_pil_to_qimage(img)
                pixmap = QPixmap.fromImage(qimg)
                scaled_pixmap = pixmap.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)

                img_label = QLabel()
                img_label.setPixmap(scaled_pixmap)
                img_label.setAlignment(Qt.AlignHCenter)
                vbox.addWidget(img_label)

                label = QLabel(os.path.basename(abs_path))
                label.setAlignment(Qt.AlignHCenter)
                vbox.addWidget(label)

                hbox.addLayout(vbox)

            self._host_set_head('show_browser')
            self._host_set_body_normal(cont)
        except Exception as e:
            print(f"[Error] Failed to show comparing images: {e}")
    
    def _overview_show_api(self):
        self._overview_show_g1b1()

    def _overview_show_g1b1(self):
        # 1. Prepare data
        self.action = "show_overview"
        if self.view_groups_update:
            if self.show_original_groups:
                self.view_groups = self.groups
            else:
                self.view_groups, self.view_summary = self.constraints.apply_to_all_groups(self.groups)
            self.view_groups_update = False
            self.duplicate_size = self._wokr_folder_count_duplicate_size(self.view_groups)

        # 2. UI head/body
        self._overview_remove_events()
        self._host_set_head('show_overview')
        self._chkbox_controller()
        self._btn_controller()
        #self.show_back_btn.setVisible(False)

        # 3. Count page
        per_page = self.overview_cols * self.overview_rows  # Images per page
        total_groups = len(self.view_groups)
        max_page = (total_groups + per_page - 1) // per_page
        self.overview_page = max(0, min(self.overview_page, max_page - 1))
        start = self.overview_page * per_page
        end = min(start + per_page, total_groups)

        cont = QWidget()
        v = QVBoxLayout(cont)
        v.setSpacing(8)
        v.setContentsMargins(1, 1, 1, 1)

        self.group_info.setText(
            self.i18n.t("label.groups_overview",
                        total=max_page,
                        page=(self.overview_page + 1 if max_page else 0))
        )

        # 4. Build QListWidget (IconMode)
        listw = QListWidget()
        listw.setViewMode(QListWidget.IconMode)
        listw.setResizeMode(QListWidget.Adjust)
        listw.setMovement(QListView.Static)
        listw.setSpacing(10)
        listw.setSelectionMode(QAbstractItemView.NoSelection)
        self._ovw_listw = listw

        edge = int(max(120, min(320, getattr(self, "current_overview_thumb_size", 240))))
        listw.setIconSize(QSize(edge, edge))
        listw.setMouseTracking(True)
        listw.viewport().setMouseTracking(True)
        listw.viewport().installEventFilter(self)
        
        # 5. Fill groups to current page
        self._ovw_items  = []
        self._ovw_qimages = []
        self._ovw_listw = listw

        pending = []  # [(row_idx, gi, abs_path)]
        indices = list(range(start, end))

        for gi in indices:
            members = self.view_groups[gi]
            if not members:
                continue
            rep_rel = members[0]
            abs_path = self._path_get_abs_path(rep_rel)

            edge = int(max(120, min(320, getattr(self, "current_overview_thumb_size", 240))))
            cache_key = abs_path
            qimg = self.group_preview_cache.get(cache_key)

            # For gray backaround and text loading
            pm = QPixmap(edge, edge)
            pm.fill(QColor("#2e2e2e"))

            # None for default, update later
            self._ovw_qimages.append(qimg if isinstance(qimg, QImage) and not qimg.isNull() else None)

            # Display number of images with i18n
            count_text = self.i18n.t("label.group_tile", count=len(members)) if isinstance(qimg, QImage) else self.i18n.t("label.loading", default="Loading…")

            item = QListWidgetItem(QIcon(pm), count_text)
            item.setData(Qt.UserRole, gi)
            listw.addItem(item)
            # v.addWidget(listw, 1) 之後
            self._overview_install_events(listw)
            self._ovw_items.append(item)

            if not (isinstance(qimg, QImage) and not qimg.isNull()):
                pending.append((len(self._ovw_items)-1, gi, abs_path))

        v.addWidget(listw, 1)

        # 6. Apply body
        self._host_set_body_normal(cont)
        self._status_refresh_text()
        self._host_set_slider_mode("show_overview")

        # Load one by one
        self._ovw_build_gen = getattr(self, "_ovw_build_gen", 0) + 1
        gen = self._ovw_build_gen

        def _fill_icons(idx=0):
            # Return if rebuild page
            if gen != getattr(self, "_ovw_build_gen", 0):
                return

            # Get current list widget
            lw_alive = getattr(self, "_ovw_listw", None)
            if lw_alive is None or sip.isdeleted(lw_alive):
                return

            if idx >= len(pending):
                edge2 = int(getattr(self, "current_overview_thumb_size", 240))
                self._overview_resize_icons(edge2, Qt.SmoothTransformation)
                return

            row, gi, abs_path = pending[idx]

            # Check life cycle and limit
            if row < 0 or row >= lw_alive.count():
                QTimer.singleShot(10, lambda: _fill_icons(idx + 1))
                return

            it = lw_alive.item(row)
            if it is None:
                QTimer.singleShot(10, lambda: _fill_icons(idx + 1))
                return

            try:
                im = _image_load_for_thumb(abs_path, want_min_edge=max(edge * 2, 240))
                im = ImageOps.exif_transpose(im)
                if im.mode != "RGBA":
                    im = im.convert("RGBA")
                qimg = QImage(im.tobytes("raw", "RGBA"), im.size[0], im.size[1], QImage.Format_RGBA8888)

                if not qimg.isNull():
                    # Update cache
                    self.group_preview_cache[abs_path] = qimg
                    if 0 <= row < len(self._ovw_qimages):
                        self._ovw_qimages[row] = qimg

                    # Write icon
                    pm2 = QPixmap.fromImage(qimg).scaled(edge, edge, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    it.setIcon(QIcon(pm2))
                    members = self.view_groups[gi]
                    it.setText(self.i18n.t("label.group_tile", count=len(members)))
                else:
                    it.setText(self.i18n.t("err.fail_to_load_images_short", default="Load failed"))
            except Exception:
                it.setText(self.i18n.t("err.fail_to_load_images_short", default="Load failed"))

            # Refresh view
            try:
                lw_alive.viewport().update()
            except Exception:
                pass

            QTimer.singleShot(10, lambda: _fill_icons(idx + 1))

        QTimer.singleShot(0, lambda: _fill_icons(0))

        _fill_icons(0)

    # Resize overview icons
    def _overview_resize_icons(self, size: int, quality):
        # Build QListWidget item icons of current overview page based on _ovw_qimages
        listw = getattr(self, "_ovw_listw", None)
        if not listw:
            return

        items = getattr(self, "_ovw_items", [])
        qimgs = getattr(self, "_ovw_qimages", [])
        n = min(len(items), len(qimgs))

        # Update size of QListWidget icon, prevent cut image
        listw.setIconSize(QSize(size, size))

        for i in range(n):
            it = items[i]
            qimg = qimgs[i]
            if isinstance(qimg, QImage) and not qimg.isNull():
                pm = QPixmap.fromImage(qimg).scaled(size, size, Qt.KeepAspectRatio, quality)
                it.setIcon(QIcon(pm))
            else:
                # Load fail
                placeholder = QPixmap(size, size)
                placeholder.fill(Qt.transparent)
                it.setIcon(QIcon(placeholder))

        # Force refresh clear background/text
        listw.viewport().update()

    def _btn_action_overview_first_page(self):
        self.overview_page = 0
        self._overview_show_api()

    def _btn_action_overview_prev_page(self):
        if self.overview_page > 0:
            self.overview_page -= 1
            self._overview_show_api()

    def _btn_action_overview_next_page(self):
        cols = self.overview_cols
        rows = self.overview_rows
        per_page = cols * rows
        max_page = (max(len(self.view_groups) - 1, 0)) // per_page
        if self.overview_page < max_page:
            self.overview_page += 1
            self._overview_show_api()

    def _btn_action_overview_last_page(self):
        cols = self.overview_cols
        rows = self.overview_rows
        per_page = cols * rows
        max_page = (max(len(self.view_groups) - 1, 0)) // per_page
        self.overview_page = max_page
        self._overview_show_api()

    def _group_show_api(self, idx: int | None = None):
        if self.view_groups_update:
            if self.show_original_groups or self.stage != "done":
                self.view_groups = self.groups
            else:
                self.view_groups, self.view_summary = self.constraints.apply_to_all_groups(self.groups)
            self.view_groups_update = False
            self.duplicate_size = self._wokr_folder_count_duplicate_size(self.view_groups)

        self._group_show_detail(idx)
    
    # Set host mode
    def _host_set_head(self, mode: str):
        self._host_build()
        if mode == 'show_browser':
            self.head_function_bar.show()
            self.head_browser_bar.show()
            self.head_navi_bar.hide()
            self.head_slider_bar.hide()
            self.head_mark_bar.hide()
        elif mode == 'show_overview':
            self.head_function_bar.show()
            self.head_browser_bar.hide()
            self.head_navi_bar.show()
            self.head_slider_bar.show()
            self.head_mark_bar.hide()
        elif mode == 'show_group':
            self.head_function_bar.show()
            self.head_browser_bar.hide()
            self.head_navi_bar.show()
            self.head_slider_bar.show()
            self.head_mark_bar.show()
            if self._db_lock_check_and_create(self.work_folder, False)==False:
                self.head_mark_bar.setDisabled(True)
            else:
                self.head_mark_bar.setDisabled(False)
        else:
            print("[Error] mode is not defined")

    # Build host
    def _host_build(self):
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

        # ---------- normal_host（head_holder + body_holder） ----------
        self.normal_host = QWidget()
        normal_layout = QVBoxLayout(self.normal_host)
        normal_layout.setContentsMargins(0, 0, 0, 0)
        normal_layout.setSpacing(3)

        # head_holder
        self.normal_head_holder = QWidget()
        head_layout = QVBoxLayout(self.normal_head_holder)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(0)
        
        # Fill head elements: function/browser/navi/slider/mark
        self.head_function_bar = QWidget()
        function_layout = QHBoxLayout(self.head_function_bar)
        function_layout.setContentsMargins(0, 0, 0, 0)
        function_layout.setSpacing(3)

        self.head_browser_bar = QWidget()
        browser_layout = QHBoxLayout(self.head_browser_bar)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.setSpacing(3)

        self.head_navi_bar = QWidget()
        navi_layout = QHBoxLayout(self.head_navi_bar)
        navi_layout.setContentsMargins(0,0,0,0)
        navi_layout.setSpacing(3)
        
        self.head_slider_bar = QWidget()
        slider_layout = QHBoxLayout(self.head_slider_bar)
        slider_layout.setContentsMargins(0,0,0,0)
        slider_layout.setSpacing(3)

        self.head_mark_bar = QWidget()
        mark_layout = QHBoxLayout(self.head_mark_bar)
        mark_layout.setContentsMargins(0,0,0,0)
        mark_layout.setSpacing(3)

        # Fill Head.Function elements: scan/pause/continue/exit
        for w in (self.scan_btn, self.pause_btn, self.continue_btn, self.exit_btn):
            function_layout.addWidget(w)
        
        # Fill Head.Browser elements: back/path/exclude folder/view style/sort
        self.browser_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.browser_folder_management = QLabel("")
        browser_layout.addWidget(self.show_browser_back_btn)
        browser_layout.addWidget(self.browser_path_prefix)
        browser_layout.addWidget(self.browser_path_label, 1)
        browser_layout.addWidget(self.browser_folder_management)
        browser_layout.addWidget(self.exclude_lbl)
        browser_layout.addWidget(self.exclude_input)
        browser_layout.addStretch()
        browser_layout.addWidget(self.browser_view_style_lbl)
        browser_layout.addWidget(self.browser_view_style_combo)
        browser_layout.addWidget(self.browser_sort_lbl)
        browser_layout.addWidget(self.browser_sort_combo)
        browser_layout.addWidget(self.browser_order_btn)
       

        # Fill Head.Navi elements: first/pre folder/pre group/next group/next folder/last
        for w in (self.first_btn, self.prev_folder_btn, self.prev_btn,
                self.next_btn, self.next_folder_btn, self.last_btn):
            navi_layout.addWidget(w)

        # Fill Head.Slider elements: back/thumbnail slider
        slider_layout.addWidget(self.show_group_back_btn)

        self.group_info = QLabel("")
        slider_layout.addWidget(self.group_info)

        self.thumb_size_lbl = getattr(self, "thumb_size_lbl", QLabel("Thumb"))
        slider_layout.addWidget(self.thumb_size_lbl)

        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(120, 320)
        self.size_slider.setSingleStep(4)
        self.size_slider.setPageStep(32)
        self.size_slider.setMinimumWidth(300)
        slider_layout.addWidget(self.size_slider)

        self.size_val_lbl = QLabel("")
        slider_layout.addWidget(self.size_val_lbl)

        # Fill Head.Mark element: delete/same/different/ignore/clear
        for b in (self.delete_btn, self.merge_btn, self.separate_btn, self.ignore_btn, self.unmarked_btn):
            mark_layout.addWidget(b)
        mark_layout.addStretch(1)
        
        head_layout.addWidget(self.head_function_bar)
        head_layout.addWidget(self.head_browser_bar)
        head_layout.addWidget(self.head_navi_bar)
        head_layout.addWidget(self.head_slider_bar)
        head_layout.addWidget(self.head_mark_bar)

        def _on_browser_sort_changed():
            self._browser_sort_key  = self.browser_sort_combo.currentData()
            self._browser_sort_asc  = (self.browser_order_btn.text() == "a->z")
            self.cfg.set("ui.browser_sort_key",self._browser_sort_key)
            self.cfg.set("ui.browser_order_asc", self._browser_sort_asc)

            # Update list, not rebuild head
            lw = getattr(self, "_browser_listw_ref", None)
            if lw and getattr(self, "browser_folder", None):
                self._browser_build_list(lw, self.browser_folder)

        def _on_browser_view_style_changed():
            self._browser_thumb_cache.clear()
            self._browser_view_style_key = self.browser_view_style_combo.currentData()
            self.cfg.set("ui.browser_view_style_key",self._browser_view_style_key)
            lw = getattr(self, "_browser_listw_ref", None)
            cur = getattr(self, "browser_folder", None)

            if lw and cur:
                self._browser_build_list(lw, cur)

        self.browser_sort_combo.currentIndexChanged.connect(_on_browser_sort_changed)
        self.browser_view_style_combo.currentIndexChanged.connect(_on_browser_view_style_changed)
        self.browser_order_btn.clicked.connect(lambda: (self.browser_order_btn.setText("z->a" if self.browser_order_btn.text()=="a->z" else "a->z"),
                                                        _on_browser_sort_changed()))

        # Fill head holder
        normal_layout.addWidget(self.normal_head_holder)

        # body
        normal_body_holder = QWidget()
        self.normal_body_layout = QVBoxLayout(normal_body_holder)
        self.normal_body_layout.setContentsMargins(0, 0, 0, 0)
        self.normal_body_layout.setSpacing(0)
        normal_layout.addWidget(normal_body_holder, 1)

        # Add normal to root
        root.addWidget(self.normal_host, 1)

        # Show normal currently
        self.normal_host.show()

        self._dual_host_ready = True
    
    # Handle group detail thumbnail resize
    def _group_apply_detail_resize_once(self, val: int, quality):
        # Re scaled pixmap based on val
        self._group_resize_thumbs(val, quality)

        # Re scaled QLabel and item size hint to prevent cut image
        lw = getattr(self, "_listw_ref", None)
        if lw is None:
            return

        # Adjust cell size one by one
        for i, lbl in enumerate(getattr(self, "_thumb_labels", [])):
            if lbl is None:
                continue

            if i < lw.count():
                it = lw.item(i)
                cell = lw.itemWidget(it)
                if cell is not None:
                    cell.adjustSize()
                    it.setSizeHint(cell.sizeHint())

        lw.doItemsLayout()
    
    # Set slider mode to overview or groups
    def _host_set_slider_mode(self, mode: str):
        if mode == "show_overview":
            # Remove old connect
            try:
                self.size_slider.valueChanged.disconnect()
            except TypeError:
                pass

            # slider configuration
            self.size_slider.blockSignals(True)
            self.size_slider.setRange(120, 320)
            self.current_overview_thumb_size = int(getattr(self, "current_overview_thumb_size", 240))
            self.size_slider.setValue(self.current_overview_thumb_size)
            if hasattr(self, "size_val_lbl"):
                self.size_val_lbl.setText(str(self.current_overview_thumb_size))
            self.size_slider.blockSignals(False)

            # Get QListWidget
            listw = getattr(self, "_ovw_listw", None)

            if not hasattr(self, "_overview_resize_timer"):
                self._overview_resize_timer = QTimer(self)
                self._overview_resize_timer.setSingleShot(True)
                self._overview_resize_timer.setInterval(120)

            def _rebuild_icons(size: int, quality):
                if not listw:
                    return
                items = getattr(self, "_ovw_items", [])
                qimgs = getattr(self, "_ovw_qimages", [])
                n = min(len(items), len(qimgs))
                for i in range(n):
                    it = items[i]
                    qimg = qimgs[i]
                    if isinstance(qimg, QImage) and not qimg.isNull():
                        pm = QPixmap.fromImage(qimg).scaled(size, size, Qt.KeepAspectRatio, quality)
                    else:
                        pm = QPixmap(size, size)
                        pm.fill(Qt.transparent)
                    it.setIcon(QIcon(pm))
                listw.setIconSize(QSize(size, size))

            def _on_overview_changed(x):
                x = max(120, min(320, int(x)))
                if x != self.current_overview_thumb_size:
                    self.current_overview_thumb_size = x
                    self.cfg.set("ui.overview_thumbnail.max_size",self.current_overview_thumb_size)
                if hasattr(self, "size_val_lbl"):
                    self.size_val_lbl.setText(str(x))

                if listw:
                    _rebuild_icons(x, Qt.FastTransformation)

                self._overview_resize_timer.stop()
                try:
                    self._overview_resize_timer.timeout.disconnect()
                except TypeError:
                    pass

                def _do_smooth():
                    if getattr(self, "action", "") == "show_overview":
                        _rebuild_icons(self.current_overview_thumb_size, Qt.SmoothTransformation)

                self._overview_resize_timer.timeout.connect(_do_smooth)
                self._overview_resize_timer.start()

            self.size_slider.valueChanged.connect(_on_overview_changed)
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

            def _on_detail_changed(x):
                x = max(400, min(1000, int(x)))
                if x != self.current_group_thumb_size:
                    self.current_group_thumb_size = x
                    self.cfg.set("ui.thumbnail.max_size",self.current_group_thumb_size)
                if hasattr(self, "size_val_lbl"):
                    self.size_val_lbl.setText(str(x))

                self._group_apply_detail_resize_once(x, Qt.FastTransformation)

                self._thumb_resize_timer.stop()
                try:
                    self._thumb_resize_timer.timeout.disconnect()
                except TypeError:
                    pass
                self._thumb_resize_timer.timeout.connect(
                    lambda: self._group_apply_detail_resize_once(self.current_group_thumb_size, Qt.SmoothTransformation)
                )
                self._thumb_resize_timer.start()

            self.size_slider.valueChanged.connect(_on_detail_changed)
    
    # Set host body to normal
    def _host_set_body_normal(self, w: QWidget):
        # Set normal body (For show groups detail and show overview)
        lay = self.normal_body_layout
        while lay.count():
            it = lay.takeAt(0)
            ww = it.widget()
            if ww:
                ww.setParent(None)
        lay.addWidget(w)

    # Group thumbnail resize
    def _group_resize_thumbs(self, size: int, quality=Qt.SmoothTransformation):
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

    # Update group information
    def _group_info_update(self, grp):
        # Clear cache
        self.group_checkboxes = []
        self._thumb_labels = []
        self._thumb_qimages = []
        self._thumb_styles = []

        # Get element relation
        relation = self._constraints_query_groups_relation(grp)

        cont = QWidget()
        v = QHBoxLayout(cont)
        v.setSpacing(8)
        v.setContentsMargins(0, 0, 0, 0)

        # Build drag able list 
        listw = DraggableListWidget()
        listw.setViewMode(QListWidget.IconMode)
        listw.setResizeMode(QListWidget.Adjust)
        listw.setDragDropMode(QAbstractItemView.InternalMove)
        listw.setDragDropOverwriteMode(False)
        listw.setDefaultDropAction(Qt.MoveAction)
        listw.setSpacing(10)
        listw.setSelectionMode(QAbstractItemView.ExtendedSelection)
        listw.setDragEnabled(True)
        listw.setAcceptDrops(True)
        listw.setDropIndicatorShown(True)
        listw.setMovement(QListView.Snap)
        listw.setIconSize(QSize(self.current_group_thumb_size, self.current_group_thumb_size))

        self._listw_ref = listw

        group_abs_paths = [self._path_get_abs_path(p) for p in grp]
        common_prefix = os.path.commonpath(group_abs_paths).replace("\\", "/").lower()
        if len(common_prefix) > 0 and not common_prefix.endswith("/"):
            common_prefix += "/"
        
        for idx, p in enumerate(grp, start=1):
            abs_path = self._path_get_abs_path(p).replace("\\", "/").lower()

            # Thumbnail
            try:
                base_size = max(self.current_group_thumb_size, 1400)
                img = _image_load_for_thumb(abs_path, want_min_edge=base_size)
                if relation == "ignored":
                    try:
                        img = ImageOps.grayscale(img)
                    except Exception:
                        img = img.convert("L")

                qimg = _image_pil_to_qimage(img)
                edge = int(self.current_group_thumb_size)
                pm = QPixmap.fromImage(qimg).scaled(edge, edge, Qt.KeepAspectRatio, Qt.SmoothTransformation)

                style = "normal"
                if relation == "different":
                    style = "dark"
                    painter = QPainter(pm)
                    painter.fillRect(pm.rect(), QColor(0, 0, 0, 110))
                    painter.end()

                cell = QWidget()
                cell_v = QVBoxLayout(cell)
                cell_v.setContentsMargins(1, 1, 1, 1)
                cell_v.setSpacing(6)

                thumb_lbl = QLabel()
                thumb_lbl.setAlignment(Qt.AlignCenter)
                thumb_lbl.setPixmap(pm)
                thumb_lbl.setCursor(Qt.PointingHandCursor)
                thumb_lbl.mouseDoubleClickEvent = lambda e, fp=abs_path: self._group_show_image(fp)
               
                cell_v.addWidget(thumb_lbl)

                # Mark button
                if relation == "same":
                    cb_text = self.i18n.t("msg.must")
                elif relation == "different":
                    cb_text = self.i18n.t("msg.separate")
                elif relation == "ignored":
                    cb_text = self.i18n.t("msg.ignore")
                elif relation == "mix":
                    cb_text = self.i18n.t("msg.mix")
                else:
                    cb_text = self.i18n.t("msg.keepfile")

                cb = QCheckBox(cb_text)
                cb.setChecked(True)
                cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                cb.path = p
                self.group_checkboxes.append(cb)
                cell_v.addWidget(cb)

                # File information
                rel_path = os.path.dirname(os.path.relpath(abs_path, common_prefix).replace("\\", "/").lower())
                if len(rel_path) > 0 and not rel_path.endswith("/"):
                    rel_path += "/"
                file_name = os.path.basename(abs_path)
                size_str = ""
                if os.path.exists(abs_path):
                    file_size_b = os.path.getsize(abs_path)
                    size_str = f"{(file_size_b / 1000):,.2f} KB" if file_size_b < 1000 * 1000 else f"{(file_size_b / (1000 * 1000)):,.2f} MB"

                info_label = QLabel()
                info_label.setTextFormat(Qt.RichText)
                info_label.setWordWrap(True)
                
                info_label.setText(
                    f"{idx}. {self.i18n.t('msg.filename')}: {file_name}<br>"
                    f"{_path_build_highlight_html(common_prefix, rel_path)}<br>"
                    f"{self.i18n.t('msg.filesize')}: {size_str}<br>"
                )
                cell_v.addWidget(info_label)

                # Finder/Explorer
                btn = QPushButton(self.i18n.t("btn.show_in_finder"))
                btn.clicked.connect(lambda _, fp=abs_path: self._system_open_in_explorer(fp))
                cell_v.addWidget(btn)

                cell_v.addStretch(1)

                # Update slider
                self._thumb_labels.append(thumb_lbl)
                self._thumb_qimages.append(qimg)
                self._thumb_styles.append(style)

                # Add to QListWidget
                item = QListWidgetItem()
                item.setSizeHint(cell.sizeHint())
                item.setData(Qt.UserRole, p)

                # Add flag
                item.setFlags(item.flags()
                            | Qt.ItemIsEnabled
                            | Qt.ItemIsSelectable
                            | Qt.ItemIsDragEnabled
                            | Qt.ItemIsDropEnabled)

                listw.addItem(item)
                listw.setItemWidget(item, cell)

            except Exception as e:
                err = QLabel(self.i18n.t("err.fail_to_load_images", path=abs_path, str=str(e)))
                err.setWordWrap(True)
                err.setFixedWidth(480)
                err_w = QWidget()
                err_l = QVBoxLayout(err_w)
                err_l.setContentsMargins(1, 1, 1, 1)
                err_l.addWidget(err)
                item = QListWidgetItem()
                item.setSizeHint(err_w.sizeHint())
                item.setData(Qt.UserRole, p)
                listw.addItem(item)
                listw.setItemWidget(item, err_w)
                self._thumb_labels.append(None)
                self._thumb_qimages.append(None)
                self._thumb_styles.append("normal")
        
        # Use signal to new method
        listw.reordered.connect(self._group_drag_apply_new_order_from_list)
        v.addWidget(listw, 1)

        if relation != "none":
            self.unmarked_btn.setEnabled(True)
        else:
            self.unmarked_btn.setEnabled(False)

        self.scroll.setWidget(cont)

    def _group_show_detail(self, idx: int | None = None):
        self._overview_remove_events()
        self.action = "show_group"
        self._host_set_head('show_group')
        if hasattr(self, "_overview_resize_timer") and self._overview_resize_timer.isActive():
            self._overview_resize_timer.stop()
            try:
                self._overview_resize_timer.timeout.disconnect()
            except TypeError:
                pass

        show_groups = getattr(self, "view_groups", self.groups) or []
        if not show_groups:
            self.group_info.setText(self.i18n.t("label.group_empty"))
            self._host_set_body_normal(QWidget())
            self._host_set_slider_mode("detail")
            return

        if idx!=None:
            self.current = idx

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
        self._btn_controller()
        
        vp = self.scroll.viewport() if hasattr(self, "scroll") else None
        if vp: vp.setUpdatesEnabled(False)
        try:
            self._group_info_update(grp)
        finally:
            if vp: vp.setUpdatesEnabled(True)

        # Add show group detail to host body
        self._host_set_body_normal(self.scroll)

        self._host_set_slider_mode("detail")

    def _group_show_image(self, image_path):
        dialog = ImageDialog(image_path)
        dialog.setModal(False)
        dialog.show()
        self.dialogs.append(dialog)
    
    def _btn_controller(self):
        cols = self.overview_cols
        rows = self.overview_rows
        per_page = cols * rows
        max_page = (max(len(self.view_groups) - 1, 0)) // per_page

        self.exclude_input.setEnabled(self.action in {
            "init", 
            "select_folder",
            "show_browser",
            "file_operation"})

        self.exclude_input.setReadOnly(self.action in {
            "collecting",
            ""})

        self.browser_view_style_combo.setEnabled(self.action in {
            "show_browser", 
            "select_folder",
            "pause"} and self.stage not in {"collecting"})

        self.browser_sort_combo.setEnabled(self.action in {
            "show_browser", 
            "select_folder",
            "pause"} and self.stage not in {"collecting"})
        
        self.browser_order_btn.setEnabled(self.action in {
            "show_browser", 
            "select_folder",
            "pause"} and self.stage not in {"collecting"})

        self.show_browser_back_btn.setEnabled((self.action in {
            "init",
            "select_folder",
            "pause",
            "show_overview",
            "show_browser"}) or (self.action in {"show_group"} and self.stage in {"done"}))
        self.show_group_back_btn.setEnabled((self.action in {
            "pause",
            "show_overview"}) or (self.action in {"show_group"} and self.stage in {"done"}))
        self._shortcuts["sc_show_back"].setEnabled((self.action in {
            "init",
            "select_folder",
            "pause",
            "show_overview",
            "show_browser"}) or (self.action in {"show_group"} and self.stage in {"done"}))

        self.scan_btn.setEnabled((self.action in {
            "init",
            "select_folder",
            "show_browser"}) or (self.action in {
            "collecting", 
            "scan", 
            "hashing", 
            "comparing", 
            "pause", 
            "continue", 
            "resuming",
            "show_group",
            "show_overview"} and self.stage in {"done"}) or (self.action in {"pause"} and self.stage in {"collecting"}))
        self._shortcuts["sc_scan"].setEnabled((self.action in {
            "init",
            "select_folder",
            "show_browser"}) or (self.action in {
            "collecting", 
            "scan", 
            "hashing", 
            "comparing", 
            "pause", 
            "continue", 
            "resuming",
            "show_group",
            "show_overview"} and self.stage in {"done"}) or (self.action in {"pause"} and self.stage in {"collecting"}))

        self.pause_btn.setEnabled(self.action in {
            "collecting",            
            "scan",
            "hashing",
            "comparing",
            "continue"})
        self._shortcuts["sc_pause"].setEnabled(self.action in {
            "collecting",
            "scan",
            "hashing",
            "comparing",
            "continue"})

        self.continue_btn.setEnabled(self.paused and self.stage != "collecting")
        self._shortcuts["sc_continue"].setEnabled(self.paused and self.stage != "collecting")

        self.exit_btn.setEnabled(self.action in {
            "init",
            "select_folder",
            "collecting",
            "pause",
            "show_group",
            "show_overview",
            "show_browser"})
        self._shortcuts["sc_exit"].setEnabled(self.action in {
            "init",
            "select_folder",
            "collecting",
            "pause",
            "show_group",
            "show_overview",
            "show_browser"})

        self.first_btn.setEnabled((self.action in {"show_group"} and (len(self.view_groups)>1 and self.current>0)) or
        (self.action in {"show_overview"} and (len(self.view_groups)>1 and self.overview_page>0)))
        self._shortcuts["sc_first_f"].setEnabled((self.action in {"show_group"} and (len(self.view_groups)>1 and self.current>0)) or
        (self.action in {"show_overview"} and (len(self.view_groups)>1 and self.overview_page>0)))
        self._shortcuts["sc_first_home"].setEnabled((self.action in {"show_group"} and (len(self.view_groups)>1 and self.current>0)) or
        (self.action in {"show_overview"} and (len(self.view_groups)>1 and self.overview_page>0)))

        self.prev_btn.setEnabled((self.action in {"show_group"} and (len(self.view_groups)>1 and self.current>0)) or
        (self.action in {"show_overview"} and (len(self.view_groups)>1 and self.overview_page>0)))
        self._shortcuts["sc_pre_group"].setEnabled((self.action in {"show_group"} and (len(self.view_groups)>1 and self.current>0)) or
        (self.action in {"show_overview"} and (len(self.view_groups)>1 and self.overview_page>0)))

        self.next_btn.setEnabled((self.action in {"show_group"} and (self.stage!="done" or (len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done"))) or
        (self.action in {"show_overview"} and (self.stage!="done" or (len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done"))))
        self._shortcuts["sc_next_group"].setEnabled((self.action in {"show_group"} and (self.stage!="done" or (len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done"))) or
        (self.action in {"show_overview"} and (self.stage!="done" or (len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done"))))

        self.prev_folder_btn.setEnabled((self.action in {
            "show_group"} and (len(self.view_groups)>1 and self.current>0 and self.stage=="done")))
        self._shortcuts["sc_pre_folder"].setEnabled((self.action in {
            "show_group"} and (len(self.view_groups)>1 and self.current>0 and self.stage=="done")))

        self.next_folder_btn.setEnabled((self.action in {
            "show_group"} and (len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")))
        self._shortcuts["sc_next_folder"].setEnabled((self.action in {
            "show_group"} and (len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")))

        self.last_btn.setEnabled((self.action in {"show_group"} and (len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")) or
        (self.action in {"show_overview"} and (len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done")))
        self._shortcuts["sc_last_l"].setEnabled((self.action in {"show_group"} and (len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")) or
        (self.action in {"show_overview"} and (len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done")))
        self._shortcuts["sc_last_end"].setEnabled((self.action in {"show_group"} and (len(self.view_groups)>1 and self.current<len(self.view_groups)-1 and self.stage=="done")) or
        (self.action in {"show_overview"} and (len(self.view_groups)>1 and self.overview_page<max_page and self.stage=="done")))

        self.delete_btn.setEnabled((self.action in {
            "show_group", 
            "show_overview"} and (len(self.view_groups)>0)))
        self._shortcuts["sc_delete_del"].setEnabled((self.action in {
            "show_group", 
            "show_overview"} and (len(self.view_groups)>0)))
        self._shortcuts["sc_delete_backspace"].setEnabled((self.action in {
            "show_group", 
            "show_overview"} and (len(self.view_groups)>0)))
        
        self.merge_btn.setEnabled(self.action in {
            "show_group"})
        self._shortcuts["sc_mark_same"].setEnabled(self.action in {
            "show_group"})

        self.separate_btn.setEnabled(self.action in {
            "show_group"})
        self._shortcuts["sc_mark_diff"].setEnabled(self.action in {
            "show_group"})

        self.ignore_btn.setEnabled(self.action in {
            "show_group"})
        self._shortcuts["sc_mark_ignore"].setEnabled(self.action in {
            "show_group"})

        self.unmarked_btn.setEnabled(self.action in {
            "show_group"})
        self._shortcuts["sc_mark_clear"].setEnabled(self.action in {
            "show_group"})

        for idx in range(0, 10):
            self._shortcuts[f"sc_num{idx}"].setEnabled(self.action in {
            "show_group"})

        self._shortcuts["sc_rename"].setEnabled(self.action in {
            "show_browser"})

        self._shortcuts["sc_refresh"].setEnabled(self.action in {
            "show_browser"})

        if self.action=="pause":
            if self.stage=="collecting":
                self.status.setText(self.i18n.t("status.stopped"))
            else:
                self.status.setText(self.i18n.t("status.paused"))
            return

        if self.action=="continue":
            return

        if self.action=="collecting":
            self.pause_btn.setText(self.i18n.t("btn.stop"))
            return

        if self.action=="hashing" or self.action=="comparing":
            self.pause_btn.setText(self.i18n.t("btn.pause"))
            return

        if self.action=="resuming":
            if self.stage=="hashing":
                self.status.setText(self.i18n.t("status.resuming_hashing"))
            if self.stage=="comparing":
                self.status.setText(self.i18n.t("status.resuming_comparison"))
            if self.stage=="done":
                self.status.setText(self.i18n.t("status.restored", groups=len(self.view_groups), total = len(self.phashes)))
            return

        if self.action=="show_group":
            self.pause_btn.setText(self.i18n.t("btn.pause"))
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
            
            self.first_btn.setText(self.i18n.t("btn.first"))
            self.prev_btn.setText(self.i18n.t("btn.prev"))
            self.prev_folder_btn.show()
            self.prev_folder_btn.setText(self.i18n.t("btn.prev_folder"))
            self.next_btn.setText(self.i18n.t("btn.next"))
            self.next_folder_btn.show()
            self.next_folder_btn.setText(self.i18n.t("btn.next_folder"))
            self.last_btn.setText(self.i18n.t("btn.last"))
            return
        if self.action=="show_overview":
            self.pause_btn.setText(self.i18n.t("btn.pause"))
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

            self.first_btn.setText(self.i18n.t("btn.first_page"))
            self.prev_btn.setText(self.i18n.t("btn.prev_page"))
            self.prev_folder_btn.hide()
            self.next_btn.setText(self.i18n.t("btn.next_page"))
            self.next_folder_btn.hide()
            self.last_btn.setText(self.i18n.t("btn.last_page"))
            return

        if self.action=="show_browser":
            self.pause_btn.setText(self.i18n.t("btn.pause"))
            return

    def _btn_handler_show_back(self):
        if self.action == "show_group":
            if self.related_files_mode:
                self._db_save_filelist(self.work_folder)
                self._db_save_progress(self.work_folder)
                self._db_save_exceptions(self.work_folder)
                self.constraints.save_constraints()
                self._db_unlock(self.work_folder)
                self._work_folder_clear_variable()
                self._browser_show(self.browser_folder)
            else:
                self._overview_show_api()
        elif self.action == "show_overview":
            self._db_save_filelist(self.work_folder)
            self._db_save_progress(self.work_folder)
            self._db_save_exceptions(self.work_folder)
            self.constraints.save_constraints()
            self._db_unlock(self.work_folder)
            self._work_folder_clear_variable()
            self._browser_show(self.browser_folder)
        elif self.action == "show_browser" or self.action == "select_folder":
            if self.stage == "collecting":
                self.paused = True
            self._btn_action_browser_to_parent(getattr(self, "browser_folder", os.path.expanduser("~")))
        else:
            print(f"[Error] Not Defined Action: {self.action}\n")

    def _btn_handler_navi(self, func):
        if self.action == "show_overview":
            match func:
                case "first":
                    self._btn_action_overview_first_page()
                case "pre_folder":
                    self._overview_show_api()
                case "pre_group":
                    self._btn_action_overview_prev_page()
                case "next_group":
                    self._btn_action_overview_next_page()
                case "next_folder":
                    self._overview_show_api()
                case "last":
                    self._btn_action_overview_last_page()
                case _:
                    print("[Error] Undefined function for show_overview")
        elif self.action == "show_group":
            match func:
                case "first":
                    self._btn_action_first_group()
                case "pre_folder":
                    self._btn_action_prev_compare_folder()
                case "pre_group":
                    self._btn_action_prev_group()
                case "next_group":
                    self._btn_action_next_group_or_compare()
                case "next_folder":
                    self._btn_action_next_compare_folder()
                case "last":
                    self._btn_action_last_group()
                case _:
                    print("[Error] Undefined function for show_group")
        elif self.action == "show_browser":
            return
        else:
            print("[Error] Undefined action")
    
    def _chkbox_controller(self):
        if self.action=="show_group" or self.action=="show_overview":
            self.display_img_dynamic_cb.setText(self.i18n.t("cb.display_original_groups"))
            self.display_img_dynamic_cb.setChecked(self.show_original_groups)  
        else:
            self.display_img_dynamic_cb.setText(self.i18n.t("cb.display_img"))
            self.display_img_dynamic_cb.setChecked(self.show_processing_image)

    def _chkbox_handler(self, func):
        if func == "auto_next":
            self.cfg.set("behavior.auto_next_group",self.auto_next_cb.isChecked())
        elif func == "img_dynamic":
            if self.action=="show_group" or self.action=="show_overview":
                self.show_original_groups = not self.show_original_groups
                self.cfg.set("ui.show_original_groups",self.show_original_groups)
                self.view_groups_update = True
                if self.action == "show_group":
                    self._group_show_api()
                elif self.action == "show_overview":
                    self._overview_show_api()
            else:
                self.show_processing_image = not self.show_processing_image
                self.cfg.set("ui.show_processing_image",self.show_processing_image)
        else:
            print(f"(Error) Not defined func {func} in chkbox_handler")

    def _btn_action_continue_processing(self):
        self.paused = False
        self.work_folder = self.browser_folder
        self._db_load_filelist(self.work_folder)
        self._db_load_progress(self.work_folder)
        self._db_load_exceptions(self.work_folder)
        self.constraints = ConstraintsStore(self.work_folder)
        self._alg_handler()

    def _btn_action_delete_unchecked(self):
        keep_folder = self.work_folder
        # 1 Collect delete file list
        checkboxes = getattr(self, "group_checkboxes", None) or self.scroll.findChildren(QCheckBox)
        to_remove = [cb.path for cb in checkboxes if not cb.isChecked()]

        if not to_remove:
            return

        # 2 Confirm dialog
        if self.confirm_delete:
            title = self.i18n.t("dlg.delete_files.title")
            body = self.i18n.t("dlg.delete_files.body", cnt=len(to_remove))
            reply = self._popup_question(title, body, True)
            if reply == QMessageBox.No:
                return

        # 3 Delete（Record success delete）
        actually_deleted = []
        failed = []
        for rel in to_remove:
            abs_path = self._path_get_abs_path(rel)
            try:
                os.remove(abs_path)
                actually_deleted.append((abs_path,None,"delete"))
            except Exception as e:
                print(f"[Delete failed] {abs_path}: {e}")
                failed.append(rel)

        # 4 Failure feedback
        if failed:
            self._popup_information(self.i18n.t("toast.delete_failed_some", cnt=len(failed)))

        # 5 Sync to database
        self._browser_sync_batch(actually_deleted)
        self._status_refresh_text()

        # 6 Restore work folder
        self.work_folder = keep_folder
        self.view_groups_update = True
        self._db_load_filelist(self.work_folder)
        self._db_load_progress(self.work_folder)
        self._db_load_exceptions(self.work_folder)
        self.constraints = ConstraintsStore(scan_folder = self.work_folder)

        if self.stage == "comparing":
            self._alg_handler()
        else:
            if len(self.groups) > 0:
                if self.forward:
                    if self.current < len(self.groups):
                        self._group_show_api()
                    else:
                        self._overview_show_api()
                else:
                    if len(self.groups):
                        if self.current > 0:
                            self.current-=1
                        self._group_show_api()
                    else:
                        self._overview_show_api()    
            else:
                self._browser_show(self.work_folder)
    
    def _popup_information(self, text: str):
        QMessageBox.information(self, self.i18n.t("msg.info", default="Info"), text)

    def _btn_action_first_group(self):
        if self.current > 0:
            self.current = 0
        self.forward = True
        self._group_show_api()

    def _btn_action_prev_group(self):
        if self.current > 0:
            self.current -= 1
        self.forward = False
        self._group_show_api()
    
    def _btn_action_prev_compare_folder(self):
        if self.current > 0:
            curkey = os.path.dirname(self.view_groups[self.current][0])
            for i in range(1, self.current+1): 
                prekey = os.path.dirname(self.view_groups[self.current-i][0])             
                if prekey != curkey:
                    self.current = self.current-i
                    break
        self.forward = False
        self._group_show_api()

    def _btn_action_next_compare_folder(self):
        if self.current < len(self.view_groups)-1:
            curkey = os.path.dirname(self.view_groups[self.current][0])
            for i in range(1, len(self.view_groups)-self.current):
                nextkey = os.path.dirname(self.view_groups[self.current+i][0])
                if nextkey != curkey:
                    self.current = self.current+i
                    break
        self.forward = True
        self._group_show_api()
    
    def _system_open_in_explorer(self, path):
        try:
            if sys.platform.startswith('darwin'):  # macOS
                os.system(f'open -R "{path}"')
            elif os.name == 'nt':  # Windows
                os.startfile(os.path.dirname(path))
            elif os.name == 'posix':  # Linux
                os.system(f'xdg-open "{os.path.dirname(path)}"')
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Can't open Explorer: {e}")

    def _db_load_progress(self, path):
        if path == None:
            return False
        progress_file = os.path.join(path, f"{PROGRESS_FILE}")

        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding="utf-8") as f:
                    data = json.load(f)
                    self.hash_format = data.get("hash_format","v1")
                    self.stage = data.get("stage","init")
                    self.previous_file_counter = data.get("file_counter",0)
                    self.last_group_index = data.get("current",0)
                    self.overview_page = data.get("overview_page",0)
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

    def _db_save_progress(self, path, stage="done", extra=None):
        if path == None:
            return False
        progress_file = os.path.join(path, f"{PROGRESS_FILE}")

        sorted_hashes = {
            k: self.phashes[k]
            for k in sorted(self.phashes, key=lambda k: self.phashes[k].get("hash", 0))
        }

        if progress_file is None:
            return
       
        data = {
            "hash_format": "v2",
            "stage": self.stage,
            "file_counter": len(self.phashes),
            "current": self.current,
            "compare_index": self.compare_index,
            "overview_page": self.overview_page,
            "compare_file_size": self.compare_file_size,
            "similarity_tolerance": self.similarity_tolerance,
            "duplicate_size":self.duplicate_size,
            "visited": list(self.visited),
            "groups": self.groups,
            "phashes": sorted_hashes
        }
        try:
            with open(progress_file, 'w', encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Error] saving progress: {e}")

    def _db_load_exceptions(self, path):
        if path == None:
            return False
        exceptions_file = os.path.join(path, f"{EXCEPTIONS_FILE}")
        if os.path.exists(exceptions_file):
            try:
                with open(exceptions_file, 'r', encoding="utf-8") as f:
                    data = json.load(f)
                    self.exception_file_version = data.get("version","1")
                    self.exception_file_updated = data.get("updated","")
                    self.not_duplicate_pairs = data.get("not_duplicate_pairs",[])
                    self.exception_groups = data.get("exception_groups",[])
                    self.exception_folder = data.get("exclude_folder","")
                    return True
            except Exception as e:
                print(f"[Error] Read exception file: {e}")
                return False
        else:
            print(f"[Message] Exception file does not exist") 
            return False

    def _db_save_exceptions(self, path):
        if path == None:
            return False
        exceptions_file = os.path.join(path, f"{EXCEPTIONS_FILE}")
        data = {
            "version": self.exception_file_version,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "exclude_folder": self.exclude_input.text(),
            "not_duplicate_pairs": self.not_duplicate_pairs,
            "exception_groups": self.exception_groups,
        }

        if exceptions_file is None:
            return
        try:
            with open(exceptions_file, 'w', encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Error] saving exception: {e}")

    def _db_load_filelist(self, path):
        if path == None:
            return False
        filelist_file = os.path.join(path, f"{FILELIST_FILE}")
        if os.path.exists(filelist_file):
            try:
                with open(filelist_file, 'r', encoding="utf-8") as f:
                    filelist_data = json.load(f)
                    self.image_paths = filelist_data["image_paths"]
                    self.last_scan_time = filelist_data.get("last_scan_time","None")
                    return True        
            except Exception as e:
                print(f"[Error] Read filelist file: {e}")
                return False
        else:
            return False

    def _db_save_filelist(self, path):
        if path == None:
            return False
        filelist_file = os.path.join(path, f"{FILELIST_FILE}")
        try:
            with open(filelist_file, 'w', encoding="utf-8") as f:
                json.dump({
                    "last_scan_time": self.last_scan_time,
                    "image_paths": self.image_paths
                    }, f, indent=2)
        except Exception as e:
            print(f"[Error] Write Filelist file: {e}")
    
    def _btn_action_pause_processing(self):
        self.paused = True
        self.action = "pause"
        self._btn_controller()

    def _btn_action_exit_and_save(self):
        self.action = "exit_and_save"
        self.exit = True

        if self.work_folder:
            if self.stage != "collecting":
                self._db_save_filelist(self.work_folder)
                self._db_save_progress(self.work_folder, stage=self.stage)
                self._db_save_exceptions(self.work_folder)
            self._db_unlock(self.work_folder)

        QApplication.instance().quit()

    def _btn_action_next_group_or_compare(self):
        self.forward = True
        if self.current < len(self.view_groups) - 1:
            self.current += 1
            self._group_show_api()
        else:
            if self.stage != "done":
                self.compare_index += 1
                self._alg_handler()
    
    def _btn_action_last_group(self):
        if getattr(self, "stage", None) != "done":
            return
        self.forward = False
        if len(self.view_groups)>0:
            self.current = len(self.view_groups)-1
        self._group_show_api()

    def _constraints_query_images_relation(self, a: str, b: str) -> str:
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
    
    def _constraints_query_groups_relation(self, grp: list) -> list:
        rel = None
        n = len(grp)
        for i in range(n):
            for j in range(i + 1, n):
                if rel == None:
                    rel = self._constraints_query_images_relation(grp[i], grp[j])
                else:
                    if rel == self._constraints_query_images_relation(grp[i], grp[j]):
                        continue
                    else:
                        return "mix"
        return rel

    def _about_show_information(self):
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

    def _about_show_information_and_gpg(self):
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
    multiprocessing.freeze_support()  # Prevent macOS open many Apps
    main()
