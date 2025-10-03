from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
import openai, csv, io, uuid, time, json, os

main = Blueprint('main', __name__)

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

@main.route('/ask', methods=['POST'])
@login_required
def ask():
    user_message_content = request.json.get('message')
    session_id = session.get('chat_session_id')
    user_msg = Message(sender='user', content=user_message_content, author=current_user, session_id=session_id)
    db.session.add(user_msg)
    
    full_assistant_response = get_assistant_response(user_message_content, current_user.bot_type)
    
    bot_msg = Message(sender='assistant', content=full_assistant_response, author=current_user, session_id=session_id)
    db.session.add(bot_msg)
    db.session.commit()
    return jsonify({'response': full_assistant_response})

@main.route('/')
def index():
    return redirect(url_for('main.login'))

@main.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('main.login'))

@main.route('/chatbot_redirect')
@login_required
def chatbot_redirect():
    if current_user.is_admin:
        return redirect(url_for('main.admin_dashboard'))
    return redirect(url_for(f'main.chatbot_{current_user.bot_type}'))

@main.route('/chatbot/ai')
@login_required
def chatbot_ai():
    return chatbot_view('chatbot_ai.html')

@main.route('/chatbot/gofai')
@login_required
def chatbot_gofai():
    return chatbot_view('chatbot_gofai.html')

@main.route('/reset_session')
@login_required
def reset_session():
    new_session_id = str(uuid.uuid4())
    try:
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key:
            openai.api_key = api_key
            new_thread = openai.beta.threads.create()
            current_user.current_thread_id = new_thread.id
    except Exception as e:
        print(f"Lỗi khi tạo thread mới: {e}")
        current_user.current_thread_id = None
        
    current_user.current_session_id = new_session_id
    db.session.commit()
    flash("Phiên làm việc đã được khởi động lại.", "info")
    return redirect(url_for(f'main.chatbot_{current_user.bot_type}'))

@main.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            current_user.set_password(form.new_password.data)
            db.session.commit()
            flash('Đổi mật khẩu thành công!', 'success')
            return redirect(url_for('main.chatbot_redirect'))
        else:
            flash('Mật khẩu hiện tại không đúng.', 'danger')
    return render_template('change_password.html', form=form)

@main.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    reset_form = ResetPasswordForm()
    users = User.query.filter_by(is_admin=False).all()
    # (Thêm logic xử lý form ở đây nếu bạn muốn nó hoạt động)
    return render_template('admin_dashboard.html', users=users, user_form=user_form, upload_form=upload_form, reset_form=reset_form)