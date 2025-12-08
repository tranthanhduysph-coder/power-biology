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

# --- XỬ LÝ CHAT (LOGIC QUAN TRỌNG) ---
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
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            file_html = f'<br><img src="/static/uploads/{filename}" style="max-width:200px; border-radius:10px;">'
        else:
            file_html = f'<br><a href="/static/uploads/{filename}" target="_blank">File: {filename}</a>'

    if not user_text and not file: return jsonify({'response': ""}), 400

    # 1. Lưu tin nhắn User (Giờ VN)
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
            
            # Xử lý phần JSON để lưu log vào bảng VariableLog
            json_str = parts[1].split("```")[0].replace("LOG_DATA =", "").strip()
            if json_str:
                data = json.loads(json_str)
                for k, v in data.items():
                    db.session.add(VariableLog(user_id=current_user.id, session_id=sess_id, variable_name=str(k), variable_value=str(v), timestamp=get_vietnam_time()))
    except:
        pass 

    # 4. CHỈ LƯU PHẦN TEXT SẠCH VÀO DATABASE (ui_text)
    # Đây là bước quyết định để Admin Log không thấy JSON và F5 không bị lỗi hiển thị
    bot_msg = Message(sender='assistant', content=ui_text, author=current_user, session_id=sess_id, timestamp=get_vietnam_time())
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
    # Lấy danh sách session
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

@main.route('/disclaimer')
def disclaimer(): return render_template('disclaimer.html')

# --- ADMIN DASHBOARD ---
@main.route('/admin', methods=['GET'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    reset_form = ResetPasswordForm()
    users = User.query.filter_by(is_admin=False).all()
    return render_template('admin_dashboard.html', users=users, user_form=user_form, upload_form=upload_form, reset_form=reset_form)

# --- ADMIN: TẠO 1 USER ---
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
            flash('Thêm thành công!', 'success')
        else: flash('User đã tồn tại.', 'danger')
    else: flash('Dữ liệu lỗi.', 'warning')
    return redirect(url_for('main.admin_dashboard'))

# --- ADMIN: UPLOAD CSV (FIX CỨNG 500) ---
@main.route('/admin/upload_csv', methods=['POST'])
@login_required
@admin_required
def batch_create_users():
    form = UploadCSVForm()
    if form.validate_on_submit() and form.csv_file.data:
        try:
            file = form.csv_file.data
            file.seek(0)
            file_content = file.read() # Đọc toàn bộ vào RAM
            
            # Thử decode
            text_content = None
            for enc in ['utf-8-sig', 'utf-8', 'cp1252', 'latin-1']:
                try: text_content = file_content.decode(enc); break
                except: continue
            
            if not text_content: raise ValueError("Lỗi bảng mã file.")

            stream = io.StringIO(text_content)
            # Tự động phát hiện dấu phân cách
            delimiter = ';' if ';' in text_content.splitlines()[0] else ','
            csv_reader = csv.reader(stream, delimiter=delimiter)
            
            try: next(csv_reader, None) # Bỏ header
            except: pass

            count = 0
            for row in csv_reader:
                if not row or len(row) < 3: continue
                
                # --- LOGIC THÔNG MINH: NHẬN DIỆN 3 CỘT HAY 5 CỘT ---
                if len(row) >= 5: # File kiểu NguyenVanCu (Name, Class, User, Pass, Type)
                    r_user = row[2].strip()
                    r_pass = row[3].strip()
                    raw_type = row[4].strip().lower()
                else: # File kiểu mẫu (User, Pass, Type)
                    r_user = row[0].strip()
                    r_pass = row[1].strip()
                    raw_type = row[2].strip().lower()

                if not r_user: continue
                if not r_pass: r_pass = "123456"

                # Mapping bot
                if 'ai' in raw_type or 'coach' in raw_type: r_type = 'ai'
                elif 'gofai' in raw_type or 'basic' in raw_type: r_type = 'gofai'
                else: r_type = 'gofai'

                if not User.query.filter_by(username=r_user).first():
                    nu = User(username=r_user, bot_type=r_type)
                    nu.set_password(r_pass)
                    db.session.add(nu)
                    count += 1
            
            db.session.commit()
            flash(f'Đã thêm {count} học sinh.', 'success')
        except Exception as e:
            db.session.rollback()
            print(f"CSV ERROR: {e}")
            flash(f'Lỗi xử lý file: {str(e)}', 'danger')
            
    return redirect(url_for('main.admin_dashboard'))

# --- ADMIN: XÓA ĐÃ CHỌN ---
@main.route('/admin/delete_selected', methods=['POST'])
@login_required
@admin_required
def delete_selected_users():
    ids = request.form.getlist('user_ids')
    if not ids:
        flash('Chưa chọn user.', 'warning')
        return redirect(url_for('main.admin_dashboard'))
    try:
        c = 0
        for uid in ids:
            u = User.query.get(int(uid))
            if u and not u.is_admin:
                Message.query.filter_by(user_id=u.id).delete()
                VariableLog.query.filter_by(user_id=u.id).delete()
                db.session.delete(u)
                c += 1
        db.session.commit()
        flash(f'Đã xóa {c} user.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi xóa: {str(e)}', 'danger')
    return redirect(url_for('main.admin_dashboard'))

# --- CÁC ROUTE KHÁC ---
@main.route('/admin/delete/<int:user_id>')
@login_required
@admin_required
def delete_user(user_id):
    u = User.query.get_or_404(user_id)
    if u.id != current_user.id: db.session.delete(u); db.session.commit()
    return redirect(url_for('main.admin_dashboard'))

@main.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reset_student_password(user_id):
    u = User.query.get_or_404(user_id); form = ResetPasswordForm()
    if form.validate_on_submit(): u.set_password(form.new_password.data); db.session.commit(); flash('Reset OK', 'success')
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
    cw.writerow(['Time (GMT+7)','Session','User','Type','Content'])
    msgs = db.session.query(Message, User).join(User).order_by(Message.timestamp.desc()).all()
    for m, u in msgs:
        t = m.timestamp.strftime('%Y-%m-%d %H:%M:%S') if m.timestamp else ""
        cw.writerow([t, m.session_id, u.username, m.sender, m.content])
    return Response(si.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=data.csv"})

@main.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data): current_user.set_password(form.new_password.data); db.session.commit(); flash('Đổi pass thành công', 'success'); return redirect(url_for('main.chatbot_redirect'))
        flash('Sai mật khẩu cũ', 'danger')
    return render_template('change_password.html', form=form)
