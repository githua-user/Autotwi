import asyncio
import contextlib
import os
import re
import sys
import traceback

from PyQt6 import QtCore, QtGui, QtWidgets
from scrape_twitter import extract_texts_to_txt, run

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

#把 print 输出通过 Qt 信号转发到界面日志
class _SignalStream:
    def __init__(self, log_signal):
        self._log_signal = log_signal
    def write(self, text):
        if text:
            self._log_signal.emit(text)
    def flush(self):
        pass

#在后台线程运行异步抓取逻辑
class ScrapeWorker(QtCore.QThread):
    log_signal = QtCore.pyqtSignal(str)
    finished_signal = QtCore.pyqtSignal(bool, str)

    def __init__(self, username, limit, wait, cookies, parent=None):
        super().__init__(parent)
        self.username = username
        self.limit = limit
        self.wait_seconds = wait
        self.cookies = cookies
        self._loop = None
        self._task = None

    def run(self):
        try:
            with contextlib.redirect_stdout(_SignalStream(self.log_signal)):
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                try:
                    self._task = self._loop.create_task(
                        run(self.username, self.limit, self.wait_seconds,
                             self.cookies)
                    )
                    self._loop.run_until_complete(self._task)
                finally:
                    self._task = None
                    self._loop.close()
                    self._loop = None
        except asyncio.CancelledError:
            self.finished_signal.emit(True, "")
        except BaseException:
            self.finished_signal.emit(False, traceback.format_exc())
        else:
            self.finished_signal.emit(False, "")

    def request_stop(self):
        if self._loop is not None and self._task is not None:
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
            except RuntimeError:
                pass  

class MainWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.setWindowTitle("X 推文爬取")
        self.resize(600, 600)
        self._build_ui()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)

        # 抓取参数
        params_box = QtWidgets.QGroupBox("抓取参数")
        form = QtWidgets.QFormLayout(params_box)
        self.edit_user = QtWidgets.QLineEdit(
            placeholderText="URL上的用户名")
        form.addRow("目标用户:", self.edit_user)
        self.spin_limit = QtWidgets.QLineEdit(
            placeholderText="整数")
        self.spin_limit.setValidator(QtGui.QIntValidator(0, 10000, self))
        form.addRow("抓取上限:", self.spin_limit)
        self.spin_wait = QtWidgets.QLineEdit(
            placeholderText="小数/s",
            )
        self.spin_wait.setValidator(QtGui.QDoubleValidator(0.0, 999.99, 2, self)) 
        form.addRow("翻页间隔:", self.spin_wait)
        root.addWidget(params_box)

        # Cookie 登录
        login_box = QtWidgets.QGroupBox("Cookie 登录")
        login_form = QtWidgets.QFormLayout(login_box)
        hint = QtWidgets.QLabel(   
            "获取方法：浏览器登录 x.com → F12 → Application → Cookies\n"
            "分别复制 auth_token 与 ct0 的值填入下方。"
        )
        hint.setWordWrap(True)
        login_form.addRow(hint)
        self.edit_auth_token = QtWidgets.QLineEdit()
        self.edit_ct0 = QtWidgets.QLineEdit()
        login_form.addRow("auth_token:", self.edit_auth_token)
        login_form.addRow("ct0:", self.edit_ct0)
        root.addWidget(login_box)

        # 操作行
        bar = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("开始抓取")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QtWidgets.QPushButton("停止", enabled=False)
        self.btn_stop.clicked.connect(self._stop)
        self.progress = QtWidgets.QProgressBar()  # 默认区间 0~100
        self.status_label = QtWidgets.QLabel("就绪")
        bar.addWidget(self.btn_start)
        bar.addWidget(self.btn_stop)
        bar.addWidget(self.progress)
        bar.addWidget(self.status_label)
        root.addLayout(bar)

        # 提取
        export_bar = QtWidgets.QHBoxLayout()
        self.btn_export = QtWidgets.QPushButton("提取jsonl中的text到同名txt")
        self.btn_export.clicked.connect(self._export_texts)
        export_bar.addStretch(1)
        export_bar.addWidget(self.btn_export)
        root.addLayout(export_bar)

        # 日志
        log_box = QtWidgets.QGroupBox("日志")
        log_layout = QtWidgets.QVBoxLayout(log_box)
        self.log_view = QtWidgets.QPlainTextEdit(
            readOnly=True, maximumBlockCount=2000)
        self.log_view.setFont(QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.FixedFont))
        log_layout.addWidget(self.log_view)
        root.addWidget(log_box)

    def _start(self):
        username = self.edit_user.text().strip()
        if not username:
            QtWidgets.QMessageBox.warning(self, "", "请填写目标URL")
            return

        auth_token = self.edit_auth_token.text().strip()
        ct0 = self.edit_ct0.text().strip()
        if not auth_token or not ct0:
            QtWidgets.QMessageBox.warning(self, "", "请同时填写 auth_token 与 ct0")
            return
        cookies = {"auth_token": auth_token, "ct0": ct0}

        self.log_view.clear()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        limit = int(self.spin_limit.text() or 0)
        if limit > 0:
            self.progress.setRange(0, limit)
            self.progress.setValue(0)
        else:
            self.progress.setRange(0, 0)  # 不限条数：显示忙碌动画
        self.status_label.setText("启动中...")
        self._append_log(f"开始抓取 @{username} ...\n")

        self.worker = ScrapeWorker(
            username=username,
            limit=limit,
            wait=float(self.spin_wait.text() or 0),
            cookies=cookies,
            parent=self,
        )
        self.worker.log_signal.connect(self._on_log)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()

    def _stop(self):
        if self.worker is not None and self.worker.isRunning() :
            self.worker.request_stop()
            self.btn_stop.setEnabled(False)
            self.status_label.setText("正在停止...")
            self._append_log("\n[已请求停止，将在当前网络请求完成后结束]\n")

    #接收print显示进度
    def _on_log(self, text):
        self._append_log(text)
        match = re.search(r"(?:推文类|回复类)\s*(\d+)\s*条", text)
        if match:
            saved = int(match.group(1))
            self.status_label.setText(f"已抓取 {saved} 条")
            if self.progress.maximum() > 0:
                self.progress.setValue(min(saved, self.progress.maximum()))

    def _on_finished(self, stopped, error):
        if error:
            self._append_log("\n=== 任务出错 ===\n" + error)
        elif stopped:
            self._append_log("\n=== 已停止 ===\n")
        else:
            self._append_log("\n=== 任务完成 ===\n")
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
        self.status_label.setText("出错" if error else "已停止" if stopped else "已完成")
        self.worker.wait()  
        self.worker.deleteLater()
        self.worker = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
  
    def _append_log(self, text):
        self.log_view.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        self.log_view.insertPlainText(text)
        
    def closeEvent(self, event): 
        if self.worker is not None and self.worker.isRunning():
            answer = QtWidgets.QMessageBox.question(
                self,
                "退出",
                "抓取仍在进行中，确定要退出吗？",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.request_stop()
            if not self.worker.wait(5000):
                self.worker.terminate()  
                self.worker.wait(1000)
        event.accept()

     #提取当前文件夹下每个 jsonl 的 text 到同名 txt
    def _export_texts(self):
        if self.worker is not None and self.worker.isRunning():
            QtWidgets.QMessageBox.warning(self, "", "抓取进行中，请等待完成后再提取")
            return
        try:
            count = extract_texts_to_txt(APP_DIR)
        except Exception:
            self._append_log("\n=== 提取出错 ===\n" + traceback.format_exc())
            QtWidgets.QMessageBox.warning(self, "", "提取失败，详情见日志")
            return
        if count == 0:
            QtWidgets.QMessageBox.information(self, "", "未提取到任何内容，请确认文件夹下有 jsonl 文件")
            return
        self.status_label.setText(f"已提取 {count} 条")

def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.exec()
    sys.exit()

if __name__ == "__main__":
    main()
