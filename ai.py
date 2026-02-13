# app.py
import os
import json
import time
import re
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
import streamlit as st
import PyPDF2
import docx
from groq import Groq

# -----------------------------------------------------------------------
# LOAD ENV & INIT CLIENT
# -----------------------------------------------------------------------
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

# -----------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------
st.set_page_config(page_title="Chatcruit — Interview Practice",
                   layout="wide", page_icon="💼")

HISTORY_FILE = "saved_chats.json"

# -----------------------------------------------------------------------
# SESSION STATE INIT
# -----------------------------------------------------------------------
if "initialized" not in st.session_state:
    st.session_state.update({
        "chat": [],
        "current_question": "",
        "asked_questions": [],
        "resume_text": "",
        "answered_questions": set(),
        "qb_mode": False,
        "qb_category": "HR",
        "qb_page": 0,
        "round_stage": "upload",
        "hr_index": 0,
        "resume_tech_index": 0,
        "hr_answers": [],
        "tech_answers": [],
        "mock_mode": False,
        "mode_selection": "HR Questions",
        "selected_company": "Wipro",
        "difficulty": "Easy",
        "dark_mode": False,
        "uploaded_resume": None,
        "resume_data": "",
        "intro_answer": "",
        "hr_dynamic_questions": [],
        "resume_tech_questions": [],
        "overall_rating": None,
        "ats_result": None,
        "initialized": True
    })

# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------
def extract_pdf_text(uploaded_file):
    try:
        reader = PyPDF2.PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text.strip()
    except Exception as e:
        return f"[PDF read error] {str(e)}"

def extract_docx_text(uploaded_file):
    try:
        doc = docx.Document(uploaded_file)
        return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    except Exception as e:
        return f"[DOCX error] {str(e)}"

def extract_text_from_file(uploaded_file):
    if not uploaded_file:
        return ""
    name = uploaded_file.name.lower()
    if name.endswith(".pdf"):
        return extract_pdf_text(uploaded_file)
    elif name.endswith(".docx"):
        return extract_docx_text(uploaded_file)
    elif name.endswith(".txt"):
        return uploaded_file.read().decode("utf-8", errors="ignore")
    return ""

# -----------------------------------------------------------------------
# GROQ CHAT CALL (SAFE WRAPPER)
# -----------------------------------------------------------------------
def safe_chat_call(prompt, model="llama-3.1-8b-instant", temperature=0.2, max_tokens=1000, retries=3):
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            if attempt == retries - 1:
                return f"[Error: {str(e)}]"
            time.sleep(1)

# -----------------------------------------------------------------------
# HISTORY
# -----------------------------------------------------------------------
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_chat_to_history(name, items):
    data = load_history()
    data.append({
        "id": len(data) + 1,
        "name": name,
        "timestamp": datetime.now().isoformat(),
        "chat": items
    })
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return True

# -----------------------------------------------------------------------
# DATABASE FUNCTIONS
# -----------------------------------------------------------------------
def get_db():
    return sqlite3.connect("questions.db", check_same_thread=False)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            difficulty TEXT,
            question TEXT,
            sample_answer TEXT
        )
    """)
    conn.commit()
    conn.close()
init_db()

def get_all_questions(category):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT question, sample_answer
        FROM questions
        WHERE category=?
    """, (category,))
    rows = cur.fetchall()
    conn.close()
    return rows


# -----------------------------------------------------------------------
# QUESTION GENERATION
# -----------------------------------------------------------------------
def generate_unique_question(prompt, category="HR"):
    prev = "\n".join(st.session_state.asked_questions[-5:])
    full_prompt = f"{prompt}\nAvoid repeating these: {prev}"
    q = safe_chat_call(full_prompt)
    if not q or len(q) < 5:
        db = get_all_questions(category)
        q = db[0] if db else "Tell me about yourself"
    if q not in st.session_state.asked_questions:
        st.session_state.asked_questions.append(q)
    return q

def generate_question_with_context(mode, difficulty="Easy", context=""):
    prompts = {
        "HR": f"Generate a {difficulty.lower()} HR interview question. {context}",
        "Technical": f"Generate a {difficulty.lower()} technical interview question. {context}",
        "Company": f"Generate a {difficulty.lower()} interview question for {context} company",
        "Resume": f"Generate a {difficulty.lower()} interview question based on this resume content: {context[:1000]}"
    }
    category = mode.split()[0] if " " in mode else mode
    prompt = prompts.get(category, prompts["HR"])
    return generate_unique_question(prompt, category)

