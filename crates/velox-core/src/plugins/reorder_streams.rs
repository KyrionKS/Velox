//! Move Video First - put the video stream at the front of the file.

use async_trait::async_trait;

use crate::config::ReorderStreamsConfig;
use crate::error::Result;
use crate::media::{MediaInfo, VIDEO};

use super::{PipelineContext, Plugin, PluginResult, RemuxPlan};

pub struct ReorderStreamsPlugin {
    config: ReorderStreamsConfig,
}

impl ReorderStreamsPlugin {
    pub fn new(config: ReorderStreamsConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl Plugin for ReorderStreamsPlugin {
    fn key(&self) -> &'static str {
        "reorder_streams"
    }

    fn name(&self) -> &'static str {
        "Move Video First"
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

        if media.first_stream_type() != VIDEO {
            result.note("Video is not in the first stream");
            result.process_file = true;
            result.remux = Some(RemuxPlan {
                order: Some(media.type_order()),
                ..Default::default()
            });
            result.requeue = true;
            return Ok(result);
        }

        result.note("File has video in first stream");
        result.note("File meets conditions!");
        Ok(result)
    }
}
