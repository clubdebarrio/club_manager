# app/__init__.py
from flask import (
    Flask, request, redirect, url_for, session, render_template, jsonify,
)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import MetaData
from config import Config
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from app.utils import quitar_acentos

mail = Mail()

# Naming convention
convention = {
    "ix":  "ix_%(column_0_label)s",
    "uq":  "uq_%(table_name)s_%(column_0_name)s",
    "ck":  "ck_%(table_name)s_%(constraint_name)s",
    "fk":  "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk":  "pk_%(table_name)s",
}

db = SQLAlchemy(metadata=MetaData(naming_convention=convention))
migrate = Migrate()

limiter = Limiter(
    key_func=get_remote_address,   # <-- una sola key_func
    default_limits=[],             # sin límite global; solo por ruta
)

# Esquemas de `limits` que requieren un cliente opcional instalado.
_STORAGE_DEPENDENCIAS = {
    'redis': ('redis', 'rediss', 'redis+unix', 'redis+cluster',
              'redis+sentinel'),
    'valkey': ('valkey', 'valkeys', 'valkey+unix', 'valkey+cluster',
               'valkey+sentinel'),
}


def _storage_disponible(uri):
    """Indica si el paquete necesario para el storage está instalado."""
    scheme = uri.split('://', 1)[0].lower()
    for paquete, schemes in _STORAGE_DEPENDENCIAS.items():
        if scheme in schemes:
            try:
                __import__(paquete)
            except ImportError:
                return False
    return True


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    mail.init_app(app)
    db.init_app(app)
    migrate.init_app(app, db)
    # Flask-Limiter lee RATELIMIT_STORAGE_URI del config automáticamente.
    # Si el backend configurado necesita un paquete opcional que no está
    # instalado (p. ej. `redis` en Vercel), degradamos a memoria en vez de
    # romper el arranque de la aplicación.
    storage_uri = app.config.get('RATELIMIT_STORAGE_URI') or 'memory://'
    if not _storage_disponible(storage_uri):
        app.logger.warning(
            'RATELIMIT_STORAGE_URI=%s pero su cliente no está instalado; '
            'se usa memoria (los límites no se comparten entre instancias).',
            storage_uri,
        )
        app.config['RATELIMIT_STORAGE_URI'] = 'memory://'
    limiter.init_app(app)
    # ---- Configuración SQLite ----
    # WAL permite que lectores y escritor trabajen en paralelo sin
    # bloquearse mutuamente. busy_timeout hace que una escritura que
    # encuentra la base bloqueada espere hasta 5s en lugar de fallar.
    with app.app_context():
        if db.engine.name == 'sqlite':
            @db.event.listens_for(db.engine, "connect")
            def sqlite_pragmas(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

                # Función unaccent para búsquedas con normalización
                dbapi_connection.create_function("unaccent", 1, quitar_acentos)
    # ---------------------------------------------

    # Registrar blueprints
    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    from app.routes.main import main_bp
    app.register_blueprint(main_bp)

    from app.routes.socios import socios_bp
    app.register_blueprint(socios_bp, url_prefix='/socios')

    from app.routes.cuotas import cuotas_bp
    app.register_blueprint(cuotas_bp)

    from app.routes.configuracion import config_bp
    app.register_blueprint(config_bp)

    from app.routes.instalaciones import instalaciones_bp
    from app.routes.reservas import reservas_bp
    app.register_blueprint(instalaciones_bp)
    app.register_blueprint(reservas_bp)

    from app.routes.caja import caja_bp
    from app.routes.cheques import cheques_bp
    app.register_blueprint(caja_bp)
    app.register_blueprint(cheques_bp)

    from app.routes.importacion import importacion_bp
    app.register_blueprint(importacion_bp)
    
    from app.routes.contabilidad import contabilidad_bp
    app.register_blueprint(contabilidad_bp)

    from app.routes.webhooks import webhooks_bp
    app.register_blueprint(webhooks_bp)

    from app.routes.oauth import oauth_bp
    app.register_blueprint(oauth_bp)

    from app.routes.ventas import ventas_bp
    app.register_blueprint(ventas_bp)

    from app.routes.compras import compras_bp
    app.register_blueprint(compras_bp)

    from app.routes.proveedores import proveedores_bp
    app.register_blueprint(proveedores_bp)
    
    from app.utils import registrar_filtros
    registrar_filtros(app)

    # Comandos CLI
    from app.cli import register_cli
    register_cli(app)

    from app.routes.roles import roles_bp
    from app.routes.usuarios import usuarios_bp
    app.register_blueprint(roles_bp)
    app.register_blueprint(usuarios_bp)

    from app.routes.public import public_bp
    app.register_blueprint(public_bp)
    
    from app.routes.auditoria import auditoria_bp
    app.register_blueprint(auditoria_bp)

    from app.routes.sistema import sistema_bp
    app.register_blueprint(sistema_bp)

    from app.routes.tasks import tasks_bp
    app.register_blueprint(tasks_bp)    
    
    # ---- Protección global: exige inicio de sesión ----
    ENDPOINTS_PUBLICOS = {
        'static',
        'main.index',
        'public.signup',
        'public.verificar_email',
        'auth.login',
        'auth.logout',
        'auth.seleccionar_club', 
        'webhooks.chytapay_webhook',
        'oauth.oauth_callback',
        'public.reenviar_verificacion',
        'public.solicitar_reset_password',
        'public.reset_password',
        'tasks.procesar_chytapay_pendientes',
    }

    @app.before_request
    def proteger_rutas():
        # --- Bloqueos durante impersonation ---
        if session.get('usuario_original_id'):
            IMPERSONATION_BLOQUEADAS = {
                'usuarios.listar', 'usuarios.nuevo', 'usuarios.editar',
                'usuarios.eliminar',
                'roles.listar', 'roles.nuevo', 'roles.editar',
                'roles.eliminar',
                'sistema.listar', 'sistema.detalle',
                'sistema.extender_trial', 'sistema.marcar_pago',
                'sistema.asignar_tarifa', 'sistema.suspender',
                'sistema.reactivar',
                'auth.logout',
            }
            if request.endpoint in IMPERSONATION_BLOQUEADAS:
                # Detectar si es AJAX/JSON: solo entonces devolvemos JSON.
                # Si es un POST de un form normal (como el logout),
                # mostramos la página HTML.
                acepta_json = (
                    request.accept_mimetypes.best == 'application/json'
                )
                es_ajax = (
                    request.headers.get('X-Requested-With')
                    == 'XMLHttpRequest'
                )
                es_json_path = '/api/' in request.path

                if es_ajax or acepta_json or es_json_path:
                    return jsonify({
                        'error': 'Esta acción no está permitida durante '
                                 'la suplantación.'
                    }), 403

                return render_template(
                    'errors/impersonation_bloqueado.html',
                ), 403
            
        # Endpoints públicos: pasar
        if request.endpoint in ENDPOINTS_PUBLICOS or request.endpoint is None:
            return None

        # No logueado: redirigir a login
        if 'usuario_id' not in session:
            if request.method == 'GET':
                return redirect(url_for('auth.login', next=request.path))
            return redirect(url_for('auth.login'))

        # Empresa actual: verificar que pueda escribir
        from app.utils import current_empresa
        empresa = current_empresa()

        if empresa and not empresa.puede_escribir:
            # Permitir GET siempre (solo lectura)
            if request.method in ('GET', 'HEAD', 'OPTIONS'):
                return None

            # Endpoints exentos: logout y sistema (sos_messi puede operar
            # aunque el club esté suspendido)
            exentos = {'auth.logout'}
            if request.endpoint in exentos:
                return None
            if request.endpoint and request.endpoint.startswith('sistema.'):
                return None
            # SOS_MESSI exento de todas las restricciones
            from app.decorators import cargar_usuario_actual
            u = cargar_usuario_actual()
            if u and u.es_super_admin_sistema:
                return None

            # Bloquear escritura
            if request.is_json or '/api/' in request.path:
                return jsonify({
                    'error': (
                        'El acceso de escritura está bloqueado. '
                        'Contactanos para regularizar tu situación.'
                    )
                }), 403

            # Renderizar página de bloqueo (evita flash + redirect que
            # puede causar loops en algunos flujos).
            return render_template(
                'errors/trial_vencido.html',
                empresa=empresa,
            ), 403

        return None

    # ---- Exponer el usuario actual a los templates ----
    @app.context_processor
    def inject_usuario_actual():
        from app.decorators import cargar_usuario_actual
        from app.utils import current_empresa
        return {
            'usuario_actual': cargar_usuario_actual(),
            'empresa_actual': current_empresa(),
        }


    @app.errorhandler(429)
    def ratelimit_handler(e):
        # Si es una request AJAX/JSON, devolvemos JSON
        if request.accept_mimetypes.best == 'application/json' or \
        request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'error': 'Demasiadas solicitudes. Esperá un momento antes de reintentar.',
            }), 429

        # Si no, renderizamos una página
        return render_template('errors/429.html', error=e), 429

    # ---- Manejo de errores en producción ----
    @app.errorhandler(404)
    def not_found_error(error):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()  # Si falló una transacción, la limpiamos
        return render_template('errors/500.html'), 500
    
    return app