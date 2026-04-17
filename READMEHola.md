# Sistema de Detección de Ocupación de Máquinas

## Descripción del Proyecto

Este es un **sistema de prueba** para la detección automática del estado de ocupación de máquinas mediante visión por computadora. El proyecto está siendo desarrollado para el **IDIT (Instituto de Diseño e Innovación Tecnológica) de la Universidad Iberoamericana Puebla**.

El sistema forma parte de una solución más amplia de **gestión e información** que permitirá a estudiantes, profesores y personal administrativo conocer en tiempo real qué máquinas están disponibles o en uso.

## Estado Actual

🚧 **VERSIÓN DE PRUEBA** 🚧

Esta implementación actual es un prototipo funcional que demuestra las capacidades básicas del sistema:

- Detección de personas mediante YOLO (YOLOv26)
- Seguimiento de ocupación en dos zonas independientes
- Visualización en tiempo real del estado de cada máquina
- Lógica temporal para evitar falsos positivos

## Funcionalidades

### Detección de Ocupación
- **Dos máquinas independientes**: El sistema monitorea dos zonas separadas (40% izquierda, 40% derecha, con 20% de espacio neutral en el centro)
- **Detección por presencia**: Utiliza YOLOv26 para detectar personas en tiempo real
- **Lógica temporal**:
  - Una máquina se marca como **OCUPADA** después de 10 segundos con una persona presente
  - Se marca como **DESOCUPADA** después de 5 segundos sin personas
  - Esto evita cambios de estado por movimientos momentáneos

### Visualización
- Pantalla completa adaptativa
- Rectángulos de zonas para identificar cada máquina
- Información en tiempo real:
  - Estado actual (OCUPADA/DESOCUPADA)
  - Tiempo con persona presente
  - Tiempo sin persona (cuando está ocupada)
- Texto escalable según resolución de pantalla
- Fondos semitransparentes para mejor legibilidad

### Persistencia de tiempo ocupado (MongoDB Atlas)
- Guarda segundos de ocupación por máquina de forma acumulada
- Usa escritura por lotes para bajo consumo de base de datos
- No escribe por frame; acumula en memoria y hace flush periódico
- Modelo diario por máquina (clave única: fecha + machine_id)

## Requisitos

### Hardware
- Cámara web funcional
- Computadora con capacidad para ejecutar modelos de deep learning

### Software
- Python 3.12
- OpenCV
- Ultralytics YOLO
- Modelo YOLOv26n

## Instalación

1. Clonar o descargar este repositorio

2. Crear un entorno virtual:
```bash
python -m venv venv
```

3. Activar el entorno virtual:
```bash
# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

4. Instalar dependencias:
```bash
pip install opencv-python ultralytics
```

Si usarás persistencia en Atlas, instala también:
```bash
pip install pymongo
```

5. Descargar el modelo YOLO:
   - Asegúrate de tener el archivo del modelo YOLO (`yolo26n.pt`)
   - Si no lo tienes, el sistema intentará descargarlo automáticamente (requiere conexión a internet)

## Uso

Ejecutar el script principal:
```bash
python app.py
```

## Despliegue recomendado (Render + PC detectora)

La arquitectura recomendada es:

- Render: solo API/WebSocket/frontend (`trackny.cloud_app:app`)
- PC detectora: YOLO/OpenCV local (`python app.py`) y publica estados/video al cloud

### 1) Desplegar en Render

Este repo ya incluye [render.yaml](render.yaml) y [requirements-render.txt](requirements-render.txt).

En Render:

- Crea un Web Service desde este repositorio.
- Usa Blueprint (render.yaml) o configura manualmente:
  - Build Command: `pip install -r requirements-render.txt`
  - Start Command: `uvicorn trackny.cloud_app:app --host 0.0.0.0 --port $PORT`
- Define variables de entorno:
  - `INTERNAL_API_TOKEN` (obligatoria)
  - `ALLOW_INSECURE_INTERNAL=false`
  - `ZONE_NAMES=zona1,zona2,zona3,zona4,zona5,zona6`

### 2) Configurar PC detectora para publicar al cloud

En la PC donde corre YOLO/OpenCV, define:

```bash
export REMOTE_INGEST_URL="https://tu-servicio.onrender.com"
export INTERNAL_API_TOKEN="tu_token_interno"
export RUN_LOCAL_API="false"
export REMOTE_STATE_INTERVAL="1.0"
export REMOTE_VIDEO_ENABLED="true"
export REMOTE_VIDEO_FPS="5"
export REMOTE_JPEG_QUALITY="70"
```

- `REMOTE_INGEST_URL`: URL publica de Render, sin slash final.
- `RUN_LOCAL_API=false`: desactiva API local en la PC detectora.
- `REMOTE_VIDEO_ENABLED=true`: publica frames JPEG para visualizacion remota.

### 3) Endpoints internos (ingesta)

- `POST /internal/state`
- `POST /internal/frame`

Autenticacion requerida por header:

- `x-internal-token: <INTERNAL_API_TOKEN>`

Ejemplo de envio de estado:

```bash
curl -X POST "https://tu-servicio.onrender.com/internal/state" \
  -H "Content-Type: application/json" \
  -H "x-internal-token: TU_TOKEN" \
  -d '{"states":{"zona1":1,"zona2":0},"totals_seconds":{"zona1":120.5,"zona2":11.0}}'
