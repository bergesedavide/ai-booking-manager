# M07.4A — trasporto Google monouso, revisione HQ richiesta

Nessuna migrazione, nuova dipendenza, chiamata Google reale, avvio del bot,
modifica PostgreSQL, commit o push durante l'implementazione.

## Architettura

`google-calendar-prototype/google_transport.py` fornisce:

- `load_existing_credentials`: credenziali locali già esistenti, senza nuovi scope,
  browser OAuth o salvataggio del token; rinnovo preventivo soltanto in memoria;
- `build_calendar_service`: Google API Client con un CalendarHttp per ogni tentativo;
- `CalendarHttp`: instradamento separato per metodo e percorso API;
- `SingleWriteHttp`: singola delega a requests, con requests/urllib3 reali e senza
  wrapper OAuth che ripetano una richiesta Calendar.

booking_service conserva firma e comportamento di process_booking e usa la nuova
factory tramite gli stessi confini authenticate/build. ai_reschedule_service importa
già questi confini da booking_service; li riutilizzano modifica e cancellazione.
La disponibilità AI usa il nuovo caricamento non interattivo, conservando il proprio
client di sola lettura. google_auth.authenticate generale non è modificato.

## Instradamento

Solo HTTPS e host www.googleapis.com/calendar.googleapis.com, senza userinfo,
frammenti o porte diverse da 443. Non si seguono endpoint inattesi o method override.

- POST /calendar/v3/calendars/{calendar}/events: scrittura;
- PATCH o DELETE /calendar/v3/calendars/{calendar}/events/{event}: scrittura;
- GET events/list e CalendarList: lettura;
- POST /calendar/v3/freeBusy: lettura, nonostante il metodo POST;
- altre combinazioni: rifiuto locale senza fallback.

L'alias primary è rifiutato nei percorsi evento. Gli altri controlli sul calendario
configurato, sulla provenienza e sulle conferme rimangono nei servizi esistenti.

Il trasporto letture resta AuthorizedHttp/httplib2, con la politica delle letture
preesistente; può rinnovare credenziali e ripetere una lettura. Non viene mai usato
come fallback dopo un errore del trasporto scritture. Discovery usa la documentazione
statica disponibile nel pacchetto Google API Client e retry zero nella factory.

## Garanzia di singolo invio

Una nuova istanza SingleWriteHttp viene creata per ogni client AI di tentativo,
non globalmente. Prima di autenticazione o rete viene consumato un latch protetto
da threading.Lock. Una seconda chiamata sullo stesso tentativo fallisce localmente,
anche se cambia metodo oppure se il Google API Client provasse a ritentare.
Preparazione e callback creano client distinti; una singola operazione confermata
non riceve automaticamente un nuovo trasporto dopo un errore.

La sessione requests è ordinaria, senza AuthorizedSession. Sono disabilitati:

- retry urllib3 total/connect/read/status/redirect/other (tutti zero);
- rispetto automatico di Retry-After;
- redirect requests (allow_redirects=False);
- proxy e autenticazione .netrc impliciti (trust_env=False);
- rinnovo OAuth reattivo dopo un HTTP 401 Calendar.

TLS rimane verificato. Una risposta HTTP non positiva ritorna al Google API Client,
che la interpreta senza reinvio. Le tre chiamate insert/patch/delete specificano
execute(num_retries=0). I timeout sono connessione 5 s e lettura 15 s, anche sul
rinnovo OAuth preventivo. Non costituiscono una scadenza totale garantita.

Eventuali richieste all'endpoint token sono distinte dalla scrittura Calendar:
quest'ultima non è inviata finché le credenziali non sono utilizzabili. Dopo una
risposta Calendar 401 non viene effettuato un secondo invio della scrittura.
Body, Event ID preassegnato e If-Match restano costruiti dai servizi deterministici.
Le sessioni di scrittura sono chiuse anche su errori e risposte interrotte.

## Semantica operativa invariata

Nessun retry applicativo o aggiornamento del significato degli stati. In caso di
risultato non confermabile si conservano i percorsi PENDING/UNKNOWN esistenti.
Per la creazione resta possibile il GET di verifica dell'ID preassegnato dopo un
errore: è una lettura, non un secondo tentativo INSERT. Modifica/cancellazione
conservano If-Match e il trattamento speciale di HTTP 412.

Diagnostica M07.3 rimane invariata e usa il proprio client di sola lettura. La
riconciliazione M06.6 continua a usare eventi.get attraverso il confine condiviso,
con autenticazione non interattiva. Nessuna riconciliazione automatica introdotta.

## Test offline

```bash
timeout --signal=TERM --kill-after=2s 25s .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_google_write_transport.py tests/test_ai_creation_confirmation.py tests/test_ai_reschedule.py tests/test_ai_deletion.py tests/test_reconciliation.py tests/test_operation_diagnostics.py tests/test_create_booking.py tests/test_update_booking.py tests/test_delete_booking.py tests/test_reschedule_safe_integrated.py tests/test_legacy_writes_disabled.py tests/test_date_utils.py
```

I test mantengono reali Google API Client, requests.Session, HTTPAdapter, urllib3
urlopen/_make_request e la gestione dei retry. Sostituiscono soltanto l'acquisizione
della connessione HTTPS con una connessione fittizia; contano le chiamate request
sulla connessione inferiore, non solo execute o Session.send. Le aperture reali
socket sono vietate nel fixture. Nessuna rete, PostgreSQL o credenziale reale.

Coperti successi POST/PATCH/DELETE e 204, 401/429/500/503, redirect, errori TLS,
connessione rifiutata, timeout, BadStatusLine/ResponseNotReady/disconnessione e body
interrotto. Verificati anche latch concorrente, tentativi distinti, rinnovo fallito,
body/header, FreeBusy e assenza di fallback, oltre alle regressioni dei registri
simulate senza modificare il database. Non è un test live TLS/DNS/proxy.

## Limiti

La garanzia è al massimo un invio HTTP applicativo dal trasporto controllato,
non una garanzia exactly-once sul server e non l'assenza di ritrasmissioni TCP.
Timeout e perdita della risposta lasciano necessariamente un possibile esito
incerto: non ripetere l'operazione e utilizzare la diagnostica approvata.

Il client AI è intenzionalmente monouso per le scritture. Una futura operazione
esplicitamente distinta deve creare il proprio client; non condividere il client
fra più operazioni o introdurre una factory automatica di retry.

Proxy e CA configurati solo tramite variabili requests non vengono ereditati dal
trasporto scritture: eventuali ambienti che li richiedono necessitano di una futura
configurazione esplicita approvata. I timeout delle letture e l'architettura generale
di bootstrap OAuth restano fuori da M07.4A.

Script standalone che costruiscono client Google propri non acquisiscono questa
garanzia automaticamente: lo scopo dell'integrazione sono i percorsi AI attivi.
Dopo aggiornamenti delle librerie occorre ripetere i test del livello inferiore.
Resta necessaria la revisione HQ prima di un collaudo reale separatamente autorizzato.
