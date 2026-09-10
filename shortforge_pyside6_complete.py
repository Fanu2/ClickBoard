import sys
import json
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QObject, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QFileDialog,
    QComboBox, QLineEdit, QCheckBox, QSpinBox, QDoubleSpinBox,
    QProgressBar, QGroupBox, QFormLayout, QMessageBox, QTabWidget,
    QTextEdit
)

APP_DIR = Path.home() / ".shortforge"
APP_DIR.mkdir(exist_ok=True)
SETTINGS = APP_DIR / "settings.json"

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".3gp", ".mpeg", ".mpg", ".ts", ".mts", ".m2ts", ".vob"
}
AUDIO_EXTS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"
}


def find_exe(name):
    return shutil.which(name)


def probe_duration(path):
    exe = find_exe("ffprobe")
    if not exe:
        return 0.0
    try:
        result = subprocess.run(
            [
                exe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path)
            ],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip() or 0)
    except Exception:
        return 0.0


class ConvertWorker(QObject):
    progress = Signal(int)
    log = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, files, output_dir, settings):
        super().__init__()
        self.files = files
        self.output_dir = Path(output_dir)
        self.s = settings
        self.stop_requested = False
        self.current_process = None

    def stop(self):
        self.stop_requested = True
        if self.current_process and self.current_process.poll() is None:
            try:
                self.current_process.terminate()
            except Exception:
                pass

    def video_filter(self):
        w, h = map(int, self.s["resolution"].split("x"))

        if self.s["fit"] == "Crop to Fill":
            vf = (
                f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},setsar=1"
            )
        elif self.s["fit"] == "Fit with Background":
            vf = (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:"
                f"color={self.s['background']},setsar=1"
            )
        else:
            vf = f"scale={w}:{h},setsar=1"

        if self.s["mirror"]:
            vf += ",hflip"

        if self.s["brightness"]:
            vf += f",eq=brightness={self.s['brightness'] / 100:.3f}"

        if self.s["contrast"]:
            vf += f",eq=contrast={1 + self.s['contrast'] / 100:.3f}"

        if self.s["text"]:
            t = (
                self.s["text"]
                .replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace("'", "\\'")
            )
            vf += (
                f",drawtext=text='{t}':"
                "x=(w-text_w)/2:y=h-text_h-80:"
                "fontsize=54:fontcolor=white:"
                "borderw=3:bordercolor=black@0.65"
            )

        return vf

    def build_command(self, src, out):
        ffmpeg = find_exe("ffmpeg")
        audio = self.s["external_audio"]
        has_audio = bool(audio and Path(audio).is_file())
        mode = self.s["audio_mode"]

        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

        # Loop the external music when requested.
        if has_audio and self.s["loop_music"]:
            cmd += ["-stream_loop", "-1"]

        cmd += ["-ss", str(self.s["start"]), "-i", str(src)]

        if has_audio:
            cmd += ["-i", audio]

        if self.s["duration"] > 0:
            cmd += ["-t", str(self.s["duration"])]

        vf = self.video_filter()

        common_video = [
            "-map", "[vout]",
            "-c:v", "libx264",
            "-preset", self.s["preset"],
            "-crf", str(self.s["quality"]),
            "-pix_fmt", "yuv420p"
        ]

        if not has_audio:
            return cmd + [
                "-vf", vf,
                *common_video,
                "-c:a", "aac",
                "-b:a", self.s["audio"],
                "-movflags", "+faststart",
                str(out)
            ]

        music_volume = self.s["music_volume"]
        original_volume = self.s["original_volume"]
        fade_in = self.s["fade_in"]
        fade_out = self.s["fade_out"]
        clip_duration = self.s["duration"]

        # Video and external audio are explicitly labeled. This avoids the
        # common FFmpeg filter-graph mapping error.
        filters = [f"[0:v]{vf}[vout]"]

        if mode == "Replace original":
            music_chain = f"[1:a]volume={music_volume:.3f}"
            if fade_in > 0:
                music_chain += f",afade=t=in:st=0:d={fade_in:.2f}"
            if fade_out > 0 and clip_duration > fade_out:
                music_chain += (
                    f",afade=t=out:st={clip_duration - fade_out:.2f}:"
                    f"d={fade_out:.2f}"
                )
            music_chain += "[aout]"
            filters.append(music_chain)

            return cmd + [
                "-filter_complex", ";".join(filters),
                *common_video,
                "-map", "[aout]",
                "-c:a", "aac",
                "-b:a", self.s["audio"],
                "-shortest",
                "-movflags", "+faststart",
                str(out)
            ]

        # Mix mode: preserve some original sound and add the music.
        filters.append(f"[0:a]volume={original_volume:.3f}[orig]")
        music_chain = f"[1:a]volume={music_volume:.3f}"
        if fade_in > 0:
            music_chain += f",afade=t=in:st=0:d={fade_in:.2f}"
        if fade_out > 0 and clip_duration > fade_out:
            music_chain += (
                f",afade=t=out:st={clip_duration - fade_out:.2f}:"
                f"d={fade_out:.2f}"
            )
        music_chain += "[music]"
        filters.append(music_chain)
        filters.append(
            "[orig][music]amix=inputs=2:duration=first:"
            "dropout_transition=2[aout]"
        )

        return cmd + [
            "-filter_complex", ";".join(filters),
            *common_video,
            "-map", "[aout]",
            "-c:a", "aac",
            "-b:a", self.s["audio"],
            "-shortest",
            "-movflags", "+faststart",
            str(out)
        ]

    @Slot()
    def run(self):
        ffmpeg = find_exe("ffmpeg")
        if not ffmpeg:
            self.finished.emit(
                False,
                "FFmpeg was not found in PATH. Install FFmpeg and restart PowerShell."
            )
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        total = len(self.files)
        completed = 0

        for index, source in enumerate(self.files):
            if self.stop_requested:
                self.finished.emit(False, "Conversion cancelled.")
                return

            source = Path(source)
            output = self.output_dir / f"{source.stem}_SHORT.mp4"

            self.log.emit(
                f"[{index + 1}/{total}] {source.name}"
            )

            command = self.build_command(source, output)

            try:
                self.current_process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True
                )
                _, stderr = self.current_process.communicate()
                return_code = self.current_process.returncode
                self.current_process = None

                if self.stop_requested:
                    self.finished.emit(False, "Conversion cancelled.")
                    return

                if return_code != 0:
                    self.log.emit(
                        "  ERROR: " + (stderr or "FFmpeg failed.")[-1200:]
                    )
                else:
                    self.log.emit(f"  ✓ {output.name}")
                    completed += 1

            except Exception as exc:
                self.current_process = None
                self.log.emit(f"  ERROR: {exc}")

            self.progress.emit(int((index + 1) * 100 / total))

        self.finished.emit(
            True,
            f"Finished: {completed}/{total} video(s) converted successfully."
        )


