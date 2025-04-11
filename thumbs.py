import os
import subprocess
import sys
from pathlib import Path

# Version information
__version__ = "1.5.3"

# Function to check if the given file exists and has a valid video extension
def is_valid_video(file_path):
    video_extensions = [".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".mpg"]
    return file_path.suffix.lower() in video_extensions

# Function to generate a unique file path for output (to avoid overwriting files)
def build_unique_path(base_path, suffix):
    output_path = base_path.with_name(base_path.name + suffix + ".jpg")
    count = 1
    while output_path.exists():
        output_path = base_path.with_name(base_path.name + suffix + f"_{count:02}.jpg")
        count += 1
    return output_path

# Function to extract a frame from the video at a given timestamp
def extract_frame(video_path, timestamp, suffix=""):
    video = Path(video_path)
    if not video.exists():
        print(f"[!] File not found: {video}")
        return

    # If the timestamp is 1.5 seconds, we use the default filename without a suffix
    if timestamp == 1.5:
        suffix = ""  # No suffix for 1.5s
    else:
        suffix = f"({int(timestamp)}s)"  # Add timestamp in seconds for non-default times

    jpg_path = build_unique_path(video.with_suffix(''), suffix)

    cmd = [
        "ffmpeg",
        "-ss", str(timestamp),
        "-i", str(video),
        "-frames:v", "1",
        "-q:v", "2"
    ]
    cmd.append(str(jpg_path))

    try:
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(f"[?] {video.name} ? {jpg_path.name} @ {timestamp}s")
    except subprocess.CalledProcessError as e:
        print(f"[?] Failed to extract frame @ {timestamp}s from: {video.name}")
        print(f"[ERROR] FFmpeg Error: {e.stderr}")

# Main function that handles command-line arguments and processes files
def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} [options] <video files>")
        print(f"Version: {__version__}")
        print("Options:")
        print("-B                Process all video files in the current directory recursively")
        print("-t <time>         Extract a frame at a specific time in seconds (default: 1.5s)")
        print("-2                Generate two thumbnails at 1.5s and the specified timestamp with -t")
        print("-h                Display this help")
        sys.exit(0)

    # Variables for handling command-line options
    process_dir = False
    time_point = 1.5
    two_thumbnails = False

    # Process command-line arguments
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "-B":
            process_dir = True
        elif arg == "-t" and i + 1 < len(sys.argv):
            time_point = float(sys.argv[i + 1])
        elif arg == "-2":
            two_thumbnails = True
        elif arg == "-h":
            print(f"Usage: {sys.argv[0]} [options] <video files>")
            print(f"Version: {__version__}")
            print("Options:")
            print("-B                Process all video files in the current directory recursively")
            print("-t <time>         Extract a frame at a specific time in seconds (default: 1.5s)")
            print("-2                Generate two thumbnails at 1.5s and the specified timestamp with -t")
            print("-h                Display this help")
            sys.exit(0)

    # If -t 1.5 is specified, ignore the custom timestamp and use the default behavior
    if time_point == 1.5:
        time_point = 1.5
        two_thumbnails = False  # Don't generate the second thumbnail at 1.5s

    if process_dir:
        video_files = [str(f) for f in Path().rglob("*.mp4")]  # Find all .mp4 files recursively
    else:
        video_files = sys.argv[1:]

    # Process each video file
    for video_file in video_files:
        if not is_valid_video(Path(video_file)):
            continue

        if two_thumbnails:
            extract_frame(video_file, 1.5)  # Default frame at 1.5s
            extract_frame(video_file, time_point)  # Custom timestamp frame
        else:
            extract_frame(video_file, time_point)

if __name__ == "__main__":
    main()
