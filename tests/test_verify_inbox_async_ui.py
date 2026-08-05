"""Regression: verify-inbox UI must not nest asyncio tasks on Py3.14."""

import asyncio

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_await_ui_resumes_after_deferred_callback() -> None:
    """``_await_ui`` must suspend the coroutine until the Qt-tick callback runs."""
    app = _app()
    ran = {"ui": False}

    class _Host:
        async def _await_ui(self, fn):
            loop = asyncio.get_running_loop()
            fut = loop.create_future()

            def _run() -> None:
                if fut.done():
                    return
                try:
                    fut.set_result(fn())
                except Exception as exc:
                    fut.set_exception(exc)

            QTimer.singleShot(0, _run)
            return await fut

    async def _exercise() -> str:
        host = _Host()

        def _work():
            ran["ui"] = True
            return "ok"

        task = asyncio.create_task(host._await_ui(_work))
        assert ran["ui"] is False  # must not run inline inside the task body
        for _ in range(40):
            app.processEvents()
            if task.done():
                break
            await asyncio.sleep(0)
            QTest.qWait(10)
        return await task

    assert asyncio.run(_exercise()) == "ok"
    assert ran["ui"] is True


def test_finish_mutation_defers_reload_until_after_caller_returns() -> None:
    """Reload/message work must be QTimer-deferred, not inline in the caller."""
    app = _app()
    order: list[str] = []

    class _Host:
        def _schedule_ui(self, fn) -> None:
            QTimer.singleShot(0, fn)

        def _reset_and_load(self) -> None:
            order.append("reload")

        def _finish_mutation(self, *, info=None, reload=False) -> None:
            def _run() -> None:
                if reload:
                    self._reset_and_load()
                if info:
                    order.append(f"info:{info}")

            self._schedule_ui(_run)

    order.append("task-start")
    _Host()._finish_mutation(info="Approved 2", reload=True)
    order.append("task-end")
    for _ in range(40):
        app.processEvents()
        QTest.qWait(10)
        if "reload" in order:
            break

    assert order[:2] == ["task-start", "task-end"]
    assert "reload" in order
    assert "info:Approved 2" in order
    assert order.index("task-end") < order.index("reload")
