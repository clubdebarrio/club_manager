# deploy_supabase.py
"""
Deploy inicial del schema a Supabase.

Uso:
    python deploy_supabase.py

Hace:
    1. Valida conexión a Supabase.
    2. Verifica extensiones (unaccent, pg_trgm).
    3. Corre las migraciones.
    4. Reporta el estado final.
"""
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values


ENV_FILE = '.env.supabase'


def cargar_env():
    if not Path(ENV_FILE).exists():
        print(f'❌ No existe {ENV_FILE}.')
        print(f'   Copiá los valores del dashboard de Supabase y '
              f'guardalos ahí.')
        sys.exit(1)

    config = dotenv_values(ENV_FILE)
    if not config.get('DATABASE_URL'):
        print(f'❌ Falta DATABASE_URL en {ENV_FILE}.')
        sys.exit(1)

    return config


def aplicar_env(config):
    """Exporta las variables al entorno del proceso."""
    for k, v in config.items():
        if v:
            os.environ[k] = v


def validar_conexion():
    """Prueba la conexión y verifica extensiones."""
    print('→ Validando conexión a Supabase...')
    from app import create_app, db
    from sqlalchemy import text

    app = create_app()
    with app.app_context():
        try:
            db.session.execute(text('SELECT 1'))
        except Exception as e:
            print(f'❌ No se pudo conectar: {e}')
            sys.exit(1)

        print('   ✅ Conexión OK.')

        # Verificar extensiones
        resultado = db.session.execute(text(
            "SELECT extname FROM pg_extension "
            "WHERE extname IN ('unaccent', 'pg_trgm')"
        )).fetchall()
        extensiones = {r[0] for r in resultado}

        faltantes = {'unaccent', 'pg_trgm'} - extensiones
        if faltantes:
            print()
            print(f'❌ Faltan extensiones en Supabase: {", ".join(faltantes)}')
            print('   Andá al SQL Editor de Supabase y corré:')
            for ext in faltantes:
                print(f'     CREATE EXTENSION IF NOT EXISTS {ext};')
            sys.exit(1)

        print(f'   ✅ Extensiones OK: {", ".join(extensiones)}.')


def correr_migraciones():
    print()
    print('→ Aplicando migraciones...')
    result = subprocess.run(
        [sys.executable, '-m', 'flask', 'db', 'upgrade'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print('❌ Error en migraciones:')
        print(result.stdout)
        print(result.stderr)
        sys.exit(1)
    print(result.stdout)
    print('   ✅ Migraciones aplicadas.')


def resumen():
    print()
    print('→ Verificando estado final...')
    from app import create_app, db
    from sqlalchemy import text

    app = create_app()
    with app.app_context():
        r = db.session.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            "ORDER BY table_name"
        )).fetchall()
        tablas = [row[0] for row in r]
        print(f'   Tablas creadas: {len(tablas)}')

        # Contar empresas
        count = db.session.execute(text("SELECT count(*) FROM empresa")).scalar()
        print(f'   Empresas: {count}')


def main():
    print('=' * 60)
    print('  DEPLOY A SUPABASE')
    print('=' * 60)
    print()

    config = cargar_env()
    aplicar_env(config)

    validar_conexion()
    correr_migraciones()
    resumen()

    print()
    print('=' * 60)
    print('  ✅ Deploy completo')
    print('=' * 60)
    print()
    print('Próximos pasos:')
    print('  1. Crear el primer club:')
    print('     $env:DATABASE_URL = (Get-Content .env.supabase | Select-String "DATABASE_URL").ToString().Split("=")[1]')
    print('     flask empresa:crear --nombre "Club X" --cuit "30-..." --email ... --password ...')
    print()


if __name__ == '__main__':
    main()