# Guion de la demo y checklist de criterios de aceptación

Cuatro escenas de ~1 minuto. Antes de empezar: panel → **Inicio** → fijar la *hora de la demo* en
**15:40** del día de hoy (así hay eventos pasados, uno en curso, merienda como próximo servicio y la
visita de Elena a 50 minutos). Si los datos quedaron sucios de un ensayo, **Reiniciar datos de la demo**
y volver a enrolar las caras.

## Preparación (una vez)

1. `./run.sh` (o `run.bat`). Abrir el panel en `http://127.0.0.1:8000/admin`.
2. **Residentes** → cada persona que hará de Marta, Roberto y Elena se enrola con la cámara del
   panel (2 o 3 fotos de frente). Antes, firmar el consentimiento de la demo; el perfil tiene la
   casilla "Consentimiento firmado".
3. Imprimir el **QR** de Roberto (sirve para la escena de identificación alternativa).
4. Abrir el tótem en pantalla completa: `chrome --kiosk --use-fake-ui-for-media-stream http://127.0.0.1:8000/`
   (el flag evita el diálogo de permisos de cámara/micrófono).
5. Probar la voz: panel → **Probar voz**. Si falla, revisar `FISH_API_KEY` en `.env`.

## Escena 1 · Marta · agenda personal y orientación del día

Marta se acerca. El tótem la reconoce en ~2 segundos y saluda por voz:
*"Hola Marta, buenas tardes…"*. En pantalla: tres tarjetas (Taller de arte **ahora**, Merienda 16:00,
Streaming 17:30).

- Marta: **"¿Qué tengo después de merendar?"** → "Después de merendar, a las cinco y media, tenés
  streaming en la sala de TV."
- Marta: **"¿Me repetís el horario del taller?"** → lo repite, sin enumerar lo de la mañana.
- Tocar **Agenda completa** → tarjeta con la lista del día.

| Criterio | Cómo se verifica |
|---|---|
| Saludo con nombre preferido | "Hola Marta" en pantalla y voz |
| Sólo actividades relevantes | No aparece nada donde Marta no esté inscripta (p. ej. nada personal de otros) |
| No enumera eventos finalizados | Gimnasia (10:00) no se menciona salvo que se pregunte |
| Modo "agenda general" para no identificados | Escena 4 |

## Escena 2 · Roberto · conversación contextual y cambio de tema

Roberto se acerca (o muestra su QR). El tótem no arranca con la agenda: abre con
*"Ayer tenías taller de streaming. ¿Cómo te fue?"*.

- Roberto: **"Bien, aprendí a poner la radio."** → una o dos preguntas de seguimiento, cortas.
- Roberto cambia de tema: **"¿Qué comemos hoy?"** → responde la merienda (próximo servicio) y deja
  el tema anterior.
- Al final, en panel → **Registro** se ve la opinión registrada ("le_gusto") si Roberto la dijo.

| Criterio | Cómo se verifica |
|---|---|
| Permite interrupciones y cambio de tema | Pregunta del menú en medio de la charla |
| No inventa actividades ni recuerdos | Preguntar "¿y el bingo de ayer?" → dice que no lo tiene anotado |
| Limita preguntas consecutivas y cierra | Tras dos preguntas ofrece "cuando quieras, acá estoy" |
| Intereses no derivados de datos sensibles | Perfil del panel: sólo intereses declarados |

## Escena 3 · Elena · visita familiar y momento significativo

Elena se acerca. El tótem le recuerda: *"Hoy a las cuatro y media viene Laura, su hija, a la sala
de estar de planta baja."* Tarjeta con cuenta regresiva.

- Elena: **"¿Cuánto falta?"** → "Faltan cincuenta minutos."
- Elena: **"¿Y el paseo del sábado?"** → lo informa como **a confirmar**, con punto de encuentro
  en recepción y aviso al equipo.
- Variante en vivo: desde el panel, cambiar la visita a **cancelado** y volver a preguntar → el tótem
  ya no la menciona (sin reiniciar nada).
- Variante privacidad: que una segunda persona se pare al lado → banner "Hay más personas cerca"; la
  visita se oculta hasta que Elena diga que quiere verla igual.
- Tocar **Necesito ayuda** → aviso en el panel (Inicio) con botón "Marcar atendido".

| Criterio | Cómo se verifica |
|---|---|
| Recordatorios personales no se muestran con terceros cerca | Banner + ocultamiento |
| Distingue confirmado de tentativo | Visita "confirmado", paseo "a confirmar" |
| Cambios de agenda sin intervención en el tótem | Cancelar desde el panel |
| Escalamiento a recepción | Botón o frase de malestar → pedido abierto en el panel |

## Escena 4 · persona no identificada

Alguien sin enrolar se acerca. A los pocos segundos: *"No te reconocí…"* con botones.
Tocar **Ver agenda del día** → saludo sin nombre, agenda y menú generales. Preguntar
**"¿Tengo visitas hoy?"** → explica que para eso hay que identificarse con tarjeta o PIN; no dice nombres.

| Criterio | Cómo se verifica |
|---|---|
| Fallback seguro sin nombres al azar | No propone identidades |
| Identificación alternativa | "Soy residente" + PIN, o QR |
| Menú sin reconocimiento facial | Funciona en modo general |

## Límites que conviene decir en voz alta

- El tótem **no diagnostica, no opina de medicación ni de dietas**; ante malestar deriva al equipo.
- Las caras se procesan **en el equipo local**; la voz se procesa en Fish Audio y el texto de la
  conversación en Anthropic. **No se guarda audio ni video**; sí texto de las interacciones.
- Los datos son ficticios. En un piloto real: consentimiento informado por residente, borrado de
  plantillas al retirar el consentimiento, y revisión de la licencia de los modelos faciales
  (InsightFace: uso no comercial).
