#!/bin/sh
# Installation locale et facultative ; lancer ensuite .venv-cw/bin/python ic7300_scope.py
set -eu
cd "$(dirname "$0")"
python3 -m venv --system-site-packages .venv-cw
.venv-cw/bin/python -m pip install 'onnxruntime>=1.17,<2'
.venv-cw/bin/python - <<'PY'
import hashlib
from pathlib import Path
from urllib.request import urlopen
revision = '79104fbe1ea0abd09eb27c6ebf9c6bab364d0d7f'
root = Path('models/deepcw')
root.mkdir(parents=True, exist_ok=True)
for name, checksum in {'model.onnx': 'cd5ee38106348ad86a8f2d40ef1ce7dccea0d7f7fbfd0f2dee0fd1a8a28773ae', 'model.onnx.json': '4e7729dcc64c2a208deb28beac4ded55632d11cb70337a9c0ddce523e73bdd9c'}.items():
    data = urlopen(f'https://raw.githubusercontent.com/e04/deepcw-engine/{revision}/{name}', timeout=90).read()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise RuntimeError('Empreinte incorrecte : ' + name)
    temp = root / (name + '.tmp')
    temp.write_bytes(data)
    temp.replace(root / name)
print('DeepCW installé. Lancer : .venv-cw/bin/python ic7300_scope.py')
PY
