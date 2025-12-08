from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response, current_app
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from sqlalchemy import func, desc
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
import openai, csv, io, uuid, time, json, os, traceback
from datetime import datetime, timedelta

main = Blueprint('main', __name__)

# --- HÀM HỖ TRỢ ---
def get_vietnam_time():
    return datetime.utcnow() + timedelta(hours=7)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'docx', 'doc', 'txt'}

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Cần quyền Admin.", "danger")
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)
    return decorated_function

# --- GỌI OPENAI ---
def get_assistant_response(user_message, bot_type):
    try:
        api_key = os.environ.get('OPENAI_API_KEY')
        assistant_id = os.environ.get('CHATBOT_AI_ID') if bot_type == 'ai' else os.environ.get('CHATBOT_GOFAI_ID')
        
        if not api_key or not assistant_id: return "Lỗi: Chưa cấu hình API Key."
        
        openai.api_key = api_key
        client = openai
        
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
        return "AI không phản hồi."
    except Exception as e:
        print(f"AI Error: {e}")
        return "Hệ thống bận."

# --- XỬ LÝ CHAT (LOGIC QUAN TRỌNG NHẤT) ---
def handle_chat_logic(bot_type_check):
    if not current_user.is_admin and current_user.bot_type != bot_type_check:
        return jsonify({'response': "Sai loại bot."}), 403

    user_text = request.form.get('user_input', '').strip()
    file = request.files.get('file')
    
    sess_id = current_user.current_session_id
    if not sess_id:
        sess_id = str(uuid.uuid4())
        current_user.current_session_id = sess_id
        db.session.commit()

    file_html = ""
    file_msg = ""
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(save_path)
        file_msg = f"\n[User uploaded: {filename}]"
        # Hiển thị ảnh hoặc icon file
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            file_html = f'<br><img src="/static/uploads/{filename}" style="max-width:200px; border-radius:10px;">'
        else:
            file_html = f'<br><a href="/static/uploads/{filename}" target="_blank">File: {filename}</a>'

    if not user_text and not file: return jsonify({'response': ""}), 400

    # 1. Lưu tin nhắn User
    user_msg = Message(sender='user', content=user_text + file_html, author=current_user, session_id=sess_id, timestamp=get_vietnam_time())
    db.session.add(user_msg)

    # 2. Gọi AI lấy phản hồi gốc
    full_resp = get_assistant_response(user_text + file_msg, bot_type_check)

    # 3. Tách JSON ra khỏi phản hồi
    ui_text = full_resp # Mặc định là toàn bộ
    try:
        if "```json" in full_resp:
            parts = full_resp.split("```json")
            ui_text = parts[0].strip() # ĐÂY LÀ PHẦN SẠCH ĐỂ HIỂN THỊ
            
            # Xử lý phần JSON để lưu log
            json_str = parts[1].split("```")[0].replace("LOG_DATA =", "").strip()
            if json_str:
                data = json.loads(json_str)
                for k, v in data.items():
                    db.session.add(VariableLog(user_id=current_user.id, session_id=sess_id, variable_name=str(k), variable_value=str(v), timestamp=get_vietnam_time()))
    except:
        pass # Nếu lỗi tách JSON thì thôi, vẫn hiện text

    # 4. CHỈ LƯU PHẦN TEXT SẠCH VÀO DATABASE (Fix triệt để vấn đề hiển thị lại JSON)
    bot_msg = Message(sender='assistant', content=ui_text, author=current_user, session_id=sess_id, timestamp=get_vietnam_time())
    db.session.add(bot_msg)
    db.session.commit()

    return jsonify({'response': ui_text})

# --- ROUTE ---
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
        flash('Sai thông tin.', 'danger')
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('main.login'))

@main.route('/chatbot_redirect')
@login_required
def chatbot_redirect():
    if current_user.is_admin: return redirect(url_for('main.admin_dashboard'))
    return redirect(url_for(f'main.chatbot_{current_user.bot_type}'))

def render_chat_page(bot_type, bot_name):
    if not current_user.is_admin and current_user.bot_type != bot_type: return redirect(url_for('main.chatbot_redirect'))
    if request.method == 'POST': return handle_chat_logic(bot_type)
    
    sess_id = current_user.current_session_id
    if not sess_id: sess_id = str(uuid.uuid4()); current_user.current_session_id = sess_id; db.session.commit()
    
    hist = Message.query.filter_by(user_id=current_user.id, session_id=sess_id).order_by(Message.timestamp.asc()).all()
    # Lấy danh sách session cho sidebar
    sessions = db.session.query(Message.session_id, func.max(Message.timestamp)).filter_by(user_id=current_user.id).group_by(Message.session_id).order_by(desc(func.max(Message.timestamp))).all()
    session_list = [{'id': s[0], 'name': s[1].strftime('%d/%m %H:%M'), 'active': s[0]==sess_id} for s in sessions]

    return render_template('chatbot_layout.html', chat_history=hist, bot_name=bot_name, endpoint=f"/chatbot/{bot_type}", session_list=session_list)

