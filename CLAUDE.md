# thumbs

A single-file Python CLI (`thumbs.py`) that scans the current directory
tree for video files and extracts JPEG thumbnails via `ffmpeg`/`ffprobe`.
No package structure, no dependencies beyond the standard library plus
`ffmpeg`/`ffprobe` on `PATH`.

## Running it

```bash
python thumbs.py [options]
```

Always scans `.` (the CLI has no positional "folder" argument). See
`python thumbs.py -h` or [README.md](README.md) for the full switch list.

## Layout

Everything lives in `thumbs.py`, top to bottom in execution order:

- CLI parsing: `normalize()` lowercases switches (case-insensitivity) before
  handing off to `argparse` in `parse_args()`.
- Per-video pipeline: `scan()` walks the tree → `process()` decides which
  timestamps to extract for one video → `extract()` picks the output path
  and shells out to `ffmpeg` via `ffmpeg_attempt()`, retrying across
  `hwaccels()` and seek modes until one succeeds.
- `scan()` dispatches `process()` calls into a 4-worker `ThreadPoolExecutor`
  — videos are processed concurrently, so anything touching shared
  filesystem state (output filenames, per-file folders) must go through
  `_fs_lock` / `_claimed_paths` in `reserve_output()` and `outdir()`.

## Known sharp edges (fixed once already — don't reintroduce)

- `video.parent.name` is `""` for videos sitting directly in the scanned
  root (`Path(".").name == ""`). `make_name()` falls back to
  `video.parent.resolve().name` — don't use `video.parent.name` directly
  elsewhere without the same fallback, or `-d`/`-dd` naming breaks for the
  most common case (videos at the top level).
- Any output/input path that could start with `-` (empty folder name, or a
  file/folder literally named with a leading dash) must go through
  `ffmpeg_safe()` before being placed in an `ffmpeg`/`ffprobe` argv list —
  otherwise ffmpeg parses the filename as an unrecognized option and the
  whole extraction fails silently (falls through every hwaccel/seek-mode
  retry, logs to `thumbs-error.log`).
- The output-filename collision check (avoid overwriting an existing
  thumbnail unless `-w`) is check-then-create and runs from multiple
  threads. It must stay inside `_fs_lock` (see `reserve_output()`) — outside
  the lock, concurrent videos that hash to the same name (most likely with
  `-dd`, where the name is folder-only) can race and silently clobber each
  other's output.
- Don't print non-ASCII characters (e.g. a checkmark) to stdout in `main()`
  — this is a Windows-first tool and the default `cp1252` console encoding
  will crash on a completed run. `main()` also calls
  `sys.stdout.reconfigure(errors="replace")` up front and `extract()` uses
  `safe_print()` instead of `print()` for anything that echoes a filename,
  since video filenames themselves can contain characters outside the
  console's code page.
- `process()` is a thin try/except wrapper around `_process()`. Keep new
  per-video logic in `_process()` (or something it calls) rather than
  adding it directly to `process()` — a `ThreadPoolExecutor` submit()
  silently drops exceptions unless something calls `future.result()`, so
  without that wrapper an unexpected bug would drop a video with zero
  trace instead of landing in `thumbs-error.log`.
- `log()` (the `thumbs.log` writer) is gated behind the module-level
  `LOGGING_ENABLED` flag, set from `--log` in `main()`. `thumbs.log` should
  never be created unless `--log` was passed. `log_err()` has no such gate
  — `thumbs-error.log` is meant to always appear when something fails,
  with no switch needed — but `reset_err_log()` removes any stale copy at
  the start of every run so its presence always reflects the latest run.

## Testing changes

There's no test suite. To verify a change manually, generate throwaway
clips with `ffmpeg`'s `lavfi` source (no real media needed) and run
`thumbs.py` against them from a scratch directory — never against the repo
root itself, since it writes `thumbs.log` / `thumbs-error.log` and
thumbnails into the current directory:

```bash
mkdir -p /tmp/thumbs_test/videos/sub && cd /tmp/thumbs_test/videos
ffmpeg -y -f lavfi -i "color=c=red:s=320x240:d=20" -pix_fmt yuv420p video1.mp4 -loglevel error
python /path/to/thumbs.py -c -d -l -w
```

Test at least one video directly in the scan root (empty `parent.name`
case) and one in a subfolder, since several past bugs only showed up for
root-level videos.
