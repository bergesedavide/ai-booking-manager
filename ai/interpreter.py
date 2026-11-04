import json, re
from dataclasses import replace
from datetime import date, datetime

from ai.date_utils import normalize_relative_date, ROME_TIMEZONE
from ai.ollama_client import OllamaClient
from ai.schemas import AppointmentIntent, validate_appointment_intent
from ai.duration_utils import extract_duration


SYSTEM_PROMPT = """
Sei un interprete di richieste per la gestione degli appuntamenti
di un personal trainer.

Analizza la richiesta dell'utente ed estrai le informazioni.

Rispondi esclusivamente con un oggetto JSON contenente questi campi:
- operazione
- cliente
- data
- ora
- durata_minuti
- data_originale
- ora_originale

Regole:
1. operazione deve essere esclusivamente: crea, modifica oppure cancella.
2. Non inventare informazioni mancanti: usa null.
3. Non calcolare date relative: conserva espressioni come "domani" o "venerdì".
4. Utilizza il formato HH:MM per gli orari espliciti.
5. La durata deve essere espressa in minuti come numero intero.
   Interpreta anche le durate scritte in linguaggio naturale,
   comprese espressioni con piccoli errori grammaticali.
   Esempi:
   - "un'ora" oppure "un ora" = 60 minuti
   - "mezz'ora" = 30 minuti
   - "un'ora e mezza" = 90 minuti
   - "due ore" = 120 minuti
   Se la durata non è specificata, utilizza null.
   Non inventare durate predefinite.
6. Quando l'utente chiede di spostare o modificare un appuntamento,
   distingui sempre la collocazione originale da quella nuova.

   - data_originale: data dell'appuntamento PRIMA della modifica.
   - ora_originale: orario dell'appuntamento PRIMA della modifica.
   - data: NUOVA data richiesta dall'utente.
   - ora: NUOVO orario richiesto dall'utente.
7. Esempio:
   Richiesta: "Sposta Marco da venerdì alle 17 a sabato alle 10"

   Risultato:
   {
     "operazione": "modifica",
     "cliente": "Marco",
     "data_originale": "venerdì",
     "ora_originale": "17:00",
     "data": "sabato",
     "ora": "10:00",
     "durata_minuti": null
   }

   Non copiare data_originale e ora_originale nei campi
   data e ora quando l'utente ha specificato una nuova collocazione.

   Se un'informazione non è presente, utilizza null.

8. Regola per la cancellazione:
    Quando l'utente chiede di cancellare un appuntamento:
    - operazione deve essere "cancella".
    - cliente identifica il cliente interessato.
    - data e ora identificano l'appuntamento da cancellare.
    - data_originale e ora_originale devono essere null.
    - durata_minuti deve essere null se non specificata.
biguous_duration = re.search(
                r"\b(?:oppure|o|massimo|al massimo)\b",
                text.lower(),
            )

            if ambiguous_duration:
                intent = replace(intent, durata_minuti=None)

            elif intent.durata_minuti is None:
                recovered_duration = extract_duration(text)

                if recovered_duration is not None:
                    intent = replace(
                        intent,
                        durata_minuti=recovered_duration,
                    )
    Esempio:
    Richiesta: "Cancella l'appuntamento di Luca domani alle 15"

    Risultato:
    {
    "operazione": "cancella",
    "cliente": "Luca",
    "data": "domani",
    "ora": "15:00",
    "durata_minuti": null,
    "data_originale": null,
    "ora_originale": null
    }

    Non utilizzare data_originale e ora_originale per le cancellazioni.
9.  Non aggiungere spiegazioni o testo fuori dal JSON.
"""


class AppointmentInterpreter:
    """Interpreta richieste di appuntamenti utilizzando Ollama."""

    def __init__(self, client: OllamaClient | None = None) -> None:
        self.client = client if client is not None else OllamaClient()

    def interpret(self, text: str) -> AppointmentIntent:
        """Interpreta una richiesta e restituisce dati validati."""

        if not isinstance(text, str) or not text.strip():
            raise ValueError("La richiesta deve essere una stringa non vuota.")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text.strip()},
        ]

        response = self.client.chat(messages)

        try:
            data = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Il modello AI ha restituito un JSON non valido."
            ) from exc

        intent = validate_appointment_intent(data)
        if intent.operazione == "crea":
            ambiguous_duration = re.search(
                r"\b(?:oppure|o in alternativa|o|massimo|al massimo)\b",
                text.lower(),
            )

            if ambiguous_duration:
                intent = replace(intent, durata_minuti=None)

            elif intent.durata_minuti is None:
                recovered_duration = extract_duration(text)

                if recovered_duration is not None:
                    intent = replace(
                        intent,
                        durata_minuti=recovered_duration,
                    )

        reference_date = datetime.now(ROME_TIMEZONE).date()

        return replace(
            intent,
            data=normalize_relative_date(intent.data, reference_date),
            data_originale=normalize_relative_date(
                intent.data_originale, reference_date
            ),
        )