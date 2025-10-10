import itertools
import os
from conftest import FUNC_TEST
import pytest
from helpers_ui import (
    double_click_item_by_path,
    click_button,
    open_overview_group_by_index,
    wait_browser_list_filled,
)
from Match_Image_Finder import FILELIST_FILE, PROGRESS_FILE
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMenu, QFileDialog, QApplication

_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}

def wait_done(qtbot, window):
    qtbot.waitUntil(lambda: window.stage == "done", timeout=15000)

@pytest.fixture(autouse=True)
def _restore_work_folder_after_test(window):
    original_work = window.work_folder
    original_browser = getattr(window, "browser_folder", None)
    original_last_path = window.cfg.get("ui.last_browser_path")
    yield
    window.work_folder = original_work
    window.cfg.set("ui.last_browser_path", original_last_path, autosave=False)
    window.cfg.save()
    if original_browser:
        window._browser_show(original_browser)
    elif original_last_path:
        window._browser_show(original_last_path)
    else:
        window.browser_folder = original_browser


def file_build_expected_for_scan(scan_root: Path, exclude_keyword: str | None = None) -> set[str]:
    scan_root = Path(scan_root)
    expected = set()

    for dirpath, dirnames, filenames in os.walk(scan_root, topdown=True):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(f for f in filenames if not f.startswith(".")):
            ext = os.path.splitext(fn)[1].lower()
            if ext in _EXTS:
                rel = Path(dirpath, fn).relative_to(scan_root)
                expected.add(rel.as_posix())
    return expected

def _find_group_checkboxes(window, expected_len):
    for attr in ("_grp_checks", "_group_checks", "_checks", "group_checkboxes"):
        if hasattr(window, attr):
            arr = getattr(window, attr)
            if isinstance(arr, (list, tuple)) and len(arr) == expected_len:
                return arr
    raise AssertionError("Can't find checkbox array")

def _ensure_all_checked(qtbot, checkboxes):
    paths = []
    for cb in checkboxes:
        if not cb.isChecked():
            qtbot.mouseClick(cb, Qt.LeftButton)
        assert cb.isChecked(), "Each checkbox should be checked"
        paths.append(cb.path)
    return paths

def _normalize_pairs(pairs_iterable):
    normalized = set()
    for pair in pairs_iterable or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            normalized.add(tuple(sorted(pair)))
    return normalized

def _pair_combinations(paths):
    return {tuple(sorted(pair)) for pair in itertools.combinations(paths, 2)}

def _wait_constraints_json(qtbot, helpers, json_path, predicate, timeout=5000):
    def _ready():
        data = helpers.read_json(json_path)
        return isinstance(data, dict) and predicate(data)
    qtbot.waitUntil(_ready, timeout=timeout)
    data = helpers.read_json(json_path)
    return data if isinstance(data, dict) else {}

def _find_item_by_abs_path(lw, target_path):
    target_abs = os.path.abspath(str(target_path))
    for i in range(lw.count()):
        it = lw.item(i)
        data = it.data(Qt.UserRole) if it else None
        if data and os.path.abspath(str(data)) == target_abs:
            return it
    return None

def file_assert_filelist_matches(qtbot, window, tmp_path, helpers, src_abs, expect):
    fl = helpers.read_json(src_abs)

    assert fl is not None, f"{src_abs} read fail"
    assert set(fl.get("image_paths", [])) == expect, f": {src_abs}'s  image_paths not match: {fl.get('image_paths')}"

def file_assert_progress_matches(pr_path: Path, scan_root: Path, expected_rel_set: set[str], expected_stage="done"):
    import json

    assert pr_path.exists(), f"{pr_path} not exist"
    with pr_path.open("r", encoding="utf-8") as f:
        pr = json.load(f)

    assert pr.get("hash_format") == "v2"
    assert pr.get("stage") == expected_stage, f"stage should be {expected_stage}, actually: {pr.get('stage')}"
    phashes = pr.get("phashes")
    assert isinstance(phashes, dict), "phashes should be dict"

    if expected_stage == "done":
        got_keys = set(map(str, phashes.keys()))
        assert got_keys == expected_rel_set, f"phashes key set mismatch.\nexpected={sorted(expected_rel_set)}\n got={sorted(got_keys)}"

        assert pr.get("file_counter") == len(phashes), "file_counter should equal the length of phashes"
        assert pr.get("compare_index") == len(phashes), "compare_index should equal the phashes length when stage is done"

        def _abs(path_rel: str) -> Path:
            return (scan_root / path_rel).resolve()

        for rel, meta in phashes.items():
            assert isinstance(meta, dict), f"phashes[{rel}] should be dict"
            assert "hash" in meta and "mtime" in meta and "size" in meta, f"phashes[{rel}] is missing required fields: {meta}"

            ap = _abs(rel)
            assert ap.exists(), f"File does not exist: {ap}"
            assert meta["size"] == ap.stat().st_size, f"size mismatch: {rel}"
            assert abs(meta["mtime"] - ap.stat().st_mtime) < 1.0, f"mtime differs too much: {rel}"

        visited = pr.get("visited", [])
        assert isinstance(visited, list) and len(visited) == 0, "visited should be empty when stage is done"
        assert isinstance(pr.get("groups", []), list), "groups should be list"

