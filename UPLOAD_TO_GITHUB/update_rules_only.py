"""Standalone database updater: python update_rules_only.py"""
import json
from pathlib import Path
import forum_sync
from rule_integrity import repair_records

base = Path(__file__).resolve().parent
print('Обновление правил Santa Maria RP. Может потребоваться время...')
count, errors = forum_sync.update(report=print, destination=base / 'forum_rules.json')
from_file=base/'forum_rules.json'
if from_file.exists():
    records=json.loads(from_file.read_text(encoding='utf-8'))
    cleaned=repair_records(records,base/'rules.json')
    from_file.write_text(json.dumps(cleaned,ensure_ascii=False,indent=2),encoding='utf-8')
    import os
    cache_dir=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')/'SantaMariaHelper'
    cache_dir.mkdir(parents=True,exist_ok=True)
    (cache_dir/'forum_rules.json').write_text(json.dumps(cleaned,ensure_ascii=False,indent=2),encoding='utf-8')
report_path = base / 'forum_sync_report.json' 
if report_path.exists():
    report = json.loads(report_path.read_text(encoding='utf-8'))
    print(f"\nТем в кеше: {report['topics_in_cache']}; записей: {count}; ошибок: {len(errors)}")
    for title, data in report['section_audit'].items():
        print(f"{title}: найдено {data['discovered']}, загружено {data['downloaded']}, ошибок {data['failed']}")
    print('Отчёт:', report_path)
if errors:
    print('ВНИМАНИЕ: база не подтверждена как полная. Смотрите отчёт.')
else:
    print('Обнаруженные публичные темы обработаны, но абсолютная полнота не гарантируется.')
input('Нажмите Enter для выхода...')
