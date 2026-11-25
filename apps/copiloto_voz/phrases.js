'use strict';
/** phrases.js - Copiloto de voz: which recording to play for an event. Node 8. */
const tz = require('./tz');

/** The local hour (the head unit's clock is UTC; the "timeZone" setting says where we are). */
function localHour(settings, now) {
    const off = tz.offset(settings.get('timeZone'), now);
    return new Date(now + (off === null ? 0 : off) * 60000).getUTCHours();
}

/** Which recording to play for an event: the welcome by the time of day when that one is there. */
function phraseFor(event, settings, speaker, now) {
    if (event !== 'bienvenida') return event;
    const h = localHour(settings, now);
    const part = h >= 6 && h < 12 ? 'manana' : h >= 12 && h < 20 ? 'tarde' : 'noche';
    return speaker.file('bienvenida_' + part) ? 'bienvenida_' + part : 'bienvenida';
}

module.exports = { localHour, phraseFor };
