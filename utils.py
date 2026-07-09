import mysql.connector

def get_db_connection():
    """
    Connect to PythonAnywhere MySQL database.
    Replace placeholders with your actual credentials.
    """
    conn = mysql.connector.connect(
        user="yourusername",  # e.g. kuantorflow
        password="yourpassword",
        host="yourusername.mysql.pythonanywhere-services.com",
        database="yourusername$default"
    )
    return conn
