# app/routes/roles.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
)
from app import db
from app.models import Rol, Usuario
from app.forms import RolForm
from app.decorators import requiere_super_admin
from app.utils import current_empresa
from app.permisos import MODULOS_DISPONIBLES, permisos_de_modulo
from app.auditoria import auditar

roles_bp = Blueprint('roles', __name__, url_prefix='/roles')


def _leer_permisos_de_form():
    """
    Lee los checkboxes del POST y arma la lista de permisos.

    Convención: cada checkbox se llama `perm_<modulo>_<accion>`.
    Por ejemplo: `perm_socios_ver`, `perm_cuotas_editar`.
    """
    seleccionados = []
    for clave_modulo, _ in MODULOS_DISPONIBLES:
        for accion in ('ver', 'editar'):
            campo = f'perm_{clave_modulo}_{accion}'
            if request.form.get(campo):
                seleccionados.append(f'{clave_modulo}.{accion}')
    return seleccionados


@roles_bp.route('/')
@requiere_super_admin
def listar():
    empresa = current_empresa()
    roles = (
        Rol.query
        .filter_by(id_empresa=empresa.id)
        .order_by(Rol.nombre)
        .all()
    )
    # Contar usuarios por rol
    usuarios_por_rol = {}
    for r in roles:
        usuarios_por_rol[r.id] = Usuario.query.filter_by(id_rol=r.id).count()

    return render_template(
        'roles/listar.html',
        roles=roles,
        usuarios_por_rol=usuarios_por_rol,
    )


@roles_bp.route('/nuevo', methods=['GET', 'POST'])
@requiere_super_admin
def nuevo():
    empresa = current_empresa()
    form = RolForm()

    if form.validate_on_submit():
        nombre = form.nombre.data.strip()

        if Rol.query.filter_by(nombre=nombre, id_empresa=empresa.id).first():
            flash(f'Ya existe un rol con el nombre "{nombre}".', 'danger')
            return render_template(
                'roles/form.html',
                form=form,
                modulos=MODULOS_DISPONIBLES,
                permisos_actuales=[],
                rol=None,
            )

        rol = Rol(
            nombre=nombre,
            activo=form.activo.data,
            id_empresa=empresa.id,
        )
        rol.set_permisos(_leer_permisos_de_form())
        db.session.add(rol)
        db.session.flush()
        auditar('crear', 'Rol', rol.id, despues=rol)
        db.session.commit()
        flash(f'Rol "{rol.nombre}" creado.', 'success')
        return redirect(url_for('roles.listar'))

    return render_template(
        'roles/form.html',
        form=form,
        modulos=MODULOS_DISPONIBLES,
        permisos_actuales=[],
        rol=None,
    )


@roles_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@requiere_super_admin
def editar(id):
    empresa = current_empresa()
    rol = Rol.query.get_or_404(id)
    if rol.id_empresa != empresa.id:
        flash('Rol no encontrado.', 'danger')
        return redirect(url_for('roles.listar'))

    form = RolForm(obj=rol)
    if form.validate_on_submit():
        nombre = form.nombre.data.strip()

        otro = Rol.query.filter_by(nombre=nombre, id_empresa=empresa.id).first()
        if otro and otro.id != rol.id:
            flash(f'Ya existe otro rol con el nombre "{nombre}".', 'danger')
            return render_template(
                'roles/form.html',
                form=form,
                modulos=MODULOS_DISPONIBLES,
                permisos_actuales=rol.permisos_list,
                rol=rol,
            )

        # Snapshot del estado ANTES
        estado_antes = {
            'nombre': rol.nombre,
            'activo': rol.activo,
            'permisos': rol.permisos_list,
        }

        rol.nombre = nombre
        rol.activo = form.activo.data
        rol.set_permisos(_leer_permisos_de_form())

        db.session.flush()
        auditar(
            'editar', 'Rol', rol.id,
            antes=estado_antes, despues=rol,
        )
        db.session.commit()
        flash('Rol actualizado.', 'success')
        return redirect(url_for('roles.listar'))

    return render_template(
        'roles/form.html',
        form=form,
        modulos=MODULOS_DISPONIBLES,
        permisos_actuales=rol.permisos_list,
        rol=rol,
    )


@roles_bp.route('/<int:id>/eliminar', methods=['POST'])
@requiere_super_admin
def eliminar(id):
    empresa = current_empresa()
    rol = Rol.query.get_or_404(id)
    if rol.id_empresa != empresa.id:
        flash('Rol no encontrado.', 'danger')
        return redirect(url_for('roles.listar'))

    usuarios = Usuario.query.filter_by(id_rol=rol.id).count()
    if usuarios > 0:
        flash(
            f'No se puede eliminar el rol "{rol.nombre}" porque tiene '
            f'{usuarios} usuario(s) asignado(s). Cambiá el rol de esos '
            f'usuarios primero.',
            'danger'
        )
        return redirect(url_for('roles.listar'))

    datos_rol = {
        'nombre': rol.nombre,
        'activo': rol.activo,
        'permisos': rol.permisos_list,
        'id_empresa': rol.id_empresa,
    }
    auditar(
        'eliminar', 'Rol', rol.id,
        antes=datos_rol,
        nota=f'Rol "{rol.nombre}" eliminado',
    )
    db.session.delete(rol)
    db.session.commit()
    flash('Rol eliminado.', 'success')
    return redirect(url_for('roles.listar'))