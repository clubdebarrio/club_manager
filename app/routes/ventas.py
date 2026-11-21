# app/routes/ventas.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    jsonify, send_file, current_app,
)
from app import db
from app.models import (
    Producto, Venta, DetalleVenta, Socio, CuentaBancaria, CategoriaProducto, Cheque,
)
from app.utils import quitar_acentos, fecha_hora_efectiva, current_empresa, local_to_utc, utc_to_local
from sqlalchemy import func, or_
from app.forms import ProductoForm, VentaForm
from app.services.caja_service import CajaService
from app.services.contabilidad_service import ContabilidadService
from datetime import datetime, timedelta
from decimal import Decimal
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar
import json

ventas_bp = Blueprint('ventas', __name__, url_prefix='/ventas')


# ============================================================
# Productos
# ============================================================

@ventas_bp.route('/productos')
@requiere_ver('ventas')
def listar_productos():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 20)
    query = request.args.get('q', '').strip()

    # Query base
    base_query = Producto.query.filter_by(id_empresa=empresa.id)

    # Aplicar filtro de búsqueda si hay texto
    if query:
        query_normalizada = quitar_acentos(query).lower()
        # Buscamos por nombre del producto (ignorando mayúsculas/acentos)
        base_query = base_query.filter(
            func.unaccent(Producto.nombre).ilike(f'%{query_normalizada}%')
        )

    pagination = base_query.order_by(
        Producto.nombre
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )
    productos = pagination.items

    return render_template('ventas/productos/listar.html',
                           productos=productos, 
                           pagination=pagination,
                           query=query)


@ventas_bp.route('/productos/nuevo', methods=['GET', 'POST'])
@requiere_editar('ventas')
def nuevo_producto():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = ProductoForm(empresa_id=empresa.id)
    if form.validate_on_submit():
        categoria_nombre = None
        if form.categoria.data and form.categoria.data != 0:
            cat = CategoriaProducto.query.get(form.categoria.data)
            # Validar que la categoría sea de la empresa actual
            if cat and cat.id_empresa == empresa.id:
                categoria_nombre = cat.nombre

        producto = Producto(
            nombre=form.nombre.data,
            descripcion=form.descripcion.data,
            categoria=categoria_nombre,
            precio_venta=form.precio_venta.data,
            precio_costo=form.precio_costo.data,
            stock_actual=form.stock_actual.data,
            stock_minimo=form.stock_minimo.data,
            activo=form.activo.data,
            id_empresa=empresa.id,
        )
        db.session.add(producto)
        db.session.flush()
        archivo = request.files.get('foto')
        if archivo and archivo.filename:
            from app.services.foto_producto_service import FotoProductoService
            try:
                FotoProductoService.guardar_foto(producto, archivo)
            except Exception as e:
                flash(f'Producto creado, pero hubo un error con la foto: {str(e)}', 'warning')        
        auditar('crear', 'Producto', producto.id, despues=producto)
        db.session.commit()
        flash('Producto creado exitosamente.', 'success')
        return redirect(url_for('ventas.listar_productos'))

    return render_template('ventas/productos/form.html', form=form)


