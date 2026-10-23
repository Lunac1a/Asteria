"""Reject private deployment file paths tracked by Git; not a secret-content scanner."""
from pathlib import PurePosixPath
import subprocess
import sys


def forbidden(path):
    p = PurePosixPath(path)
    name = p.name.lower()
    if name.startswith('.env') and not name.endswith('.example'):
        return True
    return (name.endswith(('.pem', '.key', '.pfx', '.p12', '.dump'))
            or any(part in {'.local', '.venv', 'node_modules', 'uploads'} for part in p.parts)
            or path.startswith('data/'))


if __name__ == '__main__':
    paths = subprocess.check_output(['git', 'ls-files', '--full-name', '-z']).decode().split('\0')
    blocked = [p for p in paths if p and forbidden(p)]
    if blocked:
        print('Private paths tracked by Git (contents not displayed):\n' + '\n'.join(blocked))
        sys.exit(1)
    print('Tracked deployment configuration exclusion passed; content review still required.')
