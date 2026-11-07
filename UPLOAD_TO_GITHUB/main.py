"""Santa Maria Helper + Binder. Windows desktop app, PySide6."""
import json, os, sys, threading, traceback, ctypes, html, subprocess, re, shutil as shutil_lib, hashlib, hmac, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from difflib import SequenceMatcher
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QPropertyAnimation, QEasingCurve, QUrl
from PySide6.QtGui import QIcon, QAction, QCursor, QDesktopServices, QFontMetrics, QKeySequence, QShortcut, QPixmap, QColor, QBrush, QFont
from PySide6.QtWidgets import (QApplication, QWidget, QMainWindow, QFrame, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QTreeWidget, QTreeWidgetItem, QListWidget,
    QListWidgetItem, QStackedWidget, QComboBox, QCheckBox, QFormLayout, QDialog,
    QDialogButtonBox, QKeySequenceEdit, QMessageBox, QFileDialog, QSystemTrayIcon, QMenu, QSplitter, QSizePolicy, QSizeGrip, QScrollArea, QStyledItemDelegate, QStyleOptionViewItem, QAbstractItemView, QTextBrowser)
from binder_engine import Engine, validate
from admin_accounts import AccountStore, LEVELS, LEVEL_NAMES, DEV_MIN_LEVEL, can_use_command
from search_engine import normalize
from fast_retrieval import FastRuleIndex
from meaning_search import find_evidence, number_sort, topic_catalog
from query_language import repair_query
import forum_sync
from rule_integrity import repair_records
from app_updater import VERSION, check_release, download_release, schedule_replace

ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
APPDATA=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')/'SantaMariaHelper'
APPDATA.mkdir(parents=True,exist_ok=True)
PROFILE_DIR=APPDATA/'profiles'; PROFILE_DIR.mkdir(exist_ok=True)
CONFIG=APPDATA/'config.json'
RULE_CACHE=APPDATA/'forum_rules.json'
ACCOUNTS_FILE=APPDATA/'accounts.json'
COMMANDS_FILE=APPDATA/'commands_custom.json'
ORANGE='#ffad51'; DARK='#18130f'
DEVELOPER_PASSWORD_DIGEST='5dbcbc9f9b29743b54d059187c2e0107f0fd9297acf6c571a7799e0dff8b574b'

def verify_developer_password(password):
    digest=hashlib.sha256(b'SMH-dev-v22:'+password.encode('utf-8')).hexdigest()
    return hmac.compare_digest(digest, DEVELOPER_PASSWORD_DIGEST)
CATEGORY_ORDER=['Общие правила','Государственные организации','Криминальные организации','Совместные мероприятия','Семейные организации','Дополнительные правила']
DEFAULT_CLOUD_URL='https://santa-maria-helper-api.cernisenkotarik805.workers.dev' # Editable in settings

def read_json(path, default):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def write_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,path)

def profile_path(name, account_id=None):
    safe=''.join(c for c in name if c.isalnum() or c in ' _-')[:60].strip()
    if not safe:raise ValueError('Введите имя профиля')
    folder=PROFILE_DIR/(account_id or 'legacy')
    folder.mkdir(parents=True,exist_ok=True)
    return folder/(safe+'.json')

class UpdateWorker(QThread):
    update_result=Signal(object,str)
    def __init__(self,repo,release=None,parent=None):
        super().__init__(parent);self.repo=repo;self.release=release
    def run(self):
        try:
            if self.release:
                path=download_release(self.release,APPDATA/'updates')
                self.update_result.emit(str(path),'')
            else:self.update_result.emit(check_release(self.repo),'')
        except Exception as e:self.update_result.emit(None,str(e))

class SyncWorker(QThread):
    status=Signal(str); finished_sync=Signal(bool,str)
    def run(self):
        try:
            forum_sync.update(report=self.status.emit,destination=RULE_CACHE)
            fresh=read_json(RULE_CACHE,[])
            if isinstance(fresh,list) and fresh:
                write_json(RULE_CACHE,repair_records(fresh,ROOT/'rules.json'))
            self.finished_sync.emit(True,'Правила обновлены')
        except Exception as ex:self.finished_sync.emit(False,f'Не удалось обновить: {ex}')

OLLAMA_URL='http://127.0.0.1:11434'
DEFAULT_MODEL='qwen2.5:3b'

class ModelWorker(QThread):
    status=Signal(str);done=Signal(bool,str)
    def __init__(self,model,parent=None):
        super().__init__(parent);self.model=model
    def run(self):
        exe=shutil_lib.which('ollama')
        if not exe and sys.platform=='win32':
            candidate=Path(os.environ.get('LOCALAPPDATA',''))/'Programs'/'Ollama'/'ollama.exe'
            if candidate.exists():exe=str(candidate)
        if not exe:
            self.done.emit(False,'Ollama не установлена. Нажмите «Установить Ollama», затем повторите загрузку модели.');return
        try:
            # Launch without a visible console on Windows; model is stored by Ollama, not alongside EXE.
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            proc=subprocess.Popen([exe,'pull',self.model],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                                  encoding='utf-8',errors='replace',creationflags=flags)
            for line in proc.stdout:
                if line.strip():self.status.emit(line.strip()[-140:])
            code=proc.wait()
            self.done.emit(code==0,'Модель установлена. Теперь можно задавать вопросы.' if code==0 else 'Не удалось загрузить модель. Проверьте сеть и запущена ли Ollama.')
        except Exception as e:self.done.emit(False,'Ошибка установки модели: '+str(e))

class QuestionEditor(QTextEdit):
    """Enter submits; Shift+Enter inserts a newline (including numpad Enter)."""
    submitted = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            event.accept()
            if not event.isAutoRepeat():
                self.submitted.emit()
            return
        super().keyPressEvent(event)


class AnswerWorker(QThread):
    """All model and retrieval work runs off the UI thread."""
    answer_ready=Signal(dict)

    def __init__(self,question,rules,commands,server_url,index=None,parent=None):
        super().__init__(parent)
        self.question=question;self.rules=rules;self.commands=commands
        self.server_url=server_url;self.index=index

    def run(self):
        import requests
        url=self.server_url.strip().rstrip('/')
        if not url.startswith('https://'):
            self.answer_ready.emit({'retry':True,'reason':'invalid_url'});return

        def post_json(path, body, attempts=2):
            last=None
            for attempt in range(attempts):
                try:
                    response=requests.post(url+path,json=body,timeout=(8,42))
                    if response.status_code in (429,500,502,503,504) and attempt<attempts-1:
                        time.sleep(0.75*(attempt+1));continue
                    response.raise_for_status()
                    return response.json()
                except (requests.ConnectionError,requests.Timeout,requests.HTTPError,ValueError) as error:
                    last=error
                    if attempt<attempts-1:time.sleep(0.75*(attempt+1))
            raise last or RuntimeError('Не удалось получить ответ')

        interpretation={}
        try:
            # First: ask the model to understand the question (not to answer it).
            interpretation=post_json('/interpret',{
                'question':self.question[:1800], 'topics':topic_catalog(self.rules)},attempts=2)
        except Exception:
            # Search can still work without a successful intent-analysis pass.
            interpretation={}

        selected=find_evidence(self.question,self.rules,interpretation,self.index,limit=18)
        raw_commands=set(re.findall(r'/[a-z][a-z0-9]*',self.question.casefold()))
        command_candidates=[c for c in self.commands if str(c.get('command','')).casefold() in raw_commands]
        if not command_candidates and interpretation.get('question_type')=='command':
            # Also help with descriptions such as 'команда, чтобы выдать кейс'.
            terms=set(normalize(self.question).split())
            command_candidates=sorted(self.commands,key=lambda c:-len(terms&set(normalize(c.get('description','')).split())))[:8]
        command_candidates=command_candidates[:8]
        payload={'question':self.question[:1800], 'interpretation':interpretation,
                 'materials':{'rules':[{'id':str(r.get('id',''))[:55],
                     'section':str(r.get('section',''))[:130],
                     'topic':str(r.get('topic',''))[:160],
                     'url':str(r.get('url',''))[:400],
                     'text':str(r.get('text',''))[:2200]} for r in selected],
                     'commands':[{'command':str(c.get('command',''))[:80],
                       'description':str(c.get('description',''))[:300]} for c in command_candidates]}}
        try:
            parsed=post_json('/answer',payload,attempts=2)
            if not isinstance(parsed,dict) or parsed.get('status') not in ('verified','general','clarification','insufficient_evidence'):
                raise ValueError('Сервер вернул неизвестный статус')
            matched_cmds={str(c.get('command','')).lower():c for c in command_candidates}
            parsed['commands']=[{'command':c.get('command',''),
                'purpose':matched_cmds[str(c.get('command','')).lower()].get('description','')}
                for c in parsed.get('commands',[]) if isinstance(c,dict) and str(c.get('command','')).lower() in matched_cmds]
            # Cloud Worker returns exact selected source text, not model invented URLs.
            evidence=[]
            for remote in parsed.get('rules',[]):
                if not isinstance(remote,dict):continue
                rec=next((r for r in selected if str(r.get('url',''))==str(remote.get('url',''))
                         and str(r.get('id',''))==str(remote.get('id',''))
                         and str(r.get('text',''))[:120]==str(remote.get('text',''))[:120]),None)
                if rec and rec not in evidence:evidence.append(rec)
            parsed['rules']=[{'title':str(r.get('rule_number') or r.get('id',''))+' · '+str(r.get('topic','')),
                    'explanation':str(r.get('text',''))[:1600], 'url':str(r.get('url',''))}
                    for r in evidence[:6]]
            if parsed['status']=='verified' and not parsed['rules'] and not parsed['commands']:
                parsed={'status':'insufficient_evidence','answer':'', 'rules':[],'commands':[]}
            if parsed.get('status')=='insufficient_evidence':parsed['local_rules']=selected[:5]
            self.answer_ready.emit(parsed)
        except Exception as exc:
            self.answer_ready.emit({'retry':True,'reason':'temporary',
                                    'local_rules':selected[:4]})

