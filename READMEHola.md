# Trackny - README técnico

## Qué hace este programa

Trackny es un sistema de visión por computadora para detectar si ciertas máquinas o áreas de trabajo están ocupadas por una persona en tiempo real. La aplicación principal corre en una PC conectada a una cámara o a un archivo de video, detecta personas con YOLO, decide el estado de cada zona con lógica temporal para evitar falsos positivos, muestra la información en pantalla completa y, opcionalmente, persiste los tiempos ocupados en MongoDB Atlas.

Además del proceso local, el proyecto también puede publicar el estado y el video hacia un backend remoto hecho con FastAPI. Ese backend expone WebSocket, endpoints HTTP y un stream MJPEG para que otro frontend consuma los datos en tiempo real.

## Arquitectura general

El flujo real del sistema es este:

1. Se abre una fuente de video con OpenCV.
2. Un modelo YOLO detecta clases de persona en cada frame.
3. Cada detección se asocia a una zona poligonal fija.
4. Se acumula tiempo continuo con persona presente o ausente.
5. Una zona solo cambia a ocupada o libre cuando supera umbrales temporales.
6. El estado se dibuja sobre el video y se publica por API local o remota.
7. Si MongoDB está habilitado, se guardan intervalos de ocupación por día.

El proyecto está dividido en dos capas:

- Detector local: `app.py` en la raíz llama a `trackny.runner.run()`.
- API cloud: `trackny.cloud_app` expone ingesta remota, WebSocket y video.

## Cómo funciona técnicamente

### 1) Arranque del detector

El punto de entrada local es [app.py](app.py). Ese archivo solo importa `run()` desde el paquete `trackny` y lo ejecuta.

La lógica principal vive en [trackny/runner.py](trackny/runner.py). Ahí se inicializa todo el estado del sistema:

- Se cargan los nombres de zona desde [trackny/zones.py](trackny/zones.py).
- Se crea [OccupancyState](trackny/state.py) con los tiempos de ocupación y desocupación.
- Se crea [MongoOccupancyStore](trackny/storage.py) si existe `MONGO_URI`.
- Se levanta [APIServer](trackny/api.py) para exponer WebSocket y HTTP local.
- Se crea [RemotePublisher](trackny/remote_publisher.py) si hay URL remota.
- Se abre la cámara o un archivo de video con [open_video_source](trackny/video.py).
- Se carga el modelo YOLO desde `yolo26n.pt`.

### 2) Fuente de video

[trackny/video.py](trackny/video.py) intenta abrir primero un archivo de video definido por `VIDEO_PATH`. Si no existe, busca automáticamente un `.mp4`, `.avi`, `.mov` o `.mkv` en la raíz del proyecto. Si no encuentra nada, usa la cámara 0.

Cuando la fuente es un video, calcula su FPS para leerlo de forma controlada desde un lector asíncrono.

### 3) Detección con YOLO

En [trackny/runner.py](trackny/runner.py), el frame se procesa con `model(frame, verbose=False)[0]` usando Ultralytics YOLO.

Luego [_process_detections](trackny/runner.py) recorre las cajas detectadas y filtra solo la clase persona, que en COCO corresponde al índice 0. Para cada caja:

- Se calcula el centro de la detección.
- Se verifica en qué polígono cae ese centro.
- Se marca la zona correspondiente como con persona.
- Se dibuja la caja y una etiqueta visual sobre el frame.

La decisión se hace por centroide, no por intersección completa. Eso simplifica el cálculo y hace el sistema más estable para ocupación por presencia humana.

### 4) Zonas de detección

Las zonas están definidas como polígonos fijos en [trackny/zones.py](trackny/zones.py). Actualmente hay seis zonas: `zona1` a `zona6`.

No se calculan por porcentajes dinámicos; son coordenadas explícitas sobre la imagen. Eso significa que el sistema está calibrado para una escena concreta y conviene ajustar esas coordenadas si cambia la cámara, la resolución o el encuadre.