@ventas_bp.route('/productos/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('ventas')
def editar_producto(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    producto = Producto.query.get_or_404(id)
    if producto.id_empresa != empresa.id:
        flash('Producto no encontrado.', 'danger')
        return redirect(url_for('ventas.listar_productos'))

    form = ProductoForm(obj=producto, empresa_id=empresa.id)

    if producto.categoria:
        cat = CategoriaProducto.query.filter_by(
            nombre=producto.categoria, id_empresa=empresa.id
        ).first()
        if cat:
            form.categoria.data = cat.id

    if form.validate_on_submit():
        # Snapshot del estado ANTES
        estado_antes = {
            'nombre': producto.nombre,
            'descripcion': producto.descripcion,
            'categoria': producto.categoria,
            'precio_venta': str(producto.precio_venta),
            'precio_costo': str(producto.precio_costo) if producto.precio_costo else None,
            'stock_actual': producto.stock_actual,
            'stock_minimo': producto.stock_minimo,
            'activo': producto.activo,
        }

        categoria_nombre = None
        if form.categoria.data and form.categoria.data != 0:
            cat = CategoriaProducto.query.get(form.categoria.data)
            if cat and cat.id_empresa == empresa.id:
                categoria_nombre = cat.nombre

        producto.nombre = form.nombre.data
        producto.descripcion = form.descripcion.data
        producto.categoria = categoria_nombre
        producto.precio_venta = form.precio_venta.data
        producto.precio_costo = form.precio_costo.data
        producto.stock_actual = form.stock_actual.data
        producto.stock_minimo = form.stock_minimo.data
        producto.activo = form.activo.data

        db.session.flush()
        archivo = request.files.get('foto')
        if archivo and archivo.filename:
            from app.services.foto_producto_service import FotoProductoService
            try:
                FotoProductoService.guardar_foto(producto, archivo)
            except Exception as e:
                flash(f'Datos guardados, pero hubo un error con la foto: {str(e)}', 'warning')        
        auditar(
            'editar', 'Producto', producto.id,
            antes=estado_antes, despues=producto,
        )
        db.session.commit()
        flash('Producto actualizado.', 'success')
        return redirect(url_for('ventas.listar_productos'))

    return render_template('ventas/productos/form.html', form=form, producto=producto)


@ventas_bp.route('/productos/<int:id>/movimientos')
@requiere_ver('ventas')
def movimientos_producto(id):
    """
    Kardex del producto: historial de entradas (compras) y salidas (ventas)
    con saldo acumulado.
    """
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    producto = Producto.query.get_or_404(id)
    if producto.id_empresa != empresa.id:
        flash('Producto no encontrado.', 'danger')
        return redirect(url_for('ventas.listar_productos'))

    from app.models import DetalleCompra, Compra

    movimientos = []

    # --- Entradas por compras ---
    detalles_compra = (
        DetalleCompra.query
        .join(Compra, Compra.id == DetalleCompra.id_compra)
        .filter(
            DetalleCompra.id_producto == producto.id,
            Compra.id_empresa == empresa.id,
            Compra.estado == 'completada',
        )
        .all()
    )
    for d in detalles_compra:
        movimientos.append({
            'fecha': d.compra.fecha,
            'tipo': 'compra',
            'cantidad': d.cantidad,
            'precio_unitario': d.precio_compra,
            'importe': d.subtotal,
            'referencia': f'Compra #{d.id_compra}',
            'detalle': d.compra.proveedor_nombre or '',
        })

    # --- Salidas por ventas ---
    detalles_venta = (
        DetalleVenta.query
        .join(Venta, Venta.id == DetalleVenta.id_venta)
        .filter(
            DetalleVenta.id_producto == producto.id,
            Venta.id_empresa == empresa.id,
            Venta.estado == 'completada',
        )
        .all()
    )
    for d in detalles_venta:
        if d.venta.socio:
            cliente = f"{d.venta.socio.apellido}, {d.venta.socio.nombre}"
        else:
            cliente = 'No Socio'
        movimientos.append({
            'fecha': d.venta.fecha,
            'tipo': 'venta',
            'cantidad': -d.cantidad,
            'precio_unitario': d.precio_unitario,
            'importe': d.subtotal,
            'referencia': f'Venta #{d.id_venta}',
            'detalle': cliente,
        })

    # --- Salidas por notas de crédito (devoluciones a proveedor) ---
    from app.models import DetalleNotaCredito, NotaCreditoCompra
    detalles_nc = (
        DetalleNotaCredito.query
        .join(NotaCreditoCompra,
              NotaCreditoCompra.id == DetalleNotaCredito.id_nota)
        .filter(
            DetalleNotaCredito.id_producto == producto.id,
            NotaCreditoCompra.id_empresa == empresa.id,
            NotaCreditoCompra.anulada == False,
        )
        .all()
    )
    for d in detalles_nc:
        movimientos.append({
            'fecha': d.nota.fecha,
            'tipo': 'nota_credito',
            'cantidad': -d.cantidad,
            'precio_unitario': d.precio_unitario,
            'importe': d.subtotal,
            'referencia': f'NC #{d.nota.id} (Compra #{d.nota.id_compra})',
            'detalle': (d.nota.motivo or '')[:60],
        })
        
    movimientos.sort(key=lambda m: m['fecha'])

    total_movido = sum(m['cantidad'] for m in movimientos)
    saldo_inicial = producto.stock_actual - total_movido

    saldo = saldo_inicial
    for m in movimientos:
        saldo += m['cantidad']
        m['saldo'] = saldo

    return render_template(
        'ventas/productos/movimientos.html',
        producto=producto,
        movimientos=movimientos,
        saldo_inicial=saldo_inicial,
        empresa=empresa,
    )

@ventas_bp.route('/productos/<int:id>/foto')
@requiere_ver('ventas')
def servir_foto_producto(id):
    """Sirve la foto del producto desde Supabase Storage."""
    import io
    from flask import send_file
    from app.services.foto_producto_service import FotoProductoService

    empresa = current_empresa()
    if not empresa:
        return '', 404

    producto = Producto.query.get_or_404(id)
    if producto.id_empresa != empresa.id or not producto.tiene_foto:
        return '', 404

    try:
        data = FotoProductoService.obtener_foto(producto)
    except Exception:
        current_app.logger.exception(f'Error obteniendo foto de producto {producto.id}')
        return '', 500

    if not data:
        return '', 404

    response = send_file(io.BytesIO(data), mimetype='image/jpeg', as_attachment=False)
    response.headers['Cache-Control'] = 'private, max-age=0, must-revalidate'
    if producto.foto_actualizada_at:
        response.headers['ETag'] = f'W/"{producto.foto_actualizada_at.timestamp():.0f}"'
    return response


@ventas_bp.route('/productos/<int:id>/foto', methods=['POST'])
@requiere_editar('ventas')
def subir_foto_producto(id):
    """Sube o reemplaza la foto del producto."""
    from app.services.foto_producto_service import FotoProductoService
    from app.utils_db import transaccion

    empresa = current_empresa()
    if not empresa:
        return redirect(url_for('main.index'))

    producto = Producto.query.get_or_404(id)
    if producto.id_empresa != empresa.id:
        flash('Producto no encontrado.', 'danger')
        return redirect(url_for('ventas.listar_productos'))

    archivo = request.files.get('foto')
    if not archivo or not archivo.filename:
        flash('No se seleccionó ninguna foto.', 'warning')
        return redirect(url_for('ventas.editar_producto', id=producto.id))

    try:
        with transaccion():
            bytes_subidos = FotoProductoService.guardar_foto(producto, archivo)
            auditar('subir_foto', 'Producto', producto.id, nota=f'Foto actualizada ({bytes_subidos} bytes)')
        flash('Foto actualizada correctamente.', 'success')
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error subiendo foto de producto')
        flash(f'Error al subir la foto: {str(e)}', 'danger')

    return redirect(url_for('ventas.editar_producto', id=producto.id))


@ventas_bp.route('/productos/<int:id>/foto/eliminar', methods=['POST'])
@requiere_editar('ventas')
def borrar_foto_producto(id):
    """Borra la foto del producto."""
    from app.services.foto_producto_service import FotoProductoService
    from app.utils_db import transaccion

    empresa = current_empresa()
    if not empresa:
        return redirect(url_for('main.index'))

    producto = Producto.query.get_or_404(id)
    if producto.id_empresa != empresa.id:
        return redirect(url_for('ventas.listar_productos'))

    try:
        with transaccion():
            FotoProductoService.borrar_foto(producto)
            auditar('borrar_foto', 'Producto', producto.id, nota='Foto eliminada')
        flash('Foto eliminada.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error borrando foto de producto')
        flash(f'Error al borrar la foto: {str(e)}', 'danger')

    return redirect(url_for('ventas.editar_producto', id=producto.id))


# ============================================================
# Ventas
# ============================================================

@ventas_bp.route('/')
@requiere_ver('ventas')
def listar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 20)
    fecha_desde = request.args.get('fecha_desde')
    fecha_hasta = request.args.get('fecha_hasta')
    query = request.args.get('q', '').strip()

    ver_anuladas = request.args.get('ver_anuladas', '0') == '1'

    base_query = Venta.query.filter_by(id_empresa=empresa.id)
    if ver_anuladas:
        base_query = base_query.filter(Venta.estado == 'anulada')
    else:
        base_query = base_query.filter(Venta.estado == 'completada')

    if fecha_desde:
        try:
            # Convertimos la fecha de Argentina a UTC para buscar en la DB
            fecha_desde_dt = local_to_utc(datetime.strptime(fecha_desde, '%Y-%m-%d'))
            base_query = base_query.filter(Venta.fecha >= fecha_desde_dt)
        except ValueError:
            pass

    if fecha_hasta:
        try:
            # Para el "hasta", sumamos 1 día para incluir todo ese día, y luego pasamos a UTC
            fecha_hasta_dt = local_to_utc(datetime.strptime(fecha_hasta, '%Y-%m-%d') + timedelta(days=1))
            base_query = base_query.filter(Venta.fecha < fecha_hasta_dt)
        except ValueError:
            pass

    if query:
        query_normalizada = quitar_acentos(query).lower()
        condiciones = [
            func.unaccent(Socio.apellido).ilike(f'%{query_normalizada}%'),
            func.unaccent(Socio.nombre).ilike(f'%{query_normalizada}%')
        ]
        if query.isdigit():
            condiciones.append(Venta.id == int(query))

        base_query = base_query.outerjoin(
            Socio, Venta.id_socio == Socio.id
        ).filter(or_(*condiciones))

    pagination = base_query.order_by(
        Venta.fecha.desc(),
        Venta.id.desc()
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )
    ventas = pagination.items

    return render_template('ventas/listar.html',
                         ventas=ventas,
                         pagination=pagination,
                         fecha_desde=fecha_desde,
                         fecha_hasta=fecha_hasta,
                         ver_anuladas=ver_anuladas,
                         query=query)


@ventas_bp.route('/nueva', methods=['GET', 'POST'])
@requiere_editar('ventas')
def nueva():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = VentaForm()
    form.id_socio.choices = [(0, 'No Socio')] + [
        (s.id, f"{s.apellido}, {s.nombre}")
        for s in Socio.query.filter_by(
            fecha_baja=None, id_empresa=empresa.id
        ).all()
    ]
    metodo_pago = request.form.get('metodo_pago') if request.method == 'POST' else None
    if metodo_pago:
        form.id_cuenta.choices = obtener_cuentas_por_metodo(metodo_pago, empresa.id)
    else:
        form.id_cuenta.choices = []

    productos = Producto.query.filter_by(
        activo=True, id_empresa=empresa.id
    ).order_by(Producto.nombre).all()

    if form.validate_on_submit():
        productos_json = request.form.get('productos_json', '[]')
        try:
            carrito = json.loads(productos_json)
        except Exception:
            carrito = []

        if not carrito:
            flash('Agregue al menos un producto al carrito.', 'warning')
            return render_template('ventas/nueva.html', form=form, productos=productos)

        total = Decimal('0')
        detalles = []

        for item in carrito:
            producto = Producto.query.with_for_update().get(item['id'])
            if not producto or producto.id_empresa != empresa.id:
                flash(f'Producto no encontrado: {item.get("nombre")}', 'danger')
                return render_template('ventas/nueva.html', form=form, productos=productos)
            cantidad = int(item['cantidad'])
            if cantidad <= 0:
                continue
            if cantidad > producto.stock_actual:
                flash(
                    f'Stock insuficiente para "{producto.nombre}". '
                    f'Disponible: {producto.stock_actual}',
                    'danger'
                )
                return render_template('ventas/nueva.html', form=form, productos=productos)
            subtotal = producto.precio_venta * cantidad
            total += subtotal
            detalles.append({
                'producto': producto,
                'cantidad': cantidad,
                'precio_unitario': producto.precio_venta,
                'subtotal': subtotal,
            })

        if total <= 0:
            flash('El total de la venta debe ser mayor a 0.', 'warning')
            return render_template('ventas/nueva.html', form=form, productos=productos)

        # Validar cuenta de pago
        cuenta_pago = CuentaBancaria.query.get(form.id_cuenta.data)
        if not cuenta_pago or cuenta_pago.id_empresa != empresa.id:
            flash('Cuenta de pago inválida.', 'danger')
            return render_template('ventas/nueva.html', form=form, productos=productos)

        try:
            fecha_efectiva = local_to_utc(fecha_hora_efectiva(form.fecha.data))

            venta = Venta(
                fecha=fecha_efectiva,
                id_socio=form.id_socio.data if form.id_socio.data != 0 else None,
                total=total,
                metodo_pago=form.metodo_pago.data,
                id_cuenta=form.id_cuenta.data,
                id_empresa=empresa.id,
                estado='completada',
            )
            db.session.add(venta)
            db.session.flush()

            costo_total = Decimal('0')
            for det in detalles:
                costo_unit = det['producto'].precio_costo or Decimal('0')
                detalle = DetalleVenta(
                    id_venta=venta.id,
                    id_producto=det['producto'].id,
                    cantidad=det['cantidad'],
                    precio_unitario=det['precio_unitario'],
                    costo_unitario=costo_unit,
                    subtotal=det['subtotal'],
                )
                db.session.add(detalle)
                det['producto'].stock_actual -= det['cantidad']
                costo_total += costo_unit * det['cantidad']

            from app.services.cuenta_contable_resolver import CuentaContableResolver
            cuenta_contable = CuentaContableResolver.resolver_o_fallar(
                empresa.id, 'venta_indumentaria'
            )

            movimiento_data = {
                'id_cuenta': form.id_cuenta.data,
                'id_cuenta_contable': cuenta_contable.id,
                'fecha': fecha_efectiva,
                'tipo': 'ingreso',
                'monto': total,
                'descripcion': (
                    f'Venta indumentaria #{venta.id} - {form.metodo_pago.data}'
                ),
                'metodo_pago': form.metodo_pago.data,
                'comprobante': f'Venta #{venta.id}',
                'id_empresa': empresa.id,
                'conciliado': False,
            }
            id_cheque = request.form.get('id_cheque')
            if id_cheque and id_cheque.isdigit() and int(id_cheque) > 0:
                cheque = Cheque.query.get(int(id_cheque))
                if cheque and cheque.id_empresa == empresa.id:
                    movimiento_data['id_cheque'] = int(id_cheque)
                    movimiento_data['comprobante'] = (
                        f'Venta #{venta.id} - Cheque #{cheque.numero}'
                    )

            movimiento = CajaService.registrar_movimiento(movimiento_data, commit=False)

            if movimiento:
                ContabilidadService.generar_asiento(movimiento)
                ContabilidadService.generar_asiento_cmv(venta, costo_total)

            # --- Auditoría ---
            auditar('crear', 'Venta', venta.id, despues=venta)

            # Detalles de la venta (una entrada por producto)
            for detalle in venta.detalles:
                auditar('crear', 'DetalleVenta', detalle.id, despues=detalle)

            # Movimiento de caja generado
            if movimiento:
                auditar('crear', 'Movimiento', movimiento.id, despues=movimiento)

            # El cheque asociado (si el pago fue con cheque)
            if 'id_cheque' in movimiento_data and movimiento_data['id_cheque']:
                cheque = Cheque.query.get(movimiento_data['id_cheque'])
                if cheque:
                    auditar(
                        'entregar', 'Cheque', cheque.id,
                        nota=f'Entregado por Venta #{venta.id}',
                    )

            db.session.commit()
            flash(
                f'✅ Venta #{venta.id} registrada exitosamente. '
                f'Total: ${total:.2f}',
                'success'
            )
            return redirect(url_for('ventas.listar'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error al registrar venta: {str(e)}")
            flash(f'Error al registrar la venta: {str(e)}', 'danger')
            return render_template('ventas/nueva.html', form=form, productos=productos)

    if form.errors:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')

    return render_template('ventas/nueva.html', form=form, productos=productos)


# ============================================================
# Auxiliares
# ============================================================

def obtener_cuentas_por_metodo(metodo, empresa_id):
    tipos_permitidos = {
        'efectivo': ['efectivo'],
        'transferencia': ['banco'],
        'cheque': ['banco', 'efectivo'],
        'debito': ['banco'],
        'credito': ['banco'],
    }
    tipos = tipos_permitidos.get(metodo, [])
    if not tipos:
        return []

    cuentas = CuentaBancaria.query.filter(
        CuentaBancaria.id_empresa == empresa_id,
        CuentaBancaria.tipo.in_(tipos),
        CuentaBancaria.activo == True,
    ).all()

    return [(c.id, c.nombre) for c in cuentas]


# ============================================================
# Detalle / ticket / API / exportación
# ============================================================

@ventas_bp.route('/<int:id>')
@requiere_ver('ventas')
def detalle(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    venta = Venta.query.get_or_404(id)
    if venta.id_empresa != empresa.id:
        flash('Venta no encontrada.', 'danger')
        return redirect(url_for('ventas.listar'))

    return render_template('ventas/detalle.html', venta=venta)


@ventas_bp.route('/productos/api/buscar')
@requiere_ver('ventas')
def api_buscar_productos():
    query = request.args.get('q', '')
    empresa = current_empresa()
    if not empresa:
        return jsonify([])

    productos = Producto.query.filter(
        Producto.id_empresa == empresa.id,
        Producto.activo == True,
        Producto.nombre.ilike(f'%{query}%'),
    ).order_by(Producto.nombre).limit(20).all()

    return jsonify([{
        'id': p.id,
        'nombre': p.nombre,
        'precio_venta': float(p.precio_venta),
        'stock_actual': p.stock_actual,
    } for p in productos])


@ventas_bp.route('/ticket/<int:id>')
@requiere_ver('ventas')
def ticket(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    venta = Venta.query.get_or_404(id)
    if venta.id_empresa != empresa.id:
        flash('Venta no encontrada.', 'danger')
        return redirect(url_for('ventas.listar'))

    return render_template('ventas/ticket.html', venta=venta, empresa=empresa)


@ventas_bp.route('/api/cuentas_por_metodo')
@requiere_ver('ventas')
def api_cuentas_por_metodo():
    """Retorna las cuentas disponibles según el método de pago seleccionado."""
    metodo = request.args.get('metodo')
    empresa = current_empresa()
    if not empresa:
        return jsonify([])

    cuentas = obtener_cuentas_por_metodo(metodo, empresa.id)
    return jsonify([{'id': c[0], 'nombre': c[1]} for c in cuentas])


@ventas_bp.route('/exportar-excel')
@requiere_ver('ventas')
def exportar_excel():
    """Exporta el listado de ventas a Excel con filtro de fechas."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    fecha_desde = request.args.get('fecha_desde')
    fecha_hasta = request.args.get('fecha_hasta')

    query = Venta.query.filter_by(id_empresa=empresa.id)
    if fecha_desde:
        try:
            fecha_desde_dt = local_to_utc(datetime.strptime(fecha_desde, '%Y-%m-%d'))
            query = query.filter(Venta.fecha >= fecha_desde_dt)
        except ValueError:
            pass
    if fecha_hasta:
        try:
            fecha_hasta_dt = local_to_utc(datetime.strptime(fecha_hasta, '%Y-%m-%d') + timedelta(days=1))
            query = query.filter(Venta.fecha < fecha_hasta_dt)
        except ValueError:
            pass

    ventas = query.order_by(Venta.fecha.desc()).all()

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
    from io import BytesIO

    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Ventas"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F75B5", end_color="2F75B5", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    bold_font = Font(bold=True)
    currency_format = numbers.FORMAT_CURRENCY_USD_SIMPLE

    headers_resumen = ['ID', 'Fecha', 'Socio', 'Total', 'Método de pago', 'Estado']
    for col, header in enumerate(headers_resumen, 1):
        cell = ws1.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    total_general = 0
    for row_idx, venta in enumerate(ventas, start=2):
        ws1.cell(row=row_idx, column=1, value=venta.id)
        ws1.cell(row=row_idx, column=2, value=utc_to_local(venta.fecha).strftime('%d/%m/%Y %H:%M'))
        ws1.cell(
            row=row_idx, column=3,
            value=venta.socio.apellido + ', ' + venta.socio.nombre if venta.socio else 'No Socio'
        )
        ws1.cell(row=row_idx, column=4, value=float(venta.total))
        ws1.cell(row=row_idx, column=4).number_format = currency_format
        ws1.cell(row=row_idx, column=5, value=venta.metodo_pago.capitalize())
        ws1.cell(row=row_idx, column=6, value=venta.estado.capitalize())
        total_general += venta.total

    row_idx = len(ventas) + 2
    ws1.cell(row=row_idx, column=3, value="TOTAL GENERAL")
    ws1.cell(row=row_idx, column=3).font = bold_font
    ws1.cell(row=row_idx, column=4, value=float(total_general))
    ws1.cell(row=row_idx, column=4).number_format = currency_format
    for col in range(3, 5):
        ws1.cell(row=row_idx, column=col).font = bold_font
        ws1.cell(row=row_idx, column=col).fill = PatternFill(
            start_color="E9ECEF", end_color="E9ECEF", fill_type="solid"
        )
        ws1.cell(row=row_idx, column=col).border = thin_border

    ws1.column_dimensions['A'].width = 10
    ws1.column_dimensions['B'].width = 20
    ws1.column_dimensions['C'].width = 30
    ws1.column_dimensions['D'].width = 15
    ws1.column_dimensions['E'].width = 20
    ws1.column_dimensions['F'].width = 15

    # Hoja 2: Detalle
    ws2 = wb.create_sheet("Detalle")
    headers_detalle = ['ID Venta', 'Fecha', 'Socio', 'Producto', 'Cantidad',
                       'Precio Venta', 'Costo Unitario', 'Subtotal']
    for col, header in enumerate(headers_detalle, 1):
        cell = ws2.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    row_idx = 2
    total_ventas_neto = 0
    total_costo = 0
    for venta in ventas:
        for det in venta.detalles:
            ws2.cell(row=row_idx, column=1, value=venta.id)
            ws2.cell(row=row_idx, column=2, value=utc_to_local(venta.fecha).strftime('%d/%m/%Y %H:%M'))
            ws2.cell(
                row=row_idx, column=3,
                value=venta.socio.apellido + ', ' + venta.socio.nombre if venta.socio else 'No Socio'
            )
            ws2.cell(row=row_idx, column=4, value=det.producto.nombre)
            ws2.cell(row=row_idx, column=5, value=det.cantidad)
            ws2.cell(row=row_idx, column=6, value=float(det.precio_unitario))
            ws2.cell(row=row_idx, column=6).number_format = currency_format
            costo = float(det.costo_unitario) if det.costo_unitario else 0
            ws2.cell(row=row_idx, column=7, value=costo)
            ws2.cell(row=row_idx, column=7).number_format = currency_format
            ws2.cell(row=row_idx, column=8, value=float(det.subtotal))
            ws2.cell(row=row_idx, column=8).number_format = currency_format
            total_ventas_neto += float(det.subtotal)
            total_costo += costo * det.cantidad
            row_idx += 1

    if row_idx > 2:
        ws2.cell(row=row_idx, column=4, value="TOTALES")
        ws2.cell(row=row_idx, column=4).font = bold_font
        ws2.cell(row=row_idx, column=6, value=float(total_ventas_neto))
        ws2.cell(row=row_idx, column=6).number_format = currency_format
        ws2.cell(row=row_idx, column=7, value=float(total_costo))
        ws2.cell(row=row_idx, column=7).number_format = currency_format
        ws2.cell(row=row_idx, column=8, value=float(total_ventas_neto - total_costo))
        ws2.cell(row=row_idx, column=8).number_format = currency_format
        for col in range(4, 9):
            ws2.cell(row=row_idx, column=col).font = bold_font
            ws2.cell(row=row_idx, column=col).fill = PatternFill(
                start_color="E9ECEF", end_color="E9ECEF", fill_type="solid"
            )
            ws2.cell(row=row_idx, column=col).border = thin_border

    ws2.column_dimensions['A'].width = 10
    ws2.column_dimensions['B'].width = 20
    ws2.column_dimensions['C'].width = 30
    ws2.column_dimensions['D'].width = 30
    ws2.column_dimensions['E'].width = 12
    ws2.column_dimensions['F'].width = 15
    ws2.column_dimensions['G'].width = 15
    ws2.column_dimensions['H'].width = 15

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    nombre_archivo = f"ventas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        download_name=nombre_archivo,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@ventas_bp.route('/exportar-pdf')
@requiere_ver('ventas')
def exportar_pdf():
    """Exporta el listado de ventas a PDF con filtro de fechas."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    fecha_desde = request.args.get('fecha_desde')
    fecha_hasta = request.args.get('fecha_hasta')

    query = Venta.query.filter_by(id_empresa=empresa.id)
    if fecha_desde:
        try:
            fecha_desde_dt = local_to_utc(datetime.strptime(fecha_desde, '%Y-%m-%d'))
            query = query.filter(Venta.fecha >= fecha_desde_dt)
        except ValueError:
            pass
    if fecha_hasta:
        try:
            fecha_hasta_dt = local_to_utc(datetime.strptime(fecha_hasta, '%Y-%m-%d') + timedelta(days=1))
            query = query.filter(Venta.fecha < fecha_hasta_dt)
        except ValueError:
            pass

    ventas = query.order_by(Venta.fecha.desc()).all()

    from app.services.reporte_service import ReporteService
    from datetime import datetime as dt

    columnas = ['ID', 'Fecha', 'Socio', 'Total', 'Método de pago', 'Estado']
    datos = [
        {
            'ID': v.id,
            'Fecha': utc_to_local(v.fecha).strftime('%d/%m/%Y %H:%M'),
            'Socio': v.socio.apellido + ', ' + v.socio.nombre if v.socio else 'No Socio',
            'Total': f"${v.total:.2f}",
            'Método de pago': v.metodo_pago.capitalize(),
            'Estado': v.estado.capitalize(),
        } for v in ventas
    ]

    titulo = "Reporte de Ventas"
    if fecha_desde and fecha_hasta:
        titulo += f" ({fecha_desde} al {fecha_hasta})"

    buffer = ReporteService.generar_pdf(titulo, columnas, datos, orientacion='landscape')
    nombre_archivo = f"ventas_{dt.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return send_file(buffer, download_name=nombre_archivo, as_attachment=True,
                     mimetype='application/pdf')

# ============================================================
# Anulación de ventas
# ============================================================

@ventas_bp.route('/<int:id>/anular', methods=['POST'])
@requiere_editar('ventas')
def anular(id):
    """Anula una venta y revierte todo (stock, caja, contabilidad)."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    venta = Venta.query.get_or_404(id)
    if venta.id_empresa != empresa.id:
        flash('Venta no encontrada.', 'danger')
        return redirect(url_for('ventas.listar'))

    motivo = (request.form.get('motivo') or '').strip() or None

    try:
        from app.services.venta_service import VentaService
        from app.utils_db import transaccion

        with transaccion():
            venta_anulada, mov_orig, mov_espejo = VentaService.anular_venta(
                venta.id, motivo=motivo, commit=False
            )
            auditar(
                'anular', 'Venta', venta_anulada.id,
                despues=venta_anulada,
                nota=(
                    f'Venta anulada. Motivo: '
                    f'{motivo or "no especificado"}.'
                ),
            )
            if mov_orig:
                auditar(
                    'anular', 'Movimiento', mov_orig.id,
                    despues=mov_orig,
                    nota=f'Reversión por anulación de venta #{venta.id}',
                )

        flash(
            f'Venta #{venta.id} anulada. Se revirtieron stock, caja y '
            f'asientos contables.',
            'success',
        )
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error al anular venta')
        flash(f'Error al anular la venta: {str(e)}', 'danger')

    return redirect(url_for('ventas.detalle', id=venta.id))