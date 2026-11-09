# M07.3 — diagnostica in sola lettura, revisione HQ richiesta

Nessuna migrazione. Nessun recupero o aggiornamento degli stati. Il comando non
richiama la riconciliazione M06.6 né i servizi di creazione/modifica/cancellazione.

## Uso

Fermare il bot e attendere la conclusione delle richieste in corso. Dalla radice:

```bash
.venv/bin/python telegram-bot/diagnose_operations.py list
.venv/bin/python telegram-bot/diagnose_operations.py list --limit 20
.venv/bin/python telegram-bot/diagnose_operations.py show creation 123
.venv/bin/python telegram-bot/diagnose_operations.py show reschedule 45
.venv/bin/python telegram-bot/diagnose_operations.py show deletion 67 --check-google
```

Gli ID sono esempi: tipo e ID identificano insieme l'operazione. Gli ID possono
coincidere fra tabelle. `list` include soltanto PENDING e UNKNOWN, ordinati per
created_at, tipo e ID, dal più vecchio. Limite predefinito 50, ammesso 1–200;
nessuna enumerazione Google. L'età è espressa in secondi, senza valori negativi.
`show` ammette anche stati terminali per consultare la storia persistita.

Per impostazione predefinita non avviene alcuna autenticazione o chiamata Google.
Solo `--check-google` abilita una lettura del calendario e dell'evento selezionato.
Non sono disponibili opzioni apply, retry, resolve o aggiornamenti manuali.
L'output JSON distingue:

- `postgresql`: dati persistiti e, per modifica/cancellazione, creazione collegata;
- `google`: osservazione corrente oppure NOT_REQUESTED;
- `local_validation`: coerenza del contesto e dei dati;
- `record_changed`: cambiamento della riga o della creazione collegata;
- `conclusion`: ciò che le evidenze permettono di sostenere.

Exit 0 significa rapporto prodotto, NON operazione Google riuscita. Exit 1 segnala
errore di esecuzione, operazione assente o rilettura PostgreSQL fallita. Argomenti
non validi producono exit 2. Gli errori Google sono riportati nel rapporto senza
farne automaticamente un successo o un fallimento dell'operazione originaria.

## Garanzie PostgreSQL e concorrenza

Tutte le letture iniziano con SET TRANSACTION ... READ ONLY. Il dettaglio e la sua
creazione collegata usano una breve transazione REPEATABLE READ. Non sono presenti
INSERT, UPDATE, DELETE, DDL o SELECT FOR UPDATE. Le connessioni sono chiuse prima
della rete. Dopo la verifica Google si rileggono entrambe le righe; un cambiamento,
una scomparsa o un errore di rilettura impediscono conclusioni positive.

Ogni comando acquisisce il runtime_guard esistente e lo rilascia anche in caso di
errore. Un lock occupato o un bot.py Python locale rilevato interrompono il comando.
Non eliminare il file .booking-runtime.lock. Il lock coordina soltanto i processi
che usano lo stesso checkout: fermare anche istanze remote/container o wrapper
non riconoscibili. Non certifica l'assenza di editor esterni a Google.

## Verifica Google

Prima di creare il client si verificano versione 2 della creazione, proprietario,
calendario, formato e correlazione dell'Event ID e coerenza dei campi. Per le
operazioni collegate servono origine CREATED con marcatore, FK/contesto coerenti,
ETag atteso e intervalli validi. Record legacy o incoerenti restano consultabili
in PostgreSQL ma non contattano Google. Nessuna ricerca per titolo o nuovo ID.

Il calendario deve coincidere con GOOGLE_TEST_CALENDAR_ID in .env e l'owner_ref
con il riferimento locale del prototipo. Vengono rifiutati primary e i contesti
incompatibili. CalendarList.get verifica ID, nome esatto AI Booking Manager - Test
e flag primary prima di events.get. Lo strumento resta limitato al calendario
di test anche se in futuro altre parti del progetto diventassero operative.

