# CLAUDE.md — AlquilerApp (nombre provisional)

## Qué es este proyecto
SaaS para propietarios de apartamentos en República Dominicana (1 a 20 unidades) que automatiza el cobro de alquileres.
Promesa del producto: **cobrar a tiempo sin perseguir a nadie**.

- El **propietario** paga la suscripción y usa la app (web móvil/PWA) y, opcionalmente, WhatsApp.
- El **inquilino NO es usuario**: es un contacto. Nunca instala nada. Todo le llega por WhatsApp desde un **número central** de la plataforma.
- Los **agentes** trabajan en segundo plano y solo suben al propietario lo que requiere una decisión suya.

## Stack
- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2
- Base de datos: PostgreSQL 16
- Tareas programadas y colas: Redis + RQ (o APScheduler para el planificador diario)
- Frontend del propietario: Reflex (Python), mobile-first, responsive
- IA: API de Anthropic (Claude). Visión para leer comprobantes. Modelo configurable por variable de entorno
- WhatsApp: Meta WhatsApp Cloud API (número central). En desarrollo se usa un **simulador local**
- Correo: proveedor transaccional (Resend, Postmark o SES) detrás de una interfaz
- PDF: WeasyPrint
- Archivos (comprobantes, documentos): almacenamiento compatible con S3; en local, disco
- Tests: pytest. Infra local: docker-compose (postgres, redis)

## Arquitectura (capas)
1. **Canales**: app del propietario, WhatsApp (inquilino y propietario), correo
2. **Agentes**: cobranza, conciliación, acuerdos, recibos, asistente del inquilino, asistente del propietario
3. **Servicios núcleo**: libro de cuentas, motor de reglas, personalización del asistente, línea de tiempo/evidencia, consentimiento, roles y portafolios
4. **Salidas**: Inicio (resumen + pendientes + lista de inquilinos), ficha, cobros, reportes

Estructura de carpetas sugerida:
```
backend/
  app/
    core/        # config, db, seguridad, tiempo/zona horaria
    domain/      # lógica pura: ledger, reglas, estados, urgencia (sin I/O)
    models/      # SQLAlchemy
    schemas/     # Pydantic
    api/         # routers FastAPI (owner, webhooks)
    agents/      # un módulo por agente
    channels/    # whatsapp (real + simulador), email
    services/    # pdf, storage, llm
    jobs/        # tareas programadas
  tests/
frontend/        # Reflex
docs/
```

## Reglas no negociables
1. **Dinero**: nunca float. Usar `Decimal` o enteros en centavos. Moneda por defecto DOP.
2. **Zona horaria**: `America/Santo_Domingo` para toda lógica de fechas de cobro. Guardar timestamps en UTC.
3. **La IA nunca confirma un pago.** La conciliación deja el pago en estado `reported`; solo el propietario lo pasa a `confirmed` (en v1).
4. **La IA propone, las reglas deciden.** Toda acción de un agente pasa por el motor de reglas (horarios, límites de prórroga, frecuencia) antes de ejecutarse.
5. **Línea de tiempo append-only**: cada mensaje, pago, promesa, recibo y avería genera un `TimelineEvent` inmutable con timestamp. Nunca se edita ni borra.
6. **Consentimiento**: no se envía ningún mensaje iniciado por el negocio a un inquilino sin `ConsentRecord` vigente. Respetar `BAJA` (opt-out) de inmediato.
7. **Horario de contacto**: ningún mensaje ni llamada fuera del horario configurado (por defecto lunes a sábado 8:00–19:00, nunca domingos ni feriados de RD).
8. **Multi-tenant**: toda tabla de negocio lleva `organization_id`. Toda consulta filtra por la organización del usuario autenticado. Escribir tests que lo verifiquen.
9. **Datos asociados al contrato/apartamento**, no a la persona: si el inquilino se muda, el historial del apartamento permanece.
10. **Envíos reales protegidos**: variable `MESSAGING_MODE=simulator|live`. Por defecto `simulator`. Nunca enviar a números reales en desarrollo o tests.
11. **LLM detrás de una interfaz** (`services/llm.py`) con un fake determinístico para tests. Las salidas del LLM se validan con esquemas Pydantic.
12. **Secretos** solo en variables de entorno. Nunca en código ni en commits.

## Idioma y tono
- Código, nombres de variables y comentarios: inglés.
- Textos para usuarios (UI, WhatsApp, correo, PDF): español dominicano neutro, cordial, frases cortas. Tuteo con el inquilino salvo que el propietario elija tono formal.

## Estados del inquilino (para el semáforo y el orden por urgencia)
Orden de urgencia en Inicio: `overdue` (atrasado) → `pending_confirmation` (reportó pago) → `promise` (en pausa por promesa) → `upcoming` (por vencer) → `paid` (al día) → `vacant` (apartamento vacío).

## Alcance v1 (no construir nada fuera de esto sin preguntar)
Agentes: cobranza escalonada, conciliación, acuerdos de pago, recibos, asistente del inquilino (WhatsApp), asistente del propietario (WhatsApp).
Pantallas: Inicio, Propiedades, Cobros, Ajustes + ficha del inquilino.
Fuera de v1: contratos con firma, evaluación de inquilinos, depósitos/inspección, expediente legal, llamadas con IA, pasarela de pagos propia, módulo de venta, administradoras multiusuario.

## Forma de trabajar
- Antes de cada fase, proponer un plan corto y esperar aprobación.
- Escribir tests junto con el código; la lógica de `domain/` debe tener cobertura alta.
- Preguntar antes de agregar una dependencia nueva.
- Al terminar una fase: correr tests, resumir lo hecho y lo pendiente, y sugerir el mensaje de commit.
