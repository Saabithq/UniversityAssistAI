
import pymysql

def run_sql_file(filename):
    try:
        connection = pymysql.connect(
            host="localhost",
            user="root",
            password="1234",
            port=3306,
            cursorclass=pymysql.cursors.DictCursor
        )
        
        with connection.cursor() as cursor:
            with open(filename, 'r') as f:
                sql_script = f.read()
                
            # Split by semicolon to execute one by one
            statements = sql_script.split(';')
            for statement in statements:
                if statement.strip():
                    try:
                        cursor.execute(statement)
                        print(f"Executed: {statement[:50]}...")
                    except Exception as e:
                        print(f"Error executing statement: {e}")
        
        connection.commit()
        print("SQL script executed successfully.")
        
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if 'connection' in locals() and connection.open:
            connection.close()

import os

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sql_path = os.path.join(script_dir, "pcb_db_setup.sql")
    run_sql_file(sql_path)
