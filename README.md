# Certmate-Agent 🤖

Certmate-Agent is an autonomous local assistant designed to monitor the `fabriziosalmi/certmate` repository. It provides real-time alerts, code analysis, and actionable advice via a private web dashboard.

## 🌟 Key Features

- **Autonomous Monitoring**: Periodically polls GitHub for new commits, PRs, and issues.
- **AI-Powered Advice**: Uses a local or remote LLM (via OpenAI-compatible API) to analyze changes and provide insights.
- **Private Dashboard**: A clean, modern React interface accessible only on your local machine.
- **Local Persistence**: Stores history and alerts in a local SQLite database.

## 🏗️ Architecture

- **Backend**: FastAPI (Python)
  - `monitor.py`: Core logic for GitHub polling.
  - `llm.py`: Integration with your LLM endpoint.
  - `models.py`: SQLAlchemy database models.
- **Frontend**: React + TypeScript + Vite
  - Styled with Vanilla CSS for a polished, lightweight look.
- **Automation**: APScheduler runs background tasks for continuous monitoring.

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+
- Node.js & npm
- A GitHub Personal Access Token (PAT)
- A local LLM endpoint (e.g., [Ollama](https://ollama.com/))

### 2. Configuration
Create/edit `backend/.env` with your credentials:
```env
GITHUB_TOKEN=your_token_here
REPO_OWNER=fabriziosalmi
REPO_NAME=certmate
LLM_ENDPOINT=http://localhost:11434/v1
LLM_MODEL=llama3
```

### 3. Installation & Run
Simply run the provided startup script:
```bash
chmod +x start.sh
./start.sh
```

### 4. Access
- **Web Dashboard**: [http://localhost:5173](http://localhost:5173)
- **API Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)

## 🛠️ Development Tools
- `verify_setup.py`: Run this to test your GitHub token and LLM connection independently.
  ```bash
  source backend/venv/bin/activate
  python3 verify_setup.py
  ```

## 🔐 Security
This application is designed for **local use only**. The servers are bound to `127.0.0.1` to ensure your data and tokens never leave your machine.
