# AI Booking Manager — prototipo Linux

Gestione conversazionale degli appuntamenti di un personal trainer tramite
Telegram, interprete locale Ollama, Google Calendar e registri PostgreSQL.
Creazione, spostamento e cancellazione AI richiedono conferme esplicite.
I vecchi comandi Telegram di scrittura sono disabilitati. Il progetto è un
prototipo locale, non un ambiente di produzione o una piattaforma multi-tenant.

## Stato della riproducibilità

**Il bootstrap di un database vuoto è bloccato:** manca la migrazione iniziale
che crea `booking_operations`. La migrazione 0066 presuppone quella tabella.
Non avviare il bot su uno schema incompleto e non ricostruirlo per tentativi.
Le migrazioni 0066–0068 non costituiscono da sole un bootstrap completo.

Le istruzioni seguenti derivano dal codice e dai collaudi del prototipo esistente;
non sono ancora state validate con un'installazione Linux pulita. M07.5A non
applica migrazioni, non avvia servizi e non esegue nuove autorizzazioni OAuth.

## Prerequisiti

- Linux con `/proc`, supporto `fcntl.flock` e dati timezone per `Europe/Rome`.
- Git, Python con `venv` e pip, Docker Engine e plugin Docker Compose v2.
- Accesso autorizzato a Docker e porte locali 5433 e 11434 disponibili.
- Account Telegram con bot creato tramite BotFather e ID dell'utente autorizzato.
- Account Google, progetto con Calendar API abilitata e client OAuth desktop.
- Risorse locali sufficienti per Ollama e `qwen2.5:3b`; requisiti hardware minimi
  non ancora misurati. Accesso Internet per pacchetti, immagini, modello e API.

La versione Python di riferimento dell'ambiente collaudato è **3.14.4**.
Il codice usa funzionalità di Python 3.10+; le dipendenze esaminate, tra cui
python-telegram-bot, pytest e psycopg, dichiarano almeno 3.10. Questo non certifica
l'intero insieme dei requirements su tutte le versioni 3.10+: usare la versione
di riferimento e verificare separatamente altre versioni/architetture.

## Clone, ambiente virtuale e dipendenze

Clonare il repository GitHub `bergesedavide/ai-booking-manager` e posizionarsi
nella sua radice. Non copiare un virtualenv da un'altra macchina.

```bash
git clone https://github.com/bergesedavide/ai-booking-manager.git
cd ai-booking-manager
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.txt` include anche pytest e `psycopg[binary]`. Non è necessario
installare PostgreSQL come pacchetto Python né compilare psycopg sulle piattaforme
supportate dalle wheel. La disponibilità delle wheel va verificata sulla macchina
nuova; non è stata provata durante questa fase documentale.

## Configurazione locale

```bash
cp .env.example .env
chmod 600 .env
```

Compilare tutte e quattro le variabili. Il bot legge direttamente il file `.env`
alla radice, anche durante l'import: esportare soltanto variabili nella shell
non lo sostituisce. Il parser non rimuove virgolette e non interpreta commenti
in linea. Usare `CHIAVE=valore`, senza virgolette o spazi ai bordi; tenere i commenti
su righe separate. Per evitare differenze con Compose, usare una password casuale
senza caratteri di interpolazione come `$` e senza `#` o spazi. Non pubblicare segreti.

Il database applicativo è fissato a `127.0.0.1:5433`, nome `ai_booking_manager`,
utente `ai_booking`. Ollama è atteso a `http://127.0.0.1:11434`.
Il bot va eseguito sull'host: questi indirizzi non sono una configurazione per
eseguirlo dentro un container. Il fuso degli appuntamenti è `Europe/Rome`.

## PostgreSQL

Dalla radice, dopo aver compilato `.env`:

```bash
docker compose up -d postgres
docker compose ps postgres
```

`compose.yaml` usa PostgreSQL 17, healthcheck e volume persistente `postgres_data`.
La porta è esposta solo su loopback. Attendere lo stato healthy prima di procedere.
Cambiare la password nel `.env` non cambia quella di un volume già inizializzato.
Non eliminare volumi per risolvere un problema di accesso: contengono dati.

### Migrazioni

Su database nuovo **fermarsi qui** finché non è disponibile la migrazione base
approvata. Non esiste attualmente un comando completo di inizializzazione.

Su database esistente con schema base già verificato, le migrazioni sono
transazionali, da applicare una sola volta e in ordine: 0066, 0067, 0068.
Prima verificare quali sono già presenti, arrestare ogni bot e acquisire il lock
runtime secondo le procedure del progetto. Non eliminare `.booking-runtime.lock`.
Non esiste un registro automatico delle migrazioni applicate.

Per una migrazione già verificata come mancante, la modalità di applicazione è:

```bash
docker compose exec -T postgres psql -U ai_booking -d ai_booking_manager -v ON_ERROR_STOP=1 < migrations/0066_reconciliation.sql
```

Ripetere separatamente per `0067_reschedules.sql` e `0068_deletions.sql` soltanto
se mancanti e autorizzate. Il comando non acquisisce da solo il lock runtime.
Non eseguire i tre file indiscriminatamente su un database esistente.
Consultare [M06.6](migrations/README-M06.6.md),
[M06.7](migrations/README-M06.7.md) e [M06.8](migrations/README-M06.8.md).

## Ollama

Compose separato, dalla radice:

```bash
docker compose -f ollama/compose.yml up -d
docker compose -f ollama/compose.yml exec ollama ollama pull qwen2.5:3b
```

