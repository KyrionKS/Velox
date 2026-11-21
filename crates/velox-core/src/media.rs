//! Thin, read-only wrapper around ffprobe JSON output.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const VIDEO: &str = "video";
pub const AUDIO: &str = "audio";
pub const SUBTITLE: &str = "subtitle";
pub const DATA: &str = "data";
pub const ATTACHMENT: &str = "attachment";

pub const CHANNEL_LABELS: [(u32, &str); 4] = [(8, "7.1"), (6, "5.1"), (2, "2"), (1, "1")];

/// Picture codecs that sometimes show up as a "video" stream but are not real video.
pub const IMAGE_CODECS: [&str; 6] = ["mjpeg", "png", "bmp", "gif", "webp", "tiff"];

pub fn stream_type(stream: &Stream) -> &str {
    stream.codec_type.as_str()
}

pub fn stream_language(stream: &Stream) -> String {
    tag_string(&stream.tags, "language").to_lowercase()
}

pub fn stream_title(stream: &Stream) -> String {
    tag_string(&stream.tags, "title")
}

pub fn tag_string(tags: &BTreeMap<String, Value>, key: &str) -> String {
    match tags.get(key) {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Number(value)) => value.to_string(),
        Some(Value::Bool(value)) => value.to_string(),
        Some(other) => other.to_string(),
        None => String::new(),
    }
}

pub fn stream_bitrate_kbps(stream: &Stream) -> Option<u32> {
    for key in ["BPS", "BPS-eng", "bps"] {
        if let Some(value) = tags_number(&stream.tags, key) {
            return Some((value / 1000.0) as u32);
        }
    }
    stream
        .bit_rate
        .as_deref()
        .and_then(|value| value.parse::<f64>().ok())
        .map(|value| (value / 1000.0) as u32)
}

