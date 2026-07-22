from tahmeed import main
from tahmeed.main import _launch_verified_installer, _return_to_login


class FakeLogin:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def clear_fields(self) -> None:
        self.events.append("clear")

    def show(self) -> None:
        self.events.append("show")


class FakeWindow:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self._force_close = False

    def close(self) -> None:
        self.events.append("close")


def test_logout_shows_login_before_closing_last_window() -> None:
    events: list[str] = []
    login = FakeLogin(events)
    window = FakeWindow(events)
    windows = [window]

    _return_to_login(login, window, windows)

    assert events == ["clear", "show", "close"]
    assert window._force_close is True
    assert windows == []


def test_installer_launch_marks_update_launched(monkeypatch, tmp_path) -> None:
    installer = tmp_path / "TahmeedExpenseSetup-1.0.1.exe"
    installer.write_bytes(b"verified")
    monkeypatch.setattr(main, "recover_ready_update", lambda: installer)
    marked = []
    monkeypatch.setattr(main, "mark_update_launched", lambda: marked.append(True))
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: None)

    assert _launch_verified_installer(installer) is True
    assert marked == [True]


def test_installer_launch_uses_argument_list_without_shell(monkeypatch, tmp_path) -> None:
    installer = tmp_path / "TahmeedExpenseSetup-1.0.1.exe"
    installer.write_bytes(b"verified")
    monkeypatch.setattr(main, "recover_ready_update", lambda: installer)
    calls = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    assert _launch_verified_installer(installer) is True
    command, options = calls[0]
    assert command[0][0] == str(installer)
    assert "/RELAUNCH=1" in command[0]
    assert options["shell"] is False
    assert options["cwd"] == str(tmp_path)


def test_installer_launch_rejects_path_not_in_ready_state(monkeypatch, tmp_path) -> None:
    verified = tmp_path / "verified.exe"
    requested = tmp_path / "other.exe"
    monkeypatch.setattr(main, "recover_ready_update", lambda: verified)
    monkeypatch.setattr(
        main.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not launch")),
    )
    assert _launch_verified_installer(requested) is False
