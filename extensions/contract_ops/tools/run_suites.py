"""Sequential real-database regression runner; output is kept outside tracked sources."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / '.local'
SUITES = ('test_native_contract', 'test_planning', 'test_two_flows', 'test_merged_vat',
          'test_income_register', 'test_renewal', 'test_renewal_permissions', 'test_audit_security')


def main():
    selected = sys.argv[1:] or SUITES
    if any(name not in SUITES for name in selected):
        raise ValueError('Unknown regression suite')
    existing = ARTIFACTS / 'v17-suites.json'
    results = json.loads(existing.read_text(encoding='utf-8')) if existing.exists() else {}
    for name in selected:
        result = subprocess.run(['wsl', '-d', 'ERPNextDev', '-u', 'root', '--exec', 'docker', 'exec',
            'erp-v17-run', 'nice', '-n', '10', 'bench', '--site', 'erp.merged-test.localhost',
            'execute', 'contract_ops.tests.' + name + '.run'], capture_output=True, timeout=600)
        output = result.stdout.decode('utf-8', errors='replace')
        (ARTIFACTS / (name + '.log')).write_text(output + result.stderr.decode('utf-8', errors='replace'), encoding='utf-8')
        if result.returncode:
            raise RuntimeError(name + ' failed; inspect its local log')
        results[name] = json.loads(output.strip().splitlines()[-1])
        (ARTIFACTS / 'v17-suites.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(name + ': PASS', flush=True)


if __name__ == '__main__':
    main()
