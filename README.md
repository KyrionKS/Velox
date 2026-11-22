# Velox

Velox cleans up and transcodes video files with `ffmpeg`. It remuxes streams,
cleans audio and subtitle tracks, standardises audio, encodes video (hardware
where available) and verifies the result — with a desktop app built in Rust,
Tauri 2 and Svelte 5.

The original Python/PySide6 implementation is archived at the git tag
`python-final`.

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

Successful runs leave a `<name>-report.txt` next to the result. Velox uses it
to recognise files that were already processed (a report older than the media
file, or one documenting an error/cancellation, does not count). Those files
are shown as **Already processed** and skipped by default; uncheck *Skip
already processed files* in the Run panel to process them again, or use
`--process-again` on the CLI. Reports can be opened directly from the queue
and from **Activity → Reports**.

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
`⌘,` settings, `⌘1` library, `⌘2` activity, `F1` about, `Esc` close settings.

**About Velox** (info button or app menu) shows the app/Tauri version, platform,
ffmpeg/ffprobe paths and versions, and the active config path.

## Requirements

- Rust 1.82+
- Node.js 20+
- `ffmpeg` / `ffprobe` in your `PATH` (`brew install ffmpeg` on macOS)
- macOS 11+ for the desktop app; Linux and Windows builds are supported

Velox also looks in `/opt/homebrew/bin`, `/usr/local/bin`, `/opt/local/bin`
and `/usr/bin` when `PATH` is minimal (for example when launched from Finder).
If the tools are missing, the app shows a banner with a **Locate ffmpeg…**
button; the resolved paths are written back when settings are saved or a run
starts.

An invalid or unreadable `config.toml` never blocks startup: Velox falls back
to defaults, shows the exact error in a banner, and *Settings → Reload* reads
the file from disk again.

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
`--ffmpeg`, `--ffprobe`, `--init-config`, `--list-plugins`, `-v`, `-q`,
`--process-again`.

## Configuration

The TOML format is compatible with the archived Python version, including the per-user
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
- `general.skip_processed = true` — skip files with a current successful report.

Numeric and boolean values may be quoted (`concurrency = "3"`,
`enable_10bit = "true"`), matching the archived Python version.

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
npm test
```

The core tests cover the ported plugin behaviour, remux argument generation,
configuration compatibility and the pipeline engine. Backend warnings and
errors (for example unknown config keys) are shown in **Activity → Log**.

The end-to-end suite runs the real pipeline against generated ffmpeg samples
(messy stream order, 5.1 E-AC-3 → AAC, already-HEVC, MP4 container change,
output folder, dry run):

```bash
PATH=/tmp/opencode/ffbin:$PATH cargo test -p velox-core --test e2e -- --nocapture
```

A dry run is shown as **planned passes** in the app; outputs and savings are
marked with “—” and no files are written.

On macOS it also exercises `hevc_videotoolbox` in constant-quality mode when
the encoder is available:

```bash
brew install ffmpeg
cargo test -p velox-core --test e2e -- --nocapture
npm run tauri build
```

CI builds the macOS app and runs the same tests on a macOS runner.
