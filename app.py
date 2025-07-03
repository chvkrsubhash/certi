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
    return render_template('dashboard.html', exams=exams, user=user)

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
        # Add 5 hours 30 minutes to ensure IST
        naive_dt = datetime.datetime.strptime(date, '%Y-%m-%dT%H:%M')
        ist_dt = naive_dt + datetime.timedelta(hours=5, minutes=30)
        IST = pytz.timezone('Asia/Kolkata')
        ist_dt = IST.localize(ist_dt)
        utc_dt = ist_dt.astimezone(pytz.utc)
        schedule_id = schedules_collection.insert_one({
            'user_id': user_id,
            'exam_id': ObjectId(exam_id),
            'date': utc_dt,
            'coupon_applied': coupon_applied,
            'amount_paid': amount_paid,
            'confirmed': True,
            'certificate_name': certificate_name
        }).inserted_id
        # Send confirmation email
        msg = Message('Exam Scheduled', sender=app.config['MAIL_USERNAME'], recipients=[session['email']])
        msg.body = f"Your exam '{exam['title']}' is scheduled for {date}. Amount paid: ${amount_paid:.2f}."
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
    IST = pytz.timezone('Asia/Kolkata')
    now_utc = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
    scheduled_time_utc = schedule['date']
    now_ist = now_utc.astimezone(IST)
    scheduled_time_ist = scheduled_time_utc.astimezone(IST)
    show_questions = now_ist >= scheduled_time_ist
    questions = list(questions_collection.find({'exam_id': schedule['exam_id']})) if show_questions else []
    return render_template('exam.html', schedule_id=schedule_id, questions=questions, scheduled_time=scheduled_time_ist, now=now_ist, show_questions=show_questions)

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
    return redirect(url_for('result', schedule_id=schedule_id))

def generate_certificate_pdf(name, course, date, code, verify_url):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(letter))
    width, height = landscape(letter)
    # Background
    c.setFillColorRGB(1, 0.98, 0.8)
    c.rect(0, 0, width, height, fill=1)
    # Border
    c.setStrokeColorRGB(1, 0.84, 0)
    c.setLineWidth(6)
    c.rect(30, 30, width-60, height-60, fill=0)
    # Title
    c.setFont('Helvetica-Bold', 32)
    c.setFillColorRGB(0.75, 0.65, 0)
    c.drawCentredString(width/2, height-80, 'Certificate of Achievement')
    # Name
    c.setFont('Helvetica-Bold', 26)
    c.setFillColorRGB(0, 0, 0)
    c.drawCentredString(width/2, height-160, name)
    # Course
    c.setFont('Helvetica', 20)
    c.drawCentredString(width/2, height-210, f'has successfully passed the {course} exam')
    # Date
    c.setFont('Helvetica', 16)
    c.drawCentredString(width/2, height-260, f'Date: {date}')
    # Unique code
    c.setFont('Helvetica', 14)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawCentredString(width/2, height-290, f'Certificate Code: {code}')
    # Verification link
    c.setFont('Helvetica-Oblique', 13)
    c.setFillColorRGB(0.2, 0.2, 0.7)
    c.drawCentredString(width/2, height-320, f'Verify at: {verify_url}')
    # Signature
    c.setFont('Helvetica-Oblique', 18)
    c.setFillColorRGB(0.3, 0.3, 0.3)
    c.drawString(60, 60, 'Exam Portal Team')
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
    date_ist = result['date'].astimezone(pytz.timezone('Asia/Kolkata')) if 'date' in result else ''
    if result['passed']:
        # Generate unique code
        cert_code = str(uuid.uuid4())[:8]
        verify_url = url_for('verify_certificate', code=cert_code, _external=True)
        # Generate PDF certificate (landscape, with verify link)
        pdf_buffer = generate_certificate_pdf(user_name, exam['title'], date_ist, cert_code, verify_url)
        # Send certificate email with PDF and verify link
        msg = Message('Congratulations! You Passed', sender=app.config['MAIL_USERNAME'], recipients=[session['email']])
        msg.body = f"You passed the {exam['title']} exam with a score of {result['percentage']}%. Your certificate is attached.\n\nCertificate Code: {cert_code}\nVerify at: {verify_url}\nView your certificate at {url_for('certificate', schedule_id=schedule_id, _external=True)}."
        msg.attach(f"Certificate_{cert_code}.pdf", 'application/pdf', pdf_buffer.read())
        mail.send(msg)
        # Save code to result for later verification
        results_collection.update_one({'_id': result['_id']}, {'$set': {'certificate_code': cert_code}})
    else:
        # Generate fail report PDF
        pdf_buffer = generate_fail_pdf(user_name, exam['title'], date_ist)
        # Send fail email with PDF
        msg = Message('Exam Attempt Report', sender=app.config['MAIL_USERNAME'], recipients=[session['email']])
        msg.body = f"You did not pass the {exam['title']} exam. You can retake the exam after 14 days. Please see the attached report and recommended resources."
        msg.attach(f"Exam_Report_{schedule_id}.pdf", 'application/pdf', pdf_buffer.read())
        mail.send(msg)
    return render_template('result.html', result=result, exam=exam, date_ist=date_ist)

