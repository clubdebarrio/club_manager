# app/routes/compras.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    jsonify, current_app, send_file, session,
)
from app import db
from app.models import (
    Producto, Compra, DetalleCompra, CategoriaProducto,
    CuentaBancaria, Cheque, Proveedor, PagoCompra,
    Movimiento,
)
from app.forms import CompraForm, AgregarProductoCompraForm
from datetime import datetime
from decimal import Decimal
import json
import pandas as pd
from app.utils import fecha_hora_efectiva, current_empresa, local_to_utc, utc_to_local
from io import BytesIO
from sqlalchemy import or_
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar

compras_bp = Blueprint('compras', __name__, url_prefix='/compras')


# ============================================================
# Listado de compras
# ============================================================

@compras_bp.route('/')
@requiere_ver('compras')
def listar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 20)
    query = request.args.get('q', '').strip()

    ver_anuladas = request.args.get('ver_anuladas', '0') == '1'

    base_query = Compra.query.filter_by(id_empresa=empresa.id)
    if ver_anuladas:
        base_query = base_query.filter(Compra.estado == 'anulada')
    else:
        base_query = base_query.filter(Compra.estado == 'completada')

    if query:
        # Buscar por ID o por nombre de proveedor (string legacy o FK)
        condiciones = [Compra.proveedor.ilike(f'%{query}%')]
        if query.isdigit():
            condiciones.append(Compra.id == int(query))
        base_query = (
            base_query
            .outerjoin(Proveedor, Compra.id_proveedor == Proveedor.id)
            .filter(or_(
                *condiciones,
                Proveedor.nombre.ilike(f'%{query}%'),
            ))
        )

    pagination = base_query.order_by(
        Compra.fecha.desc(),
        Compra.id.desc()
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )
    compras = pagination.items

    return render_template(
        'compras/listar.html',
        compras=compras,
        pagination=pagination,
        query=query,
        ver_anuladas=ver_anuladas,
    )


# ============================================================
# Nueva compra
# ============================================================

