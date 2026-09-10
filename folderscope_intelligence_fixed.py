import sys, os, json, csv, hashlib, shutil, subprocess, platform
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QFrame, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QMessageBox,
    QComboBox, QAbstractItemView, QTabWidget, QListWidget, QListWidgetItem,
    QCheckBox, QSpinBox, QDialog, QDialogButtonBox, QFormLayout
)

APP_NAME = "FolderScope"
CACHE_FILE = Path.home() / ".folderscope_cache.json"


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{n:,.1f} {unit}"
        n /= 1024


def age_text(ts):
    if not ts:
        return "—"
    d = max(0, (datetime.now() - datetime.fromtimestamp(ts)).days)
    if d == 0: return "Today"
    if d == 1: return "Yesterday"
    if d < 30: return f"{d} days ago"
    if d < 365: return f"{d // 30} months ago"
    return f"{d // 365} years ago"


def stat_card(title, object_name, subtitle):
    w = QFrame()
    w.setObjectName("statCard")
    l = QVBoxLayout(w)
    l.setContentsMargins(15, 12, 15, 12)
    a = QLabel(title); a.setObjectName("cardTitle")
    b = QLabel("—"); b.setObjectName(object_name)
    c = QLabel(subtitle); c.setObjectName("cardSubtitle")
    l.addWidget(a); l.addWidget(b); l.addWidget(c)
    return w


class Scanner(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, root, exclusions=None):
        super().__init__()
        self.root = Path(root)
        self.exclusions = set(exclusions or [])

    @Slot()
    def run(self):
        try:
            files, folder_sizes = [], defaultdict(int)
            ext_sizes, ext_counts = Counter(), Counter()
            folder_counts = Counter()
            total = folders = errors = 0
            now = datetime.now().timestamp()

            for current, dirs, names in os.walk(self.root, onerror=lambda e: None):
                dirs[:] = [d for d in dirs if d not in self.exclusions]
                folders += len(dirs)
                cp = Path(current)

                for name in names:
                    p = cp / name
                    try:
                        st = p.stat()
                        size = st.st_size
                        total += size
                        ext = p.suffix.lower() or "[no extension]"
                        ext_sizes[ext] += size
                        ext_counts[ext] += 1
                        folder_sizes[str(cp)] += size
                        folder_counts[str(cp)] += 1
                        files.append({
                            "name": name, "path": str(p), "size": size,
                            "modified": st.st_mtime, "ext": ext,
                            "age_days": max(0, int((now - st.st_mtime) / 86400))
                        })
                    except (OSError, PermissionError):
                        errors += 1

                self.progress.emit(len(files), folders, str(cp))

            files.sort(key=lambda x: x["size"], reverse=True)
            duplicates = self.find_duplicates(files)

            self.finished.emit({
                "root": str(self.root), "files": files, "folders": folders,
                "total_size": total, "folder_sizes": dict(folder_sizes),
                "folder_counts": dict(folder_counts),
                "ext_sizes": dict(ext_sizes), "ext_counts": dict(ext_counts),
                "duplicates": duplicates, "errors": errors
            })
        except Exception as e:
            self.failed.emit(str(e))

    def find_duplicates(self, files):
        by_size = defaultdict(list)
        for f in files:
            if f["size"] >= 1024:
                by_size[f["size"]].append(f)

        groups = []
        for size, candidates in by_size.items():
            if len(candidates) < 2:
                continue
            hashes = defaultdict(list)
            for f in candidates:
                try:
                    h = hashlib.sha256()
                    with open(f["path"], "rb") as fh:
                        while chunk := fh.read(1024 * 1024):
                            h.update(chunk)
                    hashes[h.hexdigest()].append(f)
                except (OSError, PermissionError):
                    continue
            for h, items in hashes.items():
                if len(items) > 1:
                    groups.append({"hash": h, "size": size, "files": items})
        return groups


