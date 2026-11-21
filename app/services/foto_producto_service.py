# app/services/foto_producto_service.py
import io
import requests
from datetime import datetime
from flask import current_app
from PIL import Image, ImageOps
from app import db

MAX_SIZE_PX = 500
JPEG_QUALITY = 85
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

class FotoProductoService:

    @staticmethod
    def _config():
        # Usamos el mismo bucket de socios o uno propio si lo preferís
        return {
            'url': current_app.config['SUPABASE_URL'],
            'key': current_app.config['SUPABASE_SERVICE_ROLE_KEY'],
            'bucket': current_app.config.get('SUPABASE_BUCKET_PRODUCTOS', current_app.config['SUPABASE_BUCKET_SOCIOS']),
        }

    @staticmethod
    def _path(producto):
        return f'productos/{producto.id_empresa}/{producto.id}.jpg'

    @staticmethod
    def _endpoint(path=''):
        cfg = FotoProductoService._config()
        base = f"{cfg['url']}/storage/v1/object/{cfg['bucket']}"
        return f'{base}/{path}' if path else base

    @staticmethod
    def _headers(content_type=None):
        cfg = FotoProductoService._config()
        headers = {'Authorization': f"Bearer {cfg['key']}"}
        if content_type:
            headers['Content-Type'] = content_type
        return headers

    @staticmethod
    def _procesar_imagen(file_stream):
        if hasattr(file_stream, 'stream'):
            file_stream = file_stream.stream
            
        file_stream.seek(0, 2)
        size = file_stream.tell()
        file_stream.seek(0)
        if size > MAX_UPLOAD_BYTES:
            raise ValueError(f'La imagen supera el máximo permitido (5 MB).')

        try:
            img = Image.open(file_stream)
            img.verify()
            file_stream.seek(0)
            img = Image.open(file_stream)
        except Exception as e:
            raise ValueError(f'El archivo no es una imagen válida: {e}')

        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Para productos, un crop cuadrado está bien (o podés usar contain si preferís no recortar)
        img = ImageOps.fit(img, (MAX_SIZE_PX, MAX_SIZE_PX), method=Image.LANCZOS, centering=(0.5, 0.5))

        output = io.BytesIO()
        img.save(output, format='JPEG', quality=JPEG_QUALITY, optimize=True, progressive=True)
        output.seek(0)
        return output.read()

    @staticmethod
    def guardar_foto(producto, file_stream):
        cfg = FotoProductoService._config()
        if not cfg['url'] or not cfg['key']:
            raise ValueError('Supabase Storage no está configurado.')

        jpeg_bytes = FotoProductoService._procesar_imagen(file_stream)
        path = FotoProductoService._path(producto)
        url = FotoProductoService._endpoint(path)

        headers = FotoProductoService._headers(content_type='image/jpeg')
        headers['x-upsert'] = 'true'

        response = requests.post(url, data=jpeg_bytes, headers=headers, timeout=30)
        if response.status_code >= 400:
            current_app.logger.error(f'Supabase error: {response.text[:300]}')
            raise ValueError('Error subiendo la foto a Supabase.')

        producto.foto_actualizada_at = datetime.utcnow()
        return len(jpeg_bytes)

    @staticmethod
    def obtener_foto(producto):
        cfg = FotoProductoService._config()
        if not cfg['url'] or not cfg['key'] or not producto.tiene_foto:
            return None

        url = FotoProductoService._endpoint(FotoProductoService._path(producto))
        response = requests.get(url, headers=FotoProductoService._headers(), timeout=10)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    @staticmethod
    def borrar_foto(producto):
        cfg = FotoProductoService._config()
        if not cfg['url'] or not cfg['key']:
            return

        url = FotoProductoService._endpoint(FotoProductoService._path(producto))
        response = requests.delete(url, headers=FotoProductoService._headers(), timeout=10)
        
        producto.foto_actualizada_at = None