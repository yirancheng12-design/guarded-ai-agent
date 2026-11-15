"""Recognize already returned text by file version and actual character coverage."""
from bisect import bisect_right
from hashlib import sha256
import re


def normalize_read_arguments(name, arguments):
    """Repair unambiguous JSON integer strings only for read-only pagination."""
    if name != 'read_file' or not isinstance(arguments, dict):
        return arguments
    result = dict(arguments)
    for key in ('start_line', 'line_count', 'char_offset', 'max_chars'):
        value = result.get(key)
        if isinstance(value, str) and re.fullmatch(r'[0-9]{1,9}', value):
            result[key] = int(value)
    return result


class ReadProgress:
    def __init__(self, toolbox):
        self.toolbox = toolbox
        self.pages = {}

    def window(self, args):
        # Use the toolbox resolver so aliases, access scope and symlinks agree
        # with the real read. Invalid requests must still reach tool validation.
        if not callable(getattr(self.toolbox, 'path', None)):
            return None
        if set(args) - {'path', 'start_line', 'line_count', 'char_offset', 'max_chars'}:
            return None
        start, count = args.get('start_line', 1), args.get('line_count', 120)
        offset, size = args.get('char_offset', 0), args.get('max_chars', 4000)
        if (any(type(v) is not int for v in (start, count, offset, size)) or
                start < 1 or not 1 <= count <= 300 or offset < 0 or not 256 <= size <= 16000):
            return None
        try:
            file = self.toolbox.path(args['path'])
            if file.stat().st_size > 2_000_000: return None
            raw = file.read_bytes()
            text = raw.decode('utf-8-sig')
            if '\x00' in text: return None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        lines = text.splitlines()
        positions, position = [], 0
        for line in lines:
            positions.append(position)
            position += len(line) + 1
        total = max(0, position - 1)
        excerpt = '\n'.join(lines[start-1:start-1+count])
        if offset > len(excerpt): return None
        begin = (positions[start-1] if start <= len(lines) else total) + offset
        end = begin + min(size, len(excerpt)-offset)
        # Paging across line boundaries omits the separating newline; it does
        # not represent unread source worth another tool call.
        if offset + size >= len(excerpt) and start-1+count < len(lines): end += 1
        return (str(file).replace('\\', '/').casefold(), sha256(raw).hexdigest(),
                begin, end, positions, total, excerpt[offset:offset+size])

    def observe(self, name, args, result):
        records = result.get('files', []) if name == 'read_files' else [result] if name == 'read_file' else []
        for record in records:
            if not isinstance(record, dict) or not record.get('sha256') or 'text' not in record: continue
            request = {'path': record.get('path', args.get('path'))} if name == 'read_files' else args
            window = self.window(request)
            if window is None or window[1] != record['sha256']: continue
            # Old checkpoints shortened text while retaining the whole-file hash.
            # A matching version alone does not prove this range was delivered.
            if record['text'] != window[6]: continue
            versions = getattr(self.toolbox, '_edit_versions', None)
            if versions is not None:
                token = versions.get(record.get('edit_version'))
                if not token or str(token[0]).replace('\\', '/').casefold() != window[0] or token[1] != window[1]:
                    # Short edit tokens are scoped to a toolbox instance. A
                    # resumed run must issue a real read to obtain a fresh one.
                    continue
            path, version, begin, end = window[:4]
            self.pages.setdefault((path, version), []).append((begin, end, record))

    def repeated(self, name, args):
        if name == 'read_files':
            paths = args.get('paths')
            if set(args) != {'paths'} or not isinstance(paths, list) or not 1 <= len(paths) <= 8: return None
            records = [self.repeated('read_file', {'path': path}) for path in paths]
            if any(record is None for record in records): return None
            return {'outcome': 'success', 'files': records}
        if name != 'read_file': return None
        window = self.window(args)
        if window is None: return None
        path, version, begin, end, positions, total, text = window
        pages = sorted(self.pages.get((path, version), []), key=lambda p: (p[0], p[1]))
        covered, previous = begin, None
        for left, right, record in pages:
            if left <= covered and right >= covered:
                covered, previous = right, record
        if previous is None or covered < end: return None
        next_read = None
        if covered < total:
            line = bisect_right(positions, covered) - 1
            next_read = {'path': args['path'], 'start_line': line+1,
                         'line_count': args.get('line_count', 120),
                         'char_offset': covered-positions[line], 'max_chars': args.get('max_chars', 4000)}
        # The last page proves coverage; it is NOT the requested page. Returning
        # its text (often only an EOF tail) makes the model ask for the file again.
        return {**previous, 'path': args['path'], 'text': text,
                'start_line': args.get('start_line', 1),
                'char_offset': args.get('char_offset', 0), 'returned_chars': len(text),
                'next_read': next_read, 'truncated': next_read is not None}
