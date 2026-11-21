# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Personal del club (tesorería / administración). Uso diario operativo de Caja y Bancos para ver saldos, registrar movimientos y conciliar. Permisos diferenciados por rol: `puede_ver('caja')` / `puede_editar('caja')`.

## Product Purpose

Club Manager: gestión integral del club (caja y bancos, socios, cuotas, tienda/ventas/compras, reservas, cheques, contabilidad, proveedores). El módulo Caja es la tesorería operativa: cuentas de efectivo/bancos/cartera, movimientos con contrapartida contable, conciliación y extracto por cuenta. Éxito = registrar sin errores, conciliar rápido y tener saldos confiables.

## Positioning

Tesorería atada a contabilidad: todo movimiento exige cuenta de contrapartida, distingue ingreso/egreso por plan de cuentas (Recurso 4.x / Gasto 5.x), soporta modalidad simple y multi-método (splits), conciliación explícita y anulación con movimiento espejo + contra-asiento que preserva trazabilidad.

## Operating Context

Flask server-rendered (Jinja `app/templates/`), Bootstrap 5.3 + FontAwesome 6, layout base `app/templates/base.html`. Ritual diario: revisar `caja/index` (tarjetas de saldo por cuenta) → `detalle_cuenta` (filtros fecha, ver anulados, tabla Debe/Haber/Saldo, paginación, exportar Excel, anular con modal) → `conciliar` (checkbox masivo) → `movimiento_form` (simple/multi, cheque) → `cuentas` / `cuenta_form` (ABM). Moneda pesos es-AR, fechas `dd/mm/yyyy HH:MM`.

## Capabilities and Constraints

Confirmado por el usuario para este rediseño (2026-10-16):
- Alcance: solo visual, sin lógica. Mantener rutas, permisos, formularios WTForms, JS existente, copy y validaciones en las 6 vistas de `app/templates/caja/`: `index.html`, `cuentas.html`, `detalle_cuenta.html`, `conciliar.html`, `movimiento_form.html`, `cuenta_form.html`.
- Stack visual: mantener Bootstrap 5.3 + `base.html` + FontAwesome. Mejorar dentro de eso (tokens, jerarquía, espaciado, tablas, cards, formularios). Sin nuevo framework.
- Responsive: debe funcionar igual en desktop y móvil.
- Reglas de negocio intocables: efectivo/cartera no admite negativo, cartera es informativa, sin cuenta contable no se puede registrar, cheques a cartera 1.1.6, conciliado/pendiente/anulado.

## Brand Commitments

Nombre: Club Manager. Navbar propia `cm-navbar` en `base.html`: fondo `#14211d` / `#1c2e28`, línea `rgba(255,255,255,0.08)`, texto `#ecf3ef` / `#9fb3a9`, acento `#e3a83b`. Tipografía navbar Manrope + sistema. Español rioplatense, formato `|pesos`. Sin rebrand aprobado.

## Evidence on Hand

- Vistas objetivo: `app/templates/caja/index.html`, `cuentas.html`, `detalle_cuenta.html`, `conciliar.html`, `movimiento_form.html`, `cuenta_form.html`.
- Layout y navbar: `app/templates/base.html` (estilos `.cm-*` líneas 50-263).
- Rutas/permisos: `app/caja.py` (blueprint caja).
- Ausencias: sin DESIGN.md, sin goldens visuales, sin screenshots de referencia. No inventar testimonios, precios ni benchmarks.

## Product Principles

1. La tarea manda: ver saldo, registrar y conciliar en el menor número de pasos y sin ambigüedad.
2. Trazabilidad antes que estética: anulado/anulación, conciliado/pendiente y desincronización siempre visibles.
3. Permisos visibles: quien solo ve nunca recibe acciones de edición rotas.
4. Densidad legible: tablas financieras alineadas a la derecha, estados con badge consistente, filtros compactos.
5. Evolución sin ruptura: todo cambio visual respeta rutas, formularios y JS existentes.
