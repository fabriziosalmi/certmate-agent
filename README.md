# Certmate-Agent

Autonomous assistant to monitor `fabriziosalmi/certmate`.

## Project Structure
- `backend/`: FastAPI application, repo monitoring, and LLM integration.
- `frontend/`: React + TypeScript dashboard.

## Setup
### Backend
1. `cd backend`
2. `python3 -m venv venv`
3. `source venv/bin/activate`
4. `pip install -r requirements.txt`
5. `uvicorn main:app --reload`

### Frontend
1. `cd frontend`
2. `npm install`
3. `npm run dev`
