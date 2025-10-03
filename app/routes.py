from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
import openai, csv, io, uuid, time, json, os

main = Blueprint('main', __name__)

# --- DECORATOR ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Bạn không có quyền truy cập trang này.", "danger")
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)
    return decorated_function

# --- HÀM GỌI ASSISTANT ---
def get_assistant_response(user_message, bot_type):
    # ... (Hàm này giữ nguyên như cũ, không thay đổi)
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

# --- CÁC ROUTE CƠ BẢN ---
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
    # ... (Hàm này giữ nguyên như cũ, không thay đổi)
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
    # ... (Hàm này giữ nguyên như cũ, không thay đổi)
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
    # ... (Hàm này giữ nguyên như cũ, không thay đổi)
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
    # ... (Hàm này giữ nguyên như cũ, không thay đổi)
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

# --- CÁC ROUTE CHO ADMIN (PHẦN BỔ SUNG QUAN TRỌNG) ---

@main.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    reset_form = ResetPasswordForm()
    
    if user_form.validate_on_submit() and 'username' in request.form:
        # Xử lý thêm người dùng
        pass

    if upload_form.validate_on_submit() and 'csv_file' in request.files:
        # Xử lý upload CSV
        pass
        
    users = User.query.filter_by(is_admin=False).all()
    return render_template('admin_dashboard.html', users=users, user_form=user_form, upload_form=upload_form, reset_form=reset_form)

@main.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reset_student_password(user_id):
    user = User.query.get_or_404(user_id)
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.new_password.data)
        db.session.commit()
        flash(f'Reset mật khẩu cho {user.username} thành công.', 'success')
    else:
        flash('Mật khẩu mới không hợp lệ.', 'danger')
    return redirect(url_for('main.admin_dashboard'))

@main.route('/admin/history/<int:user_id>')
@login_required
@admin_required
def view_chat_history(user_id):
    student = User.query.get_or_404(user_id)
    messages = Message.query.filter_by(user_id=user_id).order_by(Message.session_id, Message.timestamp.asc()).all()
    return render_template('chat_history.html', student=student, messages=messages)

@main.route('/admin/logs/<int:user_id>')
@login_required
@admin_required
def view_variable_logs(user_id):
    student = User.query.get_or_404(user_id)
    logs = VariableLog.query.filter_by(user_id=user_id).order_by(VariableLog.timestamp.desc()).all()
    return render_template('variable_logs.html', student=student, logs=logs)

@main.route('/admin/export_history')
@login_required
@admin_required
def export_chat_history():
    string_io = io.StringIO()
    csv_writer = csv.writer(string_io)
    header = ['timestamp', 'session_id', 'username', 'event_type', 'content_or_variable_name', 'value']
    csv_writer.writerow(header)
    all_messages = db.session.query(Message, User).join(User, Message.user_id == User.id).all()
    all_logs = db.session.query(VariableLog, User).join(User, VariableLog.user_id == User.id).all()
    events = []
    for message, user in all_messages:
        events.append({'timestamp': message.timestamp, 'session_id': message.session_id, 'username': user.username, 'event_type': f'MESSAGE_{message.sender.upper()}', 'content_or_variable_name': message.content, 'value': ''})
    for log, user in all_logs:
        events.append({'timestamp': log.timestamp, 'session_id': log.session_id, 'username': user.username, 'event_type': 'VARIABLE_LOG', 'content_or_variable_name': log.variable_name, 'value': log.variable_value})
    events.sort(key=lambda x: x['timestamp'])
    for event in events:
        csv_writer.writerow([event['timestamp'].strftime('%Y-%m-%d %H:%M:%S'), event['session_id'], event['username'], event['event_type'], event['content_or_variable_name'], event['value']])
    output = string_io.getvalue()
    string_io.close()
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=full_event_log.csv"})

@main.route('/admin/delete/<int:user_id>')
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("Bạn không thể tự xóa tài khoản của mình.", "danger")
    else:
        db.session.delete(user)
        db.session.commit()
        flash('Xóa người dùng thành công.', 'success')
    return redirect(url_for('main.admin_dashboard'))