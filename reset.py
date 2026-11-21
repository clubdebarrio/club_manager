# reset.py
"""
Resetea la base de datos a un estado limpio + datos de prueba.

Uso:
    python reset.py

Borra TODO el schema public, vuelve a correr las migraciones,
y carga el seed. Ideal para desarrollo cuando una migración se
rompe o querés partir de cero.
"""
import os
import subprocess
import sys

from sqlalchemy import text

from app import create_app, db


def _drop_schema():
    """Borra todas las tablas del schema public y lo recrea vacío."""
    app = create_app()
    with app.app_context():
        print("→ Borrando schema public...")
        db.session.execute(text("DROP SCHEMA public CASCADE"))
        db.session.execute(text("CREATE SCHEMA public"))
        db.session.commit()

        print("→ Habilitando extensiones...")
        db.session.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
        db.session.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        db.session.commit()


def _run_migrations():
    """Corre 'flask db upgrade' en un subproceso."""
    print("→ Aplicando migraciones...")
    result = subprocess.run(
        [sys.executable, "-m", "flask", "db", "upgrade"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("❌ Error en las migraciones:")
        print(result.stdout)
        print(result.stderr)
        sys.exit(1)
    print(result.stdout)


def _seed():
    """Corre el seed."""
    print("→ Cargando datos de prueba...")
    from seed import seed
    seed()


def reset():
    # Red de seguridad: no permitir resetear si apuntamos a Supabase.
    db_url = os.environ.get('DATABASE_URL', '')
    if 'supabase' in db_url.lower():
        print()
        print('❌ ABORTADO: DATABASE_URL apunta a SUPABASE (producción).')
        print('   reset.py solo funciona contra la base LOCAL (Docker).')
        print(f'   URL detectada: {db_url[:60]}...')
        print()
        print('   Para resetear local, corré primero:')
        print('     .\\activate-local.ps1')
        print()
        sys.exit(1)

    print("=" * 60)
    print("  RESET DE BASE DE DATOS - Entorno de desarrollo")
    # ... resto del código igual ...
    print("=" * 60)
    print("  RESET DE BASE DE DATOS - Entorno de desarrollo")
    print("=" * 60)
    print()

    respuesta = input(
        "⚠️  Esto BORRA TODA la base de datos. ¿Continuar? (escribí 'si'): "
    )
    if respuesta.strip().lower() != 'si':
        print("Cancelado.")
        return

    print()
    _drop_schema()
    print()
    _run_migrations()
    print()
    _seed()
    print()
    print("=" * 60)
    print("  ✅ Reset completo")
    print("=" * 60)


if __name__ == '__main__':
    reset()