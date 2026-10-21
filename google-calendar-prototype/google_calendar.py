import json
import os

from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from enum import Enum

from googleapiclient.errors import HttpError


BASE_DIR = Path(__file__).resolve().parent

SYNC_FILE = BASE_DIR / os.environ.get(
    "AI_BOOKING_SYNC_FILE",
    "sync_state.json",
)

class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"

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

def create_booking(
    service,
    calendar_id: str,
    title: str,
    start: datetime,
    duration_minutes: int,
) -> dict:
    """Crea un appuntamento sul calendario Google di test."""

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendar ID di test non valido")

    if not title.strip():
        raise ValueError("Il titolo dell'appuntamento è obbligatorio")

    if duration_minutes <= 0:
        raise ValueError("La durata deve essere positiva")

    timezone = ZoneInfo("Europe/Rome")

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone)
    else:
        start = start.astimezone(timezone)

    end = start + timedelta(minutes=duration_minutes)

    event = {
        "summary": title.strip(),
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": "Europe/Rome",
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": "Europe/Rome",
        },
    }

    return service.events().insert(
        calendarId=calendar_id,
        body=event,
    ).execute()

def check_availability(
    service,
    calendar_id: str,
    start: datetime,
    duration_minutes: int,
) -> bool:
    """
    Verifica la disponibilità tramite Google Calendar FreeBusy.

    Restituisce:
        True: intervallo libero.
        False: intervallo occupato.

    Solleva un'eccezione se la disponibilità non è verificabile.
    """

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendar ID di test non valido")

    if duration_minutes <= 0:
        raise ValueError("La durata deve essere positiva")

    timezone = ZoneInfo("Europe/Rome")

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone)
    else:
        start = start.astimezone(timezone)

    end = start + timedelta(minutes=duration_minutes)

    response = service.freebusy().query(
        body={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": "Europe/Rome",
            "items": [{"id": calendar_id}],
        }
    ).execute()

    calendars = response.get("calendars")

    if not isinstance(calendars, dict):
        raise RuntimeError("Risposta FreeBusy non valida")

    calendar = calendars.get(calendar_id)

    if not isinstance(calendar, dict):
        raise RuntimeError("Calendario assente nella risposta FreeBusy")

    if calendar.get("errors"):
        raise RuntimeError("Google Calendar non ha verificato la disponibilità")

    busy = calendar.get("busy")

    if not isinstance(busy, list):
        raise RuntimeError("Informazioni FreeBusy mancanti")

    for interval in busy:
        if not isinstance(interval, dict):
            raise RuntimeError("Intervallo FreeBusy non valido")

        if not isinstance(interval.get("start"), str):
            raise RuntimeError("Inizio intervallo FreeBusy mancante")

        if not isinstance(interval.get("end"), str):
            raise RuntimeError("Fine intervallo FreeBusy mancante")

    return len(busy) == 0

def get_booking(
    service,
    calendar_id: str,
    event_id: str,
) -> dict:
    """Recupera un appuntamento dal calendario Google di test."""

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendar ID di test non valido")

    if not event_id or not event_id.strip():
        raise ValueError("Google Event ID obbligatorio")

    event = service.events().get(
        calendarId=calendar_id,
        eventId=event_id.strip(),
    ).execute()

    if event.get("status") == "cancelled":
        raise ValueError("L'appuntamento è stato cancellato")

    # M04 supporta soltanto eventi singoli con orario esplicito.
    if (
        "recurrence" in event
        or "recurringEventId" in event
        or "dateTime" not in event.get("start", {})
        or "dateTime" not in event.get("end", {})
    ):
        raise ValueError(
            "M04 supporta soltanto appuntamenti singoli con orario"
        )

    return event

