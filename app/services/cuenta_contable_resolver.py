# app/services/cuenta_contable_resolver.py
"""
Resuelve cuentas contables estándar por clave lógica.

Permite que las rutas y servicios pidan "cuota_social" o "alquiler"
sin conocer los códigos concretos del plan. Si un club renombra o
renumera su plan, se ajusta acá una sola vez.
"""
from app.models import PlanCuenta


CUENTAS_ESTANDAR = {
    'cuota_social':       '4.1',
    'alquiler':           '4.2',
    'venta_indumentaria': '4.3',
    'evento':             '4.4',
    'donacion':           '4.5',

    'cheques_a_cobrar':   '1.1.6',
    'creditos_cuotas':    '1.1.2',
    'creditos_alquiler':  '1.1.3',
    'creditos_ventas':    '1.1.4',
    'compra_mercaderia':  '1.1.8',
    'proveedores':        '2.1.1',
    'cheques_a_pagar':    '2.1.6',
    'cmv':                '5.10',
}


class CuentaContableResolver:

    @classmethod
    def resolver(cls, empresa_id, clave):
        """Devuelve la PlanCuenta o None si no existe."""
        codigo = CUENTAS_ESTANDAR.get(clave)
        if not codigo:
            return None
        return PlanCuenta.query.filter_by(
            id_empresa=empresa_id, codigo=codigo, activo=True
        ).first()

    @classmethod
    def resolver_o_fallar(cls, empresa_id, clave):
        """Como resolver() pero levanta ValueError si no encuentra."""
        cuenta = cls.resolver(empresa_id, clave)
        if not cuenta:
            codigo = CUENTAS_ESTANDAR.get(clave, '(desconocido)')
            raise ValueError(
                f'No se encontró la cuenta contable estándar "{clave}" '
                f'(código {codigo}) para la empresa {empresa_id}. '
                f'Verificá que el plan de cuentas esté cargado.'
            )
        return cuenta