# This code is a simple script that helps you automatically extract information from job-related emails in your Gmail account and save the details into a CSV file. Here's a brief explanation of what it does:

# Gmail Authentication:
# The code uses the Gmail API to connect to your Gmail account. It checks for saved credentials (in credentials.json) and asks you to log in if needed.

# Fetching Emails:
# It searches your inbox for emails with subjects like "job," "application," or "interview." For each matching email, it collects the subject, sender's email, and a short snippet of the email body.

# Extracting Information with a Language Model:
# The script then uses a text-generation model (the latest GPT-o mini) to analyze each email. It sends a prompt to the model asking for details like the company name, role, and date, and then extracts these details using simple text patterns.

# Saving the Data:
# Finally, the extracted information is saved into a CSV file, which you can later open with any spreadsheet program.

# This script is useful for automating the process of sorting and saving job application details from your emails.

import os
import re
import pandas as pd
from datetime import datetime
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from transformers import pipeline

# Gmail API scope
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def authenticate_gmail():
    creds = None
    # Load credentials from file if they exist
    if os.path.exists("credentials.json"):
        creds = InstalledAppFlow.from_authorized_user_file("credentials.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
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
        body = msg.get("snippet", "")

        data.append({
            "Subject": subject,
            "From": from_email,
            "Body": body
        })

    return data

def parse_with_llm(data):
    # Use the text-generation pipeline with the latest GPT-o mini model.
    # Replace "latest/gpt-o-mini" with the actual model ID.
    generator = pipeline("text-generation", model="latest/gpt-o-mini", device=-1)
    parsed_data = []

    for email in data:
        subject = email["Subject"]
        body = email["Body"]

        # Prompt instructs the model to extract details in a structured format.
        prompt = (
            f"Extract the following details from this job application email:\n"
            f"  - Company\n"
            f"  - Role\n"
            f"  - Date (YYYY-MM-DD)\n"
            f"Format your response exactly as:\n"
            f"Company: <company name>, Role: <role>, Date: <YYYY-MM-DD>\n\n"
            f"Email Subject: {subject}\n"
            f"Email Body: {body}\n"
            f"Response:"
        )
        
        # Generate the output text (you can adjust max_length as needed)
        output = generator(prompt, max_length=100, do_sample=False)[0]["generated_text"]

        # Use regex to extract the fields from the generated text.
        company_match = re.search(r"Company:\s*([^,]+)", output)
        role_match = re.search(r"Role:\s*([^,]+)", output)
        date_match = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", output)

        company = company_match.group(1).strip() if company_match else "Unknown"
        role = role_match.group(1).strip() if role_match else "Software Engineer"
        date = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")

        parsed_data.append({
            "Company": company,
            "Role": role,
            "Date": date,
            "Source Email": email["From"]
        })
        
    return parsed_data

def save_to_csv(data):
    df = pd.DataFrame(data)
    filename = f"job_applications_{datetime.now().strftime('%Y-%m-%d')}.csv"
    df.to_csv(filename, index=False)
    return filename

# Main
if __name__ == "__main__":
    emails = fetch_emails()
    if emails:
        parsed_emails = parse_with_llm(emails)
        csv_file = save_to_csv(parsed_emails)
        print(f"Saved data to {csv_file}")
    else:
        print("No emails found matching the query.")
