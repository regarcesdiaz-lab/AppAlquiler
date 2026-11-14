# AlquilerApp (nombre provisional)

SaaS para propietarios de apartamentos en República Dominicana que automatiza el cobro de alquileres.
Contexto completo, reglas y alcance: [CLAUDE.md](CLAUDE.md). Marca: [docs/BRAND.md](docs/BRAND.md).

## Estructura

```
backend/   API FastAPI + Alembic + agentes + worker RQ (Python 3.12, corre en Docker)
mobile/    App del propietario: iOS, Android y panel web (Expo + Expo Router, TypeScript)
web/       Sitio público (Next.js, exportación estática)
infra/     Scripts de inicialización de Postgres para docker-compose
scripts/   test-backend-docker.ps1, test-web-e2e-docker.ps1, gen-api.ps1
docs/      Guía de marca
```

## Requisitos

- Windows 10/11 con PowerShell
- Docker Desktop, Node 24 (npm 11), Git
- En el teléfono: **Expo Go** (App Store / Google Play)

No hace falta Python en Windows: **el backend se ejecuta y se prueba siempre en Docker** (Smart App Control bloquea los módulos compilados de Python fuera de Docker; ver "Problemas conocidos").

Recomendado: trabajar fuera de OneDrive (p. ej. `C:\dev\alquilerapp`), porque la sincronización de `node_modules` rompe instalaciones.

## 1. Backend (Docker)

```powershell
cd C:\dev\alquilerapp
docker compose up -d
```

Levanta cuatro servicios:

| Servicio | Qué es | Puerto |
|---|---|---|
| `postgres` | PostgreSQL 16, con las bases `alquilerapp` y `alquilerapp_test` | 5432 |
| `redis` | Redis 7 | 6379 |
| `api` | FastAPI con recarga automática | 8000 |
| `worker` | Worker de RQ (tareas en segundo plano), se reinicia solo al cambiar el código | — |

- Salud: <http://localhost:8000/health> → `{"status":"ok","database":"ok","redis":"ok",...}`
- Documentación interactiva: <http://localhost:8000/docs>
- El código de `backend/` está montado dentro de los contenedores: guardas un archivo y la API se recarga en un par de segundos.
- Las dependencias salen de `backend/uv.lock` (versiones exactas, las mismas que usa CI).
- Configuración opcional: copia `backend/.env.example` a `backend/.env`. Las URLs de base de datos y Redis las pone `docker-compose.yml`; no hace falta tocarlas.
- Para cambiar usuario o clave de Postgres, copia `.env.example` a `.env` en la raíz **antes** del primer arranque.
- `MESSAGING_MODE=simulator` (por defecto) garantiza que no salga ningún mensaje real.

### Comandos del día a día

```powershell
# Levantar y apagar
docker compose up -d                 # levanta todo en segundo plano
docker compose ps                    # estado de los servicios
docker compose down                  # apaga todo (los datos de Postgres se conservan)

# Logs
docker compose logs -f api           # logs de la API en vivo (Ctrl+C para salir)
docker compose logs -f worker        # logs del worker
docker compose logs --tail 100 api   # las últimas 100 líneas

# Tests del backend (ruff + formato + pytest, igual que CI)
.\scripts\test-backend-docker.ps1
.\scripts\test-backend-docker.ps1 -k health          # solo pytest, con estos argumentos
.\scripts\test-backend-docker.ps1 tests/test_jobs.py -x

# Migraciones (Alembic)
docker compose exec api alembic upgrade head                           # aplicar
docker compose exec api alembic revision --autogenerate -m "mensaje"   # crear una nueva
docker compose exec api alembic downgrade -1                           # deshacer la última

# Cliente TypeScript de la API (después de cambiar endpoints o esquemas)
.\scripts\gen-api.ps1

# Otros
docker compose exec api ruff format .        # formatear el código
docker compose exec api python               # consola de Python dentro del contenedor
docker compose restart api worker            # reiniciar a mano
```

Los tests usan la base `alquilerapp_test`: la crean si no existe, aplican las migraciones y deshacen cada test con un rollback. Se niegan a correr si `TEST_DATABASE_URL` es igual a `DATABASE_URL`.

