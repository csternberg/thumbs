#!/usr/bin/env python3
# thumbs.py - Video thumbnail extractor
# Version: 3.11 (multi -t support)

import os
import sys
import argparse
import subprocess
from pathlib import Path
import signal
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

VERSION = "3.11"

DEFAULT_TIMESTAMP = 2.0
STOP = False
MAX_WORKERS = max(1, min(4, multiprocessing.cpu_count()))

LOG_FILE = "thumbs.log"
ERR_FILE = "thumbs-error.log"

SCAN_ROOT = None  # stable root for -k

# ------------------------------------------------------------
# Logging (overwrite per run)
# ------------------------------------------------------------
open(LOG_FILE, "w").close()
open(ERR_FILE, "w").close()


def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def log_err(msg):
    with open(ERR_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


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
# Duration
# ------------------------------------------------------------
def get_duration(p: Path):
    try:
        r = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(p)
        ], capture_output=True, text=True)

        return float(r.stdout.strip())
    except Exception:
        return None


# ------------------------------------------------------------
# Scaling
# ------------------------------------------------------------
def scale_filter(args):
    x = getattr(args, "x", None)
    y = getattr(args, "y", None)

    if x and y:
        return f"scale={x}:{y}"
    if x:
        return f"scale={x}:-1"
    if y:
        return f"scale=-1:{y}"
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
    return subprocess.run(cmd, capture_output=True, text=True)


# ------------------------------------------------------------
# OUTDIR (-k preserved)
# ------------------------------------------------------------
def outdir(video, args):
    if getattr(args, "output", None):
        base = Path(args.output)
    else:
        base = video.parent

    base.mkdir(parents=True, exist_ok=True)

    # -k KEEP STRUCTURE
    if getattr(args, "keep", False) and getattr(args, "output", None):
        try:
            rel = video.parent.resolve().relative_to(SCAN_ROOT)
            base = Path(args.output) / rel
        except Exception:
            base = Path(args.output) / video.parent.name

    # -f per file folder
    if getattr(args, "per_file", False):
        d = base / f"{video.stem}_thumbs"
        i = 1
        while d.exists():
            d = base / f"{video.stem}_thumbs_{i:02d}"
            i += 1
        d.mkdir(parents=True, exist_ok=True)
        return d

    base.mkdir(parents=True, exist_ok=True)
    return base


# ------------------------------------------------------------
# Naming
# ------------------------------------------------------------
def make_name(video, args, timestamp=None, tag=""):
    folder = video.parent.name
    stem = video.stem

    name = stem

    if getattr(args, "d", False):
        name = f"{folder}-{stem}"
    elif getattr(args, "dd", False):
        name = folder

    # label formatting
    if getattr(args, "label", False):
        if tag == "mid":
            name += "(mid)"
        elif timestamp is not None:
            name += f"({int(timestamp)} sec)"

    return name + ".jpg"


# ------------------------------------------------------------
# FFmpeg attempt
# ------------------------------------------------------------
def ffmpeg_attempt(video, out, t, args, seek_mode, hw):
    vf = scale_filter(args)
    vf = f"format=yuv420p,{vf}" if vf else "format=yuv420p"

    if seek_mode == "pre":
        cmd = ["ffmpeg", "-y"] + hw + ["-ss", str(t), "-i", str(video)]
    else:
        cmd = ["ffmpeg", "-y"] + hw + ["-i", str(video), "-ss", str(t)]

    cmd += [
        "-vf", vf,
        "-frames:v", "1",
        "-vsync", "0",
        "-an", "-sn", "-dn",
        str(out)
    ]

    r = run(cmd)
    ok = r.returncode == 0 and out.exists() and out.stat().st_size > 0
    return ok, r.stderr


# ------------------------------------------------------------
# Extract logic
# ------------------------------------------------------------
def extract(video, t, tag, args, od):
    global STOP
    if STOP:
        return

    od.mkdir(parents=True, exist_ok=True)

    out = od / make_name(video, args, t, tag)

    if not getattr(args, "overwrite", False):
        i = 1
        base = out
        while out.exists():
            out = base.with_name(f"{base.stem}_{i:02d}.jpg")
            i += 1

    last_err = None

    for hw in hwaccels():
        for mode in ["pre", "post"]:
            ok, err = ffmpeg_attempt(video, out, t, args, mode, hw)
            if ok:
                msg = f"[+] {out}"
                print(msg)
                log(msg)
                return
            last_err = err

    log_err(f"[FAIL] {video}")
    log_err(last_err or "unknown error")


