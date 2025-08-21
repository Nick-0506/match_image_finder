# settings_dialog.py
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSlider, QCheckBox,
    QLineEdit, QDialogButtonBox, QWidget, QFormLayout, QMessageBox, QSpinBox
)
from PyQt5.QtCore import Qt, pyqtSignal

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

        # Thumbnail size
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

        # Confirm delete
        self.cb_confirm_delete = QCheckBox()
        self.binder.bind(self.cb_confirm_delete, "setText", "dlg.settings.confirm_delete")
        self.lbl_confirm_delete = QLabel()
        self.binder.bind(self.lbl_confirm_delete, "setText", "dlg.settings.confirm_delete_desc")
        form.addRow(self.lbl_confirm_delete, self.cb_confirm_delete)

        # Compare file size
        self.cb_compare_file_size = QCheckBox()
        self.binder.bind(self.cb_compare_file_size, "setText", "dlg.settings.compare_file_size")
        self.lbl_compare_file_size = QLabel()
        self.binder.bind(self.lbl_compare_file_size, "setText", "dlg.settings.compare_file_size_desc")
        form.addRow(self.lbl_compare_file_size, self.cb_compare_file_size)

        # Delete directly or to trash
        #self.delete_to_trash = QCheckBox()
        #self.binder.bind(self.delete_to_trash, "setText", "dlg.settings.delete_to_trash")
        #self.lbl_deletion = QLabel()
        #self.binder.bind(self.lbl_deletion, "setText", "dlg.settings.deletion")
        #form.addRow(self.lbl_deletion, self.delete_to_trash)

        # Auto to next group
        #self.auto_next = QCheckBox()
        #self.binder.bind(self.auto_next, "setText", "dlg.settings.auto_next")
        #self.lbl_nav = QLabel()
        #self.binder.bind(self.lbl_nav, "setText", "dlg.settings.navigation")
        #form.addRow(self.lbl_nav, self.auto_next)

        # Exclude folder
        #self.exclude_dirs = QLineEdit()
        #self.exclude_dirs.setPlaceholderText(self.i18n.t("dlg.settings.exclude.placeholder"))
        #self.i18n.changed.connect(
        #    lambda: self.exclude_dirs.setPlaceholderText(self.i18n.t("dlg.settings.exclude.placeholder"))
        #)
        #self.lbl_exclude = QLabel()
        #self.binder.bind(self.lbl_exclude, "setText", "dlg.settings.exclude")
        #form.addRow(self.lbl_exclude, self.exclude_dirs)

        # Font size
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 28)
        self.font_size_spin.setSingleStep(1)
        self.lbl_font = QLabel()
        self.binder.bind(self.lbl_font, "setText", "dlg.settings.font_size")
        form.addRow(self.lbl_font, self.font_size_spin)

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

        # Bind event
        self.btns.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        self.btns.accepted.connect(self._ok)
        self.btns.rejected.connect(self.reject)

        # Update button texts when the language changes
        self.i18n.changed.connect(lambda: self.btns.button(QDialogButtonBox.Apply).setText(self.i18n.t("dlg.settings.apply")))
        self.i18n.changed.connect(lambda: self.btns.button(QDialogButtonBox.Ok).setText(self.i18n.t("dlg.settings.ok")))
        self.i18n.changed.connect(lambda: self.btns.button(QDialogButtonBox.Cancel).setText(self.i18n.t("dlg.settings.cancel")))

        # Update the thumb value label
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

        size = int(self.cfg.get("ui.thumbnail.max_size", 220))
        self.thumb_slider.setValue(size)
        self.thumb_value_label.setText(str(size))

        tolerance = int(self.cfg.get("behavior.similarity_tolerance", 10))
        self.similarity_tolerance_slider.setValue(tolerance)
        self.similarity_tolerance_value_label.setText(str(tolerance))

        self.cb_confirm_delete.setChecked(bool(self.cfg.get("behavior.confirm_delete", True)))
        self.cb_compare_file_size.setChecked(bool(self.cfg.get("behavior.compare_file_size", True)))
        #self.delete_to_trash.setChecked(bool(self.cfg.get("behavior.delete_to_trash", True)))
        #self.auto_next.setChecked(bool(self.cfg.get("ui.auto_next_group", True)))
        #self.exclude_dirs.setText(self.cfg.get("behavior.exclude_dirs", ""))

        self.font_size_spin.setValue(int(self.cfg.get("ui.font_size", 12)))

    def _collect_changes(self):
        # Compare the current UI values against the config and return ({key_path: value}, changed_keys list)
        desired = {
            #"ui.theme": self.theme.currentText(),
            "ui.lang": self.lang.itemData(self.lang.currentIndex()),  # use the locale code, not the display text.
            "ui.font_size": int(self.font_size_spin.value()),
            "ui.thumbnail.max_size": int(self.thumb_slider.value()),
            "behavior.similarity_tolerance": int(self.similarity_tolerance_slider.value()),
            "behavior.confirm_delete": bool(self.cb_confirm_delete.isChecked()),
            "behavior.compare_file_size": bool(self.cb_compare_file_size.isChecked()),
            #"behavior.delete_to_trash": bool(self.delete_to_trash.isChecked()),
            #"ui.auto_next_group": bool(self.auto_next.isChecked()),
            #"behavior.exclude_dirs": self.exclude_dirs.text().strip(),
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

    def _ok(self):
        changed, keys = self._collect_changes()
        if keys:
            self._apply()
        self.accept()