### Dependencias de Python

```powershell
docker compose run --rm api uv add <paquete>          # o: uv add --dev <paquete>
docker compose up -d --build                          # reconstruye la imagen con el nuevo uv.lock
```

`uv add` actualiza `backend/pyproject.toml` y `backend/uv.lock`; commitea ambos. Para subir versiones: `docker compose run --rm api uv lock --upgrade` y luego `docker compose up -d --build`.

## 2. App del propietario (`mobile/`)

Un solo código para tres destinos: iOS, Android y el panel web en el navegador.

```powershell
cd C:\dev\alquilerapp\mobile
npm install
Copy-Item .env.example .env.local
```

### Panel web (navegador de la PC)

```powershell
npm run web              # abre http://localhost:8081
```

Con el backend levantado, la pantalla "Estado del servidor" debe mostrar Servidor, Base de datos y Redis como "Funciona". En la misma PC sirve el valor por defecto `EXPO_PUBLIC_API_URL=http://localhost:8000`.

`npm run export:web` genera el panel como sitio estático (una sola página) en `mobile/dist/`.

### Teléfono (Expo Go)

1. Busca la IP de tu PC en la red local: `ipconfig` → "Dirección IPv4" (p. ej. `192.168.1.50`).
2. En `mobile/.env.local` pon `EXPO_PUBLIC_API_URL=http://192.168.1.50:8000`.
3. Abre el puerto 8000 en el firewall de Windows (PowerShell **como administrador**, una sola vez):

   ```powershell
   New-NetFirewallRule -DisplayName "AlquilerApp API 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```

   Tu red Wi-Fi debe estar marcada como "Privada" en Windows.
4. Con el backend levantado (`docker compose up -d`), arranca Expo:

   ```powershell
   npx expo start
   ```

5. Escanea el QR con la cámara (iPhone) o con Expo Go (Android). Teléfono y PC en la misma Wi-Fi.

Si el teléfono no conecta con Metro (red de oficina, aislamiento de clientes): `npx expo start --tunnel`. El túnel solo cubre Metro; el backend sigue necesitando la IP local o una URL pública.

Nada secreto va en variables `EXPO_PUBLIC_*`: se incluyen en el paquete de la app y en el panel web.

Calidad:

```powershell
npm run lint
npx tsc --noEmit
npm test
```

## 3. Sitio web (`web/`)

```powershell
cd C:\dev\alquilerapp\web
npm install
npm run dev              # http://localhost:3000
npm run build            # genera el sitio estático en web/out/
npm run lint
npm run typecheck
cd ..
.\scripts\test-web-e2e-docker.ps1    # Playwright en Docker; requiere npm run build antes
```

## 4. Cliente TypeScript de la API

Los tipos de la app se generan desde el OpenAPI de FastAPI; no se escriben a mano.
Después de cambiar un endpoint o esquema del backend:

```powershell
.\scripts\gen-api.ps1
```

Regenera `backend/openapi.json` (dentro de Docker) y `mobile/src/api/schema.d.ts`. Ambos se commitean; CI falla si están desactualizados.

## Problemas conocidos en Windows

**Smart App Control** (Seguridad de Windows → Control de aplicaciones y navegador) está activo y bloquea archivos compilados sin firma: módulos de SQLAlchemy y psycopg, los lanzadores `.exe` de un `.venv` y el Chromium de Playwright. El síntoma es `An Application Control policy has blocked this file` o `spawn UNKNOWN`. Por eso:

- El backend corre y se prueba solo en Docker (sección 1). No crees un `.venv` en Windows.
- Playwright corre en Docker con `.\scripts\test-web-e2e-docker.ps1`, o en CI.

## CI

`.github/workflows/ci.yml` corre en cada push y pull request, con cuatro jobs en paralelo:

- **backend**: `uv sync --locked` (versiones exactas del lock), ruff (lint + formato), pytest contra Postgres 16 y Redis 7, chequeo del OpenAPI.
- **docker**: `docker compose up` completo y `/health` respondiendo en `localhost:8000`.
- **mobile**: lint, tsc, Jest, chequeo del cliente generado, exportación del panel web.
- **web**: lint, tsc, build estático, Playwright (Chromium).