def test_scan_by_clicking(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.scan_by_clicking:
        pytest.skip()
    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=2)
    window._browser_show(str(root))
    qtbot.waitUntil(lambda: getattr(window, "_browser_listw_ref", None) is not None, timeout=5000)

    # ---- Pass 1: enter folder a and scan ----
    double_click_item_by_path(window, str(a), qtbot=qtbot)
    assert window.browser_folder == str(a)  # sanity check
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    expected = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a/FILELIST_FILE, expected)
    file_assert_progress_matches(a/PROGRESS_FILE,a,expected)

    # ---- Pass 2: go up, enter folder b, scan ----
    click_button(window.show_browser_back_btn)
    double_click_item_by_path(window, str(b), qtbot=qtbot)
    assert window.browser_folder == str(b)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    expected = file_build_expected_for_scan(b)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, b/FILELIST_FILE, expected)
    file_assert_progress_matches(b/PROGRESS_FILE,b,expected)

    # ---- Pass 3: go up twice, enter folder c, scan ----
    click_button(window.show_browser_back_btn)
    click_button(window.show_browser_back_btn)
    double_click_item_by_path(window, str(c), qtbot=qtbot)
    assert window.browser_folder == str(c)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    
    expected = file_build_expected_for_scan(c)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, c/FILELIST_FILE, expected)
    file_assert_progress_matches(c/PROGRESS_FILE,c,expected)

    click_button(window.show_browser_back_btn)
    click_button(window.show_browser_back_btn)

    app = QApplication.instance()
    orig_quit = app.quit
    try:
        app.quit = lambda: None
        window._btn_action_exit_and_save()
    finally:
        app.quit = orig_quit

    expected = file_build_expected_for_scan(b)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, b / FILELIST_FILE, expected)
    file_assert_progress_matches(b / PROGRESS_FILE, b, expected)

def test_delete_by_clicking(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.delete_by_clicking:
        pytest.skip()
    # Build a directory tree and enter the root (fewer images to keep the test fast)
    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=5)
    window._browser_show(str(root))
    qtbot.waitUntil(lambda: getattr(window, "_browser_listw_ref", None) is not None, timeout=5000)

    # Enter folder a then scan
    double_click_item_by_path(window, str(a), qtbot=qtbot)
    assert window.browser_folder == str(a)
    click_button(window.scan_btn)
    qtbot.waitUntil(lambda: window.action == "show_overview", timeout=5000)

    # Check database
    expected_before = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a / FILELIST_FILE, expected_before)
    file_assert_progress_matches(a / PROGRESS_FILE, a, expected_before)

    # Enter show_group
    open_overview_group_by_index(window, qtbot, index=0, timeout=5000)
    qtbot.waitUntil(lambda: window.action == "show_group", timeout=5000)
    assert window.action == "show_group"

    # Currently visible group members (paths relative to work_folder)
    group_index = window.current
    members_rel = list(window.view_groups[group_index])
    assert len(members_rel) >= 1

    # ---- Delete: uncheck the first image and press Delete (no rescan) ----
    group_index_before = window.current
    groups_len_before  = len(window.view_groups)
    members_before     = list(window.view_groups[group_index_before])
    count_before       = len(members_before)

    # Target: delete the first image (unchecked = delete)
    victim_rel = members_before[0]
    victim_abs = (Path(a) / victim_rel).resolve()
    victim_rel_posix = Path(victim_rel).as_posix().lower()
    fl_path = a / FILELIST_FILE
    pr_path = a / PROGRESS_FILE
    fl_m0 = fl_path.stat().st_mtime if fl_path.exists() else 0
    pr_m0 = pr_path.stat().st_mtime if pr_path.exists() else 0

    # Disable delete confirmation if the app exposes a toggle
    try:
        window.confirm_delete = False
    except Exception:
        pass

    # Locate the checkbox array in the group view
    checkboxes = None
    for attr in ("_grp_checks", "_group_checks", "_checks", "group_checkboxes"):
        if hasattr(window, attr):
            arr = getattr(window, attr)
            if isinstance(arr, (list, tuple)) and len(arr) == count_before:
                checkboxes = arr
                break

    # Drive the UI (checked = keep, unchecked = delete)
    if checkboxes:
        cb = checkboxes[0]
        if cb.isChecked():
            qtbot.mouseClick(cb, Qt.LeftButton)
        # Sanity check: only the victim should be unchecked
        assert sum(1 for c in checkboxes if not c.isChecked()) == 1
        click_button(window.delete_btn)

    # Replacement for the original "wait for delete/refresh" block

    # 1) Wait until the file truly disappears from disk
    qtbot.waitUntil(lambda: not victim_abs.exists(), timeout=10000)
    
    # 2) Validate JSON once the file is gone (the metadata should now be refreshed)
    expected_after = file_build_expected_for_scan(a)
    # Final assertions using the existing helpers
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, fl_path, expected_after)
    file_assert_progress_matches(pr_path, a, expected_after)

