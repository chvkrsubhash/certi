from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
import datetime
import os
from config import Config
from models.mongo import MongoDB
import uuid
from reportlab.lib.pagesizes import letter, landscape
from reportlab.pdfgen import canvas
from io import BytesIO
import pytz
from reportlab.lib.colors import Color, HexColor
from io import BytesIO
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from itsdangerous import URLSafeTimedSerializer

from reportlab.pdfbase.ttfonts import TTFont

app = Flask(__name__)
app.config.from_object(Config)

# MongoDB setup
mongo = MongoDB(app.config['MONGO_URI'])
users_collection = mongo.users
exams_collection = mongo.exams
schedules_collection = mongo.schedules
questions_collection = mongo.questions
results_collection = mongo.results
coupons_collection = mongo.coupons

# Flask-Mail configuration
mail = Mail(app)

# Login required decorator
def login_required(f):
    def wrap(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrap.__name__ = f.__name__
    return wrap

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = users_collection.find_one({'email': email})
        if user and check_password_hash(user['password'], password):
            session['user_id'] = str(user['_id'])
            session['email'] = user['email']
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        name = request.form['name']
        if users_collection.find_one({'email': email}):
            flash('Email already registered.', 'error')
        else:
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
            user_id = users_collection.insert_one({
                'email': email,
                'password': hashed_password,
                'name': name
            }).inserted_id
            session['user_id'] = str(user_id)
            session['email'] = email
            flash('Signup successful! Please login.', 'success')
            return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/dashboard')
@login_required
def dashboard():
    exams = exams_collection.find()
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    now = datetime.datetime.now().date()
    return render_template('dashboard.html', exams=exams, user=user, now=now)

@app.route('/schedule/<exam_id>', methods=['GET', 'POST'])
@login_required
def schedule(exam_id):
    exam = exams_collection.find_one({'_id': ObjectId(exam_id)})
    if not exam:
        flash('Exam not found.', 'error')
        return redirect(url_for('dashboard'))
    # 14-day cooldown check for both failed results and existing schedules
    user_id = ObjectId(session['user_id'])
    now = datetime.datetime.utcnow().replace(tzinfo=None)
    # Check for failed result in last 14 days
    last_failed = results_collection.find_one({
        'user_id': user_id,
        'exam_id': ObjectId(exam_id),
        'passed': False
    }, sort=[('_id', -1)])
    failed_date = None
    if last_failed:
        failed_date = last_failed.get('date') or last_failed.get('_id').generation_time
        failed_date = failed_date.replace(tzinfo=None) if failed_date and failed_date.tzinfo else failed_date
    # Check for any schedule in last 14 days (not just failed)
    recent_schedule = schedules_collection.find_one({
        'user_id': user_id,
        'exam_id': ObjectId(exam_id),
        'date': {'$gte': now - datetime.timedelta(days=14)}
    }, sort=[('date', -1)])
    cooldown = False
    cooldown_msg = ''
    if last_failed and failed_date and (now - failed_date).days < 14:
        days_left = 14 - (now - failed_date).days
        cooldown = True
        cooldown_msg = f'You must wait {days_left} more day(s) before retaking this exam.'
    elif recent_schedule:
        cooldown = True
        cooldown_msg = 'You have already scheduled this exam within the last 14 days.'
    if request.method == 'POST' and not cooldown:
        date = request.form['date']
        certificate_name = request.form['certificate_name']
        coupon_code = request.form.get('coupon_code', '')
        amount_paid = exam['price']
        coupon_applied = False
        if coupon_code:
            coupon = coupons_collection.find_one({'code': coupon_code})
            if coupon:
                amount_paid *= (1 - coupon['discount'] / 100)
                coupon_applied = True
        # Remove IST conversion, store as UTC
        naive_dt = datetime.datetime.strptime(date, '%Y-%m-%dT%H:%M')
        utc_dt = naive_dt.replace(tzinfo=pytz.utc)
        schedule_id = schedules_collection.insert_one({
            'user_id': user_id,
            'exam_id': ObjectId(exam_id),
            'date': utc_dt,
            'coupon_applied': coupon_applied,
            'amount_paid': amount_paid,
            'confirmed': True,
            'certificate_name': certificate_name
        }).inserted_id
        # Send confirmation email with exam link (HTML)
        user = users_collection.find_one({'_id': user_id})
        user_name = user.get('name', user.get('email', 'Student'))
        exam_url = url_for('exam', schedule_id=schedule_id, _external=True)
        date_display = utc_dt.strftime('%Y-%m-%d %H:%M')
        msg = Message('Exam Scheduled', sender=app.config['MAIL_USERNAME'], recipients=[session['email']])
        msg.body = f"Hello {user_name},\nYour exam '{exam['title']}' is scheduled for {date_display}. Amount paid: ₹{amount_paid:.2f}.\nExam link: {exam_url}\nYou can access the exam at the scheduled time from your dashboard or using the link above."
        msg.html = render_template('email_exam_scheduled.html', name=user_name, exam_title=exam['title'], date_display=date_display, amount_paid=f"{amount_paid:.2f}", exam_url=exam_url)
        mail.send(msg)
        return redirect(url_for('confirm', schedule_id=schedule_id))
    return render_template('schedule.html', exam=exam, cooldown=cooldown, cooldown_msg=cooldown_msg)

@app.route('/confirm/<schedule_id>')
@login_required
def confirm(schedule_id):
    schedule = schedules_collection.find_one({'_id': ObjectId(schedule_id)})
    if not schedule:
        flash('Schedule not found.', 'error')
        return redirect(url_for('dashboard'))
    exam = exams_collection.find_one({'_id': schedule['exam_id']})
    return render_template('confirm.html', schedule=schedule, exam=exam)

@app.route('/exam/<schedule_id>')
@login_required
def exam(schedule_id):
    schedule = schedules_collection.find_one({'_id': ObjectId(schedule_id)})
    if not schedule:
        flash('Schedule not found.', 'error')
        return redirect(url_for('dashboard'))
    # Prevent taking exam if result already exists
    existing_result = results_collection.find_one({'user_id': ObjectId(session['user_id']), 'schedule_id': ObjectId(schedule_id)})
    if existing_result:
        flash('You have already completed this exam.', 'error')
        return redirect(url_for('dashboard'))
    # 14-day cooldown check before taking exam
    last_failed = results_collection.find_one({
        'user_id': ObjectId(session['user_id']),
        'exam_id': schedule['exam_id'],
        'passed': False
    }, sort=[('_id', -1)])
    if last_failed:
        last_failed_date = last_failed.get('date') or last_failed.get('_id').generation_time
        now = datetime.datetime.utcnow().replace(tzinfo=None)
        failed_date = last_failed_date.replace(tzinfo=None) if last_failed_date.tzinfo else last_failed_date
        if (now - failed_date).days < 14:
            days_left = 14 - (now - failed_date).days
            flash(f'You must wait {days_left} more day(s) before retaking this exam.', 'error')
            return redirect(url_for('dashboard'))
    # Check if current time is before scheduled time
    now_utc = datetime.datetime.now(pytz.utc)
    scheduled_time_utc = schedule['date']
    now_display = now_utc
    scheduled_time_display = scheduled_time_utc
    # Ensure scheduled_time_display is always aware (UTC)
    if scheduled_time_display.tzinfo is None:
        scheduled_time_display = scheduled_time_display.replace(tzinfo=pytz.utc)
    show_questions = now_display >= scheduled_time_display
    questions = list(questions_collection.find({'exam_id': schedule['exam_id']})) if show_questions else []
    return render_template('exam.html', schedule_id=schedule_id, questions=questions, scheduled_time=scheduled_time_display, now=now_display, show_questions=show_questions)

@app.route('/submit_exam/<schedule_id>', methods=['POST'])
@login_required
def submit_exam(schedule_id):
    schedule = schedules_collection.find_one({'_id': ObjectId(schedule_id)})
    if not schedule:
        flash('Schedule not found.', 'error')
        return redirect(url_for('dashboard'))
    questions = list(questions_collection.find({'exam_id': schedule['exam_id']}))
    score = 0
    total = len(questions)
    for question in questions:
        user_answer = request.form.get(str(question['_id']))
        if user_answer == question['correct_answer']:
            score += 1
    percentage = (score / total) * 100
    passed = percentage >= 60
    # Save certificate_name from schedule
    certificate_name = schedule.get('certificate_name', session.get('email', 'Student'))
    results_collection.insert_one({
        'user_id': ObjectId(session['user_id']),
        'exam_id': schedule['exam_id'],
        'schedule_id': ObjectId(schedule_id),
        'score': score,
        'total': total,
        'percentage': percentage,
        'passed': passed,
        'date': datetime.datetime.utcnow(),
        'user_name': certificate_name
    })
    return redirect(url_for('survey', schedule_id=schedule_id))

@app.route('/survey/<schedule_id>', methods=['GET', 'POST'])
@login_required
def survey(schedule_id):
    if request.method == 'POST':
        # You can save survey responses here if needed
        return redirect(url_for('result', schedule_id=schedule_id))
    return render_template('survey.html', schedule_id=schedule_id)

def generate_certificate_pdf(name, course, date, code, verify_url, signature_image_path='C:\\Users\\chvkr\\Downloads\\COI\\sign.png'):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(letter))
    width, height = landscape(letter)

    name = str(name) if name else "Unknown"
    course = str(course) if course else "Unknown Course"
    date = str(date) if date else "Unknown Date"
    code = str(code) if code else "Unknown Code"
    verify_url = str(verify_url) if verify_url else "Unknown URL"

    bg_color = HexColor('#ffffff')
    primary_gradient_start = HexColor('#667eea')
    primary_gradient_end = HexColor('#764ba2')
    secondary_gradient_start = HexColor('#f093fb')
    secondary_gradient_end = HexColor('#f5576c')
    text_dark = HexColor('#1e293b')
    text_medium = HexColor('#475569')
    text_light = HexColor('#64748b')
    border_color = HexColor('#e2e8f0')

    c.setFillColor(bg_color)
    c.rect(0, 0, width, height, fill=1)

    margin = 40
    border_width = 4

    c.setStrokeColor(primary_gradient_start)
    c.setLineWidth(border_width)
    c.roundRect(margin, margin, width - 2 * margin, height - 2 * margin, 15, fill=0)

    inner_margin = margin + 20
    c.setStrokeColor(border_color)
    c.setLineWidth(1)
    c.roundRect(inner_margin, inner_margin, width - 2 * inner_margin, height - 2 * inner_margin, 10, fill=0)

    c.setFont('Helvetica-Bold', 48)
    c.setFillColor(primary_gradient_start)
    title_y = height - 100
    c.drawCentredString(width / 2, title_y, 'Certificate')

    c.setFont('Helvetica', 16)
    c.setFillColor(text_light)
    subtitle_y = title_y - 30
    c.drawCentredString(width / 2, subtitle_y, 'O F   A C H I E V E M E N T')

    line_y = subtitle_y - 20
    c.setStrokeColor(secondary_gradient_start)
    c.setLineWidth(3)
    c.line(width / 2 - 100, line_y, width / 2 + 100, line_y)

    c.setFont('Helvetica', 14)
    c.setFillColor(text_medium)
    certify_y = line_y - 40
    c.drawCentredString(width / 2, certify_y, 'This is to certify that')

    c.setFont('Helvetica-Bold', 36)
    c.setFillColor(text_dark)
    name_y = certify_y - 50
    c.drawCentredString(width / 2, name_y, name)

    name_width = c.stringWidth(name, 'Helvetica-Bold', 36)
    c.setStrokeColor(secondary_gradient_start)
    c.setLineWidth(2)
    c.line(width / 2 - name_width / 2, name_y - 10, width / 2 + name_width / 2, name_y - 10)

    c.setFont('Helvetica', 16)
    c.setFillColor(text_medium)
    achievement_y = name_y - 40
    c.drawCentredString(width / 2, achievement_y, 'has successfully completed the course and demonstrated')
    c.drawCentredString(width / 2, achievement_y - 20, 'exceptional knowledge in')

    c.setFont('Helvetica-Bold', 24)
    c.setFillColor(secondary_gradient_start)
    course_y = achievement_y - 60
    c.drawCentredString(width / 2, course_y, course)

    details_y = course_y - 80
    box_width = 160
    box_height = 60
    box_spacing = 40

    total_width = 3 * box_width + 2 * box_spacing
    start_x = (width - total_width) / 2

    box1_x = start_x
    c.setFillColor(HexColor('#f8fafc'))
    c.setStrokeColor(border_color)
    c.setLineWidth(1)
    c.roundRect(box1_x, details_y, box_width, box_height, 8, fill=1)

    c.setFont('Helvetica', 10)
    c.setFillColor(text_light)
    c.drawCentredString(box1_x + box_width / 2, details_y + box_height - 15, 'DATE ISSUED')

    c.setFont('Helvetica-Bold', 14)
    c.setFillColor(text_dark)
    display_date = date[:18] + "..." if len(date) > 18 else date
    c.drawCentredString(box1_x + box_width / 2, details_y + 20, display_date)

    box2_x = start_x + box_width + box_spacing
    c.setFillColor(HexColor('#f8fafc'))
    c.roundRect(box2_x, details_y, box_width, box_height, 8, fill=1)

    c.setFont('Helvetica', 10)
    c.setFillColor(text_light)
    c.drawCentredString(box2_x + box_width / 2, details_y + box_height - 15, 'CERTIFICATE ID')

    c.setFont('Helvetica-Bold', 14)
    c.setFillColor(text_dark)
    display_code = code[:15] + "..." if len(code) > 15 else code
    c.drawCentredString(box2_x + box_width / 2, details_y + 20, display_code)

    box3_x = start_x + 2 * (box_width + box_spacing)
    c.setFillColor(HexColor('#f8fafc'))
    c.roundRect(box3_x, details_y, box_width, box_height, 8, fill=1)

    c.setFont('Helvetica', 10)
    c.setFillColor(text_light)
    c.drawCentredString(box3_x + box_width / 2, details_y + box_height - 15, 'VERIFICATION')

    c.setFont('Helvetica-Bold', 12)
    c.setFillColor(primary_gradient_start)
    c.drawCentredString(box3_x + box_width / 2, details_y + 20, 'VERIFIED')

    c.setFont('Helvetica-Oblique', 10)
    c.setFillColor(text_light)
    verify_y = details_y - 30
    display_url = verify_url[:80] + "..." if len(verify_url) > 80 else verify_url
    c.drawCentredString(width / 2, verify_y, f'Verify authenticity at: {display_url}')

    qr_data = f"{verify_url}?code={code}&name={name.replace(' ', '%20')}"
    qr_code = qr.QrCodeWidget(qr_data)
    qr_code.barWidth = 80
    qr_code.barHeight = 80
    qr_code.qrVersion = 1

    qr_drawing = Drawing(80, 80)
    qr_drawing.add(qr_code)

    qr_x = width - 130
    qr_y = 40
    renderPDF.draw(qr_drawing, c, qr_x, qr_y)

    c.setFont('Helvetica', 8)
    c.setFillColor(text_light)
    c.drawCentredString(qr_x + 40, qr_y - 15, 'Scan to Verify')

    signature_y = 80
    signature_x = 80

    if signature_image_path and os.path.exists(signature_image_path):
        try:
            c.drawImage(signature_image_path, signature_x, signature_y + 10, width=120, height=40, preserveAspectRatio=True)
        except Exception as e:
            c.setFont('Helvetica-Oblique', 16)
            c.setFillColor(text_dark)
            c.drawString(signature_x, signature_y + 20, 'Digital Signature')
    else:
        c.setFont('Helvetica-Oblique', 16)
        c.setFillColor(text_dark)
        c.drawString(signature_x, signature_y + 20, 'Authorized Signature')

    c.setStrokeColor(text_light)
    c.setLineWidth(1)
    c.line(signature_x, signature_y, signature_x + 170, signature_y)

    c.setFont('Helvetica-Bold', 12)
    c.setFillColor(text_medium)
    c.drawString(signature_x, signature_y - 20, 'The Certification Authority')

    c.setFont('Helvetica', 10)
    c.setFillColor(text_light)
    c.drawString(signature_x, signature_y - 30, f'Certificate ID: {code}')

    c.setFont('Helvetica', 8)
    c.setFillColor(text_light)
    c.drawCentredString(width / 2, 30, 'This certificate is digitally signed and verified.')

    c.save()
    buffer.seek(0)
    return buffer

