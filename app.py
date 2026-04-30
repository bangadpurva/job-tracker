import os
import json
from collections import defaultdict
from datetime import date, datetime

from dotenv import load_dotenv
load_dotenv()

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # local dev only

from flask import Flask, redirect, url_for, session, request, render_template, jsonify, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from google_auth_oauthlib.flow import Flow
import google.oauth2.id_token
import google.auth.transport.requests as google_requests

from models import db, User, JobApplication
from agent import get_gmail_service, fetch_job_emails, analyze_emails_with_claude, upsert_applications

DEMO_MODE = not os.environ.get('GOOGLE_CLIENT_ID')

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-prod')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///job_tracker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

@app.template_filter('from_json')
def from_json_filter(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

REDIRECT_URI = 'http://127.0.0.1:5000/auth/callback'

CLIENT_CONFIG = {
    'web': {
        'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'redirect_uris': [REDIRECT_URI],
    }
}

SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/gmail.readonly',
]


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()
    if DEMO_MODE:
        _demo = User.query.filter_by(google_id='demo').first()
        if not _demo:
            _demo = User(google_id='demo', email='demo@example.com', name='Demo User')
            db.session.add(_demo)
            db.session.flush()
            _samples = [
                JobApplication(user_id=_demo.id, company='Google', role='Software Engineer',
                    applied_date=date(2026, 3, 10), status='interview_scheduled',
                    interview_date=date(2026, 4, 20),
                    notes='Passed phone screen, technical loop scheduled.',
                    skills=json.dumps(['Python', 'Distributed Systems', 'System Design', 'Go']),
                    gmail_message_id='demo-1'),
                JobApplication(user_id=_demo.id, company='Stripe', role='Backend Engineer',
                    applied_date=date(2026, 3, 15), status='in_process',
                    notes='Recruiter reached out for a call.',
                    skills=json.dumps(['Go', 'Python', 'Payments APIs', 'PostgreSQL']),
                    gmail_message_id='demo-2'),
                JobApplication(user_id=_demo.id, company='Notion', role='Full Stack Engineer',
                    applied_date=date(2026, 3, 18), status='applied',
                    notes='Applied via LinkedIn.',
                    skills=json.dumps(['React', 'TypeScript', 'Node.js', 'PostgreSQL']),
                    gmail_message_id='demo-3'),
                JobApplication(user_id=_demo.id, company='Figma', role='Product Engineer',
                    applied_date=date(2026, 2, 28), status='rejected',
                    notes='No feedback provided.',
                    skills=json.dumps(['React', 'TypeScript', 'WebGL', 'CSS']),
                    gmail_message_id='demo-4'),
                JobApplication(user_id=_demo.id, company='Linear', role='Software Engineer',
                    applied_date=date(2026, 2, 20), status='offer',
                    notes='Received offer, evaluating compensation.',
                    skills=json.dumps(['TypeScript', 'React', 'Electron', 'GraphQL']),
                    gmail_message_id='demo-5'),
                JobApplication(user_id=_demo.id, company='Vercel', role='DevEx Engineer',
                    applied_date=date(2026, 3, 22), status='applied',
                    notes='Applied through referral.',
                    skills=json.dumps(['Node.js', 'TypeScript', 'CI/CD', 'Docker']),
                    gmail_message_id='demo-6'),
                JobApplication(user_id=_demo.id, company='OpenAI', role='ML Engineer',
                    applied_date=date(2026, 3, 5), status='in_process',
                    notes='Technical assessment sent.',
                    skills=json.dumps(['Python', 'PyTorch', 'Machine Learning', 'LLMs', 'CUDA']),
                    gmail_message_id='demo-7'),
                JobApplication(user_id=_demo.id, company='Anthropic', role='Software Engineer',
                    applied_date=date(2026, 3, 25), status='interview_scheduled',
                    interview_date=date(2026, 4, 22),
                    notes='Two rounds scheduled: system design + coding.',
                    skills=json.dumps(['Python', 'Distributed Systems', 'ML', 'System Design']),
                    gmail_message_id='demo-8'),
            ]
            db.session.add_all(_samples)
            db.session.commit()


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route('/login')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('login.html', demo_mode=DEMO_MODE)


@app.route('/auth/demo')
def auth_demo():
    if not DEMO_MODE:
        return redirect(url_for('login'))
    user = User.query.filter_by(google_id='demo').first()
    login_user(user, remember=True)
    return redirect(url_for('dashboard'))


@app.route('/auth/google')
def auth_google():
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )
    session['oauth_state'] = state
    return redirect(auth_url)


