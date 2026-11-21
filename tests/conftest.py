# tests/conftest.py
"""
Fixtures compartidas por todos los tests.

Cada corrida de pytest crea una base SQLite temporal (archivo en tmp),
distinta a la base real del proyecto, y la destruye al final.
"""

import os
import tempfile
import pytest
from datetime import date

from app import create_app, db


# ============================================================
# Config para tests
# ============================================================

class TestConfig:
    TESTING = True
    SECRET_KEY = 'test-secret-key'
    WTF_CSRF_ENABLED = False   # deshabilitamos CSRF para facilitar POSTs
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LOGIN_DISABLED = False
    ITEMS_PER_PAGE = 20

    # Será sobreescrita en conftest por un archivo temporal único
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


# ============================================================
# Fixtures principales
# ============================================================

@pytest.fixture(scope='session')
def tmp_db_path():
    """Crea un archivo SQLite temporal único por sesión de tests."""
    fd, path = tempfile.mkstemp(suffix='.db', prefix='club_test_')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def app(tmp_db_path):
    """
    Crea la app Flask configurada para tests, con base de datos
    temporal, y crea todas las tablas.
    """
    TestConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp_db_path}'

    app = create_app(TestConfig)

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        # Forzar cierre de conexiones antes de que el archivo se borre.
        # En Windows SQLite mantiene el archivo bloqueado hasta el dispose.
        db.engine.dispose()


@pytest.fixture
def client(app):
    """Cliente HTTP anónimo (sin login)."""
    return app.test_client()


# ============================================================
# Helpers para crear clubes y usuarios
# ============================================================

def _crear_club_completo(nombre, cuit, email_admin, password='test1234'):
    """
    Crea una empresa con usuario admin, plan de cuentas, cuentas de caja
    y datos mínimos. Devuelve un dict con referencias útiles.
    """
    from app.models import (
        Empresa, Usuario, TipoSocio, CategoriaSocio,
        CategoriaProducto, PlanCuenta, CuentaBancaria,
    )
    from app.cli import PLAN_SEED, CODIGOS_AUTOMATICOS

    # --- Empresa ---
    empresa = Empresa(nombre=nombre, cuit=cuit, email=email_admin)
    db.session.add(empresa)
    db.session.flush()

    # --- Usuario admin ---
    admin = Usuario(
        nombre='Admin',
        username=email_admin,
        rol='admin',
        activo=True,
        id_empresa=empresa.id,
        es_super_admin=True,
        email_verificado=True,   # el login exige correo verificado
    )
    admin.set_password(password)
    db.session.add(admin)

    # --- Tipos y categorías de socio ---
    tipo_activo = TipoSocio(nombre='Activo', id_empresa=empresa.id, activo=True)
    tipo_familiar = TipoSocio(nombre='Familiar', id_empresa=empresa.id, activo=True)
    cat_pleno = CategoriaSocio(nombre='Pleno', id_empresa=empresa.id, activo=True)
    cat_adh = CategoriaSocio(nombre='Adherente', id_empresa=empresa.id, activo=True)
    db.session.add_all([tipo_activo, tipo_familiar, cat_pleno, cat_adh])

    # --- Categorías de producto ---
    for n in ('Remeras', 'Pantalones', 'Accesorios'):
        db.session.add(CategoriaProducto(nombre=n, id_empresa=empresa.id, activo=True))

    db.session.flush()

    # --- Plan de cuentas ---
    existentes = {}
    for codigo, nombre_c, tipo_c, codigo_padre in PLAN_SEED:
        id_padre = existentes[codigo_padre].id if codigo_padre and codigo_padre in existentes else None
        c = PlanCuenta(
            codigo=codigo,
            nombre=nombre_c,
            tipo=tipo_c,
            id_padre=id_padre,
            id_empresa=empresa.id,
            activo=True,
            es_automatica=(codigo in CODIGOS_AUTOMATICOS),
        )
        db.session.add(c)
        db.session.flush()
        existentes[codigo] = c

    # --- Caja Chica + Cartera ---
    plan_caja = existentes.get('1.1.1.1')
    plan_cartera = existentes.get('1.1.6')

    caja_chica = CuentaBancaria(
        nombre='Caja Chica', tipo='efectivo',
        saldo_inicial=0, saldo_actual=0, activo=True,
        id_empresa=empresa.id, id_cuenta_contable=plan_caja.id,
    )
    cartera = CuentaBancaria(
        nombre='Cartera de Cheques', tipo='cartera',
        saldo_inicial=0, saldo_actual=0, activo=True,
        id_empresa=empresa.id, id_cuenta_contable=plan_cartera.id,
    )
    db.session.add_all([caja_chica, cartera])
    db.session.flush()

    return {
        'empresa': empresa,
        'admin': admin,
        'password': password,
        'tipo_activo': tipo_activo,
        'tipo_familiar': tipo_familiar,
        'cat_pleno': cat_pleno,
        'cat_adherente': cat_adh,
        'caja_chica': caja_chica,
        'cartera': cartera,
        'plan_cuentas': existentes,
    }


