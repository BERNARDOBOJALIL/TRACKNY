# Sistema de Detección de Ocupación de Máquinas

## Descripción del Proyecto

Este es un **sistema de prueba** para la detección automática del estado de ocupación de máquinas mediante visión por computadora. El proyecto está siendo desarrollado para el **IDIT (Instituto de Diseño e Innovación Tecnológica) de la Universidad Iberoamericana Puebla**.

El sistema forma parte de una solución más amplia de **gestión e información** que permitirá a estudiantes, profesores y personal administrativo conocer en tiempo real qué máquinas están disponibles o en uso.

## Estado Actual

🚧 **VERSIÓN DE PRUEBA** 🚧

Esta implementación actual es un prototipo funcional que demuestra las capacidades básicas del sistema:

- Detección de personas mediante YOLO (YOLOv8)
- Seguimiento de ocupación en dos zonas independientes
- Visualización en tiempo real del estado de cada máquina
- Lógica temporal para evitar falsos positivos

## Funcionalidades

### Detección de Ocupación
- **Dos máquinas independientes**: El sistema monitorea dos zonas separadas (40% izquierda, 40% derecha, con 20% de espacio neutral en el centro)
- **Detección por presencia**: Utiliza YOLOv8 para detectar personas en tiempo real
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

## Requisitos

### Hardware
- Cámara web funcional
- Computadora con capacidad para ejecutar modelos de deep learning

### Software
- Python 3.12
- OpenCV
- Ultralytics YOLO
- Modelo YOLOv8n (yolov8n.pt o yolo26n.pt)

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

5. Descargar el modelo YOLO:
   - Asegúrate de tener el archivo del modelo YOLO (`yolov8n.pt` o `yolo26n.pt`)
   - Si no lo tienes, el sistema intentará descargarlo automáticamente (requiere conexión a internet)

## Uso

Ejecutar el script principal:
```bash
python app.py
```

### Controles
- **ESC**: Salir del programa
- El sistema se ejecuta en pantalla completa automáticamente

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
│      Detección de Personas (YOLOv8)             │
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
- **YOLOv8**: Modelo de deep learning para detección de personas

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
