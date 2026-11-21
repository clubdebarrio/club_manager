---
version: 1
slug: "app-templates-base-html"
primary_target: "app/templates/base.html"
related_targets: ["app/templates/caja/index.html","app/templates/caja/cuentas.html","app/templates/caja/detalle_cuenta.html","app/templates/caja/conciliar.html","app/templates/caja/movimiento_form.html","app/templates/caja/cuenta_form.html"]
---

# Surface brief — Rediseño visual global Club Manager (Operate)

Scope: todas las vistas de app/templates (92 archivos). Modo: Operate.
Audience/job: tesorería/admin uso diario — ver saldos, registrar, conciliar rápido.
Constraints: solo visual. Rutas, permisos puede_ver/puede_editar, WTForms, JS, copy y validaciones intactos. Bootstrap 5.3 + base.html + FontAwesome. Responsive desktop/móvil. Reglas: efectivo/cartera sin negativo, cartera informativa, sin contable no registra, cheques a 1.1.6.

## Direction contract
THESIS: Extracto bancario contemporáneo para tesorería de club: una sola tinta verde club + un ámbar reservado; jerarquía por peso/caso/regla, nunca por tamaño o color decorativo. Rehúsa el dashboard-marketing (hero-métricas, kickers, cards anidadas).
OWN-WORLD: Papel #f2f4f2, superficie #fff, tinta #14211d, línea #dde5e0, header tabla pine #1c2e28 uppercase .8rem, acento #e3a83b solo foco/estados. Manrope + sistema, tabular-nums financiero. Radius 12/16, shadow con offset+blur. Sin kickers, sin nested cards, sin border-left grueso, sin gradient/glass.
STORY: El tesorero entiende en segundos dónde está, cuánto hay y qué falta (conciliar/verificar), registra sin fricción y traza anulado/espejo siempre visible.
FIRST VIEWPORT: page-head — título 1.55rem/700/-0.02em + sub muted .92rem/68ch, acciones derecha con wrap; tabla header pine sticky; filtros en panel compacto; action-bar sticky abajo; estados Conciliado/Pendiente/Anulado/Espejo como badges + fila teñida sin romper grilla.
FORM: grounded #5 (cuaderno de caja + extracto bancario), seed 2d1007dd.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