@app.route('/auth/callback')
def auth_callback():
    try:
        flow = Flow.from_client_config(
            CLIENT_CONFIG,
            scopes=SCOPES,
            state=session.get('oauth_state'),
            redirect_uri=REDIRECT_URI,
        )
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        id_info = google.oauth2.id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            CLIENT_CONFIG['web']['client_id'],
        )

        google_id = id_info['sub']
        email = id_info['email']
        name = id_info.get('name', email)

        user = User.query.filter_by(google_id=google_id).first()
        if not user:
            user = User(google_id=google_id, email=email, name=name)
            db.session.add(user)

        user.email = email
        user.name = name
        user.access_token = credentials.token
        user.token_expiry = credentials.expiry
        if credentials.refresh_token:
            user.refresh_token = credentials.refresh_token
        db.session.commit()

        login_user(user, remember=True)
        flash(f'Welcome, {name}!', 'success')
        return redirect(url_for('dashboard'))

    except Exception as e:
        flash(f'Login failed: {str(e)}', 'danger')
        return redirect(url_for('login'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Main routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    status_filter = request.args.get('status', 'all')

    all_apps = JobApplication.query.filter_by(user_id=current_user.id).all()
    stats = {
        'total': len(all_apps),
        'applied': sum(1 for a in all_apps if a.status == 'applied'),
        'in_process': sum(1 for a in all_apps if a.status == 'in_process'),
        'interview_scheduled': sum(1 for a in all_apps if a.status == 'interview_scheduled'),
        'rejected': sum(1 for a in all_apps if a.status == 'rejected'),
        'offer': sum(1 for a in all_apps if a.status == 'offer'),
    }

    query = JobApplication.query.filter_by(user_id=current_user.id)
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    applications = query.order_by(JobApplication.created_at.desc()).all()

    return render_template('dashboard.html',
                           applications=applications,
                           stats=stats,
                           status_filter=status_filter)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@app.route('/agent/scan', methods=['POST'])
@login_required
def agent_scan():
    try:
        service = get_gmail_service(current_user)
        emails = fetch_job_emails(service, max_results=50)

        if not emails:
            flash('No job-related emails found in your inbox.', 'info')
            return redirect(url_for('dashboard'))

        existing_ids = set(
            row[0] for row in
            db.session.query(JobApplication.gmail_message_id)
            .filter_by(user_id=current_user.id).all()
        )
        new_emails = [e for e in emails if e['id'] not in existing_ids]

        if not new_emails:
            flash('All matching emails are already tracked. No new applications found.', 'info')
            return redirect(url_for('dashboard'))

        results = analyze_emails_with_claude(new_emails)
        email_map = {e['id']: e for e in new_emails}
        count = upsert_applications(current_user.id, results, email_map)
        flash(f'Scan complete! Found {count} new job application(s).', 'success')

    except Exception as e:
        flash(f'Error during scan: {str(e)}', 'danger')

    return redirect(url_for('dashboard'))


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@app.route('/analytics')
@login_required
def analytics():
    return render_template('analytics.html')


@app.route('/analytics/data')
@login_required
def analytics_data():
    all_apps = JobApplication.query.filter_by(user_id=current_user.id).all()

    # Status counts for funnel chart
    status_counts = defaultdict(int)
    for a in all_apps:
        status_counts[a.status] += 1

    # Applications per week (last 12 weeks)
    weekly_counts = defaultdict(int)
    for a in all_apps:
        if a.applied_date:
            # ISO week string e.g. "2026-W12"
            week_key = a.applied_date.strftime('%Y-W%V')
            weekly_counts[week_key] += 1
    weekly_sorted = sorted(weekly_counts.items())

    # Top skills frequency
    skill_counts = defaultdict(int)
    for a in all_apps:
        if a.skills:
            try:
                for skill in json.loads(a.skills):
                    skill_counts[skill.strip()] += 1
            except (json.JSONDecodeError, TypeError):
                pass
    top_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)[:15]

    # Conversion rates
    total = len(all_apps)
    in_process_or_above = sum(1 for a in all_apps if a.status in ('in_process', 'interview_scheduled', 'offer'))
    interview_or_above = sum(1 for a in all_apps if a.status in ('interview_scheduled', 'offer'))
    offers = sum(1 for a in all_apps if a.status == 'offer')

    def pct(n, d):
        return round(n / d * 100, 1) if d else 0

    return jsonify({
        'status_counts': dict(status_counts),
        'weekly': [{'week': w, 'count': c} for w, c in weekly_sorted],
        'top_skills': [{'skill': s, 'count': c} for s, c in top_skills],
        'conversion': {
            'applied_to_process': pct(in_process_or_above, total),
            'process_to_interview': pct(interview_or_above, in_process_or_above),
            'interview_to_offer': pct(offers, interview_or_above),
        },
        'total': total,
    })


# ---------------------------------------------------------------------------
# Application CRUD
# ---------------------------------------------------------------------------

@app.route('/applications/<int:app_id>', methods=['GET'])
@login_required
def get_application(app_id):
    rec = JobApplication.query.filter_by(id=app_id, user_id=current_user.id).first_or_404()
    return jsonify({
        'id': rec.id,
        'company': rec.company,
        'role': rec.role or '',
        'status': rec.status,
        'interview_date': rec.interview_date.isoformat() if rec.interview_date else '',
        'notes': rec.notes or '',
        'skills': json.loads(rec.skills) if rec.skills else [],
    })


@app.route('/applications/<int:app_id>/update', methods=['POST'])
@login_required
def update_application(app_id):
    rec = JobApplication.query.filter_by(id=app_id, user_id=current_user.id).first_or_404()

    rec.status = request.form.get('status', rec.status)
    rec.notes = request.form.get('notes', rec.notes)

    interview_date_str = request.form.get('interview_date', '').strip()
    if interview_date_str:
        try:
            rec.interview_date = datetime.strptime(interview_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    else:
        rec.interview_date = None

    rec.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


@app.route('/applications/<int:app_id>/delete', methods=['POST'])
@login_required
def delete_application(app_id):
    rec = JobApplication.query.filter_by(id=app_id, user_id=current_user.id).first_or_404()
    db.session.delete(rec)
    db.session.commit()
    return jsonify({'success': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
