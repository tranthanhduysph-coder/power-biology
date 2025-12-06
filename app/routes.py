from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
import openai, csv, io, uuid, time, json, os

main = Blueprint('main', __name__)

# ==============================================================================
# 1. CÁC HÀM HỖ TRỢ & DECORATOR
# ==============================================================================

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Bạn không có quyền truy cập trang này.", "danger")
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)
    return decorated_function

def get_assistant_response(user_message, bot_type):
    """Gửi tin nhắn đến OpenAI Assistant và nhận phản hồi."""
    try:
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key: return "LỖI CẤU HÌNH: OPENAI_API_KEY không được thiết lập."
        
        openai.api_key = api_key
        
        # Chọn Assistant ID dựa trên loại bot
        assistant_id = os.environ.get('CHATBOT_AI_ID') if bot_type == 'ai' else os.environ.get('CHATBOT_GOFAI_ID')
        if not assistant_id: return f"LỖI CẤU HÌNH: CHATBOT_{bot_type.upper()}_ID không được thiết lập."
        
        client = openai
        
        # Quản lý Thread ID
        thread_id = session.get('thread_id')
        if not thread_id:
            # Nếu chưa có trong session, thử lấy từ DB hoặc tạo mới
            if current_user.current_thread_id:
                thread_id = current_user.current_thread_id
            else:
                thread = client.beta.threads.create()
                thread_id = thread.id
                current_user.current_thread_id = thread_id
                db.session.commit()
            session['thread_id'] = thread_id

        # Gửi tin nhắn và Chạy Run
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_message)
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant_id)
        
        # Polling chờ kết quả
        while run.status in ['queued', 'in_progress']:
            time.sleep(0.5)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            
        if run.status == 'completed':
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            # Lấy tin nhắn mới nhất từ Assistant
            return messages.data[0].content[0].text.value
        else:
            return f"Lỗi Assistant: Trạng thái {run.status}"
            
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return "Xin lỗi, hệ thống đang bận. Vui lòng thử lại sau giây lát."

def handle_chat_logic(bot_type_check):
    """Logic cốt lõi để xử lý tin nhắn chat (Dùng chung cho cả AI và GOFAI)"""
    # 1. Kiểm tra quyền truy cập
    if not current_user.is_admin and current_user.bot_type != bot_type_check:
        return jsonify({'response': "Lỗi: Bạn không có quyền truy cập bot này."}), 403

    # 2. Lấy nội dung tin nhắn
    user_message_content = request.form.get('user_input')
    if not user_message_content:
        return jsonify({'response': ""}), 400

    session_id = session.get('chat_session_id')
    
    # 3. Lưu tin nhắn User vào DB
    user_msg = Message(sender='user', content=user_message_content, author=current_user, session_id=session_id)
    db.session.add(user_msg)
    
    # 4. Gọi OpenAI
    full_response = get_assistant_response(user_message_content, bot_type_check)
    
    # 5. Xử lý tách JSON Log (LOG_DATA)
    user_facing_text = full_response
    try:
        if "```json" in full_response:
            parts = full_response.split("```json")
            user_facing_text = parts[0].strip() # Phần văn bản hiển thị cho user
            
            # Phần JSON log ẩn
            json_part = parts[1].split("```")[0]
            json_string = json_part.replace("LOG_DATA =", "").strip()
            
            if json_string:
                logged_data = json.loads(json_string)
                for var_name, var_value in logged_data.items():
                    # Lưu log biến vào bảng VariableLog
                    new_log = VariableLog(
                        user_id=current_user.id, 
                        session_id=session_id, 
                        variable_name=str(var_name), 
                        variable_value=str(var_value)
                    )
                    db.session.add(new_log)
    except Exception as e:
        print(f"Lỗi xử lý JSON Log: {e}")
        # Nếu lỗi parse, vẫn hiển thị text gốc để không gián đoạn hội thoại
    
    # 6. Lưu tin nhắn Bot vào DB
    bot_msg = Message(sender='assistant', content=user_facing_text, author=current_user, session_id=session_id)
    db.session.add(bot_msg)
    db.session.commit()
    
    return jsonify({'response': user_facing_text})

# ==============================================================================
# 2. ROUTE XÁC THỰC (AUTH)
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

# ==============================================================================
# 3. ROUTE CHATBOT (LOGIC MỚI)
# ==============================================================================

