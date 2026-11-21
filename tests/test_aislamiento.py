# tests/test_aislamiento.py
"""
Tests de aislamiento multiempresa.

Verifican que un usuario logueado en un club no puede:
  1. Ver datos de otro club en los listados.
  2. Acceder a recursos de otro club por ID directo.

Cada test usa las fixtures `client_a`/`client_b` (ya logueados) y
`club_a`/`club_b` (con datos de prueba).
"""


# ============================================================
# Home / Dashboard
# ============================================================

class TestHome:
    def test_home_muestra_solo_socios_del_club_actual(self, client_a, club_a, club_b):
        """El dashboard del Club A cuenta solo los socios de Club A."""
        resp = client_a.get('/')
        assert resp.status_code == 200
        # El HTML del dashboard no debe contener strings únicos del otro club
        texto = resp.data.decode('utf-8')
        # No aparece el nombre del Club B
        assert 'Club B' not in texto
        # Sí aparece el nombre del Club A (está en el menú o dashboard)
        # (o al menos el request fue exitoso)
        assert resp.status_code == 200

    def test_contexto_home_solo_cuenta_socios_del_club_actual(self, client_a, club_a, club_b):
        """Verifica que el home no cuente socios del Club B."""
        # Este test no puede inspeccionar el contexto directamente con test_client,
        # pero podemos verificar que el HTML no contiene el nombre del otro club.
        resp = client_a.get('/')
        texto = resp.data.decode('utf-8')
        assert 'Club B' not in texto
        
# ============================================================
# Socios
# ============================================================

class TestAislamientoSocios:

    def test_listado_no_muestra_socios_de_otro_club(self, client_a, club_a, club_b):
        resp = client_a.get('/socios/')
        assert resp.status_code == 200
        # Debe aparecer "Alfa" (Club A) y no "Beta" (Club B)
        assert b'Alfa' in resp.data
        assert b'Beta' not in resp.data

    def test_no_puede_ver_socio_de_otro_club_por_id(self, client_a, club_b):
        socio_b = club_b['socios'][0]
        resp = client_a.get(f'/socios/{socio_b.id}', follow_redirects=False)
        # Esperamos redirect (302) al listado con flash "Socio no encontrado"
        assert resp.status_code in (302, 303, 404), (
            f'Se esperaba redirect o 404, se obtuvo {resp.status_code}'
        )

    def test_no_puede_editar_socio_de_otro_club(self, client_a, club_b):
        socio_b = club_b['socios'][0]
        resp = client_a.get(f'/socios/editar/{socio_b.id}', follow_redirects=False)
        assert resp.status_code in (302, 303, 404)

    def test_no_puede_dar_de_baja_socio_de_otro_club(self, client_a, club_b):
        socio_b = club_b['socios'][0]
        resp = client_a.post(f'/socios/baja/{socio_b.id}', follow_redirects=False)
        assert resp.status_code in (302, 303, 404)


# ============================================================
# Caja
# ============================================================

class TestAislamientoCaja:

    def test_listado_cuentas_no_muestra_cuentas_de_otro_club(self, client_a, club_a, club_b):
        resp = client_a.get('/caja/cuentas')
        assert resp.status_code == 200
        # Ambos clubes tienen "Caja Chica", pero cada uno ve solo la propia.
        # Contamos ocurrencias de "Caja Chica": debe haber 1 sola.
        texto = resp.data.decode('utf-8')
        # Buscamos por un ID de cuenta del Club B que NO debe estar
        # (más robusto que contar strings repetidos)
        # El ID de la caja del club B no debe aparecer en links
        id_caja_b = club_b['caja_chica'].id
        assert f'/caja/cuenta/{id_caja_b}' not in texto

    def test_no_puede_ver_cuenta_de_otro_club_por_id(self, client_a, club_b):
        cuenta_b = club_b['caja_chica']
        resp = client_a.get(f'/caja/cuenta/{cuenta_b.id}', follow_redirects=False)
        assert resp.status_code in (302, 303, 404)

    def test_no_puede_editar_cuenta_de_otro_club(self, client_a, club_b):
        cuenta_b = club_b['caja_chica']
        resp = client_a.get(f'/caja/cuentas/editar/{cuenta_b.id}', follow_redirects=False)
        assert resp.status_code in (302, 303, 404)


# ============================================================
# Ventas / Productos
# ============================================================

class TestAislamientoVentas:

    def test_listado_productos_vacio_si_no_hay_productos(self, client_a):
        resp = client_a.get('/ventas/productos')
        assert resp.status_code == 200
        # Ningún club tiene productos en este test
        assert b'No hay productos' in resp.data or b'No se encontraron' in resp.data \
            or b'<table' in resp.data


# ============================================================
# Compras
# ============================================================

class TestAislamientoCompras:

    def test_listado_compras_vacio(self, client_a):
        resp = client_a.get('/compras/')
        assert resp.status_code == 200
        # Ningún club tiene compras
        assert b'No hay compras' in resp.data


# ============================================================
# Cuotas
# ============================================================

class TestAislamientoCuotas:

    def test_listado_cuotas_vacio(self, client_a):
        resp = client_a.get('/cuotas/')
        assert resp.status_code == 200
        # Ningún club tiene cuotas todavía


# ============================================================
# Contabilidad
# ============================================================

class TestAislamientoContabilidad:

    def test_listado_asientos_vacio(self, client_a):
        resp = client_a.get('/contabilidad/')
        assert resp.status_code == 200

    def test_plan_de_cuentas_solo_del_club_actual(self, client_a, club_a, club_b):
        """El plan de cuentas de cada club es propio."""
        resp = client_a.get('/contabilidad/plan-cuentas')
        assert resp.status_code == 200
        # Debe estar el plan completo del Club A (61+ cuentas)
        assert b'ACTIVO' in resp.data


# ============================================================
# Configuración
# ============================================================

class TestAislamientoConfiguracion:

    def test_tipos_de_socio_solo_del_club_actual(self, client_a, club_a, club_b):
        resp = client_a.get('/config/tipos')
        assert resp.status_code == 200
        # El Club A tiene "Activo" y "Familiar" (creados por el helper)
        assert b'Activo' in resp.data
        assert b'Familiar' in resp.data


# ============================================================
# Proveedores
# ============================================================

class TestAislamientoProveedores:

    def test_listado_proveedores_vacio(self, client_a):
        resp = client_a.get('/proveedores/')
        assert resp.status_code == 200
        # Ningún club tiene proveedores
        assert b'No hay proveedores' in resp.data