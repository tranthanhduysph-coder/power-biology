from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response, current_app
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from sqlalchemy import func, desc
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
import openai, csv, io, uuid, time, json, os

# --- 1. KHỞI TẠO BLUEPRINT ---
main = Blueprint('main', __name__)

# --- 2. CÁC HÀM HỖ TRỢ ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'docx', 'doc', 'txt'}

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
        
        if not api_key or not assistant_id: return "Lỗi cấu hình OpenAI API."
        
        openai.api_key = api_key
        client = openai
        
        # Thread Management
        thread_id = current_user.current_thread_id
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            current_user.current_thread_id = thread_id
            db.session.commit()
        
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_message)
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant_id)
        
        while run.status in ['queued', 'in_progress']:
            time.sleep(0.5)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            
        if run.status == 'completed':
            msgs = client.beta.threads.messages.list(thread_id=thread_id)
            return msgs.data[0].content[0].text.value
        return f"AI Error: {run.status}"
    except Exception as e:
        print(f"AI Exception: {e}")
        return "Hệ thống đang bận."

def handle_chat_logic(bot_type_check):
    if not current_user.is_admin and current_user.bot_type != bot_type_check:
        return jsonify({'response': "Lỗi quyền truy cập."}), 403

    user_text = request.form.get('user_input', '').strip()
    file = request.files.get('file')
    
    # Session Management
    sess_id = current_user.current_session_id
    if not sess_id:
        sess_id = str(uuid.uuid4())
        current_user.current_session_id = sess_id
        db.session.commit()

    file_html = ""
    file_msg = ""

    # Xử lý File Upload (Tính năng mới)
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(save_path)
        web_path = f"/static/uploads/{filename}"
        file_msg = f"\n[User uploaded: {filename}]"
        
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            file_html = f'<br><img src="{web_path}" style="max-width:200px; border-radius:10px; margin-top:5px;">'
        else:
            file_html = f'<br><a href="{web_path}" target="_blank" class="file-link"><i class="fas fa-paperclip"></i> {filename}</a>'

    if not user_text and not file: return jsonify({'response': ""}), 400

    # Lưu tin nhắn User
    user_msg = Message(sender='user', content=user_text + file_html, author=current_user, session_id=sess_id)
    db.session.add(user_msg)

    # Gọi AI
    full_resp = get_assistant_response(user_text + file_msg, bot_type_check)

    # Xử lý JSON Log (Tính năng mới: Tách điểm số)
    ui_text = full_resp
    try:
        if "```json" in full_resp:
            parts = full_resp.split("```json")
            ui_text = parts[0].strip()
            json_str = parts[1].split("```")[0].replace("LOG_DATA =", "").strip()
            if json_str:
                data = json.loads(json_str)
                for k, v in data.items():
                    db.session.add(VariableLog(user_id=current_user.id, session_id=sess_id, variable_name=str(k), variable_value=str(v)))
    except Exception as e:
        print(f"Log Error: {e}")

    # Lưu tin nhắn Bot
    bot_msg = Message(sender='assistant', content=full_resp, author=current_user, session_id=sess_id)
    db.session.add(bot_msg)
    db.session.commit()

    # Trả về nội dung (Frontend sẽ dùng JS cleanJSON để ẩn code đi)
    return jsonify({'response': full_resp})

# --- 3. CÁC ROUTE NGƯỜI DÙNG ---

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
            if not user.current_session_id:
                user.current_session_id = str(uuid.uuid4())
                db.session.commit()
            return redirect(url_for('main.chatbot_redirect'))
        flash('Sai ID hoặc mật khẩu.', 'danger')
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.login'))

@main.route('/chatbot_redirect')
@login_required
def chatbot_redirect():
    if current_user.is_admin: return redirect(url_for('main.admin_dashboard'))
    return redirect(url_for(f'main.chatbot_{current_user.bot_type}'))

# --- LOGIC RENDER CHAT VỚI HISTORY ---
def get_user_sessions():
    return db.session.query(
        Message.session_id, func.max(Message.timestamp).label('last_active')
    ).filter_by(user_id=current_user.id).group_by(Message.session_id).order_by(desc('last_active')).all()

def render_chat_page(bot_type, bot_name):
    if not current_user.is_admin and current_user.bot_type != bot_type:
        return redirect(url_for('main.chatbot_redirect'))
    
    if request.method == 'POST': return handle_chat_logic(bot_type)

    sess_id = current_user.current_session_id
    if not sess_id:
        sess_id = str(uuid.uuid4())
        current_user.current_session_id = sess_id
        db.session.commit()
    
    hist = Message.query.filter_by(user_id=current_user.id, session_id=sess_id).order_by(Message.timestamp.asc()).all()
    
    # Sidebar Session List
    sessions = get_user_sessions()
    session_list = [{'id': s[0], 'name': f"Hội thoại {s[1].strftime('%d/%m %H:%M')}", 'active': s[0]==sess_id} for s in sessions]

    return render_template('chatbot_layout.html', 
                           chat_history=hist, 
                           bot_name=bot_name, 
                           endpoint=f"/chatbot/{bot_type}",
                           session_list=session_list)

@main.route('/chatbot/ai', methods=['GET', 'POST'])
@login_required
def chatbot_ai(): return render_chat_page('ai', "AI Coach")

@main.route('/chatbot/gofai', methods=['GET', 'POST'])
@login_required
def chatbot_gofai(): return render_chat_page('gofai', "Basic Bot")

# --- SESSION UTILS ---
@main.route('/new_chat')
@login_required
def new_chat():
    current_user.current_session_id = str(uuid.uuid4())
    try:
        openai.api_key = os.environ.get('OPENAI_API_KEY')
        current_user.current_thread_id = openai.beta.threads.create().id
    except: pass
    db.session.commit()
    return redirect(url_for('main.chatbot_redirect'))

