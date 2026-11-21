from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from io import BytesIO
import pandas as pd

class ReporteService:

    @staticmethod
    def generar_pdf(titulo, columnas, datos, orientacion='portrait'):
        """
        Genera un PDF con una tabla.
        :param titulo: str
        :param columnas: list de nombres de columnas
        :param datos: list de dicts o listas (si son dicts, se usan las claves)
        :param orientacion: 'portrait' o 'landscape'
        """
        if datos and isinstance(datos[0], dict):
            datos_procesados = [[d.get(col, '') for col in columnas] for d in datos]
        else:
            datos_procesados = datos

        buffer = BytesIO()
        page_size = landscape(letter) if orientacion == 'landscape' else letter
        doc = SimpleDocTemplate(buffer, pagesize=page_size,
                                rightMargin=0.5*inch, leftMargin=0.5*inch,
                                topMargin=0.5*inch, bottomMargin=0.5*inch)

        styles = getSampleStyleSheet()
        estilo_titulo = ParagraphStyle('Titulo', parent=styles['Heading1'], fontSize=16, alignment=1, spaceAfter=12)
        elementos = []

        elementos.append(Paragraph(titulo, estilo_titulo))
        elementos.append(Spacer(1, 0.2*inch))

        # Tabla
        tabla_datos = [columnas] + datos_procesados
        tabla = Table(tabla_datos, repeatRows=1)
        tabla.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('BOTTOMPADDING', (0,0), (-1,0), 8),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ]))

        if orientacion == 'landscape':
            col_width = 9.5 * inch / len(columnas)
        else:
            col_width = 7 * inch / len(columnas)
        tabla._argW = [col_width] * len(columnas)

        elementos.append(tabla)
        doc.build(elementos)
        buffer.seek(0)
        return buffer

    @staticmethod
    def generar_excel(titulo, columnas, datos, nombre_hoja='Reporte'):
        """
        Genera un archivo Excel con una tabla.
        :param titulo: str (se coloca como título en la primera fila)
        :param columnas: list
        :param datos: list de dicts o listas
        :param nombre_hoja: str
        """
        if datos and isinstance(datos[0], dict):
            datos_procesados = [[d.get(col, '') for col in columnas] for d in datos]
        else:
            datos_procesados = datos

        df = pd.DataFrame(datos_procesados, columns=columnas)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name=nombre_hoja, index=False)
        output.seek(0)
        return output