def generate_fail_pdf(name, course, date):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    c.setFillColorRGB(1, 0.95, 0.95)
    c.rect(0, 0, width, height, fill=1)
    c.setStrokeColorRGB(1, 0.4, 0.4)
    c.setLineWidth(6)
    c.rect(30, 30, width-60, height-60, fill=0)
    c.setFont('Helvetica-Bold', 26)
    c.setFillColorRGB(0.7, 0, 0)
    c.drawCentredString(width/2, height-100, 'Exam Attempt Report')
    c.setFont('Helvetica-Bold', 20)
    c.setFillColorRGB(0, 0, 0)
    c.drawCentredString(width/2, height-170, name)
    c.setFont('Helvetica', 16)
    c.drawCentredString(width/2, height-210, f'Attempted: {course}')
    c.setFont('Helvetica', 14)
    c.drawCentredString(width/2, height-250, f'Date: {date}')
    c.setFont('Helvetica', 13)
    c.setFillColorRGB(0.4, 0.1, 0.1)
    c.drawCentredString(width/2, height-290, 'You can retake this exam after 14 days.')
    c.setFont('Helvetica', 12)
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.drawString(60, height-340, 'Recommended Resources:')
    c.setFont('Helvetica-Oblique', 12)
    c.drawString(80, height-360, '- https://www.khanacademy.org/')
    c.drawString(80, height-380, '- https://www.coursera.org/')
    c.drawString(80, height-400, '- https://www.edx.org/')
    c.save()
    buffer.seek(0)
    return buffer

