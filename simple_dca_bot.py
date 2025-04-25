import ccxt
import time
import schedule
import yaml
import logging
import tweepy
import json
from datetime import datetime

# Configuration du journal
logging.basicConfig(
    filename='trades.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)

# Charger la configuration
def load_config():
    with open('config/config.yml', 'r') as file:
        return yaml.safe_load(file)

# Charger les clés API
def load_api_keys():
    with open('auth/API_keys.yml', 'r') as file:
        mexc_keys = yaml.safe_load(file)
    with open('auth/twitter_keys.yml', 'r') as file:
        twitter_keys = yaml.safe_load(file)
    return mexc_keys, twitter_keys

# Charger le portefeuille
def load_portfolio():
    try:
        with open('portfolio.json', 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        return {
            "BTC": {"total_quantity": 0, "average_price": 0, "purchases": []},
            "BKN": {"total_quantity": 0, "average_price": 0, "purchases": []},
            "ATR": {"total_quantity": 0, "average_price": 0, "purchases": []}
        }

# Sauvegarder le portefeuille
def save_portfolio(portfolio):
    with open('portfolio.json', 'w') as file:
        json.dump(portfolio, file, indent=4)

# Connexion à MEXC
def connect_to_exchange(config, mexc_keys):
    exchange = ccxt.mexc({
        'apiKey': mexc_keys['MEXC']['TEST' if config['TEST'] else 'REAL']['APIKEY'],
        'secret': mexc_keys['MEXC']['TEST' if config['TEST'] else 'REAL']['SECRET'],
    })
    return exchange

# Connexion à X
def connect_to_twitter(twitter_keys):
    client = tweepy.Client(
        consumer_key=twitter_keys['TWITTER']['API_KEY'],
        consumer_secret=twitter_keys['TWITTER']['API_SECRET'],
        access_token=twitter_keys['TWITTER']['ACCESS_TOKEN'],
        access_token_secret=twitter_keys['TWITTER']['ACCESS_TOKEN_SECRET']
    )
    return client

# Vérifier le solde
def check_balance(exchange, pairing):
    try:
        balance = exchange.fetch_balance()
        balance_value = balance[pairing]['free'] if pairing in balance else 0
        logging.info(f"Solde {pairing} disponible : {balance_value} {pairing}")
        return balance_value
    except Exception as e:
        logging.error(f"Erreur lors de la vérification du solde : {str(e)}")
        return 0

# Obtenir le prix actuel
def get_current_price(exchange, symbol):
    for _ in range(3):
        try:
            ticker = exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            logging.warning(f"Erreur réseau pour {symbol} : {str(e)}. Nouvelle tentative dans 10s.")
            time.sleep(10)
    logging.error(f"Échec de la récupération du prix pour {symbol} après 3 tentatives")
    return 0

# Mettre à jour le portefeuille
def update_portfolio(portfolio, coin, quantity, price, amount):
    portfolio[coin]['purchases'].append({"quantity": quantity, "price": price, "amount": amount})
    total_quantity = sum(p['quantity'] for p in portfolio[coin]['purchases'])
    total_amount = sum(p['amount'] for p in portfolio[coin]['purchases'])
    portfolio[coin]['total_quantity'] = total_quantity
    portfolio[coin]['average_price'] = total_amount / total_quantity if total_quantity > 0 else 0
    save_portfolio(portfolio)

# Calculer l'évolution
def calculate_performance(portfolio, coin, current_price):
    avg_price = portfolio[coin]['average_price']
    if avg_price == 0:
        return 0
    return ((current_price - avg_price) / avg_price) * 100

# Poster sur X
def post_to_twitter(client, message):
    try:
        client.create_tweet(text=message)
        logging.info(f"Publié sur X : {message}")
    except Exception as e:
        logging.error(f"Erreur lors de la publication sur X : {str(e)}")

# Effectuer un achat
def place_order(exchange, portfolio, symbol, amount, coin, pairing, test_mode):
    try:
        if test_mode:
            logging.info(f"Simulation : Achat de {amount} {pairing} de {symbol}")
            return {"status": "simulated"}, 0, 0
        else:
            price = get_current_price(exchange, symbol)
            if price == 0:
                raise Exception(f"Impossible de récupérer le prix pour {symbol}")
            quantity = amount / price
            order = exchange.create_market_buy_order(symbol, amount)
            logging.info(f"Achat réel : {quantity} {coin} à {price} {pairing} ({amount} {pairing}) - Ordre : {order}")
            update_portfolio(portfolio, coin, quantity, price, amount)
            return order, quantity, price
    except Exception as e:
        logging.error(f"Erreur lors de l'achat de {symbol} : {str(e)}")
        return None, 0, 0

# Tâche quotidienne
def daily_task(exchange, config, twitter_client, portfolio, test_mode):
    coins = config['COINS']
    tweet_lines = [f"[Achat DCA] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    for coin, details in coins.items():
        symbol = f"{coin}/{details['PAIRING']}"
        amount = details['AMOUNT']
        pairing = details['PAIRING']
        balance = check_balance(exchange, pairing)
        if balance < amount and not test_mode:
            logging.error(f"Solde insuffisant pour {symbol} : {balance} {pairing} < {amount} {pairing}")
            tweet_lines.append(f"- {coin}: Solde insuffisant ({balance} {pairing} < {amount} {pairing})")
            continue
        order, quantity, price = place_order(exchange, portfolio, symbol, amount, coin, pairing, test_mode)
        if order and not test_mode:
            current_price = get_current_price(exchange, symbol)
            performance = calculate_performance(portfolio, coin, current_price)
            tweet_lines.append(
                f"- {coin}: Acheté {quantity:.4f} {coin} à {price:.2f} {pairing} ({amount} {pairing}), "
                f"Total: {portfolio[coin]['total_quantity']:.4f} {coin}, "
                f"Évolution: {'+' if performance >= 0 else ''}{performance:.2f}%"
            )
    if not test_mode and len(tweet_lines) > 1:
        tweet_lines.append("#DCA #Crypto #MEXC #Investing")
        post_to_twitter(twitter_client, "\n".join(tweet_lines))

def main():
    config = load_config()
    mexc_keys, twitter_keys = load_api_keys()
    portfolio = load_portfolio()
    exchange = connect_to_exchange(config, mexc_keys)
    twitter_client = connect_to_twitter(twitter_keys)
    for coin, details in config['COINS'].items():
        schedule.every().day.at(details['AT_TIME']).do(
            daily_task, exchange=exchange, config=config, twitter_client=twitter_client,
            portfolio=portfolio, test_mode=config['TEST']
        )
    logging.info("Bot démarré ! En attente des achats programmés...")
    print("Bot démarré ! Appuie sur Ctrl+C pour arrêter.")
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()