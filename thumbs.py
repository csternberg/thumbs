#!/usr/bin/env python3
# thumbs.py - Video thumbnail extractor
# Version: 4.1

import os
import sys
import argparse
import subprocess
import threading
import traceback
from pathlib import Path
import signal
from concurrent.futures import ThreadPoolExecutor

VERSION = "4.1"

DEFAULT_TIMESTAMP = 2.0
STOP = False
MAX_WORKERS = max(1, min(4, os.cpu_count() or 1))

LOG_FILE = "thumbs.log"
ERR_FILE = "thumbs-error.log"

SCAN_ROOT = None  # stable root for -k

# Guards the filename-collision check in extract() and the per-file
# folder creation in outdir(), both of which run from worker threads.
_fs_lock = threading.Lock()
_claimed_paths = set()

# Set from --log in main(). thumbs.log is only written when this is on;
# thumbs-error.log is always written, but only once an error actually
# happens (see reset_err_log()).
LOGGING_ENABLED = False


def reset_err_log():
    try:
        os.remove(ERR_FILE)
    except OSError:
        pass


def log(msg):
    if not LOGGING_ENABLED:
        return
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def log_err(msg):
    with open(ERR_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def safe_print(msg):
    # A video filename can contain characters the console's active code
    # page can't encode (e.g. a non-Latin-1 name on a default Windows
    # cp1252 console); fall back instead of crashing the whole run.
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(msg.encode(enc, errors="replace").decode(enc))


# ------------------------------------------------------------
# Ctrl-C handling
# ------------------------------------------------------------
def sigint(sig, frame):
    global STOP
    STOP = True
    print("\n[!] stop requested...")


signal.signal(signal.SIGINT, sigint)

# ------------------------------------------------------------
# Video formats
# ------------------------------------------------------------
VIDEO_EXT = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm",
    ".mpg", ".mpeg", ".m4v", ".3gp", ".ts", ".m2ts", ".vob", ".ogv"
}


def is_video(p: Path):
    return p.suffix.lower() in VIDEO_EXT


# ------------------------------------------------------------
# ffmpeg/ffprobe argument safety
# ------------------------------------------------------------
def ffmpeg_safe(path):
    # A path starting with "-" (empty folder name, or a file/folder that
    # legitimately starts with a dash) is otherwise parsed by ffmpeg as
    # an option instead of a filename.
    s = str(path)
    return f"./{s}" if s.startswith("-") else s


# ------------------------------------------------------------
# Duration
# ------------------------------------------------------------
def get_duration(p: Path):
    try:
        r = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            ffmpeg_safe(p)
        ], capture_output=True, text=True)

        return float(r.stdout.strip())
    except Exception:
        return None


# ------------------------------------------------------------
# Scaling
# ------------------------------------------------------------
def scale_filter(args):
    if args.x and args.y:
        return f"scale={args.x}:{args.y}"
    if args.x:
        return f"scale={args.x}:-1"
    if args.y:
        return f"scale=-1:{args.y}"
    return None


# ------------------------------------------------------------
# HW accel options
# ------------------------------------------------------------
def hwaccels():
    return [
        ["-hwaccel", "cuda"],
        ["-hwaccel", "qsv"],
        ["-hwaccel", "auto"],
        []
    ]


# ------------------------------------------------------------
# FFmpeg runner
# ------------------------------------------------------------
def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        # e.g. ffmpeg/ffprobe not installed or not on PATH
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


# ------------------------------------------------------------
# OUTDIR (-k preserved, -f per-file)
# ------------------------------------------------------------
def outdir(video, args):
    base = Path(args.output) if args.output else video.parent
    base.mkdir(parents=True, exist_ok=True)

    # -k KEEP STRUCTURE (only meaningful together with -o)
    if args.keep and args.output:
        try:
            rel = video.parent.resolve().relative_to(SCAN_ROOT)
            base = Path(args.output) / rel
        except ValueError:
            base = Path(args.output) / video.parent.name
        base.mkdir(parents=True, exist_ok=True)

    # -f per file folder
    if args.per_file:
        with _fs_lock:
            d = base / f"{video.stem}_thumbs"
            i = 1
            while d.exists():
                d = base / f"{video.stem}_thumbs_{i:02d}"
                i += 1
            d.mkdir(parents=True, exist_ok=True)
        return d

    return base


# ------------------------------------------------------------
# Naming
# ------------------------------------------------------------
def format_timecode(seconds):
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}-{m:02d}-{s:02d}"


def make_name(video, args, timestamp, kind):
    # video.parent.name is "" for videos in the scan root (Path(".").name
    # == ""); fall back to the resolved folder name so -d/-dd never build
    # a name from an empty string.
    folder = video.parent.name or video.parent.resolve().name
    stem = video.stem

    if args.dd:
        name = folder
    elif args.d:
        name = f"{folder}-{stem}"
    else:
        name = stem

    if args.label:
        if kind == "mid":
            name += "(mid)"
        elif kind == "default":
            name += f"({int(timestamp)} sec)"
        else:
            # Multiple thumbnails from one video (-t, -e, -i, -n): label
            # with the timecode so filenames stay distinct and meaningful.
            name += f"_{format_timecode(timestamp)}"

    return name + ".jpg"


