from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
import os
from dotenv import load_dotenv
from whitenoise import WhiteNoise

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

def create_app():
    # 1. Tự động xác định đường dẫn thư mục 'app' và 'static'
    app_dir = os.path.dirname(__file__)  # Đường dẫn đến folder 'app'
    static_dir = os.path.join(app_dir, 'static') # Đường dẫn đến 'app/static'
    
    # Khởi tạo Flask với đường dẫn tĩnh tuyệt đối
    app = Flask(__name__, static_folder=static_dir)

    # 2. Cấu hình WhiteNoise (BẮT BUỘC ĐỂ FIX 404 CSS)
    app.wsgi_app = WhiteNoise(app.wsgi_app, root=static_dir, prefix='static/')

    # 3. Secret Key
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_key_123')

    # 4. Database Bền vững (Disk)
    disk_path = '/var/data'
    if os.path.exists(disk_path):
        db_path = os.path.join(disk_path, 'site.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
        print(f"--- DISK FOUND: Using {db_path} ---")
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
        print("--- NO DISK: Using local sqlite ---")

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # 5. Upload Folder
    upload_dir = os.path.join(static_dir, 'uploads')
    if not os.path.exists(upload_dir):
        os.makedirs(upload_dir, exist_ok=True)
        
    app.config['UPLOAD_FOLDER'] = upload_dir
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

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

    # Tự động tạo bảng và admin
    with app.app_context():
        try:
            db.create_all()
            if not User.query.filter_by(username='admin').first():
                u = User(username='admin', bot_type='ai', is_admin=True)
                u.set_password('admin123')
                db.session.add(u); db.session.commit()
                print("--- ADMIN CREATED ---")
        except: pass

    return app
