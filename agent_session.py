import glob
import json
import os
import sys
import shutil

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from rocky_persona import build_rocky_persona


CLAUDE_SEARCH_PATHS_TEMPLATE = [
    '{home}/.local/bin/claude',
    '{home}/.npm-global/bin/claude',
    '/opt/homebrew/bin/claude',
    '/usr/local/bin/claude',
    '/usr/bin/claude',
    # Windows (npm global install)
    '{appdata}/npm/claude.cmd',
    '{appdata}/npm/claude',
]

CODEX_SEARCH_PATHS_TEMPLATE = [
    '{home}/.local/bin/codex',
    '{home}/.npm-global/bin/codex',
    '/opt/homebrew/bin/codex',
    '/usr/local/bin/codex',
    '/usr/bin/codex',
    # Windows
    '{appdata}/npm/codex.cmd',
    '{appdata}/npm/codex',
]

PROVIDERS = ['claude', 'codex']
THINKING_OPTIONS = {
    'claude': ['low', 'medium', 'high', 'xhigh', 'max'],
    'codex': ['low', 'medium', 'high', 'xhigh'],
}
DEFAULT_MODELS = {'claude': 'sonnet', 'codex': 'gpt-5.5'}
MODEL_SUGGESTIONS = {
    'claude': ['sonnet', 'opus', 'claude-sonnet-4-6'],
    'codex': ['gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.3-codex', 'gpt-5.3-codex-spark'],
}


class OutputLine:
    TEXT = 'text'
    TOOL = 'tool'
    SYSTEM = 'system'
    ERROR = 'error'

    _counter = 0

    def __init__(self, text, kind=TEXT):
        self.text = text
        self.kind = kind
        OutputLine._counter += 1
        self.id = OutputLine._counter


def _real_home():
    if sys.platform != 'win32':
        try:
            import pwd
            return pwd.getpwuid(os.getuid()).pw_dir
        except Exception:
            pass
    return os.path.expanduser('~')


def _load_default_provider():
    try:
        cfg = _load_config()
        val = cfg.get('default_provider', 'claude')
        return val if val in PROVIDERS else 'claude'
    except Exception:
        return 'claude'


def _save_default_provider(provider):
    try:
        cfg = _load_config()
        cfg['default_provider'] = provider
        _save_config(cfg)
    except Exception:
        pass


def _load_user_name() -> str:
    try:
        return _load_config().get('user_name', '')
    except Exception:
        return ''


def _save_user_name(name: str):
    name = name.strip()[:40]
    if not name:
        return
    try:
        cfg = _load_config()
        cfg['user_name'] = name
        _save_config(cfg)
    except Exception:
        pass


def _config_path():
    d = os.path.join(os.path.expanduser('~'), '.config', 'agentrocky')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'settings.json')


def _load_config():
    p = _config_path()
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


def _save_config(cfg):
    with open(_config_path(), 'w') as f:
        json.dump(cfg, f)


