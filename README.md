# Velox

Velox cleans up and transcodes video files with `ffmpeg`. It remuxes streams,
cleans audio and subtitle tracks, standardises audio, encodes video (hardware
where available) and verifies the result — with a desktop app built in Rust,
Tauri 2 and Svelte 5.

This repository is the Rust rewrite of the original Python/PySide6 version.
The Python implementation remains on the `main` branch until the rewrite is
merged; the historical release is tagged `python-final`.

## What it does

The pipeline runs an ordered set of **steps**:

| Order | Key                   | Step                     | What it does                                             |
| ----: | --------------------- | ------------------------ | -------------------------------------------------------- |
| 1     | `reorder_streams`     | Move Video First         | Puts the video stream at the front of the file            |
| 2     | `clean_audio`         | Clean Audio Tracks       | Keeps only the chosen audio languages, tags/drops tracks  |
| 3     | `clean_subs`          | Clean Subtitle Tracks    | Keeps only the chosen subtitle languages, tags/drops      |
| 4     | `standardise_audio`   | Standardise Audio Codec  | Transcodes audio to one codec (keeps bitrate/channels)    |
| 5     | `hevc_gpu`            | Encode Video             | Encodes the video (hardware encoder when available)       |
| 6     | `reorder_all_streams` | Order Streams            | Sorts streams by codec/channels/language/type             |
| 7     | `file_size_check`     | Check File Size          | Errors if the result is outside the configured bounds     |

Consecutive stream-copy steps are merged into a **single ffmpeg pass**, so a
typical file is read and written twice at most (one merged remux and one
transcode). Only the encode step re-encodes.

All work happens in a scratch directory. The original is only replaced after
the whole pipeline succeeded and the output was verified (parses, has video,
duration matches). Set `verify_full_decode` to decode the whole output as well.

## Desktop app

The app is built around a task-first layout rather than a settings dialog:

- **Library** — the queue and a **Run** panel. Pick a quality preset
  (Auto · Compact · Balanced · Archive), see the plan and estimated output
  size, then start. Files show codec/resolution chips, live progress and the
  space saved.
- **Activity** — live log, one row per ffmpeg pass, and the total saving.
- **Settings** — a slide-over sheet with Video, Audio, Tracks, Output and
  Advanced sections, including encoder detection and tool paths.

Keyboard shortcuts: `⌘O` add files, `⌘⇧O` add a folder, `⌘R` start,
`⌘,` settings, `⌘1` library, `⌘2` activity, `Esc` close settings.

## Requirements

- Rust 1.82+
- Node.js 20+
- `ffmpeg` / `ffprobe` in your `PATH` (`brew install ffmpeg` on macOS)
- macOS 11+ for the desktop app; Linux and Windows builds are supported

Velox also looks in `/opt/homebrew/bin`, `/usr/local/bin`, `/opt/local/bin`
and `/usr/bin` when `PATH` is minimal (for example when launched from Finder).
The paths can be changed in *Settings → Advanced → Tools*.

## Build

```bash
npm install
npm run tauri dev      # development app
npm run tauri build    # release bundle (.app/.dmg, AppImage/deb, .msi)
```

Frontend-only preview (no Tauri backend): `npm run dev` and open
`http://localhost:1420/?demo` for the built-in demo queue.

CLI:

```bash
cargo run -p velox-cli -- --info "/path/to/Movie.mkv"
cargo run -p velox-cli -- -n "/path/to/Movie.mkv"   # dry run
cargo run -p velox-cli -- -r -c config.toml "/media"
cargo install --path crates/velox-cli
```

## CLI

The CLI keeps the original flags: `-c/--config`, `-r/--recursive`,
`-n/--dry-run`, `--info`, `--ext`, `--output-dir`, `--temp-dir`,
`--keep-original`, `--no-backup`, `--only`, `--skip`, `--mode`,
`--target-size-mb`, `--bpp`, `-j/--jobs`, `--report`/`--no-report`,
`--ffmpeg`, `--ffprobe`, `--init-config`, `--list-plugins`, `-v`, `-q`.

## Configuration

The TOML format is compatible with the Python version, including the per-user
path (`~/Library/Application Support/Velox/config.toml` on macOS,
`~/.config/velox/config.toml` elsewhere). See `config.example.toml` for all
options.

Key settings:

- `hevc_gpu.bitrate_mode = "quality"` — constant quality (VideoToolbox
  `-q:v` on Apple Silicon); falls back to tier mode elsewhere.
- `hevc_gpu.bits_per_pixel` — tier mode: 0.028 compact, 0.033 balanced,
  0.045 archive.
- `standardise_audio.audio_bitrate_kbps = 0` — keep the source bitrate.
- `general.concurrency = 2` — files processed in parallel.
- `general.temp_dir = "source"` — scratch files stay on the source volume.

## Project layout

```
Velox/
├── Cargo.toml                  # workspace
├── crates/
│   ├── velox-core/             # config, probe, pipeline, plugins, report
│   └── velox-cli/              # clap CLI
├── src-tauri/                  # Tauri commands, state and native menu
├── src/                        # Svelte 5 UI
├── config.example.toml
└── assets/icon.png
```

`velox-core` has no Tauri dependency. ffmpeg is spawned through a
`FfmpegBackend` trait, and pipeline events are emitted through `EventSink`, so
the same engine powers the CLI, the desktop app and tests.

## Tests

```bash
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
npm run check
```

The core tests cover the ported plugin behaviour, remux argument generation,
configuration compatibility and the pipeline engine. The CLI and Tauri
backend are exercised against generated ffmpeg samples during development.
