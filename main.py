import importlib
import json
import os
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

from PyQt5.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtNetwork import QNetworkCookie
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
import yt_dlp


WEBENGINE_IMPORT_ERROR: Optional[str] = None
WEBENGINE_FORCE_FALLBACK: bool = False
WEBENGINE_RUNTIME_NOTES: List[str] = []


def evaluate_webengine_environment() -> None:
    """Collect runtime diagnostics and decide whether embedded login should be disabled."""
    global WEBENGINE_IMPORT_ERROR, WEBENGINE_FORCE_FALLBACK

    notes: List[str] = []
    try:
        pyqt_ver = version("PyQt5")
    except PackageNotFoundError:
        pyqt_ver = "未安装"

    try:
        web_ver = version("PyQtWebEngine")
    except PackageNotFoundError:
        web_ver = "未安装"

    notes.append(f"PyQt5={pyqt_ver}, PyQtWebEngine={web_ver}")

    if pyqt_ver == "未安装" or web_ver == "未安装":
        WEBENGINE_IMPORT_ERROR = "缺少 PyQt5 或 PyQtWebEngine 运行包"
        WEBENGINE_FORCE_FALLBACK = True
    elif pyqt_ver != web_ver:
        WEBENGINE_IMPORT_ERROR = f"版本不匹配：PyQt5={pyqt_ver}, PyQtWebEngine={web_ver}"
        WEBENGINE_FORCE_FALLBACK = True

    if sys.maxsize <= 2**32:
        notes.append("检测到 32 位 Python，WebEngine 稳定性可能较差")

    WEBENGINE_RUNTIME_NOTES.clear()
    WEBENGINE_RUNTIME_NOTES.extend(notes)


def init_webengine_runtime() -> None:
    """Initialize QtWebEngine runtime before QApplication for better compatibility."""
    global WEBENGINE_IMPORT_ERROR

    evaluate_webengine_environment()

    if sys.platform.startswith("win"):
        chromium_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        required_flags = ["--disable-gpu", "--disable-gpu-compositing"]
        for flag in required_flags:
            if flag not in chromium_flags:
                chromium_flags = f"{chromium_flags} {flag}".strip()
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = chromium_flags

        os.environ.setdefault("QT_OPENGL", "software")
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    try:
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    except Exception:  # noqa: BLE001
        pass

    try:
        webengine_module = importlib.import_module("PyQt5.QtWebEngine")
        qtwebengine = getattr(webengine_module, "QtWebEngine", None)
        if qtwebengine and hasattr(qtwebengine, "initialize"):
            qtwebengine.initialize()
    except Exception:  # noqa: BLE001
        pass

    if WEBENGINE_FORCE_FALLBACK:
        return

    try:
        importlib.import_module("PyQt5.QtWebEngineWidgets")
        WEBENGINE_IMPORT_ERROR = None
    except Exception as exc:  # noqa: BLE001
        WEBENGINE_IMPORT_ERROR = str(exc)




def should_use_embedded_login() -> bool:
    """Prefer embedded login by default, with env-based escape hatches."""
    if WEBENGINE_FORCE_FALLBACK:
        return False
    if os.environ.get("MORAN_DISABLE_EMBEDDED_LOGIN") == "1":
        return False
    if os.environ.get("MORAN_SAFE_LOGIN_MODE") == "1":
        return False
    if os.environ.get("MORAN_FORCE_EMBEDDED_LOGIN") == "1":
        return True
    return True


def load_webengine_view_class() -> Optional[Any]:
    """Dynamically load QWebEngineView so app can run without PyQtWebEngine."""
    global WEBENGINE_IMPORT_ERROR
    if WEBENGINE_FORCE_FALLBACK:
        return None
    try:
        module = importlib.import_module("PyQt5.QtWebEngineWidgets")
    except Exception as exc:  # noqa: BLE001
        WEBENGINE_IMPORT_ERROR = str(exc)
        return None

    view_cls = getattr(module, "QWebEngineView", None)
    if view_cls is None:
        WEBENGINE_IMPORT_ERROR = "QWebEngineView 不可用"
    else:
        WEBENGINE_IMPORT_ERROR = None
    return view_cls