L'autenticazione legge soltanto token.json e credentials.json locali esistenti;
richiede client_id corrispondente e scope già dichiarati sufficienti. Non chiama
InstalledAppFlow, non apre browser, non richiede scope e non salva token. Il rinnovo
può avvenire soltanto in memoria. Se non è possibile, restituisce autenticazione
non disponibile; i dati PostgreSQL restano visibili. Trasporto Google e rinnovo
hanno timeout di rete di 10 secondi; le letture API usano num_retries=0.

Vengono mostrati solo ID, stato, ETag e intervallo dell'evento. cancelled può avere
campi mancanti. Eventi attivi privi di ETag, intervallo completo o timezone sono
segnalati come risposta incompleta/non utilizzabile per il confronto. 403, 404,
410, timeout e risposte inattese sono distinti; non si stampano dettagli HTTP,
credenziali, DSN o eccezioni grezze. Non esistono marcatori Google di provenienza
nel creatore attuale: l'attribuzione usa la correlazione PostgreSQL.

## Interpretazione e procedura manuale

1. Fermare gli esecutori e usare list per individuare tipo e ID.
2. Consultare show, verificando origine, contesto e marcatore.
3. Solo se necessario, richiedere --check-google e confrontare le evidenze.
4. Se record_changed è vero, scartare le conclusioni della lettura precedente.
5. Conservare il blocco dell'operazione se manca una prova sufficiente. Non
   ripetere scritture, eliminare registri o aggiornare direttamente gli stati.
   Ogni eventuale risoluzione richiede una successiva procedura approvata HQ.

Significato dei registri:

- creazione: PENDING / CREATED / FAILED / UNKNOWN;
- modifica: PENDING / UPDATED / FAILED / UNKNOWN;
- cancellazione: PENDING / DELETED / FAILED / UNKNOWN.

Uno stato terminale positivo attesta il completamento registrato dal flusso, non
lo stato corrente dell'evento. Un marcatore assente nel protocollo corrente, con
provenienza integra ed esecutore fermo, indica che quel tentativo non ha avviato
la scrittura. La sua presenza NON prova che Google sia stato chiamato: il processo
può morire fra commit e invio. Per i record legacy non vale la deduzione di assenza.

Una creazione con ID preassegnato trovato attivo può essere riscontrata su Google
senza aggiornare il registro. Per una modifica, trovare gli orari richiesti NON
prova che il tentativo selezionato li abbia prodotti. Un ETag invariato o l'orario
originale non sono prove autonome di mancata esecuzione. Per una cancellazione,
status cancelled o HTTP 404/410 NON provano il successo della specifica DELETE.
Un FAILED/STALE nel registro attesta un rifiuto per versione cambiata, diverso da
una chiamata mai inviata. Quando le evidenze non bastano, il rapporto mostra:

> Evidenza insufficiente: operazione ancora incerta.

## Test e limiti

```bash
timeout --signal=TERM --kill-after=2s 20s .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_operation_diagnostics.py
```

Solo fake PostgreSQL/Google, file credenziali fittizi e lock temporanei. Nessun
accesso al database o a Google reale e nessuna nuova dipendenza. La suite verifica
anche il mancato salvataggio del token, il rilascio delle transazioni prima della
rete e del lock in caso d'errore. Lo schema PostgreSQL reale non è stato ricontrollato
tramite connessione in questa implementazione: le query seguono lo schema del repository.

Il rapporto non è uno snapshot atomico DB/Google, un audit HTTP né una prova causale
completa. La rilettura riduce ma non elimina le corse con editor esterni. owner_ref
identifica ancora un profilo locale, non un'identità Google personale certificata.
Il dettaglio contiene dati operativi potenzialmente personali: consultarlo in un
terminale autorizzato, evitando redirezioni o condivisioni indiscriminate. Nessun
log o rapporto viene scritto automaticamente su file. Non è possibile ricostruire
il payload originale delle creazioni, perché il registro non lo conserva.

Attendere revisione HQ e autorizzazione separata per qualsiasi verifica reale.