La función `punto_en_zona()` usa `cv2.pointPolygonTest()` para decidir si el centro de la persona está dentro de cada polígono.

### 5) Máquina de estados temporal

[trackny/state.py](trackny/state.py) contiene la lógica que evita cambios bruscos por detecciones momentáneas.

Cada zona mantiene estos valores:

- `tiempo_con_persona`: segundos acumulados con persona presente.
- `tiempo_sin_persona`: segundos acumulados sin persona mientras la zona ya está ocupada.
- `ocupada`: estado binario actual.
- `inicio_ocupacion`: timestamp en el que comenzó el intervalo actual de ocupación.

La transición funciona así:

- Si hay persona en la zona, `tiempo_con_persona` sube y `tiempo_sin_persona` se reinicia.
- Si no hay persona y la zona está ocupada, `tiempo_sin_persona` sube.
- Si `tiempo_con_persona` supera `TIEMPO_PARA_OCUPAR` el estado pasa a ocupada.
- Si la zona ocupada acumula más de `TIEMPO_PARA_DESOCUPAR` sin persona, vuelve a libre.

Los valores por defecto vienen de [trackny/config.py](trackny/config.py):

- `TIEMPO_PARA_OCUPAR = 10`
- `TIEMPO_PARA_DESOCUPAR = 5`

### 6) Persistencia de ocupación

[trackny/storage.py](trackny/storage.py) guarda intervalos de ocupación en MongoDB Atlas. El diseño no escribe por frame, sino por intervalos completos:

- Cuando una zona pasa a ocupada, se registra `inicio_ocupacion`.
- Cuando pasa a libre, se calcula el intervalo completo y se acumula en memoria.
- Un hilo de flush escribe lotes cada `MONGO_FLUSH_INTERVAL` segundos.

La colección usa una clave única por `date` y `machine_id`, con un acumulado diario de segundos ocupados. Eso permite sumar tiempo sin perder precisión entre reinicios.

Si `MONGO_URI` no está definido, la persistencia queda desactivada y la app sigue funcionando.

### 7) Visualización en pantalla

El video se dibuja con OpenCV directamente en la ventana `Deteccion de Maquinas`.

El render hace tres cosas principales:

- Dibuja los contornos de cada zona.
- Escribe el estado `OCUPADA` o `LIBRE` sobre la escena.
- Pinta un panel lateral con el estado de todas las zonas.

El programa fuerza pantalla completa con `cv2.WND_PROP_FULLSCREEN`, así que funciona como monitor de planta o terminal de supervisión.

### 8) Publicación remota

[trackny/remote_publisher.py](trackny/remote_publisher.py) permite enviar datos a una instancia cloud.

Envía dos tipos de payload:

- Estado: `POST /internal/state`
- Frame JPEG: `POST /internal/frame`

La publicación de estado se limita por `REMOTE_STATE_INTERVAL` para evitar tráfico excesivo. El video, si está habilitado, se comprime a JPEG, se reduce de ancho si supera `REMOTE_FRAME_MAX_WIDTH` y se envía con un FPS configurado por `REMOTE_VIDEO_FPS`.

La autenticación usa el header `x-internal-token` con el valor de `INTERNAL_API_TOKEN`.

## API local

[trackny/api.py](trackny/api.py) expone la API que usa el detector local cuando `RUN_LOCAL_API=true`.

### Endpoints

- `GET /`
- `GET /ws`
- `GET /api/ocupacion/hoy`
- `GET /api/ocupacion/registros`

### WebSocket

El WebSocket emite un JSON simple con el estado actual de cada zona:

```json
{
  "zona1": 0,
  "zona2": 1,
  "zona3": 0
}
```

Semántica:

- `0` = libre
- `1` = ocupada

El servidor mantiene una lista de clientes conectados y hace broadcast cuando cambia el estado.

### HTTP diario

`GET /api/ocupacion/hoy` devuelve el total de segundos ocupados por zona para el día UTC actual.

Ejemplo:

```json
{
  "persistencia_activa": true,
  "fecha_utc": "2026-04-18",
  "zona1_segundos": 120.5,
  "zona2_segundos": 11.0
}
```

`GET /api/ocupacion/registros` devuelve todos los documentos guardados en MongoDB para la colección de ocupación.

Ejemplo de un elemento de la respuesta:

```json
{
  "_id": "66fb2d4c1a2e8f3c7d9b1234",
  "date": "2026-04-16",
  "machine_id": "zona1",
  "created_at": "2026-04-16T12:34:56.789000Z",
  "occupied_seconds": 31.91,
  "updated_at": "2026-04-16T12:35:10.123000Z"
}
```

## API cloud

[trackny/cloud_app.py](trackny/cloud_app.py) sirve cuando se quiere separar detector y frontend.

### Endpoints públicos

- `GET /`
- `GET /ws`
- `GET /healthz`
- `GET /api/ocupacion/hoy`
- `GET /api/ocupacion/registros`
- `GET /api/video/meta`
- `GET /api/video/snapshot`
- `GET /api/video/live`

### Endpoints internos

- `POST /internal/state`
- `POST /internal/frame`

El backend cloud mantiene memoria del último estado y del último frame JPEG recibido. No hace detección; solo recibe y redistribuye.

### Seguridad interna

Por defecto, los endpoints internos requieren token. Si `ALLOW_INSECURE_INTERNAL=false` y no hay `INTERNAL_API_TOKEN`, el backend rechaza la ingesta.

Acepta token por:

- Header `x-internal-token`
- Header `Authorization: Bearer ...`

## Frontend

El archivo [index.html](index.html) es una interfaz estática que se puede servir desde el backend cloud o abrirse con otro frontend que apunte al mismo origen.

Ese frontend hace tres cosas:

- Abre un WebSocket hacia `/ws`.
- Hace polling a `/api/ocupacion/hoy` para mostrar el tiempo acumulado.
- Consulta `/api/video/meta` y, si hay video, carga `/api/video/live` como stream MJPEG.

El WebSocket está pensado para actualizaciones instantáneas de estado. El HTTP se usa para métricas acumuladas que no cambian en cada frame.

## Variables de entorno relevantes

Las variables se cargan desde `.env` y `.env.detector` en [trackny/config.py](trackny/config.py).

### Detector local

- `YOLO_MODEL`: ruta del modelo, por defecto `yolo26n.pt`
- `HOST`: host de la API local, por defecto `0.0.0.0`
- `PORT`: puerto de la API local, por defecto `8000`
- `RUN_LOCAL_API`: activa o desactiva la API local
- `VIDEO_PATH`: archivo de video a usar como fuente
- `TIEMPO_PARA_OCUPAR`: segundos para marcar como ocupada
- `TIEMPO_PARA_DESOCUPAR`: segundos para marcar como libre
- `MONGO_URI`: activa persistencia en MongoDB Atlas
- `MONGO_DB_NAME`: base de datos destino
- `MONGO_COLLECTION`: colección destino
- `MONGO_FLUSH_INTERVAL`: intervalo de flush en segundos
- `REMOTE_INGEST_URL`: URL del backend cloud
- `REMOTE_STATE_INTERVAL`: frecuencia mínima de publicación de estado
- `REMOTE_VIDEO_ENABLED`: activa envío de video
- `REMOTE_VIDEO_FPS`: FPS de envío remoto
- `REMOTE_JPEG_QUALITY`: calidad JPEG del frame remoto
- `REMOTE_FRAME_MAX_WIDTH`: ancho máximo del frame remoto
- `INTERNAL_API_TOKEN`: token compartido entre detector y cloud

### Backend cloud

- `ALLOW_INSECURE_INTERNAL`: permite ingesta sin token si está en `true`
- `ZONE_NAMES`: lista separada por comas con los nombres de zonas esperadas

## Instalación

### 1. Crear entorno virtual

```bash
python -m venv venv
```

### 2. Activarlo

