import os
import numpy as np
import joblib
import pymysql
import threading

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    g,
    flash,
    Response
)

from werkzeug.utils import secure_filename
from langchain_core.messages import AIMessage, HumanMessage

from utils import (
    get_pdf_text,
    get_text_chunks,
    user_input,
    FAISS_BASE_PATH,
    index_pdf,
    user_input_stream
)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = "qwer"

# Function to get database connection per request
def get_db_connection():
    if 'db' not in g:
        g.db = pymysql.connect(host="localhost", user="root", password="1234", port=3306, db="pcb")
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

@app.route('/')
def main():
    return render_template("login.html")

@app.route('/login', methods=['POST'])
def login():
    if request.method == 'POST':
        username = request.form['textfield']
        password = request.form['textfield2']
        
        try:
            con = get_db_connection()
            cmd = con.cursor()
            
            # Protect against SQL injection using parameterized queries
            cmd.execute("SELECT * FROM login WHERE username=%s AND password=%s", (username, password))
            s = cmd.fetchone()

            if s:
                if s[3] == "admin":
                    session['username'] = username
                    session['role'] = 'admin'
                    return redirect(url_for('admin_users'))
                elif s[3] == "user":
                    session['username'] = username # Store username in session
                    session['role'] = 'user'
                    return redirect(url_for('user_dashboard'))  # Redirect to user dashboard
                else:
                    flash("Invalid user type", "error")
                    return render_template("login.html")
            else:
                flash("Invalid username or password", "error")
                return render_template("login.html")
        except Exception as e:
            flash("An error occurred. Please try again.", "error")
            print("Login error:", e)
            return render_template("login.html")
        
    return redirect(url_for('main'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('main'))

@app.after_request
def add_header(response):
    """
    Control caching: disable for dynamic HTML, enable for static assets.
    """
    # If the request is for a static file, allow caching
    if request.path.startswith('/static/'):
        response.headers["Cache-Control"] = "public, max-age=31536000" # 1 year
    else:
        # Disable caching for dynamic routes to ensure fresh data
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.route('/register')
def register():
    return render_template("reg.html")

@app.route('/register1', methods=['POST', 'GET'])
def register1():
    if request.method == 'POST':
        try:
            fn = request.form.get('fname')
            ln = request.form.get('lname')
            cont = request.form.get('contacts')
            usn = request.form.get('username')
            pwd = request.form.get('password')
            confirm_pwd = request.form.get('confirm_password')
            
            if not all([fn, ln, cont, usn, pwd, confirm_pwd]):
                flash("All fields are required", "error")
                return render_template("reg.html")
            
            # Password validation
            if len(pwd) < 8:
                flash("Password must be at least 8 characters long", "error")
                return render_template("reg.html")
            
            if not any(c.isupper() for c in pwd):
                flash("Password must contain at least one uppercase letter", "error")
                return render_template("reg.html")
            
            if not any(c.islower() for c in pwd):
                flash("Password must contain at least one lowercase letter", "error")
                return render_template("reg.html")
            
            if not any(c.isdigit() for c in pwd):
                flash("Password must contain at least one number", "error")
                return render_template("reg.html")
            
            if not any(c in "!@#$%^&*" for c in pwd):
                flash("Password must contain at least one special character (!@#$%^&*)", "error")
                return render_template("reg.html")
            
            if pwd != confirm_pwd:
                flash("Passwords do not match", "error")
                return render_template("reg.html")
            
            try:
                con = get_db_connection()
                cmd = con.cursor()

                # Check if username already exists
                cmd.execute("SELECT * FROM login WHERE username=%s", (usn,))
                if cmd.fetchone():
                    flash("Username already exists", "error")
                    return render_template("reg.html")

                # Insert user into login table
                try:
                    cmd.execute("INSERT INTO login (username, password, type) VALUES (%s, %s, %s)", (usn, pwd, 'user'))
                except Exception as e:
                    print("Login table insert error:", str(e))
                    flash(f"Error creating login account: {str(e)}", "error")
                    return render_template("reg.html")
                
                # Insert user details into user table
                try:
                    cmd.execute("INSERT INTO user (first_name, last_name, contact, username) VALUES (%s, %s, %s, %s)", 
                              (fn, ln, cont, usn))
                except Exception as e:
                    print("User table insert error:", str(e))
                    flash(f"Error creating user profile: {str(e)}", "error")
                    return render_template("reg.html")
                
                con.commit()
                flash("Registration successful! Please login.", "success")
                return redirect(url_for('main'))
                
            except Exception as e:
                print("Database connection error:", str(e))
                flash(f"Database connection error: {str(e)}", "error")
                return render_template("reg.html")
            
        except Exception as e:
            print("Registration error:", str(e))
            flash(f"Registration error: {str(e)}", "error")
            return render_template("reg.html")
    
    return render_template("reg.html")
@app.route('/user_dashboard')
def user_dashboard():
    if 'username' not in session:
        return redirect(url_for('main'))

    username = session['username']
    con = get_db_connection()
    cmd = con.cursor()

    # User info
    cmd.execute("SELECT * FROM user WHERE username=%s", (username,))
    user = cmd.fetchone()

    # Todos
    cmd.execute("""
        SELECT * FROM todo 
        WHERE user_id = (SELECT id FROM user WHERE username=%s)
    """, (username,))
    todos = cmd.fetchall()


    # 🔹 Fetch real syllabus data from DB (Optimized)
    # Get all courses, semesters, and subjects in fewer queries
    cmd.execute("SELECT id, name FROM courses")
    courses = cmd.fetchall()
    
    course_ids = [c[0] for c in courses]
    syllabus_data = []
    
    if course_ids:
        # Fetch all semesters for these courses in one query
        format_strings = ','.join(['%s'] * len(course_ids))
        cmd.execute(f"SELECT id, course_id, name FROM semesters WHERE course_id IN ({format_strings}) ORDER BY id ASC", tuple(course_ids))
        all_semesters = cmd.fetchall()
        
        # Group semesters by course_id
        sems_by_course = {}
        for s in all_semesters:
            c_id = s[1]
            if c_id not in sems_by_course:
                sems_by_course[c_id] = []
            sems_by_course[c_id].append({"id": s[0], "name": s[2], "children": []})
            
        for c in courses:
            syllabus_data.append({
                "id": c[0],
                "name": c[1],
                "children": sems_by_course.get(c[0], [])
            })
    
    syllabus = syllabus_data


    return render_template(
        "user_dashboard.html",
        user=user,
        todos=todos,
        syllabus=syllabus
    )

# ---------------- SYLLABUS FLOW (USER) ---------------- #

@app.route('/syllabus/<int:course_id>')
def syllabus_semesters(course_id):
    con = get_db_connection()
    cmd = con.cursor()
    
    # Get Course Name
    cmd.execute("SELECT name FROM courses WHERE id=%s", (course_id,))
    course = cmd.fetchone()
    if not course:
        return redirect(url_for('user_dashboard'))

    # Get Semesters
    cmd.execute("SELECT * FROM semesters WHERE course_id=%s", (course_id,))
    semesters = cmd.fetchall()

    return render_template(
        "syllabus_semesters.html",
        course_id=course_id,
        course_name=course[0], # Fetching name from tuple/list based on cursor type
        semesters=semesters
    )

@app.route('/syllabus/<int:course_id>/<int:semester_id>')
def syllabus_subjects(course_id, semester_id):
    con = get_db_connection()
    cmd = con.cursor()

    cmd.execute("SELECT name FROM semesters WHERE id=%s", (semester_id,))
    semester_name = cmd.fetchone()

    cmd.execute("SELECT * FROM subjects WHERE semester_id=%s", (semester_id,))
    subjects = cmd.fetchall()

    return render_template(
        "syllabus_subjects.html",
        semester=semester_name[0] if semester_name else "Unknown Semester",
        subjects=subjects,
        course_id=course_id,
        semester_id=semester_id
    )
@app.route('/syllabus/subject/<int:subject_id>/chatAi', methods=["GET", "POST"])
def index(subject_id):
    con = get_db_connection()
    cmd = con.cursor()

    if subject_id == 0:
        subject = ("General Chat",)
        subject1 = (0, "General Chat", None, None)
        units = []
        unit_ids = []  # Fix NameError
        pdf_paths = []
        materials_by_unit = {}
    else:
        # Subject
        cmd.execute("SELECT name FROM subjects WHERE id=%s", (subject_id,))
        subject = cmd.fetchone()
        cmd.execute("SELECT * FROM subjects WHERE id=%s", (subject_id,))
        subject1 = cmd.fetchone()

        # Units
        cmd.execute(
            "SELECT * FROM units WHERE subject_id=%s ORDER BY id ASC",
            (subject_id,)
        )
        units = cmd.fetchall()
        unit_ids = [u[0] for u in units]

        # Materials + PDF paths
        pdf_paths, materials_by_unit = get_pdf_paths_for_subject(cmd, unit_ids)

    session["current_subject_id"] = subject_id

    # Chat session reset if requested
    if (request.method == "GET" and request.args.get('reset') == '1') or (request.method == "GET" and not any(arg in request.args for arg in ['material_id', 'reset'])):
        print(f"Fresh entry or reset requested for subject {subject_id}. Clearing session.")
        session.pop("chat_history", None)
        session.pop("active_material", None)
        session.pop("active_material_title", None)
        
        # Cleanup temp indices for this subject and user
        try:
            username = session.get('username', 'anonymous')
            user_temp_path = os.path.join(FAISS_BASE_PATH, "temp", username, str(subject_id))
            if os.path.exists(user_temp_path):
                import shutil
                shutil.rmtree(user_temp_path)
                print(f"Deleted temp indices for {username} in subject {subject_id}")
        except Exception as e:
            print(f"Error cleaning up temp indices: {e}")
            
        if request.args.get('reset') == '1':
            try:
                username = session.get('username', 'anonymous')
                con_reset = get_db_connection()
                cmd_reset = con_reset.cursor()
                cmd_reset.execute("DELETE FROM chat_history WHERE username=%s AND subject_id=%s", (username, subject_id))
                con_reset.commit()
            except Exception as e:
                print(f"Error resetting DB chat history: {e}")
            return redirect(url_for('index', subject_id=subject_id))
        
    init_chat_session(is_general=(subject_id == 0))

    # --- HANDLE UNIT OR MATERIAL SELECTION ---
    material_id = request.args.get('material_id')
    unit_id_param = request.args.get('unit_id')
    
    if material_id:
        material_id = int(material_id)
        if session.get("active_material") != material_id:
            try:
                cmd.execute("SELECT filepath, title FROM materials WHERE id=%s", (material_id,))
                res = cmd.fetchone()
                if res:
                    session["active_material"] = material_id
                    session["active_material_title"] = res[1]
                    m_index_path = os.path.join(FAISS_BASE_PATH, "materials", str(material_id))
                    if not os.path.exists(m_index_path):
                        abs_pdf_path = os.path.join(app.root_path, 'static', res[0])
                        index_pdf(abs_pdf_path, m_index_path)
            except Exception as e:
                print(f"Error selecting material {material_id}: {e}")
    elif unit_id_param:
        unit_id_param = int(unit_id_param)
        try:
            cmd.execute("SELECT name FROM units WHERE id=%s", (unit_id_param,))
            u_res = cmd.fetchone()
            if u_res:
                session["active_material_title"] = f"Unit: {u_res[0]}"
        except Exception as e:
            print(f"Error selecting unit {unit_id_param}: {e}")

    # --- PROACTIVE SYLLABUS INDEXING ---
    for u_id in unit_ids:
        materials = materials_by_unit.get(u_id, [])
        for m in materials:
            m_id = m[0]
            m_path = m[3]
            m_idx_path = os.path.join(FAISS_BASE_PATH, "materials", str(m_id))
            if not os.path.exists(m_idx_path):
                abs_pdf_path = os.path.join(app.root_path, 'static', m_path)
                if os.path.exists(abs_pdf_path):
                    threading.Thread(target=index_pdf, args=(abs_pdf_path, m_idx_path), daemon=True).start()

    # --- GATHER ALL INDEX PATHS ---
    index_paths = []
    
    # 1. Add syllabus materials indices (filter if unit_id_param is set)
    for u_id in unit_ids:
        if unit_id_param and u_id != unit_id_param:
            continue
            
        materials = materials_by_unit.get(u_id, [])
        for m in materials:
            m_id = m[0]
            m_index_path = os.path.join(FAISS_BASE_PATH, "materials", str(m_id))
            if os.path.exists(m_index_path):
                index_paths.append(m_index_path)

    # 2. Add temporary user indexing paths
    username = session.get('username', 'anonymous')
    user_temp_path = os.path.join(FAISS_BASE_PATH, "temp", username, str(subject_id))
    if os.path.exists(user_temp_path):
        for temp_name in os.listdir(user_temp_path):
            index_paths.append(os.path.join(user_temp_path, temp_name))

    session["current_index_paths"] = index_paths
    session["current_subject_id"] = subject_id  # Store this for chat_stream dynamic scan
    session["current_unit_id"] = unit_id_param # Store filter

    if request.method == "POST":
        # Standard form upload (redirecting for compatibility)
        if "pdfs" in request.files:
            files = request.files.getlist("pdfs")
            if files and files[0].filename != "":
                handle_manual_pdf_upload(files, subject_id)
                new_titles = ", ".join([f.filename for f in files])
                if session.get("active_material_title"):
                    session["active_material_title"] += f", {new_titles}"
                else:
                    session["active_material_title"] = new_titles
                return redirect(request.url)

    return render_template(
        "index.html",
        subject=subject[0] if subject else "Unknown Subject",
        units=units,
        subject1=subject1,
        materials_by_unit=materials_by_unit,
        chat=session["chat_history"]
    )

@app.route('/chat/upload/<int:subject_id>', methods=['POST'])
def chat_ajax_upload(subject_id):
    if 'username' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    if "pdfs" not in request.files:
        return jsonify({"error": "No files uploaded"}), 400
        
    files = request.files.getlist("pdfs")
    if not files or files[0].filename == "":
        return jsonify({"error": "No files selected"}), 400
        
    handle_manual_pdf_upload(files, subject_id)
    
    new_titles_list = [secure_filename(f.filename) for f in files]
    new_titles = ", ".join(new_titles_list)
    
    if session.get("active_material_title"):
        session["active_material_title"] += f", {new_titles}"
    else:
        session["active_material_title"] = new_titles
    session.modified = True
    
    return jsonify({
        "status": "success",
        "filenames": new_titles_list
    })

@app.route('/chat/stream', methods=['POST'])
def chat_stream():
    if 'username' not in session:
        return Response("Unauthorized", status=401)
    
    data = request.json
    question = data.get('question')
    
    # --- DYNAMIC RE-SCAN OF INDEX PATHS ---
    # This ensures newly uploaded files are included without a refresh
    subject_id = session.get("current_subject_id")
    unit_id_param = session.get("current_unit_id")
    username = session.get('username', 'anonymous')
    
    index_paths = []
    
    # Re-gather syllabus indices
    if subject_id is not None:
        con = get_db_connection()
        cmd = con.cursor()
        cmd.execute("SELECT id FROM units WHERE subject_id=%s", (subject_id,))
        subject_unit_ids = [u[0] for u in cmd.fetchall()]
        
        for u_id in subject_unit_ids:
            if unit_id_param and u_id != unit_id_param:
                continue
            cmd.execute("SELECT id FROM materials WHERE unit_id=%s", (u_id,))
            m_ids = [m[0] for m in cmd.fetchall()]
            for m_id in m_ids:
                m_path = os.path.join(FAISS_BASE_PATH, "materials", str(m_id))
                if os.path.exists(m_path):
                    index_paths.append(m_path)
    
    # Re-gather temp indices
    user_temp_path = os.path.join(FAISS_BASE_PATH, "temp", username, str(subject_id or 0))
    if os.path.exists(user_temp_path):
        for temp_name in os.listdir(user_temp_path):
            index_paths.append(os.path.join(user_temp_path, temp_name))

    # Update session for consistency
    session["current_index_paths"] = index_paths
    session.modified = True
    
    if "chat_history" not in session:
        session["chat_history"] = []
    session["chat_history"].append({"role": "Human", "content": question})
    session.modified = True

    try:
        con_ins = get_db_connection()
        cmd_ins = con_ins.cursor()
        cmd_ins.execute("INSERT INTO chat_history (username, subject_id, role, content) VALUES (%s, %s, %s, %s)", (username, subject_id or 0, 'Human', question))
        con_ins.commit()
    except Exception as e:
        print(f"Error saving human msg: {e}")

    def generate(app_ctx):
        with app_ctx:
            full_response = ""
            for chunk in user_input_stream(question, index_paths):
                full_response += chunk
                yield chunk
            
            # Save AI response to DB after completion
            try:
                con_gen = get_db_connection()
                cmd_gen = con_gen.cursor()
                cmd_gen.execute("INSERT INTO chat_history (username, subject_id, role, content) VALUES (%s, %s, %s, %s)", (username, subject_id or 0, 'AI', full_response))
                con_gen.commit()
            except Exception as e:
                print(f"Error saving AI msg: {e}")

    return Response(generate(app.app_context()), mimetype='text/plain')
@app.route('/syllabus/subject/<int:subject_id>')
def syllabus_units(subject_id):
    con = get_db_connection()
    cmd = con.cursor()
    
    # get the subject name and full row
    cmd.execute("SELECT name FROM subjects WHERE id=%s", (subject_id,))
    subject = cmd.fetchone()
    cmd.execute("SELECT * FROM subjects WHERE id=%s", (subject_id,))
    subject1 = cmd.fetchone()
    
    # determine semester and course ids so we can build a "back" link
    semester_id = subject1[1] if subject1 else None
    course_id = None
    if semester_id is not None:
        cmd.execute("SELECT course_id FROM semesters WHERE id=%s", (semester_id,))
        res = cmd.fetchone()
        course_id = res[0] if res else None

    cmd.execute("SELECT * FROM units WHERE subject_id=%s ORDER BY id ASC", (subject_id,))
    units = cmd.fetchall()
    
    # Fetch all materials for these units
    unit_ids = [u[0] for u in units]
    materials_by_unit = {}
    if unit_ids:
        format_strings = ','.join(['%s'] * len(unit_ids))
        cmd.execute(f"SELECT * FROM materials WHERE unit_id IN ({format_strings})", tuple(unit_ids))
        all_materials = cmd.fetchall()
        
        for m in all_materials:
            # m: (id, unit_id, title, filepath, type, created_at)
            u_id = m[1]
            if u_id not in materials_by_unit:
                materials_by_unit[u_id] = []
            materials_by_unit[u_id].append(m)
    
    return render_template(
        "syllabus_units.html",
        subject=subject[0] if subject else "Unknown Subject",
        units=units,
        subject1=subject1,
        course_id=course_id,
        semester_id=semester_id,
        materials_by_unit=materials_by_unit
    )




@app.route('/todo/create', methods=['POST'])
def create_todo():
    if 'username' not in session:
        return redirect(url_for('main'))
    title = request.form.get('title')
    description = request.form.get('description')
    username = session['username']
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT id FROM user WHERE username=%s", (username,))
    user_id = cmd.fetchone()[0]
    cmd.execute("INSERT INTO todo (user_id, title, description) VALUES (%s, %s, %s)", (user_id, title, description))
    con.commit()
    return redirect(url_for('user_dashboard'))

@app.route('/todo/update/<int:todo_id>', methods=['POST'])
def update_todo(todo_id):
    if 'username' not in session:
        return redirect(url_for('main'))
    title = request.form.get('title')
    description = request.form.get('description')
    status = request.form.get('status')
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("UPDATE todo SET title=%s, description=%s, status=%s WHERE id=%s", (title, description, status, todo_id))
    con.commit()
    return redirect(url_for('user_dashboard'))

@app.route('/todo/delete/<int:todo_id>', methods=['POST'])
def delete_todo(todo_id):
    if 'username' not in session:
        return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("DELETE FROM todo WHERE id=%s", (todo_id,))
    con.commit()
    return redirect(url_for('user_dashboard'))

@app.route('/profile/view')
def view_profile():
    if 'username' not in session:
        return redirect(url_for('main'))
    username = session['username']
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT * FROM user WHERE username=%s", (username,))
    user = cmd.fetchone()
    return render_template("profile.html", user=user)

UPLOAD_FOLDER = os.path.join('static', 'profile_photos')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# --------- RAG PDF HELPERS --------- #

def get_pdf_paths_for_subject(cmd, unit_ids):
    pdf_paths = []
    materials_by_unit = {}

    if unit_ids:
        format_strings = ','.join(['%s'] * len(unit_ids))
        cmd.execute(
            f"SELECT * FROM materials WHERE unit_id IN ({format_strings})",
            tuple(unit_ids)
        )
        all_materials = cmd.fetchall()

        for m in all_materials:
            u_id = m[1]
            rel_path = m[3]  # "materials/file.pdf"

            if u_id not in materials_by_unit:
                materials_by_unit[u_id] = []
            materials_by_unit[u_id].append(m)

            if rel_path and rel_path.lower().endswith(".pdf"):
                abs_path = os.path.join(app.root_path, 'static', rel_path)
                if os.path.exists(abs_path):
                    pdf_paths.append(abs_path)

    return pdf_paths, materials_by_unit


def auto_load_subject_pdfs(pdf_paths, subject_id):
    if not pdf_paths:
        return

    if session.get("vector_loaded_for_subject") == subject_id:
        return

    # Check if vector store already exists on disk to avoid re-processing
    subject_path = os.path.join(FAISS_BASE_PATH, f"subject_{subject_id}")
    if os.path.exists(subject_path):
        print(f"Vector store for subject {subject_id} already exists. Skipping re-index.")
        session["vector_loaded_for_subject"] = subject_id
        return

    def background_process():
        raw_text = get_pdf_text(pdf_paths)
        chunks = get_text_chunks(raw_text)
        get_vector_store(chunks, subject_id)

    threading.Thread(target=background_process, daemon=True).start()
    session["vector_loaded_for_subject"] = subject_id




def init_chat_session(is_general=False):
    subject_id = session.get("current_subject_id", 0)
    username = session.get("username", "anonymous")
    try:
        con = get_db_connection()
        cmd = con.cursor()
        cmd.execute("SELECT role, content FROM chat_history WHERE username=%s AND subject_id=%s ORDER BY created_at ASC", (username, subject_id))
        history = cmd.fetchall()
        if history:
            session["chat_history"] = [{"role": row[0], "content": row[1]} for row in history]
        else:
            msg = "Hello 👋 I'm your General PDF Assistant. Please upload any PDF in the sidebar to start chatting!" if is_general else "Hello 👋 Ask questions from your syllabus PDFs."
            session["chat_history"] = [{"role": "AI", "content": msg}]
    except Exception as e:
        print(f"Error loading chat history: {e}")
        if "chat_history" not in session:
            msg = "Hello 👋 I'm your General PDF Assistant. Please upload any PDF in the sidebar to start chatting!" if is_general else "Hello 👋 Ask questions from your syllabus PDFs."
            session["chat_history"] = [{"role": "AI", "content": msg}]
    
    # Ensure these markers exist
    if "current_subject_id" not in session: session["current_subject_id"] = 0
    if "current_unit_id" not in session: session["current_unit_id"] = None
    if "current_index_paths" not in session: session["current_index_paths"] = []
    
    session.modified = True


def handle_question(question, subject_id):
    session["chat_history"].append({"role": "Human", "content": question})
    response = user_input(question, subject_id)
    session["chat_history"].append({"role": "AI", "content": response})
    session.modified = True


def handle_manual_pdf_upload(files, subject_id):
    if not files:
        return

    file_paths = []
    save_path = os.path.join(app.root_path, 'static', 'materials')
    os.makedirs(save_path, exist_ok=True)

    for file in files:
        filename = secure_filename(file.filename)
        full_path = os.path.join(save_path, filename)
        file.save(full_path)
        file_paths.append(full_path)

    username = session.get('username', 'anonymous')

    def background_manual_process(uname, s_id):
        user_temp_root = os.path.join(FAISS_BASE_PATH, "temp", uname, str(s_id))
        
        for file_path in file_paths:
            filename = os.path.basename(file_path)
            # Create a unique temp folder for each file to allow granular merging
            temp_storage = os.path.join(user_temp_root, secure_filename(filename))
            index_pdf(file_path, temp_storage)
            
        print(f"Background indexing complete for manual uploads in subject {s_id}")

    threading.Thread(
        target=background_manual_process, 
        args=(username, subject_id),
        daemon=True
    ).start()
    
    # Add status message in main thread
    msg = f"Uploading and indexing {len(files)} new document(s). I'll be ready to answer questions about them in a moment!"
    session["chat_history"].append({
        "role": "AI", 
        "content": msg
    })
    session.modified = True

    try:
        con = get_db_connection()
        cmd = con.cursor()
        cmd.execute("INSERT INTO chat_history (username, subject_id, role, content) VALUES (%s, %s, %s, %s)", (username, subject_id, 'AI', msg))
        con.commit()
    except Exception as e:
        print(f"Error saving upload status msg: {e}")


@app.route('/profile/edit', methods=['GET', 'POST'])
def edit_profile():
    if 'username' not in session:
        return redirect(url_for('main'))
    username = session['username']
    con = get_db_connection()
    cmd = con.cursor()
    if request.method == 'POST':
        fn = request.form.get('fname')
        ln = request.form.get('lname')
        cont = request.form.get('contacts')
        photo = request.files.get('photo')
        photo_filename = None
        if photo and allowed_file(photo.filename):
            filename = secure_filename(photo.filename)
            photo_filename = f"{username}_{filename}"
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename)
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            photo.save(photo_path)
            cmd.execute("UPDATE user SET first_name=%s, last_name=%s, contact=%s, photo=%s WHERE username=%s", (fn, ln, cont, photo_filename, username))
        else:
            cmd.execute("UPDATE user SET first_name=%s, last_name=%s, contact=%s WHERE username=%s", (fn, ln, cont, username))
        con.commit()
        flash("Profile updated successfully!", "success")
        return redirect(url_for('user_dashboard'))
    else:
        cmd.execute("SELECT * FROM user WHERE username=%s", (username,))
        user = cmd.fetchone()
        return render_template("edit_profile.html", user=user)

