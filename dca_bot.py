import ccxt
import tweepy
import schedule
import time
import os
import json
import random
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

try:
    print("Chargement des variables d'environnement...", flush=True)
    load_dotenv()

    print("Initialisation de MEXC...", flush=True)
    mexc = ccxt.mexc({
        'apiKey': os.getenv('MEXC_API_KEY'),
        'secret': os.getenv('MEXC_API_SECRET'),
        'enableRateLimit': True,
    })
    print("Connexion MEXC initialisée", flush=True)

    TWITTER_API_KEY = os.getenv('TWITTER_API_KEY')
    TWITTER_API_SECRET = os.getenv('TWITTER_API_SECRET')
    TWITTER_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN')
    TWITTER_ACCESS_TOKEN_SECRET = os.getenv('TWITTER_ACCESS_TOKEN_SECRET')
    print(f"Clés Twitter: API_KEY={bool(TWITTER_API_KEY)}, ACCESS_TOKEN={bool(TWITTER_ACCESS_TOKEN)}", flush=True)

    COINS = [
        {'symbol': 'BTC/USDC', 'amount_usd': 5.0, 'name': 'BTC', 'decimals': 6, 'total_decimals': 6},
        {'symbol': 'BKN/USDT', 'amount_usd': 1.01, 'name': 'BKN', 'decimals': 6, 'total_decimals': 6},
        {'symbol': 'ATR/USDT', 'amount_usd': 1.0, 'name': 'ATR', 'decimals': 6, 'total_decimals': 6},
    ]
    print("Liste des pièces chargée", flush=True)

    # Afficher l'heure actuelle en UTC et CEST (Francfort)
    now_utc = datetime.now(timezone.utc)
    now_cest = now_utc.astimezone(timezone(timedelta(hours=2)))
    print(f"Heure actuelle - UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}, CEST (Francfort): {now_cest.strftime('%Y-%m-%d %H:%M:%S %Z')}", flush=True)

    # Test d'accès au disque
    try:
        os.makedirs('/app/data', exist_ok=True)
        with open('/app/data/test.txt', 'w') as f:
            f.write("Test d'accès au disque")
        with open('/app/data/test.txt', 'r') as f:
            test_content = f.read()
        print(f"Test d'accès au disque réussi: {test_content}", flush=True)
    except Exception as e:
        print(f"Erreur lors du test d'accès au disque: {e}", flush=True)

    TOTALS_FILE = '/app/data/totals.json'
    DAY_COUNTER_FILE = '/app/data/day_counter.txt'
    LAST_EXECUTION_FILE = '/app/data/last_execution.txt'

    def load_day_counter():
        try:
            with open(DAY_COUNTER_FILE, 'r') as f:
                counter = int(f.read().strip())
                print(f"Compteur chargé: {counter}", flush=True)
                return counter
        except FileNotFoundError:
            print("Compteur non trouvé, initialisation à 1", flush=True)
            return 1
        except Exception as e:
            print(f"Erreur lors du chargement du compteur: {e}", flush=True)
            return 1

    def save_day_counter(counter):
        try:
            os.makedirs(os.path.dirname(DAY_COUNTER_FILE), exist_ok=True)
            with open(DAY_COUNTER_FILE, 'w') as f:
                f.write(str(counter))
            print(f"Compteur enregistré: {counter}", flush=True)
        except Exception as e:
            print(f"Erreur lors de l'enregistrement du compteur: {e}", flush=True)

    def load_last_execution():
        try:
            with open(LAST_EXECUTION_FILE, 'r') as f:
                last_date = f.read().strip()
                print(f"Dernière exécution chargée: {last_date}", flush=True)
                return last_date
        except FileNotFoundError:
            print("Dernière exécution non trouvée, initialisation vide", flush=True)
            return ""
        except Exception as e:
            print(f"Erreur lors du chargement de la dernière exécution: {e}", flush=True)
            return ""

    def save_last_execution(date):
        try:
            os.makedirs(os.path.dirname(LAST_EXECUTION_FILE), exist_ok=True)
            with open(LAST_EXECUTION_FILE, 'w') as f:
                f.write(date)
            print(f"Dernière exécution enregistrée: {date}", flush=True)
        except Exception as e:
            print(f"Erreur lors de l'enregistrement de la dernière exécution: {e}", flush=True)

    def load_totals():
        try:
            with open(TOTALS_FILE, 'r') as f:
                totals = json.load(f)
                print(f"Totaux chargés: {totals}", flush=True)
                # Simuler les achats manquants de BTC pour les jours 3 et 4
                if totals['BTC']['total_invested'] < 5.0 * load_day_counter():
                    missing_days = (load_day_counter() - 1) - int(totals['BTC']['total_invested'] / 5.0)
                    if missing_days > 0:
                        btc_price = 95600  # Prix estimé, à ajuster
                        additional_quantity = missing_days * (5.0 / btc_price)
                        totals['BTC']['total_quantity'] += additional_quantity
                        totals['BTC']['total_invested'] += missing_days * 5.0
                        print(f"Ajustement BTC: {missing_days} jours ajoutés, Quantité: {additional_quantity:.6f}, Investi: ${missing_days * 5.0}", flush=True)
                return totals
        except FileNotFoundError:
            print("Totaux non trouvés, initialisation par défaut", flush=True)
            return {
                'BTC': {'total_quantity': 0.0, 'total_invested': 0.0},
                'BKN': {'total_quantity': 0.0, 'total_invested': 0.0},
                'ATR': {'total_quantity': 0.0, 'total_invested': 0.0},
            }
        except Exception as e:
            print(f"Erreur lors du chargement des totaux: {e}", flush=True)
            return {
                'BTC': {'total_quantity': 0.0, 'total_invested': 0.0},
                'BKN': {'total_quantity': 0.0, 'total_invested': 0.0},
                'ATR': {'total_quantity': 0.0, 'total_invested': 0.0},
            }

    def save_totals(totals):
        try:
            os.makedirs(os.path.dirname(TOTALS_FILE), exist_ok=True)
            with open(TOTALS_FILE, 'w') as f:
                json.dump(totals, f, indent=2)
            print(f"Totaux enregistrés: {totals}", flush=True)
        except Exception as e:
            print(f"Erreur lors de l'enregistrement des totaux: {e}", flush=True)

    def post_tweet(message):
        try:
            client = tweepy.Client(
                consumer_key=TWITTER_API_KEY,
                consumer_secret=TWITTER_API_SECRET,
                access_token=TWITTER_ACCESS_TOKEN,
                access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
            )
            client.create_tweet(text=message)
            print("Tweet posté avec succès !", flush=True)
            return True
        except Exception as e:
            print(f"Erreur lors de la publication du tweet: {e}", flush=True)
            return False

    def buy_coins():
        try:
            # Vérifier si déjà exécuté aujourd'hui
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            last_execution = load_last_execution()
            if last_execution == today:
                print("Achat déjà effectué aujourd'hui, passage", flush=True)
                return

            day_counter = load_day_counter()
            totals = load_totals()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            unique_id = random.randint(1000, 9999)
            tweet_message = (
                f"DCA Jour {day_counter}, J'achète quotidiennement 5$ $BTC, 1$ $BKN et 1$ $ATR (code parrainage MEXC: 12KxM2):\n"
            )
            print(f"Début de buy_coins, jour {day_counter}", flush=True)

            for coin in COINS:
                symbol = coin['symbol']
                amount_usd = coin['amount_usd']
                name = coin['name']
                decimals = coin['decimals']
                total_decimals = coin['total_decimals']

                try:
                    print(f"Tentative de récupération du ticker pour {symbol}...", flush=True)
                    ticker = mexc.fetch_ticker(symbol)
                    price = ticker['last']
                    print(f"Prix récupéré pour {symbol}: {price}", flush=True)
                    quantity = amount_usd / price
                    market = mexc.market(symbol)
                    if quantity < market['limits']['amount']['min']:
                        print(f"Quantité pour {symbol} trop faible : {quantity}", flush=True)
                        tweet_message += f"- {name}: Échec (quantité faible)\n"
                        continue

                    print(f"Exécution de l'achat pour {symbol}, montant: ${amount_usd}", flush=True)
                    order = mexc.create_market_buy_order(symbol, amount_usd, {'createMarketBuyOrderRequiresPrice': False})
                    print(f"Achat réel effectué : {symbol}, Quantité: {quantity:.{total_decimals}f}, Coût: ${amount_usd:.2f}", flush=True)

                    totals[name]['total_quantity'] += quantity
                    totals[name]['total_invested'] += amount_usd
                    current_value = totals[name]['total_quantity'] * price
                    total_invested = totals[name]['total_invested']
                    price_change = ((current_value - total_invested) / total_invested) * 100 if total_invested > 0 else 0

                    tweet_message += (
                        f"- {name}: {quantity:.{decimals}f}, ${amount_usd:.2f}, Tot {totals[name]['total_quantity']:.{total_decimals}f}, ${total_invested:.2f}, {price_change:+.2f}%\n"
                    )

                except Exception as e:
                    print(f"Erreur lors de l'achat de {symbol}: {str(e)}", flush=True)
                    tweet_message += f"- {name}: Erreur\n"

            tweet_message += "\n#Crypto #DCA #Bitcoin #MEXC #Investing"

            if len(tweet_message) > 280:
                print(f"Erreur : Message trop long ({len(tweet_message)} caractères)", flush=True)
                tweet_message = (
                    f"DCA Jour {day_counter}, J'achète quotidiennement 5$ $BTC, 1$ $BKN et 1$ $ATR (code MEXC: 12KxM2):\n"
                    "Erreur lors de certains achats\n"
                    "#Crypto #DCA #Bitcoin #MEXC #Investing"
                )

            if post_tweet(tweet_message):
                save_totals(totals)
                save_day_counter(day_counter + 1)
                save_last_execution(today)

        except Exception as e:
            print(f"Erreur dans buy_coins: {e}", flush=True)

    # Planification quotidienne
    schedule.every().day.at("09:00").do(buy_coins)
    print("Tâche planifiée à 09:00 UTC (11:00 CEST, Francfort)", flush=True)

    def main():
        try:
            print("Bot DCA démarré...", flush=True)
            print(f"Clés MEXC: API_KEY={bool(os.getenv('MEXC_API_KEY'))}, SECRET={bool(os.getenv('MEXC_API_SECRET'))}", flush=True)
            print(f"Clés Twitter: API_KEY={bool(TWITTER_API_KEY)}, ACCESS_TOKEN={bool(TWITTER_ACCESS_TOKEN)}", flush=True)
            while True:
                schedule.run_pending()
                print(f"Vérification des tâches planifiées à {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
                time.sleep(60)
        except Exception as e:
            print(f"Erreur dans main: {e}", flush=True)
            raise

    if __name__ == "__main__":
        main()

except Exception as e:
    print(f"Erreur globale: {e}", flush=True)
    raise