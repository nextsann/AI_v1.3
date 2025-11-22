import streamlit as st
from google import genai
from google.genai import types
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime
from duckduckgo_search import DDGS  # <--- NEW IMPORT

# --- CONFIGURATION ---
if "GEMINI_API_KEY" not in st.secrets:
    st.error("⚠️ Missing API Key.")
    st.stop()

if "GOOGLE_TOKEN" not in st.secrets:
    st.error("⚠️ Missing Google Token.")
    st.stop()

# --- TOOL 1: CALENDAR (UNCHANGED) ---
def get_calendar_service():
    token_info = json.loads(st.secrets["GOOGLE_TOKEN"])
    creds = Credentials.from_authorized_user_info(token_info)
    return build('calendar', 'v3', credentials=creds)

def list_upcoming_events():
    """Get the next 10 events. Returns ID so we can delete them."""
    try:
        service = get_calendar_service()
        now = datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=10, singleEvents=True,
            orderBy='startTime').execute()
        events = events_result.get('items', [])
        if not events: return "No upcoming events found."
        return "\n".join([f"ID: {e['id']} | {e['start'].get('dateTime', e['start'].get('date'))}: {e['summary']}" for e in events])
    except Exception as e: return f"Error: {e}"

def create_calendar_event(summary: str, start_time: str, end_time: str):
    """Create event. Times must be ISO format (e.g. 2025-11-22T15:00:00)."""
    try:
        service = get_calendar_service()
        event = {'summary': summary, 'start': {'dateTime': start_time}, 'end': {'dateTime': end_time}}
        event = service.events().insert(calendarId='primary', body=event).execute()
        return f"Event created: {event.get('htmlLink')}"
    except Exception as e: return f"Error: {e}"

def delete_calendar_event(event_id: str):
    """Delete an event using its ID."""
    try:
        service = get_calendar_service()
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return "Event deleted successfully."
    except Exception as e: return f"Error: {e}"

# --- TOOL 2: WEB SEARCH (NEW!) ---
def search_web(query: str):
    """Search the web for real-time information (news, sports, stocks)."""
    try:
        results = DDGS().text(query, max_results=5)
        if not results: return "No results found."
        # Format the results nicely for the AI
        return "\n\n".join([f"Title: {r['title']}\nURL: {r['href']}\nSnippet: {r['body']}" for r in results])
    except Exception as e: return f"Search Error: {e}"

# --- SETUP ---
client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

# We just add 'search_web' to the list. No special "GoogleSearch" object needed.
tools_list = [list_upcoming_events, create_calendar_event, delete_calendar_event, search_web]

# --- APP ---
st.title("Mimi Bebesita 💃")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Que pasa?"):
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    gemini_history = []
    for msg in st.session_state.messages:
        role = "model" if msg["role"] == "assistant" else "user"
        gemini_history.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    sys_instruct = """You are a talented secretary of latin descent. Your nickname for me is papasito.
    You have access to my Google Calendar and the Web.
    - If I ask about my schedule, check the calendar.
    - If I ask about news/sports/facts, use 'search_web'.
    - If I ask to delete, list events first to get the ID.
    """
    
    if gemini_history and gemini_history[-1].role == "user":
        gemini_history[-1].parts[0].text += f"\n\n(SYSTEM REMINDER: {sys_instruct})"

    with st.chat_message("assistant"):
        try:
            # 1. Ask Gemini
            # NOTE: We ONLY pass 'tools=tools_list'. We removed the conflicting config.
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=gemini_history,
                config=types.GenerateContentConfig(
                    system_instruction=sys_instruct,
                    tools=tools_list 
                )
            )

            # 2. Tool Loop
            while response.function_calls:
                parts = []
                gemini_history.append(response.candidates[0].content)

                for fc in response.function_calls:
                    fn_name = fc.name
                    fn_args = fc.args
                    st.status(f"Running tool: {fn_name}...", state="running")
                    
                    if fn_name == "list_upcoming_events": result = list_upcoming_events()
                    elif fn_name == "create_calendar_event": result = create_calendar_event(**fn_args)
                    elif fn_name == "delete_calendar_event": result = delete_calendar_event(**fn_args)
                    elif fn_name == "search_web": result = search_web(**fn_args) # <--- Calls our new search
                    else: result = "Error: Unknown tool."
                    
                    parts.append(types.Part.from_function_response(name=fn_name, response={"result": result}))
                
                response = client.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=gemini_history + [types.Content(role="user", parts=parts)],
                    config=types.GenerateContentConfig(system_instruction=sys_instruct)
                )

            st.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})

        except Exception as e:
            st.error(f"An error occurred: {e}")