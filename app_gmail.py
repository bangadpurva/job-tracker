import os
import re
import csv
import pandas as pd
from datetime import datetime
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from flask import Flask, render_template, request, redirect, url_for

##Simple GMAIL based approach used for basic classification. It also handles scenarios where the current data will keep on updating.

# Gmail API scope
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


def authenticate_gmail():
    creds = None
    if os.path.exists("token.json"):
        creds = InstalledAppFlow.from_authorized_user_file(
            "token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                print("Token expired. Re-authenticate.")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return creds


def fetch_emails():
    creds = authenticate_gmail()
    service = build("gmail", "v1", credentials=creds)
    results = service.users().messages().list(userId="me").execute()
    messages = results.get("messages", [])
    data = []
    for message in messages:
        msg = service.users().messages().get(
            userId="me", id=message["id"]).execute()
        payload = msg["payload"]
        headers = payload.get("headers", [])
        subject = next((item["value"]
                       for item in headers if item["name"] == "Subject"), "")
        from_email = next((item["value"]
                          for item in headers if item["name"] == "From"), "")
        date = next((item["value"]
                    for item in headers if item["name"] == "Date"), "")

        # Extract job-related information
        company = re.search(r"at (.+)", subject)
        role = re.search(r"for (.+)", subject)
        rejection = "rejected" in subject.lower()

        data.append({
            "Company": company.group(1) if company else "",
            "Applied Date": date,
            "Applied Role": role.group(1) if role else "",
            "Rejection Date": date if rejection else "",
            "Interview": "Interview" in subject,
            "Recruiter Name": from_email.split("<")[0].strip(),
            "Recruiter Email": re.search(r"<(.+?)>", from_email).group(1) if "<" in from_email else from_email
        })
    return data


def save_to_csv(data):
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"job_applications_{today}.csv"
    df = pd.DataFrame(data)
    if not os.path.exists("job_tracker"):
        os.makedirs("job_tracker")
    filepath = os.path.join("job_tracker", filename)
    df.to_csv(filepath, index=False)
    return filepath


app = Flask(__name__)


@app.route("/")
def home():
    files = sorted(os.listdir("job_tracker"), reverse=True)
    dataframes = []
    for file in files:
        if file.endswith(".csv"):
            df = pd.read_csv(os.path.join("job_tracker", file))
            dataframes.append(df)
    if dataframes:
        data = pd.concat(dataframes, ignore_index=True)
    else:
        data = pd.DataFrame(columns=["Company", "Applied Date", "Applied Role",
                            "Rejection Date", "Interview", "Recruiter Name", "Recruiter Email"])
    return render_template("index.html", tables=[data.to_html(classes="table table-striped", index=False)], titles=["Job Applications"])


@app.route("/fetch", methods=["POST"])
def fetch_data():
    data = fetch_emails()
    save_to_csv(data)
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
