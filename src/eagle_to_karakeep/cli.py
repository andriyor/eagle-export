"""Import an Eagle library into Karakeep. The library is only ever read.

    uv run eagle-to-karakeep ~/Dropbox/main.library              # dry run
    uv run --env-file .env eagle-to-karakeep ~/Dropbox/main.library --execute --limit 5
    uv run --env-file .env eagle-to-karakeep ~/Dropbox/main.library --execute

Karakeep does not dedupe assets, so progress is kept in the state file: keep it
between runs, and reruns only do what is left.
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

import requests

from .eagle import find_file, load_items, walk_folders
from .karakeep import ApiError, Karakeep

CONTENT_TYPES = {
    "gif": "image/gif",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "pdf": "application/pdf",
    "png": "image/png",
    "webp": "image/webp",
}
MAX_TITLE = 1000
MAX_LIST_NAME = 100
MAX_LIST_DESCRIPTION = 500


def skip_reason(folder, item, max_bytes):
    if item.get("isDeleted"):
        return "in Eagle trash"
    if item.get("ext", "").lower() not in CONTENT_TYPES:
        return f"unsupported type .{item.get('ext')}"
    path = find_file(folder, item)
    if path is None:
        return "file missing"
    if os.path.getsize(path) > max_bytes:
        return "larger than --max-size-mb"
    return None


def note_for(item):
    parts = [item.get("annotation", "")]
    parts += [comment.get("annotation", "") for comment in item.get("comments", [])]
    return "\n\n".join(part for part in parts if part)


def bookmark_for(item, asset_id):
    ext = item["ext"].lower()
    body = {
        "type": "asset",
        "assetType": "pdf" if ext == "pdf" else "image",
        "assetId": asset_id,
        "title": item["name"][:MAX_TITLE],
        "fileName": f"{item['name']}.{item['ext']}",
        "source": "import",
        "crawlPriority": "low",
    }
    created = item.get("btime") or item.get("modificationTime")
    if created:
        body["createdAt"] = datetime.fromtimestamp(created / 1000, timezone.utc).isoformat()
    if item.get("url"):
        body["sourceUrl"] = item["url"]
    note = note_for(item)
    if note:
        body["note"] = note
    return body


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


def ensure_lists(api, folders, state):
    for folder, parent_id in walk_folders(folders):
        if folder["id"] in state.data["lists"]:
            continue
        payload = {"name": folder["name"][:MAX_LIST_NAME], "icon": "📁", "type": "manual"}
        if parent_id:
            payload["parentId"] = state.data["lists"][parent_id]
        if folder.get("description"):
            payload["description"] = folder["description"][:MAX_LIST_DESCRIPTION]
        state.data["lists"][folder["id"]] = api.post_json("/lists", payload)["id"]
        state.save()


def import_item(api, folder, item, state):
    entry = state.data["items"].get(item["id"])
    if entry is None:
        ext = item["ext"].lower()
        # The server mangles non-ASCII upload names; the bookmark carries the real one.
        asset = api.upload(find_file(folder, item), f"{item['id']}.{ext}", CONTENT_TYPES[ext])
        bookmark = api.post_json("/bookmarks", bookmark_for(item, asset["assetId"]))
        entry = {"bookmarkId": bookmark["id"], "done": False}
        state.data["items"][item["id"]] = entry
        state.save()

    bookmark_id = entry["bookmarkId"]
    tags = sorted(set(item.get("tags", [])))
    if tags:
        payload = {"tags": [{"tagName": tag} for tag in tags]}
        api.post_json(f"/bookmarks/{bookmark_id}/tags", payload)
    for folder_id in item.get("folders", []):
        list_id = state.data["lists"].get(folder_id)
        if list_id:  # items can point at folders deleted in Eagle
            api.request("PUT", f"/lists/{list_id}/bookmarks/{bookmark_id}")
    entry["done"] = True
    state.save()


def run(args, api_url, api_key):
    with open(os.path.join(args.library, "metadata.json"), encoding="utf-8") as file:
        folders = json.load(file).get("folders", [])
    state = State(args.state)
    max_bytes = args.max_size_mb * 1024 * 1024
    pending, skipped, done = [], Counter(), 0
    for folder, item in load_items(args.library):
        if state.data["items"].get(item["id"], {}).get("done"):
            done += 1
        elif reason := skip_reason(folder, item, max_bytes):
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
    if not api_url or not api_key:
        print("set KARAKEEP_URL and KARAKEEP_API_KEY", file=sys.stderr)
        return 2

    api = Karakeep(api_url, api_key, args.retry_pause)
    failed = 0
    try:
        ensure_lists(api, folders, state)
        batch = pending[: args.limit] if args.limit else pending
        for number, (folder, item) in enumerate(batch, 1):
            try:
                import_item(api, folder, item, state)
                print(f"[{number}/{len(batch)}] {item['name']}")
            except (ApiError, OSError, ValueError) as error:
                if getattr(error, "status", None) in (401, 403):
                    raise
                failed += 1
                print(f"[{number}/{len(batch)}] FAILED {item['name']}: {error}", file=sys.stderr)
    except (ApiError, requests.RequestException) as error:
        print(f"stopped: {error}", file=sys.stderr)
        return 1
    print(f"finished, failed: {failed}" + (" (rerun to retry them)" if failed else ""))
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("library", help="path to the .library folder")
    parser.add_argument("--execute", action="store_true", help="send to Karakeep; without it, only report")
    parser.add_argument("--limit", type=int, help="import at most this many items, for a trial")
    parser.add_argument("--max-size-mb", type=int, default=50, help="match the server's MAX_ASSET_SIZE_MB")
    parser.add_argument("--state", default="eagle-to-karakeep-state.json", help="progress file")
    parser.add_argument("--retry-pause", type=float, default=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.library = os.path.expanduser(args.library)
    return run(args, os.environ.get("KARAKEEP_URL"), os.environ.get("KARAKEEP_API_KEY"))


if __name__ == "__main__":
    sys.exit(main())
