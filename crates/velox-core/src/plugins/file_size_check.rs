//! Check File Size - validate the result against relative/absolute bounds.

use async_trait::async_trait;

use crate::config::FileSizeCheckConfig;
use crate::error::Result;
use crate::media::MediaInfo;

use super::{PipelineContext, Plugin, PluginResult};

const MIB: f64 = 1024.0 * 1024.0;

pub struct FileSizeCheckPlugin {
    config: FileSizeCheckConfig,
}

impl FileSizeCheckPlugin {
    pub fn new(config: FileSizeCheckConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl Plugin for FileSizeCheckPlugin {
    fn key(&self) -> &'static str {
        "file_size_check"
    }

    fn name(&self) -> &'static str {
        "Check File Size"
    }

    fn mergeable(&self) -> bool {
        false
    }

    fn enabled(&self) -> bool {
        self.config.enabled
    }

    async fn run(&self, media: &MediaInfo, ctx: &PipelineContext) -> Result<PluginResult> {
        let mut result = PluginResult::new(media.container());

        let new_size = media.size as f64;
        let old_size = ctx.original_media.size as f64;
        let new_mb = new_size / MIB;
        let upper = self.config.upper_bound;
        let lower = self.config.lower_bound;
        let min_mb = self.config.min_size_mb;
        let max_mb = self.config.max_size_mb;

        let ratio = if old_size > 0.0 {
            (new_size / old_size * 100.0) as i64
        } else {
            0
        };
        let size_text = format!(
            "New file has size {new_mb:.3} MiB ({ratio}% of the original{})",
            if old_size > 0.0 {
                format!(" {:.3} MiB", old_size / MIB)
            } else {
                String::new()
            }
        );

        if upper > 0.0 && old_size > 0.0 && new_size > (upper / 100.0) * old_size {
            result.error = Some(format!(
                "New file size not within limits. {size_text}. upperBound is {upper}%"
            ));
        } else if lower > 0.0 && old_size > 0.0 && new_size < (lower / 100.0) * old_size {
            result.error = Some(format!(
                "New file size not within limits. {size_text}. lowerBound is {lower}%"
            ));
        } else if max_mb > 0.0 && new_mb > max_mb {
            result.error = Some(format!(
                "New file size not within limits. {size_text}. max_size_mb is {max_mb} MiB"
            ));
        } else if min_mb > 0.0 && new_mb < min_mb {
            result.error = Some(format!(
                "New file size not within limits. {size_text}. min_size_mb is {min_mb} MiB"
            ));
        } else {
            result.note(size_text);
        }
        Ok(result)
    }
}
