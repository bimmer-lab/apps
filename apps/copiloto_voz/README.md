# Copiloto de voz

Su BMW le habla: frases grabadas, de usted, cuando pasa algo. Arranca sola con el coche y trabaja en segundo plano,
también con la pantalla cerrada.

## Los avisos, por prioridad

Si dos coinciden, suena antes el de número más bajo; **nunca dos voces a la vez**. **Durante una llamada no habla**:
los avisos esperan a que cuelgue. Un aviso que lleva más de 2 minutos esperando se descarta (ya no vendría a cuento),
salvo el de combustible y el de neumáticos.

| # | Aviso | Cuándo | Cada cuánto |
|---|---|---|---|
| 1 | Combustible | autonomía de 50 km o menos (ajuste): «Nivel de combustible bajo» | una vez, hasta repostar |
| 2 | Neumáticos | el propio BMW avisa de presión baja | una vez por viaje |
| 3 | Posible hielo | 3 °C o menos fuera, conduciendo | una vez por viaje |
| 4 | Motor frío | aceite por debajo de 80 °C y más de 2.500 rpm | una vez por viaje |
| 5 | Bienvenida | al arrancar el motor: buenos días / tardes / noches | una vez por viaje (no al volver a arrancar en menos de 3 min) |
| 6 | Mantenimiento | al arrancar, si faltan 2.000 km o 60 días para el aceite, los frenos o la inspección | como mucho una vez por semana |
| 7 | Ventanillas | al apagar, con una ventanilla o el techo abierto | una vez por apagado |
| 8 | Fin del viaje | al apagar después de conducir más de 1 minuto | una vez por viaje |
| 9 | Pausa | cada 2 horas conduciendo (una parada de 15 min reinicia la cuenta) | cada 2 horas |
| 10 | Motor caliente | el aceite llega a 80 °C, en un viaje que empezó en frío | una vez por viaje, siempre el último |

Cada aviso se activa o desactiva en **Ajustes**, donde también se elige la autonomía del aviso de combustible.

Solo lee del coche. Lo único que hace es reproducir sonido. No necesita
internet ni el móvil.

## La voz

Una sola voz: **español, Elvira** (femenina, de España, la más parecida a «Penélope» de Waze). Las 13 frases son una
herramienta aparte del Store, **copiloto-voz-voces** (unos 1 MB), que se instala sola con la app en
`/var/opt/copiloto-voz-voces/current/`. El reproductor de BMW abre los archivos con su propio usuario y no puede
leerlos dentro de la carpeta de una app (ITEM_UNPLAYABLE); en `/var/opt` sí, como el audio de RideTube. Son AAC en
.m4a, como RideTube: el reproductor del coche rechaza el MP3.

Los textos están en `tools-prebuilt/copiloto-voz-voces/frases.json`. Para grabarlas otra vez (en el PC, con internet):

```
py -m pip install edge-tts
winget install ffmpeg
py scripts\generar_voces.py
```

El script rehace solo las frases que cambiaron, las pasa a .m4a y actualiza la herramienta
(`scripts/voces_componente.py`: su lista de archivos y su versión, y la versión que pide la app).

## La pantalla

Tres páginas, en las pestañas de arriba (junto a Settings):

- **Avisos**: estilo BMW, un recuadro por aviso, con su icono, su prioridad y si está activo, en cola, hablando o
  cuándo sonó; «sin grabación» si falta su frase. El último recuadro: lo que lee del coche.
- **Diagnóstico** (la lista de comprobación): cada señal del coche con ✓ (verde: llega, con su último valor), gris
  (aún no ha llegado) o rojo (error al suscribirse, con el motivo); la voz (grabaciones encontradas y cuáles faltan, el
  audio del coche, llamada); y el resultado de cada uno de los 10 avisos (de la demo o de la última vez que sonó).
- **Registro**: las últimas líneas del registro en la pantalla (avisos en ámbar, errores en rojo), para ver qué falla
  exactamente sin sacar el archivo `copiloto_voz.log`.

Debajo de las pestañas: la voz, el estado del audio y del coche.

