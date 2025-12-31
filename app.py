import os
import chainlit as cl
from dotenv import load_dotenv
from openai import AsyncOpenAI
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.types import ThreadDict

# Load env vars
load_dotenv()

# OpenAI client
openai_client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

# Model settings
SETTINGS = {
    "model": "gpt-4o-mini",
    "temperature": 0,
    "max_tokens": 500,
}

# -------------------------
# Data layer with proper initialization
# -------------------------
cl.data._data_layer = None

@cl.data_layer
def get_data_layer():
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        print("  WARNING: DATABASE_URL not set. Chat history won't be saved.")
        return None
    
    try:
        # Create data layer with show_logger to see what's happening
        data_layer = SQLAlchemyDataLayer(
            conninfo=database_url,
            show_logger=False  # Set to True if you want SQL logs
        )
        print(" Database connection established")
        return data_layer
    except Exception as e:
        print(f" Database connection failed: {e}")
        print("  Continuing without persistence...")
        return None

# -------------------------
# Restore chat history
# -------------------------
@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    chat_history = []

    for step in thread.get("steps", []):
        msg_type = step.get("type")
        output = step.get("output", "")
        
        if msg_type == "user_message":
            chat_history.append({"role": "user", "content": output})
        elif msg_type == "assistant_message":
            chat_history.append({"role": "assistant", "content": output})

    cl.user_session.set("chat_history", chat_history)

# -------------------------
# Main message handler
# -------------------------
@cl.on_message
async def on_message(message: cl.Message):
    chat_history = cl.user_session.get("chat_history", [])

    chat_history.append(
        {"role": "user", "content": message.content}
    )

    response = await openai_client.chat.completions.create(
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Always reply in English."},
            *chat_history
        ],
        **SETTINGS
    )

    reply = response.choices[0].message.content

    chat_history.append(
        {"role": "assistant", "content": reply}
    )

    cl.user_session.set("chat_history", chat_history)

    await cl.Message(content=reply).send()

# -------------------------
# Authentication
# -------------------------
@cl.password_auth_callback
def auth_callback(username: str, password: str):
    if username == "admin" and password == "admin":
        return cl.User(
            identifier="admin",
            metadata={"role": "admin", "provider": "credentials"}
        )
    return None

# -------------------------
# Chat start
# -------------------------
@cl.on_chat_start
async def on_chat_start():
    # Initialize empty chat history
    cl.user_session.set("chat_history", [])
    
    user = cl.user_session.get("user")
    name = user.identifier if user else "there"
    await cl.Message(content=f"Hello {name} ").send()