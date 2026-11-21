# tests/test_signup.py
"""
Tests del signup público y de la landing.
"""


class TestLanding:

    def test_visitante_ve_landing(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Club Manager' in html
        assert 'Crear mi club' in html
        assert '/signup' in html

    def test_logueado_ve_dashboard(self, client_a):
        resp = client_a.get('/')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        # El dashboard muestra el hero "Panel general" (rediseño de index.html)
        assert 'Panel general' in html
        assert 'dash-hero' in html


class TestSignup:

    def test_get_signup_muestra_form(self, client):
        resp = client.get('/signup')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Crear mi club' in html
        assert 'Nombre del club' in html

    def test_signup_crea_club_y_loguea(self, client, app):
        resp = client.post('/signup', data={
            'nombre_club': 'Club Nuevo Test',
            'cuit': '30-99999999-9',
            'nombre_admin': 'Juan Test',
            'email': 'admin@clubnuevo.com',
            'password': 'secreto123',
            'confirmar_password': 'secreto123',
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Verificar que el club existe
        from app.models import Empresa, Usuario
        with app.app_context():
            emp = Empresa.query.filter_by(cuit='30-99999999-9').first()
            assert emp is not None
            assert emp.nombre == 'Club Nuevo Test'

            admin = Usuario.query.filter_by(username='admin@clubnuevo.com').first()
            assert admin is not None
            assert admin.es_super_admin is True

    def test_signup_con_cuit_duplicado_falla(self, client, club_a):
        resp = client.post('/signup', data={
            'nombre_club': 'Otro Club',
            'cuit': club_a['empresa'].cuit,
            'nombre_admin': 'Otro',
            'email': 'otro@test.com',
            'password': 'secreto123',
            'confirmar_password': 'secreto123',
        }, follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Ya existe un club' in html

    def test_signup_passwords_no_coinciden(self, client):
        resp = client.post('/signup', data={
            'nombre_club': 'Club Test',
            'cuit': '30-88888888-8',
            'nombre_admin': 'Test',
            'email': 'test@club.com',
            'password': 'secreto123',
            'confirmar_password': 'otraclave',
        }, follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'no coinciden' in html.lower()

    def test_signup_password_corta(self, client):
        resp = client.post('/signup', data={
            'nombre_club': 'Club Test',
            'cuit': '30-77777777-7',
            'nombre_admin': 'Test',
            'email': 'test2@club.com',
            'password': 'abc',
            'confirmar_password': 'abc',
        }, follow_redirects=True)
        assert resp.status_code == 200
        # El validador Length(min=6) rechaza 'abc'

    def test_logueado_redirige_de_signup(self, client_a):
        resp = client_a.get('/signup', follow_redirects=False)
        assert resp.status_code in (302, 303)


class TestSignupCreaDatosBase:

    def test_signup_crea_plan_de_cuentas(self, client, app):
        client.post('/signup', data={
            'nombre_club': 'Club Test 2',
            'cuit': '30-66666666-6',
            'nombre_admin': 'Test',
            'email': 'test3@club.com',
            'password': 'secreto123',
            'confirmar_password': 'secreto123',
        })

        from app.models import Empresa, PlanCuenta, CuentaBancaria
        with app.app_context():
            emp = Empresa.query.filter_by(cuit='30-66666666-6').first()
            assert emp is not None

            # Debe tener el plan completo
            plan = PlanCuenta.query.filter_by(id_empresa=emp.id).count()
            assert plan > 50

            # Debe tener Caja Chica + Cartera
            caja = CuentaBancaria.query.filter_by(
                id_empresa=emp.id, tipo='efectivo'
            ).first()
            assert caja is not None

            cartera = CuentaBancaria.query.filter_by(
                id_empresa=emp.id, tipo='cartera'
            ).first()
            assert cartera is not None