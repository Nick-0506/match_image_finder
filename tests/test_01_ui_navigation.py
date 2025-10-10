import os
from contextlib import contextmanager
from helpers_ui import double_click_item_by_path, click_button, open_overview_group_by_index
from Match_Image_Finder import FILELIST_FILE, PROGRESS_FILE
from pathlib import Path
from PyQt5.QtCore import Qt
from conftest import UI_TEST
import pytest

_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}

def wait_done(qtbot, window):
    qtbot.waitUntil(lambda: window.stage == "done", timeout=15000)

def _norm_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix().lower()

@contextmanager
def _preserve_work_and_browser(window):
    original_work = window.work_folder
    original_browser = getattr(window, "browser_folder", None)
    original_last_path = window.cfg.get("ui.last_browser_path")
    try:
        yield
    finally:
        window.work_folder = original_work
        window.cfg.set("ui.last_browser_path", original_last_path, autosave=False)
        window.cfg.save()
        if original_browser:
            window._browser_show(original_browser)
        elif original_last_path:
            window._browser_show(original_last_path)
        else:
            window.browser_folder = original_browser

def test_navigation_browser_by_clicking(qtbot, window, tmp_path, helpers):
    if not UI_TEST.navigation_browser:
        pytest.skip()
    with _preserve_work_and_browser(window):
        root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=2)
        window._browser_show(str(root))
        qtbot.waitUntil(lambda: getattr(window, "_browser_listw_ref", None) is not None, timeout=5000)

        double_click_item_by_path(window, str(a), qtbot=qtbot)
        assert window.browser_folder == str(a)  # sanity check

        double_click_item_by_path(window, str(b), qtbot=qtbot)
        assert window.browser_folder == str(b)  # sanity check

        double_click_item_by_path(window, str(d), qtbot=qtbot)
        assert window.browser_folder == str(d)  # sanity check

        click_button(window.show_browser_back_btn) # Back to B
        click_button(window.show_browser_back_btn) # Back to A

        double_click_item_by_path(window, str(c), qtbot=qtbot)
        assert window.browser_folder == str(c)  # sanity check

        double_click_item_by_path(window, str(f), qtbot=qtbot)
        assert window.browser_folder == str(f)  # sanity check

        double_click_item_by_path(window, str(h), qtbot=qtbot)
        assert window.browser_folder == str(h)  # sanity check
        
        click_button(window.show_browser_back_btn) # Back to F
        click_button(window.show_browser_back_btn) # Back to C
        click_button(window.show_browser_back_btn) # Back to A
        click_button(window.show_browser_back_btn) # Back to root
        assert window.browser_folder == str(root) 

def test_navigation_overview_by_clicking(qtbot, window, tmp_path, helpers):
    if not UI_TEST.navigation_overview:
        pytest.skip()
    with _preserve_work_and_browser(window):
        root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers)
        window._browser_show(str(root))
        qtbot.waitUntil(lambda: getattr(window, "_browser_listw_ref", None) is not None, timeout=5000)

        double_click_item_by_path(window, str(a), qtbot=qtbot)
        assert window.browser_folder == str(a)  # sanity check

        click_button(window.scan_btn)
        wait_done(qtbot, window)
        
        cols = window.overview_cols
        rows = window.overview_rows
        per_page = cols * rows
        max_page = (max(len(window.view_groups) - 1, 0)) // per_page

        click_button(window.last_btn)
        qtbot.waitUntil(lambda: window.overview_page == max_page, timeout=3000)

        click_button(window.last_btn)
        qtbot.waitUntil(lambda: window.overview_page == max_page, timeout=3000)

        click_button(window.next_btn)
        qtbot.waitUntil(lambda: window.overview_page == max_page, timeout=3000)

        click_button(window.first_btn)
        qtbot.waitUntil(lambda: window.overview_page == 0, timeout=3000)
        
        click_button(window.first_btn)
        qtbot.waitUntil(lambda: window.overview_page == 0, timeout=3000)

        click_button(window.prev_btn)
        qtbot.waitUntil(lambda: window.overview_page == 0, timeout=3000)

        click_button(window.next_btn)
        qtbot.waitUntil(lambda: window.overview_page == 1, timeout=3000)

        click_button(window.prev_btn)
        qtbot.waitUntil(lambda: window.overview_page == 0, timeout=3000)

        click_button(window.show_browser_back_btn)
        click_button(window.show_browser_back_btn)
        assert window.browser_folder == str(root)  # sanity check

def test_navigation_group_by_clicking(qtbot, window, tmp_path, helpers):
    if not UI_TEST.navigation_group:
        pytest.skip()
    with _preserve_work_and_browser(window):
        root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers)
        window._browser_show(str(root))
        qtbot.waitUntil(lambda: getattr(window, "_browser_listw_ref", None) is not None, timeout=15000)

        double_click_item_by_path(window, str(a), qtbot=qtbot, timeout=5000)
        assert window.browser_folder == str(a)  # sanity check
        click_button(window.scan_btn)
        wait_done(qtbot, window)
        
        window._overview_show_g1b1()
        qtbot.waitUntil(lambda: window.action == "show_overview")

        listw = getattr(window, "_ovw_listw", None)
        assert listw is not None and listw.count() > 0

        open_overview_group_by_index(window, qtbot, index=0, timeout=3000)

        assert window.action == "show_group"

        click_button(window.last_btn)
        qtbot.waitUntil(lambda: window.current == len(window.view_groups)-1, timeout=3000)

        click_button(window.last_btn)
        qtbot.waitUntil(lambda: window.current == len(window.view_groups)-1, timeout=3000)

        click_button(window.next_btn)
        qtbot.waitUntil(lambda: window.current == len(window.view_groups)-1, timeout=3000)

        click_button(window.first_btn)
        qtbot.waitUntil(lambda: window.current == 0, timeout=3000)
        
        click_button(window.first_btn)
        qtbot.waitUntil(lambda: window.current == 0, timeout=3000)

        click_button(window.prev_btn)
        qtbot.waitUntil(lambda: window.current == 0, timeout=3000)

        click_button(window.next_btn)
        qtbot.waitUntil(lambda: window.current == 1, timeout=3000)

        click_button(window.prev_btn)
        qtbot.waitUntil(lambda: window.current == 0, timeout=3000)
