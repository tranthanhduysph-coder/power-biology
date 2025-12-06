from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
import os
from dotenv import load_dotenv
from whitenoise import WhiteNoise

# Load env
load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

def create_app():
    # 1. Định nghĩa đường dẫn tĩnh (FIX LỖI 404 CSS)
    # Lấy đường dẫn gốc của dự án trên Server
    project_root = os.getcwd() 
    # Trỏ thẳng vào app/static
    static_path = os.path.join(project_root, 'app/static')
    
    app = Flask(__name__, static_folder=static_path)
    
    # 2. Cấu hình WhiteNoise (Phục vụ file tĩnh)
    # root=static_path: Bảo WhiteNoise tìm file ở đúng chỗ này
    # prefix='static/': Đường dẫn trên URL sẽ là /static/css/style.css
    app.wsgi_app = WhiteNoise(app.wsgi_app, root=static_path, prefix='static/')

    # 3. Secret Key
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'key_du_phong_123456')

    # 4. Cấu hình Database Bền Vững (FIX LỖI MẤT HISTORY)
    # Render Disk luôn mount tại /var/data
    disk_path = '/var/data'
    
    # Ưu tiên dùng Disk trên Render
    if os.path.exists(disk_path):
        db_path = os.path.join(disk_path, 'site.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
        print(f"--- [INFO] SỬ DỤNG DATABASE TRÊN DISK: {db_path} ---")
    else:
        # Fallback cho Localhost
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
        print("--- [INFO] SỬ DỤNG DATABASE LOCAL (SẼ MẤT KHI DEPLOY) ---")

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # 5. Cấu hình Upload
    UPLOAD_FOLDER = os.path.join(static_path, 'uploads')
    if not os.path.exists(UPLOAD_FOLDER):
        try:
            os.makedirs(UPLOAD_FOLDER)
        except: pass # Bỏ qua nếu lỗi quyền (hiếm gặp)
        
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    # 6. Init Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    login_manager.login_message_category = 'info'

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from .routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    # 7. Tự động tạo bảng & Admin
    with app.app_context():
        try:
            db.create_all()
            # Tạo admin nếu chưa có
            if not User.query.filter_by(username='admin').first():
                admin = User(username='admin', bot_type='ai', is_admin=True)
                admin.set_password('admin123')
                db.session.add(admin)
                db.session.commit()
                print("--- [SUCCESS] ĐÃ KHÔI PHỤC ADMIN (admin/admin123) ---")
        except Exception as e:
            print(f"--- [ERROR] DB ERROR: {e} ---")

    return app
