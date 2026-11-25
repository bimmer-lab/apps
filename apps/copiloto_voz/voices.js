'use strict';
/**
 * voices.js - Copiloto de voz: where the recordings of the chosen language and voice are. scripts/generar_voces.py
 * leaves them as voces/<idioma>/<voz>/<aviso>.m4a (voces/frases.json says which languages and voices); the settings
 * "idioma" (es, zh, en) and "voz_<idioma>" pick one. Looked for in that voice's folder, then any other voice of the
 * same language (one missing phrase is still said), then voces/ itself (recordings put there by hand). Node 8.
 */
const fs = require('fs');
const path = require('path');

const LANGS = ['es', 'zh', 'en'];
const DEFAULT = { es: 'es-ES-ElviraNeural', zh: 'zh-CN-XiaoxiaoNeural', en: 'en-GB-SoniaNeural' };
const LANG_NAME = { es: 'Español', zh: 'Chino', en: 'Inglés' };

function dirsFor(root, lang, voice) {
    lang = LANGS.indexOf(lang) >= 0 ? lang : 'es';
    voice = voice || DEFAULT[lang];
    const out = [path.join(root, lang, voice)];
    try {
        fs.readdirSync(path.join(root, lang)).sort().forEach((v) => {
            const d = path.join(root, lang, v);
            if (v !== voice && fs.statSync(d).isDirectory()) out.push(d);
        });
    } catch (e) { /* no recordings in that language */ }
    out.push(root);
    return out;
}

// the recordings installed by the Store tool copiloto-voz-voces (root's, under /var/opt: BMW's media engine opens them)
const TOOL_ROOT = '/var/opt/copiloto-voz-voces/current';
/** Where the recordings are: the tool's folder when installed, else the app's own voces/ (bench, older installs). */
function root(own) {
    try { if (fs.statSync(path.join(TOOL_ROOT, 'es')).isDirectory()) return TOOL_ROOT; } catch (e) { /* not installed */ }
    return own;
}

/** The voice's display name from its id: es-ES-ElviraNeural -> Elvira. */
const voiceName = (id) => String(id || '').replace(/^[a-z]{2}-[A-Z]{2}-/, '').replace(/Neural$/, '');

module.exports = { LANGS, DEFAULT, LANG_NAME, TOOL_ROOT, root, dirsFor, voiceName };
