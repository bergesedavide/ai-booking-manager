# M06.6 — verifica HQ richiesta

Schema PostgreSQL 17 ispezionato prima della migrazione: PK identity bigint,
request_key e google_event_id univoci, status limitato a PENDING/CREATED/FAILED/UNKNOWN,
created_at e updated_at timestamptz. Nessun trigger applicativo. Tre righe preesistenti.

`0066_reconciliation.sql` è una migrazione transazionale additiva da applicare una
sola volta. È stata applicata al database locale. Non elimina dati e non associa
automaticamente contesti ai record storici. La versione predefinita resta 1:
solo la registrazione AI completa imposta esplicitamente versione 2.

## Uso locale

Arrestare il bot, incluse vecchie istanze, e attendere la fine delle richieste
in corso. Bot e comando aggiornati usano lo stesso flock esclusivo per tutta
l'esecuzione. Non eliminare `.booking-runtime.lock`: viene rilasciato dal sistema
alla terminazione. Il comando controlla anche i processi Python locali bot.py
per intercettare vecchie istanze. Il protocollo supporta un solo checkout locale;
non certifica l'assenza di bot su altri host/container o avviati tramite wrapper
che non mostrano bot.py negli argomenti. Questi devono essere fermati dall'operatore.

Dalla radice del repository:

```bash
.venv/bin/python telegram-bot/reconcile_booking.py 123 --preview
.venv/bin/python telegram-bot/reconcile_booking.py 123
```

123 è la PK PostgreSQL di UNA operazione. L'anteprima legge Google ma non aggiorna
il DB (acquisisce comunque un lock di riga). Il risultato VERIFIED implica CREATED
solo dopo commit del comando senza --preview. Qualsiasi altro esito conserva lo
stato precedente e salva diagnostica minima per PENDING/UNKNOWN. Un errore di
commit non è un successo: verificare la riga con una nuova connessione, oppure
ripetere il comando che non effettua scritture Google.

Il calendario ammesso è esclusivamente GOOGLE_TEST_CALENDAR_ID in .env; primary
è rifiutato. owner_ref identifica il profilo OAuth locale fisso
`prototype-local-google-profile`, non un tenant né un'identità Google verificata.
Non sostituire token/account mantenendo lo stesso riferimento. Il token esistente
può essere rinnovato da authenticate; nessun nuovo scope è richiesto.

I record versione 1 richiedono revisione manuale e non contattano Google. Non
inventare calendario, proprietario o timestamp per renderli riconciliabili.
Il marcatore versione 2 viene committato prima dell'insert: può essere presente
anche se il processo muore prima dell'invio, e non prova che Google sia stato
chiamato. Se assente il comando restituisce WRITE_NOT_STARTED senza riprendere
la creazione. Non esiste riapertura o nuovo tentativo automatico.

La verifica richiede ID esatto, status confirmed, evento singolo con intervallo
valido e timezone. Non confronta titolo/orari con il payload originario, che il
registro non conserva: ID casuale persistito e contesto sono la correlazione.
Non garantisce che l'evento non cambi dopo la lettura.

## Test espliciti sicuri

```bash
.venv/bin/python -m pytest -q tests/test_reconciliation.py
.venv/bin/python tests/check_reconciliation_postgres.py
```

Il secondo usa una tabella TEMP clonata dallo schema reale e rollback finale;
non scrive nelle operazioni reali. Entrambi usano Google simulato e vietano
metodi diversi dalla lettura. Non eseguire indiscriminatamente tutta la suite:
sono presenti test d'integrazione Google reali.
