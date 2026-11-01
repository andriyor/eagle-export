"""Import an Eagle library into Karakeep, Raindrop or Resurf. The library is only ever read.

    uv run eagle-export ~/Dropbox/main.library              # dry run
    uv run --env-file .env eagle-export ~/Dropbox/main.library --execute --limit 5
    uv run --env-file .env eagle-export ~/Dropbox/main.library --execute
    uv run --env-file .env eagle-export ~/Dropbox/main.library --target raindrop --execute
    uv run eagle-export ~/Dropbox/main.library --target resurf --execute

Karakeep and Raindrop do not dedupe uploads, so progress is kept in the state file: keep it
between runs, and reruns only do what is left.
"""
import argparse
import json
import os
import sys
from collections import Counter

import requests

from . import karakeep, raindrop, resurf
from .api import ApiError, Stopped
from .eagle import find_file, load_items, walk_folders

TARGETS = {
    "karakeep": (karakeep, karakeep.Karakeep),
    "raindrop": (raindrop, raindrop.Raindrop),
    "resurf": (resurf, resurf.Resurf),
}


def skip_reason(folder, item, max_bytes, content_types):
    if item.get("isDeleted"):
        return "in Eagle trash"
    if content_types is not None and item.get("ext", "").lower() not in content_types:
        return f"unsupported type .{item.get('ext')}"
    path = find_file(folder, item)
    if path is None:
        return "file missing"
    if os.path.getsize(path) > max_bytes:
        return "larger than --max-size-mb"
    return None


class State:
    def __init__(self, path):
        self.path = path
        self.data = {"lists": {}, "items": {}}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as file:
                self.data = json.load(file)

    def save(self):
        temporary = f"{self.path}.tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(self.data, file, indent=1)
        os.replace(temporary, self.path)


def run(args, api_url, api_key):
    target, client = TARGETS[args.target]
    with open(os.path.join(args.library, "metadata.json"), encoding="utf-8") as file:
        folders = json.load(file).get("folders", [])
    state = State(args.state)
    max_bytes = args.max_size_mb * 1024 * 1024
    pending, skipped, done = [], Counter(), 0
    for folder, item in load_items(args.library):
        if state.data["items"].get(item["id"], {}).get("done"):
            done += 1
        elif reason := skip_reason(folder, item, max_bytes, target.CONTENT_TYPES):
            skipped[reason] += 1
            print(f"skip: {item.get('name')} ({reason})")
        else:
            pending.append((folder, item))

    print(
        f"already imported: {done}, to import: {len(pending)}, skipped: {sum(skipped.values())}, "
        f"folders: {sum(1 for _ in walk_folders(folders))}"
    )
    for reason, count in skipped.most_common():
        print(f"  {count} {reason}")
    if not args.execute:
        print("dry run: nothing was sent, add --execute to import")
        return 0
    if target.CREDENTIALS and (not api_url or not api_key):
        print(f"set {target.CREDENTIALS}", file=sys.stderr)
        return 2

    api = client(api_url, api_key, args.retry_pause)
    failed = 0
    try:
        target.prepare(api, folders, state)
        batch = pending[: args.limit] if args.limit else pending
        for number, (folder, item) in enumerate(batch, 1):
            try:
                target.import_item(api, folder, item, state)
                print(f"[{number}/{len(batch)}] {item['name']}")
            except (ApiError, OSError, ValueError) as error:
                if getattr(error, "status", None) in (401, 403):
                    raise
                failed += 1
                print(f"[{number}/{len(batch)}] FAILED {item['name']}: {error}", file=sys.stderr)
    except (ApiError, Stopped, requests.RequestException) as error:
        print(f"stopped: {error}", file=sys.stderr)
        return 1
    print(f"finished, failed: {failed}" + (" (rerun to retry them)" if failed else ""))
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("library", help="path to the .library folder")
    parser.add_argument("--target", choices=TARGETS, default="karakeep", help="where to import")
    parser.add_argument("--execute", action="store_true", help="send to the target; without it, only report")
    parser.add_argument("--limit", type=int, help="import at most this many items, for a trial")
    parser.add_argument("--max-size-mb", type=int, help="skip larger files; default 50 for karakeep, 100 for raindrop, 2048 for resurf")
    parser.add_argument("--state", help="progress file; default eagle-to-<target>-state.json")
    parser.add_argument("--retry-pause", type=float, default=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.library = os.path.expanduser(args.library)
    args.max_size_mb = args.max_size_mb or TARGETS[args.target][0].MAX_SIZE_MB
    args.state = args.state or f"eagle-to-{args.target}-state.json"
    if args.target == "resurf":
        return run(args, os.environ.get("RESURF_CLI", resurf.CLI), None)
    if args.target == "raindrop":
        return run(args, raindrop.API_URL, os.environ.get("RAINDROP_TOKEN"))
    return run(args, os.environ.get("KARAKEEP_URL"), os.environ.get("KARAKEEP_API_KEY"))


if __name__ == "__main__":
    sys.exit(main())