@app.route('/reset_password', methods=['POST'])
def reset_password():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            phone = request.form.get('phone')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            if not all([username, phone, new_password, confirm_password]):
                flash("All fields are required", "error")
                return redirect(url_for('main'))
            
            if new_password != confirm_password:
                flash("Passwords do not match", "error")
                return redirect(url_for('main'))
            
            con = get_db_connection()
            cmd = con.cursor()
            
            # Verify username and phone number
            cmd.execute("SELECT * FROM user WHERE username=%s AND contact=%s", (username, phone))
            user = cmd.fetchone()
            
            if not user:
                flash("Invalid username or phone number", "error")
                return redirect(url_for('main'))
            
            # Update password
            cmd.execute("UPDATE login SET password=%s WHERE username=%s", (new_password, username))
            con.commit()
            
            flash("Password reset successful! Please login with your new password.", "success")
            return redirect(url_for('main'))
            
        except Exception as e:
            print("Password reset error:", str(e))
            flash("An error occurred while resetting your password. Please try again.", "error")
            return redirect(url_for('main'))
    
    return redirect(url_for('main'))

@app.route('/admin/users')
def admin_users():
    # Only allow admin
    if 'username' not in session:
        return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    # Check if admin
    cmd.execute("SELECT type FROM login WHERE username=%s", (session['username'],))
    user_type = cmd.fetchone()
    if not user_type or user_type[0] != 'admin':
        flash('Access denied', 'error')
        return redirect(url_for('main'))
    cmd.execute("SELECT * FROM user")
    users = cmd.fetchall()
    return render_template('admin_users.html', users=users)

