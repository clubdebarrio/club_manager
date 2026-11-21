# Guía de emergencia: restaurar un backup

> **Cuándo usar esto**: cuando la base de datos se corrompió, alguien
> borró datos por error, o hay que volver a un estado anterior.

## Antes de empezar

1. **Parar la aplicación** (Ctrl+C en el server de Flask).
2. **Conseguir la clave privada de age** (`backup_keys/backup_key.txt`).
3. **Anotar la fecha/hora a la que querés volver.**

## Paso 1 — Ver backups disponibles

```powershell
python restore.py
```

Salida esperada:

```
Backups disponibles en backups:
----------------------------------------------------------------------
  clubmanager_2026-10-05_0300.dump.age                   8.42 MB
  clubmanager_2026-10-04_0300.dump.age                   8.40 MB
  ...
----------------------------------------------------------------------
```

## Paso 2 — Restaurar

```powershell
python restore.py backups/clubmanager_2026-10-05_0300.dump.age
```

Te va a pedir confirmación: escribí **`CONFIRMO`** (con mayúsculas).

El script:
1. Verifica el SHA256 (integridad).
2. Desencripta con age (usando tu clave privada).
3. Restaura con `pg_restore` sobre la base actual.

## Paso 3 — Verificar

1. Levantá la app:

```powershell
flask run
```

2. Logueate y verificá:
   - Los últimos socios que había antes del backup.
   - Los movimientos de caja más recientes.
   - Los asientos contables.

## Paso 4 — Si algo salió mal

1. **NO borres el backup que usaste**, puede servir para reintentar.
2. Probá con un backup más viejo:

```powershell
python restore.py backups/clubmanager_2026-10-04_0300.dump.age
```

## Casos de emergencia

### "Se rompió el Docker"

```powershell
docker start clubmanager-postgres
# Esperar 5s
python restore.py backups/clubmanager_XXXXXXXX_XXXX.dump.age
```

### "Perdí el contenedor pero tengo los backups"

```powershell
docker run --name clubmanager-postgres \
    -e POSTGRES_USER=clubmanager \
    -e POSTGRES_PASSWORD=dev1234 \
    -e POSTGRES_DB=clubmanager \
    -p 5432:5432 \
    -v clubmanager_pgdata:/var/lib/postgresql/data \
    -d postgres:16

# Esperar a que levante
docker exec -it clubmanager-postgres pg_isready

# Restaurar
python restore.py backups/clubmanager_XXXXXXXX_XXXX.dump.age
```

### "Perdí la clave privada de age"

**Los backups son irrecuperables.** No hay forma de desencriptarlos sin la clave.
Si esto pasa, el único camino es restaurar de un backup que esté sin encriptar (si tenés alguno) o empezar de cero.

**Por eso es CRÍTICO tener la clave guardada en al menos 2 lugares fuera del server.**