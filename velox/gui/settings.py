"""The settings panel: a simple Basic tab and an Advanced tab."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import DEFAULT_ORDER, VeloxConfig
from .presets import DEFAULT_PRESET, QUALITY_PRESETS, PRESET_LABELS, preset_for_bpp

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


def _spin(minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
    box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    return box


def _dspin(minimum: float, maximum: float, value: float, decimals: int, suffix: str = "") -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setSingleStep(10 ** -decimals)
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
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.addTab(self._scrollable(self._build_basic()), "Basic")
        tabs.addTab(self._scrollable(self._build_advanced()), "Advanced")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(tabs, 1)
        layout.addLayout(self._build_footer())

    def _scrollable(self, content: QWidget) -> QScrollArea:
        # Keep the content at a comfortable macOS-pane width and left-aligned,
        # scrolling vertically when needed (so nothing is ever cut off).
        content.setMaximumWidth(760)
        wrapper = QWidget()
        column = QVBoxLayout(wrapper)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(content, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        column.addStretch(1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(wrapper)
        return area

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

    def _build_basic(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # --- Quality -----------------------------------------------------
        qform = QFormLayout()
        self.preset = QComboBox()
        for name, label in PRESET_LABELS:
            self.preset.addItem(label, name)
        self.preset.setToolTip(
            "Overall size/quality preset for the 'Automatic' (tier) mode.\n"
            "Each preset maps to a bits-per-pixel value."
        )
        self.bpp = _dspin(0.005, 0.3, QUALITY_PRESETS[DEFAULT_PRESET], 4, " bpp")
        self.bpp.setToolTip(
            "Bits per pixel per frame (Automatic mode). Higher = better quality."
        )
        self.quality = _spin(1, 100, 50)
        self.quality.setToolTip(
            "Constant quality (-q:v) for Apple Silicon VideoToolbox.\n"
            "1-100, higher is better. Recommended 45-65 (50 = good, smaller files)."
        )
        qform.addRow("Preset", self.preset)
        qform.addRow("Target bits per pixel", self.bpp)
        qform.addRow("Constant quality", self.quality)
        layout.addWidget(self._group("Quality", qform))
        self._qform = qform

        # --- Bitrate mode ------------------------------------------------
        mform = QFormLayout()
        self.mode = QComboBox()
        for label, value in BITRATE_MODES:
            self.mode.addItem(label, value)
        self.target_size = _dspin(1, 200_000, 1500, 0, " MiB")
        self.target_size.setToolTip("Desired output size per file.")
        mform.addRow("Mode", self.mode)
        mform.addRow("Target size", self.target_size)
        self.mode.currentIndexChanged.connect(self._update_mode_visibility)
        layout.addWidget(self._group("Bitrate", mform))
        self._mform = mform

        # --- Audio -------------------------------------------------------
        aform = QFormLayout()
        self.audio_langs = QLineEdit("eng,und")
        self.audio_langs.setToolTip("ISO-639-2 codes to keep, comma separated. Keep 'und' for untagged.")
        self.audio_codec = QComboBox()
        self.audio_codec.addItems(AUDIO_CODECS)
        self.audio_cap = _spin(0, 1536, 256, " kbps")
        self.audio_cap.setSpecialValueText("keep original")
        self.audio_cap.setToolTip("Cap audio bitrate so it cannot eat the video budget. 0 = keep original.")
        self.default_audio = QComboBox()
        self.default_audio.setEditable(True)
        for label in DEFAULT_AUDIO_PRESETS:
            self.default_audio.addItem(label)
        self.default_audio.setToolTip(
            "Which kept audio track becomes the player default.\n"
            "Pick a preset or type a language code (e.g. eng)."
        )
        aform.addRow("Keep languages", self.audio_langs)
        aform.addRow("Output codec", self.audio_codec)
        aform.addRow("Bitrate cap", self.audio_cap)
        aform.addRow("Default track", self.default_audio)
        layout.addWidget(self._group("Audio", aform))

        # --- Subtitles ---------------------------------------------------
        sform = QFormLayout()
        self.sub_langs = QLineEdit("eng")
        self.sub_langs.setToolTip("Subtitle languages to keep, comma separated.")
        self.sub_sdh = QCheckBox("Remove SDH / hard-of-hearing subtitles")
        self.commentary = QCheckBox("Remove commentary / description tracks")
        sform.addRow("Keep languages", self.sub_langs)
        sform.addRow("", self.sub_sdh)
        sform.addRow("", self.commentary)
        layout.addWidget(self._group("Subtitles", sform))

        # --- Encoder -----------------------------------------------------
        eform = QFormLayout()
        self.encoder = QComboBox()
        for label, value in ENCODERS:
            self.encoder.addItem(label, value)
        self.hw_decode = QCheckBox("Use hardware decoding (VideoToolbox)")
        self.tenbit = QCheckBox("10-bit output (Main10)")
        self.tenbit.setToolTip("Better compression and less banding. Disable for very old players.")
        self.spatial_aq = QCheckBox("Spatial adaptive quantization")
        eform.addRow("Encoder", self.encoder)
        eform.addRow("", self.hw_decode)
        eform.addRow("", self.tenbit)
        eform.addRow("", self.spatial_aq)
        layout.addWidget(self._group("Video encoder", eform))

        # --- Output ------------------------------------------------------
        oform = QFormLayout()
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
        oform.addRow("", self.keep_original)
        oform.addRow("", self.verify_output)
        oform.addRow("", self.verify_full_decode)
        oform.addRow("Output folder", out_widget)
        oform.addRow("Temp folder", self.temp_dir)
        layout.addWidget(self._group("Output", oform))

        layout.addStretch(1)
        self._update_mode_visibility()
        return page

    def _build_advanced(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        vform = QFormLayout()
        self.target_codec = QComboBox()
        self.target_codec.addItems(["hevc", "h264"])
        self.container = QComboBox()
        self.container.addItems(["mkv", "mp4", "original"])
        self.min_kbps = _spin(0, 500_000, 300, " kbps")
        self.max_kbps = _spin(0, 500_000, 40000, " kbps")
        self.cutoff = _spin(0, 500_000, 0, " kbps")
        self.cutoff.setSpecialValueText("disabled")
        vform.addRow("Target codec", self.target_codec)
        vform.addRow("Container", self.container)
        vform.addRow("Min bitrate", self.min_kbps)
        vform.addRow("Max bitrate", self.max_kbps)
        vform.addRow("Skip files below", self.cutoff)
        layout.addWidget(self._group("Video (advanced)", vform))

        zform = QFormLayout()
        self.upper_bound = _dspin(0, 1000, 110, 1, " %")
        self.lower_bound = _dspin(0, 1000, 0, 1, " %")
        self.min_size = _dspin(0, 1_000_000, 0, 0, " MiB")
        self.max_size = _dspin(0, 1_000_000, 0, 0, " MiB")
        zform.addRow("Upper bound (vs original)", self.upper_bound)
        zform.addRow("Lower bound (vs original)", self.lower_bound)
        zform.addRow("Minimum size", self.min_size)
        zform.addRow("Maximum size", self.max_size)
        layout.addWidget(self._group("Size check", zform))

        perf_form = QFormLayout()
        self.concurrency = _spin(1, 8, 2)
        self.concurrency.setToolTip(
            "How many files to process at the same time.\n"
            "2 is a good default on Apple Silicon (the media engine serialises\n"
            "beyond a few concurrent transcodes)."
        )
        perf_form.addRow("Parallel files", self.concurrency)
        layout.addWidget(self._group("Performance", perf_form))

        pform = QFormLayout()
        self.plugin_boxes: dict[str, QCheckBox] = {}
        for key in DEFAULT_ORDER:
            box = QCheckBox(PLUGIN_LABELS[key])
            box.setChecked(True)
            self.plugin_boxes[key] = box
            pform.addRow(box)
        layout.addWidget(self._group("Pipeline steps", pform))

        tform = QFormLayout()
        self.ffmpeg_path = QLineEdit("ffmpeg")
        self.ffprobe_path = QLineEdit("ffprobe")
        ff_row = QHBoxLayout()
        ff_row.addWidget(self.ffmpeg_path)
        ff_browse = QPushButton("Locate ffmpeg…")
        ff_browse.clicked.connect(self._browse_ffmpeg)
        ff_row.addWidget(ff_browse)
        ff_widget = QWidget()
        ff_widget.setLayout(ff_row)
        tform.addRow("ffmpeg", ff_widget)
        tform.addRow("ffprobe", self.ffprobe_path)
        layout.addWidget(self._group("Tools", tform))

        layout.addStretch(1)
        return page

    # --------------------------------------------------------------- actions
    def _update_mode_visibility(self) -> None:
        mode = self.mode.currentData()
        tier = mode == "tier"
        self._mform.setRowVisible(self.target_size, mode == "size")
        self._qform.setRowVisible(self.preset, tier)
        self._qform.setRowVisible(self.bpp, tier)
        self._qform.setRowVisible(self.quality, mode == "quality")

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
        g, audio, subs, enc, general = (
            config.hevc_gpu,
            config.clean_audio,
            config.clean_subs,
            config.hevc_gpu,
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
        self.hw_decode.setChecked(g.hardware_decode)
        self.tenbit.setChecked(g.enable_10bit)
        self.spatial_aq.setChecked(g.spatial_aq)

        self.keep_original.setChecked(general.keep_original)
        self.verify_output.setChecked(general.verify_output)
        self.verify_full_decode.setChecked(general.verify_full_decode)
        self.output_dir.setText(general.output_dir or "")
        self.temp_dir.setText(str(general.temp_dir or "source"))

        self._set_combo_text(self.target_codec, g.target_codec)
        self._set_combo_text(self.container, g.container)
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
        g.hardware_decode = self.hw_decode.isChecked()
        g.enable_10bit = self.tenbit.isChecked()
        g.spatial_aq = self.spatial_aq.isChecked()

        config.general.keep_original = self.keep_original.isChecked()
        config.general.verify_output = self.verify_output.isChecked()
        config.general.verify_full_decode = self.verify_full_decode.isChecked()
        config.general.output_dir = self.output_dir.text().strip() or None
        config.general.temp_dir = self.temp_dir.text().strip() or "source"

        g.target_codec = self.target_codec.currentText()
        g.container = self.container.currentText()
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