```bash
# Windows
venv\Scripts\activate

# Linux o macOS
source venv/bin/activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

Si solo vas a desplegar el backend cloud en Render:

```bash
pip install -r requirements-render.txt
```

## Ejecución

### Detector local completo

```bash
python app.py
```

Ese comando inicia:

- lectura de video
- detección YOLO
- ventana OpenCV
- API local si `RUN_LOCAL_API=true`
- publicación remota si `REMOTE_INGEST_URL` está configurado

### Backend cloud por separado

```bash
uvicorn trackny.cloud_app:app --host 0.0.0.0 --port 8000
```

## Despliegue recomendado

La arquitectura más estable es esta:

- Una PC local ejecuta el detector YOLO/OpenCV.
- Un servicio cloud recibe estados y video.
- Un frontend consulta WebSocket, HTTP y video desde el cloud.

Esto evita cargar la cámara, el modelo y la UI en el mismo hosting.

### Render

El archivo [render.yaml](render.yaml) ya deja el servicio preparado con:

- build con `requirements-render.txt`
- start con `uvicorn trackny.cloud_app:app --host 0.0.0.0 --port $PORT`
- `INTERNAL_API_TOKEN` generado automáticamente
- `ALLOW_INSECURE_INTERNAL=false`
- `ZONE_NAMES=zona1,zona2,zona3,zona4,zona5,zona6`

## Formato de datos

### Estado por zonas

```json
{
  "zona1": 0,
  "zona2": 1
}
```

### Totales diarios

```json
{
  "zona1_segundos": 120.5,
  "zona2_segundos": 11.0
}
```

### Frame remoto

El frame se envía como bytes JPEG puros con `Content-Type: image/jpeg`.

## Estructura del proyecto

```text
TRACKNY/
├── app.py
├── index.html
├── render.yaml
├── requirements.txt
├── requirements-render.txt
├── yolo26n.pt
└── trackny/
    ├── api.py
    ├── cloud_app.py
    ├── config.py
    ├── frame_reader.py
    ├── remote_publisher.py
    ├── runner.py
    ├── state.py
    ├── storage.py
    ├── video.py
    └── zones.py
