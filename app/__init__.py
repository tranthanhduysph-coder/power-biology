from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
import os
from dotenv import load_dotenv
# THÊM DÒNG NÀY
from whitenoise import WhiteNoise

# Load env
load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

def create_app():
    app = Flask(__name__)
    
    # 1. Secret Key
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_key_cho_dev_12345')

    # 2. Cấu hình Database (Logic thông minh)
    disk_path = '/var/data'
    render_db_url = os.environ.get('DATABASE_URL')
    
    if os.path.exists(disk_path):
        print(f"--- [INFO] PHÁT HIỆN DISK TẠI {disk_path}. SỬ DỤNG SQLITE BỀN VỮNG. ---")
        db_file = os.path.join(disk_path, 'site.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_file}'
    elif render_db_url:
        print("--- [INFO] SỬ DỤNG DATABASE_URL (POSTGRESQL). ---")
        if render_db_url.startswith("postgres://"):
            render_db_url = render_db_url.replace("postgres://", "postgresql://", 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = render_db_url
    else:
        print("--- [WARNING] CHẠY SQLITE TẠM THỜI. ---")
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # 3. Cấu hình Upload
    UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    # --- CẤU HÌNH STATIC FILES (FIX LỖI CSS 404) ---
    # Ép Flask phục vụ static file thông qua WhiteNoise
    app.wsgi_app = WhiteNoise(app.wsgi_app, root=os.path.join(app.root_path, 'static'), prefix='static/')
    # -----------------------------------------------

    # 4. Init Extensions
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

    # 5. Tự động tạo bảng & Admin
    with app.app_context():
        try:
            db.create_all()
            if not User.query.filter_by(username='admin').first():
                admin = User(username='admin', bot_type='ai', is_admin=True)
                admin.set_password('admin123')
                db.session.add(admin)
                db.session.commit()
                print("--- [SUCCESS] ĐÃ TẠO ADMIN MẶC ĐỊNH ---")
        except Exception as e:
            print(f"--- [ERROR] DB ERROR: {e} ---")

    return app
