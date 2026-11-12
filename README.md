# AlquilerApp (nombre provisional)

SaaS para propietarios de apartamentos en República Dominicana que automatiza el cobro de alquileres.
Contexto completo, reglas y alcance: [CLAUDE.md](CLAUDE.md). Marca: [docs/BRAND.md](docs/BRAND.md).

## Estructura

```
backend/   API FastAPI + Alembic + agentes (Python 3.12)
mobile/    App del propietario (Expo + Expo Router, TypeScript)
web/       Sitio público (Next.js, exportación estática)
infra/     Scripts de inicialización de Postgres para docker-compose
scripts/   gen-api.ps1 (regenera el cliente TypeScript desde el OpenAPI)
docs/      Guía de marca
```

## Requisitos

- Windows 10/11 con PowerShell
- Python 3.12 con [uv](https://docs.astral.sh/uv/), Node 24 (npm 11), Docker Desktop, Git
- En el teléfono: **Expo Go** (App Store / Google Play)

Recomendado: trabajar fuera de OneDrive (p. ej. `C:\dev\alquilerapp`), porque la sincronización de `node_modules` y `.venv` rompe instalaciones.

## 1. Base de datos y Redis

```powershell
cd C:\dev\alquilerapp
docker compose up -d
docker compose ps        # postgres y redis deben salir "healthy"
```

Crea las bases `alquilerapp` (desarrollo) y `alquilerapp_test` (tests). Para cambiar usuario o clave, copia `.env.example` a `.env` en la raíz **antes** del primer arranque.
Para usar una base en la nube, basta con cambiar `DATABASE_URL` y `REDIS_URL` en `backend/.env`.

## 2. Backend

Las dependencias se manejan con [uv](https://docs.astral.sh/uv/). `backend/uv.lock` fija la versión exacta de cada paquete para Windows y Linux; CI instala exactamente esas versiones.

```powershell
py -3.12 -m pip install uv          # una sola vez
cd C:\dev\alquilerapp\backend
uv sync                             # crea .venv e instala lo que fija uv.lock
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Salud: <http://localhost:8000/health> → `{"status":"ok","database":"ok","redis":"ok",...}`
- Documentación interactiva: <http://localhost:8000/docs>
- `--host 0.0.0.0` permite que el teléfono llegue al servidor por la red local.
- Agregar una dependencia: `uv add <paquete>` (o `uv add --dev <paquete>`). Actualiza `pyproject.toml` y `uv.lock`; commitea ambos.
- Actualizar versiones: `uv lock --upgrade` y luego `uv sync`.

Tests y lint:

```powershell
pytest
ruff check .
ruff format --check .
```

Los tests usan `TEST_DATABASE_URL` (base `alquilerapp_test`), la crean si no existe, aplican las migraciones y deshacen cada test con un rollback. Se niegan a correr si `TEST_DATABASE_URL` es igual a `DATABASE_URL`.

`MESSAGING_MODE=simulator` (por defecto) garantiza que no salga ningún mensaje real.

## 3. App móvil (Expo Go en tu teléfono)

```powershell
cd C:\dev\alquilerapp\mobile
npm install
Copy-Item .env.example .env.local
```

1. Busca la IP de tu PC en la red local: `ipconfig` → "Dirección IPv4" (p. ej. `192.168.1.50`).
2. En `mobile/.env.local` pon `EXPO_PUBLIC_API_URL=http://192.168.1.50:8000`.
3. Abre el puerto 8000 en el firewall de Windows (PowerShell **como administrador**, una sola vez):

   ```powershell
   New-NetFirewallRule -DisplayName "AlquilerApp API 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```

   Tu red Wi-Fi debe estar marcada como "Privada" en Windows.
4. Arranca el backend (paso 2) y luego Expo:

   ```powershell
   npx expo start
   ```

5. Escanea el QR con la cámara (iPhone) o con Expo Go (Android). Teléfono y PC en la misma Wi-Fi.
   La pantalla "Estado del servidor" debe mostrar Servidor, Base de datos y Redis como "Funciona".

Si el teléfono no conecta con Metro (red de oficina, aislamiento de clientes): `npx expo start --tunnel`. El túnel solo cubre Metro; el backend sigue necesitando la IP local o una URL pública.

Nada secreto va en variables `EXPO_PUBLIC_*`: se incluyen en el paquete de la app.

Calidad:

```powershell
npm run lint
npx tsc --noEmit
npm test
```

## 4. Sitio web

```powershell
cd C:\dev\alquilerapp\web
npm install
npm run dev              # http://localhost:3000
npm run build            # genera el sitio estático en web/out/
npx playwright install chromium   # una sola vez
npm run test:e2e         # requiere npm run build antes
npm run lint
npm run typecheck
```

## 5. Cliente TypeScript de la API

Los tipos de la app se generan desde el OpenAPI de FastAPI; no se escriben a mano.
Después de cambiar un endpoint o esquema del backend:

```powershell
.\scripts\gen-api.ps1
```

Regenera `backend/openapi.json` y `mobile/src/api/schema.d.ts`. Ambos se commitean; CI falla si están desactualizados.

## Problemas conocidos en Windows

**Smart App Control** (Seguridad de Windows → Control de aplicaciones y navegador) bloquea archivos compilados sin firma: módulos de SQLAlchemy y psycopg, los lanzadores `.exe` de `.venv\Scripts` y el Chromium de Playwright. El síntoma es `An Application Control policy has blocked this file` o `spawn UNKNOWN`. Mientras esté activo:

- Backend: `.\scripts\test-backend-docker.ps1` corre `uv sync --locked`, ruff y pytest dentro de un contenedor Linux (igual que CI), contra el Postgres y Redis de `docker compose`.
- Playwright: córrelo en la imagen oficial `mcr.microsoft.com/playwright` o déjalo a CI.

## CI

`.github/workflows/ci.yml` corre en cada push y pull request, con tres jobs en paralelo:

- **backend**: `uv sync --locked` (versiones exactas del lock), ruff (lint + formato), pytest contra Postgres 16 y Redis 7, chequeo del OpenAPI.
- **mobile**: lint, tsc, Jest, chequeo del cliente generado.
- **web**: lint, tsc, build estático, Playwright (Chromium).