# ------------------------------------------------------------
# FFmpeg attempt
# ------------------------------------------------------------
def ffmpeg_attempt(video, out, t, args, seek_mode, hw):
    vf = scale_filter(args)
    vf = f"format=yuv420p,{vf}" if vf else "format=yuv420p"

    video_arg = ffmpeg_safe(video)
    out_arg = ffmpeg_safe(out)

    if seek_mode == "pre":
        cmd = ["ffmpeg", "-y"] + hw + ["-ss", str(t), "-i", video_arg]
    else:
        cmd = ["ffmpeg", "-y"] + hw + ["-i", video_arg, "-ss", str(t)]

    cmd += [
        "-vf", vf,
        "-frames:v", "1",
        "-vsync", "0",
        "-an", "-sn", "-dn",
        out_arg
    ]

    r = run(cmd)
    ok = r.returncode == 0 and out.exists() and out.stat().st_size > 0
    return ok, r.stderr


# ------------------------------------------------------------
# Output path reservation (thread-safe)
# ------------------------------------------------------------
def reserve_output(out, overwrite):
    if overwrite:
        return out

    with _fs_lock:
        candidate = out
        i = 1
        while candidate.exists() or candidate in _claimed_paths:
            candidate = out.with_name(f"{out.stem}_{i:02d}{out.suffix}")
            i += 1
        _claimed_paths.add(candidate)
        return candidate


# ------------------------------------------------------------
# Extract logic
# ------------------------------------------------------------
def extract(video, t, args, od, kind="custom"):
    global STOP
    if STOP:
        return

    od.mkdir(parents=True, exist_ok=True)

    out = reserve_output(od / make_name(video, args, t, kind), args.overwrite)

    last_err = None

    for hw in hwaccels():
        for mode in ("pre", "post"):
            ok, err = ffmpeg_attempt(video, out, t, args, mode, hw)
            if ok:
                msg = f"[+] {out}"
                safe_print(msg)
                log(msg)
                return
            last_err = err

    log_err(f"[FAIL] {video}")
    log_err(last_err or "unknown error")


# ------------------------------------------------------------
# Modes
# ------------------------------------------------------------
def clamp_timestamp(t, dur):
    return min(max(t, 0.0), max(dur - 0.05, 0.0))


def interval(video, dur, step, args, od):
    if step <= 0:
        log_err(f"[SKIP bad -i {step}] {video}")
        return
    t = 0.0
    while t <= dur:
        extract(video, t, args, od)
        t += step


def nframes(video, dur, n, args, od):
    if n <= 0:
        log_err(f"[SKIP bad -n {n}] {video}")
        return
    step = dur / (n + 1)
    for i in range(1, n + 1):
        extract(video, i * step, args, od)


# ------------------------------------------------------------
# PROCESS
# ------------------------------------------------------------
def process(video, args):
    try:
        _process(video, args)
    except Exception:
        # A ThreadPoolExecutor swallows exceptions from submitted tasks
        # unless something calls future.result(); without this, an
        # unexpected bug here would silently drop the video with no
        # trace instead of surfacing in the error log.
        log_err(f"[ERROR] {video}")
        log_err(traceback.format_exc())


def _process(video, args):
    global STOP
    if STOP:
        return

    dur = get_duration(video)
    if not dur:
        log_err(f"[NO DURATION] {video}")
        return

    # skip short videos unless allowed
    if dur < 10 and not args.short:
        log(f"[SKIP <10s] {video}")
        return

    od = outdir(video, args)

    if args.interval is not None:
        interval(video, dur, args.interval, args, od)
        return

    if args.nframes is not None:
        nframes(video, dur, args.nframes, args, od)
        return

    explicit = bool(args.time) or bool(args.end) or args.middle

    # Default 2 sec thumbnail
    if args.dual or not explicit:
        extract(video, DEFAULT_TIMESTAMP, args, od, kind="default")

    # Multiple -t support
    if args.time:
        for value in args.time:
            extract(video, clamp_timestamp(value, dur), args, od)

    # Multiple -e support (timestamp measured from the end)
    if args.end:
        for value in args.end:
            extract(video, clamp_timestamp(dur - value, dur), args, od)

    # Middle frame
    if args.middle:
        extract(video, dur / 2, args, od, kind="mid")


# ------------------------------------------------------------
# SCAN
# ------------------------------------------------------------
def scan(folder, args, recursive):
    global SCAN_ROOT
    SCAN_ROOT = Path(folder).resolve()

    vids = []

    try:
        if recursive:
            for r, _, files in os.walk(folder):
                for name in files:
                    p = Path(r) / name
                    if is_video(p):
                        vids.append(p)
        else:
            for name in os.listdir(folder):
                p = Path(folder) / name
                if p.is_file() and is_video(p):
                    vids.append(p)
    except OSError as e:
        print(f"[!] could not scan {folder}: {e}", file=sys.stderr)
        return

    print(f"[+] {len(vids)} videos")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for v in vids:
            if STOP:
                break
            ex.submit(process, v, args)


