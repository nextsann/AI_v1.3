import streamlit as st
from google import genai
from google.genai import types
import os
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime

# --- CONFIGURATION ---
if "GEMINI_API_KEY" not in st.secrets:
    st.error("⚠️ Missing API Key.")
    st.stop()

if "GOOGLE_TOKEN" not in st.secrets:
    st.error("⚠️ Missing Google Token.")
    st.stop()

# --- TOOLS ---
def get_calendar_service():
    token_info = json.loads(st.secrets["GOOGLE_TOKEN"])
    creds = Credentials.from_authorized_user_info(token_info)
    return build('calendar', 'v3', credentials=creds)

def list_upcoming_events():
    """Get the next 10 events. Returns ID so we can delete them."""
    service = get_calendar_service()
    now = datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(
        calendarId='primary', timeMin=now,
        maxResults=10, singleEvents=True,
        orderBy='startTime').execute()
    events = events_result.get('items', [])
    
    if not events:
        return "No upcoming events found."
    
    event_list = []
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        # WE ADDED THE ID HERE SO THE AI CAN SEE IT
        event_list.append(f"ID: {event['id']} | Date: {start} | Event: {event['summary']}")
    return "\n".join(event_list)

def create_calendar_event(summary: str, start_time: str, end_time: str):
    """Create event. Times must be ISO format (e.g. 2025-11-22T15:00:00)."""
    service = get_calendar_service()
    event = {
        'summary': summary,
        'start': {'dateTime': start_time, 'timeZone': 'UTC'},
        'end': {'dateTime': end_time, 'timeZone': 'UTC'},
    }
    event = service.events().insert(calendarId='primary', body=event).execute()
    return f"Event created: {event.get('htmlLink')}"

# --- NEW TOOL: DELETE ---
def delete_calendar_event(event_id: str):
    """Delete an event using its ID."""
    try:
        service = get_calendar_service()
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return "Event deleted successfully."
    except Exception as e:
        return f"Error deleting event: {e}"

# --- SETUP ---
client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

# Register all 3 tools
tools_list = [list_upcoming_events, create_calendar_event, delete_calendar_event]
google_search_tool = types.Tool(google_search=types.GoogleSearch())

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

    # Updated Instruction to explain how to delete
    sys_instruct = """You are a talented secretary of latin descent. Your nickname for me is papasito. 
    You have access to my Google Calendar.
    IMPORTANT: To delete an event, you must FIRST use 'list_upcoming_events' to find the event's ID, and THEN use 'delete_calendar_event' with that ID.
    Never make up an ID. Always check the list first."""
    
    if gemini_history and gemini_history[-1].role == "user":
        gemini_history[-1].parts[0].text += f"\n\n(SYSTEM REMINDER: {sys_instruct})"

    with st.chat_message("assistant"):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=gemini_history,
                config=types.GenerateContentConfig(
                    system_instruction=sys_instruct,
                    tools=tools_list + [google_search_tool]
                )
            )

            # THE LOOP (Handles multiple steps: List -> Delete)
            while response.function_calls:
                parts = []
                # Add the model's request to history logic
                gemini_history.append(response.candidates[0].content)

                for fc in response.function_calls:
                    fn_name = fc.name
                    fn_args = fc.args
                    st.status(f"Running tool: {fn_name}...", state="running")
                    
                    if fn_name == "list_upcoming_events":
                        result = list_upcoming_events()
                    elif fn_name == "create_calendar_event":
                        result = create_calendar_event(**fn_args)
                    elif fn_name == "delete_calendar_event":
                        result = delete_calendar_event(**fn_args)
                    else:
                        result = "Error: Unknown tool."
                    
                    parts.append(types.Part.from_function_response(
                        name=fn_name,
                        response={"result": result}
                    ))
                
                # Send tool results back to Brain
                response = client.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=gemini_history + [types.Content(role="user", parts=parts)],
                    config=types.GenerateContentConfig(system_instruction=sys_instruct)
                )

            st.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})

        except Exception as e:
            st.error(f"An error occurred: {e}")