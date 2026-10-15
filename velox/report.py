"""Erzeugt einen gut lesbaren Text-Bericht zu einer verarbeiteten Datei.

Der Bericht wird neben der Zieldatei abgelegt (``<name>-report.txt``) und
fasst zusammen, was mit der Datei passiert ist: Zustand vorher, die einzelnen
Durchläufe mit den konkreten Änderungen, Zustand nachher, die Ersparnis und die
verwendeten Einstellungen. Bei einem Fehler wird der Fehler mit ausgegeben.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from .config import VeloxConfig
from .media import AUDIO, SUBTITLE, VIDEO, MediaInfo, stream_language, stream_type

WIDTH = 62
MINUS = "\u2212"  # echtes Minuszeichen


# --------------------------------------------------------------- Formatierung
def _num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _size(num: float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1000 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{_num(value)} {unit}"
        value /= 1000.0
    return f"{_num(value)} TB"


def _percent(value: float) -> str:
    return f"{_num(value, 1)} %"


def _duration(seconds: float) -> str:
    total = int(max(0.0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _fps(stream: dict[str, Any]) -> str:
    raw = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "")
    if not raw or raw in {"0/0", "0"}:
        return ""
    try:
        num, _, den = raw.partition("/")
        value = float(num) / (float(den) if den else 1.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return ""
    return f"{value:.3f}".rstrip("0").rstrip(".") + "fps" if value else ""


def _is_10bit(stream: dict[str, Any]) -> bool:
    profile = str(stream.get("profile") or "").lower()
    return (
        profile in {"high 10", "main 10"}
        or str(stream.get("bits_per_raw_sample") or "") == "10"
        or str(stream.get("pix_fmt") or "") == "yuv420p10le"
    )


def _channels(count: Any) -> str:
    return {8: "7.1", 6: "5.1", 2: "2.0", 1: "1.0"}.get(count, str(count or ""))


def _is_default(stream: dict[str, Any]) -> bool:
    disposition = stream.get("disposition") or {}
    return bool(disposition.get("default"))


def _rule(char: str = "\u2500") -> str:
    return char * WIDTH


def _heading(title: str) -> list[str]:
    return ["", _rule(), f"  {title}", _rule()]


# ---------------------------------------------------------------- Stream-Tabelle
def _stream_line(stream: dict[str, Any]) -> str:
    kind = stream_type(stream)
    codec = str(stream.get("codec_name") or "?")
    parts: list[str] = []
    if kind == VIDEO:
        if stream.get("width") and stream.get("height"):
            parts.append(f"{stream['width']}x{stream['height']}")
        fps = _fps(stream)
        if fps:
            parts.append(fps)
        if _is_10bit(stream):
            parts.append("10-bit")
    elif kind == AUDIO:
        if stream.get("channels"):
            parts.append(_channels(stream.get("channels")))
        parts.append(stream_language(stream) or "und")
        if _is_default(stream):
            parts.append("(Standard)")
    elif kind == SUBTITLE:
        parts.append(stream_language(stream) or "und")
        if _is_default(stream):
            parts.append("(Standard)")
    detail = "  ".join(parts)
    return f"    #{stream.get('index', '?'):<3} {kind:<9} {codec:<10} {detail}".rstrip()


def _stream_table(media: MediaInfo) -> list[str]:
    return [_stream_line(stream) for stream in media.streams]


def _media_block(media: MediaInfo) -> list[str]:
    counts: dict[str, int] = {}
    for stream in media.streams:
        counts[stream_type(stream)] = counts.get(stream_type(stream), 0) + 1
    summary = " · ".join(
        f"{kind.capitalize()} {counts[kind]}"
        for kind in (VIDEO, AUDIO, SUBTITLE)
        if counts.get(kind)
    )
    lines = [
        (
            f"  Container      {media.container or '?'}        "
            f"Größe {_size(media.size)}        Laufzeit {_duration(media.duration)}"
        )
    ]
    if summary:
        lines.append(f"  Streams        {summary}")
    lines += _stream_table(media)
    return lines


# ------------------------------------------------------------------- Schritte
def _plan_bullets(plan: Any, media: MediaInfo) -> list[str]:
    by_index = {int(s.get("index", -1)): s for s in media.streams}
    bullets: list[str] = []

    for index in sorted(plan.drop):
        stream = by_index.get(index, {})
        kind = {"video": "Video", "audio": "Audio", "subtitle": "Untertitel", "data": "Daten"}.get(
            stream_type(stream), "Spur"
        )
        language = stream_language(stream)
        label = f"{kind}spur #{index}"
        if language and language != "und":
            label += f" ({language})"
        bullets.append(f"{label} entfernt")

    for index, codec in plan.codecs.items():
        stream = by_index.get(index, {})
        old = str(stream.get("codec_name") or "?")
        bitrate = plan.bitrates.get(index)
        tail = f", {bitrate}" if bitrate else ""
        bullets.append(f"Spur #{index}: {old} \u2192 {codec}{tail}")

    for index, value in plan.dispositions.items():
        if value == "default":
            bullets.append(f"Spur #{index} als Standard markiert")

    for index, items in plan.metadata.items():
        for key, value in items:
            bullets.append(f"Spur #{index}: {key}={value}")

    if plan.order is not None:
        bullets.append("Stream-Reihenfolge angepasst")

    return bullets


def _encoder_from_command(command: list[str]) -> str | None:
    for index, token in enumerate(command[:-1]):
        if token == "-c:v":
            return command[index + 1]
    return None


def _step_lines(report: Any, config: VeloxConfig) -> list[str]:
    lines: list[str] = []
    total = len(report.passes)
    for number, event in enumerate(report.passes, 1):
        saved = event.input_bytes - event.output_bytes
        percent = (saved / event.input_bytes * 100.0) if event.input_bytes else 0.0
        sign = "+" if saved < 0 else MINUS
        lines.append(f"  {number}/{total}  {' + '.join(event.steps)}")
        lines.append(
            f"        {_size(event.input_bytes)} \u2192 {_size(event.output_bytes)}"
            f"   ({sign}{_size(abs(saved))}, {_percent(percent)})"
        )
        if event.kind == "transcode":
            encoder = _encoder_from_command(event.command) or config.hevc_gpu.encoder
            lines.append(f"        \u2022 Video neu kodiert ({_mode_label(config, encoder)})")
        elif event.plan is not None and report.original_media is not None:
            for bullet in _plan_bullets(event.plan, report.original_media):
                lines.append(f"        \u2022 {bullet}")
        lines.append("")
    return lines


def _mode_label(config: VeloxConfig, encoder: str | None = None) -> str:
    hevc = config.hevc_gpu
    encoder = encoder or hevc.encoder
    if hevc.bitrate_mode == "quality":
        return f"konstante Qualität {hevc.quality}, {encoder}"
    if hevc.bitrate_mode == "size":
        return f"Zielgröße {_num(hevc.target_size_mb, 0)} MiB, {encoder}"
    return f"automatisch ({_num(hevc.bits_per_pixel, 3)} bpp), {encoder}"


def _settings_block(config: VeloxConfig) -> list[str]:
    hevc = config.hevc_gpu
    verify = "nein"
    if config.general.verify_output:
        verify = "ja (vollständig)" if config.general.verify_full_decode else "ja (einfach)"
    return [
        f"  Modus          {_mode_label(config)}",
        f"  Container      {hevc.container}",
        f"  Prüfung        {verify}",
        (
            f"  Audio          {config.standardise_audio.audio_codec} \u00b7 "
            f"Sprachen {config.clean_audio.language}"
        ),
        f"  Untertitel     Sprachen {config.clean_subs.language}",
    ]


def _result_text(report: Any) -> str:
    if report.cancelled:
        return "abgebrochen"
    if report.error:
        return "Fehler"
    if report.modified:
        return (
            "ersetzt (Original entfernt)"
            if not _keeps_original(report)
            else "ersetzt (Original behalten)"
        )
    return "unverändert"


def _keeps_original(report: Any) -> bool:
    return bool(getattr(report, "kept_original", False))


# ------------------------------------------------------------------- öffentlich
def build_report(report: Any, config: VeloxConfig) -> str:
    """Baut den Berichtstext für einen ``RunReport``."""
    now = datetime.datetime.now().strftime("%d.%m.%Y, %H:%M")
    lines: list[str] = []

    lines.append(_rule("\u2550"))
    lines.append("  VELOX \u2013 Verarbeitungsbericht")
    lines.append(_rule("\u2550"))
    lines.append("")
    lines.append(f"  Datei          {report.source.name}")
    lines.append(f"  Verarbeitet    {now}")
    if report.elapsed:
        lines.append(f"  Dauer          {_duration(report.elapsed)}")
    lines.append(f"  Ergebnis       {_result_text(report)}")

    if report.original_media is not None:
        lines += _heading("VORHER")
        lines += _media_block(report.original_media)

    if report.passes:
        lines += _heading("SCHRITTE")
        lines += _step_lines(report, config)

    if report.final_media is not None and report.modified:
        lines += _heading("NACHHER")
        lines.append(f"  Größe          {_size(report.final_media.size)}")
        lines += _stream_table(report.final_media)

    if report.original_bytes and report.final_bytes and report.modified:
        saved = report.original_bytes - report.final_bytes
        percent = saved / report.original_bytes * 100.0
        lines += _heading("GESAMT")
        lines.append(
            f"  {_size(report.original_bytes)} \u2192 {_size(report.final_bytes)}"
            f"   ({MINUS}{_size(abs(saved))}, {_percent(percent)})"
        )
        lines.append("")
        lines += _settings_block(config)

    if report.error:
        lines += _heading("FEHLER")
        lines.append(f"  {report.error}")

    lines.append("")
    lines.append(_rule("\u2550"))
    lines.append("")
    return "\n".join(lines)


def report_path_for(report: Any) -> Path:
    """Zielpfad des Berichts: neben der Ergebnis- bzw. Quelldatei."""
    base = report.final_path or report.source
    return Path(base).with_name(f"{Path(base).stem}-report.txt")


def write_report(report: Any, config: VeloxConfig) -> Path:
    """Schreibt den Bericht und gibt den Pfad zurück."""
    path = report_path_for(report)
    path.write_text(build_report(report, config), encoding="utf-8")
    return path
