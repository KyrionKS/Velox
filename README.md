# Velox

Velox is a standalone Python port of a Tdarr classic-plugin stack. It runs the
same stream-cleanup / remux / transcode steps locally with `ffmpeg` — no Tdarr
server or node required. Runs on macOS (and Linux/Windows).

The name of the pipeline steps, their inputs and their semantics are kept close
to the original Tdarr plugins so behaviour is familiar:

| Order | Velox key             | Tdarr plugin                                                       |
| ----: | --------------------- | ------------------------------------------------------------------ |
| 1     | `reorder_streams`     | `Tdarr_Plugin_lmg1_Reorder_Streams`                                |
| 2     | `clean_audio`         | `Tdarr_Plugin_MC93_Migz3CleanAudio`                                |
| 3     | `clean_subs`          | `Tdarr_Plugin_MC93_Migz4CleanSubs`                                 |
| 4     | `standardise_audio`   | `Tdarr_Plugin_00td_action_standardise_audio_stream_codecs`         |
| 5     | `hevc_gpu`            | `Tdarr_Plugin_AC_HEVC_GPU`                                         |
| 6     | `reorder_all_streams` | `Tdarr_Plugin_00td_action_re_order_all_streams_v2`                 |
| 7     | `file_size_check`     | `Tdarr_Plugin_a9he_New_file_size_check`                            |

## What it does

1. **Reorder Streams** – moves the video stream to the front (needed so later
   tools detect the codec).
2. **Clean Audio** – keeps only the configured audio languages, optionally
   drops commentary/description tracks and tags untagged tracks.
3. **Clean Subtitles** – keeps only the configured subtitle languages,
   optionally drops commentary/SDH tracks and tags untagged tracks.
4. **Standardise Audio** – transcodes every audio track that is not in the
   target codec (keeping bitrate/channels).
5. **HEVC GPU** – transcodes the video to HEVC/H.264, using a hardware encoder
   when available (on macOS: `hevc_videotoolbox`), otherwise optionally CPU.
6. **Reorder All Streams V2** – sorts streams by codec/channels/language/type.
7. **New File Size Check** – errors if the result is outside the configured
   size bounds relative to the original.

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

A native macOS window with three tabs:

- **Files** — add files or a whole folder with the native Finder dialog.
- **Settings** — a simple *Basic* tab (quality preset, bitrate mode, audio,
  subtitles, encoder, output) plus an *Advanced* tab (bitrate limits, size
  bounds, per-step enable/disable, ffmpeg location).
- **Results** — one row per ffmpeg pass with the space it saved, plus a total.

The window shows live progress — the current file (x of y), the running step
with percentage and an estimated time left, and an overall bar — plus a
collapsible *Details* log. *Dry run* previews without writing anything; *Stop*
cancels safely (the original is never left half-processed). Settings are stored
in `~/Library/Application Support/Velox/config.toml`.

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
- `clean_audio.default_audio = "eng"` — which kept audio track becomes the
  container/player default (`""` = unchanged, `"first"`, `"best"`, or a
  language code)
- `standardise_audio.audio_codec = "aac"`, `audio_bitrate_kbps = 256`
  (caps audio so it can't eat the video budget)
- `hevc_gpu.encoder = "hevc_videotoolbox"` (Apple Silicon)
- `hevc_gpu.bitrate_mode = "tier"`, `bits_per_pixel = 0.033`
- `hevc_gpu.hardware_decode = true` (VideoToolbox decode)

### One setting for movies *and* series (1080p through 4K)

A fixed "1.5 GB per file" target only makes sense for feature films — applying
it to a 45-minute episode would waste bits. Velox therefore defaults to
**tier mode**, which sizes the bitrate from the *resolution and framerate*
(`bits_per_pixel * width * height * fps`) instead of a fixed size. The source
resolution is never changed, so 4K stays 4K and simply gets ~4x the bitrate of
1080p:

| Content (bpp 0.033)    | Bitrate   | Result |
| ---------------------- | --------- | ------ |
| 1080p24 feature (2 h)  | ~1.6 Mbps | ~1.5 GB |
| 1080p24 episode (45 m) | ~1.6 Mbps | ~0.6 GB |
| 720p24 episode (45 m)  | ~0.7 Mbps | ~0.26 GB |
| 2160p24 feature (2 h)  | ~6.6 Mbps | ~5.9 GB |
| 2160p24 episode (45 m) | ~6.6 Mbps | ~2.2 GB |

Quality stays consistent because every file gets the same bits-per-pixel.
Change one number to shift the whole library:

- `bits_per_pixel = 0.03` – smaller files
- `bits_per_pixel = 0.045` – more quality

The other modes:

- `bitrate_mode = "size"` with `target_size_mb = 1500` — fixed size per file,
  ideal when you want a specific size for a movie. Override per run with
  `--target-size-mb 1500` (or `--mode size`).
- `bitrate_mode = "quality"` — constant quality via VideoToolbox `-q:v`
  (Apple Silicon only; Velox falls back to tier mode elsewhere), with a
  resolution-aware `-maxrate` ceiling. Usually the best quality per byte, but
  the size is not predictable.

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
