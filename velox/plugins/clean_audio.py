"""Clean Audio Tracks - keep only the chosen audio languages."""

from __future__ import annotations

from typing import Any

from ..media import AUDIO, MediaInfo, stream_language, stream_title, stream_type
from .base import PipelineContext, Plugin, PluginResult, RemuxPlan

# channels -> label used when tagging untitled tracks
TITLE_BY_CHANNELS = {8: "7.1", 6: "5.1", 2: "2.0"}


class CleanAudioPlugin(Plugin):
    key = "clean_audio"
    name = "Clean Audio Tracks"
    description = (
        "Keeps only the specified language audio tracks and can tag tracks with "
        "an unknown language."
    )

    def run(self, media: MediaInfo, ctx: PipelineContext) -> PluginResult:
        result = PluginResult(container=media.container)

        if not media.is_video:
            result.note("☒ File is not video")
            return result

        language_input = str(getattr(self.config, "language", "") or "")
        if language_input == "":
            result.note("☒ Language/s options not set, please configure required options. "
                        "Skipping this plugin.")
            return result

        languages = [item.strip() for item in language_input.split(",")]
        commentary = bool(getattr(self.config, "commentary", False))
        tag_language = str(getattr(self.config, "tag_language", "") or "")
        tag_title = bool(getattr(self.config, "tag_title", False))
        default_audio = str(getattr(self.config, "default_audio", "") or "").strip().lower()

        drop: set[int] = set()
        metadata: dict[int, list[tuple[str, str]]] = {}
        kept_audio: list[dict[str, Any]] = []
        audio_total = len(media.streams_of(AUDIO))

        for stream in media.streams:
            if stream_type(stream) != AUDIO:
                continue

            index = int(stream["index"])
            tags = stream.get("tags") or {}
            language = stream_language(stream)
            remove = False

            # Language check (only when the tag actually exists - mirrors the
            # try/catch behaviour of the original).
            if "language" in tags and language not in languages:
                remove = True
                result.note(
                    f"☒ Audio stream {index} has unwanted language tag {language}, removing."
                )

            # Commentary / description / SDH removal.
            if commentary and "title" in tags:
                title = stream_title(stream).lower()
                if any(word in title for word in ("commentary", "description", "sdh")):
                    remove = True
                    result.note(
                        f"☒ Audio stream {index} detected as being descriptive, removing."
                    )

            if remove:
                drop.add(index)
                continue

            kept_audio.append(stream)

            # Tag unknown language.
            if tag_language:
                if "language" not in tags or "und" in language:
                    metadata.setdefault(index, []).append(("language", tag_language))
                    result.note(
                        f"☒ Audio stream {index} detected as having no language, "
                        f"tagging as {tag_language}."
                    )

            # Tag missing title with the channel layout.
            if tag_title and "title" not in tags:
                channels = stream.get("channels")
                label = TITLE_BY_CHANNELS.get(channels if isinstance(channels, int) else None)
                if label:
                    metadata.setdefault(index, []).append(("title", label))
                    result.note(
                        f"☒ Audio stream {index} detected as {channels} channel "
                        f"with no title, tagging."
                    )

        # Failsafe: never remove every audio track.
        if audio_total > 0 and len(drop) == audio_total:
            result.process_file = False
            result.note("☒ Cancelling plugin otherwise all audio tracks would be removed.")
            return result

        # Which kept track should be the default one?
        dispositions: dict[int, str] = {}
        if default_audio and kept_audio:
            chosen = self._pick_default(kept_audio, default_audio)
            if chosen is not None:
                for stream in kept_audio:
                    index = int(stream["index"])
                    dispositions[index] = "default" if index == chosen else "0"
                label = stream_language(
                    next(s for s in kept_audio if int(s["index"]) == chosen)
                ) or "und"
                result.note(
                    f"☒ Marking audio stream {chosen} ({label}) as the default track."
                )

        if drop or metadata or dispositions:
            result.process_file = True
            result.remux = RemuxPlan(drop=drop, metadata=metadata, dispositions=dispositions)
            result.requeue = True
        else:
            result.note(
                "☑ File doesn't contain audio tracks which are unwanted or that require tagging."
            )
        return result

    @staticmethod
    def _pick_default(kept_audio: list[dict[str, Any]], preference: str) -> int | None:
        """Return the absolute index of the track that should be default."""
        if preference == "first":
            return int(kept_audio[0]["index"])
        if preference == "best":
            best = max(kept_audio, key=lambda s: int(s.get("channels") or 0))
            return int(best["index"])
        # otherwise treat it as a language code
        for stream in kept_audio:
            language = stream_language(stream)
            if language == preference or (preference == "und" and not language):
                return int(stream["index"])
        return None
