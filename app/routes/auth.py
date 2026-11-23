# app/routes/auth.py
from functools import wraps
from flask import (
    Blueprint, render_template, redirect, url_for, session, flash, request,
)
from app.forms import LoginForm
from app.forms import LoginForm, CambiarPasswordForm
from app.decorators import cargar_usuario_actual
from app.models import Usuario
from app import db, limiter
from app.auditoria import auditar


auth_bp = Blueprint('auth', __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Debes iniciar sesión para acceder.', 'warning')
            return redirect(url_for('auth.login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _loguear_usuario(usuario):
    """Guarda la sesión del usuario y audita el login."""
    session.clear()
    session['usuario_id'] = usuario.id
    session['usuario_nombre'] = usuario.nombre
    session['usuario_username'] = usuario.username
    session['id_empresa'] = usuario.id_empresa

    auditar('login', 'Usuario', usuario.id, nota='Inicio de sesión exitoso')
    db.session.commit()


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
@limiter.limit("50 per hour", methods=["POST"])
def login():
    if 'usuario_id' in session:
        return redirect(url_for('main.index'))

    # Descartar una selección pendiente de un intento anterior
    session.pop('login_pendiente', None)

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        password = form.password.data

        # 1) ¿Existe algún usuario con ese username?
        todos = Usuario.query.filter_by(username=username).all()
        if not todos:
            flash('Usuario o contraseña incorrectos.', 'danger')
            return render_template('auth/login.html', form=form)

        # 2) Filtrar los que tengan la contraseña correcta
        con_password_ok = [u for u in todos if u.check_password(password)]
        if not con_password_ok:
            flash('Usuario o contraseña incorrectos.', 'danger')
            return render_template('auth/login.html', form=form)

        # 3) Filtrar los que estén activos y verificados
        utilizables = [
            u for u in con_password_ok if u.activo and u.email_verificado
        ]

        if not utilizables:
            alguno_activo = any(u.activo for u in con_password_ok)
            if not alguno_activo:
                flash('Tu cuenta está desactivada.', 'danger')
                return render_template('auth/login.html', form=form)

            flash(
                'Debés verificar tu correo antes de iniciar sesión. '
                'Revisá tu bandeja de entrada o pedí un nuevo enlace.',
                'warning',
            )
            return render_template(
                'auth/login.html',
                form=form,
                mostrar_reenvio=True,
                email_pendiente=username,
            )

        # 4) Un solo club => login directo
        if len(utilizables) == 1:
            usuario = utilizables[0]
            _loguear_usuario(usuario)
            flash(f'Bienvenido, {usuario.nombre}.', 'success')

            next_url = request.args.get('next')
            if (next_url and next_url.startswith('/')
                    and not next_url.startswith('//')):
                return redirect(next_url)
            return redirect(url_for('main.index'))

        # 5) Varios clubes => selector
        session['login_pendiente'] = [u.id for u in utilizables]
        return redirect(url_for('auth.seleccionar_club'))

    return render_template('auth/login.html', form=form)


@auth_bp.route('/login/seleccionar-club', methods=['GET', 'POST'])
def seleccionar_club():
    ids = session.get('login_pendiente') or []
    if not ids:
        flash('Tu sesión de inicio expiró. Volvé a intentar.', 'warning')
        return redirect(url_for('auth.login'))

    usuarios = Usuario.query.filter(Usuario.id.in_(ids)).all()
    if not usuarios:
        session.pop('login_pendiente', None)
        flash('Los usuarios ya no existen.', 'danger')
        return redirect(url_for('auth.login'))

    usuarios.sort(key=lambda u: ((u.empresa.nombre or '').lower(), u.username))

    if request.method == 'POST':
        try:
            id_elegido = int(request.form.get('id_usuario') or 0)
        except (TypeError, ValueError):
            id_elegido = 0

        usuario = next((u for u in usuarios if u.id == id_elegido), None)
        if not usuario:
            flash('Selección inválida.', 'danger')
            return redirect(url_for('auth.seleccionar_club'))

        _loguear_usuario(usuario)
        flash(f'Bienvenido, {usuario.nombre}.', 'success')
        return redirect(url_for('main.index'))

    return render_template('auth/seleccionar_club.html', usuarios=usuarios)


@auth_bp.route('/logout', methods=['POST'])
def logout():
    usuario_id = session.get('usuario_id')
    id_empresa = session.get('id_empresa')

    if usuario_id and id_empresa:
        auditar('logout', 'Usuario', usuario_id, nota='Cierre de sesión')

    session.clear()
    db.session.commit()
    flash('Sesión cerrada correctamente.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/cambiar-password', methods=['GET', 'POST'])
@login_required
def cambiar_password():
    usuario = cargar_usuario_actual()
    form = CambiarPasswordForm()
    
    if form.validate_on_submit():
        if not usuario.check_password(form.password_actual.data):
            flash('La contraseña actual es incorrecta.', 'danger')
            return render_template('auth/cambiar_password.html', form=form)
            
        usuario.set_password(form.password_nuevo.data)
        db.session.commit()
        
        auditar('editar', 'Usuario', usuario.id, nota='El usuario cambió su propia contraseña')
        
        flash('¡Tu contraseña se actualizó correctamente!', 'success')
        return redirect(url_for('main.index'))
        
    return render_template('auth/cambiar_password.html', form=form)