class Editor(QDialog):
    def __init__(self,parent=None,item=None,kind='trigger'):
        super().__init__(parent)
        self.setWindowTitle('Настройка биндера')
        self.resize(520,390)
        self.kind=QComboBox();self.kind.addItems(['Триггер','Бинд','Макрос'])
        self.kind.setCurrentIndex({'trigger':0,'bind':1,'macro':2}.get((item or {}).get('kind',kind),0))
        self.name=QLineEdit((item or {}).get('name',''))
        self.shortcut=QLineEdit((item or {}).get('shortcut',''))
        self.shortcut.setPlaceholderText('Например: .ку')
        self.capture=QKeySequenceEdit()
        self.capture.setToolTip('Нажмите сюда и затем нужную клавишу или сочетание, например F6 или Ctrl+Alt+K')
        self.capture.setMaximumSequenceLength(1)
        self.capture.setKeySequence(QKeySequence((item or {}).get('shortcut','')))
        self.capture.keySequenceChanged.connect(self.on_capture)
        self.clear_capture=QPushButton('Сбросить')
        self.clear_capture.clicked.connect(self.capture.clear)
        self.hotkey_row=QWidget();hr=QHBoxLayout(self.hotkey_row);hr.setContentsMargins(0,0,0,0)
        hr.addWidget(self.capture,1);hr.addWidget(self.clear_capture)
        self.payload=QTextEdit();self.payload.setPlainText((item or {}).get('payload',''))
        self.active=QCheckBox('Включён');self.active.setChecked((item or {}).get('enabled',True))
        form=QFormLayout();form.addRow('Тип',self.kind);form.addRow('Название',self.name)
        form.addRow('Текстовый триггер',self.shortcut)
        form.addRow('Записать клавиши',self.hotkey_row)
        form.addRow('Текст / действия',self.payload)
        layout=QVBoxLayout(self);layout.addLayout(form);layout.addWidget(self.active)
        self.hint=QLabel('Нажмите поле записи клавиш и выберите F6 либо Ctrl+Alt+K. Для макроса: TEXT Привет / WAIT 250 / KEY enter')
        self.hint.setWordWrap(True);layout.addWidget(self.hint)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self.update_mode)
        self.update_mode()

    def update_mode(self,*_):
        trigger=self.kind.currentIndex()==0
        self.shortcut.setVisible(trigger)
        self.hotkey_row.setVisible(not trigger)
        if not trigger:self.capture.setFocus()

    def on_capture(self,sequence):
        # Qt records a single combination; the engine accepts the same normalized names.
        self.captured=sequence.toString(QKeySequence.PortableText)

    def validate_accept(self):
        trigger=self.kind.currentIndex()==0
        raw=self.shortcut.text() if trigger else self.capture.keySequence().toString(QKeySequence.PortableText)
        try:self.result_item=validate({'kind':['trigger','bind','macro'][self.kind.currentIndex()],
          'name':self.name.text(),'shortcut':raw,'payload':self.payload.toPlainText(),
          'enabled':self.active.isChecked()})
        except Exception as e:QMessageBox.warning(self,'Проверь запись',str(e));return
        self.accept()

class LoginDialog(QDialog):
    def __init__(self,store,parent=None):
        super().__init__(parent);self.store=store;self.account=None
        self.setWindowTitle('Santa Maria Helper — вход администратора');self.setMinimumWidth(420)
        lay=QVBoxLayout(self)
        head=QLabel('Santa Maria Helper · Авторизация');head.setObjectName('heading');lay.addWidget(head)
        self.first_run=not bool(store.users)
        if self.first_run:
            info=QLabel('Первый запуск: создайте учётную запись владельца. Для подтверждения нужен пароль разработчика.')
            info.setWordWrap(True);lay.addWidget(info)
            self.dev=QLineEdit();self.dev.setEchoMode(QLineEdit.Password);self.dev.setPlaceholderText('Пароль разработчика');lay.addWidget(self.dev)
        else:self.dev=None
        self.login=QLineEdit();self.login.setPlaceholderText('Логин администратора');lay.addWidget(self.login)
        self.password=QLineEdit();self.password.setEchoMode(QLineEdit.Password);self.password.setPlaceholderText('Пароль администратора');lay.addWidget(self.password)
        submit=QPushButton('Создать владельца' if self.first_run else 'Войти');submit.clicked.connect(self.submit);lay.addWidget(submit)
        self.password.returnPressed.connect(self.submit)
    def submit(self):
        try:
            if self.first_run:
                if not verify_developer_password(self.dev.text()):raise ValueError('Неверный пароль разработчика')
                self.account=self.store.create(self.login.text(),self.password.text(),8,owner=True)
            else:
                self.account=self.store.authenticate(self.login.text(),self.password.text())
                if not self.account:raise ValueError('Неверный логин или пароль')
            self.accept()
        except ValueError as ex:QMessageBox.warning(self,'Вход',str(ex))

