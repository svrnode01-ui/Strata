import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest

import worker


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/v1/chat":
            self.server.last_native_request = payload
            body = json.dumps({"output": [{"type": "message", "content": "native answer"}],
                               "stats": {"total_output_tokens": 7, "reasoning_output_tokens": 0}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        text = payload["messages"][-1]["content"]
        if text == "slow":
            time.sleep(3)
        body = json.dumps({"model": "fixture", "choices": [{"message": {"role": "assistant", "content": "Ответ"},
                          "finish_reason": "length" if text == "truncated" else "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_queue_distinguishes_complete_from_truncated(self):
        first = worker.enqueue(self.root, "normal", 256, 10, "none")
        second = worker.enqueue(self.root, "truncated", 256, 10, "low")
        worker.work(self.root, self.url, "fixture")
        self.assertEqual(json.loads((self.root / (first + ".json")).read_text(encoding="utf-8"))["status"], "completed")
        self.assertEqual(json.loads((self.root / (second + ".json")).read_text(encoding="utf-8"))["status"], "incomplete")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_wall_deadline_and_crash_recovery(self):
        abandoned = worker.enqueue(self.root, "never replay this", 256, 10, "none")
        with worker.database(self.root) as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (abandoned,))
        slow = worker.enqueue(self.root, "slow", 256, 1, "none")
        worker.work(self.root, self.url, "fixture")
        with worker.database(self.root) as db:
            rows = {r["id"]: r["status"] for r in db.execute("SELECT id,status FROM jobs")}
        self.assertEqual(rows[abandoned], "interrupted")
        self.assertEqual(rows[slow], "timed_out")

    def test_single_owner_and_local_endpoint(self):
        with worker.worker_lock(self.root):
            with self.assertRaises(RuntimeError):
                with worker.worker_lock(self.root):
                    self.fail("Second worker acquired lock")
        for url in ("https://example.com/v1", "http://127.0.0.1@evil.example/v1", "http://localhost/v1?x=1"):
            with self.assertRaises(ValueError):
                worker.endpoint(url)
        with self.assertRaises(ValueError):
            worker.enqueue(self.root, "Я" * 13000, 256, 10, "none")

    def test_native_lmstudio_uses_supported_reasoning_control(self):
        job_id = worker.enqueue(self.root, "normal", 256, 10, "none")
        worker.work(self.root, self.url, "fixture", protocol="lmstudio")
        result = json.loads((self.root / (job_id + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["message"]["content"], "native answer")
        self.assertEqual(self.server.last_native_request["reasoning"], "off")
        self.assertFalse(self.server.last_native_request["store"])


if __name__ == "__main__":
    unittest.main()