def _crear_socio(club, numero_socio='0001', apellido='Pérez', nombre='Juan',
                 nro_doc=None):
    """Crea un socio en el club dado. numero_socio único por club."""
    from app.models import Socio
    # nro_doc único por club (agregamos el id_empresa para evitar colisiones)
    nro_doc = nro_doc or f'DOC{numero_socio}{club["empresa"].id}'
    socio = Socio(
        numero_socio=numero_socio,
        nombre=nombre,
        apellido=apellido,
        tipo_documento='DNI',
        nro_documento=nro_doc,
        id_tipo_socio=club['tipo_activo'].id,
        id_categoria_socio=club['cat_pleno'].id,
        id_empresa=club['empresa'].id,
        fecha_alta=date.today(),
    )
    db.session.add(socio)
    db.session.flush()
    return socio


# ============================================================
# Fixtures de clubes con datos
# ============================================================

@pytest.fixture
def club_a(app):
    """
    Club A con:
      - 2 socios
      - Caja Chica y Cartera
    """
    club = _crear_club_completo(
        nombre='Club A', cuit='30-11111111-1', email_admin='admin@club-a.com',
    )
    socio1 = _crear_socio(club, numero_socio='0001', apellido='Alfa', nombre='Ana')
    socio2 = _crear_socio(club, numero_socio='0002', apellido='Alfa2', nombre='Luis')
    db.session.commit()

    club['socios'] = [socio1, socio2]
    return club


@pytest.fixture
def club_b(app):
    """
    Club B con:
      - 1 socio
      - Caja Chica y Cartera
    """
    club = _crear_club_completo(
        nombre='Club B', cuit='30-22222222-2', email_admin='admin@club-b.com',
    )
    socio1 = _crear_socio(club, numero_socio='0001', apellido='Beta', nombre='Bruno')
    db.session.commit()

    club['socios'] = [socio1]
    return club


# ============================================================
# Fixtures de clientes logueados
# ============================================================

def _login_client(app, username, password):
    """Devuelve un test_client ya logueado con el usuario dado."""
    c = app.test_client()
    resp = c.post('/login', data={
        'username': username,
        'password': password,
    }, follow_redirects=False)
    assert resp.status_code in (302, 303), (
        f'Login falló para {username}: status {resp.status_code}'
    )
    return c


@pytest.fixture
def client_a(app, club_a):
    """Cliente logueado como admin del Club A."""
    return _login_client(app, club_a['admin'].username, club_a['password'])


@pytest.fixture
def client_b(app, club_b):
    """Cliente logueado como admin del Club B."""
    return _login_client(app, club_b['admin'].username, club_b['password'])

# ============================================================
# Fixtures para tests de roles y permisos
# ============================================================

def _crear_rol(empresa, nombre, permisos):
    """Crea un rol con la lista de permisos dada."""
    from app.models import Rol
    rol = Rol(nombre=nombre, id_empresa=empresa.id, activo=True)
    rol.set_permisos(permisos)
    db.session.add(rol)
    db.session.flush()
    return rol


def _crear_usuario_regular(empresa, rol, username, password='test1234'):
    """
    Crea un usuario NO super admin con el rol dado (puede ser None).
    """
    from app.models import Usuario
    u = Usuario(
        nombre=username.capitalize(),
        username=username,
        activo=True,
        id_empresa=empresa.id,
        es_super_admin=False,
        id_rol=rol.id if rol else None,
        email_verificado=True,   # el login exige correo verificado
    )
    u.set_password(password)
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def club_a_con_roles(app, club_a):
    """
    Extiende club_a con:
      - un rol 'Cobrador' con permisos limitados:
          socios.ver, cuotas.ver+editar, caja.ver
      - un usuario 'limitado' con ese rol
      - un usuario 'sinrol' sin rol
    """
    rol_limitado = _crear_rol(club_a['empresa'], 'Cobrador', [
        'socios.ver',
        'cuotas.ver', 'cuotas.editar',
        'caja.ver',
    ])

    user_limitado = _crear_usuario_regular(
        club_a['empresa'], rol_limitado, 'limitado'
    )
    user_sin_rol = _crear_usuario_regular(
        club_a['empresa'], None, 'sinrol'
    )

    db.session.commit()

    club_a['rol_limitado'] = rol_limitado
    club_a['user_limitado'] = user_limitado
    club_a['user_sin_rol'] = user_sin_rol
    return club_a


@pytest.fixture
def client_a_limitado(app, club_a_con_roles):
    """Cliente logueado con el usuario de rol limitado (Cobrador)."""
    return _login_client(app, 'limitado', 'test1234')


@pytest.fixture
def client_a_sin_rol(app, club_a_con_roles):
    """Cliente logueado con el usuario sin rol."""
    return _login_client(app, 'sinrol', 'test1234')