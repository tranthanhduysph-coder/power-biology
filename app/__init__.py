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
    
    # 1. Cấu hình Secret Key
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_secret_key_dev')

    # --- 2. CẤU HÌNH DATABASE (FIX LỖI MẤT DỮ LIỆU) ---
    # Đường dẫn đến ổ đĩa bền vững trên Render (Bạn cần kiểm tra Mount Path trong settings)
    # Mặc định thường là /var/data
    RENDER_DISK_PATH = '/var/data'
    
    # Kiểm tra xem thư mục Disk có tồn tại không (Chỉ có trên Render khi đã gắn Disk)
    if os.path.exists(RENDER_DISK_PATH):
        print(f">>> PHÁT HIỆN DISK TẠI: {RENDER_DISK_PATH}. ĐANG SỬ DỤNG DATABASE BỀN VỮNG.")
        # Lưu file db vào trong ổ đĩa này: /var/data/site.db
        db_path = os.path.join(RENDER_DISK_PATH, 'site.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    else:
        print(">>> KHÔNG THẤY DISK. ĐANG CHẠY CHẾ ĐỘ LOCAL (Dữ liệu sẽ mất khi deploy lại trên Cloud).")
        # Chạy local hoặc chưa gắn disk đúng cách
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # ----------------------------------------------------

    # 3. Cấu hình Upload (Cũng nên lưu vào Disk nếu muốn ảnh không bị mất)
    # Nếu có Disk, ta lưu ảnh vào /var/data/uploads, sau đó symlink hoặc serve file
    # Nhưng để đơn giản, tạm thời ta giữ nguyên cấu hình upload cũ, ưu tiên cứu Database trước.
    UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
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

    # 4. TỰ ĐỘNG TẠO LẠI BẢNG VÀ ADMIN MẶC ĐỊNH
    # Vì database cũ đã mất, đoạn này sẽ tự chạy khi deploy để tạo lại cấu trúc và acc admin
    with app.app_context():
        try:
            db.create_all()
            print(">>> Database Tables created successfully.")
            
            # Tự động tạo lại admin nếu chưa có
            if not User.query.filter_by(username='admin').first():
                admin = User(username='admin', bot_type='ai', is_admin=True)
                admin.set_password('admin123') # Mật khẩu mặc định
                db.session.add(admin)
                db.session.commit()
                print(">>> ĐÃ KHÔI PHỤC TÀI KHOẢN ADMIN: admin / admin123")
        except Exception as e:
            print(f">>> Lỗi khởi tạo DB: {e}")

    return app
