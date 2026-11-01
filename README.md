# eagle-to-karakeep

Imports an [Eagle](https://en.eagle.cool/) library into [Karakeep](https://github.com/karakeep-app/karakeep) or [Raindrop](https://raindrop.io/). The library is only read, never changed.

## Usage

Requires [uv](https://docs.astral.sh/uv/). Without `--execute` it is a dry run that reports what would be imported.

```bash
uv run eagle-to-karakeep /path/to/main.library
```

Create an API key in Karakeep's settings and put it in `.env`, which git ignores:

```bash
cp .env.example .env
```

Then try a few items before importing everything:

```bash
uv run --env-file .env eagle-to-karakeep /path/to/main.library --execute --limit 5
uv run --env-file .env eagle-to-karakeep /path/to/main.library --execute
```

| Option | Default | Meaning |
|---|---|---|
| `--target NAME` | `karakeep` | `karakeep` or `raindrop` |
| `--execute` | off | Send to the target |
| `--limit N` | all | Import at most N items |
| `--max-size-mb N` | 50 for Karakeep, 100 for Raindrop | Skip larger files; for Karakeep, match the server's `MAX_ASSET_SIZE_MB`. Use 300 for Raindrop Pro |
| `--state PATH` | `eagle-to-<target>-state.json` | Progress file; keep a separate one per target |

## Raindrop

Open an application in Raindrop's [integration settings](https://app.raindrop.io/settings/integrations), copy its test token to `RAINDROP_TOKEN` in `.env`, then run with `--target raindrop`:

```bash
uv run eagle-to-karakeep /path/to/main.library --target raindrop
uv run --env-file .env eagle-to-karakeep /path/to/main.library --target raindrop --execute --limit 5
uv run --env-file .env eagle-to-karakeep /path/to/main.library --target raindrop --execute
```

Raindrop limits uploads per month: 100 MB on Free and 10 GB on Pro. When the next file doesn't fit, the import stops; run it again after the quota resets to continue. The import sends about two requests a second to stay within Raindrop's rate limit.

| Eagle | Raindrop |
|---|---|
| File | Uploaded file |
| Name | Title |
| Created date | Created date |
| Source URL | First line of the note |
| Annotation and comments | Note |
| Tags | Tags |
| First folder, including nested ones | Collection |
| Any other folders | Tags |

Also not imported to Raindrop: folder descriptions, and anything Raindrop can't store. It accepts JPEG, PNG, GIF, WebP, HEIC, MP4, MOV, WMV, WebM, MP3, WAV, AIFF, FLAC, EPUB, PDF, Markdown and text files.

## What gets imported to Karakeep

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

Neither service is relied on to detect duplicate files, so progress is kept in the state file. Keep that file: reruns then do only what's left and retry failed items, but deleting it imports everything again.

If a response is lost while a bookmark or raindrop is being created, that item is reported as failed and a rerun can create a duplicate of it.

## Privacy

If your server's AI tagging uses a cloud provider, every imported image is sent to it. Use a local model or turn off AI tagging first if that matters.

## Tests

```bash
uv run python -m unittest discover -s tests
```

The tests run against fake Karakeep and Raindrop servers, so they need no real ones.
