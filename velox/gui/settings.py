"""The settings panel: a sub-sidebar (Video / Audio / Subtitles / Output /
Advanced) with one scrollable page per section."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import DEFAULT_ORDER, VeloxConfig
from .presets import DEFAULT_PRESET, PRESET_LABELS, QUALITY_PRESETS, preset_for_bpp

ENCODERS = [
    ("Apple VideoToolbox HEVC (recommended)", "hevc_videotoolbox"),
    ("Apple VideoToolbox H.264", "h264_videotoolbox"),
    ("Automatic (detect GPU encoder)", "auto"),
    ("NVIDIA NVENC HEVC", "hevc_nvenc"),
    ("Intel Quick Sync HEVC", "hevc_qsv"),
    ("AMD AMF HEVC", "hevc_amf"),
    ("VAAPI HEVC (Linux)", "hevc_vaapi"),
    ("CPU — libx265 (slow)", "libx265"),
    ("CPU — libx264 (slow)", "libx264"),
]

AUDIO_CODECS = ["aac", "ac3", "eac3", "flac", "libmp3lame"]

CONTAINERS = ["mkv", "mp4", "original"]

PLUGIN_LABELS = {
    "reorder_streams": "Move Video First",
    "clean_audio": "Clean Audio Tracks",
    "clean_subs": "Clean Subtitle Tracks",
    "standardise_audio": "Standardise Audio Codec",
    "hevc_gpu": "Encode Video",
    "reorder_all_streams": "Order Streams",
    "file_size_check": "Check File Size",
}

BITRATE_MODES = [
    ("Constant quality (recommended)", "quality"),
    ("Automatic — predictable size", "tier"),
    ("Target file size", "size"),
]

# label -> stored value for the "Default audio track" selector
DEFAULT_AUDIO_PRESETS = {
    "Leave unchanged": "",
    "First kept track": "first",
    "Highest channels": "best",
}
DEFAULT_AUDIO_REVERSE = {value: label for label, value in DEFAULT_AUDIO_PRESETS.items()}

SECTIONS = ["Video", "Audio", "Subtitles", "Output", "Advanced"]


def _spin(minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
    box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    return box


def _dspin(
    minimum: float, maximum: float, value: float, decimals: int, suffix: str = ""
) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setSingleStep(10**-decimals)
    if suffix:
        box.setSuffix(suffix)
    return box


class SettingsPanel(QWidget):
    load_requested = Signal()
    save_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("subnav")
        self.nav.setFixedWidth(150)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setSpacing(1)
        for title in SECTIONS:
            item = QListWidgetItem(title)
            item.setSizeHint(QSize(0, 28))
            self.nav.addItem(item)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedWidth(1)
        divider.setFrameShape(QFrame.Shape.NoFrame)

        self.pages = QStackedWidget()
        self.pages.addWidget(
            self._page(
                "Video",
                "How the video is encoded and how its size is chosen.",
                self._quality_group(),
                self._encoder_group(),
            )
        )
        self.pages.addWidget(
            self._page(
                "Audio",
                "Which audio tracks are kept and how they are encoded.",
                self._audio_group(),
            )
        )
        self.pages.addWidget(
            self._page(
                "Subtitles",
                "Which subtitle tracks are kept.",
                self._subtitles_group(),
            )
        )
        self.pages.addWidget(
            self._page(
                "Output",
                "What happens to the original file.",
                self._output_group(),
            )
        )
        self.pages.addWidget(
            self._page(
                "Advanced",
                "Fine-tuning, performance and external tools.",
                self._limits_group(),
                self._sizecheck_group(),
                self._performance_group(),
                self._steps_group(),
                self._tools_group(),
            )
        )

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        body.addWidget(self.nav)
        body.addWidget(divider)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        root.addLayout(self._build_footer())

        self._update_mode_visibility()

    def _page(self, title: str, description: str, *blocks: QWidget) -> QScrollArea:
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(20, 16, 20, 16)
        column.setSpacing(12)
        content.setMaximumWidth(760)

        heading = QLabel(title)
        font = heading.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 1.0)
        heading.setFont(font)
        column.addWidget(heading)

        if description:
            desc = QLabel(description)
            desc.setObjectName("secondary")
            desc.setWordWrap(True)
            column.addWidget(desc)

        for block in blocks:
            column.addWidget(block)
        column.addStretch(1)

        wrapper = QWidget()
        row = QVBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(content, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(wrapper)
        return area

    def _group(self, title: str, form: QFormLayout) -> QGroupBox:
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setVerticalSpacing(7)
        form.setHorizontalSpacing(12)
        form.setContentsMargins(14, 12, 14, 12)
        box = QGroupBox(title)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        box.setLayout(form)
        return box

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(20, 8, 20, 12)
        row.setSpacing(8)
        load = QPushButton("Reload")
        load.clicked.connect(self.load_requested.emit)
        save = QPushButton("Save")
        save.clicked.connect(self.save_requested.emit)
        row.addStretch(1)
        row.addWidget(load)
        row.addWidget(save)
        return row

    # ---------------------------------------------------------------- blocks
    def _quality_group(self) -> QGroupBox:
        form = QFormLayout()
        self.mode = QComboBox()
        for label, value in BITRATE_MODES:
            self.mode.addItem(label, value)
        self.mode.setToolTip("How the output size is decided.")

        self.quality = _spin(1, 100, 50)
        self.quality.setToolTip(
            "Constant quality (-q:v) for Apple Silicon VideoToolbox.\n"
            "1-100, higher is better. Recommended 45-65 (50 = good, smaller files)."
        )
        self.preset = QComboBox()
        for name, label in PRESET_LABELS:
            self.preset.addItem(label, name)
        self.preset.setToolTip("Size/quality preset for the 'Automatic' mode.")
        self.bpp = _dspin(0.005, 0.3, QUALITY_PRESETS[DEFAULT_PRESET], 4, " bpp")
        self.bpp.setToolTip("Bits per pixel per frame (Automatic mode). Higher = better quality.")
        self.target_size = _dspin(1, 200_000, 1500, 0, " MiB")
        self.target_size.setToolTip("Desired output size per file.")

        form.addRow("Mode", self.mode)
        form.addRow("Constant quality", self.quality)
        form.addRow("Preset", self.preset)
        form.addRow("Bits per pixel", self.bpp)
        form.addRow("Target size", self.target_size)
        self._vform = form
        self.mode.currentIndexChanged.connect(self._update_mode_visibility)
        return self._group("Quality && size", form)

    def _encoder_group(self) -> QGroupBox:
        form = QFormLayout()
        self.encoder = QComboBox()
        for label, value in ENCODERS:
            self.encoder.addItem(label, value)
        self.container = QComboBox()
        self.container.addItems(CONTAINERS)
        self.container.setToolTip("Output container. 'original' keeps the source's.")
        self.tenbit = QCheckBox("10-bit output (Main10)")
        self.tenbit.setToolTip("Better compression and less banding. Disable for very old players.")
        self.spatial_aq = QCheckBox("Spatial adaptive quantization")
        self.hw_decode = QCheckBox("Use hardware decoding (VideoToolbox)")

        form.addRow("Encoder", self.encoder)
        form.addRow("Container", self.container)
        form.addRow("", self.tenbit)
        form.addRow("", self.spatial_aq)
        form.addRow("", self.hw_decode)
        return self._group("Encoder", form)

    def _audio_group(self) -> QGroupBox:
        form = QFormLayout()
        self.audio_langs = QLineEdit("eng,und")
        self.audio_langs.setToolTip(
            "ISO-639-2 codes to keep, comma separated. Keep 'und' for untagged."
        )
        self.audio_codec = QComboBox()
        self.audio_codec.addItems(AUDIO_CODECS)
        self.audio_cap = _spin(0, 1536, 256, " kbps")
        self.audio_cap.setSpecialValueText("keep original")
        self.audio_cap.setToolTip(
            "Cap audio bitrate so it cannot eat the video budget. 0 = keep original."
        )
        self.default_audio = QComboBox()
        self.default_audio.setEditable(True)
        for label in DEFAULT_AUDIO_PRESETS:
            self.default_audio.addItem(label)
        self.default_audio.setToolTip(
            "Which kept audio track becomes the player default.\n"
            "Pick a preset or type a language code (e.g. eng)."
        )
        form.addRow("Keep languages", self.audio_langs)
        form.addRow("Output codec", self.audio_codec)
        form.addRow("Bitrate cap", self.audio_cap)
        form.addRow("Default track", self.default_audio)
        return self._group("Tracks", form)

    def _subtitles_group(self) -> QGroupBox:
        form = QFormLayout()
        self.sub_langs = QLineEdit("eng")
        self.sub_langs.setToolTip("Subtitle languages to keep, comma separated.")
        self.sub_sdh = QCheckBox("Remove SDH / hard-of-hearing subtitles")
        self.commentary = QCheckBox("Remove commentary / description tracks")
        form.addRow("Keep languages", self.sub_langs)
        form.addRow("", self.sub_sdh)
        form.addRow("", self.commentary)
        return self._group("Tracks", form)

    def _output_group(self) -> QGroupBox:
        form = QFormLayout()
        self.keep_original = QCheckBox("Keep the original as a backup (.velox-orig — uses space)")
        self.keep_original.setToolTip(
            "Off (recommended): after a successful encode only the new,\n"
            "smaller file remains. On: a copy of the original is kept next to it."
        )
        self.verify_output = QCheckBox("Verify output before replacing the original")
        self.verify_output.setToolTip(
            "Checks that the result parses, contains video and has the same\n"
            "duration. If it fails, the original is kept untouched."
        )
        self.verify_full_decode = QCheckBox("Deep verification (full decode — slower)")
        self.verify_full_decode.setToolTip(
            "Also decode the whole output to catch corrupt frames.\n"
            "Reads the file once more (expensive on a NAS)."
        )
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("(same folder as the source)")
        browse_out = QPushButton("Browse…")
        browse_out.clicked.connect(self._browse_output_dir)
        out_row = QHBoxLayout()
        out_row.addWidget(self.output_dir)
        out_row.addWidget(browse_out)
        out_widget = QWidget()
        out_widget.setLayout(out_row)
        self.temp_dir = QLineEdit("source")
        self.temp_dir.setToolTip(
            "Where temporary files are written. 'source' keeps them next to each "
            "file (on the NAS) so your Mac SSD is never used."
        )
        form.addRow("", self.keep_original)
        form.addRow("", self.verify_output)
        form.addRow("", self.verify_full_decode)
        form.addRow("Output folder", out_widget)
        form.addRow("Temp folder", self.temp_dir)
        self.verify_output.toggled.connect(self.verify_full_decode.setEnabled)
        return self._group("Result", form)

    def _limits_group(self) -> QGroupBox:
        form = QFormLayout()
        self.target_codec = QComboBox()
        self.target_codec.addItems(["hevc", "h264"])
        self.min_kbps = _spin(0, 500_000, 300, " kbps")
        self.max_kbps = _spin(0, 500_000, 40000, " kbps")
        self.cutoff = _spin(0, 500_000, 0, " kbps")
        self.cutoff.setSpecialValueText("disabled")
        self.cutoff.setToolTip("Skip files whose video bitrate is below this.")
        form.addRow("Target codec", self.target_codec)
        form.addRow("Min bitrate", self.min_kbps)
        form.addRow("Max bitrate", self.max_kbps)
        form.addRow("Skip files below", self.cutoff)
        return self._group("Encoding limits", form)

    def _sizecheck_group(self) -> QGroupBox:
        form = QFormLayout()
        self.upper_bound = _dspin(0, 1000, 110, 1, " %")
        self.lower_bound = _dspin(0, 1000, 0, 1, " %")
        self.min_size = _dspin(0, 1_000_000, 0, 0, " MiB")
        self.max_size = _dspin(0, 1_000_000, 0, 0, " MiB")
        form.addRow("Upper bound (vs original)", self.upper_bound)
        form.addRow("Lower bound (vs original)", self.lower_bound)
        form.addRow("Minimum size", self.min_size)
        form.addRow("Maximum size", self.max_size)
        return self._group("Size check", form)

    def _performance_group(self) -> QGroupBox:
        form = QFormLayout()
        self.concurrency = _spin(1, 8, 2)
        self.concurrency.setToolTip(
            "How many files to process at the same time.\n"
            "2 is a good default on Apple Silicon (the media engine serialises\n"
            "beyond a few concurrent transcodes)."
        )
        form.addRow("Parallel files", self.concurrency)
        return self._group("Performance", form)

    def _steps_group(self) -> QGroupBox:
        form = QFormLayout()
        self.plugin_boxes: dict[str, QCheckBox] = {}
        for key in DEFAULT_ORDER:
            box = QCheckBox(PLUGIN_LABELS[key])
            box.setChecked(True)
            self.plugin_boxes[key] = box
            form.addRow(box)
        return self._group("Pipeline steps", form)

    def _tools_group(self) -> QGroupBox:
        form = QFormLayout()
        self.ffmpeg_path = QLineEdit("ffmpeg")
        self.ffprobe_path = QLineEdit("ffprobe")
        ff_row = QHBoxLayout()
        ff_row.addWidget(self.ffmpeg_path)
        ff_browse = QPushButton("Locate ffmpeg…")
        ff_browse.clicked.connect(self._browse_ffmpeg)
        ff_row.addWidget(ff_browse)
        ff_widget = QWidget()
        ff_widget.setLayout(ff_row)
        form.addRow("ffmpeg", ff_widget)
        form.addRow("ffprobe", self.ffprobe_path)
        return self._group("Tools", form)

    # --------------------------------------------------------------- actions
    def _update_mode_visibility(self) -> None:
        mode = self.mode.currentData()
        tier = mode == "tier"
        self._vform.setRowVisible(self.quality, mode == "quality")
        self._vform.setRowVisible(self.preset, tier)
        self._vform.setRowVisible(self.bpp, tier)
        self._vform.setRowVisible(self.target_size, mode == "size")

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if path:
            self.output_dir.setText(path)

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Locate ffmpeg")
        if path:
            self.ffmpeg_path.setText(path)

    # ------------------------------------------------------------- load/save
    def load_from_config(self, config: VeloxConfig) -> None:
        g, audio, subs, general = (
            config.hevc_gpu,
            config.clean_audio,
            config.clean_subs,
            config.general,
        )
        self.bpp.setValue(g.bits_per_pixel)
        preset = preset_for_bpp(g.bits_per_pixel)
        index = self.preset.findData(preset)
        self.preset.setCurrentIndex(max(0, index))
        self._set_combo_data(self.mode, g.bitrate_mode)
        self.target_size.setValue(g.target_size_mb)
        self.quality.setValue(g.quality)

        self.audio_langs.setText(audio.language)
        self._set_combo_text(self.audio_codec, config.standardise_audio.audio_codec)
        self.audio_cap.setValue(config.standardise_audio.audio_bitrate_kbps)
        default_audio = str(audio.default_audio or "")
        label = DEFAULT_AUDIO_REVERSE.get(default_audio)
        if label is not None:
            self.default_audio.setCurrentText(label)
        else:
            self.default_audio.setEditText(default_audio)

        self.sub_langs.setText(subs.language)
        self.sub_sdh.setChecked(subs.sdh)
        self.commentary.setChecked(audio.commentary or subs.commentary)

        self._set_combo_data(self.encoder, g.encoder)
        self._set_combo_text(self.container, g.container)
        self.hw_decode.setChecked(g.hardware_decode)
        self.tenbit.setChecked(g.enable_10bit)
        self.spatial_aq.setChecked(g.spatial_aq)

        self.keep_original.setChecked(general.keep_original)
        self.verify_output.setChecked(general.verify_output)
        self.verify_full_decode.setChecked(general.verify_full_decode)
        self.verify_full_decode.setEnabled(general.verify_output)
        self.output_dir.setText(general.output_dir or "")
        self.temp_dir.setText(str(general.temp_dir or "source"))

        self._set_combo_text(self.target_codec, g.target_codec)
        self.min_kbps.setValue(g.min_video_kbps)
        self.max_kbps.setValue(g.max_video_kbps)
        self.cutoff.setValue(g.bitrate_cutoff)
        self.upper_bound.setValue(config.file_size_check.upper_bound)
        self.lower_bound.setValue(config.file_size_check.lower_bound)
        self.min_size.setValue(config.file_size_check.min_size_mb)
        self.max_size.setValue(config.file_size_check.max_size_mb)
        self.concurrency.setValue(int(getattr(general, "concurrency", 2) or 2))
        for key, box in self.plugin_boxes.items():
            box.setChecked(config.plugin(key).enabled)
        self.ffmpeg_path.setText(general.ffmpeg_path)
        self.ffprobe_path.setText(general.ffprobe_path)
        self._update_mode_visibility()

    def apply_to(self, config: VeloxConfig) -> VeloxConfig:
        config = config.copy()
        g = config.hevc_gpu

        preset = self.preset.currentData()
        if preset != "custom":
            g.bits_per_pixel = QUALITY_PRESETS[preset]
        else:
            g.bits_per_pixel = self.bpp.value()
        g.bitrate_mode = self.mode.currentData()
        g.target_size_mb = self.target_size.value()
        g.quality = self.quality.value()

        config.clean_audio.language = self.audio_langs.text().strip() or "eng,und"
        config.standardise_audio.audio_codec = self.audio_codec.currentText()
        config.standardise_audio.audio_bitrate_kbps = self.audio_cap.value()
        default_text = self.default_audio.currentText().strip()
        config.clean_audio.default_audio = DEFAULT_AUDIO_PRESETS.get(default_text, default_text)

        config.clean_subs.language = self.sub_langs.text().strip() or "eng"
        config.clean_subs.sdh = self.sub_sdh.isChecked()
        config.clean_audio.commentary = self.commentary.isChecked()
        config.clean_subs.commentary = self.commentary.isChecked()

        g.encoder = self.encoder.currentData()
        g.container = self.container.currentText()
        g.hardware_decode = self.hw_decode.isChecked()
        g.enable_10bit = self.tenbit.isChecked()
        g.spatial_aq = self.spatial_aq.isChecked()

        config.general.keep_original = self.keep_original.isChecked()
        config.general.verify_output = self.verify_output.isChecked()
        config.general.verify_full_decode = self.verify_full_decode.isChecked()
        config.general.output_dir = self.output_dir.text().strip() or None
        config.general.temp_dir = self.temp_dir.text().strip() or "source"

        g.target_codec = self.target_codec.currentText()
        g.min_video_kbps = self.min_kbps.value()
        g.max_video_kbps = self.max_kbps.value()
        g.bitrate_cutoff = self.cutoff.value()
        config.file_size_check.upper_bound = self.upper_bound.value()
        config.file_size_check.lower_bound = self.lower_bound.value()
        config.file_size_check.min_size_mb = self.min_size.value()
        config.file_size_check.max_size_mb = self.max_size.value()
        config.general.concurrency = max(1, self.concurrency.value())
        for key, box in self.plugin_boxes.items():
            config.plugin(key).enabled = box.isChecked()
        config.general.ffmpeg_path = self.ffmpeg_path.text().strip() or "ffmpeg"
        config.general.ffprobe_path = self.ffprobe_path.text().strip() or "ffprobe"
        return config

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _set_combo_text(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)
