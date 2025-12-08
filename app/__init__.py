import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from dotenv import load_dotenv

# Load biến môi trường
load_dotenv()

# --- CẤU HÌNH TRỰC TIẾP ---
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'ban-khong-bao-gio-doan-duoc-dau'
    # Fix lỗi Database URL trên Render (postgres:// -> postgresql://)
    uri = os.environ.get('DATABASE_URL') or 'sqlite:///site.db'
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = uri
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static/uploads')
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

# Khởi tạo Extension
db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = 'main.login'
login.login_message = 'Vui lòng đăng nhập.'

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)

    # Headers bảo mật
    @app.after_request
    def add_headers(response):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

    from app.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    return app
