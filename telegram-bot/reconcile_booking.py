"""Manual CLI; requires the bot to be stopped. No Google writes."""
import argparse
from pathlib import Path
from database import get_connection
from reconciliation_service import reconcile_one
from runtime_guard import OWNER_REF, runtime_guard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation_id", type=int)
    parser.add_argument("--preview", action="store_true", help="Legge Google senza aggiornare il DB")
    args = parser.parse_args()
    if args.operation_id <= 0:
        parser.error("operation_id deve essere positivo")
    try:
        with runtime_guard():
            config = {}
            for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    key, separator, value = line.partition("=")
                    if separator:
                        config[key.strip()] = value.strip()
            calendar_id = config.get("GOOGLE_TEST_CALENDAR_ID", "")
            if not calendar_id or calendar_id.strip().lower() == "primary":
                raise ValueError("Calendario non valido")

            def service_factory():
                # Lazy import: legacy/missing context never invokes authentication.
                from booking_service import authenticate, build
                return build("calendar", "v3", credentials=authenticate())

            with get_connection(config.get("POSTGRES_PASSWORD", "")) as connection:
                outcome = reconcile_one(connection, args.operation_id, service_factory,
                                        calendar_id, OWNER_REF, args.preview)
            # Context manager has committed before any successful report.
            print(("ANTEPRIMA: " if args.preview else "ESITO: ") + outcome)
            return 0
    except Exception:
        print("Operazione non confermata: verificare lock, configurazione e PostgreSQL. "
              "Nessuna scrittura Google; un eventuale commit DB va verificato prima di concludere.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
