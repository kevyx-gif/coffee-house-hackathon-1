# Coffee House AI para el Hackathon 1

Construí una demo web que responde consultas sobre el menú de Coffee & Matcha House. Combina recuperación de información del catálogo con Llama y reglas que verifican los datos antes de mostrarlos al cliente. El resultado está publicado para su revisión; el inventario es una simulación y no se realizan compras.

## Problema y solución

Los clientes necesitan encontrar rápidamente bebidas, tamaños y precios, y saber si hay existencias. Un asistente puede reducir consultas repetitivas, pero debe evitar inventar productos o información del negocio. La solución usa un menú documentado y reconoce los datos que todavía no están confirmados.

## Funciones de la demostración

- Chat como vista principal, con lenguaje breve en español, indicador de búsqueda y fuentes discretas al pie de cada respuesta.

- Carta con 72 bebidas en tarjetas, búsqueda, categorías, tamaños, precios y disponibilidad de demostración. Puede consultarse sin usar el asistente.

- Panel privado para cambiar disponibilidad mediante revisión y guardado explícitos. SQLite conserva el estado y la auditoría después de reiniciar.

- Hasta tres consultas activas y diez en espera, una pendiente por conversación. Se admite cancelación y se aplica un límite total de dos minutos.

## Cómo se construyó el asistente

RAG recupera información del menú mediante embeddings multilingües MiniLM, búsqueda por términos y coincidencias del catálogo. El backend interpreta las preferencias de forma conservadora, consulta el estado guardado y envía a Llama 3.2 1B Instruct un contexto acotado. El modelo genera un borrador interno; los precios y la disponibilidad de la respuesta final se validan con los datos, sin aceptar hechos libres que los contradigan.

El proyecto utiliza Python, FastAPI, Gradio, SQLite, sentence-transformers, HTTPX y llama.cpp. La interfaz y el panel se alojan en un VPS con HTTPS. La inferencia se ejecuta en una torre propia conectada mediante un túnel privado; por eso necesita que la torre esté encendida.

## Relación con el Hackathon 1

El proyecto aplica prompting, RAG, Llama y evaluación a la atención de clientes. Se exploró LoRA en Colab con Transformers y PEFT, sin demostrar una mejora suficiente de exactitud factual. El adaptador del despliegue sigue siendo experimental y sus borradores no tienen autoridad sobre los hechos. La copia pública permite ejecutar el modelo base sin adaptador.

## Validación y ejemplos comprobados

La copia pública pasó 276 pruebas de catálogo, precios, recuperación, respuestas, persistencia, permisos y cola. Usan datos sintéticos y dobles de inferencia; no califican la calidad del modelo. En el VPS pasaron 49 pruebas de API, interfaz y conversación, con comprobación manual de acceso privado, guardado y consultas en HTTPS.

- Latte caliente mediano sin extras: respuesta de $70.00 MXN con referencia al menú.

- Iced Oreo Latte: agotado en el estado inicial de la demo; el menú conserva las otras preparaciones Oreo por separado.

- Wifi, horarios o dirección no documentados: se reconoce la falta de información y se remite al personal.

- Una petición de administrador escrita en el chat no otorga permisos para modificar precios, stock ni acceder a credenciales.

## Cómo revisar la entrega

Página de demostración https://www.xn--k-9gaa.com/soodo/CoffeeHouse/

Repositorio público https://github.com/kevyx-gif/coffee-house-hackathon-1

Abrir la página, enviar una pregunta sobre precio o existencia y desplegar Menú para contrastar la respuesta con el catálogo. El README del repositorio explica la ejecución local, los requisitos y la preparación de los modelos. No se necesita acceso al panel privado para revisar el asistente público.

## Límites y trabajo futuro

Es una demo académica sin pedidos, pagos, inventario real ni WhatsApp. Los datos provisionales están señalados. Las consultas ambiguas o con extras pueden requerir aclaraciones; no se declara una evaluación conversacional completa de 20 sobre 20. Siguen pendientes la revisión táctil desde celular y la evaluación humana integral.

Para el Hackathon 2 se plantea integrar WhatsApp, roles operativos y tickets con confirmación previa al descuento de inventario; todavía no está implementado. La entrega no incluye credenciales, pesos de modelos, historial administrativo, conversaciones ni documentos internos.
