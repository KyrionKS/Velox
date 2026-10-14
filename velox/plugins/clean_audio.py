"""Tdarr_Plugin_MC93_Migz3CleanAudio - Migz Clean Audio Streams (v2.4)."""

from __future__ import annotations

from ..media import AUDIO, MediaInfo, stream_language, stream_title, stream_type
from .base import PipelineContext, Plugin, PluginResult, RemuxPlan

# channels -> label used when tagging untitled tracks
TITLE_BY_CHANNELS = {8: "7.1", 6: "5.1", 2: "2.0"}


class CleanAudioPlugin(Plugin):
    key = "clean_audio"
    name = "Migz Clean Audio Streams"
    tdarr_id = "Tdarr_Plugin_MC93_Migz3CleanAudio"
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

        drop: set[int] = set()
        metadata: dict[int, list[tuple[str, str]]] = {}
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

        if drop or metadata:
            result.process_file = True
            result.remux = RemuxPlan(drop=drop, metadata=metadata)
            result.requeue = True
        else:
            result.note(
                "☑ File doesn't contain audio tracks which are unwanted or that require tagging."
            )
        return result
