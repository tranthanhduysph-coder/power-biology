
    
  

@main.route('/admin/logs/<int:user_id>')
@login_required
@admin_required
def view_variable_logs(user_id):
    u = User.query.get_or_404(user_id)

    logs = (
        VariableLog.query
        .filter_by(user_id=user_id)
        .order_by(VariableLog.timestamp.desc())
        .all()
    )

    return render_template(
        'variable_logs.html',
        student=u,
        logs=logs
    )


@main.route('/admin/export_history')
@login_required
@admin_required
def export_chat_history():
    si = io.StringIO()
    cw = csv.writer(si)

    cw.writerow(['Time (GMT+7)', 'Session', 'User', 'Type', 'Content'])

    msgs = (
        db.session.query(Message, User)
        .join(User)
        .order_by(Message.timestamp.desc())
        .all()
    )

    for m, u in msgs:
        t = m.timestamp.strftime('%Y-%m-%d %H:%M:%S') if m.timestamp else ""
        cw.writerow([t, m.session_id, u.username, m.sender, m.content])

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=data.csv"}
    )


@main.route('/admin/export_logs')
@login_required
@admin_required
def export_variable_logs():
    si = io.StringIO()
    cw = csv.writer(si)

    cw.writerow([
        'Time (GMT+7)',
        'Session',
        'Username',
        'Variable Name',
        'Value'
    ])

    logs = (
        db.session.query(VariableLog, User)
        .join(User)
        .order_by(VariableLog.timestamp.desc())
        .all()
    )

    for log, user in logs:
        t_str = (
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            if log.timestamp
            else ""
        )
        cw.writerow([
            t_str,
            log.session_id,
            user.username,
            log.variable_name,
            log.variable_value
        ])

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=logs_export.csv"}
    )


@main.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()

    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            current_user.set_password(form.new_password.data)
            db.session.commit()
            return redirect(url_for('main.chatbot_redirect'))

        flash('Sai mật khẩu.', 'danger')

    return render_template('change_password.html', form=form)

