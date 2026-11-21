# seed_categorias.py
from app import create_app, db
from app.models import Empresa, CategoriaProducto

def seed_categorias():
    app = create_app()
    with app.app_context():
        empresa = Empresa.query.first()
        if not empresa:
            print("Primero crea un club (empresa)")
            return

        categorias_default = [
            "Remeras",
            "Pantalones",
            "Camperas",
            "Accesorios",
            "Calzado",
            "Otros"
        ]

        for nombre in categorias_default:
            if not CategoriaProducto.query.filter_by(nombre=nombre, id_empresa=empresa.id).first():
                cat = CategoriaProducto(nombre=nombre, id_empresa=empresa.id)
                db.session.add(cat)
        db.session.commit()
        print(f"Categorías creadas: {categorias_default}")

if __name__ == '__main__':
    seed_categorias()