# tools-prebuilt/

Componentes nativos tal como los publican sus autores, desempaquetados como carpetas de herramienta del
MGU Store: `component.json` en la raíz y, al lado, `files/`, `units/` y `dropins/`. El `from` de cada
entrada de `component.json` dice dónde está su archivo; el Store calcula los sha256 al empaquetar.

| Carpeta | Versión | De dónde | Fuente | Licencia |
|---|---|---|---|---|
| `cluster-render/` | 0.8.13 | release `cluster-render@0.8.13` de robertnisipeanu/idrive-apps | `vendor/robertnisipeanu/native/cluster-render` | GPL-3.0 (cliente MPL-2.0) |
| `waze-on-cluster/` | 0.14.15 | release `waze-on-cluster@0.14.15` de robertnisipeanu/idrive-apps | `vendor/robertnisipeanu/native/waze-on-cluster` | GPL-3.0 |
| `idrive7-renderd/` | 2.1.19 | `kit/renderd/prebuilt` de cauliflowermossgrape/idrive7-apps | `vendor/idrive7-apps/native/renderd` | MIT |
| `mgu-inputgate/` | 1.2.1 | `kit/renderd/inputgate/tool` de cauliflowermossgrape/idrive7-apps | `vendor/idrive7-apps/native/renderd/inputgate` | MIT |
| `jap-ridetube-d/` | 0.5.4 | release `catalog` de JustAnotherPU/ridetube | `vendor/ridetube/native/jap-ridetube-d`, `ytdec` | GPL-3.0 |
| `mgu-render-id7-touch/` | 1.4.0 | release `catalog` de JustAnotherPU/ridetube | `vendor/ridetube/native/mgu-render-id7-touch` | GPL-3.0 |

Cada artefacto se comprobó contra el sha256 del `catalog.json` de su autor antes de desempaquetarlo.

`jap-node22` (Node.js 22, que necesita `jap-ridetube-d`) no está: su binario pesa 137 MB y GitHub no
admite archivos de más de 100 MB. Se instala desde el origen `JustAnotherPU/mgu-apps` (Releases).