def test_marked_same_by_clicking(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.mark_same_by_clicking:
        pytest.skip()
    # Build a directory tree and enter the root (fewer images to keep the test fast)
    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=5)
    window._browser_show(str(root))
    qtbot.waitUntil(lambda: getattr(window, "_browser_listw_ref", None) is not None, timeout=5000)

    # ---- Enter folder a and scan ----
    double_click_item_by_path(window, str(a), qtbot=qtbot)
    assert window.browser_folder == str(a)
    click_button(window.scan_btn)
    wait_done(qtbot, window)

    # Verify state before any mutations (expected_before)
    expected_before = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a / FILELIST_FILE, expected_before)
    file_assert_progress_matches(a / PROGRESS_FILE, a, expected_before)

    # ---- Open the overview and drill into the first group ----
    window._overview_show_g1b1()
    qtbot.waitUntil(lambda: window.action == "show_overview", timeout=5000)
    open_overview_group_by_index(window, qtbot, index=0, timeout=5000)
    qtbot.waitUntil(lambda: window.action == "show_group", timeout=5000)
    assert window.action == "show_group"

    # Currently visible group members (paths relative to work_folder)
    group_index = window.current
    members_rel = list(window.view_groups[group_index])
    assert len(members_rel) >= 1

    # ---- Select the entire group and click Merge (mark as same) ----
    group_index_before = window.current
    members_before = list(window.view_groups[group_index_before])
    assert len(members_before) >= 2, "Need at least two images to mark as the same"

    checkboxes = _find_group_checkboxes(window, len(members_before))
    selected_paths = _ensure_all_checked(qtbot, checkboxes)
    assert len(selected_paths) == len(members_before)

    expected_pairs = _pair_combinations(selected_paths)

    click_button(window.merge_btn)

    def _must_pairs_ready():
        pairs_obj = getattr(window.constraints, "must_pairs", set())
        return expected_pairs.issubset(_normalize_pairs(pairs_obj))

    qtbot.waitUntil(_must_pairs_ready, timeout=5000)

    constraints_path = a / ".constraints.json"

    data = _wait_constraints_json(
        qtbot,
        helpers,
        constraints_path,
        lambda d: expected_pairs.issubset(_normalize_pairs(d.get("must_links", [])))
                  and not d.get("cannot_links")
                  and not d.get("ignored_files"),
        timeout=5000,
    )

    stored_pairs = _normalize_pairs(data.get("must_links", []))
    assert stored_pairs.issuperset(expected_pairs)
    assert not data.get("cannot_links"), "Merge should not create cannot links"
    assert not data.get("ignored_files"), "Merge should not populate the ignored list"

    qtbot.waitUntil(lambda: not window.view_groups_update, timeout=1000)
    current_group = list(window.view_groups[window.current])
    assert sorted(current_group) == sorted(selected_paths)

def test_marked_different_by_clicking(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.mark_different_by_clicking:
        pytest.skip()
    # Build a directory tree and enter the root (fewer images to keep the test fast)
    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=5)
    window._browser_show(str(root))
    qtbot.waitUntil(lambda: getattr(window, "_browser_listw_ref", None) is not None, timeout=5000)

    # ---- Enter folder a and scan ----
    double_click_item_by_path(window, str(a), qtbot=qtbot)
    assert window.browser_folder == str(a)
    click_button(window.scan_btn)
    wait_done(qtbot, window)

    # Verify state before any mutations (expected_before)
    expected_before = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a / FILELIST_FILE, expected_before)
    file_assert_progress_matches(a / PROGRESS_FILE, a, expected_before)

    # ---- Open the overview and drill into the first group ----
    window._overview_show_g1b1()
    qtbot.waitUntil(lambda: window.action == "show_overview", timeout=5000)
    open_overview_group_by_index(window, qtbot, index=0, timeout=5000)
    qtbot.waitUntil(lambda: window.action == "show_group", timeout=5000)
    assert window.action == "show_group"

    # Currently visible group members (paths relative to work_folder)
    group_index = window.current
    members_rel = list(window.view_groups[group_index])
    assert len(members_rel) >= 1

    # ---- Select the entire group and click Separate (mark as different) ----
    group_index_before = window.current
    members_before = list(window.view_groups[group_index_before])
    assert len(members_before) >= 2, "Need at least two images to declare them different"

    checkboxes = _find_group_checkboxes(window, len(members_before))
    selected_paths = _ensure_all_checked(qtbot, checkboxes)
    assert len(selected_paths) == len(members_before)

    expected_pairs = _pair_combinations(selected_paths)

    click_button(window.separate_btn)

    def _cannot_pairs_ready():
        pairs_obj = getattr(window.constraints, "cannot_pairs", set())
        return expected_pairs.issubset(_normalize_pairs(pairs_obj))

    qtbot.waitUntil(_cannot_pairs_ready, timeout=5000)

    constraints_path = a / ".constraints.json"
    data = _wait_constraints_json(
        qtbot,
        helpers,
        constraints_path,
        lambda d: expected_pairs.issubset(_normalize_pairs(d.get("cannot_links", [])))
                  and not d.get("must_links")
                  and not d.get("ignored_files"),
        timeout=5000,
    )

    stored_cannot = _normalize_pairs(data.get("cannot_links", []))
    assert stored_cannot.issuperset(expected_pairs)
    assert not data.get("must_links"), "Separate should not create must links"
    assert not data.get("ignored_files"), "Separate should not populate the ignored list"

    qtbot.waitUntil(lambda: not window.view_groups_update, timeout=1000)
    selected_set = set(selected_paths)
    remaining_groups = [set(grp) for grp in window.view_groups]
    assert all(not selected_set.issubset(grp_set) for grp_set in remaining_groups), "Groups should refresh or be removed after separation"

