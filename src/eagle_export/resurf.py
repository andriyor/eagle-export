import json
import subprocess

from .api import Stopped
from .eagle import find_file, note_for, walk_folders

CLI = "/Applications/Resurf.app/Contents/Helpers/resurf"
CONTENT_TYPES = None  # Resurf saves any file
CREDENTIALS = None
MAX_SIZE_MB = 2048  # the CLI's limit per file
TIMEOUT = 300
# Every later item would fail the same way.
FATAL = {"interaction_required", "license_gate", "scope_denied", "vault_invalid", "vault_too_new", "vault_unavailable"}


class Resurf:
    def __init__(self, cli, token=None, pause=None):
        self.cli = cli
        self.folder_names = {}

    def run(self, *args):
        try:
            done = subprocess.run(
                [self.cli, *args, "--json", "--non-interactive"],
                capture_output=True, text=True, timeout=TIMEOUT,
            )
        except FileNotFoundError:
            raise Stopped(f"Resurf CLI not found at {self.cli}, install Resurf or set RESURF_CLI") from None
        except subprocess.TimeoutExpired:
            raise ValueError(f"no answer from the Resurf CLI in {TIMEOUT} seconds") from None
        reply = json.loads(done.stdout)
        if reply.get("ok"):
            return reply["data"]
        error = reply["error"]
        message = f"{error['code']}: {error['message']}"
        if error["code"] in FATAL:
            raise Stopped(message)
        raise ValueError(message)


def prepare(api, folders, state):
    api.folder_names = {folder["id"]: folder["name"] for folder, _ in walk_folders(folders)}


def file_args(path, item, folder_names):
    # The joined form keeps a value starting with a dash from being read as an option.
    args = ["file", path, f"--title={item['name']}", "--create-spaces"]
    args += [f"--tag={tag}" for tag in sorted(set(item.get("tags", [])))]
    args += [f"--space={folder_names[i]}" for i in item.get("folders", []) if i in folder_names]
    note = note_for(item)
    if note:
        args.append(f"--description={note}")
    return args


def import_item(api, folder, item, state):
    entry = state.data["items"].get(item["id"])
    if entry is None:
        # Saving the same file twice reuses the capture, so a lost reply can't duplicate it.
        capture = api.run(*file_args(find_file(folder, item), item, api.folder_names))["capture"]
        entry = {"captureId": capture["id"], "done": False}
        state.data["items"][item["id"]] = entry
        state.save()

    url = item.get("url", "")
    if url.startswith(("http://", "https://")):
        api.run("capture", "update", entry["captureId"], f"--source-url={url}")
    entry["done"] = True
    state.save()
