"""Keyboard automation engine. No logging, no networking; listener only runs when enabled."""
import json, os, re, sys, threading, time, ctypes
from pathlib import Path
from pynput import keyboard

DATA_DIR = Path(os.environ.get('LOCALAPPDATA') or (Path.home()/'.local'/'share'))/'SantaMariaBinder'
DATA_FILE = DATA_DIR/'bindings.json'
KINDS = ('bind','macro','trigger')


def normal_hotkey(value):
    chunks = [p.strip().lower() for p in value.split('+') if p.strip()]
    aliases = {'control':'ctrl','ctl':'ctrl','option':'alt','windows':'win','meta':'win','command':'win', 'escape':'esc', 'return':'enter', 'del':'delete', 'pgup':'page_up', 'pgdown':'page_down','pageup':'page_up','pagedown':'page_down','meta':'win','super':'win','spacebar':'space','esc':'esc','backtab':'tab'}
    chunks = [aliases.get(p,p) for p in chunks]
    order = {'ctrl':0,'alt':1,'shift':2,'win':3}
    chunks = sorted(set(chunks),key=lambda x:(order.get(x,10),x))
    if not chunks or any(not re.fullmatch(r'[a-z0-9_]+',x) for x in chunks):
        return ''
    return '+'.join(chunks)


def validate(item):
    item = dict(item)
    if item.get('kind') not in KINDS: raise ValueError('Неизвестный тип')
    item['name'] = str(item.get('name','')).strip()[:90]
    item['shortcut'] = str(item.get('shortcut','')).strip()[:100]
    item['payload'] = str(item.get('payload',''))[:10000]
    item['enabled'] = bool(item.get('enabled',True))
    if not item['name'] or not item['shortcut'] or not item['payload'].strip():
        raise ValueError('Заполни название, сочетание/триггер и действие')
    if item['kind'] == 'trigger':
        if len(item['shortcut'])<2 or len(item['shortcut'])>40 or '\n' in item['shortcut']:
            raise ValueError('Триггер: от 2 до 40 символов без переноса строки')
    else:
        item['shortcut'] = normal_hotkey(item['shortcut'])
        if not item['shortcut'] or not any(part not in ('ctrl','alt','shift','win') for part in item['shortcut'].split('+')):
            raise ValueError('Укажи клавишу, например ctrl+alt+1 или f6')
    if item['kind'] == 'macro':
        parse_macro(item['payload'])
    return item


def parse_macro(source):
    steps=[]
    for line in source.splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        action, separator, argument=line.partition(' ')
        action=action.upper(); argument=argument.strip()
        if action == 'TEXT' and argument: steps.append(('TEXT',argument))
        elif action == 'WAIT' and argument.isdigit() and 0<=int(argument)<=10000: steps.append(('WAIT',int(argument)))
        elif action == 'KEY' and re.fullmatch(r'[a-zA-Z0-9_+]+',argument or ''): steps.append(('KEY',normal_hotkey(argument)))
        else: raise ValueError('Строка макроса: TEXT сообщение / WAIT 300 / KEY enter')
    if not steps or len(steps)>100: raise ValueError('Макрос должен содержать 1–100 действий')
    return steps


def load():
    try:
        data=json.loads(DATA_FILE.read_text(encoding='utf-8'))
        if not isinstance(data,list): return []
        valid=[]
        for record in data:
            try: valid.append(validate(record))
            except (ValueError,TypeError): pass
        return valid
    except (OSError,ValueError,TypeError): return []


def save(items):
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    text=json.dumps(items,ensure_ascii=False,indent=2)
    tmp=DATA_FILE.with_suffix('.tmp')
    tmp.write_text(text,encoding='utf-8')
    os.replace(tmp,DATA_FILE)