def test_marked_ignore_by_clicking(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.mark_ignore_by_clicking:
        pytest.skip()
    # Build a directory tree and enter the root (fewer images to keep the test fast)
    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=5)
    window._browser_show(str(root))
    qtbot.waitUntil(lambda: getattr(window, "_browser_listw_ref", None) is not None, timeout=5000)

    # ---- Enter folder a and scan ----
    double_click_item_by_path(window, str(a), qtbot=qtbot)
    assert window.browser_folder == str(a)
    click_button(window.scan_btn)
    wait_done(qtbot, window)

    # Verify state before any mutations (expected_before)
    expected_before = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a / FILELIST_FILE, expected_before)
    file_assert_progress_matches(a / PROGRESS_FILE, a, expected_before)

    # ---- Open the overview and drill into the first group ----
    window._overview_show_g1b1()
    qtbot.waitUntil(lambda: window.action == "show_overview", timeout=5000)
    open_overview_group_by_index(window, qtbot, index=0, timeout=5000)
    qtbot.waitUntil(lambda: window.action == "show_group", timeout=5000)
    assert window.action == "show_group"

    # Currently visible group members (paths relative to work_folder)
    group_index = window.current
    members_rel = list(window.view_groups[group_index])
    assert len(members_rel) >= 1

    # ---- Select the entire group and click Ignore ----
    group_index_before = window.current
    members_before = list(window.view_groups[group_index_before])
    assert len(members_before) >= 1, "Need at least one image to ignore"

    checkboxes = _find_group_checkboxes(window, len(members_before))
    selected_paths = _ensure_all_checked(qtbot, checkboxes)
    assert len(selected_paths) == len(members_before)

    expected_ignored = {p.lower() for p in selected_paths}

    click_button(window.ignore_btn)

    def _ignored_ready():
        ignored = getattr(window.constraints, "ignored_files", set())
        normalized = {s.lower() for s in (ignored or [])}
        return expected_ignored.issubset(normalized)

    qtbot.waitUntil(_ignored_ready, timeout=5000)

    constraints_path = a / ".constraints.json"
    data = _wait_constraints_json(
        qtbot,
        helpers,
        constraints_path,
        lambda d: expected_ignored.issubset({str(s).lower() for s in d.get("ignored_files", [])})
                  and not d.get("must_links")
                  and not d.get("cannot_links"),
        timeout=5000,
    )

    stored_ignored = {str(s).lower() for s in data.get("ignored_files", [])}
    assert stored_ignored.issuperset(expected_ignored)
    assert not data.get("must_links"), "Ignore should not create must links"
    assert not data.get("cannot_links"), "Ignore should not create cannot links"

    qtbot.waitUntil(lambda: not window.view_groups_update, timeout=1000)
    remaining_groups = [{p.lower() for p in grp} for grp in window.view_groups]
    assert all(not expected_ignored.issubset(grp_set) for grp_set in remaining_groups), "Ignored groups should no longer appear"

def test_marked_clear_by_clicking(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.mark_clear_by_clicking:
        pytest.skip()
    # Build a directory tree and enter the root (fewer images to keep the test fast)
    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=5)
    window._browser_show(str(root))
    qtbot.waitUntil(lambda: getattr(window, "_browser_listw_ref", None) is not None, timeout=5000)

    # ---- Enter folder a and scan ----
    double_click_item_by_path(window, str(a), qtbot=qtbot)
    assert window.browser_folder == str(a)
    click_button(window.scan_btn)
    wait_done(qtbot, window)

    # Verify state before any mutations (expected_before)
    expected_before = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a / FILELIST_FILE, expected_before)
    file_assert_progress_matches(a / PROGRESS_FILE, a, expected_before)

    # ---- Open the overview and drill into the first group ----
    window._overview_show_g1b1()
    qtbot.waitUntil(lambda: window.action == "show_overview", timeout=5000)
    open_overview_group_by_index(window, qtbot, index=0, timeout=5000)
    qtbot.waitUntil(lambda: window.action == "show_group", timeout=5000)
    assert window.action == "show_group"

    # Currently visible group members (paths relative to work_folder)
    group_index = window.current
    members_rel = list(window.view_groups[group_index])
    assert len(members_rel) >= 1

    # ---- Select the entire group, Merge, then Clear (remove the marker) ----
    group_index_before = window.current
    members_before = list(window.view_groups[group_index_before])
    assert len(members_before) >= 2, "Need at least two images to exercise clearing"

    checkboxes = _find_group_checkboxes(window, len(members_before))
    selected_paths = _ensure_all_checked(qtbot, checkboxes)
    assert len(selected_paths) == len(members_before)

    expected_pairs = _pair_combinations(selected_paths)

    click_button(window.merge_btn)

    def _must_pairs_ready():
        pairs_obj = getattr(window.constraints, "must_pairs", set())
        return expected_pairs.issubset(_normalize_pairs(pairs_obj))

    qtbot.waitUntil(_must_pairs_ready, timeout=5000)

    constraints_path = a / ".constraints.json"
    _wait_constraints_json(
        qtbot,
        helpers,
        constraints_path,
        lambda d: expected_pairs.issubset(_normalize_pairs(d.get("must_links", []))),
        timeout=5000,
    )

    click_button(window.unmarked_btn)

    def _constraints_cleared():
        return (
            not _normalize_pairs(getattr(window.constraints, "must_pairs", set()))
            and not _normalize_pairs(getattr(window.constraints, "cannot_pairs", set()))
            and not getattr(window.constraints, "ignored_files", set())
        )

    qtbot.waitUntil(_constraints_cleared, timeout=5000)

    data = _wait_constraints_json(
        qtbot,
        helpers,
        constraints_path,
        lambda d: not d.get("must_links") and not d.get("cannot_links") and not d.get("ignored_files"),
        timeout=5000,
    )

    assert not data.get("must_links"), "Must links should be cleared after unmarking"
    assert not data.get("cannot_links"), "Cannot links should be cleared after unmarking"
    assert not data.get("ignored_files"), "Ignored files should be cleared after unmarking"

    qtbot.waitUntil(lambda: not window.view_groups_update, timeout=1000)
    selected_set = set(selected_paths)
    assert any(selected_set == set(grp) for grp in window.view_groups), "Original group should be restored after unmarking"

