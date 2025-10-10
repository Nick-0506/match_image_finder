import os

import pytest
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication, QDialogButtonBox, QListView, QWidget, QLabel, QMenu, QMessageBox, QListWidgetItem
from conftest import SETT_TEST
from utils.settings_dialog import SettingsDialog
from utils.constraints_store import ConstraintsStore

def _open_settings_dialog(window, qtbot):
    dlg = SettingsDialog(window.cfg, window.i18n, window.i18n_binder, parent=window)
    dlg.settings_applied.connect(window._cfg_apply_settings)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    return dlg


def _dismiss_message_box(qtbot, timeout=2000):
    from PyQt5.QtWidgets import QApplication, QMessageBox

    def _active_box():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.isVisible():
                return widget
        return None

    try:
        qtbot.waitUntil(lambda: _active_box() is not None, timeout=timeout)
    except Exception:
        return
    box = _active_box()
    if box is None:
        return
    button = box.button(QMessageBox.Ok)
    if button is not None:
        qtbot.mouseClick(button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not box.isVisible(), timeout=timeout)

_STYLE_EXPECTATIONS = {
    "list": (QListView.ListMode, 24),
    "small": (QListView.IconMode, 96),
    "medium": (QListView.IconMode, 128),
    "large": (QListView.IconMode, 196),
    "huge": (QListView.IconMode, 361),
}


def _select_combo_by_data(combo, value):
    idx = combo.findData(value)
    assert idx != -1, f"combo does not contain data '{value}'"
    combo.setCurrentIndex(idx)


def _wait_browser_ready(qtbot, window, expected_dir):
    expected_dir = os.path.abspath(str(expected_dir))

    def _ready():
        lw = getattr(window, "_browser_listw_ref", None)
        if lw is None or lw.count() == 0:
            return False
        folder = getattr(window, "browser_folder", "")
        return os.path.abspath(str(folder)) == expected_dir

    qtbot.waitUntil(_ready, timeout=5000)
    return getattr(window, "_browser_listw_ref", None)


def _wait_style_applied(qtbot, window, style_key):
    expected_mode, expected_icon = _STYLE_EXPECTATIONS[style_key]

    def _ok():
        lw = getattr(window, "_browser_listw_ref", None)
        if lw is None:
            return False
        return (
            window._browser_view_style_key == style_key
            and window.browser_view_style_combo.currentData() == style_key
            and lw.viewMode() == expected_mode
            and lw.iconSize().width() == expected_icon
            and lw.iconSize().height() == expected_icon
        )

    qtbot.waitUntil(_ok, timeout=4000)


def _browser_entry_basenames(window):
    lw = getattr(window, "_browser_listw_ref", None)
    assert lw is not None, "browser list widget is not available"
    names = []
    for i in range(lw.count()):
        it = lw.item(i)
        if not it:
            continue
        if it.text() == "..":
            continue
        data = it.data(Qt.UserRole)
        if data:
            names.append(os.path.basename(os.path.abspath(str(data))))
    return names


def _exercise_browser_style(qtbot, window, tmp_path, target_style):
    assert target_style in _STYLE_EXPECTATIONS, f"unsupported style '{target_style}'"
    window._browser_show(str(tmp_path))
    _wait_browser_ready(qtbot, window, str(tmp_path))

    original_style = window.cfg.get("ui.browser_view_style_key", "medium")
    fallback_style = "list" if target_style != "list" else "medium"
    dlg = None

    try:
        if original_style == target_style:
            window.cfg.set("ui.browser_view_style_key", fallback_style, autosave=False)
            window.cfg.save()
            window._cfg_apply_settings(["ui.browser_view_style_key"])
            _wait_style_applied(qtbot, window, fallback_style)

        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        _select_combo_by_data(dlg.browser_view_style_combo, target_style)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        _wait_style_applied(qtbot, window, target_style)
        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        window.cfg.set("ui.browser_view_style_key", original_style, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["ui.browser_view_style_key"])
        _wait_style_applied(qtbot, window, original_style)
        if dlg and dlg.isVisible():
            dlg.close()


