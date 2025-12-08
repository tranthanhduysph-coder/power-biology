import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from dotenv import load_dotenv

# Tải biến môi trường
load_dotenv()

# Khởi tạo Extension
db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = 'main.login'
login.login_message = 'Vui lòng đăng nhập.'

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-123'
    
    # Fix lỗi database URL trên Render
    uri = os.environ.get('DATABASE_URL') or 'sqlite:///site.db'
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
        
    SQLALCHEMY_DATABASE_URI = uri
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static/uploads')

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Tạo thư mục upload
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)

    # --- ĐĂNG KÝ USER LOADER (BẮT BUỘC ĐỂ FIX LỖI LOGIN) ---
    from app.models import User
    
    @login.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    # --- TỰ ĐỘNG TẠO BẢNG NẾU CHƯA CÓ (FIX LỖI 500 DB) ---
    with app.app_context():
        db.create_all()
        # Tự động tạo Admin mặc định nếu chưa có
        if not User.query.filter_by(username='admin').first():
            try:
                admin = User(username='admin', is_admin=True, bot_type='gofai')
                admin.set_password('admin123') # Mật khẩu admin mặc định
                db.session.add(admin)
                db.session.commit()
                print(">>> Đã tạo tài khoản Admin mặc định (Pass: admin123)")
            except:
                pass
    # -----------------------------------------------------

    from app.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    return app
