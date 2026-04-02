import os
import fitz

from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import SentenceTransformerEmbeddings
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv

load_dotenv()

FAISS_BASE_PATH = "faiss_index"

_embeddings_model = None
_llm_instance = None
_vector_stores = {}
_merged_stores = {}


# ---------------- EMBEDDINGS ----------------
def get_embeddings():
    global _embeddings_model
    if _embeddings_model is None:
        print("Initializing embeddings...")
        _embeddings_model = SentenceTransformerEmbeddings(
            model_name="all-MiniLM-L6-v2"
        )
    return _embeddings_model


# ---------------- LLM ----------------
def get_llm():
    global _llm_instance
    if _llm_instance is None:
        print("Initializing Groq model...")
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not found. Please set it in a .env file.")
            
        _llm_instance = ChatGroq(
            groq_api_key=api_key,
            model_name="llama-3.1-8b-instant",
            temperature=0,      # fast + deterministic
            max_tokens=250      # Limit generation length to stay extremely snappy
        )
    return _llm_instance


# ---------------- PDF TEXT ----------------
def get_pdf_text(pdf_docs):
    text = ""
    for pdf in pdf_docs:
        try:
            doc = fitz.open(pdf)
            for page in doc:
                text += page.get_text()
            doc.close()
        except Exception as e:
            print("PDF error:", e)
    return text


# ---------------- CHUNKING ----------------
def get_text_chunks(text):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=200,
        chunk_overlap=100
    )
    return splitter.split_text(text)


# ---------------- INDEX ----------------
def index_pdf(pdf_path, storage_path):
    print(f"Indexing: {pdf_path}")

    if os.path.exists(storage_path):
        print("Already indexed")
        return

    text = get_pdf_text([pdf_path])
    if not text:
        return

    chunks = get_text_chunks(text)
    embeddings = get_embeddings()

    from langchain_core.documents import Document
    docs = [Document(page_content=c) for c in chunks]

    db = FAISS.from_documents(docs, embeddings)

    os.makedirs(storage_path, exist_ok=True)
    db.save_local(storage_path)


# ---------------- LOAD + MERGE ----------------
def load_and_merge_indices(index_paths):
    if not index_paths:
        return None

    key = frozenset(index_paths)

    global _merged_stores, _vector_stores

    if key in _merged_stores:
        return _merged_stores[key]

    embeddings = get_embeddings()
    main_db = None

    for path in index_paths:
        if not os.path.exists(os.path.join(path, "index.faiss")):
            continue

        try:
            if path in _vector_stores:
                db = _vector_stores[path]
            else:
                db = FAISS.load_local(path, embeddings, allow_dangerous_deserialization=True)
                _vector_stores[path] = db

            if main_db is None:
                main_db = db
            else:
                main_db.merge_from(db)

        except Exception as e:
            print("Load error:", e)

    if main_db:
        _merged_stores[key] = main_db

    return main_db


# ---------------- PROMPT ----------------
def get_qa_chain():
    prompt = PromptTemplate(
        template="""
You are an intelligent AI tutor for students. Your job is to answer questions strictly using the provided PDF Context.

CRITICAL INSTRUCTIONS:
1. When the student types a broad command like "explain", "explain the pdf", "summarize", or "what is this", you MUST provide a detailed structural summary of the Context provided. Absolutely DO NOT say "out of syllabus" for these commands. This is your most important rule.
2. Logical Inference: Use logical reasoning to stitch together pieces from the Context to construct an accurate, educational answer.
3. In-Syllabus Only: You can explain and expand on concepts found in the Context to make them easier to understand, but do NOT introduce entirely new facts or topics that are not in the Context.
4. Out-of-Syllabus: Only if the student asks a specific factual question about a target topic that is completely absent from the Context, you must respectfully decline by saying exactly: "This topic is out of syllabus (not covered in the provided PDF)."

Context from the PDF:
{context}

Student's Question:
{question}

Your Tutor Answer:
""",
        input_variables=["context", "question"]
    )

    return prompt | get_llm() | StrOutputParser()


# ---------------- STREAM ----------------
def user_input_stream(question, index_paths):
    print("Question:", question)

    db = load_and_merge_indices(index_paths)

    if not db:
        yield "❌ No PDFs ready"
        return

    # 🔥 Better logic for broad/general summaries vs specific queries
    search_query = question
    lower_q = question.strip().lower()
    is_broad = (
        len(lower_q.split()) <= 3 or 
        any(w in lower_q for w in ["explain", "summarize", "summary", "overview", "list", "content", "describe", "what is this", "tell me about"])
    )
    
    if is_broad:
        search_query = lower_q + " main concepts major topics outline introduction summary"
        db_results = db.similarity_search_with_score(search_query, k=8)
    else:
        db_results = db.similarity_search_with_score(search_query, k=5)
        
    filtered_docs = [doc for doc, _ in db_results]

    if not filtered_docs:
        yield "I couldn't find any relevant sections in the PDF for this."
        return

    context = "\n\n".join([d.page_content for d in filtered_docs])
    print("\n--- RETRIEVED CONTEXT ---")
    print(context[:300] + "...")
    print("-------------------------")

    # 🔥 context validation
    if len(context.strip()) < 30:
        print("FAIL: Context length < 30 characters.")
        yield "I couldn't find enough information about this in the uploaded documents."
        return

    chain = get_qa_chain()

    response_text = ""

    try:
        for chunk in chain.stream({
            "context": context,
            "question": question
        }):
            response_text += chunk
            yield chunk

        # 🔥 anti prompt-leak + fallback
        if (
            not response_text.strip()
            or "learning assistant" in response_text.lower()
        ):
            yield "\nI couldn't find a direct answer to that in the documents."

    except Exception as e:
        print("LLM error:", e)
        yield "⚠️ Error"


# ---------------- NON-STREAM ----------------
def user_input(question, subject_id_or_paths):
    if isinstance(subject_id_or_paths, list):
        index_paths = subject_id_or_paths
    else:
        index_paths = [os.path.join(FAISS_BASE_PATH, f"subject_{subject_id_or_paths}")]

    response = ""
    for chunk in user_input_stream(question, index_paths):
        response += chunk
    return response