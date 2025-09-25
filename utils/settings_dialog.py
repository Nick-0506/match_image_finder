# settings_dialog.py
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSlider, QCheckBox,
    QLineEdit, QDialogButtonBox, QWidget, QFormLayout, QMessageBox, QSpinBox, QToolButton
)
from PyQt5.QtGui import QPainter, QPen, QFontMetrics, QFont
from PyQt5.QtCore import Qt, pyqtSignal, QRect

class LabeledLine(QWidget):
    def __init__(self, label="UI", line_height=2, size=None, gap=12, parent=None):
        super().__init__(parent)
        self._label = label
        self._line_height = line_height  # Line height
        self._font_size = size
        self._gap = gap                  # Gap between text with line
        self.setMinimumHeight(30)

    def setLabel(self, text):
        self._label = text
        self.update()

    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Font size
        font = self.font()
        if self._font_size is not None:
            font.setPointSize(self._font_size)
        painter.setFont(font)

        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(self._label)
        text_h = fm.height()

        center_y = h // 2

        # Put text in center
        text_x = (w - text_w) // 2
        text_rect = QRect(text_x, center_y - text_h//2, text_w, text_h)

        # Style of line
        pen = QPen()
        pen.setWidth(self._line_height)
        pen.setColor(self.palette().color(self.foregroundRole()))  # Use widget color
        painter.setPen(pen)

        left_x1 = 8  # Left margin
        left_x2 = text_rect.left() - self._gap

        right_x1 = text_rect.right() + self._gap
        right_x2 = w - 8  # Right margin

        # Line of left
        if left_x2 > left_x1:
            painter.drawLine(left_x1, center_y, left_x2, center_y)
        # Line of right
        if right_x2 > right_x1:
            painter.drawLine(right_x1, center_y, right_x2, center_y)

        # Text
        painter.drawText(text_rect, Qt.AlignCenter, self._label)

        painter.end()

    def setLabel(self, text):
        self._label = text
        self.update()

    def setFontSize(self, size):
        self._font_size = size
        self.update()

class SettingsDialog(QDialog):
    # Emits the list of changed configuration keys so the app can hot‑apply changes when the user presses Apply or OK.
    settings_applied = pyqtSignal(list)

    def __init__(self, cfg, i18n, binder, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.i18n = i18n
        self.binder = binder

        self._build_ui()
        self._load_from_config()

    # -------- UI --------
    def _build_ui(self):
        v = QVBoxLayout(self)

        # Title
        self.binder.bind(self, "setWindowTitle", "dlg.settings.title")

        form = QFormLayout()
        
        self.line_ui_text = LabeledLine(self.i18n.t("line.dlg.settings.ui.text"), line_height=1, gap=10)
        form.addRow(self.line_ui_text)

        # Theme
        #self.theme = QComboBox()
        #self.theme.addItems(["system", "light", "dark"])
        #self.lbl_theme = QLabel()
        #self.binder.bind(self.lbl_theme, "setText", "dlg.settings.theme")
        #form.addRow(self.lbl_theme, self.theme)
        
        # Language (Auto + dynamically scan i18n files)
        self.lang = QComboBox()
        langs = [("auto", self.i18n.t("lang.auto"))] + self.i18n.available_locales()  # [(code,name),...]
        for code, name in langs:
            self.lang.addItem(name, code)
        self.lbl_lang = QLabel()
        self.binder.bind(self.lbl_lang, "setText", "dlg.settings.lang")
        form.addRow(self.lbl_lang, self.lang)

        # Font size
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 28)
        self.font_size_spin.setSingleStep(1)
        self.lbl_font = QLabel()
        self.binder.bind(self.lbl_font, "setText", "dlg.settings.font_size")
        form.addRow(self.lbl_font, self.font_size_spin)

        self.line_ui_browser = LabeledLine(self.i18n.t("line.dlg.settings.ui.browser"), line_height=1, gap=10)
        form.addRow(self.line_ui_browser)

        # Browser UI
        browser_info_row = QWidget()
        browser_info_h = QHBoxLayout(browser_info_row)

        self.browser_lbl_type = QLabel()
        self.binder.bind(self.browser_lbl_type, "setText", "dlg.settings.browser_type_lbl")
        browser_info_h.addWidget(self.browser_lbl_type)
        
        self.browser_view_style_lbl = QLabel(self.i18n.t("label.browser_view_style", default="View Style:"))
        self.browser_view_style_combo = QComboBox()
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.list", default="List View"), "list")
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.small", default="Small Icons"), "small")
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.medium", default="Medium Icons"), "medium")
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.large", default="Large Icons"), "large")
        self.browser_view_style_combo.addItem(self.i18n.t("browser_view_style.huge", default="Huge Icons"), "huge")
        browser_info_h.addWidget(self.browser_view_style_lbl)
        browser_info_h.addWidget(self.browser_view_style_combo)

        self.browser_sort_lbl = QLabel(self.i18n.t("label.browser_sort", default="Sort:"))
        self.browser_sort_combo = QComboBox()
        self.browser_sort_combo.addItem(self.i18n.t("browser_sort.name",  default="Name"), "name")
        self.browser_sort_combo.addItem(self.i18n.t("browser_sort.mtime", default="Modified Time"), "mtime")
        self.browser_sort_combo.addItem(self.i18n.t("browser_sort.type",  default="Type"), "type")
        self.browser_order_btn = QToolButton()
        self.browser_order_btn.clicked.connect(self._btn_action_order)

        browser_info_h.addWidget(self.browser_sort_lbl)
        browser_info_h.addWidget(self.browser_sort_combo)
        browser_info_h.addWidget(self.browser_order_btn)
        form.addRow(browser_info_row)

        # Show processing image
        self.cb_show_processing_image = QCheckBox()
        self.binder.bind(self.cb_show_processing_image, "setText", "dlg.settings.show_processing_image")
        self.lbl_show_processing_image = QLabel()
        self.binder.bind(self.lbl_show_processing_image, "setText", "dlg.settings.show_processing_image_desc")
        form.addRow(self.lbl_show_processing_image, self.cb_show_processing_image)

        self.line_ui_overview = LabeledLine(self.i18n.t("line.dlg.settings.ui.overview"), line_height=1, gap=10)
        form.addRow(self.line_ui_overview)

        # Overview Thumbnail size
        overview_thumb_row = QWidget()
        overview_thumb_h = QHBoxLayout(overview_thumb_row)
        self.overview_thumb_slider = QSlider(Qt.Horizontal)
        self.overview_thumb_slider.setMinimum(120)
        self.overview_thumb_slider.setMaximum(320)
        self.overview_thumb_slider.setSingleStep(10)
        self.overview_thumb_value_label = QLabel("--")
        overview_thumb_h.addWidget(self.overview_thumb_slider, 1)
        overview_thumb_h.addWidget(self.overview_thumb_value_label)
        self.overview_lbl_thumb = QLabel()
        self.binder.bind(self.overview_lbl_thumb, "setText", "dlg.settings.overview_thumb_size")
        form.addRow(self.overview_lbl_thumb, overview_thumb_row)

        # Show original group
        self.cb_show_original_group = QCheckBox()
        self.binder.bind(self.cb_show_original_group, "setText", "dlg.settings.show_original_group")
        self.lbl_show_original_group = QLabel()
        self.binder.bind(self.lbl_show_original_group, "setText", "dlg.settings.show_original_group_desc")
        form.addRow(self.lbl_show_original_group, self.cb_show_original_group)

        self.line_ui_groups = LabeledLine(self.i18n.t("line.dlg.settings.ui.groups"), line_height=1, gap=10)
        form.addRow(self.line_ui_groups)

        # Group Thumbnail size
        thumb_row = QWidget()
        thumb_h = QHBoxLayout(thumb_row)
        self.thumb_slider = QSlider(Qt.Horizontal)
        self.thumb_slider.setMinimum(400)
        self.thumb_slider.setMaximum(1000)
        self.thumb_slider.setSingleStep(10)
        self.thumb_value_label = QLabel("--")
        thumb_h.addWidget(self.thumb_slider, 1)
        thumb_h.addWidget(self.thumb_value_label)
        self.lbl_thumb = QLabel()
        self.binder.bind(self.lbl_thumb, "setText", "dlg.settings.thumb_size")
        form.addRow(self.lbl_thumb, thumb_row)

        self.line_behavior_compare = LabeledLine(self.i18n.t("line.dlg.settings.behavior.compare"), line_height=1, gap=10)
        form.addRow(self.line_behavior_compare)
        
        # Similarity Tolerance
        similarity_tolerance_row = QWidget()
        similarity_tolerance_h = QHBoxLayout(similarity_tolerance_row)
        self.similarity_tolerance_slider = QSlider(Qt.Horizontal)
        self.similarity_tolerance_slider.setMinimum(0)
        self.similarity_tolerance_slider.setMaximum(15)
        self.similarity_tolerance_slider.setSingleStep(1)
        self.similarity_tolerance_value_label = QLabel("--")
        similarity_tolerance_h.addWidget(self.similarity_tolerance_slider, 1)
        similarity_tolerance_h.addWidget(self.similarity_tolerance_value_label)
        self.lbl_similarity_tolerance = QLabel()
        self.binder.bind(self.lbl_similarity_tolerance, "setText", "dlg.settings.similarity_tolerance")
        form.addRow(self.lbl_similarity_tolerance, similarity_tolerance_row)

        # Compare file size
        self.cb_compare_file_size = QCheckBox()
        self.binder.bind(self.cb_compare_file_size, "setText", "dlg.settings.compare_file_size")
        self.lbl_compare_file_size = QLabel()
        self.binder.bind(self.lbl_compare_file_size, "setText", "dlg.settings.compare_file_size_desc")
        form.addRow(self.lbl_compare_file_size, self.cb_compare_file_size)

        # Auto next group
        self.cb_auto_next_group = QCheckBox()
        self.binder.bind(self.cb_auto_next_group, "setText", "dlg.settings.auto_next_group")
        self.lbl_auto_net_group = QLabel()
        self.binder.bind(self.lbl_auto_net_group, "setText", "dlg.settings.auto_next_group_desc")
        form.addRow(self.lbl_auto_net_group, self.cb_auto_next_group)

        self.line_behavior_files = LabeledLine(self.i18n.t("line.dlg.settings.behavior.files"), line_height=1, gap=10)
        form.addRow(self.line_behavior_files)

        # Confirm delete
        self.cb_confirm_delete = QCheckBox()
        self.binder.bind(self.cb_confirm_delete, "setText", "dlg.settings.confirm_delete")
        self.lbl_confirm_delete = QLabel()
        self.binder.bind(self.lbl_confirm_delete, "setText", "dlg.settings.confirm_delete_desc")
        form.addRow(self.lbl_confirm_delete, self.cb_confirm_delete)

        # Check same images first
        self.cb_display_same_images = QCheckBox()
        self.binder.bind(self.cb_display_same_images, "setText", "dlg.settings.display_same_images")
        self.lbl_display_same_images = QLabel()
        self.binder.bind(self.lbl_display_same_images, "setText", "dlg.settings.display_same_images_desc")
        form.addRow(self.lbl_display_same_images, self.cb_display_same_images)

        # Delete directly or to trash
        #self.delete_to_trash = QCheckBox()
        #self.binder.bind(self.delete_to_trash, "setText", "dlg.settings.delete_to_trash")
        #self.lbl_deletion = QLabel()
        #self.binder.bind(self.lbl_deletion, "setText", "dlg.settings.deletion")
        #form.addRow(self.lbl_deletion, self.delete_to_trash)

        v.addLayout(form)

        # Buttons
        self.btns = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        v.addWidget(self.btns)

        # Bind i18n
        self.btns.button(QDialogButtonBox.Apply).setText(self.i18n.t("dlg.settings.apply"))
        self.btns.button(QDialogButtonBox.Ok).setText(self.i18n.t("dlg.settings.ok"))
        self.btns.button(QDialogButtonBox.Cancel).setText(self.i18n.t("dlg.settings.cancel"))
        
        self.binder.bind(self.browser_view_style_lbl,"setText","label.browser_view_style")
        self.binder.bind(self.browser_view_style_combo,("setItemText",0),"browser_view_style.list")
        self.binder.bind(self.browser_view_style_combo,("setItemText",1),"browser_view_style.small")
        self.binder.bind(self.browser_view_style_combo,("setItemText",2),"browser_view_style.medium")
        self.binder.bind(self.browser_view_style_combo,("setItemText",3),"browser_view_style.large")
        self.binder.bind(self.browser_view_style_combo,("setItemText",4),"browser_view_style.huge")

        self.binder.bind(self.browser_sort_lbl,"setText","label.browser_sort")
        self.binder.bind(self.browser_sort_combo,("setItemText",0),"browser_sort.name")
        self.binder.bind(self.browser_sort_combo,("setItemText",1),"browser_sort.mtime")
        self.binder.bind(self.browser_sort_combo,("setItemText",2),"browser_sort.type")

        # Bind event
        self.btns.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        self.btns.accepted.connect(self._ok)
        self.btns.rejected.connect(self.reject)

        # Update button texts when the language changes
        self.i18n.changed.connect(lambda: self.btns.button(QDialogButtonBox.Apply).setText(self.i18n.t("dlg.settings.apply")))
        self.i18n.changed.connect(lambda: self.btns.button(QDialogButtonBox.Ok).setText(self.i18n.t("dlg.settings.ok")))
        self.i18n.changed.connect(lambda: self.btns.button(QDialogButtonBox.Cancel).setText(self.i18n.t("dlg.settings.cancel")))
        self.i18n.changed.connect(lambda: self.line_ui_text.setLabel(self.i18n.t("line.dlg.settings.ui.text", default="Text Settings")))
        self.i18n.changed.connect(lambda: self.line_ui_browser.setLabel(self.i18n.t("line.dlg.settings.ui.browser", default="Browser Settings")))
        self.i18n.changed.connect(lambda: self.line_ui_overview.setLabel(self.i18n.t("line.dlg.settings.ui.overview", default="Overview Settings")))
        self.i18n.changed.connect(lambda: self.line_ui_groups.setLabel(self.i18n.t("line.dlg.settings.ui.groups", default="Groups Settings")))
        self.i18n.changed.connect(lambda: self.line_behavior_compare.setLabel(self.i18n.t("line.dlg.settings.behavior.compare", default="Compare Settings")))
        self.i18n.changed.connect(lambda: self.line_behavior_files.setLabel(self.i18n.t("line.dlg.settings.behavior.files", default="Files Operation Settings")))

        # Update the overview thumb value label
        self.overview_thumb_slider.valueChanged.connect(
            lambda val: self.overview_thumb_value_label.setText(str(val))
        )

        # Update the group thumb value label
        self.thumb_slider.valueChanged.connect(
            lambda val: self.thumb_value_label.setText(str(val))
        )

        # Update the similarity tolerance value label
        self.similarity_tolerance_slider.valueChanged.connect(
            lambda val: self.similarity_tolerance_value_label.setText(str(val))
        )
    # -------- data flow --------
    def _load_from_config(self):
        #self.theme.setCurrentText(self.cfg.get("ui.theme", "system"))

        # Language: Align by code
        want = self.cfg.get("ui.lang", "en-US")
        for i in range(self.lang.count()):
            if self.lang.itemData(i) == want:
                self.lang.setCurrentIndex(i)
                break

        idx = self.browser_view_style_combo.findData(self.cfg.get("ui.browser_view_style_key","medium"))
        self.browser_view_style_combo.setCurrentIndex(idx)

        idx = self.browser_sort_combo.findData(self.cfg.get("ui.browser_sort_key","name"))
        self.browser_sort_combo.setCurrentIndex(idx)

        self.browser_order_btn.setText("a->z" if self.cfg.get("ui.browser_order_asc",True) else "z->a")        

        size = int(self.cfg.get("ui.overview_thumbnail.max_size", 240))
        self.overview_thumb_slider.setValue(size)
        self.overview_thumb_value_label.setText(str(size))

        size = int(self.cfg.get("ui.thumbnail.max_size", 400))
        self.thumb_slider.setValue(size)
        self.thumb_value_label.setText(str(size))
        self.cb_show_processing_image.setChecked(self.cfg.get("ui.show_processing_image",False))
        self.cb_show_original_group.setChecked(self.cfg.get("ui.show_original_groups",False))

        tolerance = int(self.cfg.get("behavior.similarity_tolerance", 10))
        self.similarity_tolerance_slider.setValue(tolerance)
        self.similarity_tolerance_value_label.setText(str(tolerance))
        self.cb_auto_next_group.setChecked(self.cfg.get("behavior.auto_next_group",True))
        self.cb_display_same_images.setChecked(self.cfg.get("behavior.display_same_images",True))
        self.cb_confirm_delete.setChecked(bool(self.cfg.get("behavior.confirm_delete", True)))
        self.cb_compare_file_size.setChecked(bool(self.cfg.get("behavior.compare_file_size", True)))
        #self.delete_to_trash.setChecked(bool(self.cfg.get("behavior.delete_to_trash", True)))
        self.font_size_spin.setValue(int(self.cfg.get("ui.font_size", 12)))

    def _collect_changes(self):
        # Compare the current UI values against the config and return ({key_path: value}, changed_keys list)
        desired = {
            #"ui.theme": self.theme.currentText(),
            "ui.lang": self.lang.itemData(self.lang.currentIndex()),  # use the locale code, not the display text.
            "ui.browser_view_style_key": self.browser_view_style_combo.currentData(),
            "ui.browser_sort_key": self.browser_sort_combo.currentData(),
            "ui.browser_order_asc": True if self.browser_order_btn.text()=="a->z" else False,
            "ui.show_processing_image": self.cb_show_processing_image.isChecked(),
            "ui.show_original_groups": self.cb_show_original_group.isChecked(),
            "ui.font_size": int(self.font_size_spin.value()),
            "ui.overview_thumbnail.max_size": int(self.overview_thumb_slider.value()),
            "ui.thumbnail.max_size": int(self.thumb_slider.value()),
            "behavior.auto_next_group": self.cb_auto_next_group.isChecked(),
            "behavior.display_same_images": self.cb_display_same_images.isChecked(),
            "behavior.similarity_tolerance": int(self.similarity_tolerance_slider.value()),
            "behavior.confirm_delete": bool(self.cb_confirm_delete.isChecked()),
            "behavior.compare_file_size": bool(self.cb_compare_file_size.isChecked()),
            #"behavior.delete_to_trash": bool(self.delete_to_trash.isChecked()),
        }

        changed, changed_keys = {}, []
        for k, v in desired.items():
            if self.cfg.get(k) != v:
                changed[k] = v
                changed_keys.append(k)
        return changed, changed_keys

    def _apply(self):
        changed, keys = self._collect_changes()
        if not keys:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle(self.i18n.t("dlg.settings.title"))
            box.setText(self.i18n.t("dlg.settings.no_changes"))
            box.setStandardButtons(QMessageBox.Ok)
            box.button(QMessageBox.Ok).setText(self.i18n.t("btn.ok"))
            box.exec_()
            return

        # Save configuration changes
        for k, v in changed.items():
            self.cfg.set(k, v, autosave=False)
        self.cfg.save()
        self.settings_applied.emit(keys)

        # Show success message
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(self.i18n.t("dlg.settings.title"))
        box.setText(self.i18n.t("dlg.settings.applied"))
        box.setStandardButtons(QMessageBox.Ok)
        box.button(QMessageBox.Ok).setText(self.i18n.t("btn.ok"))
        box.exec_()

    def _btn_action_order(self):
        if self.browser_order_btn.text() == "a->z":
            self.browser_order_btn.setText("z->a")
        else:
            self.browser_order_btn.setText("a->z")
    
    def _ok(self):
        changed, keys = self._collect_changes()
        if keys:
            self._apply()
        self.accept()