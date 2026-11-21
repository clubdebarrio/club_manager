# app/routes/usuarios.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session,
)
from app import db
from app.models import Usuario, Rol
from app.forms import UsuarioForm
from app.decorators import requiere_super_admin, cargar_usuario_actual
from app.utils import current_empresa
from app.auditoria import auditar

usuarios_bp = Blueprint('usuarios', __name__, url_prefix='/usuarios')


def _choices_roles(empresa_id):
    """Roles activos de la empresa, para el dropdown del form."""
    roles = (
        Rol.query
        .filter_by(id_empresa=empresa_id, activo=True)
        .order_by(Rol.nombre)
        .all()
    )
    return [(0, '— Sin rol —')] + [(r.id, r.nombre) for r in roles]


@usuarios_bp.route('/')
@requiere_super_admin
def listar():
    empresa = current_empresa()
    usuarios = (
        Usuario.query
        .filter_by(id_empresa=empresa.id)
        .order_by(Usuario.es_super_admin.desc(), Usuario.nombre)
        .all()
    )
    return render_template('usuarios/listar.html', usuarios=usuarios)


@usuarios_bp.route('/nuevo', methods=['GET', 'POST'])
@requiere_super_admin
def nuevo():
    empresa = current_empresa()
    form = UsuarioForm()
    form.id_rol.choices = _choices_roles(empresa.id)

    if form.validate_on_submit():
        username = form.username.data.strip()

        if Usuario.query.filter_by(
            username=username, id_empresa=empresa.id
        ).first():
            flash(
                f'Ya existe un usuario con el username "{username}" en este club.',
                'danger',
            )
            return render_template('usuarios/form.html', form=form, usuario=None)

        if not form.password.data or len(form.password.data) < 6:
            flash('La contraseña es obligatoria (mínimo 6 caracteres).', 'danger')
            return render_template('usuarios/form.html', form=form, usuario=None)

        usuario = Usuario(
            nombre=form.nombre.data.strip(),
            username=username,
            activo=form.activo.data,
            id_empresa=empresa.id,
            es_super_admin=False,
            id_rol=form.id_rol.data if form.id_rol.data else None,
        )
        usuario.set_password(form.password.data)
        db.session.add(usuario)
        db.session.flush()
        auditar('crear', 'Usuario', usuario.id, despues=usuario)
        db.session.commit()
        flash(f'Usuario "{usuario.username}" creado.', 'success')
        return redirect(url_for('usuarios.listar'))

    return render_template('usuarios/form.html', form=form, usuario=None)


@usuarios_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@requiere_super_admin
def editar(id):
    empresa = current_empresa()
    usuario = Usuario.query.get_or_404(id)
    if usuario.id_empresa != empresa.id:
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('usuarios.listar'))

    # No se puede editar a un super admin desde acá (para evitar lock-outs)
    if usuario.es_super_admin:
        flash(
            'Este usuario es super administrador. No se puede editar '
            'desde esta pantalla.',
            'warning'
        )
        return redirect(url_for('usuarios.listar'))

    form = UsuarioForm(obj=usuario)
    form.id_rol.choices = _choices_roles(empresa.id)

    if form.validate_on_submit():
        username = form.username.data.strip()
        otro = Usuario.query.filter_by(
            username=username, id_empresa=empresa.id
        ).first()
        if otro and otro.id != usuario.id:
            flash(
                f'Ya existe otro usuario con el username "{username}" en este club.',
                'danger',
            )
            return render_template('usuarios/form.html', form=form, usuario=usuario)

        # Snapshot del estado ANTES
        estado_antes = {
            'nombre': usuario.nombre,
            'username': usuario.username,
            'activo': usuario.activo,
            'id_rol': usuario.id_rol,
        }

        usuario.nombre = form.nombre.data.strip()
        usuario.username = username
        usuario.activo = form.activo.data
        usuario.id_rol = form.id_rol.data if form.id_rol.data else None

        # Cambio de contraseña: solo si se especificó una nueva
        if form.password.data:
            if len(form.password.data) < 6:
                flash('La nueva contraseña debe tener al menos 6 caracteres.',
                      'danger')
                return render_template('usuarios/form.html', form=form,
                                       usuario=usuario)
            usuario.set_password(form.password.data)
            cambio_password = True
        else:
            cambio_password = False

        db.session.flush()
        auditar(
            'editar', 'Usuario', usuario.id,
            antes=estado_antes, despues=usuario,
            nota='Cambió contraseña' if cambio_password else None,
        )
        db.session.commit()
        flash('Usuario actualizado.', 'success')
        return redirect(url_for('usuarios.listar'))

    return render_template('usuarios/form.html', form=form, usuario=usuario)


@usuarios_bp.route('/<int:id>/eliminar', methods=['POST'])
@requiere_super_admin
def eliminar(id):
    empresa = current_empresa()
    usuario = Usuario.query.get_or_404(id)
    if usuario.id_empresa != empresa.id:
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('usuarios.listar'))

    if usuario.es_super_admin:
        flash('No se puede eliminar a un super administrador.', 'danger')
        return redirect(url_for('usuarios.listar'))

    # No podés eliminarte a vos mismo
    actual = cargar_usuario_actual()
    if actual and actual.id == usuario.id:
        flash('No podés eliminar tu propio usuario.', 'danger')
        return redirect(url_for('usuarios.listar'))

    # Snapshot ANTES de borrar (después ya no existe el objeto)
    datos_usuario = {
        'nombre': usuario.nombre,
        'username': usuario.username,
        'id_empresa': usuario.id_empresa,
        'activo': usuario.activo,
        'id_rol': usuario.id_rol,
    }
    auditar(
        'eliminar', 'Usuario', usuario.id,
        antes=datos_usuario,
        nota=f'Usuario "{usuario.username}" eliminado',
    )
    db.session.delete(usuario)
    db.session.commit()
    flash('Usuario eliminado.', 'success')
    return redirect(url_for('usuarios.listar'))