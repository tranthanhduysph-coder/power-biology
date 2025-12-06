from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response, current_app
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
import openai, csv, io, uuid, time, json, os

main = Blueprint('main', __name__)

# --- HELPERS ---

def allowed_file(filename):
    ALLOWED = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'docx', 'txt'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Bạn không có quyền truy cập.", "danger")
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)
    return decorated_function

def get_assistant_response(user_message, bot_type):
    try:
        api_key = os.environ.get('OPENAI_API_KEY')
        assistant_id = os.environ.get('CHATBOT_AI_ID') if bot_type == 'ai' else os.environ.get('CHATBOT_GOFAI_ID')
        
        if not api_key or not assistant_id: return "Lỗi cấu hình API Key hoặc Assistant ID."
        
        openai.api_key = api_key
        client = openai
        
        thread_id = session.get('thread_id')
        if not thread_id:
            if current_user.current_thread_id:
                thread_id = current_user.current_thread_id
            else:
                thread = client.beta.threads.create()
                thread_id = thread.id
                current_user.current_thread_id = thread_id
                db.session.commit()
            session['thread_id'] = thread_id

        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_message)
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant_id)
        
        while run.status in ['queued', 'in_progress']:
            time.sleep(0.5)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            
        if run.status == 'completed':
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            return messages.data[0].content[0].text.value
        return f"Lỗi: {run.status}"
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return "Hệ thống đang bận. Vui lòng thử lại."

def handle_chat_logic(bot_type_check):
    # 1. Check quyền
    if not current_user.is_admin and current_user.bot_type != bot_type_check:
        return jsonify({'response': "Lỗi quyền truy cập."}), 403

    # 2. Lấy data (Form Data vì có file)
    user_text = request.form.get('user_input', '').strip()
    uploaded_file = request.files.get('file')
    session_id = session.get('chat_session_id')
    
    file_msg = ""
    db_file_path = None

    # 3. Xử lý File
    if uploaded_file and allowed_file(uploaded_file.filename):
        filename = secure_filename(uploaded_file.filename)
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        uploaded_file.save(save_path)
        
        db_file_path = f"/static/uploads/{filename}"
        file_msg = f"\n[User sent file: {filename}]"

    if not user_text and not uploaded_file:
        return jsonify({'response': ""}), 400

    # 4. Lưu User Message vào DB
    content_to_save = user_text
    if db_file_path:
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            content_to_save += f'<br><img src="{db_file_path}" style="max-width: 200px; border-radius: 10px; margin-top: 5px;">'
        else:
            content_to_save += f'<br><a href="{db_file_path}" target="_blank"><i class="fas fa-paperclip"></i> {filename}</a>'

    user_msg = Message(sender='user', content=content_to_save, author=current_user, session_id=session_id)
    db.session.add(user_msg)

    # 5. Gọi AI
    prompt = user_text + file_msg
    full_resp = get_assistant_response(prompt, bot_type_check)

    # 6. Tách JSON Log
    ui_text = full_resp
    try:
        if "```json" in full_resp:
            parts = full_resp.split("```json")
            ui_text = parts[0].strip()
            json_str = parts[1].split("```")[0].replace("LOG_DATA =", "").strip()
            if json_str:
                data = json.loads(json_str)
                for k, v in data.items():
                    db.session.add(VariableLog(user_id=current_user.id, session_id=session_id, variable_name=str(k), variable_value=str(v)))
    except: pass

    # 7. Lưu Bot Message
    bot_msg = Message(sender='assistant', content=ui_text, author=current_user, session_id=session_id)
    db.session.add(bot_msg)
    db.session.commit()

    return jsonify({'response': ui_text})

# --- ROUTES ---

@main.route('/')
def index(): return redirect(url_for('main.login'))

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('main.chatbot_redirect'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            return redirect(url_for('main.chatbot_redirect'))
        flash('Sai ID hoặc mật khẩu.', 'danger')
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('main.login'))

@main.route('/chatbot_redirect')
@login_required
def chatbot_redirect():
    if current_user.is_admin: return redirect(url_for('main.admin_dashboard'))
    return redirect(url_for(f'main.chatbot_{current_user.bot_type}'))

@main.route('/chatbot/ai', methods=['GET', 'POST'])
@login_required
def chatbot_ai():
    if not current_user.is_admin and current_user.bot_type != 'ai':
        return redirect(url_for('main.chatbot_redirect'))
    
    if request.method == 'POST': return handle_chat_logic('ai')

    if not current_user.current_session_id:
        current_user.current_session_id = str(uuid.uuid4())
        db.session.commit()
    session['chat_session_id'] = current_user.current_session_id
    session['thread_id'] = current_user.current_thread_id
    
    hist = Message.query.filter_by(user_id=current_user.id, session_id=session['chat_session_id']).order_by(Message.timestamp.asc()).all()
    return render_template('chatbot_layout.html', chat_history=hist, bot_name="POWER Coach (AI)", endpoint="/chatbot/ai")

@main.route('/chatbot/gofai', methods=['GET', 'POST'])
@login_required
def chatbot_gofai():
    if not current_user.is_admin and current_user.bot_type != 'gofai':
        return redirect(url_for('main.chatbot_redirect'))
    
    if request.method == 'POST': return handle_chat_logic('gofai')

    if not current_user.current_session_id:
        current_user.current_session_id = str(uuid.uuid4())
        db.session.commit()
    session['chat_session_id'] = current_user.current_session_id
    session['thread_id'] = current_user.current_thread_id
    
    hist = Message.query.filter_by(user_id=current_user.id, session_id=session['chat_session_id']).order_by(Message.timestamp.asc()).all()
    return render_template('chatbot_layout.html', chat_history=hist, bot_name="POWER Biology (Basic)", endpoint="/chatbot/gofai")

@main.route('/reset_session')
@login_required
def reset_session():
    try:
        openai.api_key = os.environ.get('OPENAI_API_KEY')
        current_user.current_thread_id = openai.beta.threads.create().id
    except: pass
    current_user.current_session_id = str(uuid.uuid4())
    db.session.commit()
    flash("Đã bắt đầu phiên mới.", "success")
    return redirect(url_for('main.chatbot_redirect'))

# --- ADMIN ROUTES (GIỮ NGUYÊN NHƯ CŨ, CHỈ CẦN COPY PHẦN ADMIN TỪ CODE CŨ VÀO ĐÂY NẾU CẦN) ---
# ... (Phần Admin Dashboard, Create User, Export CSV giữ nguyên như bạn đã có) ...