@compras_bp.route('/nueva', methods=['GET', 'POST'])
@requiere_editar('compras')
def nueva():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuentas = CuentaBancaria.query.filter(
        CuentaBancaria.activo == True,
        CuentaBancaria.id_empresa == empresa.id,
        CuentaBancaria.tipo != 'cartera',
    ).all()
    cuentas_tipo = {c.id: c.tipo for c in cuentas}

    cheques_tercero = Cheque.query.filter_by(
        id_empresa=empresa.id, tipo='recibido', estado='en_cartera'
    ).order_by(Cheque.fecha_cobro).all()

    productos = Producto.query.filter_by(
        activo=True, id_empresa=empresa.id
    ).order_by(Producto.nombre).all()

    proveedores = Proveedor.query.filter_by(
        id_empresa=empresa.id, activo=True
    ).order_by(Proveedor.nombre).all()

    form = CompraForm()

    carrito_inicial = []
    pagos_iniciales = []
    if request.method == 'POST':
        try:
            carrito_inicial = json.loads(request.form.get('productos_json', '[]')) or []
        except Exception:
            carrito_inicial = []
        try:
            pagos_iniciales = json.loads(request.form.get('pagos_json', '[]')) or []
        except Exception:
            pagos_iniciales = []

    def _render_error():
        return render_template(
            'compras/nueva.html',
            form=form,
            productos=productos,
            proveedores=proveedores,
            cuentas=cuentas,
            cuentas_tipo=cuentas_tipo,
            cheques_tercero=cheques_tercero,
            carrito_inicial=carrito_inicial,
            pagos_iniciales=pagos_iniciales,
            empresa_nombre=empresa.nombre,
        )

    if form.validate_on_submit():
        # ---------- 1. Parsear y validar carrito ----------
        try:
            carrito = json.loads(request.form.get('productos_json', '[]'))
        except Exception:
            carrito = []

        if not carrito:
            flash('Agregá al menos un producto al carrito.', 'warning')
            return _render_error()

        total = Decimal('0')
        detalles = []
        for item in carrito:
            producto = Producto.query.get(item['id'])
            if not producto or producto.id_empresa != empresa.id:
                flash(f'Producto no encontrado: {item.get("nombre")}', 'danger')
                return _render_error()
            cantidad = int(item['cantidad'])
            if cantidad <= 0:
                continue
            precio_compra = Decimal(str(item['precio_compra']))
            if precio_compra <= 0:
                flash(f'Precio inválido para "{producto.nombre}"', 'danger')
                return _render_error()
            subtotal = precio_compra * cantidad
            total += subtotal
            detalles.append({
                'producto': producto, 'cantidad': cantidad,
                'precio_compra': precio_compra, 'subtotal': subtotal,
            })

        if total <= 0:
            flash('El total debe ser mayor a 0.', 'warning')
            return _render_error()

        # ---------- 2. Parsear y validar pagos ----------
        try:
            pagos_data = json.loads(request.form.get('pagos_json', '[]'))
        except Exception:
            pagos_data = []

        if not pagos_data:
            flash('Agregá al menos un medio de pago.', 'warning')
            return _render_error()

        total_pagado = Decimal('0')
        for p in pagos_data:
            try:
                total_pagado += Decimal(str(p.get('monto', 0)))
            except Exception:
                pass

        if total_pagado > total:
            flash(
                f'El total de los pagos (${total_pagado}) supera el total '
                f'de la compra (${total}).',
                'danger'
            )
            return _render_error()

        deuda = total - total_pagado

        # ---------- 3. Crear todo en una transacción ----------
        try:
            from app.services.cuenta_contable_resolver import CuentaContableResolver
            from app.services.caja_service import CajaService
            from app.services.contabilidad_service import ContabilidadService

            cuenta_mercaderia = CuentaContableResolver.resolver_o_fallar(
                empresa.id, 'compra_mercaderia'
            )
            fecha_efectiva = local_to_utc(fecha_hora_efectiva(form.fecha.data))

            id_prov = _resolver_proveedor(empresa, form)
            prov_nombre = None
            if id_prov:
                p = Proveedor.query.get(id_prov)
                if p:
                    prov_nombre = p.nombre
            else:
                prov_nombre = (form.proveedor.data or '').strip() or None

            compra = Compra(
                fecha=fecha_efectiva,
                proveedor=prov_nombre,
                id_proveedor=id_prov,
                total=total,
                metodo_pago='mixto',
                id_cuenta=None,
                id_cuenta_contable=cuenta_mercaderia.id,
                id_empresa=empresa.id,
                estado='completada',
            )
            db.session.add(compra)
            db.session.flush()

            for det in detalles:
                db.session.add(DetalleCompra(
                    id_compra=compra.id,
                    id_producto=det['producto'].id,
                    cantidad=det['cantidad'],
                    precio_compra=det['precio_compra'],
                    subtotal=det['subtotal'],
                ))
                det['producto'].stock_actual += det['cantidad']
                det['producto'].precio_costo = det['precio_compra']

            pagos_creados = []
            for pdata in pagos_data:
                metodo = pdata.get('metodo')
                monto = Decimal(str(pdata.get('monto', 0)))
                if monto <= 0:
                    raise ValueError('Todos los pagos deben tener monto mayor a 0.')

                pago = PagoCompra(
                    id_compra=compra.id, metodo=metodo, monto=monto,
                )

                # -- Efectivo / transferencia / débito / crédito --
                if metodo in ('efectivo', 'transferencia', 'debito', 'credito'):
                    id_cuenta = pdata.get('id_cuenta')
                    cuenta_pago = CuentaBancaria.query.get(id_cuenta) if id_cuenta else None
                    if not cuenta_pago or cuenta_pago.id_empresa != empresa.id:
                        raise ValueError('Cuenta de pago inválida.')
                    if not cuenta_pago.id_cuenta_contable:
                        raise ValueError(
                            f'La cuenta "{cuenta_pago.nombre}" no tiene cuenta '
                            f'contable asignada.'
                        )
                    if metodo == 'efectivo' and cuenta_pago.tipo != 'efectivo':
                        raise ValueError(
                            'Para efectivo, elegí una cuenta de tipo Caja Chica.'
                        )
                    if metodo in ('transferencia', 'debito', 'credito') \
                            and cuenta_pago.tipo != 'banco':
                        raise ValueError(
                            f'Para {metodo}, elegí una cuenta bancaria.'
                        )

                    pago.id_cuenta = cuenta_pago.id

                    CajaService.registrar_movimiento({
                        'id_cuenta': cuenta_pago.id,
                        'id_cuenta_contable': cuenta_mercaderia.id,
                        'fecha': fecha_efectiva,
                        'tipo': 'egreso',
                        'monto': monto,
                        'descripcion': f'Compra #{compra.id} - pago {metodo}',
                        'metodo_pago': metodo,
                        'comprobante': f'Compra #{compra.id}',
                        'id_empresa': empresa.id,
                        'conciliado': metodo == 'efectivo',
                        '_generar_asiento': False,
                    }, commit=False)

                # -- Cheque propio --
                elif metodo == 'cheque_propio':
                    ch = pdata.get('cheque') or {}
                    numero = (ch.get('numero') or '').strip()
                    banco = (ch.get('banco_emisor') or '').strip()
                    fe = ch.get('fecha_emision') or ''
                    fc = ch.get('fecha_cobro') or ''
                    if not all([numero, banco, fe, fc]):
                        raise ValueError(
                            'Completá todos los datos del cheque propio.'
                        )
                    try:
                        fecha_emision = datetime.strptime(fe, '%Y-%m-%d').date()
                        fecha_cobro = datetime.strptime(fc, '%Y-%m-%d').date()
                    except ValueError:
                        raise ValueError('Fechas del cheque propio inválidas.')

                    cheque = Cheque(
                        numero=numero, banco_emisor=banco,
                        titular=empresa.nombre, monto=monto,
                        fecha_emision=fecha_emision,
                        fecha_cobro=fecha_cobro,
                        tipo='emitido', estado='pendiente',
                        id_empresa=empresa.id,
                    )
                    db.session.add(cheque)
                    db.session.flush()
                    pago.id_cheque = cheque.id

                # -- Cheque de tercero --
                elif metodo == 'cheque_tercero':
                    id_ch = pdata.get('id_cheque_tercero')
                    cheque = Cheque.query.get(id_ch) if id_ch else None
                    if not cheque or cheque.id_empresa != empresa.id:
                        raise ValueError('Cheque de tercero no encontrado.')
                    if cheque.tipo != 'recibido' or cheque.estado != 'en_cartera':
                        raise ValueError(
                            'El cheque de tercero no está disponible.'
                        )
                    if cheque.monto != monto:
                        raise ValueError(
                            f'El cheque #{cheque.numero} es de ${cheque.monto} '
                            f'pero cargaste ${monto}. Ajustá el monto.'
                        )
                    cheque.estado = 'entregado'
                    pago.id_cheque = cheque.id

                    cartera = CuentaBancaria.query.filter_by(
                        id_empresa=empresa.id, tipo='cartera', activo=True
                    ).first()
                    if cartera:
                        CajaService.registrar_movimiento({
                            'id_cuenta': cartera.id,
                            'id_cuenta_contable': None,
                            'fecha': fecha_efectiva,
                            'tipo': 'egreso',
                            'monto': monto,
                            'descripcion': (
                                f'Cheque #{cheque.numero} entregado a proveedor '
                                f'(Compra #{compra.id})'
                            ),
                            'metodo_pago': 'cheque',
                            'comprobante': cheque.numero,
                            'id_cheque': cheque.id,
                            'id_empresa': empresa.id,
                            'conciliado': False,
                            '_forzar_cuenta': True,
                            '_generar_asiento': False,
                            '_skip_mirror': True,
                        }, commit=False)
                    else:
                        raise ValueError(
                            'No existe la Cartera de Cheques. '
                            'Corré: flask caja:seed-cartera-cheques'
                        )

                else:
                    raise ValueError(f'Método de pago desconocido: {metodo}')

                db.session.add(pago)
                pagos_creados.append(pago)

            db.session.flush()

            ContabilidadService.generar_asiento_compra_mixta(
                compra, pagos_creados, deuda
            )

            # --- Auditoría: compra + pagos + movimientos/cheques generados ---
            auditar('crear', 'Compra', compra.id, despues=compra)

            for pago in pagos_creados:
                auditar('crear', 'PagoCompra', pago.id, despues=pago)

                # Si el pago fue con cheque propio, quedó un Cheque creado
                if pago.metodo == 'cheque_propio' and pago.id_cheque:
                    cheque = Cheque.query.get(pago.id_cheque)
                    if cheque:
                        auditar('crear', 'Cheque', cheque.id, despues=cheque)

                # Si el pago fue con cheque de tercero, quedó un Cheque
                # modificado (estado: en_cartera → entregado)
                if pago.metodo == 'cheque_tercero' and pago.id_cheque:
                    cheque = Cheque.query.get(pago.id_cheque)
                    if cheque:
                        auditar(
                            'entregar', 'Cheque', cheque.id,
                            nota=f'Entregado a proveedor por Compra #{compra.id}',
                        )

            # Movimientos de caja generados por pagos en efectivo/transferencia
            # o por la cartera al entregar un cheque de tercero.
            # Buscamos los movimientos que apunten a esta compra.
            movs_compra = Movimiento.query.filter(
                Movimiento.id_empresa == empresa.id,
                Movimiento.descripcion.like(f'%Compra #{compra.id}%'),
            ).all()
            for m in movs_compra:
                auditar('crear', 'Movimiento', m.id, despues=m)

            db.session.commit()

            msg = f'✅ Compra #{compra.id} registrada. Total: ${total:.2f}.'
            if deuda > 0:
                msg += f' Queda debiendo ${deuda:.2f} a proveedores.'
            flash(msg, 'success')
            return redirect(url_for('compras.listar'))

        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'danger')
            return _render_error()
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Error al registrar compra mixta')
            flash(f'Error al registrar la compra: {str(e)}', 'danger')
            return _render_error()

    return _render_error()


