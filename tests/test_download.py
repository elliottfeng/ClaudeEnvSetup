from __future__ import annotations

import hashlib
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.download import DownloadCancelled, DownloadError, DownloadManager
from app.models import ComponentId, ReleaseInfo, UrlCandidate


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # pragma: no cover
        pass

    def do_GET(self) -> None:  # noqa: N802
        server = self.server  # type: ignore[assignment]
        data: bytes = server.data
        if self.path == "/slow":
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            for offset in range(0, len(data), 64 * 1024):
                try:
                    self.wfile.write(data[offset : offset + 64 * 1024])
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                time.sleep(0.02)
            return
        range_header = self.headers.get("Range")
        if range_header and server.range_support:
            server.range_requests.append(range_header)
            try:
                start = int(range_header.split("=", 1)[1].split("-", 1)[0])
            except (IndexError, ValueError):
                start = 0
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.end_headers()
                return
            body = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _Server(ThreadingHTTPServer):
    data = b""
    range_support = True
    range_requests: list[str]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.range_requests = []
        self.data = b""
        self.range_support = True


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.temp = tempfile.TemporaryDirectory(prefix="claudeenv-dl-")
        self.dest = Path(self.temp.name)
        self.data = (b"claude-env-test-" * 200_000)[: 2 * 1024 * 1024]
        self.server.data = self.data
        self.manager = DownloadManager(retries=2, read_timeout=10)
        self.manager._wait = lambda attempt, cancel: None  # type: ignore[assignment]
        self.release = ReleaseInfo(
            ComponentId.NODE,
            "1.0.0",
            (1, 0, 0),
            "test.bin",
            (UrlCandidate(f"{self.base}/file", "official"),),
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_full_download_and_sha(self) -> None:
        digest = hashlib.sha256(self.data).hexdigest()
        release = ReleaseInfo(
            ComponentId.NODE,
            "1.0.0",
            (1, 0, 0),
            "test.bin",
            (UrlCandidate(f"{self.base}/file", "official", digest),),
        )
        path = self.manager.fetch(release, self.dest)
        self.assertEqual(path.read_bytes(), self.data)
        self.assertFalse((self.dest / "test.bin.part").exists())

    def test_resume_with_range(self) -> None:
        part = self.dest / "test.bin.part"
        part.write_bytes(self.data[:100_000])
        path = self.manager.fetch(self.release, self.dest)
        self.assertEqual(path.read_bytes(), self.data)
        self.assertTrue(self.server.range_requests)

    def test_416_restarts(self) -> None:
        part = self.dest / "test.bin.part"
        part.write_bytes(b"x" * (len(self.data) + 10))
        path = self.manager.fetch(self.release, self.dest)
        self.assertEqual(path.read_bytes(), self.data)

    def test_sha_mismatch_fails(self) -> None:
        release = ReleaseInfo(
            ComponentId.NODE,
            "1.0.0",
            (1, 0, 0),
            "test.bin",
            (UrlCandidate(f"{self.base}/file", "official", "0" * 64),),
        )
        with self.assertRaises(DownloadError):
            self.manager.fetch(release, self.dest)
        self.assertFalse((self.dest / "test.bin").exists())

    def test_existing_target_is_reused(self) -> None:
        target = self.dest / "test.bin"
        target.write_bytes(b"already-downloaded")
        path = self.manager.fetch(self.release, self.dest)
        self.assertEqual(path, target)
        self.assertEqual(target.read_bytes(), b"already-downloaded")

    def test_cancel_keeps_part(self) -> None:
        release = ReleaseInfo(
            ComponentId.NODE,
            "1.0.0",
            (1, 0, 0),
            "test.bin",
            (UrlCandidate(f"{self.base}/slow", "official"),),
        )
        cancel = threading.Event()
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                self.manager.fetch(release, self.dest, cancel=cancel)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        time.sleep(0.15)
        cancel.set()
        thread.join(timeout=10)
        self.assertTrue(errors)
        self.assertIsInstance(errors[0], DownloadCancelled)
        self.assertTrue((self.dest / "test.bin.part").exists())


if __name__ == "__main__":
    unittest.main()
