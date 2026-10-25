"""Text changes belong to a conversation run; recovery checks the exact version."""
from pathlib import Path
from hashlib import sha256
import difflib
import itertools
import json
import re
import time
import uuid


def record_change(tools, path, old, raw, backup):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', tools.run_id): raise ValueError('Invalid run identifier')
    folder = tools.directory / 'changes' / tools.run_id
    folder.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    (folder / (identifier + '.after')).write_bytes(raw)
    record = {'id': tools.run_id + '/' + identifier, 'run_id': tools.run_id, 'path': path,
              'workspace': str(tools.root), 'created': time.time(), 'backup': str(backup) if backup else None,
              'before_sha256': sha256(old).hexdigest() if old is not None else None,
              'after_sha256': sha256(raw).hexdigest(), 'kind': 'modified' if old is not None else 'created'}
    target = folder / (identifier + '.json')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)
    return record['id']


class FileChanges:
    def __init__(self, tools, owner_runs):
        self.tools, self.owner_runs = tools, owner_runs
        self.directory = (tools.directory / 'changes').resolve()

    def _load(self, change_id):
        if not isinstance(change_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}/[0-9a-f]{32}', change_id):
            raise ValueError('修改记录无效')
        run, _ = change_id.split('/')
        if run not in self.owner_runs: raise ValueError('修改记录不属于本对话')
        file = (self.directory / (change_id + '.json')).resolve()
        if not file.is_relative_to(self.directory): raise ValueError('修改记录路径无效')
        record = json.loads(file.read_text(encoding='utf-8'))
        if record.get('id') != change_id or Path(record['workspace']).resolve() != self.tools.root:
            raise ValueError('修改记录的工作文件夹不匹配')
        self.tools.path(record['path'])
        return record, file

    def list(self):
        records = []
        for run in self.owner_runs:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run): continue
            for path in (self.directory / run).glob('*.json'):
                try:
                    record, _ = self._load(run + '/' + path.stem)
                    records.append({k: record[k] for k in ('id','path','created','kind')})
                except (ValueError, KeyError, OSError): continue
        return sorted(records, key=lambda r: r['created'], reverse=True)[:100]

    def _bytes(self, record, file):
        after = file.with_suffix('.after').resolve()
        if not after.is_relative_to(self.directory) or after.stat().st_size > 2_000_000: raise ValueError('版本数据无效')
        new = after.read_bytes()
        if sha256(new).hexdigest() != record['after_sha256']: raise ValueError('保存的版本校验失败')
        old = b''
        if record['backup']:
            backup = Path(record['backup']).resolve()
            if not backup.is_relative_to((self.tools.directory/'backups').resolve()) or backup.stat().st_size > 2_000_000:
                raise ValueError('备份路径无效')
            old = backup.read_bytes()
            if sha256(old).hexdigest() != record['before_sha256']: raise ValueError('旧版本备份校验失败')
        return old, new

    def preview(self, change_id):
        record, file = self._load(change_id)
        old, new = self._bytes(record, file)
        current = self.tools.path(record['path'])
        current_sha = sha256(current.read_bytes()).hexdigest() if current.is_file() and current.stat().st_size <= 2_000_000 else None
        before, after = old.decode('utf-8'), new.decode('utf-8')
        diff = ''.join(itertools.islice(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                     fromfile='修改前/'+record['path'], tofile='修改后/'+record['path']), 5001))
        truncated = len(diff)>60000 or len(diff.splitlines())>5000
        can_restore = bool(record['backup']) and current_sha==record['after_sha256'] and not truncated
        reason = ('新建文件没有旧版本；此处不会删除文件。' if not record['backup'] else
                  '文件已再次修改或移动，不能覆盖后续改动。' if current_sha!=record['after_sha256'] else
                  '差异过大，请在编辑器中检查完整内容。' if truncated else '恢复前会保存当前内容，随后可从新记录恢复回来。')
        return {**{k:record[k] for k in ('id','path','created','kind')}, 'diff':diff[:60000] or '内容未变化',
                'current_sha256':current_sha, 'can_restore':can_restore, 'notice':reason, 'truncated':truncated}

    def restore_data(self, change_id, expected_sha256):
        preview = self.preview(change_id)
        if not preview['can_restore'] or expected_sha256!=preview['current_sha256']:
            raise ValueError('当前文件与预览不一致，或此版本不能恢复。请重新查看修改记录。')
        record, file = self._load(change_id)
        old, _ = self._bytes(record, file)
        return record, old
