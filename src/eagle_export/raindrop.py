import os
import time

from .api import ApiError, Client, Stopped
from .eagle import created_at, find_file, note_for, walk_folders

API_URL = "https://api.raindrop.io"
CONTENT_TYPES = {
    "aiff": "audio/aiff",
    "epub": "application/epub+zip",
    "flac": "audio/flac",
    "gif": "image/gif",
    "heic": "image/heic",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "md": "text/markdown",
    "mov": "video/quicktime",
    "mp3": "audio/mpeg",
    "mp4": "video/mp4",
    "pdf": "application/pdf",
    "png": "image/png",
    "txt": "text/plain",
    "wav": "audio/wav",
    "webm": "video/webm",
    "webp": "image/webp",
    "wmv": "video/x-ms-wmv",
}
CREDENTIALS = "RAINDROP_TOKEN"
MAX_SIZE_MB = 100  # the Free plan's limit per file
MAX_TITLE = 1000
MAX_NOTE = 10000
MIN_INTERVAL = 0.5  # the API allows 120 requests a minute


class Raindrop(Client):
    def __init__(self, url, token, pause=2):
        super().__init__(url.rstrip("/") + "/rest/v1", token, pause)
        self.next_request = 0
        self.upload_room = 0
        self.folder_names = {}

    def request(self, method, path, **kwargs):
        time.sleep(max(0, self.next_request - time.monotonic()))
        self.next_request = time.monotonic() + MIN_INTERVAL
        reply = super().request(method, path, **kwargs)
        if reply and reply.get("result") is False:
            raise ApiError(200, reply.get("errorMessage", reply))
        return reply

    def upload(self, path, file_name, content_type):
        size = os.path.getsize(path)
        if size > self.upload_room:
            raise Stopped("upload quota reached for this month, rerun after it resets")
        with open(path, "rb") as file:
            files = {"file": (file_name, file, content_type)}
            raindrop = self.request("PUT", "/raindrop/file", files=files)["item"]
        self.upload_room -= size
        return raindrop


def fields_for(item, state, folder_names):
    """A raindrop has one collection, so further Eagle folders become tags."""
    folder_ids = [folder_id for folder_id in item.get("folders", []) if folder_id in state.data["lists"]]
    tags = set(item.get("tags", []))
    tags.update(folder_names[folder_id] for folder_id in folder_ids[1:])
    body = {"title": item["name"][:MAX_TITLE], "tags": sorted(tags)}
    note = note_for(item)
    # The link is the uploaded file, so the note keeps the source, first to survive the cut.
    if item.get("url"):
        note = f"Source: {item['url']}\n\n{note}".strip()
    if note:
        body["note"] = note[:MAX_NOTE]
    created = created_at(item)
    if created:
        body["created"] = created
    if folder_ids:
        body["collection"] = {"$id": state.data["lists"][folder_ids[0]]}
    return body


def prepare(api, folders, state):
    files = api.request("GET", "/user")["user"]["files"]
    api.upload_room = files["size"] - files["used"]
    for folder, parent_id in walk_folders(folders):
        api.folder_names[folder["id"]] = folder["name"]
        if folder["id"] in state.data["lists"]:
            continue
        payload = {"title": folder["name"]}
        if parent_id:
            payload["parent"] = {"$id": state.data["lists"][parent_id]}
        state.data["lists"][folder["id"]] = api.post_json("/collection", payload)["item"]["_id"]
        state.save()


def import_item(api, folder, item, state):
    entry = state.data["items"].get(item["id"])
    if entry is None:
        ext = item["ext"].lower()
        raindrop = api.upload(find_file(folder, item), f"{item['id']}.{ext}", CONTENT_TYPES[ext])
        entry = {"raindropId": raindrop["_id"], "done": False}
        state.data["items"][item["id"]] = entry
        state.save()

    api.request("PUT", f"/raindrop/{entry['raindropId']}", json=fields_for(item, state, api.folder_names))
    entry["done"] = True
    state.save()
