import sys
import asyncio
import subprocess
from pathlib import Path

import qasync
from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox

from tahmeed.config import APP_NAME, APP_VERSION
from tahmeed.db.connection import close as close_db
from tahmeed.services.api_client import api_client, close_api
from tahmeed.services.update_controller import UpdateController
from tahmeed.services.update_service import (
    install_on_exit_path,
    recover_ready_update,
    update_root,
)
from tahmeed.ui.login import LoginWindow
from tahmeed.ui.main_window import MainWindow

WINDOWS_APP_MUTEX = "TahmeedExpense.A3F1C2D4-5E6F-4A7B-8C9D-0E1F2A3B4C5D"


def _acquire_windows_app_mutex() -> tuple[object | None, bool]:
    """Create the named mutex Inno Setup uses to detect a running app."""
    if sys.platform != "win32":
        return None, False
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, False, WINDOWS_APP_MUTEX)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    already_exists = ctypes.get_last_error() == 183
    if already_exists:
        kernel32.CloseHandle(handle)
        return None, True
    return handle, False


def _release_windows_app_mutex(handle: object | None) -> None:
    if sys.platform == "win32" and handle is not None:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.CloseHandle(handle)


def _return_to_login(login: LoginWindow, window: MainWindow, open_windows: list) -> None:
    login.clear_fields()
    # Keep a top-level window visible before closing the dashboard; otherwise
    # Qt's default last-window behavior exits the process.
    login.show()
    window._force_close = True
    window.close()
    if window in open_windows:
        open_windows.remove(window)


def _launch_verified_installer(installer: Path) -> bool:
    """Launch only the installer currently represented by valid ready metadata."""
    verified = recover_ready_update()
    if verified is None or verified.resolve() != installer.resolve():
        return False
    subprocess.Popen(
        [
            str(verified),
            "/SILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/RELAUNCH=1",
        ],
        cwd=str(verified.parent),
        shell=False,
        close_fds=True,
    )
    return True


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")

    lock_path = update_root().parent / "desktop.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    app_lock = QLockFile(str(lock_path))
    app_lock.setStaleLockTime(30_000)
    if not app_lock.tryLock(100):
        QMessageBox.information(
            None,
            APP_NAME,
            f"{APP_NAME} is already running. Use the existing window.",
        )
        return
    windows_mutex, already_running = _acquire_windows_app_mutex()
    if already_running:
        app_lock.unlock()
        QMessageBox.information(
            None,
            APP_NAME,
            f"{APP_NAME} is already running. Use the existing window.",
        )
        return

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    # Keep a reference so the GC doesn't collect the window
    _open_windows: list = []
    pending_installer: list[Path] = []
    install_request_running = False

    login = LoginWindow()

    async def _prepare_and_exit(path: str) -> None:
        nonlocal install_request_running
        if install_request_running:
            return
        install_request_running = True
        try:
            verified = recover_ready_update()
            if verified is None or verified.resolve() != Path(path).resolve():
                return
            for window in list(_open_windows):
                if not await window.prepare_to_leave():
                    return
            pending_installer[:] = [verified]
            for window in list(_open_windows):
                window._force_close = True
                window.close()
            app.quit()
        finally:
            install_request_running = False

    def request_install(path: str) -> None:
        asyncio.ensure_future(_prepare_and_exit(path))

    update_controller = UpdateController(request_install, app)
    update_controller.start()

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
        loop.run_until_complete(
            asyncio.gather(close_api(), close_db(), return_exceptions=True)
        )

    installer = pending_installer[0] if pending_installer else install_on_exit_path()
    app_lock.unlock()
    # Retain the Win32 mutex until this point so Inno cannot start while the
    # process still owns application resources.
    _release_windows_app_mutex(windows_mutex)
    if installer is not None:
        _launch_verified_installer(installer)


if __name__ == "__main__":
    main()
