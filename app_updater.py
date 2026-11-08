"""GitHub Releases updater. All network operations happen outside the Qt UI thread."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import requests

VERSION='36.0.8'
GITHUB_REPO=re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


def version_tuple(version):
    m=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(version).strip())
    return tuple(map(int,m.groups())) if m else None


def check_release(repo, current=VERSION):
    if not GITHUB_REPO.fullmatch(repo or ''):raise ValueError('Введите репозиторий в формате владелец/репозиторий')
    response=requests.get(f'https://api.github.com/repos/{repo}/releases/latest',headers={'Accept':'application/vnd.github+json','User-Agent':'SantaMariaHelper-Updater'},timeout=(5,12))
    if response.status_code==404:raise ValueError('Релиз GitHub не найден. Создайте первый Release с файлом SantaMariaHelper.exe.')
    response.raise_for_status()
    data=response.json()
    remote=version_tuple(data.get('tag_name',''))
    if not remote:raise ValueError('У релиза неверный тег версии (ожидается v36.0.2 или новее)')
    if remote <= version_tuple(current):return None
    if data.get('draft') or data.get('prerelease'):return None
    asset=next((a for a in data.get('assets',[]) if a.get('name')=='SantaMariaHelper.exe'),None)
    digest=next((a for a in data.get('assets',[]) if a.get('name')=='SantaMariaHelper.exe.sha256'),None)
    if not asset or not digest:raise ValueError('В релизе отсутствует SantaMariaHelper.exe или файл SHA-256')
    if not all(str(x.get('browser_download_url','')).startswith('https://github.com/') for x in (asset,digest)):
        raise ValueError('Некорректная ссылка на файл релиза')
    return {'version':data['tag_name'],'exe_url':asset['browser_download_url'],'sha_url':digest['browser_download_url'],'size':asset.get('size',0)}


def download_release(release, root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    sha=requests.get(release['sha_url'],timeout=(5,20));sha.raise_for_status()
    value=sha.text.strip().split()[0].lower()
    if not re.fullmatch(r'[a-f0-9]{64}',value):raise ValueError('Неверная контрольная сумма обновления')
    target=root/'SantaMariaHelper.next.exe'
    h=hashlib.sha256();size=0
    try:
        with requests.get(release['exe_url'],stream=True,timeout=(8,90)) as response:
            response.raise_for_status()
            with target.open('wb') as f:
                for chunk in response.iter_content(128*1024):
                    if chunk:
                        size+=len(chunk)
                        if size > 250*1024*1024:raise ValueError('Слишком большой размер обновления')
                        h.update(chunk);f.write(chunk)
        if h.hexdigest()!=value:raise ValueError('Контрольная сумма обновления не совпала')
        if size < 100*1024:raise ValueError('Получен подозрительно маленький EXE')
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def schedule_replace(current_exe, downloaded):
    if sys.platform!='win32' or not getattr(sys,'frozen',False):
        raise ValueError('Установка обновления доступна только из собранного Windows EXE')
    current=Path(current_exe).resolve(); downloaded=Path(downloaded).resolve()
    if not current.is_file() or not downloaded.is_file():raise ValueError('Не найдены файлы для обновления')
    if current.suffix.lower()!='.exe':raise ValueError('Неизвестный путь исполняемого файла')
    # PowerShell script invoked separately to wait for process exit and replace the running EXE.
    script=downloaded.parent/'replace_santa_maria.ps1'
    script.write_text(r"""param([string]$Old,[string]$New,[int]$AppPid)
$ErrorActionPreference='Stop'
try {
  try { Wait-Process -Id $AppPid -Timeout 60 -ErrorAction SilentlyContinue } catch {}
  if (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {
    throw 'Приложение не закрылось. Завершите SantaMariaHelper.exe и повторите обновление.'
  }
  $dest=Split-Path -Parent $Old
  $backup=Join-Path $dest 'SantaMariaHelper.previous.exe'
  if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
  Move-Item -LiteralPath $Old -Destination $backup -Force
  try { Copy-Item -LiteralPath $New -Destination $Old -Force }
  catch { Move-Item -LiteralPath $backup -Destination $Old -Force; throw }
  Start-Process -FilePath $Old
  Remove-Item -LiteralPath $New -Force -ErrorAction SilentlyContinue
} catch {
  $msg='Обновление не установлено: ' + $_.Exception.Message
  try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($msg,'Santa Maria Helper') | Out-Null
  } catch { Write-Error $msg }
}
""",encoding='utf-8-sig')
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),'-Old',str(current),'-New',str(downloaded),'-AppPid',str(os.getpid())],creationflags=flags,close_fds=True)