class AccountManagerDialog(QDialog):
    def __init__(self,store,active_id,active_level,parent=None):
        super().__init__(parent);self.store=store;self.active_id=active_id;self.active_level=active_level
        self.setWindowTitle('Для разработчиков · Администраторы')
        self.resize(900,630);self.setMinimumSize(710,480)
        lay=QVBoxLayout(self);lay.setSpacing(12)
        title=QLabel('Управление администраторами');title.setObjectName('heading');lay.addWidget(title)
        subtitle=QLabel('Выберите администратора слева. Справа — создание и изменение учётной записи.')
        subtitle.setWordWrap(True);lay.addWidget(subtitle)
        split=QSplitter(Qt.Horizontal);lay.addWidget(split,1)
        left=QWidget();left_lay=QVBoxLayout(left);left_lay.setContentsMargins(0,0,8,0)
        self.account_search=QLineEdit();self.account_search.setPlaceholderText('Поиск по логину…')
        self.account_search.textChanged.connect(self.refresh);left_lay.addWidget(self.account_search)
        self.account_filter=QComboBox();self.account_filter.addItem('Все уровни',None)
        for name,value in LEVELS:self.account_filter.addItem(name,value)
        self.account_filter.currentIndexChanged.connect(self.refresh);left_lay.addWidget(self.account_filter)
        self.people=QListWidget();self.people.setAlternatingRowColors(True)
        self.people.setSpacing(5);self.people.currentRowChanged.connect(self.selected)
        left_lay.addWidget(self.people,1)
        self.accounts_count=QLabel();left_lay.addWidget(self.accounts_count)
        split.addWidget(left)
        right=QWidget();right_lay=QVBoxLayout(right);right_lay.setContentsMargins(10,0,0,0)
        right_lay.addWidget(QLabel('Данные администратора'))
        form=QFormLayout()
        self.login=QLineEdit();self.login.setPlaceholderText('Логин');form.addRow('Логин',self.login)
        self.password=QLineEdit();self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText('Новый пароль (8+ символов)');form.addRow('Пароль',self.password)
        self.level=QComboBox()
        for name,value in LEVELS:self.level.addItem(name,value)
        form.addRow('Уровень',self.level);right_lay.addLayout(form)
        right_lay.addWidget(QLabel('При выборе аккаунта справа можно изменить его уровень или пароль.'))
        actions=QVBoxLayout()
        for name,fn in [('＋ Создать аккаунт',self.create),('Сохранить уровень',self.change_level),
                        ('Изменить пароль',self.change_password),('Удалить аккаунт',self.delete)]:
            button=QPushButton(name);button.clicked.connect(fn);actions.addWidget(button)
        right_lay.addLayout(actions);right_lay.addStretch(1)
        export_btn=QPushButton('Экспортировать базу аккаунтов')
        export_btn.clicked.connect(self.export_accounts);right_lay.addWidget(export_btn)
        note=QLabel('Учётные записи пока хранятся локально. Другие компьютеры не получают изменения автоматически.')
        note.setWordWrap(True);right_lay.addWidget(note)
        split.addWidget(right);split.setSizes([510,340]);split.setStretchFactor(0,3);split.setStretchFactor(1,2)
        close=QPushButton('Закрыть');close.clicked.connect(self.accept);lay.addWidget(close)
        self.refresh()
    def export_accounts(self):
        filename,_=QFileDialog.getSaveFileName(self,'Экспорт пользователей',str(Path.home()/'smh_accounts.json'),'JSON (*.json)')
        if not filename:return
        try:
            path=Path(filename)
            if path.suffix.lower()!='.json':path=path.with_suffix('.json')
            write_json(path,{'schema':1,'users':self.store.users})
            QMessageBox.information(self,'Экспорт','Учётные записи сохранены (пароли в виде хешей). Для друзей: скопируйте этот файл как accounts.json в %LOCALAPPDATA%\\SantaMariaHelper до первого запуска. Не публикуйте файл в открытом доступе.')
        except OSError as e:QMessageBox.warning(self,'Экспорт',str(e))

    def refresh(self, *_):
        current=self.people.currentItem()
        current_id=current.data(Qt.UserRole) if current else None
        self.people.blockSignals(True);self.people.clear()
        query=self.account_search.text().strip().casefold()
        selected_level=self.account_filter.currentData()
        entries=[u for u in self.store.users if query in u['login'].casefold() and
                 (selected_level is None or int(u['level'])==selected_level)]
        for item in sorted(entries,key=lambda u:(-int(u['level']),u['login'].casefold())):
            row=QListWidgetItem(f"{item['login']}   ·   {LEVEL_NAMES.get(item['level'],'?')}" +
                                 ('   ·   владелец' if item.get('owner') else ''))
            row.setData(Qt.UserRole,item['id']);row.setToolTip('Нажмите, чтобы редактировать аккаунт')
            self.people.addItem(row)
            if item['id']==current_id:self.people.setCurrentItem(row)
        self.people.blockSignals(False)
        self.accounts_count.setText(f'Показано: {len(entries)} из {len(self.store.users)}')
    def selected(self,*_):
        record=self.current()
        if record:
            self.login.setText(record['login']);idx=self.level.findData(record['level'])
            if idx>=0:self.level.setCurrentIndex(idx)
    def current(self):
        row=self.people.currentItem();uid=row.data(Qt.UserRole) if row else None
        return next((u for u in self.store.users if u['id']==uid),None)
    def create(self):
        try:
            if self.level.currentData()>self.active_level:raise ValueError('Нельзя выдавать уровень выше собственного')
            self.store.create(self.login.text(),self.password.text(),self.level.currentData());self.password.clear();self.refresh()
        except ValueError as e:QMessageBox.warning(self,'Администраторы',str(e))
    def change_level(self):
        item=self.current()
        if item:
            try:
                if item['id']==self.active_id:raise ValueError('Свой уровень нельзя менять во время сеанса')
                if item['level']>self.active_level or self.level.currentData()>self.active_level:raise ValueError('Недостаточно полномочий для этого уровня')
                if item.get('owner'):raise ValueError('Уровень владельца не редактируется')
                self.store.set_level(item['id'],self.level.currentData());self.refresh()
            except ValueError as e:QMessageBox.warning(self,'Уровень',str(e))
    def change_password(self):
        item=self.current()
        if item:
            if item['level']>self.active_level or item.get('owner') and item['id']!=self.active_id:
                QMessageBox.warning(self,'Доступ запрещён','Нельзя менять пароль владельца или старшего администратора');return
            try:self.store.reset_password(item['id'],self.password.text());self.password.clear();QMessageBox.information(self,'Пароль','Пароль обновлён')
            except ValueError as e:QMessageBox.warning(self,'Пароль',str(e))
    def delete(self):
        item=self.current()
        if item and (item['level']>self.active_level or item.get('owner')):
            QMessageBox.warning(self,'Доступ запрещён','Нельзя удалить владельца или старшего администратора');return
        if item and QMessageBox.question(self,'Удалить?',f"Удалить администратора {item['login']}?",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
            try:self.store.delete(item['id'],self.active_id);self.refresh()
            except ValueError as e:QMessageBox.warning(self,'Удаление',str(e))

class CommandManagerDialog(QDialog):
    def __init__(self,commands,parent=None):
        super().__init__(parent);self.commands=[dict(x) for x in commands]
        self.setWindowTitle('Для разработчиков · Редактор команд')
        self.resize(1050,650);self.setMinimumSize(760,490)
        lay=QVBoxLayout(self);lay.setSpacing(10)
        head=QLabel('Команды администратора');head.setObjectName('heading');lay.addWidget(head)
        lay.addWidget(QLabel('Выберите уровень и команду слева, измените её описание и уровень справа.'))
        split=QSplitter(Qt.Horizontal);lay.addWidget(split,1)
        left=QWidget();ll=QVBoxLayout(left);ll.setContentsMargins(0,0,8,0)
        filter_row=QHBoxLayout()
        self.command_search=QLineEdit();self.command_search.setPlaceholderText('Поиск команды…')
        self.command_search.textChanged.connect(self.refresh);filter_row.addWidget(self.command_search)
        ll.addLayout(filter_row)
        lists=QSplitter(Qt.Horizontal);ll.addWidget(lists,1)
        self.level_list=QListWidget();self.level_list.setSpacing(3)
        self.level_list.currentRowChanged.connect(self.refresh)
        lists.addWidget(self.level_list)
        self.list=QListWidget();self.list.setSpacing(5)
        self.list.currentRowChanged.connect(self.selected);lists.addWidget(self.list)
        lists.setSizes([165,340]);lists.setStretchFactor(1,2)
        self.commands_count=QLabel();ll.addWidget(self.commands_count)
        split.addWidget(left)
        right=QWidget();rr=QVBoxLayout(right);rr.setContentsMargins(10,0,0,0)
        rr.addWidget(QLabel('Редактирование команды'))
        form=QFormLayout()
        self.command=QLineEdit();self.command.setPlaceholderText('/command');form.addRow('Команда',self.command)
        self.description=QTextEdit();self.description.setMinimumHeight(130)
        form.addRow('Описание',self.description)
        self.level=QComboBox()
        for name,value in LEVELS:self.level.addItem(name,value)
        form.addRow('Доступна начиная с',self.level);rr.addLayout(form)
        for label,callback in [('＋ Добавить',self.add),('Сохранить изменения',self.update),('Удалить команду',self.delete)]:
            b=QPushButton(label);b.clicked.connect(callback);rr.addWidget(b)
        rr.addStretch(1)
        export=QPushButton('Экспортировать команды в JSON')
        export.clicked.connect(self.export_commands);rr.addWidget(export)
        split.addWidget(right);split.setSizes([650,360]);split.setStretchFactor(0,2)
        finish=QPushButton('Сохранить все изменения и закрыть')
        finish.clicked.connect(self.accept);lay.addWidget(finish)
        self._level_filter=None;self._visible_commands=[]
        self.populate_levels();self.refresh()
    def populate_levels(self):
        self.level_list.blockSignals(True);self.level_list.clear()
        item=QListWidgetItem('Все уровни');item.setData(Qt.UserRole,None)
        self.level_list.addItem(item)
        for name,value in LEVELS:
            count=sum(int(c.get('level',2) or 2)==value for c in self.commands)
            item=QListWidgetItem(f'{name}  ·  {count}')
            item.setData(Qt.UserRole,value);self.level_list.addItem(item)
        desired=self._level_filter
        for i in range(self.level_list.count()):
            if self.level_list.item(i).data(Qt.UserRole)==desired:
                self.level_list.setCurrentRow(i);break
        self.level_list.blockSignals(False)
    def export_commands(self):
        name,_=QFileDialog.getSaveFileName(self,'Экспорт команд',str(Path.home()/'commands_custom.json'),'JSON (*.json)')
        if name:
            try:write_json(name,self.commands)
            except OSError as e:QMessageBox.warning(self,'Команды',str(e))
    def refresh(self, *_):
        chosen=self.level_list.currentItem()
        self._level_filter=chosen.data(Qt.UserRole) if chosen else None
        query=self.command_search.text().strip().casefold()
        current=self.list.currentItem()
        current_command=current.data(Qt.UserRole) if current else None
        self.commands.sort(key=lambda x:x.get('command','').casefold())
        self._visible_commands=[c for c in self.commands if
            (self._level_filter is None or int(c.get('level',2) or 2)==self._level_filter) and
            (not query or query in (str(c.get('command',''))+' '+str(c.get('description',''))).casefold())]
        self.list.blockSignals(True);self.list.clear()
        for c in self._visible_commands:
            item=QListWidgetItem(f"{c.get('command','')}  ·  {c.get('description','')[:110]}")
            item.setData(Qt.UserRole,c.get('command','').lower());self.list.addItem(item)
            if c.get('command','').lower()==current_command:self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.commands_count.setText(f'Показано {len(self._visible_commands)} из {len(self.commands)} команд')
        self.populate_levels()
    def selected(self,i):
        if 0<=i<len(self._visible_commands):
            r=self._visible_commands[i]
            self.command.setText(r.get('command',''))
            self.description.setPlainText(r.get('description',''))
            j=self.level.findData(int(r.get('level',2)))
            if j>=0:self.level.setCurrentIndex(j)
    def chosen_index(self):
        row=self.list.currentItem()
        key=row.data(Qt.UserRole) if row else None
        return next((i for i,c in enumerate(self.commands) if str(c.get('command','')).lower()==key),-1)
    def data(self):
        command=self.command.text().strip().lower()
        if not re.fullmatch(r'/[a-z][a-z0-9_]{0,40}',command):raise ValueError('Введите команду в формате /command')
        description=self.description.toPlainText().strip()
        if not description:raise ValueError('Укажите описание')
        return {'command':command,'description':description[:500],'level':self.level.currentData()}
    def add(self):
        try:
            r=self.data()
            if any(x.get('command','').lower()==r['command'] for x in self.commands):raise ValueError('Такая команда уже есть')
            self.commands.append(r);self.refresh()
        except ValueError as e:QMessageBox.warning(self,'Команды',str(e))
    def update(self):
        i=self.chosen_index()
        if not 0<=i<len(self.commands):return
        try:
            r=self.data()
            if any(k!=i and x.get('command','').lower()==r['command'] for k,x in enumerate(self.commands)):
                raise ValueError('Такая команда уже существует')
            self.commands[i]=r;self.refresh()
        except ValueError as e:QMessageBox.warning(self,'Команды',str(e))
    def delete(self):
        i=self.chosen_index()
        if 0<=i<len(self.commands):self.commands.pop(i);self.refresh()

class DragBar(QFrame):
    """Custom title bar for a frameless Windows window."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.drag_position = None
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window.windowHandle()
            if handle and handle.startSystemMove():
                self.drag_position = None
                event.accept()
                return
            self.drag_position = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
        super().mousePressEvent(event)
    def mouseMoveEvent(self, event):
        if self.drag_position is not None and (event.buttons() & Qt.LeftButton):
            self.window.move(event.globalPosition().toPoint() - self.drag_position)
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self, event):
        self.drag_position = None
        super().mouseReleaseEvent(event)

class WrappingTreeDelegate(QStyledItemDelegate):
    """Measure long forum topic names against the actual available tree width."""
    def sizeHint(self, option, index):
        result = super().sizeHint(option, index)
        title = str(index.data(Qt.DisplayRole) or '')
        tree = self.parent()
        depth = 0
        p = index.parent()
        while p.isValid():
            depth += 1
            p = p.parent()
        available = max(115, tree.viewport().width() - 36 - depth * tree.indentation())
        bounds = option.fontMetrics.boundingRect(0, 0, available, 500, Qt.TextWordWrap, title)
        result.setHeight(max(40, bounds.height() + 18))
        return result

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        opt.textElideMode = Qt.ElideNone
        super().paint(painter, opt, index)


class WrappingListDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        from PySide6.QtCore import QSize
        metrics=QFontMetrics(option.font)
        width=max(180,option.rect.width()-28)
        if self.parent(): width=max(180,self.parent().viewport().width()-36)
        text=str(index.data(Qt.DisplayRole) or '')
        # Full text can be hundreds of lines; cap preview to keep scroll fast.
        preview=text[:850]
        rect=metrics.boundingRect(0,0,width,10000,Qt.TextWordWrap,preview)
        return QSize(width,min(260,max(58,rect.height()+26)))
    def paint(self,painter,option,index):
        opt=QStyleOptionViewItem(option)
        opt.textElideMode=Qt.ElideNone
        super().paint(painter,opt,index)

class Window(QMainWindow):
    def __init__(self, account, store):
        self.account=account;self.store=store
        self.account_dir=APPDATA/'accounts'/account['id'];self.account_dir.mkdir(parents=True,exist_ok=True)
        self.profile_cfg=read_json(self.account_dir/'profile_config.json',{'profile':'Основной'})
        super().__init__();self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint);self.setWindowTitle('Santa Maria Helper');self.setWindowIcon(QIcon(str(ROOT/'helper_logo.png')))
        self.resize(1080,740);self.setMinimumSize(640,450)
        self.cfg=read_json(CONFIG,{'profile':'Основной','running':False,'auto_update':True,'last_sync':'','start_with_windows':False})
        self.items=[];self.rules=[];self.all_commands=read_json(COMMANDS_FILE,read_json(ROOT/'commands.json',[]))
        self.commands=[c for c in self.all_commands if can_use_command(self.account['level'],c)]
        self.filtered=[];self.mode='rules';self.category=None;self.page='search';self.answerer=None;self.model_name=self.cfg.get('ollama_model',DEFAULT_MODEL);self.model_worker=None
        self.engine=Engine(lambda m: self.engine_msg.emit(m))
        self.engine_msg.connect(self.on_engine_message)
        self.syncer=None
        self.update_worker=None;self.available_release=None
        self.rule_index=None
        self.make_ui();self.reload_rules();self.refresh_profiles();self.load_profile(self.profile_cfg.get('profile','Основной'))
        self.make_tray();self.update_engine_state(start=False)
        self.sync_timer=QTimer(self);self.sync_timer.timeout.connect(self.maybe_sync);self.sync_timer.start(60*60*1000)
        QTimer.singleShot(900,self.maybe_sync)
        QTimer.singleShot(2400,self.maybe_check_app_update)
    def nativeEvent(self, eventType, message):
        if sys.platform == 'win32' and eventType in ('windows_generic_MSG', 'windows_dispatcher_MSG'):
            try:
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084 and not self.isMaximized():  # WM_NCHITTEST
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    g = self.frameGeometry()
                    scale = self.devicePixelRatioF()
                    # MSG cursor coordinates are physical; Qt geometry is logical.
                    left = abs(x - round(g.x() * scale)) <= 9 * scale
                    right = abs(x - round((g.x() + g.width()) * scale)) <= 9 * scale
                    top = abs(y - round(g.y() * scale)) <= 9 * scale
                    bottom = abs(y - round((g.y() + g.height()) * scale)) <= 9 * scale
                    hit = (13 if top and left else 14 if top and right else
                           16 if bottom and left else 17 if bottom and right else
                           10 if left else 11 if right else 12 if top else 15 if bottom else 0)
                    if hit:
                        return True, hit
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    engine_msg=Signal(str)
    def make_ui(self):
        host=QWidget();host.setObjectName('host');self.setCentralWidget(host)
        row=QHBoxLayout(host);row.setContentsMargins(0,0,0,0);row.setSpacing(0)
        self.sidebar=QFrame();self.sidebar.setObjectName('side');self.sidebar.setMinimumWidth(58);self.sidebar.setMaximumWidth(58)
        # Hover is detected with a global cursor timer. Child controls must not
        # trigger spurious leave events which previously broke expanding.
        side=QVBoxLayout(self.sidebar);side.setContentsMargins(9,14,9,12);side.setSpacing(7)
        self.brand=QLabel();self.brand.setAlignment(Qt.AlignCenter);self.brand.setFixedHeight(48);self.brand.setPixmap(QPixmap(str(ROOT/'palm_ui.png')).scaled(40,40,Qt.KeepAspectRatio,Qt.SmoothTransformation));side.addWidget(self.brand)
        self.nav_ai=QPushButton('✦');self.nav_ai.clicked.connect(lambda:self.navigate('ai'));side.addWidget(self.nav_ai)
        self.nav_rules=QPushButton('≡');self.nav_rules.clicked.connect(lambda:self.navigate('rules'));side.addWidget(self.nav_rules)
        self.rule_tree=QTreeWidget();self.rule_tree.setHeaderHidden(True);self.rule_tree.itemClicked.connect(self.select_category)
        self.rule_tree.setIndentation(9);self.rule_tree.setVisible(False);self.rule_tree.setMinimumHeight(90);self.rule_tree.setUniformRowHeights(False);self.rule_tree.setWordWrap(True);self.rule_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.rule_tree.setTextElideMode(Qt.ElideNone);self.rule_tree.setItemDelegate(WrappingTreeDelegate(self.rule_tree));self.rule_tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel);self.rule_tree.setRootIsDecorated(True)
        self.nav_commands=QPushButton('⌨');self.nav_commands.clicked.connect(lambda:self.navigate('commands'));side.addWidget(self.nav_commands)
        self.sep=QFrame();self.sep.setFrameShape(QFrame.HLine);side.addWidget(self.sep)
        self.binder_title=QLabel('B');self.binder_title.setAlignment(Qt.AlignCenter);side.addWidget(self.binder_title)
        self.nav_triggers=QPushButton('⚡');self.nav_triggers.clicked.connect(lambda:self.navigate('trigger'));side.addWidget(self.nav_triggers)
        self.nav_binds=QPushButton('⌘');self.nav_binds.clicked.connect(lambda:self.navigate('bind'));side.addWidget(self.nav_binds)
        self.nav_macros=QPushButton('▶');self.nav_macros.clicked.connect(lambda:self.navigate('macro'));side.addWidget(self.nav_macros)
        self.nav_profiles=QPushButton('▣');self.nav_profiles.clicked.connect(lambda:self.navigate('profiles'));side.addWidget(self.nav_profiles)
        self.nav_settings=QPushButton('⚙');self.nav_settings.clicked.connect(lambda:self.navigate('settings'));side.addWidget(self.nav_settings)
        self.status=QLabel('');self.status.setWordWrap(True);self.status.setVisible(False);side.addWidget(self.status)
        self.side_animation=QPropertyAnimation(self.sidebar,b'maximumWidth',self)
        self.side_animation.setDuration(240);self.side_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.collapsed_width=58;self.expanded_width=340;self.expanded=False;self.sidebar_pinned=False;self.side_labels=[(self.nav_ai,'Santa Maria Helper'),(self.nav_rules,'Правила'),(self.nav_commands,'Команды'),(self.nav_triggers,'Триггеры'),(self.nav_binds,'Бинды'),(self.nav_macros,'Макросы'),(self.nav_profiles,'Профили'),(self.nav_settings,'Настройки')]
        self.content=QWidget();self.content.setMinimumWidth(0);row.addWidget(self.sidebar);row.addWidget(self.content,1)
        outer=QVBoxLayout(self.content);outer.setContentsMargins(23,14,23,13);outer.setSpacing(12)
        self.topbar=DragBar(self);header=QHBoxLayout(self.topbar);header.setContentsMargins(6,2,0,2)
        header.addStretch();header_palm=QLabel();header_palm.setPixmap(QPixmap(str(ROOT/'palm_ui.png')).scaled(27,27,Qt.KeepAspectRatio,Qt.SmoothTransformation));header_palm.setAttribute(Qt.WA_TransparentForMouseEvents,True);header.addWidget(header_palm);logo=QLabel('Santa Maria Helper');logo.setObjectName('title');logo.setAttribute(Qt.WA_TransparentForMouseEvents,True);header.addWidget(logo);header.addStretch()
        minimize=QPushButton('—');minimize.setObjectName('windowAction');minimize.setFixedSize(34,28);minimize.clicked.connect(self.showMinimized);header.addWidget(minimize)
        close=QPushButton('✕');close.setObjectName('windowAction');close.setFixedSize(34,28);close.clicked.connect(self.close);header.addWidget(close)
        outer.addWidget(self.topbar)
        self.stack=QStackedWidget();outer.addWidget(self.stack,1)
        self.ai_page=QWidget();self.stack.addWidget(self.ai_page)
        ai_layout=QVBoxLayout(self.ai_page);ai_layout.setContentsMargins(0,0,0,0);ai_layout.setSpacing(12)
        ai_heading=QLabel('Santa Maria Helper');ai_heading.setObjectName('heading');ai_layout.addWidget(ai_heading)
        ai_subtitle=QLabel('Опишите ситуацию своими словами. Ответы будут опираться на загруженные правила и команды.')
        ai_subtitle.setObjectName('subtitle');ai_layout.addWidget(ai_subtitle)
        self.ai_question=QuestionEditor();self.ai_question.setStyleSheet('color:#ffffff;');self.ai_question.submitted.connect(self.ask_question);self.ai_question.setPlaceholderText('Например: игрок убил без причины — какие правила относятся к ситуации?');self.ai_question.setMinimumHeight(75);self.ai_question.setMaximumHeight(150);self.ai_question.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred);self.ai_question.setObjectName('question');ai_layout.addWidget(self.ai_question)
        QShortcut(QKeySequence('Ctrl+Return'),self.ai_question,activated=self.ask_question)
        QShortcut(QKeySequence('Ctrl+Enter'),self.ai_question,activated=self.ask_question)
        ai_bar=QHBoxLayout();self.ai_ask=QPushButton('Найти ответ');self.ai_ask.clicked.connect(self.ask_question);self.ai_status=QLabel('Enter — отправить · Shift+Enter — новая строка');self.ai_status.setWordWrap(True);ai_bar.addWidget(self.ai_status,1);ai_bar.addStretch();ai_bar.addWidget(self.ai_ask);ai_layout.addLayout(ai_bar)
        self.ai_answer=QTextBrowser();self.ai_answer.setOpenExternalLinks(False);self.ai_answer.setOpenLinks(False);self.ai_answer.anchorClicked.connect(self.open_forum_url);self.ai_answer.setObjectName('answer');self.ai_answer.setReadOnly(True);self.ai_answer.setHtml(self.welcome_html());ai_layout.addWidget(self.ai_answer,1);self.ai_answer.setMinimumHeight(105)
        self.search_page=QWidget();self.stack.addWidget(self.search_page)
        search_layout=QVBoxLayout(self.search_page);search_layout.setContentsMargins(0,0,0,0)
        top=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('Поиск по тексту, номеру, команде…');self.search_timer=QTimer(self);self.search_timer.setSingleShot(True);self.search_timer.setInterval(280)
        self.search_timer.timeout.connect(self.update_search);self.search.textChanged.connect(lambda _text:self.search_timer.start())
        top.addWidget(self.search,1)
        self.update_button=QPushButton('↻ Обновить форум');self.update_button.clicked.connect(self.sync_rules);top.addWidget(self.update_button)
        search_layout.addLayout(top)
        # Categories live under search, never inside the narrow navigation sidebar.
        self.rule_tree.setMaximumHeight(210)
        self.rule_tree.setMinimumHeight(75)
        self.rule_tree.setObjectName('categoryTree')
        search_layout.addWidget(self.rule_tree)
        # Dedicated command levels under the search field, not in the global sidebar.
        self.command_tiers=QListWidget()
        self.command_tiers.setMaximumHeight(126)
        self.command_tiers.setMinimumHeight(64)
        self.command_tiers.setSpacing(3)
        all_levels_item=QListWidgetItem('Все доступные уровни')
        all_levels_item.setData(Qt.UserRole,None)
        self.command_tiers.addItem(all_levels_item)
        for tier_name,tier_value in LEVELS:
            if tier_value<=self.account['level']:
                count=sum(int(c.get('level',2) or 2)==tier_value for c in self.commands)
                entry=QListWidgetItem(f'{tier_name}   ·   {count} команд')
                entry.setData(Qt.UserRole,tier_value)
                self.command_tiers.addItem(entry)
        self.command_tiers.setCurrentRow(0)
        self.command_tiers.currentRowChanged.connect(self.update_search)
        self.command_tiers.setToolTip('Список уровней. Выберите уровень для фильтрации команд')
        search_layout.addWidget(self.command_tiers)
        self.command_tiers.setVisible(False)
        self.context_label=QLabel('Правила • все разделы');self.context_label.setObjectName('categorySummary');search_layout.addWidget(self.context_label)
        self.results=QListWidget()
        self.results.setWordWrap(True)
        self.results.setTextElideMode(Qt.ElideNone)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.results.setItemDelegate(WrappingListDelegate(self.results))
        self.results.setSpacing(4)
        self.results.itemDoubleClicked.connect(self.open_full_record)
        search_layout.addWidget(self.results,1)
        self.binder_page=QWidget();self.stack.addWidget(self.binder_page)
        bl=QVBoxLayout(self.binder_page);bar=QHBoxLayout();self.binder_heading=QLabel('Триггеры');self.binder_heading.setObjectName('heading');bar.addWidget(self.binder_heading);bar.addStretch()
        self.enabled_box=QCheckBox('Биндер активен');self.enabled_box.toggled.connect(self.toggle_engine);bar.addWidget(self.enabled_box)
        bl.addLayout(bar);self.record_list=QListWidget();self.record_list.setWordWrap(True);self.record_list.setSpacing(8);self.record_list.setTextElideMode(Qt.ElideNone);self.record_list.setItemDelegate(WrappingListDelegate(self.record_list));self.record_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.record_list.itemDoubleClicked.connect(lambda _item:self.edit_record());bl.addWidget(self.record_list,1)
        actions=QHBoxLayout()
        for text_,handler in [('＋ Создать',self.add_record),('Изменить',self.edit_record),('Удалить',self.delete_record)]:
            btn=QPushButton(text_);btn.clicked.connect(handler);actions.addWidget(btn)
        bl.addLayout(actions)
        self.profile_page=QWidget();self.stack.addWidget(self.profile_page)
        pl=QVBoxLayout(self.profile_page);pl.addWidget(QLabel('Профили биндера'))
        self.profile_combo=QComboBox();self.profile_combo.currentTextChanged.connect(self.on_profile_changed);pl.addWidget(self.profile_combo)
        name_row=QHBoxLayout();self.new_profile=QLineEdit();self.new_profile.setPlaceholderText('Название нового профиля');name_row.addWidget(self.new_profile)
        create_btn=QPushButton('Создать профиль');create_btn.clicked.connect(self.create_profile);name_row.addWidget(create_btn);pl.addLayout(name_row)
        file_row=QHBoxLayout()
        import_btn=QPushButton('↓ Загрузить профиль из файла');import_btn.clicked.connect(self.import_profile);file_row.addWidget(import_btn)
        export_btn=QPushButton('↑ Сохранить профиль в файл');export_btn.clicked.connect(self.export_profile);file_row.addWidget(export_btn)
        pl.addLayout(file_row)
        self.profile_info=QLabel('Все триггеры, бинды и макросы сохраняются отдельно для каждого профиля.');self.profile_info.setWordWrap(True);pl.addWidget(self.profile_info);pl.addStretch()
        self.settings_page=QWidget();self.stack.addWidget(self.settings_page)
        sl_root=QVBoxLayout(self.settings_page);sl_root.setContentsMargins(0,0,0,0)
        settings_scroll=QScrollArea();settings_scroll.setWidgetResizable(True);settings_scroll.setFrameShape(QFrame.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sl_root.addWidget(settings_scroll)
        settings_host=QWidget();settings_scroll.setWidget(settings_host)
        sl=QVBoxLayout(settings_host);sl.setContentsMargins(4,2,12,12);sl.setSpacing(15)
        head=QLabel('Настройки');head.setObjectName('heading');sl.addWidget(head)
        description=QLabel('Управляйте помощником, обновлениями и биндером в одном месте.')
        description.setObjectName('subtitle');description.setWordWrap(True);sl.addWidget(description)
        general_card=QFrame();general_card.setObjectName('settingsCard');gc=QVBoxLayout(general_card);gc.setSpacing(14)
        general_title=QLabel('Приложение');general_title.setObjectName('cardTitle');gc.addWidget(general_title)
        self.zoom_label=QLabel('Масштаб: 100%  (Ctrl + / Ctrl - / Ctrl 0)');gc.addWidget(self.zoom_label)
        zoom_line=QHBoxLayout()
        for caption,delta in [('−',-10),('+',10)]:
            z=QPushButton(caption);z.setToolTip('Масштаб интерфейса');z.clicked.connect(lambda checked=False,d=delta:self.adjust_ui_scale(d));zoom_line.addWidget(z)
        gc.addLayout(zoom_line)
        self.pin_sidebar=QCheckBox('Закрепить раскрытую левую панель')
        self.pin_sidebar.toggled.connect(self.toggle_sidebar_pin);gc.addWidget(self.pin_sidebar)
        self.auto_update=QCheckBox('Проверять обновления правил автоматически (раз в 24 часа)')
        self.auto_update.setChecked(self.cfg.get('auto_update',True));self.auto_update.toggled.connect(self.change_auto_update);gc.addWidget(self.auto_update)
        self.autostart=QCheckBox('Запускать Santa Maria Helper вместе с Windows')
        self.autostart.setChecked(self.cfg.get('start_with_windows',False));self.autostart.toggled.connect(self.change_autostart);gc.addWidget(self.autostart)
        gc.addWidget(QLabel('Обновления приложения через GitHub Releases'))
        self.github_repo=QLineEdit(self.cfg.get('github_repo',''))
        self.github_repo.setPlaceholderText('владелец/репозиторий (например, myname/SantaMariaHelper)')
        self.github_repo.editingFinished.connect(self.save_github_repo)
        self.app_update_auto=QCheckBox('Проверять обновления приложения при запуске')
        self.app_update_auto.setChecked(self.cfg.get('app_update_auto',True))
        self.app_update_auto.toggled.connect(self.change_app_update_auto);gc.addWidget(self.app_update_auto)
        self.app_update_button=QPushButton('Проверить обновление приложения')
        self.app_update_button.clicked.connect(self.check_app_update);gc.addWidget(self.app_update_button)
        self.app_update_note=QLabel('Версия '+VERSION+'. Укажите GitHub-репозиторий для обновлений.')
        self.app_update_note.setWordWrap(True);gc.addWidget(self.app_update_note)
        general_hint=QLabel('При закрытии окно скрывается в трей. Включённый биндер продолжает работать.')
        general_hint.setObjectName('subtitle');general_hint.setWordWrap(True);gc.addWidget(general_hint)
        sl.addWidget(general_card)
        identity_card=QFrame();identity_card.setObjectName('settingsCard')
        il=QVBoxLayout(identity_card);il.addWidget(QLabel('Администратор: '+self.account['login']+' · '+LEVEL_NAMES.get(self.account['level'],'?')))
        exit_user=QPushButton('Сменить администратора / выйти');exit_user.clicked.connect(self.logout_account)
        il.addWidget(exit_user);sl.addWidget(identity_card)
        model_card=QFrame();model_card.setObjectName('settingsCard');mc=QVBoxLayout(model_card);mc.setSpacing(13)
        model_title=QLabel('Santa Maria Helper · Нейросеть');model_title.setObjectName('cardTitle');mc.addWidget(model_title)
        intro=QLabel('Облачный ИИ работает через сервер. Друзьям не нужно устанавливать нейросеть или вводить API-ключ. Владелец один раз настраивает сервер Cloudflare.')
        intro.setWordWrap(True);intro.setObjectName('subtitle');mc.addWidget(intro)
        self.cloud_url=QLineEdit(self.cfg.get('cloud_url') or DEFAULT_CLOUD_URL)
        self.cloud_url.setPlaceholderText('https://your-helper.your-name.workers.dev')
        self.cloud_url.editingFinished.connect(self.save_cloud_url)
        test_btn=QPushButton('Проверить сервер');test_btn.clicked.connect(self.check_cloud);mc.addWidget(test_btn)
        self.model_status=QLabel('Впишите адрес опубликованного сервера. Этот адрес можно встроить в клиент для друзей.')
        self.model_status.setWordWrap(True);self.model_status.setObjectName('modelStatus');mc.addWidget(self.model_status)
        # User-facing cloud status remains accessible, but the endpoint is protected.
        sl.addWidget(model_card)
        developer_entry=QPushButton('🔒 Для разработчиков')
        developer_entry.setToolTip('Технические настройки доступны после ввода пароля')
        developer_entry.clicked.connect(self.open_developer_settings)
        sl.addWidget(developer_entry)
        self.developer_entry=developer_entry
        self.developer_entry.setVisible(self.account['level']>=DEV_MIN_LEVEL)
        self.developer_card=QFrame();self.developer_card.setObjectName('settingsCard')
        dev=QVBoxLayout(self.developer_card)
        dev.addWidget(QLabel('Для разработчиков · технические настройки'))
        users_button=QPushButton('Администраторы · логины, пароли, уровни')
        users_button.clicked.connect(self.manage_accounts);dev.addWidget(users_button)
        commands_button=QPushButton('Редактор команд · добавить, изменить, удалить')
        commands_button.clicked.connect(self.manage_commands);dev.addWidget(commands_button)
        dev.addWidget(QLabel('Адрес Cloudflare Worker (HTTPS)'))
        dev.addWidget(self.cloud_url)
        dev.addWidget(QLabel('Репозиторий обновлений GitHub (владелец/репозиторий)'))
        dev.addWidget(self.github_repo)
        lock_button=QPushButton('Закрыть настройки разработчика')
        lock_button.clicked.connect(self.lock_developer_settings)
        dev.addWidget(lock_button)
        self.developer_card.setVisible(False)
        sl.addWidget(self.developer_card)
        notes=QFrame();notes.setObjectName('settingsCard');nl=QVBoxLayout(notes)
        nl.addWidget(QLabel('Данные и профили'))
        hint=QLabel('Профили биндера сохраняются локально. Правила работают из сохранённой базы, даже если форум временно недоступен.')
        hint.setObjectName('subtitle');hint.setWordWrap(True);nl.addWidget(hint);sl.addWidget(notes)
        sl.addStretch()
        footer=QHBoxLayout();self.sync_note=QLabel('');footer.addWidget(self.sync_note,1);credit=QLabel('by taras_chereshnia');credit.setObjectName('credit');footer.addWidget(credit)
        self.resize_grip=QSizeGrip(self)
        self.resize_grip.setFixedSize(23,23)
        self.resize_grip.setToolTip('Потяните для изменения размера окна')
        footer.addWidget(self.resize_grip);outer.addLayout(footer)
        self.base_stylesheet='''
QWidget { background:#141210; color:#f3d6b9; font:14px "Segoe UI"; }
QFrame#side {background:#1c1815; border-right:1px solid #34281f;}
QFrame#settingsCard {background:#221b16; border:1px solid #3b2a1f; border-radius:16px; padding:12px;}
QLabel#cardTitle {font-size:19px; font-weight:700; color:#ffbf85;}
QLabel#modelStatus {background:#332419; color:#ffcd97; padding:12px; border-radius:9px; font-size:14px;}
QScrollArea {border:0; background:transparent;}
QScrollArea > QWidget > QWidget {background:transparent;}
QLabel#title {color:#ffa347; font-size:20px; font-weight:700;}
QLabel#heading {color:#ffe5ce; font-size:24px; font-weight:700;}
QLabel#subtitle {color:#a99482; font-size:14px;}
QLabel#credit {color:#ffab59; font-size:12px; font-weight:600;}
QLabel#categorySummary {font-size:13px; color:#ddb38e; padding:8px 10px; background:#251d17; border-radius:9px;}
QPushButton {background:#30241c; color:#ffbf85; border:1px solid #473222; border-radius:11px; padding:11px 15px; font-weight:600; text-align:center;}
QPushButton:hover {background:#4a3121; border-color:#9c5b2e;}
QPushButton:pressed {background:#6c3a1c;}
QPushButton:disabled {color:#796b60; background:#231c17;}
QPushButton#windowAction {background:transparent; border:0; padding:0; border-radius:7px;}
QPushButton#windowAction:hover {background:#3b2c24;}
QLineEdit,QTextEdit,QTextBrowser,QListWidget,QTreeWidget,QComboBox {background:#211b17; color:#ffffff; border:1px solid #433124; border-radius:12px; padding:12px; selection-background-color:#a15c2b;}
QLineEdit,QTextEdit {selection-color:#ffffff;}
QTextEdit#question {color:#ffffff; font-size:16px; padding:15px; border:1px solid #6a4529; background:#251e19;}
QTextBrowser#answer {background:#1b1714; border:1px solid #382b22; border-radius:14px; font-size:15px; padding:15px;}
QFrame#side QPushButton {text-align:left; padding-left:13px; padding-right:7px; font-size:14px; min-height:26px;}
QFrame#side QLabel {padding-left:5px;}
QTreeWidget::item {padding:3px 1px; min-height:24px;}
QTreeWidget::item:selected {background:#69452a; border-radius:6px;}
QListWidget::item {padding:7px 9px; border-radius:8px;}
QListWidget::item:selected {background:#503321; color:#ffe4c8;}
QListWidget::item:hover {background:#3a2920;}
QCheckBox {spacing:8px;}
QScrollBar:vertical {background:#1d1713; width:12px; border:0; margin:0;}
QScrollBar::handle:vertical {background:#8d5b39; border-radius:5px; min-height:35px;}
QScrollBar::handle:vertical:hover {background:#c27b44;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0px;}
'''
        self.apply_ui_scale(self.cfg.get('ui_scale',100))
        QShortcut(QKeySequence('Ctrl++'), self, activated=lambda:self.adjust_ui_scale(10))
        QShortcut(QKeySequence('Ctrl+='), self, activated=lambda:self.adjust_ui_scale(10))
        QShortcut(QKeySequence('Ctrl+-'), self, activated=lambda:self.adjust_ui_scale(-10))
        QShortcut(QKeySequence('Ctrl+0'), self, activated=lambda:self.apply_ui_scale(100))
        self.navigate('ai')
        self.hover_timer=QTimer(self);self.hover_timer.timeout.connect(self.check_sidebar_hover)
        self.hover_timer.start(85)
    def adjust_ui_scale(self, amount):
        self.apply_ui_scale(self.ui_scale + amount)

    def apply_ui_scale(self, percent):
        self.ui_scale = max(75, min(135, int(percent)))
        # Scale type and spacing together instead of stretching bitmap content.
        self.setStyleSheet(re.sub(r'(\d+)px', lambda m: str(max(1,round(int(m.group(1))*self.ui_scale/100)))+'px',self.base_stylesheet))
        self.cfg['ui_scale']=self.ui_scale
        write_json(CONFIG,self.cfg)
        if hasattr(self,'zoom_label'):self.zoom_label.setText(f'Масштаб: {self.ui_scale}%  (Ctrl + / Ctrl - / Ctrl 0)')
        if hasattr(self,'rule_tree'):self.rule_tree.doItemsLayout()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'results'):
            # Recalculate wrapping after resizing the overlay without rebuilding search results.
            if not hasattr(self,'result_relayout_timer'):
                self.result_relayout_timer=QTimer(self)
                self.result_relayout_timer.setSingleShot(True)
                self.result_relayout_timer.timeout.connect(lambda: (self.results.doItemsLayout(),self.record_list.doItemsLayout()))
            self.result_relayout_timer.start(90)
        if hasattr(self,'sidebar') and self.expanded:
            desired=min(self.expanded_width,max(210,int(self.width()*0.38)))
            self.sidebar.setMaximumWidth(desired)
            if hasattr(self,'rule_tree'):self.rule_tree.doItemsLayout()

    def check_sidebar_hover(self):
        if not self.isVisible() or self.isMinimized():return
        point=self.sidebar.mapFromGlobal(QCursor.pos())
        # A fixed narrow hover zone opens the menu. A slightly padded, full
        # expanded rectangle keeps it open while the cursor moves to buttons.
        if self.expanded:
            inside=(-6 <= point.x() <= self.sidebar.width()+8 and -6 <= point.y() <= self.sidebar.height()+6)
        else:
            inside=(0 <= point.x() <= self.collapsed_width+4 and 0 <= point.y() < self.sidebar.height())
        if not self.sidebar_pinned and inside != self.expanded:self.expand_side(inside)
    def expand_side(self, yes):
        if self.expanded==yes:return
        self.expanded=yes
        self.side_animation.stop();self.side_animation.setStartValue(self.sidebar.maximumWidth());self.side_animation.setEndValue(min(self.expanded_width,max(210,int(self.width()*0.38))) if yes else self.collapsed_width);self.side_animation.start()
        for button,label in self.side_labels:button.setText(label if yes else {'Santa Maria Helper':'✦','Правила':'≡','Команды':'⌨','Триггеры':'⚡','Бинды':'⌘','Макросы':'▶','Профили':'▣','Настройки':'⚙'}[label])
        self.binder_title.setText('Santa Maria Binder' if yes else 'SM')
        self.rule_tree.setVisible(self.mode=='rules' and self.page=='search')
        self.status.setVisible(yes)
    def toggle_sidebar_pin(self, checked):
        self.sidebar_pinned=checked
        self.expand_side(checked)

    def navigate(self,key):
        if key=='ai':
            self.page='ai';self.stack.setCurrentWidget(self.ai_page)
        elif key in ('rules','commands'):
            self.page='search';self.mode=key;self.stack.setCurrentWidget(self.search_page)
            # Admin commands use the whole page; no extra context/update block.
            self.context_label.setVisible(key=='rules')
            self.update_button.setVisible(key=='rules')
            self.rule_tree.setVisible(key=='rules')
            self.command_tiers.setVisible(key=='commands')
            self.update_search()
        elif key in ('trigger','bind','macro'):
            self.page=key;self.stack.setCurrentWidget(self.binder_page);self.binder_heading.setText({'trigger':'Триггеры','bind':'Бинды','macro':'Макросы'}[key]);self.refresh_records()
        elif key=='profiles':self.page=key;self.stack.setCurrentWidget(self.profile_page)
        else:self.page=key;self.stack.setCurrentWidget(self.settings_page)
        if key not in ('rules','commands'):
            self.rule_tree.setVisible(False)
            self.command_tiers.setVisible(False)
    def open_developer_settings(self):
        if self.account['level']<DEV_MIN_LEVEL:
            QMessageBox.warning(self,'Доступ запрещён','Только ЗГА, ГА и Спец админ имеют доступ к разделу.');return
        dialog=QDialog(self)
        dialog.setWindowTitle('Для разработчиков · Вход')
        dialog.setMinimumWidth(340)
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Введите пароль разработчика'))
        password=QLineEdit(dialog)
        password.setEchoMode(QLineEdit.Password)
        password.setPlaceholderText('Пароль')
        layout.addWidget(password)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        password.returnPressed.connect(dialog.accept)
        if dialog.exec()!=QDialog.Accepted:
            return
        if not verify_developer_password(password.text()):
            QMessageBox.warning(self,'Доступ запрещён','Неверный пароль разработчика.')
            return
        self.developer_card.setVisible(True)
        self.cloud_url.setFocus()

    def manage_accounts(self):
        if self.account['level']<DEV_MIN_LEVEL or not self.developer_card.isVisible():return
        AccountManagerDialog(self.store,self.account['id'],self.account['level'],self).exec()

    def manage_commands(self):
        if self.account['level']<DEV_MIN_LEVEL or not self.developer_card.isVisible():return
        editor=CommandManagerDialog(self.all_commands,self)
        if editor.exec()==QDialog.Accepted:
            self.all_commands=editor.commands
            write_json(COMMANDS_FILE,self.all_commands)
            self.commands=[c for c in self.all_commands if can_use_command(self.account['level'],c)]
            self.update_search()

    def logout_account(self):
        self.save_profile()
        self.update_engine_state(False)
        dialog=LoginDialog(self.store,self)
        if dialog.exec()!=QDialog.Accepted:return
        # Relaunch the window to prevent state mixing across user accounts.
        next_window=Window(dialog.account,self.store)
        QApplication.instance()._active_window=next_window
        next_window.show();self.tray.hide();self.deleteLater()

    def lock_developer_settings(self):
        self.developer_card.setVisible(False)

    def save_cloud_url(self):
        url=self.cloud_url.text().strip().rstrip('/')
        self.cfg['cloud_url']=url
        write_json(CONFIG,self.cfg)
    def check_cloud(self):
        self.save_cloud_url()
        url=self.cfg.get('cloud_url',DEFAULT_CLOUD_URL)
        if not url.startswith('https://'):
            self.model_status.setText('Укажите полный HTTPS-адрес сервера.');return
        import requests
        try:
            r=requests.get(url+'/health',timeout=8);r.raise_for_status()
            body=r.json()
            self.model_status.setText(('✓ ИИ-сервер V'+str(body.get('version','?'))+' готов' if str(body.get('version'))=='33' else '⚠ Сервер отвечает, но нужен Worker V33 (сейчас '+str(body.get('version','неизвестная версия'))+')') if body.get('ok') else 'Сервер доступен, но не готов')
        except Exception as ex:self.model_status.setText('Не удалось подключиться: '+str(ex)[:160])
    def ask_question(self):
        q=self.ai_question.toPlainText().strip()
        if not q:return
        if self.answerer and self.answerer.isRunning():return
        # Always send the full question. Semantic interpretation runs inside the worker,
        # never blocking the UI thread, and works even without keyword matches.
        self.last_spell_edits=[]
        self.ai_ask.setEnabled(False);self.ai_status.setText('Анализируем ситуацию…')
        self.ai_answer.setHtml("<p style='color:#f6bd82;font-size:17px'>Анализируем вопрос и проверяем правила…</p>")
        self.answerer=AnswerWorker(q,self.rules,self.commands,(self.cfg.get('cloud_url') or DEFAULT_CLOUD_URL),self.rule_index,self)
        self.answerer.answer_ready.connect(self.receive_answer)
        self.answerer.finished.connect(lambda:self.ai_ask.setEnabled(True))
        self.answerer.start()
    def welcome_html(self):
        return ("<div style='color:#b9a08a;font-size:15px;padding:12px'>"
                "<p style='color:#ffd5ad;font-size:21px;font-weight:700'>Какой у вас вопрос?</p>"
                "<p>Опишите ситуацию своими словами. Santa Maria Helper объяснит возможные нарушения "
                "и покажет связанные команды из загруженной базы.</p>"
                "<p style='color:#c58b55'>Например: «Игрок убил другого без причины. Какое правило относится к ситуации?»</p></div>")
    def receive_answer(self,result):
        esc=lambda v:html.escape(str(v or ''))
        if result.get('retry') or result.get('error'):
            reason=result.get('reason','temporary')
            detail = ('Попробуйте задать вопрос ещё раз, немного иначе. Уточните, что произошло, и при необходимости укажите название нарушения.'
                      if reason == 'no_evidence' else
                      'Попробуйте задать вопрос ещё раз чуть позже. Если ошибка повторяется, проверьте подключение к интернету.')
            local_rules=result.get('local_rules') or []
            if local_rules:
                rows=[]
                for r in local_rules[:4]:
                    rows.append('<p style="background:#302218;padding:10px"><b>'+esc(r.get('topic'))+'</b>'
                                +'<br>'+esc(r.get('text'))[:900]+'</p>')
                self.ai_answer.setHtml('<div style="font-size:15px;color:#eac5a3;padding:12px">'
                    '<b style="color:#ffac64">Облачный ИИ временно не ответил</b>'
                    '<p>Ниже показаны найденные пункты из локальной базы. Это не вывод ИИ: '
                    'проверьте, какой пункт относится к вашей ситуации.</p>'
                    +''.join(rows)+'</div>')
                self.ai_status.setText('Показаны локальные правила · облачный ИИ недоступен')
            else:
                self.ai_answer.setHtml("<div style='font-size:16px;color:#eac5a3;padding:15px'>"
                    "<b style='color:#ffac64'>Не получилось подготовить ответ</b><p>"+esc(detail)+"</p></div>")
                self.ai_status.setText('Попробуйте задать вопрос ещё раз')
            return
        status=result.get('status')
        if status=='general':
            result['answer']='Общее объяснение (неофициальная трактовка, не правило Santa Maria RP):\n\n'+str(result.get('answer',''))
        if status in ('clarification','insufficient_evidence'):
            if status=='clarification':
                content=result.get('clarification') or 'Уточните, что произошло, кто участвовал и при каких обстоятельствах.'
                heading='Уточните ситуацию'
            else:
                content=result.get('answer') or 'Пока нет подтверждённого вывода. Уточните, какие организации и обстоятельства имеются в виду.'
                heading='Не удалось подтвердить нарушение'
            suggestions=[]
            if status=='insufficient_evidence':
                for rule in result.get('local_rules',[])[:5]:
                    url=str(rule.get('url',''))
                    link=("<br><a href='"+esc(url)+"'>Открыть тему форума</a>" if url.startswith('https://forum.santamaria-rp.com/') else '')
                    suggestions.append('<p style="background:#302218;padding:8px"><b>'+esc(rule.get('topic',''))+'</b><br>'+esc(str(rule.get('text',''))[:470])+link+'</p>')
            self.ai_answer.setHtml("<div style='font-size:16px;color:#eac5a3;padding:15px'>"
                "<b style='color:#ffac64'>"+esc(heading)+"</b><p>"+esc(content)+"</p>"
                +(('<p>Возможно, относятся следующие пункты (не проверенное ИИ заключение):</p>'+''.join(suggestions)) if suggestions else '')+'</div>')
            self.ai_status.setText('Нужно уточнение' if status=='clarification' else 'Недостаточно подтверждений')
            return
        parts=["<div style='font-family:Segoe UI;color:#efd2b8;font-size:15px'>",
               "<p style='font-size:21px;color:#ffb365;font-weight:700;margin:4px 0 10px'>Ответ</p>",
               "<p style='line-height:145%'>"+esc(result.get('answer','Недостаточно данных для точного ответа.'))+"</p>"]
        rules=result.get('rules',[])
        if rules:
            parts.append("<p style='font-size:18px;color:#ffb365;font-weight:700;margin-top:22px'>Подходящие правила</p>")
            for x in rules[:5]:
                if not isinstance(x,dict):continue
                parts.append("<p style='background:#302218;padding:10px;border-radius:8px'><b style='color:#ffc28a'>"+esc(x.get('title'))+"</b><br>"+esc(x.get('explanation'))+("<br><a href='"+esc(x.get('url'))+"'>Открыть правило на форуме</a>" if str(x.get('url','')).startswith('https://forum.santamaria-rp.com/') else "")+"</p>")
        commands=result.get('commands',[])
        if commands:
            parts.append("<p style='font-size:18px;color:#ffb365;font-weight:700;margin-top:22px'>Связанные команды</p>")
            for x in commands[:6]:
                if not isinstance(x,dict):continue
                parts.append("<p><b style='color:#ffb365'>"+esc(x.get('command'))+"</b> — "+esc(x.get('purpose'))+"</p>")

        parts.append("<p style='color:#aa907b;font-size:12px;margin-top:24px'>Ответ составлен по найденным материалам; окончательное решение принимайте по актуальным правилам проекта.</p></div>")
        self.ai_answer.setHtml(''.join(parts))
        edits = getattr(self,'last_spell_edits',[])
        if edits:
            self.ai_status.setText('Ответ готов · учтены возможные опечатки')
        else:
            self.ai_status.setText('Ответ готов')
    def reload_rules(self):
        bundled=read_json(ROOT/'forum_rules.json',[])
        if not isinstance(bundled,list) or not bundled: bundled=read_json(ROOT/'rules.json',[])
        cached=read_json(RULE_CACHE,None)
        # An old, incomplete cache must not silently replace the packaged 942 rules.
        rules=cached if isinstance(cached,list) and len(cached)>=len(bundled) and len({r.get('url') for r in cached if isinstance(r,dict)})>=len({r.get('url') for r in bundled if isinstance(r,dict)}) else bundled
        self.rules=repair_records(rules,ROOT/'rules.json')
        self.rule_index=FastRuleIndex(self.rules)
        self.make_rule_tree();self.update_search()
    def make_rule_tree(self):
        """Main rules page: categories with expandable TOPICS, no rules in the navigation rail."""
        self.rule_tree.setUpdatesEnabled(False)
        self.rule_tree.clear()
        counts={}
        for r in self.rules:
            section=str(r.get('section','Дополнительные правила'))
            topic=str(r.get('topic','Без темы'))
            counts.setdefault(section,{})[topic]=counts.setdefault(section,{}).get(topic,0)+1
        for section in CATEGORY_ORDER:
            topics=counts.get(section,{})
            if not topics:continue
            total=sum(topics.values())
            item=QTreeWidgetItem([f'{section}  ·  {total}'])
            item.setData(0,Qt.UserRole,('section',section))
            item.setToolTip(0,section)
            self.rule_tree.addTopLevelItem(item)
            for topic in sorted(topics,key=str.casefold):
                child=QTreeWidgetItem([f'{topic}  ·  {topics[topic]}'])
                child.setData(0,Qt.UserRole,('topic',(section,topic)))
                child.setToolTip(0,topic)
                item.addChild(child)
        self.rule_tree.setRootIsDecorated(True)
        self.rule_tree.setIndentation(16)
        self.rule_tree.setUpdatesEnabled(True)
        self.rule_tree.setVisible(self.mode=='rules' and self.page=='search')

    def select_category(self,item,column):
        chosen=item.data(0,Qt.UserRole)
        if not chosen:return
        # Click the arrow to expand child topics; clicking the category also filters.
        self.category=None if self.category==chosen else chosen
        self.update_search()

    def score(self,query,record):
        title=' '.join(str(record.get(k,'')) for k in ('id','topic','text','command','description'))
        text=normalize(title);q=normalize(query)
        if not q:return 1
        if q in text:return 20+len(q)/max(len(text),1)
        parts=q.split();hits=sum(1 for word in parts if word in text)
        score=hits/max(len(parts),1)*10
        if score<5 and len(q)>=4:score+=SequenceMatcher(None,q,text[:max(90,len(q)*3)]).ratio()*3
        return score
    def update_search(self):
        if not hasattr(self,'results'):return
        self.results.setUpdatesEnabled(False)
        self.results.clear()
        query=self.search.text().strip()
        records=self.rules if self.mode=='rules' else self.commands
        if self.mode=='commands' and self.command_tiers.currentItem() and self.command_tiers.currentItem().data(Qt.UserRole) is not None:
            tier=self.command_tiers.currentItem().data(Qt.UserRole)
            records=[c for c in records if int(c.get('level',2) or 2)==tier]
        if self.mode=='rules' and self.category:
            typ,val=self.category
            if typ=='section':records=[r for r in records if r.get('section')==val]
            elif typ=='topic':records=[r for r in records if (r.get('section'),r.get('topic'))==tuple(val)]
        if query:
            matches=[(self.score(query,r),r) for r in records]
            filtered=[(score,r) for score,r in matches if score>=3.2]
            # When searching: relevance ranking within each topic, not random-looking order.
        else:filtered=[(1,r) for r in records]
        if self.mode=='rules':
            grouped={}
            for rank,r in filtered:
                grouped.setdefault((r.get('section',''),r.get('topic','')),[]).append((rank,r))
            topics=sorted(grouped,key=lambda k:(CATEGORY_ORDER.index(k[0]) if k[0] in CATEGORY_ORDER else 999,
                                                  0 if k[1].casefold().startswith(('общие правила','правила государственных')) else 1,
                                                  k[1].casefold()))
            self.filtered=[]
            for category,topic in topics:
                subset=grouped[(category,topic)]
                if query:subset.sort(key=lambda x:(-x[0],number_sort(x[1].get('rule_number') or x[1].get('id'))))
                else:subset.sort(key=lambda x:number_sort(x[1].get('rule_number') or x[1].get('id')))
                header=QListWidgetItem(f'▰  {topic}   ·   {len(subset)} пунктов')
                header.setFlags(Qt.NoItemFlags)
                header.setForeground(QBrush(QColor('#ffbe78')))
                header.setBackground(QBrush(QColor('#302116')))
                header.setToolTip(topic)
                self.results.addItem(header)
                for _rank,r in subset:
                    index=len(self.filtered);self.filtered.append(r)
                    num=str(r.get('rule_number') or r.get('id') or '—')
                    text=str(r.get('text','')).strip()
                    # Avoid repeating the number twice while keeping original full text in tooltip/dialog.
                    short=re.sub(r'^\s*'+re.escape(num)+r'(?=[\s.:)\-]|$)\s*[.:)]?\s*','',text,count=1)
                    preview=short[:380].replace('\n','  ·  ')
                    if len(short)>380:preview+='…'
                    row=QListWidgetItem(f'ПУНКТ {num}\n{preview}')
                    row.setData(Qt.UserRole,index)
                    row.setForeground(QBrush(QColor('#f0d8c3')))
                    row.setBackground(QBrush(QColor('#241c17')))
                    row.setToolTip('Двойной щелчок — полный текст и ссылка на форум\n'+text[:1900])
                    self.results.addItem(row)
            label=((' · '.join(self.category[1]) if self.category[0]=='topic' else self.category[1]) if self.category else 'Все категории')
            self.context_label.setText(f'{label}  •  {len(self.filtered)} пунктов  •  {len(topics)} тем')
            self.context_label.setVisible(True)
        else:
            self.filtered=[r for score,r in sorted(filtered,key=lambda x: (-x[0],x[1].get('command',''))) ] if query else sorted((r for _score,r in filtered),key=lambda r:r.get('command',''))
            for index,r in enumerate(self.filtered):
                row=QListWidgetItem(f"{r.get('command','')}\n{r.get('description','')}")
                row.setData(Qt.UserRole,index)
                row.setBackground(QBrush(QColor('#241c17')))
                row.setToolTip('Двойной щелчок — описание команды')
                self.results.addItem(row)
            self.context_label.setVisible(False)
        self.results.setUpdatesEnabled(True)

    def open_forum_url(self,url):
        # Only open safe public forum destinations; never navigate QTextBrowser internally.
        from urllib.parse import urlsplit
        value=url.toString()
        parsed=urlsplit(value)
        if parsed.scheme=='https' and parsed.hostname=='forum.santamaria-rp.com':
            QDesktopServices.openUrl(url)

    def open_full_record(self,item):
        row=item.data(Qt.UserRole)
        if not isinstance(row,int) or not (0<=row<len(self.filtered)):return
        r=self.filtered[row]
        self.show_record_dialog(r)

    def show_record_dialog(self,r):
        dialog=QDialog(self);dialog.setWindowTitle('Полный текст правила' if self.mode=='rules' else 'Команда')
        dialog.resize(min(780,max(440,self.width()-80)),min(560,max(330,self.height()-90)))
        layout=QVBoxLayout(dialog)
        viewer=QTextBrowser();viewer.setOpenExternalLinks(False);viewer.setOpenLinks(False);viewer.anchorClicked.connect(self.open_forum_url)
        title=r.get('topic','Правило') if self.mode=='rules' else r.get('command','Команда')
        content=r.get('text','') if self.mode=='rules' else r.get('description','')
        link=str(r.get('url',''))
        page='<h3>'+html.escape(str(title))+'</h3><p style="white-space:pre-wrap">'+html.escape(str(content)).replace('\n','<br>')+'</p>'
        if link.startswith('https://forum.santamaria-rp.com/'):
            page+='<p><a href="'+html.escape(link,quote=True)+'">Открыть тему на форуме</a></p>'
        viewer.setHtml(page);layout.addWidget(viewer)
        close=QPushButton('Закрыть');close.clicked.connect(dialog.accept);layout.addWidget(close)
        dialog.exec()
    def maybe_sync(self):
        if not self.cfg.get('auto_update',True) or self.syncer is not None:return
        last=self.cfg.get('last_sync','')
        try:recent=datetime.now(timezone.utc)-datetime.fromisoformat(last)<timedelta(hours=24)
        except (ValueError,TypeError):recent=False
        if not recent:self.sync_rules()
    def sync_rules(self):
        if self.syncer is not None:return
        self.syncer=SyncWorker(self);self.syncer.status.connect(self.sync_note.setText)
        self.syncer.finished_sync.connect(self.sync_finished)
        self.update_button.setEnabled(False);self.sync_note.setText('Синхронизация форума…');self.syncer.start()
    def sync_finished(self,ok,msg):
        self.sync_note.setText(msg);self.update_button.setEnabled(True)
        worker=self.syncer;self.syncer=None
        if worker:worker.deleteLater()
        if ok:self.cfg['last_sync']=datetime.now(timezone.utc).isoformat();self.save_config();self.reload_rules()
    def save_config(self):write_json(CONFIG,self.cfg)
    def refresh_profiles(self):
        profiles=sorted(p.stem for p in (PROFILE_DIR/self.account['id']).glob('*.json'))
        if 'Основной' not in profiles:profiles.insert(0,'Основной')
        self.profile_combo.blockSignals(True);self.profile_combo.clear();self.profile_combo.addItems(profiles)
        self.profile_combo.blockSignals(False)
    def load_profile(self,name):
        self.profile_cfg['profile']=name
        write_json(self.account_dir/'profile_config.json',self.profile_cfg)
        self.items=read_json(profile_path(name,self.account['id']),[])
        self.items=[r for r in self.items if isinstance(r,dict)]
        self.profile_combo.blockSignals(True);self.profile_combo.setCurrentText(name);self.profile_combo.blockSignals(False)
        self.engine.set_items(self.items);self.refresh_records();self.save_config()
    def save_profile(self):write_json(profile_path(self.profile_cfg['profile'],self.account['id']),self.items);self.engine.set_items(self.items)
    def on_profile_changed(self,name):
        if name:self.load_profile(name)
    def create_profile(self):
        try:p=profile_path(self.new_profile.text(),self.account['id'])
        except ValueError as e:QMessageBox.warning(self,'Профиль',str(e));return
        if p.exists():QMessageBox.warning(self,'Профиль','Профиль уже существует');return
        write_json(p,[]);self.refresh_profiles();self.load_profile(p.stem);self.new_profile.clear()
    def import_profile(self):
        filename,_=QFileDialog.getOpenFileName(self,'Загрузить профиль',str(Path.home()),'Профиль Santa Maria (*.json);;JSON (*.json)')
        if not filename:return
        try:
            if Path(filename).stat().st_size>2_000_000:raise ValueError('Файл слишком большой (максимум 2 МБ)')
            raw=json.loads(Path(filename).read_text(encoding='utf-8-sig'))
            # Accept plain profile lists and wrapped format for compatibility.
            records=raw.get('items') if isinstance(raw,dict) else raw
            if not isinstance(records,list):raise ValueError('Неверный формат: ожидается список записей')
            if len(records)>1000:raise ValueError('Слишком много записей (максимум 1000)')
            checked=[validate(item) for item in records]
            suggested=Path(filename).stem
            from PySide6.QtWidgets import QInputDialog
            name,ok=QInputDialog.getText(self,'Импорт профиля','Как назвать профиль?',text=suggested)
            if not ok:return
            dst=profile_path(name,self.account['id'])
            if dst.exists():
                answer=QMessageBox.question(self,'Перезаписать профиль?',f'Профиль «{dst.stem}» уже существует. Заменить его содержимое?',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
                if answer!=QMessageBox.Yes:return
            write_json(dst,checked)
            self.refresh_profiles();self.load_profile(dst.stem)
            QMessageBox.information(self,'Профиль загружен',f'Загружено записей: {len(checked)}. Активный профиль: {dst.stem}')
        except (OSError,UnicodeError,ValueError,TypeError,KeyError) as ex:
            QMessageBox.warning(self,'Не удалось загрузить',f'Не удалось импортировать профиль: {ex}')
    def export_profile(self):
        name=self.profile_cfg.get('profile','Основной')
        filename,_=QFileDialog.getSaveFileName(self,'Сохранить профиль',str(Path.home()/(name+'.json')),'Профиль Santa Maria (*.json)')
        if not filename:return
        try:
            path=Path(filename)
            if path.suffix.lower()!='.json':path=path.with_suffix('.json')
            write_json(path,self.items)
            QMessageBox.information(self,'Профиль сохранён',f'Экспортировано записей: {len(self.items)}')
        except (OSError,ValueError) as ex:
            QMessageBox.warning(self,'Ошибка экспорта',str(ex))
    def refresh_records(self):
        if not hasattr(self,'record_list'):return
        self.record_list.clear()
        kind=self.page if self.page in ('trigger','bind','macro') else 'trigger'
        self.visible_indices=[]
        for idx,item in enumerate(self.items):
            if item.get('kind')!=kind:continue
            self.visible_indices.append(idx)
            state='● ВКЛЮЧЁН' if item.get('enabled') else '○ ВЫКЛЮЧЕН'
            name=item.get('name') or 'Без названия'
            shortcut=item.get('shortcut') or 'Не задано'
            payload=str(item.get('payload','')).replace('\n','   ↵   ')
            if len(payload)>230:payload=payload[:230]+'…'
            row=QListWidgetItem(f'{state}    {name}\n{shortcut}   →   {payload}')
            row.setBackground(QBrush(QColor('#302218' if item.get('enabled') else '#211d1a')))
            row.setForeground(QBrush(QColor('#ffdfc1' if item.get('enabled') else '#9f9388')))
            row.setToolTip('Двойной щелчок — редактировать запись')
            self.record_list.addItem(row)

    def run_binder_editor(self,dlg):
        was_running=self.engine.active
        if was_running:self.engine.stop()
        try:return dlg.exec()==QDialog.Accepted
        finally:
            if was_running:
                try:self.engine.start()
                except Exception as exc:
                    self.cfg['running']=False
                    self.enabled_box.setChecked(False)
                    QMessageBox.warning(self,'Биндер остановлен',str(exc))

    def add_record(self):
        dlg=Editor(self,kind=self.page)
        accepted=self.run_binder_editor(dlg)
        if accepted:self.items.append(dlg.result_item);self.save_profile();self.refresh_records()
    def record_index(self):
        row=self.record_list.currentRow()
        return self.visible_indices[row] if 0<=row<len(self.visible_indices) else None
    def edit_record(self):
        idx=self.record_index()
        if idx is None:return
        dlg=Editor(self,item=self.items[idx])
        accepted=self.run_binder_editor(dlg)
        if accepted:self.items[idx]=dlg.result_item;self.save_profile();self.refresh_records()
    def delete_record(self):
        idx=self.record_index()
        if idx is None:return
        if QMessageBox.question(self,'Удалить','Удалить выбранную запись?')==QMessageBox.Yes:
            self.items.pop(idx);self.save_profile();self.refresh_records()
    def update_engine_state(self,start):
        if start:
            try:self.engine.start()
            except Exception as e:self.cfg['running']=False;self.sync_note.setText(f'Биндер не запустился: {e}')
            else:self.cfg['running']=True
        else:self.engine.stop();self.cfg['running']=False
        self.enabled_box.blockSignals(True);self.enabled_box.setChecked(self.cfg['running']);self.enabled_box.blockSignals(False);self.save_config()
    def toggle_engine(self,checked):self.update_engine_state(checked)
    def on_engine_message(self,msg):self.sync_note.setText(msg[:150])
    def save_github_repo(self):
        self.cfg['github_repo']=self.github_repo.text().strip()
        self.save_config()
    def change_app_update_auto(self,on):
        self.cfg['app_update_auto']=on;self.save_config()
    def maybe_check_app_update(self):
        if self.cfg.get('app_update_auto',True) and self.cfg.get('github_repo'):
            self.check_app_update(silent=True)
    def check_app_update(self,silent=False):
        if self.update_worker and self.update_worker.isRunning():return
        repo=self.cfg.get('github_repo','')
        if not repo:
            if not silent:self.app_update_note.setText('Укажите GitHub-репозиторий в формате владелец/репозиторий.')
            return
        self.app_update_button.setEnabled(False)
        self.app_update_note.setText('Проверяем GitHub Releases…')
        self.update_worker=UpdateWorker(repo,parent=self)
        self.update_worker.update_result.connect(lambda data,err:self._queue_update_result(data,err,silent,False))
        self.update_worker.finished.connect(lambda:self.app_update_button.setEnabled(True))
        self.update_worker.start()
    def _queue_update_result(self,data,error,silent,is_download):
        # QThread must finish before its reference is replaced by a new worker.
        worker=self.update_worker
        worker.finished.connect(lambda: self._process_update_result(data,error,silent,is_download))
    def _process_update_result(self,data,error,silent,is_download):
        if is_download:self.on_app_update_downloaded(data,error)
        else:self.on_update_checked(data,error,silent)
    def on_update_checked(self,release,error,silent):
        if error:
            self.app_update_note.setText('Не удалось проверить обновления: '+error)
            return
        if not release:
            self.app_update_note.setText('Установлена последняя версия ('+VERSION+').')
            return
        self.available_release=release
        self.app_update_note.setText('Доступна версия '+release['version'])
        answer=QMessageBox.question(self,'Обновление Santa Maria Helper',
            'Доступна версия '+release['version']+'.\nСкачать и установить обновление?\nВаши профили и настройки сохранятся.',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer==QMessageBox.Yes:
            self.download_app_update(release)
    def download_app_update(self,release):
        self.app_update_button.setEnabled(False)
        self.app_update_note.setText('Загрузка обновления и проверка SHA-256…')
        self.update_worker=UpdateWorker(self.cfg.get('github_repo',''),release,parent=self)
        self.update_worker.update_result.connect(lambda data,err:self._queue_update_result(data,err,False,True))
        self.update_worker.finished.connect(lambda:self.app_update_button.setEnabled(True))
        self.update_worker.start()
    def on_app_update_downloaded(self,path,error):
        if error:
            self.app_update_note.setText('Ошибка обновления: '+error)
            return
        try:
            schedule_replace(sys.executable,path)
        except Exception as e:
            self.app_update_note.setText('Не удалось установить обновление: '+str(e))
            return
        self.app_update_note.setText('Обновление загружено. Перезапуск приложения…')
        self.quit_app()
    def change_auto_update(self,on):self.cfg['auto_update']=on;self.save_config();self.maybe_sync()
    def change_autostart(self,on):
        if sys.platform!='win32':self.autostart.setChecked(False);return
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run',0,winreg.KEY_SET_VALUE) as key:
                if on:
                    exe=Path(sys.executable).resolve()
                    cmd=f'"{exe}"' if getattr(sys,'frozen',False) else f'"{exe}" "{Path(__file__).resolve()}"'
                    winreg.SetValueEx(key,'SantaMariaHelper',0,winreg.REG_SZ,cmd)
                else:
                    try:winreg.DeleteValue(key,'SantaMariaHelper')
                    except FileNotFoundError:pass
            self.cfg['start_with_windows']=on;self.save_config()
        except OSError as e:QMessageBox.warning(self,'Автозапуск',str(e));self.autostart.blockSignals(True);self.autostart.setChecked(not on);self.autostart.blockSignals(False)
    def make_tray(self):
        self.tray=QSystemTrayIcon(QIcon(str(ROOT/'helper.ico')),self)
        menu=QMenu();open_action=menu.addAction('Открыть Santa Maria Helper');open_action.triggered.connect(self.show_normal)
        toggle=menu.addAction('Включить / выключить биндер');toggle.triggered.connect(lambda:self.update_engine_state(not self.cfg['running']))
        quit_action=menu.addAction('Выход');quit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu);self.tray.activated.connect(lambda reason:self.show_normal() if reason==QSystemTrayIcon.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():self.tray.show()
    def show_normal(self):self.show();self.showNormal();self.raise_();self.activateWindow()
    def closeEvent(self,event):
        if QSystemTrayIcon.isSystemTrayAvailable():event.ignore();self.hide()
        else:self.quit_app()
    def quit_app(self):
        self.save_profile();self.engine.stop();self.tray.hide();QApplication.instance().quit()

def main():
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(ROOT/'helper.ico')))
    store=AccountStore(ACCOUNTS_FILE)
    dialog=LoginDialog(store)
    if dialog.exec()!=QDialog.Accepted:return
    window=Window(dialog.account,store)
    app._active_window=window
    window.show();sys.exit(app.exec())
if __name__=='__main__':main()
