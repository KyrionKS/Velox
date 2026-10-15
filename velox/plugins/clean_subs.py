"""Clean Subtitle Tracks - keep only the chosen subtitle languages."""

from __future__ import annotations

from ..media import SUBTITLE, MediaInfo, stream_language, stream_title, stream_type
from .base import PipelineContext, Plugin, PluginResult, RemuxPlan


class CleanSubsPlugin(Plugin):
    key = "clean_subs"
    name = "Clean Subtitle Tracks"
    description = (
        "Keeps only the specified language subtitle tracks and can tag tracks "
        "with an unknown language."
    )

    def run(self, media: MediaInfo, ctx: PipelineContext) -> PluginResult:
        result = PluginResult(container=media.container)

        if not media.is_video:
            result.note("☒ File is not video")
            return result

        language_input = str(getattr(self.config, "language", "") or "")
        if language_input == "":
            result.note(
                "☒ Language/s to keep have not been configured, please configure "
                "required options. Skipping this plugin."
            )
            return result

        languages = [item.strip() for item in language_input.split(",")]
        commentary = bool(getattr(self.config, "commentary", False))
        sdh = bool(getattr(self.config, "sdh", True))
        tag_language = str(getattr(self.config, "tag_language", "") or "")

        drop: set[int] = set()
        metadata: dict[int, list[tuple[str, str]]] = {}

        for stream in media.streams:
            if stream_type(stream) != SUBTITLE:
                continue

            index = int(stream["index"])
            tags = stream.get("tags") or {}
            language = stream_language(stream)

            # Unwanted language.
            if "language" in tags and language not in languages:
                drop.add(index)
                result.note(
                    f"☒ Subtitle stream {index} has unwanted language tag {language}, removing."
                )
                continue

            # Commentary / description / SDH removal.
            if commentary and "title" in tags:
                title = stream_title(stream).lower()
                if "commentary" in title or "description" in title:
                    drop.add(index)
                    result.note(
                        f"☒ Subtitle stream {index} detected as being descriptive, removing."
                    )
                    continue
                if sdh and "sdh" in title:
                    drop.add(index)
                    result.note(f"☒ Subtitle stream {index} detected as SDH, removing.")
                    continue

            # Tag unknown language.
            if tag_language and ("language" not in tags or "und" in language):
                metadata.setdefault(index, []).append(("language", tag_language))
                result.note(
                    f"☒ Subtitle stream {index} has no language, tagging as {tag_language}."
                )

        if drop or metadata:
            result.process_file = True
            result.remux = RemuxPlan(drop=drop, metadata=metadata)
            result.requeue = True
        else:
            result.note(
                "☑ File doesn't contain subtitle tracks which are unwanted or that require tagging."
            )
        return result
