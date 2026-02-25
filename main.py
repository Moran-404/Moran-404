import html
import re
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List
from urllib.parse import quote

import requests
from PyQt5.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
except Exception:
    QWebEngineView = None


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}


@dataclass
class Track:
    title: str
    artist: str
    source: str
    page_url: str


class SearchWorker(QThread):
    done = pyqtSignal(list, str)

    def __init__(self, keyword: str, sources: List[str]) -> None:
        super().__init__()
        self.keyword = keyword
        self.sources = sources

    def run(self) -> None:
        try:
            engines: Dict[str, Callable[[str], List[Track]]] = {
                "网易云": search_netease,
                "酷狗": search_kugou,
                "酷我": search_kuwo,
                "Bilibili": search_bilibili,
            }
            results: List[Track] = []
            for source in self.sources:
                results.extend(engines[source](self.keyword))
            self.done.emit(results, "")
        except Exception as exc:
            self.done.emit([], str(exc))


class ExtractWorker(QThread):
    done = pyqtSignal(str, str)

    def __init__(self, page_url: str) -> None:
        super().__init__()
        self.page_url = page_url

    def run(self) -> None:
        try:
            import yt_dlp

            ydl_opts = {
                "quiet": True,
                "skip_download": True,
                "format": "bestaudio/best",
                "noplaylist": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.page_url, download=False)
                if isinstance(info, dict) and "entries" in info and info["entries"]:
                    info = info["entries"][0]
                stream_url = info.get("url") if isinstance(info, dict) else None
                if not stream_url:
                    raise RuntimeError("未解析到可播放音频地址")
            self.done.emit(stream_url, "")
        except Exception as exc:
            self.done.emit("", str(exc))


class LoginDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("平台登录")
        self.resize(980, 700)

        layout = QVBoxLayout(self)
        if QWebEngineView is None:
            layout.addWidget(QLabel("未安装 PyQtWebEngine，无法内置网页登录。\n请安装: pip install PyQtWebEngine"))
            return

        hint = QLabel("选择一个平台打开登录页，登录后可在同页浏览内容（受平台策略限制）。")
        layout.addWidget(hint)

        button_layout = QHBoxLayout()
        self.view = QWebEngineView(self)
        links = {
            "网易云": "https://music.163.com",
            "酷狗": "https://www.kugou.com",
            "酷我": "https://www.kuwo.cn",
            "Bilibili": "https://passport.bilibili.com/login",
        }

        for name, url in links.items():
            btn = QPushButton(name)
            btn.clicked.connect(lambda _=False, u=url: self.view.setUrl(u))
            button_layout.addWidget(btn)

        layout.addLayout(button_layout)
        layout.addWidget(self.view)
        self.view.setUrl("https://music.163.com")


class MusicPlayerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Aurora Music · PyQt5")
        self.resize(1100, 700)
        self.player = QMediaPlayer(self)
        self.search_worker: SearchWorker | None = None
        self.extract_worker: ExtractWorker | None = None

        self.track_map: dict[int, Track] = {}
        self._build_ui()
        self._connect_signals()
        self._apply_stylesheet()

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        title = QLabel("Aurora Music")
        title.setObjectName("title")
        subtitle = QLabel("支持网易云 / 酷狗 / 酷我 / Bilibili 聚合搜索")
        subtitle.setObjectName("subtitle")
        main_layout.addWidget(title)
        main_layout.addWidget(subtitle)

        search_group = QGroupBox("歌曲搜索")
        sg_layout = QGridLayout(search_group)
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("输入歌名 / 歌手，例如：稻香")
        self.search_btn = QPushButton("搜索")
        self.login_btn = QPushButton("平台登录")

        self.source_boxes = [QCheckBox("网易云"), QCheckBox("酷狗"), QCheckBox("酷我"), QCheckBox("Bilibili")]
        for box in self.source_boxes:
            box.setChecked(True)

        sg_layout.addWidget(self.keyword_input, 0, 0, 1, 4)
        sg_layout.addWidget(self.search_btn, 0, 4)
        sg_layout.addWidget(self.login_btn, 0, 5)
        for idx, box in enumerate(self.source_boxes):
            sg_layout.addWidget(box, 1, idx)

        main_layout.addWidget(search_group)

        splitter = QSplitter(Qt.Horizontal)
        self.result_list = QListWidget()
        self.result_list.setObjectName("resultList")
        self.playlist = QListWidget()
        self.playlist.setObjectName("playlist")
        splitter.addWidget(self.result_list)
        splitter.addWidget(self.playlist)
        splitter.setSizes([620, 420])
        main_layout.addWidget(splitter)

        ctrl_layout = QHBoxLayout()
        self.add_btn = QPushButton("加入播放列表")
        self.play_btn = QPushButton("播放")
        self.pause_btn = QPushButton("暂停")
        self.stop_btn = QPushButton("停止")
        self.remove_btn = QPushButton("移除")
        for b in [self.add_btn, self.play_btn, self.pause_btn, self.stop_btn, self.remove_btn]:
            ctrl_layout.addWidget(b)
        main_layout.addLayout(ctrl_layout)

        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, 0)
        main_layout.addWidget(self.progress)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪")

    def _connect_signals(self) -> None:
        self.search_btn.clicked.connect(self.search_music)
        self.keyword_input.returnPressed.connect(self.search_music)
        self.add_btn.clicked.connect(self.add_to_playlist)
        self.play_btn.clicked.connect(self.play_selected)
        self.pause_btn.clicked.connect(self.player.pause)
        self.stop_btn.clicked.connect(self.player.stop)
        self.remove_btn.clicked.connect(lambda: self.playlist.takeItem(self.playlist.currentRow()))
        self.result_list.itemDoubleClicked.connect(lambda _: self.add_to_playlist())
        self.playlist.itemDoubleClicked.connect(self.play_item)
        self.progress.sliderMoved.connect(self.player.setPosition)
        self.login_btn.clicked.connect(self.open_login)

        self.player.positionChanged.connect(self.progress.setValue)
        self.player.durationChanged.connect(lambda d: self.progress.setRange(0, d))

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #0f172a; color: #e2e8f0; }
            QLabel#title { font-size: 28px; font-weight: 700; color: #f8fafc; }
            QLabel#subtitle { color: #94a3b8; margin-bottom: 8px; }
            QGroupBox { border: 1px solid #334155; border-radius: 12px; margin-top: 8px; padding: 10px; color: #cbd5e1; }
            QLineEdit, QListWidget { background: #111827; border: 1px solid #374151; border-radius: 10px; padding: 8px; color: #f1f5f9; }
            QPushButton { background: #2563eb; color: white; border: none; border-radius: 10px; padding: 8px 14px; font-weight: 600; }
            QPushButton:hover { background: #3b82f6; }
            QStatusBar { color: #cbd5e1; }
            QSlider::groove:horizontal { background: #334155; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #60a5fa; width: 14px; margin: -5px 0; border-radius: 7px; }
            """
        )

    def selected_sources(self) -> List[str]:
        return [box.text() for box in self.source_boxes if box.isChecked()]

    def search_music(self) -> None:
        keyword = self.keyword_input.text().strip()
        sources = self.selected_sources()
        if not keyword:
            QMessageBox.information(self, "提示", "请输入关键词")
            return
        if not sources:
            QMessageBox.information(self, "提示", "请至少选择一个音乐源")
            return

        self.search_btn.setEnabled(False)
        self.status.showMessage(f"正在搜索：{keyword}")
        self.result_list.clear()
        self.track_map.clear()

        self.search_worker = SearchWorker(keyword, sources)
        self.search_worker.done.connect(self.on_search_done)
        self.search_worker.start()

    def on_search_done(self, tracks: List[Track], error: str) -> None:
        self.search_btn.setEnabled(True)
        if error:
            self.status.showMessage("搜索失败")
            QMessageBox.critical(self, "搜索失败", error)
            return

        for idx, track in enumerate(tracks):
            item = QListWidgetItem(f"[{track.source}] {track.title} - {track.artist}")
            self.result_list.addItem(item)
            self.track_map[idx] = track

        self.status.showMessage(f"搜索完成，共 {len(tracks)} 条")

    def add_to_playlist(self) -> None:
        row = self.result_list.currentRow()
        if row < 0:
            return
        track = self.track_map.get(row)
        if not track:
            return
        item = QListWidgetItem(f"[{track.source}] {track.title} - {track.artist}")
        item.setData(Qt.UserRole, track.page_url)
        self.playlist.addItem(item)
        self.status.showMessage(f"已加入播放列表：{track.title}")

    def play_selected(self) -> None:
        item = self.playlist.currentItem()
        if item:
            self.play_item(item)

    def play_item(self, item: QListWidgetItem) -> None:
        page_url = item.data(Qt.UserRole)
        if not page_url:
            return
        self.status.showMessage("正在解析播放链接...")

        self.extract_worker = ExtractWorker(page_url)
        self.extract_worker.done.connect(self.on_extract_done)
        self.extract_worker.start()

    def on_extract_done(self, stream_url: str, error: str) -> None:
        if error:
            self.status.showMessage("播放失败")
            QMessageBox.critical(self, "播放失败", f"解析失败：{error}\n\n提示：某些平台可能需要登录或会员权限。")
            return
        self.player.setMedia(QMediaContent(QUrl(stream_url)))
        self.player.play()
        self.status.showMessage("正在播放")

    def open_login(self) -> None:
        dlg = LoginDialog(self)
        dlg.exec_()


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def search_netease(keyword: str) -> List[Track]:
    url = "https://music.163.com/api/search/get/web"
    params = {"s": keyword, "type": 1, "offset": 0, "total": "true", "limit": 15}
    headers = {**HEADERS, "Referer": "https://music.163.com/"}
    data = requests.get(url, params=params, headers=headers, timeout=10).json()
    songs = data.get("result", {}).get("songs", [])
    result = []
    for s in songs:
        sid = s.get("id")
        artists = "/".join(a.get("name", "") for a in s.get("artists", []))
        result.append(Track(s.get("name", "未知歌曲"), artists or "未知歌手", "网易云", f"https://music.163.com/#/song?id={sid}"))
    return result


def search_kugou(keyword: str) -> List[Track]:
    url = "https://songsearch.kugou.com/song_search_v2"
    params = {"keyword": keyword, "page": 1, "pagesize": 15}
    data = requests.get(url, params=params, headers=HEADERS, timeout=10).json()
    songs = data.get("data", {}).get("lists", [])
    result = []
    for s in songs:
        name = _clean_html(s.get("SongName", "未知歌曲"))
        singer = _clean_html(s.get("SingerName", "未知歌手"))
        h = s.get("FileHash")
        aid = s.get("AlbumID", 0)
        result.append(Track(name, singer, "酷狗", f"https://www.kugou.com/song/#hash={h}&album_id={aid}"))
    return result


def search_kuwo(keyword: str) -> List[Track]:
    token = "kw_token"
    headers = {**HEADERS, "Referer": "https://www.kuwo.cn/", "csrf": token, "Cookie": f"kw_token={token}"}
    url = "https://www.kuwo.cn/api/www/search/searchMusicBykeyWord"
    params = {"key": keyword, "pn": 1, "rn": 15, "httpsStatus": 1}
    data = requests.get(url, params=params, headers=headers, timeout=10).json()
    songs = data.get("data", {}).get("list", [])
    result = []
    for s in songs:
        rid = s.get("rid") or s.get("musicrid", "")
        result.append(Track(s.get("name", "未知歌曲"), s.get("artist", "未知歌手"), "酷我", f"https://www.kuwo.cn/play_detail/{rid}"))
    return result


def search_bilibili(keyword: str) -> List[Track]:
    url = "https://api.bilibili.com/x/web-interface/search/type"
    params = {"search_type": "video", "keyword": keyword, "page": 1}
    data = requests.get(url, params=params, headers=HEADERS, timeout=10).json()
    items = data.get("data", {}).get("result", [])[:15]
    result = []
    for v in items:
        title = _clean_html(v.get("title", "未知标题"))
        artist = v.get("author", "UP主")
        bvid = v.get("bvid", "")
        result.append(Track(title, artist, "Bilibili", f"https://www.bilibili.com/video/{bvid}"))
    return result


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    window = MusicPlayerWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
