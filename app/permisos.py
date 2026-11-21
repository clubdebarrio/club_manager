# app/permisos.py
"""
Catálogo de permisos disponibles en el sistema.

Cada módulo tiene dos permisos:
    <modulo>.ver      -> puede ver listados, detalles, reportes
    <modulo>.editar   -> puede crear, modificar, eliminar

El flag `es_super_admin` en Usuario ignora este catálogo: tiene todo por
definición. Los permisos de "usuarios" y "roles" solo los maneja el
super admin (no se pueden asignar por permiso).
"""

# Lista de (clave_modulo, etiqueta_visible)
MODULOS_DISPONIBLES = [
    ('socios',        'Socios'),
    ('cuotas',        'Cuotas sociales'),
    ('caja',          'Caja y Bancos'),
    ('cheques',       'Cheques'),
    ('ventas',        'Ventas / Tienda'),
    ('compras',       'Compras'),
    ('proveedores',   'Proveedores'),
    ('reservas',      'Reservas'),
    ('instalaciones', 'Instalaciones'),
    ('contabilidad',  'Contabilidad'),
    ('importacion',   'Importación de datos'),
    ('configuracion', 'Configuración de socios'),
    ('auditoria',     'Auditoría'),   
]


def permisos_de_modulo(clave_modulo):
    """Devuelve las dos claves de permiso de un módulo."""
    return [f'{clave_modulo}.ver', f'{clave_modulo}.editar']


def todos_los_permisos():
    """Devuelve la lista completa de claves de permiso."""
    lista = []
    for clave, _ in MODULOS_DISPONIBLES:
        lista.extend(permisos_de_modulo(clave))
    return lista


def es_permiso_valido(clave):
    return clave in todos_los_permisos()