# Move files from a/b to a
def test_move_files_by_dragging(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.move_files_by_dragging:
        pytest.skip()

    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=3)
    window._browser_show(str(root))
    wait_browser_list_filled(qtbot, window, root, timeout_ms=5000)

    # Enter folder a then scan
    double_click_item_by_path(window, str(a), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, a, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    # Enter folder b then scan
    double_click_item_by_path(window, str(b), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, b, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    app = QApplication.instance()
    orig_quit = app.quit
    try:
        app.quit = lambda: None
        window._btn_action_exit_and_save()
    finally:
        app.quit = orig_quit

    lw = getattr(window, "_browser_listw_ref", None)
    assert lw is not None, "browser list widget has not been created yet"

    # Source: a/b
    src_candidates = sorted(b.glob("*.png"))
    assert src_candidates, "No PNG files available in the source directory to move"
    src_files = src_candidates
    src_items = []
    for file_path in src_files:
        item = _find_item_by_abs_path(lw, file_path)
        assert item is not None, f"Unable to find source file {file_path}"
        src_items.append(item)

    multi_select_mod = Qt.ControlModifier | Qt.MetaModifier
    for idx, item in enumerate(src_items):
        rect = lw.visualItemRect(item)
        pos = rect.center()
        modifiers = multi_select_mod if idx > 0 else Qt.NoModifier
        qtbot.mouseClick(lw.viewport(), Qt.LeftButton, pos=pos, modifier=modifiers)

    selected = lw.selectedItems()
    assert {Path(it.data(Qt.UserRole)) for it in selected} == set(src_files), "Selected items do not match the source files"

    # Destination: a
    dest_dir = a
    lw.operationRequested.emit("move", [str(p) for p in src_files], str(dest_dir))

    dest_files = [dest_dir / p.name for p in src_files]

    qtbot.waitUntil(lambda: all(df.exists() for df in dest_files), timeout=10000)
    assert all(not sf.exists() for sf in src_files), "Source files should no longer exist in the original location"

    qtbot.waitUntil(lambda: all(_find_item_by_abs_path(lw, sf) is None for sf in src_files), timeout=5000)

    window._browser_show(str(dest_dir))
    wait_browser_list_filled(qtbot, window, dest_dir, timeout_ms=5000)

    lw_dest = getattr(window, "_browser_listw_ref", None)
    assert lw_dest is not None
    for df in dest_files:
        assert _find_item_by_abs_path(lw_dest, df) is not None, f"Moved file {df} is missing from the destination list"
    
    expected_after = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a/FILELIST_FILE, expected_after)
    file_assert_progress_matches(a/PROGRESS_FILE, a, expected_after)
    
    expected_after = file_build_expected_for_scan(b)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, b/FILELIST_FILE, expected_after)
    file_assert_progress_matches(b/PROGRESS_FILE, b, expected_after)