PLATFORMS = {
    "网易云": {
        "domain": "music.163.com",
        "login_url": "https://music.163.com/#/login",
    },
    "酷狗": {
        "domain": "kugou.com",
        "login_url": "https://www.kugou.com/",
    },
    "酷我": {
        "domain": "kuwo.cn",
        "login_url": "https://www.kuwo.cn/",
    },
    "Bilibili": {
        "domain": "bilibili.com",
        "login_url": "https://www.bilibili.com/",
    },
}


@dataclass
class TrackResult:
    platform: str
    title: str
    artist: str
    duration: str
    webpage_url: str


class SearchWorker(QThread):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, keyword: str, platforms: List[str]) -> None:
        super().__init__()
        self.keyword = keyword
        self.platforms = platforms

    @staticmethod
    def _seconds_to_text(seconds: Optional[int]) -> str:
        if isinstance(seconds, int) and seconds >= 0:
            return f"{seconds // 60:02d}:{seconds % 60:02d}"
        return "--:--"

    @staticmethod
    def _read_json(url: str, *, headers: Optional[Dict[str, str]] = None, method: str = "GET", data: Optional[bytes] = None) -> Dict[str, Any]:
        req = Request(url, data=data, method=method)
        req.add_header("User-Agent", "Mozilla/5.0")
        if headers:
            for key, value in headers.items():
                req.add_header(key, value)
        with urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def _search_netease(self) -> List[TrackResult]:
        payload = urlencode({"s": self.keyword, "type": "1", "offset": "0", "limit": "12"}).encode("utf-8")
        data = self._read_json(
            "https://music.163.com/api/cloudsearch/pc",
            headers={"Referer": "https://music.163.com/", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
            data=payload,
        )
        songs = data.get("result", {}).get("songs", [])
        out: List[TrackResult] = []
        for song in songs[:5]:
            song_id = song.get("id")
            artists = song.get("ar") or song.get("artists") or []
            artist_text = "/".join(a.get("name", "") for a in artists if a.get("name")) or "未知作者"
            out.append(
                TrackResult(
                    platform="网易云",
                    title=song.get("name") or "未知标题",
                    artist=artist_text,
                    duration=self._seconds_to_text(int(song.get("dt", 0) / 1000) if song.get("dt") else None),
                    webpage_url=f"https://music.163.com/#/song?id={song_id}",
                )
            )
        return out

    def _search_kugou(self) -> List[TrackResult]:
        url = f"https://mobilecdn.kugou.com/api/v3/search/song?format=json&keyword={quote_plus(self.keyword)}&page=1&pagesize=12"
        data = self._read_json(url)
        infos = data.get("data", {}).get("info", [])
        out: List[TrackResult] = []
        for song in infos[:5]:
            song_id = song.get("hash")
            out.append(
                TrackResult(
                    platform="酷狗",
                    title=song.get("songname") or "未知标题",
                    artist=song.get("singername") or "未知作者",
                    duration=self._seconds_to_text(song.get("duration")),
                    webpage_url=f"https://www.kugou.com/song/#hash={song_id}",
                )
            )
        return out

    def _search_kuwo(self) -> List[TrackResult]:
        url = (
            "https://www.kuwo.cn/api/www/search/searchMusicBykeyWord?"
            f"key={quote_plus(self.keyword)}&pn=1&rn=12&httpsStatus=1&reqId="
        )
        data = self._read_json(url, headers={"Referer": "https://www.kuwo.cn/"})
        songs = data.get("data", {}).get("list", [])
        out: List[TrackResult] = []
        for song in songs[:5]:
            rid = song.get("rid")
            duration_text = song.get("duration")
            out.append(
                TrackResult(
                    platform="酷我",
                    title=song.get("name") or "未知标题",
                    artist=song.get("artist") or "未知作者",
                    duration=duration_text or "--:--",
                    webpage_url=f"https://www.kuwo.cn/play_detail/{rid}",
                )
            )
        return out

    def _search_bilibili(self) -> List[TrackResult]:
        url = (
            "https://api.bilibili.com/x/web-interface/search/type?"
            f"search_type=video&keyword={quote_plus(self.keyword)}&page=1"
        )
        data = self._read_json(url)
        videos = data.get("data", {}).get("result", [])
        out: List[TrackResult] = []
        for video in videos[:5]:
            bvid = video.get("bvid")
            title = (video.get("title") or "未知标题").replace("<em class=\"keyword\">", "").replace("</em>", "")
            out.append(
                TrackResult(
                    platform="Bilibili",
                    title=title,
                    artist=video.get("author") or "未知作者",
                    duration=video.get("duration") or "--:--",
                    webpage_url=f"https://www.bilibili.com/video/{bvid}",
                )
            )
        return out

    def run(self) -> None:
        merged: List[TrackResult] = []
        searchers = {
            "网易云": self._search_netease,
            "酷狗": self._search_kugou,
            "酷我": self._search_kuwo,
            "Bilibili": self._search_bilibili,
        }

        errors: List[str] = []
        for platform in self.platforms:
            searcher = searchers.get(platform)
            if not searcher:
                continue
            try:
                merged.extend(searcher())
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{platform}: {exc}")

        if not merged and errors:
            self.failed.emit("；".join(errors))
            return

        self.finished.emit(merged)


class ResolveWorker(QThread):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, track: TrackResult, cookie_file: Optional[str], browser_cookie_source: Optional[str]) -> None:
        super().__init__()
        self.track = track
        self.cookie_file = cookie_file
        self.browser_cookie_source = browser_cookie_source

    def _resolve_with_options(self, options: Dict[str, Any]) -> Optional[str]:
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(self.track.webpage_url, download=False)
                stream_url = info.get("url") if info else None
                if stream_url:
                    return stream_url
        except Exception:  # noqa: BLE001
            return None
        return None

    def run(self) -> None:
        base_options = {
            "quiet": True,
            "skip_download": True,
            "format": "bestaudio/best",
            "noplaylist": True,
        }

        # 1) plain resolve first
        stream_url = self._resolve_with_options(dict(base_options))
        if stream_url:
            self.finished.emit(stream_url)
            return

        # 2) cookie file resolve
        if self.cookie_file and os.path.exists(self.cookie_file):
            opts = dict(base_options)
            opts["cookiefile"] = self.cookie_file
            stream_url = self._resolve_with_options(opts)
            if stream_url:
                self.finished.emit(stream_url)
                return

        # 3) browser-cookie resolve (免导入)
        browsers = [self.browser_cookie_source] if self.browser_cookie_source else []
        for fallback in ["edge", "chrome", "firefox"]:
            if fallback not in browsers:
                browsers.append(fallback)

        for browser in browsers:
            opts = dict(base_options)
            opts["cookiesfrombrowser"] = (browser, None, None, None)
            stream_url = self._resolve_with_options(opts)
            if stream_url:
                self.finished.emit(stream_url)
                return

        self.failed.emit("未解析到可播放音频地址（已尝试普通解析、Cookie 文件与浏览器登录态）")


