#!/bin/bash

# Certmate-Agent Startup Script

echo "Starting Certmate-Agent Backend..."
cd backend
if [ ! -d "venv" ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi
# Run backend in background
uvicorn main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "Starting Certmate-Agent Frontend..."
cd ../frontend
# Run frontend in background
npm run dev &
FRONTEND_PID=$!

echo "Certmate-Agent is running!"
echo "Frontend: http://localhost:5173"
echo "Backend API: http://localhost:8000"
echo "Press Ctrl+C to stop both."

# Handle shutdown
trap "kill $BACKEND_PID $FRONTEND_PID; exit" INT TERM
wait
