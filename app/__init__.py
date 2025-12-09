import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

# Tải biến môi trường
load_dotenv()

# Khởi tạo Extension
db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = 'main.login'
login.login_message = 'Vui lòng đăng nhập.'

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-123456'
    
    # --- CẤU HÌNH LƯU TRỮ VĨNH VIỄN (DISK) ---
    # Kiểm tra xem có ổ cứng gắn tại /var/data không (Render thường mount vào đây)
    if os.path.exists('/var/data'):
        print(">>> PHÁT HIỆN Ổ CỨNG (/var/data). SỬ DỤNG DATABASE VĨNH VIỄN.")
        # Lưu file site.db vào ổ cứng thuê
        SQLALCHEMY_DATABASE_URI = 'sqlite:////var/data/site.db'
        # Lưu ảnh upload vào ổ cứng thuê
        UPLOAD_FOLDER = '/var/data/uploads'
    else:
        print(">>> KHÔNG THẤY Ổ CỨNG. CHẠY CHẾ ĐỘ TẠM THỜI (MẤT DỮ LIỆU KHI RESET).")
        # Chạy local hoặc server không có disk
        uri = os.environ.get('DATABASE_URL') or 'sqlite:///site.db'
        if uri.startswith("postgres://"):
            uri = uri.replace("postgres://", "postgresql://", 1)
        SQLALCHEMY_DATABASE_URI = uri
        UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static/uploads')
        
    SQLALCHEMY_TRACK_MODIFICATIONS = False

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Tạo thư mục upload (dù ở local hay trong disk)
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        try:
            os.makedirs(app.config['UPLOAD_FOLDER'])
        except Exception as e:
            print(f"Lỗi tạo thư mục upload: {e}")

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)

    # Đăng ký User Loader
    from app.models import User
    @login.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    # Tự động tạo bảng và Admin nếu chưa có
    with app.app_context():
        db.create_all()
        
        # Chỉ tạo Admin nếu chưa tồn tại
        if not User.query.filter_by(username='admin').first():
            print(">>> Tạo tài khoản Admin mặc định...")
            try:
                admin = User(username='admin', bot_type='gofai', is_admin=True)
                # Dùng generate_password_hash thay vì set_password nếu model chưa có method
                admin.password_hash = generate_password_hash('123456')
                db.session.add(admin)
                db.session.commit()
                print(">>> Đã tạo Admin: admin / 123456")
            except Exception as e:
                print(f">>> Lỗi tạo Admin: {e}")

    from app.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    return app
