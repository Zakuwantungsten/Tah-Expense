from tahmeed.main import _return_to_login


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
