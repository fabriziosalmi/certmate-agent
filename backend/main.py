from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler
from .database import SessionLocal, engine, get_db
from .models import Base, RepoEvent, AgentAdvice
from .monitor import poll_repo
import os

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Certmate-Agent API")

# Configure CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup Background Scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(poll_repo, 'interval', seconds=int(os.getenv("CHECK_INTERVAL_SECONDS", 3600)))
scheduler.start()

@app.get("/")
async def root():
    return {"message": "Certmate-Agent API is running"}

@app.get("/status")
async def get_status():
    return {
        "repo": f"{os.getenv('REPO_OWNER', 'fabriziosalmi')}/{os.getenv('REPO_NAME', 'certmate')}",
        "status": "active",
        "interval": os.getenv("CHECK_INTERVAL_SECONDS", 3600)
    }

@app.get("/events")
async def get_events(db: Session = Depends(get_db)):
    events = db.query(RepoEvent).order_by(RepoEvent.created_at.desc()).limit(20).all()
    return events

@app.get("/advice")
async def get_advice_list(db: Session = Depends(get_db)):
    advice = db.query(AgentAdvice).order_by(AgentAdvice.created_at.desc()).limit(20).all()
    return advice

@app.post("/trigger-poll")
async def trigger_poll():
    poll_repo()
    return {"message": "Polling triggered"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
