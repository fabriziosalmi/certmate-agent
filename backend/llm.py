import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    base_url=os.getenv("LLM_ENDPOINT", "http://localhost:11434/v1"),
    api_key=os.getenv("LLM_API_KEY", "not-needed"),
)

def get_advice(context: str):
    try:
        response = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "llama3"),
            messages=[
                {"role": "system", "content": "You are Certmate-Agent, an expert in repository monitoring and code analysis. Provide concise, actionable advice based on the provided code changes or repository events."},
                {"role": "user", "content": f"Analyze this repository event and provide advice:\n\n{context}"}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error communicating with LLM: {str(e)}"
