# backup.py
"""
Backup diario de Supabase a Backblaze B2, con encriptación age.

Uso:
    python backup.py

Requiere en .env.supabase:
    SUPABASE_DB_HOST, SUPABASE_DB_PORT, SUPABASE_DB_USER,
    SUPABASE_DB_PASSWORD, SUPABASE_DB_NAME
    BACKUP_DIR, BACKUP_AGE_PUBLIC_KEY, AGE_BIN
    B2_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_NAME, B2_ENDPOINT

Estrategia:
    1. pg_dump -Fc (vía Docker efímero)
    2. Encriptar con age (clave pública)
    3. Guardar local + SHA256
    4. Subir a B2
    5. Retención: 7 diarios + 4 semanales + 12 mensuales
"""
import hashlib
import os
import shutil
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# --- Configuración ---
BACKUP_DIR = Path(os.environ.get('BACKUP_DIR', 'backups_supabase'))
AGE_PUBLIC_KEY = os.environ.get('BACKUP_AGE_PUBLIC_KEY', '').strip()
AGE_BIN = os.environ.get('AGE_BIN', '').strip()

SUPABASE_DB_HOST = os.environ.get('SUPABASE_DB_HOST', '').strip()
SUPABASE_DB_PORT = os.environ.get('SUPABASE_DB_PORT', '5432').strip()
SUPABASE_DB_USER = os.environ.get('SUPABASE_DB_USER', '').strip()
SUPABASE_DB_PASSWORD = os.environ.get('SUPABASE_DB_PASSWORD', '')
# El "or 'postgres'" asegura que si viene vacío, use 'postgres'
SUPABASE_DB_NAME = os.environ.get('SUPABASE_DB_NAME', '').strip() or 'postgres' 

B2_KEY_ID = os.environ.get('B2_KEY_ID', '').strip()
B2_APPLICATION_KEY = os.environ.get('B2_APPLICATION_KEY', '').strip()
B2_BUCKET_NAME = os.environ.get('B2_BUCKET_NAME', '').strip()
B2_ENDPOINT = os.environ.get('B2_ENDPOINT', '').strip()


# ============================================================
# Helpers
# ============================================================

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    linea = f'[{ts}] {msg}'
    print(linea)
    BACKUP_DIR.mkdir(exist_ok=True)
    with open(BACKUP_DIR / 'backup.log', 'a', encoding='utf-8') as f:
        f.write(linea + '\n')


def chequear_requisitos():
    errores = []

    # age
    if not AGE_PUBLIC_KEY or not AGE_PUBLIC_KEY.startswith('age1'):
        errores.append(
            'BACKUP_AGE_PUBLIC_KEY no está configurada o no empieza con "age1".'
        )
    age_path = AGE_BIN or shutil.which('age')
    if not age_path:
        errores.append(
            'No se encontró "age". Configurá AGE_BIN en el .env con la ruta '
            'absoluta al age.exe.'
        )

    # Conexión a Supabase
    for var_name, var_val in [
        ('SUPABASE_DB_HOST', SUPABASE_DB_HOST),
        ('SUPABASE_DB_USER', SUPABASE_DB_USER),
        ('SUPABASE_DB_PASSWORD', SUPABASE_DB_PASSWORD),
        ('SUPABASE_DB_NAME', SUPABASE_DB_NAME),
    ]:
        if not var_val:
            errores.append(f'{var_name} no está configurada.')

    # Docker (necesario para pg_dump efímero)
    if not shutil.which('docker'):
        errores.append(
            'No se encontró "docker". Es necesario para ejecutar pg_dump.'
        )

    # B2
    for var_name, var_val in [
        ('B2_KEY_ID', B2_KEY_ID),
        ('B2_APPLICATION_KEY', B2_APPLICATION_KEY),
        ('B2_BUCKET_NAME', B2_BUCKET_NAME),
        ('B2_ENDPOINT', B2_ENDPOINT),
    ]:
        if not var_val:
            errores.append(f'{var_name} no está configurada.')

    if errores:
        for e in errores:
            log(f'❌ {e}')
        sys.exit(1)