class LoginDialog(QDialog):
    cookies_exported = pyqtSignal(str, str)
    browser_cookie_selected = pyqtSignal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("平台登录")
        self.resize(900, 620)

        self.tabs = QTabWidget(self)
        self.tip_label = QLabel(
            "已切换为浏览器登录模式（不再使用内嵌 WebEngine）。\n"
            "在系统浏览器完成登录后，播放器会自动读取浏览器登录态，无需手动导入 Cookie 文件。"
        )

        self._build_browser_tabs()

        self.open_and_enable_button = QPushButton("打开浏览器登录并启用当前平台")
        self.close_button = QPushButton("关闭")

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.open_and_enable_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tip_label)
        layout.addWidget(self.tabs)
        layout.addLayout(button_layout)

        self.open_and_enable_button.clicked.connect(self.start_browser_login_flow)
        self.close_button.clicked.connect(self.accept)

    def _build_browser_tabs(self) -> None:
        for platform, conf in PLATFORMS.items():
            page = QWidget(self)
            page_layout = QVBoxLayout(page)
            desc = QLabel(f"{platform} 登录地址：{conf['login_url']}")
            desc.setWordWrap(True)
            tip = QLabel("点击下方按钮会打开系统浏览器，并自动启用该平台‘免导入登录态’模式。")
            tip.setWordWrap(True)
            open_btn = QPushButton("打开该平台登录页并启用")
            open_btn.clicked.connect(lambda _, p=platform: self.start_browser_login_flow(p))
            page_layout.addWidget(desc)
            page_layout.addWidget(tip)
            page_layout.addWidget(open_btn)
            page_layout.addStretch(1)
            self.tabs.addTab(page, platform)

    def _open_url(self, url: str) -> None:
        from PyQt5.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))

    def start_browser_login_flow(self, platform: Optional[str] = None) -> None:
        target_platform = platform or self.tabs.tabText(self.tabs.currentIndex())
        conf = PLATFORMS.get(target_platform)
        if not conf:
            QMessageBox.warning(self, "提示", "未找到平台配置")
            return

        self._open_url(conf["login_url"])

        browser = "edge" if sys.platform.startswith("win") else "chrome"
        self.browser_cookie_selected.emit(target_platform, browser)
        QMessageBox.information(
            self,
            "已启用",
            f"已打开 {target_platform} 登录页，并启用‘免导入浏览器登录态’。\n"
            f"请在 {browser} 浏览器完成登录后直接回到播放器点击播放即可。",
        )


