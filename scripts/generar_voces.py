#!/usr/bin/env python3
"""Copiloto de voz: genera las grabaciones de las frases con voces neuronales naturales (las de «Leer en voz alta» de
Microsoft Edge), en cada idioma y voz de tools-prebuilt/copiloto-voz-voces/frases.json.

En el PC (Windows, con Python e internet), desde la carpeta del repositorio:

    py -m pip install edge-tts
    py scripts\\generar_voces.py                 todas las frases, idiomas y voces (solo las que cambiaron)
    py scripts\\generar_voces.py --idioma es     solo español
    py scripts\\generar_voces.py --todo          todas otra vez, aunque no hayan cambiado

Deja tools-prebuilt/copiloto-voz-voces/<idioma>/<voz>/<aviso>.m4a (p. ej. voces/es/es-ES-ElviraNeural/fin_viaje.m4a; AAC, como
RideTube: el reproductor del coche no acepta el MP3 de edge-tts; hace falta ffmpeg) y en cada
carpeta un .textos.json con el texto de cada archivo, para saber cuáles cambiaron. Después: git add, commit y push (o
GitHub Desktop), y el MGU Store instala la app con las voces nuevas. En el coche, Ajustes → Idioma y Voz.
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VOCES = os.path.join(ROOT, 'tools-prebuilt', 'copiloto-voz-voces')


FFMPEG = shutil.which('ffmpeg')


def to_m4a(mp3):
    """The car's media player refuses edge-tts' MP3 (ITEM_UNPLAYABLE): AAC-LC 44.1 kHz stereo in an M4A, as RideTube
    gives it. Needs ffmpeg; without it the MP3 stays (convert it later with this script on a PC that has ffmpeg)."""
    if not FFMPEG:
        return mp3
    m4a = mp3[:-4] + '.m4a'
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', mp3, '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
                    '-movflags', '+faststart', '-f', 'ipod', m4a], check=True)
    os.remove(mp3)
    return m4a


def load():
    with open(os.path.join(VOCES, 'frases.json'), encoding='utf-8') as f:
        return json.load(f)


async def generate(doc, only_lang=None, all_again=False):
    try:
        import edge_tts
    except ImportError:
        sys.exit('Falta edge-tts: ejecute primero  py -m pip install edge-tts')
    rate = doc.get('velocidad', '+0%')
    made = skipped = 0
    failed = []
    for lang, info in doc['idiomas'].items():
        if only_lang and lang != only_lang:
            continue
        for voice in info['voces']:
            out_dir = os.path.join(VOCES, lang, voice['id'])
            os.makedirs(out_dir, exist_ok=True)
            memo_file = os.path.join(out_dir, '.textos.json')
            try:
                with open(memo_file, encoding='utf-8') as f:
                    memo = json.load(f)
            except (OSError, ValueError):
                memo = {}
            for key, texts in doc['frases'].items():
                text = (texts.get(lang) or '').strip()
                out = os.path.join(out_dir, key + '.mp3')
                done = [f for f in (out[:-4] + '.m4a', out) if os.path.exists(f)]
                if not text:
                    continue
                if not all_again and memo.get(key) == [text, rate] and done:
                    skipped += 1
                    continue
                try:
                    await edge_tts.Communicate(text, voice['id'], rate=rate).save(out)
                    if os.path.exists(out[:-4] + '.m4a'):
                        os.remove(out[:-4] + '.m4a')
                    to_m4a(out)
                    memo[key] = [text, rate]
                    made += 1
                    print('  ' + lang + ' ' + voice['id'] + ' ' + key)
                except Exception as e:  # one failing leaves the others
                    failed.append(lang + '/' + voice['id'] + '/' + key + ': ' + str(e))
            with open(memo_file, 'w', encoding='utf-8') as f:
                json.dump(memo, f, ensure_ascii=False, indent=1)
    print('%d generadas, %d sin cambios, %d con error' % (made, skipped, len(failed)))
    if made:
        subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'voces_componente.py')])
    if made and not FFMPEG:
        print('AVISO: sin ffmpeg quedan en MP3, que el coche no reproduce. Instale ffmpeg (winget install ffmpeg) y ejecute otra vez con --todo.')
    for f in failed:
        print('  ERROR ' + f)
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description='Genera las voces de Copiloto de voz')
    ap.add_argument('--idioma', choices=['es', 'zh', 'en'])
    ap.add_argument('--todo', action='store_true', help='todas otra vez, aunque no hayan cambiado')
    a = ap.parse_args()
    sys.exit(asyncio.run(generate(load(), a.idioma, a.todo)))


if __name__ == '__main__':
    main()