def _prepare_settings_groups(window, tmp_path, helpers):
    grp1 = tmp_path / "grp1"
    grp2 = tmp_path / "grp2"
    grp1.mkdir(parents=True, exist_ok=True)
    grp2.mkdir(parents=True, exist_ok=True)

    img_a = grp1 / "a.png"
    img_b = grp1 / "b.png"
    img_c = grp2 / "c.png"
    helpers.make_big_png(img_a)
    helpers.make_big_png(img_b)
    helpers.make_big_png(img_c)

    def _rel(p):
        return str(p.relative_to(tmp_path)).replace("\\", "/")

    rel_a = _rel(img_a)
    rel_b = _rel(img_b)
    rel_c = _rel(img_c)

    window.work_folder = str(tmp_path)
    window.cfg.set("ui.last_browser_path", str(tmp_path), autosave=False)
    window.cfg.save()
    window.constraints = ConstraintsStore(str(tmp_path))
    window.groups = [[rel_a, rel_b], [rel_c]]
    window.view_groups = []
    window.view_groups_update = True
    window.stage = "done"
    window.compare_index = len(window.groups[0])
    window.phashes = {
        rel_a: {"hash": 0},
        rel_b: {"hash": 0},
        rel_c: {"hash": 0},
    }
    window.current = 0
    return rel_a, rel_b, rel_c


def _apply_browser_order(qtbot, window, tmp_path, *, ascend: bool):
    window._browser_show(str(tmp_path))
    _wait_browser_ready(qtbot, window, str(tmp_path))

    original_order = bool(window.cfg.get("ui.browser_order_asc", True))
    desired_text = "a->z" if ascend else "z->a"
    desired_value = ascend
    dlg = None

    try:
        if bool(window.cfg.get("ui.browser_order_asc", True)) == desired_value:
            window.cfg.set("ui.browser_order_asc", not desired_value, autosave=False)
            window.cfg.save()
            window._cfg_apply_settings(["ui.browser_order_asc"])
            _wait_browser_ready(qtbot, window, str(tmp_path))

        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        if dlg.browser_order_btn.text() != desired_text:
            qtbot.mouseClick(dlg.browser_order_btn, Qt.LeftButton)

        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: bool(window.cfg.get("ui.browser_order_asc")) == desired_value, timeout=2000)
        _wait_browser_ready(qtbot, window, str(tmp_path))

        names = _browser_entry_basenames(window)
        assert len(names) >= 2, "not enough items to verify ordering"
        lowered = [n.lower() for n in names]
        expected = sorted(lowered)
        if not ascend:
            expected = list(reversed(expected))
        assert lowered == expected, f"browser entries order mismatch: {names}"
        assert window.browser_order_btn.text() == desired_text

        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        window.cfg.set("ui.browser_order_asc", original_order, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["ui.browser_order_asc"])
        _wait_browser_ready(qtbot, window, str(tmp_path))
        if dlg and dlg.isVisible():
            dlg.close()


def test_setting_language(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.language:
        pytest.skip()

    original_lang = window.cfg.get("ui.lang", "zh-TW")
    original_locale = window.settings.value("locale", original_lang)

    dlg = _open_settings_dialog(window, qtbot)
    lang_combo = dlg.lang
    buttons = dlg.btns
    apply_btn = buttons.button(QDialogButtonBox.Apply)
    ok_btn = buttons.button(QDialogButtonBox.Ok)
    assert apply_btn is not None and ok_btn is not None

    def select_lang(code: str):
        idx = lang_combo.findData(code)
        assert idx != -1, f"language {code} not available"
        lang_combo.setCurrentIndex(idx)

    try:
        select_lang("en-US")
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: window.cfg.get("ui.lang") == "en-US", timeout=2000)
        assert window.settings.value("locale") == "en-US"
        assert window.i18n.t("menu.help") == "Help"

        select_lang("zh-TW")
        qtbot.mouseClick(ok_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: window.cfg.get("ui.lang") == "zh-TW", timeout=2000)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
        assert window.settings.value("locale") == "zh-TW"
        assert window.i18n.t("menu.help") == "說明"
    finally:
        if dlg.isVisible():
            dlg.close()
        window.cfg.set("ui.lang", original_lang, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["ui.lang"])
        qtbot.waitUntil(lambda: window.cfg.get("ui.lang") == original_lang, timeout=2000)
        assert window.settings.value("locale") == original_locale