@main.route('/chatbot_redirect')
@login_required
def chatbot_redirect():
    """Điều hướng người dùng về đúng giao diện bot của họ."""
    if current_user.is_admin:
        return redirect(url_for('main.admin_dashboard'))
    
    # Dựa vào bot_type để chuyển hướng
    if current_user.bot_type == 'ai':
        return redirect(url_for('main.chatbot_ai'))
    elif current_user.bot_type == 'gofai':
        return redirect(url_for('main.chatbot_gofai'))
    else:
        return "Lỗi: Loại tài khoản không xác định."

# --- CHATBOT AI (POWER COACH) ---
@main.route('/chatbot/ai', methods=['GET', 'POST'])
@login_required
def chatbot_ai():
    # 1. Bảo mật: Chỉ cho phép Admin hoặc User loại 'ai'
    if not current_user.is_admin and current_user.bot_type != 'ai':
        flash("Bạn không có quyền truy cập bot này.", "danger")
        return redirect(url_for('main.chatbot_redirect'))

    # 2. Xử lý POST (Gửi tin nhắn)
    if request.method == 'POST':
        return handle_chat_logic('ai')

    # 3. Xử lý GET (Hiển thị giao diện)
    # Khởi tạo session nếu chưa có
    if not current_user.current_session_id:
        current_user.current_session_id = str(uuid.uuid4())
        db.session.commit()
    
    session['chat_session_id'] = current_user.current_session_id
    session['thread_id'] = current_user.current_thread_id # Sync thread cũ
    
    # Lấy lịch sử chat
    history = Message.query.filter_by(
        user_id=current_user.id, 
        session_id=session['chat_session_id']
    ).order_by(Message.timestamp.asc()).all()

    return render_template('chatbot_ai.html', 
                           chat_history=history,
                           bot_name="POWER Coach (AI)", # Tên hiển thị trên UI
                           endpoint="/chatbot/ai")      # API endpoint cho JS

# --- CHATBOT GOFAI (PLACEBO) ---
@main.route('/chatbot/gofai', methods=['GET', 'POST'])
@login_required
def chatbot_gofai():
    # 1. Bảo mật: Chỉ cho phép Admin hoặc User loại 'gofai'
    if not current_user.is_admin and current_user.bot_type != 'gofai':
        flash("Bạn không có quyền truy cập bot này.", "danger")
        return redirect(url_for('main.chatbot_redirect'))

    # 2. Xử lý POST (Gửi tin nhắn)
    if request.method == 'POST':
        return handle_chat_logic('gofai')

    # 3. Xử lý GET (Hiển thị giao diện)
    if not current_user.current_session_id:
        current_user.current_session_id = str(uuid.uuid4())
        db.session.commit()
    
    session['chat_session_id'] = current_user.current_session_id
    session['thread_id'] = current_user.current_thread_id
    
    history = Message.query.filter_by(
        user_id=current_user.id, 
        session_id=session['chat_session_id']
    ).order_by(Message.timestamp.asc()).all()

    return render_template('chatbot_gofai.html', 
                           chat_history=history,
                           bot_name="POWER Biology (Basic)", # Tên hiển thị khác biệt
                           endpoint="/chatbot/gofai")        # API endpoint riêng

# Route Reset Session (Tiện ích)
@main.route('/reset_session')
@login_required
def reset_session():
    new_session_id = str(uuid.uuid4())
    # Tạo thread mới trên OpenAI để xóa ký ức ngắn hạn
    try:
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key:
            openai.api_key = api_key
            new_thread = openai.beta.threads.create()
            current_user.current_thread_id = new_thread.id
            session['thread_id'] = new_thread.id
    except Exception as e:
        print(f"Lỗi tạo thread mới: {e}")
    
    current_user.current_session_id = new_session_id
    db.session.commit()
    session['chat_session_id'] = new_session_id
    
    flash("Đã bắt đầu phiên học mới.", "success")
    return redirect(url_for('main.chatbot_redirect'))

# ==============================================================================
# 4. ROUTE ADMIN (QUẢN TRỊ)
# ==============================================================================

@main.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    reset_form = ResetPasswordForm()
    
    # Xử lý thêm 1 user
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
            next(csv_reader, None) # Skip header
            count = 0
            for row in csv_reader:
                if len(row) >= 3:
                    u_id, pwd, b_type = row[0], row[1], row[2]
                    if not User.query.filter_by(username=u_id.strip()).first():
                        new_u = User(username=u_id.strip(), bot_type=b_type.strip().lower())
                        new_u.set_password(pwd.strip())
                        db.session.add(new_u)
                        count += 1
            db.session.commit()
            flash(f'Đã thêm {count} tài khoản từ CSV.', 'success')
        except Exception as e:
            flash(f'Lỗi CSV: {e}', 'danger')
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
    
    # Query gộp cả Message và Log
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
