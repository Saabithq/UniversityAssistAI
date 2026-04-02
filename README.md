# University Assist LMS

A comprehensive Learning Management System built with Flask and Python, featuring AI-powered document interactions. Students can upload their course materials (PDFs) and interact directly with them using intelligent semantic search and AI chat features.

## Key Features

- **AI-Powered Chat Assistant**: Ask questions based directly on course materials to get precise, syllabus-aligned answers. Powered by LangChain and Groq LLMs.
- **Semantic Search**: Instant contextual search across uploaded PDFs via FAISS vector indexing and sentence embeddings.
- **Dynamic Syllabus Management**: Organized access to units and subjects, efficiently tracking educational progress.
- **Clean Interface**: Responsive web interface crafted using modern HTML, CSS, and JS components.

## Tech Stack

- **Backend**: Python, Flask
- **Database**: MySQL (PyMySQL)
- **AI & Vector DB**: LangChain, FAISS CPU, Sentence Transformers, Groq API
- **Document Processing**: PyMuPDF
- **Frontend**: HTML5, CSS3, JavaScript

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone <your-github-repo-url>
   cd University_Assist_LMS
   ```

2. **Create a virtual environment (optional but recommended):**
   ```bash
   python -m venv venv
   source venv/Scripts/activate  # on Windows
   ```

3. **Install the dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Configuration:**
   - Copy `.env.example` to `.env`
   - Fill in your `GROQ_API_KEY` and any MySQL connection credentials inside the `.env` file.

5. **Database Setup:**
   Ensure MySQL is running on your local machine and create the necessary database schema using the provided SQL files (`create_hierarchy.sql`, `pcb_db_setup.sql`) or initialization script (`src/init_db.py`).

6. **Run the Application:**
   ```bash
   python src/app.py
   ```
   The interactive web service should now be live on `http://127.0.0.1:5000/`.

## Important Note

This repository does not commit any sensitive keys or local database vector clusters. Ensure `.env` and local caches (`faiss_index`, `__pycache__`) remain in your local workspace.
