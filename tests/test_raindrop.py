"""Runs the importer against a fake Raindrop."""
import argparse
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from eagle_to_karakeep import cli as importer
from test_cli import add_item


class FakeRaindrop(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def handle_request(self):
        server = self.server
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        path = self.path.removeprefix("/rest/v1")
        server.calls.append((self.command, path))
        if self.headers["Authorization"] != "Bearer key":
            return self.reply(401, {})
        if path in server.fail_once:
            server.fail_once.remove(path)
            return self.reply(500, {"result": False})
        if path == "/user":
            return self.reply(200, {"result": True, "user": {"files": {"used": server.used, "size": 1000}}})
        if path == "/collection":
            server.collections.append(json.loads(body))
            return self.reply(200, {"result": True, "item": {"_id": 100 + len(server.collections)}})
        if path == "/raindrop/file":
            if server.reject_upload:
                return self.reply(200, {"result": False, "errorMessage": "File is invalid"})
            server.uploads.append(body)
            return self.reply(200, {"result": True, "item": {"_id": len(server.uploads)}})
        server.updates[int(path.split("/")[2])] = json.loads(body)
        return self.reply(200, {"result": True})

    def reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class RaindropTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.library = os.path.join(directory.name, "main.library")
        self.state = os.path.join(directory.name, "state.json")
        os.makedirs(os.path.join(self.library, "images"))
        folders = [{"id": "F1", "name": "parent", "children": [{"id": "F2", "name": "child"}]}]
        with open(os.path.join(self.library, "metadata.json"), "w", encoding="utf-8") as file:
            json.dump({"folders": folders}, file)
        add_item(
            self.library, "A", 'мем "1"', "png", content=b"x" * 300, btime=1636280708874,
            url="https://example.com/a", annotation="note", tags=["art", "art"],
            folders=["GONE", "F1", "F2"],
        )
        add_item(self.library, "B", "clip", "MP4", content=b"x" * 300)
        add_item(self.library, "C", "bad", "exe")
        add_item(self.library, "D", "trashed", "png", isDeleted=True)

        patcher = mock.patch.object(importer.raindrop, "MIN_INTERVAL", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeRaindrop)
        self.server.calls, self.server.uploads, self.server.collections = [], [], []
        self.server.updates, self.server.fail_once, self.server.used = {}, [], 0
        self.server.reject_upload = False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def run_importer(self, execute=True, key="key"):
        args = argparse.Namespace(
            library=self.library, execute=execute, limit=None, max_size_mb=1,
            state=self.state, retry_pause=0, target="raindrop",
        )
        url = f"http://127.0.0.1:{self.server.server_port}"
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = importer.run(args, url, key)
        return code, output.getvalue()

    def test_dry_run_sends_nothing(self):
        code, output = self.run_importer(execute=False)
        self.assertEqual(code, 0)
        self.assertEqual(self.server.calls, [])
        self.assertIn("to import: 2, skipped: 2", output)

    def test_maps_fields(self):
        code, _ = self.run_importer()
        self.assertEqual(code, 0)
        self.assertEqual(self.server.collections, [{"title": "parent"}, {"title": "child", "parent": {"$id": 101}}])
        first = self.server.updates[1]
        self.assertEqual(first["title"], 'мем "1"')
        self.assertEqual(first["tags"], ["art", "child"])
        self.assertEqual(first["note"], "Source: https://example.com/a\n\nnote")
        self.assertEqual(first["collection"], {"$id": 101})
        self.assertTrue(first["created"].startswith("2021-11-07T"))
        self.assertEqual(self.server.updates[2], {"title": "clip", "tags": []})
        self.assertIn(b'filename="A.png"', self.server.uploads[0])

    def test_rerun_uploads_nothing(self):
        self.run_importer()
        self.server.calls.clear()
        code, output = self.run_importer()
        self.assertEqual(code, 0)
        self.assertNotIn(("PUT", "/raindrop/file"), self.server.calls)
        self.assertIn("already imported: 2", output)

    def test_failed_update_resumes_without_a_second_upload(self):
        self.server.fail_once.append("/raindrop/1")
        code, output = self.run_importer()
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)
        code, _ = self.run_importer()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.server.uploads), 2)
        self.assertIn(1, self.server.updates)

    def test_quota_stops_and_a_later_run_resumes(self):
        self.server.used = 600  # room for one 300 byte file
        code, output = self.run_importer()
        self.assertEqual(code, 1)
        self.assertIn("stopped: upload quota reached", output)
        self.assertEqual(len(self.server.uploads), 1)
        self.server.used = 0
        code, output = self.run_importer()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.server.uploads), 2)
        self.assertIn("already imported: 1", output)

    def test_error_inside_a_200_counts_as_failed(self):
        self.server.reject_upload = True
        code, output = self.run_importer()
        self.assertEqual(code, 1)
        self.assertIn("File is invalid", output)
        self.assertEqual(self.server.updates, {})

    def test_bad_token_stops_immediately(self):
        code, output = self.run_importer(key="wrong")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 401", output)
        self.assertEqual(len(self.server.calls), 1)


if __name__ == "__main__":
    unittest.main()
