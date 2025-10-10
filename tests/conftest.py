# tests/conftest.py
import json
from pathlib import Path
import random
from types import SimpleNamespace
from copy import deepcopy

import pytest
from PIL import Image
from PyQt5.QtWidgets import QApplication
from shutil import copyfile
from Match_Image_Finder import MatchImageFinder, FILELIST_FILE, PROGRESS_FILE

UI_TEST = SimpleNamespace(
    navigation_browser=True,
    navigation_overview=True,
    navigation_group=True,
)

FUNC_TEST = SimpleNamespace(
    scan_by_clicking=True,
    delete_by_clicking=True,
    mark_same_by_clicking=True,
    mark_different_by_clicking=True,
    mark_ignore_by_clicking=True,
    mark_clear_by_clicking=True,
    move_files_by_dragging=True,
    copy_files_by_dragging=True,
    rename_file_by_menu=True,
    delete_files_by_menu=True,
    copy_files_by_menu=True,
    move_files_by_menu=True,
)

SETT_TEST = SimpleNamespace(
    language=True,
    fontsize=True,
    stylelist=True,
    stylesmall=True,
    stylemedium=True,
    stylelarge=True,
    stylehuge=True,
    orderasc=True,
    orderdsc=True,
    showprocessingimage=True,
    overview_thumb_size=True,
    show_original_groups=True,
    group_thumb_size=True,
    similarity_tolerance=True,
    compare_file_size=True,
    auto_next_group=True,
    confirm_delete=True,
    display_same_images=True,
)
# -------------------------------
# Helpers
# -------------------------------
def _file_gen_images(tmp_path, helpers, number=11):
    root = tmp_path / "root"
    a = root / "a"
    b = a / "b"
    c = a / "c"
    d = b / "d"
    e = b / "e"
    f = c / "f"
    g = c / "g"
    h = f / "h"
    for folder in (a, b, c, d, e, f, h):
        folder.mkdir(parents=True, exist_ok=True)
        for img in range(1,number):
            prefix = folder.name.lower()
            _make_big_png(folder / f"{prefix}{img}.png")
            if img%2:
                src = folder / f"{prefix}{img}.png"
                dst = folder / f"{prefix}{img}-{img}.png"
                copyfile(src, dst)
    
    return root, a, b, c, d, e, f, g, h
def _make_big_png(path: Path, min_bytes: int = 60000):
    """
    產生大於 min_bytes 的 PNG。
    為避免 PNG 壓縮太好導致過小，使用隨機雜訊並逐步放大尺寸直到超過門檻。
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # 從 640x640 開始，必要時放大
    w, h = 256, 256
    for _ in range(6):  # 最多嘗試 6 次
        # 產生雜訊圖以避免高壓縮
        img = Image.new("RGB", (w, h))
        px = img.load()
        for y in range(h):
            for x in range(w):
                # 隨機像素（避免可壓縮塊）
                px[x, y] = (random.randint(0, 255),
                            random.randint(0, 255),
                            random.randint(0, 255))
        img.save(str(path), format="PNG")
        if path.stat().st_size >= min_bytes:
            break
        # 放大 1.6 倍再試
        w = int(w * 1.6)
        h = int(h * 1.6)

    assert path.stat().st_size > 50000, f"檔案太小: {path} ({path.stat().st_size} bytes)"


def _read_json(path: Path):
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _snapshot_fs(root: Path):
    """回傳 {相對路徑: 檔案大小} 的快照。"""
    result = {}
    for p in root.rglob("*"):
        if p.is_file():
            result[str(p.relative_to(root)).replace("\\", "/")] = p.stat().st_size
    return result


def _filelist_path(root: Path) -> Path:
    return Path(root) / FILELIST_FILE


def _progress_path(root: Path) -> Path:
    return Path(root) / PROGRESS_FILE


def _jump_folder(window: MatchImageFinder, path: Path):
    """同步切換 Browser 到指定資料夾（避免測試中 QTimer 非同步導致 race）。"""
    window._browser_show(str(path))


def _scan_now(window: MatchImageFinder):
    """立即觸發掃描（不做額外等待，交由 pytest-qt 控事件圈）。"""
    window._btn_action_scan()

def _write_detailed_report(root: Path, case_name: str, **kwargs):
    """可選的除錯報告，測試不會檢查內容；存在即可，避免 AttributeError。"""
    try:
        out = {
            "case": case_name,
            **{k: v for k, v in kwargs.items()}
        }
        # 將不可序列化的物件轉成字串
        def _default(o):
            try:
                json.dumps(o)
                return o
            except Exception:
                return str(o)
        report_path = root / f"_report_{case_name}.json"
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=_default)
    except Exception:
        # 測試不依賴此檔案，失敗就靜默
        pass

# -------------------------------
# pytest fixtures
# -------------------------------

@pytest.fixture(scope="session")
def qapp():
    """Session 等級 QApplication。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def window(qapp, qtbot, tmp_path):
    w = MatchImageFinder()
    qtbot.addWidget(w)

    original_cfg_data = w.cfg.data()

    # —— 初始化，避免跨測試干擾 ——
    try:
        w.settings.clear()        # 清掉 QSettings
    except Exception:
        pass
    w.cfg.set("ui.last_browser_path", str(tmp_path))
    w.browser_folder = str(tmp_path)
    w._browser_show(str(tmp_path))
    QApplication.processEvents()

    # 把物件交給測試使用
    yield w

    # —— teardown（測試結束後自動執行）——
    try:
        # 讓懸掛中的 lazy 任務作廢
        w._browser_lazy_gen = getattr(w, "_browser_lazy_gen", 0) + 1
    except Exception:
        pass
    try:
        w._ovw_build_gen = getattr(w, "_ovw_build_gen", 0) + 1
    except Exception:
        pass
    try:
        if getattr(w, "lock_timer", None):
            w.lock_timer.stop()
    except Exception:
        pass
    try:
        w.close()
    except Exception:
        pass
    QApplication.processEvents()
    try:
        w.cfg._cfg = deepcopy(original_cfg_data)
        w.cfg.save()
    except Exception:
        pass


@pytest.fixture
def helpers():
    """
    以「屬性」方式提供常用 helper。
    e.g. helpers.make_big_png(path)
    """
    return SimpleNamespace(
        file_gen_images = _file_gen_images,
        make_big_png=_make_big_png,
        read_json=_read_json,
        snapshot_fs=_snapshot_fs,
        filelist_path=_filelist_path,
        progress_path=_progress_path,
        jump_folder=_jump_folder,
        scan_now=_scan_now,
        write_detailed_report=_write_detailed_report,

    )
