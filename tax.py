import os
import chainlit as cl
from dotenv import load_dotenv
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.types import ThreadDict
import aiohttp
import asyncio
import fitz
from docx import Document

# --------------------------------------------------
# ENV
# --------------------------------------------------
load_dotenv()
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")

# --------------------------------------------------
# STRICT CITATION PROMPT
# --------------------------------------------------
def enforce_citation_prompt(user_message: str):
    return f"""
You are a regulated tax consultant.

MANDATORY RULES (NON-NEGOTIABLE):
- Every response MUST include citations
- Citations MUST reference real tax laws, regulations, or official guidance
- Return ONLY valid JSON in the format below

FORMAT:
{{
  "answer": "Clear and professional response",
  "citations": [
    {{
      "source": "Document name",
      "section": "Section or clause",
      "reference": "Exact legal citation"
    }}
  ]
}}

If citations cannot be provided, respond with:
{{
  "answer": "I cannot answer this question with certainty.",
  "citations": []
}}

User question:
{user_message}
"""

# --------------------------------------------------
# NORMALIZE & ENFORCE CITATIONS
# --------------------------------------------------
def extract_text(data):
    if not data:
        return " No response received.\n\n Sources: None"

    if isinstance(data, list):
        data = data[0]

    if not isinstance(data, dict):
        return f"{str(data)}\n\n Sources: None"

    answer = (
        data.get("answer")
        or data.get("response")
        or data.get("output")
        or data.get("cleanedResponse")
        or ""
    )

    citations = data.get("citations", [])

    if not citations:
        return (
            f"{answer or 'Response rejected.'}\n\n"
            ""
        )

    formatted_sources = "\n".join(
        f"- {c.get('source')} ({c.get('reference', c.get('section', ''))})"
        for c in citations
    )

    return f"{answer.strip()}\n\n Sources:\n{formatted_sources}"

# --------------------------------------------------
# DOCUMENT READER
# --------------------------------------------------
def read_documents(documents):
    text = ""
    for document in documents:
        text += f"\n\nFILE: {document.name}\n"

        if document.path.endswith(".pdf"):
            doc = fitz.open(document.path)
            for page in doc:
                text += page.get_text()

        elif document.path.endswith(".docx"):
            doc = Document(document.path)
            text += "\n".join(p.text for p in doc.paragraphs)

        elif document.path.endswith(".txt"):
            with open(document.path, "r", encoding="utf-8") as f:
                text += f.read()

    return text

# --------------------------------------------------
# n8n WEBHOOK CALL
# --------------------------------------------------
async def call_n8n_chain(user_message: str, document_context: str = None):
    if not N8N_WEBHOOK_URL:
        return " N8N_WEBHOOK_URL not configured."

    enforced_message = enforce_citation_prompt(user_message)

    if document_context:
        enforced_message = (
            f"DOCUMENT CONTEXT (FOR CITATION ONLY):\n"
            f"{document_context}\n\n"
            f"{enforced_message}"
        )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                N8N_WEBHOOK_URL,
                json={"chatInput": enforced_message},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:

                if response.status != 200:
                    return f" n8n returned status {response.status}"

                content_type = response.headers.get("Content-Type", "")
                if "application/json" not in content_type:
                    return " n8n returned invalid content type."

                data = await response.json()
                return extract_text(data)

    except aiohttp.ClientTimeout:
        return " Request to n8n timed out."
    except Exception as e:
        return f" Unexpected error: {str(e)}"

# --------------------------------------------------
# DATABASE PERSISTENCE
# --------------------------------------------------
cl.data._data_layer = None

@cl.data_layer
def get_data_layer():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print(" DATABASE_URL not set. Persistence disabled.")
        return None

    try:
        print("✓ Database connected")
        return SQLAlchemyDataLayer(conninfo=database_url, show_logger=False)
    except Exception as e:
        print(f" Database error: {e}")
        return None

# --------------------------------------------------
# RESTORE CHAT HISTORY
# --------------------------------------------------
@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    history = []

    for step in thread.get("steps", []):
        if step.get("type") == "user_message":
            history.append({"role": "user", "content": step.get("output", "")})
        elif step.get("type") == "assistant_message":
            history.append({"role": "assistant", "content": step.get("output", "")})

    cl.user_session.set("chat_history", history)

# --------------------------------------------------
# MAIN MESSAGE HANDLER
# --------------------------------------------------
@cl.on_message
async def on_message(message: cl.Message):
    chat_history = cl.user_session.get("chat_history", [])
    document_context = None

    # Handle file uploads
    files = [f for f in message.elements if f]

    if files:
        async with cl.Step(name="Reading documents", type="tool") as step:
            loop = asyncio.get_event_loop()
            document_context = await loop.run_in_executor(None, read_documents, files)
            step.output = f"Processed {len(files)} document(s)"
            await step.update()

    msg = cl.Message(content="")
    await msg.send()

    reply = await call_n8n_chain(message.content, document_context)

    msg.content = reply.strip()
    await msg.update()

    chat_history.extend([
        {"role": "user", "content": message.content},
        {"role": "assistant", "content": reply},
    ])
    cl.user_session.set("chat_history", chat_history)

# --------------------------------------------------
# AUTH
# --------------------------------------------------
@cl.password_auth_callback
def auth_callback(username: str, password: str):
    if username == "admin" and password == "admin":
        return cl.User(identifier="admin")
    return None

# --------------------------------------------------
# CHAT START
# --------------------------------------------------
@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("chat_history", [])

    user = cl.user_session.get("user")
    name = user.identifier if user else "there"

    await cl.Message(
        content=(
            f"Hello {name} \n\n"
            "I am a **tax compliance assistant**.\n"
            "✔ All answers include legal citations\n"
            "✔ You may upload PAYSLIPS, PDFs, DOCX, or TXT files\n"
        )
    ).send()
