# app/utils_db.py
"""
Context manager para operaciones transaccionales.

Uso:
    from app.utils_db import transaccion

    with transaccion():
        venta = Venta(...)
        db.session.add(venta)
        CajaService.registrar_movimiento(data, commit=False)
        ContabilidadService.generar_asiento(movimiento)

    # Al salir del with: commit automático.
    # Si hay excepción: rollback automático y se propaga.
"""

from contextlib import contextmanager
from app import db


@contextmanager
def transaccion():
    """
    Agrupa varias operaciones en una única transacción.

    - Al salir del `with` sin excepción → commit.
    - Al salir con excepción → rollback + re-raise.
    """
    try:
        yield
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise