"""PieCAD Dockable Panel for FreeCAD - Pure UI Client."""

import base64
import json
import urllib.request
import urllib.error

import FreeCADGui as Gui

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
    except ImportError:
        from PySide import QtGui as QtWidgets
        from PySide import QtCore
        from PySide import QtGui


class RequestWorker(QtCore.QThread):
    response_received = QtCore.Signal(str)
    error_occurred = QtCore.Signal(str)

    def __init__(self, url: str, message: str):
        super().__init__()
        self.url = url
        self.message = message

    def run(self):
        payload = json.dumps({"message": self.message}).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120.0) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                self.response_received.emit(res_data.get("reply", "No reply."))
        except urllib.error.URLError as e:
            self.error_occurred.emit(f"Backend unreachable: {e.reason}")
        except Exception as e:
            self.error_occurred.emit(f"Error: {str(e)}")


class PieCADPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__("PieCAD Copilot", parent)
        self.setObjectName("PieCADPanel")
        self.worker = None
        self.init_ui()

    def init_ui(self):
        main_widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(main_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Status Label
        self.status_label = QtWidgets.QLabel("Ready | Connected to Core")
        self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px;")
        layout.addWidget(self.status_label)

        # Chat / Action Log
        self.chat_history = QtWidgets.QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #333;
                border-radius: 6px;
                font-family: Consolas, monospace;
                font-size: 12px;
                padding: 6px;
            }
        """)
        layout.addWidget(self.chat_history)

        # Input Box
        self.prompt_input = QtWidgets.QLineEdit()
        self.prompt_input.setPlaceholderText("Describe CAD shape (e.g. 'Make a 100x50x20 box')...")
        self.prompt_input.setStyleSheet("""
            QLineEdit {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 8px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #007acc;
            }
        """)
        self.prompt_input.returnPressed.connect(self.send_message)
        layout.addWidget(self.prompt_input)

        # Action Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self.send_btn = QtWidgets.QPushButton("Generate")
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #0098ff;
            }
            QPushButton:disabled {
                background-color: #444;
                color: #888;
            }
        """)
        self.send_btn.clicked.connect(self.send_message)

        self.pair_btn = QtWidgets.QPushButton("Pair Phone")
        self.pair_btn.setStyleSheet("""
            QPushButton {
                background-color: #3c3489;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #534ab7;
            }
        """)
        self.pair_btn.clicked.connect(self.open_pairing_dialog)

        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #333;
                color: #bbb;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #444;
            }
        """)
        self.clear_btn.clicked.connect(self.chat_history.clear)

        btn_layout.addWidget(self.send_btn)
        btn_layout.addWidget(self.pair_btn)
        btn_layout.addWidget(self.clear_btn)
        layout.addLayout(btn_layout)

        self.setWidget(main_widget)

    def append_log(self, sender: str, message: str, color: str = "#ffffff"):
        self.chat_history.append(f'<span style="color:{color}; font-weight:bold;">{sender}:</span> {message}<br>')

    def send_message(self):
        text = self.prompt_input.text().strip()
        if not text:
            return

        self.append_log("You", text, "#4fc3f7")
        self.prompt_input.clear()
        self.send_btn.setEnabled(False)
        self.status_label.setText("Processing with LLM...")
        self.status_label.setStyleSheet("color: #FFC107; font-weight: bold; font-size: 11px;")

        self.worker = RequestWorker("http://127.0.0.1:8000/chat", text)
        self.worker.response_received.connect(self.on_success)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.start()

    def on_success(self, reply: str):
        self.append_log("PieCAD", reply, "#81c784")
        self.status_label.setText("Ready")
        self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px;")
        self.send_btn.setEnabled(True)

    def on_error(self, error_msg: str):
        self.append_log("System Error", error_msg, "#e57373")
        self.status_label.setText("Error")
        self.status_label.setStyleSheet("color: #e57373; font-weight: bold; font-size: 11px;")
        self.send_btn.setEnabled(True)

    def open_pairing_dialog(self):
        """Request a pairing session from the backend and show its QR code.

        Synchronous/blocking on purpose -- this is a local call to a
        backend running on the same machine, and it's a one-off action
        (not on the hot chat path), so a RequestWorker thread would be
        overkill here.
        """
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8000/api/session",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as e:
            self.append_log("System Error", f"Could not start pairing session: {e}", "#e57373")
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Pair PieCAD Remote")
        layout = QtWidgets.QVBoxLayout(dialog)

        img_label = QtWidgets.QLabel()
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(base64.b64decode(data["qr_png_base64"]))
        img_label.setPixmap(pixmap.scaledToWidth(240))
        img_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(img_label)

        url_label = QtWidgets.QLabel(data["url"])
        url_label.setWordWrap(True)
        url_label.setStyleSheet("color: #bbb; font-size: 11px;")
        layout.addWidget(url_label)

        hint = QtWidgets.QLabel("Scan with your phone's camera on the same Wi-Fi network.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(hint)

        dialog.exec()


def load_in_freecad():
    main_window = Gui.getMainWindow()
    existing = main_window.findChild(QtWidgets.QDockWidget, "PieCADPanel")
    if existing:
        main_window.removeDockWidget(existing)
        existing.deleteLater()

    panel = PieCADPanel(main_window)
    main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()


# Directly execute load when run via exec()
load_in_freecad()
