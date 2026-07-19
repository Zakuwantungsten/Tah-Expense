import sys
import asyncio

import qasync
from PySide6.QtWidgets import QApplication

from tahmeed.config import APP_NAME
from tahmeed.db.connection import close as close_db
from tahmeed.services.api_client import api_client, close_api
from tahmeed.ui.login import LoginWindow
from tahmeed.ui.main_window import MainWindow


def _return_to_login(login: LoginWindow, window: MainWindow, open_windows: list) -> None:
    login.clear_fields()
    # Keep a top-level window visible before closing the dashboard; otherwise
    # Qt's default last-window behavior exits the process.
    login.show()
    window._force_close = True
    window.close()
    if window in open_windows:
        open_windows.remove(window)


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

        def on_logout():
            asyncio.ensure_future(_do_logout(win))

        async def _do_logout(w: MainWindow):
            # Same save/discard prompt as window close — don't return to login
            # until the cashier has dealt with unsaved register rows.
            if not await w.prepare_to_leave():
                return
            try:
                await api_client.logout()
            except Exception:
                # Local logout must still complete if the server is unavailable.
                pass
            _return_to_login(login, w, _open_windows)

        win.logout_requested.connect(on_logout)
        win.show()

    login.login_successful.connect(on_login_success)
    login.show()

    with loop:
        loop.run_forever()
        # Clean up the DB client while the loop is still open (exiting the
        # `with` block closes the loop, so this must run inside it).
        loop.run_until_complete(asyncio.gather(close_api(), close_db()))


if __name__ == "__main__":
    main()
