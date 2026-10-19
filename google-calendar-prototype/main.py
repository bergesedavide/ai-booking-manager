from googleapiclient.discovery import build

from google_auth import authenticate
from google_calendar import sync_calendar


def main() -> None:
    print("AI Booking Manager — Google Calendar Prototype\n")

    credentials = authenticate()

    service = build(
        "calendar",
        "v3",
        credentials=credentials,
    )

    sync_calendar(service)


if __name__ == "__main__":
    main()