```

## Dependencias principales

- `opencv-python`: captura, dibujo y codificación de frames
- `ultralytics`: inferencia YOLO
- `fastapi`: API local y cloud
- `uvicorn`: servidor ASGI
- `requests`: publicación remota
- `pymongo`: persistencia opcional en MongoDB
- `python-dotenv`: carga de variables de entorno

## Notas técnicas importantes

- La precisión del sistema depende mucho de la posición real de la cámara y de que las zonas sigan alineadas con el encuadre.
- El estado ocupada o libre no cambia por una sola detección; requiere tiempo continuo.
- El guardado en MongoDB es por intervalos, no por frame, para reducir escrituras.
- El frontend consume mejor el estado vía WebSocket y los totales vía HTTP separado.

## Reporte de resultados (para tu entrega)

Esta sección está pensada para que puedas copiarla casi tal cual en tu reporte final. Solo reemplaza los campos entre corchetes.

### 1) Objetivo de la prueba

Validar que Trackny detecta ocupación de zonas de trabajo en tiempo real con estabilidad temporal, y que publica correctamente estados y tiempos acumulados.

### 2) Configuración experimental

- Fecha de prueba: [AAAA-MM-DD]
- Lugar: [laboratorio / planta / aula]
- Fuente de video: [cámara USB / archivo MP4]
- Resolución: [ej. 1280x720]
- Modelo: [yolo26n.pt]
- Umbral para marcar ocupada (TIEMPO_PARA_OCUPAR): [10] s
- Umbral para marcar libre (TIEMPO_PARA_DESOCUPAR): [5] s
- Persistencia MongoDB: [activada / desactivada]
- Publicación remota: [si / no]

### 3) Escenarios evaluados

| Escenario | Descripción | Duración | Resultado esperado |
|---|---|---:|---|
| E1 | Sin personas en escena | [xx min] | Todas las zonas en LIBRE |
| E2 | Persona entra a zona y permanece | [xx min] | Cambio a OCUPADA tras umbral |
| E3 | Persona sale de zona | [xx min] | Cambio a LIBRE tras umbral |
| E4 | Tránsito rápido por zona | [xx min] | Sin cambio permanente de estado |
| E5 | Dos zonas activas simultáneamente | [xx min] | Estados independientes por zona |

### 4) Métricas que sí puedes reportar con este sistema

1. Latencia de activación por zona.
  Tiempo entre entrada real de la persona y cambio de estado a OCUPADA.

2. Latencia de liberación por zona.
  Tiempo entre salida real de la persona y cambio de estado a LIBRE.

3. Tiempo acumulado ocupado por zona.
  Se obtiene en segundos por endpoint /api/ocupacion/hoy.

4. Estabilidad de estado.
  Número de cambios espurios (falsas transiciones) observados durante la prueba.

5. Disponibilidad del sistema.
  Tiempo total en ejecución sin caídas durante la sesión.

### 5) Fórmulas sugeridas

- Porcentaje de ocupación por zona:
  ocupacion_porcentaje = (segundos_ocupada / segundos_totales_prueba) x 100

- Error absoluto de latencia:
  error_latencia = |latencia_observada - latencia_objetivo|

- Tasa de falsas transiciones:
  tasa_falsas_transiciones = (transiciones_falsas / transiciones_totales) x 100

### 6) Tabla de resultados (llenado rápido)

| Zona | Tiempo ocupado (s) | % ocupación | Latencia activación (s) | Latencia liberación (s) | Falsas transiciones |
|---|---:|---:|---:|---:|---:|
| zona1 | [ ] | [ ] | [ ] | [ ] | [ ] |
| zona2 | [ ] | [ ] | [ ] | [ ] | [ ] |
| zona3 | [ ] | [ ] | [ ] | [ ] | [ ] |
| zona4 | [ ] | [ ] | [ ] | [ ] | [ ] |
| zona5 | [ ] | [ ] | [ ] | [ ] | [ ] |
| zona6 | [ ] | [ ] | [ ] | [ ] | [ ] |

### 7) Análisis breve (plantilla redactada)

Durante la evaluación, el sistema detectó correctamente la presencia de personas en las zonas definidas, manteniendo estabilidad en los cambios de estado gracias a los umbrales temporales configurados. Se observó que la latencia de activación promedio fue de [X] s y la latencia de liberación promedio de [Y] s, valores consistentes con los parámetros del sistema. En términos de ocupación, las zonas con mayor uso fueron [zona(s)], alcanzando [Z] % del tiempo total de prueba. No obstante, se identificaron [N] transiciones falsas en condiciones de [oclusiones/cambios de iluminación/tránsito rápido], lo que sugiere como mejora futura el ajuste fino de zonas y umbrales para escenarios más exigentes.

### 8) Conclusión (plantilla)

Trackny cumple con el objetivo de monitorear ocupación de zonas en tiempo real y generar métricas útiles para análisis operativo. Como trabajo futuro, se propone ampliar la validación con más condiciones de iluminación, múltiples cámaras y comparación contra etiquetado manual para cuantificar precisión global.

### 9) Evidencia recomendada para anexos

- Captura de pantalla de la vista con zonas y estados.
- Captura del endpoint /api/ocupacion/hoy con datos del día.
- Captura o liga del stream de video remoto (si aplica).
- Tabla final de resultados en formato hoja de cálculo.

## Licencia

No se ha definido una licencia pública en este repositorio.

Este proyecto es parte de un desarrollo académico para la Universidad Iberoamericana Puebla.

## Contacto y Soporte

Para dudas, sugerencias o reportar problemas con el sistema, contactar al equipo de desarrollo del IDIT.

---

**Nota**: Este es un prototipo en fase de pruebas. El comportamiento y las funcionalidades pueden cambiar en versiones futuras.