# Move files from a/b to a
def test_move_files_by_menu(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.move_files_by_menu:
        pytest.skip()

    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=3)
    window._browser_show(str(root))
    wait_browser_list_filled(qtbot, window, root, timeout_ms=5000)

    # Enter folder a then scan
    double_click_item_by_path(window, str(a), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, a, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    # Enter folder b then scan
    double_click_item_by_path(window, str(b), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, b, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    lw = getattr(window, "_browser_listw_ref", None)
    assert lw is not None, "browser list widget has not been created yet"

    png_entries = [
        (lw.item(i), Path(str(lw.item(i).data(Qt.UserRole))))
        for i in range(lw.count())
        if lw.item(i) and lw.item(i).data(Qt.UserRole)
        and str(lw.item(i).data(Qt.UserRole)).lower().endswith(".png")
    ]
    assert png_entries, "No PNG files available in the source directory to move"
    items, src_files = zip(*png_entries)
    items = list(items)
    src_files = [Path(p) for p in src_files]

    multi_select_mod = Qt.ControlModifier | Qt.MetaModifier
    for idx, it in enumerate(items):
        rect = lw.visualItemRect(it)
        pos = rect.center()
        modifiers = multi_select_mod if idx > 0 else Qt.NoModifier
        qtbot.mouseClick(lw.viewport(), Qt.LeftButton, pos=pos, modifier=modifiers)

    selected = lw.selectedItems()
    assert {Path(it.data(Qt.UserRole)) for it in selected} == set(src_files)

    dest_dir = a
    move_text = window.i18n.t("btn.browser_move_to", default="Move to…")
    orig_exec = QMenu.exec_
    orig_dialog = QFileDialog.getExistingDirectory
    orig_request = window._browser_action_move_copy_request

    def fake_exec(menu, *args, **kwargs):
        for act in menu.actions():
            if act.text() == move_text:
                return act
        return menu.actions()[-1] if menu.actions() else None

    def fake_dialog(*args, **kwargs):
        return str(dest_dir)

    def fake_request(op_hint, src_list, dst_dir):
        return orig_request("move", src_list, dst_dir)

    QMenu.exec_ = fake_exec
    QFileDialog.getExistingDirectory = fake_dialog
    window._browser_action_move_copy_request = fake_request
    try:
        pos = lw.visualItemRect(items[0]).center()
        window._browser_action_context_menu(pos)
    finally:
        QMenu.exec_ = orig_exec
        QFileDialog.getExistingDirectory = orig_dialog
        window._browser_action_move_copy_request = orig_request

    dest_files = [dest_dir / p.name for p in src_files]

    qtbot.waitUntil(lambda: all(df.exists() for df in dest_files), timeout=10000)
    assert all(not sf.exists() for sf in src_files), "Source files should have been moved away"

    window._browser_show(str(dest_dir))
    wait_browser_list_filled(qtbot, window, dest_dir, timeout_ms=5000)
    lw_dest = getattr(window, "_browser_listw_ref", None)
    assert lw_dest is not None
    for df in dest_files:
        assert _find_item_by_abs_path(lw_dest, df) is not None, f"Moved file {df} is missing from the destination list"

    window._browser_show(str(c))
    wait_browser_list_filled(qtbot, window, c, timeout_ms=5000)
    lw_src_after = getattr(window, "_browser_listw_ref", None)
    assert lw_src_after is not None
    for sf in src_files:
        assert _find_item_by_abs_path(lw_src_after, sf) is None, f"Source list still shows moved file {sf}"
    
    expected_after = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a/FILELIST_FILE, expected_after)
    file_assert_progress_matches(a/PROGRESS_FILE, a, expected_after)
    
    expected_after = file_build_expected_for_scan(b)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, b/FILELIST_FILE, expected_after)
    file_assert_progress_matches(b/PROGRESS_FILE, b, expected_after)

# Copy files from a/c to a/b
def test_copy_files_by_dragging(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.copy_files_by_dragging:
        pytest.skip()

    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=3)
    window._browser_show(str(root))
    wait_browser_list_filled(qtbot, window, root, timeout_ms=5000)

    double_click_item_by_path(window, str(a), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, a, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    double_click_item_by_path(window, str(b), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, b, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)
    click_button(window.show_browser_back_btn)

    double_click_item_by_path(window, str(c), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, c, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    lw = getattr(window, "_browser_listw_ref", None)
    assert lw is not None, "browser list widget has not been created yet"

    # Source: a/c
    png_entries = [
        (lw.item(i), Path(str(lw.item(i).data(Qt.UserRole))))
        for i in range(lw.count())
        if lw.item(i) and lw.item(i).data(Qt.UserRole)
        and str(lw.item(i).data(Qt.UserRole)).lower().endswith(".png")
    ]
    assert png_entries, "No PNG files available in the source directory to copy"
    src_items, src_files = zip(*png_entries)
    src_items = list(src_items)
    src_files = [Path(p) for p in src_files]

    multi_select_mod = Qt.ControlModifier | Qt.MetaModifier
    for idx, item in enumerate(src_items):
        rect = lw.visualItemRect(item)
        pos = rect.center()
        modifiers = multi_select_mod if idx > 0 else Qt.NoModifier
        qtbot.mouseClick(lw.viewport(), Qt.LeftButton, pos=pos, modifier=modifiers)

    selected = lw.selectedItems()
    assert {Path(it.data(Qt.UserRole)) for it in selected} == set(src_files), "Selected items do not match the source files"

    # Destination: a/b
    dest_dir = b
    lw.operationRequested.emit("copy", [str(p) for p in src_files], str(dest_dir))

    def _copies_exist():
        return all((dest_dir / p.name).exists() for p in src_files)

    qtbot.waitUntil(_copies_exist, timeout=10000)
    assert all(sf.exists() for sf in src_files), "Source files should remain after copying"

    qtbot.waitUntil(lambda: all(_find_item_by_abs_path(lw, sf) is not None for sf in src_files), timeout=5000)

    window._browser_show(str(dest_dir))
    wait_browser_list_filled(qtbot, window, dest_dir, timeout_ms=5000)

    lw_dest = getattr(window, "_browser_listw_ref", None)
    assert lw_dest is not None
    for src in src_files:
        df = dest_dir / src.name
        assert df.exists(), f"Destination is missing copied file {df}"
        assert _find_item_by_abs_path(lw_dest, df) is not None, f"Copied file {df} is missing from the destination list"

    window._browser_show(str(c))
    wait_browser_list_filled(qtbot, window, c, timeout_ms=5000)
    lw_src_final = getattr(window, "_browser_listw_ref", None)
    assert lw_src_final is not None
    for sf in src_files:
        assert _find_item_by_abs_path(lw_src_final, sf) is not None, f"Source list should retain original file {sf}"

    expected_after = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a/FILELIST_FILE, expected_after)
    file_assert_progress_matches(a/PROGRESS_FILE, a, expected_after, expected_stage="hashing")
    
    expected_after = file_build_expected_for_scan(b)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, b/FILELIST_FILE, expected_after)
    file_assert_progress_matches(b/PROGRESS_FILE, b, expected_after, expected_stage="hashing")

    expected_after = file_build_expected_for_scan(c)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, c/FILELIST_FILE, expected_after)
    file_assert_progress_matches(c/PROGRESS_FILE, c, expected_after)

