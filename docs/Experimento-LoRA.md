# Experimento LoRA: protocolo y resultado

Se ajustó Llama 3.2 1B Instruct con Transformers y PEFT en una GPU Tesla T4 de Colab. El objetivo fue practicar respuestas breves, naturales y prudentes en español. Los datos de precios y existencia siguen siendo responsabilidad del catálogo y del sistema, no del conocimiento memorizado por el modelo.

## Datos y entrenamiento

700 conversaciones sintéticas: 560 de entrenamiento y 140 de validación. Las familias de escenarios se separaron entre particiones; hay similitud lingüística entre preguntas, por lo que esta división no demuestra por sí sola generalización a conversaciones nuevas. Los nombres y precios del corpus son ficticios y no representan la carta publicada. No se utilizaron conversaciones reales de clientes.

FP16, LoRA r=8, alpha=16, dropout=0.05 sobre q_proj/v_proj; dos épocas, batch 1, acumulación 8, tasa de aprendizaje 0.0001 y semilla 42. Solo la última respuesta del asistente se supervisa; el resto de tokens se enmascara. No se truncan ejemplos silenciosamente. El notebook reproduce el protocolo; hardware y versiones pueden cambiar los resultados exactos.

## Evidencia observada

140 pasos en 260.34 segundos. Pérdida de validación de 1.1035 a 0.9046 entre épocas. Los 64 tensores del adaptador se recargaron idénticos y finitos. Los hashes de datos y medidas están en `training/results.json`.

**La menor pérdida no demostró mejor calidad conversacional.** En una comparación de 24 consultas iguales, el adaptador anterior de 600 ejemplos y el de 700 obtuvieron ambos 7 respuestas completamente correctas. Esta comparación es entre dos adaptadores, no entre el modelo base y LoRA. No equivale a la evaluación de aceptación del producto. Se conservaron errores de disponibilidad y cálculo: no se aprobó publicar sus respuestas libres como información fiable.

## Decisión técnica

Separar interpretación del lenguaje y respuesta factual. La demo usa **Llama 3.2 1B base sin LoRA** para clasificar la intención; el backend conserva las preferencias expresadas por el usuario, consulta la existencia y calcula los precios. El experimento LoRA se entrega como evidencia reproducible de entrenamiento y evaluación, no como una mejora de calidad demostrada. La revisión cualitativa favorable de Kevin corresponde a las respuestas finales de esta demo; no acredita el tono ni la exactitud de los adaptadores LoRA.

## Reproducción

Abrir `training/LoRA-Colab.ipynb` en Colab con GPU. Añadir `HF_TOKEN` a Secretos, con acceso al notebook y al modelo oficial. Subir los dos JSONL de esta carpeta y ejecutar las celdas en orden. El entrenamiento requiere cambiar expresamente `RUN_TRAINING` a `True`; por defecto solo se prepara el experimento. Los pesos de Meta no se incluyen en este repositorio y conservan su licencia original.
