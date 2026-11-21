# test_conexion.py
import psycopg

DB_USER = 'postgres.ttnaucudygmuxbbljnxy'
DB_PASS = 'BI4NNNu0fKsChtGN'
DB_HOST = 'aws-0-us-east-1.pooler.supabase.com'
DB_PORT = 5432
DB_NAME = 'postgres'

print('Intentando conectar...')
try:
    conn = psycopg.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        dbname=DB_NAME,
        connect_timeout=10,
    )
    print('✅ CONEXIÓN EXITOSA')
    with conn.cursor() as cur:
        cur.execute('SELECT current_user, current_database()')
        print(f'   Usuario: {cur.fetchone()[0]}')
        cur.execute("SELECT extname FROM pg_extension WHERE extname IN ('unaccent','pg_trgm')")
        print(f'   Extensiones: {cur.fetchall()}')
    conn.close()
except Exception as e:
    print(f'❌ Error: {e}')