# app/services/foto_socio_service.py
"""
Maneja las fotos de perfil de socios con Supabase Storage.

- Procesa la imagen con Pillow (resize, compresión, strip EXIF).
- Sube/descarga/borra del bucket privado de Supabase.
- Un archivo por socio: {id_empresa}/{id_socio}.jpg
"""
import io
import requests
from datetime import datetime

from flask import current_app
from PIL import Image, ImageOps

from app import db
from app.models import Socio


# --- Parámetros del procesamiento ---
MAX_SIZE_PX = 400          # Lado máximo de la imagen final
JPEG_QUALITY = 82          # Calidad de compresión
MAX_UPLOAD_BYTES = 5 * 1024 * 1024   # 5 MB


class FotoSocioService:

    # ------------------------------------------------------------------
    # Helpers de configuración
    # ------------------------------------------------------------------

    @staticmethod
    def _config():
        return {
            'url': current_app.config['SUPABASE_URL'],
            'key': current_app.config['SUPABASE_SERVICE_ROLE_KEY'],
            'bucket': current_app.config['SUPABASE_BUCKET_SOCIOS'],
        }

    @staticmethod
    def _path(socio):
        """Path determinístico en el bucket."""
        return f'{socio.id_empresa}/{socio.id}.jpg'

    @staticmethod
    def _endpoint(path=''):
        cfg = FotoSocioService._config()
        base = f"{cfg['url']}/storage/v1/object/{cfg['bucket']}"
        return f'{base}/{path}' if path else base

    @staticmethod
    def _headers(content_type=None):
        cfg = FotoSocioService._config()
        headers = {
            'Authorization': f"Bearer {cfg['key']}",
        }
        if content_type:
            headers['Content-Type'] = content_type
        return headers

    # ------------------------------------------------------------------
    # Procesamiento de imagen
    # ------------------------------------------------------------------

    @staticmethod
    def _procesar_imagen(file_stream):
        """
        Lee la imagen subida, la procesa y devuelve bytes JPEG.
        - Resize a max 400x400 manteniendo aspect ratio (con crop cuadrado).
        - Strip EXIF.
        - Comprime a JPEG calidad 82.
        """
        # Si viene un FileStorage de Werkzeug (upload web),
        # usamos su .stream subyacente.
        if hasattr(file_stream, 'stream'):
            file_stream = file_stream.stream
                    
        # Verificar tamaño máximo
        file_stream.seek(0, 2)   # mover al final
        size = file_stream.tell()
        file_stream.seek(0)      # volver al inicio
        if size > MAX_UPLOAD_BYTES:
            raise ValueError(
                f'La imagen supera el máximo permitido '
                f'({MAX_UPLOAD_BYTES // (1024 * 1024)} MB).'
            )

        try:
            img = Image.open(file_stream)
            img.verify()   # valida que sea imagen
            file_stream.seek(0)
            img = Image.open(file_stream)   # reabrir porque verify() la cierra
        except Exception as e:
            raise ValueError(f'El archivo no es una imagen válida: {e}')

        # Convertir a RGB (PNG con transparencia, grayscale, CMYK, etc.)
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Crop cuadrado centrado + resize
        img = ImageOps.fit(
            img,
            (MAX_SIZE_PX, MAX_SIZE_PX),
            method=Image.LANCZOS,
            centering=(0.5, 0.5),
        )

        # Guardar sin EXIF
        output = io.BytesIO()
        img.save(
            output,
            format='JPEG',
            quality=JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
        output.seek(0)
        return output.read()

    # ------------------------------------------------------------------
    # Operaciones con Supabase Storage
    # ------------------------------------------------------------------

    @staticmethod
    def guardar_foto(socio, file_stream):
        """
        Procesa y sube la foto del socio.
        Si ya había una foto, la sobreescribe (upsert).
        Actualiza `foto_actualizada_at`. No commitea.
        """
        cfg = FotoSocioService._config()
        if not cfg['url'] or not cfg['key']:
            raise ValueError(
                'Supabase Storage no está configurado. '
                'Revisá SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY.'
            )

        jpeg_bytes = FotoSocioService._procesar_imagen(file_stream)
        path = FotoSocioService._path(socio)
        url = FotoSocioService._endpoint(path)

        headers = FotoSocioService._headers(content_type='image/jpeg')
        headers['x-upsert'] = 'true'   # sobreescribir si existe

        response = requests.post(
            url,
            data=jpeg_bytes,
            headers=headers,
            timeout=30,
        )
        if response.status_code >= 400:
            current_app.logger.error(
                f'Supabase Storage upload error ({response.status_code}): '
                f'{response.text[:300]}'
            )
            raise ValueError(
                f'Error subiendo la foto: {response.status_code}'
            )

        socio.foto_actualizada_at = datetime.utcnow()
        return len(jpeg_bytes)

    @staticmethod
    def obtener_foto(socio):
        """
        Devuelve los bytes de la foto o None si no existe.
        """
        cfg = FotoSocioService._config()
        if not cfg['url'] or not cfg['key'] or not socio.tiene_foto:
            return None

        url = FotoSocioService._endpoint(FotoSocioService._path(socio))
        response = requests.get(
            url, headers=FotoSocioService._headers(), timeout=10
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    @staticmethod
    def borrar_foto(socio):
        """
        Borra la foto del storage y limpia el timestamp.
        No falla si no existía. No commitea.
        """
        cfg = FotoSocioService._config()
        if not cfg['url'] or not cfg['key']:
            return

        url = FotoSocioService._endpoint(FotoSocioService._path(socio))
        response = requests.delete(
            url, headers=FotoSocioService._headers(), timeout=10
        )
        # 404 significa que no existía → OK
        if response.status_code not in (200, 204, 404):
            current_app.logger.warning(
                f'Error borrando foto de socio {socio.id}: '
                f'{response.status_code} {response.text[:200]}'
            )

        socio.foto_actualizada_at = None