# --- 1. KHAI BÁO THƯ VIỆN ĐẦY ĐỦ ---
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response, current_app
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from sqlalchemy import func, desc
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
import openai, csv, io, uuid, time, json, os

# --- 2. KHỞI TẠO BLUEPRINT ---
main = Blueprint('main', __name__)

# --- 3. CÁC HÀM HỖ TRỢ (HELPERS) ---

def allowed_file(filename):
    # Hỗ trợ ảnh và tài liệu
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
    """Gửi tin nhắn đến OpenAI Assistant và nhận phản hồi"""
    try:
        api_key = os.environ.get('OPENAI_API_KEY')
        assistant_id = os.environ.get('CHATBOT_AI_ID') if bot_type == 'ai' else os.environ.get('CHATBOT_GOFAI_ID')
        
        if not api_key or not assistant_id: return "Lỗi: Chưa cấu hình API Key hoặc Assistant ID."
        
        openai.api_key = api_key
        client = openai
        
        # Quản lý Thread (Mỗi user 1 thread cho phiên hiện tại)
        thread_id = current_user.current_thread_id
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            current_user.current_thread_id = thread_id
            db.session.commit()
        
        # Gửi tin nhắn
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_message)
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant_id)
        
        # Polling chờ kết quả
        while run.status in ['queued', 'in_progress']:
            time.sleep(0.5)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            
        if run.status == 'completed':
            msgs = client.beta.threads.messages.list(thread_id=thread_id)
            return msgs.data[0].content[0].text.value
        
        return f"AI Error Status: {run.status}"
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return "Hệ thống đang bận hoặc gặp lỗi kết nối AI."

def handle_chat_logic(bot_type_check):
    """Xử lý logic chat: Lưu tin nhắn, gọi AI, tách JSON log"""
    if not current_user.is_admin and current_user.bot_type != bot_type_check:
        return jsonify({'response': "Lỗi quyền truy cập loại Bot này."}), 403

    user_text = request.form.get('user_input', '').strip()
    file = request.files.get('file')
    
    # Tạo session mới nếu chưa có
    sess_id = current_user.current_session_id
    if not sess_id:
        sess_id = str(uuid.uuid4())
        current_user.current_session_id = sess_id
        db.session.commit()

    file_html = ""
    file_msg = ""

    # Xử lý file upload
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(save_path)
        web_path = f"/static/uploads/{filename}"
        file_msg = f"\n[User uploaded file: {filename}]"
        
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            file_html = f'<br><img src="{web_path}" style="max-width:200px; border-radius:10px; margin-top:5px;">'
        else:
            file_html = f'<br><a href="{web_path}" target="_blank" class="file-link"><i class="fas fa-paperclip"></i> {filename}</a>'

    if not user_text and not file: return jsonify({'response': ""}), 400

    # 1. Lưu tin nhắn User vào DB
    user_msg = Message(sender='user', content=user_text + file_html, author=current_user, session_id=sess_id)
    db.session.add(user_msg)

    # 2. Gọi AI
    full_resp = get_assistant_response(user_text + file_msg, bot_type_check)

    # 3. Xử lý JSON Log (Tách điểm số/phase để lưu DB)
    ui_text = full_resp
    try:
        if "```json" in full_resp:
            parts = full_resp.split("```json")
            ui_text = parts[0].strip() # Phần hiển thị cho user (đã cắt JSON)
            
            # Lấy phần JSON để lưu Log
            json_str = parts[1].split("```")[0].replace("LOG_DATA =", "").strip()
            if json_str:
                data = json.loads(json_str)
                for k, v in data.items():
                    db.session.add(VariableLog(user_id=current_user.id, session_id=sess_id, variable_name=str(k), variable_value=str(v)))
    except Exception as e:
        print(f"JSON Parsing Error: {e}") 
        # Nếu lỗi parse JSON, vẫn hiện tin nhắn bình thường, không crash app

    # 4. Lưu tin nhắn Bot (chứa cả JSON để sau này debug nếu cần, hoặc chỉ ui_text tùy ý)
    # Ở đây ta lưu full_resp để giữ lịch sử gốc, frontend sẽ dùng JS cleanJSON để ẩn đi.
    bot_msg = Message(sender='assistant', content=full_resp, author=current_user, session_id=sess_id)
    db.session.add(bot_msg)
    db.session.commit()

    return jsonify({'response': full_resp})

# --- 4. CÁC ROUTE ĐIỀU HƯỚNG CHÍNH ---

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
        flash('Sai tên đăng nhập hoặc mật khẩu.', 'danger')
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

# --- 5. LOGIC QUẢN LÝ SESSION (LỊCH SỬ CHAT) ---

def get_user_sessions():
    """Lấy danh sách session cũ để hiện lên Sidebar"""
    return db.session.query(
        Message.session_id,
        func.max(Message.timestamp).label('last_active')
    ).filter_by(user_id=current_user.id).group_by(Message.session_id).order_by(desc('last_active')).all()

def render_chat_page(bot_type, bot_name):
    if not current_user.is_admin and current_user.bot_type != bot_type:
        return redirect(url_for('main.chatbot_redirect'))
    
    if request.method == 'POST': return handle_chat_logic(bot_type)

    # Đảm bảo có session
    sess_id = current_user.current_session_id
    if not sess_id:
        sess_id = str(uuid.uuid4())
        current_user.current_session_id = sess_id
        db.session.commit()
    
    # Lấy tin nhắn
    hist = Message.query.filter_by(user_id=current_user.id, session_id=sess_id).order_by(Message.timestamp.asc()).all()
    
    # Lấy danh sách sidebar
    all_sessions = get_user_sessions()
    session_list = []
    for s_id, s_time in all_sessions:
        display_name = s_time.strftime('%d/%m %H:%M')
        session_list.append({
            'id': s_id,
            'name': f"Hội thoại {display_name}",
            'active': (s_id == sess_id)
        })

    return render_template('chatbot_layout.html', 
                           chat_history=hist, 
                           bot_name=bot_name, 
                           endpoint=f"/chatbot/{bot_type}",
                           session_list=session_list)