```

Ejemplo de envio de frame JPEG:

```bash
curl -X POST "https://tu-servicio.onrender.com/internal/frame" \
  -H "Content-Type: image/jpeg" \
  -H "x-internal-token: TU_TOKEN" \
  --data-binary "@frame.jpg"
```

### 4) Endpoints de consumo remoto

- `GET /ws`: estado de zonas en tiempo real.
- `GET /api/ocupacion/hoy`: segundos por zona (segun ultima publicacion).
- `GET /api/video/live`: stream MJPEG (si hay frames publicados).
- `GET /api/video/snapshot`: ultimo frame JPEG.
- `GET /api/video/meta`: disponibilidad de video.
- `GET /healthz`: health check.

### Variables de entorno para MongoDB Atlas (opcional)

Si no defines `MONGO_URI`, la app funciona igual pero sin persistencia en base de datos.

```bash
export MONGO_URI="mongodb+srv://usuario:password@cluster.mongodb.net/?retryWrites=true&w=majority"
export MONGO_DB_NAME="trackny"
export MONGO_COLLECTION="occupancy_daily"
export MONGO_FLUSH_INTERVAL="86400"
```

- `MONGO_URI`: cadena de conexión de Atlas
- `MONGO_DB_NAME`: base de datos (default: `trackny`)
- `MONGO_COLLECTION`: colección de acumulados diarios (default: `occupancy_daily`)
- `MONGO_FLUSH_INTERVAL`: segundos entre escrituras por lote (default: `86400`, 24h)

### Variables de entorno para despliegue publico

Para exponer HTTP y WebSocket fuera de localhost:

```bash
export HOST="0.0.0.0"
export PORT="8000"
```

Tambien se aceptan `APP_HOST` y `APP_PORT` por compatibilidad.

- `HOST`: interfaz de escucha de Uvicorn (usa `0.0.0.0` para acceso externo)
- `PORT`: puerto de escucha

Si despliegas en una VM o servidor:

- Abre el puerto en firewall/security group.
- Si usas dominio HTTPS, publica por proxy con TLS y usa `wss://` en frontend.

Endpoint para consultar acumulado del día (UTC):

```bash
GET /api/ocupacion/hoy
```

### Controles
- **ESC**: Salir del programa
- El sistema se ejecuta en pantalla completa automáticamente

## Integrar el WebSocket en tu frontend

El backend expone un WebSocket en:

```text
ws://<host>:<port>/ws
```

Si el frontend corre en HTTPS, usa `wss://`.

### Estructura de mensajes

Cada mensaje recibido es un objeto JSON con una clave por zona:

```json
{
  "zona1": 0,
  "zona2": 1,
  "zona3": 0
}
```

- `0` = LIBRE
- `1` = OCUPADA

### Ejemplo base (JavaScript puro)

```html
<script>
  const wsProtocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsHost = '127.0.0.1:8000'; // Cambia este host en produccion
  const wsUrl = `${wsProtocol}://${wsHost}/ws`;

  let socket;
  let reconnectTimer;

  function connect() {
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      console.log('WebSocket conectado');
      clearTimeout(reconnectTimer);
    };

    socket.onmessage = (event) => {
      const zoneState = JSON.parse(event.data);
      renderZoneState(zoneState);
    };

    socket.onclose = () => {
      console.log('WebSocket desconectado. Reintentando...');
      reconnectTimer = setTimeout(connect, 2000);
    };

    socket.onerror = (err) => {
      console.error('Error WebSocket:', err);
      socket.close();
    };
  }

  function renderZoneState(state) {
    Object.entries(state).forEach(([zone, value]) => {
      const element = document.getElementById(zone);
      if (!element) return;
      element.textContent = value === 1 ? 'OCUPADA' : 'LIBRE';
      element.dataset.state = value === 1 ? 'ocupada' : 'libre';
    });
  }

  connect();
