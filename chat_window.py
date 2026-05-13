from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from agent_session import (
    DEFAULT_MODELS, MODEL_SUGGESTIONS, PROVIDERS, THINKING_OPTIONS, OutputLine,
    _load_default_provider, _save_default_provider,
    _load_user_name,
)

_COLOR_TEXT   = QColor('#00cc00')
_COLOR_TOOL   = QColor('#66ccff')
_COLOR_SYSTEM = QColor('#006600')
_COLOR_ERROR  = QColor('#ff6666')
_BG           = '#0a0a0a'
_BAR_BG       = '#000000'


def _color_for(kind):
    return {
        OutputLine.TEXT:   _COLOR_TEXT,
        OutputLine.TOOL:   _COLOR_TOOL,
        OutputLine.SYSTEM: _COLOR_SYSTEM,
        OutputLine.ERROR:  _COLOR_ERROR,
    }.get(kind, _COLOR_TEXT)


class SettingsPanel(QWidget):
    settings_changed = pyqtSignal(str, str, str)  # provider, model, thinking
    default_provider_changed = pyqtSignal(str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._syncing = False
        self.setStyleSheet(f'background:{_BG}; color:#00cc00; font-family:monospace; font-size:12px;')
        self.setFixedWidth(260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        layout.addWidget(self._label('Agent Settings', bold=True, size=13))

        layout.addWidget(self._label('Default Agent', dim=True))
        self._default_provider_box = self._combo(PROVIDERS)
        self._default_provider_box.currentTextChanged.connect(self._on_default_provider_changed)
        layout.addWidget(self._default_provider_box)

        layout.addWidget(self._label('Agent', dim=True))
        self._provider_box = self._combo(PROVIDERS)
        self._provider_box.currentTextChanged.connect(self._on_provider_changed)
        layout.addWidget(self._provider_box)

        layout.addWidget(self._label('Thinking', dim=True))
        self._thinking_box = self._combo([])
        self._thinking_box.currentTextChanged.connect(self._on_thinking_changed)
        layout.addWidget(self._thinking_box)

        layout.addWidget(self._label('Model', dim=True))
        self._model_box = self._combo([])
        self._model_box.setEditable(True)
        self._model_box.currentTextChanged.connect(self._on_model_changed)
        layout.addWidget(self._model_box)

        layout.addWidget(self._label('Your name', dim=True))
        self._name_field = QLineEdit()
        self._name_field.setPlaceholderText('What should Rocky call you?')
        self._name_field.setMaxLength(40)
        self._name_field.setStyleSheet(
            'background:#111; color:#00cc00; font-family:monospace; font-size:12px;'
            'border:1px solid #003300; padding:2px;'
        )
        self._name_field.editingFinished.connect(self._on_name_committed)
        layout.addWidget(self._name_field)

        layout.addWidget(self._label('Changes apply immediately.', dim=True, size=10))
        layout.addStretch()

        self.sync_from_session()

    def sync_from_session(self):
        self._syncing = True
        self._default_provider_box.setCurrentText(_load_default_provider())
        self._provider_box.setCurrentText(self.session.provider)
        self._refresh_thinking_options(self.session.provider)
        self._refresh_model_suggestions(self.session.provider)
        self._thinking_box.setCurrentText(self.session.thinking)
        self._model_box.setCurrentText(self.session.model)
        self._name_field.setText(self.session.user_name)
        self._syncing = False

    def set_enabled(self, enabled):
        self._provider_box.setEnabled(enabled)
        self._thinking_box.setEnabled(enabled)
        self._model_box.setEnabled(enabled)
        self._name_field.setEnabled(enabled)

    def _on_name_committed(self):
        if self._syncing:
            return
        self.session.set_user_name(self._name_field.text())

    def _on_default_provider_changed(self, provider):
        if self._syncing:
            return
        _save_default_provider(provider)
        self.default_provider_changed.emit(provider)

    def _on_provider_changed(self, provider):
        if self._syncing or not provider:
            return
        self._syncing = True
        self._refresh_thinking_options(provider)
        self._refresh_model_suggestions(provider)
        self._model_box.setCurrentText(DEFAULT_MODELS.get(provider, ''))
        self._syncing = False
        self._emit_settings()

    def _on_thinking_changed(self, _):
        if not self._syncing:
            self._emit_settings()

    def _on_model_changed(self, _):
        if not self._syncing:
            self._emit_settings()

    def _refresh_thinking_options(self, provider):
        self._syncing = True
        self._thinking_box.clear()
        self._thinking_box.addItems(THINKING_OPTIONS.get(provider, ['high']))
        self._syncing = False

    def _refresh_model_suggestions(self, provider):
        self._syncing = True
        current = self._model_box.currentText()
        self._model_box.clear()
        self._model_box.addItems(MODEL_SUGGESTIONS.get(provider, []))
        self._model_box.setCurrentText(current or DEFAULT_MODELS.get(provider, ''))
        self._syncing = False

    def _emit_settings(self):
        provider = self._provider_box.currentText()
        model = self._model_box.currentText().strip()
        thinking = self._thinking_box.currentText()
        if provider and model and thinking:
            self.settings_changed.emit(provider, model, thinking)

    def _label(self, text, bold=False, dim=False, size=12):
        lbl = QLabel(text)
        color = '#336633' if dim else '#00cc00'
        weight = 'bold' if bold else 'normal'
        lbl.setStyleSheet(f'color:{color}; font-size:{size}px; font-weight:{weight}; background:transparent;')
        return lbl

    def _combo(self, items):
        box = QComboBox()
        box.addItems(items)
        box.setStyleSheet(
            f'background:#111; color:#00cc00; font-family:monospace; font-size:12px;'
            f'border:1px solid #003300; padding:2px;'
        )
        return box


class ChatWindow(QWidget):
    closed = pyqtSignal()

    def __init__(self, session, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.session = session
        self.setFixedSize(420, 520)
        self.setStyleSheet(f'background:{_BG};')
        self._settings_visible = False
        self._settings_panel = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_divider())
        root.addWidget(self._build_output(), stretch=1)
        root.addWidget(self._build_divider())
        root.addWidget(self._build_input_row())

        session.line_added.connect(self._on_line_added)
        session.running_changed.connect(self._on_running_changed)
        session.ready_changed.connect(self._on_ready_changed)

        self._input.returnPressed.connect(self._send)

        for line in session.lines:
            self._append_line(line)

        self._input.setFocus()

    # ----------------------------------------------------------------- build

    def _build_top_bar(self):
        bar = QWidget()
        bar.setFixedHeight(38)
        bar.setStyleSheet(f'background:{_BAR_BG};')
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        self._settings_btn = QPushButton('⚙ Settings')
        self._settings_btn.setFixedWidth(112)
        self._settings_btn.setStyleSheet(self._btn_style())
        self._settings_btn.clicked.connect(self._toggle_settings)

        self._new_btn = QPushButton('+ New')
        self._new_btn.setFixedWidth(84)
        self._new_btn.setStyleSheet(self._btn_style())
        self._new_btn.clicked.connect(self._new_session)

        layout.addWidget(self._settings_btn)
        layout.addWidget(self._new_btn)
        layout.addStretch()
        return bar

    def _build_divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet('color:#003300; background:#003300;')
        line.setFixedHeight(1)
        return line

    def _build_output(self):
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setStyleSheet(
            f'background:{_BG}; color:#00cc00; font-family:monospace; font-size:12px;'
            f'border:none; padding:10px;'
        )
        self._output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        return self._output

    def _build_input_row(self):
        row = QWidget()
        row.setFixedHeight(36)
        row.setStyleSheet(f'background:rgba(0,0,0,127);')
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)

        self._prompt_label = QLabel(self._dir_label())
        self._prompt_label.setStyleSheet('color:#006600; font-family:monospace; font-size:11px; background:transparent;')

        self._ready_indicator = QLabel('❯')
        self._ready_indicator.setStyleSheet('color:#00cc00; font-family:monospace; font-size:12px; background:transparent;')
        self._update_ready_indicator()

        self._input = QLineEdit()
        self._input.setStyleSheet(
            'background:transparent; color:#00cc00; font-family:monospace; font-size:12px;'
            'border:none; padding:0;'
        )

        layout.addWidget(self._prompt_label)
        layout.addWidget(self._ready_indicator)
        layout.addWidget(self._input, stretch=1)
        return row

    # ----------------------------------------------------------------- slots

    def _toggle_settings(self):
        if self._settings_visible:
            self._hide_settings()
        else:
            self._show_settings()

    def _show_settings(self):
        if self._settings_panel is None:
            panel = SettingsPanel(self.session, self)
            panel.settings_changed.connect(self._apply_settings)
            panel.setWindowFlags(Qt.WindowType.Popup)
            self._settings_panel = panel
        self._settings_panel.sync_from_session()
        self._settings_panel.set_enabled(not self.session.is_running)
        btn_pos = self._settings_btn.mapToGlobal(self._settings_btn.rect().bottomLeft())
        self._settings_panel.move(btn_pos)
        self._settings_panel.show()
        self._settings_visible = True

    def _hide_settings(self):
        if self._settings_panel:
            self._settings_panel.hide()
        self._settings_visible = False

    def _apply_settings(self, provider, model, thinking):
        self.session.apply_settings(provider, model, thinking)
        if self._settings_panel:
            self._settings_panel.sync_from_session()
        self._input.setFocus()

    def _new_session(self):
        self.session.new_session()
        self._output.clear()
        if self._settings_panel:
            self._settings_panel.sync_from_session()
        self._input.setFocus()

    def _send(self):
        text = self._input.text().strip()
        if not text or self.session.is_running:
            return
        self._append_text(f'{self._dir_label()} ❯ {text}', OutputLine.SYSTEM)
        self._input.clear()
        self.session.send(text)
        self._input.setFocus()

    def _on_line_added(self, line):
        self._append_line(line)

    def _on_running_changed(self, running):
        self._new_btn.setEnabled(not running)
        self._settings_btn.setEnabled(not running)
        if self._settings_panel:
            self._settings_panel.set_enabled(not running)
        if running:
            self._append_text('▋', OutputLine.TEXT)
        self._scroll_to_bottom()

    def _on_ready_changed(self, _ready):
        self._update_ready_indicator()

    # ----------------------------------------------------------------- helpers

    def _append_line(self, line):
        self._append_text(line.text, line.kind)

    def _append_text(self, text, kind):
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(_color_for(kind))
        cursor.setCharFormat(fmt)
        if self._output.toPlainText():
            cursor.insertText('\n')
        cursor.insertText(text)
        self._output.setTextCursor(cursor)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        sb = self._output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _update_ready_indicator(self):
        color = '#00cc00' if self.session.is_ready else '#003300'
        self._ready_indicator.setStyleSheet(
            f'color:{color}; font-family:monospace; font-size:12px; background:transparent;'
        )

    def _dir_label(self):
        import os
        return os.path.basename(self.session.working_directory) or self.session.working_directory

    def _btn_style(self):
        return (
            'background:#111; color:#00cc00; font-family:monospace; font-size:11px;'
            'border:1px solid #003300; padding:3px 6px;'
        )

    def closeEvent(self, event):
        self.closed.emit()
        event.accept()
