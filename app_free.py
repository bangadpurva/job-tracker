import os
import re
import pandas as pd
from datetime import datetime
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Gmail API scope
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def authenticate_gmail():
    creds = None
    if os.path.exists("credentials.json"):
        creds = InstalledAppFlow.from_authorized_user_file(
            "credentials.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("credentials.json", "w") as token:
            token.write(creds.to_json())
    return creds


def fetch_emails():
    creds = authenticate_gmail()
    service = build("gmail", "v1", credentials=creds)

    # Fetch emails matching specific keywords
    query = "subject:job OR subject:application OR subject:interview"
    results = service.users().messages().list(userId="me", q=query).execute()
    messages = results.get("messages", [])
    data = []

    for message in messages:
        msg = service.users().messages().get(userId="me", id=message["id"]).execute()
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        subject = next((item["value"] for item in headers if item["name"] == "Subject"), "")
        from_email = next((item["value"] for item in headers if item["name"] == "From"), "")
        date = next((item["value"] for item in headers if item["name"] == "Date"), "")
        body = msg.get("snippet", "")

        # Extract job-related information using regex
        company = re.search(r"at (.+)", subject)
        role = re.search(r"for (.+)", subject)
        rejection = "rejected" in subject.lower()

        data.append({
            "Company": company.group(1) if company else "Unknown",
            "Applied Date": date,
            "Applied Role": role.group(1) if role else "Unknown",
            "Rejection Date": date if rejection else "",
            "Interview": "Interview" in subject,
            "Source Email": from_email
        })

    return data


def save_to_csv(data):
    df = pd.DataFrame(data)
    filename = f"job_applications_{datetime.now().strftime('%Y-%m-%d')}.csv"
    df.to_csv(filename, index=False)
    return filename


# Main
if __name__ == "__main__":
    emails = fetch_emails()
    csv_file = save_to_csv(emails)
    print(f"Saved data to {csv_file}")
