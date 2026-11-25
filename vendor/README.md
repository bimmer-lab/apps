# vendor/

Apps traídas de otros repositorios, cada una con el kit de su propio origen, para que el Store las
compile en el coche exactamente como en el original. Los créditos y las licencias son de sus autores.

| Carpeta | Origen | Commit | Licencia | Apps |
|---|---|---|---|---|
| `idrive7-apps/` | [cauliflowermossgrape/idrive7-apps](https://github.com/cauliflowermossgrape/idrive7-apps) | `874f7b8` | MIT | redline 2.0.51, draggy 2.7.2, dyno 1.6.5, ambient_config 2.7.26 |
| `namelsmissing/` | [NamelsMissing/mgu-apps](https://github.com/NamelsMissing/mgu-apps) | `d552013` | GPL-3.0 | dpf_monitor 0.2.8 |
| `justanotherpu/` | [JustAnotherPU/mgu-apps](https://github.com/JustAnotherPU/mgu-apps) | `42cd224` | MIT | jap_sysmon 1.0.6 |
| `ridetube/` | [JustAnotherPU/ridetube](https://github.com/JustAnotherPU/ridetube) | `eb0973b` | GPL-3.0 | jap_ridetube 0.6.4 |
| `robertnisipeanu/` | [robertnisipeanu/idrive-apps](https://github.com/robertnisipeanu/idrive-apps) | `9da968c` | GPL-3.0 (cliente de cluster-render: MPL-2.0) | robertn_waze_cluster 0.6.16 |

## Qué se ha cambiado respecto al original

- **Redline** (`idrive7-apps/apps/redline`): sin la salida al HUD (el coche no tiene). `main.js` lleva
  `const HUD = false`: no se arranca la salida al HUD, ni la sonda de navegación, ni el aviso de
  llamadas, y la página *HUD looks* desaparece (quedan Tach, Shift log y Learned). En `app.json` se
  quitan las seis interfaces que solo servían al HUD (navegación, CarPlay / Android Auto, llamadas), así
  la app se suscribe a menos señales, y los ajustes del HUD quedan ocultos. `hud.js` se queda porque la
  pantalla usa sus funciones de luces de cambio. Pasa `kit/tools/test_redline_running.js` (68 comprobaciones).
- `idrive7-apps/kit/localization/diagnostic-full.*.json` (25 MB, solo los usa el Fault Scanner) no se
  han traído: el coche descarga `kit/` en cada compilación.
- `ridetube/kit/tools/.font-cache/` (una caché de 17 MB para hornear fuentes) no se ha traído.
- **Prioridad baja en cada kit** (`<origen>/kit/runtime/priority.js`, nuestro, y dos líneas en su
  `kit/runtime/mgu.js` justo después de `new OnlineApp()`): las apps corren con nice 10 y E/S en clase
  *idle*, por debajo de CarPlay y del HMI. `scripts/vendor_sync.py` lo vuelve a aplicar tras cada
  actualización (`kit_low_priority`).
- Nada más: el resto es el código de sus autores tal cual.

## Código fuente de los nativos

`idrive7-apps/native/renderd`, `ridetube/native` y `robertnisipeanu/native` son las fuentes de los
componentes de `tools-prebuilt/` (en el mismo commit), para poder estudiarlos o recompilarlos. El Store
no los usa: instala los binarios de `tools-prebuilt/`.

## Actualizar desde el original

Copia la carpeta de la app y el `kit/` del origen encima de la suya, y vuelve a aplicar los cambios de
arriba. Para Redline, busca `const HUD = false` en `main.js`.