@app.route('/result/<schedule_id>')
@login_required
def result(schedule_id):
    result = results_collection.find_one({'schedule_id': ObjectId(schedule_id)})
    if not result:
        flash('Result not found.', 'error')
        return redirect(url_for('dashboard'))
    exam = exams_collection.find_one({'_id': result['exam_id']})
    user_name = result.get('user_name', session.get('email', 'Student'))
    date_display = result['date'].strftime('%Y-%m-%d %H:%M') if 'date' in result else ''
    if result['passed']:
        # Generate unique code
        cert_code = str(uuid.uuid4())[:8]
        verify_url = url_for('verify_certificate', code=cert_code, _external=True)
        certificate_url = url_for('certificate', schedule_id=schedule_id, _external=True)
        # Generate PDF certificate (landscape, with verify link)
        pdf_buffer = generate_certificate_pdf(user_name, exam['title'], date_display, cert_code, verify_url)
        # Send certificate email with PDF and verify link (HTML)
        msg = Message('Congratulations! You Passed', sender=app.config['MAIL_USERNAME'], recipients=[session['email']])
        msg.body = f"You passed the {exam['title']} exam with a score of {result['percentage']}%. Your certificate is attached.\n\nCertificate Code: {cert_code}\nVerify at: {verify_url}\nView your certificate at {certificate_url}."
        msg.html = render_template('email_result_pass.html', name=user_name, course=exam['title'], score=result['percentage'], cert_code=cert_code, date=date_display, certificate_url=certificate_url, verify_url=verify_url)
        msg.attach(f"Certificate_{cert_code}.pdf", 'application/pdf', pdf_buffer.read())
        mail.send(msg)
        # Save code to result for later verification
        results_collection.update_one({'_id': result['_id']}, {'$set': {'certificate_code': cert_code}})
    else:
        # Generate fail report PDF
        pdf_buffer = generate_fail_pdf(user_name, exam['title'], date_display)
        # Send fail email with PDF (HTML)
        msg = Message('Exam Attempt Report', sender=app.config['MAIL_USERNAME'], recipients=[session['email']])
        msg.body = f"You did not pass the {exam['title']} exam. You can retake the exam after 14 days. Please see the attached report and recommended resources."
        resources_url = 'https://www.example.com/resources'  # Replace with actual resources link if available
        msg.html = render_template('email_result_fail.html', name=user_name, course=exam['title'], score=result['percentage'], date=date_display, resources_url=resources_url)
        msg.attach(f"Exam_Report_{schedule_id}.pdf", 'application/pdf', pdf_buffer.read())
        mail.send(msg)
    return render_template('result.html', result=result, exam=exam, date_display=date_display)

