# How It Works:
# Authenticate Gmail Access

# The script connects to the user's Gmail using Google's API and authentication process.
# It requires credentials.json for authentication.
# Fetch Emails Related to Jobs

# It searches for emails with subjects containing keywords like "job," "application," or "interview."
# Extracts details such as the subject, sender, and a snippet of the email body.
# Process Emails Using DeepSeek AI Model

# The script loads the deepseek-coder-6.7b model, a powerful AI model designed for text processing.
# It extracts key job details (such as company name and role) from the email using AI.
# Save Extracted Data to a CSV File

# The extracted job details (company, role, date, and sender email) are saved into a CSV file.
# The file is named with the current date for easy tracking.
# Why This is Useful?
# Automates job email tracking.
# Saves time by extracting important details instead of manually reading emails.
# Uses AI to improve accuracy in job detail extraction.

import os
import re
import torch
import pandas as pd
from datetime import datetime
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from transformers import AutoModelForCausalLM, AutoTokenizer

# Load DeepSeek model
model_name = "deepseek-ai/deepseek-coder-6.7b"  # Adjust model as needed
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=torch.float16, device_map="auto")

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def authenticate_gmail():
    creds = None
    if os.path.exists("credentials.json"):
        creds = InstalledAppFlow.from_authorized_user_file("credentials.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("credentials.json", "w") as token:
            token.write(creds.to_json())
    return creds


def fetch_emails():
    creds = authenticate_gmail()
    service = build("gmail", "v1", credentials=creds)

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
        body = msg.get("snippet", "")

        data.append({"Subject": subject, "From": from_email, "Body": body})

    return data


def parse_with_deepseek(data):
    parsed_data = []
    for email in data:
        subject = email["Subject"]
        body = email["Body"]
        prompt = f"Extract job details from this email: {subject} {body}"

        inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
        outputs = model.generate(**inputs, max_length=150)
        response = tokenizer.decode(outputs[0], skip_special_tokens=True)

        parsed_data.append({
            "Company": re.search(r"Company: (.+)", response).group(1) if re.search(r"Company: (.+)", response) else "Unknown",
            "Role": re.search(r"Role: (.+)", response).group(1) if re.search(r"Role: (.+)", response) else "Unknown",
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "Source Email": email["From"]
        })
    return parsed_data


def save_to_csv(data):
    df = pd.DataFrame(data)
    filename = f"job_applications_{datetime.now().strftime('%Y-%m-%d')}.csv"
    df.to_csv(filename, index=False)
    return filename


# Main Execution
if __name__ == "__main__":
    emails = fetch_emails()
    parsed_emails = parse_with_deepseek(emails)
    csv_file = save_to_csv(parsed_emails)
    print(f"Saved data to {csv_file}")
