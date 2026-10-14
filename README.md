# Velox

Velox cleans up and transcodes video files with `ffmpeg` — remux/stream-copy,
audio & subtitle cleanup, audio standardisation, video encoding and a size
check. Runs on macOS (Apple Silicon optimized) and Linux/Windows.

The pipeline runs an ordered set of **steps**, each with a clear name:

| Order | Key                   | Step                     | What it does                                             |
| ----: | --------------------- | ------------------------ | -------------------------------------------------------- |
| 1     | `reorder_streams`     | Move Video First         | Puts the video stream at the front of the file            |
| 2     | `clean_audio`         | Clean Audio Tracks       | Keeps only the chosen audio languages, tags/drops tracks  |
| 3     | `clean_subs`          | Clean Subtitle Tracks    | Keeps only the chosen subtitle languages, tags/drops      |
| 4     | `standardise_audio`   | Standardise Audio Codec  | Transcodes audio to one codec (keeps bitrate/channels)    |
| 5     | `hevc_gpu`            | Encode Video             | Encodes the video (hardware encoder when available)       |
| 6     | `reorder_all_streams` | Order Streams            | Sorts streams by codec/channels/language/type             |
| 7     | `file_size_check`     | Check File Size          | Errors if the result is outside the configured size bounds|

## What it does

1. **Move Video First** – ensures the video stream leads (needed so later
   tools detect the codec correctly).
2. **Clean Audio Tracks** – keeps only the configured audio languages,
   optionally drops commentary/description tracks and tags untagged tracks.
3. **Clean Subtitle Tracks** – keeps only the configured subtitle languages,
   optionally drops commentary/SDH tracks and tags untagged tracks.
4. **Standardise Audio Codec** – transcodes every audio track that is not in
   the target codec (keeping bitrate/channels).
5. **Encode Video** – encodes the video to the target codec, using a hardware
   encoder when available (on macOS: `hevc_videotoolbox`), otherwise optionally
   CPU.
6. **Order Streams** – sorts streams by codec/channels/language/type.
7. **Check File Size** – errors if the result is outside the configured size
   bounds relative to the original.

All the stream-copy steps (1, 2, 3, 4 and 6) are **merged into a single
`ffmpeg` pass**, so a file is read and written as few times as possible. Only
step 5 re-encodes and needs its own pass. The original file is only touched
once the whole pipeline succeeded.

## Requirements

- Python 3.11+
- `ffmpeg` / `ffprobe` in your `PATH` (`brew install ffmpeg` on macOS)

## Install

```bash
cd Velox
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Or run it directly without installing:

```bash
python3 -m velox --help
```

## Usage

```bash
# Preview what would happen (no files written)
python3 -m velox -n "/path/to/Movie.mkv"

# Process a single file (uses built-in defaults)
python3 -m velox "/path/to/Movie.mkv"

# Process a whole folder recursively with a config
python3 -m velox -r -c config.toml "/path/to/library"

# Inspect the streams of a file
python3 -m velox --info "/path/to/Movie.mkv"

# Run only some plugins
python3 -m velox --skip hevc_gpu "/path/to/Movie.mkv"

# Write results elsewhere, leave originals untouched
python3 -m velox --output-dir ./out "/path/to/Movie.mkv"

# Create a config file to edit
python3 -m velox --init-config config.toml
```

### Safety & disk space

Velox processes everything in a scratch folder and only touches the original
once the whole pipeline succeeded. By default the original is then **replaced
and the backup removed**, so after a successful encode only the new (smaller)
file remains — no leftovers. Use `--keep-original` to keep a `.velox-orig`
copy, or `--output-dir DIR` to leave the originals untouched entirely.

## Desktop app (PySide6)

A native macOS window built around a **sidebar** (Files · Settings · Results)
with the actions in a toolbar, following the current macOS layout conventions:
the toolbar is unified with the title bar, the controls use the native system
appearance (accent colour, dark mode, Liquid Glass), and the app menu has
**Settings… (⌘,)** and **About Velox**. An app icon ships in `assets/`.

- **Toolbar** — Add Files / Add Folder, and Start / Dry Run / Stop.
- **Files** — a table with **one row per file** (progress, current step, result).
  Several files are processed **in parallel** (`Parallel files`, default 2).
- **Settings** — Basic and Advanced, left-aligned and scrollable so nothing is
  cut off: quality preset, bitrate mode, audio (incl. default track),
  subtitles, encoder, output, and advanced limits / performance / per-step
  toggles.
- **Results** — one row per ffmpeg pass with the space it saved, plus a total.

The always-visible status strip shows the current file (x of y), the running
step with percentage and an estimated time left, and an overall bar, with a
collapsible *Details* log. *Dry run* previews without writing anything; *Stop*
cancels safely (the original is never left half-processed). The window size is
remembered, and settings live in
`~/Library/Application Support/Velox/config.toml`.

### Installing / starting on macOS

```bash
# one-time: creates .venv and installs Velox + PySide6
./setup.command                 # double-click in Finder

# start the app
./Velox.command                 # double-click in Finder