# -----------------------------------------------------------------------
# ATS ANALYSIS
# -----------------------------------------------------------------------
ATS_PROMPT = """
You are an ATS resume analyzer. Return ONLY JSON:
- ats_score (0-100)
- issues (list)
- suggestions (list)
- skills (list)
- one_line_summary (string)

Resume:
\"\"\"{text}\"\"\"
"""

def analyze_resume_ats(resume_text):
    out = safe_chat_call(ATS_PROMPT.format(text=resume_text[:3000]))
    try:
        start = out.find("{")
        end = out.rfind("}")
        if start != -1 and end != -1:
            return json.loads(out[start:end+1])
    except:
        pass
    return {"ats_score": 0, "issues": ["Parsing error"], "suggestions": ["Analysis failed"], "skills": [], "one_line_summary": "N/A"}

# -----------------------------------------------------------------------
# FEEDBACK GENERATION
# -----------------------------------------------------------------------
def generate_feedback(question, answer, q_type):
    prompt = f"""
    As an experienced interviewer, provide constructive feedback on this answer:
    Question: {question}
    Candidate's Answer: {answer}
    Return JSON: rating(1-10), strengths, improvements, sample_answer
    """
    response = safe_chat_call(prompt)
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    return {"rating": "N/A", "strengths": "N/A", "improvements": "N/A", "sample_answer": ""}

def export_chat():
    """Export chat to various formats"""
    if not st.session_state.chat:
        return None
    
    chat_text = "=" * 50 + "\n"
    chat_text += "Chatcruit Interview Practice Log\n"
    chat_text += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    chat_text += "=" * 50 + "\n\n"
    
    for role, text in st.session_state.chat:
        chat_text += f"{role}:\n{text}\n\n" + "-" * 40 + "\n"
    
    return chat_text

def show_progress_bar():
    """Show progress bar for mock interview"""
    stages = {
        "upload": ("📄 Upload Resume", 0),
        "intro": ("👋 Introduction", 20),
        "hr_follow": ("💬 HR Questions", 40),
        "resume_hr": ("📋 Resume HR", 60),
        "resume_tech": ("⚙️ Technical", 80),
        "completed": ("✅ Completed", 100)
    }
    
    current_stage = st.session_state.get("round_stage", "upload")
    stage_name, progress = stages.get(current_stage, ("Unknown", 0))
    
    st.progress(progress/100)
    st.caption(f"**Stage:** {stage_name} ({progress}%)")

