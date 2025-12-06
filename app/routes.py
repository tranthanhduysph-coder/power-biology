from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response, current_app
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
import openai, csv, io, uuid, time, json, os

main = Blueprint('main', __name__)

# ==============================================================================
# 1. CÁC HÀM HỖ TRỢ (HELPERS)
# ==============================================================================

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'docx', 'txt'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
        assistant_id = os.environ.get('CHATBOT_AI_ID') if bot_type == 'ai' else os.environ.get('CHATBOT_GOFAI_ID')
        
        if not api_key or not assistant_id: 
            return "Lỗi cấu hình: Thiếu API Key hoặc Assistant ID."
        
        openai.api_key = api_key
        client = openai
        
        # Quản lý Thread
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

        # Gửi tin nhắn
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_message)
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant_id)
        
        # Chờ kết quả
        while run.status in ['queued', 'in_progress']:
            time.sleep(0.5)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            
        if run.status == 'completed':
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            return messages.data[0].content[0].text.value
        return f"Lỗi Assistant: Trạng thái {run.status}"
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return "Hệ thống đang bận. Vui lòng thử lại sau giây lát."

def handle_chat_logic(bot_type_check):
    """Logic xử lý chat chung cho cả 2 bot"""
    # 1. Check quyền
    if not current_user.is_admin and current_user.bot_type != bot_type_check:
        return jsonify({'response': "Lỗi: Bạn không có quyền truy cập bot này."}), 403

    # 2. Lấy dữ liệu từ Form (Text & File)
    user_text = request.form.get('user_input', '').strip()
    uploaded_file = request.files.get('file')
    session_id = session.get('chat_session_id')
    
    file_msg = ""
    db_file_path = None
    filename = None

    # 3. Xử lý File Upload
    if uploaded_file and allowed_file(uploaded_file.filename):
        filename = secure_filename(uploaded_file.filename)
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        uploaded_file.save(save_path)
        
        db_file_path = f"/static/uploads/{filename}"
        file_msg = f"\n[Hệ thống: Người dùng đã gửi file {filename}]"

    if not user_text and not uploaded_file:
        return jsonify({'response': ""}), 400

    # 4. Lưu tin nhắn User vào DB
    content_to_save = user_text
    if db_file_path:
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            content_to_save += f'<br><img src="{db_file_path}" style="max-width: 200px; border-radius: 10px; margin-top: 10px;">'
        else:
            content_to_save += f'<br><a href="{db_file_path}" target="_blank" style="color: #fff; text-decoration: underline;"><i class="fas fa-paperclip"></i> {filename}</a>'

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
    except Exception as e:
        print(f"Log Error: {e}")

    # 7. Lưu Bot Message
    bot_msg = Message(sender='assistant', content=ui_text, author=current_user, session_id=session_id)
    db.session.add(bot_msg)
    db.session.commit()

    return jsonify({'response': ui_text})

# ==============================================================================
# 2. CÁC ROUTE CƠ BẢN (Login, Logout, Redirect)
# ==============================================================================

@main.route('/')
def index():
    return redirect(url_for('main.login'))

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
        flash('Đăng nhập thất bại. Kiểm tra lại ID và mật khẩu.', 'danger')
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('main.login'))

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

@main.route('/chatbot_redirect')
@login_required
def chatbot_redirect():
    if current_user.is_admin:
        # ĐÂY LÀ DÒNG GÂY LỖI NẾU HÀM admin_dashboard KHÔNG TỒN TẠI
        return redirect(url_for('main.admin_dashboard'))
    return redirect(url_for(f'main.chatbot_{current_user.bot_type}'))

# ==============================================================================
# 3. CÁC ROUTE CHATBOT (AI & GOFAI)
# ==============================================================================

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
    flash("Đã bắt đầu phiên làm việc mới.", "success")
    return redirect(url_for('main.chatbot_redirect'))

# ==============================================================================
# 4. CÁC ROUTE ADMIN (QUAN TRỌNG - PHẢI CÓ ĐỂ TRÁNH LỖI 500)
# ==============================================================================

@main.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    reset_form = ResetPasswordForm()
    
    # Xử lý thêm user lẻ
    if user_form.validate_on_submit() and 'username' in request.form:
        if not User.query.filter_by(username=user_form.username.data).first():
            new_user = User(username=user_form.username.data, bot_type=user_form.bot_type.data, is_admin=user_form.is_admin.data)
            new_user.set_password(user_form.password.data)
            db.session.add(new_user)
            db.session.commit()
            flash('Thêm người dùng thành công!', 'success')
        else:
            flash('ID đã tồn tại.', 'danger')
        return redirect(url_for('main.admin_dashboard'))
    
    # Xử lý upload CSV
    if upload_form.validate_on_submit() and 'csv_file' in request.files:
        try:
            stream = io.StringIO(upload_form.csv_file.data.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.reader(stream)
            next(csv_reader, None)
            count = 0
            for row in csv_reader:
                if len(row) >= 3:
                    u, p, t = row[0].strip(), row[1].strip(), row[2].strip().lower()
                    if not User.query.filter_by(username=u).first():
                        usr = User(username=u, bot_type=t)
                        usr.set_password(p)
                        db.session.add(usr)
                        count += 1
            db.session.commit()
            flash(f'Đã thêm {count} tài khoản từ CSV.', 'success')
        except Exception as e:
            flash(f'Lỗi file CSV: {e}', 'danger')
        return redirect(url_for('main.admin_dashboard'))

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
        flash(f'Đã reset mật khẩu cho {user.username}.', 'success')
    return redirect(url_for('main.admin_dashboard'))

@main.route('/admin/delete/<int:user_id>')
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        db.session.delete(user)
        db.session.commit()
        flash('Đã xóa người dùng.', 'success')
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
    csv_writer.writerow(['Time', 'Session', 'User', 'Type', 'Content', 'Value'])
    
    msgs = db.session.query(Message, User).join(User).all()
    logs = db.session.query(VariableLog, User).join(User).all()
    
    events = []
    for m, u in msgs:
        events.append({'t': m.timestamp, 's': m.session_id, 'u': u.username, 'type': f'MSG_{m.sender.upper()}', 'c': m.content, 'v': ''})
    for l, u in logs:
        events.append({'t': l.timestamp, 's': l.session_id, 'u': u.username, 'type': 'LOG', 'c': l.variable_name, 'v': l.variable_value})
    
    events.sort(key=lambda x: x['t'])
    for e in events:
        csv_writer.writerow([e['t'], e['s'], e['u'], e['type'], e['c'], e['v']])
        
    return Response(string_io.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=full_data.csv"})
