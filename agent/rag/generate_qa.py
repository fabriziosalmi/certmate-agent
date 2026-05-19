import asyncio
import json
from pathlib import Path
from typing import Any

from agent.rag.chunker import chunk_markdown
from agent.rag.indexer import DEFAULT_PATHS
from agent.llm.lmstudio import LMStudioClient

# We read the repo directly from the filesystem for the prototype
REPO_ROOT = Path(__file__).parent.parent.parent

PROMPT_TEMPLATE = """
Sei un technical writer esperto. Ho un pezzo di documentazione ufficiale del progetto CertMate.
Il tuo compito è estrarre dalle 1 alle 3 coppie di Domanda e Risposta (Q&A) basate ESCLUSIVAMENTE su questo testo.

Devi fornire le Q&A in formato JSON array, con la domanda e la risposta in due lingue: Italiano (it) ed Inglese (en).
La risposta deve essere in formato Markdown, precisa, professionale e dritta al punto. 

Formato JSON richiesto (solo JSON, niente markdown code blocks attorno):
[
  {
    "q_it": "Domanda in italiano",
    "a_it": "Risposta testuale in markdown in italiano.",
    "q_en": "Question in english",
    "a_en": "Markdown answer in english."
  }
]

Documentazione:
{chunk_text}
"""

async def generate_qa_for_chunk(llm: LMStudioClient, chunk_text: str) -> list[dict[str, str]]:
    prompt = PROMPT_TEMPLATE.replace("{chunk_text}", chunk_text)
    try:
        # Chiamata al LLM (modello chat)
        response_text = ""
        # Simulate a chat request using LMStudioClient structure
        # LMStudioClient might have a different method for chat, assuming `chat` or we send a raw payload
        async for token in llm.stream_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        ):
            response_text += token

        # Pulisce eventuali markdown fences
        clean_json = response_text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        print(f"Errore generazione chunk: {e}")
        return []

async def main():
    print("🚀 Avvio generazione Brutal JSON (Q&A) da documentazione locale...")
    all_qa = []
    
    async with LMStudioClient() as llm:
        for path_str in DEFAULT_PATHS:
            file_path = REPO_ROOT / path_str
            if not file_path.exists():
                print(f"⚠️ Skip {path_str} (non trovato localmente)")
                continue
                
            print(f"\n📄 Analizzando {path_str}...")
            content = file_path.read_text("utf-8")
            chunks = chunk_markdown(content, source=path_str)
            
            for i, chunk in enumerate(chunks, 1):
                print(f"   Generazione Q&A per chunk {i}/{len(chunks)}...")
                qa_list = await generate_qa_for_chunk(llm, chunk.text)
                for qa in qa_list:
                    qa["source"] = chunk.source
                    if chunk.title:
                        qa["title"] = chunk.title
                    all_qa.append(qa)
                
                # Pausa minima per non sovraccaricare il LLM locale
                await asyncio.sleep(0.5)

    # Salvataggio del Brutal JSON in site/public
    out_dir = REPO_ROOT / "site" / "public"
    out_dir.mkdir(exist_ok=True, parents=True)
    
    out_file = out_dir / "brutal_qa_cache.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_qa, f, indent=2, ensure_ascii=False)
        
    print(f"\n✅ Completato! Generate {len(all_qa)} Q&A.")
    print(f"💾 Salvato in: {out_file}")

if __name__ == "__main__":
    asyncio.run(main())
