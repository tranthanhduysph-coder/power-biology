from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
import openai, csv, io, uuid, time, json, os

main = Blueprint('main', __name__)

# (Các hàm admin_required và get_assistant_response giữ nguyên)
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Bạn không có quyền truy cập trang này.", "danger")
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)
    return decorated_function

def get_assistant_response(user_message, bot_type):
    try:
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key: return "LỖI CẤU HÌNH: OPENAI_API_KEY không được thiết lập."
        openai.api_key = api_key
        assistant_id = os.environ.get('CHATBOT_AI_ID') if bot_type == 'ai' else os.environ.get('CHATBOT_GOFAI_ID')
        if not assistant_id: return f"LỖI CẤU HÌNH: CHATBOT_{bot_type.upper()}_ID không được thiết lập."
        client = openai
        thread_id = session.get('thread_id')
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            session['thread_id'] = thread_id
            current_user.current_thread_id = thread_id
            db.session.commit()
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_message)
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant_id)
        while run.status in ['queued', 'in_progress']:
            time.sleep(0.5)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run.status == 'completed':
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            return messages.data[0].content[0].text.value
        return f"Lỗi Assistant: {run.status}"
    except Exception as e:
        return f"Lỗi API: {e}"

# --- SỬA LỖI TRONG HÀM /ask ---
@main.route('/ask', methods=['POST'])
@login_required
def ask():
    user_message_content = request.json.get('message')
    if not user_message_content:
        return jsonify({'response': 'Lỗi: Không nhận được tin nhắn.'}), 400

    session_id = session.get('chat_session_id')

    # LƯU TIN NHẮN CỦA NGƯỜI DÙNG
    user_msg = Message(sender='user', content=user_message_content, author=current_user, session_id=session_id)
    db.session.add(user_msg)

    # GỌI ASSISTANT ĐỂ NHẬN PHẢN HỒI
    full_assistant_response = get_assistant_response(user_message_content, current_user.bot_type)

    # XỬ LÝ VÀ LƯU CÁC LOG BIẾN (NẾU CÓ)
    user_facing_text = full_assistant_response
    try:
        parts = full_assistant_response.split("```json")
        if len(parts) > 1:
            user_facing_text = parts[0].strip()
            json_string = parts[1].replace("LOG_DATA = ", "").strip()
            if json_string.endswith("```"):
                json_string = json_string[:-3].strip()
            
            logged_data = json.loads(json_string)
            for var_name, var_value in logged_data.items():
                new_log = VariableLog(
                    user_id=current_user.id,
                    session_id=session_id,
                    variable_name=str(var_name),
                    variable_value=str(var_value)
                )
                db.session.add(new_log)
    except Exception as e:
        print(f"Không tìm thấy hoặc lỗi khi xử lý log JSON: {e}")

    # LƯU TIN NHẮN CỦA BOT
    bot_msg = Message(sender='assistant', content=user_facing_text, author=current_user, session_id=session_id)
    db.session.add(bot_msg)
    
    # COMMIT TẤT CẢ THAY ĐỔI VÀO DATABASE
    db.session.commit()

    return jsonify({'response': user_facing_text})

# (Tất cả các hàm route khác giữ nguyên)
@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.chatbot_redirect'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            return redirect(url_for('main.chatbot_redirect'))
        flash('Đăng nhập thất bại. Vui lòng kiểm tra lại ID và mật khẩu.', 'danger')
    return render_template('login.html', form=form)

def chatbot_view(template_name):
    bot_type = template_name.split('_')[1].split('.')[0]
    if not current_user.is_admin and current_user.bot_type != bot_type:
        flash("Bạn không có quyền truy cập chatbot này.", "danger")
        return redirect(url_for('main.chatbot_redirect'))
    if not current_user.current_session_id:
        current_user.current_session_id = str(uuid.uuid4())
        db.session.commit()
    session['chat_session_id'] = current_user.current_session_id
    session['thread_id'] = current_user.current_thread_id
    chat_history = Message.query.filter_by(user_id=current_user.id, session_id=session['chat_session_id']).order_by(Message.timestamp.asc()).all()
    return render_template(template_name, chat_history=chat_history)
# ... và các hàm route khác