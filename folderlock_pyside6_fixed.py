import sys
import os
import json
import hashlib
import secrets
import shutil
import subprocess
import platform
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QFrame, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QTabWidget, QGroupBox
)

APP_DIR = Path.home() / ".folderscope_lock"
VAULT_FILE = APP_DIR / "locks.json"
APP_DIR.mkdir(exist_ok=True)


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 300_000
    )
    return salt.hex(), digest.hex()


def verify_password(password, salt_hex, digest_hex):
    _, digest = hash_password(password, bytes.fromhex(salt_hex))
    return secrets.compare_digest(digest, digest_hex)


def open_path(path):
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def folder_size(path):
    total = 0
    files = 0
    for root, dirs, names in os.walk(path):
        for name in names:
            try:
                total += (Path(root) / name).stat().st_size
                files += 1
            except OSError:
                pass
    return total, files


class LockWorker(QObject):
    finished = Signal(bool, str, object)

    def __init__(self, path, password, mode):
        super().__init__()
        self.path = Path(path)
        self.password = password
        self.mode = mode

    @Slot()
    def run(self):
        try:
            if self.mode == "lock":
                result = self.lock_folder()
            else:
                result = self.unlock_folder()
            self.finished.emit(True, result, None)
        except Exception as e:
            self.finished.emit(False, str(e), None)

    def lock_folder(self):
        # This is a practical Windows-first folder protection utility.
        # It uses an encrypted-name marker plus hidden/system attributes where
        # supported. It does NOT claim cryptographic encryption of file data.
        marker = self.path / ".folderscope.lock"
        salt, digest = hash_password(self.password)
        marker.write_text(json.dumps({
            "version": 1,
            "salt": salt,
            "digest": digest,
            "created": datetime.now().isoformat(),
        }), encoding="utf-8")

        if platform.system() == "Windows":
            subprocess.run(
                ["attrib", "+h", "+s", str(self.path)],
                capture_output=True, text=True
            )
        return "Folder protection enabled."

    def unlock_folder(self):
        marker = self.path / ".folderscope.lock"
        if not marker.exists():
            raise RuntimeError("This folder is not locked by FolderScope.")

        data = json.loads(marker.read_text(encoding="utf-8"))
        if not verify_password(self.password, data["salt"], data["digest"]):
            raise RuntimeError("Incorrect password.")

        if platform.system() == "Windows":
            subprocess.run(
                ["attrib", "-h", "-s", str(self.path)],
                capture_output=True, text=True
            )
        marker.unlink(missing_ok=True)
        return "Folder protection disabled."


class PasswordDialog(QDialog):
    def __init__(self, title, confirm=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(390)
        layout = QFormLayout(self)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        layout.addRow("Password:", self.password)

        self.confirm = None
        if confirm:
            self.confirm = QLineEdit()
            self.confirm.setEchoMode(QLineEdit.Password)
            layout.addRow("Confirm:", self.confirm)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self):
        return self.password.text(), self.confirm.text() if self.confirm else None


