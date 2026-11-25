#!/usr/bin/env python3
"""Copiloto de voz: tools-prebuilt/copiloto-voz-voces/component.json, the Store tool "copiloto-voz-voces" that installs the
recordings in /var/opt/copiloto-voz-voces/current/<idioma>/<voz>/<aviso>.m4a (root's, 0755 folders, 0644 files).

BMW's media engine plays a file as its own user and answers ITEM_UNPLAYABLE for one in an app's folder; RideTube's
audio lives under /var/opt too. Run after the voices change (scripts/generar_voces.py does it): the version goes up
when the files do, so the Store installs the new ones.
"""
import hashlib
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VOCES = os.path.join(ROOT, 'tools-prebuilt', 'copiloto-voz-voces')
OUT = os.path.join(VOCES, 'component.json')


def main():
    files = []
    for d, _, names in os.walk(VOCES):
        for n in names:
            if n.lower().endswith('.m4a'):
                files.append(os.path.relpath(os.path.join(d, n), VOCES).replace(os.sep, '/'))
    files.sort()
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode() + b'\0')
        with open(os.path.join(VOCES, f), 'rb') as fh:
            h.update(hashlib.sha256(fh.read()).digest())
    digest = h.hexdigest()[:16]
    try:
        with open(OUT, encoding='utf-8') as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        old = {}
    version = old.get('version', '1.0.0')
    if old.get('voicesHash') and old.get('voicesHash') != digest:
        a, b, c = (int(x) for x in version.split('.'))
        version = '%d.%d.%d' % (a, b, c + 1)
    comp = {
        'id': 'copiloto-voz-voces',
        'kind': 'tool',
        'version': version,
        'title': 'Copiloto de voz: voces',
        'description': 'La voz de Copiloto de voz (español, Elvira) en /var/opt, donde el reproductor de BMW puede abrirla',
        'mguHmi': ['ID7', 'ID8'],
        'requires': {'mgu-deploy': '0.4.0'},
        'voicesHash': digest,
        'files': [{'path': f, 'mode': '0644'} for f in files],
        'units': [],
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(comp, fh, ensure_ascii=False, indent=1)
        fh.write('\n')
    # the app asks the Store for this version of the tool
    app = os.path.join(ROOT, 'apps', 'copiloto_voz', 'app.json')
    with open(app, encoding='utf-8') as fh:
        text = fh.read()
    spec = json.loads(text)
    if (spec.get('requires') or {}).get(comp['id']) != version:
        import re
        text = re.sub(r'("%s":\s*")[^"]*(")' % re.escape(comp['id']), r'\g<1>%s\g<2>' % version, text)
        with open(app, 'w', encoding='utf-8') as fh:
            fh.write(text)
        print('app.json: requires %s %s (suba también la versión de la app)' % (comp['id'], version))
    print('%s %s: %d archivos' % (comp['id'], version, len(files)))


if __name__ == '__main__':
    main()
