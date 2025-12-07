@main.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    reset_form = ResetPasswordForm()

    # --- 1. XỬ LÝ THÊM USER THỦ CÔNG ---
    if user_form.validate_on_submit():
        if not User.query.filter_by(username=user_form.username.data).first():
            u = User(username=user_form.username.data, bot_type=user_form.bot_type.data, is_admin=user_form.is_admin.data)
            u.set_password(user_form.password.data)
            db.session.add(u)
            db.session.commit()
            flash('Thêm thành công tài khoản mới.', 'success')
        else:
            flash('Tên đăng nhập đã tồn tại.', 'danger')
        return redirect(url_for('main.admin_dashboard'))

    # --- 2. XỬ LÝ UPLOAD CSV (ĐÃ FIX LỖI 500) ---
    if upload_form.validate_on_submit() and upload_form.csv_file.data:
        try:
            file = upload_form.csv_file.data
            
            # SỬA LỖI ENCODING: Dùng TextIOWrapper với 'utf-8-sig' để đọc được file từ Excel
            stream = io.TextIOWrapper(file.stream, encoding='utf-8-sig', newline=None)
            csv_input = csv.reader(stream)
            
            # Bỏ qua dòng tiêu đề (Header)
            next(csv_input, None)
            
            added_count = 0
            for row in csv_input:
                # Bỏ qua dòng trống hoặc dòng thiếu dữ liệu
                if not row or len(row) < 1: 
                    continue
                
                # Lấy dữ liệu an toàn (tránh lỗi Index out of range)
                # Cột 0: Username
                r_username = row[0].strip()
                if not r_username: continue 

                # Cột 1: Password (Mặc định 123456 nếu thiếu)
                r_password = row[1].strip() if len(row) > 1 and row[1].strip() else "123456" 
                
                # Cột 2: Bot Type (Mặc định gofai nếu thiếu)
                r_type = row[2].strip().lower() if len(row) > 2 and row[2].strip() else "gofai"

                # Kiểm tra trùng trong DB trước khi thêm
                if not User.query.filter_by(username=r_username).first():
                    u = User(username=r_username, bot_type=r_type)
                    u.set_password(r_password)
                    db.session.add(u)
                    added_count += 1
            
            db.session.commit()
            
            if added_count > 0:
                flash(f'Đã thêm thành công {added_count} học sinh từ file CSV!', 'success')
            else:
                flash('Không thêm được ai (Có thể do file rỗng hoặc trùng lặp hết).', 'warning')
                
        except Exception as e:
            db.session.rollback() # QUAN TRỌNG: Rollback để tránh treo Database
            print(f"Lỗi CSV: {e}") # In lỗi ra log của Render để debug
            flash(f'Lỗi xử lý file: {str(e)}', 'danger')
            
        return redirect(url_for('main.admin_dashboard'))

    # --- 3. CÁC LOGIC KHÁC (GIỮ NGUYÊN) ---
    users = User.query.filter_by(is_admin=False).all()
    return render_template('admin_dashboard.html', users=users, user_form=user_form, upload_form=upload_form, reset_form=reset_form)