@main.route('/chatbot/ai', methods=['GET', 'POST'])
@login_required
def chatbot_ai(): return render_chat_page('ai', "AI Coach")

@main.route('/chatbot/gofai', methods=['GET', 'POST'])
@login_required
def chatbot_gofai(): return render_chat_page('gofai', "Basic Bot")

@main.route('/new_chat')
@login_required
def new_chat():
    current_user.current_session_id = str(uuid.uuid4())
    # Reset Thread AI
    try: openai.api_key = os.environ.get('OPENAI_API_KEY'); current_user.current_thread_id = openai.beta.threads.create().id
    except: pass
    db.session.commit()
    return redirect(url_for('main.chatbot_redirect'))

@main.route('/switch_session/<session_id>')
@login_required
def switch_session(session_id):
    current_user.current_session_id = session_id; db.session.commit()
    return redirect(url_for('main.chatbot_redirect'))

@main.route('/delete_session/<session_id>')
@login_required
def delete_session(session_id):
    Message.query.filter_by(user_id=current_user.id, session_id=session_id).delete()
    if current_user.current_session_id == session_id: return redirect(url_for('main.new_chat'))
    db.session.commit()
    return redirect(url_for('main.chatbot_redirect'))

# --- ADMIN: LOGIC CŨ, CHỈ SỬA CSV ---
@main.route('/admin', methods=['GET'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    users = User.query.filter_by(is_admin=False).all()
    return render_template('admin_dashboard.html', users=users, user_form=user_form, upload_form=upload_form)

@main.route('/admin/create_user', methods=['POST'])
@login_required
@admin_required
def create_single_user():
    form = UserForm()
    if form.validate_on_submit():
        if not User.query.filter_by(username=form.username.data).first():
            u = User(username=form.username.data, bot_type=form.bot_type.data, is_admin=form.is_admin.data)
            u.set_password(form.password.data)
            db.session.add(u); db.session.commit()
            flash('Thêm thành công', 'success')
        else: flash('User đã tồn tại', 'danger')
    return redirect(url_for('main.admin_dashboard'))

@main.route('/admin/upload_csv', methods=['POST'])
@login_required
@admin_required
def batch_create_users():
    form = UploadCSVForm()
    if form.validate_on_submit() and form.csv_file.data:
        try:
            file = form.csv_file.data
            file.seek(0)
            # Đọc file bất chấp định dạng
            content = file.read().decode('utf-8-sig', errors='ignore')
            
            stream = io.StringIO(content)
            csv_reader = csv.reader(stream)
            try: next(csv_reader, None) # Bỏ header
            except: pass

            count = 0
            for row in csv_reader:
                if not row or len(row) < 3: continue
                
                u = row[0].strip()
                if not u: continue
                p = row[1].strip() or "123456"
                
                # Logic Map Bot
                raw_type = row[2].strip().lower()
                b = 'ai' if ('ai' in raw_type or 'coach' in raw_type) and 'gofai' not in raw_type else 'gofai'
                if 'gofai' in raw_type: b = 'gofai'

                if not User.query.filter_by(username=u).first():
                    nu = User(username=u, bot_type=b)
                    nu.set_password(p)
                    db.session.add(nu)
                    count += 1
            
            db.session.commit()
            flash(f'Đã thêm {count} user.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi CSV: {str(e)}', 'danger')
    return redirect(url_for('main.admin_dashboard'))

@main.route('/admin/delete_selected', methods=['POST'])
@login_required
@admin_required
def delete_selected_users():
    ids = request.form.getlist('user_ids')
    for uid in ids:
        u = User.query.get(int(uid))
        if u and not u.is_admin:
            Message.query.filter_by(user_id=u.id).delete()
            VariableLog.query.filter_by(user_id=u.id).delete()
            db.session.delete(u)
    db.session.commit()
    flash(f'Đã xóa {len(ids)} user.', 'success')
    return redirect(url_for('main.admin_dashboard'))

# Các route khác giữ nguyên...
@main.route('/admin/delete/<int:user_id>')
@login_required
@admin_required
def delete_user(user_id):
    u = User.query.get_or_404(user_id)
    if u.id != current_user.id: db.session.delete(u); db.session.commit()
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