@main.route('/reset_session')
@login_required
def reset_session(): return redirect(url_for('main.new_chat'))

@main.route('/switch_session/<session_id>')
@login_required
def switch_session(session_id):
    if Message.query.filter_by(user_id=current_user.id, session_id=session_id).first():
        current_user.current_session_id = session_id
        db.session.commit()
    return redirect(url_for('main.chatbot_redirect'))

@main.route('/delete_session/<session_id>')
@login_required
def delete_session(session_id):
    Message.query.filter_by(user_id=current_user.id, session_id=session_id).delete()
    if current_user.current_session_id == session_id:
        current_user.current_session_id = str(uuid.uuid4())
        try:
            openai.api_key = os.environ.get('OPENAI_API_KEY')
            current_user.current_thread_id = openai.beta.threads.create().id
        except: pass
    db.session.commit()
    return redirect(url_for('main.chatbot_redirect'))

@main.route('/disclaimer')
def disclaimer(): return render_template('disclaimer.html')

# --- 4. ADMIN DASHBOARD (CODE CŨ CỦA BẠN - ĐÃ TÍCH HỢP) ---

@main.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    reset_form = ResetPasswordForm()

    # 1. Thêm thủ công
    if user_form.validate_on_submit() and 'username' in request.form:
        if not User.query.filter_by(username=user_form.username.data).first():
            u = User(username=user_form.username.data, bot_type=user_form.bot_type.data, is_admin=user_form.is_admin.data)
            u.set_password(user_form.password.data)
            db.session.add(u)
            db.session.commit()
            flash('Thêm thành công!', 'success')
        else:
            flash('ID đã tồn tại.', 'danger')
        return redirect(url_for('main.admin_dashboard'))

    # 2. Upload CSV (SỬ DỤNG LẠI LOGIC CŨ CỦA BẠN)
    if upload_form.validate_on_submit() and upload_form.csv_file.data:
        try:
            # Đọc file stream và decode trực tiếp như code cũ
            stream = io.StringIO(upload_form.csv_file.data.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.reader(stream)
            
            # Bỏ qua Header
            next(csv_reader, None)
            
            count = 0
            for row in csv_reader:
                # Kiểm tra dữ liệu dòng
                if not row or len(row) < 1: continue
                
                # Cấu trúc: user, pass, bot_type
                r_user = row[0].strip()
                r_pass = row[1].strip() if len(row) > 1 else "123456"
                r_type = row[2].strip().lower() if len(row) > 2 else "gofai"
                
                # Map lại tên bot cho chuẩn với hệ thống mới
                if 'ai' in r_type: r_type = 'ai'
                elif 'basic' in r_type or 'gofai' in r_type: r_type = 'gofai'
                else: r_type = 'gofai'

                if not User.query.filter_by(username=r_user).first():
                    new_user = User(username=r_user, bot_type=r_type)
                    new_user.set_password(r_pass)
                    db.session.add(new_user)
                    count += 1
            
            db.session.commit()
            flash(f'Thêm thành công {count} tài khoản từ file CSV!', 'success')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi xử lý file CSV: {e}', 'danger')
            print(f"CSV Error: {e}")
            
        return redirect(url_for('main.admin_dashboard'))

    users = User.query.filter_by(is_admin=False).all()
    return render_template('admin_dashboard.html', users=users, user_form=user_form, upload_form=upload_form, reset_form=reset_form)

# --- CÁC ROUTE ADMIN KHÁC (Export, History...) ---

@main.route('/admin/delete/<int:user_id>')
@login_required
@admin_required
def delete_user(user_id):
    u = User.query.get_or_404(user_id)
    if u.id != current_user.id:
        db.session.delete(u); db.session.commit()
        flash('Đã xóa người dùng.', 'success')
    return redirect(url_for('main.admin_dashboard'))

@main.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reset_student_password(user_id):
    u = User.query.get_or_404(user_id); form = ResetPasswordForm()
    if form.validate_on_submit():
        u.set_password(form.new_password.data); db.session.commit()
        flash('Reset mật khẩu thành công.', 'success')
    return redirect(url_for('main.admin_dashboard'))

@main.route('/admin/history/<int:user_id>')
@login_required
@admin_required
def view_chat_history(user_id):
    u = User.query.get_or_404(user_id)
    msgs = Message.query.filter_by(user_id=user_id).order_by(Message.timestamp.asc()).all()
    return render_template('chat_history.html', student=u, messages=msgs)

@main.route('/admin/logs/<int:user_id>')
@login_required
@admin_required
def view_variable_logs(user_id):
    u = User.query.get_or_404(user_id)
    logs = VariableLog.query.filter_by(user_id=user_id).order_by(VariableLog.timestamp.desc()).all()
    return render_template('variable_logs.html', student=u, logs=logs)

@main.route('/admin/export_history')
@login_required
@admin_required
def export_chat_history():
    si = io.StringIO(); cw = csv.writer(si)
    # Header chuẩn cho báo cáo
    cw.writerow(['Time', 'Session ID', 'Username', 'Role', 'Content/Value'])
    
    all_msgs = db.session.query(Message, User).join(User).all()
    for m, u in all_msgs:
        cw.writerow([m.timestamp, m.session_id, u.username, m.sender, m.content])
        
    return Response(si.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=full_report.csv"})

@main.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            current_user.set_password(form.new_password.data); db.session.commit()
            flash('Đổi mật khẩu thành công.', 'success')
            return redirect(url_for('main.chatbot_redirect'))
        flash('Mật khẩu cũ không đúng.', 'danger')
    return render_template('change_password.html', form=form)
