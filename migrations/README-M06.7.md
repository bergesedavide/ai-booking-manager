# M06.7 — implementazione locale per revisione HQ

Solo spostamento di data/ora, senza variazioni di titolo o durata. Nessuna chiamata
Google reale è stata eseguita durante l'implementazione. Nessun commit o push.

## Provenienza degli appuntamenti

Non ci sono metadati Google di appartenenza nel creatore attuale. Esiste però una
correlazione PostgreSQL sufficiente: la creazione AI M06.6 salva atomicamente ID
casuale preassegnato, calendario, owner_ref e execution_version=2 prima della
scrittura; successivamente conferma CREATED. M06.7 richiede anche il marcatore di
avvio scrittura. Tutti questi criteri sono obbligatori in ricerca, preparazione
e registrazione del tentativo. Non vengono aggiunti marker o attribuzioni storiche.
Le nuove creazioni M06.6 sono già identificabili senza modificarne il flusso.

Il titolo normalizzato `Allenamento - <cliente>` è solo un filtro di ricerca,
mai una prova di appartenenza. Record versione 1, UNKNOWN/PENDING di creazione,
eventi esterni e titoli uguali senza correlazione DB vengono esclusi. Gli eventi
creati dai comandi tradizionali senza registro AI non sono ammessi.

## Uso conversazionale

Esempio: «Sposta Test M066 dal 20 novembre 2026 alle 15 al 21 novembre 2026 alle 16».
Vengono richiesti cliente, giorno originale, nuova data e ora se mancanti.
L'ora originale è facoltativa: più corrispondenze richiedono selezione esplicita.
Nessun risultato chiude la richiesta e invita a correggerla. Massimo 20 candidati;
oltre questo limite si richiede una ricerca più precisa. La corrispondenza cliente
è esatta dopo normalizzazione di maiuscole/spazi, senza ricerca fuzzy.

Una durata specificata viene rifiutata anche se uguale a quella corrente, per non
interpretare implicitamente richieste fuori ambito. Titolo e durata si leggono da
Google; DST ambiguo/inesistente o attraversato dall'intervallo non è supportato.

Riepilogo prima/dopo e conferma sempre obbligatori. In caso di conflitto viene
richiesta una seconda conferma dedicata, anche se il conflitto è apparso dopo il
riepilogo. UNKNOWN blocca anche una conferma di sovrapposizione. Ad ogni conferma
si rileggono evento, provenienza e disponibilità; la scrittura conserva If-Match.
Richieste e callback hanno scadenza di 5 minuti, token casuale consumabile una
sola volta, legame a utente/chat/calendario/proprietario e piano conservato server-side.
«annulla» interrompe; nuove richieste richiedono prima l'annullamento di quella pendente.
Un riavvio perde le conferme in memoria: si riparte con una nuova selezione.

## Database e attivazione

Schema booking_operations verificato in sola lettura prima dell'implementazione.
`0067_reschedules.sql` è una migrazione additiva transazionale, NON applicata al
database reale in questa sessione. Va applicata una sola volta a bot fermo, prima
dell'uso operativo. Il flusso di creazione e il comando M06.6 non vengono modificati.
Se manca la tabella, una modifica non raggiunge la scrittura Google.

La nuova booking_reschedules registra FK alla creazione, contesto, richiesta,
ETag e intervalli UTC originali/richiesti, stato, marcatore e diagnostica minima.
La chiave richiesta è univoca nel contesto proprietario/calendario. Un indice
parziale impedisce nuovi spostamenti AI dell'evento se esiste PENDING o UNKNOWN.
Più spostamenti conclusi dello stesso evento sono invece ammessi.

Commit del registro e del marcatore precedono il PATCH. Un errore DB impedisce
la scrittura, o impedisce di dichiarare successo se la scrittura è già avvenuta.
HTTP 412 è FAILED/STALE, senza retry. Timeout, errori HTTP diversi e risposte
incomplete sono UNKNOWN. Un errore del commit finale può lasciare PENDING o uno
stato già salvato: non ripetere alla cieca. Non esiste riconciliazione automatica
per modifiche; M06.6 resta dedicata esclusivamente alle creazioni.

## Verifiche isolate

```bash
.venv/bin/python -m pytest -q tests/test_ai_reschedule.py tests/test_reconciliation.py tests/test_get_booking.py tests/test_create_booking.py tests/test_update_booking.py tests/test_reschedule_safe_integrated.py tests/test_reschedule_unknown.py tests/test_reschedule_callback_unknown.py
.venv/bin/python tests/check_reschedule_postgres.py
```

Google viene simulato, incluso un solo PATCH fittizio per tentativo; insert/delete
non sono disponibili nel fake. Il test PostgreSQL crea solo tabelle TEMP, applica
lì la migrazione e usa savepoint per le chiamate al repository; rollback finale.
Le vere separazioni di commit sono inoltre verificabili nel codice dei context
manager del repository; nessuna fixture viene inserita nelle tabelle reali.

## Limiti residui

ETag protegge l'evento, non prenota atomicamente l'intervallo contro altri editor.
owner_ref identifica ancora il profilo OAuth locale: non sostituire account/token
senza rivedere il contesto. Nessuna architettura multi-tenant completa, ma contesto
esplicito, chiavi e query sono predisposti a una successiva evoluzione.
Le modifiche tradizionali non usano il nuovo blocco PostgreSQL; un evento con esito
AI incerto richiede verifica manuale prima di ulteriori interventi.
Il prompt AI esistente non è stato ripulito per evitare variazioni fuori ambito
all'interpretazione delle creazioni. Rimane necessaria la revisione HQ prima di
applicare la migrazione e svolgere un collaudo reale separatamente autorizzato.