# ============================================================
# Detalle y pago
# ============================================================

@compras_bp.route('/<int:id>')
@requiere_ver('compras')
def detalle(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    compra = Compra.query.get_or_404(id)
    if compra.id_empresa != empresa.id:
        flash('Compra no encontrada.', 'danger')
        return redirect(url_for('compras.listar'))

    from app.services.nota_credito_service import NotaCreditoService

    # Info de máximos devolvibles (para el botón "Nueva nota de crédito")
    productos_info = []
    for det in compra.detalles:
        maximo = NotaCreditoService.calcular_devolucion_maxima(det)
        productos_info.append({
            'detalle': det,
            'maximo_devolvible': maximo,
        })

    return render_template(
        'compras/detalle.html',
        compra=compra,
        productos_info=productos_info,
    )


@compras_bp.route('/<int:id>/pagar', methods=['GET', 'POST'])
@requiere_editar('compras')
def pagar_compra(id):
    """Registra un pago (parcial o total) contra una compra existente."""
    from app.services.contabilidad_service import ContabilidadService
    from app.services.caja_service import CajaService

    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    compra = Compra.query.get_or_404(id)
    if compra.id_empresa != empresa.id:
        flash('Compra no encontrada.', 'danger')
        return redirect(url_for('compras.listar'))

    saldo = compra.saldo_pendiente
    if saldo <= 0:
        flash('Esta compra ya está pagada.', 'info')
        return redirect(url_for('compras.detalle', id=compra.id))

    cuentas = CuentaBancaria.query.filter(
        CuentaBancaria.activo == True,
        CuentaBancaria.id_empresa == empresa.id,
        CuentaBancaria.tipo != 'cartera',
    ).all()

    cheques_tercero = Cheque.query.filter_by(
        id_empresa=empresa.id, tipo='recibido', estado='en_cartera'
    ).order_by(Cheque.fecha_cobro).all()

    if request.method == 'POST':
        try:
            monto = Decimal(str(request.form.get('monto', '0')))
        except Exception:
            flash('Monto inválido.', 'danger')
            return redirect(url_for('compras.pagar_compra', id=id))

        metodo = (request.form.get('metodo') or '').strip()
        observaciones = (request.form.get('observaciones') or '').strip() or None
        fecha_pago_str = request.form.get('fecha_pago') or ''

        if monto <= 0:
            flash('El monto debe ser mayor a 0.', 'danger')
            return redirect(url_for('compras.pagar_compra', id=id))
        if monto > saldo:
            flash(
                f'El monto (${monto}) supera el saldo pendiente (${saldo}).',
                'danger'
            )
            return redirect(url_for('compras.pagar_compra', id=id))
        if not metodo:
            flash('Elegí un método de pago.', 'danger')
            return redirect(url_for('compras.pagar_compra', id=id))

        fecha_pago = datetime.utcnow()
        if fecha_pago_str:
            try:
                fecha_base = datetime.strptime(fecha_pago_str, '%Y-%m-%d').date()
                # Convertimos a UTC antes de guardar
                fecha_pago = local_to_utc(fecha_hora_efectiva(fecha_base))
            except ValueError:
                pass

        try:
            pago = PagoCompra(
                id_compra=compra.id,
                metodo=metodo,
                monto=monto,
                fecha_pago=fecha_pago,
                observaciones=observaciones,
            )

            # ---------- Efectivo / transferencia / débito / crédito ----------
            if metodo in ('efectivo', 'transferencia', 'debito', 'credito'):
                id_cuenta = request.form.get('id_cuenta', type=int)
                cuenta_pago = CuentaBancaria.query.get(id_cuenta) if id_cuenta else None
                if not cuenta_pago or cuenta_pago.id_empresa != empresa.id:
                    raise ValueError('Cuenta de pago inválida.')
                if not cuenta_pago.id_cuenta_contable:
                    raise ValueError(
                        f'La cuenta "{cuenta_pago.nombre}" no tiene cuenta '
                        f'contable asignada.'
                    )
                if metodo == 'efectivo' and cuenta_pago.tipo != 'efectivo':
                    raise ValueError(
                        'Para efectivo, elegí una cuenta de tipo Caja Chica.'
                    )
                if metodo in ('transferencia', 'debito', 'credito') \
                        and cuenta_pago.tipo != 'banco':
                    raise ValueError(
                        f'Para {metodo}, elegí una cuenta bancaria.'
                    )

                pago.id_cuenta = cuenta_pago.id

                CajaService.registrar_movimiento({
                    'id_cuenta': cuenta_pago.id,
                    'id_cuenta_contable': None,
                    'fecha': fecha_pago,
                    'tipo': 'egreso',
                    'monto': monto,
                    'descripcion': (
                        f'Pago deuda Compra #{compra.id}'
                        + (f' - {compra.proveedor_nombre}' if compra.proveedor_nombre else '')
                    ),
                    'metodo_pago': metodo,
                    'comprobante': f'Compra #{compra.id}',
                    'id_empresa': empresa.id,
                    'conciliado': metodo == 'efectivo',
                    '_generar_asiento': False,
                }, commit=False)

            # ---------- Cheque propio ----------
            elif metodo == 'cheque_propio':
                numero = (request.form.get('cheque_numero') or '').strip()
                banco = (request.form.get('cheque_banco_emisor') or '').strip()
                fe_str = request.form.get('cheque_fecha_emision') or ''
                fc_str = request.form.get('cheque_fecha_cobro') or ''
                if not all([numero, banco, fe_str, fc_str]):
                    raise ValueError('Completá todos los datos del cheque.')
                try:
                    fecha_emision = datetime.strptime(fe_str, '%Y-%m-%d').date()
                    fecha_cobro = datetime.strptime(fc_str, '%Y-%m-%d').date()
                except ValueError:
                    raise ValueError('Fechas del cheque inválidas.')

                cheque = Cheque(
                    numero=numero,
                    banco_emisor=banco,
                    titular=empresa.nombre,
                    monto=monto,
                    fecha_emision=fecha_emision,
                    fecha_cobro=fecha_cobro,
                    tipo='emitido',
                    estado='pendiente',
                    id_empresa=empresa.id,
                )
                db.session.add(cheque)
                db.session.flush()
                pago.id_cheque = cheque.id

            # ---------- Cheque de tercero ----------
            elif metodo == 'cheque_tercero':
                id_ch = request.form.get('id_cheque_tercero', type=int)
                cheque = Cheque.query.get(id_ch) if id_ch else None
                if not cheque or cheque.id_empresa != empresa.id:
                    raise ValueError('Cheque de tercero no encontrado.')
                if cheque.tipo != 'recibido' or cheque.estado != 'en_cartera':
                    raise ValueError('El cheque no está disponible.')
                if cheque.monto != monto:
                    raise ValueError(
                        f'El cheque #{cheque.numero} es de ${cheque.monto} '
                        f'pero cargaste ${monto}. Ajustá el monto.'
                    )
                cheque.estado = 'entregado'
                pago.id_cheque = cheque.id

                cartera = CuentaBancaria.query.filter_by(
                    id_empresa=empresa.id, tipo='cartera', activo=True
                ).first()
                if cartera:
                    CajaService.registrar_movimiento({
                        'id_cuenta': cartera.id,
                        'id_cuenta_contable': None,
                        'fecha': fecha_pago,
                        'tipo': 'egreso',
                        'monto': monto,
                        'descripcion': (
                            f'Cheque #{cheque.numero} entregado a proveedor '
                            f'(Pago deuda Compra #{compra.id})'
                        ),
                        'metodo_pago': 'cheque',
                        'comprobante': cheque.numero,
                        'id_cheque': cheque.id,
                        'id_empresa': empresa.id,
                        'conciliado': False,
                        '_forzar_cuenta': True,
                        '_generar_asiento': False,
                        '_skip_mirror': True,
                    }, commit=False)
                else:
                    raise ValueError(
                        'No existe la Cartera de Cheques. '
                        'Corré: flask caja:seed-cartera-cheques'
                    )

            else:
                raise ValueError(f'Método desconocido: {metodo}')

            db.session.add(pago)
            db.session.flush()

            ContabilidadService.generar_asiento_pago_proveedor(pago, compra)

            # --- Auditoría ---
            auditar('crear', 'PagoCompra', pago.id, despues=pago)

            if pago.metodo == 'cheque_propio' and pago.id_cheque:
                cheque = Cheque.query.get(pago.id_cheque)
                if cheque:
                    auditar('crear', 'Cheque', cheque.id, despues=cheque)

            if pago.metodo == 'cheque_tercero' and pago.id_cheque:
                cheque = Cheque.query.get(pago.id_cheque)
                if cheque:
                    auditar(
                        'entregar', 'Cheque', cheque.id,
                        nota=f'Entregado a proveedor por pago de Compra #{compra.id}',
                    )

            movs_pago = Movimiento.query.filter(
                Movimiento.id_empresa == empresa.id,
                Movimiento.descripcion.like(f'%Compra #{compra.id}%'),
            ).all()
            for m in movs_pago:
                auditar('crear', 'Movimiento', m.id, despues=m)

            db.session.commit()

            nuevo_saldo = compra.saldo_pendiente
            msg = f'Pago de ${monto:.2f} registrado.'
            if nuevo_saldo > 0:
                msg += f' Saldo pendiente: ${nuevo_saldo:.2f}.'
            else:
                msg += ' Deuda saldada.'
            flash(msg, 'success')

            if compra.id_proveedor:
                return redirect(url_for('proveedores.detalle',
                                        id=compra.id_proveedor))
            return redirect(url_for('compras.detalle', id=compra.id))

        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'danger')
            return redirect(url_for('compras.pagar_compra', id=id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Error al registrar pago de compra')
            flash(f'Error al registrar el pago: {str(e)}', 'danger')
            return redirect(url_for('compras.pagar_compra', id=id))

    return render_template(
        'compras/pagar_compra.html',
        compra=compra,
        saldo=saldo,
        cuentas=cuentas,
        cheques_tercero=cheques_tercero,
        empresa_nombre=empresa.nombre,
        now=utc_to_local(datetime.utcnow()),
    )


# ============================================================
# Productos - API y plantilla
# ============================================================

@compras_bp.route('/productos/api/buscar')
@requiere_ver('compras')
def api_buscar_productos():
    query = request.args.get('q', '')
    empresa = current_empresa()
    if not empresa:
        return jsonify([])

    productos = Producto.query.filter(
        Producto.id_empresa == empresa.id,
        Producto.activo == True,
        Producto.nombre.ilike(f'%{query}%')
    ).order_by(Producto.nombre).limit(20).all()

    return jsonify([{
        'id': p.id,
        'nombre': p.nombre,
        'stock_actual': p.stock_actual
    } for p in productos])


@compras_bp.route('/productos/plantilla')
@requiere_ver('compras')
def descargar_plantilla_productos():
    """Genera y descarga una plantilla Excel para importar productos."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    categorias_existentes = set()
    for p in Producto.query.filter_by(id_empresa=empresa.id).all():
        if p.categoria:
            categorias_existentes.add(p.categoria)

    wb = Workbook()
    ws = wb.active
    ws.title = "Productos"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F75B5", end_color="2F75B5", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    headers = ['nombre', 'descripcion', 'categoria', 'precio_venta', 'precio_costo', 'stock_actual', 'stock_minimo', 'activo']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    if categorias_existentes:
        formula = '"' + ','.join(sorted(categorias_existentes)) + '"'
        dv = DataValidation(type="list", formula1=formula, allow_blank=True)
        ws.add_data_validation(dv)
        dv.add('C2:C1048576')

    dv_activo = DataValidation(type="list", formula1='"Sí,No"', allow_blank=True)
    ws.add_data_validation(dv_activo)
    dv_activo.add('H2:H1048576')

    ws.column_dimensions['A'].width = 30
    ws.column_dimensions['B'].width = 40
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 15
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 12
    ws.column_dimensions['G'].width = 12
    ws.column_dimensions['H'].width = 12

    ws2 = wb.create_sheet("Instrucciones")
    instrucciones = [
        ("INSTRUCCIONES PARA IMPORTAR PRODUCTOS", ""),
        ("", ""),
        ("Campos obligatorios (*)", ""),
        ("nombre", "Nombre del producto (obligatorio)"),
        ("precio_venta", "Precio de venta (obligatorio, mayor a 0)"),
        ("", ""),
        ("Campos opcionales", ""),
        ("descripcion", "Descripción del producto"),
        ("categoria", "Categoría del producto (puede ser vacío, o usar las existentes)"),
        ("precio_costo", "Precio de costo (para cálculo de margen)"),
        ("stock_actual", "Cantidad en stock (default 0)"),
        ("stock_minimo", "Stock mínimo para alerta (default 0)"),
        ("activo", "Sí/No (default Sí)"),
        ("", ""),
        ("⚠️ NOTAS:", ""),
        ("- Si el producto ya existe (mismo nombre), se actualizarán sus datos.", ""),
        ("- Los campos numéricos deben usar punto (.) como separador decimal.", ""),
        ("- Para precio_venta y precio_costo no usar símbolos de moneda.", "")
    ]
    for row, (a, b) in enumerate(instrucciones, 1):
        ws2.cell(row=row, column=1, value=a)
        ws2.cell(row=row, column=2, value=b)
        if "INSTRUCCIONES" in a or "⚠️" in a:
            ws2.cell(row=row, column=1).font = Font(bold=True)
    ws2.column_dimensions['A'].width = 25
    ws2.column_dimensions['B'].width = 60

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        download_name='plantilla_productos.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ============================================================
# Importación de productos
# ============================================================

@compras_bp.route('/productos/importar', methods=['GET', 'POST'])
@requiere_editar('compras')
def importar_productos():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        file = request.files.get('archivo')
        if not file:
            flash('Selecciona un archivo.', 'warning')
            return redirect(url_for('compras.importar_productos'))

        try:
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file, engine='openpyxl')
        except Exception as e:
            flash(f'Error al leer el archivo: {str(e)}', 'danger')
            return redirect(url_for('compras.importar_productos'))

        columnas_esperadas = ['nombre', 'descripcion', 'categoria', 'precio_venta', 'precio_costo', 'stock_actual', 'stock_minimo', 'activo']
        columnas_faltantes = [col for col in columnas_esperadas if col not in df.columns]
        if columnas_faltantes:
            flash(f'Columnas faltantes: {", ".join(columnas_faltantes)}', 'danger')
            return redirect(url_for('compras.importar_productos'))

        registros_validos = []
        registros_invalidos = []
        productos_existentes = {
            p.nombre.lower(): p
            for p in Producto.query.filter_by(id_empresa=empresa.id).all()
        }

        for idx, row in df.iterrows():
            errores = []
            datos = {}

            nombre = str(row.get('nombre', '')).strip()
            if not nombre:
                errores.append('nombre es obligatorio')
            else:
                datos['nombre'] = nombre

            descripcion = str(row.get('descripcion', '')).strip() if not pd.isna(row.get('descripcion')) else ''
            datos['descripcion'] = descripcion if descripcion else None

            categoria_nombre = str(row.get('categoria', '')).strip()
            if categoria_nombre:
                datos['categoria'] = categoria_nombre
            else:
                datos['categoria'] = None

            precio_venta = row.get('precio_venta')
            try:
                pv = float(precio_venta)
                if pv <= 0:
                    errores.append('precio_venta debe ser mayor a 0')
                else:
                    datos['precio_venta'] = pv
            except Exception:
                errores.append('precio_venta debe ser un número válido')

            precio_costo = row.get('precio_costo')
            try:
                pc = float(precio_costo) if not pd.isna(precio_costo) else None
                if pc is not None and pc < 0:
                    errores.append('precio_costo no puede ser negativo')
                else:
                    datos['precio_costo'] = pc
            except Exception:
                errores.append('precio_costo debe ser un número válido')

            stock_actual = row.get('stock_actual', 0)
            try:
                sa = int(stock_actual) if not pd.isna(stock_actual) else 0
                if sa < 0:
                    errores.append('stock_actual no puede ser negativo')
                else:
                    datos['stock_actual'] = sa
            except Exception:
                errores.append('stock_actual debe ser un número entero')

            stock_minimo = row.get('stock_minimo', 0)
            try:
                sm = int(stock_minimo) if not pd.isna(stock_minimo) else 0
                if sm < 0:
                    errores.append('stock_minimo no puede ser negativo')
                else:
                    datos['stock_minimo'] = sm
            except Exception:
                errores.append('stock_minimo debe ser un número entero')

            activo_raw = str(row.get('activo', '')).strip().lower()
            if activo_raw in ['sí', 'si', 's', 'true', '1', 'yes', 'y']:
                datos['activo'] = True
            elif activo_raw in ['no', 'n', 'false', '0']:
                datos['activo'] = False
            else:
                datos['activo'] = True

            if not errores:
                nombre_lower = datos['nombre'].lower()
                if nombre_lower in productos_existentes:
                    datos['_accion'] = 'actualizar'
                    datos['_producto_existente'] = productos_existentes[nombre_lower]
                else:
                    datos['_accion'] = 'crear'
                registros_validos.append(datos)
            else:
                registros_invalidos.append({
                    'fila': idx + 2,
                    'errores': errores,
                    'datos': datos
                })

        session['importacion_productos_validos'] = registros_validos
        session['importacion_productos_invalidos'] = registros_invalidos

        return render_template('compras/previsualizacion_importacion.html',
                             validos=registros_validos,
                             invalidos=registros_invalidos)

    return render_template('compras/importar_productos.html')


@compras_bp.route('/productos/importar/confirmar', methods=['POST'])
@requiere_editar('compras')
def confirmar_importacion_productos():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    registros_validos = session.get('importacion_productos_validos', [])
    if not registros_validos:
        flash('No hay productos válidos para importar.', 'warning')
        return redirect(url_for('compras.importar_productos'))

    importados = 0
    actualizados = 0
    errores = []

    for registro in registros_validos:
        try:
            if registro['_accion'] == 'actualizar':
                producto = registro['_producto_existente']
                producto.descripcion = registro.get('descripcion')
                producto.categoria = registro.get('categoria')
                producto.precio_venta = registro['precio_venta']
                if registro.get('precio_costo') is not None:
                    producto.precio_costo = registro['precio_costo']
                producto.stock_actual = registro.get('stock_actual', 0)
                producto.stock_minimo = registro.get('stock_minimo', 0)
                producto.activo = registro.get('activo', True)
                actualizados += 1
            else:
                producto = Producto(
                    nombre=registro['nombre'],
                    descripcion=registro.get('descripcion'),
                    categoria=registro.get('categoria'),
                    precio_venta=registro['precio_venta'],
                    precio_costo=registro.get('precio_costo'),
                    stock_actual=registro.get('stock_actual', 0),
                    stock_minimo=registro.get('stock_minimo', 0),
                    activo=registro.get('activo', True),
                    id_empresa=empresa.id
                )
                db.session.add(producto)
                importados += 1
        except Exception as e:
            errores.append(
                f"Error con producto {registro.get('nombre', 'desconocido')}: {str(e)}"
            )

    try:
        db.session.flush()
        auditar(
            'importar', 'Producto', None,
            nota=(
                f'Importación masiva: {importados} productos nuevos, '
                f'{actualizados} actualizados, {len(errores)} errores'
            ),
        )
        db.session.commit()
        flash(
            f'✅ Se importaron {importados} productos nuevos y se '
            f'actualizaron {actualizados} existentes.',
            'success'
        )
        if errores:
            for e in errores:
                flash(f'⚠️ {e}', 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al guardar: {str(e)}', 'danger')

    session.pop('importacion_productos_validos', None)
    session.pop('importacion_productos_invalidos', None)
    return redirect(url_for('ventas.listar_productos'))


# ============================================================
# Auxiliares
# ============================================================

def _resolver_proveedor(empresa, form):
    """
    Resuelve el proveedor a partir del POST.
    - Si viene id_proveedor válido -> lo usa.
    - Si viene nuevo_proveedor_nombre y no está vacío -> crea uno.
    - Si no -> devuelve None.
    """
    id_prov = request.form.get('id_proveedor', type=int)
    if id_prov:
        prov = Proveedor.query.get(id_prov)
        if prov and prov.id_empresa == empresa.id:
            return prov.id

    nuevo = (request.form.get('nuevo_proveedor_nombre') or '').strip()
    if nuevo:
        existente = Proveedor.query.filter_by(
            id_empresa=empresa.id, nombre=nuevo
        ).first()
        if existente:
            return existente.id
        prov = Proveedor(nombre=nuevo, id_empresa=empresa.id, activo=True)
        db.session.add(prov)
        db.session.flush()
        return prov.id

    return None


# ============================================================
# Categorías de productos
# ============================================================

@compras_bp.route('/categorias')
@requiere_ver('compras')
def listar_categorias():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))
    categorias = CategoriaProducto.query.filter_by(
        id_empresa=empresa.id
    ).order_by(CategoriaProducto.nombre).all()
    return render_template('compras/categorias/listar.html', categorias=categorias)


@compras_bp.route('/categorias/nueva', methods=['GET', 'POST'])
@requiere_editar('compras')
def nueva_categoria():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio.', 'danger')
            return render_template('compras/categorias/form.html')
        if CategoriaProducto.query.filter_by(
            nombre=nombre, id_empresa=empresa.id
        ).first():
            flash('Ya existe una categoría con ese nombre.', 'danger')
            return render_template('compras/categorias/form.html')
        cat = CategoriaProducto(nombre=nombre, id_empresa=empresa.id)
        db.session.add(cat)
        db.session.flush()
        auditar('crear', 'CategoriaProducto', cat.id, despues=cat)
        db.session.commit()
        flash('Categoría creada.', 'success')
        return redirect(url_for('compras.listar_categorias'))
    return render_template('compras/categorias/form.html')


@compras_bp.route('/categorias/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('compras')
def editar_categoria(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cat = CategoriaProducto.query.get_or_404(id)
    if cat.id_empresa != empresa.id:
        flash('Categoría no encontrada.', 'danger')
        return redirect(url_for('compras.listar_categorias'))

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio.', 'danger')
            return render_template('compras/categorias/form.html', categoria=cat)
        if CategoriaProducto.query.filter_by(
            nombre=nombre, id_empresa=empresa.id
        ).first() and cat.nombre != nombre:
            flash('Ya existe una categoría con ese nombre.', 'danger')
            return render_template('compras/categorias/form.html', categoria=cat)
        estado_antes = {'nombre': cat.nombre, 'activo': cat.activo}
        cat.nombre = nombre
        cat.activo = 'activo' in request.form
        db.session.flush()
        auditar(
            'editar', 'CategoriaProducto', cat.id,
            antes=estado_antes, despues=cat,
        )
        db.session.commit()
        flash('Categoría actualizada.', 'success')
        return redirect(url_for('compras.listar_categorias'))
    return render_template('compras/categorias/form.html', categoria=cat)

# ============================================================
# Anulación de compras
# ============================================================

@compras_bp.route('/<int:id>/anular', methods=['POST'])
@requiere_editar('compras')
def anular(id):
    """Anula una compra y revierte todo (stock, caja, contabilidad)."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    compra = Compra.query.get_or_404(id)
    if compra.id_empresa != empresa.id:
        flash('Compra no encontrada.', 'danger')
        return redirect(url_for('compras.listar'))

    motivo = (request.form.get('motivo') or '').strip() or None

    try:
        from app.services.compra_service import CompraService
        from app.utils_db import transaccion

        with transaccion():
            compra_anulada = CompraService.anular_compra(
                compra.id, motivo=motivo, commit=False
            )
            auditar(
                'anular', 'Compra', compra_anulada.id,
                despues=compra_anulada,
                nota=f'Compra anulada. Motivo: {motivo or "no especificado"}.',
            )
            # Loguear movimientos y cheques afectados
            movs = Movimiento.query.filter(
                Movimiento.id_empresa == empresa.id,
                Movimiento.descripcion.like(f'Anulación Compra #{compra.id}%'),
            ).all()
            for m in movs:
                auditar(
                    'crear', 'Movimiento', m.id, despues=m,
                    nota=f'Espejo de anulación por compra #{compra.id}',
                )
            for pago in compra.pagos:
                if pago.id_cheque:
                    ch = Cheque.query.get(pago.id_cheque)
                    if ch:
                        auditar(
                            'anular', 'Cheque', ch.id, despues=ch,
                            nota=f'Revertido por anulación compra #{compra.id}',
                        )

        flash(
            f'Compra #{compra.id} anulada. Se revirtieron stock, caja '
            f'y asientos contables.',
            'success',
        )
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error al anular compra')
        flash(f'Error al anular la compra: {str(e)}', 'danger')

    return redirect(url_for('compras.detalle', id=compra.id))

# ============================================================
# Notas de crédito
# ============================================================

@compras_bp.route('/<int:id>/nota-credito/nueva', methods=['GET', 'POST'])
@requiere_editar('compras')
def nueva_nota_credito(id):
    """Formulario para crear una nota de crédito sobre una compra."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    compra = Compra.query.get_or_404(id)
    if compra.id_empresa != empresa.id:
        flash('Compra no encontrada.', 'danger')
        return redirect(url_for('compras.listar'))

    if compra.estado == 'anulada':
        flash('No se puede crear una nota sobre una compra anulada.',
              'danger')
        return redirect(url_for('compras.detalle', id=compra.id))

    from app.services.nota_credito_service import NotaCreditoService

    # Armar info de productos: máximo devolvible + precio original
    productos_info = []
    for det in compra.detalles:
        maximo = NotaCreditoService.calcular_devolucion_maxima(det)
        productos_info.append({
            'detalle': det,
            'producto': det.producto,
            'cantidad_original': det.cantidad,
            'precio_original': det.precio_compra,
            'maximo_devolvible': maximo,
        })

    if request.method == 'POST':
        motivo = (request.form.get('motivo') or '').strip()

        # Los items vienen como JSON (armado en el JS del form)
        try:
            items = json.loads(request.form.get('items_json', '[]'))
        except Exception:
            items = []

        try:
            from app.utils_db import transaccion
            with transaccion():
                nota = NotaCreditoService.crear_nota(
                    id_compra=compra.id,
                    items=items,
                    motivo=motivo,
                    commit=False,
                )
                auditar(
                    'crear', 'NotaCreditoCompra', nota.id, despues=nota,
                    nota=f'Nota de crédito sobre compra #{compra.id}. '
                         f'Motivo: {motivo}',
                )
                for d in nota.detalles:
                    auditar(
                        'crear', 'DetalleNotaCredito', d.id, despues=d,
                    )

            from app.utils import formato_pesos
            flash(
                f'Nota de crédito #{nota.id} creada por '
                f'{formato_pesos(nota.monto)}.',
                'success',
            )
            return redirect(url_for('compras.detalle', id=compra.id))

        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'danger')
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Error al crear nota de crédito')
            flash(f'Error: {str(e)}', 'danger')

    return render_template(
        'compras/nota_credito_form.html',
        compra=compra,
        productos_info=productos_info,
    )


@compras_bp.route('/nota-credito/<int:nota_id>/anular', methods=['POST'])
@requiere_editar('compras')
def anular_nota_credito(nota_id):
    """Anula una nota de crédito."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    from app.models import NotaCreditoCompra
    nota = NotaCreditoCompra.query.get_or_404(nota_id)
    if nota.id_empresa != empresa.id:
        flash('Nota de crédito no encontrada.', 'danger')
        return redirect(url_for('compras.listar'))

    motivo = (request.form.get('motivo') or '').strip() or None
    id_compra = nota.id_compra

    try:
        from app.services.nota_credito_service import NotaCreditoService
        from app.utils_db import transaccion

        with transaccion():
            nota_anulada = NotaCreditoService.anular_nota(
                nota.id, motivo=motivo, commit=False
            )
            auditar(
                'anular', 'NotaCreditoCompra', nota_anulada.id,
                despues=nota_anulada,
                nota=f'Nota anulada. Motivo: {motivo or "no especificado"}',
            )

        flash(
            f'Nota de crédito #{nota.id} anulada.',
            'success',
        )
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error al anular nota de crédito')
        flash(f'Error: {str(e)}', 'danger')

    return redirect(url_for('compras.detalle', id=id_compra))