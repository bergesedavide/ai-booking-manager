# M06.8 — implementazione locale, revisione HQ richiesta

Solo cancellazione AI di un evento singolo verificabile del prototipo. Nessuna
cancellazione reale o chiamata Google durante l'implementazione. Nessun commit/push.

## Migrazione e attivazione

`0068_deletions.sql` è additiva e transazionale: crea booking_deletions e relativo
indice, con FK alla creazione. NON è applicata alle tabelle public del prototipo.
Applicarla una sola volta, con il bot fermo, solo dopo autorizzazione HQ. Riavviare
poi tutti i processi con il nuovo codice: un vecchio M06.7 non rispetta il nuovo
protocollo di lock. Fino alla migrazione anche la registrazione M06.7 rifiuta una
scrittura se manca booking_deletions; il comportamento è conservativo.

Campi: riferimento creazione, request_key, owner_ref/calendar_id/Event ID, ETag,
intervallo visualizzato, stato PENDING/DELETED/FAILED/UNKNOWN, marcatore e diagnostica.
La chiave richiesta è univoca per proprietario/calendario; l'indice parziale
impedisce ulteriori cancellazioni per eventi con PENDING, UNKNOWN o DELETED.
La creazione resta CREATED: i registri sono una storia di operazioni.

## Provenienza e conversazione

Riutilizzata la ricerca deterministica M06.7 con un adattatore per data/ora
(intent cancella) verso data_originale/ora_originale (ricerca M06.7). Nessuna
modifica a quel motore di ricerca. Il titolo normalizzato è solo un filtro.
Servono registrazione di creazione versione 2 CREATED, marcatore di scrittura,
Event ID e contesto proprietario/calendario corrispondenti. Esclusi record storici
non verificabili, eventi esterni e appuntamenti non supportati dal servizio esistente
(ricorrenti, intera giornata, cancellati, durata non valida).

Esempio: «Cancella l'allenamento di Test M066 del 21 novembre 2026 alle 16».
Cliente e giorno mancanti vengono richiesti; ora facoltativa. Più risultati
richiedono scelta esplicita; oltre 20 risultati si richiede una ricerca più precisa.
Nessuna cancellazione multipla. Il riepilogo mostra titolo e intervallo effettivi.
La scelta di un risultato non autorizza la cancellazione: segue sempre il pulsante
Conferma cancellazione. «annulla» interrompe senza eliminare eventi.

Callback separati aidelete, token casuale, scadenza di cinque minuti, legame a
utente/chat/calendario/proprietario. Il piano resta server-side; non è ricostruito
con dati forniti dal callback. Consumo prima delle chiamate esterne; al riavvio
le conferme in memoria sono perse e non possono essere riutilizzate. Una conferma
scaduta o ripetuta non contatta Google. Le conversazioni precedenti devono essere
annullate prima di avviarne un'altra, come nel percorso M06.7.

## Coordinamento M06.7/M06.8

La sola modifica di produzione a M06.7 è nella registrazione PostgreSQL. Il nuovo
helper event_operation_guard richiede READ COMMITTED e acquisisce FOR UPDATE sulla
riga di creazione già attribuita al contesto. Dopo aver acquisito il lock, una
seconda query verifica entrambi i registri con uno snapshot aggiornato:

- modifica PENDING/UNKNOWN: blocca modifica e cancellazione;
- cancellazione PENDING/UNKNOWN/DELETED: blocca modifica e cancellazione.

Il registrante inserisce PENDING e committa prima di rilasciare il lock. Il secondo
attende e vede l'operazione già committata. Non ci sono transazioni aperte durante
le chiamate Google. Un commit del marcatore precede DELETE; se fallisce non si
prosegue. Aggiornamenti finali condizionali PENDING impediscono transizioni ripetute.
Un crash lascia il registro persistente a bloccare ulteriori operazioni AI.

## Esiti

Prima della scrittura si rileggono provenienza, contesto, evento ed ETag e si
confronta lo snapshot visualizzato. La funzione delete_booking esistente rilegge
ancora l'evento e usa If-Match. Non è cambiata. Una sola DELETE, nessun retry.

- Risposta DELETE di successo + commit finale: DELETED.
- HTTP 412 dalla funzione: FAILED/STALE, nuova selezione/conferma.
- Timeout, HTTP 404/410 o altri errori dopo il marcatore: UNKNOWN.
- Errori/mancanza evento durante il precontrollo: nessuna DELETE e nessun successo.
- Errore PostgreSQL prima della DELETE: nessuna scrittura Google.
- Commit finale incerto: nessuna conferma positiva all'utente; verifica manuale.

Non c'è un GET finale che trasformi un 404/410 in successo. Non esistono retry o
riconciliazione automatica per cancellazioni. M06.6 resta dedicata alle creazioni.
Anche un ValueError dopo il marcatore resta incerto: potrebbe riguardare la risposta.

## Test sicuri

```bash
.venv/bin/python -m pytest -q tests/test_ai_deletion.py tests/test_ai_reschedule.py tests/test_reconciliation.py tests/test_get_booking.py tests/test_create_booking.py tests/test_update_booking.py tests/test_reschedule_safe_integrated.py tests/test_reschedule_unknown.py tests/test_reschedule_callback_unknown.py tests/test_delete_booking.py tests/test_delete_callback.py
.venv/bin/python tests/check_deletion_postgres.py
.venv/bin/python tests/check_reschedule_postgres.py
```

Il fake Google vieta insert/patch, non accetta retry e simula al massimo una DELETE.
Lo script check_deletion_postgres crea uno schema isolato dal nome casuale, clona
la sola struttura delle creazioni, applica lì le migrazioni 67/68 e usa fixture
artificiali senza leggere eventi del collaudo. Verifica entrambe le direzioni di
concorrenza con connessioni separate e attesa reale del lock PostgreSQL. Esegue
commit reali solo nello schema isolato e lo elimina nel finally. Non esegue la
migrazione nelle tabelle public. Il test precedente usa solo TEMP e rollback ed è
stato adeguato alla presenza del secondo registro.

## Limiti e verifica HQ

/cancella tradizionale è invariato e fuori dal coordinamento PostgreSQL AI. Anche
editor esterni restano possibili: ETag blocca la cancellazione di una versione
cambiata. Il profilo OAuth locale resta identificato dall'owner_ref del prototipo;
non sostituire account senza rivedere il contesto. Nessun sistema multi-tenant
completo, ma chiavi/query conservano il contesto esplicito.

PENDING/UNKNOWN non vengono sbloccati automaticamente. Prima di intervenire
manualmente occorre verificare sia Google sia PostgreSQL. Una cancellazione già
DELETED non viene ripetuta anche se l'evento viene successivamente ripristinato.
HQ deve autorizzare separatamente migrazione e collaudo reale, con un solo evento
verificabile, conferma manuale Telegram, controllo finale e arresto del bot.
M06.8 non è dichiarata completata.
