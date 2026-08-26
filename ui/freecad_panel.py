"""PieCAD Dockable Panel for FreeCAD."""

import sys
import json
import urllib.request
import urllib.error

# FreeCAD provides PySide / PySide6 / PySide2 internally
try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
    except ImportError:
        from PySide import QtGui as QtWidgets
        from PySide import QtCore, QtGui


class PieCADPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__("PieCAD Copilot", parent)
        self.setObjectName("PieCADPanel")
        self.init_ui()

    def init_ui(self):
        main_widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(main_widget)

        # 1. Server Configuration
        config_layout = QtWidgets.QHBoxLayout()
        self.server_input = QtWidgets.QLineEdit("http://localhost:8000/chat")
        self.server_input.setPlaceholderText("Backend URL (e.g. http://localhost:8000/chat)")
        config_layout.addWidget(QtWidgets.QLabel("Server:"))
        config_layout.addWidget(self.server_input)
        layout.addLayout(config_layout)

        # 2. Chat / Log Output
        self.chat_history = QtWidgets.QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setPlaceholderText("PieCAD activity and responses will appear here...")
        layout.addWidget(self.chat_history)

        # 3. Prompt Input Box
        self.prompt_input = QtWidgets.QLineEdit()
        self.prompt_input.setPlaceholderText("Describe what to create (e.g. 'Create a 10x10x10 box')...")
        self.prompt_input.returnPressed.connect(self.send_message)
        layout.addWidget(self.prompt_input)

        # 4. Action Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.clicked.connect(self.send_message)
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.clicked.connect(self.chat_history.clear)
        
        btn_layout.addWidget(self.send_btn)
        btn_layout.addWidget(self.clear_btn)
        layout.addLayout(btn_layout)

        self.setWidget(main_widget)

    def append_log(self, sender: str, message: str):
        self.chat_history.append(f"<b>{sender}:</b> {message}<br>")

    def send_message(self):
        text = self.prompt_input.text().strip()
        if not text:
            return

        self.append_log("You", text)
        self.prompt_input.clear()

        url = self.server_input.text().strip()
        payload = json.dumps({"message": text}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                reply = res_data.get("reply", "No reply received.")
                self.append_log("PieCAD", reply)
        except urllib.error.URLError as e:
            self.append_log("System Error", f"Could not reach backend: {e.reason}")
        except Exception as e:
            self.append_log("System Error", f"Unexpected error: {str(e)}")


def load_in_freecad():
    """Loads this dock panel directly into the active FreeCAD main window."""
    import FreeCADGui
    main_window = FreeCADGui.getMainWindow()
    
    # Remove existing panel instance if reloading
    existing = main_window.findChild(QtWidgets.QDockWidget, "PieCADPanel")
    if existing:
        main_window.removeDockWidget(existing)
        existing.deleteLater()

    panel = PieCADPanel(main_window)
    main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()


if __name__ == "__main__":
    # If run inside FreeCAD's Python console:
    load_in_freecad()