# optional: build Velox.app for a real double-clickable app (no Terminal)
./make_app.command
```

If you launch from the Finder, macOS does not provide your shell `PATH`, so
Velox also looks in `/opt/homebrew/bin`, `/usr/local/bin` and `/usr/bin` for
ffmpeg, and can locate it for you from the app if needed.

Prefer no GUI? The CLI is unchanged: `velox --help`.

## Configuration

See `config.example.toml` for the recommended Apple Silicon setup and every
option with comments. Key settings:

- `clean_audio.language = "eng,und"`
- `general.concurrency = 2` — how many files are processed at the same time
  (`--jobs N` on the CLI; 1 = sequential). On Apple Silicon 2 is a good default;
  the VideoToolbox engine serialises beyond a few concurrent transcodes.
- `clean_audio.default_audio = "eng"` — which kept audio track becomes the
  container/player default (`""` = unchanged, `"first"`, `"best"`, or a
  language code)
- `standardise_audio.audio_codec = "aac"`, `audio_bitrate_kbps = 256`
  (caps audio so it can't eat the video budget)
- `hevc_gpu.encoder = "hevc_videotoolbox"` (Apple Silicon)
- `hevc_gpu.bitrate_mode = "quality"`, `quality = 50` (VideoToolbox `-q:v`, 1-100)
- `hevc_gpu.hardware_decode = true` (VideoToolbox decode)

### Choosing the size/quality mode

The default is **constant quality** via VideoToolbox `-q:v` (Apple Silicon only;
Velox falls back to tier mode elsewhere). It is the encoder's native quality
control and gives the best quality per byte — the file size adapts to the
content, so it works equally for a feature film and a short episode.

- `-q:v` scale is **1–100, higher is better** (there is no `-crf`/`-cq` in
  VideoToolbox). Recommended range **45–65**; 55–65 is "excellent"
  (VMAF ~95–98), 45–50 "good" (VMAF ~90–93). The default `50` favours small
  files. A resolution-aware `-maxrate` ceiling keeps complex scenes in check.
- Speed is essentially unaffected by `-q:v` (hardware encoding) — the real
  trade-off is quality vs. file size.

The alternatives:

- `bitrate_mode = "tier"` — resolution & framerate aware ABR
  (`bits_per_pixel * width * height * fps`), i.e. a **predictable size** that
  scales for movies and episodes:

  | Content (bpp 0.033)    | Bitrate   | Result |
  | ---------------------- | --------- | ------ |
  | 1080p24 feature (2 h)  | ~1.6 Mbps | ~1.5 GB |
  | 1080p24 episode (45 m) | ~1.6 Mbps | ~0.6 GB |
  | 2160p24 feature (2 h)  | ~6.6 Mbps | ~5.9 GB |

- `bitrate_mode = "size"` with `target_size_mb = 1500` — fixed size per file.
  Override per run with `--target-size-mb 1500` (or `--mode size`).

Velox never encodes above the source bitrate, so already-small files are not
bloated, and the `file_size_check` step guards against surprises
(`upper_bound`, plus optional `min_size_mb`/`max_size_mb`).

### Apple Silicon

Velox is tuned for Apple Silicon: `hevc_videotoolbox` (hardware encode),
optional hardware decode, Main10 (10-bit), spatial AQ, and the Apple-only
constant-quality mode are all enabled by default. The encoder-quality flags
live in `velox/plugins/hevc_gpu.py` (`HevcGpuPlugin._video_encode_args`).

### NAS / external drives (no SSD wear)

By default all temporary data is written **next to the source file**, i.e. on
the same volume — when your media lives on a NAS, the scratch files and the
intermediate remuxes stay on the NAS and never touch the Mac's SSD. Because the
final step is then a plain rename on the same volume, it costs no extra copy.
The scratch directory is printed at the start of each file:

```
Scratch directory: /Volumes/media/Movies/.velox-1a2b3c
```

- Default: `temp_dir = "source"` (same folder as each file).
- Point it somewhere specific (e.g. a NAS scratch share):
  `temp_dir = "/Volumes/scratch/velox"` or `temp_dir = "{source_dir}/.velox"`.
  Override per run with `--temp-dir`.
- If the scratch directory ends up on a *different* volume than the file, Velox
  warns and falls back to copy+delete for the final move (so it still works, it
  is just slower).

`--output-dir` is the only mode that writes elsewhere by design.

> Note: the stream-copy steps are merged into a single pass, so a typical file
> is read/written twice at most (one merged remux + one transcode); if no
> transcode is needed it is a single pass. Only the transcode step re-encodes.

## Project layout

```
Velox/
├── setup.command / Velox.command / make_app.command   # macOS launchers
├── config.example.toml
├── pyproject.toml
├── velox/
│   ├── cli.py            # argparse entry point
│   ├── config.py         # typed config + TOML load/save
│   ├── events.py         # progress/event layer (shared by CLI + GUI)
│   ├── ffmpeg.py         # ffmpeg/ffprobe wrappers, progress + encoder probing
│   ├── media.py          # ffprobe data model
│   ├── pipeline.py       # engine (runs plugins, merges remuxes, commits)
│   ├── plugins/          # one module per ported plugin
│   └── gui/              # PySide6 desktop app
│       ├── app.py, main_window.py
│       ├── settings.py, results.py, worker.py, presets.py
└── tests/
```

## Roadmap

- Watch-folder mode (process new files automatically).
- Fully standalone `.app` bundle (PyInstaller/py2app) including ffmpeg.