@app.route('/verify/<code>')
def verify_certificate(code):
    result = results_collection.find_one({'certificate_code': code})
    if not result or not result.get('passed'):
        return (
            '''
            <html><head><title>Certificate Verification</title>
            <link rel="stylesheet" href="/static/style.css">
            <style>
            body { background: #f8fafc; font-family: 'Segoe UI', Arial, sans-serif; }
            .verify-container { max-width: 480px; margin: 3rem auto; background: #fff; border-radius: 16px; box-shadow: 0 4px 24px #e0e0e0; padding: 2.5rem 2rem; text-align: center; }
            .verify-title { font-size: 2rem; color: #d32f2f; margin-bottom: 1.5rem; }
            .verify-details { font-size: 1.1rem; color: #444; margin-bottom: 2rem; }
            .verify-fail { color: #d32f2f; font-weight: bold; }
            .navbar { background: #1976d2; color: #fff; padding: 1rem 0; text-align: center; margin-bottom: 2rem; }
            .navbar a { color: #fff; margin: 0 1.2rem; text-decoration: none; font-weight: 500; }
            </style></head><body>
            # <div class="navbar"><a href="/">Home</a> <a href="/dashboard">Dashboard</a> <a href="/certificates">Certificates</a></div>
            <div class="verify-container">
            <div class="verify-title">Certificate Not Found or Not Valid</div>
            <div class="verify-details verify-fail">This certificate could not be verified. Please check the code and try again.</div>
            </div></body></html>
            ''', 404
        )
    exam = exams_collection.find_one({'_id': result['exam_id']})
    user_name = result.get('user_name', 'Student')
    date_display = result['date'].strftime('%Y-%m-%d %H:%M') if 'date' in result else ''
    code = result.get('certificate_code', code)
    return (
        f'''
        <html><head><title>Certificate Verification</title>
        <link rel="stylesheet" href="/static/style.css">
        <style>
        body {{ background: #f8fafc; font-family: 'Segoe UI', Arial, sans-serif; }}
        .verify-container {{ max-width: 480px; margin: 3rem auto; background: #fff; border-radius: 16px; box-shadow: 0 4px 24px #e0e0e0; padding: 2.5rem 2rem; text-align: center; }}
        .verify-title {{ font-size: 2rem; color: #1976d2; margin-bottom: 1.5rem; }}
        .verify-details {{ font-size: 1.1rem; color: #444; margin-bottom: 2rem; }}
        .verify-label {{ color: #888; font-size: 0.98rem; text-transform: uppercase; letter-spacing: 1px; }}
        .verify-value {{ color: #222; font-size: 1.15rem; font-weight: 600; margin-bottom: 1.1rem; }}
        .verify-code {{ background: #e3eafe; color: #1976d2; border-radius: 6px; padding: 0.5rem 1.2rem; font-size: 1.1rem; font-weight: 600; display: inline-block; margin-bottom: 1.2rem; }}
        .navbar {{ background: #1976d2; color: #fff; padding: 1rem 0; text-align: center; margin-bottom: 2rem; }}
        .navbar a {{ color: #fff; margin: 0 1.2rem; text-decoration: none; font-weight: 500; }}
        </style></head><body>
        <div class="verify-container">
            <div class="verify-title">Certificate Verified</div>
            <div class="verify-details">
                <div class="verify-label">Name</div>
                <div class="verify-value">{user_name}</div>
                <div class="verify-label">Course</div>
                <div class="verify-value">{exam['title']}</div>
                <div class="verify-label">Date</div>
                <div class="verify-value">{date_display} IST</div>
                <div class="verify-label">Certificate Code</div>
                <div class="verify-code">{code}</div>
            </div>
            <div style="color:#1976d2;font-weight:500;">This certificate is digitally signed and verified.</div>
        </div></body></html>
        '''
    )

