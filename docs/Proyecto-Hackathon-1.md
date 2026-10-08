# Coffee House AI para el Hackathon 1

Construí una demo web que responde consultas sobre el menú de Coffee & Matcha House. Combina recuperación de información del catálogo con Llama y reglas que verifican los datos antes de mostrarlos al cliente. El resultado está publicado para su revisión; el inventario es una simulación y no se realizan compras.

## Problema y solución

Los clientes necesitan encontrar rápidamente bebidas, tamaños y precios, y saber si hay existencias. Un asistente puede reducir consultas repetitivas, pero debe evitar inventar productos o información del negocio. La solución usa un menú documentado y reconoce los datos que todavía no están confirmados.

## Funciones de la demostración

- Chat como vista principal, con respuestas breves en español e indicador de búsqueda. El botón flotante abre el menú sin desplazar el chat.

- Carta con 72 bebidas en tarjetas, búsqueda, categorías, tamaños, precios y disponibilidad de demostración. Puede consultarse sin usar el asistente.

- Panel privado para cambiar disponibilidad mediante revisión y guardado explícitos. SQLite conserva el estado y la auditoría después de reiniciar.

- Hasta tres consultas activas y diez en espera, una pendiente por conversación. Se admite cancelación y se aplica un límite total de dos minutos.

## Cómo se construyó el asistente

RAG recupera información del menú con MiniLM multilingüe, búsqueda por términos y coincidencias del catálogo. Llama 3.2 1B Instruct clasifica la intención de la consulta. El sistema conserva preferencias explícitas entre turnos, pide aclaraciones y construye los precios y la disponibilidad desde datos comprobados. El modelo no decide los importes ni los permisos.

El proyecto utiliza Python, FastAPI, Gradio, SQLite, sentence-transformers, HTTPX y llama.cpp. La interfaz y el panel se alojan en un VPS con HTTPS. La inferencia se ejecuta en una torre propia conectada mediante un túnel privado; por eso necesita que la torre esté encendida.

## Relación con el Hackathon 1

El proyecto aplica prompting, RAG, Llama y evaluación a la atención de clientes. También se entrenó LoRA en Colab con Transformers y PEFT sobre 700 conversaciones sintéticas. La comparación de los adaptadores de 600 y 700 ejemplos no demostró una mejora global suficiente. La demo utiliza el modelo base sin LoRA; el notebook y los resultados del experimento se conservan en el repositorio.

## Validación y ejemplos comprobados

La copia pública pasó 328 pruebas automatizadas de negocio, persistencia, permisos y cola, con datos sintéticos y dobles de inferencia. Además, 20 de 20 escenarios funcionales pasaron con Llama y MiniLM reales en un entorno aislado, incluyendo cambios de existencias y desconexión. Kevin consideró adecuadas las respuestas y comprobó desde su celular ambos precios de E07. No se registró una revisión exhaustiva de teclado y cancelación.

- Latte caliente mediano sin extras: $70.00 MXN, 12 oz / 360 ml. Iced Latte grande con avena y espresso extra: $110.00 MXN, 16 oz / 480 ml.

- Iced Oreo Latte: agotado en el estado inicial de la demo; el menú conserva las otras preparaciones Oreo por separado.

- Wifi, horarios o dirección no documentados: se reconoce la falta de información y se remite al personal.

- Una petición de administrador escrita en el chat no otorga permisos para modificar precios, stock ni acceder a credenciales.

## Cómo revisar la entrega

Página de demostración https://www.xn--k-9gaa.com/soodo/CoffeeHouse/

Repositorio público https://github.com/kevyx-gif/coffee-house-hackathon-1

Abrir la página, enviar una pregunta sobre precio o existencia y desplegar Menú para contrastar la respuesta con el catálogo. El README del repositorio explica la ejecución local, los requisitos y la preparación de los modelos. No se necesita acceso al panel privado para revisar el asistente público.

## Límites y trabajo futuro

Es una demo académica sin pedidos, pagos, inventario real ni WhatsApp. Los datos provisionales se señalan. Las consultas ambiguas requieren confirmación y los datos desconocidos se remiten al personal. Una batería finita no garantiza todas las conversaciones; la valoración humana del tono y la revisión táctil desde celular se registran por separado.

Para el Hackathon 2 se plantea integrar WhatsApp, roles operativos y tickets con confirmación previa al descuento de inventario; todavía no está implementado. La entrega no incluye credenciales, pesos de modelos, historial administrativo, conversaciones ni documentos internos.
