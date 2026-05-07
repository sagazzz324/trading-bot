"""
Laboratorio interno de estrategias.

Esta capa vive dentro de la app del bot, pero separada del runtime de trading.
Su trabajo es:
- generar candidatos
- validarlos con disciplina anti-overfitting
- promover solo estrategias aprobadas
- publicar perfiles consumibles por el bot
"""