@app.route('/certificate/<schedule_id>')
@login_required
def certificate(schedule_id):
    result = results_collection.find_one({'schedule_id': ObjectId(schedule_id)})
    if not result or not result['passed']:
        flash('Certificate not available.', 'error')
        return redirect(url_for('dashboard'))
    exam = exams_collection.find_one({'_id': result['exam_id']})
    date_display = result['date'].strftime('%Y-%m-%d %H:%M') if 'date' in result else ''
    return render_template('certificate.html', exam=exam, result=result, date_display=date_display)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('email', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('login'))

@app.route('/upcoming_exams')
@login_required
def upcoming_exams():
    now_utc = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
    user_id = ObjectId(session['user_id'])
    upcoming = list(schedules_collection.find({
        'user_id': user_id,
        'confirmed': True
    }))
    # Attach exam info and results for each schedule
    for s in upcoming:
        s['exam'] = exams_collection.find_one({'_id': s['exam_id']})
        # Find results for this user and exam
        s['exam_results'] = list(results_collection.find({'user_id': user_id, 'exam_id': s['exam_id']}))
        s['date_display'] = s['date'].strftime('%Y-%m-%d %H:%M') if s.get('date') else None
    return render_template('upcoming_exams.html', schedules=upcoming, now=now_utc)