@app.route('/verify/<code>')
def verify_certificate(code):
    result = results_collection.find_one({'certificate_code': code})
    if not result or not result.get('passed'):
        return '<h2>Certificate not found or not valid.</h2>', 404
    exam = exams_collection.find_one({'_id': result['exam_id']})
    user_name = result.get('user_name', 'Student')
    date_ist = result['date'].astimezone(pytz.timezone('Asia/Kolkata')) if 'date' in result else ''
    return f"""
    <h2>Certificate Verified</h2>
    <p><strong>Name:</strong> {user_name}</p>
    <p><strong>Course:</strong> {exam['title']}</p>
    <p><strong>Date:</strong> {date_ist}</p>
    <p><strong>Certificate Code:</strong> {code}</p>
    """

@app.route('/certificate/<schedule_id>')
@login_required
def certificate(schedule_id):
    result = results_collection.find_one({'schedule_id': ObjectId(schedule_id)})
    if not result or not result['passed']:
        flash('Certificate not available.', 'error')
        return redirect(url_for('dashboard'))
    exam = exams_collection.find_one({'_id': result['exam_id']})
    date_ist = result['date'].astimezone(pytz.timezone('Asia/Kolkata')) if 'date' in result else ''
    return render_template('certificate.html', exam=exam, result=result, date_ist=date_ist)

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
    now_ist = now_utc.astimezone(pytz.timezone('Asia/Kolkata'))
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
        s['date_ist'] = s['date'].astimezone(pytz.timezone('Asia/Kolkata')) if s.get('date') else None
    return render_template('upcoming_exams.html', schedules=upcoming, now=now_ist)

@app.route('/certificates')
@login_required
def certificates():
    certs = list(results_collection.find({
        'user_id': ObjectId(session['user_id']),
        'passed': True
    }))
    for c in certs:
        c['exam'] = exams_collection.find_one({'_id': c['exam_id']})
        c['date_ist'] = c['date'].astimezone(pytz.timezone('Asia/Kolkata')) if 'date' in c else ''
    return render_template('certificates.html', certificates=certs)

@app.route('/profile')
@login_required
def profile():
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    return render_template('profile.html', user=user)

@app.route('/download_certificate_pdf/<schedule_id>')
@login_required
def download_certificate_pdf(schedule_id):
    result = results_collection.find_one({'schedule_id': ObjectId(schedule_id)})
    if not result or not result.get('passed'):
        flash('Certificate not available.', 'error')
        return redirect(url_for('dashboard'))
    exam = exams_collection.find_one({'_id': result['exam_id']})
    user_name = result.get('user_name', session.get('email', 'Student'))
    date_ist = result['date'].astimezone(pytz.timezone('Asia/Kolkata')) if 'date' in result else ''
    cert_code = result.get('certificate_code', 'N/A')
    verify_url = url_for('verify_certificate', code=cert_code, _external=True)
    pdf_buffer = generate_certificate_pdf(user_name, exam['title'], date_ist, cert_code, verify_url)
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
        ist_dt = naive_dt + datetime.timedelta(hours=5, minutes=30)
        IST = pytz.timezone('Asia/Kolkata')
        ist_dt = IST.localize(ist_dt)
        utc_dt = ist_dt.astimezone(pytz.utc)
        schedules_collection.update_one({'_id': ObjectId(schedule_id)}, {'$set': {'date': utc_dt, 'time_changed': True}})
        flash('Exam time changed successfully.', 'success')
        return redirect(url_for('upcoming_exams'))
    schedule['date_ist'] = schedule['date'].astimezone(pytz.timezone('Asia/Kolkata')) if schedule.get('date') else None
    return render_template('change_exam_time.html', schedule=schedule)

def enter_name():
    pass

if __name__ == '__main__':
    app.run(debug=True)