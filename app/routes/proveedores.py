# app/routes/proveedores.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, current_app,
)
from app import db
from app.models import Proveedor, Compra
from app.forms import ProveedorForm
from app.utils import current_empresa
from decimal import Decimal
from sqlalchemy import or_
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar

proveedores_bp = Blueprint('proveedores', __name__, url_prefix='/proveedores')


# ---------- CRUD ----------

@proveedores_bp.route('/')
@requiere_ver('proveedores')
def listar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    q = request.args.get('q', '').strip()
    ver_inactivos = request.args.get('ver_inactivos', '0') == '1'

    base = Proveedor.query.filter_by(id_empresa=empresa.id)
    if not ver_inactivos:
        base = base.filter(or_(
            Proveedor.activo.is_(True),
            Proveedor.activo.is_(None),
        ))
    if q:
        base = base.filter(or_(
            Proveedor.nombre.ilike(f'%{q}%'),
            Proveedor.cuit.ilike(f'%{q}%'),
        ))

    proveedores = base.order_by(Proveedor.nombre).all()

    deudas = {}
    for p in proveedores:
        total = Decimal('0')
        for c in p.compras:
            if c.id_empresa != empresa.id:
                continue
            # Total compra - notas de crédito - pagos
            total += c.total_neto - sum(
                (pg.monto for pg in c.pagos), Decimal('0')
            )
        deudas[p.id] = total

    return render_template(
        'proveedores/listar.html',
        proveedores=proveedores,
        deudas=deudas,
        q=q,
        ver_inactivos=ver_inactivos,
    )


@proveedores_bp.route('/nueva', methods=['GET', 'POST'])
@requiere_editar('proveedores')
def nueva():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = ProveedorForm()
    if form.validate_on_submit():
        prov = Proveedor(
            nombre=form.nombre.data.strip(),
            cuit=(form.cuit.data or '').strip() or None,
            condicion_iva=form.condicion_iva.data or None,
            direccion=(form.direccion.data or '').strip() or None,
            telefono=(form.telefono.data or '').strip() or None,
            email=(form.email.data or '').strip() or None,
            notas=(form.notas.data or '').strip() or None,
            activo=form.activo.data,
            id_empresa=empresa.id,
        )
        db.session.add(prov)
        db.session.flush()
        auditar('crear', 'Proveedor', prov.id, despues=prov)
        db.session.commit()
        flash(f'Proveedor "{prov.nombre}" creado.', 'success')
        return redirect(url_for('proveedores.listar'))

    return render_template('proveedores/form.html', form=form, proveedor=None)


@proveedores_bp.route('/<int:id>')
@requiere_ver('proveedores')
def detalle(id):
    empresa = current_empresa()
    if not empresa:
        return redirect(url_for('main.index'))

    prov = Proveedor.query.get_or_404(id)
    if prov.id_empresa != empresa.id:
        flash('Proveedor no encontrado.', 'danger')
        return redirect(url_for('proveedores.listar'))

    compras = (
        Compra.query
        .filter_by(id_proveedor=prov.id, id_empresa=empresa.id)
        .order_by(Compra.fecha.asc(), Compra.id.asc())
        .all()
    )

    eventos = []
    for c in compras:
        eventos.append({
            'fecha': c.fecha,
            'tipo': 'compra',
            'descripcion': f'Compra #{c.id}',
            'detalle': f'{len(c.detalles)} ítem(s)' if c.detalles else '',
            'compra': c,
            'debe': c.total or 0,
            'haber': 0,
        })
        for p in c.pagos:
            eventos.append({
                'fecha': p.fecha_pago or c.fecha,
                'tipo': 'pago',
                'descripcion': f'Pago Compra #{c.id}',
                'detalle': (p.metodo or '').replace('_', ' ').capitalize(),
                'compra': c,
                'pago': p,
                'debe': 0,
                'haber': p.monto or 0,
            })
        # Notas de crédito vigentes (reducen el saldo con el proveedor)
        for n in c.notas_credito:
            if n.anulada:
                continue
            eventos.append({
                'fecha': n.fecha,
                'tipo': 'nota_credito',
                'descripcion': f'Nota de crédito #{n.id} (Compra #{c.id})',
                'detalle': (n.motivo or '')[:60],
                'compra': c,
                'nota': n,
                'debe': 0,
                'haber': n.monto or 0,
            })

    def _sort_key(ev):
        return (ev['fecha'], 0 if ev['tipo'] == 'compra' else 1)

    eventos.sort(key=_sort_key)

    saldo = 0
    for ev in eventos:
        saldo = saldo + ev['debe'] - ev['haber']
        ev['saldo'] = saldo

    total_comprado = sum((c.total or Decimal('0') for c in compras), Decimal('0'))
    total_notas = sum(
        (c.total_notas_credito for c in compras), Decimal('0')
    )
    total_pagado = sum(
        (p.monto or Decimal('0') for c in compras for p in c.pagos),
        Decimal('0')
    )
    saldo_actual = total_comprado - total_notas - total_pagado

    return render_template(
        'proveedores/detalle.html',
        proveedor=prov,
        compras=compras,
        eventos=eventos,
        total_comprado=total_comprado,
        total_pagado=total_pagado,
        total_notas=total_notas,
        saldo=saldo_actual,
    )