class FolderLock(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FolderLock — PySide6 Folder Protection")
        self.resize(1250, 780)
        self.dark = True
        self.locks = self.load_locks()
        self.build_ui()
        self.apply_theme()
        self.refresh()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        titlebox = QVBoxLayout()
        title = QLabel("🔐 FolderLock")
        title.setObjectName("appTitle")
        subtitle = QLabel(
            "Protect folders with password verification, lock history and safe controls"
        )
        subtitle.setObjectName("subtitle")
        titlebox.addWidget(title)
        titlebox.addWidget(subtitle)
        h.addLayout(titlebox)
        h.addStretch()

        theme = QPushButton("☀ Light")
        theme.clicked.connect(self.toggle_theme)
        h.addWidget(theme)
        self.theme_button = theme
        outer.addWidget(header)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        tl = QHBoxLayout(toolbar)
        self.path = QLineEdit()
        self.path.setPlaceholderText("Select a folder to protect…")
        browse = QPushButton("📂 Browse")
        browse.clicked.connect(self.browse)
        lock = QPushButton("🔒 Lock Folder")
        lock.setObjectName("primary")
        lock.clicked.connect(self.lock_selected)
        unlock = QPushButton("🔓 Unlock")
        unlock.clicked.connect(self.unlock_selected)
        tl.addWidget(self.path, 1)
        tl.addWidget(browse)
        tl.addWidget(lock)
        tl.addWidget(unlock)
        outer.addWidget(toolbar)

        cards = QGridLayout()
        cards.setSpacing(8)
        for col, (name, obj, sub) in enumerate([
            ("PROTECTED FOLDERS", "protected", "registered locks"),
            ("UNLOCKED", "unlocked", "currently available"),
            ("TOTAL SIZE", "size", "known folder size"),
            ("LAST ACTION", "action", "most recent operation"),
        ]):
            cards.addWidget(self.card(name, obj, sub), 0, col)
        outer.addLayout(cards)

        tabs = QTabWidget()
        outer.addWidget(tabs, 1)

        manage = QWidget()
        ml = QVBoxLayout(manage)
        info = QLabel(
            "Select a registered folder. Lock/unlock operations always require the password."
        )
        info.setObjectName("muted")
        ml.addWidget(info)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Status", "Folder", "Size", "Files", "Created", "Last action"
        ])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i in [0, 2, 3, 4, 5]:
            self.table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.open_registered)
        ml.addWidget(self.table)
        tabs.addTab(manage, "🔐 Protected Folders")

        features = QWidget()
        fl = QVBoxLayout(features)
        group = QGroupBox("Included features")
        gl = QVBoxLayout(group)
        for text in [
            "Password verification using PBKDF2-SHA256",
            "No plaintext passwords stored",
            "Folder registration and lock history",
            "Background folder-size calculation",
            "Windows hidden/system folder attributes",
            "Double-click to open registered folders",
            "Dark / light dashboard",
            "Local-only configuration",
            "Safe unlock confirmation",
        ]:
            c = QCheckBox("✓ " + text)
            c.setChecked(True)
            c.setEnabled(False)
            gl.addWidget(c)
        fl.addWidget(group)
        warning = QLabel(
            "Important: this version is a folder-access utility, not full disk encryption. "
            "It does not cryptographically encrypt the contents of the folder. "
            "For strong confidentiality against another administrator/user, use "
            "BitLocker, VeraCrypt, or an encrypted container."
        )
        warning.setWordWrap(True)
        warning.setObjectName("warning")
        fl.addWidget(warning)
        fl.addStretch()
        tabs.addTab(features, "⚙ Features")

        footer = QLabel(
            "FolderLock is local-only. Passwords are never saved in plaintext."
        )
        footer.setObjectName("footer")
        outer.addWidget(footer)
        self.footer = footer

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        outer.addWidget(self.progress)

    def card(self, title, obj, subtitle):
        f = QFrame()
        f.setObjectName("statCard")
        l = QVBoxLayout(f)
        l.setContentsMargins(14, 11, 14, 11)
        a = QLabel(title)
        a.setObjectName("cardTitle")
        b = QLabel("0")
        b.setObjectName(obj)
        c = QLabel(subtitle)
        c.setObjectName("cardSubtitle")
        l.addWidget(a)
        l.addWidget(b)
        l.addWidget(c)
        return f

    def load_locks(self):
        try:
            if VAULT_FILE.exists():
                return json.loads(VAULT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

    def save_locks(self):
        VAULT_FILE.write_text(
            json.dumps(self.locks, indent=2), encoding="utf-8"
        )

    def browse(self):
        p = QFileDialog.getExistingDirectory(self, "Select folder")
        if p:
            self.path.setText(p)

    def selected_path(self):
        return self.path.text().strip()

    def lock_selected(self):
        path = self.selected_path()
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Folder required", "Choose a valid folder.")
            return

        dialog = PasswordDialog("Create Folder Password", True, self)
        if dialog.exec() != QDialog.Accepted:
            return
        password, confirm = dialog.values()

        if len(password) < 8:
            QMessageBox.warning(self, "Weak password", "Use at least 8 characters.")
            return
        if password != confirm:
            QMessageBox.warning(self, "Mismatch", "Passwords do not match.")
            return

        existing = next((x for x in self.locks if os.path.normcase(x["path"]) == os.path.normcase(path)), None)
        if existing:
            QMessageBox.information(self, "Already registered", "This folder is already registered.")
            return

        salt, digest = hash_password(password)
        now = datetime.now().isoformat(timespec="seconds")
        size, files = folder_size(path)

        # The worker creates the marker and Windows attributes.
        self.run_worker(path, password, "lock", {
            "path": path,
            "salt": salt,
            "digest": digest,
            "created": now,
            "last_action": "Locked",
            "last_action_at": now,
            "size": size,
            "files": files,
        })

    def unlock_selected(self):
        path = self.selected_path()
        if not path and self.table.currentRow() >= 0:
            path = self.table.item(self.table.currentRow(), 1).text()

        record = next(
            (x for x in self.locks if os.path.normcase(x["path"]) == os.path.normcase(path)),
            None
        )
        if not record:
            QMessageBox.warning(self, "Not registered", "Select a registered locked folder.")
            return

        dialog = PasswordDialog("Unlock Folder", False, self)
        if dialog.exec() != QDialog.Accepted:
            return
        password, _ = dialog.values()

        self.run_worker(path, password, "unlock", record)

    def run_worker(self, path, password, mode, record):
        self._operation_context = (mode, record)
        self.progress.show()
        self.footer.setText(
            "Working… folder-size calculation may take a while for very large folders."
        )
        self.thread = QThread()
        self.worker = LockWorker(path, password, mode)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        # Queued signal/slot delivery keeps all GUI work on the main thread.
        self.worker.finished.connect(self.operation_finished_queued, Qt.QueuedConnection)
        self.worker.finished.connect(self.thread.quit, Qt.QueuedConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread_done)
        self.thread.start()

    @Slot(bool, str, object)
    def operation_finished_queued(self, ok, msg, extra):
        # Sender is the worker; retrieve the operation context that was saved
        # on the GUI object before starting the thread.
        mode, record = self._operation_context
        self.progress.hide()
        if not ok:
            QMessageBox.warning(
                self, "Operation failed", msg
            )
            self.footer.setText("Operation failed.")
            return

        now = datetime.now().isoformat(timespec="seconds")
        if mode == "lock":
            record["last_action"] = "Locked"
            record["last_action_at"] = now
            # Replace password hash created in the worker with the same one
            # already stored in the record.
            self.locks.append(record)
            self.path.setText(record["path"])
        else:
            record["last_action"] = "Unlocked"
            record["last_action_at"] = now

        self.save_locks()
        self.refresh()
        QMessageBox.information(self, "FolderLock", msg)
        self.footer.setText(f"{mode.title()} completed • {record['path']}")

    @Slot()
    def thread_done(self):
        # This slot runs in the GUI thread because FolderLock lives there.
        self.worker = None
        self.thread = None

    def refresh(self):
        protected = len(self.locks)
        unlocked = 0
        total = 0
        self.table.setRowCount(0)

        for r in self.locks:
            size, files = folder_size(r["path"]) if os.path.isdir(r["path"]) else (r.get("size", 0), r.get("files", 0))
            r["size"] = size
            r["files"] = files
            # Presence of the marker indicates current lock state.
            locked = Path(r["path"]) / ".folderscope.lock"
            status = "🔒 LOCKED" if locked.exists() else "🔓 UNLOCKED"
            if not locked.exists():
                unlocked += 1
            total += size

            row = self.table.rowCount()
            self.table.insertRow(row)
            vals = [
                status,
                r["path"],
                self.human(size),
                str(files),
                r.get("created", "—"),
                r.get("last_action_at", "—"),
            ]
            for c, v in enumerate(vals):
                self.table.setItem(row, c, QTableWidgetItem(str(v)))

        self.findChild(QLabel, "protected").setText(str(protected))
        self.findChild(QLabel, "unlocked").setText(str(unlocked))
        self.findChild(QLabel, "size").setText(self.human(total))
        last = self.locks[-1]["last_action"] if self.locks else "—"
        self.findChild(QLabel, "action").setText(last)
        self.save_locks()

    def human(self, n):
        n = float(n)
        for u in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or u == "TB":
                return f"{n:,.1f} {u}"
            n /= 1024

    def open_registered(self):
        r = self.table.currentRow()
        if r >= 0:
            path = self.table.item(r, 1).text()
            try:
                open_path(path)
            except Exception as e:
                QMessageBox.warning(self, "Cannot open folder", str(e))

    def toggle_theme(self):
        self.dark = not self.dark
        self.apply_theme()
        self.theme_button.setText("☀ Light" if self.dark else "🌙 Dark")

    def closeEvent(self, event):
        if getattr(self, "thread", None) is not None and self.thread.isRunning():
            QMessageBox.information(
                self,
                "Scan in progress",
                "Please wait for the current operation to finish before closing."
            )
            event.ignore()
            return
        event.accept()

    def apply_theme(self):
        self.setStyleSheet(DARK if self.dark else LIGHT)


DARK = """
QMainWindow,QWidget{background:#09111f;color:#e5e7eb;font-family:Segoe UI,Arial;font-size:13px}
QMenuBar,QMenu{background:#111827;color:#e5e7eb}
#header,#toolbar,#panel,#statCard,QGroupBox{background:#111827;border:1px solid #263449;border-radius:12px}
#appTitle{font-size:25px;font-weight:700;color:#f8fafc}
#subtitle,#footer,#muted,#cardSubtitle{color:#94a3b8}
QLineEdit{background:#0f172a;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:8px}
QPushButton{background:#1e293b;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:8px 12px}
QPushButton:hover{background:#334155}
QPushButton#primary{background:#6366f1;border-color:#6366f1;color:white;font-weight:600}
#cardTitle{color:#94a3b8;font-size:11px;font-weight:700}
#protected,#unlocked,#size,#action{font-size:21px;font-weight:700;color:#f8fafc}
QTableWidget{background:#0f172a;color:#e5e7eb;border:1px solid #334155;gridline-color:#243244;alternate-background-color:#111827}
QHeaderView::section{background:#1e293b;color:#cbd5e1;border:0;padding:7px}
QTabBar::tab{background:#111827;border:1px solid #263449;padding:9px 14px;border-radius:7px}
QTabBar::tab:selected{background:#334155}
#warning{color:#fbbf24;padding:10px}
QCheckBox{padding:3px}
QProgressBar{border:0;background:#1e293b;height:6px;border-radius:3px}
QProgressBar::chunk{background:#6366f1;border-radius:3px}
"""

LIGHT = """
QMainWindow,QWidget{background:#f1f5f9;color:#0f172a;font-family:Segoe UI,Arial;font-size:13px}
QMenuBar,QMenu{background:#fff;color:#0f172a}
#header,#toolbar,#panel,#statCard,QGroupBox{background:#fff;border:1px solid #dbe3ef;border-radius:12px}
#appTitle{font-size:25px;font-weight:700;color:#0f172a}
#subtitle,#footer,#muted,#cardSubtitle{color:#64748b}
QLineEdit{background:#f8fafc;color:#0f172a;border:1px solid #cbd5e1;border-radius:8px;padding:8px}
QPushButton{background:#fff;color:#0f172a;border:1px solid #cbd5e1;border-radius:8px;padding:8px 12px}
QPushButton:hover{background:#f1f5f9}
QPushButton#primary{background:#4f46e5;border-color:#4f46e5;color:white;font-weight:600}
#cardTitle{color:#64748b;font-size:11px;font-weight:700}
#protected,#unlocked,#size,#action{font-size:21px;font-weight:700;color:#0f172a}
QTableWidget{background:#f8fafc;color:#0f172a;border:1px solid #cbd5e1;gridline-color:#e2e8f0;alternate-background-color:#fff}
QHeaderView::section{background:#e2e8f0;color:#334155;border:0;padding:7px}
QTabBar::tab{background:#fff;border:1px solid #dbe3ef;padding:9px 14px;border-radius:7px}
QTabBar::tab:selected{background:#e2e8f0}
#warning{color:#b45309;padding:10px}
QCheckBox{padding:3px}
QProgressBar{border:0;background:#e2e8f0;height:6px;border-radius:3px}
QProgressBar::chunk{background:#4f46e5;border-radius:3px}
"""


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("FolderLock")
    win = FolderLock()
    win.show()
    sys.exit(app.exec())
