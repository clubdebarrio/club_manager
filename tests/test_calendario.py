"""Regresión del calendario de reservas.

Cubre los dos fallos que dejaban la página sin estilos/rotos:
1. url_for con un placeholder no entero en <int:id> -> 500 al renderizar.
2. r.socio == None (socio borrado) -> 500 en la API de eventos y en el reporte.
"""
from datetime import datetime, timedelta

from sqlalchemy import text

from app import db
from app.models import Instalacion, Reserva, Socio


def _crear_reserva(club):
    inst = Instalacion.query.filter_by(id_empresa=club['empresa'].id).first()
    if inst is None:
        inst = Instalacion(nombre='Cancha A', tipo_cobro='por_hora',
                           id_empresa=club['empresa'].id, activo=True)
        db.session.add(inst)
        db.session.flush()
    hoy = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    reserva = Reserva(
        id_instalacion=inst.id,
        id_socio=club['socios'][0].id,
        fecha_inicio=hoy, fecha_fin=hoy + timedelta(hours=1),
        motivo='Entrenamiento', estado='confirmada', monto_total=1000,
    )
    db.session.add(reserva)
    db.session.commit()
    return reserva


def _socio_a_huerfano(reserva, club):
    """Deja la reserva sin socio asociado (simula un socio borrado).

    Los datos heredados de la DB real tienen FKs huérfanas (las restricciones
    de SQLite no estaban activas al crearse), así que relajamos la FK solo
    durante esta simulación.
    """
    db.session.execute(text('PRAGMA foreign_keys=OFF'))
    reserva.id_socio = 999999
    db.session.commit()
    Socio.query.filter_by(id=club['socios'][0].id).delete(synchronize_session=False)
    db.session.commit()
    db.session.execute(text('PRAGMA foreign_keys=ON'))
    db.session.expire_all()


def test_calendario_renderiza_con_sistema_visual(client_a):
    """La página carga con club.css (super()) y la barra acorde al resto."""
    resp = client_a.get('/reservas/calendario')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200, html[:500]

    # super() recupera el sistema visual de la app
    assert 'css/club.css' in html
    assert 'fullcalendar' in html
    # barra .cm-cal con leyenda y lienzo
    assert 'cm-cal__bar' in html and 'cm-cal__legend' in html
    assert 'cm-cal__canvas' in html and 'id="calendar"' in html
    # url_for exige un entero para <int:id>: nunca un placeholder de texto
    assert 'var URL_DETALLE = "/reservas/0";' in html
    assert '__ID__' not in html
    # permisos: el admin ve la acción de crear
    assert 'Nueva Reserva' in html


def test_api_eventos_con_socio_huerfano_no_revienta(app, client_a, club_a):
    reserva = _crear_reserva(club_a)
    _socio_a_huerfano(reserva, club_a)

    resp = client_a.get('/reservas/api/eventos')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    eventos = resp.get_json()
    propio = next((e for e in eventos if e['id'] == reserva.id), None)
    assert propio is not None, 'la reserva sin socio debe seguir en la API'
    assert 'No Socio' in propio['title']
    assert 'Alfa' not in propio['title']


def test_reporte_con_socio_huerfano_no_revienta(app, client_a, club_a):
    reserva = _crear_reserva(club_a)
    _socio_a_huerfano(reserva, club_a)

    resp = client_a.get('/reservas/reporte?formato=excel')
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
