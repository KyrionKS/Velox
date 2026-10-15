"""Velox command line interface."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__
from .config import DEFAULT_ORDER, VeloxConfig, load_config, save_config
from .errors import VeloxError
from .events import ProgressObserver, fmt_bytes
from .ffmpeg import FFmpegTools
from .logging_setup import get_logger, setup_logging
from .pipeline import Pipeline, RunReport

DEFAULT_EXTENSIONS = {
    "mkv",
    "mp4",
    "m4v",
    "avi",
    "mov",
    "ts",
    "m2ts",
    "mts",
    "webm",
    "wmv",
    "flv",
    "mpg",
    "mpeg",
    "vob",
    "mp2",
}


class _CliObserver(ProgressObserver):
    """Prefixes per-pass size savings with the file name (parallel-friendly)."""

    def __init__(self, logger, source: str | Path | None = None) -> None:
        self.log = logger
        self.source = Path(source) if source is not None else None

    def on_pass_finished(self, event) -> None:
        prefix = f"{self.source.name}: " if self.source is not None else ""
        self.log.info("   ✓ %s%s", prefix, event.describe())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="velox",
        description=(
            "Velox - clean up and transcode video files with ffmpeg "
            "(remux/stream-copy, audio & subtitle cleanup, standardise audio, "
            "encode video, size check)."
        ),
    )
    parser.add_argument("paths", nargs="*", help="Files and/or directories to process.")
    parser.add_argument("-c", "--config", help="Path to a Velox TOML config file.")
    parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into directories.")
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would be done without running ffmpeg.",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Only print stream information for the given files and exit.",
    )
    parser.add_argument(
        "--ext",
        help=f"Comma separated extensions to include for directories "
        f"(default: {','.join(sorted(DEFAULT_EXTENSIONS))}).",
    )
    parser.add_argument(
        "--output-dir", help="Write results here and leave the originals untouched."
    )
    parser.add_argument(
        "--temp-dir",
        help="Scratch directory for temporary files (default: next to each source "
        "file, i.e. on the same volume/NAS).",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not keep a copy of the original file (this is the default).",
    )
    parser.add_argument(
        "--keep-original",
        action="store_true",
        help="Keep a '.velox-orig' backup of the original (uses extra disk space).",
    )
    parser.add_argument("--only", help="Only run these plugin keys (comma separated).")
    parser.add_argument("--skip", help="Skip these plugin keys (comma separated).")
    parser.add_argument(
        "--mode",
        choices=["tier", "size", "quality"],
        help="Video bitrate mode for this run (overrides the config).",
    )
    parser.add_argument(
        "--target-size-mb",
        type=float,
        help="Target output size in MiB for this run (implies --mode size).",
    )
    parser.add_argument(
        "--bpp",
        type=float,
        help="Bits per pixel for tier mode for this run (implies --mode tier).",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        help="Process this many files in parallel (default from the config).",
    )
    parser.add_argument("--ffmpeg", help="Path to the ffmpeg binary (overrides config).")
    parser.add_argument("--ffprobe", help="Path to the ffprobe binary (overrides config).")
    parser.add_argument(
        "--init-config",
        metavar="PATH",
        help="Write a default config file to PATH and exit.",
    )
    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="List the processing steps and exit.",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--version", action="version", version=f"velox {__version__}")
    return parser


def _gather_files(paths: list[str], recursive: bool, extensions: set[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            for candidate in sorted(path.glob(pattern)):
                if candidate.is_file() and candidate.suffix.lstrip(".").lower() in extensions:
                    resolved = candidate.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        files.append(candidate)
        elif path.is_file():
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(path)
        else:
            get_logger().warning("Path does not exist, skipping: %s", path)
    return files


def _apply_plugin_selection(config: VeloxConfig, only: str | None, skip: str | None) -> None:
    if only:
        wanted = {k.strip() for k in only.split(",") if k.strip()}
        for key in DEFAULT_ORDER:
            config.plugin(key).enabled = key in wanted
    if skip:
        unwanted = {k.strip() for k in skip.split(",") if k.strip()}
        for key in unwanted:
            if key in DEFAULT_ORDER:
                config.plugin(key).enabled = False


def _print_plugins() -> None:
    from .plugins import PLUGIN_MAP

    for key in DEFAULT_ORDER:
        cls = PLUGIN_MAP.get(key)
        print(f"{key:<22} {cls.name if cls else '?'}")


def _run_info(ffmpeg: FFmpegTools, files: list[Path]) -> int:
    exit_code = 0
    for path in files:
        try:
            media = ffmpeg.probe(path)
        except VeloxError as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        print(f"\n{path}")
        print(f"  {media.summary()}")
        print(media.stream_table())
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_plugins:
        _print_plugins()
        return 0

    if args.init_config:
        save_config(VeloxConfig(), args.init_config)
        print(f"Wrote default config to {args.init_config}")
        return 0

    verbosity = 1 if args.verbose else (-1 if args.quiet else 0)
    log = setup_logging(verbosity)

    try:
        config = load_config(args.config) if args.config else VeloxConfig()
    except (OSError, RuntimeError) as exc:
        log.error("Could not load config: %s", exc)
        return 2

    if args.output_dir:
        config.general.output_dir = args.output_dir
    if args.temp_dir:
        config.general.temp_dir = args.temp_dir
    if args.no_backup:
        config.general.keep_original = False
    if args.keep_original:
        config.general.keep_original = True
    if args.ffmpeg:
        config.general.ffmpeg_path = args.ffmpeg
    if args.ffprobe:
        config.general.ffprobe_path = args.ffprobe
    if args.mode:
        config.hevc_gpu.bitrate_mode = args.mode
    if args.target_size_mb is not None:
        config.hevc_gpu.target_size_mb = args.target_size_mb
        config.hevc_gpu.bitrate_mode = "size"
    if args.bpp is not None:
        config.hevc_gpu.bits_per_pixel = args.bpp
        config.hevc_gpu.bitrate_mode = "tier"
    if args.jobs is not None:
        config.general.concurrency = max(1, args.jobs)
    _apply_plugin_selection(config, args.only, args.skip)

    if not args.paths:
        parser.print_help()
        return 1

    extensions = DEFAULT_EXTENSIONS
    if args.ext:
        extensions = {e.strip().lstrip(".").lower() for e in args.ext.split(",") if e.strip()}

    files = _gather_files(args.paths, args.recursive, extensions)

    # --info uses ffprobe only.
    if args.info:
        ffmpeg = FFmpegTools(config.general.ffmpeg_path, config.general.ffprobe_path, log)
        try:
            ffmpeg.resolve()
        except VeloxError as exc:
            log.error("%s", exc)
            return 2
        return _run_info(ffmpeg, files)

    if not files:
        log.warning("No matching files found.")
        return 1

    try:
        pipeline = Pipeline(config, log)
    except VeloxError as exc:
        log.error("%s", exc)
        return 2

    active = [p.name for p in pipeline.enabled_plugins]
    log.info("Velox %s - pipeline: %s", __version__, " → ".join(active) or "(empty)")
    if args.dry_run:
        log.info("Dry run: no files will be modified.")
    jobs = max(1, int(getattr(config.general, "concurrency", 1) or 1))
    workers = min(jobs, max(1, len(files)))
    log.info("Files to process: %d (%d at a time)", len(files), workers)

    def run_one(file: Path) -> RunReport:
        return pipeline.process(file, dry_run=args.dry_run, observer=_CliObserver(log, file))

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            reports = list(pool.map(run_one, files))
    else:
        reports = [run_one(file) for file in files]

    # ------------------------------------------------------------------ summary
    modified = sum(1 for r in reports if r.ok and r.modified)
    skipped = sum(1 for r in reports if r.skipped)
    failed = [r for r in reports if not r.ok]

    log.info("")
    total_original = sum(r.original_bytes for r in reports if r.modified)
    total_final = sum(r.final_bytes for r in reports if r.modified)
    if total_original:
        saved = total_original - total_final
        pct = saved / total_original * 100
        log.info(
            "Space: %s → %s (saved %s, %.1f%%)",
            fmt_bytes(total_original),
            fmt_bytes(total_final),
            fmt_bytes(saved),
            pct,
        )
    log.info(
        "Done. %d processed, %d modified, %d skipped, %d failed.",
        len(reports),
        modified,
        skipped,
        len(failed),
    )
    for report in failed:
        log.error("  ✗ %s: %s", report.source.name, report.error)

    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
