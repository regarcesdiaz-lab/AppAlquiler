# Guía de marca de AlquilerApp

Referencia de marca del proyecto. Se usa junto con el skill de diseño instalado en Claude Code.
**Regla de prioridad:** en colores, tipografía, colores de estado, textos y tono manda esta guía; en composición, jerarquía y buenas prácticas de interfaz manda el skill de diseño.

## Personalidad
La app maneja dinero y relaciones entre personas. Debe transmitir **confianza, calma y claridad**. Se parece más a una buena app bancaria que a una red social.
- Seria pero cálida. Nunca infantil ni recargada.
- El propietario típico tiene entre 40 y 70 años: textos grandes, botones amplios y nada escondido detrás de gestos difíciles.
- Cada pantalla responde una sola pregunta y tiene como máximo una acción principal.

## Lo que NO hacer
- Nada de gradientes morados, efectos de brillo, glassmorphism ni ilustraciones genéricas de "startup de IA".
- Nada de emojis en la interfaz.
- Nunca comunicar un estado solo con color: siempre acompañarlo de texto ("Atrasado", "Al día").
- No llenar las pantallas con métricas: si un número no lleva a una decisión, no va.
- No usar textos en inglés en la interfaz.

## Color (tokens)
Definir como tokens en `mobile/src/theme/colors.ts` y como variables CSS en `web/`. Soportar modo claro y oscuro.

| Token | Claro | Oscuro | Uso |
|---|---|---|---|
| `brand` | #0E5E6F | #5FB3C2 | Botón principal, enlaces, marca |
| `brandSoft` | #E6F2F4 | #0F3540 | Fondos de marca suaves |
| `bg` | #F7F7F5 | #121414 | Fondo de pantalla |
| `surface` | #FFFFFF | #1C1F1F | Tarjetas |
| `border` | #E4E4E0 | #2E3333 | Bordes finos |
| `text` | #1A1D1D | #F2F2F0 | Texto principal |
| `textMuted` | #5E6666 | #A3ABAB | Texto secundario |

Colores de estado (siempre con texto):

| Estado | Texto claro | Fondo claro | Texto oscuro | Fondo oscuro | Etiqueta |
|---|---|---|---|---|---|
| `overdue` | #B42318 | #FEF3F2 | #FDA29B | #55160C | Atrasado / Debe RD$X |
| `pending_confirmation` | #B54708 | #FFFAEB | #FEC84B | #4E1D09 | Por confirmar |
| `promise` | #475467 | #F2F4F7 | #D0D5DD | #344054 | En pausa · promesa día X |
| `upcoming` | #0E5E6F | #E6F2F4 | #5FB3C2 | #0F3540 | Vence el día X |
| `paid` | #067647 | #ECFDF3 | #75E0A7 | #053321 | Al día |
| `vacant` | #5E6666 | transparente con borde #E4E4E0 | #A3ABAB | transparente con borde #2E3333 | Vacío |

Contraste mínimo WCAG AA en todo texto.

## Tipografía
- Fuente: Inter (expo-google-fonts en móvil, next/font en web). Dos pesos: 400 y 600.
- Escala móvil: título de pantalla 28, título de sección 20, cuerpo 17, secundario 15, mínimo 13 (solo metadatos).
- Respetar el tamaño de letra del sistema (Dynamic Type / escalado de fuente); probar al 130 %.
- Montos: cifras tabulares, formato `RD$25,000` (sin decimales si son ,00). Fechas cortas: `1 nov`, largas: `viernes 1 de noviembre`.

## Espacio, forma y toque
- Escala de espacios: 4, 8, 12, 16, 24, 32, 48.
- Radios: 8 controles, 12 tarjetas, 999 chips.
- Área táctil mínima 48×48. Separación mínima entre botones de 8.
- Sombras casi inexistentes; separar con bordes finos y espacio.

## Componentes base (crear una vez, reutilizar siempre)
- `ScreenHeader`: título grande + acción opcional a la derecha.
- `MonthSummary`: monto cobrado vs esperado, barra de progreso y texto "de RD$X".
- `InboxBanner`: "N pendientes por resolver" que abre la bandeja.
- `StatusChip`: color + texto del estado.
- `TenantRow`: nombre, "Apto X · detalle", chip a la derecha. Toda la fila es tocable.
- `PendingCard`: descripción + hasta dos botones (secundario y principal).
- `StatTile`: etiqueta pequeña + número (para la ficha).
- `Timeline`: lista vertical de eventos con fecha corta.
- `PrimaryButton` (uno por pantalla), `SecondaryButton`, `TextButton`.
- `EmptyState`: título que invita, una línea de explicación y una acción.
- `FilterChips`: filtros rápidos horizontales.

## Diseño adaptable (teléfono y tablet)
- Teléfono (< 600 de ancho): navegación inferior con 4 pestañas; la ficha se abre como pantalla nueva.
- Tablet (≥ 600): navegación lateral; Inicio y Propiedades en **maestro-detalle** (lista a la izquierda ~40 %, ficha a la derecha). Ancho máximo de contenido de lectura ~720.
- Probar siempre en: iPhone pequeño, iPhone grande, Android mediano, iPad vertical y horizontal.

## Textos (microcopy)
- Botones con verbo: "Confirmar pago", "Agregar apartamento". Nunca "OK" ni "Enviar".
- Errores: qué pasó y qué hacer. "No pudimos conectar. Revisa tu internet e inténtalo de nuevo."
- Estados vacíos como invitación: "Agrega tu primer apartamento" + "Toma 2 minutos".
- Tratar al propietario de "tú" salvo que se configure lo contrario.

## Sitio web
- Mismos tokens y tipografía que la app: el sitio y la app deben sentirse como la misma marca.
- Estilo editorial y sobrio: mucho espacio, titulares claros, capturas reales de la app en marcos de dispositivo (teléfono y tablet).
- Estructura de la página de inicio: titular con la promesa → subtítulo → botones de descarga (App Store y Google Play) → captura de la app → 3 beneficios → cómo funciona en 3 pasos → lo que vive el inquilino (WhatsApp) → planes → preguntas frecuentes → llamada final a descargar.
- Página "Sobre el creador": foto profesional, historia breve, experiencia y por qué creó la app. Tono personal, en primera persona.
- Rendimiento: imágenes optimizadas, Lighthouse ≥ 90 en todo.

## Accesibilidad
- Etiquetas de accesibilidad en todo elemento tocable (VoiceOver / TalkBack).
- Orden de lectura lógico.
- Animaciones cortas y respetando "reducir movimiento".
