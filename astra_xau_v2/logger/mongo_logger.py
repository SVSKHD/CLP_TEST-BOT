import logging
from datetime import datetime

logger = logging.getLogger("astra.mongo_logger")

_client = None
_db = None
_collection = None


def init_mongo(uri: str, db_name: str = "astra_xau"):
    global _client, _db, _collection
    if not uri:
        logger.warning("MongoDB URI not set, logging disabled")
        return False
    try:
        from pymongo import MongoClient
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        _client.server_info()
        _db = _client[db_name]
        _collection = _db["trades"]
        logger.info(f"MongoDB connected: {db_name}")
        return True
    except Exception as e:
        logger.warning(f"MongoDB connection failed: {e}")
        _client = None
        return False


def log_trade(trade_data: dict):
    if _collection is None:
        logger.debug("MongoDB not connected, skipping trade log")
        return None

    doc = {
        "symbol": trade_data.get("symbol", ""),
        "direction": trade_data.get("direction", ""),
        "lot": trade_data.get("lot", 0.0),
        "entry_price": trade_data.get("entry_price", 0.0),
        "sl": trade_data.get("sl_price", 0.0),
        "tp": trade_data.get("tp_price", 0.0),
        "close_price": trade_data.get("exit_price", 0.0),
        "pips": trade_data.get("pips", 0.0),
        "pnl_usd": trade_data.get("pnl_usd", 0.0),
        "duration_seconds": trade_data.get("duration_seconds", 0),
        "result": trade_data.get("result", ""),
        "exit_reason": trade_data.get("exit_reason", ""),
        "mode": trade_data.get("mode", "live"),
        "magic": trade_data.get("magic", 0),
        "timestamp": datetime.utcnow(),
        "entry_time": trade_data.get("entry_time"),
        "exit_time": trade_data.get("exit_time"),
    }

    try:
        result = _collection.insert_one(doc)
        logger.debug(f"Trade logged to MongoDB: {result.inserted_id}")
        return result.inserted_id
    except Exception as e:
        logger.warning(f"MongoDB insert failed: {e}")
        return None


def get_today_trades(symbol: str = None) -> list:
    if _collection is None:
        return []

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    query = {"timestamp": {"$gte": today}}
    if symbol:
        query["symbol"] = symbol

    try:
        return list(_collection.find(query).sort("timestamp", -1))
    except Exception as e:
        logger.warning(f"MongoDB query failed: {e}")
        return []


def get_trade_stats(days: int = 30) -> dict:
    if _collection is None:
        return {}

    from datetime import timedelta
    start = datetime.utcnow() - timedelta(days=days)

    try:
        pipeline = [
            {"$match": {"timestamp": {"$gte": start}}},
            {"$group": {
                "_id": "$symbol",
                "total_trades": {"$sum": 1},
                "total_pnl": {"$sum": "$pnl_usd"},
                "total_pips": {"$sum": "$pips"},
                "wins": {"$sum": {"$cond": [{"$gt": ["$pnl_usd", 0]}, 1, 0]}},
                "avg_pnl": {"$avg": "$pnl_usd"},
            }},
        ]
        results = list(_collection.aggregate(pipeline))
        return {r["_id"]: r for r in results}
    except Exception as e:
        logger.warning(f"MongoDB aggregation failed: {e}")
        return {}


def close_mongo():
    global _client
    if _client:
        _client.close()
        _client = None
        logger.info("MongoDB connection closed")


if __name__ == "__main__":
    from config.settings import MONGO_URI
    if MONGO_URI:
        init_mongo(MONGO_URI)
        trades = get_today_trades()
        print(f"Today's trades: {len(trades)}")
        stats = get_trade_stats()
        print(f"30-day stats: {stats}")
        close_mongo()
    else:
        print("MONGO_URI not set. Set in .env to enable MongoDB logging.")
