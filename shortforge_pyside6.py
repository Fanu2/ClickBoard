import sys, os, json, shutil, subprocess
from pathlib import Path
from PySide6.QtCore import Qt, QThread, QObject, Signal, Slot
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QListWidget,QListWidgetItem,QFileDialog,QComboBox,QLineEdit,QCheckBox,QSpinBox,QDoubleSpinBox,QProgressBar,QGroupBox,QFormLayout,QMessageBox,QTabWidget,QTextEdit

APP=Path.home()/'.shortforge'; APP.mkdir(exist_ok=True); SETTINGS=APP/'settings.json'
EXTS={'.mp4','.mkv','.avi','.mov','.wmv','.flv','.webm','.m4v','.3gp','.mpeg','.mpg','.ts','.mts','.m2ts','.vob'}

def exe(name): return shutil.which(name)
def probe_duration(p):
    x=exe('ffprobe')
    if not x:return 0
    try:return float(subprocess.run([x,'-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(p)],capture_output=True,text=True,timeout=20).stdout.strip() or 0)
    except:return 0

class Worker(QObject):
    progress=Signal(int); log=Signal(str); finished=Signal(bool,str)
    def __init__(self,files,out,s): super().__init__(); self.files=files; self.out=Path(out); self.s=s; self.stop_requested=False
    def stop(self): self.stop_requested=True
    def filters(self):
        w,h=map(int,self.s['resolution'].split('x'))
        if self.s['fit']=='Crop to Fill': vf=f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1'
        elif self.s['fit']=='Fit with Background': vf=f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={self.s["background"]},setsar=1'
        else: vf=f'scale={w}:{h},setsar=1'
        if self.s['mirror']:vf+=',hflip'
        if self.s['brightness']:vf+=f',eq=brightness={self.s["brightness"]/100:.3f}'
        if self.s['contrast']:vf+=f',eq=contrast={1+self.s["contrast"]/100:.3f}'
        if self.s['text']:
            t=self.s['text'].replace('\\','\\\\').replace(':','\\:').replace("'","\\'")
            vf+=f",drawtext=text='{t}':x=(w-text_w)/2:y=h-text_h-80:fontsize=54:fontcolor=white:borderw=3:bordercolor=black@0.65"
        return vf
    @Slot()
    def run(self):
        f=exe('ffmpeg')
        if not f:self.finished.emit(False,'FFmpeg was not found in PATH.');return
        self.out.mkdir(parents=True,exist_ok=True)
        for i,src in enumerate(self.files):
            if self.stop_requested:self.finished.emit(False,'Conversion cancelled.');return
            src=Path(src); out=self.out/(src.stem+'_SHORT.mp4'); self.log.emit(f'[{i+1}/{len(self.files)}] {src.name}')
            cmd=[f,'-y','-hide_banner','-loglevel','error','-ss',str(self.s['start']),'-i',str(src)]
            if self.s['duration']:cmd += ['-t',str(self.s['duration'])]
            cmd += ['-vf',self.filters(),'-c:v','libx264','-preset',self.s['preset'],'-crf',str(self.s['quality']),'-pix_fmt','yuv420p','-c:a','aac','-b:a',self.s['audio'],'-movflags','+faststart',str(out)]
            try:
                p=subprocess.run(cmd,capture_output=True,text=True)
                if p.returncode:self.log.emit('  ERROR: '+p.stderr[-700:])
                else:self.log.emit('  ✓ '+out.name)
            except Exception as e:self.log.emit('  ERROR: '+str(e))
            self.progress.emit(int((i+1)*100/len(self.files)))
        self.finished.emit(True,f'Finished processing {len(self.files)} video(s).')

class App(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('ShortForge — Vertical Video Converter'); self.resize(1180,800); self.thread=self.worker=None; self.ui(); self.load(); self.theme()
    def ui(self):
        root=QWidget(); self.setCentralWidget(root); o=QVBoxLayout(root); o.setContentsMargins(18,16,18,16)
        h=QHBoxLayout(); t=QVBoxLayout(); a=QLabel('🎬 ShortForge');a.setObjectName('title');b=QLabel('Turn videos into polished vertical Shorts');b.setObjectName('muted');t.addWidget(a);t.addWidget(b);h.addLayout(t);h.addStretch();self.ff=QLabel();h.addWidget(self.ff);o.addLayout(h)
        tabs=QTabWidget();o.addWidget(tabs,1)
        main=QWidget();m=QHBoxLayout(main); left=QVBoxLayout(); right=QVBoxLayout()
        gb=QGroupBox('Source videos');l=QVBoxLayout(gb);br=QHBoxLayout()
        for text,fn in [('＋ Add Videos',self.add),('＋ Add Folder',self.addfolder),('Clear',self.clear)]:
            q=QPushButton(text);q.clicked.connect(fn);br.addWidget(q)
        l.addLayout(br);self.list=QListWidget();l.addWidget(self.list,1);self.count=QLabel('0 videos');self.count.setObjectName('muted');l.addWidget(self.count);left.addWidget(gb,1)
        out=QGroupBox('Output');f=QFormLayout(out);self.output=QLineEdit(str(Path.home()/'ShortForge_Output'));q=QPushButton('Browse…');q.clicked.connect(self.choose);r=QHBoxLayout();r.addWidget(self.output);r.addWidget(q);f.addRow('Folder:',r)
        self.res=QComboBox();self.res.addItems(['1080x1920 — Full HD','720x1280 — HD']);f.addRow('Resolution:',self.res)
        self.fit=QComboBox();self.fit.addItems(['Crop to Fill','Fit with Background','Stretch']);f.addRow('Framing:',self.fit)
        self.bg=QComboBox();self.bg.addItems(['black','white','gray']);f.addRow('Background:',self.bg)
        self.dur=QComboBox();self.dur.addItems(['Auto / full video','15 seconds','30 seconds','60 seconds','90 seconds']);self.dur.setCurrentIndex(3);f.addRow('Clip length:',self.dur)
        self.start=QDoubleSpinBox();self.start.setRange(0,999999);self.start.setSuffix(' s');f.addRow('Start at:',self.start);right.addWidget(out)
        en=QGroupBox('Shorts enhancements');e=QFormLayout(en);self.text=QLineEdit();self.text.setPlaceholderText('Optional title / caption');e.addRow('Text overlay:',self.text);self.mirror=QCheckBox('Mirror video');e.addRow('',self.mirror);self.bright=QSpinBox();self.bright.setRange(-100,100);self.bright.setSuffix('%');e.addRow('Brightness:',self.bright);self.contrast=QSpinBox();self.contrast.setRange(-100,100);self.contrast.setSuffix('%');e.addRow('Contrast:',self.contrast);self.quality=QComboBox();self.quality.addItems(['18 — Very high','20 — High','22 — Recommended','24 — Smaller','27 — Compact']);self.quality.setCurrentIndex(2);e.addRow('Quality:',self.quality);self.preset=QComboBox();self.preset.addItems(['fast','medium','slow']);self.preset.setCurrentIndex(1);e.addRow('Encoding:',self.preset);self.audio=QComboBox();self.audio.addItems(['128k','160k','192k','256k']);self.audio.setCurrentIndex(2);e.addRow('Audio:',self.audio);right.addWidget(en)
        ar=QHBoxLayout();self.convert=QPushButton('🚀 Convert to Shorts');self.convert.setObjectName('primary');self.convert.clicked.connect(self.convertall);self.stop=QPushButton('■ Stop');self.stop.setEnabled(False);self.stop.clicked.connect(self.stopall);ar.addWidget(self.convert,1);ar.addWidget(self.stop);right.addLayout(ar);m.addLayout(left,5);m.addLayout(right,5);tabs.addTab(main,'🎞 Convert')
        log=QWidget();ll=QVBoxLayout(log);self.log=QTextEdit();self.log.setReadOnly(True);ll.addWidget(self.log);tabs.addTab(log,'📋 Log')
        about=QLabel('<h2>ShortForge</h2><p>Batch-convert MP4, MKV, AVI, MOV, WMV, FLV, WebM, M4V, 3GP, MPEG, MPG, TS, MTS, M2TS and VOB into 9:16 MP4 Shorts using FFmpeg.</p><p>Recommended: 1080×1920 • Crop to Fill • 60 seconds • CRF 20–22 • AAC 192k.</p><p>Use only videos you have the right to upload and distribute.</p>');about.setWordWrap(True);tabs.addTab(about,'ℹ About');self.progress=QProgressBar();self.progress.setValue(0);o.addWidget(self.progress);self.status=QLabel('Ready.');self.status.setObjectName('muted');o.addWidget(self.status);self.updateff()
    def add(self):
        x,_=QFileDialog.getOpenFileNames(self,'Select videos','','Video Files (*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.m4v *.3gp *.mpeg *.mpg *.ts *.mts *.m2ts *.vob);;All Files (*)');self.addpaths(x)
    def addfolder(self):
        d=QFileDialog.getExistingDirectory(self,'Select folder');self.addpaths([str(p) for p in Path(d).rglob('*') if d and p.is_file() and p.suffix.lower() in EXTS]) if d else None
    def addpaths(self,x):
        have={self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())}
        for p in x:
            if p not in have:q=QListWidgetItem(Path(p).name);q.setData(Qt.UserRole,p);q.setToolTip(p);self.list.addItem(q)
        self.count.setText(f'{self.list.count()} video(s)')
    def clear(self):self.list.clear();self.count.setText('0 videos')
    def choose(self):
        d=QFileDialog.getExistingDirectory(self,'Output folder');self.output.setText(d) if d else None
    def settings(self):
        d=[0,15,30,60,90][self.dur.currentIndex()];return {'resolution':'1080x1920' if self.res.currentIndex()==0 else '720x1280','fit':self.fit.currentText(),'background':self.bg.currentText(),'duration':d,'start':self.start.value(),'text':self.text.text().strip(),'mirror':self.mirror.isChecked(),'brightness':self.bright.value(),'contrast':self.contrast.value(),'quality':int(self.quality.currentText().split()[0]),'preset':self.preset.currentText(),'audio':self.audio.currentText()}
    def convertall(self):
        if not self.list.count():QMessageBox.warning(self,'No videos','Add one or more videos first.');return
        if not exe('ffmpeg'):QMessageBox.critical(self,'FFmpeg required','Install FFmpeg and make sure ffmpeg.exe is in PATH.');return
        files=[self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())];self.log.clear();self.progress.setValue(0);self.convert.setEnabled(False);self.stop.setEnabled(True);self.thread=QThread();self.worker=Worker(files,self.output.text(),self.settings());self.worker.moveToThread(self.thread);self.thread.started.connect(self.worker.run);self.worker.progress.connect(self.progress.setValue,Qt.QueuedConnection);self.worker.log.connect(self.log.append,Qt.QueuedConnection);self.worker.finished.connect(self.done,Qt.QueuedConnection);self.worker.finished.connect(self.thread.quit,Qt.QueuedConnection);self.thread.finished.connect(self.worker.deleteLater);self.thread.finished.connect(self.thread.deleteLater);self.thread.finished.connect(self.thread_done);self.thread.start()
    @Slot(bool,str)
    def done(self,ok,msg):self.convert.setEnabled(True);self.stop.setEnabled(False);self.status.setText(msg);QMessageBox.information(self,'ShortForge',msg) if ok else QMessageBox.warning(self,'ShortForge',msg)
    def stopall(self):self.worker.stop() if self.worker else None;self.stop.setEnabled(False)
    @Slot()
    def thread_done(self):self.worker=None;self.thread=None
    def updateff(self):self.ff.setText('● FFmpeg detected' if exe('ffmpeg') else '● FFmpeg not found')
    def load(self):
        try:
            d=json.loads(SETTINGS.read_text());self.output.setText(d.get('output',self.output.text()));self.res.setCurrentIndex(d.get('res',0));self.fit.setCurrentIndex(d.get('fit',0));self.bg.setCurrentIndex(d.get('bg',0));self.dur.setCurrentIndex(d.get('dur',3));self.start.setValue(d.get('start',0));self.text.setText(d.get('text',''));self.mirror.setChecked(d.get('mirror',False));self.bright.setValue(d.get('bright',0));self.contrast.setValue(d.get('contrast',0));self.quality.setCurrentIndex(d.get('quality',2));self.preset.setCurrentIndex(d.get('preset',1));self.audio.setCurrentIndex(d.get('audio',2))
        except:pass
    def save(self):
        SETTINGS.write_text(json.dumps({'output':self.output.text(),'res':self.res.currentIndex(),'fit':self.fit.currentIndex(),'bg':self.bg.currentIndex(),'dur':self.dur.currentIndex(),'start':self.start.value(),'text':self.text.text(),'mirror':self.mirror.isChecked(),'bright':self.bright.value(),'contrast':self.contrast.value(),'quality':self.quality.currentIndex(),'preset':self.preset.currentIndex(),'audio':self.audio.currentIndex()}))
    def theme(self):self.setStyleSheet('QMainWindow,QWidget{background:#09111f;color:#e5e7eb;font-family:Segoe UI;font-size:13px}QGroupBox{background:#111827;border:1px solid #263449;border-radius:12px;margin-top:8px;padding:10px}QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTextEdit,QListWidget{background:#0f172a;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:7px}QPushButton{background:#1e293b;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:8px 12px}QPushButton:hover{background:#334155}QPushButton#primary{background:#6366f1;color:white;font-weight:700}#title{font-size:27px;font-weight:700}#muted{color:#94a3b8}QTabBar::tab{background:#111827;color:#cbd5e1;padding:9px 14px;border-radius:7px}QTabBar::tab:selected{background:#334155}QProgressBar{background:#1e293b;border:0;height:8px}QProgressBar::chunk{background:#6366f1}')
    def closeEvent(self,e):
        if self.thread and self.thread.isRunning():QMessageBox.information(self,'Conversion running','Please wait for the current conversion to finish.');e.ignore();return
        self.save();e.accept()

if __name__=='__main__':
    app=QApplication(sys.argv);w=App();w.show();sys.exit(app.exec())
