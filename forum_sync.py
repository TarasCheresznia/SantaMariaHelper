"""Download public Santa Maria RP forum rules, including ordinary and pinned threads.
The updater preserves previously downloaded topics when a later request fails.
"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote
import requests
from bs4 import BeautifulSoup

BASE = 'https://forum.santamaria-rp.com'
SECTIONS = {
    'Общие правила': '/forums/Общие-правила-сервера.93/',
    'Государственные организации': '/forums/Правила-государственных-организаций.43/',
    'Криминальные организации': '/forums/Правила-криминальных-организаций.42/',
    'Семейные организации': '/forums/Правила-семейных-организаций.86/',
}
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36'})
RULE_RE = re.compile(r'^\s*(?:пункт\s*)?(\d+(?:\.\d+){0,4})\s*[.)\-–:]\s*(.*)$', re.I)
ID_RE = re.compile(r'\.(\d+)(?:/|$)')

def forum_id(url):
    m = ID_RE.search(urlparse(url).path)
    return m.group(1) if m else None

def canonical(url):
    p = urlparse(urljoin(BASE, url))
    if p.netloc.lower() != urlparse(BASE).netloc or p.scheme != 'https':
        return None
    # Keep only path (remove query strings and fragments), but preserve unicode slugs
    return BASE + p.path

def download(url):
    response = SESSION.get(url, timeout=(12, 25))
    response.raise_for_status()
    if len(response.content) < 250:
        raise ValueError('Пустая страница')
    return BeautifulSoup(response.content, 'html.parser')

def collect_threads(section, path, report):
    """Collect all listed rule topics, nested forum sections, and their pagination.

    Scope is the rules subtree, not all forum sections. XenForo paging URLs retain
    the same forum ID with a /page-2 suffix; depth is retained across pagination.
    """
    from collections import deque
    root = canonical(path)
    root_id = forum_id(root)
    queue = deque([(root, 0)])
    visited = set()
    threads = {}
    children = {}
    max_pages = 350
    while queue and len(visited) < max_pages:
        page, depth = queue.popleft()
        if not page or page in visited:
            continue
        visited.add(page)
        doc = download(page)
        for row in doc.select('.structItem--thread'):
            title_node = row.select_one('.structItem-title')
            if not title_node:
                continue
            for a in title_node.select('a[href]'):
                href = canonical(a.get('href'))
                if href and '/threads/' in href and forum_id(href):
                    threads[href] = a.get_text(' ', strip=True) or href
                    break
        # Pagination on current forum, including XenForo's /page-2 suffix.
        for a in doc.select('a[href]'):
            link = canonical(a.get('href'))
            if not link or '/forums/' not in link:
                continue
            if forum_id(link) == forum_id(page) and re.search(r'/page-\d+/?$', link):
                if link not in visited and not any(link == x for x, _ in queue):
                    queue.append((link, depth))
        if depth < 5:
            # Actual XenForo subforum nodes; this does not follow generic sidebar links.
            for a in doc.select('.node--forum .node-title a[href], .node--category .node-title a[href], .node-title a[href]'):
                link = canonical(a.get('href'))
                if not link or '/forums/' not in link or not forum_id(link):
                    continue
                if forum_id(link) == forum_id(page):
                    continue
                children[link] = a.get_text(' ', strip=True)
                if link not in visited and not any(link == x for x, _ in queue):
                    queue.append((link, depth + 1))
        report(f'{section}: {len(threads)} тем · {len(visited)} страниц · {len(children)} подразделов')
        time.sleep(.15)
    if queue:
        raise RuntimeError('Достигнут лимит страниц; результаты могут быть неполными')
    return threads

def extract_rules(doc, section, title, url):
    # Only the first post: replies to rule threads are not themselves official rules.
    wrapper = doc.select_one('article.message--post .message-body .bbWrapper, article.message--post .bbWrapper')
    if wrapper is None:
        raise ValueError('Не найден текст первого сообщения')
    body = BeautifulSoup(str(wrapper), 'html.parser')
    for node in body.select('script, style, blockquote, .bbCodeBlock'):
        node.decompose()
    for br in body.find_all('br'):
        br.replace_with('\n')
    for element in body.find_all(['p','div','li','h1','h2','h3','h4','tr','hr']):
        element.insert_before('\n')
        element.insert_after('\n')
    lines = [re.sub(r'\s+', ' ', line).strip() for line in body.get_text(' ', strip=False).split('\n')]
    lines = [line for line in lines if line]
    if not lines:
        raise ValueError('Текст темы пуст')
    entries = []
    prefix = []
    current = None
    for line in lines:
        match = RULE_RE.match(line)
        if match:
            if current:
                entries.append(current)
            current = {'id': match.group(1), 'text': line, 'section': section, 'topic': title, 'url': url}
        elif current:
            current['text'] += '\n' + line
        else:
            prefix.append(line)
    if current:
        entries.append(current)
    # Make the entire unnumbered thread searchable, rather than dropping it.
    if not entries:
        entries = [{'id': '—', 'text': '\n'.join(lines), 'section': section, 'topic': title, 'url': url}]
    elif prefix and len(' '.join(prefix)) > 30:
        entries.insert(0, {'id': '—', 'text': '\n'.join(prefix), 'section': section, 'topic': title, 'url': url})
    return entries

def update(report=lambda message: None, destination=None):
    cache = Path(destination) if destination else Path(__file__).resolve().parent / 'forum_rules.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    topic_cache_path = cache.with_name('forum_topics.json')
    meta_path = cache.with_name('forum_sync_report.json')
    try:
        old_topics = json.loads(topic_cache_path.read_text(encoding='utf-8'))
        if not isinstance(old_topics, dict): old_topics = {}
    except (OSError, ValueError):
        old_topics = {}
    topic_cache = dict(old_topics)
    errors = []
    visited_topics = set()
    sections_ok = []
    section_audit = {}
    for section, path in SECTIONS.items():
        try:
            threads = collect_threads(section, path, report)
            if not threads:
                raise ValueError('Темы не найдены')
            sections_ok.append(section)
            section_audit[section] = {'discovered':len(threads),'downloaded':0,'failed':0,'topics':[]}
        except Exception as exc:
            section_audit[section] = {'discovered':0,'downloaded':0,'failed':1,'topics':[], 'error':str(exc)}
            errors.append(f'{section}: {exc}')
            continue
        for index, (url, title) in enumerate(threads.items(), 1):
            if url in visited_topics:
                section_audit[section].setdefault('already_seen', 0)
                section_audit[section]['already_seen'] += 1
                continue
            visited_topics.add(url)
            try:
                entries = extract_rules(download(url), section, title, url)
                topic_cache[url] = entries
                section_audit[section]['downloaded'] += 1
                section_audit[section]['topics'].append({'title':title,'url':url,'records':len(entries),'status':'ok'})
            except Exception as exc:
                section_audit[section]['failed'] += 1
                section_audit[section]['topics'].append({'title':title,'url':url,'status':'failed','error':str(exc)})
                errors.append(f'{title}: {exc}')
            report(f'{section}: {index}/{len(threads)} · готово {len(topic_cache)} тем')
            time.sleep(.2)
    # Don't erase cached topics because a page failed or vanished temporarily.
    if topic_cache:
        entries = [entry for items in topic_cache.values() for entry in items if isinstance(entry, dict)]
        def atomic_json(path, obj):
            tmp = path.with_suffix(path.suffix + '.tmp')
            tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(path)
        atomic_json(topic_cache_path, topic_cache)
        atomic_json(cache, entries)
    else:
        entries = []
        errors.append('Ни одну тему не удалось загрузить')
    atomic_json_report = {
        'updated_utc': datetime.now(timezone.utc).isoformat(),
        'sections_ok': sections_ok, 'topics_in_cache': len(topic_cache),
        'topics_seen_now': len(visited_topics), 'rules_in_cache': len(entries),
        'forum_root': BASE, 'sources': {k: BASE+v for k,v in SECTIONS.items()},
        'complete': not errors and len(sections_ok) == len(SECTIONS), 'errors': errors,
        'section_audit': section_audit,
        'note': 'complete означает успешную обработку обнаруженных публичных тем, а не гарантию доступа ко всем правилам форума',
    }
    temp = meta_path.with_suffix('.tmp')
    temp.write_text(json.dumps(atomic_json_report, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(meta_path)
    return len(entries), errors
