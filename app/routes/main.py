# app/routes/main.py
from flask import Blueprint, render_template, session
from app.models import (
    Empresa, Socio, CuotaSocial, Instalacion, Reserva, CuentaBancaria
)
from app.utils import current_empresa
from datetime import date, timedelta
from decimal import Decimal

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    # Si no está logueado, mostrar landing
    if 'usuario_id' not in session:
        return render_template('public/landing.html')

    empresa = current_empresa()
    if not empresa:
        return render_template('index.html',
                             total_socios=0,
                             activos=0,
                             cuotas_pendientes=0,
                             total_instalaciones=0,
                             reservas_activas=0,
                             saldo_caja=0,
                             proximas_reservas=[],
                             proximas_cuotas=[])

    # --- Estadísticas (todas filtradas por empresa) ---
    total_socios = Socio.query.filter_by(id_empresa=empresa.id).count()
    activos = Socio.query.filter_by(
        id_empresa=empresa.id, fecha_baja=None
    ).count()

    cuotas_pendientes = (
        CuotaSocial.query
        .join(Socio)
        .filter(
            Socio.id_empresa == empresa.id,
            CuotaSocial.estado == 'pendiente',
        )
        .count()
    )

    total_instalaciones = Instalacion.query.filter_by(
        activo=True, id_empresa=empresa.id
    ).count()

    cuentas = CuentaBancaria.query.filter_by(
        id_empresa=empresa.id, activo=True
    ).all()
    saldo_caja = sum(
        (c.saldo_actual for c in cuentas), Decimal('0')
    )

    hoy = date.today()

    reservas_activas = (
        Reserva.query
        .join(Instalacion)
        .filter(
            Reserva.estado.in_(['pendiente', 'confirmada']),
            Instalacion.id_empresa == empresa.id,
            Reserva.fecha_inicio >= hoy,
        )
        .count()
    )

    fecha_limite = hoy + timedelta(days=7)

    proximas_reservas = (
        Reserva.query
        .join(Instalacion)
        .filter(
            Reserva.estado.in_(['pendiente', 'confirmada']),
            Instalacion.id_empresa == empresa.id,
            Reserva.fecha_inicio >= hoy,
            Reserva.fecha_inicio <= fecha_limite,
        )
        .order_by(Reserva.fecha_inicio)
        .limit(5)
        .all()
    )

    proximas_cuotas = (
        CuotaSocial.query
        .join(Socio)
        .filter(
            CuotaSocial.estado == 'pendiente',
            Socio.id_empresa == empresa.id,
            CuotaSocial.fecha_vencimiento >= hoy,
            CuotaSocial.fecha_vencimiento <= fecha_limite,
        )
        .order_by(CuotaSocial.fecha_vencimiento)
        .limit(5)
        .all()
    )

    return render_template('index.html',
                         total_socios=total_socios,
                         activos=activos,
                         cuotas_pendientes=cuotas_pendientes,
                         total_instalaciones=total_instalaciones,
                         reservas_activas=reservas_activas,
                         saldo_caja=saldo_caja,
                         proximas_reservas=proximas_reservas,
                         proximas_cuotas=proximas_cuotas)