L'immagine è attualmente `ollama/ollama:latest`: **non garantisce riproducibilità**.
La versione del server effettivamente utilizzata non è stata verificata in M07.5A;
identificarla e fissare una versione approvata è un'attività successiva.
Il modello viene conservato nel volume `ollama_data`; avviare il container non
lo scarica automaticamente. Non cambiare modello o prompt per risolvere errori
di configurazione. I timeout del client sono connessione 5s e lettura 60s,
non una scadenza totale dell'inferenza.

## Google Calendar di test e OAuth manuale

Creare un calendario separato denominato **AI Booking Manager - Test**, posseduto
dall'account OAuth del prototipo. Inserire il suo ID in `GOOGLE_TEST_CALENDAR_ID`:
non usare `primary` né l'ID di un calendario personale come sostituto.
Verificare nome, ID, account e accesso prima di qualsiasi collaudo di scrittura.

Configurare il consenso OAuth e, se previsto dalla configurazione del progetto
Google, l'utente di test. Salvare il JSON del client desktop come
`google-calendar-prototype/credentials.json`, fuori da Git, con permessi limitati.
Gli scope già dichiarati in `google_auth.py` sono CalendarList readonly,
Calendar events owned e Calendar FreeBusy: non vengono ampliati dal bot.

La funzione esistente `google_auth.authenticate()` supporta il bootstrap manuale.
A bot fermo, su una postazione con browser e callback locale raggiungibile,
può essere invocata direttamente, senza avviare gli script Calendar:

```bash
(cd google-calendar-prototype && ../.venv/bin/python -c 'from google_auth import authenticate; authenticate()')
```

Questa chiamata può avviare browser/server OAuth, richiedere consenso e salvare
`token.json` nella stessa directory con permessi 0600. Non esegue operazioni Calendar.
È una procedura manuale supportata dalla funzione esistente, non un comando CLI
dedicato; non è stata eseguita durante M07.5A. Su Linux headless richiede una
procedura separata: non avviare il bot aspettandosi che apra l'autorizzazione.

**Non usare `google-calendar-prototype/main.py`, `main_legacy.py` o test reali
come test di autenticazione:** possono accedere a calendari, incluso primary.
Il runtime Telegram carica soltanto credenziali già esistenti; rinnova in memoria,
non salva token e non apre OAuth interattivo. Credenziali mancanti o revocate
richiedono intervento manuale esterno. Vedere [M07.4C](migrations/README-M07.4C.md).
Non sostituire account/token su un database già popolato senza rivedere owner_ref.

## Avvio e arresto Telegram

Solo dopo configurazione Google verificata, modello disponibile e schema completo:

```bash
.venv/bin/python telegram-bot/bot.py
```

Avviare una sola istanza da un solo checkout. Usare `/start` dall'utente autorizzato,
poi richieste in linguaggio naturale. I pulsanti di conferma sono obbligatori;
`/prenota`, `/modifica` e `/cancella` tradizionali restano disabilitati.
Arrestare con `Ctrl+C` e attendere la conclusione dei worker attivi. Il sistema
rilascia il lock alla terminazione: non cancellarne il file per forzare un avvio.
Il lock locale non coordina istanze su altri host/container o checkout indipendenti.

## Test offline

Selezione pertinente già collaudata prima di M07.5A, senza bot o servizi reali:

```bash
timeout --signal=TERM --kill-after=2s 35s .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_ollama_robustness.py tests/test_telegram_delivery.py tests/test_runtime_oauth.py tests/test_postgres_workers.py tests/test_ai_creation_confirmation.py tests/test_ai_reschedule.py tests/test_ai_deletion.py tests/test_reconciliation.py tests/test_operation_diagnostics.py tests/test_google_write_transport.py tests/test_create_booking.py tests/test_update_booking.py tests/test_delete_booking.py tests/test_reschedule_safe_integrated.py tests/test_legacy_writes_disabled.py tests/test_date_utils.py
```

I servizi sono simulati. Non eseguire indiscriminatamente l'intera suite: esistono
script Google reali e controlli PostgreSQL non offline. La selezione sopra non
verifica installazione pulita, migrazioni reali o accesso ai servizi.

## Problemi comuni

- `No module named psycopg`: installare i requirements nel virtualenv usato dal bot.
- `.env` mancante/valori vuoti: compilare il file alla radice, nel formato descritto.
- Connessione PostgreSQL rifiutata: verificare container healthy e porta 5433.
- `booking_operations` assente: bootstrap bloccato; attendere la migrazione base.
- Password errata con volume esistente: non cancellare dati e non presumere che
  modificare `.env` aggiorni l'utente PostgreSQL.
- Ollama indisponibile/modello assente: verificare il servizio e il download del
  modello; nessun retry automatico viene introdotto dall'applicazione.
- Token assente/revocato: eseguire la procedura manuale a bot fermo; non stampare token.
- Lock occupato: verificare l'istanza attiva, senza eliminare il file o forzature.
- Esito PENDING/UNKNOWN o risposta Telegram mancante: non ripetere la scrittura;
  usare la [diagnostica M07.3](migrations/README-M07.3.md) a bot fermo.

Non pubblicare `.env`, token, credenziali, dump del database o dati dei collaudi.
I file segreti sono esclusi da `.gitignore`; controllare comunque il diff prima
di ogni commit. Non è previsto alcun avvio automatico di un ambiente di produzione.
