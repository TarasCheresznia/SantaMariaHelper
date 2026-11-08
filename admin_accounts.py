"""Local admin accounts and permission levels. Offline-only; not a central auth service."""
import os, json, hashlib, hmac, secrets, re, uuid
from pathlib import Path

LEVELS = [('1 (2)', 2), ('2 (3)', 3), ('3 (4)', 4), ('4 (5)', 5), ('ЗГА', 6), ('ГА', 7), ('Спец админ', 8)]
LEVEL_NAMES = dict((level, name) for name, level in LEVELS)
DEV_MIN_LEVEL = 6
_ITERATIONS = 240_000

def normalized_login(login):
    value = str(login or '').strip().casefold()
    if not re.fullmatch(r'[a-zа-яё0-9_.-]{3,32}', value):
        raise ValueError('Логин: от 3 до 32 символов, буквы, цифры, точка, _ или -')
    return value

def hash_password(password, salt=None):
    if not isinstance(password, str) or len(password)<8:
        raise ValueError('Пароль должен содержать не менее 8 символов')
    salt = salt or secrets.token_hex(16)
    value=hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), _ITERATIONS).hex()
    return {'salt':salt,'hash':value,'iterations':_ITERATIONS}

def check_password(password, record):
    try:
        actual=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'), bytes.fromhex(record['salt']),int(record['iterations'])).hex()
        return hmac.compare_digest(actual,record['hash'])
    except (KeyError,ValueError,TypeError):return False

class AccountStore:
    def __init__(self,path):
        self.path=Path(path)
        self.users=self._read()
    def _read(self):
        try:
            data=json.loads(self.path.read_text('utf-8'))
            if isinstance(data,dict) and isinstance(data.get('users'),list):return data['users']
        except (OSError,ValueError):pass
        return []
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        payload=json.dumps({'schema':1,'users':self.users},ensure_ascii=False,indent=2)
        temp=self.path.with_suffix('.tmp');temp.write_text(payload,'utf-8');os.replace(temp,self.path)
    def create(self, login, password, level, owner=False):
        login=normalized_login(login)
        if any(x['login']==login for x in self.users):raise ValueError('Такой логин уже существует')
        level=int(level)
        if level not in LEVEL_NAMES:raise ValueError('Неверный уровень администратора')
        if len(password)>256:raise ValueError('Слишком длинный пароль')
        record={'id':uuid.uuid4().hex,'login':login,'level':level,'owner':bool(owner),**hash_password(password)}
        self.users.append(record);self.save();return record
    def authenticate(self,login,password):
        try:login=normalized_login(login)
        except ValueError:return None
        for item in self.users:
            if item['login']==login and check_password(password,item):return dict(item)
        return None
    def set_level(self,uid,level):
        if int(level) not in LEVEL_NAMES:raise ValueError('Неверный уровень')
        for item in self.users:
            if item['id']==uid:
                if item.get('owner') and int(level)<DEV_MIN_LEVEL:raise ValueError('Владельцу нельзя закрыть доступ к панели')
                item['level']=int(level);self.save();return
        raise ValueError('Учётная запись не найдена')
    def reset_password(self,uid,password):
        for item in self.users:
            if item['id']==uid:
                item.update(hash_password(password));self.save();return
        raise ValueError('Учётная запись не найдена')
    def delete(self,uid,active_id):
        item=next((x for x in self.users if x['id']==uid),None)
        if item is None:raise ValueError('Учётная запись не найдена')
        if uid==active_id:raise ValueError('Нельзя удалить текущую учётную запись')
        if item.get('owner') and sum(bool(x.get('owner')) for x in self.users)<=1:
            raise ValueError('Нельзя удалить последнего владельца')
        self.users=[x for x in self.users if x['id']!=uid];self.save()

def can_use_command(level, command):
    """Show only commands explicitly assigned to the logged-in admin's RP level."""
    try:
        active_level = int(level)
        assigned = command.get('available_levels')
        if isinstance(assigned, list):
            return active_level in {int(value) for value in assigned}
        # Newly created developer commands without an explicit mapping are
        # available only to the single level selected in the editor.
        return active_level == int(command.get('level', 2))
    except (ValueError, TypeError):
        return False
