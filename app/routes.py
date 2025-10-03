from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from . import db
from .models import User
from .forms import LoginForm

main = Blueprint('main', __name__)

# --- ROUTE CỬA NGÕ ---
@main.route('/')
def index():
    """
    Đây là route quan trọng nhất. Khi người dùng truy cập vào trang chủ,
    nó sẽ tự động chuyển hướng họ đến trang đăng nhập.
    """
    return redirect(url_for('main.login'))

# --- ROUTE ĐĂNG NHẬP ---
@main.route('/login', methods=['GET', 'POST'])
def login():
    # Nếu người dùng đã đăng nhập, chuyển hướng họ đi luôn
    if current_user.is_authenticated:
        return redirect(url_for('main.chatbot_redirect'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            # Chuyển hướng đến trang tiếp theo sau khi đăng nhập thành công
            return redirect(url_for('main.chatbot_redirect'))
        else:
            flash('Đăng nhập thất bại. Vui lòng kiểm tra lại ID và mật khẩu.', 'danger')
            
    return render_template('login.html', form=form)

# --- ROUTE ĐĂNG XUẤT ---
@main.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.login'))

# --- ROUTE CHUYỂN HƯỚNG SAU ĐĂNG NHẬP ---
@main.route('/chatbot_redirect')
@login_required
def chatbot_redirect():
    # Tạm thời chúng ta sẽ bỏ qua trang admin để chẩn đoán
    # if current_user.is_admin:
    #     return redirect(url_for('main.admin_dashboard'))
    
    # Thay vào đó, chuyển thẳng đến trang chatbot (giả sử là 'ai')
    # Chúng ta sẽ sửa lại logic này sau
    return redirect(url_for('main.chatbot_ai'))

# --- ROUTE CHATBOT (GIẢ LẬP) ---
# Tạm thời tạo một trang chatbot đơn giản để kiểm tra
@main.route('/chatbot/ai')
@login_required
def chatbot_ai():
    return "<h1>Chào mừng bạn đến với Chatbot!</h1><p><a href='/logout'>Đăng xuất</a></p>"