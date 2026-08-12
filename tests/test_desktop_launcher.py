from __future__ import annotations

import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rasp.desktop import (
    InstanceLock,
    main,
    reserve_loopback_socket,
    resolve_data_directory,
)


class DesktopLauncherTests(unittest.TestCase):
    def test_windows_data_directory_uses_local_app_data(self) -> None:
        with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\User\AppData\Local"}):
            directory = resolve_data_directory(platform="win32")

        self.assertEqual(
            directory,
            Path(r"C:\Users\User\AppData\Local") / "CollegeAutoSchedule",
        )

    def test_missing_local_app_data_is_reported(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                resolve_data_directory(platform="win32")

    def test_instance_lock_rejects_second_process_and_can_be_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "application.lock"
            first = InstanceLock(lock_path)
            second = InstanceLock(lock_path)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_instance_lock_recovers_from_invalid_stale_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "application.lock"
            lock_path.write_text("not-a-pid", encoding="utf-8")
            lock = InstanceLock(lock_path, url="http://127.0.0.1:43125")

            self.assertTrue(lock.acquire())
            lock.release()

    def test_windows_process_probe_closes_the_process_handle(self) -> None:
        with patch("ctypes.windll", create=True) as windll:
            windll.kernel32.OpenProcess.return_value = 123

            running = InstanceLock._process_is_running(42, platform="win32")

        self.assertTrue(running)
        windll.kernel32.OpenProcess.assert_called_once_with(0x1000, False, 42)
        windll.kernel32.CloseHandle.assert_called_once_with(123)

    def test_main_writes_safe_startup_error_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_directory = Path(directory)
            with (
                patch("rasp.desktop.resolve_data_directory", return_value=data_directory),
                patch("rasp.desktop._run", side_effect=RuntimeError("startup failed")),
            ):
                exit_code = main()

            log = (data_directory / "startup-error.log").read_text(encoding="utf-8")
            self.assertEqual(exit_code, 1)
            self.assertIn("RuntimeError: startup failed", log)

    def test_desktop_server_does_not_require_console_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            listener = MagicMock()
            listener.close = MagicMock()
            server = MagicMock()
            server.started = True
            with (
                patch("rasp.desktop.resolve_data_directory", return_value=Path(directory)),
                patch("rasp.desktop.reserve_loopback_socket", return_value=(listener, 43125)),
                patch("rasp.desktop.SqliteImportRepository.initialize"),
                patch("rasp.desktop.create_app", return_value=MagicMock()),
                patch("rasp.desktop.uvicorn.Config", return_value=MagicMock()) as config,
                patch("rasp.desktop.uvicorn.Server", return_value=server),
                patch("rasp.desktop.threading.Thread"),
            ):
                exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertIsNone(config.call_args.kwargs["log_config"])
        self.assertFalse(config.call_args.kwargs["access_log"])

    def test_reserved_socket_is_bound_to_loopback(self) -> None:
        try:
            listener, port = reserve_loopback_socket()
        except PermissionError:
            self.skipTest("Sandbox does not allow binding a loopback socket")
        try:
            host, bound_port = listener.getsockname()
            self.assertEqual(host, "127.0.0.1")
            self.assertEqual(port, bound_port)
            self.assertEqual(listener.family, socket.AF_INET)
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main()