class MusicPlayer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Moran 多平台音乐播放器")
        self.resize(1140, 760)

        self.player = QMediaPlayer(self)
        self.results: List[TrackResult] = []
        self.cookie_files: Dict[str, str] = {}
        self.browser_cookie_sources: Dict[str, str] = {}
        self.current_track: Optional[TrackResult] = None

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        title = QLabel("Moran 多平台音乐播放器")
        title.setObjectName("title")

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入歌曲名、歌手或关键字")
        self.search_button = QPushButton("聚合搜索")
        self.login_button = QPushButton("平台登录")

        self.platform_checks: Dict[str, QCheckBox] = {}
        platform_row = QHBoxLayout()
        for name in PLATFORMS:
            check = QCheckBox(name)
            check.setChecked(True)
            self.platform_checks[name] = check
            platform_row.addWidget(check)
        platform_row.addStretch(1)

        search_row = QHBoxLayout()
        search_row.addWidget(self.search_input, 6)
        search_row.addWidget(self.search_button, 1)
        search_row.addWidget(self.login_button, 1)

        self.result_table = QTableWidget(0, 5)
        self.result_table.setHorizontalHeaderLabels(["平台", "标题", "作者", "时长", "链接"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.add_button = QPushButton("加入播放列表")
        self.play_button = QPushButton("播放选中")
        self.pause_resume_button = QPushButton("暂停")

        self.search_button.setObjectName("primaryButton")
        self.play_button.setObjectName("primaryButton")
        self.add_button.setObjectName("subtleButton")
        self.login_button.setObjectName("subtleButton")
        self.pause_resume_button.setObjectName("subtleButton")

        control_row = QHBoxLayout()
        control_row.setSpacing(10)
        for button in [self.add_button, self.play_button, self.pause_resume_button]:
            control_row.addWidget(button)

        self.playlist = QListWidget()
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setObjectName("progressBar")
        self.progress.setRange(0, 0)
        self.status = QLabel("状态：就绪")

        layout.addWidget(title)
        layout.addLayout(search_row)
        layout.addLayout(platform_row)
        layout.addWidget(self.result_table, 5)
        layout.addLayout(control_row)
        layout.addWidget(QLabel("播放列表"))
        layout.addWidget(self.playlist, 3)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

        app_font = QFont("Microsoft YaHei UI", 10)
        self.setFont(app_font)

        self._setup_style()
        self._connect_signals()

    def _setup_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #0b1220;
                color: #e2e8f0;
                font-size: 14px;
                font-family: "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif;
            }
            QLineEdit, QListWidget, QTableWidget {
                background: #111827;
                border: 1px solid #334155;
                border-radius: 10px;
                padding: 7px;
            }
            QListWidget::item { padding: 5px 2px; }
            QListWidget::item:selected, QTableWidget::item:selected {
                background: #1d4ed8;
                color: #f8fafc;
            }
            QPushButton {
                border-radius: 10px;
                padding: 8px 16px;
                border: 1px solid transparent;
                font-weight: 600;
            }
            QPushButton#primaryButton {
                background: #2563eb;
                color: #ffffff;
            }
            QPushButton#primaryButton:hover { background: #1d4ed8; }
            QPushButton#subtleButton {
                background: #1e293b;
                color: #cbd5e1;
                border-color: #334155;
            }
            QPushButton#subtleButton:hover {
                background: #273449;
                color: #f1f5f9;
            }
            QHeaderView::section {
                background: #182235;
                color: #cbd5e1;
                padding: 7px;
                border: 0;
                font-weight: 600;
            }
            QSlider#progressBar::groove:horizontal {
                border: none;
                height: 8px;
                border-radius: 4px;
                background: #1f2937;
            }
            QSlider#progressBar::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #22d3ee, stop:1 #3b82f6);
                border-radius: 4px;
            }
            QSlider#progressBar::add-page:horizontal {
                background: #273549;
                border-radius: 4px;
            }
            QSlider#progressBar::handle:horizontal {
                width: 16px;
                margin: -4px 0;
                border-radius: 8px;
                border: 2px solid #dbeafe;
                background: #60a5fa;
            }
            QLabel#title { font-size: 30px; font-weight: 700; color: #93c5fd; padding: 6px 0; }
            """
        )

    def _connect_signals(self) -> None:
        self.search_button.clicked.connect(self.search_tracks)
        self.login_button.clicked.connect(self.open_login_dialog)
        self.add_button.clicked.connect(self.add_selected_to_playlist)
        self.play_button.clicked.connect(self.play_selected_playlist)
        self.pause_resume_button.clicked.connect(self.toggle_playback)

        self.result_table.doubleClicked.connect(self.add_selected_to_playlist)
        self.playlist.itemDoubleClicked.connect(self.play_playlist_item)

        self.player.positionChanged.connect(self.progress.setValue)
        self.player.durationChanged.connect(lambda d: self.progress.setRange(0, d))
        self.player.stateChanged.connect(self.on_player_state_changed)
        self.progress.sliderMoved.connect(self.player.setPosition)

    def selected_platforms(self) -> List[str]:
        return [name for name, check in self.platform_checks.items() if check.isChecked()]

    def search_tracks(self) -> None:
        keyword = self.search_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入搜索关键词")
            return

        platforms = self.selected_platforms()
        if not platforms:
            QMessageBox.warning(self, "提示", "请至少勾选一个平台")
            return

        self.status.setText("状态：正在聚合搜索...")
        self.search_button.setEnabled(False)
        self.search_worker = SearchWorker(keyword, platforms)
        self.search_worker.finished.connect(self.on_search_finished)
        self.search_worker.failed.connect(self.on_worker_failed)
        self.search_worker.finished.connect(lambda _: self.search_button.setEnabled(True))
        self.search_worker.failed.connect(lambda _: self.search_button.setEnabled(True))
        self.search_worker.start()

    def on_search_finished(self, results: List[TrackResult]) -> None:
        self.results = results
        self.result_table.setRowCount(len(results))
        for row, track in enumerate(results):
            self.result_table.setItem(row, 0, QTableWidgetItem(track.platform))
            self.result_table.setItem(row, 1, QTableWidgetItem(track.title))
            self.result_table.setItem(row, 2, QTableWidgetItem(track.artist))
            self.result_table.setItem(row, 3, QTableWidgetItem(track.duration))
            self.result_table.setItem(row, 4, QTableWidgetItem(track.webpage_url))
        self.status.setText(f"状态：搜索完成，共 {len(results)} 条结果")

    def on_worker_failed(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)
        self.status.setText("状态：请求失败")
        self.pause_resume_button.setText("播放")

    def add_selected_to_playlist(self) -> None:
        row = self.result_table.currentRow()
        if row < 0 or row >= len(self.results):
            QMessageBox.information(self, "提示", "请先在搜索结果中选择一项")
            return

        track = self.results[row]
        item = QListWidgetItem(f"[{track.platform}] {track.title} - {track.artist}")
        item.setData(Qt.UserRole, track)
        self.playlist.addItem(item)
        self.status.setText(f"状态：已加入播放列表 - {track.title}")

    def play_selected_playlist(self) -> None:
        item = self.playlist.currentItem()
        if not item and self.playlist.count() > 0:
            item = self.playlist.item(0)
            self.playlist.setCurrentItem(item)

        if not item:
            QMessageBox.information(self, "提示", "请先在播放列表选择一项")
            return

        track = item.data(Qt.UserRole)
        if self.current_track and self.current_track.webpage_url == track.webpage_url:
            if self.player.state() != QMediaPlayer.PlayingState:
                self.player.play()
                self.status.setText(f"状态：继续播放 {track.title}")
            return

        self.play_playlist_item(item)

    def play_playlist_item(self, item: QListWidgetItem) -> None:
        track = item.data(Qt.UserRole)
        cookie_file = self.cookie_files.get(track.platform)
        browser_cookie_source = self.browser_cookie_sources.get(track.platform)
        self.status.setText(f"状态：正在解析音频流 - {track.title}")
        self.play_button.setEnabled(False)

        self.resolve_worker = ResolveWorker(track, cookie_file, browser_cookie_source)
        self.resolve_worker.finished.connect(lambda url, t=track: self.on_stream_resolved(t, url))
        self.resolve_worker.failed.connect(self.on_worker_failed)
        self.resolve_worker.finished.connect(lambda _: self.play_button.setEnabled(True))
        self.resolve_worker.failed.connect(lambda _: self.play_button.setEnabled(True))
        self.resolve_worker.start()

    def on_stream_resolved(self, track: TrackResult, stream_url: str) -> None:
        self.current_track = track
        self.player.setMedia(QMediaContent(QUrl(stream_url)))
        self.player.play()
        self.status.setText(f"状态：正在播放 {track.title}")

    def toggle_playback(self) -> None:
        if not self.current_track:
            self.play_selected_playlist()
            return

        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.status.setText(f"状态：已暂停 {self.current_track.title}")
        else:
            self.player.play()
            self.status.setText(f"状态：正在播放 {self.current_track.title}")

    def on_player_state_changed(self, state: int) -> None:
        if state == QMediaPlayer.PlayingState:
            self.pause_resume_button.setText("暂停")
        else:
            self.pause_resume_button.setText("播放")

    def open_login_dialog(self) -> None:
        dialog = LoginDialog(self)
        dialog.cookies_exported.connect(self.on_cookies_exported)
        dialog.browser_cookie_selected.connect(self.on_browser_cookie_selected)
        dialog.exec_()

    def on_cookies_exported(self, platform: str, cookie_file: str) -> None:
        self.cookie_files[platform] = cookie_file
        self.status.setText(f"状态：{platform} 登录信息已保存")

    def on_browser_cookie_selected(self, platform: str, browser: str) -> None:
        self.browser_cookie_sources[platform] = browser
        self.status.setText(f"状态：{platform} 已启用免导入模式（{browser} 浏览器登录态）")


def main() -> None:
    app = QApplication(sys.argv)
    window = MusicPlayer()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
