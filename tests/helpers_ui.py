# tests/helpers_ui.py
import os, time
from PyQt5.QtCore import Qt, QEventLoop, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from pathlib import Path

def _absnorm(p):  # 你原本已經有的
    return os.path.abspath(os.path.normpath(p)) if p else ""

def _current_lw(window):
    lw = getattr(window, "_browser_listw_ref", None)
    assert lw is not None, "browser list widget 尚未建立"
    return lw

def wait_browser_list_filled(qtbot, window, expected_parent, timeout_ms=10000):
    exp = _absnorm(expected_parent)
    def _ready():
        lw = getattr(window, "_browser_listw_ref", None)
        if lw is None:
            return False
        # 至少有 ".." 或一筆子項目就算建好
        return _absnorm(getattr(window, "browser_folder", "")) == exp and lw.count() >= 1
    qtbot.waitUntil(_ready, timeout=timeout_ms)

def _process_events(ms=0):
    # 允許 event loop 處理當前排隊事件
    QApplication.processEvents()
    if ms:
        # 小小暫停可讓 Qt 完整繪製/派發
        time.sleep(ms/1000.0)
        QApplication.processEvents()

def double_click_item_by_path(window, target_path: str, qtbot=None, timeout=10000):
    """
    更穩定的雙擊導航：
    1) 切到父層並等待清單建好
    2) 找 item → 先 setCurrentItem
    3) 模擬雙擊；若 300ms 內沒變化，後援呼叫 _browser_action_click(item)
    4) 等待 browser_folder == 目標
    """
    target_path_n = _absnorm(target_path)
    parent = _absnorm(os.path.dirname(target_path_n))

    # 1) 先讓視圖真的切到父層並建好清單
    window._browser_show(parent)
    if qtbot:
        wait_browser_list_filled(qtbot, window, parent, timeout_ms=timeout)
    _process_events(10)

    lw = _current_lw(window)

    # 2) 找到對應項目
    target_item = None
    for i in range(lw.count()):
        it = lw.item(i)
        p = it.data(Qt.UserRole)
        if p and _absnorm(p) == target_path_n:
            target_item = it
            break
    if not target_item:
        items = []
        for i in range(lw.count()):
            it = lw.item(i); p = it.data(Qt.UserRole)
            items.append(_absnorm(p) if p else "<None>")
        raise AssertionError(
            "清單中找不到項目:\n"
            f"  目標: {target_path_n}\n"
            f"  目前 browser_folder: {getattr(window,'browser_folder',None)}\n"
            "  現有項目:\n    " + "\n    ".join(items)
        )

    # 先選中，確保 currentItem 正確
    lw.setCurrentItem(target_item)
    _process_events(10)

    # 3) 模擬雙擊
    rect = lw.visualItemRect(target_item)
    pos = rect.center()
    if qtbot:
        qtbot.mouseDClick(lw.viewport(), Qt.LeftButton, pos=pos)
    _process_events(50)

    # 若雙擊後 300ms 仍未導航，後援用你的邏輯直接觸發
    start = time.time()
    def _navigated():
        return _absnorm(getattr(window, "browser_folder","")) == target_path_n
    while time.time() - start < 0.3 and not _navigated():
        _process_events(20)

    if not _navigated():
        # 後援：直接呼叫你的點擊處理器
        if hasattr(window, "_browser_action_click"):
            window._browser_action_click(target_item)
            _process_events(20)

    # 4) 最終等待導航完成
    if qtbot:
        qtbot.waitUntil(_navigated, timeout=timeout)
    else:
        # 沒有 qtbot 時做簡單等候
        end = time.time() + (timeout/1000.0)
        while time.time() < end and not _navigated():
            _process_events(20)
        assert _navigated(), "導航超時"

def click_button(btn):
    QTest.mouseClick(btn, Qt.LeftButton)

# 放在檔案尾端或合適處
def open_overview_group_by_index(window, qtbot, index=0, timeout=5000):
    """
    在 overview 畫面中，穩定地打開第 index 個群組。
    步驟：
      - setCurrentItem
      - mouse double click
      - 超時則 emit itemActivated 作為後援
      - 再超時可選擇直接呼叫你的 open-group handler（若你有提供）
    """
    from PyQt5.QtWidgets import QApplication

    listw = getattr(window, "_ovw_listw", None)
    assert listw is not None and listw.count() > 0, "_ovw_listw 尚未建立或沒有項目"

    item = listw.item(index)
    assert item is not None, f"overview 第 {index} 個 item 不存在"

    # 先設為 current，確保你的處理邏輯（若依賴 currentItem）能拿到
    listw.setCurrentItem(item)
    QApplication.processEvents()

    # 嘗試模擬雙擊
    rect = listw.visualItemRect(item)
    pos = rect.center()
    qtbot.mouseDClick(listw.viewport(), Qt.LeftButton, pos=pos)
    QApplication.processEvents()

    def _entered_group():
        return getattr(window, "action", None) == "show_group"

    try:
        qtbot.waitUntil(_entered_group, timeout=timeout)
        return  # 成功進群組
    except Exception:
        pass  # 改用後援

    # 後援 1：直接發出 itemActivated
    try:
        listw.itemActivated.emit(item)
        QApplication.processEvents()
        qtbot.waitUntil(_entered_group, timeout=timeout // 2)
        return
    except Exception:
        pass

    # 後援 2（可選）：如果你的類別有公開的 open-handler，直接呼叫
    # 名稱視你的實作而定，以下示意幾個常見命名，存在就用其中之一。
    for fn_name in ("_overview_action_open_group",
                    "_ovw_action_open_group",
                    "_overview_open_group"):
        if hasattr(window, fn_name):
            getattr(window, fn_name)(item)
            QApplication.processEvents()
            qtbot.waitUntil(_entered_group, timeout=timeout // 2)
            return

    # 都失敗就拋錯，協助除錯
    raise AssertionError("雙擊/emit/handler 後仍無法進入群組（window.action 未變為 'show_group'）")