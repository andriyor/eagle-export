import json
import os


def walk_folders(folders, parent_id=None):
    """Yield (folder, parent id), parents first so a list can name its parent."""
    for folder in folders:
        yield folder, parent_id
        yield from walk_folders(folder.get("children", []), folder["id"])


def load_items(library):
    images = os.path.join(library, "images")
    for entry in sorted(os.listdir(images)):
        if not entry.endswith(".info"):
            continue
        folder = os.path.join(images, entry)
        with open(os.path.join(folder, "metadata.json"), encoding="utf-8") as file:
            yield folder, json.load(file)


def find_file(folder, item):
    """Name match first; Eagle's stored name can differ in Unicode form on macOS."""
    names = os.listdir(folder)
    expected = f"{item['name']}.{item['ext']}"
    if expected in names:
        return os.path.join(folder, expected)
    media = [n for n in names if n != "metadata.json" and not n.endswith("_thumbnail.png")]
    return os.path.join(folder, media[0]) if len(media) == 1 else None