def update_booking(
    service,
    calendar_id: str,
    event_id: str,
    new_start: datetime,
    expected_etag: str | None = None,
) -> dict:
    """
    Sposta un appuntamento esistente.

    Mantiene invariati titolo e durata.
    Non esegue il controllo disponibilità:
    questo verrà gestito separatamente.
    """

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendar ID di test non valido")

    if not event_id or not event_id.strip():
        raise ValueError("Google Event ID obbligatorio")

    # Recupera l'evento e verifica che sia supportato da M04.
    event = get_booking(
        service=service,
        calendar_id=calendar_id,
        event_id=event_id,
    )

    timezone = ZoneInfo("Europe/Rome")

    # Legge gli orari originali.
    original_start = datetime.fromisoformat(
        event["start"]["dateTime"]
    )

    original_end = datetime.fromisoformat(
        event["end"]["dateTime"]
    )

    # Calcola la durata originale in modo corretto.
    duration = (
        original_end.astimezone(ZoneInfo("UTC"))
        - original_start.astimezone(ZoneInfo("UTC"))
    )

    if duration <= timedelta(0):
        raise ValueError("Durata originale dell'evento non valida")

    # Normalizza il nuovo orario.
    if new_start.tzinfo is None:
        new_start = new_start.replace(tzinfo=timezone)
    else:
        new_start = new_start.astimezone(timezone)

    new_end = (
        new_start.astimezone(ZoneInfo("UTC")) + duration
    ).astimezone(ZoneInfo("Europe/Rome"))

    # Modifica esclusivamente gli orari.
    body = {
        "start": {
            "dateTime": new_start.isoformat(),
            "timeZone": "Europe/Rome",
        },
        "end": {
            "dateTime": new_end.isoformat(),
            "timeZone": "Europe/Rome",
        },
    }

    # Recupera la versione dell'evento.
    etag = event.get("etag")

    if not etag:
        raise ValueError(
            "ETag mancante: modifica dell'appuntamento bloccata"
        )

    # Verifica che l'evento sia ancora nella versione già controllata.
    if expected_etag is not None:
        if not expected_etag or etag != expected_etag:
            raise ValueError(
                "L'appuntamento è cambiato dopo la verifica iniziale. "
                "Modifica bloccata."
            )

    # Prepara la richiesta di modifica.
    request = service.events().patch(
        calendarId=calendar_id,
        eventId=event_id.strip(),
        body=body,
    )

    # Richiede che la versione dell'evento non sia cambiata.
    request.headers["If-Match"] = etag

    return request.execute()


def delete_booking(
    service,
    calendar_id: str,
    event_id: str,
    expected_etag: str,
) -> None:
    """
    Cancella un appuntamento dal calendario Google di test.

    Supporta soltanto appuntamenti singoli con orario esplicito.

    Blocca la cancellazione se l'evento è stato modificato
    dopo la richiesta iniziale.
    """

    # Impedisce operazioni sul calendario principale.
    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendar ID di test non valido")

    if not event_id or not event_id.strip():
        raise ValueError("Google Event ID obbligatorio")

    if not expected_etag:
        raise ValueError("ETag originale obbligatorio")

    # Recupera la versione corrente dell'appuntamento.
    event = get_booking(
        service=service,
        calendar_id=calendar_id,
        event_id=event_id,
    )

    current_etag = event.get("etag")

    if not current_etag:
        raise ValueError(
            "ETag mancante: cancellazione bloccata"
        )

    # Blocca la cancellazione se l'evento è cambiato.
    if current_etag != expected_etag:
        raise ValueError(
            "L'appuntamento è cambiato dopo la richiesta iniziale. "
            "Cancellazione bloccata."
        )

    # Prepara la richiesta DELETE.
    request = service.events().delete(
        calendarId=calendar_id,
        eventId=event_id.strip(),
    )

    # Protegge anche da modifiche concorrenti
    # tra il recupero dell'evento e la richiesta DELETE.
    request.headers["If-Match"] = expected_etag

    # Esegue la cancellazione.
    # Eventuali errori vengono propagati al chiamante.
    request.execute()


