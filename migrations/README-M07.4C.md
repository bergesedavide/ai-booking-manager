# M07.4C — OAuth non interattivo nel runtime Telegram

Il bot importa `google_transport.load_existing_credentials` anche per il nome
storico `authenticate`. Entrambe le funzioni non richiedono parametri e restituiscono
credenziali Google; il caricamento runtime rifiuta le credenziali non utilizzabili
senza avviare autorizzazioni. I comandi tradizionali restano disabilitati.

## Autorizzazione iniziale manuale

`google_auth.authenticate` resta disponibile esclusivamente per procedure manuali
esterne al bot: può aprire browser/server OAuth, richiedere consenso e salvare il
token. Il suo comportamento funzionale è invariato. Non importarla nei servizi
raggiungibili dal bot e non usarla durante l'esecuzione Telegram.

Gli script standalone storici `main.py` e `main_legacy.py` possono ancora avviare
OAuth interattivo e svolgere operazioni Calendar. Non avviarli come semplici test
di autenticazione; richiedono una procedura e un'autorizzazione separate.

## Esecuzione del bot

I percorsi AI caricano soltanto credenziali locali esistenti, senza nuovi scope.
Un token valido viene usato direttamente; se necessario, il rinnovo preventivo
avviene soltanto in memoria. File token e permessi non vengono aggiornati.

Token mancante, malformato, senza refresh utilizzabile o con rinnovo fallito causa
un rifiuto non interattivo. Non ripetere una prenotazione con esito incerto:
utilizzare la diagnostica M07.3 e, dove applicabile, la procedura M06.6 approvata.
Il ripristino dell'autorizzazione richiede intervento manuale esterno al bot.

Un access token revocato può risultare valido localmente fino alla risposta Google.
Il trasporto M07.4A non rinnova e non ripete una scrittura dopo HTTP 401; le letture
mantengono la propria politica OAuth separata. Non cambiano registri o stati.
Il riferimento locale owner_ref non certifica l'identità Google: non sostituire
account/token senza rivedere il contesto. Il calendario consentito resta quello
di test; questa modifica non aggiunge scope o autorizzazioni Calendar.

## Verifica offline

`tests/test_runtime_oauth.py` simula credenziali e filesystem, vieta socket,
browser, server OAuth e scritture/permessi del token. Le regressioni pertinenti
coprono i percorsi AI, i vecchi ingressi disabilitati, la diagnostica,
la riconciliazione e l'assenza di replay HTTP 401 delle scritture.

Implementazione locale soggetta a revisione HQ. Nessuna migrazione necessaria.