# -----------------------------------------------------------------------
# STYLING
# -----------------------------------------------------------------------
def apply_styles(dark_mode):
    if dark_mode:
        # Premium Dark Theme
        bg = "#0f172a"
        text = "#ffffff"
        chat_left = "#1e293b"
        chat_center = "#451a03"
        chat_right = "#052e16"
        card = "#1e293b"
        accent_color = "#818cf8"
        sidebar_bg = "#1e293b"
        sidebar_text = "#e2e8f0"
        border_color = "#334155"
        hover_color = "#2d3748"
        shadow_color = "rgba(0, 0, 0, 0.3)"
        gradient_start = "#0f172a"
        gradient_end = "#1e293b"
    else:
        # Premium SaaS Light Theme
        bg = "#dacafd"
        text = "#2e2e48"
        chat_left = "#f8f9ff"
        chat_center = "#fefce8"
        chat_right = "#f0fdf9"
        card = "#ffffff"
        accent_color = "#6366f1"
        sidebar_bg = "#f5f7ff"
        sidebar_text = "#2e2e48"
        border_color = "#e2e8f0"
        hover_color = "#f1f5f9"
        shadow_color = "rgba(99, 102, 241, 0.1)"
        gradient_start = "#f5f7ff"
        gradient_end = "#ffffff"
    
    return f"""
    <style>
    /* Premium SaaS Theme - Clean & Professional */
    [data-testid="stAppViewContainer"] {{
        background: {bg};
        color: {text};
        font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
        line-height: 1.5;
    }}
    
    /* ============= FIX: VISIBLE TEXT IN INPUT BOX ============= */
    /* CRITICAL FIX: Make ALL input text VISIBLE */
    textarea, 
    .stTextArea textarea,
    .stTextInput input,
    input[type="text"],
    input[type="textarea"] {{
        color: {text} !important;
        background-color: {card if dark_mode else "white"} !important;
        caret-color: {text} !important; /* Cursor color */
    }}
    
    /* When typing */
    textarea:focus,
    .stTextArea textarea:focus,
    .stTextInput input:focus,
    input[type="text"]:focus,
    input[type="textarea"]:focus {{
        color: {text} !important;
        background-color: {card if dark_mode else "white"} !important;
    }}
    
    /* Already typed text */
    textarea:not(:placeholder-shown),
    .stTextArea textarea:not(:placeholder-shown) {{
        color: {text} !important;
    }}
    
    /* Placeholder text - visible but lighter */
    textarea::placeholder,
    .stTextArea textarea::placeholder,
    .stTextInput input::placeholder {{
        color: {sidebar_text} !important;
        opacity: 0.7 !important;
    }}
    
    /* Direct child selectors for maximum specificity */
    div.stTextArea > div > div > textarea,
    div[data-testid="stVerticalBlock"] div.stTextArea textarea,
    div[data-baseweb="textarea"] textarea {{
        color: {text} !important;
    }}
    
    /* Text color for all other elements */
    body, p, span, div, label, li, td, th {{
        color: {text};
    }}
    
    /* ============= END FIX ============= */
    
    /* Sidebar with subtle gradient */
    [data-testid="stSidebar"] {{
        background: linear-gradient(180deg, {gradient_start} 0%, {gradient_end} 100%);
        border-right: 1px solid rgba(99, 102, 241, 0.08);
        padding: 2rem 1.5rem;
        box-shadow: 2px 0 12px {shadow_color};
    }}
    
    /* Sidebar headings with accent line */
    [data-testid="stSidebar"] h3 {{
        color: {accent_color} !important;
        font-weight: 600;
        font-size: 1.125rem;
        margin-top: 2rem;
        margin-bottom: 1rem;
        padding-bottom: 0.75rem;
        border-bottom: 2px solid {accent_color}20;
        letter-spacing: -0.01em;
    }}
    
    /* Cards with premium shadow */
    .card, .panel-feedback {{
        background: {card};
        border: 1px solid {border_color};
        border-radius: 16px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 
                    0 2px 6px rgba(99, 102, 241, 0.05);
        padding: 1.5rem;
        margin: 1rem 0;
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    }}
    
    .card:hover, .panel-feedback:hover {{
        box-shadow: 0 4px 12px rgba(99, 102, 241, 0.08), 
                    0 6px 20px rgba(99, 102, 241, 0.05);
        transform: translateY(-2px);
        border-color: {accent_color}30;
    }}
    
    /* Premium Buttons */
    .stButton>button {{
        background: linear-gradient(135deg, {accent_color} 0%, #4f46e5 100%);
        color: white !important;
        border: none;
        padding: 0.75rem 1.5rem;
        border-radius: 10px;
        font-weight: 500;
        font-size: 0.875rem;
        letter-spacing: 0.01em;
        transition: all 0.2s;
        box-shadow: 0 2px 4px {shadow_color};
        position: relative;
        overflow: hidden;
    }}
    
    .stButton>button:hover {{
        transform: translateY(-1px);
        box-shadow: 0 4px 12px {shadow_color};
        background: linear-gradient(135deg, #4f46e5 0%, #4338ca 100%);
    }}
    
    .stButton>button:active {{
        transform: translateY(0);
    }}
    
    /* Input Fields - Premium */
    .stTextInput>div>input, .stTextArea>div>textarea {{
        background: {card if dark_mode else "white"};
        border: 1px solid {border_color};
        border-radius: 10px;
        padding: 0.875rem 1rem;
        font-size: 0.9375rem;
        transition: all 0.2s;
        color: {text} !important; /* Force text color */
    }}
    
    .stTextInput>div>input:focus, .stTextArea>div>textarea:focus {{
        border-color: {accent_color};
        box-shadow: 0 0 0 3px {accent_color}15;
        outline: none;
    }}
    
    /* Premium Checkboxes & Radio */
    .stCheckbox>div>div>div, .stRadio>div>div>div {{
        border: 1px solid {border_color};
        border-radius: 8px;
        padding: 0.75rem;
        margin: 0.375rem 0;
        transition: all 0.2s;
    }}
    
    .stCheckbox>div>div>div:hover, .stRadio>div>div>div:hover {{
        background: {hover_color};
        border-color: {accent_color}40;
        transform: translateY(-1px);
    }}
    
    /* Premium Typography */
    h1 {{
        color: {text} !important;
        font-weight: 700;
        font-size: 2.5rem;
        margin-bottom: 0.5rem;
        letter-spacing: -0.02em;
        background: linear-gradient(135deg, {accent_color} 0%, #8b5cf6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }}
    
    h2 {{
        color: {text} !important;
        font-weight: 600;
        font-size: 1.875rem;
        margin: 1.5rem 0 1rem 0;
        letter-spacing: -0.01em;
    }}
    
    h3 {{
        color: {text} !important;
        font-weight: 600;
        font-size: 1.5rem;
        margin: 1.25rem 0 0.75rem 0;
    }}
    
    /* Premium Scrollbar */
    ::-webkit-scrollbar {{
        width: 8px;
        height: 8px;
    }}
    
    ::-webkit-scrollbar-track {{
        background: {border_color};
        border-radius: 4px;
    }}
    
    ::-webkit-scrollbar-thumb {{
        background: linear-gradient(135deg, {accent_color} 0%, #8b5cf6 100%);
        border-radius: 4px;
    }}
    
    ::-webkit-scrollbar-thumb:hover {{
        background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
    }}
    
    /* Animation for chat */
    @keyframes fadeIn {{
        from {{ opacity: 0; transform: translateY(10px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}
    
    .chat-left, .chat-right {{
        animation: fadeIn 0.3s ease-out;
    }}
    
    /* Responsive adjustments */
    @media (max-width: 768px) {{
        [data-testid="stSidebar"] {{
            padding: 1.5rem 1rem;
        }}
        
        .chat-left, .chat-right {{
            max-width: 90%;
        }}
        
        h1 {{
            font-size: 2rem;
        }}
    }}
    </style>
    """
