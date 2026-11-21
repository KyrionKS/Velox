//! Clean Subtitle Tracks - keep only the chosen subtitle languages.

use std::collections::{BTreeMap, BTreeSet};

use async_trait::async_trait;

use crate::config::CleanSubsConfig;
use crate::error::Result;
use crate::media::{MediaInfo, SUBTITLE};

use super::{PipelineContext, Plugin, PluginResult, RemuxPlan};

pub struct CleanSubsPlugin {
    config: CleanSubsConfig,
}

impl CleanSubsPlugin {
    pub fn new(config: CleanSubsConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl Plugin for CleanSubsPlugin {
    fn key(&self) -> &'static str {
        "clean_subs"
    }

    fn name(&self) -> &'static str {
        "Clean Subtitle Tracks"
    }

    fn enabled(&self) -> bool {
        self.config.enabled
    }

    async fn run(&self, media: &MediaInfo, _ctx: &PipelineContext) -> Result<PluginResult> {
        let mut result = PluginResult::new(media.container());

        if !media.is_video() {
            result.note("File is not video");
            return Ok(result);
        }

        if self.config.language.is_empty() {
            result.note(
                "Language/s to keep have not been configured, please configure required options. Skipping this plugin.",
            );
            return Ok(result);
        }

        let languages: Vec<String> = self
            .config
            .language
            .split(',')
            .map(|item| item.trim().to_lowercase())
            .collect();
        let commentary = self.config.commentary;
        let sdh = self.config.sdh;
        let tag_language = self.config.tag_language.trim().to_lowercase();

        let mut drop: BTreeSet<usize> = BTreeSet::new();
        let mut metadata: BTreeMap<usize, Vec<(String, String)>> = BTreeMap::new();

        for stream in media.streams.iter().filter(|s| s.codec_type == SUBTITLE) {
            let index = stream.index;
            let has_language = stream.tags.contains_key("language");
            let language = stream.language();

            if has_language && !languages.iter().any(|value| value == &language) {
                drop.insert(index);
                result.note(format!(
                    "Subtitle stream {index} has unwanted language tag {language}, removing."
                ));
                continue;
            }

            if stream.tags.contains_key("title") {
                let title = stream.title().to_lowercase();
                if commentary && (title.contains("commentary") || title.contains("description")) {
                    drop.insert(index);
                    result.note(format!(
                        "Subtitle stream {index} detected as being descriptive, removing."
                    ));
                    continue;
                }
                if sdh && title.contains("sdh") {
                    drop.insert(index);
                    result.note(format!(
                        "Subtitle stream {index} detected as SDH, removing."
                    ));
                    continue;
                }
            }

            if !tag_language.is_empty() && (!has_language || language.contains("und")) {
                metadata
                    .entry(index)
                    .or_default()
                    .push(("language".to_string(), tag_language.clone()));
                result.note(format!(
                    "Subtitle stream {index} has no language, tagging as {tag_language}."
                ));
            }
        }

        if !drop.is_empty() || !metadata.is_empty() {
            result.process_file = true;
            result.remux = Some(RemuxPlan {
                drop,
                metadata,
                ..Default::default()
            });
            result.requeue = true;
        } else {
            result.note(
                "File doesn't contain subtitle tracks which are unwanted or that require tagging.",
            );
        }
        Ok(result)
    }
}
