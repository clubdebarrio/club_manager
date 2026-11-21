# tests/test_roles.py
"""
Tests de roles y permisos.

Verifican:
  1. El menú lateral se filtra según permisos del usuario.
  2. Las rutas bloqueadas rechazan el acceso (302 al home).
  3. Las APIs AJAX devuelven 403 JSON en lugar de redirect.
  4. Los botones de acción se ocultan si no tiene permiso de edición.
  5. Las rutas administrativas (roles, usuarios) solo las ve el super admin.
"""


class TestMenuFiltrado:

    def test_super_admin_ve_todos_los_modulos(self, client_a):
        resp = client_a.get('/')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        for url in ['/caja/', '/socios/', '/cuotas/',
                    '/ventas/', '/compras/', '/reservas/',
                    '/contabilidad/']:
            assert f'href="{url}"' in html, \
                f'Super admin debería ver link a {url}'

    def test_limitado_ve_solo_sus_modulos(self, client_a_limitado):
        resp = client_a_limitado.get('/')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        # Tiene caja.ver, socios.ver, cuotas.ver
        assert 'href="/caja/"' in html
        assert 'href="/socios/"' in html
        assert 'href="/cuotas/"' in html
        # NO tiene ventas, compras, reservas, contabilidad
        assert 'href="/ventas/"' not in html
        assert 'href="/compras/"' not in html
        assert 'href="/reservas/"' not in html
        assert 'href="/contabilidad/"' not in html

    def test_sin_rol_no_ve_ningun_modulo(self, client_a_sin_rol):
        resp = client_a_sin_rol.get('/')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        for url in ['/caja/', '/socios/', '/cuotas/',
                    '/ventas/', '/compras/', '/reservas/',
                    '/contabilidad/', '/cheques/']:
            assert f'href="{url}"' not in html, \
                f'Sin rol NO debería ver link a {url}'


class TestAccesoRutas:

    def test_super_admin_entra_a_todos(self, client_a):
        for url in ['/caja/', '/socios/', '/cuotas/', '/ventas/',
                    '/compras/', '/reservas/', '/contabilidad/',
                    '/instalaciones/', '/cheques/', '/proveedores/']:
            resp = client_a.get(url, follow_redirects=False)
            assert resp.status_code == 200, \
                f'Super admin debería entrar a {url} (devolvió {resp.status_code})'

    def test_limitado_entra_a_sus_modulos(self, client_a_limitado):
        for url in ['/caja/', '/socios/', '/cuotas/']:
            resp = client_a_limitado.get(url, follow_redirects=False)
            assert resp.status_code == 200, \
                f'Limitado debería entrar a {url}'

    def test_limitado_no_entra_a_otros(self, client_a_limitado):
        for url in ['/ventas/', '/compras/', '/reservas/', '/contabilidad/',
                    '/instalaciones/', '/cheques/', '/proveedores/']:
            resp = client_a_limitado.get(url, follow_redirects=False)
            assert resp.status_code in (302, 303), \
                f'Limitado NO debería entrar a {url} (devolvió {resp.status_code})'

    def test_sin_rol_no_entra_a_ningun_modulo(self, client_a_sin_rol):
        for url in ['/caja/', '/socios/', '/cuotas/', '/ventas/',
                    '/compras/', '/reservas/', '/contabilidad/',
                    '/instalaciones/', '/cheques/', '/proveedores/']:
            resp = client_a_sin_rol.get(url, follow_redirects=False)
            assert resp.status_code in (302, 303), \
                f'Sin rol NO debería entrar a {url}'


class TestAPIsResponden403:

    def test_api_preview_asiento_rechaza_sin_permiso(self, client_a_sin_rol):
        resp = client_a_sin_rol.post(
            '/caja/api/preview_asiento',
            json={
                'id_cuenta': 1,
                'id_cuenta_contable': 1,
                'tipo': 'ingreso',
            },
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        # El path contiene /api/, así que _denegar() devuelve JSON 403
        assert resp.status_code == 403
        data = resp.get_json()
        assert data is not None
        assert 'error' in data


class TestBotonesAccion:

    def test_super_admin_ve_boton_nuevo_socio(self, client_a):
        resp = client_a.get('/socios/')
        html = resp.data.decode('utf-8')
        assert '/socios/crear' in html

    def test_limitado_sin_editar_socios_no_ve_boton(self, client_a_limitado):
        resp = client_a_limitado.get('/socios/')
        html = resp.data.decode('utf-8')
        # El rol limitado tiene socios.ver pero NO socios.editar
        assert '/socios/crear' not in html

    def test_limitado_con_editar_cuotas_ve_boton_generar(self, client_a_limitado):
        resp = client_a_limitado.get('/cuotas/')
        html = resp.data.decode('utf-8')
        # Tiene cuotas.editar
        assert '/cuotas/generar' in html

    def test_limitado_sin_editar_caja_no_ve_boton_nuevo_movimiento(
            self, client_a_limitado):
        resp = client_a_limitado.get('/caja/')
        html = resp.data.decode('utf-8')
        # El rol limitado solo tiene caja.ver, no caja.editar
        # El botón "Registrar Movimiento" apunta a /caja/movimientos/nuevo
        assert '/caja/movimientos/nuevo' not in html


class TestRutasAdmin:

    def test_super_admin_accede_a_roles(self, client_a):
        resp = client_a.get('/roles/', follow_redirects=False)
        assert resp.status_code == 200

    def test_super_admin_accede_a_usuarios(self, client_a):
        resp = client_a.get('/usuarios/', follow_redirects=False)
        assert resp.status_code == 200

    def test_limitado_no_accede_a_roles(self, client_a_limitado):
        resp = client_a_limitado.get('/roles/', follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_limitado_no_accede_a_usuarios(self, client_a_limitado):
        resp = client_a_limitado.get('/usuarios/', follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_sin_rol_no_accede_a_roles(self, client_a_sin_rol):
        resp = client_a_sin_rol.get('/roles/', follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_sin_rol_no_accede_a_usuarios(self, client_a_sin_rol):
        resp = client_a_sin_rol.get('/usuarios/', follow_redirects=False)
        assert resp.status_code in (302, 303)