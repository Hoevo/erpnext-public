"""Dependency-free validation for this extension, not for the entire ERPNext tree."""
import ast
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
count = 0
for path in root.rglob('*'):
    if not path.is_file() or '__pycache__' in path.parts or path.suffix not in ('.py', '.json', '.js', '.toml', '.md'):
        continue
    source = path.read_text(encoding='utf-8')
    assert '\ufffd' not in source, path
    if path.suffix == '.py':
        ast.parse(source, filename=str(path))
    if path.suffix == '.json':
        data = json.loads(source)
        if isinstance(data, dict) and data.get('doctype') == 'DocType':
            fields = [field['fieldname'] for field in data['fields']]
            assert len(fields) == len(set(fields)), path
            assert set(fields) == set(data['field_order']), path
    count += 1
print(f'PASS: {count} UTF-8 extension files; Python syntax and DocType metadata valid.')
