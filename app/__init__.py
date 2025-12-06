from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
import os
from dotenv import load_dotenv

# Load biến môi trường
load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

def create_app():
    app = Flask(__name__)
    
    # --- 1. CẤU HÌNH SECRET KEY ---
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_secret_key_cho_dev')

    # --- 2. CẤU HÌNH DATABASE (FIX LỖI RENDER 500) ---
    # Lấy URL từ biến môi trường của Render
    db_url = os.environ.get('DATABASE_URL')

    if db_url:
        # Fix lỗi: SQLAlchemy yêu cầu 'postgresql://' nhưng Render trả về 'postgres://'
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    else:
        # Fallback về SQLite nếu chạy ở máy local (hoặc quên set Env trên Render)
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # --- 3. CẤU HÌNH UPLOAD ---
    UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    # --- 4. KHỞI TẠO EXTENSIONS ---
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    login_manager.login_message_category = 'info'

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # --- 5. ĐĂNG KÝ BLUEPRINT ---
    from .routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    # --- 6. TỰ ĐỘNG TẠO BẢNG (FIX LỖI BẢNG KHÔNG TỒN TẠI) ---
    # Đoạn này cực quan trọng trên Render để tránh lỗi 500 khi chưa chạy migrate
    with app.app_context():
        db.create_all()
        # (Tùy chọn) Tạo admin mặc định nếu chưa có
        if not User.query.filter_by(username='admin').first():
            try:
                admin = User(username='admin', bot_type='ai', is_admin=True)
                admin.set_password('admin123') # Mật khẩu mặc định
                db.session.add(admin)
                db.session.commit()
                print(">>> Đã tạo tài khoản admin mặc định: admin / admin123")
            except Exception as e:
                print(f">>> Lỗi tạo admin: {e}")

    return app