# -----------------------------------------------------------------------
# HEADER
# -----------------------------------------------------------------------
colA, colB = st.columns([4, 1])
with colA:
    st.markdown("<h1 style='color:#0b4a8f'>⚡ Chatcruit</h1>", unsafe_allow_html=True)
    st.markdown("<div style='color:#4b5563; font-size: 18px;'>Chat. Prepare. Get Hired.</div>", unsafe_allow_html=True)

with colB:
    st.session_state.dark_mode = st.checkbox("🌙 Dark Mode", value=st.session_state.dark_mode)

# Apply styles
st.markdown(apply_styles(st.session_state.dark_mode), unsafe_allow_html=True)

# -----------------------------------------------------------------------
# LAYOUT
# -----------------------------------------------------------------------
left, main, right = st.columns([1, 2, 1.2])

# -----------------------------------------------------------------------
# LEFT SIDEBAR
# -----------------------------------------------------------------------
with left:
    st.markdown("### 🎤 Mock Interview")
    mock_enabled = st.checkbox(
        "Enable Mock Interview Mode",
        value=st.session_state.get("mock_mode", False),
        key="mock_checkbox"
    )
    st.session_state.mock_mode = mock_enabled
    
    if mock_enabled and st.session_state.get("round_stage") != "upload":
        show_progress_bar()
    
    st.markdown("---")
    
    # Practice Mode Options
    if not mock_enabled:
        st.markdown("### 📁 Question Bank Mode")
        st.session_state.qb_mode = st.checkbox(
            "Enable Question Bank",
            value=st.session_state.qb_mode,
            key="qb_checkbox"
        )
        
        if st.session_state.qb_mode:
            st.session_state.qb_category = st.radio(
                "Select Category",
                ["HR", "Tech", "Coding"],
                key="qb_category_radio"
            )
            st.info("📚 Question Bank Mode is active")
        
        st.markdown("---")
        st.markdown("### 🎯 Practice Modes")
        
        mode = st.radio(
            "Select Mode:",
            ["HR Questions", "Technical Prep", "Resume Based", "Company Based", "History"],
            key="mode_radio"
        )
        st.session_state.mode_selection = mode
        
        difficulty = st.selectbox(
            "Select Difficulty",
            ["Easy", "Medium", "Hard"],
            key="difficulty_select"
        )
        st.session_state.difficulty = difficulty
        
        if mode == "Company Based":
            companies = [
                "Wipro", "TCS", "Infosys", "HCL", "Accenture", "Cognizant",
                "Google", "Amazon", "Microsoft", "Meta", "Apple", "Netflix",
                "Adobe", "Tesla", "Oracle", "Salesforce", "IBM", "LinkedIn"
            ]
            company_name = st.selectbox(
                "Select Company",
                companies,
                index=companies.index(st.session_state.selected_company) if st.session_state.selected_company in companies else 0,
                key="company_select"
            )
            st.session_state.selected_company = company_name
    
    st.markdown("---")
    st.markdown("### 🗂 Chat History")
    
    hist = load_history()
    if hist:
        for item in reversed(hist[-8:]):
            if st.button(
                f"📝 {item['name'][:30]}",
                key=f"load_{item['id']}",
                use_container_width=True
            ):
                st.session_state.chat = [(m["role"], m["text"]) for m in item["chat"]]
                st.rerun()
    else:
        st.caption("No saved chats yet")
    
    st.markdown("---")
    
    # Save and Export
    col_save, col_export = st.columns(2)
    with col_save:
        if st.button("💾 Save Chat", use_container_width=True):
            if st.session_state.chat:
                if save_chat_to_history(
                    f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    [{"role": r, "text": t} for r, t in st.session_state.chat]
                ):
                    st.success("Chat saved successfully!")
                else:
                    st.error("Failed to save chat")
            else:
                st.warning("No chat to save")
    
    with col_export:
        if st.session_state.chat:
            chat_text = export_chat()
            if chat_text:
                st.download_button(
                    "📤 Export Chat",
                    data=chat_text,
                    file_name=f"chatcruit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
    
    st.markdown("---")
    
    if st.button("🔄 Reset Session", type="secondary", use_container_width=True):
        keys_to_keep = ["dark_mode", "initialized"]
        for key in list(st.session_state.keys()):
            if key not in keys_to_keep:
                del st.session_state[key]
        st.rerun()

# -----------------------------------------------------------------------
# QUESTION BANK NAVIGATION
# -----------------------------------------------------------------------
def show_question_bank():
    cat = st.session_state.qb_category

    file_map = {
        "HR": "data/questions_hr.json",
        "Tech": "data/questions_technical.json",
        "Coding": "data/questions_coding.json"
    }

    file_name = file_map.get(cat)

    if not file_name or not os.path.exists(file_name):
        st.info(f"No questions found for {cat} category")
        return

    # Load JSON
    with open(file_name, "r", encoding="utf-8") as f:
        questions = json.load(f)   # directly list

    total = len(questions)
    per_page = 10
    page = st.session_state.qb_page
    start = page * per_page
    end = start + per_page

    if not questions:
        st.info(f"No questions found for {cat} category")
        return

    for idx, item in enumerate(questions[start:end], start=start + 1):
        question = item.get("question", "")
        answer = item.get("sample_answer", "")

        with st.expander(f"Q{idx}: {question[:50]}..."):
            st.markdown(f"**Question:** {question}")
            st.markdown(f"**Answer:** {answer or 'No answer provided'}")

    # Pagination
    if total > per_page:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col1:
            if st.button("⬅️ Previous") and page > 0:
                st.session_state.qb_page -= 1
                st.rerun()
        with col2:
            st.write(f"Page {page + 1}/{(total - 1)//per_page + 1}")
        with col3:
            if st.button("Next ➡️") and end < total:
                st.session_state.qb_page += 1
                st.rerun()

# -----------------------------------------------------------------------
# MAIN PANEL
# -----------------------------------------------------------------------
with main:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    
    # MOCK INTERVIEW MODE
    if st.session_state.get("mock_mode", False):
        st.markdown("### 🎤 Mock Interview Mode")

    # ---------------- STEP 0: Upload Resume ----------------
        if st.session_state.round_stage == "upload":
            uploaded = st.file_uploader("Upload your resume (PDF, DOCX, TXT)")
            if uploaded:
                st.session_state.uploaded_resume = uploaded

            if st.session_state.uploaded_resume and st.button("Start Interview"):
                resume_text = extract_text_from_file(st.session_state.uploaded_resume)
                st.session_state.resume_text = resume_text
                st.session_state.resume_data = safe_chat_call(
                    f"Extract candidate skills, projects, tools, education and important info:\n\n{resume_text}\n\nReturn as clean bullet points."
                )

                # Initialize interview state
                st.session_state.chat = []
                st.session_state.intro_answer = ""
                st.session_state.hr_index = 0
                st.session_state.resume_tech_index = 0
                st.session_state.hr_dynamic_questions = []
                st.session_state.resume_tech_questions = []
                st.session_state.hr_answers = []
                st.session_state.tech_answers = []
                st.session_state.round_stage = "intro"

        # ---------------- STEP 1: Intro ----------------
        elif st.session_state.round_stage == "intro":
            with st.form("intro_form"):
                intro_answer = st.text_area("Question 1: Tell me about yourself")
                submitted = st.form_submit_button("Submit")
                if submitted and intro_answer.strip():
                    st.session_state.intro_answer = intro_answer.strip()
                    st.session_state.chat.append(("You", intro_answer.strip()))
                    
                    # ✅ REPLACE WITH THIS (Dynamic without generic questions):
                    st.session_state.hr_dynamic_questions = []
                    
                    for i in range(3):
                        # Generate dynamic follow-up questions
                        prompt = f"""
                        Candidate said: "{intro_answer[:200]}"

                        Generate ONE simple and friendly HR follow-up question.

                        Rules:
                        - Question should be easy to understand
                        - Suitable for a fresher
                        - Use simple English
                        - Ask about their skills, project, or experience
                        - Do not ask tricky or deep psychological questions
                        - Sound like a supportive interviewer
                        - Ask only one question
                        """

                        question = safe_chat_call(prompt, max_tokens=150)
                        st.session_state.hr_dynamic_questions.append(question)
                    
                    st.session_state.round_stage = "hr_follow"

        # ---------------- STEP 2: HR Follow-ups ----------------
        elif st.session_state.round_stage == "hr_follow":
            idx = st.session_state.hr_index
            if idx < len(st.session_state.hr_dynamic_questions):
                q = st.session_state.hr_dynamic_questions[idx]
                st.markdown(f"### HR Question {idx+1}: {q}")
                with st.form(f"hr_form_{idx}"):
                    ans = st.text_area("Your Answer")
                    submitted = st.form_submit_button("Submit")
                    if submitted and ans.strip():
                        st.session_state.chat.append(("You", ans.strip()))
                        st.session_state.hr_answers.append(ans.strip())
                        st.session_state.hr_index += 1
                        if st.session_state.hr_index >= len(st.session_state.hr_dynamic_questions):
                            # Generate 3 resume-based tech questions
                            st.session_state.resume_tech_questions = [
                                generate_unique_question(
                                    
                                        f"""
                                You are an AI technical interviewer .
                                ask very basic friendly questions 
                                like what is difference between class and id ? 

                                
                                Resume:
                                {st.session_state.resume_data}

                        
                                - Do not add extra text
                                """

                                ) for _ in range(3)
                            ]
                            st.session_state.round_stage = "resume_tech"
            else:
                st.session_state.round_stage = "resume_tech"

        # ---------------- STEP 3: Resume-based Technical ----------------
        elif st.session_state.round_stage == "resume_tech":
            idx = st.session_state.resume_tech_index
            if idx < len(st.session_state.resume_tech_questions):
                q = st.session_state.resume_tech_questions[idx]
                st.markdown(f"### Technical Question {idx+1}: {q}")
                with st.form(f"tech_form_{idx}"):
                    ans = st.text_area("Your Answer")
                    submitted = st.form_submit_button("Submit")
                    if submitted and ans.strip():
                        st.session_state.chat.append(("You", ans.strip()))
                        st.session_state.tech_answers.append(ans.strip())
                        st.session_state.resume_tech_index += 1
                        if st.session_state.resume_tech_index >= len(st.session_state.resume_tech_questions):
                            st.session_state.round_stage = "completion"

        # ---------------- STEP 4: Completion ----------------
        elif st.session_state.round_stage == "completion":
            st.markdown("### ✅ Interview Completed")
            st.markdown("**HR Answers:**")
            for i, a in enumerate(st.session_state.hr_answers):
                st.markdown(f"{i+1}. {a}")
            st.markdown("**Technical Answers:**")
            for i, a in enumerate(st.session_state.tech_answers):
                st.markdown(f"{i+1}. {a}")

            # Optionally generate feedback
            feedback = generate_unique_question(
                f"Provide constructive feedback and rating for this candidate based on these HR and Technical answers:\nHR: {st.session_state.hr_answers}\nTech: {st.session_state.tech_answers}",
                "Feedback"
            )
            st.markdown("### Feedback:")
            st.markdown(feedback)

    # PRACTICE MODE
    else:
        if st.session_state.qb_mode:
            st.markdown("### 📚 Question Bank")
            show_question_bank()
        else:
            mode = st.session_state.get("mode_selection", "HR Questions")
            st.markdown(f"### {mode}")
            
            if mode == "HR Questions":
                col_gen, col_clear = st.columns([3, 1])
                with col_gen:
                    if st.button("🎯 Generate HR Question", use_container_width=True):
                        q = generate_question_with_context("HR", st.session_state.difficulty)
                        st.session_state.current_question = q
                        st.session_state.chat.append(("Interviewer", q))
                
                with col_clear:
                    if st.button("🗑️ Clear", use_container_width=True):
                        st.session_state.current_question = ""
                
                if st.session_state.current_question:
                    st.markdown("#### Question:")
                    st.info(st.session_state.current_question)
            
            elif mode == "Technical Prep":
                col_gen, col_clear = st.columns([3, 1])
                with col_gen:
                    if st.button("⚙️ Generate Technical Question", use_container_width=True):
                        q = generate_question_with_context("Technical", st.session_state.difficulty)
                        st.session_state.current_question = q
                        st.session_state.chat.append(("Interviewer", q))
                
                with col_clear:
                    if st.button("🗑️ Clear", use_container_width=True):
                        st.session_state.current_question = ""
                
                if st.session_state.current_question:
                    st.markdown("#### Question:")
                    st.info(st.session_state.current_question)
                    
                    # Show sample answer from database
                    db_result = get_all_questions("Tech")
                    if db_result and db_result[1]:
                        with st.expander("💡 View Expected Answer"):
                            st.write(db_result[1])
            
            elif mode == "Resume Based":
                st.markdown("#### Upload Resume for Analysis")
                
                uploaded_file = st.file_uploader(
                    "Choose a file",
                    type=["pdf", "docx", "txt"],
                    key="resume_uploader_practice"
                )
                
                if uploaded_file:
                    st.session_state.resume_text = extract_text_from_file(uploaded_file)
                    
                    if st.session_state.resume_text:
                        with st.expander("📄 Resume Preview"):
                            st.text_area("", st.session_state.resume_text[:1000], height=200, disabled=True)
                        
                        col_ats, col_q = st.columns(2)
                        with col_ats:
                            if st.button("📊 Analyze ATS Score", use_container_width=True):
                                with st.spinner("Analyzing resume..."):
                                    st.session_state.ats_result = analyze_resume_ats(st.session_state.resume_text)
                        
                        with col_q:
                            if st.button("❓ Generate Resume Question", use_container_width=True):
                                q = generate_question_with_context(
                                    "Resume", 
                                    st.session_state.difficulty,
                                    st.session_state.resume_text[:1000]
                                )
                                st.session_state.current_question = q
                                st.session_state.chat.append(("Interviewer", q))
                        
                        # Show ATS Results
                        if "ats_result" in st.session_state:
                            ats = st.session_state.ats_result
                            st.markdown("#### 📈 ATS Analysis")
                            
                            score = ats.get("ats_score", 0)
                            if isinstance(score, str) and score.isdigit():
                                score = int(score)
                            
                            if isinstance(score, (int, float)):
                                if score >= 80:
                                    st.success(f"**ATS Score:** {score}/100")
                                elif score >= 60:
                                    st.warning(f"**ATS Score:** {score}/100")
                                else:
                                    st.error(f"**ATS Score:** {score}/100")
                            
                            st.write(f"**Summary:** {ats.get('one_line_summary', 'N/A')}")
                            
                            if ats.get("skills"):
                                st.write(f"**Skills Found:** {', '.join(ats['skills'][:10])}")
                            
                            if ats.get("issues"):
                                with st.expander("⚠️ Issues to Fix"):
                                    for issue in ats["issues"][:5]:
                                        st.write(f"- {issue}")
                            
                            if ats.get("suggestions"):
                                with st.expander("💡 Suggestions"):
                                    for suggestion in ats["suggestions"][:5]:
                                        st.write(f"- {suggestion}")
                        
                        if st.session_state.current_question:
                            st.markdown("#### Generated Question:")
                            st.info(st.session_state.current_question)
                    else:
                        st.error("Could not extract text from the uploaded file")
                else:
                    st.info("👆 Upload your resume to analyze and generate questions")
            
            elif mode == "Company Based":
                company = st.session_state.get("selected_company", "Wipro")
                st.write(f"**Selected Company:** {company}")
                
                if st.button(f"🏢 Generate {company} Question", use_container_width=True):
                    q = generate_question_with_context(
                        "Company",
                        st.session_state.difficulty,
                        company
                    )
                    st.session_state.current_question = q
                    st.session_state.chat.append(("Interviewer", q))
                
                if st.session_state.current_question:
                    st.markdown("#### Question:")
                    st.info(st.session_state.current_question)
            
            elif mode == "History":
                hist = load_history()
                if hist:
                    for item in hist:
                        with st.expander(f"{item['name']} - {item['timestamp'][:10]}"):
                            st.write(f"**Time:** {item['timestamp'][:16]}")
                            if item.get('chat'):
                                preview = item['chat'][0]['text'][:100] + "..." if len(item['chat'][0]['text']) > 100 else item['chat'][0]['text']
                                st.write(f"**Preview:** {preview}")
                            if st.button("Load This Chat", key=f"load_{item['id']}"):
                                st.session_state.chat = [(m["role"], m["text"]) for m in item["chat"]]
                                st.rerun()
                else:
                    st.info("No saved chat history found")
    
    st.markdown("</div>", unsafe_allow_html=True)

# -----------------------------------------------------------------------
# RIGHT PANEL - CHAT & FEEDBACK
# -----------------------------------------------------------------------
with right:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("### 💬 Chat & Feedback")
    
    # Chat container with auto-scroll
    chat_container = st.container(height=500, border=False)
    
    with chat_container:
        for role, text in st.session_state.chat[-20:]:  # Show last 20 messages
            if role == "HR Feedback":
                st.markdown(f"""
                <div class='panel-feedback' style='border-left: 4px solid #0b4a8f;'>
                    <b>📋 HR Feedback:</b><br>
                    {text}
                </div>
                """, unsafe_allow_html=True)
            elif role == "Technical Feedback":
                st.markdown(f"""
                <div class='panel-feedback' style='border-left: 4px solid #28a745;'>
                    <b>⚙️ Technical Feedback:</b><br>
                    {text}
                </div>
                """, unsafe_allow_html=True)
            elif role == "Tips":
                st.markdown(f"""
                <div class='panel-feedback' style='background: rgba(255, 247, 209, 0.3); border-left: 4px solid #ffc107;'>
                    <b>💡 Tips:</b><br>
                    {text}
                </div>
                """, unsafe_allow_html=True)
            elif role == "Interviewer":
                st.markdown(f"""
                <div class='chat-left'>
                    <b>👨‍💼 Interviewer:</b><br>
                    {text}
                </div>
                """, unsafe_allow_html=True)
            elif role == "You":
                st.markdown(f"""
                <div class='chat-right'>
                    <b>👤 You:</b><br>
                    {text}
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class='chat-center'>
                    <b>{role}:</b><br>
                    {text}
                </div>
                """, unsafe_allow_html=True)
    
    # Auto-scroll JavaScript
    st.markdown("""
    <script>
    function scrollToBottom() {
        const container = document.querySelector('[data-testid="stVerticalBlock"] [data-testid="stVerticalBlockBorderWrapper"]');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }
    setTimeout(scrollToBottom, 100);
    </script>
    """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Input and Feedback Section
    st.markdown("<div class='card' style='margin-top: 20px;'>", unsafe_allow_html=True)
    
    # Only show input in practice mode
    if not st.session_state.get("mock_mode", False):
        user_input = st.text_area(
            "✍️ Your Answer:",
            height=100,
            placeholder="Type your answer here...",
            key="user_input_text"
        )
        
        col_send, col_feedback = st.columns([1, 1])
        
        with col_send:
            if st.button("📤 Send", use_container_width=True):
                if user_input.strip():
                    st.session_state.chat.append(("You", user_input.strip()))
                    
                    # Generate feedback if there's a current question
                    current_q = st.session_state.get("current_question", "")
                    if current_q:
                        # Determine question type
                        if st.session_state.mode_selection in ["HR Questions", "Resume Based"]:
                            q_type = "HR"
                        else:
                            q_type = "Technical"
                        
                        with st.spinner("Generating feedback..."):
                            feedback = generate_feedback(current_q, user_input.strip(), q_type)
                            
                            # Add feedback to chat
                            st.session_state.chat.append(
                                (f"{q_type} Feedback", 
                                 f"**Rating:** {feedback.get('rating', 'N/A')}/10\n"
                                 f"**Strengths:** {feedback.get('strengths', 'N/A')}\n"
                                 f"**Improvements:** {feedback.get('improvements', 'N/A')}")
                            )
                            
                            if feedback.get("sample_answer"):
                                st.session_state.chat.append(
                                    ("Tips", f"**Sample Answer:** {feedback.get('sample_answer')}")
                                )
                        
                        st.session_state.answered_questions.add(current_q)
                    
                    st.session_state.current_question = ""
                    st.rerun()
                else:
                    st.warning("Please enter your answer")
        
        with col_feedback:
            if st.button("💡 Get Feedback", use_container_width=True):
                if st.session_state.chat and st.session_state.chat[-1][0] == "You":
                    latest_answer = st.session_state.chat[-1][1]
                    # Find the most recent interviewer question
                    for role, text in reversed(st.session_state.chat):
                        if role == "Interviewer":
                            with st.spinner("Analyzing..."):
                                feedback = generate_feedback(text, latest_answer, "General")
                                st.session_state.chat.append(
                                    ("General Feedback",
                                     f"**Rating:** {feedback.get('rating', 'N/A')}/10\n"
                                     f"**Feedback:** {feedback.get('strengths', '')}\n"
                                     f"**Improvements:** {feedback.get('improvements', '')}")
                                )
                            st.rerun()
                            break
                    else:
                        st.warning("No question found to provide feedback on")
                else:
                    st.warning("Please answer a question first")
    
    st.markdown("</div>", unsafe_allow_html=True)