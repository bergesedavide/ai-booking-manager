import re
from dataclasses import replace

from ai.schemas import AppointmentIntent, validate_appointment_intent, normalize_time
from ai.intent_checks import missing_creation_fields
from ai.duration_utils import extract_duration
from ai.date_utils import normalize_relative_date
from ai.interpreter import AppointmentInterpreter

class ConversationManager:
    """Gestisce lo stato temporaneo di una conversazione."""

    def __init__(self) -> None:
        self.current_intent: AppointmentIntent | None = None
        self.request_key: str | None = None

    def start(self, intent: AppointmentIntent, request_key: str,) -> None:
        """Memorizza una nuova richiesta."""
        self.current_intent = intent
        self.request_key = request_key

    def reset(self) -> None:
        """Elimina la richiesta corrente."""
        self.current_intent = None
        self.request_key = None

    def next_question(self) -> str | None:
        """Restituisce una domanda per il primo campo mancante."""

        if self.current_intent is None:
            return None

        if self.current_intent.operazione != "crea":
            return None

        missing = missing_creation_fields(self.current_intent)

        if not missing:
            return None

        questions = {
            "cliente": "Per quale cliente vuoi prenotare?",
            "data": "Per quale giorno vuoi prenotare?",
            "ora": "A che ora vuoi prenotare l'appuntamento?",
            "durata_minuti": "Quanto deve durare l'appuntamento?",
        }

        return questions[missing[0]]

    def update_field(self, field: str, value: str | int) -> None:
        """Aggiorna un campo della richiesta corrente."""

        if self.current_intent is None:
            raise ValueError("Nessuna conversazione attiva.")

        allowed_fields = {
            "cliente",
            "data",
            "ora",
            "durata_minuti",
        }

        if field not in allowed_fields:
            raise ValueError(f"Campo non modificabile: {field}")

        from dataclasses import asdict

        data = asdict(self.current_intent)
        data[field] = value

        self.current_intent = validate_appointment_intent(data)

    def handle_time_reply(self, text: str) -> bool:
        """Riconosce un orario e aggiorna la richiesta corrente."""

        if self.current_intent is None:
            return False

        # Una durata non deve essere interpretata come un orario.
        duration_words = (
            "minuto",
            "minuti",
            "ora di allenamento",
            "ore di allenamento",
        )

        normalized = text.strip().lower()

        if any(word in normalized for word in duration_words):
            return False
        
        # Una data numerica non deve essere interpretata come un orario.
        numeric_date_pattern = (
            r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?\b"
        )

        if re.search(numeric_date_pattern, normalized):
            return False


        match = re.search(
            r"\b(?:alle\s+)?(\d{1,2}(?::\d{2})?)\b",
            text.lower(),
        )

        if match is None:
            return False

        try:
            time = normalize_time(match.group(1))
        except ValueError:
            return False

        self.update_field("ora", time)

        return True

    def handle_duration_reply(self, text: str) -> bool:
        """Riconosce una durata e aggiorna la richiesta corrente."""

        if self.current_intent is None:
            return False

        duration = extract_duration(text)

        if duration is None:
            return False

        self.update_field("durata_minuti", duration)

        return True

    def handle_reply(self, text: str, interpreter: AppointmentInterpreter | None = None, request_key: str | None = None,) -> bool:
        """Gestisce una risposta relativa al primo campo mancante."""

        if self.current_intent is None:
            return False

        if self.handle_cancellation(text):
            return True

        if self.is_new_cancellation_request(text):
            if interpreter is None:
                return False

            return self.handle_intent_change(text, interpreter, request_key)

        if self.handle_time_correction(text):
            return True

        if self.handle_duration_correction(text):
            return True

        if self.current_intent.operazione != "crea":
            return False

        missing = missing_creation_fields(self.current_intent)

        if not missing:
            return False

        field = missing[0]

        if field == "cliente":
            return self.handle_client_reply(text)

        if field == "data":
            return self.handle_date_reply(text)

        if field == "ora":
            return self.handle_time_reply(text)

        if field == "durata_minuti":
            return self.handle_duration_reply(text)

        return False

    def handle_client_reply(self, text: str) -> bool:
        """Aggiorna il cliente quando viene richiesto il suo nome."""

        if self.current_intent is None:
            return False

        name = text.strip()

        if not name:
            return False

        self.update_field("cliente", name)

        return True

    def handle_date_reply(self, text: str) -> bool:
        """Riconosce una data e aggiorna la richiesta corrente."""

        if self.current_intent is None:
            return False

        try:
            normalized_date = normalize_relative_date(text.strip())
        except ValueError:
            return False

        if normalized_date is None:
            return False

        self.update_field("data", normalized_date)

        return True

    def handle_cancellation(self, text: str) -> bool:
        """Interrompe la conversazione su richiesta esplicita."""

        cancellation_phrases = {
            "annulla",
            "annulla tutto",
            "lascia perdere",
            "non importa",
            "niente",
            "fermati",
        }

        normalized = text.strip().lower().rstrip(".!?")

        if normalized not in cancellation_phrases:
            return False

        if self.current_intent is None:
            return False

        self.reset()
        return True

    def handle_time_correction(self, text: str) -> bool:
        """Gestisce una correzione esplicita dell'orario."""

        if self.current_intent is None:
            return False

        if self.current_intent.operazione != "crea":
            return False

        normalized = text.strip().lower()

        # Riconosciamo soltanto correzioni esplicite.
        correction_markers = (
            "anzi",
            "invece",
            "facciamo",
            "sposta alle",
            "no, alle",
            "no alle",
        )

        if not any(marker in normalized for marker in correction_markers):
            return False

        # Richiediamo un riferimento esplicito all'orario.
        if "alle " not in normalized:
            return False

        return self.handle_time_reply(text)

    def handle_duration_correction(self, text: str) -> bool:
        """Gestisce una correzione esplicita della durata."""

        if self.current_intent is None:
            return False

        if self.current_intent.operazione != "crea":
            return False

        normalized = text.strip().lower()

        correction_markers = (
            "anzi",
            "invece",
            "facciamo",
            "sposta alle",
            "no, alle",
            "no alle",
        )

        if not any(marker in normalized for marker in correction_markers):
            return False

        return self.handle_duration_reply(text)

    def change_intent(self, new_intent: AppointmentIntent, request_key: str) -> None:
        """Sostituisce completamente la richiesta precedente."""

        self.reset()
        self.start(new_intent, request_key)

    def is_new_cancellation_request(self, text: str) -> bool:
        """Riconosce una nuova richiesta esplicita di cancellazione."""

        if self.current_intent is None:
            return False

        normalized = text.strip().lower()

        cancellation_verbs = (
            "cancella",
            "elimina",
            "rimuovi",
        )

        appointment_words = (
            "appuntamento",
            "prenotazione",
            "sessione",
        )

        has_cancellation_verb = any(
            re.search(rf"\b{verb}\b", normalized)
            for verb in cancellation_verbs
        )

        has_appointment_reference = any(
            re.search(rf"\b{word}\b", normalized)
            for word in appointment_words
        )

        return has_cancellation_verb and has_appointment_reference

    def handle_intent_change(
        self,
        text: str,
        interpreter: "AppointmentInterpreter",
        request_key: str,
    ) -> bool:
        """Interpreta e gestisce una nuova richiesta di cancellazione."""

        if not self.is_new_cancellation_request(text):
            return False

        new_intent = interpreter.interpret(text)

        if new_intent.operazione != "cancella":
            return False

        self.change_intent(new_intent, request_key)
        return True