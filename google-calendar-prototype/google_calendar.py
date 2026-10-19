import json
import os

from pathlib import Path

from googleapiclient.errors import HttpError


BASE_DIR = Path(__file__).resolve().parent

SYNC_FILE = BASE_DIR / os.environ.get(
    "AI_BOOKING_SYNC_FILE",
    "sync_state.json",
)


def sync_calendar(service) -> None:
    sync_token = None

    if SYNC_FILE.exists():
        state = json.loads(SYNC_FILE.read_text())
        sync_token = state.get("sync_token")

    def fetch_events(token: str | None) -> tuple[list[dict], str]:
        params = {
            "calendarId": "primary",
            "maxResults": 250,
        }

        if token:
            params["syncToken"] = token

        events = []

        while True:
            response = service.events().list(**params).execute()

            events.extend(response.get("items", []))

            if "nextPageToken" in response:
                params["pageToken"] = response["nextPageToken"]
            else:
                return events, response["nextSyncToken"]

    try:
        if sync_token:
            print("Sincronizzazione incrementale...")
        else:
            print("Sincronizzazione completa...")

        events, new_token = fetch_events(sync_token)

    except HttpError as error:
        if error.resp.status != 410:
            raise

        print("SyncToken non valido. Avvio sincronizzazione completa...")
        events, new_token = fetch_events(None)

    print(f"Eventi ricevuti: {len(events)}")

    for event in events:
        print(
            f"- {event.get('summary', 'Senza titolo')} "
            f"| ID: {event['id']} "
            f"| Stato: {event.get('status')}"
        )

    SYNC_FILE.write_text(
        json.dumps({"sync_token": new_token}, indent=2)
    )

    print("SyncToken aggiornato.")