# ============================================================
# 1. pg_dump
# ============================================================

def ejecutar_pg_dump():
    log('Ejecutando pg_dump (local)...')

    env = os.environ.copy()
    env['PGPASSWORD'] = SUPABASE_DB_PASSWORD

    cmd = [
        'pg_dump',
        '-h', SUPABASE_DB_HOST,
        '-p', SUPABASE_DB_PORT,
        '-U', SUPABASE_DB_USER,
        '-d', SUPABASE_DB_NAME,
        '--no-owner', '--no-acl',
        '-Fc',
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=300, env=env)
    if result.returncode != 0:
        err = result.stderr.decode(errors='replace')
        log(f'❌ Error en pg_dump: {err}')
        sys.exit(1)

    return result.stdout


# ============================================================
# 2. Encriptar con age
# ============================================================

def encriptar_con_age(datos_bytes):
    log('Encriptando con age...')

    age_bin = AGE_BIN or shutil.which('age')
    if not age_bin:
        log('❌ No se encontró age.')
        sys.exit(1)

    result = subprocess.run(
        [age_bin, '-r', AGE_PUBLIC_KEY, '-o', '-'],
        input=datos_bytes,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        err = result.stderr.decode(errors='replace')
        log(f'❌ Error en age: {err}')
        sys.exit(1)

    return result.stdout


# ============================================================
# 3. Guardar local + SHA256
# ============================================================

def escribir_backup(datos_encriptados, timestamp):
    BACKUP_DIR.mkdir(exist_ok=True)

    nombre_base = f'clubmanager_{timestamp}.dump.age'
    ruta_backup = BACKUP_DIR / nombre_base
    ruta_hash = BACKUP_DIR / f'{nombre_base}.sha256'

    with open(ruta_backup, 'wb') as f:
        f.write(datos_encriptados)

    sha256 = hashlib.sha256(datos_encriptados).hexdigest()
    with open(ruta_hash, 'w') as f:
        f.write(f'{sha256}  {nombre_base}\n')

    tamano_mb = len(datos_encriptados) / (1024 * 1024)
    log(f'✅ Backup local: {nombre_base} ({tamano_mb:.2f} MB)')
    log(f'   SHA256: {sha256}')

    return ruta_backup, sha256


# ============================================================
# 4. Subir a B2
# ============================================================

def _cliente_b2():
    endpoint_url = B2_ENDPOINT
    if not endpoint_url.startswith('http'):
        endpoint_url = f'https://{endpoint_url}'
    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=B2_KEY_ID,
        aws_secret_access_key=B2_APPLICATION_KEY,
        config=Config(signature_version='s3v4'),
    )


def subir_a_b2(ruta_archivo_local, nombre_archivo):
    log(f'☁️  Subiendo {nombre_archivo} a B2...')

    s3 = _cliente_b2()
    try:
        s3.upload_file(
            str(ruta_archivo_local), B2_BUCKET_NAME, nombre_archivo
        )
        log(f'✅ {nombre_archivo} subido a B2.')
    except ClientError as e:
        log(f'❌ Error subiendo a B2: {e}')
        sys.exit(1)
    except Exception as e:
        log(f'❌ Error inesperado subiendo a B2: {e}')
        sys.exit(1)


# ============================================================
# 5. Retención local
# ============================================================

def _fecha_de_nombre(nombre_archivo):
    """Extrae la fecha de 'clubmanager_YYYY-MM-DD_HHMM.dump.age'."""
    try:
        partes = nombre_archivo.split('_')
        if len(partes) < 3:
            return None
        return datetime.strptime(partes[1], '%Y-%m-%d').date()
    except (ValueError, IndexError):
        return None