class AgentSession(QObject):
    line_added = pyqtSignal(object)       # OutputLine
    running_changed = pyqtSignal(bool)
    ready_changed = pyqtSignal(bool)

    def __init__(self, working_directory):
        super().__init__()
        self.working_directory = working_directory
        self.lines = []
        self.is_ready = False
        self.is_running = False
        self.user_name = _load_user_name()
        self.provider = _load_default_provider()
        self.model = DEFAULT_MODELS[self.provider]
        self.thinking = 'high'
        self._process = None
        self._read_buffer = b''
        self._conversation_history = []
        self._start()

    # ------------------------------------------------------------------ public

    def send(self, prompt):
        if self.is_running:
            return
        self._remember('user', prompt)
        if self.provider == 'claude':
            self._send_claude(prompt)
        else:
            self._run_codex(prompt)

    def new_session(self):
        self._stop_process()
        self._read_buffer = b''
        self._conversation_history.clear()
        self._set_running(False)
        self._set_ready(False)
        self.lines.clear()
        self._start()

    def set_user_name(self, name: str):
        name = name.strip()[:40]
        if name == self.user_name:
            return
        self.user_name = name
        _save_user_name(name)
        self.new_session()

    def apply_settings(self, provider, model, thinking):
        if self.is_running:
            self._append('Wait for the current task to finish before changing agent settings.', OutputLine.SYSTEM)
            return
        model = model.strip()
        self.provider = provider if provider in PROVIDERS else 'claude'
        self.model = model if model else DEFAULT_MODELS[self.provider]
        opts = THINKING_OPTIONS.get(self.provider, ['high'])
        self.thinking = thinking if thinking in opts else 'high'
        self.new_session()

    # ----------------------------------------------------------------- private

    def _start(self):
        if self.provider == 'claude':
            self._start_claude()
        else:
            path = self._find_binary('codex')
            if path:
                self._set_ready(True)
                self._append('Codex ready. Rocky keeps conversation history.', OutputLine.SYSTEM)
            else:
                self._append(
                    'codex binary not found - checked:\n' + '\n'.join(self._search_paths('codex')),
                    OutputLine.ERROR,
                )

    # ---- Claude ----

    def _start_claude(self):
        path = self._find_binary('claude')
        if not path:
            self._append(
                'claude binary not found - checked:\n' + '\n'.join(self._search_paths('claude')),
                OutputLine.ERROR,
            )
            return

        proc = QProcess(self)
        proc.setWorkingDirectory(self.working_directory)

        env = QProcessEnvironment.systemEnvironment()
        env.remove('CLAUDECODE')
        env.remove('CLAUDE_CODE_ENTRYPOINT')
        proc.setProcessEnvironment(env)

        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)

        proc.start(path, self._claude_args())
        self._process = proc

        self._append(f'Claude starting with model {self.model}, thinking {self.thinking}...', OutputLine.SYSTEM)
        QTimer.singleShot(4000, self._set_ready_fallback)

    def _claude_args(self):
        args = ['-p', '--output-format', 'stream-json', '--input-format', 'stream-json',
                '--verbose', '--dangerously-skip-permissions']
        if self.model.strip():
            args += ['--model', self.model]
        args += ['--effort', self.thinking]
        args += ['--append-system-prompt', build_rocky_persona(self.user_name)]
        return args

    def _send_claude(self, prompt):
        if not self.is_ready:
            self._append('Claude is still starting. Try again in a moment.', OutputLine.SYSTEM)
            return
        self._set_running(True)
        payload = {'type': 'user', 'message': {'role': 'user', 'content': prompt}}
        self._process.write((json.dumps(payload) + '\n').encode())

    def _on_stdout(self):
        data = bytes(self._process.readAllStandardOutput())
        self._read_buffer += data
        while b'\n' in self._read_buffer:
            idx = self._read_buffer.index(b'\n')
            line = self._read_buffer[:idx]
            self._read_buffer = self._read_buffer[idx + 1:]
            text = line.decode('utf-8', errors='replace').strip()
            if text:
                if self.provider == 'claude':
                    self._parse_claude(text)
                else:
                    self._parse_codex(text)

    def _on_stderr(self):
        data = bytes(self._process.readAllStandardError())
        text = data.decode('utf-8', errors='replace').strip()
        if text:
            self._append(text, OutputLine.ERROR)

    def _on_finished(self, exit_code, _exit_status):
        self._process = None
        self._set_ready(False)
        self._set_running(False)
        self._append(f'Claude exited (code {exit_code})', OutputLine.SYSTEM)

    def _parse_claude(self, raw):
        try:
            obj = json.loads(raw)
        except Exception:
            self._append(f'[raw] {raw}', OutputLine.SYSTEM)
            return

        type_ = obj.get('type', '')
        subtype = obj.get('subtype', '')

        if type_ == 'system' and subtype == 'init':
            self._set_ready(True)
        elif type_ == 'assistant':
            for block in (obj.get('message') or {}).get('content') or []:
                self._render_claude_block(block)
        elif type_ == 'result':
            self._set_running(False)
            self._append('', OutputLine.TEXT)

    def _render_claude_block(self, block):
        btype = block.get('type', '')
        if btype == 'text':
            text = (block.get('text') or '').strip()
            if text:
                self._remember('assistant', text)
                self._append(f'Rocky: {text}', OutputLine.TEXT)
        elif btype == 'tool_use':
            name = block.get('name', 'tool')
            inp = block.get('input') or {}
            detail = inp.get('command') or inp.get('path') or inp.get('description') or ', '.join(inp.keys())
            self._append(f'[{name}] {detail}', OutputLine.TOOL)

    # ---- Codex ----

    def _run_codex(self, prompt):
        path = self._find_binary('codex')
        if not path:
            self._append(
                'codex binary not found - checked:\n' + '\n'.join(self._search_paths('codex')),
                OutputLine.ERROR,
            )
            return

        self._set_running(True)
        proc = QProcess(self)
        proc.setWorkingDirectory(self.working_directory)
        proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())

        proc.readyReadStandardOutput.connect(lambda: self._on_codex_stdout(proc))
        proc.readyReadStandardError.connect(lambda: self._on_codex_stderr(proc))
        proc.finished.connect(lambda code, _: self._on_codex_finished(proc, code))

        args = self._codex_args()
        self._append(f'Codex running with model {self.model}, thinking {self.thinking}...', OutputLine.SYSTEM)
        proc.start(path, args)
        self._process = proc
        proc.write(self._codex_prompt(prompt).encode())
        proc.closeWriteChannel()

    def _codex_args(self):
        args = ['exec', '--json', '--skip-git-repo-check',
                '-C', self.working_directory, '--dangerously-bypass-approvals-and-sandbox']
        if self.model.strip():
            args += ['-m', self.model]
        args += ['-c', f'model_reasoning_effort="{self.thinking}"', '-']
        return args

    def _codex_prompt(self, current_prompt):
        persona_block = f'Persona instructions:\n{build_rocky_persona(self.user_name)}\n\n'
        history = self._conversation_history[:-1][-12:]
        if not history:
            return persona_block + current_prompt
        lines = '\n\n'.join(f'{t["role"]}: {t["text"]}' for t in history)
        return (
            persona_block
            + 'Continue this conversation. Use the prior turns for context and answer the latest user message.\n\n'
            f'Prior conversation:\n{lines}\n\nLatest user message:\n{current_prompt}'
        )

    def _on_codex_stdout(self, proc):
        data = bytes(proc.readAllStandardOutput())
        self._read_buffer += data
        while b'\n' in self._read_buffer:
            idx = self._read_buffer.index(b'\n')
            line = self._read_buffer[:idx]
            self._read_buffer = self._read_buffer[idx + 1:]
            text = line.decode('utf-8', errors='replace').strip()
            if text:
                self._parse_codex(text)

    def _on_codex_stderr(self, proc):
        data = bytes(proc.readAllStandardError())
        text = data.decode('utf-8', errors='replace').strip()
        if text and 'failed to record rollout' not in text and 'Reading additional input' not in text:
            self._append(text, OutputLine.ERROR)

    def _on_codex_finished(self, proc, exit_code):
        if self._process is proc:
            self._process = None
        self._set_running(False)
        self._set_ready(True)
        if exit_code == 0:
            self._append('Codex done', OutputLine.SYSTEM)
        else:
            self._append(f'Codex stopped (code {exit_code})', OutputLine.ERROR)

    def _parse_codex(self, raw):
        try:
            obj = json.loads(raw)
        except Exception:
            self._append(f'[codex] {raw}', OutputLine.SYSTEM)
            return

        type_ = (obj.get('type') or obj.get('event') or '').lower()
        item = obj.get('item') or {}
        item_type = (item.get('type') or '').lower()
        text = self._extract_text(obj)

        if 'agent_message' in item_type:
            if text:
                self._remember('assistant', text)
                self._append(f'Rocky: {text}', OutputLine.TEXT)
        elif 'error' in type_:
            self._append(text or f'[codex error] {obj}', OutputLine.ERROR)
        elif any(k in type_ for k in ('message', 'response', 'final', 'answer')):
            if text:
                self._remember('assistant', text)
                self._append(f'Rocky: {text}', OutputLine.TEXT)

    def _extract_text(self, value):
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for k in ('message', 'text', 'content', 'summary', 'command', 'cmd', 'path', 'output'):
                v = value.get(k)
                if isinstance(v, str) and v.strip():
                    return v
            for k in ('item', 'delta', 'result', 'data', 'payload'):
                t = self._extract_text(value.get(k))
                if t:
                    return t
        if isinstance(value, list):
            parts = [self._extract_text(v) for v in value]
            return '\n'.join(p for p in parts if p.strip())
        return ''

    # ---- helpers ----

    def _set_ready_fallback(self):
        if self.provider == 'claude' and not self.is_ready:
            self._set_ready(True)

    def _set_ready(self, val):
        if self.is_ready != val:
            self.is_ready = val
            self.ready_changed.emit(val)

    def _set_running(self, val):
        if self.is_running != val:
            self.is_running = val
            self.running_changed.emit(val)

    def _append(self, text, kind=OutputLine.TEXT):
        line = OutputLine(text, kind)
        self.lines.append(line)
        self.line_added.emit(line)

    def _remember(self, role, text):
        text = text.strip()
        if not text:
            return
        self._conversation_history.append({'role': role, 'text': text})
        if len(self._conversation_history) > 40:
            self._conversation_history = self._conversation_history[-40:]

    def _stop_process(self):
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
        self._process = None

    def _find_binary(self, name):
        # Check PATH first (covers nvm, volta, and other version managers)
        found = shutil.which(name)
        if found:
            return found
        for p in self._search_paths(name):
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        return None

    def _search_paths(self, name):
        home = _real_home()
        appdata = os.environ.get('APPDATA', '') if sys.platform == 'win32' else ''
        templates = CLAUDE_SEARCH_PATHS_TEMPLATE if name == 'claude' else CODEX_SEARCH_PATHS_TEMPLATE
        paths = [t.format(home=home, appdata=appdata) for t in templates]
        # Also search nvm and volta managed installs (Linux/macOS)
        paths += sorted(glob.glob(f'{home}/.nvm/versions/node/*/bin/{name}'), reverse=True)
        paths += sorted(glob.glob(f'{home}/.volta/tools/image/packages/{name}/*/bin/{name}'), reverse=True)
        return paths