@app.route('/admin/user/delete/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if 'username' not in session:
        return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    # Check if admin
    cmd.execute("SELECT type FROM login WHERE username=%s", (session['username'],))
    user_type = cmd.fetchone()
    if not user_type or user_type[0] != 'admin':
        flash('Access denied', 'error')
        return redirect(url_for('main'))
    # Delete user (will cascade to feedback and todos)
    cmd.execute("DELETE FROM user WHERE id=%s", (user_id,))
    con.commit()
    flash('User deleted successfully', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/feedbacks')
def admin_feedbacks():
    if 'username' not in session:
        return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    # Check if admin
    cmd.execute("SELECT type FROM login WHERE username=%s", (session['username'],))
    user_type = cmd.fetchone()
    if not user_type or user_type[0] != 'admin':
        flash('Access denied', 'error')
        return redirect(url_for('main'))
    cmd.execute("SELECT feedback.id, user.first_name, user.last_name, feedback.message, feedback.created_at FROM feedback JOIN user ON feedback.user_id = user.id ORDER BY feedback.id DESC")
    feedbacks = cmd.fetchall()
    return render_template('admin_feedbacks.html', feedbacks=feedbacks)
@app.route('/admin/dashboard')
def admin_dashboard():
    return render_template('admin_dashboard.html')



@app.route('/admin/syllabus')
def admin_syllabus():
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT * FROM courses ORDER BY id DESC")
    courses = cmd.fetchall()
    return render_template('admin_syllabus.html', courses=courses)

@app.route('/admin/course/add', methods=['POST'])
def admin_add_course():
    if 'username' not in session: return redirect(url_for('main'))
    name = request.form.get('name')
    desc = request.form.get('description')
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("INSERT INTO courses (name, description) VALUES (%s, %s)", (name, desc))
    con.commit()
    flash("Course added successfully", "success")
    return redirect(url_for('admin_syllabus'))

@app.route('/admin/course/delete/<int:course_id>', methods=['POST'])
def admin_delete_course(course_id):
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("DELETE FROM courses WHERE id=%s", (course_id,))
    con.commit()
    flash("Course deleted successfully", "success")
    return redirect(url_for('admin_syllabus'))

@app.route('/forgot_password')
def forgot_password():
    return render_template("forgot_password.html")

# --- Semester Management ---

@app.route('/admin/course/<int:course_id>/semesters')
def admin_semesters(course_id):
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT * FROM courses WHERE id=%s", (course_id,))
    course = cmd.fetchone()
    if not course:
        flash("Course not found", "error")
        return redirect(url_for('admin_syllabus'))
        
    cmd.execute("SELECT * FROM semesters WHERE course_id=%s ORDER BY id DESC", (course_id,))
    semesters = cmd.fetchall()
    return render_template('admin_semesters.html', course=course, semesters=semesters)

@app.route('/admin/semester/add', methods=['POST'])
def admin_add_semester():
    if 'username' not in session: return redirect(url_for('main'))
    course_id = request.form.get('course_id')
    name = request.form.get('name')
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("INSERT INTO semesters (course_id, name) VALUES (%s, %s)", (course_id, name))
    con.commit()
    flash("Semester added successfully", "success")
    return redirect(url_for('admin_semesters', course_id=course_id))

@app.route('/admin/semester/delete/<int:semester_id>', methods=['POST'])
def admin_delete_semester(semester_id):
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    # Get course_id to redirect back
    cmd.execute("SELECT course_id FROM semesters WHERE id=%s", (semester_id,))
    res = cmd.fetchone()
    if res:
        course_id = res[0]
        cmd.execute("DELETE FROM semesters WHERE id=%s", (semester_id,))
        con.commit()
        flash("Semester deleted successfully", "success")
        return redirect(url_for('admin_semesters', course_id=course_id))
    return redirect(url_for('admin_syllabus'))

# --- Subject Management ---

@app.route('/admin/semester/<int:semester_id>/subjects')
def admin_subjects(semester_id):
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT * FROM semesters WHERE id=%s", (semester_id,))
    semester = cmd.fetchone()
    
    # Need course info for breadcrumbs usually, but let's stick to basics
    if semester:
        cmd.execute("SELECT * FROM courses WHERE id=%s", (semester[1],))
        course = cmd.fetchone()
    else:
        course = None

    cmd.execute("SELECT * FROM subjects WHERE semester_id=%s ORDER BY id DESC", (semester_id,))
    subjects = cmd.fetchall()
    return render_template('admin_subjects.html', semester=semester, course=course, subjects=subjects)

@app.route('/admin/subject/add', methods=['POST'])
def admin_add_subject():
    if 'username' not in session: return redirect(url_for('main'))
    semester_id = request.form.get('semester_id')
    name = request.form.get('name')
    code = request.form.get('code')
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("INSERT INTO subjects (semester_id, name, code) VALUES (%s, %s, %s)", (semester_id, name, code))
    con.commit()
    flash("Subject added successfully", "success")
    return redirect(url_for('admin_subjects', semester_id=semester_id))

@app.route('/admin/subject/delete/<int:subject_id>', methods=['POST'])
def admin_delete_subject(subject_id):
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT semester_id FROM subjects WHERE id=%s", (subject_id,))
    res = cmd.fetchone()
    if res:
        semester_id = res[0]
        cmd.execute("DELETE FROM subjects WHERE id=%s", (subject_id,))
        con.commit()
        flash("Subject deleted successfully", "success")
        return redirect(url_for('admin_subjects', semester_id=semester_id))
    return redirect(url_for('admin_syllabus'))

# --- Unit Management ---

@app.route('/admin/subject/<int:subject_id>/units')
def admin_units(subject_id):
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT * FROM subjects WHERE id=%s", (subject_id,))
    subject = cmd.fetchone()
    
    if subject:
        cmd.execute("SELECT * FROM semesters WHERE id=%s", (subject[1],))
        semester = cmd.fetchone()
    else:
        semester = None

    cmd.execute("SELECT * FROM units WHERE subject_id=%s ORDER BY id DESC", (subject_id,))
    units = cmd.fetchall()
    return render_template('admin_units.html', subject=subject, semester=semester, units=units)

@app.route('/admin/unit/add', methods=['POST'])
def admin_add_unit():
    if 'username' not in session: return redirect(url_for('main'))
    subject_id = request.form.get('subject_id')
    name = request.form.get('name')
    description = request.form.get('description')
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("INSERT INTO units (subject_id, name, description) VALUES (%s, %s, %s)", (subject_id, name, description))
    con.commit()
    flash("Unit added successfully", "success")
    return redirect(url_for('admin_units', subject_id=subject_id))

@app.route('/admin/unit/delete/<int:unit_id>', methods=['POST'])
def admin_delete_unit(unit_id):
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT subject_id FROM units WHERE id=%s", (unit_id,))
    res = cmd.fetchone()
    if res:
        subject_id = res[0]
        cmd.execute("DELETE FROM units WHERE id=%s", (unit_id,))
        con.commit()
        flash("Unit deleted successfully", "success")
        return redirect(url_for('admin_units', subject_id=subject_id))
    return redirect(url_for('admin_syllabus'))

# --- Material Management ---

@app.route('/admin/unit/<int:unit_id>/materials', methods=['GET', 'POST'])
def admin_materials(unit_id):
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    
    if request.method == 'POST':
        title = request.form.get('title')
        pdf = request.files.get('pdf')
        if pdf and allowed_file(pdf.filename):
            filename = secure_filename(pdf.filename)

            # Create unique filename to avoid overwrite?
            # For now simple
            save_path = os.path.join(app.root_path, 'static', 'materials')
            os.makedirs(save_path, exist_ok=True)
            pdf.save(os.path.join(save_path, filename))
            
            # DB store relative path
            db_path = f"materials/{filename}"

            
            cmd.execute("INSERT INTO materials (unit_id, title, filepath, type) VALUES (%s, %s, %s, 'pdf')", 
                        (unit_id, title, db_path))
            con.commit()
            
            # Index PDF in background using its ID
            material_id = cmd.lastrowid
            abs_pdf_path = os.path.join(app.root_path, 'static', db_path)
            storage_path = os.path.join(FAISS_BASE_PATH, "materials", str(material_id))
            
            def background_index():
                index_pdf(abs_pdf_path, storage_path)
                
            threading.Thread(target=background_index, daemon=True).start()

            flash("Material uploaded and indexing started", "success")
        else:
            flash("Invalid file or no file selected", "error")
        return redirect(url_for('admin_materials', unit_id=unit_id))

    cmd.execute("SELECT * FROM units WHERE id=%s", (unit_id,))
    unit = cmd.fetchone()
    
    cmd.execute("SELECT * FROM materials WHERE unit_id=%s", (unit_id,))
    materials = cmd.fetchall()
    
    return render_template('admin_materials.html', unit=unit, materials=materials)

@app.route('/admin/material/delete/<int:material_id>', methods=['POST'])
def admin_delete_material(material_id):
    if 'username' not in session: return redirect(url_for('main'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT unit_id, filepath FROM materials WHERE id=%s", (material_id,))
    res = cmd.fetchone()
    if res:
        unit_id = res[0]
        filepath = res[1]
        
        # Delete file from system
        try:
            full_path = os.path.join(app.root_path, 'static', filepath)
            if os.path.exists(full_path):
                os.remove(full_path)
            
            # Delete index
            m_index_path = os.path.join(FAISS_BASE_PATH, "materials", str(material_id))
            import shutil
            if os.path.exists(m_index_path):
                shutil.rmtree(m_index_path)
        except Exception as e:
            print(f"Error deleting file or index: {e}")

        cmd.execute("DELETE FROM materials WHERE id=%s", (material_id,))
        con.commit()
        flash("Material deleted successfully", "success")
        return redirect(url_for('admin_materials', unit_id=unit_id))
    return redirect(url_for('admin_syllabus'))



@app.route('/feedback', methods=['POST'])
def submit_feedback():
    if 'username' not in session:
        return redirect(url_for('main'))
    message = request.form.get('message')
    if not message:
        flash('Feedback message cannot be empty', 'error')
        return redirect(url_for('user_dashboard'))
    con = get_db_connection()
    cmd = con.cursor()
    cmd.execute("SELECT id FROM user WHERE username=%s", (session['username'],))
    user_id = cmd.fetchone()[0]
    cmd.execute("INSERT INTO feedback (user_id, message) VALUES (%s, %s)", (user_id, message))
    con.commit()
    flash('Thank you for your feedback!', 'success')
    return redirect(url_for('user_dashboard'))
# 🔥 Warmup
print("🔥 Warming up LLM...")
try:
    from utils import get_llm
    get_llm().invoke("hello")
    print("✅ LLM Ready")
except Exception as e:
    print("LLM warmup failed:", e)

if __name__ == '__main__':
    app.run(debug=True)
