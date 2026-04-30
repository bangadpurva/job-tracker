import os
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


# Tool definition for Claude to call when it finds a job-related email
_SAVE_APPLICATION_TOOL = {
    'name': 'save_job_application',
    'description': (
        'Save a job application extracted from an email. Only call this for emails '
        'that are clearly about a job application, interview, offer, or rejection.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'gmail_message_id': {
                'type': 'string',
                'description': 'The exact email ID from the EMAIL block header',
            },
            'company': {
                'type': 'string',
                'description': 'Name of the company',
            },
            'role': {
                'type': 'string',
                'description': 'Job title or role name',
            },
            'status': {
                'type': 'string',
                'enum': ['applied', 'in_process', 'interview_scheduled', 'rejected', 'offer'],
                'description': (
                    'applied=submitted/received, in_process=recruiter outreach or screening, '
                    'interview_scheduled=interview confirmed with date/time, '
                    'rejected=rejection notice, offer=job offer received'
                ),
            },
            'applied_date': {
                'type': 'string',
                'description': 'Date in YYYY-MM-DD format from the email date header',
            },
            'interview_date': {
                'type': 'string',
                'description': 'Date in YYYY-MM-DD format if an interview is scheduled, else omit',
            },
            'notes': {
                'type': 'string',
                'description': 'One-sentence summary of the email',
            },
            'skills': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': (
                    'Technical skills and tools mentioned in the job description or email '
                    '(e.g. Python, SQL, React, AWS, Machine Learning). Max 8 items.'
                ),
            },
        },
        'required': ['gmail_message_id', 'company', 'status'],
    },
}


def analyze_emails_with_claude(emails):
    """Use Claude tool use to extract structured job application data from emails."""
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
            f"Body: {email['body'][:600]}\n"
            f"---"
        )

    prompt = (
        "Analyze the following emails. For each email that is related to a job application, "
        "interview, offer, or rejection, call the save_job_application tool with the extracted data. "
        "Skip emails that are not job-related (newsletters, receipts, etc.).\n\n"
        + "\n".join(email_blocks)
    )

    message = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=4096,
        tools=[_SAVE_APPLICATION_TOOL],
        messages=[{'role': 'user', 'content': prompt}],
    )

    results = []
    for block in message.content:
        if block.type == 'tool_use' and block.name == 'save_job_application':
            results.append(block.input)

    return results


def upsert_applications(user_id, claude_results, email_map):
    """Write Claude tool call results into the database."""
    count = 0
    for result in claude_results:
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
        skills_json = json.dumps(result.get('skills') or [])

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
            if result.get('skills'):
                existing.skills = skills_json
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
                skills=skills_json,
            )
            db.session.add(app)
            db.session.commit()
            count += 1

    return count
