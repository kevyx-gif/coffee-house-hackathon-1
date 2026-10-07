# Guion de demostración — Hackathon 1

Duración orientativa: 4 minutos. Abrir la web antes de presentar y comprobar que el asistente está conectado.

## 1. Problema (30 segundos)

«Una persona quiere saber qué bebidas hay y cuánto pagaría con su tamaño y extras. Coffee House permite consultar el menú y orientarse sin depender de respuestas inventadas. La demostración usa disponibilidad simulada.»

## 2. Asistente y fuentes (60 segundos)

Preguntar: «¿Cuánto cuesta un latte caliente mediano?» Mostrar la respuesta, su referencia y el menú en tarjetas. Explicar que el catálogo aporta los precios y el backend calcula los totales. El menú se puede consultar aunque el asistente no esté disponible.

## 3. Límites y existencia (60 segundos)

Preguntar por una bebida marcada como agotada en el panel en ese momento. Comprobar la existencia antes de la presentación, porque puede cambiar. Luego preguntar por un horario no documentado: el asistente debe reconocer que no tiene la información y remitir al personal.

## 4. Arquitectura y aprendizaje (60 segundos)

«La aplicación usa FastAPI, una interfaz web y SQLite. La recuperación combina búsqueda con representaciones multilingües del catálogo. Llama participa en el flujo; los hechos y permisos los valida el backend. Entrenamos LoRA con Transformers y PEFT en Colab y evaluamos los resultados. Al pasar de 600 a 700 ejemplos no aumentaron las respuestas completamente correctas de la batería de desarrollo; por eso no usamos ese resultado como prueba de mejora.»

Mostrar el notebook y el documento del experimento, no archivos privados ni credenciales. Describir el modelo activo según el README de la versión presentada: no atribuir a producción las pruebas aisladas de 3B.

## 5. Cierre (30 segundos)

Mostrar el repositorio público y sus instrucciones de ejecución. «WhatsApp, pedidos reales y descuento de inventario corresponden a la siguiente etapa; esta entrega demuestra el asistente del módulo 1.»

## Comprobación con celular real

- Abrir la URL HTTPS desde el teléfono y enviar una consulta.
- Abrir/cerrar el menú, recorrer tarjetas y volver al chat.
- Comprobar que el teclado no impide usar Enviar y que no hay desplazamiento horizontal.
- Verificar el mensaje de espera, cancelar una consulta y comenzar otra.
- Registrar fecha, dispositivo, navegador y resultado. Pendiente de ejecución humana; este listado no es evidencia de aprobación.
