"""Runs the importer against a fake Karakeep. uv run python -m unittest"""
import argparse
import contextlib
import io
import json
import os
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import eagle_to_karakeep as importer


class FakeKarakeep(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def handle_request(self):
        server = self.server
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        path = self.path.removeprefix("/api/v1")
        server.calls.append((self.command, path))
        if self.headers["Authorization"] != "Bearer key":
            return self.reply(401, {})
        if path in server.fail_once:
            server.fail_once.remove(path)
            return self.reply(500, {"error": "boom"})
        if path in server.busy_once:
            server.busy_once.remove(path)
            return self.reply(503, {"error": "busy"})
        if path == "/assets":
            server.uploads.append(body)
            return self.reply(200, {"assetId": f"asset{len(server.uploads)}"})
        if path == "/bookmarks":
            server.bookmarks.append(json.loads(body))
            if server.drop_response:
                server.drop_response = False
                self.close_connection = True
                return None
            return self.reply(201, {"id": f"bookmark{len(server.bookmarks)}"})
        if path == "/lists":
            server.lists.append(json.loads(body))
            return self.reply(201, {"id": f"list{len(server.lists)}"})
        if path.endswith("/tags"):
            server.tags.setdefault(path.split("/")[2], []).extend(
                tag["tagName"] for tag in json.loads(body)["tags"]
            )
            return self.reply(200, {"attached": []})
        return self.reply(204, None)

    def reply(self, status, payload):
        data = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def add_item(library, item_id, name, ext, content=b"data", **metadata):
    folder = os.path.join(library, "images", f"{item_id}.info")
    os.makedirs(folder)
    with open(os.path.join(folder, f"{name}.{ext}"), "wb") as file:
        file.write(content)
    with open(os.path.join(folder, f"{name}_thumbnail.png"), "wb") as file:
        file.write(b"thumb")
    with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as file:
        json.dump({"id": item_id, "name": name, "ext": ext, **metadata}, file)


class ImportTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.library = os.path.join(directory.name, "main.library")
        self.state = os.path.join(directory.name, "state.json")
        os.makedirs(os.path.join(self.library, "images"))
        folders = [
            {"id": "F1", "name": "parent", "description": "about", "children": [{"id": "F2", "name": "child"}]},
        ]
        with open(os.path.join(self.library, "metadata.json"), "w", encoding="utf-8") as file:
            json.dump({"folders": folders}, file)
        add_item(
            self.library, "A", 'мем "1"', "png", btime=1636280708874, url="https://example.com/a",
            annotation="note", comments=[{"annotation": "comment"}], tags=["art", "art", "cute"],
            folders=["F1", "F2", "GONE"],
        )
        add_item(self.library, "B", "doc", "PDF")
        add_item(self.library, "C", "clip", "mp4")
        add_item(self.library, "D", "trashed", "png", isDeleted=True)
        add_item(self.library, "E", "huge", "png", content=b"x" * (2 * 1024 * 1024))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeKarakeep)
        self.server.calls, self.server.uploads, self.server.bookmarks = [], [], []
        self.server.lists, self.server.tags, self.server.fail_once = [], {}, []
        self.server.busy_once, self.server.drop_response = [], False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def run_importer(self, execute=True, limit=None, key="key"):
        args = argparse.Namespace(
            library=self.library, execute=execute, limit=limit, max_size_mb=1,
            state=self.state, retry_pause=0,
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
        self.assertFalse(os.path.exists(self.state))
        self.assertIn("to import: 2, skipped: 3", output)

    def test_maps_fields_and_skips_what_karakeep_rejects(self):
        code, _ = self.run_importer()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.server.bookmarks), 2)
        first, second = self.server.bookmarks
        self.assertEqual(first["assetType"], "image")
        self.assertEqual(first["title"], 'мем "1"')
        self.assertEqual(first["sourceUrl"], "https://example.com/a")
        self.assertEqual(first["note"], "note\n\ncomment")
        self.assertTrue(first["createdAt"].startswith("2021-11-07T"))
        self.assertEqual(second["assetType"], "pdf")
        self.assertEqual(self.server.tags, {"bookmark1": ["art", "cute"]})
        self.assertEqual(self.server.lists[1]["parentId"], "list1")
        self.assertEqual(self.server.lists[0]["description"], "about")
        puts = [path for method, path in self.server.calls if method == "PUT"]
        self.assertEqual(puts, ["/lists/list1/bookmarks/bookmark1", "/lists/list2/bookmarks/bookmark1"])
        self.assertTrue(re.search(rb'filename="A\.png"', self.server.uploads[0]))

    def test_rerun_creates_no_duplicates(self):
        self.run_importer()
        self.server.calls.clear()
        code, output = self.run_importer()
        self.assertEqual(code, 0)
        self.assertEqual(self.server.calls, [])
        self.assertIn("already imported: 2", output)

    def test_limit_then_resume(self):
        self.run_importer(limit=1)
        self.assertEqual(len(self.server.bookmarks), 1)
        self.run_importer()
        self.assertEqual(len(self.server.bookmarks), 2)
        self.assertEqual(len(self.server.lists), 2)

    def test_failed_tagging_resumes_without_a_second_bookmark(self):
        self.server.fail_once.append("/bookmarks/bookmark1/tags")
        code, output = self.run_importer()
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)
        self.assertEqual(self.server.tags, {})
        code, _ = self.run_importer()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.server.bookmarks), 2)
        self.assertEqual(self.server.tags, {"bookmark1": ["art", "cute"]})

    def test_busy_server_is_retried(self):
        self.server.busy_once += ["/assets", "/bookmarks", "/lists"]
        code, _ = self.run_importer()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.server.bookmarks), 2)
        self.assertEqual(len(self.server.lists), 2)

    def test_lost_create_response_is_not_retried(self):
        self.server.drop_response = True
        code, output = self.run_importer()
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)
        created = [call for call in self.server.calls if call == ("POST", "/bookmarks")]
        self.assertEqual(len(created), 2)  # the dropped one, then item B, no retry of A

    def test_unreachable_server_is_reported(self):
        self.server.shutdown()
        self.server.server_close()
        code, output = self.run_importer()
        self.assertEqual(code, 1)
        self.assertIn("stopped:", output)

    def test_bad_key_stops_immediately(self):
        code, output = self.run_importer(key="wrong")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 401", output)
        self.assertEqual(len(self.server.calls), 1)


if __name__ == "__main__":
    unittest.main()