class ShortForge(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ShortForge — Vertical Video Converter")
        self.resize(1220, 850)
        self.thread = None
        self.worker = None
        self.build_ui()
        self.load_settings()
        self.apply_theme()
        self.update_ffmpeg_status()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        header = QHBoxLayout()
        title_box = QVBoxLayout()

        title = QLabel("🎬 ShortForge")
        title.setObjectName("title")
        subtitle = QLabel(
            "Turn videos into polished 9:16 Shorts with music, captions and batch processing"
        )
        subtitle.setObjectName("muted")

        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self.ffmpeg_status = QLabel()
        header.addWidget(self.ffmpeg_status)
        outer.addLayout(header)

        tabs = QTabWidget()
        outer.addWidget(tabs, 1)

        # ---------------- Convert tab ----------------
        main = QWidget()
        main_layout = QHBoxLayout(main)
        left = QVBoxLayout()
        right = QVBoxLayout()

        source_box = QGroupBox("Source videos")
        source_layout = QVBoxLayout(source_box)
        source_buttons = QHBoxLayout()

        add = QPushButton("＋ Add Videos")
        add.clicked.connect(self.add_videos)
        add_folder = QPushButton("＋ Add Folder")
        add_folder.clicked.connect(self.add_folder)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_videos)

        source_buttons.addWidget(add)
        source_buttons.addWidget(add_folder)
        source_buttons.addWidget(clear)
        source_layout.addLayout(source_buttons)

        self.video_list = QListWidget()
        self.video_list.setSelectionMode(QListWidget.ExtendedSelection)
        source_layout.addWidget(self.video_list, 1)

        self.count_label = QLabel("0 videos")
        self.count_label.setObjectName("muted")
        source_layout.addWidget(self.count_label)
        left.addWidget(source_box, 1)

        # Output
        output_box = QGroupBox("Output")
        output_form = QFormLayout(output_box)

        self.output = QLineEdit(str(Path.home() / "ShortForge_Output"))
        output_browse = QPushButton("Browse…")
        output_browse.clicked.connect(self.choose_output)

        output_row = QHBoxLayout()
        output_row.addWidget(self.output, 1)
        output_row.addWidget(output_browse)
        output_form.addRow("Folder:", output_row)

        self.resolution = QComboBox()
        self.resolution.addItems([
            "1080x1920 — Full HD",
            "720x1280 — HD"
        ])
        output_form.addRow("Resolution:", self.resolution)

        self.fit = QComboBox()
        self.fit.addItems([
            "Crop to Fill",
            "Fit with Background",
            "Stretch"
        ])
        output_form.addRow("Framing:", self.fit)

        self.background = QComboBox()
        self.background.addItems(["black", "white", "gray"])
        output_form.addRow("Background:", self.background)

        self.duration = QComboBox()
        self.duration.addItems([
            "Auto / full video",
            "15 seconds",
            "30 seconds",
            "60 seconds",
            "90 seconds"
        ])
        self.duration.setCurrentIndex(3)
        output_form.addRow("Clip length:", self.duration)

        self.start = QDoubleSpinBox()
        self.start.setRange(0, 999999)
        self.start.setDecimals(1)
        self.start.setSuffix(" s")
        output_form.addRow("Start at:", self.start)

        right.addWidget(output_box)

        # External audio
        audio_box = QGroupBox("🎵 External music / audio")
        audio_form = QFormLayout(audio_box)

        audio_row = QHBoxLayout()
        self.external_audio = QLineEdit()
        self.external_audio.setPlaceholderText(
            "Optional MP3, WAV, M4A, AAC, FLAC, OGG or OPUS"
        )
        audio_browse = QPushButton("Browse…")
        audio_browse.clicked.connect(self.choose_audio)
        audio_clear = QPushButton("Clear")
        audio_clear.clicked.connect(self.external_audio.clear)

        audio_row.addWidget(self.external_audio, 1)
        audio_row.addWidget(audio_browse)
        audio_row.addWidget(audio_clear)
        audio_form.addRow("Music:", audio_row)

        self.audio_mode = QComboBox()
        self.audio_mode.addItems([
            "Replace original",
            "Mix with original"
        ])
        audio_form.addRow("Audio mode:", self.audio_mode)

        self.music_volume = QDoubleSpinBox()
        self.music_volume.setRange(0.0, 3.0)
        self.music_volume.setSingleStep(0.05)
        self.music_volume.setValue(1.0)
        self.music_volume.setSuffix("×")
        audio_form.addRow("Music volume:", self.music_volume)

        self.original_volume = QDoubleSpinBox()
        self.original_volume.setRange(0.0, 3.0)
        self.original_volume.setSingleStep(0.05)
        self.original_volume.setValue(0.25)
        self.original_volume.setSuffix("×")
        audio_form.addRow("Original volume:", self.original_volume)

        self.loop_music = QCheckBox("Loop music to fill the Short")
        audio_form.addRow("", self.loop_music)

        fade_row = QHBoxLayout()
        self.fade_in = QDoubleSpinBox()
        self.fade_in.setRange(0, 30)
        self.fade_in.setSingleStep(0.5)
        self.fade_in.setSuffix(" s")
        self.fade_out = QDoubleSpinBox()
        self.fade_out.setRange(0, 30)
        self.fade_out.setSingleStep(0.5)
        self.fade_out.setSuffix(" s")
        fade_row.addWidget(QLabel("In"))
        fade_row.addWidget(self.fade_in)
        fade_row.addWidget(QLabel("Out"))
        fade_row.addWidget(self.fade_out)
        audio_form.addRow("Fade:", fade_row)

        right.addWidget(audio_box)

        # Enhancements
        enhancement_box = QGroupBox("✨ Shorts enhancements")
        enhancement_form = QFormLayout(enhancement_box)

        self.text_overlay = QLineEdit()
        self.text_overlay.setPlaceholderText(
            "Optional title / caption overlay"
        )
        enhancement_form.addRow("Text:", self.text_overlay)

        self.mirror = QCheckBox("Mirror video horizontally")
        enhancement_form.addRow("", self.mirror)

        self.brightness = QSpinBox()
        self.brightness.setRange(-100, 100)
        self.brightness.setSuffix("%")
        enhancement_form.addRow("Brightness:", self.brightness)

        self.contrast = QSpinBox()
        self.contrast.setRange(-100, 100)
        self.contrast.setSuffix("%")
        enhancement_form.addRow("Contrast:", self.contrast)

        self.quality = QComboBox()
        self.quality.addItems([
            "18 — Very high",
            "20 — High",
            "22 — Recommended",
            "24 — Smaller",
            "27 — Compact"
        ])
        self.quality.setCurrentIndex(2)
        enhancement_form.addRow("Quality:", self.quality)

        self.preset = QComboBox()
        self.preset.addItems(["fast", "medium", "slow"])
        self.preset.setCurrentIndex(1)
        enhancement_form.addRow("Encoding:", self.preset)

        self.audio_bitrate = QComboBox()
        self.audio_bitrate.addItems(["128k", "160k", "192k", "256k"])
        self.audio_bitrate.setCurrentIndex(2)
        enhancement_form.addRow("Audio:", self.audio_bitrate)

        right.addWidget(enhancement_box)

        actions = QHBoxLayout()
        self.convert_button = QPushButton("🚀 Convert to Shorts")
        self.convert_button.setObjectName("primary")
        self.convert_button.clicked.connect(self.convert_videos)

        self.stop_button = QPushButton("■ Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_conversion)

        actions.addWidget(self.convert_button, 1)
        actions.addWidget(self.stop_button)
        right.addLayout(actions)

        main_layout.addLayout(left, 5)
        main_layout.addLayout(right, 5)
        tabs.addTab(main, "🎞 Convert")

        # Log
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        log_layout.addWidget(self.log)
        tabs.addTab(log_tab, "📋 Log")

        # About
        about = QLabel(
            "<h2>ShortForge</h2>"
            "<p>Batch-convert common video formats into vertical 9:16 MP4 Shorts.</p>"
            "<p><b>Video inputs:</b> MP4, MKV, AVI, MOV, WMV, FLV, WebM, "
            "M4V, 3GP, MPEG, MPG, TS, MTS, M2TS and VOB.</p>"
            "<p><b>External audio:</b> MP3, WAV, M4A, AAC, FLAC, OGG and OPUS. "
            "Music can replace the original audio or be mixed with it, with "
            "volume, looping and fade controls.</p>"
            "<p><b>Recommended:</b> 1080×1920 • Crop to Fill • 60 seconds • "
            "CRF 20–22 • AAC 192k.</p>"
            "<p><b>Copyright:</b> only use music and video that you have the "
            "rights or permission to upload.</p>"
        )
        about.setWordWrap(True)
        tabs.addTab(about, "ℹ About")

        self.progress = QProgressBar()
        self.progress.setValue(0)
        outer.addWidget(self.progress)

        self.status = QLabel("Ready.")
        self.status.setObjectName("muted")
        outer.addWidget(self.status)

    def add_videos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select videos",
            "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm "
            "*.m4v *.3gp *.mpeg *.mpg *.ts *.mts *.m2ts *.vob);;"
            "All Files (*)"
        )
        self.add_paths(files)

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select folder containing videos"
        )
        if not folder:
            return

        files = [
            str(p)
            for p in Path(folder).rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS
        ]
        self.add_paths(files)

    def add_paths(self, paths):
        existing = {
            self.video_list.item(i).data(Qt.UserRole)
            for i in range(self.video_list.count())
        }

        for path in paths:
            if path not in existing:
                item = QListWidgetItem(Path(path).name)
                item.setData(Qt.UserRole, path)
                item.setToolTip(path)
                self.video_list.addItem(item)

        self.update_count()

    def update_count(self):
        self.count_label.setText(
            f"{self.video_list.count()} video(s)"
        )

    def clear_videos(self):
        self.video_list.clear()
        self.update_count()

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose output folder"
        )
        if folder:
            self.output.setText(folder)

    def choose_audio(self):
        audio, _ = QFileDialog.getOpenFileName(
            self,
            "Select external music / audio",
            "",
            "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus);;"
            "All Files (*)"
        )
        if audio:
            self.external_audio.setText(audio)

    def current_settings(self):
        durations = [0, 15, 30, 60, 90]
        return {
            "resolution": (
                "1080x1920"
                if self.resolution.currentIndex() == 0
                else "720x1280"
            ),
            "fit": self.fit.currentText(),
            "background": self.background.currentText(),
            "duration": durations[self.duration.currentIndex()],
            "start": self.start.value(),
            "text": self.text_overlay.text().strip(),
            "mirror": self.mirror.isChecked(),
            "brightness": self.brightness.value(),
            "contrast": self.contrast.value(),
            "quality": int(self.quality.currentText().split()[0]),
            "preset": self.preset.currentText(),
            "audio": self.audio_bitrate.currentText(),
            "external_audio": self.external_audio.text().strip(),
            "audio_mode": self.audio_mode.currentText(),
            "music_volume": self.music_volume.value(),
            "original_volume": self.original_volume.value(),
            "loop_music": self.loop_music.isChecked(),
            "fade_in": self.fade_in.value(),
            "fade_out": self.fade_out.value()
        }

    def convert_videos(self):
        if not self.video_list.count():
            QMessageBox.warning(
                self, "No videos", "Add one or more videos first."
            )
            return

        if not find_exe("ffmpeg"):
            QMessageBox.critical(
                self,
                "FFmpeg required",
                "FFmpeg was not found in PATH.\n\n"
                "Install FFmpeg, reopen PowerShell and run again."
            )
            return

        external = self.external_audio.text().strip()
        if external:
            if not Path(external).is_file():
                QMessageBox.warning(
                    self, "Audio not found",
                    "The selected external audio file does not exist."
                )
                return
            if Path(external).suffix.lower() not in AUDIO_EXTS:
                QMessageBox.warning(
                    self, "Unsupported audio",
                    "Select MP3, WAV, M4A, AAC, FLAC, OGG or OPUS."
                )
                return

        output = self.output.text().strip()
        if not output:
            QMessageBox.warning(
                self, "Output folder", "Choose an output folder."
            )
            return

        files = [
            self.video_list.item(i).data(Qt.UserRole)
            for i in range(self.video_list.count())
        ]

        self.save_settings()
        self.log.clear()
        self.progress.setValue(0)
        self.status.setText("Converting…")
        self.convert_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        self.thread = QThread()
        self.worker = ConvertWorker(
            files,
            output,
            self.current_settings()
        )
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(
            self.progress.setValue, Qt.QueuedConnection
        )
        self.worker.log.connect(
            self.log.append, Qt.QueuedConnection
        )
        self.worker.finished.connect(
            self.conversion_finished, Qt.QueuedConnection
        )
        self.worker.finished.connect(
            self.thread.quit, Qt.QueuedConnection
        )

        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    @Slot(bool, str)
    def conversion_finished(self, ok, message):
        self.convert_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status.setText(message)

        if ok:
            QMessageBox.information(
                self, "ShortForge", message
            )
        else:
            QMessageBox.warning(
                self, "ShortForge", message
            )

    def stop_conversion(self):
        if self.worker:
            self.worker.stop()
        self.stop_button.setEnabled(False)
        self.status.setText(
            "Stopping current conversion…"
        )

    @Slot()
    def thread_finished(self):
        self.worker = None
        self.thread = None

    def update_ffmpeg_status(self):
        if find_exe("ffmpeg"):
            self.ffmpeg_status.setText("● FFmpeg detected")
            self.ffmpeg_status.setObjectName("ok")
        else:
            self.ffmpeg_status.setText("● FFmpeg not found")
            self.ffmpeg_status.setObjectName("bad")

    def save_settings(self):
        try:
            settings = self.current_settings()
            settings["output"] = self.output.text()
            settings["resolution_index"] = self.resolution.currentIndex()
            settings["fit_index"] = self.fit.currentIndex()
            settings["background_index"] = self.background.currentIndex()
            settings["duration_index"] = self.duration.currentIndex()
            settings["quality_index"] = self.quality.currentIndex()
            settings["preset_index"] = self.preset.currentIndex()
            settings["audio_index"] = self.audio_bitrate.currentIndex()
            SETTINGS.write_text(
                json.dumps(settings, indent=2),
                encoding="utf-8"
            )
        except Exception:
            pass

    def load_settings(self):
        try:
            data = json.loads(
                SETTINGS.read_text(encoding="utf-8")
            )

            self.output.setText(
                data.get(
                    "output",
                    str(Path.home() / "ShortForge_Output")
                )
            )
            self.resolution.setCurrentIndex(
                data.get("resolution_index", 0)
            )
            self.fit.setCurrentIndex(
                data.get("fit_index", 0)
            )
            self.background.setCurrentIndex(
                data.get("background_index", 0)
            )
            self.duration.setCurrentIndex(
                data.get("duration_index", 3)
            )
            self.start.setValue(
                data.get("start", 0)
            )
            self.text_overlay.setText(
                data.get("text", "")
            )
            self.mirror.setChecked(
                data.get("mirror", False)
            )
            self.brightness.setValue(
                data.get("brightness", 0)
            )
            self.contrast.setValue(
                data.get("contrast", 0)
            )
            self.quality.setCurrentIndex(
                data.get("quality_index", 2)
            )
            self.preset.setCurrentIndex(
                data.get("preset_index", 1)
            )
            self.audio_bitrate.setCurrentIndex(
                data.get("audio_index", 2)
            )
            self.external_audio.setText(
                data.get("external_audio", "")
            )
            self.audio_mode.setCurrentText(
                data.get("audio_mode", "Replace original")
            )
            self.music_volume.setValue(
                data.get("music_volume", 1.0)
            )
            self.original_volume.setValue(
                data.get("original_volume", 0.25)
            )
            self.loop_music.setChecked(
                data.get("loop_music", False)
            )
            self.fade_in.setValue(
                data.get("fade_in", 0.0)
            )
            self.fade_out.setValue(
                data.get("fade_out", 0.0)
            )
        except Exception:
            pass

    def apply_theme(self):
        self.setStyleSheet("""
        QMainWindow,QWidget {
            background:#09111f;
            color:#e5e7eb;
            font-family:Segoe UI,Arial;
            font-size:13px;
        }
        QGroupBox {
            background:#111827;
            border:1px solid #263449;
            border-radius:12px;
            margin-top:8px;
            padding:10px;
        }
        QGroupBox::title {
            color:#cbd5e1;
            subcontrol-origin:margin;
            left:12px;
            padding:0 5px;
        }
        #title {
            font-size:27px;
            font-weight:700;
            color:#f8fafc;
        }
        #muted {
            color:#94a3b8;
        }
        #ok {
            color:#4ade80;
            font-weight:600;
        }
        #bad {
            color:#fb7185;
            font-weight:600;
        }
        QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTextEdit,QListWidget {
            background:#0f172a;
            color:#e5e7eb;
            border:1px solid #334155;
            border-radius:8px;
            padding:7px;
        }
        QPushButton {
            background:#1e293b;
            color:#e5e7eb;
            border:1px solid #334155;
            border-radius:8px;
            padding:8px 12px;
        }
        QPushButton:hover {
            background:#334155;
        }
        QPushButton:disabled {
            color:#64748b;
        }
        QPushButton#primary {
            background:#6366f1;
            border-color:#6366f1;
            color:white;
            font-weight:700;
        }
        QTabBar::tab {
            background:#111827;
            color:#cbd5e1;
            border:1px solid #263449;
            padding:9px 14px;
            border-radius:7px;
        }
        QTabBar::tab:selected {
            background:#334155;
            color:white;
        }
        QProgressBar {
            background:#1e293b;
            border:0;
            height:8px;
            border-radius:4px;
        }
        QProgressBar::chunk {
            background:#6366f1;
            border-radius:4px;
        }
        QListWidget::item {
            padding:7px;
            border-radius:5px;
        }
        QListWidget::item:selected {
            background:#334155;
        }
        """)

    def closeEvent(self, event):
        if self.thread and self.thread.isRunning():
            QMessageBox.information(
                self,
                "Conversion running",
                "Please stop/wait for the current conversion before closing."
            )
            event.ignore()
            return

        self.save_settings()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("ShortForge")
    window = ShortForge()
    window.show()
    sys.exit(app.exec())
