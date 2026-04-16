import os
import re
import json
import base64
from datetime import datetime

import anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from models import db, JobApplication, STATUS_RANK, TERMINAL_STATUSES


def get_gmail_service(user):
    creds = Credentials(
        token=user.access_token,
        refresh_token=user.refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        expiry=user.token_expiry,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        user.access_token = creds.token
        user.token_expiry = creds.expiry
        db.session.commit()
    return build('gmail', 'v1', credentials=creds)


GMAIL_QUERY = (
    'subject:application OR subject:interview OR subject:offer OR '
    'subject:rejection OR subject:rejected OR subject:opportunity OR '
    'subject:position OR subject:role OR subject:hiring OR subject:applied OR '
    'subject:resume OR subject:candidacy OR subject:candidate'
)


def _extract_body(payload):
    if payload.get('body', {}).get('data'):
        return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
    for part in payload.get('parts', []):
        if part.get('mimeType') == 'text/plain':
            text = _extract_body(part)
            if text:
                return text
    for part in payload.get('parts', []):
        text = _extract_body(part)
        if text:
            return text
    return ''


def fetch_job_emails(service, max_results=50):
    results = service.users().messages().list(
        userId='me',
        q=GMAIL_QUERY,
        maxResults=max_results,
    ).execute()

    messages = results.get('messages', [])
    emails = []

    for message in messages:
        msg = service.users().messages().get(
            userId='me',
            id=message['id'],
            format='full',
        ).execute()

        payload = msg.get('payload', {})
        headers = payload.get('headers', [])

        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
        from_email = next((h['value'] for h in headers if h['name'] == 'From'), '')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
        snippet = msg.get('snippet', '')
        body = _extract_body(payload)[:2000] or snippet

        emails.append({
            'id': message['id'],
            'subject': subject,
            'from': from_email,
            'date': date,
            'snippet': snippet,
            'body': body,
        })

    return emails


def analyze_emails_with_claude(emails):
    if not emails:
        return []

    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])

    email_blocks = []
    for i, email in enumerate(emails, 1):
        email_blocks.append(
            f"EMAIL {i}:\n"
            f"ID: {email['id']}\n"
            f"Subject: {email['subject']}\n"
            f"From: {email['from']}\n"
            f"Date: {email['date']}\n"
            f"Body: {email['body'][:500]}\n"
            f"---"
        )

    prompt = (
        "Analyze these emails and identify job application related ones.\n\n"
        + "\n".join(email_blocks)
        + "\n\nReturn a JSON array. Each element must have:\n"
        '- "gmail_message_id": the ID from the EMAIL block (must match exactly)\n'
        '- "is_job_related": true if this is about a job application, interview, offer, or rejection\n'
        '- "company": company name string, or null\n'
        '- "role": job title string, or null\n'
        '- "applied_date": "YYYY-MM-DD" from the email date, or null\n'
        '- "status": one of "applied", "in_process", "interview_scheduled", "rejected", "offer"\n'
        '  * applied = application submitted/received\n'
        '  * in_process = recruiter outreach, screening, under review\n'
        '  * interview_scheduled = interview confirmed with date/time\n'
        '  * rejected = rejection notice\n'
        '  * offer = job offer received\n'
        '- "interview_date": "YYYY-MM-DD" if an interview is scheduled, else null\n'
        '- "notes": one-sentence summary\n\n'
        'Include ALL emails in the output, even non-job-related ones (set is_job_related=false for those).\n'
        'Return ONLY valid JSON array, no markdown, no explanation.'
    )

    message = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=4096,
        messages=[{'role': 'user', 'content': prompt}],
    )

    text = message.content[0].text.strip()
    json_match = re.search(r'\[.*\]', text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    return []


def upsert_applications(user_id, claude_results, email_map):
    count = 0
    for result in claude_results:
        if not result.get('is_job_related'):
            continue
        company = result.get('company')
        if not company:
            continue

        gmail_id = result.get('gmail_message_id')
        email_data = email_map.get(gmail_id, {})

        applied_date = None
        if result.get('applied_date'):
            try:
                applied_date = datetime.strptime(result['applied_date'], '%Y-%m-%d').date()
            except ValueError:
                pass

        interview_date = None
        if result.get('interview_date'):
            try:
                interview_date = datetime.strptime(result['interview_date'], '%Y-%m-%d').date()
            except ValueError:
                pass

        new_status = result.get('status', 'applied')

        existing = None
        if gmail_id:
            existing = JobApplication.query.filter_by(
                user_id=user_id,
                gmail_message_id=gmail_id,
            ).first()

        if existing:
            if existing.status not in TERMINAL_STATUSES:
                if STATUS_RANK.get(new_status, 0) > STATUS_RANK.get(existing.status, 0):
                    existing.status = new_status
            if interview_date:
                existing.interview_date = interview_date
            existing.updated_at = datetime.utcnow()
            db.session.commit()
        else:
            app = JobApplication(
                user_id=user_id,
                gmail_message_id=gmail_id,
                company=company,
                role=result.get('role'),
                applied_date=applied_date,
                status=new_status,
                interview_date=interview_date,
                notes=result.get('notes'),
                raw_email_snippet=email_data.get('snippet', ''),
            )
            db.session.add(app)
            db.session.commit()
            count += 1

    return count
