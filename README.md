# F1 Predictor

Webapp che prevede l'ordine d'arrivo del prossimo Gran Premio e mostra lo storico 2001–oggi.

## Avvio

```powershell
cd f1-predictor
.\.venv\Scripts\python.exe backend\app.py
```
Poi apri http://127.0.0.1:8000

Se la cartella `.venv` non c'è (ad esempio su un altro PC):
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Aggiornare dati e modello

Dalla pagina **Modello** → "Aggiorna dati e riaddestra", oppure a mano:
```powershell
cd backend
..\.venv\Scripts\python.exe data_fetch.py   # risultati, qualifiche, classifiche (Jolpica)
..\.venv\Scripts\python.exe race_pace.py    # tempi giro dal 2018 -> passo gara (Jolpica, ~2 min a gara)
..\.venv\Scripts\python.exe weather.py      # meteo di ogni gara (Open-Meteo)
..\.venv\Scripts\python.exe train.py        # backtest + modelli finali
..\.venv\Scripts\python.exe test_leakage.py # verifica che le feature non usino il risultato della gara
```

## Prezzi Fanta F1
Pagina **Fanta F1** (API `/api/fanta?min_price=3.0..4.0`, logica in `backend/fanta.py`):
- valore = punti fanta attesi nel weekend secondo il tabellone (`QUALI_POINTS` e `RACE_POINTS` in `fanta.py`), mescolando:
  - la previsione dei modelli: punti qualifica (modello dell'ordine in qualifica, `train.py quali`) + punti gara (modello pre-qualifica), simulati con una "temperatura" tarata sui punti fanta invece che sul vincitore;
  - la media reale dei punti fanta nella stagione in corso, con peso `form_weight × n / (n + form_k)` dopo n gare.
  
  Temperature e pesi vengono scelti sul backtest 2010-2015 e verificati sul 2016-oggi (`python fanta.py`, eseguito anche a fine `train.py`; risultato in `data/fanta_calibration.json`). Tutto è noto prima della qualifica, quindi i prezzi restano fissi nel weekend;
- il più quotato costa 30,0 coins, il meno quotato tra 3,0 e 4,0, gli altri in proporzione lineare tra i due, con una cifra decimale;
- prezzo minimo automatico = 30 × valore dell'ultimo ÷ valore del primo, riportato tra 3,0 e 4,0; dalla pagina si può scegliere a mano;
- prezzo dei team = valore medio dei due piloti sulla stessa scala (30 = pilota migliore), tra 4 e 30;
- **Team Building** (menu a sinistra): 6 piloti con al massimo 140 coins, 3 team con al massimo 140 coins (regole in `fanta.py`). La squadra scelta resta salvata nel browser; "Suggerisci la migliore" trova la combinazione con più punti attesi.

## Lingua e orari
Il selettore in alto sceglie il paese: imposta la lingua (italiano, inglese, spagnolo; testi in `frontend/i18n.js`) e il fuso orario degli orari di partenza (Italia, Spagna, Messico, Regno Unito, USA costa est).

## Fonti dati
- **Jolpica F1 API** (`api.jolpi.ca/ergast`): successore di Ergast, stesso schema del dataset Kaggle "Formula 1 World Championship", aggiornato alla stagione in corso. Nessuna chiave. Limite: ~500 richieste/ora (tutto viene salvato in `data/cache`).
- **Open-Meteo**: archivio storico (rianalisi ERA5 di ECMWF, orario, dal 1940) per le gare passate; API di previsione per la prossima gara (fino a 16 giorni). Nessuna chiave. Valori alle coordinate del circuito nelle 2 ore di gara; prima del 2005 l'orario di partenza non è nei dati e si assume 14:00 locali.

## Modello
- LightGBM LambdaRank, due versioni: pre-qualifica e post-qualifica (griglia + distacco dalla pole).
- Feature: forma pilota e team, storico pluriennale della scuderia (titoli, % vittorie ultimi 3 anni, posizione anno scorso, con i team raggruppati per fabbrica), storico sul circuito, peso della griglia sul circuito, anni di cambio regolamento, meteo (temperatura, pioggia, umidità, vento, abilità del pilota sul bagnato).
- Rating Elo pilota + team (solo post-qualifica).
- Passo gara dai tempi giro Jolpica (dal 2018; esclusi 1° giro, safety car, pit stop): distacco % mediano dal pilota più veloce, medie sulle ultime gare. Usato solo dal modello pre-qualifica, l'unico in cui migliora il backtest.
- Probabilità: simulazione Plackett-Luce (20.000 gare), calibrate sul backtest.
- Valutazione walk-forward: ogni stagione è prevista da un modello addestrato solo sulle stagioni precedenti.