# ------------------------------------------------------------
# Modes
# ------------------------------------------------------------
def interval(video, dur, step, args, od):
    t = 0
    while t <= dur:
        extract(video, t, str(int(t)), args, od)
        t += step


def nframes(video, dur, n, args, od):
    step = dur / (n + 1)
    for i in range(1, n + 1):
        extract(video, i * step, str(int(i * step)), args, od)


# ------------------------------------------------------------
# PROCESS
# ------------------------------------------------------------
def process(video, args):
    global STOP
    if STOP:
        return

    dur = get_duration(video)
    if not dur:
        log_err(f"[NO DURATION] {video}")
        return

    # skip short videos unless allowed
    if dur < 10 and not getattr(args, "short", False):
        log(f"[SKIP <10s] {video}")
        return

    od = outdir(video, args)

    if getattr(args, "interval", None):
        interval(video, dur, args.interval, args, od)
        return

    if getattr(args, "nframes", None):
        nframes(video, dur, args.nframes, args, od)
        return

    # Default 2 sec thumbnail
    if getattr(args, "dual", False) or (
        not args.time and not getattr(args, "middle", False)
    ):
        extract(video, DEFAULT_TIMESTAMP, "2s", args, od)

    # Multiple -t support
    if args.time:
        for t_value in args.time:
            t = min(t_value, max(dur - 0.05, 0))
            extract(video, t, "t", args, od)

    # Middle frame
    if getattr(args, "middle", False):
        extract(video, dur / 2, "mid", args, od)


# ------------------------------------------------------------
# SCAN
# ------------------------------------------------------------
def scan(folder, args, recursive):
    global SCAN_ROOT
    SCAN_ROOT = Path(folder).resolve()

    vids = []

    if recursive:
        for r, _, f in os.walk(folder):
            for x in f:
                p = Path(r) / x
                if is_video(p):
                    vids.append(p)
    else:
        for x in os.listdir(folder):
            p = Path(folder) / x
            if is_video(p):
                vids.append(p)

    print(f"[+] {len(vids)} videos")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for v in vids:
            if STOP:
                break
            ex.submit(process, v, args)


# ------------------------------------------------------------
# CLI (case-insensitive preserved)
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

    p.add_argument("-r", action="store_true")
    p.add_argument("-c", action="store_true")

    p.add_argument("-o", dest="output", type=str)
    p.add_argument("-f", dest="per_file", action="store_true")
    p.add_argument("-k", dest="keep", action="store_true")

    p.add_argument("-l", dest="label", action="store_true")
    p.add_argument("-d", action="store_true")
    p.add_argument("-dd", action="store_true")

    # MULTIPLE -t SUPPORT
    p.add_argument(
        "-t",
        dest="time",
        type=float,
        action="append",
        help="Can be used multiple times"
    )

    p.add_argument("-2", dest="dual", action="store_true")
    p.add_argument("-m", dest="middle", action="store_true")
    p.add_argument("-i", dest="interval", type=float)
    p.add_argument("-n", dest="nframes", type=int)
    p.add_argument("-e", dest="end", type=float)

    p.add_argument("-x", type=int)
    p.add_argument("-y", type=int)

    p.add_argument("-w", dest="overwrite", action="store_true")
    p.add_argument("-v", action="store_true")

    p.add_argument("--short", action="store_true")

    return p.parse_args(normalize(sys.argv[1:]))


# ------------------------------------------------------------
# HELP
# ------------------------------------------------------------
def help():
    print(f"""
thumbs.py v{VERSION}

-r recursive
-o output folder (optional)
-k keep folder structure

-l label timestamps (2 sec, mid)
-f per-file folders
-d / -dd naming modes

-t time (can be repeated, e.g. -t 3 -t 9)
-2 default + time
-m middle
-i interval
-n frames

-x width
-y height

-w overwrite
--short include <10 sec videos
-v version
""")


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
def main():
    args = parse_args()

    if args.v:
        print(VERSION)
        return

    scan(Path("."), args, recursive=True)
    print("[✓] done")


if __name__ == "__main__":
    main()