class FolderScope(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FolderScope — Big Folder Intelligence")
        self.resize(1480, 930)
        self.data = None
        self.thread = self.worker = None
        self.dark = True
        self.exclusions = {".git", "__pycache__", "node_modules", ".venv"}
        self.build_ui()
        self.apply_theme()

    def build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(16, 14, 16, 14); outer.setSpacing(10)

        header = QFrame(); header.setObjectName("header")
        hl = QHBoxLayout(header); hl.setContentsMargins(18, 13, 18, 13)
        box = QVBoxLayout()
        title = QLabel("📁 FolderScope"); title.setObjectName("appTitle")
        sub = QLabel("Big Folder Intelligence Dashboard"); sub.setObjectName("appSubtitle")
        box.addWidget(title); box.addWidget(sub); hl.addLayout(box); hl.addStretch()
        self.theme = QPushButton("☀ Light"); self.theme.clicked.connect(self.toggle_theme)
        self.settings_btn = QPushButton("⚙ Scan settings"); self.settings_btn.clicked.connect(self.scan_settings)
        hl.addWidget(self.settings_btn); hl.addWidget(self.theme)
        outer.addWidget(header)

        toolbar = QFrame(); toolbar.setObjectName("toolbar")
        tl = QHBoxLayout(toolbar); tl.setContentsMargins(9, 8, 9, 8)
        self.path = QLineEdit(); self.path.setPlaceholderText("Select a large folder…")
        browse = QPushButton("📂 Browse"); browse.clicked.connect(self.browse)
        self.scan_btn = QPushButton("🔍 Analyze"); self.scan_btn.setObjectName("primary"); self.scan_btn.clicked.connect(self.scan)
        tl.addWidget(self.path, 1); tl.addWidget(browse); tl.addWidget(self.scan_btn)
        outer.addWidget(toolbar)

        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.hide()
        outer.addWidget(self.progress)

        cards = QGridLayout(); cards.setSpacing(8)
        for i, x in enumerate([
            ("TOTAL SIZE", "totalValue", "storage consumed"),
            ("FILES", "filesValue", "files discovered"),
            ("FOLDERS", "foldersValue", "subfolders"),
            ("LARGEST FILE", "largestValue", "largest single item"),
            ("DUPLICATES", "dupValue", "duplicate files"),
            ("OLD FILES", "oldValue", "older than 1 year"),
        ]):
            cards.addWidget(stat_card(*x), 0, i)
        outer.addLayout(cards)

        self.tabs = QTabWidget(); outer.addWidget(self.tabs, 1)
        self.build_overview_tab()
        self.build_files_tab()
        self.build_folders_tab()
        self.build_duplicates_tab()
        self.build_cleanup_tab()

        self.status = QLabel("Ready — choose a folder to begin.")
        self.status.setObjectName("footer"); outer.addWidget(self.status)

        tools = self.menuBar().addMenu("&Tools")
        rescan = QAction("Rescan", self); rescan.triggered.connect(self.scan); tools.addAction(rescan)
        export = QAction("Export report…", self); export.triggered.connect(self.export_report); tools.addAction(export)

    def build_overview_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        split = QSplitter(Qt.Horizontal)

        p = QFrame(); p.setObjectName("panel"); pl = QVBoxLayout(p)
        pl.addWidget(self.section("Storage by file type"))
        self.ext_table = self.table(["Type", "Files", "Size", "Share"])
        pl.addWidget(self.ext_table); split.addWidget(p)

        p2 = QFrame(); p2.setObjectName("panel"); p2l = QVBoxLayout(p2)
        p2l.addWidget(self.section("Storage hotspots"))
        self.hot_table = self.table(["Folder", "Size", "Files"])
        p2l.addWidget(self.hot_table); split.addWidget(p2)
        split.setSizes([520, 800]); l.addWidget(split)
        self.tabs.addTab(w, "📊 Overview")

    def build_files_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.file_search = QLineEdit(); self.file_search.setPlaceholderText("Filter files by name, path or extension…")
        self.file_search.textChanged.connect(self.refresh_files)
        self.file_sort = QComboBox(); self.file_sort.addItems(["Largest first", "Smallest first", "Name A–Z", "Newest", "Oldest"])
        self.file_sort.currentIndexChanged.connect(self.refresh_files)
        bar.addWidget(self.file_search, 1); bar.addWidget(self.file_sort)
        l.addLayout(bar)
        self.files_table = self.table(["File", "Type", "Size", "Modified", "Age"])
        self.files_table.doubleClicked.connect(self.open_selected)
        l.addWidget(self.files_table)
        self.tabs.addTab(w, "📄 Files")

    def build_folders_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.folder_search = QLineEdit(); self.folder_search.setPlaceholderText("Filter folders…")
        self.folder_search.textChanged.connect(self.refresh_folders)
        l.addWidget(self.folder_search)
        self.folder_table = self.table(["Folder", "Size", "Files", "Share"])
        l.addWidget(self.folder_table)
        self.tabs.addTab(w, "🗂️ Folders")

    def build_duplicates_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.dup_info = QLabel("Duplicate detection groups identical files using SHA-256.")
        self.dup_info.setObjectName("muted")
        l.addWidget(self.dup_info)
        self.dup_table = self.table(["Duplicate group", "Size each", "Wasted space", "Files"])
        self.dup_table.doubleClicked.connect(self.open_duplicate)
        l.addWidget(self.dup_table)
        self.tabs.addTab(w, "♻️ Duplicates")

    def build_cleanup_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        intro = QLabel("Cleanup candidates are suggestions only. Review every item before deleting.")
        intro.setObjectName("warning")
        l.addWidget(intro)
        self.cleanup_table = self.table(["Candidate", "Reason", "Size", "Age", "Path"])
        self.cleanup_table.doubleClicked.connect(self.open_selected_cleanup)
        l.addWidget(self.cleanup_table)
        self.trash_btn = QPushButton("🗑 Move selected to Trash / Recycle Bin")
        self.trash_btn.clicked.connect(self.trash_cleanup)
        l.addWidget(self.trash_btn)
        self.tabs.addTab(w, "🧹 Cleanup")

    def table(self, headers):
        t = QTableWidget(0, len(headers)); t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(headers)): t.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setAlternatingRowColors(True)
        return t

    def section(self, text):
        x = QLabel(text); x.setObjectName("sectionTitle"); return x

    def browse(self):
        p = QFileDialog.getExistingDirectory(self, "Select folder")
        if p: self.path.setText(p); self.scan()

    def scan_settings(self):
        d = QDialog(self); d.setWindowTitle("Scan settings")
        l = QVBoxLayout(d); l.addWidget(QLabel("Directory names to skip:"))
        checks = []
        for name in [".git", "__pycache__", "node_modules", ".venv", ".cache", "venv", "build", "dist"]:
            c = QCheckBox(name); c.setChecked(name in self.exclusions); checks.append((name,c)); l.addWidget(c)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); buttons.accepted.connect(d.accept); buttons.rejected.connect(d.reject); l.addWidget(buttons)
        if d.exec():
            self.exclusions = {n for n,c in checks if c.isChecked()}

    def scan(self):
        p = self.path.text().strip()
        if not os.path.isdir(p):
            QMessageBox.warning(self, "Folder required", "Choose a valid folder first."); return
        self.scan_btn.setEnabled(False); self.progress.show(); self.status.setText("Starting scan…")
        self.thread = QThread(); self.worker = Scanner(p, self.exclusions); self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.scan_progress)
        self.worker.finished.connect(self.scan_finished)
        self.worker.failed.connect(self.scan_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.scan_thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def scan_progress(self, files, folders, current):
        self.status.setText(f"Scanning… {files:,} files • {folders:,} folders • {current}")

    def scan_finished(self, data):
        self.data = data
        self.progress.hide()
        self.scan_btn.setEnabled(True)
        self.render()
        self.save_cache()
        self.status.setText(
            f"Analysis complete • {len(data['files']):,} files • "
            f"{data['folders']:,} folders • {data['errors']} inaccessible"
        )

    def scan_failed(self, msg):
        self.progress.hide()
        self.scan_btn.setEnabled(True)
        QMessageBox.critical(self, "Scan failed", msg)
        self.status.setText("Scan failed.")

    @Slot()
    def scan_thread_finished(self):
        # The QThread has actually stopped now. Only here is it safe to
        # release our Python references. Releasing them earlier can produce:
        # "QThread: Destroyed while thread '' is still running"
        self.worker = None
        self.thread = None

    def closeEvent(self, event):
        # Never let Qt destroy a running QThread. This is especially important
        # when the user closes the window during a scan of a large directory.
        if self.thread is not None and self.thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Scan still running",
                "A folder scan is still running. Stop waiting for it to finish and close the application?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return

            # The current scanner is not cancellable yet, so wait for the
            # worker to finish naturally rather than destroying the thread.
            self.status.setText("Finishing scan before closing…")
            self.thread.quit()
            self.thread.wait()

        event.accept()

    def render(self):
        d=self.data; files=d["files"]; total=max(d["total_size"],1)
        self.findChild(QLabel,"totalValue").setText(human_size(d["total_size"]))
        self.findChild(QLabel,"filesValue").setText(f"{len(files):,}")
        self.findChild(QLabel,"foldersValue").setText(f"{d['folders']:,}")
        self.findChild(QLabel,"largestValue").setText(human_size(files[0]["size"]) if files else "0 B")
        dup_files=sum(len(g["files"]) for g in d["duplicates"])
        self.findChild(QLabel,"dupValue").setText(f"{dup_files:,}")
        old=sum(1 for f in files if f["age_days"]>365)
        self.findChild(QLabel,"oldValue").setText(f"{old:,}")

        self.ext_table.setRowCount(0)
        for ext,size in sorted(d["ext_sizes"].items(), key=lambda x:x[1], reverse=True)[:30]:
            r=self.ext_table.rowCount(); self.ext_table.insertRow(r)
            vals=[ext,d["ext_counts"][ext],human_size(size),f"{size/total*100:.1f}%"]
            for c,v in enumerate(vals): self.ext_table.setItem(r,c,QTableWidgetItem(str(v)))

        self.hot_table.setRowCount(0)
        for folder,size in sorted(d["folder_sizes"].items(), key=lambda x:x[1], reverse=True)[:40]:
            r=self.hot_table.rowCount(); self.hot_table.insertRow(r)
            vals=[folder,human_size(size),d["folder_counts"].get(folder,0)]
            for c,v in enumerate(vals): self.hot_table.setItem(r,c,QTableWidgetItem(str(v)))

        self.refresh_files(); self.refresh_folders(); self.refresh_duplicates(); self.refresh_cleanup()

    def refresh_files(self):
        if not self.data:return
        q=self.file_search.text().lower()
        files=[f for f in self.data["files"] if q in (f["name"]+" "+f["path"]+" "+f["ext"]).lower()]
        mode=self.file_sort.currentIndex()
        key=[lambda x:-x["size"],lambda x:x["size"],lambda x:x["name"].lower(),lambda x:-x["modified"],lambda x:x["modified"]][mode]
        files.sort(key=key); self.files_table.setRowCount(0)
        for f in files[:1000]:
            r=self.files_table.rowCount();self.files_table.insertRow(r)
            vals=[f["path"],f["ext"],human_size(f["size"]),datetime.fromtimestamp(f["modified"]).strftime("%Y-%m-%d %H:%M"),age_text(f["modified"])]
            for c,v in enumerate(vals):self.files_table.setItem(r,c,QTableWidgetItem(str(v)))

    def refresh_folders(self):
        if not self.data:return
        q=self.folder_search.text().lower(); total=max(self.data["total_size"],1)
        rows=[(p,s,self.data["folder_counts"].get(p,0)) for p,s in self.data["folder_sizes"].items() if q in p.lower()]
        rows.sort(key=lambda x:x[1],reverse=True);self.folder_table.setRowCount(0)
        for p,s,n in rows[:1000]:
            r=self.folder_table.rowCount();self.folder_table.insertRow(r)
            for c,v in enumerate([p,human_size(s),n,f"{s/total*100:.1f}%"]):self.folder_table.setItem(r,c,QTableWidgetItem(str(v)))

    def refresh_duplicates(self):
        if not self.data:return
        self.dup_table.setRowCount(0)
        for i,g in enumerate(self.data["duplicates"],1):
            r=self.dup_table.rowCount();self.dup_table.insertRow(r)
            wasted=g["size"]*(len(g["files"])-1)
            vals=[f"Group {i} ({len(g['files'])} files)",human_size(g["size"]),human_size(wasted),"\n".join(x["path"] for x in g["files"])]
            for c,v in enumerate(vals):self.dup_table.setItem(r,c,QTableWidgetItem(str(v)))
        self.dup_info.setText(f"{len(self.data['duplicates']):,} duplicate groups • "
                              f"{sum(g['size']*(len(g['files'])-1) for g in self.data['duplicates']):,.0f} bytes potentially reclaimable")

    def refresh_cleanup(self):
        if not self.data:return
        candidates=[]
        for f in self.data["files"]:
            if f["age_days"]>365 and f["size"]>=100*1024*1024:
                candidates.append((f,"Large and older than one year"))
            elif f["age_days"]>730 and f["size"]>=10*1024*1024:
                candidates.append((f,"Old file"))
            elif f["name"].lower().endswith((".tmp",".bak",".log")) and f["age_days"]>90:
                candidates.append((f,"Old temporary/backup/log file"))
        candidates.sort(key=lambda x:x[0]["size"],reverse=True)
        self.cleanup_table.setRowCount(0)
        for f,reason in candidates[:1000]:
            r=self.cleanup_table.rowCount();self.cleanup_table.insertRow(r)
            vals=[f["name"],reason,human_size(f["size"]),age_text(f["modified"]),f["path"]]
            for c,v in enumerate(vals):self.cleanup_table.setItem(r,c,QTableWidgetItem(str(v)))

    def open_path(self,path):
        try:
            if platform.system()=="Windows":os.startfile(path)
            elif platform.system()=="Darwin":subprocess.Popen(["open",path])
            else:subprocess.Popen(["xdg-open",path])
        except Exception as e:QMessageBox.warning(self,"Cannot open",str(e))

    def open_selected(self):
        r=self.files_table.currentRow()
        if r>=0:self.open_path(self.files_table.item(r,0).text())

    def open_duplicate(self):
        r=self.dup_table.currentRow()
        if r>=0:
            paths=self.dup_table.item(r,3).text().splitlines()
            if paths:self.open_path(paths[0])

    def open_selected_cleanup(self):
        r=self.cleanup_table.currentRow()
        if r>=0:self.open_path(self.cleanup_table.item(r,4).text())

    def trash_cleanup(self):
        r=self.cleanup_table.currentRow()
        if r<0:return
        path=self.cleanup_table.item(r,4).text()
        if not os.path.isfile(path):return
        if QMessageBox.question(self,"Move to Trash",f"Move this file to Trash/Recycle Bin?\n\n{path}")!=QMessageBox.Yes:return
        try:
            # Prefer platform trash utility when available.
            if platform.system()=="Windows":
                import winshell
                winshell.delete_file(path, no_confirm=True, allow_undo=True)
            elif platform.system()=="Darwin":
                subprocess.run(["osascript","-e",f'tell application "Finder" to delete POSIX file "{path}"'])
            else:
                trash=shutil.which("gio")
                if trash:subprocess.run([trash,"trash",path],check=True)
                else:raise RuntimeError("Install gio/trash support to move files safely to Trash.")
            self.status.setText("Moved to Trash. Rescan to update the dashboard.");self.scan()
        except Exception as e:
            QMessageBox.warning(self,"Safe delete unavailable",str(e))

    def export_report(self):
        if not self.data:
            self.status.setText("Scan a folder before exporting a report.");return
        p,_=QFileDialog.getSaveFileName(self,"Export report","","JSON (*.json);;CSV (*.csv)")
        if not p:return
        if p.lower().endswith(".csv"):
            with open(p,"w",newline="",encoding="utf-8") as fh:
                w=csv.writer(fh);w.writerow(["Path","Type","Size","Modified","Age days"])
                for f in self.data["files"]:w.writerow([f["path"],f["ext"],f["size"],datetime.fromtimestamp(f["modified"]).isoformat(),f["age_days"]])
        else:
            with open(p,"w",encoding="utf-8") as fh:json.dump(self.data,fh,indent=2)
        self.status.setText(f"Report exported: {p}")

    def save_cache(self):
        try:
            CACHE_FILE.write_text(json.dumps({"root":self.data["root"],"scanned":datetime.now().isoformat(),
                                              "files":len(self.data["files"]),"size":self.data["total_size"]},indent=2))
        except OSError:pass

    def toggle_theme(self):
        self.dark=not self.dark;self.apply_theme();self.theme.setText("☀ Light" if self.dark else "🌙 Dark")

    def apply_theme(self):
        self.setStyleSheet(DARK if self.dark else LIGHT)


DARK="""
QMainWindow,QWidget{background:#09111f;color:#e5e7eb;font-family:Segoe UI,Arial;font-size:13px}
QMenuBar,QMenu{background:#111827;color:#e5e7eb} QMenu::item:selected{background:#334155}
#header,#toolbar,#panel,#statCard{background:#111827;border:1px solid #263449;border-radius:12px}
#appTitle{font-size:25px;font-weight:700;color:#f8fafc}#appSubtitle,#footer,#cardSubtitle,#muted{color:#94a3b8}
QLineEdit,QComboBox,QTableWidget{background:#0f172a;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:7px}
QHeaderView::section{background:#1e293b;color:#cbd5e1;border:0;padding:7px}
QTableWidget{gridline-color:#243244;alternate-background-color:#111827}
QPushButton{background:#1e293b;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:8px 12px}
QPushButton:hover{background:#334155}QPushButton#primary{background:#6366f1;border-color:#6366f1;color:white;font-weight:600}
#cardTitle{color:#94a3b8;font-size:11px;font-weight:700}#totalValue,#filesValue,#foldersValue,#largestValue,#dupValue,#oldValue{font-size:22px;font-weight:700;color:#f8fafc}
#sectionTitle{font-size:14px;font-weight:700;color:#f8fafc;padding:4px}
QProgressBar{border:0;background:#1e293b;height:6px;border-radius:3px}QProgressBar::chunk{background:#6366f1;border-radius:3px}
QTabBar::tab{background:#111827;border:1px solid #263449;padding:9px 15px;margin-right:3px;border-radius:7px}
QTabBar::tab:selected{background:#334155}
#warn{color:#fbbf24}
"""

LIGHT="""
QMainWindow,QWidget{background:#f1f5f9;color:#0f172a;font-family:Segoe UI,Arial;font-size:13px}
QMenuBar,QMenu{background:#fff;color:#0f172a}QMenu::item:selected{background:#e2e8f0}
#header,#toolbar,#panel,#statCard{background:#fff;border:1px solid #dbe3ef;border-radius:12px}
#appTitle{font-size:25px;font-weight:700;color:#0f172a}#appSubtitle,#footer,#cardSubtitle,#muted{color:#64748b}
QLineEdit,QComboBox,QTableWidget{background:#f8fafc;color:#0f172a;border:1px solid #cbd5e1;border-radius:8px;padding:7px}
QHeaderView::section{background:#e2e8f0;color:#334155;border:0;padding:7px}QTableWidget{gridline-color:#e2e8f0;alternate-background-color:#f8fafc}
QPushButton{background:#fff;color:#0f172a;border:1px solid #cbd5e1;border-radius:8px;padding:8px 12px}
QPushButton:hover{background:#f1f5f9}QPushButton#primary{background:#4f46e5;border-color:#4f46e5;color:white;font-weight:600}
#cardTitle{color:#64748b;font-size:11px;font-weight:700}#totalValue,#filesValue,#foldersValue,#largestValue,#dupValue,#oldValue{font-size:22px;font-weight:700;color:#0f172a}
#sectionTitle{font-size:14px;font-weight:700;color:#0f172a;padding:4px}
QProgressBar{border:0;background:#e2e8f0;height:6px;border-radius:3px}QProgressBar::chunk{background:#4f46e5;border-radius:3px}
QTabBar::tab{background:#fff;border:1px solid #dbe3ef;padding:9px 15px;margin-right:3px;border-radius:7px}
QTabBar::tab:selected{background:#e2e8f0}
#warn{color:#b45309}
"""


if __name__=="__main__":
    app=QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win=FolderScope();win.show()
    sys.exit(app.exec())
