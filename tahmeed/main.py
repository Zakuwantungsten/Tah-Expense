import sys
import asyncio

import qasync
from PySide6.QtWidgets import QApplication

from tahmeed.config import APP_NAME
from tahmeed.db.connection import close as close_db
from tahmeed.ui.login import LoginWindow
from tahmeed.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    # Keep a reference so the GC doesn't collect the window
    _open_windows: list = []

    login = LoginWindow()

    def on_login_success(user):
        login.hide()
        win = MainWindow(user)
        _open_windows.append(win)
        win.show()

    login.login_successful.connect(on_login_success)
    login.show()

    with loop:
        loop.run_forever()

    loop.run_until_complete(close_db())


if __name__ == "__main__":
    main()
