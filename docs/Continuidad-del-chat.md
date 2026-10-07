# Continuidad de las consultas

El chat conserva en memoria la bebida, temperatura, tamaño, extras y presupuesto que el visitante ya expresó. Una aclaración no borra esos datos. Una nueva bebida inicia una selección distinta; las conversaciones no se guardan en disco.

Ejemplo comprobado con el modelo base Llama 3.2 1B, sin LoRA:

- «Quiero un latte» → pregunta si se prefiere caliente o frío.
- «Caliente grande con avena» → $95 MXN.
- «¿Y con un espresso extra?» → conserva la avena y calcula $110 MXN.
- «Quita la avena» → conserva el espresso y calcula $95 MXN.
- «Sin extras» → calcula $80 MXN.

La recuperación híbrida aporta referencias del menú. Llama devuelve una clasificación breve en JSON. El backend valida la intención y conserva las preferencias respaldadas por el mensaje; el modelo no puede añadir tamaño, leche ni cantidades. El dominio calcula los importes y SQLite aporta disponibilidad actual. Los nombres aproximados requieren confirmación, y las restricciones desconocidas siguen pendientes entre turnos.

Para activar este recorrido en una copia local, añadir `--semantic` a la ejecución de `scripts/run_web.py`, usando el modelo base sin adaptador. Sin esa opción se conserva el recorrido anterior. La API no publica la clasificación interna ni el estado de preferencias.

El modelo base de 1B pasó 19 turnos de desarrollo y una comprobación adicional de 13 turnos. Tres consultas simultáneas respondieron en unos 29 segundos; la cancelación confirmó la recuperación de los cupos. Son recorridos concretos, sin garantía de tiempo para otras consultas o cargas. También se probaron estado, restricciones, API y separación de sesiones.

Estos recorridos no equivalen a la evaluación final de veinte escenarios ni a la aceptación humana del tono. El clasificador todavía necesita una evaluación amplia: la exactitud de los importes y del stock depende de las validaciones del backend. El ensayo con 3B no mostró una ventaja en los resultados finales de estos recorridos; las versiones del prompt y la carga del equipo cambiaron entre pruebas, por lo que no se presenta como una comparación controlada de modelos.
