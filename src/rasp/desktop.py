from __future__ import annotations

import json
import os
import socket
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

import uvicorn

from rasp.web.app import create_app
from rasp.storage.sqlite import SqliteImportRepository


LOOPBACK_HOST = "127.0.0.1"


def resolve_data_directory(*, platform: str | None = None) -> Path:
    current_platform = platform or sys.platform
    if current_platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA is unavailable")
        return Path(local_app_data) / "CollegeAutoSchedule"
    return Path.home() / ".college-auto-schedule"


def reserve_loopback_socket() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((LOOPBACK_HOST, 0))
        listener.listen(128)
        return listener, int(listener.getsockname()[1])
    except BaseException:
        listener.close()
        raise


class InstanceLock:
    def __init__(self, path: Path, *, url: str | None = None) -> None:
        self.path = path
        self.url = url
        self._descriptor: int | None = None

    @staticmethod
    def _process_is_running(pid: int, *, platform: str | None = None) -> bool:
        if pid < 1:
            return False
        current_platform = platform or sys.platform
        if current_platform == "win32":
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def existing_url(self) -> str | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
            url = str(payload["url"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None
        return url if self._process_is_running(pid) else None

    def acquire(self) -> bool:
        if self._descriptor is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                self._descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                break
            except FileExistsError:
                if attempt or self.existing_url() is not None:
                    return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
        if self._descriptor is None:
            return False
        payload = json.dumps({"pid": os.getpid(), "url": self.url})
        os.write(self._descriptor, payload.encode("utf-8"))
        return True

    def release(self) -> None:
        if self._descriptor is None:
            return
        os.close(self._descriptor)
        self._descriptor = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> InstanceLock:
        if not self.acquire():
            raise RuntimeError("Application is already running")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def _open_browser_when_ready(server: uvicorn.Server, url: str) -> None:
    while not server.started and not server.should_exit:
        threading.Event().wait(0.05)
    if server.started:
        webbrowser.open(url)


def _run() -> int:
    data_directory = resolve_data_directory()
    data_directory.mkdir(parents=True, exist_ok=True)
    listener, port = reserve_loopback_socket()
    url = f"http://{LOOPBACK_HOST}:{port}"
    lock = InstanceLock(data_directory / "application.lock", url=url)
    if not lock.acquire():
        listener.close()
        if existing_url := lock.existing_url():
            webbrowser.open(existing_url)
        return 0

    try:
        database_path = data_directory / "rasp.sqlite3"
        SqliteImportRepository(database_path).initialize()
        app = create_app(database_path=database_path)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=LOOPBACK_HOST,
                port=port,
                log_config=None,
                access_log=False,
            )
        )
        browser_thread = threading.Thread(
            target=_open_browser_when_ready,
            args=(server, url),
            daemon=True,
        )
        browser_thread.start()
        server.run(sockets=[listener])
    finally:
        listener.close()
        lock.release()
    return 0


def main() -> int:
    try:
        return _run()
    except BaseException:
        try:
            data_directory = resolve_data_directory()
            data_directory.mkdir(parents=True, exist_ok=True)
            (data_directory / "startup-error.log").write_text(
                traceback.format_exc(),
                encoding="utf-8",
            )
        except BaseException:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