def _calcular_a_mantener(fechas_disponibles):
    """
    Devuelve el conjunto de fechas a conservar según la política:
      - 7 diarios
      - 4 semanales (últimos domingos)
      - 12 mensuales (días 1)
    """
    hoy = date.today()
    a_mantener = set()

    # 7 diarios
    for i in range(7):
        d = hoy - timedelta(days=i)
        if d in fechas_disponibles:
            a_mantener.add(d)

    # 4 domingos
    domingos = []
    d = hoy
    while len(domingos) < 4 and (hoy - d).days < 120:
        if d.weekday() == 6:
            domingos.append(d)
        d -= timedelta(days=1)
    for d in domingos:
        if d in fechas_disponibles:
            a_mantener.add(d)

    # 12 primeros de mes
    d = hoy.replace(day=1)
    for _ in range(12):
        if d in fechas_disponibles:
            a_mantener.add(d)
        if d.month == 1:
            d = d.replace(year=d.year - 1, month=12)
        else:
            d = d.replace(month=d.month - 1)

    return a_mantener


def aplicar_retencion_local():
    archivos = [
        f for f in BACKUP_DIR.glob('clubmanager_*.dump.age') if f.is_file()
    ]
    if not archivos:
        return

    por_fecha = {}
    for a in archivos:
        d = _fecha_de_nombre(a.name)
        if d:
            por_fecha[d] = a

    a_mantener = _calcular_a_mantener(set(por_fecha.keys()))

    borrados = 0
    for fecha, a in por_fecha.items():
        if fecha not in a_mantener:
            try:
                a.unlink()
                sha = BACKUP_DIR / f'{a.name}.sha256'
                if sha.exists():
                    sha.unlink()
                borrados += 1
                log(f'🗑️  Local eliminado: {a.name}')
            except OSError as e:
                log(f'⚠️  No se pudo borrar {a.name}: {e}')

    log(
        f'Retención local: {len(a_mantener)} conservados, '
        f'{borrados} eliminados.'
    )


# ============================================================
# 6. Retención en B2
# ============================================================

def aplicar_retencion_b2():
    log('Aplicando retención en B2...')
    s3 = _cliente_b2()

    try:
        resp = s3.list_objects_v2(Bucket=B2_BUCKET_NAME)
    except ClientError as e:
        log(f'⚠️  No se pudo listar B2: {e}')
        return

    objetos = resp.get('Contents', [])
    if not objetos:
        log('   (bucket vacío)')
        return

    por_fecha = {}
    for obj in objetos:
        key = obj['Key']
        if not key.endswith('.dump.age'):
            continue
        d = _fecha_de_nombre(key.split('/')[-1])
        if d:
            por_fecha[d] = key

    a_mantener = _calcular_a_mantener(set(por_fecha.keys()))

    borrados = 0
    for fecha, key in por_fecha.items():
        if fecha not in a_mantener:
            try:
                s3.delete_object(Bucket=B2_BUCKET_NAME, Key=key)
                borrados += 1
                log(f'🗑️  B2 eliminado: {key}')
            except ClientError as e:
                log(f'⚠️  No se pudo borrar {key} en B2: {e}')

    log(
        f'Retención B2: {len(a_mantener)} conservados, '
        f'{borrados} eliminados.'
    )


# ============================================================
# Main
# ============================================================

def main():
    log('=' * 60)
    log('INICIO BACKUP')
    log('=' * 60)

    chequear_requisitos()

    timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')

    dump = ejecutar_pg_dump()
    if not dump:
        log('❌ El dump está vacío. Abortando.')
        sys.exit(1)

    encriptado = encriptar_con_age(dump)
    ruta_backup, sha256 = escribir_backup(encriptado, timestamp)

    subir_a_b2(ruta_backup, ruta_backup.name)

    aplicar_retencion_local()
    aplicar_retencion_b2()

    log('BACKUP FINALIZADO OK')
    log('=' * 60)


if __name__ == '__main__':
    main()