import requests
import os
from .database import SessionLocal
from .models import RepoEvent, AgentAdvice
from .llm import get_advice
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_OWNER = os.getenv("REPO_OWNER", "fabriziosalmi")
REPO_NAME = os.getenv("REPO_NAME", "certmate")

def poll_repo():
    db = SessionLocal()
    try:
        # Check commits
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits"
        headers = {}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            commits = response.json()
            for commit in commits[:5]: # Check last 5 commits
                sha = commit["sha"]
                message = commit["commit"]["message"]
                
                # Check if we already have this event
                existing = db.query(RepoEvent).filter(RepoEvent.external_id == sha).first()
                if not existing:
                    new_event = RepoEvent(
                        event_type="commit",
                        external_id=sha,
                        content=message
                    )
                    db.add(new_event)
                    db.commit()
                    db.refresh(new_event)
                    
                    # Get advice from LLM
                    advice_content = get_advice(f"Commit message: {message}")
                    new_advice = AgentAdvice(
                        event_id=new_event.id,
                        advice_type="info",
                        title=f"New Commit: {sha[:7]}",
                        content=advice_content
                    )
                    db.add(new_advice)
                    db.commit()
                    print(f"Processed commit {sha}")
        else:
            print(f"Error polling GitHub: {response.status_code}")
    finally:
        db.close()
