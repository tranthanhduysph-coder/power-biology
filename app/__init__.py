from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from dotenv import load_dotenv
import os

# Tải biến môi trường
load_dotenv()

# Khởi tạo các extension
db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = 'main.login'
login.login_message = 'Vui lòng đăng nhập.'

# Cấu hình trực tiếp (Tránh lỗi thiếu file config.py)
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-123'
    # Fix lỗi database URL của Render
    uri = os.environ.get('DATABASE_URL') or 'sqlite:///site.db'
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = uri
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Thư mục upload
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static/uploads')

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Tạo thư mục upload nếu chưa có
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)

    # Đăng ký routes
    from app.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    return app