## Demo: viaje simulado

**Demo: viaje simulado con los 10 avisos** (solo con el coche parado): simula un viaje completo (mantenimiento y aviso de neumáticos antes de salir; arranque; hielo y motor frío a los dos minutos; autonomía de 45 km;
dos horas conduciendo; aceite a 85 °C; ventanilla abierta y apagado) con las mismas reglas que el coche, y pasa los 10
avisos por la cola y el altavoz de verdad, por orden de prioridad, con 1,5 s entre una frase y la siguiente
(unos dos minutos). No toca las señales del coche.
Se abre la página Diagnóstico: cada aviso en azul (en cola), verde (sonó) o rojo con el motivo (p. ej. «falta la
grabación», «el coche no conectó el audio», «el reproductor no pudo con el archivo»). Lo que no suena en 5 minutos sale
en rojo. El detalle, en la página Registro. **Probar la siguiente frase** sigue reproduciéndolas una a una.

## Aprender las ventanillas

Ninguna app pública lee las ventanillas, así que la app aprende de su coche qué campo dice «abierta». En el menú,
**Aprender ventanillas** (se abre la página Diagnóstico y abajo sale qué hacer):

1. Con todo cerrado y el contacto dado, pulse **Aprender ventanillas** dos veces: la primera empieza, la segunda guarda
   «todo cerrado».
2. Abra la ventanilla del conductor y pulse otra vez. Ciérrela, abra la del acompañante y pulse; y así con las traseras
   y el techo. Si una no la tiene o no quiere enseñarla, pulse sin abrir nada: se salta.
3. Al final dice cuántas aprendió. Desde entonces la fila **Puertas y ventanillas** de Diagnóstico muestra en directo
   qué está abierto, y el aviso de ventanillas al apagar usa lo aprendido (se guarda; volver a aprender lo sustituye).

Hasta aprenderlas, la app adivina por el nombre de los campos («sin aprender» en Diagnóstico). Las puertas no cuentan.

## Cómo suena

Como RideTube (JustAnotherPU/ridetube, probado en un X5 G05): el gestor de audio de BMW conecta la fuente de medios
de las apps a los altavoces, el reproductor de BMW reproduce la frase y se desconecta. **Mientras habla, la música se
pausa**; al terminar vuelve la fuente que sonaba. **La voz pasa por encima de todo:** radio, CarPlay o Bluetooth se cortan mientras habla y vuelven después; un vídeo de RideTube se detiene y queda en pausa donde estaba (se reanuda con Play).

## Por comprobar en el coche

Todo está probado fuera del coche, no en él todavía. Si algo no funciona, la pantalla y el registro
(`copiloto_voz.log`) dicen por qué:

- **El sonido** desde una app (RideTube lo hace desde un servicio): la demo.
- **Ventanillas y techo**: hay que enseñárselas una vez (Aprender ventanillas, arriba).
- **El aviso de neumáticos de BMW**: se reconoce por el nombre del mensaje de Check Control (en español, alemán o
  inglés); el registro anota los mensajes activos.
- **Mantenimiento** (la lista de Service), **aceite**, **llamadas**:
  señales que otras apps ya leen en coches reales.

## Memoria y CPU

Escucha señales que el coche manda cuando cambian y hace una cuenta cada 5 s; no hace diagnosis. Se abre enseguida: el
segundo plano se suscribe a las señales después de arrancar, sin hacer esperar a la pantalla. La pantalla se dibuja como
mucho cada 0,4 s (y cada 30 s si no cambia nada); las grabaciones se buscan una vez. Sin la pantalla abierta no dibuja
nada. El sonido lo reproduce el reproductor de BMW, no la app. La app pesa 0,9 MB; la voz, aparte, 1 MB.

## Pruebas

`node tools/test_copiloto_voz.js`: los 10 avisos, la cola (prioridad, de una en una, llamadas, avisos caducados), la
lectura de las señales, la frase según la hora, la secuencia del audio, la carpeta de la voz, las 13 frases y la
herramienta de la voz, y la demo (63 comprobaciones).