@app.route('/certificates')
@login_required
def certificates():
    certs = list(results_collection.find({
        'user_id': ObjectId(session['user_id']),
        'passed': True
    }))
    for c in certs:
        c['exam'] = exams_collection.find_one({'_id': c['exam_id']})
        c['date_display'] = c['date'].strftime('%Y-%m-%d %H:%M') if 'date' in c else ''
    return render_template('certificates.html', certificates=certs)

@app.route('/profile')
@login_required
def profile():
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    now = datetime.datetime.now().date()
    return render_template('profile.html', user=user, now=now)

@app.route('/download_certificate_pdf/<schedule_id>')
@login_required
def download_certificate_pdf(schedule_id):
    result = results_collection.find_one({'schedule_id': ObjectId(schedule_id)})
    if not result or not result.get('passed'):
        flash('Certificate not available.', 'error')
        return redirect(url_for('dashboard'))
    exam = exams_collection.find_one({'_id': result['exam_id']})
    user_name = result.get('user_name', session.get('email', 'Student'))
    date_display = result['date'].strftime('%Y-%m-%d %H:%M') if 'date' in result else ''
    cert_code = result.get('certificate_code', 'N/A')
    verify_url = url_for('verify_certificate', code=cert_code, _external=True)
    pdf_buffer = generate_certificate_pdf(user_name, exam['title'], date_display, cert_code, verify_url)
    return send_file(pdf_buffer, as_attachment=True, download_name=f'Certificate_{cert_code}.pdf', mimetype='application/pdf')

