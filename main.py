import sys
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class OnlineMusicPlayer(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyQt5 在线音乐播放器")
        self.resize(720, 420)

        self.player = QMediaPlayer(self)
        self.tracks: list[tuple[str, str]] = []

        self.url_input = QLineEdit(self)
        self.url_input.setPlaceholderText("输入音频 URL（如 https://...mp3）")

        self.name_input = QLineEdit(self)
        self.name_input.setPlaceholderText("歌曲名称（可选）")

        self.add_button = QPushButton("添加到播放列表", self)
        self.play_button = QPushButton("播放", self)
        self.pause_button = QPushButton("暂停", self)
        self.stop_button = QPushButton("停止", self)
        self.delete_button = QPushButton("删除选中", self)
        self.local_button = QPushButton("导入本地文件", self)

        self.list_widget = QListWidget(self)
        self.status_label = QLabel("状态：就绪", self)

        self.position_slider = QSlider(self)
        self.position_slider.setOrientation(Qt.Horizontal)
        self.position_slider.setRange(0, 0)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        input_layout = QHBoxLayout()
        input_layout.addWidget(self.url_input, 3)
        input_layout.addWidget(self.name_input, 2)
        input_layout.addWidget(self.add_button, 1)

        control_layout = QHBoxLayout()
        for button in [
            self.play_button,
            self.pause_button,
            self.stop_button,
            self.delete_button,
            self.local_button,
        ]:
            control_layout.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addLayout(input_layout)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.position_slider)
        layout.addLayout(control_layout)
        layout.addWidget(self.status_label)

    def _connect_signals(self) -> None:
        self.add_button.clicked.connect(self.add_track)
        self.play_button.clicked.connect(self.play_selected)
        self.pause_button.clicked.connect(self.player.pause)
        self.stop_button.clicked.connect(self.player.stop)
        self.delete_button.clicked.connect(self.delete_selected)
        self.local_button.clicked.connect(self.import_local_file)

        self.list_widget.itemDoubleClicked.connect(self.play_item)

        self.player.stateChanged.connect(self.on_state_changed)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)

        self.position_slider.sliderMoved.connect(self.player.setPosition)

    def add_track(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入音频 URL")
            return

        name = self.name_input.text().strip() or url.split("/")[-1] or "未命名音频"
        self.tracks.append((name, url))

        item = QListWidgetItem(name)
        item.setData(256, url)  # Qt.UserRole
        self.list_widget.addItem(item)

        self.url_input.clear()
        self.name_input.clear()
        self.status_label.setText(f"状态：已添加 {name}")

    def import_local_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频文件",
            "",
            "Audio Files (*.mp3 *.wav *.ogg *.flac);;All Files (*)",
        )
        if not file_path:
            return

        name = file_path.split("/")[-1]
        item = QListWidgetItem(name)
        item.setData(256, file_path)
        self.list_widget.addItem(item)
        self.status_label.setText(f"状态：已导入 {name}")

    def play_selected(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一首歌曲")
            return
        self.play_item(item)

    def play_item(self, item: QListWidgetItem) -> None:
        source = item.data(256)
        if source.startswith("http://") or source.startswith("https://"):
            media = QMediaContent(QUrl(source))
        else:
            media = QMediaContent(QUrl.fromLocalFile(source))

        self.player.setMedia(media)
        self.player.play()
        self.status_label.setText(f"状态：正在播放 {item.text()}")

    def delete_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        removed = self.list_widget.takeItem(row)
        self.status_label.setText(f"状态：已删除 {removed.text()}")

    def on_state_changed(self, state: QMediaPlayer.State) -> None:
        state_text = {
            QMediaPlayer.StoppedState: "停止",
            QMediaPlayer.PlayingState: "播放中",
            QMediaPlayer.PausedState: "已暂停",
        }.get(state, "未知")
        self.status_label.setText(f"状态：{state_text}")

    def on_position_changed(self, position: int) -> None:
        self.position_slider.setValue(position)

    def on_duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, duration)

    def on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.InvalidMedia:
            QMessageBox.critical(self, "错误", "无法播放该音频，请检查 URL 或音频格式")
            self.status_label.setText("状态：播放失败")


def main() -> None:
    app = QApplication(sys.argv)
    window = OnlineMusicPlayer()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