# Copy files from a/c to a/b
def test_copy_files_by_menu(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.copy_files_by_menu:
        pytest.skip()

    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=3)
    window._browser_show(str(root))
    wait_browser_list_filled(qtbot, window, root, timeout_ms=5000)

    double_click_item_by_path(window, str(a), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, a, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    double_click_item_by_path(window, str(b), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, b, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)
    click_button(window.show_browser_back_btn)

    double_click_item_by_path(window, str(c), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, c, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    lw = getattr(window, "_browser_listw_ref", None)
    assert lw is not None, "browser list widget has not been created yet"

    png_entries = [
        (lw.item(i), Path(str(lw.item(i).data(Qt.UserRole))))
        for i in range(lw.count())
        if lw.item(i) and lw.item(i).data(Qt.UserRole)
        and str(lw.item(i).data(Qt.UserRole)).lower().endswith(".png")
    ]
    assert png_entries, "No PNG files available in the source directory to copy"
    src_items, src_files = zip(*png_entries)
    src_items = list(src_items)
    src_files = [Path(p) for p in src_files]

    multi_select_mod = Qt.ControlModifier | Qt.MetaModifier
    for idx, item in enumerate(src_items):
        rect = lw.visualItemRect(item)
        pos = rect.center()
        modifiers = multi_select_mod if idx > 0 else Qt.NoModifier
        qtbot.mouseClick(lw.viewport(), Qt.LeftButton, pos=pos, modifier=modifiers)

    selected = lw.selectedItems()
    assert {Path(it.data(Qt.UserRole)) for it in selected} == set(src_files), "Selected items do not match the source files"

    dest_dir = b
    copy_text = window.i18n.t("btn.browser_copy_to", default="Copy to…")
    orig_exec = QMenu.exec_
    orig_dialog = QFileDialog.getExistingDirectory

    def fake_exec(menu, *args, **kwargs):
        for act in menu.actions():
            if act.text() == copy_text:
                return act
        return menu.actions()[-1] if menu.actions() else None

    def fake_dialog(*args, **kwargs):
        return str(dest_dir)

    QMenu.exec_ = fake_exec
    QFileDialog.getExistingDirectory = fake_dialog
    try:
        pos = lw.visualItemRect(src_items[0]).center()
        window._browser_action_context_menu(pos)
    finally:
        QMenu.exec_ = orig_exec
        QFileDialog.getExistingDirectory = orig_dialog

    def _copies_exist():
        return all((dest_dir / p.name).exists() for p in src_files)

    qtbot.waitUntil(_copies_exist, timeout=10000)
    assert all(sf.exists() for sf in src_files), "Source files should remain after copying"

    qtbot.waitUntil(lambda: all(_find_item_by_abs_path(lw, sf) is not None for sf in src_files), timeout=5000)

    window._browser_show(str(dest_dir))
    wait_browser_list_filled(qtbot, window, dest_dir, timeout_ms=5000)

    lw_dest = getattr(window, "_browser_listw_ref", None)
    assert lw_dest is not None
    for src in src_files:
        df = dest_dir / src.name
        assert df.exists(), f"Destination is missing copied file {df}"
        assert _find_item_by_abs_path(lw_dest, df) is not None, f"Copied file {df} is missing from the destination list"

    window._browser_show(str(c))
    wait_browser_list_filled(qtbot, window, c, timeout_ms=5000)
    lw_src_final = getattr(window, "_browser_listw_ref", None)
    assert lw_src_final is not None
    for sf in src_files:
        assert _find_item_by_abs_path(lw_src_final, sf) is not None, f"Source list should retain original file {sf}"
    
    expected_after = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a/FILELIST_FILE, expected_after)
    file_assert_progress_matches(a/PROGRESS_FILE, a, expected_after, expected_stage="hashing")
    
    expected_after = file_build_expected_for_scan(b)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, b/FILELIST_FILE, expected_after)
    file_assert_progress_matches(b/PROGRESS_FILE, b, expected_after, expected_stage="hashing")

    expected_after = file_build_expected_for_scan(c)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, c/FILELIST_FILE, expected_after)
    file_assert_progress_matches(c/PROGRESS_FILE, c, expected_after)

