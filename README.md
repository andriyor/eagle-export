# eagle-to-karakeep

Imports an [Eagle](https://en.eagle.cool/) library into [Karakeep](https://github.com/karakeep-app/karakeep). The library is only read, never changed.

## Usage

Requires [uv](https://docs.astral.sh/uv/). Without `--execute` it is a dry run that reports what would be imported.

```bash
uv run eagle-to-karakeep /path/to/main.library
```

Create an API key in Karakeep's settings, then try a few items before importing everything:

```bash
export KARAKEEP_URL=http://your-server:3000
export KARAKEEP_API_KEY=...

uv run eagle-to-karakeep /path/to/main.library --execute --limit 5
uv run eagle-to-karakeep /path/to/main.library --execute
```

| Option | Default | Meaning |
|---|---|---|
| `--execute` | off | Send to Karakeep |
| `--limit N` | all | Import at most N items |
| `--max-size-mb N` | 50 | Skip larger files; match the server's `MAX_ASSET_SIZE_MB` |
| `--state PATH` | `eagle-to-karakeep-state.json` | Progress file |

## What gets imported

| Eagle | Karakeep |
|---|---|
| File | Asset bookmark |
| Name | Title and file name |
| Created date | Created date |
| Source URL | Source URL |
| Annotation and comments | Note |
| Tags | Tags |
| Folders, including nested ones | Lists |

Not imported:

- Ratings, colour palettes and image dimensions
- Smart folders
- Items in Eagle's trash
- Anything other than JPEG, PNG, GIF, WebP and PDF, because Karakeep can't store other types as bookmarks
- Password protection on folders: items in those folders are imported like any others

## Reruns

Karakeep doesn't detect duplicate images, so progress is kept in the state file. Keep that file: reruns then do only what's left and retry failed items, but deleting it imports everything again.

If a response is lost while a bookmark is being created, that item is reported as failed and a rerun can create a duplicate of it.

## Privacy

If your server's AI tagging uses a cloud provider, every imported image is sent to it. Use a local model or turn off AI tagging first if that matters.

## Tests

```bash
uv run python -m unittest discover -s tests
```

The tests run against a fake Karakeep server, so they need no real one.