class Engine:
    def __init__(self, callback):
        self.callback=callback
        self.listener=None
        self.items=[]
        self.pressed=set()
        self.history=''
        self.lock=threading.RLock()
        self.busy=False
        self.active=False
        self.last_run=0
        self.last_focus=None
        self.controller=keyboard.Controller()

    def set_items(self,items):
        with self.lock: self.items=[dict(x) for x in items]

    def start(self):
        if self.listener and self.listener.is_alive(): return
        self.listener=None
        self.active=True
        try:
            self.listener=keyboard.Listener(on_press=self._press,on_release=self._release)
            self.listener.start()
        except Exception:
            self.active=False
            self.listener=None
            raise

    def stop(self):
        self.active=False
        listener=self.listener
        self.listener=None
        if listener: listener.stop()
        with self.lock:
            self.history='';self.pressed.clear()

    def _keyname(self,key):
        try:
            if isinstance(key,keyboard.KeyCode):
                # Control combinations can be delivered as control chars rather than letters.
                c=(key.char or '').lower()
                if key.vk and 65 <= key.vk <= 90 and (not c or ord(c) < 32):
                    return chr(key.vk).lower()
                if c: return c
                if key.vk and 65 <= key.vk <= 90: return chr(key.vk).lower()
                return f'vk{key.vk}' if key.vk else ''
            s=key.name.lower()
            if s in ('ctrl_l','ctrl_r'): return 'ctrl'
            if s in ('alt_l','alt_r','alt_gr'): return 'alt'
            if s in ('shift_l','shift_r'): return 'shift'
            if s in ('cmd','cmd_l','cmd_r'): return 'win'
            return {'return':'enter','escape':'esc','space':'space','delete':'delete'}.get(s,s)
        except Exception:return ''

    def _press(self,key):
        k=self._keyname(key)
        with self.lock:
            if not self.active or self.busy or not k:return
            fresh=k not in self.pressed
            self.pressed.add(k)
            if not fresh:return
            now=time.monotonic()
            # Never discard text characters during macro cooldown.
            current=normal_hotkey('+'.join(self.pressed))
            for item in self.items:
                if now-self.last_run >= .35 and item['enabled'] and item['kind']!='trigger' and item['shortcut']==current:
                    self._dispatch(item,0);return
            # Reset the partial trigger when the user switches windows.
            if sys.platform == 'win32':
                focus=ctypes.windll.user32.GetForegroundWindow()
                if self.last_focus is not None and focus != self.last_focus:
                    self.history=''
                self.last_focus=focus
            # Text triggers are matched only against recent keystrokes, never saved to disk.
            if len(k)==1 and not any(mod in self.pressed for mod in ('ctrl','alt','win')):
                self.history=(self.history+k)[-100:]
            elif k=='space':self.history=(self.history+' ')[-100:]
            elif k=='backspace':self.history=self.history[:-1]
            elif k not in ('shift',):self.history='' # navigation/shortcuts end the fragment
            for item in sorted(self.items,key=lambda x:len(x['shortcut']),reverse=True):
                if item['enabled'] and item['kind']=='trigger' and self.history.endswith(item['shortcut'].lower()):
                    self._dispatch(item,len(item['shortcut']))
                    self.history=''
                    return

    def _release(self,key):
        with self.lock:self.pressed.discard(self._keyname(key))

    def _dispatch(self,item,erase):
        self.busy=True;self.last_run=time.monotonic()
        threading.Thread(target=self._execute,args=(dict(item),erase),daemon=True).start()

    def _type_text(self, text):
        """Paste every TEXT action with the Windows Unicode clipboard and Ctrl+V.

        This intentionally updates the user's clipboard (no per-character typing).
        """
        if sys.platform != 'win32':
            raise OSError('Вставка через буфер обмена поддерживается в версии для Windows')
        if not text:
            return
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL

        raw = text.encode('utf-16-le') + b'\x00\x00'
        hmem = kernel32.GlobalAlloc(0x0042, len(raw))  # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not hmem:
            raise OSError('Не удалось выделить память для буфера обмена')
        transferred = False
        try:
            ptr = kernel32.GlobalLock(hmem)
            if not ptr:
                raise OSError('Не удалось открыть память буфера обмена')
            try:
                ctypes.memmove(ptr, raw, len(raw))
            finally:
                kernel32.GlobalUnlock(hmem)
            opened = False
            for _ in range(15):
                if user32.OpenClipboard(None):
                    opened = True
                    break
                time.sleep(0.03)
            if not opened:
                raise OSError('Буфер обмена занят другой программой')
            try:
                if not user32.EmptyClipboard():
                    raise OSError('Не удалось очистить буфер обмена')
                if not user32.SetClipboardData(13, hmem):  # CF_UNICODETEXT
                    raise OSError('Не удалось записать текст в буфер обмена')
                transferred = True  # Windows now owns hmem
            finally:
                user32.CloseClipboard()
        finally:
            if not transferred:
                kernel32.GlobalFree(hmem)
        # Clipboard content is left in place intentionally; restoration can race
        # with the target application's asynchronous paste handling.
        self.controller.press(keyboard.Key.ctrl)
        try:
            self.controller.press('v')
            self.controller.release('v')
        finally:
            self.controller.release(keyboard.Key.ctrl)
        time.sleep(0.08)

    def _execute(self,item,erase):
        try:
            # Allow the invoking shortcut to be released before injecting text.
            for _ in range(40):
                with self.lock:
                    released = not self.pressed
                if released or not self.active: break
                time.sleep(.05)
            if not self.active:return
            for _ in range(erase):
                if not self.active:return
                self.controller.press(keyboard.Key.backspace)
                self.controller.release(keyboard.Key.backspace)
                time.sleep(.018)
            steps=parse_macro(item['payload']) if item['kind']=='macro' else [('TEXT',item['payload'])]
            for action,value in steps:
                if not self.active:break
                if action=='TEXT':self._type_text(value)
                elif action=='WAIT':time.sleep(value/1000)
                elif action=='KEY':
                    names=value.split('+')
                    keys=[]
                    for name in names:
                        key={'ctrl':keyboard.Key.ctrl,'alt':keyboard.Key.alt,'shift':keyboard.Key.shift,'win':keyboard.Key.cmd,
                             'enter':keyboard.Key.enter,'tab':keyboard.Key.tab,'space':keyboard.Key.space,'esc':keyboard.Key.esc,
                             'backspace':keyboard.Key.backspace,'delete':keyboard.Key.delete,
                             'up':keyboard.Key.up,'down':keyboard.Key.down,'left':keyboard.Key.left,'right':keyboard.Key.right}.get(name)
                        if key is None:key=getattr(keyboard.Key,name,None)
                        keys.append(key or name)
                    for key in keys:self.controller.press(key)
                    for key in reversed(keys):self.controller.release(key)
            self.callback(f'Выполнено: {item["name"]}')
        except Exception as exc:self.callback(f'Ошибка: {exc}')
        finally:
            time.sleep(.2)
            with self.lock:self.busy=False;self.history='';self.pressed.clear()