def list_events_in_interval(
    service,
    calendar_id: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Recupera gli eventi presenti in un intervallo."""

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendar ID di test non valido")

    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Gli orari devono includere il fuso orario")

    if end <= start:
        raise ValueError("Intervallo temporale non valido")

    events = []
    page_token = None

    while True:
        response = service.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            showDeleted=False,
            maxResults=250,
            pageToken=page_token,
        ).execute()

        if not isinstance(response.get("items"), list):
            raise RuntimeError("Risposta Google Calendar non valida")

        events.extend(response["items"])

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return events


def check_reschedule_availability(
    service,
    calendar_id: str,
    event_id: str,
    new_start: datetime,
    duration_minutes: int,
) -> bool:
    """
    Verifica la disponibilità per lo spostamento di un appuntamento.

    Esclude l'evento da modificare.

    True: nessuna sovrapposizione rilevata.
    False: sovrapposizione rilevata.

    Solleva un'eccezione se non può verificare gli eventi.
    """

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendar ID di test non valido")

    if not event_id or not event_id.strip():
        raise ValueError("Google Event ID obbligatorio")

    if duration_minutes <= 0:
        raise ValueError("La durata deve essere positiva")

    timezone = ZoneInfo("Europe/Rome")

    if new_start.tzinfo is None:
        new_start = new_start.replace(tzinfo=timezone)
    else:
        new_start = new_start.astimezone(timezone)

    new_end = new_start + timedelta(minutes=duration_minutes)

    events = list_events_in_interval(
        service=service,
        calendar_id=calendar_id,
        start=new_start,
        end=new_end,
    )

    for event in events:
        # Ignora l'appuntamento che stiamo modificando.
        if event.get("id") == event_id.strip():
            continue

        # Ignora gli eventi cancellati o trasparenti.
        if event.get("status") == "cancelled":
            continue

        if event.get("transparency") == "transparent":
            continue

        event_start_data = event.get("start", {})
        event_end_data = event.get("end", {})

        # Gli eventi senza orario esplicito vengono trattati
        # come occupati, per evitare falsi risultati di disponibilità.
        if (
            "dateTime" not in event_start_data
            or "dateTime" not in event_end_data
        ):
            return False

        event_start = datetime.fromisoformat(
            event_start_data["dateTime"]
        )

        event_end = datetime.fromisoformat(
            event_end_data["dateTime"]
        )

        # Due intervalli si sovrappongono quando:
        # inizio A < fine B e fine A > inizio B.
        if event_start < new_end and event_end > new_start:
            return False

    return True


def check_reschedule_availability_safe(
    service,
    calendar_id: str,
    event_id: str,
    new_start: datetime,
    duration_minutes: int,
) -> AvailabilityStatus:
    """
    Verifica conservativa della disponibilità per uno spostamento.

    AVAILABLE: intervallo verificato come libero.
    CONFLICT: sovrapposizione con un altro evento.
    UNKNOWN: verifica non attendibile.

    Non modifica alcun evento.
    """

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendar ID di test non valido")

    if not event_id or not event_id.strip():
        raise ValueError("Google Event ID obbligatorio")

    if duration_minutes <= 0:
        raise ValueError("La durata deve essere positiva")

    if not isinstance(new_start, datetime) or new_start.tzinfo is None:
        raise ValueError("Nuovo orario con fuso obbligatorio")

    utc = ZoneInfo("UTC")

    start_utc = new_start.astimezone(utc)
    end_utc = start_utc + timedelta(minutes=duration_minutes)

    try:
        # Verifica che l'evento originale esista e sia supportato.
        original_event = get_booking(
            service=service,
            calendar_id=calendar_id,
            event_id=event_id,
        )

        if original_event.get("id") != event_id.strip():
            return AvailabilityStatus.UNKNOWN

        # Verifica che la durata richiesta corrisponda
        # alla durata effettiva dell'appuntamento originale.

        original_intervals = extract_event_intervals([original_event])

        if len(original_intervals) != 1:
            return AvailabilityStatus.UNKNOWN

        original_start, original_end = original_intervals[0]

        actual_duration = original_end - original_start
        requested_duration = timedelta(minutes=duration_minutes)

        if actual_duration != requested_duration:
            return AvailabilityStatus.UNKNOWN

        # Recupera tutti gli eventi nella nuova finestra.
        events = list_events_in_interval(
            service=service,
            calendar_id=calendar_id,
            start=start_utc,
            end=end_utc,
        )

        # Non possiamo escludere correttamente eventi senza ID.
        if any(
            not isinstance(event, dict)
            or not isinstance(event.get("id"), str)
            or not event["id"].strip()
            for event in events
        ):
            return AvailabilityStatus.UNKNOWN

        # Verifica che non esistano identificativi duplicati.
        event_ids = [event["id"] for event in events]

        if len(event_ids) != len(set(event_ids)):
            return AvailabilityStatus.UNKNOWN

        # Ricostruisce l'occupazione complessiva.
        event_intervals = extract_event_intervals(events)

        event_intervals = clip_intervals(
            event_intervals,
            start_utc,
            end_utc,
        )

        # Recupera l'occupazione segnalata da FreeBusy.
        freebusy_intervals = get_freebusy_intervals(
            service=service,
            calendar_id=calendar_id,
            start=start_utc,
            end=end_utc,
        )

        freebusy_intervals = clip_intervals(
            freebusy_intervals,
            start_utc,
            end_utc,
        )

        # Se le due rappresentazioni non coincidono,
        # la disponibilità non è verificabile.
        if not compare_busy_intervals(
            event_intervals,
            freebusy_intervals,
        ):
            return AvailabilityStatus.UNKNOWN

        # La verifica è coerente: possiamo cercare conflitti.
        target_id = event_id.strip()

        for event in events:
            if event["id"] == target_id:
                continue

            if event.get("status") == "cancelled":
                continue

            if event.get("transparency") == "transparent":
                continue

            # extract_event_intervals ha già validato
            # gli intervalli degli eventi bloccanti.
            intervals = extract_event_intervals([event])

            if clip_intervals(intervals, start_utc, end_utc):
                return AvailabilityStatus.CONFLICT

        return AvailabilityStatus.AVAILABLE

    except Exception:
        # Un errore di Google o dati inattesi
        # non devono autorizzare una modifica.
        return AvailabilityStatus.UNKNOWN


def get_freebusy_intervals(
    service,
    calendar_id: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """
    Recupera gli intervalli occupati da Google FreeBusy.

    Non modifica eventi.
    Solleva un'eccezione se la risposta non è attendibile.
    """

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendar ID di test non valido")

    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Gli orari devono includere il fuso orario")

    if end <= start:
        raise ValueError("Intervallo temporale non valido")

    response = service.freebusy().query(
        body={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": "Europe/Rome",
            "items": [{"id": calendar_id}],
        }
    ).execute()

    if not isinstance(response, dict):
        raise RuntimeError("Risposta FreeBusy non valida")

    calendars = response.get("calendars")

    if not isinstance(calendars, dict):
        raise RuntimeError("Calendari FreeBusy mancanti")

    calendar_data = calendars.get(calendar_id)

    if not isinstance(calendar_data, dict):
        raise RuntimeError("Calendario assente dalla risposta FreeBusy")

    if calendar_data.get("errors"):
        raise RuntimeError("Google FreeBusy ha restituito errori")

    busy = calendar_data.get("busy")

    if not isinstance(busy, list):
        raise RuntimeError("Intervalli FreeBusy non validi")

    intervals = []

    for interval in busy:
        if not isinstance(interval, dict):
            raise RuntimeError("Intervallo FreeBusy non valido")

        try:
            busy_start = datetime.fromisoformat(interval["start"])
            busy_end = datetime.fromisoformat(interval["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "Orari FreeBusy mancanti o non validi"
            ) from exc

        if (
            busy_start.tzinfo is None
            or busy_end.tzinfo is None
            or busy_end <= busy_start
        ):
            raise RuntimeError("Intervallo FreeBusy non valido")

        intervals.append((busy_start, busy_end))

    return intervals


def merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """
    Unisce intervalli temporali sovrapposti o consecutivi.

    Confronta gli istanti in UTC per evitare problemi
    con fusi orari e cambi tra ora legale e ora solare.
    """

    utc = ZoneInfo("UTC")
    normalized = []

    for start, end in intervals:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Gli intervalli devono avere un fuso orario")

        start_utc = start.astimezone(utc)
        end_utc = end.astimezone(utc)

        if end_utc <= start_utc:
            raise ValueError("Intervallo temporale non valido")

        normalized.append((start_utc, end_utc))

    if not normalized:
        return []

    normalized.sort(key=lambda interval: interval[0])

    merged = [normalized[0]]

    for current_start, current_end in normalized[1:]:
        previous_start, previous_end = merged[-1]

        if current_start <= previous_end:
            merged[-1] = (
                previous_start,
                max(previous_end, current_end),
            )
        else:
            merged.append((current_start, current_end))

    return merged


def compare_busy_intervals(
    event_intervals: list[tuple[datetime, datetime]],
    freebusy_intervals: list[tuple[datetime, datetime]],
) -> bool:
    """
    Confronta l'occupazione ricostruita dagli eventi
    con quella restituita da Google FreeBusy.

    Restituisce True se gli intervalli coincidono,
    False altrimenti.

    Solleva ValueError per intervalli non validi.
    """

    merged_events = merge_intervals(event_intervals)
    merged_freebusy = merge_intervals(freebusy_intervals)

    return merged_events == merged_freebusy


def extract_event_intervals(
    events: list[dict],
) -> list[tuple[datetime, datetime]]:
    """
    Estrae gli intervalli occupati dagli eventi Google Calendar.

    Include gli eventi di un'intera giornata.
    Ignora eventi cancellati e trasparenti.

    Solleva ValueError se i dati non sono attendibili.
    """

    utc = ZoneInfo("UTC")
    rome = ZoneInfo("Europe/Rome")

    intervals = []

    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Evento Google non valido")

        if event.get("status") == "cancelled":
            continue

        if event.get("transparency") == "transparent":
            continue

        start_data = event.get("start")
        end_data = event.get("end")

        if not isinstance(start_data, dict):
            raise ValueError("Inizio evento non valido")

        if not isinstance(end_data, dict):
            raise ValueError("Fine evento non valida")

        if "dateTime" in start_data and "dateTime" in end_data:
            try:
                start = datetime.fromisoformat(
                    start_data["dateTime"]
                )
                end = datetime.fromisoformat(
                    end_data["dateTime"]
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Orario evento non valido"
                ) from exc

            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError(
                    "Fuso orario evento mancante"
                )

        elif "date" in start_data and "date" in end_data:
            try:
                start_date = datetime.strptime(
                    start_data["date"], "%Y-%m-%d"
                ).date()

                end_date = datetime.strptime(
                    end_data["date"], "%Y-%m-%d"
                ).date()
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Data evento non valida"
                ) from exc

            # Google Calendar utilizza una data finale esclusiva.
            start = datetime.combine(
                start_date,
                datetime.min.time(),
                tzinfo=rome,
            )

            end = datetime.combine(
                end_date,
                datetime.min.time(),
                tzinfo=rome,
            )

        else:
            raise ValueError(
                "Formato temporale evento non supportato"
            )

        start_utc = start.astimezone(utc)
        end_utc = end.astimezone(utc)

        if end_utc <= start_utc:
            raise ValueError(
                "Durata evento non valida"
            )

        intervals.append((start_utc, end_utc))

    return intervals


def clip_intervals(
    intervals: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """
    Limita gli intervalli a una finestra temporale.

    Normalizza gli orari in UTC.
    Esclude gli intervalli completamente esterni.
    """

    utc = ZoneInfo("UTC")

    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("La finestra deve avere un fuso orario")

    start_utc = window_start.astimezone(utc)
    end_utc = window_end.astimezone(utc)

    if end_utc <= start_utc:
        raise ValueError("Finestra temporale non valida")

    result = []

    for start, end in intervals:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Intervallo senza fuso orario")

        start = start.astimezone(utc)
        end = end.astimezone(utc)

        if end <= start:
            raise ValueError("Intervallo non valido")

        clipped_start = max(start, start_utc)
        clipped_end = min(end, end_utc)

        if clipped_start < clipped_end:
            result.append((clipped_start, clipped_end))

    return result