</script>
```

### Ejemplo rapido en React

```jsx
import { useEffect, useRef, useState } from 'react';

export function useTracknySocket(wsUrl) {
  const [zones, setZones] = useState({});
  const retryRef = useRef(null);

  useEffect(() => {
    let ws;

    const connect = () => {
      ws = new WebSocket(wsUrl);

      ws.onmessage = (event) => {
        setZones(JSON.parse(event.data));
      };

      ws.onclose = () => {
        retryRef.current = setTimeout(connect, 2000);
      };

      ws.onerror = () => ws.close();
    };

    connect();

    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      if (ws && ws.readyState <= 1) ws.close();
    };
  }, [wsUrl]);

  return zones;
}
```

### Recomendaciones para produccion

- Configura `HOST` y `PORT` por variables de entorno en el backend.
- Si pones un proxy (Nginx, Caddy, Traefik), habilita upgrade de WebSocket.
- Usa `wss://` detras de TLS.
- Implementa reconexion exponencial para evitar saturacion de reintentos.

## Configuración

Puedes ajustar los siguientes parámetros en el archivo `app.py`:

```python
TIEMPO_PARA_OCUPAR = 10      # Segundos con persona para marcar como ocupada
TIEMPO_PARA_DESOCUPAR = 5    # Segundos sin persona para marcar como desocupada
```

También puedes modificar las zonas de detección ajustando los porcentajes:
```python
zona_m1_x2 = int(ancho * 0.40)  # Máquina 1: 40% izquierda
zona_m2_x1 = int(ancho * 0.60)  # Máquina 2: 40% derecha (con 20% de espacio)
```

## Arquitectura del Sistema

```
┌─────────────────────────────────────────────────┐
│           Captura de Video (OpenCV)             │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│      Detección de Personas (YOLOv26)             │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│    Clasificación por Zona (Máquina 1 o 2)      │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│    Lógica Temporal (Contadores de Tiempo)       │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  Actualización de Estado (Ocupada/Desocupada)   │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│      Visualización en Pantalla Completa         │
└─────────────────────────────────────────────────┘
```

## Mejoras Futuras

Este prototipo será expandido para incluir:

### Funcionalidades Planificadas
- [ ] Soporte para más de 2 máquinas simultáneas
- [ ] Integración con base de datos para historial de uso
- [ ] API REST para consultar estados desde aplicaciones externas
- [ ] Dashboard web para visualización remota
- [ ] Notificaciones cuando máquinas quedan disponibles
- [ ] Detección de tipo de máquina (impresora 3D, cortadora láser, etc.)
- [ ] Sistema de reservas integrado
- [ ] Análisis de patrones de uso y estadísticas
- [ ] Detección de anomalías (máquina encendida sin usuario)
- [ ] Múltiples cámaras para cobertura completa del IDIT

### Integración con IDIT
El sistema final permitirá:
- Monitoreo de todas las máquinas del taller del IDIT
- Información disponible en pantallas del laboratorio
- Consulta desde aplicación móvil o web
- Integración con sistema de acceso y permisos
- Reportes de uso para mantenimiento preventivo

## Estructura del Proyecto

```
ASEIII/
│
├── app.py              # Script principal
├── README.md           # Este archivo
├── venv/               # Entorno virtual (no incluido en repositorio)
└── yolo26n.pt          # Modelo YOLO (descargar por separado)
```

## Tecnologías Utilizadas

- **Python 3.12**: Lenguaje principal
- **OpenCV**: Captura y procesamiento de video
- **Ultralytics YOLO**: Detección de objetos en tiempo real
- **YOLOv26**: Modelo de deep learning para detección de personas

## Créditos

**Desarrollado para**: Instituto de Diseño e Innovación Tecnológica (IDIT)  
**Universidad**: Iberoamericana Puebla  
**Propósito**: Sistema de gestión e información de máquinas

## Licencia

Este proyecto es parte de un desarrollo académico para la Universidad Iberoamericana Puebla.

## Contacto y Soporte

Para dudas, sugerencias o reportar problemas con el sistema, contactar al equipo de desarrollo del IDIT.

---

**Nota**: Este es un prototipo en fase de pruebas. El comportamiento y las funcionalidades pueden cambiar en versiones futuras.