def test_rename_file_by_menu(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.rename_file_by_menu:
        pytest.skip()

    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=3)
    window._browser_show(str(root))
    wait_browser_list_filled(qtbot, window, root, timeout_ms=5000)

    double_click_item_by_path(window, str(a), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, a, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    double_click_item_by_path(window, str(b), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, b, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    lw = getattr(window, "_browser_listw_ref", None)
    assert lw is not None, "browser list widget has not been created yet"

    src_file = next(iter(sorted(b.glob("*.png"))), None)
    assert src_file is not None, "No PNG files available in the source directory to rename"

    item = _find_item_by_abs_path(lw, src_file)
    assert item is not None, f"Unable to find source file {src_file}"

    rect = lw.visualItemRect(item)
    pos = rect.center()
    qtbot.mouseClick(lw.viewport(), Qt.LeftButton, pos=pos)
    assert len(lw.selectedItems()) == 1

    new_name = f"{src_file.stem}_renamed{src_file.suffix}"
    counter = 1
    while (src_file.parent / new_name).exists():
        new_name = f"{src_file.stem}_renamed_{counter}{src_file.suffix}"
        counter += 1
    new_path = src_file.parent / new_name

    rename_text = window.i18n.t("btn.browser_rename", default="Rename…")

    orig_input = window._popup_input
    orig_exec = QMenu.exec_

    def fake_popup_input(title, label, default_text=""):
        return new_name, True

    def fake_exec(menu, *args, **kwargs):
        for act in menu.actions():
            if act.text() == rename_text:
                return act
        return menu.actions()[0] if menu.actions() else None

    window._popup_input = fake_popup_input
    QMenu.exec_ = fake_exec
    try:
        window._browser_action_context_menu(pos)
    finally:
        QMenu.exec_ = orig_exec
        window._popup_input = orig_input

    qtbot.waitUntil(lambda: new_path.exists(), timeout=5000)
    assert not src_file.exists(), "Old file still exists; rename failed"

    wait_browser_list_filled(qtbot, window, b, timeout_ms=5000)
    lw_after = getattr(window, "_browser_listw_ref", None)
    assert lw_after is not None
    assert _find_item_by_abs_path(lw_after, new_path) is not None, "Renamed file did not appear in the list"
    assert _find_item_by_abs_path(lw_after, src_file) is None, "List still shows the old file name"

    expected_after = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a/FILELIST_FILE, expected_after)
    file_assert_progress_matches(a/PROGRESS_FILE, a, expected_after)
    
    expected_after = file_build_expected_for_scan(b)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, b/FILELIST_FILE, expected_after)
    file_assert_progress_matches(b/PROGRESS_FILE, b, expected_after)


def test_delete_files_by_menu(qtbot, window, tmp_path, helpers):
    if not FUNC_TEST.delete_files_by_menu:
        pytest.skip()

    root, a, b, c, d, e, f, g, h = helpers.file_gen_images(tmp_path, helpers, number=3)
    window._browser_show(str(root))
    wait_browser_list_filled(qtbot, window, root, timeout_ms=5000)

    double_click_item_by_path(window, str(a), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, a, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    double_click_item_by_path(window, str(b), qtbot=qtbot)
    wait_browser_list_filled(qtbot, window, b, timeout_ms=5000)
    click_button(window.scan_btn)
    wait_done(qtbot, window)
    click_button(window.show_browser_back_btn)

    lw = getattr(window, "_browser_listw_ref", None)
    assert lw is not None, "browser list widget has not been created yet"

    window.confirm_delete = False

    b_abs = os.path.abspath(str(b))
    targets = []
    for i in range(lw.count()):
        it = lw.item(i)
        data = it.data(Qt.UserRole) if it else None
        if not data:
            continue
        data_abs = os.path.abspath(str(data))
        # Skip the parent folder or the folder itself
        if data_abs == b_abs:
            continue
        if os.path.commonpath([data_abs, b_abs]) != b_abs:
            continue
        targets.append((it, Path(data_abs)))

    assert targets, "No entries found for deletion"

    lw.clearSelection()
    for it, _ in targets:
        it.setSelected(True)
    selected_paths = [path for _, path in targets]
    assert set(Path(it.data(Qt.UserRole)) for it in lw.selectedItems()) == set(selected_paths)

    delete_text = window.i18n.t("btn.browser_delete", default="Delete")
    orig_exec = QMenu.exec_

    def fake_exec(menu, *args, **kwargs):
        for act in menu.actions():
            if act.text() == delete_text:
                return act
        return menu.actions()[-1] if menu.actions() else None

    QMenu.exec_ = fake_exec
    try:
        first_item = targets[0][0]
        pos = lw.visualItemRect(first_item).center()
        window._browser_action_context_menu(pos)
    finally:
        QMenu.exec_ = orig_exec

    def _deleted():
        return all(not p.exists() for p in selected_paths)

    qtbot.waitUntil(_deleted, timeout=10000)
    assert b.exists(), "The destination folder itself should not be deleted"

    wait_browser_list_filled(qtbot, window, b, timeout_ms=5000)
    lw_after = getattr(window, "_browser_listw_ref", None)
    assert lw_after is not None
    for path in selected_paths:
        assert _find_item_by_abs_path(lw_after, path) is None, f"Deleted entry {path} still appears in the list"

    expected_after = file_build_expected_for_scan(a)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, a/FILELIST_FILE, expected_after)
    file_assert_progress_matches(a/PROGRESS_FILE, a, expected_after)
    
    expected_after = file_build_expected_for_scan(b)
    file_assert_filelist_matches(qtbot, window, tmp_path, helpers, b/FILELIST_FILE, expected_after)
    file_assert_progress_matches(b/PROGRESS_FILE, b, expected_after)