# ------------------------------------------------------------
# CLI (case-insensitive)
# ------------------------------------------------------------
def normalize(argv):
    out = []
    i = 0

    switches_with_values = (
        "-o", "-t", "-i", "-n", "-e", "-x", "-y"
    )

    while i < len(argv):
        a = argv[i].lower()
        out.append(a)

        if a in switches_with_values:
            if i + 1 < len(argv):
                out.append(argv[i + 1])
                i += 1

        i += 1

    return out


def parse_args():
    p = argparse.ArgumentParser(add_help=False)

    scope = p.add_mutually_exclusive_group()
    scope.add_argument("-r", action="store_true")
    scope.add_argument("-c", action="store_true")

    p.add_argument("-o", dest="output", type=str)
    p.add_argument("-f", dest="per_file", action="store_true")
    p.add_argument("-k", dest="keep", action="store_true")

    p.add_argument("-l", dest="label", action="store_true")

    naming = p.add_mutually_exclusive_group()
    naming.add_argument("-d", action="store_true")
    naming.add_argument("-dd", action="store_true")

    # MULTIPLE -t / -e SUPPORT
    p.add_argument(
        "-t",
        dest="time",
        type=float,
        action="append",
        help="Timestamp in seconds, can be used multiple times"
    )
    p.add_argument(
        "-e",
        dest="end",
        type=float,
        action="append",
        help="Seconds before the end, can be used multiple times"
    )

    p.add_argument("-2", dest="dual", action="store_true")
    p.add_argument("-m", dest="middle", action="store_true")
    p.add_argument("-i", dest="interval", type=float)
    p.add_argument("-n", dest="nframes", type=int)

    p.add_argument("-x", type=int)
    p.add_argument("-y", type=int)

    p.add_argument("-w", dest="overwrite", action="store_true")
    p.add_argument("-v", action="store_true")

    p.add_argument("--short", action="store_true")
    p.add_argument("--log", action="store_true")

    return p.parse_args(normalize(sys.argv[1:]))


# ------------------------------------------------------------
# HELP
# ------------------------------------------------------------
def print_help():
    print(f"""thumbs.py v{VERSION} - recursive video thumbnail extractor

USAGE
  thumbs.py [options]

Scans the current folder for videos and extracts JPEG thumbnails next to
each video (or into -o / -f / -k as configured). All switches below are
case-insensitive (-D, -R, --SHORT, ... all work the same as lowercase).

SCOPE
  -r            scan current folder and all subfolders (default)
  -c            scan the current folder only

OUTPUT LOCATION
  -o <folder>   write thumbnails to this folder instead of next to the video
  -k            keep the source folder structure under -o
  -f            put each video's thumbnails in its own "<name>_thumbs/" folder

NAMING
  -d            prefix with the folder name: Folder-video.jpg
  -dd           name after the folder only: Folder.jpg
  -l            label multiple thumbnails with their timecode (_hh-mm-ss);
                the default 2s shot and the middle frame use (2 sec) / (mid)
  -w            overwrite existing thumbnails instead of adding _01, _02, ...

WHICH FRAMES (pick one style; -i and -n replace the others)
  (default)     one frame at 2 seconds
  -t <sec>      frame at an explicit timestamp, repeatable: -t 3 -t 9
  -e <sec>      frame <sec> seconds before the end, repeatable
  -2            also produce the default 2s frame alongside -t / -e / -m
  -m            frame at the exact middle of the video
  -i <sec>      one frame every <sec> seconds, start to end
  -n <count>    <count> frames evenly spaced across the video

IMAGE
  -x <width>    scale to this width (aspect kept if -y is omitted)
  -y <height>   scale to this height (aspect kept if -x is omitted)

MISC
  --short       also process videos under 10 seconds (skipped by default)
  --log         write thumbs.log (one line per thumbnail/skip); off by default
  -h            show this help and exit
  -v / -V       show version and exit

thumbs-error.log is only written if something actually fails.
""")


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
def main():
    # A video filename outside the console's code page would otherwise
    # crash any plain print() of it (e.g. default cp1252 on Windows).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    argv = sys.argv[1:]

    if any(a.lower() in ("-h", "--help") for a in argv):
        print_help()
        return

    args = parse_args()

    if args.v:
        print(VERSION)
        return

    if args.interval is not None and args.interval <= 0:
        print("[!] -i must be greater than 0", file=sys.stderr)
        sys.exit(2)
    if args.nframes is not None and args.nframes <= 0:
        print("[!] -n must be greater than 0", file=sys.stderr)
        sys.exit(2)

    global LOGGING_ENABLED
    LOGGING_ENABLED = args.log
    if LOGGING_ENABLED:
        open(LOG_FILE, "w").close()
    reset_err_log()

    scan(Path("."), args, recursive=not args.c)
    print("[done]")


if __name__ == "__main__":
    main()
