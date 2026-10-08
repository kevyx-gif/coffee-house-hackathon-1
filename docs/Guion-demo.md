# Guion de demostración — Hackathon 1

Duración orientativa: 4 minutos. Abrir la web antes de presentar y comprobar que el asistente está conectado.

## 1. Problema (30 segundos)

«Una persona quiere saber qué bebidas hay y cuánto pagaría con su tamaño y extras. Coffee House permite consultar el menú y orientarse sin depender de respuestas inventadas. La demostración usa disponibilidad simulada.»

## 2. Asistente y menú (60 segundos)

Preguntar: «¿Cuánto cuesta un Latte caliente mediano y uno grande, sin extras?» Mostrar $70 y $80 con sus medidas. Abrir el botón flotante Menú y contrastar las tarjetas. Explicar que el catálogo aporta los precios y el backend calcula los totales. El último menú recibido se puede consultar aunque el asistente no esté disponible; el aviso indica si la disponibilidad no está actualizada.

## 3. Límites y existencia (60 segundos)

Preguntar por una bebida marcada como agotada en el panel en ese momento. Comprobar la existencia antes de la presentación, porque puede cambiar. Luego preguntar por un horario no documentado: el asistente debe reconocer que no tiene la información y remitir al personal.

## 4. Arquitectura y aprendizaje (60 segundos)

«La aplicación usa FastAPI, una interfaz web y SQLite. La recuperación combina búsqueda con representaciones multilingües del catálogo. Llama clasifica la intención; el sistema conserva las preferencias explícitas y verifica los hechos y permisos. Entrenamos LoRA con Transformers y PEFT en Colab. Al pasar de 600 a 700 ejemplos no aumentaron las respuestas completamente correctas de la batería de desarrollo; la demo usa el modelo base sin ese adaptador. Esto no fue una comparación controlada de LoRA contra el modelo base.»

Mostrar el notebook y el documento del experimento, no archivos privados ni credenciales. Mostrar la evaluación funcional de 20 escenarios y explicar que no sustituye una valoración humana ni demuestra todas las conversaciones posibles. No atribuir a producción las pruebas aisladas de 3B.

## 5. Cierre (30 segundos)

Mostrar el repositorio público y sus instrucciones de ejecución. «WhatsApp, pedidos reales y descuento de inventario corresponden a la siguiente etapa; esta entrega demuestra el asistente del módulo 1.»

## Comprobación con celular real

- Abrir la URL HTTPS desde el teléfono y enviar una consulta.
- Abrir/cerrar el menú, recorrer tarjetas y volver al chat.
- Comprobar que el teclado no impide usar Enviar y que no hay desplazamiento horizontal.
- Verificar el mensaje de espera, cancelar una consulta y comenzar otra.
- Kevin reportó el 7 de octubre que desde su celular el diseño se ve mejor y E07 devuelve ambos precios y medidas correctos. No informó dispositivo ni navegador; teclado y cancelación aún necesitan comprobación explícita. Este listado no acredita pasos que no se hayan realizado.