def test_setting_fontsize(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.fontsize:
        pytest.skip()

    original_size = int(window.cfg.get("ui.font_size", 12))
    original_qfont_size = QApplication.font().pointSize()

    dlg = _open_settings_dialog(window, qtbot)
    font_spin = dlg.font_size_spin
    buttons = dlg.btns
    apply_btn = buttons.button(QDialogButtonBox.Apply)
    ok_btn = buttons.button(QDialogButtonBox.Ok)
    assert apply_btn is not None and ok_btn is not None

    try:
        font_spin.setValue(28)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: window.fontsize == 28, timeout=2000)
        assert QApplication.font().pointSize() == 28

        font_spin.setValue(10)
        qtbot.mouseClick(ok_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: window.fontsize == 10, timeout=2000)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
        assert QApplication.font().pointSize() == 10
    finally:
        if dlg.isVisible():
            dlg.close()
        window.cfg.set("ui.font_size", original_size, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["ui.font_size"])
        qtbot.waitUntil(lambda: window.fontsize == original_size, timeout=2000)
        assert QApplication.font().pointSize() == original_qfont_size

def test_setting_browser_list(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.stylelist:
        pytest.skip()
    _exercise_browser_style(qtbot, window, tmp_path, "list")

def test_setting_browser_small(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.stylesmall:
        pytest.skip()
    _exercise_browser_style(qtbot, window, tmp_path, "small")

def test_setting_browser_medium(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.stylemedium:
        pytest.skip()
    _exercise_browser_style(qtbot, window, tmp_path, "medium")

def test_setting_browser_large(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.stylelarge:
        pytest.skip()
    _exercise_browser_style(qtbot, window, tmp_path, "large")

def test_setting_browser_huge(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.stylehuge:
        pytest.skip()
    _exercise_browser_style(qtbot, window, tmp_path, "huge")

def test_setting_browser_asc(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.orderasc:
        pytest.skip()
    (tmp_path / "bbb_dir").mkdir(exist_ok=True)
    (tmp_path / "aaa_dir").mkdir(exist_ok=True)
    _apply_browser_order(qtbot, window, tmp_path, ascend=True)

def test_setting_browser_dsc(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.orderdsc:
        pytest.skip()
    (tmp_path / "bbb_dir").mkdir(exist_ok=True)
    (tmp_path / "aaa_dir").mkdir(exist_ok=True)
    _apply_browser_order(qtbot, window, tmp_path, ascend=False)

def test_setting_show_processing_images(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.showprocessingimage:
        pytest.skip()

    def _current_body_widget():
        lay = window.normal_body_layout
        return lay.itemAt(0).widget() if lay.count() else None

    original_flag = bool(window.cfg.get("ui.show_processing_image", False))
    dlg = None

    img_hash = tmp_path / "hash_preview.png"
    img_compare = tmp_path / "compare_preview.png"
    helpers.make_big_png(img_hash)
    helpers.make_big_png(img_compare)

    window.work_folder = str(tmp_path)
    rel_hash = os.path.relpath(img_hash, tmp_path).replace("\\", "/").lower()
    rel_compare = os.path.relpath(img_compare, tmp_path).replace("\\", "/").lower()

    try:
        window.cfg.set("ui.show_processing_image", False, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["ui.show_processing_image"])
        qtbot.waitUntil(lambda: not bool(window.show_processing_image), timeout=2000)

        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        cb = dlg.cb_show_processing_image
        cb.setChecked(True)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: bool(window.show_processing_image), timeout=2000)
        assert window.cfg.get("ui.show_processing_image") is True

        window.action = "hashing"
        window._chkbox_controller()
        assert window.display_img_dynamic_cb.isChecked()
        window._host_set_body_normal(QWidget())
        window._alg_hashing_show_current_image("hashing", rel_hash)
        QApplication.processEvents()
        body = _current_body_widget()
        assert body is not None
        previews = [
            lbl for lbl in body.findChildren(QLabel)
            if hasattr(lbl, "pixmap") and lbl.pixmap() and not lbl.pixmap().isNull()
        ]
        assert previews, "hashing stage should show preview when enabled"

        window.action = "comparing"
        window._chkbox_controller()
        assert window.display_img_dynamic_cb.isChecked()
        window._alg_comparing_show_pair_images(rel_hash, rel_compare)
        QApplication.processEvents()
        body = _current_body_widget()
        assert body is not None
        pair_previews = [
            lbl for lbl in body.findChildren(QLabel)
            if hasattr(lbl, "pixmap") and lbl.pixmap() and not lbl.pixmap().isNull()
        ]
        assert len(pair_previews) >= 2, "comparing stage should show both previews when enabled"

        cb.setChecked(False)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: not bool(window.show_processing_image), timeout=2000)
        assert window.cfg.get("ui.show_processing_image") is False

        window.action = "hashing"
        window._chkbox_controller()
        assert not window.display_img_dynamic_cb.isChecked()
        if window.display_img_dynamic_cb.isChecked():
            window._alg_hashing_show_current_image("hashing", rel_hash)
        else:
            window._host_set_body_normal(QWidget())
        QApplication.processEvents()
        body = _current_body_widget()
        assert body is not None
        disabled_previews = [
            lbl for lbl in body.findChildren(QLabel)
            if hasattr(lbl, "pixmap") and lbl.pixmap() and not lbl.pixmap().isNull()
        ]
        assert not disabled_previews, "hashing stage should hide preview when disabled"

        window.action = "comparing"
        window._chkbox_controller()
        assert not window.display_img_dynamic_cb.isChecked()
        if window.display_img_dynamic_cb.isChecked():
            window._alg_comparing_show_pair_images(rel_hash, rel_compare)
        else:
            window._host_set_body_normal(QWidget())
        QApplication.processEvents()
        body = _current_body_widget()
        assert body is not None
        disabled_pair_previews = [
            lbl for lbl in body.findChildren(QLabel)
            if hasattr(lbl, "pixmap") and lbl.pixmap() and not lbl.pixmap().isNull()
        ]
        assert not disabled_pair_previews, "comparing stage should hide preview when disabled"

        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        if dlg and dlg.isVisible():
            dlg.close()
        window.cfg.set("ui.show_processing_image", original_flag, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["ui.show_processing_image"])
        qtbot.waitUntil(lambda: bool(window.show_processing_image) == bool(original_flag), timeout=2000)
        window.action = "show_browser"
        window._chkbox_controller()
        window._host_set_body_normal(QWidget())


def test_setting_overview_thumbnial_size(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.overview_thumb_size:
        pytest.skip()

    _prepare_settings_groups(window, tmp_path, helpers)
    window.show_original_groups = False
    window.view_groups_update = True
    window._overview_show_api()
    qtbot.waitUntil(lambda: getattr(window, "_ovw_listw", None) is not None, timeout=4000)
    listw = window._ovw_listw
    assert listw is not None

    original_size = int(window.cfg.get("ui.overview_thumbnail.max_size", 240))
    target_size = 300 if original_size != 300 else 320

    dlg = None
    try:
        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        dlg.overview_thumb_slider.setValue(target_size)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: int(window.current_overview_thumb_size) == target_size, timeout=4000)
        qtbot.waitUntil(lambda: getattr(window, "_ovw_listw", None) is not None and int(window._ovw_listw.iconSize().width()) == target_size, timeout=4000)
        assert window.cfg.get("ui.overview_thumbnail.max_size") == target_size

        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        if dlg and dlg.isVisible():
            dlg.close()
        window.cfg.set("ui.overview_thumbnail.max_size", original_size, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["ui.overview_thumbnail.max_size"])
        qtbot.waitUntil(lambda: int(window.current_overview_thumb_size) == original_size, timeout=4000)
        qtbot.waitUntil(lambda: getattr(window, "_ovw_listw", None) is not None and int(window._ovw_listw.iconSize().width()) == int(max(120, min(320, original_size))), timeout=4000)


def test_setting_show_original_groups(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.show_original_groups:
        pytest.skip()

    _prepare_settings_groups(window, tmp_path, helpers)
    window.show_original_groups = False
    window.view_groups_update = True
    window._overview_show_api()
    qtbot.waitUntil(lambda: getattr(window, "_ovw_listw", None) is not None, timeout=4000)
    qtbot.waitUntil(lambda: window._ovw_listw.count() == 1, timeout=4000)

    original_flag = bool(window.cfg.get("ui.show_original_groups", False))
    dlg = None
    try:
        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        dlg.cb_show_original_group.setChecked(True)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: bool(window.show_original_groups), timeout=4000)
        qtbot.waitUntil(lambda: window._ovw_listw.count() == len(window.groups), timeout=4000)
        assert window.cfg.get("ui.show_original_groups") is True

        dlg.cb_show_original_group.setChecked(False)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: not bool(window.show_original_groups), timeout=4000)
        qtbot.waitUntil(lambda: window._ovw_listw.count() == 1, timeout=4000)
        assert window.cfg.get("ui.show_original_groups") is False

        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        if dlg and dlg.isVisible():
            dlg.close()
        window.cfg.set("ui.show_original_groups", original_flag, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["ui.show_original_groups"])
        qtbot.waitUntil(lambda: bool(window.show_original_groups) == original_flag, timeout=4000)


def test_setting_group_thumbnial_size(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.group_thumb_size:
        pytest.skip()

    _prepare_settings_groups(window, tmp_path, helpers)
    window.show_original_groups = True
    window.view_groups_update = True
    window._group_show_api()
    qtbot.waitUntil(lambda: getattr(window, "_listw_ref", None) is not None, timeout=4000)
    qtbot.waitUntil(lambda: window._listw_ref.count() > 0, timeout=4000)

    original_size = int(window.cfg.get("ui.thumbnail.max_size", 400))
    target_size = 420 if original_size != 420 else 460

    dlg = None
    try:
        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        dlg.thumb_slider.setValue(target_size)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: int(window.current_group_thumb_size) == target_size, timeout=4000)
        qtbot.waitUntil(lambda: getattr(window, "_listw_ref", None) is not None and int(window._listw_ref.iconSize().width()) == target_size, timeout=4000)
        assert window.cfg.get("ui.thumbnail.max_size") == target_size

        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        if dlg and dlg.isVisible():
            dlg.close()
        window.cfg.set("ui.thumbnail.max_size", original_size, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["ui.thumbnail.max_size"])
        qtbot.waitUntil(lambda: int(window.current_group_thumb_size) == original_size, timeout=4000)
        qtbot.waitUntil(lambda: getattr(window, "_listw_ref", None) is not None and int(window._listw_ref.iconSize().width()) == original_size, timeout=4000)

def test_setting_similarity_tolerance(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.similarity_tolerance:
        pytest.skip()

    original_value = int(window.cfg.get("behavior.similarity_tolerance", 5))
    original_overview_api = window._overview_show_api
    dlg = None
    try:
        dlg = _open_settings_dialog(window, qtbot)
        slider = dlg.similarity_tolerance_slider
        label = dlg.similarity_tolerance_value_label
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        hash_a = 0b1010101010101010
        hash_b = hash_a ^ 0b11  # bit distance = 2

        def _make_phashes():
            return {
                "a.png": {"hash": hash_a, "size": 100},
                "b.png": {"hash": hash_b, "size": 100},
            }

        def _run_comparing():
            window.phashes = _make_phashes()
            window.groups = []
            window.visited = set()
            window.compare_index = 0
            window.duplicate_size = 0
            window.image_paths = list(window.phashes.keys())
            window.paused = False
            window.action = "collecting"
            window._alg_comparing_pairwise()
            return [grp[:] for grp in window.groups]

        window._overview_show_api = lambda: None

        slider.setValue(0)
        qtbot.waitUntil(lambda: label.text() == "0", timeout=2000)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: int(window.similarity_tolerance) == 0, timeout=4000)
        assert int(window.cfg.get("behavior.similarity_tolerance")) == 0
        groups_zero = _run_comparing()
        assert not any(len(grp) > 1 for grp in groups_zero), "tolerance=0 should not group hashes with 2-bit distance"

        slider.setValue(15)
        qtbot.waitUntil(lambda: label.text() == "15", timeout=2000)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: int(window.similarity_tolerance) == 15, timeout=4000)
        assert int(window.cfg.get("behavior.similarity_tolerance")) == 15
        groups_high = _run_comparing()
        assert any(len(grp) > 1 for grp in groups_high), "tolerance=15 should group near hashes"

        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        if dlg and dlg.isVisible():
            dlg.close()
        window._overview_show_api = original_overview_api
        window.cfg.set("behavior.similarity_tolerance", original_value, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["behavior.similarity_tolerance"])
        qtbot.waitUntil(lambda: int(window.similarity_tolerance) == int(original_value), timeout=4000)
        assert int(window.cfg.get("behavior.similarity_tolerance")) == int(original_value)


def test_setting_compare_file_size(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.compare_file_size:
        pytest.skip()

    original_flag = bool(window.cfg.get("behavior.compare_file_size", True))
    original_tolerance = int(window.similarity_tolerance)
    original_overview_api = window._overview_show_api
    dlg = None
    try:
        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        cb = dlg.cb_compare_file_size
        cb.setChecked(True)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: bool(window.compare_file_size), timeout=4000)
        assert bool(window.cfg.get("behavior.compare_file_size")) is True

        window._overview_show_api = lambda: None
        window.similarity_tolerance = 15

        def _prepare(size_equal: bool):
            size_a = 100
            size_b = 100 if size_equal else 200
            window.phashes = {
                "a.png": {"hash": 0b1010, "size": size_a},
                "b.png": {"hash": 0b1010, "size": size_b},
            }
            window.groups = []
            window.visited = set()
            window.compare_index = 0
            window.duplicate_size = 0
            window.image_paths = list(window.phashes.keys())
            window.paused = False
            window.action = "collecting"

        _prepare(size_equal=False)
        window._alg_comparing_pairwise()
        assert not any(len(grp) > 1 for grp in window.groups), "compare_file_size=True should skip size-mismatched files"

        cb.setChecked(False)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: not bool(window.compare_file_size), timeout=4000)
        assert bool(window.cfg.get("behavior.compare_file_size")) is False

        _prepare(size_equal=False)
        window._alg_comparing_pairwise()
        assert any(len(grp) > 1 for grp in window.groups), "compare_file_size=False should group even when sizes differ"

        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        if dlg and dlg.isVisible():
            dlg.close()
        window._overview_show_api = original_overview_api
        window.cfg.set("behavior.compare_file_size", original_flag, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["behavior.compare_file_size"])
        qtbot.waitUntil(lambda: bool(window.compare_file_size) == bool(original_flag), timeout=4000)
        assert bool(window.cfg.get("behavior.compare_file_size")) == bool(original_flag)
        window.similarity_tolerance = original_tolerance


def test_setting_auto_next_group(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.auto_next_group:
        pytest.skip()

    original_flag = bool(window.cfg.get("behavior.auto_next_group", True))
    original_overview_api = window._overview_show_api
    original_group_api = window._group_show_api
    targets = [not original_flag, original_flag]

    dlg = None
    try:
        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        window._overview_show_api = lambda: None
        phashes = {
            "a.png": {"hash": 0b11110000, "size": 100},
            "b.png": {"hash": 0b11110000, "size": 100},
        }

        def _prepare():
            window.phashes = {k: dict(v) for k, v in phashes.items()}
            window.groups = []
            window.visited = set()
            window.compare_index = 0
            window.duplicate_size = 0
            window.image_paths = list(window.phashes.keys())
            window.paused = False
            window.action = "collecting"

        cb = dlg.cb_auto_next_group
        for desired in targets:
            cb.setChecked(desired)
            qtbot.mouseClick(apply_btn, Qt.LeftButton)
            _dismiss_message_box(qtbot)
            qtbot.waitUntil(lambda: window.auto_next_cb.isChecked() == desired, timeout=4000)
            assert bool(window.cfg.get("behavior.auto_next_group")) == desired

            calls = {"count": 0}

            def _fake_group_show_api():
                calls["count"] += 1
                window.action = "show_group"

            window._group_show_api = _fake_group_show_api
            _prepare()
            window._alg_comparing_pairwise()

            if desired:
                assert calls["count"] == 0, "auto-next enabled should not open group view"
                assert window.paused is False
            else:
                assert calls["count"] >= 1, "auto-next disabled should open group view"
                assert window.paused is True

            window.paused = False
            window._group_show_api = original_group_api

        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        if dlg and dlg.isVisible():
            dlg.close()
        window._overview_show_api = original_overview_api
        window._group_show_api = original_group_api
        window.cfg.set("behavior.auto_next_group", original_flag, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["behavior.auto_next_group"])
        qtbot.waitUntil(lambda: window.auto_next_cb.isChecked() == original_flag, timeout=4000)
        assert bool(window.cfg.get("behavior.auto_next_group")) == original_flag


def test_setting_confirm_delete(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.confirm_delete:
        pytest.skip()

    original_flag = bool(window.cfg.get("behavior.confirm_delete", True))
    original_popup_question = getattr(window, "_popup_question", None)

    dlg = _open_settings_dialog(window, qtbot)
    cancel_btn = dlg.btns.button(QDialogButtonBox.Cancel)
    assert cancel_btn is not None
    dlg.cb_confirm_delete.setChecked(not original_flag)
    qtbot.mouseClick(cancel_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    assert bool(window.cfg.get("behavior.confirm_delete")) == original_flag
    assert bool(window.confirm_delete) == original_flag

    dlg = None
    try:
        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and ok_btn is not None and cancel_btn is not None

        cb = dlg.cb_confirm_delete
        cb.setChecked(not original_flag)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: bool(window.confirm_delete) == (not original_flag), timeout=4000)
        assert bool(window.cfg.get("behavior.confirm_delete")) == (not original_flag)

        cb.setChecked(original_flag)
        qtbot.mouseClick(ok_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
        assert bool(window.cfg.get("behavior.confirm_delete")) == original_flag
        assert bool(window.confirm_delete) == original_flag

        target_img = tmp_path / "confirm_delete.png"
        helpers.make_big_png(target_img)
        window._browser_show(str(tmp_path))
        _wait_browser_ready(qtbot, window, str(tmp_path))

        lw = window._browser_listw_ref
        assert lw is not None

        def _select_item():
            for idx in range(lw.count()):
                it = lw.item(idx)
                data = it.data(Qt.UserRole)
                if data and os.path.abspath(data) == os.path.abspath(str(target_img)):
                    lw.setCurrentItem(it)
                    it.setSelected(True)
                    return it
            raise AssertionError("target file not found in browser list")

        def _trigger_delete(pos):
            original_exec = QMenu.exec_

            def fake_exec(menu, *args, **kwargs):
                acts = menu.actions()
                return acts[-1] if acts else None

            QMenu.exec_ = fake_exec
            try:
                window._browser_action_context_menu(pos)
            finally:
                QMenu.exec_ = original_exec

        window.cfg.set("behavior.confirm_delete", True, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["behavior.confirm_delete"])
        qtbot.waitUntil(lambda: bool(window.confirm_delete) is True, timeout=4000)

        prompts = []

        def popup_yesno(title, body, default):
            prompts.append((title, body, default))
            return QMessageBox.No

        window._popup_question = popup_yesno
        item = _select_item()
        pos = lw.visualItemRect(item).center()
        _trigger_delete(pos)
        assert prompts, "confirm_delete=True should ask for confirmation"
        assert target_img.exists(), "delete should be cancelled when user answers No"

        window.cfg.set("behavior.confirm_delete", False, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["behavior.confirm_delete"])
        qtbot.waitUntil(lambda: bool(window.confirm_delete) is False, timeout=4000)

        unexpected_prompts = []

        def popup_should_not_call(*args, **kwargs):
            unexpected_prompts.append(True)
            return QMessageBox.Yes

        window._popup_question = popup_should_not_call
        item = _select_item()
        pos = lw.visualItemRect(item).center()
        _trigger_delete(pos)
        assert not unexpected_prompts, "confirm_delete=False should bypass confirmation dialog"
        assert not target_img.exists(), "file should be deleted when confirmation disabled"
    finally:
        if dlg and dlg.isVisible():
            dlg.close()
        window._popup_question = original_popup_question
        window.cfg.set("behavior.confirm_delete", original_flag, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["behavior.confirm_delete"])
        qtbot.waitUntil(lambda: bool(window.confirm_delete) == original_flag, timeout=4000)
        assert bool(window.cfg.get("behavior.confirm_delete")) == original_flag


def test_setting_display_same_images(qtbot, window, tmp_path, helpers):
    if not SETT_TEST.display_same_images:
        pytest.skip()

    original_flag = bool(window.cfg.get("behavior.display_same_images", True))
    original_detail = window._group_show_detail
    original_show_image = window._group_show_image
    original_work_folder = window.work_folder
    original_browser_folder = getattr(window, "browser_folder", None)
    original_last_path = window.cfg.get("ui.last_browser_path")
    rel_a, rel_b, rel_c = _prepare_settings_groups(window, tmp_path, helpers)
    work_folder_path = window.work_folder
    window.image_paths = [rel_a, rel_b, rel_c]
    window.last_scan_time = "just-now"
    window._db_save_filelist(work_folder_path)
    window._db_save_progress(work_folder_path, stage="done")
    group1_abs = os.path.abspath(os.path.join(work_folder_path, rel_a))
    group2_abs = os.path.abspath(os.path.join(work_folder_path, rel_c))
    dlg = None
    try:
        dlg = _open_settings_dialog(window, qtbot)
        buttons = dlg.btns
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        assert apply_btn is not None and cancel_btn is not None

        def _assert_browser_click(target_abs: str, expect_detail: bool):
            detail_calls = []
            image_calls = []

            def fake_detail(index):
                detail_calls.append(index)

            def fake_show(path):
                image_calls.append(path)

            window._group_show_detail = fake_detail
            window._group_show_image = fake_show
            original_single_shot = None
            if not expect_detail:
                original_single_shot = QTimer.singleShot

                def _immediate_single_shot(timeout, arg1, arg2=None):
                    if callable(arg1) and arg2 is None:
                        arg1()
                    elif arg2 is not None and callable(arg2):
                        arg2()
                    else:
                        original_single_shot(timeout, arg1, arg2)

                QTimer.singleShot = staticmethod(_immediate_single_shot)
            try:
                window.related_files_mode = False
                window.browser_folder = os.path.dirname(target_abs)
                item = QListWidgetItem()
                item.setData(Qt.UserRole, target_abs)
                window._browser_action_click(item)
                if expect_detail:
                    qtbot.waitUntil(lambda: bool(detail_calls), timeout=2000)
                    assert not image_calls, "group detail should be used instead of single image view"
                else:
                    qtbot.waitUntil(lambda: bool(image_calls), timeout=2000)
                    assert not detail_calls, "group detail should not be used when disabled"
            finally:
                if original_single_shot is not None:
                    QTimer.singleShot = original_single_shot
                window._group_show_detail = original_detail
                window._group_show_image = original_show_image

        cb = dlg.cb_display_same_images
        cb.setChecked(True)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: bool(window.display_same_images) is True, timeout=4000)
        assert bool(window.cfg.get("behavior.display_same_images")) is True
        _assert_browser_click(group1_abs, expect_detail=True)

        cb.setChecked(False)
        qtbot.mouseClick(apply_btn, Qt.LeftButton)
        _dismiss_message_box(qtbot)
        qtbot.waitUntil(lambda: bool(window.display_same_images) is False, timeout=4000)
        assert bool(window.cfg.get("behavior.display_same_images")) is False
        _assert_browser_click(group2_abs, expect_detail=False)

        qtbot.mouseClick(cancel_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=2000)
    finally:
        if dlg and dlg.isVisible():
            dlg.close()
        window._group_show_detail = original_detail
        window._group_show_image = original_show_image
        window.work_folder = original_work_folder
        window.cfg.set("ui.last_browser_path", original_last_path, autosave=False)
        window.cfg.save()
        if original_browser_folder:
            window._browser_show(original_browser_folder)
        elif original_last_path:
            window._browser_show(original_last_path)
        else:
            window.browser_folder = original_browser_folder
        window.cfg.set("behavior.display_same_images", original_flag, autosave=False)
        window.cfg.save()
        window._cfg_apply_settings(["behavior.display_same_images"])
        qtbot.waitUntil(lambda: bool(window.display_same_images) == original_flag, timeout=4000)
        assert bool(window.cfg.get("behavior.display_same_images")) == original_flag