@main.route('/chatbot/ai', methods=['GET', 'POST'])
@login_required
def chatbot_ai(): return render_chat_page('ai', "AI Coach (POWER)")

@main.route('/chatbot/gofai', methods=['GET', 'POST'])
@login_required
def chatbot_gofai(): return render_chat_page('gofai', "Basic Bot")

@main.route('/new_chat')
@login_required
def new_chat():
    current_user.current_session_id = str(uuid.uuid4())
    # Reset thread AI để bắt đầu ngữ cảnh mới
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
    exists = Message.query.filter_by(user_id=current_user.id, session_id=session_id).first()
    if exists:
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

# --- 6. ADMIN DASHBOARD & XỬ LÝ CSV (FIXED 500 ERROR) ---

@main.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    reset_form = ResetPasswordForm()

    # --- 6A. Xử lý Thêm Thủ công ---
    if user_form.validate_on_submit():
        if not User.query.filter_by(username=user_form.username.data).first():
            u = User(username=user_form.username.data, bot_type=user_form.bot_type.data, is_admin=user_form.is_admin.data)
            u.set_password(user_form.password.data)
            db.session.add(u)
            db.session.commit()
            flash('Thêm thành công', 'success')
        else:
            flash('Tên đăng nhập đã tồn tại', 'danger')
        return redirect(url_for('main.admin_dashboard'))

    # --- 6B. Xử lý Upload CSV (READ METHOD - ROBUST) ---
    if upload_form.validate_on_submit() and upload_form.csv_file.data:
        try:
            file = upload_form.csv_file.data
            
            # BƯỚC 1: Đưa con trỏ về đầu
            file.seek(0)
            
            # BƯỚC 2: Đọc toàn bộ Bytes vào RAM
            content_bytes = file.read()
            
            # BƯỚC 3: Giải mã thông minh (3 lớp)
            text_content = ""
            try:
                text_content = content_bytes.decode('utf-8-sig') # Excel
            except UnicodeDecodeError:
                try:
                    text_content = content_bytes.decode('cp1252') # Windows
                except:
                    text_content = content_bytes.decode('latin-1') # Fallback

            # BƯỚC 4: Parse CSV từ String
            stream = io.StringIO(text_content)
            csv_input = csv.reader(stream)
            
            # Bỏ qua Header
            try:
                next(csv_input, None)
            except: pass

            count = 0
            for row in csv_input:
                if not row or len(row) < 1: continue
                
                # Làm sạch dữ liệu
                r_user = row[0].strip()
                if not r_user: continue
                
                r_pass = row[1].strip() if len(row) > 1 and row[1].strip() else "123456"
                
                # Map dữ liệu bot_type (AI Coach -> ai, Basic Bot -> gofai)
                raw_type = row[2].strip().lower() if len(row) > 2 and row[2].strip() else "gofai"
                
                if 'ai' in raw_type: 
                    r_type = 'ai'
                elif 'basic' in raw_type or 'gofai' in raw_type:
                    r_type = 'gofai'
                else:
                    r_type = 'gofai' # Default
                
                # Check trùng
                if not User.query.filter_by(username=r_user).first():
                    u = User(username=r_user, bot_type=r_type)
                    u.set_password(r_pass)
                    db.session.add(u)
                    count += 1
            
            db.session.commit()
            
            if count > 0:
                flash(f'Thành công! Đã thêm {count} học sinh.', 'success')
            else:
                flash('File rỗng hoặc tất cả user đã tồn tại.', 'warning')
                
        except Exception as e:
            db.session.rollback() # Cứu DB
            print(f"CSV ERROR: {e}")
            flash(f'Lỗi xử lý file: {str(e)}', 'danger')
            
        return redirect(url_for('main.admin_dashboard'))

    # Load danh sách user
    users = User.query.filter_by(is_admin=False).all()
    return render_template('admin_dashboard.html', users=users, user_form=user_form, upload_form=upload_form, reset_form=reset_form)

@main.route('/admin/delete/<int:user_id>')
@login_required
@admin_required
def delete_user(user_id):
    u = User.query.get_or_404(user_id)
    if u.id != current_user.id:
        db.session.delete(u); db.session.commit()
        flash('Đã xóa.', 'success')
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
    cw.writerow(['Time','Session','User','Type','Content'])
    msgs = db.session.query(Message, User).join(User).all()
    for m, u in msgs: cw.writerow([m.timestamp, m.session_id, u.username, m.sender, m.content])
    return Response(si.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=chat_history.csv"})

@main.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reset_student_password(user_id):
    u = User.query.get_or_404(user_id); form = ResetPasswordForm()
    if form.validate_on_submit():
        u.set_password(form.new_password.data); db.session.commit()
        flash('Reset pass thành công', 'success')
    return redirect(url_for('main.admin_dashboard'))

@main.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            current_user.set_password(form.new_password.data); db.session.commit()
            flash('Đổi pass thành công', 'success')
            return redirect(url_for('main.chatbot_redirect'))
        flash('Sai pass cũ', 'danger')
    return render_template('change_password.html', form=form)