@app.route('/change_exam_time/<schedule_id>', methods=['GET', 'POST'])
@login_required
def change_exam_time(schedule_id):
    schedule = schedules_collection.find_one({'_id': ObjectId(schedule_id)})
    if not schedule or schedule.get('time_changed'):
        flash('You cannot change the exam time again.', 'error')
        return redirect(url_for('upcoming_exams'))
    if request.method == 'POST':
        new_date = request.form['new_date']
        naive_dt = datetime.datetime.strptime(new_date, '%Y-%m-%dT%H:%M')
        utc_dt = naive_dt.replace(tzinfo=pytz.utc)
        schedules_collection.update_one({'_id': ObjectId(schedule_id)}, {'$set': {'date': utc_dt, 'time_changed': True}})
        flash('Exam time changed successfully.', 'success')
        return redirect(url_for('upcoming_exams'))
    schedule['date_display'] = schedule['date'].strftime('%Y-%m-%d %H:%M') if schedule.get('date') else None
    return render_template('change_exam_time.html', schedule=schedule)

@app.route('/create_exam', methods=['GET', 'POST'])
@login_required
def create_exam():
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    if not user or not user.get('admin', True):
        flash('Access denied. Admins only.', 'error')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        price = float(request.form.get('price', 0))
        questions = []
        # Parse questions from form
        for key in request.form:
            if key.startswith('questions['):
                # We'll collect all question indices
                break
        # The following parses the questions from the form data
        import re
        question_indices = set()
        for key in request.form:
            m = re.match(r'questions\[(\d+)\]\[question\]', key)
            if m:
                question_indices.add(m.group(1))
        for idx in question_indices:
            q_text = request.form.get(f'questions[{idx}][question]')
            options = request.form.getlist(f'questions[{idx}][options][]')
            correct = request.form.get(f'questions[{idx}][correct_answer]')
            questions.append({
                'question': q_text,
                'options': options,
                'correct_answer': correct
            })
        # Save exam
        exam_id = exams_collection.insert_one({
            'title': title,
            'description': description,
            'price': price
        }).inserted_id
        # Save questions
        for q in questions:
            q['exam_id'] = exam_id
            questions_collection.insert_one(q)
        flash('Exam and questions created successfully!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('create_exam.html')

@app.route('/manage_exams')
@login_required
def manage_exams():
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    if not user or not user.get('admin', True):
        flash('Access denied. Admins only.', 'error')
        return redirect(url_for('dashboard'))
    exams = list(exams_collection.find())
    return render_template('manage_exams.html', exams=exams)

@app.route('/edit_exam/<exam_id>', methods=['GET', 'POST'])
@login_required
def edit_exam(exam_id):
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    if not user or not user.get('admin', True):
        flash('Access denied. Admins only.', 'error')
        return redirect(url_for('dashboard'))
    exam = exams_collection.find_one({'_id': ObjectId(exam_id)})
    if not exam:
        flash('Exam not found.', 'error')
        return redirect(url_for('manage_exams'))
    questions = list(questions_collection.find({'exam_id': ObjectId(exam_id)}))
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        # Update exam
        exams_collection.update_one({'_id': ObjectId(exam_id)}, {'$set': {'title': title, 'description': description}})
        # Remove old questions
        questions_collection.delete_many({'exam_id': ObjectId(exam_id)})
        # Add new questions
        import re
        question_indices = set()
        for key in request.form:
            m = re.match(r'questions\[(\d+)\]\[question\]', key)
            if m:
                question_indices.add(m.group(1))
        for idx in question_indices:
            q_text = request.form.get(f'questions[{idx}][question]')
            options = request.form.getlist(f'questions[{idx}][options][]')
            correct = request.form.get(f'questions[{idx}][correct_answer]')
            questions_collection.insert_one({
                'exam_id': ObjectId(exam_id),
                'question': q_text,
                'options': options,
                'correct_answer': correct
            })
        flash('Exam updated successfully!', 'success')
        return redirect(url_for('manage_exams'))
    return render_template('create_exam.html', exam=exam, questions=questions, edit_mode=True)

@app.route('/view_questions/<exam_id>')
@login_required
def view_questions(exam_id):
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    if not user or not user.get('admin', True):
        flash('Access denied. Admins only.', 'error')
        return redirect(url_for('dashboard'))
    exam = exams_collection.find_one({'_id': ObjectId(exam_id)})
    questions = list(questions_collection.find({'exam_id': ObjectId(exam_id)}))
    return render_template('view_questions.html', exam=exam, questions=questions)

@app.route('/delete_exam/<exam_id>', methods=['POST'])
@login_required
def delete_exam(exam_id):
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    if not user or not user.get('admin', True):
        flash('Access denied. Admins only.', 'error')
        return redirect(url_for('dashboard'))
    exams_collection.delete_one({'_id': ObjectId(exam_id)})
    questions_collection.delete_many({'exam_id': ObjectId(exam_id)})
    flash('Exam and its questions deleted.', 'success')
    return redirect(url_for('manage_exams'))

@app.route('/admin_dashboard')
@login_required
def admin_dashboard():
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    if not user or not user.get('admin', True):
        flash('Access denied. Admins only.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('admin_dashboard.html')

@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        dob = request.form.get('dob', '').strip()
        password = request.form.get('password', '').strip()
        update_fields = {}
        if name:
            update_fields['name'] = name
        if dob:
            update_fields['dob'] = dob
        if password:
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
            update_fields['password'] = hashed_password
        if update_fields:
            users_collection.update_one({'_id': ObjectId(session['user_id'])}, {'$set': update_fields})
            flash('Profile updated successfully.', 'success')
        else:
            flash('No changes made.', 'info')
        return redirect(url_for('profile'))
    return render_template('edit_profile.html', user=user)

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = users_collection.find_one({'email': email})
        if user:
            s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
            token = s.dumps(email, salt='password-reset')
            reset_url = url_for('reset_password', token=token, _external=True)
            msg = Message('Password Reset Request', sender=app.config['MAIL_USERNAME'], recipients=[email])
            msg.body = f"To reset your password, click the following link: {reset_url}\nIf you did not request this, please ignore this email."
            mail.send(msg)
            flash('A password reset link has been sent to your email.', 'success')
        else:
            flash('No account found with that email.', 'error')
        return redirect(url_for('forgot_password'))
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        email = s.loads(token, salt='password-reset', max_age=3600)
    except Exception:
        flash('The password reset link is invalid or has expired.', 'error')
        return redirect(url_for('login'))
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        if not password:
            flash('Password cannot be empty.', 'error')
            return redirect(request.url)
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        users_collection.update_one({'email': email}, {'$set': {'password': hashed_password}})
        flash('Your password has been reset. Please login.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token)

def enter_name():
    pass

if __name__ == '__main__':
    app.run(debug=True)