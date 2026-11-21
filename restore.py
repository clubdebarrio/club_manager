# restore.py
"""
Restaura un backup encriptado con age a Supabase.

Uso:
    python restore.py                         # lista backups disponibles
    python restore.py backups_supabase/clubmanager_2026-10-16_1954.dump.age

ADVERTENCIA: restaura SOBREESCRIBIENDO la base actual.
Pide confirmación antes de hacer nada.

Requiere las mismas variables que backup.py (.env.supabase):
    SUPABASE_DB_HOST, SUPABASE_DB_PORT, SUPABASE_DB_USER,
    SUPABASE_DB_PASSWORD, SUPABASE_DB_NAME
    BACKUP_DIR, AGE_BIN, BACKUP_AGE_KEY_FILE
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKUP_DIR = Path(os.environ.get('BACKUP_DIR', 'backups_supabase'))
AGE_BIN = os.environ.get('AGE_BIN', '').strip()
AGE_KEY_FILE = os.environ.get('BACKUP_AGE_KEY_FILE', 'backup_keys/backup_key.txt')

SUPABASE_DB_HOST = os.environ.get('SUPABASE_DB_HOST', '').strip()
SUPABASE_DB_PORT = os.environ.get('SUPABASE_DB_PORT', '5432').strip()
SUPABASE_DB_USER = os.environ.get('SUPABASE_DB_USER', '').strip()
SUPABASE_DB_PASSWORD = os.environ.get('SUPABASE_DB_PASSWORD', '')
SUPABASE_DB_NAME = os.environ.get('SUPABASE_DB_NAME', 'postgres').strip()

# Imagen de Postgres para el contenedor efímero
PG_DOCKER_IMAGE = 'postgres:17'


def _age_bin():
    return AGE_BIN or shutil.which('age') or 'age'


def listar_backups():
    if not BACKUP_DIR.exists():
        print(f'No existe el directorio {BACKUP_DIR}')
        return
    archivos = sorted(
        BACKUP_DIR.glob('clubmanager_*.dump.age'),
        reverse=True,
    )
    if not archivos:
        print(f'No hay backups disponibles en {BACKUP_DIR}')
        return
    print()
    print(f'Backups disponibles en {BACKUP_DIR}:')
    print('-' * 70)
    for a in archivos:
        tamano_mb = a.stat().st_size / (1024 * 1024)
        print(f'  {a.name:<50} {tamano_mb:>7.2f} MB')
    print('-' * 70)
    print()


def verificar_hash(ruta_backup):
    """Verifica el SHA256 del backup contra el .sha256 guardado."""
    ruta_hash = Path(str(ruta_backup) + '.sha256')
    if not ruta_hash.exists():
        print(f'⚠️  No se encontró {ruta_hash.name}, no se puede verificar '
              f'integridad.')
        return True

    with open(ruta_backup, 'rb') as f:
        sha_real = hashlib.sha256(f.read()).hexdigest()

    with open(ruta_hash, 'r') as f:
        sha_esperado = f.read().split()[0]

    if sha_real != sha_esperado:
        print(f'❌ SHA256 NO COINCIDE.')
        print(f'   Esperado: {sha_esperado}')
        print(f'   Real:     {sha_real}')
        return False

    print('✅ Integridad verificada (SHA256 OK).')
    return True


def desencriptar(ruta_backup):
    """Desencripta con age usando la clave privada."""
    if not os.path.exists(AGE_KEY_FILE):
        print(f'❌ No se encontró la clave privada en {AGE_KEY_FILE}.')
        sys.exit(1)

    age = _age_bin()
    result = subprocess.run(
        [age, '-d', '-i', AGE_KEY_FILE, str(ruta_backup)],
        capture_output=True,
        timeout=300,
    )
    if result.returncode != 0:
        print(f'❌ Error en age: {result.stderr.decode(errors="replace")}')
        sys.exit(1)
    return result.stdout


def restaurar(datos_dump):
    """Restaura el dump sobre Supabase usando pg_restore vía Docker."""
    # Validar config
    for var_name, var_val in [
        ('SUPABASE_DB_HOST', SUPABASE_DB_HOST),
        ('SUPABASE_DB_USER', SUPABASE_DB_USER),
        ('SUPABASE_DB_PASSWORD', SUPABASE_DB_PASSWORD),
        ('SUPABASE_DB_NAME', SUPABASE_DB_NAME),
    ]:
        if not var_val:
            print(f'❌ {var_name} no está configurada.')
            sys.exit(1)

    if not shutil.which('docker'):
        print('❌ No se encontró docker. Instalalo o agregalo al PATH.')
        sys.exit(1)

    # Escribir el dump en un archivo temporal
    with tempfile.NamedTemporaryFile(
        delete=False, suffix='.dump'
    ) as tmp:
        tmp.write(datos_dump)
        tmp_path = tmp.name

    try:
        env = os.environ.copy()
        env['PGPASSWORD'] = SUPABASE_DB_PASSWORD

        # Montamos el archivo temporal como volumen en /tmp del contenedor
        # (funciona en Windows porque Docker Desktop mapea el path)
        tmp_dir = os.path.dirname(tmp_path)
        tmp_name = os.path.basename(tmp_path)

        cmd = [
            'docker', 'run', '--rm', '-i',
            '--env', 'PGPASSWORD',
            '-v', f'{tmp_dir}:/tmp/host',   # ← Montar carpeta del host
            PG_DOCKER_IMAGE,
            'pg_restore',
            '-h', SUPABASE_DB_HOST,
            '-p', SUPABASE_DB_PORT,
            '-U', SUPABASE_DB_USER,
            '-d', SUPABASE_DB_NAME,
            '--clean', '--if-exists',
            '--no-owner', '--no-acl',
            f'/tmp/host/{tmp_name}',
        ]

        print('→ Restaurando (esto puede tardar)...')
        result = subprocess.run(
            cmd, capture_output=True, timeout=600, env=env
        )

        # pg_restore devuelve 1 si hay warnings, no es fatal
        if result.returncode > 1:
            print(result.stderr.decode(errors='replace'))
            sys.exit(1)

        if result.stderr:
            print(result.stderr.decode(errors='replace'))

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def main():
    if len(sys.argv) < 2:
        listar_backups()
        print('Uso: python restore.py backups_supabase/clubmanager_YYYY-MM-DD_HHMM.dump.age')
        return

    ruta_backup = Path(sys.argv[1])
    if not ruta_backup.exists():
        print(f'❌ No existe: {ruta_backup}')
        sys.exit(1)

    print()
    print('=' * 70)
    print('  RESTORE DE BACKUP')
    print('=' * 70)
    print(f'  Archivo: {ruta_backup}')
    print(f'  Destino: {SUPABASE_DB_HOST}/{SUPABASE_DB_NAME}')
    print(f'  Usuario: {SUPABASE_DB_USER}')
    print('=' * 70)
    print()
    print('⚠️  ADVERTENCIA: esto va a SOBREESCRIBIR la base actual de Supabase.')
    print('   Todos los datos actuales serán reemplazados por los del backup.')
    print()

    confirmacion = input('Escribí "CONFIRMO" para continuar: ')
    if confirmacion.strip() != 'CONFIRMO':
        print('Cancelado.')
        return

    if not verificar_hash(ruta_backup):
        print('❌ Backup corrupto. Abortando.')
        sys.exit(1)

    print('→ Desencriptando...')
    dump = desencriptar(ruta_backup)

    restaurar(dump)

    print()
    print('✅ Restore completado.')
    print('   Reiniciá Flask y verificá la base.')


if __name__ == '__main__':
    main()