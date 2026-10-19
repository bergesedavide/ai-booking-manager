import json, os, tempfile, sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError
from httplib2 import Response

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

def main() -> None:
    # File temporaneo: non tocchiamo lo stato reale.
    with tempfile.TemporaryDirectory() as temp_dir:
        sync_file = Path(temp_dir) / "sync_state.json"

        sync_file.write_text(
            json.dumps({"sync_token": "OLD_TOKEN"})
        )

        os.environ["AI_BOOKING_SYNC_FILE"] = str(sync_file)

        # Importiamo main dopo aver configurato la variabile.
        from google_calendar import sync_calendar

        # Simuliamo Google Calendar.
        service = MagicMock()

        expired_token_error = HttpError(
            Response({"status": 410}),
            b'{"error": {"message": "Sync token expired"}}',
        )

        service.events.return_value.list.return_value.execute.side_effect = [
            expired_token_error,
            {
                "items": [
                    {
                        "id": "test-event-123",
                        "summary": "Evento recuperato",
                        "status": "confirmed",
                    }
                ],
                "nextSyncToken": "NEW_VALID_TOKEN",
            },
        ]

        # Eseguiamo la sincronizzazione.
        sync_calendar(service)

        # Verifichiamo il risultato.
        state = json.loads(sync_file.read_text())

        assert state["sync_token"] == "NEW_VALID_TOKEN"

        calls = service.events.return_value.list.call_args_list

        assert len(calls) == 2
        assert calls[0].kwargs["syncToken"] == "OLD_TOKEN"
        assert "syncToken" not in calls[1].kwargs

        print("\nTEST SUPERATO: recupero HTTP 410 funzionante!")


if __name__ == "__main__":
    main()