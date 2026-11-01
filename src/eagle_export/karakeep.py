from .api import Client
from .eagle import created_at, find_file, note_for, walk_folders

CONTENT_TYPES = {
    "gif": "image/gif",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "pdf": "application/pdf",
    "png": "image/png",
    "webp": "image/webp",
}
CREDENTIALS = "KARAKEEP_URL and KARAKEEP_API_KEY"
MAX_SIZE_MB = 50
MAX_TITLE = 1000
MAX_LIST_NAME = 100
MAX_LIST_DESCRIPTION = 500


class Karakeep(Client):
    def __init__(self, url, api_key, pause=2):
        super().__init__(url.rstrip("/") + "/api/v1", api_key, pause)

    def upload(self, path, file_name, content_type):
        with open(path, "rb") as file:
            return self.request("POST", "/assets", files={"file": (file_name, file, content_type)})


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
    created = created_at(item)
    if created:
        body["createdAt"] = created
    if item.get("url"):
        body["sourceUrl"] = item["url"]
    note = note_for(item)
    if note:
        body["note"] = note
    return body


def prepare(api, folders, state):
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