@proveedores_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@requiere_editar('proveedores')
def editar(id):
    empresa = current_empresa()
    if not empresa:
        return redirect(url_for('main.index'))

    prov = Proveedor.query.get_or_404(id)
    if prov.id_empresa != empresa.id:
        flash('Proveedor no encontrado.', 'danger')
        return redirect(url_for('proveedores.listar'))

    form = ProveedorForm(obj=prov)
    if form.validate_on_submit():
        estado_antes = { # <-- AGREGAR
            'nombre': prov.nombre,
            'cuit': prov.cuit,
            'email': prov.email,
            'telefono': prov.telefono,
            'activo': prov.activo,
        }        
        prov.nombre = form.nombre.data.strip()
        prov.cuit = (form.cuit.data or '').strip() or None
        prov.condicion_iva = form.condicion_iva.data or None
        prov.direccion = (form.direccion.data or '').strip() or None
        prov.telefono = (form.telefono.data or '').strip() or None
        prov.email = (form.email.data or '').strip() or None
        prov.notas = (form.notas.data or '').strip() or None
        prov.activo = form.activo.data
        db.session.flush()
        auditar('editar', 'Proveedor', prov.id, antes=estado_antes, despues=prov)
        db.session.commit()
        flash('Proveedor actualizado.', 'success')
        return redirect(url_for('proveedores.detalle', id=prov.id))

    return render_template('proveedores/form.html', form=form, proveedor=prov)


@proveedores_bp.route('/<int:id>/toggle-activo', methods=['POST'])
@requiere_editar('proveedores')
def toggle_activo(id):
    empresa = current_empresa()
    if not empresa:
        return redirect(url_for('main.index'))

    prov = Proveedor.query.get_or_404(id)
    if prov.id_empresa != empresa.id:
        flash('Proveedor no encontrado.', 'danger')
        return redirect(url_for('proveedores.listar'))

    prov.activo = not prov.activo
    db.session.flush() 
    
    accion = 'activar' if prov.activo else 'desactivar' 
    auditar(accion, 'Proveedor', prov.id, nota=f'Proveedor {accion}do manualmente')
    db.session.commit()
    flash(
        f'Proveedor {"activado" if prov.activo else "desactivado"}.',
        'success'
    )
    return redirect(url_for('proveedores.listar',
                            ver_inactivos='1' if not prov.activo else '0'))


# ---------- Cuentas por pagar ----------

@proveedores_bp.route('/cuentas-por-pagar')
@requiere_ver('proveedores')
def cuentas_por_pagar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    proveedores = (
        Proveedor.query
        .filter_by(id_empresa=empresa.id)
        .order_by(Proveedor.nombre)
        .all()
    )

    filas = []
    total_general = Decimal('0')
    for p in proveedores:
        compras_con_deuda = []
        for c in p.compras:
            if c.id_empresa != empresa.id:
                continue
            # Ahora saldo_pendiente ya descuenta notas de crédito
            saldo = c.saldo_pendiente
            if saldo > 0:
                compras_con_deuda.append(c)
        if compras_con_deuda:
            saldo_prov = sum(
                (c.saldo_pendiente for c in compras_con_deuda),
                Decimal('0')
            )
            total_general += saldo_prov
            filas.append({
                'proveedor': p,
                'compras': compras_con_deuda,
                'cantidad': len(compras_con_deuda),
                'saldo': saldo_prov,
            })

    return render_template(
        'proveedores/cuentas_por_pagar.html',
        filas=filas,
        total_general=total_general,
    )