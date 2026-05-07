#!/usr/bin/env python3
import os
import sys

from PyQt6.QtWidgets import QApplication

from agent_session import AgentSession, _real_home
from rocky_window import RockyWindow


def main():
    # Allow transparency on X11
    os.environ.setdefault('QT_XCB_GL_INTEGRATION', 'xcb_glx')

    app = QApplication(sys.argv)
    app.setApplicationName('rockyAI')
    app.setQuitOnLastWindowClosed(False)

    assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agentrocky')

    session = AgentSession(working_directory=_real_home())
    window = RockyWindow(session=session, assets_dir=assets_dir)
    window  # keep reference

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
