"""Make the local MongoDB (127.0.0.1:27017) a one-node replica set, once, and wait until it accepts writes.

Inkwell's protected account writes use MongoDB transactions, which need a replica set (MongoDB Atlas always is one).
Start mongod with `--replSet rs0` (start.command does), then run this. Safe to run again: it does nothing if the set
is already initiated. Your data folder is kept as it is."""
import sys
import time

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

URI = "mongodb://127.0.0.1:27017/?directConnection=true"


def main():
    client = MongoClient(URI, serverSelectionTimeoutMS=15000)
    try:
        hello = client.admin.command("hello")
    except PyMongoError as e:
        print(f"MongoDB isn't reachable on 127.0.0.1:27017 ({type(e).__name__}).")
        return 1
    if "setName" not in hello and not hello.get("isreplicaset"):
        print("MongoDB is running as a plain server. Restart it with --replSet rs0 (start.command does this).")
        return 1
    if not hello.get("setName"):
        try:
            client.admin.command("replSetInitiate", {"_id": "rs0", "members": [{"_id": 0, "host": "127.0.0.1:27017"}]})
        except OperationFailure as e:
            if e.code != 23:  # AlreadyInitialized
                raise
    for _ in range(120):
        if client.admin.command("hello").get("isWritablePrimary"):
            return 0
        time.sleep(0.25)
    print("MongoDB replica set didn't become ready. See data/mongodb/mongod.log")
    return 1


if __name__ == "__main__":
    sys.exit(main())
