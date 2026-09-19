# Estado de evaluación del LLM

El prompt v1 fue aprobado por el usuario. Se hicieron pruebas parciales reales con llama3.2 y llama3.2:1b en CPU; mostraron salidas con señales no sustentadas y acciones incorrectas. Las respuestas se conservan en `evaluation_runs/`.

La ejecución se detuvo cuando el usuario pidió controlar la generación desde un notebook. No se completó una evaluación integral ni se declaró un modelo aceptado. No hay métricas finales de generalización ni aceptación.

Usar las celdas opcionales de `normalization_workflow.ipynb` para evaluar todas las notas, inspeccionar discrepancias, guardar nuevas versiones del prompt y ejecutar por separado holdout, seguridad y contexto. Las pruebas unitarias y la validación de datasets no sustituyen esa evaluación real.
