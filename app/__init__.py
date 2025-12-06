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
    # 1. Định nghĩa đường dẫn tuyệt đối (FIX LỖI 404 CSS)
    # Lấy đường dẫn của file __init__.py hiện tại, đi vào thư mục 'static'
    basedir = os.path.abspath(os.path.dirname(__file__))
    static_folder_path = os.path.join(basedir, 'static')
    
    app = Flask(__name__, static_folder=static_folder_path)
    
    # 2. Secret Key & Database
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_key_123')
    
    disk_path = '/var/data'
    render_db_url = os.environ.get('DATABASE_URL')
    
    if os.path.exists(disk_path):
        db_file = os.path.join(disk_path, 'site.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_file}'
    elif render_db_url:
        if render_db_url.startswith("postgres://"):
            render_db_url = render_db_url.replace("postgres://", "postgresql://", 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = render_db_url
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # 3. Cấu hình Upload
    UPLOAD_FOLDER = os.path.join(static_folder_path, 'uploads')
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    # --- FIX LỖI CSS: Kích hoạt WhiteNoise ---
    # root=static_folder_path: Chỉ định chính xác thư mục static nằm ở đâu
    app.wsgi_app = WhiteNoise(app.wsgi_app, root=static_folder_path, prefix='static/')
    # ---------------------------------------

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

    with app.app_context():
        try:
            db.create_all()
            if not User.query.filter_by(username='admin').first():
                admin = User(username='admin', bot_type='ai', is_admin=True)
                admin.set_password('admin123')
                db.session.add(admin)
                db.session.commit()
        except: pass

    return app