fn tags_number(tags: &BTreeMap<String, Value>, key: &str) -> Option<f64> {
    match tags.get(key) {
        Some(Value::String(value)) => value.parse::<f64>().ok(),
        Some(Value::Number(value)) => value.as_f64(),
        _ => None,
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Stream {
    #[serde(default)]
    pub index: usize,
    #[serde(default)]
    pub codec_type: String,
    #[serde(default)]
    pub codec_name: Option<String>,
    #[serde(default)]
    pub codec_long_name: Option<String>,
    #[serde(default)]
    pub profile: Option<String>,
    #[serde(default)]
    pub width: Option<u32>,
    #[serde(default)]
    pub height: Option<u32>,
    #[serde(default)]
    pub channels: Option<u32>,
    #[serde(default)]
    pub channel_layout: Option<String>,
    #[serde(default)]
    pub bit_rate: Option<String>,
    #[serde(default)]
    pub pix_fmt: Option<String>,
    #[serde(default)]
    pub bits_per_raw_sample: Option<String>,
    #[serde(default)]
    pub avg_frame_rate: Option<String>,
    #[serde(default)]
    pub r_frame_rate: Option<String>,
    #[serde(default)]
    pub duration: Option<String>,
    #[serde(default)]
    pub tags: BTreeMap<String, Value>,
    #[serde(default)]
    pub disposition: BTreeMap<String, i64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl Stream {
    pub fn codec(&self) -> String {
        self.codec_name.clone().unwrap_or_default().to_lowercase()
    }

    pub fn is_default(&self) -> bool {
        self.disposition.get("default").copied().unwrap_or(0) != 0
    }

    pub fn language(&self) -> String {
        stream_language(self)
    }

    pub fn title(&self) -> String {
        stream_title(self)
    }

    pub fn bitrate_kbps(&self) -> Option<u32> {
        stream_bitrate_kbps(self)
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FormatInfo {
    #[serde(default)]
    pub filename: Option<String>,
    #[serde(default)]
    pub format_name: Option<String>,
    #[serde(default)]
    pub duration: Option<String>,
    #[serde(default)]
    pub size: Option<String>,
    #[serde(default)]
    pub bit_rate: Option<String>,
    #[serde(default)]
    pub tags: BTreeMap<String, Value>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ProbeData {
    #[serde(default)]
    pub streams: Vec<Stream>,
    #[serde(default)]
    pub format: FormatInfo,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MediaInfo {
    pub path: PathBuf,
    pub size: u64,
    pub streams: Vec<Stream>,
    pub format: FormatInfo,
}

impl MediaInfo {
    pub fn from_probe(path: impl AsRef<Path>, probe: ProbeData, size: u64) -> Self {
        Self {
            path: path.as_ref().to_path_buf(),
            size,
            streams: probe.streams,
            format: probe.format,
        }
    }

    pub fn container(&self) -> String {
        self.path
            .extension()
            .map(|value| value.to_string_lossy().to_lowercase())
            .unwrap_or_default()
    }

    pub fn extension(&self) -> String {
        self.container()
    }

    pub fn is_video(&self) -> bool {
        self.streams
            .iter()
            .any(|stream| stream_type(stream) == VIDEO)
    }

    pub fn file_medium(&self) -> &'static str {
        if self.is_video() {
            "video"
        } else {
            "other"
        }
    }

    pub fn duration(&self) -> f64 {
        if let Some(value) = self
            .format
            .duration
            .as_deref()
            .and_then(|value| value.parse::<f64>().ok())
        {
            if value > 0.0 {
                return value;
            }
        }
        self.video_stream()
            .and_then(|stream| stream.duration.as_deref())
            .and_then(|value| value.parse::<f64>().ok())
            .unwrap_or(0.0)
    }

    pub fn bitrate_kbps(&self) -> Option<u32> {
        self.format
            .bit_rate
            .as_deref()
            .and_then(|value| value.parse::<f64>().ok())
            .map(|value| (value / 1000.0) as u32)
    }

    pub fn streams_of(&self, kind: &str) -> Vec<&Stream> {
        self.streams
            .iter()
            .filter(|stream| stream_type(stream) == kind)
            .collect()
    }

    pub fn video_stream(&self) -> Option<&Stream> {
        self.streams.iter().find(|stream| {
            stream_type(stream) == VIDEO && !IMAGE_CODECS.contains(&stream.codec().as_str())
        })
    }

    pub fn first_stream_type(&self) -> &str {
        self.streams.first().map(stream_type).unwrap_or_default()
    }

    pub fn summary(&self) -> String {
        let container = self.container();
        let mut parts = vec![
            format!(
                "container={}",
                if container.is_empty() {
                    "?"
                } else {
                    &container
                }
            ),
            format!("size={:.1}MB", self.size as f64 / 1_000_000.0),
            format!("duration={:.1}s", self.duration()),
        ];
        let mut counts: Vec<(&str, usize)> = Vec::new();
        for stream in &self.streams {
            let kind = stream_type(stream);
            if let Some(entry) = counts.iter_mut().find(|(value, _)| *value == kind) {
                entry.1 += 1;
            } else {
                counts.push((kind, 1));
            }
        }
        if !counts.is_empty() {
            let text = counts
                .iter()
                .filter(|(kind, _)| !kind.is_empty())
                .map(|(kind, count)| format!("{kind}:{count}"))
                .collect::<Vec<_>>()
                .join(",");
            parts.push(format!("streams={text}"));
        }
        parts.join(" ")
    }

    pub fn stream_table(&self) -> String {
        let header = format!(
            "{:>4} {:<9} {:<12} {:>3} {:<5} title",
            "idx", "type", "codec", "ch", "lang"
        );
        let mut lines = vec![header.clone(), "-".repeat(header.len())];
        for stream in &self.streams {
            let language = stream.language();
            lines.push(format!(
                "{:>4} {:<9} {:<12} {:>3} {:<5} {}",
                stream.index,
                stream_type(stream),
                stream.codec(),
                stream
                    .channels
                    .map(|value| value.to_string())
                    .unwrap_or_default(),
                if language.is_empty() { "-" } else { &language },
                stream.title()
            ));
        }
        lines.join("\n")
    }

    pub fn as_debug_json(&self) -> String {
        serde_json::to_string_pretty(self).unwrap_or_default()
    }

    /// A copy with the given absolute stream indices removed.
    pub fn without_streams(&self, drop: &std::collections::BTreeSet<usize>) -> Self {
        let mut copy = self.clone();
        copy.streams.retain(|stream| !drop.contains(&stream.index));
        copy
    }

    /// Absolute stream indices ordered by type: video, audio, subtitle, data, attachment.
    pub fn type_order(&self) -> Vec<usize> {
        const TYPE_ORDER: [&str; 5] = [VIDEO, AUDIO, SUBTITLE, DATA, ATTACHMENT];
        let mut ordered = Vec::with_capacity(self.streams.len());
        for kind in TYPE_ORDER {
            ordered.extend(
                self.streams
                    .iter()
                    .filter(|stream| stream_type(stream) == kind)
                    .map(|stream| stream.index),
            );
        }
        for stream in &self.streams {
            if !ordered.contains(&stream.index) {
                ordered.push(stream.index);
            }
        }
        ordered
    }

    pub fn channel_label(channels: u32) -> Option<&'static str> {
        CHANNEL_LABELS
            .iter()
            .find(|(value, _)| *value == channels)
            .map(|(_, label)| *label)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> MediaInfo {
        let json = r#"{
          "streams": [
            {"index":0,"codec_type":"audio","codec_name":"eac3","channels":6,"channel_layout":"5.1(side)","bit_rate":"448000","tags":{"language":"eng","title":"Surround"}},
            {"index":1,"codec_type":"video","codec_name":"h264","width":1920,"height":1080,"avg_frame_rate":"24000/1001","duration":"10.5"},
            {"index":2,"codec_type":"subtitle","codec_name":"subrip","tags":{"language":"deu"}}
          ],
          "format": {"duration":"10.5","bit_rate":"5000000"}
        }"#;
        let probe: ProbeData = serde_json::from_str(json).unwrap();
        MediaInfo::from_probe("/movies/test.mkv", probe, 1_000_000)
    }

    #[test]
    fn basic_helpers() {
        let media = sample();
        assert!(media.is_video());
        assert_eq!(media.container(), "mkv");
        assert_eq!(media.first_stream_type(), "audio");
        assert_eq!(media.video_stream().unwrap().codec(), "h264");
        assert_eq!(media.streams_of(AUDIO).len(), 1);
        assert_eq!(media.duration(), 10.5);
        assert_eq!(media.bitrate_kbps(), Some(5000));
        assert_eq!(media.streams[0].bitrate_kbps(), Some(448));
        assert_eq!(media.streams[0].language(), "eng");
        assert_eq!(MediaInfo::channel_label(6), Some("5.1"));
    }

    #[test]
    fn type_order_moves_video_first() {
        assert_eq!(sample().type_order(), vec![1, 0, 2]);
    }

    #[test]
    fn filters_dropped_streams() {
        let media = sample();
        let dropped = [0usize].into_iter().collect();
        let filtered = media.without_streams(&dropped);
        assert_eq!(filtered.streams.len(), 2);
        assert_eq!(filtered.first_stream_type(), "video");
    }
}
