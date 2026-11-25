"""MongoDB connection (`M`): lazily connected collection handles and the GridFS bucket."""

from gridfs import GridFSBucket
from pymongo import MongoClient

from core.config import MONGODB_DB, MONGODB_URI


def normalised(uri):
    """Some copied connection strings separate options with ';' as well as '&'; MongoClient accepts only one kind."""
    base, sep, opts = uri.partition("?")
    return base + sep + opts.replace(";", "&") if sep else uri


class Mongo:
    """Lazily connected handles to the MongoDB collections and GridFS bucket."""

    def __init__(self):
        self.client = self.db = self.files = None

    def connect(self):
        self.client = MongoClient(normalised(MONGODB_URI), serverSelectionTimeoutMS=8000, appname="reachout", tz_aware=True)
        self.client.admin.command("ping")
        self.db = self.client[MONGODB_DB]
        self.files = GridFSBucket(self.db, bucket_name="files")

    def __getattr__(self, name):  # M.users, M.kv, M.send_log, ...
        if self.db is None:
            raise RuntimeError("Database not connected")
        return self.db[name]


M = Mongo()
