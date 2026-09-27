# thumbs

A single-file Python CLI that scans a folder tree for video files and
extracts JPEG thumbnails with `ffmpeg`/`ffprobe`. No install step beyond
having `ffmpeg` and `ffprobe` on your `PATH`.

```bash
python thumbs.py [options]
```

It always scans the **current directory**. Point a terminal at the folder
you want thumbnails for and run it from there.

Videos under 10 seconds are skipped by default (use `--short` to include
them), and up to 4 videos are processed concurrently.

All switches are case-insensitive: `-D`, `-d`, `-R`, `--SHORT` all work the
same as their lowercase form.

## Scope

| Switch | Meaning |
|---|---|
| `-r` | scan the current folder and all subfolders (default) |
| `-c` | scan the current folder only |

## Output location

| Switch | Meaning |
|---|---|
| `-o <folder>` | write thumbnails to `<folder>` instead of next to each video |
| `-k` | keep the source folder structure under `-o` |
| `-f` | put each video's thumbnails in their own `<name>_thumbs/` folder |

## Naming

| Switch | Meaning |
|---|---|
| `-d` | prefix with the folder name: `Folder-video.jpg` |
| `-dd` | name after the folder only: `Folder.jpg` |
| `-l` | label multiple thumbnails with their timecode (`_hh-mm-ss`); the default 2s shot and the middle frame use `(2 sec)` / `(mid)` instead |
| `-w` | overwrite existing thumbnails instead of adding `_01`, `_02`, ... |

`-d` and `-dd` are mutually exclusive.

## Which frames

Pick one style — `-i` and `-n` replace the timestamp-based options below them:

| Switch | Meaning |
|---|---|
| *(default)* | one frame at 2 seconds |
| `-t <sec>` | frame at an explicit timestamp, repeatable: `-t 3 -t 9` |
| `-e <sec>` | frame `<sec>` seconds before the end, repeatable |
| `-2` | also produce the default 2s frame alongside `-t` / `-e` / `-m` |
| `-m` | frame at the exact middle of the video |
| `-i <sec>` | one frame every `<sec>` seconds, start to end |
| `-n <count>` | `<count>` frames evenly spaced across the video |

## Image

| Switch | Meaning |
|---|---|
| `-x <width>` | scale to this width (aspect kept if `-y` is omitted) |
| `-y <height>` | scale to this height (aspect kept if `-x` is omitted) |

## Misc

| Switch | Meaning |
|---|---|
| `--short` | also process videos under 10 seconds (skipped by default) |
| `-h` | show help and exit |
| `-v` | show version and exit |

## Examples

```bash
# Default: one 2s thumbnail per video, recursively, next to each video
python thumbs.py

# One thumbnail per video, current folder only, named after its folder
python thumbs.py -c -dd

# A frame at 3s and one 10s before the end, labeled with their timecode
python thumbs.py -t 3 -e 10 -l

# A filmstrip of 8 frames per video, scaled to 320px wide, into out/
python thumbs.py -n 8 -x 320 -o out -k
```

## Logs

Each run truncates and rewrites two files in the current directory:

- `thumbs.log` — one line per extracted thumbnail (and skipped short videos)
- `thumbs-error.log` — failures, with the last `ffmpeg`/`ffprobe` error output

## Requirements

- Python 3.8+
- `ffmpeg` and `ffprobe` available on `PATH`
