# app/services/socio_service.py
from app import db
from app.models import Socio
from sqlalchemy import func, cast, Integer

class SocioService:

    @staticmethod
    def generar_numero_socio(empresa_id, id_familia=None):
        """
        Genera un número de socio único para la empresa.
        - Si id_familia es None, genera un nuevo número de socio (sin guion).
        - Si id_familia existe, genera un subnúmero para esa familia.
        """
        if id_familia is None:
            # 1. Buscar el número de socio más alto (sin guion) como entero
            max_num = db.session.query(
                func.max(cast(Socio.numero_socio, Integer))
            ).filter(
                Socio.id_empresa == empresa_id,
                Socio.numero_socio.notlike('%-%')  # Excluye los que tienen guión
            ).scalar()

            # 2. Si no hay socios, empezar desde 1; si hay, sumar 1 al máximo
            if max_num is None:
                numero = 1
            else:
                numero = max_num + 1

            # 3. Formatear a 4 dígitos (ej. 1 -> "0001")
            return f"{numero:04d}"

        else:
            # Lógica para miembros de familia (sin cambios)
            socio_principal = Socio.query.get(id_familia)
            if not socio_principal:
                raise ValueError("El socio principal no existe")

            miembros = Socio.query.filter_by(id_familia=id_familia).all()
            contador = 1
            for m in miembros:
                if m.numero_socio and '-' in m.numero_socio:
                    try:
                        parte = m.numero_socio.split('-')[1]
                        if int(parte) > contador:
                            contador = int(parte)
                    except:
                        pass

            siguiente = contador + 1
            return f"{socio_principal.numero_socio}-{siguiente}"