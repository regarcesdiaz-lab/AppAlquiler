# CLAUDE.md — AlquilerApp (nombre provisional)

## Qué es este proyecto
SaaS para propietarios de apartamentos en República Dominicana (1 a 20 unidades) que automatiza el cobro de alquileres.
Promesa del producto: **cobrar a tiempo sin perseguir a nadie**.

El proyecto tiene tres piezas:
1. **App móvil del propietario** (iPhone, Android y tablets), descargable desde App Store y Google Play.
2. **Sitio web profesional** para presentar el producto, explicar cómo funciona, mostrar planes, presentar al creador y llevar a la descarga.
3. **Backend** con la API, los agentes y el canal de WhatsApp.

Usuarios:
- El **propietario** paga la suscripción, descarga la app y, opcionalmente, maneja pendientes por WhatsApp.
- El **inquilino NO es usuario**: es un contacto. Nunca instala nada. Todo le llega por WhatsApp desde un **número central** de la plataforma.
- Los **agentes** trabajan en segundo plano y solo suben al propietario lo que requiere una decisión suya.

## Stack
- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, PostgreSQL 16, Redis + RQ (tareas), WeasyPrint (PDF)
- **App móvil**: React Native con Expo (SDK estable más reciente), TypeScript, Expo Router, TanStack Query para datos, Expo Notifications para push, EAS Build/Submit para publicar
- **Sitio web**: Next.js (App Router) con TypeScript, generado de forma estática donde sea posible, optimizado para SEO y velocidad
- **Tipos compartidos**: cliente de la API generado desde el OpenAPI de FastAPI (no escribir tipos a mano dos veces)
- **IA**: API de Anthropic (Claude). Visión para leer comprobantes. Modelo configurable por variable de entorno
- **WhatsApp**: Meta WhatsApp Cloud API (número central). En desarrollo se usa un **simulador local**
- **Correo**: proveedor transaccional (Resend, Postmark o SES) detrás de una interfaz
- **Archivos**: almacenamiento compatible con S3; en local, disco
- **Tests**: pytest (backend), Jest + React Native Testing Library (móvil), Playwright para flujos críticos del sitio
- **CI**: GitHub Actions (lint + tests en cada push)

## Estructura del repositorio (monorepo)
```
backend/
  app/
    core/        # config, db, seguridad, zona horaria
    domain/      # lógica pura: ledger, reglas, estados, urgencia (sin I/O)
    models/      # SQLAlchemy
    schemas/     # Pydantic
    api/         # routers (owner, webhooks, auth)
    agents/      # un módulo por agente
    channels/    # whatsapp (real + simulador), email, push
    services/    # pdf, storage, llm
    jobs/        # tareas programadas
  tests/
mobile/          # app Expo (propietario)
  app/           # rutas Expo Router
  src/components/  src/theme/  src/api/
web/             # sitio Next.js
.claude/skills/  # skill de diseño descargado
docs/
  BRAND.md       # guía de marca (colores, tipografía, estados, textos)
```

## Arquitectura (capas)
1. **Canales**: app móvil, WhatsApp (inquilino y propietario), correo, notificaciones push
2. **Agentes**: cobranza, conciliación, acuerdos, recibos, asistente del inquilino, asistente del propietario
3. **Servicios núcleo**: libro de cuentas, motor de reglas, personalización del asistente, línea de tiempo/evidencia, consentimiento, roles y portafolios
4. **Salidas**: Inicio (resumen + pendientes + lista de inquilinos), ficha, cobros, reportes

## Diseño
- Todo trabajo de interfaz (app y sitio) usa el **skill de diseño instalado** en `.claude/skills/` y la **guía de marca** `docs/BRAND.md`. Lee ambos antes de crear o modificar cualquier pantalla o página.
- **Prioridad**: en colores, tipografía, colores de estado, textos y tono manda `docs/BRAND.md`. En composición, jerarquía, espaciado fino y buenas prácticas de interfaz manda el skill. Si el skill sugiere un estilo que contradice la marca (colores audaces, tipografías decorativas, efectos llamativos), se sigue la marca.
- Los tokens de diseño (colores, tipografía, espacios) viven en un solo lugar por proyecto (`mobile/src/theme/`, `web/` con las mismas variables) y nunca se escriben valores sueltos en los componentes.

## Reglas no negociables
1. **Dinero**: nunca float. Usar `Decimal` o enteros en centavos. Moneda por defecto DOP.
2. **Zona horaria**: `America/Santo_Domingo` para toda lógica de fechas de cobro. Guardar timestamps en UTC.
3. **La IA nunca confirma un pago.** La conciliación deja el pago en `reported`; solo el propietario lo pasa a `confirmed` (en v1).
4. **La IA propone, las reglas deciden.** Toda acción de un agente pasa por el motor de reglas antes de ejecutarse.
5. **Línea de tiempo append-only**: cada mensaje, pago, promesa, recibo y avería genera un `TimelineEvent` inmutable.
6. **Consentimiento**: ningún mensaje iniciado por el negocio a un inquilino sin `ConsentRecord` vigente. Respetar `BAJA` de inmediato.
7. **Horario de contacto**: por defecto lunes a sábado 8:00–19:00, nunca domingos ni feriados de RD.
8. **Multi-tenant**: toda tabla de negocio lleva `organization_id` y toda consulta filtra por la organización autenticada. Con tests.
9. **Datos asociados al contrato/apartamento**, no a la persona.
10. **Envíos reales protegidos**: `MESSAGING_MODE=simulator|live`, por defecto `simulator`.
11. **LLM detrás de una interfaz** con un fake determinístico para tests; salidas validadas con Pydantic.
12. **Secretos** solo en variables de entorno; nunca en código, commits ni en el bundle de la app móvil.
13. **Requisitos de tiendas**: la app debe permitir eliminar la cuenta desde dentro, enlazar política de privacidad y términos, y pedir permisos (notificaciones, cámara, fotos) solo cuando se usan, explicando por qué.

## Idioma y tono
- Código y comentarios: inglés.
- Textos para usuarios (app, sitio, WhatsApp, correo, PDF): español dominicano neutro, cordial, frases cortas.

## Estados del inquilino (semáforo y orden por urgencia)
`overdue` → `pending_confirmation` → `promise` → `upcoming` → `paid` → `vacant`.

## Alcance v1 (no construir nada fuera de esto sin preguntar)
- Agentes: cobranza escalonada, conciliación, acuerdos de pago, recibos, asistente del inquilino, asistente del propietario.
- App móvil: Inicio, Propiedades, Cobros, Ajustes + ficha del inquilino + onboarding. En tablet, vista maestro-detalle (lista a la izquierda, ficha a la derecha).
- Sitio web: inicio, cómo funciona, planes, sobre el creador, descarga, preguntas frecuentes, contacto, privacidad, términos, eliminación de cuenta.
- Fuera de v1: contratos con firma, evaluación de inquilinos, depósitos/inspección, expediente legal, llamadas con IA, pasarela de pagos propia, módulo de venta, administradoras multiusuario, panel web del propietario.

## Forma de trabajar
- Antes de cada fase, proponer un plan corto y esperar aprobación.
- Escribir tests junto con el código.
- Preguntar antes de agregar una dependencia nueva.
- Al terminar una fase: correr tests, resumir lo hecho y lo pendiente, y sugerir el mensaje de commit.
