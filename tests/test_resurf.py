"""Runs the importer against a fake Resurf CLI."""
import argparse
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest

from eagle_export import cli as importer
from test_cli import add_item

FAKE_CLI = f"""#!{sys.executable}
import json, os, sys
folder = os.path.dirname(sys.argv[0])
args = sys.argv[1:]
with open(os.path.join(folder, "calls.jsonl"), "a") as file:
    file.write(json.dumps(args) + "\\n")
fail = os.path.join(folder, "fail")
if os.path.exists(fail):
    code = open(fail).read()
    if args[0] == code.split()[0] or len(code.split()) == 1:
        if len(code.split()) == 2:
            os.remove(fail)
        print(json.dumps({{"ok": False, "error": {{"code": code.split()[-1], "message": "nope"}}}}))
        sys.exit(1)
capture = "cap_" + os.path.basename(os.path.dirname(args[1])) if args[0] == "file" else args[2]
print(json.dumps({{"ok": True, "data": {{"capture": {{"id": capture}}}}}}))
"""


class ResurfTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.library = os.path.join(directory.name, "main.library")
        self.state = os.path.join(directory.name, "state.json")
        self.cli = os.path.join(directory.name, "resurf")
        with open(self.cli, "w", encoding="utf-8") as file:
            file.write(FAKE_CLI)
        os.chmod(self.cli, os.stat(self.cli).st_mode | stat.S_IEXEC)
        os.makedirs(os.path.join(self.library, "images"))
        folders = [{"id": "F1", "name": "parent", "children": [{"id": "F2", "name": "child"}]}]
        with open(os.path.join(self.library, "metadata.json"), "w", encoding="utf-8") as file:
            json.dump({"folders": folders}, file)
        add_item(
            self.library, "A", 'мем "1"', "png", url="https://example.com/a", annotation="note",
            tags=["art", "art", "-odd"], folders=["GONE", "F1", "F2"],
        )
        add_item(self.library, "B", "archive", "zip", url="file:///local")
        add_item(self.library, "C", "trashed", "png", isDeleted=True)

    def calls(self):
        path = os.path.join(self.directory, "calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as file:
            return [json.loads(line) for line in file]

    def fail(self, text):
        with open(os.path.join(self.directory, "fail"), "w", encoding="utf-8") as file:
            file.write(text)

    def run_importer(self, execute=True, cli=None):
        args = argparse.Namespace(
            library=self.library, execute=execute, limit=None, max_size_mb=1,
            state=self.state, retry_pause=0, target="resurf",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = importer.run(args, cli or self.cli, None)
        return code, output.getvalue()

    def test_dry_run_runs_nothing(self):
        code, output = self.run_importer(execute=False)
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), [])
        self.assertIn("to import: 2, skipped: 1", output)

    def test_maps_fields(self):
        code, _ = self.run_importer()
        self.assertEqual(code, 0)
        first, update, second = self.calls()
        self.assertEqual(first[0], "file")
        self.assertTrue(first[1].endswith('мем "1".png'))
        self.assertEqual(first[2:9], [
            '--title=мем "1"', "--create-spaces", "---odd".replace("---", "--tag=-"), "--tag=art",
            "--space=parent", "--space=child", "--description=note",
        ])
        self.assertEqual(update[:4], ["capture", "update", "cap_A.info", "--source-url=https://example.com/a"])
        self.assertEqual(second[0], "file")  # B's file:// source can't be stored

    def test_rerun_runs_nothing(self):
        self.run_importer()
        count = len(self.calls())
        code, output = self.run_importer()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls()), count)
        self.assertIn("already imported: 2", output)

    def test_failed_update_resumes_without_saving_the_file_again(self):
        self.fail("capture conflict")
        code, output = self.run_importer()
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)
        code, _ = self.run_importer()
        self.assertEqual(code, 0)
        self.assertEqual(sum(call[0] == "file" for call in self.calls()), 2)

    def test_license_gate_stops_immediately(self):
        self.fail("license_gate")
        code, output = self.run_importer()
        self.assertEqual(code, 1)
        self.assertIn("stopped: license_gate", output)
        self.assertEqual(len(self.calls()), 1)

    def test_missing_cli_stops(self):
        code, output = self.run_importer(cli=os.path.join(self.directory, "missing"))
        self.assertEqual(code, 1)
        self.assertIn("Resurf CLI not found", output)


if __name__ == "__main__":
    unittest.main()
